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

from sieve_core import classify, delivery, doctor, escalate, matrix, probe, queue, scheduler, setup  # noqa: E402

_VERSION = "0.1.0"
_CTX = None
DEFAULT_TARGET = "telegram"


def _safe_int(raw, default=0) -> int:
    try:
        return int(str(raw).strip())
    except (TypeError, ValueError):
        return default


def _landed(result: dict) -> bool:
    """True when a person was reached, or a phone write was accepted.

    Messaging success counts. An accepted alarm or timer also counts, so a
    dead chat pipe cannot make the next sweep ring the phone again.
    """
    if not result:
        return False
    if result.get("delivered"):
        return True
    return result.get("arm") in ("alarm", "timer") and result.get("escalated") is True


def _record_delivery(conn, sent: dict, what: str) -> None:
    rec = {
        "what": what,
        "ok": list(sent.get("ok") or []),
        "failed": list(sent.get("failed") or []),
        "delivered": bool(sent.get("delivered")),
        "at": datetime.now().isoformat(timespec="seconds"),
    }
    queue.kv_set(conn, "last_delivery", json.dumps(rec)[:500])


def _phone_wanted(conn) -> bool:
    return (queue.kv_get(conn, "phone_arm") or "yes") != "no"


def _desktop_notice(conn, tier: int, title: str, body: str) -> None:
    rec = {
        "id": datetime.now().isoformat(timespec="seconds"),
        "tier": int(tier),
        "title": title[:80],
        "body": body[:240],
    }
    queue.kv_set(conn, "desktop_notice", json.dumps(rec))


def _digest_targets(conn) -> list:
    """Global chain, else any tier chain the user actually set, else telegram.

    A Discord-only or iMessage-only setup must not have its digest silently
    rewritten onto telegram. The telegram rung is only the empty-store default.
    """
    chain = delivery.parse_chain(queue.kv_get(conn, "target") or "")
    if chain:
        return chain
    named = []
    for tier in range(6):
        for leg in delivery.parse_chain(queue.kv_get(conn, f"target:T{tier}") or ""):
            if leg not in named:
                named.append(leg)
    return named or [DEFAULT_TARGET]

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
    promo = _safe_int(queue.kv_get(conn, "promo_minutes") or 0)
    if not promo:
        return 0
    backoff = queue.kv_get(conn, "promo_backoff_until") or ""
    if backoff > datetime.now().isoformat(timespec="seconds"):
        return 0
    cutoff = (datetime.now() - timedelta(minutes=promo)).isoformat(timespec="seconds")
    rows = queue.stale_holds(conn, cutoff)
    n = 0
    for (hold_id, eid, tier, event_class) in rows:
        new_tier = min(tier + 1, 5)
        still_held, _why = scheduler.is_held(
            new_tier, queue.windows(conn), queue.active_mutes(conn, queue.now_iso()),
            queue.kv_get(conn, "paused_until"))
        if still_held:
            continue
        decision = matrix.Decision(tier=new_tier, rule_id=f"promoted:{eid}",
                                   label=f"promotion ladder: held {promo}m, escalated T{tier}->T{new_tier}",
                                   source="user")
        result = _deliver(conn, eid, event_class, {}, decision, detail=f"was held since {cutoff}")
        if _landed(result):
            queue.release_one(conn, hold_id)
            n += 1
            continue
        queue.kv_set(conn, "promo_backoff_until",
                     (datetime.now() + timedelta(minutes=promo)).isoformat(timespec="seconds"))
        break
    return n


def _maybe_digest(conn, now=None) -> str:
    """Resurface held items on a timer. Held must never mean forgotten.

    Called at session end so it never interrupts a turn. Interval comes from
    `digest_every_hours` (default 8, 0 disables). Nothing else in the plugin
    schedules a digest, so without this held items only resurface when the user
    happens to run /sieve flush.
    """
    try:
        hours = float(queue.kv_get(conn, "digest_every_hours") or 8)
    except (TypeError, ValueError):
        hours = 8.0
    if hours <= 0:
        return ""
    rows = queue.held_events(conn)
    if not rows:
        return ""
    now = now or datetime.now()
    last = queue.kv_get(conn, "last_digest_at")
    if last:
        try:
            if (now - datetime.fromisoformat(last)).total_seconds() < hours * 3600:
                return ""
        except Exception:
            pass
    text = delivery.format_digest(rows)
    sent = delivery.send_chain(text, _digest_targets(conn))
    _record_delivery(conn, sent, "digest")
    if not sent.get("delivered"):
        failed = ", ".join(sent.get("failed") or ["no target"])
        return f"digest failed: {len(rows)} held item(s) kept | failed: {failed}"
    queue.release_all(conn)
    queue.kv_set(conn, "last_digest_at", now.isoformat(timespec="seconds"))
    queue.kv_set(conn, "escalation_degraded", "")
    return f"digest sent: {len(rows)} held item(s)"


