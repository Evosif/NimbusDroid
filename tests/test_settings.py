from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class SettingsTests(unittest.TestCase):
    def test_environment_overrides_runtime_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            environment = {
                **os.environ,
                "DROID_CLOUD_HOME": str(root / "home"),
                "DROID_CLOUD_SDK_ROOT": str(root / "sdk"),
                "DROID_CLOUD_AVD_HOME": str(root / "avd"),
                "DROID_CLOUD_STATE_DIR": str(root / "state"),
                "DROID_CLOUD_CACHE_DIR": str(root / "cache"),
                "DROID_CLOUD_API_PORT": "9091",
                "DROID_CLOUD_SSH_USER": "operator",
            }
            script = (
                "import settings;"
                "print(settings.SDK_ROOT);"
                "print(settings.AVD_DIR);"
                "print(settings.STATE_DIR);"
                "print(settings.CACHE_DIR);"
                "print(settings.API_PORT);"
                "print(settings.SSH_USER)"
            )
            result = subprocess.run(
                [sys.executable, "-c", script],
                cwd=ROOT / "controller",
                env=environment,
                capture_output=True,
                text=True,
                check=True,
            )
            self.assertEqual(
                result.stdout.splitlines(),
                [
                    str(root / "sdk"),
                    str(root / "avd"),
                    str(root / "state"),
                    str(root / "cache"),
                    "9091",
                    "operator",
                ],
            )


if __name__ == "__main__":
    unittest.main()
