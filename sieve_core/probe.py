"""Edge-triggered Tailscale check. Runs only when SIEVE is asked.

No Hermes hook fires when a service dies. This probe runs from the desktop
status poll and from `/sieve probe`. It does not run when Desktop is closed
or the Mac is asleep. A missing `tailscale` binary is not an outage.
`tailscale status --json` is local IPC to the Tailscale daemon, not an HTTP
call from SIEVE.
"""

import json
import os
import shutil
import subprocess
from datetime import datetime, timedelta
from pathlib import Path

from . import queue

_BIN = "tailscale"


def parse_status(raw: str) -> dict:
    try:
        data = json.loads(raw or "")
    except Exception:
        return {"up": False, "detail": "tailscale status was not JSON"}
    backend = str(data.get("BackendState") or "")
    online = (data.get("Self") or {}).get("Online")
    up = backend == "Running" and online is True
    return {"up": up, "detail": f"BackendState={backend or 'missing'} Online={online}"}


def read_status(runner=None) -> dict:
    if runner is None and shutil.which(_BIN) is None:
        return {"installed": False, "up": None, "detail": "tailscale not installed"}
    try:
        if runner is not None:
            raw, code = runner()
        else:
            proc = subprocess.run(
                [_BIN, "status", "--json"],
                capture_output=True, text=True, timeout=3,
            )
            raw, code = proc.stdout or "", proc.returncode
    except subprocess.TimeoutExpired:
        return {"installed": True, "up": None, "detail": "tailscale status timed out"}
    except OSError as exc:
        return {"installed": True, "up": None, "detail": f"tailscale status failed: {exc}"}
    if code != 0 and not (raw or "").strip():
        return {"installed": True, "up": False, "detail": f"tailscale status exited {code}"}
    parsed = parse_status(raw)
    parsed["installed"] = True
    return parsed


def _gap_note(last_iso: str, now, gap_seconds: int) -> str:
    """A probe gap means the Mac slept or the watcher was not running.

    After a gap we cannot claim the link stayed up: it may have dropped and
    recovered while nothing was watching. Say so instead of implying uptime.
    """
    if not last_iso:
        return ""
    try:
        elapsed = (now - datetime.fromisoformat(last_iso)).total_seconds()
    except ValueError:
        return ""
    if elapsed <= gap_seconds:
        return ""
    mins = int(elapsed // 60)
    return (f"probe gap of {mins} min (Mac likely slept). "
            "Link state during that gap is unknown: it may have dropped and recovered unobserved.")


def observe(conn, route, *, force=False, now=None, runner=None, min_interval=60, gap_seconds=None) -> dict:
    now = now or datetime.now()
    if gap_seconds is None:
        gap_seconds = 3 * min_interval
    last = queue.kv_get(conn, "tailscale_probe_at") or ""
    if not force and last:
        try:
            if now - datetime.fromisoformat(last) < timedelta(seconds=min_interval):
                cached = queue.kv_get(conn, "tailscale_up") or ""
                return {"skipped": True, "fired": False, "installed": cached != "absent",
                        "up": True if cached == "yes" else False if cached == "no" else None,
                        "detail": "probe interval not elapsed"}
        except ValueError:
            pass
    gap = _gap_note(last, now, gap_seconds)
    if gap:
        queue.kv_set(conn, "probe_gap_note", gap)
    state = read_status(runner)
    queue.kv_set(conn, "tailscale_probe_at", now.isoformat(timespec="seconds"))
    queue.kv_set(conn, "tailscale_detail", state.get("detail") or "")
    if not state.get("installed"):
        queue.kv_set(conn, "tailscale_up", "absent")
        return {"skipped": False, "fired": False, "installed": False, "up": None,
                "detail": state.get("detail") or ""}
    if state.get("up") is None:
        return {"skipped": False, "fired": False, "installed": True, "up": None,
                "detail": state.get("detail") or ""}
    up = "yes" if state.get("up") else "no"
    prev = queue.kv_get(conn, "tailscale_up") or ""
    fired = False
    if prev == "yes" and up == "no":
        route("service_down", {
            "summary": "Tailscale is down",
            "context": "base",
            "detail": state.get("detail") or "tailscale down",
        })
        fired = True
    queue.kv_set(conn, "tailscale_up", up)
    detail = state.get("detail") or ""
    if gap and state.get("up"):
        detail = f"{detail} | {gap}"
    return {"skipped": False, "fired": fired, "installed": True, "up": bool(state.get("up")),
            "detail": detail}


def gateway_alive(home: Path, pid_alive=None) -> dict:
    """True only when gateway.pid names a process that is still running.

    This does not call the gateway, a model, or the network. A missing or dead
    pid is down. It does not prove the provider is up.
    """
    path = Path(home) / "gateway.pid"
    if not path.exists():
        return {"up": False, "detail": "gateway.pid missing"}
    try:
        rec = json.loads(path.read_text(encoding="utf-8"))
        pid = int(rec.get("pid"))
    except Exception:
        return {"up": False, "detail": "gateway.pid unreadable"}
    check = pid_alive or _pid_alive
    if not check(pid):
        return {"up": False, "detail": f"gateway pid {pid} is not running"}
    return {"up": True, "detail": f"gateway pid {pid} alive"}


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def observe_gateway(conn, route, *, home, force=False, now=None, pid_alive=None, min_interval=60) -> dict:
    now = now or datetime.now()
    last = queue.kv_get(conn, "gateway_probe_at") or ""
    if not force and last:
        try:
            if now - datetime.fromisoformat(last) < timedelta(seconds=min_interval):
                cached = queue.kv_get(conn, "gateway_up") or ""
                return {"skipped": True, "fired": False,
                        "up": True if cached == "yes" else False if cached == "no" else None,
                        "detail": "probe interval not elapsed"}
        except ValueError:
            pass
    state = gateway_alive(home, pid_alive=pid_alive)
    queue.kv_set(conn, "gateway_probe_at", now.isoformat(timespec="seconds"))
    queue.kv_set(conn, "gateway_detail", state.get("detail") or "")
    up = "yes" if state.get("up") else "no"
    prev = queue.kv_get(conn, "gateway_up") or ""
    fired = False
    if prev == "yes" and up == "no":
        route("service_down", {
            "summary": "Hermes gateway is down",
            "context": "base",
            "detail": state.get("detail") or "gateway down",
        })
        fired = True
    queue.kv_set(conn, "gateway_up", up)
    return {"skipped": False, "fired": fired, "up": bool(state.get("up")),
            "detail": state.get("detail") or ""}
