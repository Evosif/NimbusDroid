"""VPN profiles for Droid Cloud devices.

Profiles (.ovpn) are stored privately in STATE_DIR/vpn. Tunnels and routing
are handled by the root helper droid-cloud-netd (see netd.py); this module
only stores profiles, sanitises them and talks to the helper.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import socket
import threading
import time
from typing import Any

from settings import STATE_DIR

VPN_DIR = STATE_DIR / "vpn"
PROFILES_FILE = VPN_DIR / "profiles.json"
NETD_SOCKET = "/run/droid-cloud-netd.sock"
MAX_PROFILES = 32
MAX_CONFIG_BYTES = 64 * 1024
# DNS used by VPN devices; queries leave through the tunnel.
VPN_DNS = "1.1.1.1,8.8.8.8"

# Directives that could run code, write files or change host routing. The
# helper also overrides scripts/routes on the command line; this is a second
# layer so stored profiles stay clean.
BLOCKED = {
    "up", "down", "route-up", "route-pre-down", "ipchange", "tls-verify", "client-connect",
    "client-disconnect", "learn-address", "auth-user-pass-verify", "plugin", "script-security",
    "management", "management-client", "management-query-passwords", "management-hold",
    "log", "log-append", "status", "writepid", "cd", "chroot", "daemon", "user", "group",
    "dev", "dev-type", "dev-node", "redirect-gateway", "route", "route-ipv6", "route-gateway",
    "up-delay", "iproute", "config", "askpass", "auth-user-pass", "tls-export-cert",
    "mlock", "tmp-dir", "setenv-safe", "x509-username-field", "pkcs11-providers", "engine",
}
INLINE_BLOCKS = {"ca", "cert", "key", "tls-auth", "tls-crypt", "tls-crypt-v2", "extra-certs", "pkcs12", "secret", "dh", "crl-verify", "peer-fingerprint", "http-proxy-user-pass", "auth-user-pass"}

_lock = threading.Lock()


class VpnError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


# ---------------------------------------------------------------- helper client
def netd(op: str, timeout: float = 25, **payload: Any) -> dict[str, Any]:
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(timeout)
            sock.connect(NETD_SOCKET)
            sock.sendall((json.dumps({"op": op, **payload}) + "\n").encode())
            data = b""
            while not data.endswith(b"\n"):
                chunk = sock.recv(65536)
                if not chunk:
                    break
                data += chunk
    except OSError as exc:
        raise VpnError(f"VPN helper is not running ({exc.strerror or exc}).", 503) from exc
    try:
        reply = json.loads(data or b"{}")
    except ValueError as exc:
        raise VpnError("VPN helper returned an invalid response.", 502) from exc
    if not reply.get("ok"):
        raise VpnError(reply.get("error") or "VPN helper error.", 500)
    return reply


def helper_available() -> bool:
    try:
        netd("ping", timeout=3)
        return True
    except VpnError:
        return False


# ---------------------------------------------------------------- storage
def _read() -> list[dict[str, Any]]:
    try:
        data = json.loads(PROFILES_FILE.read_text())
        return data if isinstance(data, list) else []
    except (OSError, ValueError):
        return []


def _write(profiles: list[dict[str, Any]]) -> None:
    VPN_DIR.mkdir(parents=True, exist_ok=True)
    os.chmod(VPN_DIR, 0o700)
    tmp = PROFILES_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(profiles, indent=2) + "\n")
    os.chmod(tmp, 0o600)
    tmp.replace(PROFILES_FILE)


def _private_write(path, text: str) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(text)


def config_path(profile_id: str):
    return VPN_DIR / f"{profile_id}.ovpn"


def auth_path(profile_id: str):
    return VPN_DIR / f"{profile_id}.auth"


def get(profile_id: str | None) -> dict[str, Any] | None:
    if not profile_id:
        return None
    return next((p for p in _read() if p["id"] == profile_id), None)


def sanitize(text: str) -> tuple[str, dict[str, Any]]:
    if len(text.encode()) > MAX_CONFIG_BYTES:
        raise VpnError("Profile is too large (max 64 KB).")
    out: list[str] = []
    info: dict[str, Any] = {"remote": None, "port": None, "proto": None, "needsAuth": False}
    inline = None
    removed: list[str] = []
    for raw in text.replace("\r\n", "\n").split("\n"):
        line = raw.strip()
        if inline:
            out.append(raw)
            if line.lower() == f"</{inline}>":
                inline = None
            continue
        m = re.fullmatch(r"<([a-z0-9-]+)>", line.lower())
        if m:
            tag = m.group(1)
            if tag not in INLINE_BLOCKS or tag == "auth-user-pass":
                raise VpnError(f"Inline block <{tag}> is not supported.")
            inline = tag
            out.append(raw)
            continue
        if not line or line.startswith(("#", ";")):
            out.append(raw)
            continue
        words = line.split()
        key = words[0].lower().lstrip("-")
        if key == "auth-user-pass":
            info["needsAuth"] = True
        if key in BLOCKED:
            removed.append(key)
            continue
        if key == "remote" and len(words) >= 2 and info["remote"] is None:
            info["remote"] = words[1]
            info["port"] = words[2] if len(words) > 2 else "1194"
            if len(words) > 3:
                info["proto"] = words[3]
        if key == "proto" and len(words) > 1:
            info["proto"] = info["proto"] or words[1]
        out.append(raw)
    if inline:
        raise VpnError(f"Inline block <{inline}> is not closed.")
    if not info["remote"]:
        raise VpnError("Profile has no 'remote' server line.")
    info["proto"] = (info["proto"] or "udp").lower()
    info["removed"] = sorted(set(removed))
    return "\n".join(out).rstrip() + "\n", info


# ---------------------------------------------------------------- operations
def _free_slot(profiles: list[dict[str, Any]]) -> int:
    used = {p["slot"] for p in profiles}
    for slot in range(1, MAX_PROFILES + 1):
        if slot not in used:
            return slot
    raise VpnError(f"Maximum of {MAX_PROFILES} VPN profiles reached.", 409)


def _push(profile: dict[str, Any], restart: bool = False) -> None:
    netd(
        "configure",
        slot=profile["slot"],
        config=str(config_path(profile["id"])),
        auth=str(auth_path(profile["id"])) if profile.get("hasAuth") else None,
        enabled=bool(profile.get("enabled")),
        restart=restart,
    )


def create(payload: dict[str, Any]) -> dict[str, Any]:
    name = str(payload.get("name") or "").strip()[:48]
    text = str(payload.get("config") or "")
    if not text.strip():
        raise VpnError("Paste or upload an .ovpn profile.")
    clean, info = sanitize(text)
    username = str(payload.get("username") or "")
    password = str(payload.get("password") or "")
    if info["needsAuth"] and not (username and password):
        raise VpnError("This profile needs a username and password.")
    with _lock:
        profiles = _read()
        profile = {
            "id": "vpn-" + secrets.token_hex(4),
            "name": name or f"{info['remote']}:{info['port']}",
            "slot": _free_slot(profiles),
            "remote": info["remote"],
            "port": info["port"],
            "proto": info["proto"],
            "hasAuth": bool(username and password),
            "enabled": bool(payload.get("connect", True)),
            "createdAt": int(time.time()),
            "removedDirectives": info["removed"],
        }
        VPN_DIR.mkdir(parents=True, exist_ok=True)
        _private_write(config_path(profile["id"]), clean)
        if profile["hasAuth"]:
            _private_write(auth_path(profile["id"]), f"{username}\n{password}\n")
        profiles.append(profile)
        _write(profiles)
    _push(profile)
    return profile


def update(profile_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    with _lock:
        profiles = _read()
        profile = next((p for p in profiles if p["id"] == profile_id), None)
        if not profile:
            raise VpnError("VPN profile not found.", 404)
        if "name" in payload:
            profile["name"] = str(payload["name"] or "").strip()[:48] or profile["name"]
        if "enabled" in payload:
            profile["enabled"] = bool(payload["enabled"])
        restart = False
        text = str(payload.get("config") or "")
        needs_auth = None
        if text.strip():
            clean, info = sanitize(text)
            _private_write(config_path(profile_id), clean)
            profile.update(
                remote=info["remote"], port=info["port"], proto=info["proto"], removedDirectives=info["removed"]
            )
            needs_auth = info["needsAuth"]
            restart = True
        username = str(payload.get("username") or "")
        password = str(payload.get("password") or "")
        if username and password:
            _private_write(auth_path(profile_id), f"{username}\n{password}\n")
            profile["hasAuth"] = True
            restart = True
        elif payload.get("clearAuth") or needs_auth is False:
            if profile.get("hasAuth"):
                restart = True
            profile["hasAuth"] = False
            try:
                auth_path(profile_id).unlink()
            except FileNotFoundError:
                pass
        if needs_auth and not profile.get("hasAuth"):
            raise VpnError("This profile needs a username and password.")
        profile["updatedAt"] = int(time.time())
        _write(profiles)
    _push(profile, restart=restart)
    return profile


def delete(profile_id: str) -> dict[str, Any]:
    with _lock:
        profiles = _read()
        profile = next((p for p in profiles if p["id"] == profile_id), None)
        if not profile:
            raise VpnError("VPN profile not found.", 404)
        profiles = [p for p in profiles if p["id"] != profile_id]
        _write(profiles)
    try:
        netd("remove", slot=profile["slot"])
    finally:
        for path in (config_path(profile_id), auth_path(profile_id)):
            try:
                path.unlink()
            except FileNotFoundError:
                pass
    return profile


def ensure_enabled(profile: dict[str, Any]) -> None:
    """Starting a device on a VPN profile brings its tunnel up."""
    if not profile.get("enabled"):
        update(profile["id"], {"enabled": True})
    else:
        _push(profile)


def assign(pid: int, profile: dict[str, Any] | None) -> int:
    reply = netd("assign", pid=int(pid), slot=profile["slot"] if profile else None)
    return int(reply.get("moved", 0))


def wait_connected(profile: dict[str, Any], timeout: float = 20) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        slot = netd("status").get("slots", {}).get(str(profile["slot"]), {})
        if slot.get("state") == "connected":
            return True
        time.sleep(1)
    return False


def listing() -> dict[str, Any]:
    profiles = _read()
    available = True
    slots: dict[str, Any] = {}
    try:
        slots = netd("status").get("slots", {})
    except VpnError:
        available = False
    out = []
    for p in profiles:
        s = slots.get(str(p["slot"]), {})
        out.append(
            {
                **{k: v for k, v in p.items() if k != "slot"},
                "state": s.get("state", "unknown" if not available else "disconnected"),
                "tunnelIp": s.get("tunnelIp"),
                "rxBytes": s.get("rxBytes", 0),
                "txBytes": s.get("txBytes", 0),
                "log": s.get("log", []),
            }
        )
    return {"profiles": out, "helper": available}


def egress(profile_id: str) -> str | None:
    profile = get(profile_id)
    if not profile:
        raise VpnError("VPN profile not found.", 404)
    return netd("egress", slot=profile["slot"]).get("ip")
