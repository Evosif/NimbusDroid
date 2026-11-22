# Troubleshooting Guide

## Start with Evidence

```bash
sudo /opt/droid-cloud/scripts/verify.sh
sudo systemctl --failed
sudo journalctl -u droid-cloud-controller -u droid-cloud-web -u caddy -n 250 --no-pager
```

Do not restart every service repeatedly before reading the first error. Process
startup time for Android can be several minutes.

## Dashboard Never Loads

Check DNS:

```bash
getent ahostsv4 android.example.com
```

Check ports:

```bash
sudo ss -lntp | grep -E ':(80|443)\b'
sudo ufw status
```

Check Caddy certificates:

```bash
sudo journalctl -u caddy -n 150 --no-pager
```

If the hostname does not resolve to this host, Caddy cannot complete ACME.

## 502 from Caddy

```bash
curl -v http://127.0.0.1:8081/api/health
sudo ss -lntp | grep -E ':(8080|8081)\b'
```

If the controller is down, inspect:

```bash
sudo journalctl -u droid-cloud-controller -n 100 --no-pager
```

If the web bridge is down, inspect:

```bash
sudo journalctl -u droid-cloud-web -n 100 --no-pager
```

## `/dev/kvm` Missing

```bash
lsmod | grep '^kvm'
ls -l /dev/kvm
```

On a cloud VM, enable nested virtualization or select an instance type that
exposes KVM. The application cannot fix a missing host capability.

## Permission Denied for KVM

```bash
id droid-cloud
ls -l /dev/kvm
sudo usermod -aG kvm droid-cloud
sudo systemctl restart droid-cloud-controller
```

Log out and back in for interactive shells after changing group membership.

## Android SDK Command Missing

```bash
ls -l /opt/android-sdk/cmdline-tools/latest/bin/sdkmanager
ls -l /opt/android-sdk/platform-tools/adb
ls -l /opt/android-sdk/emulator/emulator
```

If an external SDK is used, update `DROID_CLOUD_SDK_ROOT`, fix ownership, and
restart the controller. A service-account permission failure often looks like
a missing executable.

## Image Download Fails

```bash
sudo -u droid-cloud env \
  HOME=/var/lib/droid-cloud \
  ANDROID_HOME=/opt/android-sdk \
  ANDROID_SDK_ROOT=/opt/android-sdk \
  /opt/android-sdk/cmdline-tools/latest/bin/sdkmanager --list
```

Check outbound HTTPS, clock accuracy, DNS, disk space, and SDK licenses. Keep
the VM creation request active while a large image downloads.

## VM Stuck at Starting

Watch the service and per-VM logs:

```bash
sudo journalctl -u droid-cloud-controller -f
sudo tail -f /var/lib/droid-cloud/cache/logs/VM_ID-emulator.log
```

Check ADB:

```bash
sudo -u droid-cloud /opt/android-sdk/platform-tools/adb devices -l
```

The controller marks a VM running only after
`sys.boot_completed=1`. First boot can be slow.

## VM Ports Conflict

```bash
sudo ss -lntp | grep -E ':(555[4-9]|55[6-9][0-9]|590[1-9]|59[1-9][0-9])\b'
```

Stop stale VMs through the dashboard. If a manually started process owns a
port, identify it before terminating it:

```bash
ps -eo pid,user,cmd | grep -E 'emulator|qemu-system'
```

Do not delete metadata first. That can orphan disks or make ports look free
while an old process is still using them.

## Blank or Disconnected Console

Check that the VM is running and:

```bash
sudo journalctl -u droid-cloud-web -n 100 --no-pager
sudo tail -n 100 /var/lib/droid-cloud/cache/logs/VM_ID-mirror.log
sudo tail -n 100 /var/lib/droid-cloud/cache/logs/VM_ID-vnc.log
```

Confirm the websockify origin list exactly matches the browser origin:

```text
https://android.example.com
```

A hostname change requires updating `DROID_CLOUD_ALLOWED_ORIGINS`, regenerating
the Caddyfile, and restarting both services.

