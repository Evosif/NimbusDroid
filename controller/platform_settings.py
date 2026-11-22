"""Workspace-wide settings and audit log for the Droid Cloud controller.

Settings live in STATE_DIR/platform-settings.json and the audit trail in
STATE_DIR/audit.log (JSON lines). Both are shared by server.py and manager.py.
"""

from __future__ import annotations

import json
import os
import threading
import time
from collections import deque
from typing import Any

from settings import STATE_DIR

SETTINGS_FILE = STATE_DIR / "platform-settings.json"
AUDIT_FILE = STATE_DIR / "audit.log"
AUDIT_MAX_BYTES = 5 * 1024 * 1024

_lock = threading.Lock()

DEFAULTS: dict[str, Any] = {
    "workspaceName": "Droid Cloud",
    "sessionHours": 12,
    "performance": {
        "disableAnimations": True,
        "streamFps": 30,
        "streamBitrateMbps": 4,
        "streamMaxSize": 1200,
    },
}

LIMITS = {
    "sessionHours": (1, 168),
    "streamFps": (10, 60),
    "streamBitrateMbps": (1, 16),
    "streamMaxSize": (480, 1600),
}


def _merge(base: dict[str, Any], extra: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for key, value in (extra or {}).items():
        if isinstance(out.get(key), dict) and isinstance(value, dict):
            out[key] = _merge(out[key], value)
        elif key in out:
            out[key] = value
    return out


def _clamp_int(value: Any, key: str, default: int) -> int:
    lo, hi = LIMITS[key]
    try:
        return max(lo, min(hi, int(value)))
    except (TypeError, ValueError):
        return default


def _normalize(data: dict[str, Any]) -> dict[str, Any]:
    merged = _merge(DEFAULTS, data if isinstance(data, dict) else {})
    name = str(merged.get("workspaceName") or DEFAULTS["workspaceName"]).strip()[:48]
    merged["workspaceName"] = name or DEFAULTS["workspaceName"]
    merged["sessionHours"] = _clamp_int(merged.get("sessionHours"), "sessionHours", 12)
    perf = merged["performance"]
    perf["disableAnimations"] = bool(perf.get("disableAnimations"))
    for key, default in (("streamFps", 30), ("streamBitrateMbps", 4), ("streamMaxSize", 1200)):
        perf[key] = _clamp_int(perf.get(key), key, default)
    return merged


def load() -> dict[str, Any]:
    try:
        return _normalize(json.loads(SETTINGS_FILE.read_text()))
    except (OSError, ValueError):
        return _normalize({})


def save(update: dict[str, Any]) -> dict[str, Any]:
    with _lock:
        current = load()
        merged = _normalize(_merge(current, update if isinstance(update, dict) else {}))
        SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = SETTINGS_FILE.with_name(f".{SETTINGS_FILE.name}.tmp")
        tmp.write_text(json.dumps(merged, indent=2) + "\n")
        os.chmod(tmp, 0o600)
        tmp.replace(SETTINGS_FILE)
        return merged


def audit(event: str, *, actor: str = "admin", ip: str = "", target: str = "", status: int = 200, detail: str = "") -> None:
    entry = {
        "ts": round(time.time(), 3),
        "event": event[:64],
        "actor": actor[:64],
        "ip": ip[:64],
        "target": target[:128],
        "status": int(status),
        "detail": detail[:240],
    }
    line = json.dumps(entry, ensure_ascii=False) + "\n"
    with _lock:
        try:
            AUDIT_FILE.parent.mkdir(parents=True, exist_ok=True)
            if AUDIT_FILE.exists() and AUDIT_FILE.stat().st_size > AUDIT_MAX_BYTES:
                AUDIT_FILE.replace(AUDIT_FILE.with_suffix(".log.1"))
            fd = os.open(AUDIT_FILE, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
            with os.fdopen(fd, "a", encoding="utf-8") as handle:
                handle.write(line)
        except OSError:
            pass


def read_audit(limit: int = 200, query: str = "") -> list[dict[str, Any]]:
    limit = max(1, min(1000, int(limit)))
    q = query.strip().lower()
    rows: deque[dict[str, Any]] = deque(maxlen=limit)
    try:
        with open(AUDIT_FILE, encoding="utf-8") as handle:
            for raw in handle:
                if q and q not in raw.lower():
                    continue
                try:
                    rows.append(json.loads(raw))
                except ValueError:
                    continue
    except OSError:
        return []
    return list(reversed(rows))
