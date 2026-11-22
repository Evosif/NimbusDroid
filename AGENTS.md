# AGENTS.md — Handoff Guide for Coding Agents

This file is the primary operational and engineering handoff for AI coding agents working on **NimbusDroid / Droid Cloud**. Read this before editing controller code, initrd hooks, native guest binaries, or restarting services.

## 1. Repository vs. Live Runtime Layout

```text
<REPO_DIR>/
  controller/
    manager.py            VM lifecycle (AVD emulator + Community ISO QEMU/KVM)
    server.py             Authenticated HTTP API (stdlib only)
    settings.py           Single source of truth for all paths, ports, env vars
    fingerprints.py       Device identity profiles, ISO boot hook (initrd overlay),
                          KernelSU scripts, modem/BT config, audit scoring
    droidcloud_ril.c      Pure-bionic AOSP RIL v12 shared lib (IMEI/SIM/baseband/LTE)
    droidcloud_bt.c       Pure-bionic virtual Bluetooth 5.2 VHCI daemon (/dev/vhci -> hci0)
    unppu.c               Pure-bionic Zygisk module: disables BlissOS PixelPropsUtils
                          (zork_cheets Chromebook / Pixel spoofing) and bridges
                          ro.bliss.* props inside com.android.settings only
    vpn.py                OpenVPN profile management & cgroup assignment
    netd.py               Per-VM cgroup policy routing daemon (droid-cloud-netd)
    platform_settings.py  Persistent performance/stream settings
  web/
    index.html, style.css, app.js, farm.js, location-map.js, lib/leaflet/, core/ (noVNC)
  deploy/                 Caddy & systemd unit templates
  scripts/                install.sh, verify.sh, check.sh, password scripts
  tests/                  unittest suite (stdlib only, no third-party deps)
  docs/                   ARCHITECTURE.md, DEPLOYMENT.md, DEVELOPMENT.md,
                          OPERATIONS.md, PANDUAN-INSTALASI.md, SECURITY.md,
                          TROUBLESHOOTING.md
```

Runtime paths are configured via `controller/settings.py` (never hardcode usernames, home directories, public IPs, or hostnames inside `controller/` or `web/`):

- `$DROID_CLOUD_STATE_DIR` (default `~/.config/android-cloud`):
  - `machines/<id>.json` — per-VM metadata (`backend: "avd"` or `"iso"`)
  - `iso-vms/<id>/data.img` — persistent ext4 userdata disk for ISO VMs
  - `isos/*.iso` — Community Android ISO files (e.g., BlissOS 16.9.7 Gapps)
  - `image-artifacts/` — compiled native artifacts & root/module archives:
    - `libdroidcloud_ril.so` (built from `controller/droidcloud_ril.c`)
    - `droidcloud_bt` (built from `controller/droidcloud_bt.c`)
    - `unppu-x86_64.so` (built from `controller/unppu.c`)
    - `busybox-x86_64`, `resetprop-x86_64`
    - `ReZygisk-v1.0.0-release.zip`, `PlayIntegrityFork-v17.zip`, `Tricky-Store-v1.4.1.zip`
    - `playstore-api35-ramdisk-rootavd-magisk26.4.img`, `playstore-api36-ramdisk-rootavd-magisk26.4.img`
- `$DROID_CLOUD_CACHE_DIR` (default `~/.cache/android-cloud`):
  - `logs/<id>-emulator.log` (includes QEMU `-serial stdio` console for ISO VMs)
  - `iso-boot/<iso-slug>/kernel`, `initrd.img`, `initrd-droidcloud-<hash>.img`

If the deployment runs live controller code from a separate directory (e.g. an installed API directory), **always diff before syncing** `settings.py`, `fingerprints.py`, `manager.py`, `server.py`, and frontend assets so no concurrent runtime changes are overwritten.

---

## 2. Critical Operational Rules (Read Carefully!)

