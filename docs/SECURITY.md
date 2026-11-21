# Security Guide

## Access Model

The dashboard password grants control of every VM managed by the installation.
There are no per-user roles in the current version. Deploy it on a dedicated
host or dedicated VM, not beside unrelated sensitive workloads.

Services use an unprivileged account. Only the installer requires root.
Emulator processes need membership in the `kvm` group.

## Network Exposure

Expected public listeners:

- TCP 80 for Caddy's HTTPS redirect and ACME challenge.
- TCP 443 for the dashboard.

Expected loopback listeners:

- controller API
- websockify
- each VM's emulator console port
- each VM's ADB port
- each VM's VNC port

Verify with:

```bash
sudo ss -lntp
```

Do not publish ADB, VNC, or emulator console ports with a cloud firewall,
reverse proxy, or public bind address.

## SSH

ADB access uses an SSH local forward. Protect the server SSH account with:

- key-based authentication
- a strong passphrase for the private key
- a firewall restricted to trusted addresses where practical
- current OpenSSH server packages

Private keys are user credentials. The repository intentionally ignores
`*.pem`, `*.key`, `.env`, and runtime secrets. Never send a private key in a
chat, commit, issue, release, or build log.

## Dashboard Authentication

- Password digests use PBKDF2-HMAC-SHA256.
- Session tokens are random and stored in process memory.
- Sessions expire after 12 hours.
- Login attempts are rate-limited per source address.
- Mutating requests reject cross-origin browser requests.
- The Caddy route adds HSTS, a restrictive content security policy, and
  framing protection.

Reset the password if it appears in a screenshot, shell transcript, or shared
terminal.

## File Permissions

The service state is private to the service account. Passwords, tokens,
metadata, uploads, and logs are created with restricted permissions. The
application source can be world-readable but contains no runtime secrets.

Check:

```bash
sudo find /var/lib/droid-cloud -maxdepth 3 -type f -printf '%m %u:%g %p\n'
sudo stat /etc/droid-cloud/droid-cloud.env
```

## Uploads and APKs

The controller limits upload size and checks basic APK structure before
passing a file to ADB. This is not malware scanning. Install only applications
from trustworthy sources. Android app content remains untrusted.

## Android Accounts

Sign in to Google and other applications directly inside the Android VM.
Never put account passwords in:

- repository files
- deployment env files
- browser console snippets
- API client configuration
- support messages

VM disks can contain authentication material. Protect and encrypt backups.

## Play Integrity and Device Identity

Android Emulator uses Google-signed official system images, but it does not
provide hardware-backed attestation equivalent to a physical phone. Google's
Play Integrity device and strong verdicts can remain unevaluated or unmet on
an emulator.

This project does not include:

- manufacturer fingerprint spoofing
- fake IMEI, serial, or hardware identity
- root hiding
- Play Integrity bypasses
- anti-fraud evasion

Use a physical Android device with `scrcpy` and a private network tunnel when
an application requires hardware attestation or treats emulators as
unsupported.

## Multi-Tenancy

The current dashboard is a single-admin control plane. It is not isolated for
mutually untrusted customers. For multiple tenants, deploy separate hosts or
separate service instances and add an authorization layer before exposing VM
operations.

## Incident Response

If the host or dashboard is suspected compromised:

1. Block public ingress.
2. Stop controller and web services.
3. Preserve logs and a disk snapshot for investigation.
4. Rotate dashboard, Google, SSH, and application credentials that were used
   inside the host or VMs.
5. Rebuild from a known-good image.
6. Restore only verified VM data.
