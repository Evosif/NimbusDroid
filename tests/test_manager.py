from __future__ import annotations

import sys
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "controller"))

import manager  # noqa: E402


class RootBootTests(unittest.TestCase):
    def test_root_is_enabled_before_console_mirror_starts(self) -> None:
        machine = object.__new__(manager.MachineManager)
        machine.lock = threading.RLock()
        machine.runtime = {
            "root-test": {
                "generation": 7,
                "emulator": object(),
                "scrcpy": None,
            }
        }
        machine._last_errors = {}

        metadata = {
            "id": "root-test",
            "consolePort": 5560,
            "root": True,
        }
        serial = "emulator-5560"
        events: list[str] = []

        machine._load = lambda machine_id: metadata
        machine._adb_devices = lambda force=False: {serial: {"state": "device"}}
        machine._boot_complete = lambda current_serial, force=False: True
        machine._mark_error = lambda machine_id, message: events.append(
            f"error:{message}"
        )

        def ensure_mirror(machine_id: str, current_metadata: dict) -> None:
            events.append("mirror")

        def run(command: list[str], **kwargs) -> SimpleNamespace:
            if command[-1] == "root":
                events.append("root")
                return SimpleNamespace(
                    returncode=1,
                    stdout="adbd cannot run as root in production builds\n",
                    stderr="",
                )
            if command[-4:] == ["shell", "su", "-c", "id"]:
                events.append("magisk")
                return SimpleNamespace(
                    returncode=0,
                    stdout="uid=0(root) gid=0(root) context=u:r:magisk:s0\n",
                    stderr="",
                )
            if command[-1] == "id":
                events.append("identity")
                return SimpleNamespace(
                    returncode=1,
                    stdout="",
                    stderr="permission denied\n",
                )
            raise AssertionError(f"Unexpected command: {command}")

        machine._ensure_mirror = ensure_mirror

        with (
            mock.patch.object(manager, "process_alive", return_value=True),
            mock.patch.object(manager.subprocess, "run", side_effect=run),
            mock.patch.object(manager.time, "sleep", return_value=None),
        ):
            machine._watch_boot("root-test", 7)

        self.assertEqual(events, ["root", "identity", "magisk", "mirror"])
        self.assertEqual(machine.runtime["root-test"]["phase"], "running")
        self.assertIsNone(machine.runtime["root-test"]["error"])

    def test_send_text_quotes_spaces_and_runs_adb(self) -> None:
        machine = object.__new__(manager.MachineManager)
        metadata = {"id": "vm1", "consolePort": 5554}
        machine._load = lambda mid: metadata
        machine.machine_state = lambda md: {"ready": True}
        calls: list[list[str]] = []

        def fake_run(cmd: list[str], **kwargs) -> SimpleNamespace:
            calls.append(cmd)
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        with mock.patch.object(manager.subprocess, "run", side_effect=fake_run):
            res = machine.send_text("vm1", "hello world", submit=True)

        self.assertTrue(res["ok"])
        self.assertEqual(len(calls), 2)
        self.assertIn("hello%sworld", calls[0][-1])
        self.assertEqual(calls[1][-2:], ["keyevent", "66"])

    def test_package_action_rejects_invalid_package_name(self) -> None:
        machine = object.__new__(manager.MachineManager)
        metadata = {"id": "vm1", "consolePort": 5554}
        machine._load = lambda mid: metadata
        machine.machine_state = lambda md: {"ready": True}

        with self.assertRaises(manager.MachineError):
            machine.package_action("vm1", "com.bad;rm -rf /", "launch")

    def test_get_logs_tails_file_safely(self) -> None:
        machine = object.__new__(manager.MachineManager)
        machine._load = lambda mid: {"id": mid}
        with tempfile.TemporaryDirectory() as tmp:
            log_dir = Path(tmp)
            (log_dir / "vm1-emulator.log").write_text("line1\nline2\nline3\n", encoding="utf-8")
            with mock.patch.object(manager, "LOG_DIR", log_dir):
                res = machine.get_logs("vm1", "emulator", 50)
        self.assertTrue(res["ok"])
        self.assertTrue(res["exists"])
        self.assertIn("line3", res["content"])

    def test_set_battery_validates_bounds(self) -> None:
        machine = object.__new__(manager.MachineManager)
        machine._load = lambda mid: {"id": mid, "consolePort": 5554}
        machine.machine_state = lambda md: {"ready": True}
        with self.assertRaises(manager.MachineError):
            machine.set_battery("vm1", 150, True)


if __name__ == "__main__":
    unittest.main()