1. **DO NOT restart the controller systemd service without explicit user approval.**
   The controller service uses `KillMode=mixed` (or spawns VMs in its cgroup), so restarting it terminates **every running AVD and ISO VM** on the host.
   - Only touch the specific VM(s) in scope; never stop/restart unrelated user AVDs.
   - To ship an updated initrd overlay to ISO VMs without restarting the controller, atomically replace the currently cached `initrd-droidcloud-<oldhash>.img` in `$DROID_CLOUD_CACHE_DIR/iso-boot/<slug>/` with `initrd.img + gzip(cpio(build_iso_boot_hook()))` preserving service-user ownership. On the next controller restart, `_iso_boot_initrd()` switches to the new canonical digest filename automatically.

2. **Use the service user's `HOME` when running `adb` or API scripts as root.**
   Always `export HOME=<service_user_home>` so `adb` shares the host's `.android/adbkey` and adb server rather than spawning a second daemon with an untrusted key.

3. **Keep secrets and host-specific identifiers out of Git.**
   Never commit passwords, `auth.json`, `tokens`, private keys, machine metadata, VM disks, APKs, logs, or hardcoded server IPs/domains. Always run `./scripts/check.sh` before committing.

4. **Frontend UI text stays in English.**
   Keep all dashboard UI labels, statuses, and errors in English.

---

## 3. Community ISO (BlissOS 16.9.7 x86_64) Architecture & Known Pitfalls

BlissOS ISO VMs boot directly on QEMU/KVM (`-nodefaults -enable-kvm -cpu host,kvm=off,-hypervisor -machine q35`) with `-smbios` tables populated from the VM's selected device profile, OEM MAC OUI (`generate_oem_mac`), `VIRT_WIFI=1`, and a concatenated initramfs overlay (`fingerprints.build_iso_boot_hook()`, v3).

### Pitfalls Already Solved — Do NOT Regress These

