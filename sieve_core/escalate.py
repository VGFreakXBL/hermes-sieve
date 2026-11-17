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
        if "ctx unavailable" in str(exc).lower():
            return {"available": False,
                    "reason": "no tool context on this surface (this surface cannot dispatch phone tools)"}
        return {"available": False, "reason": f"device toolset unavailable ({exc})"}
    if isinstance(raw, str) and raw.startswith("ERROR"):
        return {"available": False, "reason": raw[:200]}
    data = _parse(raw)
    if not data:
        return {"available": False, "reason": "mobile_device toolset not loaded"}
    devices = data.get("devices") or data.get("current_access", {}).get("devices", []) or []
    return {"available": True, "raw": data, "devices": devices}


def push_ready(dispatch) -> tuple:
    """(ready, reason). Ready = device toolset answers AND a device has been seen recently."""
    status = device_status(dispatch)
    if not status["available"]:
        # Name the real reason, not a canned one: "device toolset unavailable"
        # and "no tool context" are different failures and the message must not
        # merge them. The pane has no device toolset in its context at all.
        return False, status.get("reason") or "device toolset unavailable"
    if not status["devices"]:
        return False, "no device paired to this profile (open the Hermes app and sign in)"
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
        return False, "device stale (no recent sync)"
    return True, "device fresh"


def push_fix(reason: str) -> str:
    """The reachable next step for a push-gate reason. Never points at an
    action this build cannot complete: there is no push registration to
    configure, and the fix text must not pretend otherwise."""
    r = (reason or "").lower()
    if "no tool context" in r or "ctx unavailable" in r:
        return ("this surface cannot write the phone by design. "
                "Run /sieve doctor and /sieve test from a Discord or Telegram chat.")
    if "toolset" in r:
        return "hermes config set toolsets '[\"hermes-cli\",\"mobile_device\"]', then restart."
    if "no device" in r or "not paired" in r:
        return "open the Hermes app and sign in to this profile. Pairing follows the sign-in."
    if "stale" in r:
        return ("open the Hermes app once and let it sync. Writes queue while the app is closed; "
                "closed-app wake is a known limit of this build.")
    if "push is not configured" in r:
        return ("no enable path exists in this build. T4/T5 ring only while the Hermes app is "
                "open and synced; writes queue while it is closed.")
    return "run /sieve doctor for the fix path."


def fire_alarm(dispatch, label: str, at_iso: str = None, time_hhmm: str = None,
               idempotency_key: str = None) -> dict:
    """T5/T4 alarm. Rings through Silent and Focus. Verified by read-back."""
    key = idempotency_key or f"sieve-{uuid.uuid4()}"
    args = {"idempotency_key": key, "label": T4_LABEL.format(label=label)[:100]}
    if at_iso:
        # The device bridge requires ISO 8601 WITH a timezone; callers pass
        # naive local time (datetime.now() + delta), which the bridge rejects
        # with "Use an ISO 8601 date with timezone for at." Normalize here so
        # every caller (the live T5 path and /sieve test alike) is covered.
        ts = str(at_iso)
        try:
            from datetime import datetime as _dt
            parsed = _dt.fromisoformat(ts)
            if parsed.tzinfo is None:
                ts = parsed.astimezone().isoformat(timespec="seconds")
        except Exception:
            pass
        args["at"] = ts
    elif time_hhmm:
        args["time"] = time_hhmm
    else:
        return {"ok": False, "reason": "no schedule given"}
    try:
        raw = _dispatch(dispatch, "mobile_alarm_create", args)
    except Exception as exc:
        return {"ok": False, "reason": f"alarm write could not be dispatched ({exc})"}
    if str(raw).startswith("ERROR"):
        return {"ok": False, "reason": str(raw)[:200]}
    data = raw if isinstance(raw, dict) else _parse(raw)
    if isinstance(data, dict):
        err = data.get("error")
        res = data.get("result")
        if not err and isinstance(res, dict):
            err = res.get("error")
        landed = isinstance(res, dict) and res.get("data")
        if err and not landed:
            reason = str(err)[:200]
            if "idempotency" in reason.lower():
                reason += (" (a changed payload is a new action and needs a fresh idempotency "
                           "key; only an identical retry reuses the key)")
            return {"ok": False, "reason": reason}
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
