# Deployment Guide

This guide installs Droid Cloud on a new Ubuntu host. Run every command as a
user with sudo access. Do not copy state or private keys from another server
unless you intend to migrate its VMs.

## 1. Prepare DNS and Firewall

Create an `A` record for a hostname such as `android.example.com` that resolves
to the server's public IPv4 address. If the server uses a stable public host
record, a provider DNS name also works.

Allow inbound TCP 80 and 443:

```bash
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
```

Do not allow emulator console, ADB, VNC, websockify, or controller ports from
the internet.

## 2. Verify KVM

The Emulator must be able to use hardware acceleration:

```bash
ls -l /dev/kvm
grep -E -m1 '(vmx|svm)' /proc/cpuinfo
```

On cloud instances, the provider must expose nested virtualization. If
`/dev/kvm` is missing, fix the instance type or host settings before
continuing. An emulator without KVM is not a supported deployment.

Check capacity:

```bash
nproc
free -h
df -h /opt /var
```

A practical starting point for one Android 15 Play Store VM is 4 vCPU, 8 GB
host RAM, and 80 GB free disk. More running VMs require proportional host
memory and storage.

## 3. Install from the Repository

```bash
sudo apt-get update
sudo apt-get install -y git
git clone https://github.com/Evosif/NimbusDroid.git
cd NimbusDroid
```

Review the installer before use:

```bash
less scripts/install.sh
```

Run it with the public hostname:

```bash
sudo -E DROID_CLOUD_PUBLIC_HOST=android.example.com \
  ./scripts/install.sh
```

The installer is idempotent for source files and service configuration. It
does not delete existing VM or SDK data when rerun.

## 4. Optional Installer Variables

The installer honors these environment variables:

| Variable | Default |
| --- | --- |
| `DROID_CLOUD_INSTALL_DIR` | `/opt/droid-cloud` |
| `DROID_CLOUD_CONFIG_FILE` | `/etc/droid-cloud/droid-cloud.env` |
| `DROID_CLOUD_SERVICE_USER` | `droid-cloud` |
| `DROID_CLOUD_HOME` | `/var/lib/droid-cloud` |
| `DROID_CLOUD_SDK_ROOT` | `/opt/android-sdk` |
| `DROID_CLOUD_INSTALL_ANDROID_SDK` | `1` |
| `DROID_CLOUD_INSTALL_SYSTEM_IMAGE` | `1` |
| `DROID_CLOUD_SYSTEM_IMAGE` | Android 15 Google Play x86_64 |
| `DROID_CLOUD_SSH_USER` | invoking sudo user |

For an application-only deployment with an SDK already installed elsewhere:

```bash
sudo -E \
  DROID_CLOUD_PUBLIC_HOST=android.example.com \
  DROID_CLOUD_INSTALL_ANDROID_SDK=0 \
  DROID_CLOUD_SDK_ROOT=/mnt/android-sdk \
  ./scripts/install.sh
```

The SDK root must be readable and executable by the service user. The
controller writes new system images into that SDK when a user selects an
uninstalled Android version.

## 5. SDK Licenses and Image Size

The installer accepts Android SDK licenses non-interactively. This is an
explicit deployment step. Review Google's Android SDK terms before installing
the packages.

The Android 15 Play Store image is installed by default. Other Android
versions are downloaded on first use. Ensure the service user can reach
`dl.google.com` and has sufficient disk.

## 6. Confirm Services

```bash
sudo /opt/droid-cloud/scripts/verify.sh
systemctl status droid-cloud-controller --no-pager
systemctl status droid-cloud-web --no-pager
systemctl status caddy --no-pager
```

Expected listeners:

```bash
sudo ss -lntp | grep -E ':(80|443|8080|8081)\b'
```

Ports 8080 and 8081 must listen on `127.0.0.1`. Ports 80 and 443 belong to
Caddy. VM ports are created on demand and should also bind locally.

## 7. Open the Dashboard

Open `https://android.example.com`. Retrieve the generated password:

```bash
sudo /opt/droid-cloud/scripts/show-password.sh
```

If the certificate is not ready, check DNS and Caddy:

```bash
getent ahostsv4 android.example.com
sudo journalctl -u caddy -n 100 --no-pager
```

Caddy cannot issue a public certificate for a hostname that does not resolve
to this server or is not reachable on port 80/443.

## 8. Create the First VM

Use the dashboard rather than creating AVDs manually. The controller tracks
the emulator console, ADB, VNC, display, and state ports. A manually created
AVD is detected on controller startup, but dashboard-managed metadata is the
supported path.

For ordinary Play Store use:

- Use a Google Play image.
- Leave root disabled.
- Allocate at least 4 GB RAM to Android 15.
- Keep the VM running while using the browser stream.

For root testing:

- Use an official Google APIs image.
- Expect Play Store behavior to differ.
- Keep test VMs separate from normal Play Store VMs.

## 9. SSH and ADB

The dashboard prints an SSH command using `DROID_CLOUD_SSH_USER`. Confirm that
account has key-based SSH login:

```bash
ssh -i YOUR_KEY.pem ubuntu@android.example.com
```

The server's SSH service is independent of the dashboard password. A private
key used to connect to the server must never be copied into this repository.

## 10. Upgrade an Existing Deployment

```bash
cd /path/to/droid-cloud
git pull --ff-only
sudo -E DROID_CLOUD_INSTALL_ANDROID_SDK=0 ./scripts/install.sh
sudo /opt/droid-cloud/scripts/verify.sh
```

The existing env file is loaded automatically. Review changes between tags or
commits before upgrading a production host.

## 11. Roll Back

Keep a source release directory and a data backup before upgrades. A source
rollback does not automatically roll back Android VM disks or metadata, so
verify compatibility before downgrading.

Back up:

- `/etc/droid-cloud/droid-cloud.env`
- `DROID_CLOUD_STATE_DIR`
- `DROID_CLOUD_AVD_HOME`
- `/etc/caddy/Caddyfile`

Restore those paths, check ownership, then run the installer from the previous
known-good source release. Finally, run `scripts/verify.sh`.

## 12. Uninstall

Stop and remove the services, but preserve VM and state data until after a
separate backup:

```bash
sudo systemctl disable --now \
  droid-cloud-controller \
  droid-cloud-web
sudo rm /etc/systemd/system/droid-cloud-controller.service
sudo rm /etc/systemd/system/droid-cloud-web.service
sudo systemctl daemon-reload
```

Do not remove `/var/lib/droid-cloud` or an external AVD path until its VMs and
metadata have been backed up or intentionally discarded.