| Area | Pitfall / Failure Mode | Required Invariant |
| --- | --- | --- |
| **Initrd VINTF manifest** | Bind-mounting a directory from initramfs (`/dc_boot/...`) onto `/android/system/vendor/etc/vintf/manifest` gets emptied across `switch_root`, breaking `keymaster@4.1`, `gatekeeper@1.0`, `cas@1.2`, and causing a bootanimation hang. | BlissOS's `bliss_feature` already bind-mounts tmpfs `/tmp/fakeboot.xml` onto `system/vendor/etc/vintf/manifest/android.hardware.boot@1.2.xml` before `/scripts/*` runs. `scripts/99-droidcloud` simply overwrites `/tmp/fakeboot.xml` with `ISO_RADIO_VINTF_XML` (`android.hardware.radio@1.1::IRadio/slot1`). Zero extra directory mounts. |
| **KernelSU `/system` overlays** | Installing a KernelSU module with a `system/` overlay on BlissOS hides initrd `/system/vendor` bind-mounts and blanks SurfaceFlinger/EGL (black screen). | Never use a full `/system` KSU overlay on BlissOS. Bind-mount only narrow targets in `05_droidcloud_platform.sh`: `/dev/dc_perms -> /system/etc/permissions` (adds 9 GSM telephony features), single-file empty XML `/dev/dc_empty_perms.xml` onto `android.hardware.ethernet.xml` and `android.software.freeform_window_management.xml`, and `/dev/dc_ril.so -> /vendor/lib64/libreference-ril.so`. |
| **`/proc/cpuinfo` mount bomb** | BlissOS mounts `/proc` as `MS_SHARED`; per-app `/proc/cpuinfo` bind mounts multiply `2^n` (4,096+ mounts, loadavg >30). | `05_droidcloud_platform.sh` runs `busybox mount --make-rprivate /proc` first, then bind-mounts `/system/etc/cpuinfo.arm64.txt -> /proc/cpuinfo` once privately. Verify `cpuinfoMounts == 1` and `cpuinfoX86Leak == 0`. |
| **First-boot `VIRT_WIFI=1` timeout** | On an empty `data.img`, KernelSU scripts do not exist yet; BlissOS tears down `eth0`, leaving `wlan0` disconnected (no `10.0.2.15`, ADB offline forever) and without a wake lock (`s2idle` freeze). | `droidcloud/ksu.rc` in initrd v3 holds `/sys/power/wake_lock` (`wlan_ctrl`), brings `wlan0` up in `on boot`, and runs `service dc_virt_wifi` (`cmd wifi connect-network VirtWifi open`) on `sys.boot_completed=1`. |
| **Virtual RIL (`rild` permissions)** | `rild` runs as `uid=1001 (radio)` and cannot traverse `/data/adb` (`0700 root:root`). Also `GsmCdmaCallTracker` spams `GET_CURRENT_CALLS error 6` if opcode `9` is unsupported. | `05_droidcloud_platform.sh` copies `/data/adb/droidcloud_modem.conf -> /dev/dc_modem.conf (0644)` and `libdroidcloud_ril.so -> /dev/dc_ril.so (0755, vendor_file)` and bind-mounts onto `/vendor/lib64/libreference-ril.so`. `droidcloud_ril.c` handles `GET_CURRENT_CALLS (9)`, `DEVICE_IDENTITY (98)`, `SIM_IO (28)`, `GET_SIM_STATUS (1)`, `GET_IMSI (11)`, `BASEBAND_VERSION (51)`, etc., while refusing `SETUP_DATA_CALL (27)` so Wi-Fi/ADB stays primary. |
| **Virtual Bluetooth (`hci_vhci`)** | BlissOS `init.sh` runs `pm disable com.android.bluetooth` if no `hci*` exists at `bootcomplete`. Also stopping `vendor.bluetooth-1-1` after Bluetooth is already `ON` causes `EBUSY` crash loops (`retry count=7`). | `05_droidcloud_platform.sh` loads `ecc.ko`, `ecdh_generic.ko`, `bluetooth.ko`, `hci_vhci.ko` and starts `/dev/dc_bt` in `post-fs-data` so `hci0` exists before `init.sh` runs. Stock `vendor.bluetooth-1-1` brings Bluetooth `state: ON` natively with the OEM BT MAC. `service.sh` must check `dumpsys bluetooth_manager` and do nothing if already `state: ON`. |
| **Device Wi-Fi factory MAC** | `virt_wifi` has no vendor Wi-Fi HAL, so `WifiManager.getFactoryMacAddresses()` returns empty (`Unavailable` in Settings). | `05_droidcloud_platform.sh` writes `<string name="wifi_sta_factory_mac_address"><wlan0_mac></string>` into `/data/misc/apexdata/com.android.wifi/WifiConfigStore.xml` pre-`system_server` **in-place** (`sed ... > /dev/dc_wcfg && cat /dev/dc_wcfg > "$WCFG"`) to preserve inode, UID, mode, and SELinux context. |
| **`raven` & `Chromebook` spoof leaks** | 1) PlayIntegrityFork's `autopif4.sh` overwrote `custom.pif.prop` with `google/raven_beta/raven:CANARY` (`spoofVendingFinger=1`), making Play Store/GMS see Pixel 6 Pro (`raven`). 2) BlissOS `framework.jar` has `PixelPropsUtils.spoofBuildGms()` hardcoded to `google/zork/zork_cheets` (Chromebook!) and Pixel 5/7 Pro/XL for Google apps. | Never run `autopif4.sh`; always generate `custom.pif.prop` from `fingerprints.build_custom_pif_prop(identity)` with spoof flags `0`. `unppu.c` (Zygisk) puts each targeted Google/Samsung/Chrome/Vending app into `PixelPropsUtils.packagesToKeep` and clears its spoof maps before `Application.onCreate()`. |
| **BlissOS Settings `Serial`/`CPU` `unknown`** | Deleting `ro.bliss.*` keeps `leakyPropCount=0`, but BlissOS `Settings.apk` reads `ro.bliss.serialnumber`, `ro.bliss.cpuname`, `ro.bliss.version` directly. | `unppu.c` hooks `android/os/SystemProperties.native_get` **only** inside `com.android.settings` to map those `ro.bliss.*` reads to `ro.serialno`, `ro.soc.manufacturer + ro.soc.model`, and `ro.build.version.incremental`. Global `getprop` stays 100% free of `ro.bliss.*`. |

