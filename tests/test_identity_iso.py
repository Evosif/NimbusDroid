from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "controller"))

import fingerprints  # noqa: E402
import manager  # noqa: E402


def _luhn_valid(digits: str) -> bool:
    if len(digits) != 15 or not digits.isdigit():
        return False
    total = 0
    for idx, ch in enumerate(digits[:-1]):
        d = int(ch)
        if idx % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return (10 - (total % 10)) % 10 == int(digits[-1])


class IdentityAndIsoTests(unittest.TestCase):
    def test_builtin_profiles_and_luhn_imei(self) -> None:
        profiles = fingerprints.list_profiles()
        self.assertGreaterEqual(len(profiles), 15)
        for p in profiles:
            parsed = fingerprints.parse_fingerprint_string(p["fingerprint"])
            self.assertEqual(parsed["tags"], "release-keys")
            self.assertEqual(parsed["type"], "user")
            self.assertNotEqual((p.get("bootloader") or "").lower(), "unknown", f"Unknown bootloader in {p['id']}")
            self.assertTrue(p.get("bootloader"), f"Missing bootloader in {p['id']}")
            self.assertTrue(p.get("baseband"), f"Missing baseband in {p['id']}")
            imei = fingerprints.generate_luhn_imei(p["tac"])
            self.assertTrue(_luhn_valid(imei), f"Invalid Luhn IMEI {imei} for {p['id']}")

    def test_magisk_module_files_include_system_prop_and_safe_hals(self) -> None:
        prof = fingerprints.get_profile("pixel-9-pro-a16")
        self.assertIsNotNone(prof)
        ident = fingerprints.build_identity_record(prof, carrier_id="telkomsel")
        files = fingerprints.build_magisk_module_files(ident)
        self.assertIn("system.prop", files)
        self.assertIn("post-fs-data.sh", files)
        self.assertIn("service.sh", files)
        self.assertIn("ro.product.model=Pixel 9 Pro", files["system.prop"])
        self.assertIn("ro.build.characteristics=default", files["system.prop"])
        # Critical graphics HAL props must NOT be touched on AVD
        self.assertNotIn("ro.hardware.gralloc=", files["system.prop"])
        self.assertNotIn("ro.hardware.egl=", files["system.prop"])
        self.assertNotIn("ro.hardware.vulkan=", files["system.prop"])

    def test_audit_scoring_distinguishes_emulator_vs_real(self) -> None:
        emu_audit = fingerprints.score_identity_audit(
            {
                "model": "sdk_gphone64_x86_64",
                "brand": "google",
                "device": "emu64xa",
                "fingerprint": "google/sdk_gphone64_x86_64/emu64xa:16/BE2A/1:user/release-keys",
                "tags": "release-keys",
                "hardware": "ranchu",
                "board": "goldfish_x86_64",
                "qemu": "1",
                "bootQemu": "1",
                "characteristics": "emulator",
                "serial": "EMULATOR37X3X10X0",
                "deviceName": "sdk_gphone64_x86_64",
            },
            backend="avd",
        )
        self.assertEqual(emu_audit["status"], "emulator")
        self.assertLess(emu_audit["score"], 40)
        self.assertGreaterEqual(len(emu_audit["warnings"]), 5)

        real_audit = fingerprints.score_identity_audit(
            {
                "model": "Pixel 9 Pro",
                "brand": "google",
                "device": "caiman",
                "fingerprint": "google/caiman/caiman:16/BP4A.260205.002.A1/14624750:user/release-keys",
                "tags": "release-keys",
                "hardware": "caiman",
                "board": "caiman",
                "qemu": "",
                "bootQemu": "0",
                "characteristics": "default",
                "serial": "4HVOUUE7SWRBP7",
                "deviceName": "Pixel 9 Pro",
            },
            backend="avd",
        )
        self.assertEqual(real_audit["status"], "real")
        self.assertEqual(real_audit["score"], 100)
        self.assertEqual(real_audit["warnings"], [])

    def test_import_pif_json_and_delete(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            custom_file = Path(tmp) / "fingerprints_custom.json"
            with (
                mock.patch.object(fingerprints, "STATE_DIR", Path(tmp)),
                mock.patch.object(fingerprints, "CUSTOM_FINGERPRINTS_FILE", custom_file),
            ):
                pif = json.dumps(
                    {
                        "MANUFACTURER": "Google",
                        "MODEL": "Pixel 8 Pro",
                        "FINGERPRINT": "google/husky/husky:15/BP1A.250505.005.B1/13277630:user/release-keys",
                        "SECURITY_PATCH": "2025-05-05",
                    }
                )
                entry = fingerprints.import_custom_profile({"raw": pif})
                self.assertEqual(entry["model"], "Pixel 8 Pro")
                self.assertEqual(entry["device"], "husky")
                self.assertTrue(entry["custom"])
                self.assertIsNotNone(fingerprints.get_profile(entry["id"]))
                self.assertTrue(fingerprints.delete_custom_profile(entry["id"]))
                self.assertIsNone(fingerprints.get_profile(entry["id"]))

    def test_iso_local_discovery_and_adb_serial(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            iso_dir = Path(tmp)
            fake_iso = iso_dir / "CustomAndroid14.iso"
            fake_iso.write_bytes(b"0" * 1024)
            with mock.patch.object(fingerprints, "ISO_DIR", iso_dir):
                isos = fingerprints.list_isos()
                self.assertTrue(any(item["filename"] == "CustomAndroid14.iso" and item["local"] for item in isos))

        self.assertEqual(
            manager.MachineManager._adb_serial({"backend": "iso", "adbPort": 5565, "consolePort": 5564}),
            "127.0.0.1:5565",
        )
        self.assertEqual(
            manager.MachineManager._adb_serial({"backend": "avd", "adbPort": 5555, "consolePort": 5554}),
            "emulator-5554",
        )

    def test_iso_stealth_mac_pif_ksu_and_deep_audit(self) -> None:
        mac = fingerprints.generate_oem_mac("google", "blissos-1:ABC")
        self.assertTrue(mac.startswith("3c:28:6d:"))
        self.assertFalse(mac.startswith("52:54:00:"))
        prof = fingerprints.get_profile("pixel-7-pro-a13")
        ident = fingerprints.build_identity_record(prof, carrier_id="telkomsel")
        pif = fingerprints.build_custom_pif_prop(ident)
        self.assertIn("MANUFACTURER=Google", pif)
        self.assertIn("FINGERPRINT=google/cheetah/cheetah:", pif)
        self.assertIn("spoofVendingFinger=0", pif)
        self.assertIn("spoofBuild=0", pif)
        self.assertNotIn("raven", pif)
        ksu = fingerprints.build_ksu_iso_scripts(ident)
        self.assertIn("--delete ro.bliss.build", ksu["post-fs-data.sh"])
        # Ensure battery level is set BEFORE present=1 (prevents 0% low-battery shutdown)
        svc = ksu["service.sh"]
        self.assertLess(svc.index("set level 83"), svc.index("set present 1"))
        # Deep audit catches QEMU/SeaBIOS/hypervisor/52:54:00 leaks
        leaky = fingerprints.score_identity_audit(
            {
                "model": "Pixel 7 Pro",
                "brand": "google",
                "device": "cheetah",
                "fingerprint": ident["fingerprint"],
                "tags": "release-keys",
                "hardware": "cheetah",
                "board": "cheetah",
                "dmiBiosVendor": "SeaBIOS",
                "dmiChassisVendor": "QEMU",
                "cpuHypervisor": "1",
                "macAddress": "52:54:00:12:34:56",
                "batteryPresent": "false",
                "leakyPropCount": "6",
            },
            backend="iso",
        )
        self.assertLess(leaky["score"], 65)
        self.assertGreaterEqual(len(leaky["warnings"]), 5)

    def test_iso_boot_hook_overlay_cpio_roundtrip(self) -> None:
        hook = fingerprints.build_iso_boot_hook()
        self.assertIn("scripts/99-droidcloud", hook)
        rc = hook["droidcloud/ksu.rc"]
        self.assertIn("exec u:r:su:s0 root root -- /system/bin/timeout 90 /data/adb/ksud post-fs-data", rc)
        self.assertIn("write /sys/power/wake_lock wlan_ctrl", rc)
        self.assertIn("start dc_virt_wifi", rc)
        self.assertIn("cmd wifi connect-network VirtWifi open", rc)
        self.assertIn("ksud services", rc)
        self.assertIn("ksud boot-completed", rc)
        # The bind-mounted rc must be unmounted again once init has parsed it
        self.assertIn("umount @RC_PATH@", rc)
        self.assertIn("mount --bind /dc_boot/hw.rc", hook["scripts/99-droidcloud"])
        self.assertIn("droidcloud/radio_manifest.xml", hook)
        self.assertIn("android.hardware.radio", hook["droidcloud/radio_manifest.xml"])
        self.assertIn("@1.1::IRadio/slot1", hook["droidcloud/radio_manifest.xml"])
        self.assertIn("/tmp/fakeboot.xml", hook["scripts/99-droidcloud"])

        archive = manager.MachineManager._cpio_newc(hook)
        self.assertEqual(len(archive) % 512, 0)
        names: dict[str, bytes] = {}
        off = 0
        while True:
            self.assertEqual(archive[off:off + 6], b"070701")
            fields = [int(archive[off + 6 + i * 8: off + 14 + i * 8], 16) for i in range(13)]
            fsize, nsize = fields[6], fields[11]
            name_start = off + 110
            name = archive[name_start:name_start + nsize - 1].decode()
            data_start = name_start + nsize + (-(110 + nsize) % 4)
            data = archive[data_start:data_start + fsize]
            off = data_start + fsize + (-fsize % 4)
            if name == "TRAILER!!!":
                break
            names[name] = data
        self.assertEqual(names["droidcloud/ksu.rc"].decode(), rc)
        self.assertEqual(names["scripts/99-droidcloud"].decode(), hook["scripts/99-droidcloud"])

    def test_iso_platform_scripts_virt_wifi_and_wakelock(self) -> None:
        scripts = fingerprints.build_iso_platform_scripts()
        pfd = scripts["post-fs-data.sh"]
        self.assertIn("/sys/power/wake_lock", pfd)
        self.assertIn("make-rprivate /proc", pfd)
        self.assertIn("/system/etc/cpuinfo.arm64.txt /proc/cpuinfo", pfd)
        self.assertIn("mount --bind /dev/dc_perms /system/etc/permissions", pfd)
        self.assertIn("mount --bind /dev/dc_empty_perms.xml", pfd)
        self.assertIn("android.software.freeform_window_management.xml", pfd)
        self.assertIn("mount --bind /dev/dc_ril.so /vendor/lib64/libreference-ril.so", pfd)
        self.assertIn("/dev/dc_modem.conf", pfd)
        self.assertIn("hci_vhci.ko", pfd)
        self.assertIn("wifi_sta_factory_mac_address", pfd)
        self.assertIn("grep -qw 'VIRT_WIFI=1' /proc/cmdline", pfd)
        self.assertIn("type virt_wifi", pfd)
        # eth0 must be renamed before system_server so EthernetTracker never claims it,
        # with a rollback path that restores eth0 (ADB must never be lost)
        self.assertLess(pfd.index("name rmnet0"), pfd.index("type virt_wifi"))
        self.assertIn("ip link set rmnet0 name eth0", pfd)
        svc = scripts["service.sh"]
        self.assertIn("cmd wifi connect-network VirtWifi open", svc)
        # Watchdog loop must not block ksud services
        self.assertIn(") </dev/null >/dev/null 2>&1 &", svc)

    def test_audit_flags_storage_cpuinfo_optical_and_ethernet(self) -> None:
        base = {
            "model": "Pixel 7 Pro", "brand": "google", "device": "cheetah",
            "fingerprint": "google/cheetah/cheetah:13/TQ3A.230901.001.C2/10753682:user/release-keys",
            "tags": "release-keys", "hardware": "cheetah", "board": "cheetah",
        }
        # Single private ARM cpuinfo bind-mount with telephony feature and no x86 leak is clean.
        clean = fingerprints.score_identity_audit(
            {**base, "netTransport": "WIFI", "emulatedStorage": "mounted", "publicVolumes": "0",
             "opticalDrives": "0", "cpuinfoMounts": "1", "cpuinfoX86Leak": "0", "telephonyFeature": "1"},
            backend="iso",
        )
        self.assertEqual(clean["warnings"], [])
        self.assertEqual(clean["score"], 100)
        # Missing android.hardware.telephony (tablet profile) must be flagged.
        self.assertIn('android.hardware.telephony.gsm', fingerprints.ISO_TELEPHONY_FEATURES_XML)
        no_tel = fingerprints.score_identity_audit(
            {**base, "netTransport": "WIFI", "emulatedStorage": "mounted", "publicVolumes": "0",
             "opticalDrives": "0", "cpuinfoMounts": "1", "cpuinfoX86Leak": "0", "telephonyFeature": "0"},
            backend="iso",
        )
        self.assertEqual(len(no_tel["warnings"]), 1)
        self.assertEqual(no_tel["score"], 94)
        # Shared mount propagation explosion (>2 stacked /proc/cpuinfo mounts) must be flagged.
        exploded = fingerprints.score_identity_audit(
            {**base, "netTransport": "WIFI", "emulatedStorage": "mounted", "publicVolumes": "0",
             "opticalDrives": "0", "cpuinfoMounts": "64", "cpuinfoX86Leak": "0"},
            backend="iso",
        )
        self.assertEqual(len(exploded["warnings"]), 1)
        self.assertEqual(exploded["score"], 92)
        broken = fingerprints.score_identity_audit(
            {**base, "netTransport": "ETHERNET", "emulatedStorage": "missing", "publicVolumes": "1",
             "opticalDrives": "2", "cpuinfoMounts": "0", "cpuinfoX86Leak": "2"},
            backend="iso",
        )
        self.assertEqual(len(broken["warnings"]), 4)
        self.assertEqual(broken["score"], 100 - 5 - 10 - 6 - 8)

    def test_iso_modem_conf_and_imei_audit(self) -> None:
        prof = fingerprints.get_profile("xiaomi-13-pro-a15")
        self.assertIsNotNone(prof)
        ident = fingerprints.build_identity_record(prof, carrier_id="telkomsel")
        self.assertNotEqual(ident["bootloader"].lower(), "unknown")
        conf = dict(
            line.split("=", 1)
            for line in fingerprints.build_iso_modem_conf(ident).splitlines()
            if "=" in line
        )
        self.assertTrue(_luhn_valid(conf["imei"]))
        self.assertEqual(conf["imeisv"], "01")
        self.assertEqual(len(conf["imsi"]), 15)
        self.assertTrue(conf["imsi"].startswith("51010"))
        self.assertEqual(len(conf["iccid"]), 20)
        self.assertTrue(conf["iccid"].startswith("8962"))
        self.assertTrue(conf["msisdn"].startswith("62812"))
        self.assertEqual(conf["operator_long"], "Telkomsel")
        self.assertEqual(conf["operator_numeric"], "51010")
        self.assertEqual(conf["baseband"], ident["baseband"])

        base = {
            "model": "2210132C", "brand": "Xiaomi", "device": "nuwa",
            "fingerprint": ident["fingerprint"],
            "tags": "release-keys", "hardware": "qcom", "board": "kalama",
        }
        missing_imei = fingerprints.score_identity_audit({**base, "imei": ""}, backend="iso")
        self.assertEqual(len(missing_imei["warnings"]), 1)
        self.assertEqual(missing_imei["score"], 95)
        valid_imei = fingerprints.score_identity_audit({**base, "imei": conf["imei"]}, backend="iso")
        self.assertEqual(valid_imei["warnings"], [])
        self.assertEqual(valid_imei["score"], 100)


if __name__ == "__main__":
    unittest.main()
