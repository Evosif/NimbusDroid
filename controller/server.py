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
from urllib.parse import urlparse

from manager import MachineError, MachineManager
from settings import API_HOST, API_PORT, AUTH_FILE, PASSWORD_FILE, STATE_DIR, UPLOAD_DIR
MAX_JSON = 64 * 1024
MAX_APK = 250 * 1024 * 1024
SESSION_COOKIE = "android_cloud_session"
SESSION_SECONDS = 12 * 60 * 60

manager = MachineManager()
sessions: dict[str, float] = {}
sessions_lock = threading.Lock()
login_failures: dict[str, list[float]] = {}


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


def valid_session(token: str | None) -> bool:
    if not token:
        return False
    current = time.time()
    with sessions_lock:
        expiry = sessions.get(token)
        if not expiry or expiry < current:
            sessions.pop(token, None)
            return False
        sessions[token] = current + SESSION_SECONDS
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

    def authenticated(self) -> bool:
        return valid_session(self.cookie_token())

    def require_auth(self) -> bool:
        if self.authenticated():
            return True
        self.respond(401, {"ok": False, "message": "Silakan masuk."})
        return False

    def same_origin(self) -> bool:
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
            if path == "/api/options":
                self.respond(200, {"ok": True, **manager.options()})
                return
            if path == "/api/machines":
                self.respond(200, {"ok": True, **manager.list_machines()})
                return
            parts = self.path_parts()
            if (
                len(parts) == 4
                and parts[:2] == ["api", "machines"]
                and parts[3] == "screenshot"
            ):
                image = manager.screenshot(parts[2])
                self.respond_bytes(200, image, "image/png")
                return
            if len(parts) == 3 and parts[:2] == ["api", "machines"]:
                self.respond(200, {"ok": True, "machine": manager.get_machine(parts[2])})
                return
            self.respond(404, {"ok": False, "message": "Endpoint tidak ditemukan."})
        except MachineError as exc:
            self.respond(exc.status, {"ok": False, "message": exc.message})
        except Exception as exc:
            self.respond(500, {"ok": False, "message": f"Kesalahan controller: {exc}"})

    def do_POST(self) -> None:
        if not self.same_origin():
            self.respond(403, {"ok": False, "message": "Origin tidak diizinkan."})
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
            parts = self.path_parts()
            if path == "/api/machines":
                machine = manager.create_machine(self.read_json())
                self.respond(201, {"ok": True, "machine": machine})
                return
            if len(parts) == 4 and parts[:2] == ["api", "machines"]:
                machine_id, action = parts[2], parts[3]
                if action == "start":
                    self.respond(200, {"ok": True, "machine": manager.start_machine(machine_id)})
                    return
                if action == "stop":
                    self.respond(200, {"ok": True, "machine": manager.stop_machine(machine_id)})
                    return
                if action == "restart":
                    self.respond(202, {"ok": True, **manager.restart_machine(machine_id)})
                    return
                if action == "clone":
                    machine = manager.clone_machine(machine_id, self.read_json())
                    self.respond(201, {"ok": True, "machine": machine})
                    return
                if action == "wipe":
                    self.respond(200, {"ok": True, "machine": manager.wipe_machine(machine_id)})
                    return
                if action == "autostart":
                    payload = self.read_json()
                    machine = manager.set_autostart(machine_id, bool(payload.get("enabled")))
                    self.respond(200, {"ok": True, "machine": machine})
                    return
                if action == "install":
                    self._install_apk(machine_id)
                    return
                if action == "key":
                    payload = self.read_json()
                    self.respond(
                        200,
                        {"ok": True, **manager.send_key(machine_id, str(payload.get("key", "")))},
                    )
                    return
                if action == "location":
                    payload = self.read_json()
                    self.respond(
                        200,
                        {
                            "ok": True,
                            **manager.set_location(
                                machine_id,
                                payload.get("latitude"),
                                payload.get("longitude"),
                                str(payload.get("label", "")),
                            ),
                        },
                    )
                    return
                if action == "configure":
                    machine = manager.configure_machine(machine_id, self.read_json())
                    self.respond(200, {"ok": True, "machine": machine})
                    return
            self.respond(404, {"ok": False, "message": "Endpoint tidak ditemukan."})
        except MachineError as exc:
            self.respond(exc.status, {"ok": False, "message": exc.message})
        except Exception as exc:
            self.respond(500, {"ok": False, "message": f"Kesalahan controller: {exc}"})

    def do_DELETE(self) -> None:
        if not self.same_origin():
            self.respond(403, {"ok": False, "message": "Origin tidak diizinkan."})
            return
        if not self.require_auth():
            return
        parts = self.path_parts()
        if len(parts) != 3 or parts[:2] != ["api", "machines"]:
            self.respond(404, {"ok": False, "message": "Endpoint tidak ditemukan."})
            return
        try:
            manager.delete_machine(parts[2])
            self.respond(200, {"ok": True, "message": "Mesin dihapus."})
        except MachineError as exc:
            self.respond(exc.status, {"ok": False, "message": exc.message})
        except Exception as exc:
            self.respond(500, {"ok": False, "message": f"Kesalahan controller: {exc}"})

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
            self.respond(401, {"ok": False, "message": "Kata sandi salah."})
            return
        token = secrets.token_urlsafe(36)
        with sessions_lock:
            sessions[token] = time.time() + SESSION_SECONDS
            login_failures.pop(address, None)
        cookie = (
            f"{SESSION_COOKIE}={token}; Path=/; HttpOnly; SameSite=Strict; "
            f"Max-Age={SESSION_SECONDS}"
        )
        self.respond(200, {"ok": True}, {"Set-Cookie": cookie})

    def _logout(self) -> None:
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
            success, message = manager.install_apk(machine_id, path)
            self.respond(
                200 if success else 400,
                {"ok": success, "message": message},
            )
        finally:
            if path:
                path.unlink(missing_ok=True)

    def log_message(self, format: str, *args) -> None:
        pass


def main() -> None:
    ensure_credentials()
    os.umask(0o077)
    server = ThreadingHTTPServer((API_HOST, API_PORT), Handler)
    server.daemon_threads = True

    def stop_handler(signum, frame):
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, stop_handler)
    signal.signal(signal.SIGINT, stop_handler)
    manager.start_autostart()
    try:
        server.serve_forever()
    finally:
        manager.shutdown()
        server.server_close()


if __name__ == "__main__":
    main()