---

## 4. Building Pure-Bionic Native Guest Artifacts

All three C components are compiled on the Linux x86_64 host with `-nostdlib` linked **strictly against BlissOS bionic `libc.so` and `liblog.so`** (never host glibc!).

Pull the two bionic stub libraries once from any running BlissOS VM:

```bash
mkdir -p /tmp/bionic_libs
adb -s 127.0.0.1:<port> pull /apex/com.android.runtime/lib64/bionic/libc.so /tmp/bionic_libs/
adb -s 127.0.0.1:<port> pull /system/lib64/liblog.so /tmp/bionic_libs/
```

Compile all three artifacts into `$DROID_CLOUD_STATE_DIR/image-artifacts/`:

```bash
OUT="${DROID_CLOUD_STATE_DIR:-$HOME/.config/android-cloud}/image-artifacts"

# 1. Virtual GSM/LTE RIL (AOSP RIL v12 shared library)
gcc -shared -fPIC -O2 -Wall -Wextra -nostdlib -Wl,-soname,libdroidcloud_ril.so \
  controller/droidcloud_ril.c -L/tmp/bionic_libs -lc -llog \
  -o "$OUT/libdroidcloud_ril.so"

# 2. Virtual Bluetooth 5.2 VHCI controller daemon
gcc -fPIE -pie -O2 -Wall -Wextra -nostdlib \
  -Wl,--dynamic-linker=/system/bin/linker64 -Wl,--allow-shlib-undefined \
  controller/droidcloud_bt.c -L/tmp/bionic_libs -lc -llog \
  -o "$OUT/droidcloud_bt"

# 3. Zygisk module (PixelPropsUtils disabler + Settings ro.bliss.* bridge)
gcc -shared -fPIC -O2 -Wall -Wextra -nostdlib -Wl,-soname,x86_64.so \
  controller/unppu.c -L/tmp/bionic_libs -lc -llog \
  -o "$OUT/unppu-x86_64.so"

chmod 755 "$OUT/libdroidcloud_ril.so" "$OUT/droidcloud_bt" "$OUT/unppu-x86_64.so"
```

Verify linkage after every rebuild (`NEEDED` must list **only** `libc.so` and `liblog.so`):

```bash
readelf -d "$OUT/libdroidcloud_ril.so" "$OUT/droidcloud_bt" "$OUT/unppu-x86_64.so" | grep NEEDED
```

Clean up `/tmp/bionic_libs` after building.

---

## 5. Honest Hardware Limitations (Important for User Expectations)

- **Play Integrity / Hardware Attestation:** x86_64 KVM cloud VMs do **not** have a physical hardware TEE, KeyMint, or StrongBox chip. Even with consistent identity properties, IMEI, SIM, Bluetooth, Wi-Fi MAC, and disabled BlissOS spoofing, Google Play Integrity (`MEETS_DEVICE_INTEGRITY` / `MEETS_STRONG_INTEGRITY`) will not pass without real attested hardware.
- **Strict Anti-Fraud Apps:** Banking apps, ride-hailing apps (Grab, Gojek), and similar anti-fraud SDKs also inspect Play Integrity verdicts, x86_64 ABI / SwiftShader GPU, root/KernelSU presence, and datacenter/VPN IP reputation. Always communicate this limitation honestly rather than promising an app will not flag a cloud VM.

---

## 6. Verification Checklist Before Committing

```bash
./scripts/check.sh
```

`check.sh` compiles all Python modules, runs the full `unittest` suite (`tests/`), runs `node --check` on frontend JS files (`web/app.js`, `web/farm.js`, `web/location-map.js`), runs `bash -n` on shell scripts, and rejects hardcoded installation paths/IPs or tracked secret files.
