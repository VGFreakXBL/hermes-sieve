"""Setup Doctor: every missing prerequisite has an exact fix.

Checks are cheap and read-only. The walk doubles as demo footage.
"""

from . import escalate


def run_checks(dispatch, conn) -> list:
    """Returns [{name, status: 'ok'|'warn'|'fail', detail, fix}]."""
    checks = []

    # 1. Delivery reachable
    import shutil
    if shutil.which("hermes"):
        checks.append({"name": "hermes send (delivery)", "status": "ok",
                       "detail": "CLI available", "fix": ""})
    else:
        checks.append({"name": "hermes send (delivery)", "status": "fail",
                       "detail": "hermes CLI not on PATH", "fix": "add hermes to PATH"})

    # 2. Device toolset + registration
    status = escalate.device_status(dispatch)
    if not status["available"]:
        checks.append({"name": "mobile_device toolset", "status": "fail",
                       "detail": status["reason"],
                       "fix": "hermes config set toolsets '[\"hermes-cli\",\"mobile_device\"]' then restart"})
    elif not status["devices"]:
        checks.append({"name": "mobile_device toolset", "status": "warn",
                       "detail": "tools loaded but no device registered",
                       "fix": "open the Hermes app on your phone and sign in"})
    else:
        checks.append({"name": "mobile_device toolset", "status": "ok",
                       "detail": f"{len(status['devices'])} device(s)", "fix": ""})

    # 3. Push freshness (gates T4/T5)
    ready, reason = escalate.push_ready(dispatch)
    checks.append({"name": "push (T4/T5 gate)", "status": "ok" if ready else "warn",
                   "detail": reason,
                   "fix": "" if ready else "open the Hermes app; if writes stay pending, configure push"})

    # 4. Delivery target configured
    from . import queue as q
    target = q.kv_get(conn, "target")
    checks.append({"name": "delivery target", "status": "ok" if target else "warn",
                   "detail": target or "not set (will use hermes send home channel)",
                   "fix": "" if target else "/sieve target <platform[:chat_id]>"})

    # 5. Out-of-band watchdog (catches a link drop while Desktop is closed)
    import os
    import subprocess
    plist = os.path.expanduser("~/Library/LaunchAgents/com.vgfreak.sieve-watch.plist")
    loaded = False
    try:
        subprocess.run(["launchctl", "print", f"gui/{os.getuid()}/com.vgfreak.sieve-watch"],
                       capture_output=True, timeout=5, check=True)
        loaded = True
    except Exception:
        loaded = False
    checks.append({
        "name": "link watchdog (catches a drop while Desktop is closed)",
        "status": "ok" if loaded else "warn",
        "detail": "loaded" if loaded else (
            "not loaded" if os.path.exists(plist) else "plist not installed"),
        "fix": "" if loaded else "run once in a terminal: launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.vgfreak.sieve-watch.plist (the agent cannot). It cannot see an outage while the Mac is asleep.",
    })

    return checks


def format_checks(checks) -> str:
    icon = {"ok": "✓", "warn": "⚠", "fail": "✗"}
    lines = ["SIEVE Setup Doctor:"]
    for c in checks:
        lines.append(f"{icon.get(c['status'], '?')} {c['name']}: {c['detail']}")
        if c["fix"]:
            lines.append(f"    fix: {c['fix']}")
    return "\n".join(lines)
