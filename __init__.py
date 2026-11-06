"""SIEVE — a user-owned notification boundary for Hermes Agent.

Pipeline: event -> user-owned boundary matrix (rules beat defaults) ->
scheduler (quiet hours / mutes / pause, delay-never-drop) -> delivery arm
(digest hold, message, or push-gated mobile escalation).

Observer spikes from seam recon are retained for one more cycle (docs/seams.md).
"""

import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

_ROOT = Path(__file__).resolve().parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from sieve_core import classify, delivery, doctor, escalate, matrix, queue, scheduler  # noqa: E402

_VERSION = "0.1.0"
_CTX = None
DEFAULT_TARGET = "telegram"

# --- seam-recon spikes (log-only observers; Task 1.2, retained) -----------
_SPIKE_LOG = _ROOT / "docs" / "spike-events.jsonl"
_REDACT_KEYS = {"command", "prompt", "password", "answer", "content", "text"}
SPIKE_EVENTS = (
    "pre_gateway_dispatch", "on_human_input_request", "on_human_input_resolved",
    "pre_approval_request", "post_approval_response", "kanban_task_blocked",
    "subagent_stop", "on_session_end", "on_interim_message",
)


def _spike(event, args, kwargs):
    rec = {"ts": datetime.now().isoformat(timespec="seconds"), "event": event,
           "args": [str(a)[:200] for a in args]}
    rec.update({k: str(v)[:200] for k, v in kwargs.items() if k not in _REDACT_KEYS})
    try:
        with _SPIKE_LOG.open("a") as f:
            f.write(json.dumps(rec, default=str) + "\n")
    except Exception:
        pass


def _make_spiker(event):
    def spiker(*args, **kwargs):
        _spike(event, args, kwargs)
    return spiker


# --- core routing ----------------------------------------------------------

def _promote_sweep(conn) -> int:
    """Promotion ladder: held items older than promo_minutes escalate one tier."""
    promo = int(queue.kv_get(conn, "promo_minutes") or 0)
    if not promo:
        return 0
    cutoff = (datetime.now() - timedelta(minutes=promo)).isoformat(timespec="seconds")
    rows = queue.stale_holds(conn, cutoff)
    n = 0
    for (hold_id, eid, tier, event_class) in rows:
        queue.release_one(conn, hold_id)
        new_tier = min(tier + 1, 5)
        decision = matrix.Decision(tier=new_tier, rule_id=f"promoted:{eid}",
                                   label=f"promotion ladder: held {promo}m, escalated T{tier}->T{new_tier}",
                                   source="user")
        queue.log_event(conn, event_class, {"promoted_from": eid}, new_tier, "delivered",
                        decision.rule_id, detail=decision.label)
        _deliver(conn, eid, event_class, {}, decision, detail=f"was held since {cutoff}")
        n += 1
    return n


def _route(event_class: str, payload: dict, detail: str = "") -> dict:
    try:
        conn = queue.db()
    except Exception:
        return {"error": "store unavailable"}
    try:
        _promote_sweep(conn)
        payload = dict(payload)
        payload.setdefault("context", queue.kv_get(conn, "active_context") or "base")
        decision = matrix.decide(event_class, payload, queue.rules_for(conn, event_class))
        now = datetime.now()
        paused_until = queue.kv_get(conn, "paused_until")
        held, reason = scheduler.is_held(
            decision.tier, queue.windows(conn),
            queue.active_mutes(conn, queue.now_iso()), paused_until, now)
        if decision.tier == 0 and not held:
            held, reason = True, "tier T0 -> digest by design"

        # attention budget: cap interrupts per rolling hour; overflow auto-holds
        budget = int(queue.kv_get(conn, "budget_per_hour") or 0)
        if not held and budget and decision.tier >= 2:
            since = (now - timedelta(hours=1)).isoformat(timespec="seconds")
            if queue.recent_deliveries(conn, since) >= budget:
                held, reason = True, f"attention budget ({budget}/h) reached"

        # repeat limit (olympus pattern): the same class re-alerts at most once
        # per repeat_limit_hours. Applies to T2 only: gates and alarms (T3+)
        # must always reach the human because they block work or breach quiet.
        limit_h = float(queue.kv_get(conn, "repeat_limit_hours") or 6)
        if not held and limit_h and decision.tier == 2:
            since = (now - timedelta(hours=limit_h)).isoformat(timespec="seconds")
            row = conn.execute(
                "SELECT COUNT(*) FROM events WHERE event_class = ? AND action = 'delivered'"
                " AND tier = 2 AND ts > ?", (event_class, since)).fetchone()
            if row and row[0] > 0:
                held, reason = True, f"repeat limit ({limit_h}h) for {event_class}"

        eid = queue.log_event(
            conn, event_class, payload, decision.tier,
            "held" if held else "delivered", decision.rule_id,
            detail=f"{decision.label} | {reason}".strip(" |"))

        if held:
            queue.add_hold(conn, eid)
            return {"event_id": eid, "held": True, "reason": reason, "tier": decision.tier}

        return _deliver(conn, eid, event_class, payload, decision, detail)
    except Exception as exc:
        return {"error": str(exc)[:200]}


