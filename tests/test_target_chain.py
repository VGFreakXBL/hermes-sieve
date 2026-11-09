"""Target chain tests: per-tier routing that keeps alerts arriving when the
closed-app alarm arm is unavailable.

The chain is redundant delivery, not failover. A T5 that lands on two pipes is
worth the duplicate, and a per-tier chain must never silently swallow an alert
because one target failed.
"""

import importlib.util
import sqlite3
from pathlib import Path

from sieve_core import delivery, matrix, queue

_ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("sieve_plugin_chain", _ROOT / "__init__.py")
plugin = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(plugin)


def _fresh(monkeypatch, tmp_path):
    conn = sqlite3.connect(str(tmp_path / "chain.db"))
    queue.init_schema(conn)
    monkeypatch.setattr(queue, "db", lambda: conn)
    return conn


def test_parse_chain_trims_and_drops_blanks():
    assert delivery.parse_chain("telegram, photon:+15551234567 ,") == [
        "telegram", "photon:+15551234567"]
    assert delivery.parse_chain("") == []
    assert delivery.parse_chain(None) == []


def test_send_chain_succeeds_when_one_pipe_works(monkeypatch):
    calls = []

    def fake_send(text, target=None, timeout=30):
        calls.append(target)
        return target == "photon"

    monkeypatch.setattr(delivery, "send", fake_send)
    result = delivery.send_chain("x", ["telegram", "photon"])
    assert result["delivered"] is True
    assert result["ok"] == ["photon"]
    assert result["failed"] == ["telegram"]
    assert calls == ["telegram", "photon"]


def test_send_chain_all_failed_is_not_success(monkeypatch):
    monkeypatch.setattr(delivery, "send", lambda *a, **k: False)
    result = delivery.send_chain("x", ["telegram", "photon"])
    assert result["delivered"] is False
    assert result["failed"] == ["telegram", "photon"]


def test_send_chain_empty_chain_is_false(monkeypatch):
    monkeypatch.setattr(delivery, "send", lambda *a, **k: True)
    assert delivery.send_chain("x", [])["delivered"] is False


def test_per_tier_chain_overrides_global(monkeypatch, tmp_path):
    conn = _fresh(monkeypatch, tmp_path)
    queue.kv_set(conn, "target", "telegram")
    queue.kv_set(conn, "target:T5", "telegram,photon")
    assert plugin._targets_for(conn, 5) == ["telegram", "photon"]
    assert plugin._targets_for(conn, 2) == ["telegram"]


def test_tier_chain_falls_back_to_default(monkeypatch, tmp_path):
    conn = _fresh(monkeypatch, tmp_path)
    assert plugin._targets_for(conn, 4) == [plugin.DEFAULT_TARGET]


def test_cleared_tier_chain_inherits_global(monkeypatch, tmp_path):
    conn = _fresh(monkeypatch, tmp_path)
    queue.kv_set(conn, "target", "photon")
    queue.kv_set(conn, "target:T5", "telegram")
    assert plugin._targets_for(conn, 5) == ["telegram"]
    queue.kv_set(conn, "target:T5", "")
    assert plugin._targets_for(conn, 5) == ["photon"]


def test_global_target_can_be_multi(monkeypatch, tmp_path):
    conn = _fresh(monkeypatch, tmp_path)
    queue.kv_set(conn, "target", "telegram,photon")
    assert plugin._targets_for(conn, 3) == ["telegram", "photon"]


def test_degraded_escalation_still_reaches_the_chain(monkeypatch, tmp_path):
    """No alarm arm available: every target in the chain still gets the message."""
    conn = _fresh(monkeypatch, tmp_path)
    seen = []
    monkeypatch.setattr(delivery, "send", lambda text, target=None, timeout=30: seen.append(target) or True)

    def not_ready(_dispatch):
        return False, "device stale (no recent sync; open the Hermes app)"

    monkeypatch.setattr(plugin.escalate, "push_ready", not_ready)
    queue.kv_set(conn, "target:T4", "telegram,photon")
    decision = matrix.Decision(tier=4, rule_id="t", label="gate", source="user")
    out = plugin._deliver(conn, 1, "needs_human", {}, decision)
    assert out["delivered"] is True
    assert out["degraded"].startswith("device stale")
    assert seen == ["telegram", "photon"]
    assert queue.kv_get(conn, "escalation_degraded").startswith("device stale")