def _safe_route(event_class: str, payload: dict, detail: str = "") -> dict:
    """Hook entry point. Fails closed and quiet.

    A boundary plugin that raises into the host can break the user's turn, and a
    boundary that breaks the thing it is guarding is worse than no boundary. The
    error is recorded for `/sieve why` instead of propagating.
    """
    try:
        return _route(event_class, payload, detail)
    except Exception as exc:
        try:
            queue.kv_set(queue.db(), "last_hook_error", f"{event_class}: {exc}"[:200])
        except Exception:
            pass
        return {"error": "hook error swallowed", "detail": str(exc)[:200]}


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

        # attention budget: cap interrupts per rolling hour; overflow auto-holds.
        # Applies to chatter only (T2/T3). An alarm or gate is not chatter: a
        # budget that swallows a T4 defeats the reason the tier exists.
        budget = _safe_int(queue.kv_get(conn, "budget_per_hour") or 0)
        if not held and budget and 2 <= decision.tier <= 3:
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

        result = _deliver(conn, eid, event_class, payload, decision, detail)
        if not _landed(result):
            queue.set_event_outcome(
                conn, eid, "failed",
                f"{detail} | delivery failed on every target".strip(" |"))
            queue.add_hold(conn, eid)
            result = dict(result)
            result["held"] = True
        return result
    except Exception as exc:
        return {"error": str(exc)[:200]}


def _targets_for(conn, tier):
    """Per-tier chain, else the global target, else the default.

    A chain is redundant delivery, not failover: for T4/T5 the point is that the
    alert arrives on at least one pipe the user actually looks at.
    """
    chain = delivery.parse_chain(queue.kv_get(conn, f"target:T{tier}"))
    if chain:
        return chain
    raw = queue.kv_get(conn, "target") or DEFAULT_TARGET
    return delivery.parse_chain(raw) or [DEFAULT_TARGET]


def _deliver(conn, eid, event_class, payload, decision, detail=""):
    targets = _targets_for(conn, decision.tier)
    tier = decision.tier
    if tier >= 4 and not _phone_wanted(conn):
        why = "no phone arm configured (desktop and chat only)"
        queue.kv_set(conn, "escalation_degraded", why)
        msg = delivery.format_event(
            event_class, tier, decision.label, detail,
            arm="desktop toast (in-app; no phone)", degraded=why)
        sent = delivery.send_chain(msg, targets)
        _record_delivery(conn, sent, f"T{tier}")
        _desktop_notice(conn, tier, f"SIEVE T{tier}", msg)
        return {"event_id": eid, "delivered": sent["delivered"],
                "escalated": False, "arm": "desktop", "verified": False,
                "degraded": why, "targets": sent, "tier": tier}
    if tier >= 4:
        ready, why = escalate.push_ready(_dispatch)
        if ready:
            if tier == 5:
                at = (datetime.now() + timedelta(minutes=1)).isoformat(timespec="seconds")
                result = escalate.fire_alarm(
                    _dispatch, event_class, at_iso=at,
                    idempotency_key=f"sieve-{eid}-alarm")
                achieved = "alarm"
            else:
                result = escalate.fire_timer(
                    _dispatch, 60, event_class,
                    idempotency_key=f"sieve-{eid}-timer")
                achieved = "timer"
            if not result.get("ok"):
                why = str(result.get("reason") or "phone arm write refused")[:200]
                queue.kv_set(conn, "escalation_degraded", why)
                msg = delivery.format_event(
                    event_class, tier, decision.label, detail,
                    arm=delivery.arm_label(tier, "push"), degraded=why)
                sent = delivery.send_chain(msg, targets)
                _record_delivery(conn, sent, f"T{tier}")
                _desktop_notice(conn, tier, f"SIEVE T{tier}", msg)
                return {"event_id": eid, "delivered": sent["delivered"],
                        "escalated": False, "arm": "push", "verified": False,
                        "degraded": why, "targets": sent, "tier": tier}
            verified = bool(result.get("verified"))
            arm = delivery.arm_label(tier, achieved)
            if achieved == "alarm":
                arm += " [verified]" if verified else " [write unverified]"
            sent = delivery.send_chain(
                delivery.format_event(event_class, tier, decision.label, detail, arm=arm), targets)
            _record_delivery(conn, sent, f"T{tier}")
            if achieved == "alarm" and not verified:
                why = str(result.get("reason") or "alarm write accepted, read-back missed")[:200]
                queue.kv_set(conn, "escalation_degraded", why)
                return {"event_id": eid, "delivered": sent["delivered"], "escalated": True,
                        "arm": achieved, "verified": False, "degraded": why,
                        "targets": sent, "tier": tier}
            if verified or sent.get("delivered"):
                queue.kv_set(conn, "escalation_degraded", "")
            return {"event_id": eid, "delivered": sent["delivered"],
                    "escalated": True, "arm": achieved, "verified": verified,
                    "targets": sent, "tier": tier}
        queue.kv_set(conn, "escalation_degraded", why)
        msg = delivery.format_event(event_class, tier, decision.label, detail,
                                    arm=delivery.arm_label(tier, "push"), degraded=why)
        sent = delivery.send_chain(msg, targets)
        _record_delivery(conn, sent, f"T{tier}")
        _desktop_notice(conn, tier, f"SIEVE T{tier}", msg)
        return {"event_id": eid, "delivered": sent["delivered"], "arm": "push",
                "degraded": why, "targets": sent, "tier": tier}
    if tier >= 2:
        arm = delivery.arm_label(tier, "push")
        sent = delivery.send_chain(
            delivery.format_event(event_class, tier, decision.label, detail, arm=arm), targets)
        _record_delivery(conn, sent, f"T{tier}")
        return {"event_id": eid, "delivered": sent["delivered"], "arm": "push",
                "targets": sent, "tier": tier}
    # tier 1: chip only (desktop chip reads held/recent counts)
    return {"event_id": eid, "delivered": True, "chip": True, "tier": tier}