def _deliver(conn, eid, event_class, payload, decision, detail=""):
    target = queue.kv_get(conn, "target") or DEFAULT_TARGET
    tier = decision.tier
    if tier >= 4:
        ready, why = escalate.push_ready(_dispatch)
        if ready:
            if tier == 5:
                at = (datetime.now() + timedelta(minutes=1)).isoformat(timespec="seconds")
                result = escalate.fire_alarm(_dispatch, event_class, at_iso=at)
            else:
                result = escalate.fire_timer(_dispatch, 60, event_class)
            delivery.send(delivery.format_event(event_class, tier, decision.label, detail), target)
            return {"event_id": eid, "delivered": True, "escalated": result.get("ok"),
                    "verified": result.get("verified"), "tier": tier}
        # degrade: never drop, alarm impossible -> message + flag
        queue.kv_set(conn, "escalation_degraded", why)
        msg = delivery.format_event(event_class, 3, decision.label,
                                    f"[escalation degraded: {why}] {detail}")
        delivery.send(msg, target)
        return {"event_id": eid, "delivered": True, "degraded": why, "tier": tier}
    if tier >= 2:
        ok = delivery.send(delivery.format_event(event_class, tier, decision.label, detail), target)
        return {"event_id": eid, "delivered": ok, "tier": tier}
    # tier 1: chip only (desktop chip reads held/recent counts)
    return {"event_id": eid, "delivered": True, "chip": True, "tier": tier}


def _dispatch(tool: str, args: dict):
    if _CTX is None:
        raise RuntimeError("ctx unavailable")
    return _CTX.dispatch_tool(tool, args)


# --- commands ---------------------------------------------------------------

def _cmd(raw_args: str) -> str:
    try:
        conn = queue.db()
    except Exception:
        return "SIEVE: store unavailable"
    parts = (raw_args or "").strip().split()
    sub = parts[0].lower() if parts else "status"
    rest = parts[1:]
    try:
        return _cmd_inner(conn, sub, rest)
    except Exception as exc:
        return f"SIEVE error: {exc}"


