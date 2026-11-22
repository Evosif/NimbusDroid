#!/usr/bin/env python3
"""Droid Cloud network helper (runs as root).

The controller runs unprivileged with NoNewPrivileges, so everything that needs
root for per-device VPN routing lives here behind a small unix-socket API:

  * one OpenVPN client per profile "slot", as transient unit dc-vpn<N>.service,
    started with --route-nopull so the host's own routing is never changed;
  * policy routing: fwmark 0xdc00+N -> table 7100+N -> default dev dcvpn<N>,
    with a blackhole fallback so traffic is blocked (not leaked) when the
    tunnel is down (kill switch). IPv6 for VPN devices is always blackholed;
  * nftables marks packets from sockets owned by the cgroup
    <controller cgroup>/vpn-<N>; device emulator processes are moved there.

Requests are one JSON object per line; replies are one JSON object per line.
"""

from __future__ import annotations

import grp
import json
import os
import re
import socket
import subprocess
import threading
import time
from pathlib import Path

SOCKET_PATH = "/run/droid-cloud-netd.sock"
STATE_FILE = Path("/var/lib/droid-cloud-netd/state.json")
# Directory holding the controller's VPN profiles (set by the systemd unit).
CONFIG_ROOT = Path(os.environ.get("DROID_CLOUD_VPN_DIR", "/nonexistent"))
CONTROLLER_CGROUP = "system.slice/android-controller.service"
CGROUP_FS = Path("/sys/fs/cgroup")
SOCKET_GROUP = os.environ.get("DROID_CLOUD_NETD_GROUP", "ubuntu")
MAX_SLOT = 32
NFT_TABLE = "dcvpn"

_lock = threading.RLock()
_desired: dict[int, dict] = {}  # slot -> {"config": str, "auth": str|None, "enabled": bool}
_built_cgroups: dict[int, int] = {}  # slot -> cgroup inode used in the current nft ruleset


def run(cmd: list[str], check: bool = False, timeout: int = 15) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=check)


def iface(slot: int) -> str:
    return f"dcvpn{slot}"


def unit(slot: int) -> str:
    return f"dc-vpn{slot}.service"


def mark(slot: int) -> int:
    return 0xDC00 + slot


def table(slot: int) -> int:
    return 7100 + slot


def cgroup_rel(slot: int) -> str:
    return f"{CONTROLLER_CGROUP}/vpn-{slot}"


def cgroup_dir(slot: int) -> Path:
    return CGROUP_FS / cgroup_rel(slot)


def valid_slot(value) -> int:
    slot = int(value)
    if not 1 <= slot <= MAX_SLOT:
        raise ValueError("invalid slot")
    return slot


def safe_path(value) -> str | None:
    if not value:
        return None
    path = Path(str(value)).resolve()
    if path.parent != CONFIG_ROOT.resolve() or not path.is_file():
        raise ValueError("config path must be a file inside the VPN profile directory")
    return str(path)


# ---------------------------------------------------------------- persistence
def save_state() -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    data = {str(k): v for k, v in _desired.items()}
    tmp = STATE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data))
    os.chmod(tmp, 0o600)
    tmp.replace(STATE_FILE)


def load_state() -> None:
    try:
        data = json.loads(STATE_FILE.read_text())
    except (OSError, ValueError):
        return
    for key, value in data.items():
        try:
            slot = valid_slot(key)
            _desired[slot] = {
                "config": safe_path(value.get("config")),
                "auth": safe_path(value.get("auth")) if value.get("auth") else None,
                "enabled": bool(value.get("enabled")),
            }
        except (ValueError, AttributeError):
            continue


# ---------------------------------------------------------------- openvpn units
def unit_active(slot: int) -> bool:
    return run(["systemctl", "is-active", "--quiet", unit(slot)]).returncode == 0


def start_unit(slot: int, spec: dict) -> None:
    cmd = [
        "systemd-run", "--unit", unit(slot), "--collect",
        "-p", "Restart=on-failure", "-p", "RestartSec=5",
        "/usr/sbin/openvpn",
        "--config", spec["config"],
        # Everything below overrides the profile: no host routes, no scripts,
        # userspace data channel (DCO would carry the fwmark onto the outer
        # UDP packets and loop them back into the tunnel).
        "--dev", iface(slot), "--dev-type", "tun",
        "--route-nopull", "--pull-filter", "ignore", "dhcp-option",
        "--disable-dco", "--script-security", "1",
        "--connect-retry", "2", "10", "--verb", "3",
    ]
    if spec.get("auth"):
        cmd += ["--auth-user-pass", spec["auth"], "--auth-nocache"]
    run(["systemctl", "reset-failed", unit(slot)])
    run(cmd, check=True)


def stop_unit(slot: int) -> None:
    run(["systemctl", "stop", unit(slot)], timeout=20)
    run(["systemctl", "reset-failed", unit(slot)])


