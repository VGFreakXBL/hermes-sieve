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

from sieve_core import queue  # noqa: E402

_spec = importlib.util.spec_from_file_location("sieve_plugin", _ROOT / "__init__.py")
_plugin = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_plugin)

router = APIRouter()


@router.get("/status")
async def status():
    conn = queue.db()
    wins = [{"id": w[0], "start": w[1], "end": w[2], "days": w[3], "floor": w[4], "label": w[5]}
            for w in queue.windows(conn)]
    mutes = [{"id": m[0], "tier": m[1], "until": m[2]}
             for m in queue.active_mutes(conn, queue.now_iso())]
    return {
        "held": queue.held_count(conn),
        "paused_until": queue.kv_get(conn, "paused_until") or "",
        "degraded": queue.kv_get(conn, "escalation_degraded") or "",
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
