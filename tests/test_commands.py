"""Command parsing tests: _cmd against an in-memory store (delivery monkeypatched)."""

import importlib.util
import sqlite3
from pathlib import Path

from sieve_core import queue

_ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("sieve_plugin", _ROOT / "__init__.py")
plugin = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(plugin)


def _fresh(monkeypatch, tmp_path):
    conn = sqlite3.connect(str(tmp_path / "t.db"))
    queue.init_schema(conn)
    monkeypatch.setattr(queue, "db", lambda: conn)
    monkeypatch.setattr(plugin.delivery, "send", lambda *a, **k: True)
    return conn


def test_setup_walk_and_apply(monkeypatch, tmp_path):
    conn = _fresh(monkeypatch, tmp_path)
    first = plugin._cmd("setup")
    assert "no matter the hour" in first
    plugin._cmd("setup needs_human,service_down")
    plugin._cmd("setup 23:00-08:00")
    plugin._cmd("setup T5")
    plugin._cmd("setup discord:#ops")
    done = plugin._cmd("setup T2")
    assert "Setup is complete" in done
    assert queue.kv_get(conn, "target") == "discord:#ops"
    wins = queue.windows(conn)
    assert wins and wins[0][4] == 5
    decision = plugin.matrix.decide("service_down", {"context": "base"}, queue.rules_for(conn, "service_down"))
    assert decision.tier == 5
    decision = plugin.matrix.decide("task_done", {"context": "base"}, queue.rules_for(conn, "task_done"))
    assert decision.tier == 2


def test_setup_apply_is_one_shot(monkeypatch, tmp_path):
    conn = _fresh(monkeypatch, tmp_path)
    out = plugin._cmd(
        "setup apply always=needs_human sleep=22:00-07:00 floor=none digest=slack:C012 daytime=T0")
    assert "applied" in out
    assert queue.kv_get(conn, "target") == "slack:C012"
    assert queue.windows(conn)[0][4] == 6
    cell = plugin._cmd("matrix set world_message T3 context=meeting")
    assert "meeting" in cell
    grid = plugin._cmd("matrix")
    assert "needs_human: T5" in grid


def test_status_and_why(monkeypatch, tmp_path):
    _fresh(monkeypatch, tmp_path)
    assert "SIEVE status" in plugin._cmd("status")
    assert "last decisions" in plugin._cmd("why")


def test_mute_command(monkeypatch, tmp_path):
    conn = _fresh(monkeypatch, tmp_path)
    out = plugin._cmd("mute T4 until 9am")
    assert "muted T0-T4" in out
    assert queue.active_mutes(conn, queue.now_iso())


def test_quiet_command(monkeypatch, tmp_path):
    conn = _fresh(monkeypatch, tmp_path)
    out = plugin._cmd("quiet 23:00-08:00 floor=T5 weekdays")
    assert "floor=T5" in out
    wins = queue.windows(conn)
    assert wins[0][2] == "08:00" and wins[0][4] == 5 and wins[0][3] == "01234"


def test_pause_and_clear(monkeypatch, tmp_path):
    conn = _fresh(monkeypatch, tmp_path)
    assert "paused" in plugin._cmd("pause 2h")
    assert queue.kv_get(conn, "paused_until")
    assert "cleared" in plugin._cmd("pause off")


def test_rules_add_and_list(monkeypatch, tmp_path):
    _fresh(monkeypatch, tmp_path)
    out = plugin._cmd("rules add world_message 5 sender=boss")
    assert "rule #" in out
    assert "world_message T5 user" in plugin._cmd("rules")


def test_target_set(monkeypatch, tmp_path):
    conn = _fresh(monkeypatch, tmp_path)
    assert "discord" in plugin._cmd("target discord:#digests")
    assert queue.kv_get(conn, "target") == "discord:#digests"


def test_flush_releases_and_digests(monkeypatch, tmp_path):
    conn = _fresh(monkeypatch, tmp_path)
    eid = queue.log_event(conn, "cron_done", {}, 0, "held")
    queue.add_hold(conn, eid)
    sent = []
    monkeypatch.setattr(plugin.delivery, "send", lambda text, target=None, **k: sent.append(text) or True)
    out = plugin._cmd("flush")
    assert "flushed 1" in out
    assert sent and "SIEVE digest" in sent[0]
