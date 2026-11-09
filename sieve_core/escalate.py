"""Escalation arm: mobile alarms/timers/reminders via dispatched device tools.

Every write uses an idempotency key (device rule: never retry with a fresh key)
and alarm writes are verified by read-back (an empty alarms list FALSIFIES a
claimed set). All calls go through ctx.dispatch_tool — the host wires the
mobile_device toolset; if absent, everything degrades cleanly.
"""

import json
import uuid

T4_LABEL = "SIEVE: {label}"
# last_seen older than this counts as stale -> degraded escalation arm
STALE_SECONDS = 24 * 3600


def _dispatch(dispatch, tool, args):
    """dispatch(tool, args) -> result (str or dict). Never raises."""
    try:
        result = dispatch(tool, args)
        if isinstance(result, (dict, list)):
            return json.dumps(result)
        return str(result)
    except Exception as exc:
        return f"ERROR: {exc}"


def _parse(result: str):
    try:
        return json.loads(result)
    except Exception:
        return None


def device_status(dispatch) -> dict:
    """Freshness + grants from mobile_devices (call it every time: the grant
    list is server-synced truth; a remembered scope is stale the moment the user
    toggles a switch).

    Never raises. A readiness check that throws is indistinguishable from a busy
    phone, and both must degrade the alert rather than abort it.
    """
    try:
        raw = _dispatch(dispatch, "mobile_devices", {})
    except Exception as exc:
        return {"available": False, "reason": f"device toolset unavailable ({exc})"}
    if isinstance(raw, str) and raw.startswith("ERROR"):
        return {"available": False, "reason": raw[:200]}
    data = _parse(raw)
    if not data:
        return {"available": False, "reason": "mobile_device toolset unavailable"}
    devices = data.get("devices") or data.get("current_access", {}).get("devices", []) or []
    return {"available": True, "raw": data, "devices": devices}


def push_ready(dispatch) -> tuple:
    """(ready, reason). Ready = device toolset answers AND a device has been seen recently."""
    status = device_status(dispatch)
    if not status["available"]:
        return False, "device toolset unavailable (mobile_device not loaded)"
    if not status["devices"]:
        return False, "no device registered (open the Hermes app on your phone)"
    import time
    fresh = False
    for dev in status["devices"]:
        seen = dev.get("last_seen") or dev.get("lastSeen")
        if seen:
            try:
                from datetime import datetime
                ts = datetime.fromisoformat(str(seen).replace("Z", "+00:00")).timestamp()
                if time.time() - ts < STALE_SECONDS:
                    fresh = True
            except Exception:
                pass
    if not fresh:
        return False, "device stale (no recent sync; open the Hermes app)"
    return True, "device fresh"


def fire_alarm(dispatch, label: str, at_iso: str = None, time_hhmm: str = None,
               idempotency_key: str = None) -> dict:
    """T5/T4 alarm. Rings through Silent and Focus. Verified by read-back."""
    key = idempotency_key or f"sieve-{uuid.uuid4()}"
    args = {"idempotency_key": key, "label": T4_LABEL.format(label=label)[:100]}
    if at_iso:
        args["at"] = at_iso
    elif time_hhmm:
        args["time"] = time_hhmm
    else:
        return {"ok": False, "reason": "no schedule given"}
    try:
        raw = _dispatch(dispatch, "mobile_alarm_create", args)
    except Exception as exc:
        return {"ok": False, "reason": f"alarm write could not be dispatched ({exc})"}
    if str(raw).startswith("ERROR"):
        return {"ok": False, "reason": raw}
    try:
        listing = _dispatch(dispatch, "mobile_alarms_list", {})
    except Exception as exc:
        return {"ok": True, "verified": False,
                "reason": f"alarm write accepted, read-back failed ({exc})"}
    data = _parse(listing)
    alarms = (data or {}).get("alarms", []) if isinstance(data, dict) else []
    verified = any(str(args["label"]) in str(a) for a in alarms) if alarms else False
    return {"ok": True, "verified": verified, "create": raw[:300], "idempotency_key": key}


def fire_timer(dispatch, duration_seconds: int, label: str,
               idempotency_key: str = None) -> dict:
    key = idempotency_key or f"sieve-{uuid.uuid4()}"
    try:
        raw = _dispatch(dispatch, "mobile_timer_start", {
            "idempotency_key": key,
            "duration_seconds": int(duration_seconds),
            "label": (T4_LABEL.format(label=label))[:100],
        })
    except Exception as exc:
        return {"ok": False, "reason": f"timer could not be dispatched ({exc})"}
    return {"ok": not str(raw).startswith("ERROR"), "result": str(raw)[:300]}


def fire_reminder(dispatch, title: str, due_iso: str, idempotency_key: str = None) -> dict:
    """Reminders always carry a due date: an undated item never fires."""
    key = idempotency_key or f"sieve-{uuid.uuid4()}"
    try:
        raw = _dispatch(dispatch, "mobile_reminder_create", {
            "idempotency_key": key, "title": title[:100], "due": due_iso,
        })
    except Exception as exc:
        return {"ok": False, "reason": f"reminder could not be dispatched ({exc})"}
    return {"ok": not str(raw).startswith("ERROR"), "result": str(raw)[:300]}