def _cmd_inner(conn, sub, rest) -> str:
    if sub in ("status", ""):
        held = queue.held_count(conn)
        paused = queue.kv_get(conn, "paused_until") or "no"
        mutes = queue.active_mutes(conn, queue.now_iso())
        wins = queue.windows(conn)
        degraded = queue.kv_get(conn, "escalation_degraded")
        lines = [f"SIEVE status: {held} held (digest pending) | paused: {paused}",
                 f"mutes: {len(mutes)} active | quiet windows: {len(wins)}"]
        for (_id, s, e, days, floor, label) in wins:
            lines.append(f"  quiet {s}-{e} days[{days}] floor=T{floor} {label}")
        if degraded:
            lines.append(f"! escalation arm degraded: {degraded}")
        return "\n".join(lines)

    if sub == "why":
        rows = queue.recent_events(conn, 10)
        lines = ["SIEVE - last decisions:"]
        for (_id, ts, event_class, tier, action, detail) in rows:
            lines.append(f"[{ts}] {event_class} T{tier} -> {action} ({detail or 'no rule detail'})")
        held = queue.held_events(conn)
        if held:
            lines.append(f"held now: {len(held)} item(s) awaiting digest")
        return "\n".join(lines)

    if sub == "flush":
        rows = queue.held_events(conn)
        n = queue.release_all(conn)
        if rows:
            target = queue.kv_get(conn, "target") or DEFAULT_TARGET
            delivery.send(delivery.format_digest(rows), target)
        return f"flushed {n} held item(s)"

    if sub == "mute":
        # /sieve mute T4 until 9am
        if len(rest) < 2:
            return "usage: /sieve mute T<n> until <9am|2h|23:30>"
        tier = int(rest[0].lower().lstrip("t"))
        until_spec = " ".join(rest[2:]) if rest[1].lower() == "until" else " ".join(rest[1:])
        until = scheduler.parse_until(until_spec)
        queue.add_mute(conn, tier, until.isoformat(timespec="seconds"))
        return f"muted T{tier} until {until:%H:%M}"

    if sub == "quiet":
        # /sieve quiet 23:00-08:00 [floor=T4] [weekdays]
        if not rest:
            return "usage: /sieve quiet 23:00-08:00 [floor=T<n>] [weekdays|daily|mon,wed]"
        window_spec = rest[0]
        floor, days = 4, "0123456"
        for tok in rest[1:]:
            if tok.lower().startswith("floor="):
                val = tok.split("=", 1)[1].lower()
                floor = 6 if val == "none" else int(val.lstrip("t"))
            else:
                days = scheduler.parse_days(tok)
        start, end = window_spec.split("-", 1)
        queue.add_window(conn, start, end, days, floor)
        return f"quiet window {window_spec} days[{days}] floor=T{floor} added"

    if sub == "pause":
        if rest and rest[0].lower() in ("off", "clear", "0"):
            queue.kv_set(conn, "paused_until", "")
            return "pause cleared"
        spec = rest[0] if rest else "2h"
        until = scheduler.parse_until(spec)
        queue.kv_set(conn, "paused_until", until.isoformat(timespec="seconds"))
        return f"paused (everything held to digest) until {until:%H:%M}"

    if sub == "rules":
        if rest and rest[0] == "add":
            # /sieve rules add <class> <tier> [key=val ...]
            if len(rest) < 3:
                return "usage: /sieve rules add <event_class> <tier> [key=val ...]"
            match = {}
            for tok in rest[3:]:
                if "=" in tok:
                    k, v = tok.split("=", 1)
                    match[k] = v
            rid = queue.add_rule(conn, rest[1], match, int(rest[2].lower().lstrip("t")))
            return f"user rule #{rid} added: {rest[1]} -> T{rest[2]} match={match}"
        rows = queue.all_rules(conn)
        lines = ["SIEVE rules (user rules beat defaults):"]
        for (rid, cls, match_json, tier, enabled, source, label) in rows:
            flag = "" if enabled else " [off]"
            lines.append(f"#{rid} {cls} T{tier} {source}{flag} {label} {match_json}")
        for (cls, match, tier, label) in matrix.DEFAULT_RULES:
            lines.append(f"- {cls} T{tier} default {label}")
        return "\n".join(lines)

    if sub == "doctor":
        checks = doctor.run_checks(_dispatch, conn)
        return doctor.format_checks(checks)

    if sub == "test":
        # /sieve test T4 | T5  (the first-enable ritual)
        tier = int(rest[0].lower().lstrip("t")) if rest else 4
        ready, why = escalate.push_ready(_dispatch)
        if not ready:
            return f"push-gate blocked: {why}. Run /sieve doctor for the fix path."
        key = f"sieve-test-{int(datetime.now().timestamp())}"
        if tier >= 5:
            at = (datetime.now() + timedelta(minutes=1)).isoformat(timespec="seconds")
            result = escalate.fire_alarm(_dispatch, "SIEVE TEST (siren tier)", at_iso=at,
                                        idempotency_key=key)
        else:
            result = escalate.fire_timer(_dispatch, 60, "SIEVE TEST (pleasant tier)",
                                        idempotency_key=key)
        verdict = "ok" if result.get("ok") else f"failed: {result.get('reason', result)}"
        out = f"test T{tier}: {verdict}"
        if "verified" in result:
            out += f" (read-back {'verified' if result.get('verified') else 'UNVERIFIED'})"
        return out

    if sub == "target":
        if not rest:
            return f"target: {queue.kv_get(conn, 'target') or DEFAULT_TARGET + ' (default)'}"
        queue.kv_set(conn, "target", rest[0])
        return f"delivery target set: {rest[0]}"

    if sub == "context":
        if not rest:
            names = ", ".join(c for c, _d in matrix.CONTEXTS)
            return f"active context: {queue.kv_get(conn, 'active_context') or 'base'} ({names})"
        queue.kv_set(conn, "active_context", rest[0])
        return f"active context set: {rest[0]}"

    if sub == "simulate":
        # /sieve simulate <event_class> [context] [keyword]  (dry-run, no side effects)
        if not rest:
            return "usage: /sieve simulate <event_class> [context] [keyword]"
        payload = {"context": rest[1] if len(rest) > 1 else (queue.kv_get(conn, "active_context") or "base")}
        if len(rest) > 2:
            payload["text"] = " ".join(rest[2:])
        d = matrix.decide(rest[0], payload, queue.rules_for(conn, rest[0]))
        held, why = scheduler.is_held(d.tier, queue.windows(conn),
                                      queue.active_mutes(conn, queue.now_iso()),
                                      queue.kv_get(conn, "paused_until"), datetime.now())
        if d.tier == 0 and not held:
            held, why = True, "tier T0 -> digest by design"
        action = "HOLD to digest" if held else ("ESCALATE (alarm)" if d.tier >= 4 else "deliver now")
        line = f"simulate {rest[0]} [{payload['context']}]: T{d.tier} -> {action} | {d.label}"
        return line + (f" | {why}" if why else "")

    if sub == "budget":
        if not rest:
            return f"attention budget: {queue.kv_get(conn, 'budget_per_hour') or 'off'} interrupts/hour (0=off)"
        queue.kv_set(conn, "budget_per_hour", rest[0])
        return f"attention budget set: {rest[0]}/hour"

    if sub == "promote":
        if not rest:
            return f"promotion ladder: {queue.kv_get(conn, 'promo_minutes') or 'off'} min (0=off)"
        queue.kv_set(conn, "promo_minutes", rest[0])
        return f"promotion ladder set: held items escalate after {rest[0]} min"

    return "commands: status | why | flush | mute | quiet | pause | rules | context | simulate | budget | promote | doctor | test | target"