# ---------------------------------------------------------------- routing
def iface_info(slot: int) -> dict:
    out = run(["ip", "-j", "addr", "show", "dev", iface(slot)])
    if out.returncode != 0:
        return {}
    try:
        data = json.loads(out.stdout)[0]
    except (ValueError, IndexError):
        return {}
    ipv4 = next((a["local"] for a in data.get("addr_info", []) if a.get("family") == "inet"), None)
    info = {"up": "UP" in data.get("flags", []), "ip": ipv4}
    stats = run(["ip", "-j", "-s", "link", "show", "dev", iface(slot)])
    try:
        s = json.loads(stats.stdout)[0]["stats64"]
        info["rxBytes"] = s["rx"]["bytes"]
        info["txBytes"] = s["tx"]["bytes"]
    except (ValueError, KeyError, IndexError):
        pass
    return info


def ensure_routing(slot: int) -> None:
    t = str(table(slot))
    m = hex(mark(slot))
    prio = str(table(slot))
    # Kill switch: always present, lower priority than the tunnel route.
    run(["ip", "route", "replace", "blackhole", "default", "table", t, "metric", "4000"])
    run(["ip", "-6", "route", "replace", "blackhole", "default", "table", t, "metric", "4000"])
    info = iface_info(slot)
    if info.get("ip"):
        run(["ip", "route", "replace", "default", "dev", iface(slot), "table", t, "metric", "10"])
        run(["sysctl", "-qw", f"net.ipv4.conf.{iface(slot)}.rp_filter=2"])
    for fam in ("-4", "-6"):
        rules = run(["ip", fam, "rule", "show", "priority", prio]).stdout
        if f"lookup {t}" not in rules:
            run(["ip", fam, "rule", "add", "fwmark", m, "lookup", t, "priority", prio])


def drop_routing(slot: int) -> None:
    for fam in ("-4", "-6"):
        run(["ip", fam, "rule", "del", "priority", str(table(slot))])
        run(["ip", fam, "route", "flush", "table", str(table(slot))])


def cgroup_inode(slot: int) -> int | None:
    try:
        return cgroup_dir(slot).stat().st_ino
    except OSError:
        return None


def rebuild_nft() -> None:
    """Mark packets of VPN cgroups. nft resolves cgroup paths when the rule is
    loaded, so the ruleset is rebuilt whenever a cgroup is (re)created."""
    present = {}
    for slot in sorted(_desired):
        ino = cgroup_inode(slot)
        if ino is not None:
            present[slot] = ino
    level = len(CONTROLLER_CGROUP.split("/")) + 1
    lines = [f"table inet {NFT_TABLE}", f"delete table inet {NFT_TABLE}", f"table inet {NFT_TABLE} {{"]
    lines.append("  chain out { type route hook output priority mangle; policy accept;")
    for slot in present:
        lines.append(f'    socket cgroupv2 level {level} "{cgroup_rel(slot)}" meta mark set {hex(mark(slot))}')
    lines.append("  }")
    lines.append("  chain post { type nat hook postrouting priority srcnat; policy accept;")
    lines.append('    oifname "dcvpn*" masquerade')
    lines.append("  }")
    lines.append("}")
    res = subprocess.run(["nft", "-f", "-"], input="\n".join(lines) + "\n", capture_output=True, text=True, timeout=10)
    if res.returncode != 0:
        raise RuntimeError(f"nft: {res.stderr.strip()[:300]}")
    _built_cgroups.clear()
    _built_cgroups.update(present)


def ensure_cgroup(slot: int) -> Path:
    path = cgroup_dir(slot)
    path.mkdir(exist_ok=True)
    if _built_cgroups.get(slot) != cgroup_inode(slot):
        rebuild_nft()
    return path


def reconcile() -> None:
    with _lock:
        for slot, spec in list(_desired.items()):
            try:
                ensure_routing(slot)
                active = unit_active(slot)
                if spec.get("enabled") and not active:
                    start_unit(slot, spec)
                elif not spec.get("enabled") and active:
                    stop_unit(slot)
            except Exception as exc:  # keep the loop alive
                print(f"reconcile slot {slot}: {exc}", flush=True)
        current = {s: cgroup_inode(s) for s in _desired}
        current = {s: i for s, i in current.items() if i is not None}
        if current != _built_cgroups:
            try:
                rebuild_nft()
            except Exception as exc:
                print(f"nft: {exc}", flush=True)


def reconcile_loop() -> None:
    while True:
        try:
            reconcile()
        except Exception as exc:
            print(f"reconcile: {exc}", flush=True)
        time.sleep(5)


# ---------------------------------------------------------------- processes
def controller_owned(pid: int) -> bool:
    try:
        text = Path(f"/proc/{pid}/cgroup").read_text()
    except OSError:
        return False
    return f"0::/{CONTROLLER_CGROUP}" in text


