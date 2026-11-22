# Droid Cloud

Droid Cloud is a self-hosted Android virtual machine controller with a
browser dashboard, touch-capable device stream, GPS emulation, APK upload, and
ADB access over SSH. It uses the official Android Emulator command-line tools
and Play Store system images.

The project is designed for a dedicated x86_64 Linux host with KVM. It does
not turn Android Emulator into a hardware-attested physical device. Apps that
require Play Integrity device or strong integrity verdicts should be used on a
physical phone.

## What It Provides

- Create, start, stop, restart, clone, reset, and delete Android VMs.
- **Two VM backends:**
  - **Official Android Emulator (AVD):** Google Play system images from Android 11 through Android 16 (with optional Magisk-patched Play Store ramdisk for root).
  - **Community Android ISO (QEMU/KVM):** Boot BlissOS x86_64 ISOs directly with persistent ext4 userdata, initramfs boot hooks, KernelSU/ReZygisk bootstrap, virtual Wi-Fi (`VIRT_WIFI=1`), virtual GSM/LTE RIL (`libdroidcloud_ril.so`), virtual Bluetooth 5.2 VHCI (`droidcloud_bt`), and BlissOS `PixelPropsUtils` neutralization (`unppu.c`).
- Configure vCPU, RAM, storage, screen profile, device identity profile, carrier, per-VM OpenVPN routing (`droid-cloud-netd`), and autostart.
- Control Android from a responsive browser interface with multi-device Farm actions, macros, interactive Leaflet GPS map, and APK upload/install.
- Send Android back, home, recents, power, and volume actions.
- Expose ADB and emulator ports only through an SSH tunnel.
- Monitor host CPU and RAM from the dashboard.
- Run as managed systemd services: controller, web bridge, per-device network daemon, and Caddy.

## Supported Host

- Ubuntu 24.04 LTS or newer is recommended.
- x86_64 CPU with virtualization enabled.
- `/dev/kvm` available to the installer.
- At least 4 vCPU, 8 GB RAM, and 80 GB free disk for the first usable VM.
- A DNS hostname pointing to the server.
- Inbound TCP 80 and 443 reachable by users.
- Outbound HTTPS for Android SDK packages and Caddy certificates.

Disk use grows quickly. The Android SDK and a single Play Store system image
can use several gigabytes, while each VM receives its own data image. Plan at
least 200 GB when several VMs will run.

## Quick Install

On a fresh Ubuntu host:

```bash
sudo apt-get update
sudo apt-get install -y git
git clone https://github.com/Evosif/NimbusDroid.git
cd NimbusDroid
```

Point a DNS record such as `android.example.com` to the server, then run:

```bash
sudo -E DROID_CLOUD_PUBLIC_HOST=android.example.com \
  ./scripts/install.sh
```

The installer:

1. Installs system packages.
2. Creates the unprivileged `droid-cloud` service user.
3. Installs Android command-line tools, platform-tools, emulator, and an
   Android 15 Google Play system image.
4. Copies the application to `/opt/droid-cloud`.
5. Writes runtime configuration to `/etc/droid-cloud/droid-cloud.env`.
6. Configures and starts Caddy, the controller, and the web bridge.

The dashboard is available at the configured HTTPS hostname. Print its
generated administrator password with:

```bash
sudo /opt/droid-cloud/scripts/show-password.sh
```

Verify the deployment with:

```bash
sudo /opt/droid-cloud/scripts/verify.sh
```

The full procedure, prerequisites, upgrade path, and rollback are in
[docs/DEPLOYMENT.md](docs/DEPLOYMENT.md).

The Indonesian quick-start guide is
[docs/PANDUAN-INSTALASI.md](docs/PANDUAN-INSTALASI.md).

## First VM

1. Sign in to the dashboard.
2. Select **Buat VM**.
3. Choose a device profile, Android version, vCPU, RAM, storage, and root
   preference.
4. Leave **Google Play** enabled. Root VMs use the official Play Store image
   with a Magisk-patched ramdisk.
5. Wait for the VM phase to become **Running**.
6. Use the on-screen controls or upload an APK.

Google account credentials belong in the Android VM. Do not place them in
repository files, environment variables, shell history, or issue reports.

## ADB Access

ADB is not exposed publicly. The dashboard shows a per-VM SSH tunnel command:

```bash
ssh -i YOUR_KEY.pem -N \
  -L 127.0.0.1:5555:127.0.0.1:5555 \
  ubuntu@android.example.com
```

In a second terminal:

```bash
adb connect 127.0.0.1:5555
adb devices
```

The SSH user must already have key-based access to the host. The tunnel target
is loopback on the server.

## Architecture

```text
Browser
  |
  | HTTPS / WSS
  v
Caddy
  |-- /api/* ------> controller (127.0.0.1:8081)
  |-- everything ---> websockify (127.0.0.1:8080)
                         |
                         +--> noVNC token --> loopback VNC stream

controller
  |-- Android Emulator processes
  |-- Xvfb + scrcpy + x11vnc per running VM
  |-- ADB and emulator console
  +-- state and VM metadata
```

Each running VM gets a private X display, a loopback VNC port, an emulator
console port, and an ADB port. The controller starts these processes on
demand. Only Caddy is publicly exposed.

Read [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) before changing lifecycle
behavior or port allocation.

## Configuration

The installer writes `/etc/droid-cloud/droid-cloud.env`. The documented
template is [config/droid-cloud.env.example](config/droid-cloud.env.example).
Important variables include:

| Variable | Purpose |
| --- | --- |
| `DROID_CLOUD_PUBLIC_HOST` | Hostname served by Caddy |
| `DROID_CLOUD_SSH_USER` | SSH user shown in the ADB tunnel command |
| `DROID_CLOUD_SDK_ROOT` | Android SDK installation |
| `DROID_CLOUD_AVD_HOME` | Android AVD storage |
| `DROID_CLOUD_STATE_DIR` | Passwords, tokens, and VM metadata |
| `DROID_CLOUD_CACHE_DIR` | Logs and temporary uploads |
| `DROID_CLOUD_WEB_BIND` | Loopback websockify listener |
| `DROID_CLOUD_API_PORT` | Loopback controller listener |
| `DROID_CLOUD_ALLOWED_ORIGINS` | Allowed browser WebSocket origins |

Restart affected services after changing the environment:

```bash
sudo systemctl restart droid-cloud-controller droid-cloud-web caddy
```

## Updates

```bash
cd /path/to/droid-cloud
git pull --ff-only
sudo ./scripts/install.sh
sudo ./scripts/verify.sh
```

Set `DROID_CLOUD_INSTALL_ANDROID_SDK=0` to skip SDK package checks during a
source-only update. The existing config file is read automatically.

## Development

The controller uses only the Python standard library. There is no frontend
build step.

```bash
./scripts/check.sh
```

For a disposable local test, install a separate copy under a non-production
directory and point all runtime paths to temporary storage. Do not run a
second controller against the same state directory or allocate the same VM
ports.

See [AGENTS.md](AGENTS.md) (coding-agent handoff, Community ISO invariants, and
native bionic build commands) and [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md) for
invariants, API endpoints, and a safe change checklist.

## Operational Safety

- Never commit private keys, dashboard passwords,
  `auth.json`, `tokens`, machine metadata, VM disks, APKs, logs, or browser
  session data.
- Keep ADB bound to loopback and reach it over SSH.
- Use a dedicated host or VM for this service.
- Root VMs use Play Store images with Magisk. Keep their test state separate
  from production accounts.
- Back up the state directory and VM data together before maintenance.
- Treat VM snapshots and disks as sensitive user data.

The security model and threat boundaries are documented in
[docs/SECURITY.md](docs/SECURITY.md).

The vendored noVNC client retains its upstream notices in
`LICENSES/novnc-copyright.txt`.

## Troubleshooting

Start with:

```bash
sudo /opt/droid-cloud/scripts/verify.sh
sudo journalctl -u droid-cloud-controller -u droid-cloud-web -u caddy -n 200
```

Common failures and diagnostics are covered in
[docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md).

## Status

This repository packages the working single-host controller. It is not a
multi-tenant product. The web bridge, controller, and Caddy route are ready
for deployment, but a new operator should verify DNS, KVM, certificates, and
SSH access on the target server before importing sensitive data.

## License

NimbusDroid is released under the [MIT License](LICENSE).

Third-party components retain their upstream licenses. In particular, the
vendored noVNC files and their notices are documented in
[LICENSES/novnc-copyright.txt](LICENSES/novnc-copyright.txt).