## APK Upload Fails

Check:

- file is a valid APK
- size is below 250 MB
- VM is running
- free space exists under `DROID_CLOUD_CACHE_DIR`
- ADB recognizes the VM

The built-in uploader installs one APK. Split APK bundles may require ADB
`install-multiple` from a trusted workstation.

## Login Rate Limited

Failures are limited per source address. Wait five minutes or restart the
controller. If the source address is hidden behind a proxy, ensure Caddy is the
only ingress path and review request logs before changing limits.

## Root VM Has No Play Store

Root VMs use the official Android 16 Play Store image and a Magisk-patched
ramdisk. Confirm the registered image and ramdisk are present:

```bash
sudo systemctl show android-controller.service -p Environment
ls -lh "${DROID_CLOUD_STATE_DIR:-$HOME/.config/android-cloud}/image-artifacts/"
```

If Magisk reports that root access has not been approved, open Magisk, select
**Superuser**, and enable the `com.android.shell` switch once.

## Community ISO (BlissOS) Diagnostics

- **Boot hangs at bootanimation:** Inspect guest tombstones (`debugfs -R "ls -l /tombstones" data.img`) and `$DROID_CLOUD_CACHE_DIR/logs/<id>-emulator.log`. Never bind-mount an initramfs directory onto `/android/system/vendor/etc/vintf/manifest`; overwrite `/tmp/fakeboot.xml` in `scripts/99-droidcloud` instead (see [AGENTS.md](../AGENTS.md)).
- **Blank display after adding a KernelSU module:** Remove any KernelSU module containing a `system/` overlay directory; bind-mount only `/system/etc/permissions` from `05_droidcloud_platform.sh`.
- **High guest CPU / `/proc/cpuinfo` mount explosion:** Verify `grep -c ' /proc/cpuinfo ' /proc/mounts` is `1` and `busybox mount --make-rprivate /proc` ran in `05_droidcloud_platform.sh`.
- **IMEI / SIM / Bluetooth / Wi-Fi MAC missing:** Check `lshal | grep radio`, `service call iphonesubinfo 1 s16 com.android.shell`, `hciconfig -a`, `dumpsys bluetooth_manager`, and `grep wifi_sta_factory_mac_address /data/misc/apexdata/com.android.wifi/WifiConfigStore.xml`.
- **Apps show `raven` or `Chromebook` instead of selected profile:** Confirm `custom.pif.prop` matches the VM profile (`spoofVendingFinger=0`) and `logcat -d | grep DroidCloudUnPPU` confirms BlissOS `PixelPropsUtils` is disabled.

## App Rejects Emulator

Official Play images can still be recognized as emulator environments by
applications that inspect device characteristics or require hardware-backed
Play Integrity verdicts.

Check verdicts directly, then move the application to a physical phone if it
requires certified hardware. Identity spoofing and integrity bypasses are
outside this repository's scope.

## Certificate Renewal Failure

```bash
sudo journalctl -u caddy -n 200 --no-pager
getent ahostsv4 android.example.com
curl -I http://android.example.com/.well-known/acme-challenge/test
```

Restore ports 80/443 and DNS first. Avoid repeated configuration restarts while
the ACME service is rate-limiting requests.

## Collect a Support Bundle

```bash
mkdir -m 700 -p /tmp/droid-cloud-support
sudo /opt/droid-cloud/scripts/verify.sh \
  > /tmp/droid-cloud-support/verify.txt 2>&1 || true
sudo journalctl -u droid-cloud-controller -u droid-cloud-web -u caddy \
  -n 500 --no-pager > /tmp/droid-cloud-support/services.txt
sudo ss -lntp > /tmp/droid-cloud-support/listeners.txt
sudo tar -C /tmp -czf /tmp/droid-cloud-support.tar.gz droid-cloud-support
```

Inspect the archive before sharing. Remove VM names, account details, internal
hostnames, public IPs, and any encoded credentials.
