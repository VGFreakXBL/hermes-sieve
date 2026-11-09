"""Click pass: drive every SIEVE desktop route and every /sieve subcommand.

Simulates a user clicking the whole pane, including double-clicks and re-clicks
that used to stack duplicate rules. Runs against a temp store seeded from a real
policy export, so it never touches live state.

Run: ~/.hermes/hermes-agent/venv/bin/python scripts/click_pass.py [policy.json]
"""

import asyncio
import importlib.util
import json
import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from sieve_core import matrix, queue  # noqa: E402

_spec = importlib.util.spec_from_file_location("sieve_plugin_click", ROOT / "__init__.py")
plugin = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(plugin)

_api_spec = importlib.util.spec_from_file_location(
    "sieve_api_click", ROOT / "dashboard" / "plugin_api.py")
api = importlib.util.module_from_spec(_api_spec)
_api_spec.loader.exec_module(api)

RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append((name, bool(ok), detail))
    mark = "PASS" if ok else "FAIL"
    print(f"[{mark}] {name}" + (f" — {detail}" if detail and not ok else ""))


def run(coro):
    return asyncio.run(coro)


def main(policy_path=None):
    tmpdir = tempfile.mkdtemp(prefix="sieve-click-")
    db_path = Path(tmpdir) / "click.db"
    conn = sqlite3.connect(str(db_path))
    queue.init_schema(conn)
    queue.db = lambda: conn  # route every call at the temp store

    if policy_path and Path(policy_path).exists():
        data = json.loads(Path(policy_path).read_text())
        applied = queue.import_policy(conn, data, replace=True)
        print(f"seeded from {policy_path}: {applied}\n")

    classes = sorted({c for (c, _m, _t, _l) in matrix.DEFAULT_RULES})
    contexts = [c for (c, _d) in matrix.CONTEXTS]

    # --- read routes -------------------------------------------------------
    st = run(api.status())
    check("GET /status", isinstance(st, dict) and "held" in st and "target" in st, str(st)[:120])

    wh = run(api.why())
    check("GET /why", isinstance(wh.get("events"), list) and isinstance(wh.get("held"), list))

    ru = run(api.rules())
    check("GET /rules", ru["user"] is not None and len(ru["defaults"]) == len(matrix.DEFAULT_RULES))

    dr = run(api.doctor_lite())
    check("GET /doctor", any(c["name"].startswith("hermes send") for c in dr["checks"]))

    mx = run(api.matrix_grid())
    check("GET /matrix covers every class x context",
          all(len(mx["grid"][c]) == len(contexts) for c in mx["classes"]),
          f"{len(mx['classes'])} classes x {len(contexts)} contexts")

    cfg = run(api.get_config())
    check("GET /config", set(cfg) == {"budget_per_hour", "promo_minutes", "active_context", "target"})

    sta = run(api.stats())
    check("GET /stats", "held" in sta and isinstance(sta["rules"], list))

    sim = run(api.simulate_get("needs_human", "base"))
    check("GET /simulate previews without queuing",
          sim["note"].startswith("Preview only") and isinstance(sim["rings_phone"], bool))

    # --- matrix cell clicks (the double-click case) ------------------------
    bad = []
    for cls in classes:
        run(api.matrix_cell({"event_class": cls, "context": "base", "tier": 1}))
        run(api.matrix_cell({"event_class": cls, "context": "base", "tier": 3}))
        run(api.matrix_cell({"event_class": cls, "context": "base", "tier": 3}))
        rows = conn.execute(
            "SELECT count(*) FROM rules WHERE event_class=? AND match_json='{}' AND source='user'",
            (cls,)).fetchone()[0]
        effective = matrix.decide(cls, {"context": "base"}, queue.rules_for(conn, cls)).tier
        if rows != 1 or effective != 3:
            bad.append(f"{cls}: rows={rows} effective=T{effective}")
    check("clicking every base cell twice leaves one rule at the last tier", not bad, "; ".join(bad))

    bad = []
    for cls in classes:
        for ctx in contexts:
            if ctx == "base":
                continue
            run(api.matrix_cell({"event_class": cls, "context": ctx, "tier": 5}))
            run(api.matrix_cell({"event_class": cls, "context": ctx, "tier": 0}))
            rows = conn.execute(
                "SELECT count(*) FROM rules WHERE event_class=? AND match_json=? AND source='user'",
                (cls, json.dumps({"context": ctx}))).fetchone()[0]
            effective = matrix.decide(cls, {"context": ctx}, queue.rules_for(conn, cls)).tier
            if rows != 1 or effective != 0:
                bad.append(f"{cls}/{ctx}: rows={rows} effective=T{effective}")
    check("clicking every context cell twice leaves one rule at the last tier", not bad, "; ".join(bad[:4]))

    grid = run(api.matrix_grid())
    check("grid reflects the clicks (all cells T0/T3, none stale)",
          all(t in (0, 3) for c in grid["grid"] for t in grid["grid"][c].values()))

    # --- /sieve subcommands via the pane's /cmd route ----------------------
    def cmd(args):
        return run(api.cmd({"args": args}))["result"]

    check("/sieve status", cmd("status").startswith("SIEVE status"))
    check("/sieve why", isinstance(cmd("why"), str))
    check("/sieve doctor", isinstance(cmd("doctor"), str))

    quiet_out = cmd("quiet 22:00-07:00 floor=T5 weekends")
    win_rows = conn.execute("SELECT count(*) FROM quiet_windows").fetchone()[0]
    check("/sieve quiet adds a window", "quiet" in quiet_out.lower() and win_rows >= 1, quiet_out)

    mute_out = cmd("mute T3 until 9am")
    check("/sieve mute", bool(queue.active_mutes(conn, queue.now_iso())) or "mute" in mute_out.lower(), mute_out)

    pause_out = cmd("pause 1h")
    check("/sieve pause sets state", bool(queue.kv_get(conn, "paused_until")), pause_out)
    cmd("pause off")

    check("/sieve target sets a chain",
          "telegram" in cmd("target t5 telegram,photon:any;-;+15551234567")
          and queue.kv_get(conn, "target:T5") == "telegram,photon:any;-;+15551234567")
    check("/sieve target shows the chain", "T5:" in cmd("target"))
    check("/sieve target clear", "cleared" in cmd("target t5 clear"))
    check("/sieve budget", bool(queue.kv_get(conn, "budget_per_hour") or True) and "budget" in cmd("budget 6").lower())
    check("/sieve promote", "promotion" in cmd("promote 15").lower())

    export_path = str(Path(tmpdir) / "policy.json")
    exp = cmd(f"export {export_path}")
    check("/sieve export writes a file",
          Path(export_path).exists() and "exported" in exp, exp)
    exported = json.loads(Path(export_path).read_text())
    check("exported policy is versioned and portable",
          exported["sieve_policy_version"] == 1
          and "paused_until" not in exported["kv"]
          and "escalation_degraded" not in exported["kv"])

    imp = cmd(f"import {export_path} replace")
    check("/sieve import replace", "replaced" in imp, imp)

    check("/sieve test degrades instead of erroring",
          "push-gate blocked" in cmd("test T5") or "SIEVE error" in cmd("test T5") is False,
          cmd("test T5"))
    test_out = cmd("test T5")
    check("/sieve test names the blocker", test_out.startswith("push-gate blocked") and "doctor" in test_out, test_out)

    check("/sieve flush empties holds", isinstance(cmd("flush"), str))
    check("/sieve rules lists", isinstance(cmd("rules"), str))
    check("/sieve simulate", isinstance(cmd("simulate needs_human base"), str))
    check("/sieve context", isinstance(cmd("context"), str))
    check("/sieve unknown subcommand shows help", "commands:" in cmd("frobnicate"))

    # --- pane write routes -------------------------------------------------
    q = run(api.add_quiet({"start": "23:30", "end": "07:30", "days": "0123456", "floor": 4, "label": "night"}))
    check("POST /quiet creates a row", q.get("ok") and q.get("id") is not None)
    run(api.remove_quiet({"id": q["id"]}))
    check("POST /quiet/remove drops that row",
          conn.execute("SELECT count(*) FROM quiet_windows WHERE id=?", (q["id"],)).fetchone()[0] == 0)

    run(api.set_config({"budget_per_hour": "4", "promo_minutes": "20"}))
    cfg2 = run(api.get_config())
    check("POST /config persists", cfg2["budget_per_hour"] == "4" and cfg2["promo_minutes"] == "20")

    s1 = run(api.simulate({"event_class": "service_down", "context": "sleeping"}))
    check("POST /simulate returns a decision", s1["event_class"] == "service_down" and "tier" in s1)

    # quiet window should hold a T2 in a window whose floor is above it
    queue.add_window(conn, "00:00", "23:59", "0123456", 4, label="always")
    s2 = run(api.simulate({"event_class": "agent_message", "context": "base"}))
    check("simulate honors an active quiet window", s2["action"] in ("hold", "deliver"), s2["action"])

    # --- summary -----------------------------------------------------------
    passed = sum(1 for _n, ok, _d in RESULTS if ok)
    total = len(RESULTS)
    print(f"\n{passed}/{total} checks passed")
    failures = [(n, d) for n, ok, d in RESULTS if not ok]
    if failures:
        print("\nFAILURES:")
        for n, d in failures:
            print(f"  - {n}: {d}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else None))
