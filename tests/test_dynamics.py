"""Dynamics tests: context matrix, attention budget, promotion ladder, simulator."""

import importlib.util
import sqlite3
from pathlib import Path

from sieve_core import matrix, queue

_ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("sieve_plugin_dyn", _ROOT / "__init__.py")
plugin = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(plugin)


def _fresh(monkeypatch, tmp_path):
    conn = sqlite3.connect(str(tmp_path / "dyn.db"))
    queue.init_schema(conn)
    monkeypatch.setattr(queue, "db", lambda: conn)
    monkeypatch.setattr(plugin.delivery, "send", lambda *a, **k: True)
    return conn


def test_context_rule_overrides_base():
    conn = sqlite3.connect(":memory:")
    queue.init_schema(conn)
    queue.set_context_rule(conn, "world_message", "meeting", 0)
    rules = queue.rules_for(conn, "world_message")
    assert matrix.decide("world_message", {"context": "meeting"}, rules).tier == 0
    assert matrix.decide("world_message", {"context": "base"}, rules).tier == 2


def test_context_rules_roundtrip():
    conn = sqlite3.connect(":memory:")
    queue.init_schema(conn)
    queue.set_context_rule(conn, "world_message", "streaming", 0)
    queue.set_context_rule(conn, "world_message", "streaming", 1)  # replaces
    rows = queue.context_rules(conn)
    assert rows == [("world_message", "streaming", 1, True)]


def test_budget_holds_overflow(monkeypatch, tmp_path):
    conn = _fresh(monkeypatch, tmp_path)
    queue.kv_set(conn, "budget_per_hour", "1")
    # one delivery within the hour already
    queue.log_event(conn, "world_message", {}, 2, "delivered")
    result = plugin._route("world_message", {"context": "base"})
    assert result.get("held") is True
    assert "attention budget" in result.get("reason", "")


def test_budget_off_delivers(monkeypatch, tmp_path):
    conn = _fresh(monkeypatch, tmp_path)
    result = plugin._route("world_message", {"context": "base"})
    assert result.get("delivered") is True


def test_promotion_ladder_escalates_stale_hold(monkeypatch, tmp_path):
    conn = _fresh(monkeypatch, tmp_path)
    queue.kv_set(conn, "promo_minutes", "1")
    # craft a hold that is already 10 minutes old
    import json as _json
    from datetime import datetime, timedelta
    old = (datetime.now() - timedelta(minutes=10)).isoformat(timespec="seconds")
    cur = conn.execute(
        "INSERT INTO events(ts, event_class, payload_json, rule_id, tier, action, detail)"
        " VALUES(?,?,?,?,?,?,?)", (old, "world_message", "{}", None, 2, "held", "quiet"))
    eid = cur.lastrowid
    conn.execute("INSERT INTO holds(event_id, hold_until) VALUES(?, NULL)", (eid,))
    conn.commit()
    sent = []
    monkeypatch.setattr(plugin.delivery, "send", lambda text, target=None, **k: sent.append(text) or True)
    n = plugin._promote_sweep(conn)
    assert n == 1
    assert sent and "promotion ladder" in sent[0]
    # promoted to T3 (2+1), hold released
    assert queue.held_count(conn) == 0


def test_simulate_command(monkeypatch, tmp_path):
    _fresh(monkeypatch, tmp_path)
    out = plugin._cmd("simulate needs_human meeting")
    assert "T4" in out and ("deliver now" in out or "ESCALATE" in out)
    out2 = plugin._cmd("simulate cron_done base")
    assert "HOLD to digest" in out2


def test_context_and_budget_and_promote_commands(monkeypatch, tmp_path):
    conn = _fresh(monkeypatch, tmp_path)
    assert "context set" in plugin._cmd("context streaming")
    assert queue.kv_get(conn, "active_context") == "streaming"
    assert "budget set" in plugin._cmd("budget 3")
    assert queue.kv_get(conn, "budget_per_hour") == "3"
    assert "ladder set" in plugin._cmd("promote 30")
    assert queue.kv_get(conn, "promo_minutes") == "30"


def test_context_override_applies_in_route(monkeypatch, tmp_path):
    conn = _fresh(monkeypatch, tmp_path)
    queue.set_context_rule(conn, "world_message", "meeting", 0)
    queue.kv_set(conn, "active_context", "meeting")
    result = plugin._route("world_message", {})
    assert result.get("held") is True  # T0 by context rule -> digest
