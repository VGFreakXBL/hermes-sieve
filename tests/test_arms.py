"""Two-axis tests: requested tier vs achieved arm.

The tier is the intent the policy chose. The arm is the primitive that actually
carried it out. A T5 that lands as a messaging push must say both things in the
same message: it was a max-priority alert, and the arm that delivered it was not
the siren. Collapsing those two facts is the failure mode this file guards.
"""

import importlib.util
import sqlite3
from pathlib import Path

from sieve_core import delivery, matrix, queue

_ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("sieve_plugin_arms", _ROOT / "__init__.py")
plugin = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(plugin)


def _fresh(monkeypatch, tmp_path):
    conn = sqlite3.connect(str(tmp_path / "arms.db"))
    queue.init_schema(conn)
    monkeypatch.setattr(queue, "db", lambda: conn)
    return conn


def test_degraded_top_tier_keeps_tier_and_names_the_arm(monkeypatch, tmp_path):
    conn = _fresh(monkeypatch, tmp_path)
    sent = []
    monkeypatch.setattr(delivery, "send", lambda text, target=None, timeout=30: sent.append(text) or True)
    monkeypatch.setattr(plugin.escalate, "push_ready", lambda _d: (False, "device stale"))

    decision = matrix.Decision(tier=5, rule_id="t", label="service down", source="user")
    out = plugin._deliver(conn, 1, "service_down", {}, decision)

    assert out["tier"] == 5, "intent is preserved, not demoted to T3"
    assert out["arm"] == "push"
    assert out["degraded"] == "device stale"
    body = sent[0]
    assert "T5" in body, "the requested tier must still be visible"
    assert "top rung: alarm" in body
    assert "max interrupt available" in body
    assert "escalation degraded: device stale" in body


def test_achieved_alarm_reports_verified(monkeypatch, tmp_path):
    conn = _fresh(monkeypatch, tmp_path)
    sent = []
    monkeypatch.setattr(delivery, "send", lambda text, target=None, timeout=30: sent.append(text) or True)
    monkeypatch.setattr(plugin.escalate, "push_ready", lambda _d: (True, "device fresh"))

    def fake_alarm(_d, label, at_iso=None, time_hhmm=None, **kw):
        return {"ok": True, "verified": True}

    monkeypatch.setattr(plugin.escalate, "fire_alarm", fake_alarm)
    decision = matrix.Decision(tier=5, rule_id="t", label="service down", source="user")
    out = plugin._deliver(conn, 1, "service_down", {}, decision)

    assert out["arm"] == "alarm"
    assert out["verified"] is True
    assert "phone alarm" in sent[0]
    assert "[verified]" in sent[0]
    assert "max interrupt available" not in sent[0]


def test_alarm_write_that_cannot_be_read_back_is_flagged(monkeypatch, tmp_path):
    conn = _fresh(monkeypatch, tmp_path)
    sent = []
    monkeypatch.setattr(delivery, "send", lambda text, target=None, timeout=30: sent.append(text) or True)
    monkeypatch.setattr(plugin.escalate, "push_ready", lambda _d: (True, "device fresh"))
    monkeypatch.setattr(plugin.escalate, "fire_alarm",
                        lambda _d, label, at_iso=None, time_hhmm=None, **kw: {"ok": True, "verified": False})

    decision = matrix.Decision(tier=5, rule_id="t", label="gate", source="user")
    out = plugin._deliver(conn, 1, "needs_human", {}, decision)

    assert out["arm"] == "alarm"
    assert out["verified"] is False
    assert out["degraded"].startswith("alarm write unverified")
    assert "[write unverified]" in sent[0]
    assert "max interrupt available" not in sent[0]


def test_arm_label_does_not_upgrade_low_tiers(monkeypatch):
    assert delivery.arm_label(3, "push") == "messaging push"
    assert "max interrupt" in delivery.arm_label(4, "push")
    assert delivery.arm_label(5, "alarm").startswith("phone alarm")


def test_plain_notify_carries_an_arm_too(monkeypatch, tmp_path):
    conn = _fresh(monkeypatch, tmp_path)
    sent = []
    monkeypatch.setattr(delivery, "send", lambda text, target=None, timeout=30: sent.append(text) or True)
    decision = matrix.Decision(tier=2, rule_id="t", label="task done", source="default")
    out = plugin._deliver(conn, 1, "task_done", {}, decision)
    assert out["arm"] == "push"
    assert "arm: messaging push" in sent[0]
