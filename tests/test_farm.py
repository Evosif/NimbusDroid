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
import server  # noqa: E402


class FarmTests(unittest.TestCase):
    def _fake_manager(self, machines: dict[str, dict]) -> manager.MachineManager:
        m = object.__new__(manager.MachineManager)
        m.lock = threading.RLock()
        m._macro_lock = threading.Lock()
        m.runtime = {}
        m._last_errors = {}
        m._shot_cache = {}
        m._load = lambda mid: machines[mid]
        m.machine_state = lambda md: {
            **md,
            "ready": True,
            "running": True,
            "phase": "running",
            "screen": {"width": 1080, "height": 2400, "density": 420},
        }
        return m

    def test_farm_touch_normalizes_coords_for_portrait_and_landscape(self) -> None:
        m = self._fake_manager(
            {
                "vm1": {"id": "vm1", "name": "VM 1", "consolePort": 5554},
                "vm2": {"id": "vm2", "name": "VM 2", "consolePort": 5556},
            }
        )
        cmds: list[list[str]] = []

        def fake_run(cmd: list[str], **kwargs) -> SimpleNamespace:
            cmds.append(cmd)
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        with mock.patch.object(manager.subprocess, "run", side_effect=fake_run):
            res = m.farm_touch(
                {
                    "machineIds": ["vm1", "vm2"],
                    "type": "tap",
                    "x": 0.5,
                    "y": 0.25,
                    "landscape": False,
                }
            )
        self.assertEqual(res["succeeded"], 2)
        self.assertEqual(res["failed"], 0)
        # farm_touch may probe the live display geometry first; only the input events matter here
        taps = [cmd for cmd in cmds if cmd[-4:-2] == ["input", "tap"]]
        self.assertEqual(len(taps), 2)
        for cmd in taps:
            self.assertEqual(cmd[-4:], ["input", "tap", "540", "600"])

    def test_farm_text_per_line_distributes_unique_lines(self) -> None:
        m = self._fake_manager(
            {
                "vm1": {"id": "vm1", "name": "VM 1", "consolePort": 5554},
                "vm2": {"id": "vm2", "name": "VM 2", "consolePort": 5556},
                "vm3": {"id": "vm3", "name": "VM 3", "consolePort": 5558},
            }
        )
        sent: dict[str, str] = {}
        m.send_text = lambda mid, text, submit=False: sent.__setitem__(mid, text) or {"ok": True}

        res = m.farm_text(
            {
                "machineIds": ["vm1", "vm2", "vm3"],
                "mode": "per-line",
                "text": "user1@example.com\nuser2@example.com",
            }
        )
        self.assertEqual(res["succeeded"], 3)
        self.assertEqual(sent["vm1"], "user1@example.com")
        self.assertEqual(sent["vm2"], "user2@example.com")
        self.assertNotIn("vm3", sent)
        self.assertTrue(res["results"][2].get("skipped"))

    def test_farm_gps_scatter_stays_within_bounds(self) -> None:
        m = self._fake_manager(
            {
                "vm1": {"id": "vm1", "name": "VM 1", "consolePort": 5554},
                "vm2": {"id": "vm2", "name": "VM 2", "consolePort": 5556},
            }
        )
        coords: list[tuple[float, float]] = []

        def fake_loc(mid, lat, lon, label=""):
            coords.append((lat, lon))
            return {"ok": True, "location": {"latitude": lat, "longitude": lon, "label": label}}

        m.set_location = fake_loc
        res = m.farm_gps(
            {
                "machineIds": ["vm1", "vm2"],
                "latitude": -6.2088,
                "longitude": 106.8456,
                "scatterMeters": 1000,
                "label": "Jakarta",
            }
        )
        self.assertEqual(res["succeeded"], 2)
        for lat, lon in coords:
            self.assertAlmostEqual(lat, -6.2088, delta=0.02)
            self.assertAlmostEqual(lon, 106.8456, delta=0.02)

    def test_macros_crud_and_run(self) -> None:
        m = self._fake_manager({"vm1": {"id": "vm1", "name": "VM 1", "consolePort": 5554}})
        actions: list[str] = []
        m.send_key = lambda mid, key: actions.append(f"key:{key}") or {"ok": True}
        m.send_text = lambda mid, text, submit=False: actions.append(f"text:{text}") or {"ok": True}

        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(manager, "STATE_DIR", Path(tmp)):
                created = m.save_macro(
                    {
                        "name": "Quick Test",
                        "steps": [
                            {"type": "key", "key": "home"},
                            {"type": "text", "text": "hello"},
                        ],
                    }
                )
                mid = created["macro"]["id"]
                listed = m.list_macros()["macros"]
                self.assertTrue(any(item["id"] == mid for item in listed))

                run_res = m.run_macro(mid, {"machineIds": ["vm1"]})
                self.assertEqual(run_res["succeeded"], 1)
                self.assertEqual(actions, ["key:home", "text:hello"])

                m.delete_macro(mid)
                after = m.list_macros()["macros"]
                self.assertFalse(any(item["id"] == mid for item in after))

    def test_api_key_lifecycle(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            keys_file = Path(tmp) / "api_keys.json"
            with (
                mock.patch.object(server, "STATE_DIR", Path(tmp)),
                mock.patch.object(server, "API_KEYS_FILE", keys_file),
            ):
                created = server.create_api_key("CI Bot")
                token = created["token"]
                self.assertTrue(token.startswith("dc_"))
                self.assertTrue(server.verify_api_key(token))
                self.assertFalse(server.verify_api_key(token + "bad"))
                self.assertEqual(len(server.list_api_keys()), 1)
                server.delete_api_key(created["id"])
                self.assertFalse(server.verify_api_key(token))
                self.assertEqual(len(server.list_api_keys()), 0)


if __name__ == "__main__":
    unittest.main()