def _surface() -> str:
    try:
        from gateway.session_context import get_session_env
        return (get_session_env("HERMES_SESSION_PLATFORM")
                or get_session_env("HERMES_PLATFORM")
                or get_session_env("HERMES_SESSION_SOURCE")
                or "messaging")
    except Exception:
        return "unknown"


def _stamp_phone_check(conn, text: str) -> str:
    """Remember a phone check that actually ran, so the Desktop pane can show it later.

    The pane and a refused Desktop slash command never reach this. Only a handler
    with a plugin context (Discord, Telegram) stamps the result.
    """
    if _CTX is None:
        return text
    rec = {
        "at": datetime.now().isoformat(timespec="seconds"),
        "surface": _surface(),
        "text": text[:400],
    }
    queue.kv_set(conn, "last_phone_check", json.dumps(rec))
    return (text + f"\n[ran on {rec['surface']}. The Desktop pane cannot repeat a phone write. "
            "Desktop chat may refuse /sieve with permission_denied. This result stays on the pane.]")


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
        last = queue.kv_get(conn, "last_delivery")
        if last:
            lines.append(f"last delivery: {last}")
        ts = queue.kv_get(conn, "tailscale_up")
        if ts:
            lines.append(f"tailscale: {ts} ({queue.kv_get(conn, 'tailscale_detail') or 'no detail'})")
        gw = queue.kv_get(conn, "gateway_up")
        if gw:
            lines.append(f"gateway: {gw} ({queue.kv_get(conn, 'gateway_detail') or 'no detail'})")
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

    if sub == "digest":
        if not rest:
            every = queue.kv_get(conn, "digest_every_hours") or "8"
            held = queue.held_count(conn)
            last = queue.kv_get(conn, "last_digest_at") or "never"
            return (f"digest every {every}h (0=off) | {held} held | last sent: {last}")
        arg = rest[0].lower()
        if arg in ("off", "0", "never"):
            queue.kv_set(conn, "digest_every_hours", "0")
            return "digest disabled (held items only on /sieve flush)"
        try:
            hours = float(arg.rstrip("h"))
        except ValueError:
            return "usage: /sieve digest [<hours>|off]"
        queue.kv_set(conn, "digest_every_hours", str(hours))
        return f"digest every {hours}h"

    if sub == "flush":
        rows = queue.held_events(conn)
        if not rows:
            return "flushed 0 held item(s)"
        sent = delivery.send_chain(delivery.format_digest(rows), _digest_targets(conn))
        _record_delivery(conn, sent, "flush")
        if not sent.get("delivered"):
            failed = ", ".join(sent.get("failed") or ["no target"])
            return f"flush failed: {len(rows)} held item(s) kept | failed: {failed}"
        n = queue.release_all(conn)
        queue.kv_set(conn, "escalation_degraded", "")
        return f"flushed {n} held item(s)"

    if sub == "mute":
        # /sieve mute T4 until 9am
        if len(rest) < 2:
            return "usage: /sieve mute T<n> until <9am|2h|23:30>"
        tier = int(rest[0].lower().lstrip("t"))
        until_spec = " ".join(rest[2:]) if rest[1].lower() == "until" else " ".join(rest[1:])
        until = scheduler.parse_until(until_spec)
        queue.add_mute(conn, tier, until.isoformat(timespec="seconds"))
        band = f"T0-T{tier}" if tier else "T0"
        alarm_note = "" if tier >= 4 else " (alarms T4+ still reach you)"
        return f"muted {band} until {until:%H:%M}{alarm_note}"

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
        return f"paused (everything held to digest, including alarms) until {until:%H:%M}"

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
        return _stamp_phone_check(conn, doctor.format_checks(checks))

    if sub == "test":
        # /sieve test T4 | T5  (the first-enable ritual)
        tier = int(rest[0].lower().lstrip("t")) if rest else 4
        ready, why = escalate.push_ready(_dispatch)
        if not ready:
            blocked = f"push-gate blocked: {why}. Run /sieve doctor in Discord or Telegram, not in the Desktop pane or Desktop chat."
            return _stamp_phone_check(conn, blocked)
        key = f"sieve-test-{int(datetime.now().timestamp())}"
        if tier >= 5:
            at = (datetime.now() + timedelta(minutes=1)).isoformat(timespec="seconds")
            result = escalate.fire_alarm(_dispatch, "SIEVE TEST (alarm tier)", at_iso=at,
                                        idempotency_key=key)
        else:
            result = escalate.fire_timer(_dispatch, 60, "SIEVE TEST (pleasant tier)",
                                        idempotency_key=key)
        verdict = "ok" if result.get("ok") else f"failed: {result.get('reason', result)}"
        out = f"test T{tier}: {verdict}"
        if "verified" in result:
            out += f" (read-back {'verified' if result.get('verified') else 'UNVERIFIED'})"
        return _stamp_phone_check(conn, out)

    if sub == "target":
        # /sieve target                          -> show global + per-tier chains
        # /sieve target telegram                 -> set global
        # /sieve target t5 telegram,photon:...   -> set chain for T5
        # /sieve target t5 clear                 -> drop the per-tier override
        if not rest:
            global_t = queue.kv_get(conn, "target") or DEFAULT_TARGET
            lines = [f"target: {global_t}" + (" (default)" if not queue.kv_get(conn, 'target') else "")]
            for t in (2, 3, 4, 5):
                chain = delivery.parse_chain(queue.kv_get(conn, f"target:T{t}"))
                if chain:
                    lines.append(f"  T{t}: {', '.join(chain)}")
            lines.append("set with: /sieve target t5 telegram,photon:any;-;+15551234567")
            return "\n".join(lines)
        first = rest[0]
        if first.lower().startswith("t") and first[1:].isdigit():
            tier = int(first[1:])
            if not 0 <= tier <= 5:
                return "tier must be T0-T5"
            if len(rest) == 1:
                chain = delivery.parse_chain(queue.kv_get(conn, f"target:T{tier}"))
                return f"T{tier} chain: {', '.join(chain) if chain else '(inherits global)'}"
            if rest[1].lower() == "clear":
                queue.kv_set(conn, f"target:T{tier}", "")
                return f"T{tier} chain cleared (inherits global target)"
            chain = delivery.parse_chain(rest[1])
            if not chain:
                return "empty chain"
            queue.kv_set(conn, f"target:T{tier}", ",".join(chain))
            return f"T{tier} chain set: {', '.join(chain)}"
        chain = delivery.parse_chain(first)
        if not chain:
            return "empty target"
        queue.kv_set(conn, "target", ",".join(chain))
        return f"delivery target set: {', '.join(chain)}"

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

    if sub == "export":
        import os
        path = rest[0] if rest else os.path.expanduser("~/.hermes/sieve-policy.json")
        path = os.path.expanduser(path)
        data = queue.export_policy(conn)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, sort_keys=True)
        return (f"policy exported: {len(data['rules'])} rule(s), "
                f"{len(data['quiet_windows'])} window(s), {len(data['mutes'])} mute(s), "
                f"{len(data['kv'])} setting(s) -> {path}")

    if sub == "import":
        import os
        if not rest:
            return "usage: /sieve import <path> [replace]"
        path = os.path.expanduser(rest[0])
        replace = len(rest) > 1 and rest[1].lower() in ("replace", "overwrite")
        if not os.path.exists(path):
            return f"no such policy file: {path}"
        if os.path.getsize(path) > 1_000_000:
            return "policy file too large (1MB limit)"
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
        except Exception as exc:
            return f"policy file is not valid JSON: {exc}"
        try:
            applied = queue.import_policy(conn, data, replace=replace)
        except Exception as exc:
            return f"import refused: {exc}"
        mode = "replaced" if replace else "merged"
        return (f"policy {mode}: {applied['rules']} rule(s), "
                f"{applied['quiet_windows']} window(s), {applied['mutes']} mute(s), "
                f"{applied['kv']} setting(s). Run /sieve status to confirm.")

    if sub == "probe":
        result = probe.observe(conn, _safe_route, force=True)
        if not result.get("installed"):
            return ("tailscale is not installed. That is not an outage. "
                    "/sieve fire service_down is the manual path.")
        if result.get("fired"):
            return f"Tailscale went down. Routed service_down. {result.get('detail') or ''}"
        state = "up" if result.get("up") else "down" if result.get("up") is False else "unknown"
        return f"Tailscale {state}. No new outage. {result.get('detail') or ''}"

    if sub == "fire":
        cls = rest[0] if rest else "task_done"
        if cls not in setup.CLASSES:
            return f"unknown class {cls}. Known: {', '.join(setup.CLASSES)}"
        ctx_name = rest[1] if len(rest) > 1 else "base"
        result = _safe_route(cls, {"summary": "manual fire", "context": ctx_name})
        return f"fired {cls}: {result}"

    if sub == "arm":
        if not rest:
            mode = queue.kv_get(conn, "phone_arm") or "yes"
            return (f"phone arm: {mode} (yes | no | both). "
                    "no means T4/T5 stay on desktop toast and the chat chain. "
                    "A ring through Silent is not available.")
        mode = rest[0].lower()
        if mode not in ("yes", "no", "both"):
            return "usage: /sieve arm yes|no|both"
        queue.kv_set(conn, "phone_arm", "no" if mode == "no" else "yes")
        if mode == "no":
            return "phone arm off. T4/T5 use an in-app desktop toast plus the chat chain. No ring."
        return "phone arm on. T4/T5 still try the phone, and say so when they cannot."

    if sub == "setup":
        try:
            return setup.handle(conn, rest)
        except ValueError as exc:
            return f"setup refused: {exc}"

    if sub == "matrix":
        if rest and rest[0] == "set":
            try:
                return setup.matrix_set(conn, rest[1:])
            except ValueError as exc:
                return f"matrix refused: {exc}"
        return setup.matrix_lines(conn)

    return ("commands: status | why | flush | mute | quiet | pause | rules | context | "
            "simulate | budget | promote | export | import | doctor | test | target | "
            "setup | matrix")


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
        _safe_route("needs_human", {"kind": str(kind), "platform": str(kwargs.get("platform", "")),
                               "summary": str(kwargs.get("prompt", ""))[:200]})

    ctx.register_hook("on_human_input_request", hook_needs_human)

    def hook_human_resolved(*args, **kwargs):
        try:
            conn = queue.db()
            _desktop_notice(conn, 2, "SIEVE", "Needs-human request was answered. No further alarm from this one.")
            queue.kv_set(conn, "needs_human_resolved_at", datetime.now().isoformat(timespec="seconds"))
        except Exception:
            pass

    ctx.register_hook("on_human_input_resolved", hook_human_resolved)

    def hook_kanban_blocked(*args, **kwargs):
        _safe_route("needs_human", {"kind": "kanban", "summary": str(kwargs)[:200]})

    ctx.register_hook("kanban_task_blocked", hook_kanban_blocked)

    def hook_subagent_stop(*args, **kwargs):
        _safe_route("task_done", {"summary": f"subagent finished: {str(kwargs)[:150]}"})

    ctx.register_hook("subagent_stop", hook_subagent_stop)

    def hook_session_end(*args, **kwargs):
        # Digest opportunity first: session end is the natural place to resurface
        # held items, and doing it here never interrupts a turn.
        try:
            _maybe_digest(queue.db())
        except Exception:
            pass
        task_id = kwargs.get("task_id") or ""
        if not task_id:
            return  # ordinary conversation end: not a notification
        failed = kwargs.get("failed")
        event_class = "task_failed" if failed else "cron_done"
        _safe_route(event_class, {"summary": f"run {task_id} exit={kwargs.get('turn_exit_reason', '')}"[:200]})

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
        description="SIEVE notification boundary: status|why|flush|digest|mute|quiet|pause|rules|setup|matrix|doctor|test|target|export|import",
        args_hint="[subcommand] [args]")
