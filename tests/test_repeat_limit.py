"""Repeat-limit tests (olympus pattern: same class re-alerts at most once per window)."""

import importlib.util
import sqlite3
from pathlib import Path

from sieve_core import queue

_ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("sieve_plugin_rep", _ROOT / "__init__.py")
plugin = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(plugin)


def _fresh(monkeypatch, tmp_path):
    conn = sqlite3.connect(str(tmp_path / "rep.db"))
    queue.init_schema(conn)
    monkeypatch.setattr(queue, "db", lambda: conn)
    monkeypatch.setattr(plugin.delivery, "send", lambda *a, **k: True)
    return conn


def test_repeat_limit_holds_second_t2(monkeypatch, tmp_path):
    conn = _fresh(monkeypatch, tmp_path)
    queue.log_event(conn, "world_message", {}, 2, "delivered")  # one already alerted
    result = plugin._route("world_message", {"context": "base"})
    assert result.get("held") is True
    assert "repeat limit" in result.get("reason", "")


def test_repeat_limit_different_class_delivers(monkeypatch, tmp_path):
    conn = _fresh(monkeypatch, tmp_path)
    queue.log_event(conn, "world_message", {}, 2, "delivered")
    result = plugin._route("agent_message", {"context": "base"})
    assert result.get("delivered") is True


def test_repeat_limit_never_holds_gates(monkeypatch, tmp_path):
    conn = _fresh(monkeypatch, tmp_path)
    queue.log_event(conn, "needs_human", {}, 4, "delivered")  # gate already alerted
    result = plugin._route("needs_human", {"context": "base"})
    # T4 gate: repeat limit does not apply (gates block work and must arrive)
    assert result.get("held") is not True


def test_repeat_limit_off_via_config(monkeypatch, tmp_path):
    conn = _fresh(monkeypatch, tmp_path)
    queue.kv_set(conn, "repeat_limit_hours", "0")
    queue.log_event(conn, "world_message", {}, 2, "delivered")
    result = plugin._route("world_message", {"context": "base"})
    assert result.get("delivered") is True
