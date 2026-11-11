"""SIEVE desktop backend: /api/plugins/sieve/* routes for the Desktop pane.

Runs inside the gateway process. Reads the same plugin_db store the hooks write.
Device-dependent checks (mobile tools) need the agent turn, so the pane points at
the `/sieve doctor` command for those — this route serves store state only.
"""

import importlib.util
import shutil
import sys
from pathlib import Path

from fastapi import APIRouter

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from sieve_core import queue, probe as tailscale_probe  # noqa: E402

_spec = importlib.util.spec_from_file_location("sieve_plugin", _ROOT / "__init__.py")
_plugin = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_plugin)

router = APIRouter()


@router.get("/status")
async def status():
    conn = queue.db()
    try:
        tailscale_probe.observe(conn, _plugin._safe_route)
    except Exception:
        pass
    wins = [{"id": w[0], "start": w[1], "end": w[2], "days": w[3], "floor": w[4], "label": w[5]}
            for w in queue.windows(conn)]
    mutes = [{"id": m[0], "tier": m[1], "until": m[2]}
             for m in queue.active_mutes(conn, queue.now_iso())]
    return {
        "held": queue.held_count(conn),
        "paused_until": queue.kv_get(conn, "paused_until") or "",
        "degraded": queue.kv_get(conn, "escalation_degraded") or "",
        "last_delivery": queue.kv_get(conn, "last_delivery") or "",
        "desktop_notice": queue.kv_get(conn, "desktop_notice") or "",
        "phone_arm": queue.kv_get(conn, "phone_arm") or "yes",
        "target": queue.kv_get(conn, "target") or "telegram",
        "windows": wins,
        "mutes": mutes,
    }


@router.get("/why")
async def why():
    conn = queue.db()
    events = [{"id": r[0], "ts": r[1], "event_class": r[2], "tier": r[3],
               "action": r[4], "detail": r[5]} for r in queue.recent_events(conn, 25)]
    held = [{"id": h[0], "ts": h[1], "event_class": h[2], "tier": h[3]}
            for h in queue.held_events(conn)]
    return {"events": events, "held": held}


@router.get("/rules")
async def rules():
    conn = queue.db()
    user = [{"id": r[0], "event_class": r[1], "match": r[2], "tier": r[3],
             "enabled": bool(r[4]), "source": r[5], "label": r[6]} for r in queue.all_rules(conn)]
    defaults = [{"event_class": c, "match": m, "tier": t, "label": lbl, "source": "default"}
                for (c, m, t, lbl) in _plugin.matrix.DEFAULT_RULES]
    return {"user": user, "defaults": defaults}


@router.get("/doctor")
async def doctor_lite():
    conn = queue.db()
    return {
        "checks": [
            {"name": "hermes send (delivery)", "ok": bool(shutil.which("hermes"))},
            {"name": "delivery target", "ok": bool(queue.kv_get(conn, "target")),
             "detail": queue.kv_get(conn, "target") or "not set"},
        ],
        "note": "device checks (grants, push) need the agent turn: run /sieve doctor in chat",
    }


@router.post("/cmd")
async def cmd(body: dict):
    args = str((body or {}).get("args", ""))
    return {"result": _plugin._cmd(args)}


@router.get("/matrix")
async def matrix_grid():
    """Effective tier for every event class x context (rules applied)."""
    conn = queue.db()
    classes = sorted({c for (c, _m, _t, _l) in _plugin.matrix.DEFAULT_RULES} |
                     {c for (c, _x, _t, _e) in queue.context_rules(conn)})
    grid = {}
    for cls in classes:
        rules = queue.rules_for(conn, cls)
        grid[cls] = {ctx_name: _plugin.matrix.decide(cls, {"context": ctx_name}, rules).tier
                     for (ctx_name, _desc) in _plugin.matrix.CONTEXTS}
    return {"contexts": _plugin.matrix.CONTEXTS, "classes": classes, "grid": grid}


@router.post("/matrix/cell")
async def matrix_cell(body: dict):
    conn = queue.db()
    cls = str(body.get("event_class", ""))
    ctx_name = str(body.get("context", "base"))
    tier = int(body.get("tier", 2))
    if ctx_name == "base":
        queue.add_rule(conn, cls, {}, tier, label=f"{cls} (base)")
    else:
        queue.set_context_rule(conn, cls, ctx_name, tier)
    return {"ok": True}


@router.get("/stats")
async def stats():
    conn = queue.db()
    return {
        "rules": [{"rule_id": r, "total": t, "delivered": d, "held": h}
                  for (r, t, d, h) in queue.rule_stats(conn)],
        "held": queue.held_count(conn),
    }


@router.post("/simulate")
async def simulate(body: dict):
    return _simulate(str(body.get("event_class", "")), str(body.get("context", "base")), body.get("text"))


@router.get("/simulate")
async def simulate_get(event_class: str = "", context: str = "base"):
    """GET so the pane can preview without a POST body (some hosts drop bodies)."""
    return _simulate(event_class, context, None)


def _simulate(event_class: str, context: str, text):
    from datetime import datetime
    conn = queue.db()
    payload = {"context": context or "base"}
    if text:
        payload["text"] = str(text)
    d = _plugin.matrix.decide(event_class, payload, queue.rules_for(conn, event_class))
    held, why = _plugin.scheduler.is_held(
        d.tier, queue.windows(conn), queue.active_mutes(conn, queue.now_iso()),
        queue.kv_get(conn, "paused_until"), datetime.now())
    if d.tier == 0 and not held:
        held, why = True, "tier T0 -> digest by design"
    action = "hold" if held else ("escalate" if d.tier >= 4 else "deliver")
    return {
        "event_class": event_class,
        "context": payload["context"],
        "tier": d.tier,
        "action": action,
        "rule": d.label,
        "reason": why,
        "rings_phone": action == "escalate",
        "note": "Preview only. Nothing was queued and the phone did not ring.",
    }


@router.post("/quiet")
async def add_quiet(body: dict):
    conn = queue.db()
    start = str(body.get("start", "23:00"))
    end = str(body.get("end", "08:00"))
    days = str(body.get("days", "0123456"))
    floor = int(body.get("floor", 4))
    label = str(body.get("label", "silent hours"))
    wid = queue.add_window(conn, start, end, days, floor, label)
    return {"ok": True, "id": wid}


@router.post("/quiet/remove")
async def remove_quiet(body: dict):
    conn = queue.db()
    if body.get("all"):
        queue.clear_windows(conn)
    elif body.get("id") is not None:
        queue.delete_window(conn, body["id"])
    return {"ok": True}


@router.get("/config")
async def get_config():
    conn = queue.db()
    return {k: queue.kv_get(conn, k) or ""
            for k in ("budget_per_hour", "promo_minutes", "active_context", "target")}


@router.post("/config")
async def set_config(body: dict):
    conn = queue.db()
    for k in ("budget_per_hour", "promo_minutes", "active_context", "target"):
        if k not in body:
            continue
        value = body[k]
        if k in ("budget_per_hour", "promo_minutes"):
            raw = str(value).strip()
            if raw and not raw.isdigit():
                return {"ok": False, "error": f"{k} must be a whole number"}
        queue.kv_set(conn, k, value)
    return {"ok": True}
