"""Environment-driven runtime settings for the Droid Cloud services."""

from __future__ import annotations

import getpass
import os
from pathlib import Path


def _path(name: str, default: Path) -> Path:
    value = os.environ.get(name)
    return Path(value).expanduser().resolve() if value else default.resolve()


def _port(name: str, default: int) -> int:
    try:
        value = int(os.environ.get(name, default))
    except ValueError as exc:
        raise RuntimeError(f"{name} harus berupa angka.") from exc
    if not 1 <= value <= 65535:
        raise RuntimeError(f"{name} harus berada di antara 1 dan 65535.")
    return value


APP_HOME = _path("DROID_CLOUD_HOME", Path.home())
SDK_ROOT = _path("DROID_CLOUD_SDK_ROOT", APP_HOME / "Android" / "Sdk")
AVD_DIR = _path(
    "DROID_CLOUD_AVD_HOME",
    Path(os.environ.get("ANDROID_AVD_HOME", APP_HOME / ".android" / "avd")),
)
STATE_DIR = _path("DROID_CLOUD_STATE_DIR", APP_HOME / ".config" / "android-cloud")
CACHE_DIR = _path("DROID_CLOUD_CACHE_DIR", APP_HOME / ".cache" / "android-cloud")

MACHINE_DIR = STATE_DIR / "machines"
LOG_DIR = CACHE_DIR / "logs"
UPLOAD_DIR = CACHE_DIR / "uploads"
TOKEN_FILE = STATE_DIR / "tokens"
PASSWORD_FILE = STATE_DIR / "password"
AUTH_FILE = STATE_DIR / "auth.json"

ADB = _path("DROID_CLOUD_ADB", SDK_ROOT / "platform-tools" / "adb")
AVDMANAGER = _path(
    "DROID_CLOUD_AVDMANAGER",
    SDK_ROOT / "cmdline-tools" / "latest" / "bin" / "avdmanager",
)
SDKMANAGER = _path(
    "DROID_CLOUD_SDKMANAGER",
    SDK_ROOT / "cmdline-tools" / "latest" / "bin" / "sdkmanager",
)
EMULATOR = _path("DROID_CLOUD_EMULATOR", SDK_ROOT / "emulator" / "emulator")
SCRCPY = _path("DROID_CLOUD_SCRCPY", Path("/usr/bin/scrcpy"))
XVFB = _path("DROID_CLOUD_XVFB", Path("/usr/bin/Xvfb"))
X11VNC = _path("DROID_CLOUD_X11VNC", Path("/usr/bin/x11vnc"))

API_HOST = os.environ.get("DROID_CLOUD_API_HOST", "127.0.0.1").strip()
API_PORT = _port("DROID_CLOUD_API_PORT", 8081)
SSH_USER = os.environ.get("DROID_CLOUD_SSH_USER", getpass.getuser()).strip()
