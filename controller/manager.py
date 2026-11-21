#!/usr/bin/env python3
"""Android virtual machine lifecycle and console manager."""

from __future__ import annotations

import json
import os
import re
import secrets
import shutil
import socket
import subprocess
import threading
import time
import unicodedata
from pathlib import Path
from typing import Any

from settings import (
    ADB,
    APP_HOME,
    AVD_DIR,
    AVDMANAGER,
    EMULATOR,
    LOG_DIR,
    MACHINE_DIR,
    SCRCPY,
    SDK_ROOT,
    SDKMANAGER,
    SSH_USER,
    STATE_DIR,
    TOKEN_FILE,
    X11VNC,
    XVFB,
)

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


def atomic_json(path: Path, value: Any) -> None:
    atomic_write(path, json.dumps(value, indent=2, ensure_ascii=False) + "\n")


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


class MachineManager:
    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.operation_locks: dict[str, threading.Lock] = {}
        self.runtime: dict[str, dict[str, Any]] = {}
        self._adb_cache: tuple[float, dict[str, dict[str, str]]] = (0.0, {})
        self._boot_cache: dict[str, tuple[float, bool]] = {}
        self._last_errors: dict[str, str] = {}
        self._system_install_lock = threading.Lock()
        self._cpu_sample: tuple[float, int, int] | None = None
        for directory in (STATE_DIR, MACHINE_DIR, LOG_DIR):
            directory.mkdir(parents=True, exist_ok=True)
            os.chmod(directory, 0o700)
        self._bootstrap_machines()
        self._write_tokens()

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
                "ram": max(1024, int(config.get("hw.ramSize", "4096") or 4096)),
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
        serial = f"emulator-{metadata['consolePort']}"
        adb = devices.get(serial)
        alive = process_alive(emulator)
        phase = runtime.get("phase", "stopped")
        if alive:
            if phase == "error":
                phase = "error"
            elif adb and adb["state"] == "device" and self._boot_complete(serial):
                phase = "running"
            else:
                phase = "starting"
        elif phase not in {"stopped", "error"}:
            phase = "error"
        result = dict(metadata)
        result.update(
            {
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
                "sshUser": SSH_USER,
                "error": runtime.get("error") or self._last_errors.get(machine_id),
            }
        )
        return result

    @staticmethod
    def _screen_details(metadata: dict[str, Any]) -> dict[str, int] | None:
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
        host_cpu = min(os.cpu_count() or 1, EMULATOR_MAX_CPUS)
        cpu_percent = self._host_cpu_percent()
        ram_used_percent = (
            round((total_ram - available_ram) / total_ram * 100)
            if total_ram and available_ram
            else 0
        )
        return {
            "machines": machines,
            "summary": {
                "total": len(machines),
                "running": running,
                "stopped": len(machines) - running,
                "cpuAllocated": cpu_allocated,
                "ramAllocated": ram_allocated,
                "hostCpu": host_cpu,
                "hostRam": total_ram,
                "hostRamAvailable": available_ram,
                "cpuPercent": cpu_percent,
                "ramPercent": ram_used_percent,
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

    def create_machine(self, payload: dict[str, Any]) -> dict[str, Any]:
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
        root_requested = bool(payload.get("root", False))
        if root_requested:
            api_level = 36
            system = next(item for item in SYSTEMS if item["api"] == 36)
            selected_image = "system-images;android-36;google_apis;x86_64"
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
                "PlayStore.enabled": "no" if root_requested else "yes",
                "hw.lcd.width": str(profile["width"]),
                "hw.lcd.height": str(profile["height"]),
                "hw.lcd.density": str(profile["density"]),
                "hw.lcd.depth": "32",
            },
        )
        metadata = {
            "id": machine_id,
            "name": name,
            "device": device,
            "androidVersion": system["label"],
            "apiLevel": api_level,
            "systemImage": selected_image,
            "root": root_requested,
            "cpu": cpu,
            "ram": ram,
            "storage": storage,
            "consolePort": ports["consolePort"],
            "adbPort": ports["adbPort"],
            "vncPort": ports["vncPort"],
            "displayNumber": ports["displayNumber"],
            "consoleToken": secrets.token_urlsafe(32),
            "autostart": bool(payload.get("autostart", False)),
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
        return process

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
                    "-rfbport",
                    str(vnc_port),
                    "-nopw",
                    "-quiet",
                ],
                environment,
            )
        self._write_tokens()

    def _ensure_mirror(self, machine_id: str, metadata: dict[str, Any]) -> None:
        runtime = self._runtime_for(machine_id)
        if process_alive(runtime.get("scrcpy")):
            return
        environment = self._environment()
        environment["DISPLAY"] = f":{metadata['displayNumber']}"
        runtime["scrcpy"] = self._spawn(
            machine_id,
            "mirror",
            [
                str(SCRCPY),
                "-s",
                f"emulator-{metadata['consolePort']}",
                "--no-audio",
                "--stay-awake",
                "--max-size=1200",
                "--max-fps=30",
                "--video-bit-rate=4M",
                "--window-x=0",
                "--window-y=0",
                "--window-width=540",
                "--window-height=1200",
                "--window-borderless",
            ],
            environment,
        )
        runtime["mirrorStarted"] = True

    def _watch_boot(self, machine_id: str, generation: int) -> None:
        metadata = self._load(machine_id)
        serial = f"emulator-{metadata['consolePort']}"
        deadline = time.monotonic() + 240
        while time.monotonic() < deadline:
            runtime = self._runtime_for(machine_id)
            if runtime.get("generation") != generation:
                return
            if not process_alive(runtime.get("emulator")):
                self._mark_error(machine_id, "Proses emulator berhenti saat booting.")
                return
            devices = self._adb_devices(force=True)
            adb = devices.get(serial)
            if adb and adb["state"] == "device":
                try:
                    self._ensure_mirror(machine_id, metadata)
                except OSError as exc:
                    self._mark_error(machine_id, f"Console layar gagal dimulai: {exc}")
                    return
                if self._boot_complete(serial, force=True):
                    if metadata.get("root"):
                        try:
                            result = subprocess.run(
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
                            self._mark_error(
                                machine_id,
                                f"Root gagal diverifikasi: {exc}",
                            )
                            return
                        if result.returncode != 0 or "uid=0(" not in identity.stdout:
                            detail = (result.stderr or result.stdout).strip()
                            self._mark_error(
                                machine_id,
                                detail
                                or "Image ini tidak mendukung akses root.",
                            )
                            return
                    with self.lock:
                        if runtime.get("generation") == generation:
                            runtime["phase"] = "running"
                            runtime["error"] = None
                    self._last_errors.pop(machine_id, None)
                    return
            time.sleep(2)
        self._mark_error(machine_id, "Booting melewati batas waktu 4 menit.")

    def _mark_error(self, machine_id: str, message: str) -> None:
        with self.lock:
            runtime = self._runtime_for(machine_id)
            runtime["phase"] = "error"
            runtime["error"] = message
            self._last_errors[machine_id] = message

    def start_machine(self, machine_id: str) -> dict[str, Any]:
        metadata = self._load(machine_id)
        operation_lock = self._operation_lock(machine_id)
        if not operation_lock.acquire(blocking=False):
            raise MachineError("Operasi lain sedang berjalan pada mesin ini.", 409)
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
                        "-gpu",
                        "swiftshader",
                        "-accel",
                        "on",
                        "-no-snapshot",
                    ]
                    + (["-writable-system"] if metadata.get("root") else []),
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
        serial = f"emulator-{metadata['consolePort']}"
        try:
            subprocess.run(
                [str(ADB), "-s", serial, "emu", "kill"],
                capture_output=True,
                text=True,
                timeout=6,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            pass
        deadline = time.monotonic() + 10
        while process_alive(runtime.get("emulator")) and time.monotonic() < deadline:
            time.sleep(0.2)
        terminate_group(runtime.get("scrcpy"))
        terminate_group(runtime.get("vnc"))
        terminate_group(runtime.get("xvfb"))
        terminate_group(runtime.get("emulator"))
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
            raise MachineError("Matikan mesin sumber sebelum membuat clone.", 409)
        name = self._validate_name(payload.get("name"))
        new_id = self._unique_id(name)
        ports = self._allocate_ports()
        source_avd = AVD_DIR / f"{machine_id}.avd"
        source_ini = AVD_DIR / f"{machine_id}.ini"
        target_avd = AVD_DIR / f"{new_id}.avd"
        target_ini = AVD_DIR / f"{new_id}.ini"
        if not source_avd.is_dir() or not source_ini.exists():
            raise MachineError("File AVD sumber tidak ditemukan.", 404)
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
            raise MachineError("Clone AVD gagal dibuat.", 500) from exc
        metadata = {
            **source,
            "id": new_id,
            "name": name,
            "consolePort": ports["consolePort"],
            "adbPort": ports["adbPort"],
            "vncPort": ports["vncPort"],
            "displayNumber": ports["displayNumber"],
            "consoleToken": secrets.token_urlsafe(32),
            "autostart": bool(payload.get("autostart", False)),
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
            raise MachineError("Mesin sedang menjalankan operasi lain.", 409)
        try:
            self._stop_runtime(machine_id, metadata)
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
                raise MachineError("AVD gagal dihapus.", 500)
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
                f"emulator-{metadata['consolePort']}",
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
        state = self.machine_state(metadata)
        if not state["ready"]:
            raise MachineError("Mesin harus menyala dan selesai booting.", 409)
        key_events = {
            "back": "4",
            "home": "3",
            "recents": "187",
            "volume-up": "24",
            "power": "26",
        }
        if key not in key_events:
            raise MachineError("Tombol perangkat tidak dikenal.")
        result = subprocess.run(
            [
                str(ADB),
                "-s",
                f"emulator-{metadata['consolePort']}",
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
        result = subprocess.run(
            [
                str(ADB),
                "-s",
                f"emulator-{metadata['consolePort']}",
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
            raise MachineError(detail or "Lokasi GPS gagal diubah.", 500)
        location = {
            "latitude": round(lat, 6),
            "longitude": round(lon, 6),
            "label": str(label or "").strip()[:60],
            "updatedAt": now(),
        }
        metadata["lastLocation"] = location
        self._save(metadata)
        return {"ok": True, "location": location, "machine": self.machine_state(metadata)}

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

    def screenshot(self, machine_id: str) -> bytes:
        metadata = self._load(machine_id)
        state = self.machine_state(metadata)
        if not state["ready"]:
            raise MachineError("Mesin harus menyala dan selesai booting.", 409)
        try:
            subprocess.run(
                [
                    str(ADB),
                    "-s",
                    f"emulator-{metadata['consolePort']}",
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
                f"emulator-{metadata['consolePort']}",
                "exec-out",
                "screencap",
                "-p",
            ],
            capture_output=True,
            timeout=20,
            check=False,
        )
        if result.returncode != 0 or not result.stdout.startswith(b"\x89PNG\r\n\x1a\n"):
            detail = result.stderr.decode("utf-8", "replace").strip()[:250]
            raise MachineError(detail or "Screenshot gagal dibuat.", 500)
        return result.stdout

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

    def _write_tokens(self) -> None:
        lines = []
        with self.lock:
            for metadata in self._all_metadata():
                runtime = self.runtime.get(metadata["id"], {})
                if runtime.get("phase") in {"starting", "running"}:
                    lines.append(
                        f"{metadata['consoleToken']}: 127.0.0.1:{metadata['vncPort']}"
                    )
        atomic_write(TOKEN_FILE, "\n".join(lines) + ("\n" if lines else ""), 0o600)
