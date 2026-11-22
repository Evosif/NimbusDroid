#!/usr/bin/env python3
"""Android virtual machine lifecycle and console manager."""

from __future__ import annotations

import base64
import concurrent.futures
import hashlib
import json
import math
import os
import random
import re
import secrets
import shlex
import shutil
import socket
import subprocess
import tempfile
import threading
import time
import unicodedata
import urllib.request
from pathlib import Path
from typing import Any

import fingerprints
import platform_settings
import vpn
from settings import (
    ADB,
    APP_HOME,
    AVD_DIR,
    AVDMANAGER,
    BT_BIN,
    BUSYBOX_BIN,
    E2FSCK,
    EMULATOR,
    EMULATOR_GPU,
    ISO_CACHE_DIR,
    ISO_DIR,
    ISO_VM_DIR,
    LOG_DIR,
    MACHINE_DIR,
    MKFS_EXT4,
    PIFORK_ZIP,
    QEMU_IMG,
    QEMU_SYSTEM,
    RESETPROP_BIN,
    REZYGISK_ZIP,
    RIL_SO,
    ROOT_RAMDISK,
    ROOT_RAMDISK_API35,
    SCRCPY,
    SDK_ROOT,
    SDKMANAGER,
    SSH_USER,
    STATE_DIR,
    TOKEN_FILE,
    TRICKYSTORE_ZIP,
    UNPPU_SO,
    X11VNC,
    XORRISO,
    XVFB,
)

SDK = SDK_ROOT
HOME = APP_HOME

SYSTEM_IMAGE = "system-images;android-35;google_apis_playstore;x86_64"
ANDROID_VERSION = "Android 15"
API_LEVEL = 35
SYSTEMS = [
    {"api": 30, "label": "Android 11"},
    {"api": 31, "label": "Android 12"},
    {"api": 32, "label": "Android 12L"},
    {"api": 33, "label": "Android 13"},
    {"api": 34, "label": "Android 14"},
    {"api": 35, "label": "Android 15"},
    {"api": 36, "label": "Android 16"},
]
MAX_MACHINES = 64
EMULATOR_MAX_CPUS = 6

DEVICES = [
    {
        "id": "pixel_6",
        "name": "Pixel 6",
        "kind": "Phone",
        "baseDevice": "pixel_6",
        "width": 1080,
        "height": 2400,
        "density": 420,
    },
    {
        "id": "pixel_8",
        "name": "Pixel 8",
        "kind": "Phone",
        "baseDevice": "pixel_8",
        "width": 1080,
        "height": 2400,
        "density": 420,
    },
    {
        "id": "galaxy_s24",
        "name": "Galaxy S24 style",
        "kind": "Phone",
        "baseDevice": "medium_phone",
        "width": 1080,
        "height": 2340,
        "density": 450,
    },
    {
        "id": "galaxy_s24_ultra",
        "name": "Galaxy S24 Ultra style",
        "kind": "Phone",
        "baseDevice": "medium_phone",
        "width": 1440,
        "height": 3120,
        "density": 560,
    },
    {
        "id": "galaxy_z_fold",
        "name": "Galaxy Z Fold style",
        "kind": "Foldable",
        "baseDevice": "pixel_fold",
        "width": 1812,
        "height": 2176,
        "density": 420,
    },
    {
        "id": "medium_phone",
        "name": "Medium Phone",
        "kind": "Phone",
        "baseDevice": "medium_phone",
        "width": 1080,
        "height": 2400,
        "density": 420,
    },
    {
        "id": "medium_tablet",
        "name": "Medium Tablet",
        "kind": "Tablet",
        "baseDevice": "medium_tablet",
        "width": 1600,
        "height": 2560,
        "density": 320,
    },
    {
        "id": "Nexus 7 2013",
        "name": "Nexus 7",
        "kind": "Tablet",
        "baseDevice": "Nexus 7 2013",
        "width": 1200,
        "height": 1920,
        "density": 320,
    },
]
DEVICE_LABELS = {entry["id"]: entry["name"] for entry in DEVICES}
DEVICE_BY_ID = {entry["id"]: entry for entry in DEVICES}

def system_image(api: int) -> str:
    return f"system-images;android-{api};google_apis_playstore;x86_64"

MACHINE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{1,47}$")


