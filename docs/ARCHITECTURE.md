# Architecture

## Components

### Controller

`controller/server.py` is a standard-library threaded HTTP server bound to a
loopback address. It owns authentication, request routing, APK upload, and the
machine manager.

`controller/manager.py` is the source of truth for VM lifecycle. It creates
AVDs, writes metadata, allocates ports, launches Emulator, starts display and
mirroring processes, and reconciles state with ADB.

### Web Bridge

`websockify` serves static frontend files and upgrades browser connections to
VNC streams. It accepts WebSocket origins listed in
`DROID_CLOUD_ALLOWED_ORIGINS` and VNC targets in the controller-owned token
file.

### Frontend

`web/` is plain HTML, CSS, and JavaScript with a vendored noVNC module. There
is no package manager or frontend build step. The vendored third-party notices
are in `LICENSES/novnc-copyright.txt`.

### Caddy

Caddy terminates HTTPS and routes `/api/*` to the controller. All other paths
go to the web bridge. It sets security headers and does not expose VNC,
websockify, ADB, or the controller listener directly.

## Runtime Layout

The default installer layout is:

```text
/opt/droid-cloud                 application source
/opt/android-sdk                 Android SDK
/var/lib/droid-cloud
  .android/avd                   AVD disks and definitions
  state
    password                     generated dashboard password
    auth.json                    password digest
    tokens                       per-VM VNC tokens
    machines/*.json              VM metadata
  cache
    logs/                        controller process logs
    uploads/                     temporary APK uploads
/etc/droid-cloud
  droid-cloud.env                service configuration
```

`controller/settings.py` maps environment variables to these paths. Runtime
code must not hardcode a username, home directory, domain, or installation
path.

## Port Model

The controller allocates, for each VM:

| Resource | Formula or example |
| --- | --- |
| Emulator console | `5554 + index * 2` |
| ADB | console port + 1 |
| VNC | `5901 + index` |
| X display | `100 + index` |

Before allocation, it rejects an index if its console or VNC port is already
recorded or currently open. Ports remain loopback-only. Increase the internal
capacity by reviewing `MAX_MACHINES` and host limits together.

## VM Lifecycle

Creation:

1. Validate name, device, Android API, CPU, RAM, and disk.
2. Ensure the official `google_apis_playstore` image exists.
3. Run `avdmanager create avd`.
4. Write hardware settings into `config.ini`.
5. Write metadata JSON with private permissions.
6. Start the VM if requested.

Start:

1. Launch headless Emulator with KVM and software GPU.
2. Start a private Xvfb display and loopback x11vnc server.
3. Wait for ADB.
4. Start scrcpy on the private display.
5. Wait for Android boot completion.
6. Mark the VM running only after the boot property is complete.

Stop:

1. Ask Emulator to terminate over its console.
2. Stop scrcpy, x11vnc, Xvfb, and any remaining Emulator process group.
3. Remove stale VNC tokens.

The controller keeps lifecycle state in memory for process handles and in the
state directory for durable metadata.

## API Surface

All authenticated endpoints use the local dashboard session cookie.

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/api/health` | Unauthenticated readiness |
| `POST` | `/api/login` | Password login |
| `POST` | `/api/logout` | Clear session |
| `GET` | `/api/machines` | List machines and host stats |
| `POST` | `/api/machines` | Create a VM |
| `GET` | `/api/machines/:id` | Read one VM |
| `POST` | `/api/machines/:id/start` | Start |
| `POST` | `/api/machines/:id/stop` | Stop |
| `POST` | `/api/machines/:id/restart` | Restart |
| `POST` | `/api/machines/:id/clone` | Clone a stopped VM |
| `POST` | `/api/machines/:id/wipe` | Reset user data |
| `POST` | `/api/machines/:id/configure` | Change stopped hardware config |
| `POST` | `/api/machines/:id/location` | Set GPS coordinates |
| `POST` | `/api/machines/:id/install` | Upload and install an APK |
| `POST` | `/api/machines/:id/key` | Send Android navigation key |
| `GET` | `/api/machines/:id/screenshot` | Download a screenshot |
| `DELETE` | `/api/machines/:id` | Delete |

## Trust Boundaries

- Browser input crosses an authenticated origin-checked HTTP boundary.
- Uploads are temporary files with APK shape checks before installation.
- Android VM content and user accounts are untrusted.
- ADB grants broad control inside the emulator. Keep it behind SSH.
- Dashboard sessions are stored in memory and expire after 12 hours.
- Passwords are represented on disk by a PBKDF2 digest. The generated
  plaintext password exists only for initial retrieval and is stored with
  service-only permissions.

See [SECURITY.md](SECURITY.md) for deployment controls.
