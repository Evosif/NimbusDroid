# Operations Guide

## Service Map

| Unit | Role |
| --- | --- |
| `droid-cloud-controller.service` | VM API and lifecycle manager |
| `droid-cloud-web.service` | Static frontend and WebSocket/VNC bridge |
| `caddy.service` | HTTPS reverse proxy |

Emulator, Xvfb, scrcpy, and x11vnc are child processes managed by the
controller. They do not normally need separate systemd units.

## Health Checks

```bash
sudo /opt/droid-cloud/scripts/verify.sh
curl -sS http://127.0.0.1:8081/api/health | jq
systemctl is-active \
  droid-cloud-controller \
  droid-cloud-web \
  caddy
```

Expected controller response:

```json
{"ok": true, "controller": "ready"}
```

## Logs

```bash
sudo journalctl -u droid-cloud-controller -f
sudo journalctl -u droid-cloud-web -f
sudo journalctl -u caddy -f
```

Per-VM process logs are under `DROID_CLOUD_CACHE_DIR/logs`:

```bash
sudo ls -lah /var/lib/droid-cloud/cache/logs
sudo tail -n 100 /var/lib/droid-cloud/cache/logs/VM_ID-emulator.log
```

## Password Reset

The reset script invalidates all current sessions:

```bash
sudo /opt/droid-cloud/scripts/reset-password.sh
```

Retrieve the current password:

```bash
sudo /opt/droid-cloud/scripts/show-password.sh
```

Passwords are generated, not passed as command-line arguments. Avoid shell
history or issue attachments that contain them.

## VM Capacity

Host summaries come from `/proc/stat` and `/proc/meminfo`. vCPU allocation is
not the only limit. Running several VMs can exceed safe RAM, KVM, file
descriptor, or disk throughput limits.

Useful commands:

```bash
nproc
free -h
vmstat 1 10
sudo ss -lntp | grep -E ':(555[4-9]|55[6-9][0-9]|808[01]|590[1-9]|59[1-9][0-9])\b'
du -sh /var/lib/droid-cloud/.android/avd/*.avd
```

Stop VMs before changing RAM or vCPU. The dashboard enforces this.

## Android Image Management

List installed packages:

```bash
sudo -u droid-cloud env \
  HOME=/var/lib/droid-cloud \
  ANDROID_HOME=/opt/android-sdk \
  ANDROID_SDK_ROOT=/opt/android-sdk \
  /opt/android-sdk/cmdline-tools/latest/bin/sdkmanager --list_installed
```

The controller downloads a selected official Play Store image if absent.
Downloads can take several minutes. Keep the process and host online.

## ADB Diagnostics

From the server:

```bash
sudo -u droid-cloud /opt/android-sdk/platform-tools/adb devices -l
sudo -u droid-cloud /opt/android-sdk/platform-tools/adb \
  -s emulator-5554 shell getprop sys.boot_completed
```

From a remote workstation, open the tunnel shown in the dashboard and then:

```bash
adb connect 127.0.0.1:5555
adb -s 127.0.0.1:5555 shell
```

Do not run `adb kill-server` on a shared host without confirming that no other
operator depends on the active server.

## Backups

Stop VMs before a filesystem-consistent backup. Back up these paths together:

- state directory
- AVD directory
- installation configuration
- custom Caddy configuration

Example using a quiesced maintenance window:

```bash
sudo systemctl stop droid-cloud-controller
sudo tar --xattrs --acls -cpf /secure-backups/droid-cloud-data.tar \
  /var/lib/droid-cloud/state \
  /var/lib/droid-cloud/.android/avd \
  /etc/droid-cloud/droid-cloud.env
sudo systemctl start droid-cloud-controller
```

Restrict backup permissions. VM disks can contain signed-in accounts, messages,
photos, and application tokens.

## Restore

1. Stop the controller.
2. Restore configuration and service-user ownership.
3. Restore state and AVD paths.
4. Start the controller.
5. Verify machine metadata and process startup.

Never restore `tokens` from an old snapshot while old processes still run.
The controller regenerates the token file at startup.

## Log Rotation

Application process logs are append-only. Configure host log rotation for
large or long-running deployments. Journald settings govern system service
logs.

## Routine Maintenance

Weekly:

- Run `scripts/verify.sh`.
- Inspect host disk and available RAM.
- Check failed service units.
- Confirm backups contain both state and VM data.

Before upgrades:

- Read repository changes.
- Back up state and VM data.
- Avoid upgrades while VMs are starting.