def process_tree(pid: int) -> list[int]:
    out = [pid]
    queue = [pid]
    while queue:
        current = queue.pop()
        try:
            children = Path(f"/proc/{current}/task/{current}/children").read_text().split()
        except OSError:
            continue
        for child in children:
            out.append(int(child))
            queue.append(int(child))
    return out


def assign(pid: int, slot: int | None) -> int:
    if pid <= 1 or not controller_owned(pid):
        raise ValueError("process is not owned by the controller")
    target = ensure_cgroup(slot) if slot else CGROUP_FS / CONTROLLER_CGROUP
    moved = 0
    for p in process_tree(pid):
        try:
            (target / "cgroup.procs").write_text(str(p))
            moved += 1
        except OSError:
            pass
    return moved


def last_log(slot: int, lines: int = 6) -> list[str]:
    out = run(["journalctl", "-u", unit(slot), "-n", str(lines), "-o", "cat", "--no-pager"])
    return [l for l in out.stdout.splitlines() if l.strip()][-lines:]


def status() -> dict:
    with _lock:
        slots = {}
        for slot, spec in _desired.items():
            info = iface_info(slot)
            active = unit_active(slot)
            state = "connected" if active and info.get("ip") else "connecting" if active else "disconnected"
            procs = 0
            try:
                procs = len((cgroup_dir(slot) / "cgroup.procs").read_text().split())
            except OSError:
                pass
            slots[str(slot)] = {
                "enabled": spec.get("enabled", False),
                "state": state,
                "tunnelIp": info.get("ip"),
                "rxBytes": info.get("rxBytes", 0),
                "txBytes": info.get("txBytes", 0),
                "processes": procs,
                "log": last_log(slot) if state != "connected" else [],
            }
        return {"slots": slots}


def egress(slot: int) -> str | None:
    if not iface_info(slot).get("ip"):
        return None
    out = run(["curl", "-s", "--max-time", "8", "--interface", iface(slot), "https://api.ipify.org"], timeout=12)
    ip = out.stdout.strip()
    return ip if re.fullmatch(r"[0-9a-fA-F:.]{3,45}", ip) else None


# ---------------------------------------------------------------- api
def handle(req: dict) -> dict:
    op = req.get("op")
    if op == "ping":
        return {"ok": True}
    if op == "configure":
        slot = valid_slot(req["slot"])
        with _lock:
            prev = _desired.get(slot)
            spec = {
                "config": safe_path(req["config"]),
                "auth": safe_path(req.get("auth")) if req.get("auth") else None,
                "enabled": bool(req.get("enabled")),
            }
            _desired[slot] = spec
            save_state()
            # Restart the tunnel when its config changed while it was running
            # (paths stay the same when a profile's file is replaced, so the
            # controller says so explicitly with "restart").
            if prev and (prev != spec or req.get("restart")) and unit_active(slot):
                stop_unit(slot)
        reconcile()
        return {"ok": True}
    if op == "remove":
        slot = valid_slot(req["slot"])
        with _lock:
            _desired.pop(slot, None)
            save_state()
            stop_unit(slot)
            drop_routing(slot)
            rebuild_nft()
        return {"ok": True}
    if op == "assign":
        slot = valid_slot(req["slot"]) if req.get("slot") else None
        with _lock:
            if slot and slot not in _desired:
                raise ValueError("unknown VPN slot")
            if slot:
                ensure_routing(slot)
            return {"ok": True, "moved": assign(int(req["pid"]), slot)}
    if op == "status":
        return {"ok": True, **status()}
    if op == "egress":
        return {"ok": True, "ip": egress(valid_slot(req["slot"]))}
    raise ValueError("unknown op")


def serve_client(conn: socket.socket) -> None:
    with conn:
        conn.settimeout(30)
        buf = b""
        while b"\n" not in buf and len(buf) < 65536:
            chunk = conn.recv(4096)
            if not chunk:
                break
            buf += chunk
        try:
            reply = handle(json.loads(buf.split(b"\n", 1)[0] or b"{}"))
        except Exception as exc:
            reply = {"ok": False, "error": str(exc)[:300]}
        try:
            conn.sendall((json.dumps(reply) + "\n").encode())
        except OSError:
            pass


def main() -> None:
    load_state()
    try:
        os.unlink(SOCKET_PATH)
    except FileNotFoundError:
        pass
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(SOCKET_PATH)
    os.chown(SOCKET_PATH, 0, grp.getgrnam(SOCKET_GROUP).gr_gid)
    os.chmod(SOCKET_PATH, 0o660)
    server.listen(16)
    threading.Thread(target=reconcile_loop, daemon=True).start()
    print("droid-cloud-netd ready", flush=True)
    while True:
        conn, _ = server.accept()
        threading.Thread(target=serve_client, args=(conn,), daemon=True).start()


if __name__ == "__main__":
    main()