class MachineError(Exception):
    """A user-facing machine operation error."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.message = message
        self.status = status


def now() -> float:
    return time.time()


def atomic_write(path: Path, text: str, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{secrets.token_hex(4)}")
    temporary.write_text(text)
    os.chmod(temporary, mode)
    temporary.replace(path)


def atomic_json(path: Path, value: Any, mode: int = 0o600) -> None:
    atomic_write(path, json.dumps(value, indent=2, ensure_ascii=False) + "\n", mode=mode)


def read_json(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return default


def parse_size_gb(value: str | None, default: int) -> int:
    if not value:
        return default
    match = re.fullmatch(r"\s*(\d+)\s*([GM])?\s*", value, re.IGNORECASE)
    if not match:
        return default
    amount = int(match.group(1))
    if (match.group(2) or "G").upper() == "M":
        return max(1, round(amount / 1024))
    return amount


def parse_size_mb(value: str | None, default: int) -> int:
    if not value:
        return default
    match = re.fullmatch(r"\s*(\d+)\s*([GM])?\s*", value, re.IGNORECASE)
    if not match:
        return default
    amount = int(match.group(1))
    if (match.group(2) or "M").upper() == "G":
        return amount * 1024
    return amount


def slugify(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^A-Za-z0-9]+", "-", normalized).strip("-").lower()
    if len(slug) < 2:
        slug = f"machine-{secrets.token_hex(3)}"
    return slug[:40].rstrip("-")


def process_alive(process: subprocess.Popen | None) -> bool:
    return process is not None and process.poll() is None


def terminate_group(process: subprocess.Popen | None, timeout: float = 5.0) -> None:
    if not process_alive(process):
        return
    try:
        os.killpg(process.pid, 15)
    except (ProcessLookupError, PermissionError):
        return
    deadline = time.monotonic() + timeout
    while process.poll() is None and time.monotonic() < deadline:
        time.sleep(0.1)
    if process.poll() is None:
        try:
            os.killpg(process.pid, 9)
        except (ProcessLookupError, PermissionError):
            pass
    try:
        process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        pass


def port_open(port: int) -> bool:
    with socket.socket() as sock:
        sock.settimeout(0.15)
        return sock.connect_ex(("127.0.0.1", port)) == 0


SCRCPY_SERVER_JAR = Path(os.environ.get("DROID_CLOUD_SCRCPY_SERVER", "/usr/share/scrcpy/scrcpy-server"))


class MachineManager:
    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.operation_locks: dict[str, threading.Lock] = {}
        self.runtime: dict[str, dict[str, Any]] = {}
        self._adb_cache: tuple[float, dict[str, dict[str, str]]] = (0.0, {})
        self._boot_cache: dict[str, tuple[float, bool]] = {}
        self._shot_cache: dict[str, tuple[float, bytes]] = {}
        self._last_errors: dict[str, str] = {}
        self._macro_lock = threading.Lock()
        self._system_install_lock = threading.Lock()
        self._iso_dl_lock = threading.Lock()
        self._iso_downloads: dict[str, dict[str, Any]] = {}
        self._cpu_sample: tuple[float, int, int] | None = None
        for directory in (STATE_DIR, MACHINE_DIR, LOG_DIR, ISO_DIR, ISO_VM_DIR, ISO_CACHE_DIR):
            directory.mkdir(parents=True, exist_ok=True)
            try:
                os.chmod(directory, 0o700)
            except OSError:
                pass
        self._bootstrap_machines()
        self._write_tokens()

    @staticmethod
    def _adb_serial(metadata: dict[str, Any]) -> str:
        if metadata.get("backend") == "iso":
            return f"127.0.0.1:{metadata['adbPort']}"
        return f"emulator-{metadata['consolePort']}"

    @staticmethod
    def _ramdisk_for_api(api_level: int) -> Path | None:
        if int(api_level) == 35 and ROOT_RAMDISK_API35.is_file():
            return ROOT_RAMDISK_API35
        if int(api_level) == 36 and ROOT_RAMDISK.is_file():
            return ROOT_RAMDISK
        return None

    def _operation_lock(self, machine_id: str) -> threading.Lock:
        with self.lock:
            return self.operation_locks.setdefault(machine_id, threading.Lock())

    def _bootstrap_machines(self) -> None:
        known = {path.stem for path in MACHINE_DIR.glob("*.json")}
        for metadata_path in MACHINE_DIR.glob("*.json"):
            metadata = read_json(metadata_path)
            if not isinstance(metadata, dict):
                continue
            if metadata.get("id") == "Android15" and metadata.get("name") in {
                "Android 15",
                "Pixel 6 · Android 15",
                "Production Android",
            }:
                metadata["name"] = "Pixel 6"
                atomic_json(metadata_path, metadata)
        if not AVD_DIR.exists():
            return
        for ini_path in sorted(AVD_DIR.glob("*.ini")):
            machine_id = ini_path.stem
            if machine_id in known:
                continue
            avd_path = AVD_DIR / f"{machine_id}.avd"
            if not avd_path.is_dir():
                continue
            config = self._read_avd_config(machine_id)
            ports = self._allocate_ports({machine_id})
            metadata = {
                "id": machine_id,
                "name": self._display_name(machine_id, config),
                "device": config.get("hw.device.name", "pixel_6"),
                "cpu": max(
                    1,
                    min(
                        min(os.cpu_count() or 1, EMULATOR_MAX_CPUS),
                        int(config.get("hw.cpu.ncore", "4") or 4),
                    ),
                ),
                "ram": max(1024, parse_size_mb(config.get("hw.ramSize"), 4096)),
                "storage": parse_size_gb(config.get("disk.dataPartition.size"), 16),
                "consolePort": ports["consolePort"],
                "adbPort": ports["adbPort"],
                "vncPort": ports["vncPort"],
                "displayNumber": ports["displayNumber"],
                "consoleToken": secrets.token_urlsafe(32),
                "autostart": machine_id == "Android15",
                "createdAt": now(),
            }
            atomic_json(MACHINE_DIR / f"{machine_id}.json", metadata)
            known.add(machine_id)

    @staticmethod
    def _display_name(machine_id: str, config: dict[str, str]) -> str:
        device = DEVICE_LABELS.get(config.get("hw.device.name", ""), "Android")
        return f"{device} · {machine_id.replace('-', ' ').title()}"

    @staticmethod
    def _read_avd_config(machine_id: str) -> dict[str, str]:
        values: dict[str, str] = {}
        path = AVD_DIR / f"{machine_id}.avd" / "config.ini"
        try:
            for line in path.read_text().splitlines():
                if "=" in line and not line.lstrip().startswith("#"):
                    key, value = line.split("=", 1)
                    values[key.strip()] = value.strip()
        except OSError:
            pass
        return values

    def _write_avd_config(self, machine_id: str, values: dict[str, str]) -> None:
        path = AVD_DIR / f"{machine_id}.avd" / "config.ini"
        lines = path.read_text().splitlines() if path.exists() else []
        updated: set[str] = set()
        output: list[str] = []
        for line in lines:
            if "=" in line and not line.lstrip().startswith("#"):
                key = line.split("=", 1)[0].strip()
                if key in values:
                    output.append(f"{key}={values[key]}")
                    updated.add(key)
                    continue
            output.append(line)
        for key, value in values.items():
            if key not in updated:
                output.append(f"{key}={value}")
        atomic_write(path, "\n".join(output).rstrip() + "\n", 0o600)

    def _machine_paths(self) -> list[Path]:
        return sorted(MACHINE_DIR.glob("*.json"))

    def _load(self, machine_id: str) -> dict[str, Any]:
        if not MACHINE_RE.fullmatch(machine_id):
            raise MachineError("ID mesin tidak valid.", 404)
        data = read_json(MACHINE_DIR / f"{machine_id}.json")
        if not isinstance(data, dict):
            raise MachineError("Mesin tidak ditemukan.", 404)
        return data

    def _save(self, metadata: dict[str, Any]) -> None:
        atomic_json(MACHINE_DIR / f"{metadata['id']}.json", metadata)

    def _all_metadata(self) -> list[dict[str, Any]]:
        machines = []
        for path in self._machine_paths():
            data = read_json(path)
            if isinstance(data, dict):
                machines.append(data)
        return machines

    def _allocate_ports(self, excluded_ids: set[str] | None = None) -> dict[str, int]:
        excluded_ids = excluded_ids or set()
        machines = [m for m in self._all_metadata() if m.get("id") not in excluded_ids]
        used_console = {int(m.get("consolePort", 0)) for m in machines}
        used_vnc = {int(m.get("vncPort", 0)) for m in machines}
        used_display = {int(m.get("displayNumber", 0)) for m in machines}
        for index in range(MAX_MACHINES):
            console_port = 5554 + index * 2
            vnc_port = 5901 + index
            display_number = 100 + index
            if (
                console_port not in used_console
                and vnc_port not in used_vnc
                and display_number not in used_display
                and not port_open(console_port)
                and not port_open(vnc_port)
            ):
                return {
                    "consolePort": console_port,
                    "adbPort": console_port + 1,
                    "vncPort": vnc_port,
                    "displayNumber": display_number,
                }
        raise MachineError("Port VM yang tersedia sudah habis.", 409)

    def options(self) -> dict[str, Any]:
        available = []
        for system in SYSTEMS:
            image = system_image(system["api"])
            installed = (SDK / image.replace(";", "/")).is_dir()
            available.append(
                {
                    "id": image,
                    **system,
                    "variant": "Google Play",
                    "abi": "x86_64",
                    "installed": installed,
                }
            )
        return {
            "devices": DEVICES,
            "systems": available,
            "fingerprints": fingerprints.list_profiles(),
            "carriers": fingerprints.CARRIERS,
            "isos": self.list_isos(),
            "qemuAvailable": QEMU_SYSTEM.is_file(),
            "rootApis": [
                api for api in (35, 36) if self._ramdisk_for_api(api) is not None
            ],
            "limits": {
                "machines": MAX_MACHINES,
                "cpu": min(os.cpu_count() or 1, EMULATOR_MAX_CPUS),
                "ram": round(self._host_ram_mb() / 1024),
                "storage": 2048,
            },
        }

    def _host_ram_mb(self) -> int:
        try:
            with open("/proc/meminfo", encoding="utf-8") as handle:
                first = handle.readline().split()
                return round(int(first[1]) / 1024)
        except (OSError, ValueError, IndexError):
            return 4096

    def ensure_system_image(self, api_level: int) -> str:
        image = system_image(api_level)
        image_path = SDK / image.replace(";", "/")
        if image_path.is_dir():
            return image
        if not SDKMANAGER.exists():
            raise MachineError("SDK manager tidak ditemukan.", 500)
        with self._system_install_lock:
            if image_path.is_dir():
                return image
            try:
                result = subprocess.run(
                    [str(SDKMANAGER), image],
                    input="y\n" * 20,
                    capture_output=True,
                    text=True,
                    timeout=1800,
                    env=self._environment(),
                    check=False,
                )
            except (OSError, subprocess.TimeoutExpired) as exc:
                raise MachineError(
                    "Pemasangan system image gagal dijalankan.",
                    500,
                ) from exc
            if result.returncode != 0 or not image_path.is_dir():
                detail = (result.stderr or result.stdout).strip().splitlines()
                raise MachineError(
                    detail[-1] if detail else "System image gagal dipasang.",
                    500,
                )
        return image

    def _adb_devices(self, force: bool = False) -> dict[str, dict[str, str]]:
        current = time.monotonic()
        if not force and current - self._adb_cache[0] < 1.2:
            return self._adb_cache[1]
        try:
            result = subprocess.run(
                [str(ADB), "devices", "-l"],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
            devices: dict[str, dict[str, str]] = {}
            for line in result.stdout.splitlines()[1:]:
                fields = line.split()
                if len(fields) < 2 or fields[1] not in {"device", "offline", "unauthorized"}:
                    continue
                properties: dict[str, str] = {}
                for field in fields[2:]:
                    if ":" in field:
                        key, value = field.split(":", 1)
                        properties[key] = value
                devices[fields[0]] = {"state": fields[1], **properties}
            self._adb_cache = (current, devices)
            return devices
        except (OSError, subprocess.TimeoutExpired):
            self._adb_cache = (current, {})
            return {}

    def _boot_complete(self, serial: str, force: bool = False) -> bool:
        current = time.monotonic()
        cached = self._boot_cache.get(serial)
        if not force and cached and current - cached[0] < 1.2:
            return cached[1]
        try:
            result = subprocess.run(
                [str(ADB), "-s", serial, "shell", "getprop", "sys.boot_completed"],
                capture_output=True,
                text=True,
                timeout=3,
                check=False,
            )
            complete = result.returncode == 0 and result.stdout.strip() == "1"
        except (OSError, subprocess.TimeoutExpired):
            complete = False
        self._boot_cache[serial] = (current, complete)
        return complete

    def _runtime_for(self, machine_id: str) -> dict[str, Any]:
        with self.lock:
            return self.runtime.setdefault(
                machine_id,
                {
                    "phase": "stopped",
                    "generation": 0,
                    "emulator": None,
                    "xvfb": None,
                    "scrcpy": None,
                    "vnc": None,
                    "mirrorStarted": False,
                },
            )

    def machine_state(self, metadata: dict[str, Any]) -> dict[str, Any]:
        machine_id = metadata["id"]
        runtime = self._runtime_for(machine_id)
        emulator = runtime.get("emulator")
        devices = self._adb_devices()
        serial = self._adb_serial(metadata)
        adb = devices.get(serial)
        alive = process_alive(emulator)
        phase = runtime.get("phase", "stopped")
        if alive:
            if phase == "error":
                phase = "error"
            elif phase == "running" and adb and adb["state"] == "device":
                phase = "running"
            else:
                phase = "starting"
        elif phase not in {"stopped", "error"}:
            phase = "error"
        result = dict(metadata)
        result.update(
            {
                "backend": metadata.get("backend", "avd"),
                "isoId": metadata.get("isoId"),
                "isoName": metadata.get("isoName"),
                "identity": metadata.get("identity"),
                "vpnProfile": metadata.get("vpnProfile"),
                "vpnName": (vpn.get(metadata.get("vpnProfile")) or {}).get("name") if metadata.get("vpnProfile") else None,
                "phase": phase,
                "running": phase in {"starting", "running"},
                "ready": phase == "running",
                "adbState": adb["state"] if adb else "missing",
                "serial": serial,
                "deviceLabel": DEVICE_LABELS.get(metadata.get("device", ""), "Android"),
                "screen": self._screen_details(metadata),
                "lastLocation": metadata.get("lastLocation"),
                "androidVersion": metadata.get("androidVersion", ANDROID_VERSION),
                "apiLevel": int(metadata.get("apiLevel", API_LEVEL)),
                "root": bool(metadata.get("root", False)),
                "group": str(metadata.get("group") or "default"),
                "tags": list(metadata.get("tags") or []),
                "isTemplate": bool(metadata.get("isTemplate", False)),
                "sshUser": SSH_USER,
                "error": runtime.get("error") or self._last_errors.get(machine_id),
            }
        )
        return result

    @staticmethod
    def _screen_details(metadata: dict[str, Any]) -> dict[str, int] | None:
        if metadata.get("backend") == "iso":
            return {"width": 720, "height": 1600, "density": 320}
        profile = DEVICE_BY_ID.get(metadata.get("device", ""))
        if not profile:
            return None
        return {
            "width": int(profile["width"]),
            "height": int(profile["height"]),
            "density": int(profile["density"]),
        }

    def list_machines(self) -> dict[str, Any]:
        machines = [self.machine_state(metadata) for metadata in self._all_metadata()]
        machines.sort(key=lambda item: (not item["running"], item.get("createdAt", 0)))
        running = sum(1 for machine in machines if machine["running"])
        cpu_allocated = sum(machine["cpu"] for machine in machines if machine["running"])
        ram_allocated = sum(machine["ram"] for machine in machines if machine["running"])
        total_ram = 0
        available_ram = 0
        try:
            with open("/proc/meminfo", encoding="utf-8") as handle:
                values = {}
                for line in handle:
                    key, value = line.split(":", 1)
                    values[key] = int(value.strip().split()[0])
                total_ram = round(values.get("MemTotal", 0) / 1024 / 1024)
                available_ram = round(values.get("MemAvailable", 0) / 1024 / 1024)
        except (OSError, ValueError, IndexError):
            pass
        host_cpu = os.cpu_count() or 1
        cpu_percent = self._host_cpu_percent()
        ram_used_percent = (
            round((total_ram - available_ram) / total_ram * 100)
            if total_ram and available_ram
            else 0
        )
        disk_total_gb = 0
        disk_free_gb = 0
        disk_used_pct = 0
        try:
            usage = shutil.disk_usage(AVD_DIR if AVD_DIR.exists() else STATE_DIR)
            disk_total_gb = round(usage.total / (1024**3))
            disk_free_gb = round(usage.free / (1024**3))
            disk_used_pct = round((usage.used / usage.total) * 100) if usage.total else 0
        except OSError:
            pass
        groups = sorted({m["group"] for m in machines if m.get("group")})
        return {
            "machines": machines,
            "summary": {
                "total": len(machines),
                "running": running,
                "stopped": len(machines) - running,
                "cpuAllocated": cpu_allocated,
                "ramAllocated": ram_allocated,
                "hostCpu": host_cpu,
                "maxVmCpu": min(host_cpu, EMULATOR_MAX_CPUS),
                "hostRam": total_ram,
                "hostRamAvailable": available_ram,
                "cpuPercent": cpu_percent,
                "ramPercent": ram_used_percent,
                "diskTotalGb": disk_total_gb,
                "diskFreeGb": disk_free_gb,
                "diskPercent": disk_used_pct,
                "maxMachines": MAX_MACHINES,
                "groups": groups,
            },
        }

    def _host_cpu_percent(self) -> int:
        try:
            with open("/proc/stat", encoding="utf-8") as handle:
                values = [int(value) for value in handle.readline().split()[1:]]
            idle = values[3] + (values[4] if len(values) > 4 else 0)
            total = sum(values)
            current = time.monotonic()
            previous = self._cpu_sample
            self._cpu_sample = (current, total, idle)
            if previous and current - previous[0] >= 0.05:
                total_delta = total - previous[1]
                idle_delta = idle - previous[2]
                if total_delta > 0:
                    return max(
                        0,
                        min(100, round((total_delta - idle_delta) / total_delta * 100)),
                    )
            return round((total - idle) / total * 100) if total else 0
        except (OSError, ValueError, IndexError):
            return 0

    def get_machine(self, machine_id: str) -> dict[str, Any]:
        return self.machine_state(self._load(machine_id))

    def _validate_name(self, name: Any) -> str:
        value = str(name or "").strip()
        if not 2 <= len(value) <= 48:
            raise MachineError("Nama mesin harus terdiri dari 2–48 karakter.")
        return value

    def _unique_id(self, name: str) -> str:
        base = slugify(name)
        existing = {path.stem for path in MACHINE_DIR.glob("*.json")}
        existing.update(path.stem for path in AVD_DIR.glob("*.ini"))
        candidate = base
        counter = 2
        while candidate in existing or len(existing) >= MAX_MACHINES:
            if len(existing) >= MAX_MACHINES:
                raise MachineError("Batas internal mesin sudah tercapai.", 409)
            candidate = f"{base[:44 - len(str(counter))]}-{counter}"
            counter += 1
        if not MACHINE_RE.fullmatch(candidate):
            raise MachineError("Nama mesin tidak dapat digunakan.")
        return candidate

    def _resolve_identity_payload(
        self, payload: dict[str, Any], api_level: int, default_profile_id: str | None = None
    ) -> dict[str, Any] | None:
        raw_profile = str(payload.get("identityProfile") or "").strip()
        if not raw_profile and default_profile_id:
            raw_profile = default_profile_id
        if not raw_profile or raw_profile.lower() in {"none", "stock"}:
            return None
        if raw_profile.lower() == "random":
            fp_profile = fingerprints.pick_random_profile(api_level)
        else:
            fp_profile = fingerprints.get_profile(raw_profile)
            if not fp_profile:
                raise MachineError(f"Unknown device identity profile: {raw_profile}")
        carrier = str(payload.get("carrierId") or "telkomsel").strip()
        return fingerprints.build_identity_record(fp_profile, carrier_id=carrier, randomize_ids=True)

    def _create_iso_data_disk(self, machine_id: str, storage_gb: int) -> Path:
        vm_dir = ISO_VM_DIR / machine_id
        vm_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        data_img = vm_dir / "data.img"
        with data_img.open("wb") as handle:
            handle.truncate(int(storage_gb) * 1024 * 1024 * 1024)
        res = subprocess.run(
            [str(MKFS_EXT4), "-q", "-F", "-L", "DATA", str(data_img)],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        if res.returncode != 0:
            data_img.unlink(missing_ok=True)
            raise MachineError(f"Failed to format ISO VM data disk: {res.stderr[:200]}", 500)
        return data_img

    def _create_iso_machine(self, payload: dict[str, Any]) -> dict[str, Any]:
        if not QEMU_SYSTEM.is_file():
            raise MachineError("qemu-system-x86_64 is not installed on this host.", 500)
        name = self._validate_name(payload.get("name"))
        device = str(payload.get("device") or "pixel_6")
        profile = DEVICE_BY_ID.get(device)
        if not profile:
            raise MachineError("Selected device profile is not available.")
        iso_id = str(payload.get("isoId") or "").strip()
        isos = self.list_isos()
        iso = next((item for item in isos if item["id"] == iso_id), None)
        if not iso:
            installed_isos = [item for item in isos if item.get("installed")]
            if not iso_id and installed_isos:
                iso = installed_isos[0]
            else:
                raise MachineError("Selected Android ISO is not available.", 404)
        if not iso.get("installed"):
            raise MachineError("Download the selected Android ISO in Templates first.", 409)
        iso_path = ISO_DIR / iso["filename"]
        if not iso_path.is_file():
            raise MachineError("Android ISO file is missing on disk.", 404)
        try:
            cpu = int(payload.get("cpu", 4))
            ram = int(payload.get("ram", 4096))
            storage = int(payload.get("storage", 32))
        except (TypeError, ValueError) as exc:
            raise MachineError("Invalid CPU, RAM, or storage configuration.") from exc
        max_iso_cpu = min(os.cpu_count() or 1, 16)
        host_ram_mb = self._host_ram_mb()
        if not 1 <= cpu <= max_iso_cpu:
            raise MachineError(f"vCPU count must be between 1 and {max_iso_cpu}.")
        if not 2048 <= ram <= host_ram_mb:
            raise MachineError(f"RAM for ISO VMs must be between 2 and {round(host_ram_mb / 1024)} GB.")
        if not 4 <= storage <= 512:
            raise MachineError("Storage for ISO VMs must be between 4 and 512 GB.")
        if len(self._all_metadata()) >= MAX_MACHINES:
            raise MachineError(f"Maximum device capacity ({MAX_MACHINES}) reached.", 409)

        machine_id = self._unique_id(name)
        ports = self._allocate_ports()
        self._create_iso_data_disk(machine_id, storage)
        identity = self._resolve_identity_payload(
            payload,
            int(iso.get("apiLevel") or 33),
            default_profile_id=iso.get("recommendedProfile") or "pixel-7-pro-a13",
        )
        metadata = {
            "id": machine_id,
            "name": name,
            "backend": "iso",
            "isoId": iso["id"],
            "isoFilename": iso["filename"],
            "isoName": iso["name"],
            "device": device,
            "androidVersion": iso.get("androidVersion", "Android 13"),
            "apiLevel": int(iso.get("apiLevel") or 33),
            "systemImage": iso["filename"],
            "root": True,
            "virtWifi": bool(payload.get("virtWifi", True)),
            "identity": identity,
            "cpu": cpu,
            "ram": ram,
            "storage": storage,
            "consolePort": ports["consolePort"],
            "adbPort": ports["adbPort"],
            "vncPort": ports["vncPort"],
            "displayNumber": ports["displayNumber"],
            "consoleToken": secrets.token_urlsafe(32),
            "autostart": bool(payload.get("autostart", False)),
            "vpnProfile": self._clean_vpn(payload.get("vpnProfile")),
            "group": self._clean_group(payload.get("group")),
            "tags": self._clean_tags(payload.get("tags")),
            "isTemplate": bool(payload.get("isTemplate", False)),
            "createdAt": now(),
        }
        self._save(metadata)
        if payload.get("start", True):
            self.start_machine(machine_id)
        return self.get_machine(machine_id)

    def create_machine(self, payload: dict[str, Any]) -> dict[str, Any]:
        if str(payload.get("backend") or "avd").strip().lower() == "iso":
            return self._create_iso_machine(payload)
        name = self._validate_name(payload.get("name"))
        device = str(payload.get("device") or "pixel_6")
        profile = DEVICE_BY_ID.get(device)
        if not profile:
            raise MachineError("Profil perangkat tidak tersedia.")
        try:
            api_level = int(payload.get("api", 35))
        except (TypeError, ValueError) as exc:
            raise MachineError("Versi Android tidak valid.") from exc
        system = next((item for item in SYSTEMS if item["api"] == api_level), None)
        if not system:
            raise MachineError("Versi Android tidak didukung.")
        identity_req = str(payload.get("identityProfile") or "").strip()
        root_requested = bool(payload.get("root", False)) or (
            bool(identity_req) and identity_req.lower() not in {"none", "stock"}
        )
        if root_requested:
            # Prefer API 35 if requested and API 35 Magisk ramdisk is present; otherwise API 36.
            if api_level not in {35, 36} or self._ramdisk_for_api(api_level) is None:
                api_level = 36 if self._ramdisk_for_api(36) else 35
            system = next(item for item in SYSTEMS if item["api"] == api_level)
            selected_image = f"system-images;android-{api_level};google_apis_playstore;x86_64"
            image_path = SDK / selected_image.replace(";", "/")
            if not image_path.is_dir():
                try:
                    result = subprocess.run(
                        [str(SDKMANAGER), selected_image],
                        input="y\n" * 20,
                        capture_output=True,
                        text=True,
                        timeout=1800,
                        env=self._environment(),
                        check=False,
                    )
                except (OSError, subprocess.TimeoutExpired) as exc:
                    raise MachineError(
                        "Pemasangan image root gagal dijalankan.",
                        500,
                    ) from exc
                if result.returncode != 0 or not image_path.is_dir():
                    detail = (result.stderr or result.stdout).strip().splitlines()
                    raise MachineError(
                        detail[-1] if detail else "Image root gagal dipasang.",
                        500,
                    )
        else:
            selected_image = self.ensure_system_image(api_level)
        identity = self._resolve_identity_payload(payload, api_level)
        try:
            cpu = int(payload.get("cpu", 8))
            ram = int(payload.get("ram", 8192))
            storage = int(payload.get("storage", 64))
        except (TypeError, ValueError) as exc:
            raise MachineError("Konfigurasi CPU, RAM, atau penyimpanan tidak valid.") from exc
        host_cpu = min(os.cpu_count() or 1, EMULATOR_MAX_CPUS)
        host_ram_mb = self._host_ram_mb()
        if not 1 <= cpu <= host_cpu:
            raise MachineError(f"Jumlah vCPU harus antara 1 dan {host_cpu}.")
        if not 1024 <= ram <= host_ram_mb or ram % 512:
            raise MachineError(
                f"RAM harus antara 1 dan {round(host_ram_mb / 1024)} GB "
                "dengan kelipatan 512 MB."
            )
        if not 4 <= storage <= 2048:
            raise MachineError("Penyimpanan harus antara 4 dan 2048 GB.")
        if len(self._all_metadata()) >= MAX_MACHINES:
            raise MachineError("Batas internal mesin sudah tercapai.", 409)

        machine_id = self._unique_id(name)
        ports = self._allocate_ports()
        environment = self._environment()
        command = [
            str(AVDMANAGER),
            "create",
            "avd",
            "-n",
            machine_id,
            "-k",
            selected_image,
            "-d",
            profile["baseDevice"],
            "--force",
        ]
        try:
            result = subprocess.run(
                command,
                input="no\n",
                capture_output=True,
                text=True,
                timeout=120,
                env=environment,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise MachineError("Pembuatan VM gagal dijalankan.", 500) from exc
        if result.returncode != 0:
            detail = (result.stderr or result.stdout).strip().splitlines()
            raise MachineError(detail[-1] if detail else "AVD gagal dibuat.", 500)

        self._write_avd_config(
            machine_id,
            {
                "hw.cpu.ncore": str(cpu),
                "hw.ramSize": str(ram),
                "disk.dataPartition.size": f"{storage}G",
                "PlayStore.enabled": "yes",
                "hw.lcd.width": str(profile["width"]),
                "hw.lcd.height": str(profile["height"]),
                "hw.lcd.density": str(profile["density"]),
                "hw.lcd.depth": "32",
            },
        )
        metadata = {
            "id": machine_id,
            "name": name,
            "backend": "avd",
            "device": device,
            "androidVersion": system["label"],
            "apiLevel": api_level,
            "systemImage": selected_image,
            "root": root_requested,
            "identity": identity,
            "cpu": cpu,
            "ram": ram,
            "storage": storage,
            "consolePort": ports["consolePort"],
            "adbPort": ports["adbPort"],
            "vncPort": ports["vncPort"],
            "displayNumber": ports["displayNumber"],
            "consoleToken": secrets.token_urlsafe(32),
            "autostart": bool(payload.get("autostart", False)),
            "vpnProfile": self._clean_vpn(payload.get("vpnProfile")),
            "group": self._clean_group(payload.get("group")),
            "tags": self._clean_tags(payload.get("tags")),
            "isTemplate": bool(payload.get("isTemplate", False)),
            "createdAt": now(),
        }
        self._save(metadata)
        if payload.get("start", True):
            self.start_machine(machine_id)
        return self.get_machine(machine_id)

    def _environment(self) -> dict[str, str]:
        environment = os.environ.copy()
        environment.update(
            {
                "HOME": str(APP_HOME),
                "ANDROID_HOME": str(SDK_ROOT),
                "ANDROID_SDK_ROOT": str(SDK_ROOT),
                "ANDROID_AVD_HOME": str(AVD_DIR),
            }
        )
        return environment

    def _log_handle(self, machine_id: str, name: str):
        path = LOG_DIR / f"{machine_id}-{name}.log"
        handle = path.open("a", encoding="utf-8")
        handle.write(f"\n--- {time.strftime('%Y-%m-%d %H:%M:%S')} ---\n")
        handle.flush()
        return handle

    def _spawn(self, machine_id: str, name: str, command: list[str], env: dict[str, str]):
        profile = self._vpn_profile_for(machine_id) if name == "emulator" else None
        if profile:
            try:
                vpn.ensure_enabled(profile)
            except vpn.VpnError as exc:
                raise MachineError(f"VPN '{profile['name']}' is unavailable: {exc}", exc.status) from exc
        handle = self._log_handle(machine_id, name)
        try:
            process = subprocess.Popen(
                command,
                stdin=subprocess.DEVNULL,
                stdout=handle,
                stderr=subprocess.STDOUT,
                env=env,
                start_new_session=True,
                close_fds=True,
            )
        finally:
            handle.close()
        if profile:
            # Fail closed: a device set to use a VPN never starts on the host's own IP.
            try:
                vpn.assign(process.pid, profile)
            except vpn.VpnError as exc:
                try:
                    process.kill()
                except OSError:
                    pass
                raise MachineError(f"Could not route device through VPN: {exc}", exc.status) from exc
        return process

    def _vpn_profile_for(self, machine_id: str) -> dict[str, Any] | None:
        try:
            profile_id = self._load(machine_id).get("vpnProfile")
        except MachineError:
            return None
        if not profile_id:
            return None
        profile = vpn.get(profile_id)
        if not profile:
            raise MachineError("The VPN profile assigned to this device no longer exists.", 409)
        return profile

    def set_vpn(self, machine_id: str, profile_id: str | None) -> dict[str, Any]:
        metadata = self._load(machine_id)
        profile = vpn.get(profile_id) if profile_id else None
        if profile_id and not profile:
            raise MachineError("VPN profile not found.", 404)
        previous = metadata.get("vpnProfile")
        metadata["vpnProfile"] = profile["id"] if profile else None
        self._save(metadata)
        state = self.machine_state(metadata)
        emulator = self._runtime_for(machine_id).get("emulator")
        if process_alive(emulator) and previous != metadata["vpnProfile"]:
            # Move the running process right away (covers QEMU/ISO devices fully).
            # AVD Wi-Fi is relayed by netsimd until the emulator is relaunched with
            # the VPN launch options, and DNS servers are fixed at boot, so the
            # switch is only complete after a restart.
            try:
                if profile:
                    vpn.ensure_enabled(profile)
                vpn.assign(emulator.pid, profile)
            except vpn.VpnError as exc:
                raise MachineError(f"Saved, but live switch failed: {exc}", exc.status) from exc
            state["vpnRestartRequired"] = bool(profile) and metadata.get("backend", "avd") != "iso"
            state["vpnRestartRecommended"] = True
        return state

    def _clean_vpn(self, profile_id: Any) -> str | None:
        if not profile_id:
            return None
        profile = vpn.get(str(profile_id))
        if not profile:
            raise MachineError("VPN profile not found.", 404)
        return profile["id"]

    def machines_using_vpn(self, profile_id: str) -> list[str]:
        return [m["id"] for m in self._all_metadata() if m.get("vpnProfile") == profile_id]

    def _ensure_display(self, machine_id: str, metadata: dict[str, Any]) -> None:
        runtime = self._runtime_for(machine_id)
        display = metadata["displayNumber"]
        vnc_port = metadata["vncPort"]
        environment = self._environment()
        if not process_alive(runtime.get("xvfb")):
            runtime["xvfb"] = self._spawn(
                machine_id,
                "display",
                [
                    str(XVFB),
                    f":{display}",
                    "-screen",
                    "0",
                    "540x1200x24",
                    "-nolisten",
                    "tcp",
                    "-noreset",
                    "-ac",
                ],
                environment,
            )
        if not process_alive(runtime.get("vnc")):
            runtime["vnc"] = self._spawn(
                machine_id,
                "vnc",
                [
                    str(X11VNC),
                    "-display",
                    f":{display}",
                    "-localhost",
                    "-forever",
                    "-shared",
                    "-noxdamage",
                    # Low-latency polling: never "nap" when idle (first touch after
                    # a pause felt very laggy) and shorten defer/poll intervals.
                    "-nonap",
                    "-wait",
                    "8",
                    "-defer",
                    "8",
                    "-rfbport",
                    str(vnc_port),
                    "-nopw",
                    "-quiet",
                ],
                environment,
            )
        self._write_tokens()

    def _ensure_mirror(self, machine_id: str, metadata: dict[str, Any]) -> None:
        if metadata.get("backend") == "iso":
            return
        runtime = self._runtime_for(machine_id)
        if process_alive(runtime.get("scrcpy")):
            return
        environment = self._environment()
        environment["DISPLAY"] = f":{metadata['displayNumber']}"
        perf = platform_settings.load()["performance"]
        runtime["scrcpy"] = self._spawn(
            machine_id,
            "mirror",
            [
                str(SCRCPY),
                "-s",
                self._adb_serial(metadata),
                "--no-audio",
                "--stay-awake",
                f"--max-size={perf['streamMaxSize']}",
                f"--max-fps={perf['streamFps']}",
                f"--video-bit-rate={perf['streamBitrateMbps']}M",
                "--window-x=0",
                "--window-y=0",
                "--window-width=540",
                "--window-height=1200",
                "--window-borderless",
            ],
            environment,
        )
        runtime["mirrorStarted"] = True

    def _extract_iso_boot_files(self, iso_path: Path) -> tuple[Path, Path]:
        slug = slugify(iso_path.stem)
        cache_dir = ISO_CACHE_DIR / slug
        kernel = cache_dir / "kernel"
        initrd = cache_dir / "initrd.img"
        if kernel.is_file() and initrd.is_file():
            return kernel, initrd
        if not XORRISO.is_file():
            raise MachineError("xorriso is required to extract ISO boot files.", 500)
        cache_dir.mkdir(parents=True, exist_ok=True)
        res = subprocess.run(
            [
                str(XORRISO),
                "-osirrox",
                "on",
                "-indev",
                str(iso_path),
                "-extract",
                "/kernel",
                str(kernel),
                "-extract",
                "/initrd.img",
                str(initrd),
            ],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        if not (kernel.is_file() and initrd.is_file()):
            raise MachineError(
                f"Failed to extract kernel/initrd from ISO: {(res.stderr or res.stdout)[:200]}",
                500,
            )
        return kernel, initrd

    @staticmethod
    def _cpio_newc(files: dict[str, str]) -> bytes:
        """Minimal SVR4 'newc' cpio archive (dirs + regular files), as used by initramfs."""
        out = bytearray()
        ino = 1

        def entry(name: str, mode: int, data: bytes) -> None:
            nonlocal ino
            raw_name = name.encode("utf-8") + b"\0"
            fields = (ino, mode, 0, 0, 1, 0, len(data), 0, 0, 0, 0, len(raw_name), 0)
            out.extend(b"070701" + "".join(f"{v:08X}" for v in fields).encode("ascii"))
            out.extend(raw_name)
            out.extend(b"\0" * (-len(out) % 4))
            out.extend(data)
            out.extend(b"\0" * (-len(out) % 4))
            ino += 1

        dirs = sorted({str(Path(n).parent) for n in files if str(Path(n).parent) != "."})
        for d in dirs:
            entry(d, 0o040755, b"")
        for name in sorted(files):
            entry(name, 0o100755 if name.startswith("scripts/") else 0o100644, files[name].encode("utf-8"))
        entry("TRAILER!!!", 0, b"")
        out.extend(b"\0" * (-len(out) % 512))
        return bytes(out)

    def _iso_boot_initrd(self, initrd: Path) -> Path:
        """Original BlissOS initrd + appended Droid Cloud initramfs overlay (boot hooks).

        The kernel unpacks concatenated initramfs archives in order, so the stock
        initrd stays byte-identical and only ``scripts/99-droidcloud`` is added.
        """
        import gzip

        overlay = gzip.compress(self._cpio_newc(fingerprints.build_iso_boot_hook()), mtime=0)
        digest = hashlib.sha256(overlay).hexdigest()[:12]
        target = initrd.with_name(f"initrd-droidcloud-{digest}.img")
        if not target.is_file():
            for stale in initrd.parent.glob("initrd-droidcloud-*.img"):
                stale.unlink(missing_ok=True)
            tmp = target.with_suffix(".tmp")
            tmp.write_bytes(initrd.read_bytes() + overlay)
            tmp.replace(target)
        return target

    def _install_avd_identity_module(
        self, serial: str, identity: dict[str, Any], apply_live: bool = True
    ) -> None:
        mod_dir = "/data/adb/modules/droidcloud_identity"
        files = fingerprints.build_magisk_module_files(identity)
        digest = hashlib.sha256(files["system.prop"].encode("utf-8")).hexdigest()[:16]
        subprocess.run(
            [str(ADB), "-s", serial, "shell", f"su -c 'mkdir -p {mod_dir} /data/adb/magisk'"],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        # Ensure Magisk has real x86_64 static busybox ELF binary so post-fs-data.sh runs before Zygote
        check_bb = subprocess.run(
            [
                str(ADB), "-s", serial, "shell",
                "su -c 'sz=$(wc -c < /data/adb/magisk/busybox 2>/dev/null || echo 0); [ \"$sz\" -gt 100000 ] && [ -x /data/adb/magisk/busybox ] && echo OK'",
            ],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        if "OK" not in (check_bb.stdout or "") and BUSYBOX_BIN.is_file():
            subprocess.run(
                [str(ADB), "-s", serial, "push", str(BUSYBOX_BIN), "/data/local/tmp/dc_bb"],
                capture_output=True,
                timeout=15,
                check=False,
            )
            subprocess.run(
                [
                    str(ADB), "-s", serial, "shell",
                    "su -c 'mv /data/local/tmp/dc_bb /data/adb/magisk/busybox && chown 0:0 /data/adb/magisk/busybox && chmod 755 /data/adb/magisk/busybox && chcon u:object_r:system_file:s0 /data/adb/magisk/busybox 2>/dev/null || true'",
                ],
                capture_output=True,
                timeout=10,
                check=False,
            )

        for fname, content in files.items():
            with tempfile.NamedTemporaryFile("w", suffix=f"_{fname}", delete=False, encoding="utf-8") as tmp:
                tmp.write(content)
                tmp.flush()
                local_path = Path(tmp.name)
            try:
                subprocess.run(
                    [str(ADB), "-s", serial, "push", str(local_path), f"/data/local/tmp/dc_{fname}"],
                    capture_output=True,
                    timeout=15,
                    check=False,
                )
                mode = "755" if fname.endswith(".sh") else "644"
                subprocess.run(
                    [
                        str(ADB), "-s", serial, "shell",
                        f"su -c 'mv /data/local/tmp/dc_{fname} {mod_dir}/{fname} && chown 0:0 {mod_dir}/{fname} && chmod {mode} {mod_dir}/{fname} && chcon u:object_r:system_file:s0 {mod_dir}/{fname} 2>/dev/null || true'",
                    ],
                    capture_output=True,
                    timeout=15,
                    check=False,
                )
            finally:
                local_path.unlink(missing_ok=True)

        subprocess.run(
            [str(ADB), "-s", serial, "shell", f"su -c 'echo {digest} > {mod_dir}/.hash'"],
            capture_output=True,
            timeout=10,
            check=False,
        )
        if apply_live:
            subprocess.run(
                [
                    str(ADB), "-s", serial, "shell",
                    f"su -c 'sh {mod_dir}/post-fs-data.sh; sh {mod_dir}/service.sh'",
                ],
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )

    def _run_adb_safe(
        self, args: list[str], timeout: float = 8.0
    ) -> subprocess.CompletedProcess[str] | None:
        try:
            return subprocess.run(
                [str(ADB), *args],
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            return None

    def _ensure_iso_adb_root(self, serial: str) -> bool:
        check = self._run_adb_safe(["-s", serial, "shell", "id"], timeout=6)
        if check and check.returncode == 0 and "uid=0(" in (check.stdout or ""):
            return True
        self._run_adb_safe(["-s", serial, "root"], timeout=10)
        time.sleep(2.5)
        self._run_adb_safe(["disconnect", serial], timeout=5)
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            time.sleep(1.5)
            conn = self._run_adb_safe(["connect", serial], timeout=5)
            if conn is None or "unable" in (conn.stdout or "").lower():
                self._run_adb_safe(["disconnect", serial], timeout=4)
                continue
            res = self._run_adb_safe(["-s", serial, "shell", "id"], timeout=5)
            if res and res.returncode == 0 and "uid=0(" in (res.stdout or ""):
                return True
            if res is None or "offline" in (res.stderr or "").lower():
                self._run_adb_safe(["disconnect", serial], timeout=4)
        return False

    def _ensure_iso_ksu_integrity_stack(self, serial: str, identity: dict[str, Any]) -> None:
        """Bootstrap KernelSU + ReZygisk + PlayIntegrityFork v17 + TrickyStore x64 + UnPPU on BlissOS."""
        probe = self._run_adb_safe(
            [
                "-s", serial, "shell",
                "KSUD=$(find /data/app -name libksud.so 2>/dev/null | head -n 1); "
                "echo \"KSUD=$KSUD\"; "
                "[ -x /data/adb/ksud ] && echo HAS_KSUD=1; "
                "mountpoint -q /data/adb/modules 2>/dev/null && echo MOUNTED=1; "
                "pidof zygisk-ptrace64 >/dev/null 2>&1 && echo ZYGISK_UP=1; "
                "ps -A 2>/dev/null | grep -q TrickyStore && echo TRICKY_UP=1",
            ],
            timeout=10,
        )
        out = probe.stdout if probe else ""
        ksud_apk = ""
        for line in out.splitlines():
            if line.startswith("KSUD="):
                ksud_apk = line.split("=", 1)[1].strip()
        if not ksud_apk and "HAS_KSUD=1" not in out:
            return

        if "HAS_KSUD=1" not in out and ksud_apk:
            self._run_adb_safe(
                [
                    "-s", serial, "shell",
                    "mkdir -p /data/adb/ksu/bin /data/adb/modules /data/adb/post-fs-data.d /data/adb/service.d",
                ],
                timeout=10,
            )
            if BUSYBOX_BIN.is_file():
                self._run_adb_safe(["-s", serial, "push", str(BUSYBOX_BIN), "/data/adb/ksu/bin/busybox"], timeout=15)
                self._run_adb_safe(["-s", serial, "shell", "chmod 755 /data/adb/ksu/bin/busybox"], timeout=6)
            if RESETPROP_BIN.is_file():
                self._run_adb_safe(["-s", serial, "push", str(RESETPROP_BIN), "/data/adb/ksu/bin/resetprop"], timeout=15)
                self._run_adb_safe(["-s", serial, "shell", "chmod 755 /data/adb/ksu/bin/resetprop"], timeout=6)
            # Run ksud install from APK path (not /data/adb/ksud) to avoid ETXTBSY
            self._run_adb_safe(["-s", serial, "shell", f"{shlex.quote(ksud_apk)} install 2>/dev/null || true"], timeout=15)

        # Mount KernelSU modules first if ksud post-fs-data did not run at boot. With the
        # Droid Cloud initrd boot hook it runs natively pre-Zygote (ReZygisk already up),
        # so re-running it (and restarting Zygote) would only duplicate mounts.
        need_zygote_restart = False
        if "MOUNTED=1" not in out and "ZYGISK_UP=1" not in out:
            self._run_adb_safe(
                ["-s", serial, "shell", "/data/adb/ksud post-fs-data </dev/null >/data/local/tmp/ksu_pfd.log 2>&1"],
                timeout=20,
            )
            need_zygote_restart = True

        unppu_size = UNPPU_SO.stat().st_size if UNPPU_SO.is_file() else 0
        ril_size = RIL_SO.stat().st_size if RIL_SO.is_file() else 0
        bt_size = BT_BIN.stat().st_size if BT_BIN.is_file() else 0
        mod_probe = self._run_adb_safe(
            [
                "-s", serial, "shell",
                "[ -f /data/adb/modules/rezygisk/post-fs-data.sh ] && echo HAS_REZYGISK=1; "
                "[ -f /data/adb/modules/playintegrityfix/autopif4.sh ] && echo HAS_PIF=1; "
                "[ -f /data/adb/modules/tricky_store/libtricky_store.so ] && echo HAS_TRICKY=1; "
                f"[ \"$(stat -c %s /data/adb/modules/droidcloud_unppu/zygisk/x86_64.so 2>/dev/null)\" = \"{unppu_size}\" ] && echo HAS_UNPPU=1; "
                f"[ \"$(stat -c %s /data/adb/ksu/lib/libdroidcloud_ril.so 2>/dev/null)\" = \"{ril_size}\" ] && echo HAS_RIL=1; "
                f"[ \"$(stat -c %s /data/adb/ksu/bin/droidcloud_bt 2>/dev/null)\" = \"{bt_size}\" ] && echo HAS_BT=1; "
                "[ -s /data/adb/droidcloud_telephony.xml ] && [ ! -d /data/adb/modules/droidcloud_phone_features ] && echo HAS_PHONE_FEATURES=1; "
                "[ -s /data/adb/tricky_store/target.txt ] && echo HAS_TRICKY_TARGET=1",
            ],
            timeout=10,
        )
        mout = mod_probe.stdout if mod_probe else ""
        installed_new = False

        if "HAS_REZYGISK=1" not in mout and REZYGISK_ZIP.is_file():
            self._run_adb_safe(["-s", serial, "push", str(REZYGISK_ZIP), "/data/local/tmp/rezygisk.zip"], timeout=20)
            self._run_adb_safe(
                ["-s", serial, "shell", "/data/adb/ksud module install /data/local/tmp/rezygisk.zip && rm -f /data/local/tmp/rezygisk.zip"],
                timeout=45,
            )
            installed_new = True

        if "HAS_PIF=1" not in mout and PIFORK_ZIP.is_file():
            self._run_adb_safe(["-s", serial, "push", str(PIFORK_ZIP), "/data/local/tmp/pifork.zip"], timeout=20)
            self._run_adb_safe(
                ["-s", serial, "shell", "/data/adb/ksud module install /data/local/tmp/pifork.zip && rm -f /data/local/tmp/pifork.zip"],
                timeout=45,
            )
            installed_new = True

        if "HAS_TRICKY=1" not in mout and TRICKYSTORE_ZIP.is_file():
            self._run_adb_safe(["-s", serial, "push", str(TRICKYSTORE_ZIP), "/data/local/tmp/trickystore.zip"], timeout=25)
            self._run_adb_safe(
                ["-s", serial, "shell", "/data/adb/ksud module install /data/local/tmp/trickystore.zip && rm -f /data/local/tmp/trickystore.zip"],
                timeout=45,
            )
            installed_new = True

        if installed_new:
            self._run_adb_safe(
                ["-s", serial, "shell", "/data/adb/ksud post-fs-data </dev/null >/data/local/tmp/ksu_pfd.log 2>&1"],
                timeout=20,
            )
            need_zygote_restart = True

        if "HAS_UNPPU=1" not in mout and UNPPU_SO.is_file():
            self._run_adb_safe(
                [
                    "-s", serial, "shell",
                    "mkdir -p /data/adb/modules/droidcloud_unppu/zygisk && "
                    "printf 'id=droidcloud_unppu\\nname=Droid Cloud BlissOS Attestation Unblocker\\n"
                    "version=v1.0\\nversionCode=100\\nauthor=Droid Cloud\\n"
                    "description=Unblocks BlissOS PixelPropsUtils key attestation for TrickyStore\\n' "
                    "> /data/adb/modules/droidcloud_unppu/module.prop",
                ],
                timeout=8,
            )
            self._run_adb_safe(["-s", serial, "push", str(UNPPU_SO), "/data/adb/modules/droidcloud_unppu/zygisk/x86_64.so"], timeout=12)
            self._run_adb_safe(["-s", serial, "shell", "chmod 755 /data/adb/modules/droidcloud_unppu/zygisk/x86_64.so"], timeout=6)
            need_zygote_restart = True

        if "HAS_PHONE_FEATURES=1" not in mout:
            with tempfile.NamedTemporaryFile("w", suffix=".xml", delete=False, encoding="utf-8") as tmp:
                tmp.write(fingerprints.ISO_TELEPHONY_FEATURES_XML)
                tmp.flush()
                local_xml = Path(tmp.name)
            try:
                # Never leave a KernelSU /system overlay module on BlissOS (it hides initrd
                # /system/vendor bind mounts and blanks SurfaceFlinger); 05_droidcloud_platform.sh
                # bind-mounts only /system/etc/permissions from /data/adb/droidcloud_telephony.xml.
                self._run_adb_safe(
                    ["-s", serial, "shell", "rm -rf /data/adb/modules/droidcloud_phone_features"],
                    timeout=8,
                )
                self._run_adb_safe(
                    ["-s", serial, "push", str(local_xml), "/data/adb/droidcloud_telephony.xml"],
                    timeout=12,
                )
                self._run_adb_safe(
                    ["-s", serial, "shell", "chmod 644 /data/adb/droidcloud_telephony.xml"],
                    timeout=6,
                )
            finally:
                local_xml.unlink(missing_ok=True)

        if "HAS_RIL=1" not in mout and RIL_SO.is_file():
            self._run_adb_safe(["-s", serial, "shell", "mkdir -p /data/adb/ksu/lib"], timeout=6)
            self._run_adb_safe(["-s", serial, "push", str(RIL_SO), "/data/adb/ksu/lib/libdroidcloud_ril.so"], timeout=15)
            self._run_adb_safe(["-s", serial, "shell", "chmod 755 /data/adb/ksu/lib/libdroidcloud_ril.so"], timeout=6)

        if "HAS_BT=1" not in mout and BT_BIN.is_file():
            self._run_adb_safe(["-s", serial, "shell", "mkdir -p /data/adb/ksu/bin"], timeout=6)
            self._run_adb_safe(["-s", serial, "push", str(BT_BIN), "/data/adb/ksu/bin/droidcloud_bt"], timeout=15)
            self._run_adb_safe(["-s", serial, "shell", "chmod 755 /data/adb/ksu/bin/droidcloud_bt"], timeout=6)

        if "HAS_TRICKY_TARGET=1" not in mout:
            self._run_adb_safe(
                [
                    "-s", serial, "shell",
                    "mkdir -p /data/adb/tricky_store && "
                    "printf 'com.google.android.gms!\\ncom.google.android.gsf!\\ncom.android.vending!\\n' "
                    "> /data/adb/tricky_store/target.txt && chmod 644 /data/adb/tricky_store/target.txt",
                ],
                timeout=8,
            )

        # Always keep custom.pif.prop synced to the VM's selected identity (never run autopif4.sh,
        # which overwrote the user's chosen phone with a random Pixel 6 Pro "raven" canary build).
        if "HAS_PIF=1" in mout or installed_new:
            pif_content = fingerprints.build_custom_pif_prop(identity)
            with tempfile.NamedTemporaryFile("w", suffix=".prop", delete=False, encoding="utf-8") as tmp:
                tmp.write(pif_content)
                tmp.flush()
                local_prop = Path(tmp.name)
            try:
                self._run_adb_safe(
                    ["-s", serial, "push", str(local_prop), "/data/adb/modules/playintegrityfix/custom.pif.prop"],
                    timeout=12,
                )
                self._run_adb_safe(
                    ["-s", serial, "shell", "chmod 644 /data/adb/modules/playintegrityfix/custom.pif.prop"],
                    timeout=6,
                )
            finally:
                local_prop.unlink(missing_ok=True)

        if "TRICKY_UP=1" not in out or need_zygote_restart:
            self._run_adb_safe(
                ["-s", serial, "shell", "/data/adb/ksud services </dev/null >/data/local/tmp/ksu_svc.log 2>&1"],
                timeout=15,
            )
            time.sleep(2.0)

        if need_zygote_restart or "ZYGISK_UP=1" not in out:
            # Attach ReZygisk + PIF + UnPPU to Zygote once per cold boot
            self._run_adb_safe(["-s", serial, "shell", "setprop ctl.restart zygote"], timeout=8)
            time.sleep(12.0)
            self._run_adb_safe(["disconnect", serial], timeout=4)
            deadline = time.monotonic() + 35
            while time.monotonic() < deadline:
                time.sleep(1.5)
                conn = self._run_adb_safe(["connect", serial], timeout=5)
                if conn is None:
                    self._run_adb_safe(["disconnect", serial], timeout=4)
                    continue
                if self._boot_complete(serial, force=True):
                    self._ensure_iso_adb_root(serial)
                    break

    def _authorize_iso_adb_key(self, serial: str) -> None:
        """Trust the host ADB key on the guest.

        The identity sets ro.adb.secure=1 like a retail phone; since it is now applied
        pre-adbd at post-fs-data, adbd would otherwise wait for an RSA confirmation.
        """
        key_file = Path(os.environ.get("ANDROID_USER_HOME", APP_HOME / ".android")) / "adbkey"
        if not key_file.is_file():
            return
        res = self._run_adb_safe(["pubkey", str(key_file)], timeout=8)
        pubkey = (res.stdout or "").strip() if res and res.returncode == 0 else ""
        if not pubkey.startswith("QAAAA"):
            return
        q = shlex.quote
        self._run_adb_safe(
            [
                "-s", serial, "shell",
                "mkdir -p /data/misc/adb && touch /data/misc/adb/adb_keys && "
                f"(grep -qF {q(pubkey.split()[0])} /data/misc/adb/adb_keys || echo {q(pubkey)} >> /data/misc/adb/adb_keys) && "
                "chown system:shell /data/misc/adb/adb_keys && chmod 640 /data/misc/adb/adb_keys && "
                "restorecon /data/misc/adb/adb_keys 2>/dev/null; true",
            ],
            timeout=8,
        )

    def _inject_iso_identity(
        self, serial: str, identity: dict[str, Any], randomize_ids: bool = False
    ) -> None:
        self._ensure_iso_adb_root(serial)
        self._authorize_iso_adb_key(serial)
        self._ensure_iso_ksu_integrity_stack(serial, identity)
        if RESETPROP_BIN.is_file():
            self._run_adb_safe(
                ["-s", serial, "push", str(RESETPROP_BIN), "/data/local/tmp/resetprop"],
                timeout=15,
            )
            rp_bin = "/data/local/tmp/resetprop"
            lines = [
                "chmod 755 /data/local/tmp/resetprop",
                "chmod 640 /sys/fs/selinux/enforce 2>/dev/null || true",
                "chmod 440 /sys/fs/selinux/policy 2>/dev/null || true",
            ] + fingerprints.build_resetprop_lines(
                identity, resetprop_bin=rp_bin, is_emulator=False
            )
        else:
            lines = []
        q = shlex.quote
        bt_mac = fingerprints.generate_oem_mac(identity["brand"], f"bt:{identity['serial']}")
        lines.extend(
            [
                f"settings put global device_name {q(identity['marketname'])} 2>/dev/null || true",
                f"settings put secure bluetooth_name {q(identity['marketname'])} 2>/dev/null || true",
                f"settings put secure bluetooth_address {q(bt_mac)} 2>/dev/null || true",
                *fingerprints.ISO_BATTERY_LINES,
                "wm dismiss-keyguard 2>/dev/null || true",
                "am start -a android.intent.action.MAIN -c android.intent.category.HOME >/dev/null 2>&1 || true",
            ]
        )
        if randomize_ids:
            lines.append(f"settings put secure android_id {q(identity['androidId'])} 2>/dev/null || true")

        # Persist virtual GSM/LTE modem config (IMEI/SIM/carrier/baseband) for libdroidcloud_ril.so
        modem_conf = fingerprints.build_iso_modem_conf(identity)
        with tempfile.NamedTemporaryFile("w", suffix=".conf", delete=False, encoding="utf-8") as tmp:
            tmp.write(modem_conf)
            tmp.flush()
            local_modem = Path(tmp.name)
        try:
            self._run_adb_safe(["-s", serial, "push", str(local_modem), "/data/adb/droidcloud_modem.conf"], timeout=12)
            self._run_adb_safe(
                [
                    "-s", serial, "shell",
                    "chmod 644 /data/adb/droidcloud_modem.conf && "
                    "cp -f /data/adb/droidcloud_modem.conf /dev/dc_modem.conf && chmod 644 /dev/dc_modem.conf",
                ],
                timeout=6,
            )
        finally:
            local_modem.unlink(missing_ok=True)

        # Persist pre-Zygote KernelSU boot scripts so Java android.os.Build.* matches on every boot
        ksu_scripts = fingerprints.build_ksu_iso_scripts(identity)
        self._run_adb_safe(
            ["-s", serial, "shell", "mkdir -p /data/adb/post-fs-data.d /data/adb/service.d"],
            timeout=8,
        )
        net_scripts = fingerprints.build_iso_platform_scripts()
        for content, target_path in (
            (ksu_scripts["post-fs-data.sh"], "/data/adb/post-fs-data.d/10_droidcloud_identity.sh"),
            (ksu_scripts["service.sh"], "/data/adb/service.d/10_droidcloud_identity.sh"),
            (net_scripts["post-fs-data.sh"], "/data/adb/post-fs-data.d/05_droidcloud_platform.sh"),
            (net_scripts["service.sh"], "/data/adb/service.d/05_droidcloud_platform.sh"),
        ):
            with tempfile.NamedTemporaryFile("w", suffix=".sh", delete=False, encoding="utf-8") as tmp:
                tmp.write(content)
                tmp.flush()
                local_ksu = Path(tmp.name)
            try:
                self._run_adb_safe(["-s", serial, "push", str(local_ksu), target_path], timeout=12)
                self._run_adb_safe(["-s", serial, "shell", f"chmod 755 {target_path}"], timeout=6)
            finally:
                local_ksu.unlink(missing_ok=True)

        script = "\n".join(lines) + "\n"
        with tempfile.NamedTemporaryFile("w", suffix=".sh", delete=False, encoding="utf-8") as tmp:
            tmp.write(script)
            tmp.flush()
            local_script = Path(tmp.name)
        try:
            self._run_adb_safe(
                ["-s", serial, "push", str(local_script), "/data/local/tmp/dc_iso_id.sh"],
                timeout=15,
            )
            self._run_adb_safe(
                ["-s", serial, "shell", "sh /data/local/tmp/dc_iso_id.sh"],
                timeout=30,
            )
        finally:
            local_script.unlink(missing_ok=True)

    def _enable_root(self, serial: str) -> None:
        try:
            root_result = subprocess.run(
                [str(ADB), "-s", serial, "root"],
                capture_output=True,
                text=True,
                timeout=15,
                check=False,
            )
            time.sleep(2)
            identity = subprocess.run(
                [str(ADB), "-s", serial, "shell", "id"],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise MachineError(f"Root gagal diverifikasi: {exc}") from exc
        if root_result.returncode == 0 and "uid=0(" in identity.stdout:
            return
        try:
            magisk_identity = subprocess.run(
                [str(ADB), "-s", serial, "shell", "su", "-c", "id"],
                capture_output=True,
                text=True,
                timeout=15,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise MachineError(f"Root Magisk gagal diverifikasi: {exc}") from exc
        if magisk_identity.returncode == 0 and "uid=0(" in magisk_identity.stdout:
            return
        detail = (
            magisk_identity.stderr
            or magisk_identity.stdout
            or root_result.stderr
            or root_result.stdout
        ).strip()
        if "permission denied" in detail.lower():
            raise MachineError(
                "Akses root Magisk belum disetujui untuk shell ADB.",
                409,
            )
        if detail:
            raise MachineError(
                detail,
                409,
            )
        raise MachineError("Image ini tidak mendukung akses root.", 500)

    def _watch_boot(self, machine_id: str, generation: int) -> None:
        metadata = self._load(machine_id)
        is_iso = metadata.get("backend") == "iso"
        serial = self._adb_serial(metadata)
        if is_iso:
            self._run_adb_safe(["disconnect", serial], timeout=4)
            # Let Linux kernel + initrd mount the ISO before probing TCP ADB over QEMU slirp
            for _ in range(12):
                runtime = self._runtime_for(machine_id)
                if runtime.get("generation") != generation:
                    return
                if not process_alive(runtime.get("emulator")):
                    self._mark_error(machine_id, "VM process exited during boot.")
                    return
                time.sleep(1.0)
        deadline = time.monotonic() + 240
        while time.monotonic() < deadline:
            runtime = self._runtime_for(machine_id)
            if runtime.get("generation") != generation:
                return
            if not process_alive(runtime.get("emulator")):
                self._mark_error(machine_id, "VM process exited during boot.")
                return
            devices = self._adb_devices(force=True)
            adb = devices.get(serial)
            if is_iso and (not adb or adb["state"] != "device"):
                if adb and adb["state"] == "offline":
                    self._run_adb_safe(["disconnect", serial], timeout=4)
                    time.sleep(0.5)
                conn = self._run_adb_safe(["connect", serial], timeout=5)
                if conn is None:
                    self._run_adb_safe(["disconnect", serial], timeout=4)
                    time.sleep(2.5)
                    continue
                devices = self._adb_devices(force=True)
                adb = devices.get(serial)
            if adb and adb["state"] == "device":
                if self._boot_complete(serial, force=True):
                    if is_iso:
                        try:
                            # Allow BlissOS bootcomplete init.sh to settle before elevating adbd
                            time.sleep(3.0)
                            self._ensure_iso_adb_root(serial)
                            setup_cmd = (
                                "wm size reset 2>/dev/null || true; "
                                "wm density reset 2>/dev/null || true; "
                                "settings put global device_provisioned 1 2>/dev/null || true; "
                                "settings put secure user_setup_complete 1 2>/dev/null || true; "
                                "settings put system screen_off_timeout 2147483647 2>/dev/null || true; "
                                "settings put global stay_on_while_plugged_in 7 2>/dev/null || true; "
                                "settings put secure doze_enabled 0 2>/dev/null || true; "
                                "dumpsys deviceidle disable >/dev/null 2>&1 || true; "
                                "svc power stayon true 2>/dev/null || true; "
                                "input keyevent KEYCODE_WAKEUP 2>/dev/null || true; "
                                "pm disable-user --user 0 com.farmerbb.taskbar 2>/dev/null || true; "
                                "pm disable-user --user 0 com.farmerbb.taskbar.support 2>/dev/null || true; "
                                "pm disable-user --user 0 cu.axel.smartdock 2>/dev/null || true; "
                                "wm dismiss-keyguard 2>/dev/null || true; "
                                "am start -a android.intent.action.MAIN -c android.intent.category.HOME >/dev/null 2>&1 || true"
                            )
                            self._run_adb_safe(["-s", serial, "shell", setup_cmd], timeout=20)
                            if metadata.get("identity"):
                                self._inject_iso_identity(serial, metadata["identity"])
                            # Re-register with Google (checkin) so the spoofed Pixel identity is
                            # evaluated for Play Protect certification / Play Integrity on each boot.
                            self._run_adb_safe(
                                [
                                    "-s", serial, "shell",
                                    "am broadcast -a android.server.checkin.CHECKIN com.google.android.gms >/dev/null 2>&1 || true",
                                ],
                                timeout=15,
                            )
                        except Exception as exc:
                            with self._log_handle(machine_id, "mirror") as logf:
                                logf.write(f"ISO post-boot setup warning: {exc}\n")
                    else:
                        if metadata.get("root"):
                            try:
                                self._enable_root(serial)
                            except MachineError as exc:
                                self._mark_error(machine_id, exc.message)
                                return
                            if metadata.get("identity"):
                                try:
                                    expected = hashlib.sha256(
                                        fingerprints.build_magisk_module_files(metadata["identity"])["system.prop"].encode("utf-8")
                                    ).hexdigest()[:16]
                                    cur_hash = subprocess.run(
                                        [
                                            str(ADB), "-s", serial, "shell",
                                            "su -c 'sz=$(wc -c < /data/adb/magisk/busybox 2>/dev/null || echo 0); [ \"$sz\" -gt 100000 ] && cat /data/adb/modules/droidcloud_identity/.hash 2>/dev/null'",
                                        ],
                                        capture_output=True,
                                        text=True,
                                        timeout=8,
                                        check=False,
                                    ).stdout.strip()
                                    if cur_hash != expected:
                                        self._install_avd_identity_module(serial, metadata["identity"], apply_live=True)
                                except Exception:
                                    pass
                        self._apply_guest_performance(serial)
                        try:
                            self._ensure_mirror(machine_id, metadata)
                        except OSError as exc:
                            self._mark_error(machine_id, f"Display mirror failed: {exc}")
                            return
                    with self.lock:
                        if runtime.get("generation") == generation:
                            runtime["phase"] = "running"
                            runtime["error"] = None
                    self._last_errors.pop(machine_id, None)
                    self._restore_location(machine_id)
                    return
            time.sleep(2)
        self._mark_error(machine_id, "Boot timed out after 4 minutes.")

    def _apply_guest_performance(self, serial: str) -> None:
        """Workspace performance profile applied to each booted AVD."""
        try:
            perf = platform_settings.load()["performance"]
            scale = "0" if perf.get("disableAnimations") else "1"
            cmd = (
                f"settings put global window_animation_scale {scale}; "
                f"settings put global transition_animation_scale {scale}; "
                f"settings put global animator_duration_scale {scale}; "
                "settings put global package_verifier_user_consent -1 2>/dev/null; "
                "settings put system screen_off_timeout 2147483647 2>/dev/null; "
                "true"
            )
            self._run_adb_safe(["-s", serial, "shell", cmd], timeout=15)
        except Exception:
            pass

    def apply_performance_to_running(self) -> dict[str, Any]:
        applied = 0
        for metadata in self._all_metadata():
            if metadata.get("backend") == "iso":
                continue
            state = self.machine_state(metadata)
            if state.get("adbState") == "device":
                self._apply_guest_performance(self._adb_serial(metadata))
                applied += 1
        return {"ok": True, "applied": applied}

    def _mark_error(self, machine_id: str, message: str) -> None:
        with self.lock:
            runtime = self._runtime_for(machine_id)
            runtime["phase"] = "error"
            runtime["error"] = message
            self._last_errors[machine_id] = message

    @staticmethod
    def _iso_qmp_path(machine_id: str) -> Path:
        return ISO_VM_DIR / machine_id / "qmp.sock"

    def _iso_qmp(self, machine_id: str, command: str) -> bool:
        """Send one QMP command (e.g. system_powerdown) to an ISO VM. Returns success."""
        path = self._iso_qmp_path(machine_id)
        if not path.exists():
            return False
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
                sock.settimeout(5)
                sock.connect(str(path))
                sock.recv(4096)
                sock.sendall(b'{"execute":"qmp_capabilities"}\n')
                sock.recv(4096)
                sock.sendall(json.dumps({"execute": command}).encode("utf-8") + b"\n")
                return b'"return"' in sock.recv(4096)
        except OSError:
            return False

    def _start_iso_runtime(
        self, machine_id: str, metadata: dict[str, Any], runtime: dict[str, Any], environment: dict[str, str]
    ) -> None:
        if not QEMU_SYSTEM.is_file():
            raise MachineError("qemu-system-x86_64 is not installed on this host.", 500)
        iso_path = ISO_DIR / str(metadata.get("isoFilename") or "")
        if not iso_path.is_file():
            raise MachineError("Android ISO file is missing on disk.", 404)
        data_img = ISO_VM_DIR / machine_id / "data.img"
        if not data_img.is_file():
            self._create_iso_data_disk(machine_id, int(metadata.get("storage", 32)))
        elif E2FSCK.is_file():
            # Repair any unclean ext4 journal/inodes before guest mounts DATA=vda
            subprocess.run(
                [str(E2FSCK), "-p", "-f", str(data_img)],
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
            subprocess.run(
                [str(E2FSCK), "-y", str(data_img)],
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
        kernel, initrd = self._extract_iso_boot_files(iso_path)
        initrd = self._iso_boot_initrd(initrd)
        ident = metadata.get("identity") or {}
        mfr = re.sub(r"[,=]", " ", str(ident.get("manufacturer") or "Google")).strip()
        brand = str(ident.get("brand") or mfr).strip()
        model = re.sub(r"[,=]", " ", str(ident.get("model") or "Pixel 7 Pro")).strip()
        dev = re.sub(r"[,=]", " ", str(ident.get("device") or "cheetah")).strip()
        bootloader_raw = str(ident.get("bootloader") or "").strip()
        if not bootloader_raw or bootloader_raw.lower() == "unknown":
            bootloader_raw = f"{dev}-1.0"
        bootloader = re.sub(r"[,=]", " ", bootloader_raw).strip()
        serial = re.sub(r"[^A-Za-z0-9_-]", "", str(ident.get("serial") or "4A91F820BC12"))[:20]
        oem_mac = fingerprints.generate_oem_mac(brand, f"{machine_id}:{serial}")
        vnc_display = max(0, int(metadata["vncPort"]) - 5900)
        cmdline = (
            "root=/dev/ram0 androidboot.selinux=permissive SRC= DATA=vda "
            "HWACCEL=0 SETUPWIZARD=0 DPI=320 FORCE_DISABLE_NAV_TASKBAR=true "
            "video=Virtual-1:720x1600@60 console=ttyS0"
        )
        if metadata.get("virtWifi", True):
            # Handled by the Droid Cloud KernelSU post-fs-data network script
            cmdline += " VIRT_WIFI=1"
        runtime["emulator"] = self._spawn(
            machine_id,
            "emulator",
            [
                str(QEMU_SYSTEM),
                # No implicit empty CD-ROM, floppy, parallel port or default NIC/monitor.
                "-nodefaults",
                "-enable-kvm",
                "-cpu",
                "host,kvm=off,-hypervisor",
                "-machine",
                "q35",
                "-global",
                "ICH9-LPC.disable_s3=1",
                "-global",
                "ICH9-LPC.disable_s4=1",
                "-smp",
                str(metadata["cpu"]),
                "-m",
                str(metadata["ram"]),
                "-smbios",
                f"type=0,vendor={mfr},version={bootloader},date=01/15/2024",
                "-smbios",
                f"type=1,manufacturer={mfr},product={dev},version={model},serial={serial}",
                "-smbios",
                f"type=2,manufacturer={mfr},product={model},version=MP1.0",
                "-smbios",
                f"type=3,manufacturer={mfr},version=1.0,serial={serial}",
                "-kernel",
                str(kernel),
                "-initrd",
                str(initrd),
                "-append",
                cmdline,
                # Explicit slots: implicit if=virtio drives are created after all
                # -device options, which would make the ISO vda and break DATA=vda.
                "-drive",
                f"file={data_img},format=raw,if=none,id=userdata",
                "-device",
                "virtio-blk-pci,drive=userdata,addr=0x4",
                # Attach the ISO as a read-only virtio disk (vdb), not an ATAPI CD-ROM:
                # vold auto-mounts sr* as a public "BlissOS-..." volume whose label is an
                # invalid MediaStore volume name, crashing MediaProvider and leaving
                # emulated storage unmountable. The BlissOS initrd still scans /dev/vd*.
                "-drive",
                f"file={iso_path},format=raw,if=none,id=bootiso,readonly=on",
                "-device",
                "virtio-blk-pci,drive=bootiso,addr=0x5",
                "-device",
                "virtio-vga,xres=720,yres=1600",
                "-device",
                "usb-ehci",
                "-device",
                "usb-tablet",
                "-netdev",
                f"user,id=net0,ipv6=off,hostfwd=tcp:127.0.0.1:{metadata['adbPort']}-:5555",
                "-device",
                f"virtio-net-pci,netdev=net0,mac={oem_mac}",
                "-display",
                f"vnc=127.0.0.1:{vnc_display},share=ignore",
                # Out-of-band control channel: ACPI power-button fallback when ADB is gone
                "-qmp",
                f"unix:{self._iso_qmp_path(machine_id)},server=on,wait=off",
                "-serial",
                "stdio",
            ],
            environment,
        )
        self._write_tokens()

    def start_machine(self, machine_id: str) -> dict[str, Any]:
        metadata = self._load(machine_id)
        operation_lock = self._operation_lock(machine_id)
        if not operation_lock.acquire(blocking=False):
            raise MachineError("Another operation is currently running on this device.", 409)
        try:
            with self.lock:
                runtime = self._runtime_for(machine_id)
                if process_alive(runtime.get("emulator")):
                    return self.machine_state(metadata)
                self._last_errors.pop(machine_id, None)
                runtime["generation"] += 1
                generation = runtime["generation"]
                runtime["phase"] = "starting"
                runtime["error"] = None
                runtime["mirrorStarted"] = False
                runtime["scrcpy"] = None
                environment = self._environment()
                if metadata.get("backend") == "iso":
                    self._start_iso_runtime(machine_id, metadata, runtime, environment)
                else:
                    root_arguments: list[str] = []
                    if metadata.get("root"):
                        ramdisk = self._ramdisk_for_api(int(metadata.get("apiLevel", 36)))
                        if not ramdisk:
                            raise MachineError(
                                "Magisk ramdisk for this Android API level is not installed on the server.",
                                500,
                            )
                        root_arguments = [
                            "-writable-system",
                            "-ramdisk",
                            str(ramdisk),
                        ]
                    runtime["emulator"] = self._spawn(
                        machine_id,
                        "emulator",
                        [
                            str(EMULATOR),
                            "-avd",
                            machine_id,
                            "-port",
                            str(metadata["consolePort"]),
                            "-no-window",
                            "-no-audio",
                            "-no-boot-anim",
                            "-no-metrics",
                            "-netfast",
                            "-gpu",
                            EMULATOR_GPU,
                            "-accel",
                            "on",
                            "-no-snapshot",
                        ]
                        # VPN devices: DNS through the tunnel, and Wi-Fi handled inside this
                        # emulator process. By default Wi-Fi traffic is relayed by the shared
                        # netsimd daemon, which sits outside the device's VPN cgroup and would
                        # leak the server's own IP.
                        + (
                            ["-dns-server", vpn.VPN_DNS, "-feature", "-WiFiPacketStream"]
                            if metadata.get("vpnProfile")
                            else []
                        )
                        + root_arguments,
                        environment,
                    )
                    self._ensure_display(machine_id, metadata)
            threading.Thread(
                target=self._watch_boot,
                args=(machine_id, generation),
                daemon=True,
                name=f"boot-{machine_id}",
            ).start()
            return self.get_machine(machine_id)
        except Exception:
            with self.lock:
                runtime = self._runtime_for(machine_id)
                if runtime.get("phase") == "starting":
                    runtime["phase"] = "error"
            raise
        finally:
            operation_lock.release()

    def _stop_runtime(self, machine_id: str, metadata: dict[str, Any]) -> None:
        runtime = self._runtime_for(machine_id)
        with self.lock:
            runtime["generation"] += 1
        serial = self._adb_serial(metadata)
        try:
            if metadata.get("backend") == "iso":
                subprocess.run(
                    [str(ADB), "-s", serial, "shell", "sync; reboot -p"],
                    capture_output=True,
                    text=True,
                    timeout=8,
                    check=False,
                )
            else:
                subprocess.run(
                    [str(ADB), "-s", serial, "emu", "kill"],
                    capture_output=True,
                    text=True,
                    timeout=6,
                    check=False,
                )
        except (OSError, subprocess.TimeoutExpired):
            pass
        # Give the Android guest time to finish ShutdownThread + ext4 sync before QEMU is killed
        is_iso = metadata.get("backend") == "iso"
        deadline = time.monotonic() + (30 if is_iso else 10)
        powerdown_sent = False
        while process_alive(runtime.get("emulator")) and time.monotonic() < deadline:
            if is_iso and not powerdown_sent and deadline - time.monotonic() < 22:
                # ADB unreachable/hung: press the ACPI power button (clean Android shutdown)
                powerdown_sent = self._iso_qmp(machine_id, "system_powerdown")
            time.sleep(0.2)
        terminate_group(runtime.get("scrcpy"))
        terminate_group(runtime.get("vnc"))
        terminate_group(runtime.get("xvfb"))
        terminate_group(runtime.get("emulator"))
        if metadata.get("backend") == "iso":
            try:
                subprocess.run(
                    [str(ADB), "disconnect", serial],
                    capture_output=True,
                    text=True,
                    timeout=4,
                    check=False,
                )
            except (OSError, subprocess.TimeoutExpired):
                pass
        with self.lock:
            runtime.update(
                {
                    "phase": "stopped",
                    "emulator": None,
                    "xvfb": None,
                    "scrcpy": None,
                    "vnc": None,
                    "mirrorStarted": False,
                    "error": None,
                }
            )
        self._last_errors.pop(machine_id, None)
        self._write_tokens()

    def stop_machine(self, machine_id: str) -> dict[str, Any]:
        metadata = self._load(machine_id)
        operation_lock = self._operation_lock(machine_id)
        if not operation_lock.acquire(blocking=False):
            raise MachineError("Operasi lain sedang berjalan pada mesin ini.", 409)
        try:
            self._stop_runtime(machine_id, metadata)
            return self.get_machine(machine_id)
        finally:
            operation_lock.release()

    def restart_machine(self, machine_id: str) -> dict[str, Any]:
        metadata = self._load(machine_id)

        def worker() -> None:
            operation_lock = self._operation_lock(machine_id)
            try:
                with operation_lock:
                    self._stop_runtime(machine_id, metadata)
                time.sleep(1)
                self.start_machine(machine_id)
            except Exception as exc:
                self._mark_error(machine_id, f"Restart gagal: {exc}")

        thread = threading.Thread(target=worker, daemon=True, name=f"restart-{machine_id}")
        thread.start()
        return {"ok": True, "message": "Restart dimulai."}

    def set_autostart(self, machine_id: str, enabled: bool) -> dict[str, Any]:
        metadata = self._load(machine_id)
        metadata["autostart"] = bool(enabled)
        self._save(metadata)
        return self.machine_state(metadata)

    def clone_machine(self, machine_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        source = self._load(machine_id)
        if process_alive(self._runtime_for(machine_id).get("emulator")):
            raise MachineError("Stop the source device before cloning.", 409)
        name = self._validate_name(payload.get("name"))
        new_id = self._unique_id(name)
        ports = self._allocate_ports()
        if source.get("backend") == "iso":
            src_dir = ISO_VM_DIR / machine_id
            dst_dir = ISO_VM_DIR / new_id
            if not src_dir.is_dir():
                raise MachineError("Source ISO VM disk not found.", 404)
            try:
                subprocess.run(
                    ["cp", "-a", "--reflink=auto", str(src_dir), str(dst_dir)],
                    check=True,
                    capture_output=True,
                    text=True,
                    timeout=300,
                )
            except (OSError, subprocess.SubprocessError) as exc:
                shutil.rmtree(dst_dir, ignore_errors=True)
                raise MachineError("ISO VM clone failed.", 500) from exc
        else:
            source_avd = AVD_DIR / f"{machine_id}.avd"
            source_ini = AVD_DIR / f"{machine_id}.ini"
            target_avd = AVD_DIR / f"{new_id}.avd"
            target_ini = AVD_DIR / f"{new_id}.ini"
            if not source_avd.is_dir() or not source_ini.exists():
                raise MachineError("Source AVD files not found.", 404)
            try:
                subprocess.run(
                    ["cp", "-a", "--reflink=auto", str(source_avd), str(target_avd)],
                    check=True,
                    capture_output=True,
                    text=True,
                    timeout=300,
                )
                atomic_write(target_ini, f"avd.ini.encoding=UTF-8\npath={target_avd}\n")
                for lock_path in target_avd.glob("*.lock"):
                    lock_path.unlink(missing_ok=True)
                self._write_avd_config(
                    new_id,
                    {
                        "avd.id": new_id,
                        "avd.name": new_id,
                    },
                )
            except (OSError, subprocess.SubprocessError) as exc:
                shutil.rmtree(target_avd, ignore_errors=True)
                target_ini.unlink(missing_ok=True)
                raise MachineError("AVD clone failed.", 500) from exc
        cloned_identity = source.get("identity")
        if isinstance(cloned_identity, dict) and cloned_identity.get("profileId"):
            fp_prof = fingerprints.get_profile(cloned_identity["profileId"])
            if fp_prof:
                cloned_identity = fingerprints.build_identity_record(
                    fp_prof,
                    carrier_id=cloned_identity.get("carrierId", "telkomsel"),
                    randomize_ids=True,
                )
        metadata = {
            **source,
            "id": new_id,
            "name": name,
            "identity": cloned_identity,
            "consolePort": ports["consolePort"],
            "adbPort": ports["adbPort"],
            "vncPort": ports["vncPort"],
            "displayNumber": ports["displayNumber"],
            "consoleToken": secrets.token_urlsafe(32),
            "autostart": bool(payload.get("autostart", False)),
            "vpnProfile": self._clean_vpn(payload.get("vpnProfile", source.get("vpnProfile"))),
            "group": self._clean_group(payload.get("group", source.get("group"))),
            "tags": self._clean_tags(payload.get("tags", source.get("tags"))),
            "isTemplate": False,
            "createdAt": now(),
        }
        self._save(metadata)
        if payload.get("start", False):
            self.start_machine(new_id)
        return self.get_machine(new_id)

    def wipe_machine(self, machine_id: str) -> dict[str, Any]:
        metadata = self._load(machine_id)
        if process_alive(self._runtime_for(machine_id).get("emulator")):
            self.stop_machine(machine_id)
        if metadata.get("backend") == "iso":
            self._create_iso_data_disk(machine_id, int(metadata.get("storage", 32)))
        else:
            avd = AVD_DIR / f"{machine_id}.avd"
            for name in ("userdata-qemu.img", "userdata-qemu.img.qcow2", "cache.img", "cache.img.qcow2"):
                (avd / name).unlink(missing_ok=True)
            shutil.rmtree(avd / "snapshots", ignore_errors=True)
        metadata["lastLocation"] = None
        self._save(metadata)
        return self.get_machine(machine_id)

    def delete_machine(self, machine_id: str) -> None:
        metadata = self._load(machine_id)
        operation_lock = self._operation_lock(machine_id)
        if not operation_lock.acquire(blocking=False):
            raise MachineError("Another operation is currently running on this device.", 409)
        try:
            self._stop_runtime(machine_id, metadata)
            if metadata.get("backend") == "iso":
                shutil.rmtree(ISO_VM_DIR / machine_id, ignore_errors=True)
            else:
                result = subprocess.run(
                    [str(AVDMANAGER), "delete", "avd", "-n", machine_id],
                    input="yes\n",
                    capture_output=True,
                    text=True,
                    env=self._environment(),
                    timeout=60,
                    check=False,
                )
                if result.returncode != 0 and (AVD_DIR / f"{machine_id}.avd").exists():
                    raise MachineError("AVD deletion failed.", 500)
            (MACHINE_DIR / f"{machine_id}.json").unlink(missing_ok=True)
            with self.lock:
                self.runtime.pop(machine_id, None)
                self.operation_locks.pop(machine_id, None)
            self._write_tokens()
        finally:
            operation_lock.release()

    def install_apk(self, machine_id: str, apk_path: Path) -> tuple[bool, str]:
        metadata = self._load(machine_id)
        state = self.machine_state(metadata)
        if not state["ready"]:
            raise MachineError("Mesin harus menyala dan selesai booting.", 409)
        result = subprocess.run(
            [
                str(ADB),
                "-s",
                self._adb_serial(metadata),
                "install",
                "-r",
                str(apk_path),
            ],
            capture_output=True,
            text=True,
            timeout=240,
            check=False,
        )
        if result.returncode == 0:
            return True, "Aplikasi berhasil dipasang."
        detail = (result.stderr or result.stdout).strip().replace("\n", " ")[:400]
        return False, detail or "Pemasangan aplikasi gagal."

    def send_key(self, machine_id: str, key: str) -> dict[str, Any]:
        metadata = self._load(machine_id)
        self._require_input_ready(metadata)
        serial = self._adb_serial(metadata)
        key_events = {
            "back": "4",
            "home": "3",
            "recents": "187",
            "volume-up": "24",
            "volume-down": "25",
            "volume-mute": "164",
            "power": "26",
            "wake": "224",
            "sleep": "223",
            "enter": "66",
            "backspace": "67",
            "tab": "61",
            "escape": "111",
            "space": "62",
            "delete": "112",
            "up": "19",
            "down": "20",
            "left": "21",
            "right": "22",
            "move-home": "122",
            "move-end": "123",
            "page-up": "92",
            "page-down": "93",
            "menu": "82",
        }
        # Ctrl+<key> shortcuts (Android 13+ `input keycombination`).
        combos = {"select-all": "29", "copy": "31", "paste": "50", "cut": "52", "undo": "54"}
        if key in combos:
            result = subprocess.run(
                [str(ADB), "-s", serial, "shell", "input", "keycombination", "113", combos[key]],
                capture_output=True, text=True, timeout=10, check=False,
            )
            if result.returncode != 0:
                detail = (result.stderr or result.stdout).strip()[:250]
                raise MachineError(detail or "Perintah tombol gagal.", 500)
            return {"ok": True}
        if key in {"notification", "quick-settings", "collapse"}:
            cmd_map = {
                "notification": ["cmd", "statusbar", "expand-notifications"],
                "quick-settings": ["cmd", "statusbar", "expand-settings"],
                "collapse": ["cmd", "statusbar", "collapse"],
            }
            result = subprocess.run(
                [str(ADB), "-s", serial, "shell", *cmd_map[key]],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
            if result.returncode != 0:
                detail = (result.stderr or result.stdout).strip()[:250]
                raise MachineError(detail or "Perintah perangkat gagal.", 500)
            return {"ok": True}
        if key == "rotate":
            cur = subprocess.run(
                [str(ADB), "-s", serial, "shell", "settings", "get", "system", "user_rotation"],
                capture_output=True,
                text=True,
                timeout=8,
                check=False,
            )
            current_rot = cur.stdout.strip()
            next_rot = "0" if current_rot == "1" else "1"
            subprocess.run(
                [str(ADB), "-s", serial, "shell", "settings", "put", "system", "accelerometer_rotation", "0"],
                capture_output=True,
                text=True,
                timeout=8,
                check=False,
            )
            result = subprocess.run(
                [str(ADB), "-s", serial, "shell", "settings", "put", "system", "user_rotation", next_rot],
                capture_output=True,
                text=True,
                timeout=8,
                check=False,
            )
            if result.returncode != 0:
                detail = (result.stderr or result.stdout).strip()[:250]
                raise MachineError(detail or "Rotasi layar gagal.", 500)
            return {"ok": True, "rotation": int(next_rot)}
        if key not in key_events:
            raise MachineError("Tombol perangkat tidak dikenal.")
        result = subprocess.run(
            [
                str(ADB),
                "-s",
                serial,
                "shell",
                "input",
                "keyevent",
                key_events[key],
            ],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        if result.returncode != 0:
            detail = (result.stderr or result.stdout).strip()[:250]
            raise MachineError(detail or "Perintah tombol gagal.", 500)
        return {"ok": True}

    def send_text(self, machine_id: str, text: str, submit: bool = False) -> dict[str, Any]:
        metadata = self._load(machine_id)
        self._require_input_ready(metadata)
        clean = str(text or "").replace("\r", "").replace("\n", " ")
        if not clean:
            raise MachineError("Teks tidak boleh kosong.")
        if len(clean) > 1000:
            raise MachineError("Teks maksimal 1000 karakter.")
        serial = self._adb_serial(metadata)
        encoded = clean.replace(" ", "%s")
        result = subprocess.run(
            [str(ADB), "-s", serial, "shell", f"input text {shlex.quote(encoded)}"],
            capture_output=True,
            text=True,
            timeout=12,
            check=False,
        )
        if result.returncode != 0:
            detail = (result.stderr or result.stdout).strip()[:250]
            raise MachineError(detail or "Gagal mengirim teks ke perangkat.", 500)
        if submit:
            subprocess.run(
                [str(ADB), "-s", serial, "shell", "input", "keyevent", "66"],
                capture_output=True,
                text=True,
                timeout=8,
                check=False,
            )
        return {"ok": True}

    def list_packages(self, machine_id: str, include_system: bool = False) -> dict[str, Any]:
        metadata = self._load(machine_id)
        state = self.machine_state(metadata)
        if not state["ready"]:
            raise MachineError("Mesin harus menyala dan selesai booting.", 409)
        serial = self._adb_serial(metadata)
        cmd = [str(ADB), "-s", serial, "shell", "pm", "list", "packages"]
        if not include_system:
            cmd.append("-3")
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        if result.returncode != 0:
            detail = (result.stderr or result.stdout).strip()[:250]
            raise MachineError(detail or "Gagal membaca daftar paket.", 500)
        packages = sorted(
            line.split(":", 1)[1].strip()
            for line in result.stdout.splitlines()
            if line.startswith("package:")
        )
        return {"ok": True, "packages": packages}

    def package_action(self, machine_id: str, package: str, action: str) -> dict[str, Any]:
        metadata = self._load(machine_id)
        state = self.machine_state(metadata)
        if not state["ready"]:
            raise MachineError("Mesin harus menyala dan selesai booting.", 409)
        pkg = str(package or "").strip()
        if not re.fullmatch(r"[A-Za-z0-9._-]{2,160}", pkg):
            raise MachineError("Nama paket aplikasi tidak valid.")
        serial = self._adb_serial(metadata)
        if action == "launch":
            cmd = [str(ADB), "-s", serial, "shell", "monkey", "-p", pkg, "-c", "android.intent.category.LAUNCHER", "1"]
        elif action == "stop":
            cmd = [str(ADB), "-s", serial, "shell", "am", "force-stop", pkg]
        elif action == "clear":
            cmd = [str(ADB), "-s", serial, "shell", "pm", "clear", pkg]
        elif action == "uninstall":
            cmd = [str(ADB), "-s", serial, "uninstall", pkg]
        else:
            raise MachineError("Aksi paket tidak dikenal.")
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        output = (result.stdout or result.stderr or "").strip()
        if result.returncode != 0 or (action == "uninstall" and "Failure" in output):
            raise MachineError(output[:250] or "Operasi paket gagal.", 500)
        return {"ok": True, "output": output[:300]}

    def get_logs(self, machine_id: str, kind: str = "emulator", lines: int = 150) -> dict[str, Any]:
        self._load(machine_id)
        if kind not in {"emulator", "mirror", "vnc", "display"}:
            raise MachineError("Jenis log tidak valid.")
        limit = max(10, min(500, int(lines)))
        log_path = LOG_DIR / f"{machine_id}-{kind}.log"
        if not log_path.exists():
            return {"ok": True, "kind": kind, "exists": False, "size": 0, "content": ""}
        try:
            size = log_path.stat().st_size
            with log_path.open("rb") as handle:
                if size > 131072:
                    handle.seek(size - 131072)
                raw = handle.read().decode("utf-8", "replace")
        except OSError as exc:
            raise MachineError(f"Log tidak dapat dibaca: {exc}", 500) from exc
        tail = "\n".join(raw.splitlines()[-limit:])
        return {"ok": True, "kind": kind, "exists": True, "size": size, "content": tail}

    def device_telemetry(self, machine_id: str) -> dict[str, Any]:
        metadata = self._load(machine_id)
        state = self.machine_state(metadata)
        if not state["ready"]:
            return {"ok": True, "ready": False, "telemetry": None}
        serial = self._adb_serial(metadata)
        script = (
            "echo RELEASE=$(getprop ro.build.version.release);"
            "echo SDK=$(getprop ro.build.version.sdk);"
            "echo ROT=$(settings get system user_rotation 2>/dev/null);"
            "echo IP=$(ip -o -4 addr show scope global 2>/dev/null | head -n 1 | awk '{print $4}');"
            "echo UPTIME=$(uptime -p 2>/dev/null || cut -d' ' -f1 /proc/uptime);"
            "dumpsys battery 2>/dev/null | grep -E 'level:|AC powered:|status:'"
        )
        result = subprocess.run(
            [str(ADB), "-s", serial, "shell", script],
            capture_output=True,
            text=True,
            timeout=8,
            check=False,
        )
        info: dict[str, Any] = {
            "androidRelease": metadata.get("androidVersion", ""),
            "sdkInt": str(metadata.get("apiLevel", "")),
            "rotation": 0,
            "ipAddress": "—",
            "uptime": "—",
            "batteryLevel": None,
            "acPowered": True,
        }
        if result.returncode == 0:
            for line in result.stdout.splitlines():
                text = line.strip()
                if text.startswith("RELEASE="):
                    val = text.split("=", 1)[1].strip()
                    if val:
                        info["androidRelease"] = f"Android {val}"
                elif text.startswith("SDK="):
                    val = text.split("=", 1)[1].strip()
                    if val:
                        info["sdkInt"] = val
                elif text.startswith("ROT="):
                    val = text.split("=", 1)[1].strip()
                    if val.isdigit():
                        info["rotation"] = int(val)
                elif text.startswith("IP="):
                    val = text.split("=", 1)[1].strip()
                    if val:
                        info["ipAddress"] = val
                elif text.startswith("UPTIME="):
                    val = text.split("=", 1)[1].strip()
                    if val:
                        info["uptime"] = val
                elif text.startswith("level:"):
                    val = text.split(":", 1)[1].strip()
                    if val.isdigit():
                        info["batteryLevel"] = int(val)
                elif text.startswith("AC powered:"):
                    info["acPowered"] = text.split(":", 1)[1].strip().lower() == "true"
        return {"ok": True, "ready": True, "telemetry": info}

    def set_battery(self, machine_id: str, level: Any, ac: bool = True) -> dict[str, Any]:
        metadata = self._load(machine_id)
        state = self.machine_state(metadata)
        if not state["ready"]:
            raise MachineError("Mesin harus menyala dan selesai booting.", 409)
        try:
            pct = int(level)
        except (TypeError, ValueError) as exc:
            raise MachineError("Level baterai harus berupa angka 0–100.") from exc
        if not 0 <= pct <= 100:
            raise MachineError("Level baterai harus di antara 0 dan 100.")
        serial = self._adb_serial(metadata)
        if metadata.get("backend") == "iso":
            r1 = subprocess.run(
                [str(ADB), "-s", serial, "shell", "dumpsys", "battery", "set", "level", str(pct)],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
            r2 = subprocess.run(
                [
                    str(ADB), "-s", serial, "shell",
                    # Keep charging status consistent with the AC state (2=charging, 3=discharging)
                    f"dumpsys battery set ac {1 if ac else 0} && dumpsys battery set status {2 if ac else 3}",
                ],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
        else:
            r1 = subprocess.run(
                [str(ADB), "-s", serial, "emu", "power", "capacity", str(pct)],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
            r2 = subprocess.run(
                [str(ADB), "-s", serial, "emu", "power", "ac", "on" if ac else "off"],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
        if r1.returncode != 0 or r2.returncode != 0:
            detail = (r1.stderr or r2.stderr or r1.stdout or r2.stdout).strip()[:250]
            raise MachineError(detail or "Battery simulation failed.", 500)
        return {"ok": True, "level": pct, "ac": bool(ac)}

    def run_shell(self, machine_id: str, command: str) -> dict[str, Any]:
        metadata = self._load(machine_id)
        state = self.machine_state(metadata)
        if not state["ready"]:
            raise MachineError("Mesin harus menyala dan selesai booting.", 409)
        cmd = str(command or "").strip()
        if not cmd:
            raise MachineError("Perintah tidak boleh kosong.")
        if len(cmd) > 500:
            raise MachineError("Perintah maksimal 500 karakter.")
        serial = self._adb_serial(metadata)
        try:
            result = subprocess.run(
                [str(ADB), "-s", serial, "shell", cmd],
                capture_output=True,
                text=True,
                timeout=15,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise MachineError("Perintah ADB shell melewati batas waktu 15 detik.", 504) from exc
        return {
            "ok": True,
            "exitCode": result.returncode,
            "stdout": (result.stdout or "")[:32768],
            "stderr": (result.stderr or "")[:8192],
        }

    def set_location(
        self,
        machine_id: str,
        latitude: Any,
        longitude: Any,
        label: str | None = None,
    ) -> dict[str, Any]:
        metadata = self._load(machine_id)
        state = self.machine_state(metadata)
        if not state["ready"]:
            raise MachineError("Mesin harus menyala dan selesai booting.", 409)
        try:
            lat = float(latitude)
            lon = float(longitude)
        except (TypeError, ValueError) as exc:
            raise MachineError("Latitude dan longitude harus berupa angka.") from exc
        if not -90 <= lat <= 90:
            raise MachineError("Latitude harus berada di antara -90 dan 90.")
        if not -180 <= lon <= 180:
            raise MachineError("Longitude harus berada di antara -180 dan 180.")
        if metadata.get("backend") == "iso":
            self._iso_set_location(self._adb_serial(metadata), lat, lon)
        else:
            result = subprocess.run(
                [
                    str(ADB),
                    "-s",
                    self._adb_serial(metadata),
                    "emu",
                    "geo",
                    "fix",
                    f"{lon:.6f}",
                    f"{lat:.6f}",
                ],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
            if result.returncode != 0 or result.stdout.strip() != "OK":
                detail = (result.stderr or result.stdout).strip()[:250]
                raise MachineError(detail or "Failed to update GPS location.", 500)
        location = {
            "latitude": round(lat, 6),
            "longitude": round(lon, 6),
            "label": str(label or "").strip()[:60],
            "updatedAt": now(),
        }
        metadata["lastLocation"] = location
        self._save(metadata)
        return {"ok": True, "location": location, "machine": self.machine_state(metadata)}

    # On-device files for the persistent GPS mock (see _iso_set_location).
    _ISO_GEO_FILE = "/data/local/tmp/droidcloud_geo"
    _ISO_GEO_DAEMON = "/data/local/tmp/droidcloud_geo_daemon.sh"
    _ISO_GEO_PID = "/data/local/tmp/droidcloud_geo.pid"

    # Tiny root daemon that keeps re-pushing the saved coordinate to the gps,
    # network and fused test providers once per second. It reads the target from
    # _ISO_GEO_FILE, so changing location only rewrites that file.
    _ISO_GEO_DAEMON_SRC = (
        "#!/system/bin/sh\n"
        f"F={_ISO_GEO_FILE}\n"
        "while :; do\n"
        '  if [ -f "$F" ]; then\n'
        '    read L A < "$F"\n'
        "    for p in gps network fused; do\n"
        '      cmd location providers set-test-provider-location $p'
        ' --location "$L" --accuracy "${A:-8}" >/dev/null 2>&1\n'
        "    done\n"
        "  fi\n"
        "  sleep 1\n"
        "done\n"
    )

    def _iso_set_location(self, serial: str, lat: float, lon: float) -> None:
        """QEMU guests have no emulator console, so GPS uses Android test
        providers. A ONE-SHOT fix is not enough for Google Maps: GMS's fused
        location provider serves apps from its own engine and needs a FRESH
        location each request interval, so a single set-test-provider-location
        goes stale and the blue dot never locks. We therefore run a small root
        daemon in the guest that re-pushes the saved coordinate every second to
        gps, network and fused. _iso_set_location (re)registers the providers,
        writes the new coordinate, pushes once immediately, and makes sure the
        daemon is running. Everything resets on reboot, so _restore_location
        re-applies it on boot."""
        loc = f"{lat:.6f},{lon:.6f}"
        daemon_b64 = base64.b64encode(self._ISO_GEO_DAEMON_SRC.encode()).decode()
        script = (
            # Grant mock permission + enable location + (re)register providers.
            "appops set 0 android:mock_location allow 2>/dev/null; "
            "appops set 2000 android:mock_location allow 2>/dev/null; "
            "cmd location set-location-enabled true 2>/dev/null; "
            "for p in gps network fused; do "
            "cmd location providers add-test-provider $p >/dev/null 2>&1; "
            "cmd location providers set-test-provider-enabled $p true >/dev/null 2>&1; "
            "done; "
            # Target the daemon keeps pushing, and an immediate first fix.
            f"echo '{loc} 8' > {self._ISO_GEO_FILE}; "
            "for p in gps network fused; do "
            f"cmd location providers set-test-provider-location $p --location {loc} --accuracy 8 >/dev/null 2>&1; "
            "done; "
            # Install + (re)start the daemon only if it is not already running.
            f"echo {daemon_b64} | base64 -d > {self._ISO_GEO_DAEMON}; "
            f'if [ -f {self._ISO_GEO_PID} ] && kill -0 "$(cat {self._ISO_GEO_PID} 2>/dev/null)" 2>/dev/null; '
            "then :; "
            f"else nohup sh {self._ISO_GEO_DAEMON} >/dev/null 2>&1 & echo $! > {self._ISO_GEO_PID}; fi"
        )
        result = subprocess.run(
            [str(ADB), "-s", serial, "shell", script],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
        output = (result.stdout + result.stderr).strip()
        if result.returncode != 0 or "Exception" in output or "not allowed" in output:
            detail = next((l for l in output.splitlines() if "Exception" in l or "not allowed" in l), output)
            raise MachineError(f"Failed to set GPS location: {detail[:200]}", 500)

    def _restore_location(self, machine_id: str) -> None:
        """Re-apply the saved GPS location after every boot (mock providers and
        emulator geo fixes do not survive a reboot)."""
        try:
            metadata = self._load(machine_id)
            loc = metadata.get("lastLocation")
            if not loc:
                return
            if metadata.get("backend") == "iso":
                self._iso_set_location(self._adb_serial(metadata), float(loc["latitude"]), float(loc["longitude"]))
            else:
                subprocess.run(
                    [str(ADB), "-s", self._adb_serial(metadata), "emu", "geo", "fix",
                     f"{float(loc['longitude']):.6f}", f"{float(loc['latitude']):.6f}"],
                    capture_output=True, text=True, timeout=10, check=False,
                )
        except Exception:
            pass

    def configure_machine(
        self, machine_id: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        metadata = self._load(machine_id)
        runtime = self._runtime_for(machine_id)
        if process_alive(runtime.get("emulator")):
            raise MachineError("Matikan mesin sebelum mengubah hardware.", 409)

        device = str(payload.get("device") or metadata.get("device") or "pixel_6")
        profile = DEVICE_BY_ID.get(device)
        if not profile:
            raise MachineError("Profil perangkat tidak tersedia.")
        try:
            cpu = int(payload.get("cpu", metadata.get("cpu", 8)))
            ram = int(payload.get("ram", metadata.get("ram", 8192)))
        except (TypeError, ValueError) as exc:
            raise MachineError("Konfigurasi CPU atau RAM tidak valid.") from exc
        host_cpu = os.cpu_count() or 1
        host_ram_mb = self._host_ram_mb()
        if not 1 <= cpu <= host_cpu:
            raise MachineError(f"Jumlah vCPU harus antara 1 dan {host_cpu}.")
        if not 1024 <= ram <= host_ram_mb or ram % 512:
            raise MachineError(
                f"RAM harus antara 1 dan {round(host_ram_mb / 1024)} GB "
                "dengan kelipatan 512 MB."
            )

        if metadata.get("backend") != "iso":
            self._write_avd_config(
                machine_id,
                {
                    "hw.cpu.ncore": str(cpu),
                    "hw.ramSize": str(ram),
                    "hw.lcd.width": str(profile["width"]),
                    "hw.lcd.height": str(profile["height"]),
                    "hw.lcd.density": str(profile["density"]),
                    "hw.lcd.depth": "32",
                },
            )
        metadata.update({"device": device, "cpu": cpu, "ram": ram})
        self._save(metadata)
        return self.machine_state(metadata)

    def screenshot(self, machine_id: str, fast: bool = False) -> bytes:
        metadata = self._load(machine_id)
        state = self.machine_state(metadata)
        if not state["ready"]:
            raise MachineError("Device must be running and booted.", 409)
        if fast:
            cached = self._shot_cache.get(machine_id)
            if cached and time.monotonic() - cached[0] < 1.2:
                return cached[1]
        else:
            try:
                subprocess.run(
                    [
                        str(ADB),
                        "-s",
                        self._adb_serial(metadata),
                        "shell",
                        "input",
                        "keyevent",
                        "KEYCODE_WAKEUP",
                    ],
                    capture_output=True,
                    timeout=5,
                    check=False,
                )
                time.sleep(1.0)
            except (OSError, subprocess.TimeoutExpired):
                pass
        result = subprocess.run(
            [
                str(ADB),
                "-s",
                self._adb_serial(metadata),
                "exec-out",
                "screencap",
                "-p",
            ],
            capture_output=True,
            timeout=15 if fast else 20,
            check=False,
        )
        if result.returncode != 0 or not result.stdout.startswith(b"\x89PNG\r\n\x1a\n"):
            detail = result.stderr.decode("utf-8", "replace").strip()[:250]
            raise MachineError(detail or "Failed to capture screenshot.", 500)
        self._shot_cache[machine_id] = (time.monotonic(), result.stdout)
        return result.stdout

    @staticmethod
    def _clean_group(value: Any) -> str:
        text = re.sub(r"[^A-Za-z0-9._ -]+", "", str(value or "")).strip()[:32]
        return text or "default"

    @staticmethod
    def _clean_tags(value: Any) -> list[str]:
        if isinstance(value, str):
            raw = [item.strip() for item in value.split(",")]
        elif isinstance(value, list):
            raw = [str(item).strip() for item in value]
        else:
            raw = []
        cleaned: list[str] = []
        for item in raw:
            tag = re.sub(r"[^A-Za-z0-9._-]+", "", item)[:24]
            if tag and tag not in cleaned:
                cleaned.append(tag)
            if len(cleaned) >= 8:
                break
        return cleaned

    def update_meta(self, machine_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        metadata = self._load(machine_id)
        if "name" in payload and payload["name"] is not None:
            metadata["name"] = self._validate_name(payload["name"])
        if "group" in payload:
            metadata["group"] = self._clean_group(payload["group"])
        if "tags" in payload:
            metadata["tags"] = self._clean_tags(payload["tags"])
        if "isTemplate" in payload:
            is_tpl = bool(payload["isTemplate"])
            if is_tpl and process_alive(self._runtime_for(machine_id).get("emulator")):
                raise MachineError("Stop the device before marking it as a template.", 409)
            metadata["isTemplate"] = is_tpl
        self._save(metadata)
        return self.machine_state(metadata)

    # ------------------------------------------------------------------
    # Massive Remote / Phone Farm Broadcast Engine
    # ------------------------------------------------------------------
    def _resolve_farm_targets(self, machine_ids: Any) -> list[str]:
        if not isinstance(machine_ids, list) or not machine_ids:
            raise MachineError("Select at least one target device.")
        targets: list[str] = []
        for raw in machine_ids:
            mid = str(raw or "").strip()
            if mid and mid not in targets:
                targets.append(mid)
            if len(targets) >= MAX_MACHINES:
                break
        if not targets:
            raise MachineError("Select at least one target device.")
        return targets

    def _run_farm_parallel(
        self,
        targets: list[str],
        worker_fn,
    ) -> dict[str, Any]:
        results: list[dict[str, Any]] = [{} for _ in targets]

        def execute(index: int, machine_id: str) -> tuple[int, dict[str, Any]]:
            name = machine_id
            try:
                meta = self._load(machine_id)
                name = meta.get("name", machine_id)
                extra = worker_fn(machine_id, meta, index)
                entry = {"id": machine_id, "name": name, "ok": True}
                if isinstance(extra, dict):
                    entry.update(extra)
                return index, entry
            except MachineError as exc:
                return index, {"id": machine_id, "name": name, "ok": False, "error": exc.message}
            except Exception as exc:
                return index, {"id": machine_id, "name": name, "ok": False, "error": str(exc)[:220]}

        workers = max(1, min(16, len(targets)))
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
            futures = [pool.submit(execute, idx, mid) for idx, mid in enumerate(targets)]
            for future in concurrent.futures.as_completed(futures):
                idx, entry = future.result()
                results[idx] = entry
        succeeded = sum(1 for item in results if item.get("ok"))
        return {
            "ok": True,
            "total": len(results),
            "succeeded": succeeded,
            "failed": len(results) - succeeded,
            "results": results,
        }

    def _display_geometry(self, serial: str) -> tuple[int, int] | None:
        """Current logical (rotation-aware) display size, cached briefly."""
        cache = self.__dict__.setdefault("_geometry_cache", {})
        hit = cache.get(serial)
        if hit and time.monotonic() - hit[0] < 4.0:
            return hit[1]
        geometry: tuple[int, int] | None = None
        try:
            res = subprocess.run(
                [str(ADB), "-s", serial, "shell", "dumpsys input | grep -m1 -E 'Viewport INTERNAL: displayId=0'"],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
            match = re.search(r"logicalFrame=\[(-?\d+), (-?\d+), (-?\d+), (-?\d+)\]", res.stdout or "")
            if match:
                left, top, right, bottom = (int(v) for v in match.groups())
                if right - left > 0 and bottom - top > 0:
                    geometry = (right - left, bottom - top)
        except (OSError, subprocess.TimeoutExpired):
            geometry = None
        cache[serial] = (time.monotonic(), geometry)
        return geometry

    def _require_input_ready(self, meta: dict[str, Any]) -> dict[str, Any]:
        try:
            return self._farm_input_ready(meta)
        except MachineError as exc:
            raise MachineError("Mesin harus menyala dan selesai booting.", 409) from exc

    def _farm_input_ready(self, meta: dict[str, Any]) -> dict[str, Any]:
        st = self.machine_state(meta)
        # Accept any device ADB reports online: a VM may be flagged "error" by a
        # post-boot step (root/identity) while still being fully controllable.
        if not st["ready"] and not (st.get("adbState") == "device" and process_alive(self._runtime_for(meta["id"]).get("emulator"))):
            raise MachineError("Device is not running.", 409)
        return st

    def farm_touch(self, payload: dict[str, Any]) -> dict[str, Any]:
        targets = self._resolve_farm_targets(payload.get("machineIds"))
        kind = str(payload.get("type") or "tap").lower()
        if kind not in {"tap", "longpress", "swipe"}:
            raise MachineError("Invalid touch action type.")
        try:
            x1_pct = max(0.0, min(1.0, float(payload.get("x", 0.5))))
            y1_pct = max(0.0, min(1.0, float(payload.get("y", 0.5))))
            x2_pct = max(0.0, min(1.0, float(payload.get("x2", x1_pct))))
            y2_pct = max(0.0, min(1.0, float(payload.get("y2", y1_pct))))
            duration_ms = max(60, min(2500, int(payload.get("durationMs", 260 if kind == "swipe" else 700))))
            jitter_ms = max(0, min(1500, int(payload.get("jitterMs", 0))))
            jitter_px = max(0, min(30, int(payload.get("jitterPx", 0))))
        except (TypeError, ValueError) as exc:
            raise MachineError("Invalid touch coordinates.") from exc
        landscape = bool(payload.get("landscape", False))

        def worker(machine_id: str, meta: dict[str, Any], idx: int) -> dict[str, Any]:
            st = self._farm_input_ready(meta)
            if jitter_ms > 0:
                time.sleep(random.uniform(0, jitter_ms / 1000.0))
            geometry = self._display_geometry(self._adb_serial(meta))
            if geometry:
                w, h = geometry
            else:
                screen = st.get("screen") or {"width": 1080, "height": 2400}
                w = int(screen["height"] if landscape else screen["width"])
                h = int(screen["width"] if landscape else screen["height"])
            dx = random.randint(-jitter_px, jitter_px) if jitter_px else 0
            dy = random.randint(-jitter_px, jitter_px) if jitter_px else 0
            px1 = max(0, min(w - 1, round(x1_pct * w) + dx))
            py1 = max(0, min(h - 1, round(y1_pct * h) + dy))
            serial = self._adb_serial(meta)
            if kind == "tap":
                cmd = [str(ADB), "-s", serial, "shell", "input", "tap", str(px1), str(py1)]
            elif kind == "longpress":
                cmd = [
                    str(ADB), "-s", serial, "shell", "input", "swipe",
                    str(px1), str(py1), str(px1), str(py1), str(duration_ms),
                ]
            else:
                px2 = max(0, min(w - 1, round(x2_pct * w) + dx))
                py2 = max(0, min(h - 1, round(y2_pct * h) + dy))
                cmd = [
                    str(ADB), "-s", serial, "shell", "input", "swipe",
                    str(px1), str(py1), str(px2), str(py2), str(duration_ms),
                ]
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=10, check=False)
            if res.returncode != 0:
                raise MachineError((res.stderr or res.stdout).strip()[:200] or "Input failed.", 500)
            return {"x": px1, "y": py1}

        return self._run_farm_parallel(targets, worker)

    def farm_key(self, payload: dict[str, Any]) -> dict[str, Any]:
        targets = self._resolve_farm_targets(payload.get("machineIds"))
        key = str(payload.get("key") or "")
        jitter_ms = max(0, min(1500, int(payload.get("jitterMs", 0) or 0)))

        def worker(machine_id: str, meta: dict[str, Any], idx: int) -> dict[str, Any]:
            if jitter_ms > 0:
                time.sleep(random.uniform(0, jitter_ms / 1000.0))
            return self.send_key(machine_id, key)

        return self._run_farm_parallel(targets, worker)

    def farm_text(self, payload: dict[str, Any]) -> dict[str, Any]:
        targets = self._resolve_farm_targets(payload.get("machineIds"))
        raw_text = str(payload.get("text") or "")
        mode = str(payload.get("mode") or "same")
        submit = bool(payload.get("submit", False))
        jitter_ms = max(0, min(1500, int(payload.get("jitterMs", 0) or 0)))
        if mode not in {"same", "per-line"}:
            raise MachineError("Invalid text broadcast mode.")
        if not raw_text.strip():
            raise MachineError("Text cannot be empty.")
        lines = [line.strip() for line in raw_text.splitlines() if line.strip()]
        if not lines:
            raise MachineError("Text cannot be empty.")

        def worker(machine_id: str, meta: dict[str, Any], idx: int) -> dict[str, Any]:
            if mode == "per-line":
                if idx >= len(lines):
                    return {"skipped": True}
                value = lines[idx]
            else:
                value = raw_text.replace("\r", "").replace("\n", " ").strip()
            if jitter_ms > 0:
                time.sleep(random.uniform(0, jitter_ms / 1000.0))
            self.send_text(machine_id, value, submit=submit)
            return {"sent": len(value)}

        return self._run_farm_parallel(targets, worker)

    def farm_open_url(self, payload: dict[str, Any]) -> dict[str, Any]:
        targets = self._resolve_farm_targets(payload.get("machineIds"))
        url = str(payload.get("url") or "").strip()
        if not url or len(url) > 1000 or any(ch.isspace() for ch in url):
            raise MachineError("Provide a valid URL without spaces.")
        if not re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", url):
            url = f"https://{url}"

        def worker(machine_id: str, meta: dict[str, Any], idx: int) -> dict[str, Any]:
            st = self.machine_state(meta)
            if not st["ready"]:
                raise MachineError("Device is not running.", 409)
            serial = self._adb_serial(meta)
            res = subprocess.run(
                [
                    str(ADB), "-s", serial, "shell",
                    f"am start -a android.intent.action.VIEW -d {shlex.quote(url)}",
                ],
                capture_output=True,
                text=True,
                timeout=12,
                check=False,
            )
            if res.returncode != 0:
                raise MachineError((res.stderr or res.stdout).strip()[:200] or "Failed to open URL.", 500)
            return {"url": url}

        return self._run_farm_parallel(targets, worker)

    def farm_gps(self, payload: dict[str, Any]) -> dict[str, Any]:
        targets = self._resolve_farm_targets(payload.get("machineIds"))
        try:
            base_lat = float(payload.get("latitude"))
            base_lon = float(payload.get("longitude"))
            scatter_m = max(0, min(50000, int(payload.get("scatterMeters", 0) or 0)))
        except (TypeError, ValueError) as exc:
            raise MachineError("Invalid latitude, longitude, or scatter radius.") from exc
        if not -90 <= base_lat <= 90 or not -180 <= base_lon <= 180:
            raise MachineError("Coordinates out of range.")
        label = str(payload.get("label") or "").strip()[:60]

        def worker(machine_id: str, meta: dict[str, Any], idx: int) -> dict[str, Any]:
            lat, lon = base_lat, base_lon
            if scatter_m > 0:
                radius = scatter_m * math.sqrt(random.random())
                angle = random.uniform(0, 2 * math.pi)
                d_lat = (radius * math.cos(angle)) / 111320.0
                cos_lat = max(0.05, abs(math.cos(math.radians(base_lat))))
                d_lon = (radius * math.sin(angle)) / (111320.0 * cos_lat)
                lat = max(-90.0, min(90.0, round(base_lat + d_lat, 6)))
                lon = max(-180.0, min(180.0, round(base_lon + d_lon, 6)))
            res = self.set_location(machine_id, lat, lon, label)
            return {"location": res["location"]}

        return self._run_farm_parallel(targets, worker)

    def farm_package(self, payload: dict[str, Any]) -> dict[str, Any]:
        targets = self._resolve_farm_targets(payload.get("machineIds"))
        package = str(payload.get("package") or "").strip()
        action = str(payload.get("action") or "").strip()

        def worker(machine_id: str, meta: dict[str, Any], idx: int) -> dict[str, Any]:
            return self.package_action(machine_id, package, action)

        return self._run_farm_parallel(targets, worker)

    def farm_shell(self, payload: dict[str, Any]) -> dict[str, Any]:
        targets = self._resolve_farm_targets(payload.get("machineIds"))
        command = str(payload.get("command") or "").strip()

        def worker(machine_id: str, meta: dict[str, Any], idx: int) -> dict[str, Any]:
            res = self.run_shell(machine_id, command)
            return {
                "exitCode": res["exitCode"],
                "stdout": res["stdout"][:4096],
                "stderr": res["stderr"][:1024],
            }

        return self._run_farm_parallel(targets, worker)

    def farm_install_apk(self, targets: list[str], apk_path: Path) -> dict[str, Any]:
        resolved = self._resolve_farm_targets(targets)

        def worker(machine_id: str, meta: dict[str, Any], idx: int) -> dict[str, Any]:
            ok, msg = self.install_apk(machine_id, apk_path)
            if not ok:
                raise MachineError(msg, 400)
            return {"message": msg}

        return self._run_farm_parallel(resolved, worker)

    def _schedule_staggered_power(
        self, targets: list[str], action: str, stagger_s: float
    ) -> None:
        for idx, mid in enumerate(targets):
            if idx > 0 and stagger_s > 0:
                time.sleep(stagger_s)
            try:
                if action == "start":
                    self.start_machine(mid)
                elif action == "restart":
                    self.restart_machine(mid)
                elif action == "stop":
                    self.stop_machine(mid)
            except Exception as exc:
                self._last_errors[mid] = str(exc)

    def farm_power(self, payload: dict[str, Any]) -> dict[str, Any]:
        targets = self._resolve_farm_targets(payload.get("machineIds"))
        action = str(payload.get("action") or "").lower()
        if action not in {"start", "stop", "restart"}:
            raise MachineError("Invalid power action.")
        try:
            stagger = max(0.0, min(15.0, float(payload.get("staggerSeconds", 2.0))))
        except (TypeError, ValueError):
            stagger = 2.0

        if action in {"start", "restart"} and len(targets) > 1 and stagger > 0:
            threading.Thread(
                target=self._schedule_staggered_power,
                args=(targets, action, stagger),
                daemon=True,
            ).start()
            return {"ok": True, "scheduled": len(targets), "action": action, "staggerSeconds": stagger}

        def worker(machine_id: str, meta: dict[str, Any], idx: int) -> dict[str, Any]:
            if action == "start":
                self.start_machine(machine_id)
            elif action == "stop":
                self.stop_machine(machine_id)
            else:
                self.restart_machine(machine_id)
            return {"action": action}

        return self._run_farm_parallel(targets, worker)

    def farm_batch_clone(self, payload: dict[str, Any]) -> dict[str, Any]:
        source_id = str(payload.get("sourceId") or "").strip()
        if not source_id:
            raise MachineError("Select a source template or device.")
        prefix = self._validate_name(payload.get("prefix"))
        try:
            count = int(payload.get("count", 1))
            stagger = max(0.0, min(15.0, float(payload.get("staggerSeconds", 2.5))))
        except (TypeError, ValueError) as exc:
            raise MachineError("Invalid clone count.") from exc
        if not 1 <= count <= 20:
            raise MachineError("Batch clone count must be between 1 and 20.")
        existing_count = len(self._all_metadata())
        if existing_count + count > MAX_MACHINES:
            raise MachineError(f"Exceeds maximum host capacity ({MAX_MACHINES} devices).", 409)
        group = self._clean_group(payload.get("group"))
        start = bool(payload.get("start", False))
        created: list[dict[str, Any]] = []
        for idx in range(1, count + 1):
            suffix = f"-{idx:02d}"
            name = f"{prefix[: 48 - len(suffix)]}{suffix}"
            machine = self.clone_machine(
                source_id,
                {"name": name, "group": group, "start": False, "autostart": bool(payload.get("autostart", False))},
            )
            created.append(machine)
        if start and created:
            threading.Thread(
                target=self._schedule_staggered_power,
                args=([m["id"] for m in created], "start", stagger),
                daemon=True,
            ).start()
        return {"ok": True, "count": len(created), "machines": created}

    def farm_batch_create(self, payload: dict[str, Any]) -> dict[str, Any]:
        prefix = self._validate_name(payload.get("name") or payload.get("prefix"))
        try:
            count = int(payload.get("count", 1))
            stagger = max(0.0, min(15.0, float(payload.get("staggerSeconds", 2.5))))
        except (TypeError, ValueError) as exc:
            raise MachineError("Invalid device count.") from exc
        if not 1 <= count <= 20:
            raise MachineError("Batch create count must be between 1 and 20.")
        if len(self._all_metadata()) + count > MAX_MACHINES:
            raise MachineError(f"Exceeds maximum host capacity ({MAX_MACHINES} devices).", 409)
        start = bool(payload.get("start", True))
        created: list[dict[str, Any]] = []
        for idx in range(1, count + 1):
            name = prefix if count == 1 else f"{prefix[:44]}-{idx:02d}"
            item_payload = {**payload, "name": name, "start": False}
            created.append(self.create_machine(item_payload))
        if start and created:
            if len(created) == 1:
                self.start_machine(created[0]["id"])
                created[0] = self.get_machine(created[0]["id"])
            else:
                threading.Thread(
                    target=self._schedule_staggered_power,
                    args=([m["id"] for m in created], "start", stagger),
                    daemon=True,
                ).start()
        return {"ok": True, "count": len(created), "machines": created}

    # ------------------------------------------------------------------
    # Reusable Automation Macros
    # ------------------------------------------------------------------
    def _macros_path(self) -> Path:
        return STATE_DIR / "macros.json"

    def _read_macros(self) -> list[dict[str, Any]]:
        path = self._macros_path()
        data = read_json(path)
        if isinstance(data, list):
            return data
        defaults = [
            {
                "id": "wake-home",
                "name": "Wake & Go Home",
                "description": "Wake screen and navigate to the Android home screen.",
                "steps": [
                    {"type": "key", "key": "wake"},
                    {"type": "wait", "ms": 400},
                    {"type": "key", "key": "home"},
                ],
                "createdAt": now(),
            },
            {
                "id": "scroll-feed",
                "name": "Scroll Feed (5x)",
                "description": "Perform 5 upward vertical swipes with natural pauses.",
                "steps": [
                    {"type": "swipe", "x": 0.5, "y": 0.78, "x2": 0.5, "y2": 0.24, "durationMs": 280},
                    {"type": "wait", "ms": 900},
                    {"type": "swipe", "x": 0.5, "y": 0.78, "x2": 0.5, "y2": 0.24, "durationMs": 280},
                    {"type": "wait", "ms": 900},
                    {"type": "swipe", "x": 0.5, "y": 0.78, "x2": 0.5, "y2": 0.24, "durationMs": 280},
                    {"type": "wait", "ms": 900},
                    {"type": "swipe", "x": 0.5, "y": 0.78, "x2": 0.5, "y2": 0.24, "durationMs": 280},
                    {"type": "wait", "ms": 900},
                    {"type": "swipe", "x": 0.5, "y": 0.78, "x2": 0.5, "y2": 0.24, "durationMs": 280},
                ],
                "createdAt": now(),
            },
        ]
        atomic_json(path, defaults, 0o600)
        return defaults

    def list_macros(self) -> dict[str, Any]:
        with self._macro_lock:
            return {"ok": True, "macros": self._read_macros()}

    def save_macro(self, payload: dict[str, Any]) -> dict[str, Any]:
        name = str(payload.get("name") or "").strip()[:60]
        if len(name) < 2:
            raise MachineError("Macro name must be at least 2 characters.")
        desc = str(payload.get("description") or "").strip()[:180]
        raw_steps = payload.get("steps")
        if not isinstance(raw_steps, list) or not 1 <= len(raw_steps) <= 30:
            raise MachineError("Macro must contain between 1 and 30 steps.")
        allowed = {"tap", "swipe", "key", "text", "url", "launch", "shell", "wait"}
        steps: list[dict[str, Any]] = []
        for item in raw_steps:
            if not isinstance(item, dict) or item.get("type") not in allowed:
                raise MachineError("Macro contains an unsupported step type.")
            steps.append(dict(item))
        with self._macro_lock:
            macros = self._read_macros()
            macro_id = str(payload.get("id") or "").strip() or f"{slugify(name)}-{secrets.token_hex(3)}"
            entry = {
                "id": macro_id,
                "name": name,
                "description": desc,
                "steps": steps,
                "createdAt": now(),
            }
            macros = [m for m in macros if m.get("id") != macro_id]
            macros.insert(0, entry)
            atomic_json(self._macros_path(), macros, 0o600)
        return {"ok": True, "macro": entry}

    def delete_macro(self, macro_id: str) -> dict[str, Any]:
        with self._macro_lock:
            macros = self._read_macros()
            updated = [m for m in macros if m.get("id") != macro_id]
            if len(updated) == len(macros):
                raise MachineError("Macro not found.", 404)
            atomic_json(self._macros_path(), updated, 0o600)
        return {"ok": True}

    def run_macro(self, macro_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        targets = self._resolve_farm_targets(payload.get("machineIds"))
        jitter_ms = max(0, min(1500, int(payload.get("jitterMs", 0) or 0)))
        with self._macro_lock:
            macro = next((m for m in self._read_macros() if m.get("id") == macro_id), None)
        if not macro:
            raise MachineError("Macro not found.", 404)
        steps = macro["steps"]

        def worker(machine_id: str, meta: dict[str, Any], idx: int) -> dict[str, Any]:
            st = self.machine_state(meta)
            if not st["ready"]:
                raise MachineError("Device is not running.", 409)
            if jitter_ms > 0:
                time.sleep(random.uniform(0, jitter_ms / 1000.0))
            executed = 0
            for step in steps:
                kind = step.get("type")
                if kind == "wait":
                    ms = max(50, min(15000, int(step.get("ms", 500))))
                    time.sleep(ms / 1000.0)
                elif kind == "key":
                    self.send_key(machine_id, str(step.get("key") or "home"))
                elif kind == "text":
                    self.send_text(machine_id, str(step.get("text") or ""), bool(step.get("submit", False)))
                elif kind == "url":
                    self.farm_open_url({"machineIds": [machine_id], "url": step.get("url")})
                elif kind == "launch":
                    self.package_action(machine_id, str(step.get("package") or ""), "launch")
                elif kind == "shell":
                    self.run_shell(machine_id, str(step.get("command") or ""))
                elif kind in {"tap", "swipe"}:
                    self.farm_touch(
                        {
                            "machineIds": [machine_id],
                            "type": kind,
                            "x": step.get("x", 0.5),
                            "y": step.get("y", 0.5),
                            "x2": step.get("x2", step.get("x", 0.5)),
                            "y2": step.get("y2", step.get("y", 0.5)),
                            "durationMs": step.get("durationMs", 280),
                            "jitterPx": step.get("jitterPx", 0),
                        }
                    )
                executed += 1
            return {"steps": executed}

        return self._run_farm_parallel(targets, worker)

    # ------------------------------------------------------------------
    # Community Real-Device Fingerprints & Android ISO Management
    # ------------------------------------------------------------------
    def list_isos(self) -> list[dict[str, Any]]:
        items = fingerprints.list_isos()
        with self._iso_dl_lock:
            dl_snapshot = {k: dict(v) for k, v in self._iso_downloads.items()}
        for item in items:
            dl = dl_snapshot.get(item["id"])
            item["download"] = dl if dl else None
        return items

    def download_iso(self, iso_id: str) -> dict[str, Any]:
        entry = next((item for item in fingerprints.ISO_CATALOG if item["id"] == iso_id), None)
        if not entry:
            raise MachineError("Unknown ISO catalog entry.", 404)
        ISO_DIR.mkdir(parents=True, exist_ok=True)
        target = ISO_DIR / entry["filename"]
        if target.is_file() and target.stat().st_size > 100 * 1024 * 1024:
            return {"ok": True, "status": "installed", "isoId": iso_id}
        with self._iso_dl_lock:
            cur = self._iso_downloads.get(iso_id)
            if cur and cur.get("status") == "downloading":
                return {"ok": True, "status": "downloading", "isoId": iso_id, "download": dict(cur)}
            self._iso_downloads[iso_id] = {
                "status": "downloading",
                "percent": 0,
                "downloadedMb": 0,
                "totalMb": 0,
                "error": None,
            }

        def worker() -> None:
            part = target.with_suffix(".iso.part")
            try:
                req = urllib.request.Request(entry["url"], headers={"User-Agent": "DroidCloud/2.0"})
                with urllib.request.urlopen(req, timeout=60) as resp:
                    total = int(resp.headers.get("Content-Length") or 0)
                    done = 0
                    with part.open("wb") as out:
                        while True:
                            chunk = resp.read(1024 * 1024)
                            if not chunk:
                                break
                            out.write(chunk)
                            done += len(chunk)
                            pct = round(done * 100 / total) if total > 0 else 0
                            with self._iso_dl_lock:
                                self._iso_downloads[iso_id] = {
                                    "status": "downloading",
                                    "percent": min(99, pct),
                                    "downloadedMb": round(done / (1024 * 1024)),
                                    "totalMb": round(total / (1024 * 1024)) if total else 0,
                                    "error": None,
                                }
                os.chmod(part, 0o644)
                part.replace(target)
                with self._iso_dl_lock:
                    self._iso_downloads[iso_id] = {
                        "status": "installed",
                        "percent": 100,
                        "downloadedMb": round(target.stat().st_size / (1024 * 1024)),
                        "totalMb": round(target.stat().st_size / (1024 * 1024)),
                        "error": None,
                    }
            except Exception as exc:
                part.unlink(missing_ok=True)
                with self._iso_dl_lock:
                    self._iso_downloads[iso_id] = {
                        "status": "error",
                        "percent": 0,
                        "downloadedMb": 0,
                        "totalMb": 0,
                        "error": str(exc)[:220],
                    }

        threading.Thread(target=worker, daemon=True, name=f"iso-dl-{iso_id}").start()
        return {"ok": True, "status": "downloading", "isoId": iso_id}

    def list_fingerprints(self) -> dict[str, Any]:
        return {
            "ok": True,
            "fingerprints": fingerprints.list_profiles(),
            "carriers": fingerprints.CARRIERS,
        }

    def sync_fingerprints(self) -> dict[str, Any]:
        synced = fingerprints.sync_official_pixel_fingerprints()
        return {
            "ok": True,
            "syncedCount": len(synced),
            "synced": synced,
            "fingerprints": fingerprints.list_profiles(),
        }

    def import_fingerprint(self, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            entry = fingerprints.import_custom_profile(payload)
        except (ValueError, json.JSONDecodeError) as exc:
            raise MachineError(str(exc), 400) from exc
        return {"ok": True, "profile": entry, "fingerprints": fingerprints.list_profiles()}

    def delete_fingerprint(self, profile_id: str) -> dict[str, Any]:
        if not fingerprints.delete_custom_profile(profile_id):
            raise MachineError("Custom fingerprint profile not found.", 404)
        return {"ok": True, "fingerprints": fingerprints.list_profiles()}

    def apply_identity(self, machine_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        metadata = self._load(machine_id)
        profile_id = str(payload.get("profileId") or "random").strip()
        carrier_id = str(
            payload.get("carrierId")
            or (metadata.get("identity") or {}).get("carrierId")
            or "telkomsel"
        ).strip()
        randomize_ids = bool(payload.get("randomize", False))
        reboot = bool(payload.get("reboot", False))

        if profile_id.lower() == "random":
            profile = fingerprints.pick_random_profile(int(metadata.get("apiLevel") or 35))
            randomize_ids = True
        else:
            profile = fingerprints.get_profile(profile_id)
            if not profile:
                raise MachineError(f"Unknown device identity profile: {profile_id}", 404)

        identity = fingerprints.build_identity_record(
            profile,
            carrier_id=carrier_id,
            existing=metadata.get("identity"),
            randomize_ids=randomize_ids,
        )

        is_iso = metadata.get("backend") == "iso"
        ramdisk_upgraded = False
        if not is_iso and not metadata.get("root"):
            ramdisk = self._ramdisk_for_api(int(metadata.get("apiLevel", 35)))
            if not ramdisk:
                raise MachineError(
                    "Real Device Identity on Android Emulator requires Android 15 or 16 (Magisk ramdisk). "
                    "Switch to an Android 15/16 device or a Community ISO VM.",
                    409,
                )
            metadata["root"] = True
            ramdisk_upgraded = True

        metadata["identity"] = identity
        self._save(metadata)

        state = self.machine_state(metadata)
        applied_live = False
        if state["ready"]:
            serial = self._adb_serial(metadata)
            if is_iso:
                self._inject_iso_identity(serial, identity, randomize_ids=randomize_ids)
                applied_live = True
            elif not ramdisk_upgraded:
                self._install_avd_identity_module(serial, identity, apply_live=True)
                applied_live = True
            if reboot or ramdisk_upgraded:
                self.restart_machine(machine_id)
        return {
            "ok": True,
            "identity": identity,
            "appliedLive": applied_live,
            "rebooting": bool(state["ready"] and (reboot or ramdisk_upgraded)),
            "ramdiskUpgraded": ramdisk_upgraded,
            "machine": self.get_machine(machine_id),
        }

    def audit_identity(self, machine_id: str) -> dict[str, Any]:
        metadata = self._load(machine_id)
        state = self.machine_state(metadata)
        backend = metadata.get("backend", "avd")
        if not state["ready"]:
            return {
                "ok": True,
                "ready": False,
                "backend": backend,
                "configured": metadata.get("identity"),
                "audit": None,
            }
        serial = self._adb_serial(metadata)
        script = (
            'echo "model=$(getprop ro.product.model)";'
            'echo "brand=$(getprop ro.product.brand)";'
            'echo "manufacturer=$(getprop ro.product.manufacturer)";'
            'echo "device=$(getprop ro.product.device)";'
            'echo "product=$(getprop ro.product.name)";'
            'echo "fingerprint=$(getprop ro.build.fingerprint)";'
            'echo "securityPatch=$(getprop ro.build.version.security_patch)";'
            'echo "tags=$(getprop ro.build.tags)";'
            'echo "type=$(getprop ro.build.type)";'
            'echo "characteristics=$(getprop ro.build.characteristics)";'
            'echo "hardware=$(getprop ro.hardware)";'
            'echo "board=$(getprop ro.product.board)";'
            'echo "socManufacturer=$(getprop ro.soc.manufacturer)";'
            'echo "socModel=$(getprop ro.soc.model)";'
            'echo "qemu=$(getprop ro.kernel.qemu)";'
            'echo "bootQemu=$(getprop ro.boot.qemu)";'
            'echo "serial=$(getprop ro.serialno)";'
            'echo "verifiedBoot=$(getprop ro.boot.verifiedbootstate)";'
            'echo "carrier=$(getprop gsm.operator.alpha)";'
            'echo "deviceName=$(settings get global device_name 2>/dev/null)";'
            'echo "androidId=$(settings get secure android_id 2>/dev/null)"'
        )
        if backend == "iso":
            script += (
                ';echo "dmiSysVendor=$(cat /sys/class/dmi/id/sys_vendor 2>/dev/null)";'
                'echo "dmiProductName=$(cat /sys/class/dmi/id/product_name 2>/dev/null)";'
                'echo "dmiBoardName=$(cat /sys/class/dmi/id/board_name 2>/dev/null)";'
                'echo "dmiBiosVendor=$(cat /sys/class/dmi/id/bios_vendor 2>/dev/null)";'
                'echo "dmiChassisVendor=$(cat /sys/class/dmi/id/chassis_vendor 2>/dev/null)";'
                'echo "cpuHypervisor=$(grep -q hypervisor /proc/cpuinfo 2>/dev/null && echo 1 || echo 0)";'
                'echo "macAddress=$(cat /sys/class/net/wlan0/address 2>/dev/null || cat /sys/class/net/eth0/address 2>/dev/null)";'
                'echo "netTransport=$(dumpsys connectivity 2>/dev/null | grep -m1 -oE \'Transports: [A-Z|_]+\' | cut -d" " -f2)";'
                'echo "emulatedStorage=$(sm list-volumes all 2>/dev/null | grep -q \'^emulated.* mounted\' && echo mounted || echo missing)";'
                'echo "publicVolumes=$(sm list-volumes all 2>/dev/null | grep -c \'^public\')";'
                'echo "opticalDrives=$(ls /sys/block 2>/dev/null | grep -c \'^sr\')";'
                'echo "cpuinfoMounts=$(grep -c \' /proc/cpuinfo \' /proc/mounts)";'
                'echo "telephonyFeature=$(pm list features 2>/dev/null | grep -q \'feature:android.hardware.telephony$\' && echo 1 || echo 0)";'
                'echo "imeiParcel=$(service call iphonesubinfo 1 s16 com.android.shell 2>/dev/null | tr \'\\n\' \' \')";'
                'echo "bootloader=$(getprop ro.bootloader)";'
                'echo "baseband=$(getprop gsm.version.baseband)";'
                # Real app-visible leak: x86 host CPU showing through /proc/cpuinfo on an
                # ARM phone identity. BlissOS over-mounts an ARM cpuinfo to hide it; apps
                # cannot see the mount count, only this content.
                'echo "cpuinfoX86Leak=$(grep -ciE \'genuineintel|authenticamd|^vendor_id\' /proc/cpuinfo)";'
                'echo "batteryPresent=$(dumpsys battery 2>/dev/null | grep -m1 present: | awk \'{print $2}\')";'
                'echo "batteryLevel=$(dumpsys battery 2>/dev/null | grep -m1 level: | awk \'{print $2}\')";'
                'echo "selinux=$(getenforce 2>/dev/null)";'
                'echo "ksuActive=$([ -x /data/adb/ksud ] && echo 1 || echo 0)";'
                'echo "zygiskActive=$( (pidof zygisk-ptrace64 >/dev/null 2>&1 || pidof zygiskd64 >/dev/null 2>&1) && echo 1 || echo 0 )";'
                'echo "trickyStoreActive=$(ps -A 2>/dev/null | grep -q TrickyStore && echo 1 || echo 0)";'
                'echo "unppuActive=$([ -x /data/adb/modules/droidcloud_unppu/zygisk/x86_64.so ] && echo 1 || echo 0)";'
                'echo "pifActive=$([ -s /data/adb/modules/playintegrityfix/custom.pif.prop ] && echo 1 || echo 0)";'
                'echo "gsfAndroidId=$(sqlite3 /data/data/com.google.android.gsf/databases/gservices.db '
                '\'select value from main where name="android_id"\' 2>/dev/null)";'
                'echo "playCertified=$(sqlite3 /data/data/com.google.android.gsf/databases/gservices.db '
                '\'select value from main where name="uncertified_status"\' 2>/dev/null | grep -q 1 && echo 0 || echo 1)";'
                'echo "leakyPropCount=$(getprop | grep -iEc \'bliss|android-x86|qemu|goldfish|ranchu\' || echo 0)"'
            )
        res = subprocess.run(
            [str(ADB), "-s", serial, "shell", script],
            capture_output=True,
            text=True,
            timeout=12,
            check=False,
        )
        props: dict[str, str] = {}
        for line in (res.stdout or "").splitlines():
            if "=" in line:
                k, v = line.split("=", 1)
                props[k.strip()] = v.strip()
        if "imeiParcel" in props:
            parcel_raw = props.pop("imeiParcel")
            quoted = "".join(re.findall(r"'([^']*)'", parcel_raw))
            props["imei"] = re.sub(r"\D", "", quoted)[:15]
        audit = fingerprints.score_identity_audit(props, backend=backend)
        return {
            "ok": True,
            "ready": True,
            "backend": backend,
            "configured": metadata.get("identity"),
            "audit": audit,
        }

    def farm_apply_identity(self, payload: dict[str, Any]) -> dict[str, Any]:
        targets = self._resolve_farm_targets(payload.get("machineIds"))
        profile_id = str(payload.get("profileId") or "random").strip()
        carrier_id = str(payload.get("carrierId") or "telkomsel").strip()
        randomize = bool(payload.get("randomize", True))
        reboot = bool(payload.get("reboot", False))

        def worker(machine_id: str, meta: dict[str, Any], idx: int) -> dict[str, Any]:
            res = self.apply_identity(
                machine_id,
                {
                    "profileId": profile_id,
                    "carrierId": carrier_id,
                    "randomize": randomize,
                    "reboot": False,
                },
            )
            ident = res["identity"]
            return {"label": ident["label"], "model": ident["model"], "serial": ident["serial"]}

        result = self._run_farm_parallel(targets, worker)
        if reboot:
            running_ids = [
                mid for mid in targets
                if self.machine_state(self._load(mid)).get("ready")
            ]
            if running_ids:
                threading.Thread(
                    target=self._schedule_staggered_power,
                    args=(running_ids, "restart", 2.5),
                    daemon=True,
                ).start()
                result["rebootScheduled"] = len(running_ids)
        return result

    def start_autostart(self) -> None:
        for metadata in self._all_metadata():
            if not metadata.get("autostart"):
                continue
            try:
                self.start_machine(metadata["id"])
            except Exception as exc:
                self._last_errors[metadata["id"]] = str(exc)

    def shutdown(self) -> None:
        with self.lock:
            metadata_items = self._all_metadata()
        for metadata in metadata_items:
            try:
                self._stop_runtime(metadata["id"], metadata)
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Remote audio: scrcpy-server (audio only) -> TCP relay -> websockify
    # ------------------------------------------------------------------
    AUDIO_PORT_OFFSET = 1100
    AUDIO_MAX_SESSIONS = 4

    @classmethod
    def _audio_port(cls, metadata: dict[str, Any]) -> int:
        return int(metadata["vncPort"]) + cls.AUDIO_PORT_OFFSET

    def _scrcpy_server_version(self) -> str:
        cached = self.__dict__.get("_scrcpy_version")
        if cached:
            return cached
        version = "3.3.4"
        try:
            out = subprocess.run([str(SCRCPY), "--version"], capture_output=True, text=True, timeout=10, check=False).stdout
            match = re.search(r"scrcpy\s+(\d+(?:\.\d+)+)", out or "")
            if match:
                version = match.group(1)
        except (OSError, subprocess.TimeoutExpired):
            pass
        self.__dict__["_scrcpy_version"] = version
        return version

    def audio_endpoint(self, machine_id: str) -> dict[str, Any]:
        metadata = self._load(machine_id)
        state = self.machine_state(metadata)
        if state.get("adbState") != "device":
            raise MachineError("Device must be running to stream audio.", 409)
        if not SCRCPY_SERVER_JAR.is_file():
            raise MachineError("scrcpy-server is not installed on the host.", 500)
        if int(metadata.get("apiLevel", 30) or 30) < 30 and metadata.get("backend") != "iso":
            raise MachineError("Audio streaming requires Android 11 or newer.", 409)
        self._ensure_audio_listener(machine_id, metadata)
        self._write_tokens()
        return {"ok": True, "token": f"{metadata['consoleToken']}-audio"}

    def _ensure_audio_listener(self, machine_id: str, metadata: dict[str, Any]) -> None:
        listeners = self.__dict__.setdefault("_audio_listeners", {})
        port = self._audio_port(metadata)
        with self.lock:
            current = listeners.get(machine_id)
            if current and current["port"] == port and current["thread"].is_alive():
                return
            server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                server.bind(("127.0.0.1", port))
            except OSError as exc:
                server.close()
                raise MachineError(f"Audio port {port} is busy: {exc}", 500) from exc
            server.listen(8)
            entry = {"port": port, "socket": server, "sessions": 0}
            thread = threading.Thread(
                target=self._audio_accept_loop, args=(machine_id, entry), daemon=True, name=f"audio-{machine_id}"
            )
            entry["thread"] = thread
            listeners[machine_id] = entry
            thread.start()

    def _audio_accept_loop(self, machine_id: str, entry: dict[str, Any]) -> None:
        server = entry["socket"]
        while True:
            try:
                conn, _ = server.accept()
            except OSError:
                return
            with self.lock:
                busy = entry["sessions"] >= self.AUDIO_MAX_SESSIONS
                if not busy:
                    entry["sessions"] += 1
            if busy:
                conn.close()
                continue
            threading.Thread(
                target=self._audio_session, args=(machine_id, entry, conn), daemon=True
            ).start()

    def _audio_session(self, machine_id: str, entry: dict[str, Any], client: socket.socket) -> None:
        proc: subprocess.Popen | None = None
        device: socket.socket | None = None
        serial = ""
        forward_port = 0
        try:
            metadata = self._load(machine_id)
            serial = self._adb_serial(metadata)
            # Optional 1-byte codec request from the browser: "r" = raw PCM, else Opus.
            codec = "opus"
            client.settimeout(1.5)
            try:
                first = client.recv(1)
                if first == b"r":
                    codec = "raw"
            except socket.timeout:
                pass
            client.settimeout(None)
            jar = "/data/local/tmp/dc-audio-server.jar"
            subprocess.run(
                [str(ADB), "-s", serial, "push", str(SCRCPY_SERVER_JAR), jar],
                capture_output=True, timeout=20, check=False,
            )
            scid = "%08x" % secrets.randbits(31)
            fwd = subprocess.run(
                [str(ADB), "-s", serial, "forward", "tcp:0", f"localabstract:scrcpy_{scid}"],
                capture_output=True, text=True, timeout=10, check=False,
            )
            forward_port = int((fwd.stdout or "0").strip() or 0)
            if not forward_port:
                return
            options = (
                f"scid={scid} log_level=warn video=false control=false audio=true "
                f"audio_codec={codec} audio_bit_rate=128000 tunnel_forward=true "
                "send_device_meta=false cleanup=false"
            )
            proc = subprocess.Popen(
                [
                    str(ADB), "-s", serial, "shell",
                    f"CLASSPATH={jar} app_process / com.genymobile.scrcpy.Server {self._scrcpy_server_version()} {options}",
                ],
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline and proc.poll() is None:
                candidate = socket.create_connection(("127.0.0.1", forward_port), timeout=3)
                candidate.settimeout(3)
                try:
                    dummy = candidate.recv(1)
                except (socket.timeout, OSError):
                    dummy = b""
                if dummy:
                    device = candidate
                    break
                candidate.close()
                time.sleep(0.2)
            if device is None:
                return
            device.settimeout(None)
            device.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            client.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            client.sendall(b"\x00")
            import select as _select
            while True:
                ready, _, _ = _select.select([device, client], [], [], 30)
                if not ready:
                    if proc.poll() is not None:
                        return
                    continue
                if client in ready:
                    if not client.recv(4096):
                        return
                if device in ready:
                    chunk = device.recv(65536)
                    if not chunk:
                        return
                    client.sendall(chunk)
        except Exception:
            return
        finally:
            for sock in (device, client):
                try:
                    if sock:
                        sock.close()
                except OSError:
                    pass
            if proc and proc.poll() is None:
                try:
                    os.killpg(proc.pid, 15)
                except OSError:
                    pass
            if serial and forward_port:
                subprocess.run(
                    [str(ADB), "-s", serial, "forward", "--remove", f"tcp:{forward_port}"],
                    capture_output=True, timeout=5, check=False,
                )
            with self.lock:
                entry["sessions"] = max(0, entry["sessions"] - 1)

    def _write_tokens(self) -> None:
        lines = []
        with self.lock:
            for metadata in self._all_metadata():
                runtime = self.runtime.get(metadata["id"], {})
                if runtime.get("phase") in {"starting", "running"}:
                    lines.append(
                        f"{metadata['consoleToken']}: 127.0.0.1:{metadata['vncPort']}"
                    )
                    lines.append(
                        f"{metadata['consoleToken']}-audio: 127.0.0.1:{self._audio_port(metadata)}"
                    )
        atomic_write(TOKEN_FILE, "\n".join(lines) + ("\n" if lines else ""), 0o600)