# --- registration -----------------------------------------------------------

def register(ctx):
    global _CTX
    _CTX = ctx

    # schema-typed tools
    schema = {
        "name": "sieve_ping",
        "description": "Health probe for the SIEVE plugin. Returns plugin status.",
        "parameters": {"type": "object", "properties": {}, "required": []},
    }

    def handle_ping(params, **kwargs):
        del params, kwargs
        return json.dumps({"success": True, "plugin": "sieve", "version": _VERSION,
                           "held": queue.held_count(queue.db())})

    ctx.register_tool(name="sieve_ping", toolset="sieve", schema=schema, handler=handle_ping)

    # seam-recon spikes (log-only)
    for event in SPIKE_EVENTS:
        ctx.register_hook(event, _make_spiker(event))

    # --- routing hooks (the SIEVE boundary) --------------------------------
    def hook_needs_human(*args, **kwargs):
        kind = kwargs.get("kind") or (args[0] if args else "unknown")
        _route("needs_human", {"kind": str(kind), "platform": str(kwargs.get("platform", "")),
                               "summary": str(kwargs.get("prompt", ""))[:200]})

    ctx.register_hook("on_human_input_request", hook_needs_human)

    def hook_kanban_blocked(*args, **kwargs):
        _route("needs_human", {"kind": "kanban", "summary": str(kwargs)[:200]})

    ctx.register_hook("kanban_task_blocked", hook_kanban_blocked)

    def hook_subagent_stop(*args, **kwargs):
        _route("task_done", {"summary": f"subagent finished: {str(kwargs)[:150]}"})

    ctx.register_hook("subagent_stop", hook_subagent_stop)

    def hook_session_end(*args, **kwargs):
        task_id = kwargs.get("task_id") or ""
        if not task_id:
            return  # ordinary conversation end: not a notification
        failed = kwargs.get("failed")
        event_class = "task_failed" if failed else "cron_done"
        _route(event_class, {"summary": f"run {task_id} exit={kwargs.get('turn_exit_reason', '')}"[:200]})

    ctx.register_hook("on_session_end", hook_session_end)

    def hook_approval_log(*args, **kwargs):
        # attribution only: approval activity is logged, never interrupts
        try:
            conn = queue.db()
            queue.log_event(conn, "approval_activity",
                            {"choice": str(kwargs.get("choice", ""))[:40],
                             "decided_by": str(kwargs.get("decided_by", ""))[:40],
                             "pattern": str(kwargs.get("pattern_key", ""))[:40]},
                            0, "logged")
        except Exception:
            pass

    ctx.register_hook("post_approval_response", hook_approval_log)

    # --- commands ----------------------------------------------------------
    ctx.register_command(
        "sieve", _cmd,
        description="SIEVE notification boundary: status|why|flush|mute|quiet|pause|rules|doctor|test|target",
        args_hint="[subcommand] [args]")
