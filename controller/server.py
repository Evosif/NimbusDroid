#!/usr/bin/env python3
"""Authenticated HTTP API for the Android cloud controller."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import signal
import tempfile
import threading
import time
import zipfile
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from manager import MachineError, MachineManager
import platform_settings
import vpn
from settings import API_HOST, API_PORT, AUTH_FILE, PASSWORD_FILE, STATE_DIR, UPLOAD_DIR
API_KEYS_FILE = STATE_DIR / "api_keys.json"
MAX_JSON = 128 * 1024
MAX_APK = 250 * 1024 * 1024
SESSION_COOKIE = "android_cloud_session"
SESSION_SECONDS = 12 * 60 * 60

manager: MachineManager | None = None
_manager_lock = threading.Lock()


def get_manager() -> MachineManager:
    global manager
    if manager is None:
        with _manager_lock:
            if manager is None:
                manager = MachineManager()
    return manager
sessions: dict[str, float] = {}
sessions_lock = threading.Lock()
login_failures: dict[str, list[float]] = {}
api_keys_lock = threading.Lock()


def _load_api_keys() -> list[dict]:
    try:
        data = json.loads(API_KEYS_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except (OSError, ValueError, json.JSONDecodeError):
        return []


def _save_api_keys(keys: list[dict]) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    tmp = API_KEYS_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(keys, indent=2), encoding="utf-8")
    os.chmod(tmp, 0o600)
    tmp.replace(API_KEYS_FILE)


def create_api_key(name: str) -> dict:
    clean = str(name or "").strip()[:48]
    if len(clean) < 2:
        raise MachineError("Token name must be at least 2 characters.")
    key_id = secrets.token_hex(4)
    secret = secrets.token_urlsafe(24)
    token = f"dc_{key_id}_{secret}"
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", token.encode(), salt, 120_000)
    record = {
        "id": key_id,
        "name": clean,
        "prefix": f"dc_{key_id}_...",
        "salt": salt.hex(),
        "digest": digest.hex(),
        "createdAt": time.time(),
        "lastUsedAt": None,
    }
    with api_keys_lock:
        keys = _load_api_keys()
        if len(keys) >= 32:
            raise MachineError("Maximum of 32 API tokens reached.", 409)
        keys.insert(0, record)
        _save_api_keys(keys)
    return {
        "id": key_id,
        "name": clean,
        "prefix": record["prefix"],
        "token": token,
        "createdAt": record["createdAt"],
    }


def list_api_keys() -> list[dict]:
    with api_keys_lock:
        return [
            {
                "id": item["id"],
                "name": item["name"],
                "prefix": item["prefix"],
                "createdAt": item.get("createdAt"),
                "lastUsedAt": item.get("lastUsedAt"),
            }
            for item in _load_api_keys()
        ]


def delete_api_key(key_id: str) -> None:
    with api_keys_lock:
        keys = _load_api_keys()
        updated = [item for item in keys if item.get("id") != key_id]
        if len(updated) == len(keys):
            raise MachineError("API token not found.", 404)
        _save_api_keys(updated)


def verify_api_key(token: str | None) -> bool:
    if not token or not token.startswith("dc_"):
        return False
    parts = token.split("_", 2)
    if len(parts) != 3:
        return False
    key_id = parts[1]
    with api_keys_lock:
        keys = _load_api_keys()
        for item in keys:
            if item.get("id") != key_id:
                continue
            try:
                salt = bytes.fromhex(item["salt"])
                expected = bytes.fromhex(item["digest"])
                actual = hashlib.pbkdf2_hmac("sha256", token.encode(), salt, 120_000)
                if hmac.compare_digest(actual, expected):
                    item["lastUsedAt"] = time.time()
                    _save_api_keys(keys)
                    return True
            except (ValueError, KeyError):
                return False
    return False


def ensure_credentials() -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    if not PASSWORD_FILE.exists():
        password = secrets.token_urlsafe(18)
        temporary = PASSWORD_FILE.with_suffix(".tmp")
        temporary.write_text(password + "\n")
        os.chmod(temporary, 0o600)
        temporary.replace(PASSWORD_FILE)
    password = PASSWORD_FILE.read_text().strip()
    if not AUTH_FILE.exists():
        salt = secrets.token_bytes(16)
        digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 310_000)
        AUTH_FILE.write_text(
            json.dumps({"salt": salt.hex(), "digest": digest.hex()})
        )
        os.chmod(AUTH_FILE, 0o600)


def verify_password(password: str) -> bool:
    try:
        data = json.loads(AUTH_FILE.read_text())
        salt = bytes.fromhex(data["salt"])
        expected = bytes.fromhex(data["digest"])
        actual = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 310_000)
        return hmac.compare_digest(actual, expected)
    except (OSError, ValueError, KeyError, json.JSONDecodeError):
        return False


def rate_limited(address: str) -> bool:
    current = time.monotonic()
    with sessions_lock:
        recent = [stamp for stamp in login_failures.get(address, []) if current - stamp < 300]
        login_failures[address] = recent
        return len(recent) >= 8


def record_login_failure(address: str) -> None:
    with sessions_lock:
        login_failures.setdefault(address, []).append(time.monotonic())


def session_seconds() -> int:
    try:
        return int(platform_settings.load()["sessionHours"]) * 3600
    except Exception:
        return SESSION_SECONDS


def change_password(current: str, new: str) -> None:
    if not verify_password(current):
        raise MachineError("Current password is incorrect.", 403)
    if len(new) < 10:
        raise MachineError("New password must be at least 10 characters.")
    if len(new) > 256:
        raise MachineError("New password is too long.")
    if new == current:
        raise MachineError("New password must be different from the current one.")
    classes = sum(bool(re.search(p, new)) for p in (r"[a-z]", r"[A-Z]", r"\d", r"[^A-Za-z0-9]"))
    if classes < 3:
        raise MachineError("Use at least 3 of: lowercase, uppercase, digits, symbols.")
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", new.encode(), salt, 310_000)
    tmp = AUTH_FILE.with_name(f".{AUTH_FILE.name}.tmp")
    tmp.write_text(json.dumps({"salt": salt.hex(), "digest": digest.hex(), "changedAt": int(time.time())}))
    os.chmod(tmp, 0o600)
    tmp.replace(AUTH_FILE)
    # Keep the root-only recovery file in sync so `cat password` on the host still works.
    tmp = PASSWORD_FILE.with_name(f".{PASSWORD_FILE.name}.tmp")
    tmp.write_text(new + "\n")
    os.chmod(tmp, 0o600)
    tmp.replace(PASSWORD_FILE)


def password_changed_at() -> int | None:
    try:
        return json.loads(AUTH_FILE.read_text()).get("changedAt")
    except (OSError, ValueError):
        return None


def revoke_sessions(keep: str | None = None) -> int:
    with sessions_lock:
        doomed = [token for token in sessions if token != keep]
        for token in doomed:
            sessions.pop(token, None)
    return len(doomed)


def host_system_info() -> dict:
    info: dict = {"cpuCount": os.cpu_count() or 0}
    try:
        info["load"] = [round(v, 2) for v in os.getloadavg()]
    except OSError:
        info["load"] = []
    try:
        with open("/proc/uptime", encoding="utf-8") as handle:
            info["uptimeSeconds"] = int(float(handle.read().split()[0]))
    except (OSError, ValueError, IndexError):
        pass
    try:
        values = {}
        with open("/proc/meminfo", encoding="utf-8") as handle:
            for line in handle:
                key, value = line.split(":", 1)
                values[key] = int(value.strip().split()[0])
        info["memTotalGb"] = round(values.get("MemTotal", 0) / 1048576, 1)
        info["memAvailableGb"] = round(values.get("MemAvailable", 0) / 1048576, 1)
    except (OSError, ValueError):
        pass
    try:
        st = os.statvfs(str(STATE_DIR))
        info["diskTotalGb"] = round(st.f_blocks * st.f_frsize / 1e9, 1)
        info["diskFreeGb"] = round(st.f_bavail * st.f_frsize / 1e9, 1)
    except OSError:
        pass
    try:
        with open("/etc/os-release", encoding="utf-8") as handle:
            for line in handle:
                if line.startswith("PRETTY_NAME="):
                    info["os"] = line.split("=", 1)[1].strip().strip('"')
    except OSError:
        pass
    info["kvm"] = os.path.exists("/dev/kvm")
    info["activeSessions"] = len(sessions)
    info["passwordChangedAt"] = password_changed_at()
    return info


def valid_session(token: str | None) -> bool:
    if not token:
        return False
    current = time.time()
    with sessions_lock:
        expiry = sessions.get(token)
        if not expiry or expiry < current:
            sessions.pop(token, None)
            return False
        sessions[token] = current + session_seconds()
        return True


class Handler(BaseHTTPRequestHandler):
    server_version = "DroidCloud"

    def respond(self, status: int, data: dict, extra_headers: dict | None = None) -> None:
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.respond_bytes(
            status,
            body,
            "application/json; charset=utf-8",
            extra_headers,
        )

    def respond_bytes(
        self,
        status: int,
        body: bytes,
        content_type: str,
        extra_headers: dict | None = None,
    ) -> None:
        self._dc_status = status
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        if extra_headers:
            for key, value in extra_headers.items():
                self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def cookie_token(self) -> str | None:
        header = self.headers.get("Cookie")
        if not header:
            return None
        cookie = SimpleCookie()
        try:
            cookie.load(header)
        except Exception:
            return None
        morsel = cookie.get(SESSION_COOKIE)
        return morsel.value if morsel else None

    def bearer_token(self) -> str | None:
        header = self.headers.get("Authorization") or ""
        if header.lower().startswith("bearer "):
            return header[7:].strip()
        return None

    def authenticated(self) -> bool:
        return valid_session(self.cookie_token()) or verify_api_key(self.bearer_token())

    def require_auth(self) -> bool:
        if self.authenticated():
            return True
        self.respond(401, {"ok": False, "message": "Authentication required."})
        return False

    def same_origin(self) -> bool:
        if self.bearer_token():
            return True
        origin = self.headers.get("Origin")
        if not origin:
            return True
        parsed = urlparse(origin)
        host = self.headers.get("Host", "")
        return parsed.netloc == host and parsed.scheme in {"http", "https"}

    def read_json(self) -> dict:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0
        if length <= 0 or length > MAX_JSON:
            raise MachineError("Body permintaan tidak valid.", 413)
        try:
            value = json.loads(self.rfile.read(length))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise MachineError("JSON tidak valid.") from exc
        if not isinstance(value, dict):
            raise MachineError("Body permintaan harus berupa objek.")
        return value

    def client_ip(self) -> str:
        forwarded = (self.headers.get("X-Forwarded-For") or "").split(",")[0].strip()
        return forwarded or self.client_address[0]

    def path_parts(self) -> list[str]:
        return [part for part in urlparse(self.path).path.split("/") if part]

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/api/session":
            self.respond(
                200,
                {
                    "ok": True,
                    "authenticated": self.authenticated(),
                },
            )
            return
        if path == "/api/health":
            self.respond(200, {"ok": True, "controller": "ready"})
            return
        if path == "/api/auth/verify":
            if self.authenticated():
                self.respond(200, {"ok": True})
            else:
                self.respond(401, {"ok": False})
            return
        if not self.require_auth():
            return
        try:
            mgr = get_manager()
            if path == "/api/options":
                self.respond(200, {"ok": True, **mgr.options()})
                return
            if path == "/api/machines":
                self.respond(200, {"ok": True, **mgr.list_machines()})
                return
            if path == "/api/macros":
                self.respond(200, mgr.list_macros())
                return
            if path == "/api/fingerprints":
                self.respond(200, mgr.list_fingerprints())
                return
            if path == "/api/isos":
                self.respond(200, {"ok": True, "isos": mgr.list_isos()})
                return
            if path == "/api/tokens":
                self.respond(200, {"ok": True, "tokens": list_api_keys()})
                return
            if path == "/api/settings":
                self.respond(200, {"ok": True, "settings": platform_settings.load()})
                return
            if path == "/api/system":
                self.respond(200, {"ok": True, "system": host_system_info()})
                return
            if path == "/api/vpn":
                data = vpn.listing()
                for p in data["profiles"]:
                    p["devices"] = mgr.machines_using_vpn(p["id"])
                self.respond(200, {"ok": True, **data})
                return
            if path == "/api/audit":
                q = parse_qs(urlparse(self.path).query)
                try:
                    limit = int(q.get("limit", ["200"])[0])
                except ValueError:
                    limit = 200
                rows = platform_settings.read_audit(limit, q.get("q", [""])[0])
                self.respond(200, {"ok": True, "events": rows})
                return
            parts = self.path_parts()
            query = parse_qs(urlparse(self.path).query)
            if len(parts) == 4 and parts[:2] == ["api", "machines"]:
                machine_id, resource = parts[2], parts[3]
                if resource == "screenshot":
                    fast = query.get("fast", ["0"])[0] in {"1", "true"}
                    image = mgr.screenshot(machine_id, fast=fast)
                    self.respond_bytes(200, image, "image/png")
                    return
                if resource == "audio":
                    self.respond(200, mgr.audio_endpoint(machine_id))
                    return
                if resource == "packages":
                    include_system = query.get("system", ["0"])[0] in {"1", "true"}
                    self.respond(200, mgr.list_packages(machine_id, include_system))
                    return
                if resource == "logs":
                    kind = query.get("kind", ["emulator"])[0]
                    try:
                        lines = int(query.get("lines", ["150"])[0])
                    except ValueError:
                        lines = 150
                    self.respond(200, mgr.get_logs(machine_id, kind, lines))
                    return
                if resource == "telemetry":
                    self.respond(200, mgr.device_telemetry(machine_id))
                    return
                if resource == "identity":
                    self.respond(200, mgr.audit_identity(machine_id))
                    return
            if len(parts) == 3 and parts[:2] == ["api", "machines"]:
                self.respond(200, {"ok": True, "machine": mgr.get_machine(parts[2])})
                return
            self.respond(404, {"ok": False, "message": "Endpoint not found."})
        except MachineError as exc:
            self.respond(exc.status, {"ok": False, "message": exc.message})
        except vpn.VpnError as exc:
            self.respond(exc.status, {"ok": False, "message": str(exc)})
        except Exception as exc:
            self.respond(500, {"ok": False, "message": f"Controller error: {exc}"})

    def do_POST(self) -> None:
        if not self.same_origin():
            self.respond(403, {"ok": False, "message": "Origin not allowed."})
            return
        path = urlparse(self.path).path
        if path == "/api/login":
            self._login()
            return
        if path == "/api/logout":
            self._logout()
            return
        if not self.require_auth():
            return
        try:
            if path == "/api/account/password":
                payload = self.read_json()
                change_password(str(payload.get("current", "")), str(payload.get("new", "")))
                keep = self.cookie_token()
                revoked = revoke_sessions(keep)
                platform_settings.audit("account.password_changed", ip=self.client_ip(), detail=f"revoked {revoked} other sessions")
                self.respond(200, {"ok": True, "revokedSessions": revoked})
                return
            if path == "/api/account/sessions/revoke":
                revoked = revoke_sessions(self.cookie_token())
                platform_settings.audit("account.sessions_revoked", ip=self.client_ip(), detail=f"{revoked} sessions")
                self.respond(200, {"ok": True, "revokedSessions": revoked})
                return
            if path == "/api/settings":
                saved = platform_settings.save(self.read_json())
                platform_settings.audit("settings.updated", ip=self.client_ip())
                self.respond(200, {"ok": True, "settings": saved})
                return
            mgr = get_manager()
            if path == "/api/settings/apply-performance":
                self.respond(200, mgr.apply_performance_to_running())
                return
            if path == "/api/vpn/profiles":
                profile = vpn.create(self.read_json())
                self.respond(201, {"ok": True, "profile": profile})
                return
            vpn_parts = self.path_parts()
            if len(vpn_parts) in (4, 5) and vpn_parts[:3] == ["api", "vpn", "profiles"]:
                profile_id = vpn_parts[3]
                if len(vpn_parts) == 5 and vpn_parts[4] == "check":
                    self.respond(200, {"ok": True, "ip": vpn.egress(profile_id)})
                    return
                if len(vpn_parts) == 4:
                    self.respond(200, {"ok": True, "profile": vpn.update(profile_id, self.read_json())})
                    return
            if path == "/api/farm/vpn":
                payload = self.read_json()
                ids = [str(i) for i in payload.get("machineIds") or []][:200]
                results = []
                for machine_id in ids:
                    try:
                        mgr.set_vpn(machine_id, payload.get("profileId") or None)
                        results.append({"id": machine_id, "ok": True})
                    except MachineError as exc:
                        results.append({"id": machine_id, "ok": False, "message": exc.message})
                ok = sum(1 for r in results if r["ok"])
                self.respond(200, {"ok": True, "total": len(results), "succeeded": ok, "results": results})
                return
            parts = self.path_parts()
            if path == "/api/machines":
                machine = mgr.create_machine(self.read_json())
                self.respond(201, {"ok": True, "machine": machine})
                return
            if len(parts) == 4 and parts[:2] == ["api", "machines"]:
                machine_id, action = parts[2], parts[3]
                if action == "start":
                    self.respond(200, {"ok": True, "machine": mgr.start_machine(machine_id)})
                    return
                if action == "stop":
                    self.respond(200, {"ok": True, "machine": mgr.stop_machine(machine_id)})
                    return
                if action == "restart":
                    self.respond(202, {"ok": True, **mgr.restart_machine(machine_id)})
                    return
                if action == "clone":
                    machine = mgr.clone_machine(machine_id, self.read_json())
                    self.respond(201, {"ok": True, "machine": machine})
                    return
                if action == "wipe":
                    self.respond(200, {"ok": True, "machine": mgr.wipe_machine(machine_id)})
                    return
                if action == "vpn":
                    payload = self.read_json()
                    machine = mgr.set_vpn(machine_id, payload.get("profileId") or None)
                    self.respond(200, {"ok": True, "machine": machine})
                    return
                if action == "autostart":
                    payload = self.read_json()
                    machine = mgr.set_autostart(machine_id, bool(payload.get("enabled")))
                    self.respond(200, {"ok": True, "machine": machine})
                    return
                if action == "install":
                    self._install_apk(machine_id)
                    return
                if action == "key":
                    payload = self.read_json()
                    self.respond(
                        200,
                        {"ok": True, **mgr.send_key(machine_id, str(payload.get("key", "")))},
                    )
                    return
                if action == "location":
                    payload = self.read_json()
                    self.respond(
                        200,
                        {
                            "ok": True,
                            **mgr.set_location(
                                machine_id,
                                payload.get("latitude"),
                                payload.get("longitude"),
                                str(payload.get("label", "")),
                            ),
                        },
                    )
                    return
                if action == "configure":
                    machine = mgr.configure_machine(machine_id, self.read_json())
                    self.respond(200, {"ok": True, "machine": machine})
                    return
                if action == "text":
                    payload = self.read_json()
                    self.respond(
                        200,
                        mgr.send_text(
                            machine_id,
                            str(payload.get("text", "")),
                            bool(payload.get("submit", False)),
                        ),
                    )
                    return
                if action == "package":
                    payload = self.read_json()
                    self.respond(
                        200,
                        mgr.package_action(
                            machine_id,
                            str(payload.get("package", "")),
                            str(payload.get("action", "")),
                        ),
                    )
                    return
                if action == "battery":
                    payload = self.read_json()
                    self.respond(
                        200,
                        mgr.set_battery(
                            machine_id,
                            payload.get("level"),
                            bool(payload.get("ac", True)),
                        ),
                    )
                    return
                if action == "shell":
                    payload = self.read_json()
                    self.respond(
                        200,
                        mgr.run_shell(machine_id, str(payload.get("command", ""))),
                    )
                    return
                if action == "meta":
                    machine = mgr.update_meta(machine_id, self.read_json())
                    self.respond(200, {"ok": True, "machine": machine})
                    return
                if action == "identity":
                    self.respond(200, mgr.apply_identity(machine_id, self.read_json()))
                    return
            if path == "/api/fingerprints/sync":
                self.respond(200, mgr.sync_fingerprints())
                return
            if path == "/api/fingerprints/import":
                self.respond(201, mgr.import_fingerprint(self.read_json()))
                return
            if len(parts) == 4 and parts[:2] == ["api", "isos"] and parts[3] == "download":
                self.respond(202, mgr.download_iso(parts[2]))
                return
            if path == "/api/tokens":
                token_info = create_api_key(str(self.read_json().get("name", "")))
                self.respond(201, {"ok": True, "token": token_info})
                return
            if path == "/api/macros":
                self.respond(201, mgr.save_macro(self.read_json()))
                return
            if len(parts) == 4 and parts[:2] == ["api", "macros"] and parts[3] == "run":
                self.respond(200, mgr.run_macro(parts[2], self.read_json()))
                return
            if len(parts) == 3 and parts[:2] == ["api", "farm"]:
                op = parts[2]
                if op == "touch":
                    self.respond(200, mgr.farm_touch(self.read_json()))
                    return
                if op == "key":
                    self.respond(200, mgr.farm_key(self.read_json()))
                    return
                if op == "text":
                    self.respond(200, mgr.farm_text(self.read_json()))
                    return
                if op == "url":
                    self.respond(200, mgr.farm_open_url(self.read_json()))
                    return
                if op == "gps":
                    self.respond(200, mgr.farm_gps(self.read_json()))
                    return
                if op == "package":
                    self.respond(200, mgr.farm_package(self.read_json()))
                    return
                if op == "shell":
                    self.respond(200, mgr.farm_shell(self.read_json()))
                    return
                if op == "power":
                    self.respond(200, mgr.farm_power(self.read_json()))
                    return
                if op == "identity":
                    self.respond(200, mgr.farm_apply_identity(self.read_json()))
                    return
                if op == "batch-clone":
                    self.respond(201, mgr.farm_batch_clone(self.read_json()))
                    return
                if op == "batch-create":
                    self.respond(201, mgr.farm_batch_create(self.read_json()))
                    return
                if op == "install":
                    self._install_farm_apk()
                    return
            self.respond(404, {"ok": False, "message": "Endpoint not found."})
        except MachineError as exc:
            self.respond(exc.status, {"ok": False, "message": exc.message})
        except vpn.VpnError as exc:
            self.respond(exc.status, {"ok": False, "message": str(exc)})
        except Exception as exc:
            self.respond(500, {"ok": False, "message": f"Controller error: {exc}"})

    def do_DELETE(self) -> None:
        if not self.same_origin():
            self.respond(403, {"ok": False, "message": "Origin not allowed."})
            return
        if not self.require_auth():
            return
        parts = self.path_parts()
        try:
            mgr = get_manager()
            if len(parts) == 3 and parts[:2] == ["api", "machines"]:
                mgr.delete_machine(parts[2])
                self.respond(200, {"ok": True, "message": "Device deleted."})
                return
            if len(parts) == 3 and parts[:2] == ["api", "macros"]:
                self.respond(200, mgr.delete_macro(parts[2]))
                return
            if len(parts) == 3 and parts[:2] == ["api", "tokens"]:
                delete_api_key(parts[2])
                self.respond(200, {"ok": True, "message": "Token revoked."})
                return
            if len(parts) == 3 and parts[:2] == ["api", "fingerprints"]:
                self.respond(200, mgr.delete_fingerprint(parts[2]))
                return
            if len(parts) == 4 and parts[:3] == ["api", "vpn", "profiles"]:
                users = mgr.machines_using_vpn(parts[3])
                if users:
                    self.respond(409, {"ok": False, "message": f"{len(users)} device(s) still use this VPN. Switch them to another network first."})
                    return
                vpn.delete(parts[3])
                self.respond(200, {"ok": True})
                return
            self.respond(404, {"ok": False, "message": "Endpoint not found."})
        except MachineError as exc:
            self.respond(exc.status, {"ok": False, "message": exc.message})
        except vpn.VpnError as exc:
            self.respond(exc.status, {"ok": False, "message": str(exc)})
        except Exception as exc:
            self.respond(500, {"ok": False, "message": f"Controller error: {exc}"})

    def _login(self) -> None:
        address = self.client_address[0]
        if rate_limited(address):
            self.respond(429, {"ok": False, "message": "Terlalu banyak percobaan. Coba lagi nanti."})
            return
        try:
            payload = self.read_json()
        except MachineError as exc:
            self.respond(exc.status, {"ok": False, "message": exc.message})
            return
        if not verify_password(str(payload.get("password", ""))):
            record_login_failure(address)
            platform_settings.audit("auth.login_failed", ip=self.client_ip(), status=401)
            self.respond(401, {"ok": False, "message": "Kata sandi salah."})
            return
        token = secrets.token_urlsafe(36)
        with sessions_lock:
            sessions[token] = time.time() + session_seconds()
            login_failures.pop(address, None)
        platform_settings.audit("auth.login", ip=self.client_ip())
        cookie = (
            f"{SESSION_COOKIE}={token}; Path=/; HttpOnly; SameSite=Strict; "
            f"Max-Age={session_seconds()}"
        )
        self.respond(200, {"ok": True}, {"Set-Cookie": cookie})

    def _logout(self) -> None:
        platform_settings.audit("auth.logout", ip=self.client_ip())
        token = self.cookie_token()
        if token:
            with sessions_lock:
                sessions.pop(token, None)
        cookie = f"{SESSION_COOKIE}=; Path=/; HttpOnly; SameSite=Strict; Max-Age=0"
        self.respond(200, {"ok": True}, {"Set-Cookie": cookie})

    def _install_apk(self, machine_id: str) -> None:
        if self.headers.get_content_type() != "application/vnd.android.package-archive":
            raise MachineError("Pilih file APK yang valid.", 415)
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0
        if not 0 < length <= MAX_APK:
            raise MachineError("Ukuran APK harus kurang dari 250 MB.", 413)
        UPLOAD_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
        path: Path | None = None
        try:
            self.connection.settimeout(45)
            with tempfile.NamedTemporaryFile(
                dir=UPLOAD_DIR, suffix=".apk", delete=False
            ) as output:
                path = Path(output.name)
                remaining = length
                while remaining:
                    chunk = self.rfile.read(min(1024 * 1024, remaining))
                    if not chunk:
                        raise MachineError("Unggahan tidak lengkap.")
                    output.write(chunk)
                    remaining -= len(chunk)
            if not zipfile.is_zipfile(path):
                raise MachineError("File bukan APK yang valid.")
            with zipfile.ZipFile(path) as archive:
                if "AndroidManifest.xml" not in archive.namelist():
                    raise MachineError("File bukan APK yang valid.")
            success, message = get_manager().install_apk(machine_id, path)
            self.respond(
                200 if success else 400,
                {"ok": success, "message": message},
            )
        finally:
            if path:
                path.unlink(missing_ok=True)

    def _install_farm_apk(self) -> None:
        if self.headers.get_content_type() != "application/vnd.android.package-archive":
            raise MachineError("Select a valid APK file.", 415)
        raw_ids = self.headers.get("X-Machine-Ids") or ""
        targets = [part.strip() for part in raw_ids.split(",") if part.strip()]
        if not targets:
            raise MachineError("Provide X-Machine-Ids header with target devices.")
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0
        if not 0 < length <= MAX_APK:
            raise MachineError("APK must be smaller than 250 MB.", 413)
        UPLOAD_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
        path: Path | None = None
        try:
            self.connection.settimeout(60)
            with tempfile.NamedTemporaryFile(
                dir=UPLOAD_DIR, suffix=".apk", delete=False
            ) as output:
                path = Path(output.name)
                remaining = length
                while remaining:
                    chunk = self.rfile.read(min(1024 * 1024, remaining))
                    if not chunk:
                        raise MachineError("Incomplete APK upload.")
                    output.write(chunk)
                    remaining -= len(chunk)
            if not zipfile.is_zipfile(path):
                raise MachineError("Invalid APK archive.")
            with zipfile.ZipFile(path) as archive:
                if "AndroidManifest.xml" not in archive.namelist():
                    raise MachineError("Invalid APK archive (missing AndroidManifest.xml).")
            result = get_manager().farm_install_apk(targets, path)
            self.respond(200, result)
        finally:
            if path:
                path.unlink(missing_ok=True)

    def log_message(self, format: str, *args) -> None:
        pass


# --- Audit trail for state-changing requests -------------------------------
_AUDIT_SKIP_SUFFIXES = ("/key", "/text", "/touch", "/audio")
_AUDIT_SKIP_PATHS = {"/api/login", "/api/logout", "/api/account/password", "/api/account/sessions/revoke", "/api/settings"}


def _audited(method_name: str):
    original = getattr(Handler, method_name)

    def wrapper(self) -> None:
        self._dc_status = None
        original(self)
        try:
            path = urlparse(self.path).path
            if not path.startswith("/api/") or path in _AUDIT_SKIP_PATHS:
                return
            if path.startswith("/api/farm/touch") or path.endswith(_AUDIT_SKIP_SUFFIXES):
                return
            parts = [p for p in path.split("/") if p]
            event = ".".join(parts[1:2] + parts[3:4]) if len(parts) >= 4 else ".".join(parts[1:])
            target = parts[2] if len(parts) >= 3 else ""
            verb = {"do_POST": "", "do_DELETE": "delete", "do_PUT": "update"}[method_name]
            if verb:
                event = f"{event}.{verb}"
            platform_settings.audit(event or path, ip=self.client_ip(), target=target, status=self._dc_status or 0)
        except Exception:
            pass

    setattr(Handler, method_name, wrapper)


for _name in ("do_POST", "do_DELETE", "do_PUT"):
    if hasattr(Handler, _name):
        _audited(_name)


def main() -> None:
    ensure_credentials()
    os.umask(0o077)
    mgr = get_manager()
    server = ThreadingHTTPServer((API_HOST, API_PORT), Handler)
    server.daemon_threads = True

    def stop_handler(signum, frame):
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, stop_handler)
    signal.signal(signal.SIGINT, stop_handler)
    mgr.start_autostart()
    try:
        server.serve_forever()
    finally:
        mgr.shutdown()
        server.server_close()


if __name__ == "__main__":
    main()
