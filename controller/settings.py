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
ISO_DIR = _path("DROID_CLOUD_ISO_DIR", STATE_DIR / "isos")
ISO_VM_DIR = _path("DROID_CLOUD_ISO_VM_DIR", STATE_DIR / "iso-vms")
ISO_CACHE_DIR = CACHE_DIR / "iso-boot"
LOG_DIR = CACHE_DIR / "logs"
UPLOAD_DIR = CACHE_DIR / "uploads"
TOKEN_FILE = STATE_DIR / "tokens"
PASSWORD_FILE = STATE_DIR / "password"
AUTH_FILE = STATE_DIR / "auth.json"
IMAGE_ARTIFACTS_DIR = STATE_DIR / "image-artifacts"
ROOT_RAMDISK = _path(
    "DROID_CLOUD_ROOT_RAMDISK",
    IMAGE_ARTIFACTS_DIR / "playstore-api36-ramdisk-rootavd-magisk26.4.img",
)
ROOT_RAMDISK_API35 = _path(
    "DROID_CLOUD_ROOT_RAMDISK_API35",
    IMAGE_ARTIFACTS_DIR / "playstore-api35-ramdisk-rootavd-magisk26.4.img",
)
RESETPROP_BIN = _path(
    "DROID_CLOUD_RESETPROP_BIN",
    IMAGE_ARTIFACTS_DIR / "resetprop-x86_64",
)
BUSYBOX_BIN = _path(
    "DROID_CLOUD_BUSYBOX_BIN",
    IMAGE_ARTIFACTS_DIR / "busybox-x86_64",
)
REZYGISK_ZIP = _path(
    "DROID_CLOUD_REZYGISK_ZIP",
    IMAGE_ARTIFACTS_DIR / "ReZygisk-v1.0.0-release.zip",
)
PIFORK_ZIP = _path(
    "DROID_CLOUD_PIFORK_ZIP",
    IMAGE_ARTIFACTS_DIR / "PlayIntegrityFork-v17.zip",
)
TRICKYSTORE_ZIP = _path(
    "DROID_CLOUD_TRICKYSTORE_ZIP",
    IMAGE_ARTIFACTS_DIR / "Tricky-Store-v1.4.1.zip",
)
UNPPU_SO = _path(
    "DROID_CLOUD_UNPPU_SO",
    IMAGE_ARTIFACTS_DIR / "unppu-x86_64.so",
)
RIL_SO = _path(
    "DROID_CLOUD_RIL_SO",
    IMAGE_ARTIFACTS_DIR / "libdroidcloud_ril.so",
)
BT_BIN = _path(
    "DROID_CLOUD_BT_BIN",
    IMAGE_ARTIFACTS_DIR / "droidcloud_bt",
)
QEMU_SYSTEM = _path("DROID_CLOUD_QEMU", Path("/usr/bin/qemu-system-x86_64"))
QEMU_IMG = _path("DROID_CLOUD_QEMU_IMG", Path("/usr/bin/qemu-img"))
XORRISO = _path("DROID_CLOUD_XORRISO", Path("/usr/bin/xorriso"))
MKFS_EXT4 = _path("DROID_CLOUD_MKFS_EXT4", Path("/usr/sbin/mkfs.ext4"))
E2FSCK = _path("DROID_CLOUD_E2FSCK", Path("/usr/sbin/e2fsck"))

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
EMULATOR_GPU = os.environ.get("DROID_CLOUD_EMULATOR_GPU", "swangle").strip()
if EMULATOR_GPU not in {
    "auto",
    "host",
    "software",
    "lavapipe",
    "swiftshader",
    "swangle",
}:
    raise RuntimeError("DROID_CLOUD_EMULATOR_GPU tidak didukung.")
SCRCPY = _path("DROID_CLOUD_SCRCPY", Path("/usr/bin/scrcpy"))
XVFB = _path("DROID_CLOUD_XVFB", Path("/usr/bin/Xvfb"))
X11VNC = _path("DROID_CLOUD_X11VNC", Path("/usr/bin/x11vnc"))

API_HOST = os.environ.get("DROID_CLOUD_API_HOST", "127.0.0.1").strip()
if API_HOST not in {"127.0.0.1", "::1", "localhost"}:
    raise RuntimeError("DROID_CLOUD_API_HOST harus berupa alamat loopback.")
API_PORT = _port("DROID_CLOUD_API_PORT", 8081)
SSH_USER = os.environ.get("DROID_CLOUD_SSH_USER", getpass.getuser()).strip()
