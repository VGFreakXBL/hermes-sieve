"""Setup-assistant behaviors: reason-aware push gate, clear-on-verify, the
chain walk step, and idempotency-retry guidance. These are the walls a new
user hit on 2026-11-11 (docs/SETUP-ASSISTANT.md Part 1); each test pins the
fix so the wall cannot come back."""

import importlib.util
import sqlite3
from pathlib import Path

from sieve_core import queue

_ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("sieve_plugin_sa", _ROOT / "__init__.py")
plugin = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(plugin)


def _fresh(monkeypatch, tmp_path):
    conn = sqlite3.connect(str(tmp_path / "sa.db"))
    queue.init_schema(conn)
    monkeypatch.setattr(queue, "db", lambda: conn)
    monkeypatch.setattr(plugin.delivery, "send", lambda *a, **k: True)
    return conn


def test_push_ready_propagates_specific_reason(monkeypatch):
    monkeypatch.setattr(plugin.escalate, "device_status",
                        lambda d: {"available": False,
                                   "reason": "no tool context on this surface"})
    ready, why = plugin.escalate.push_ready(lambda *a, **k: None)
    assert not ready
    assert "no tool context" in why  # not a canned toolset claim


def test_push_fix_never_points_at_push_registration():
    fix = plugin.escalate.push_fix
    assert "Discord or Telegram" in fix("no tool context on this surface (ctx unavailable)")
    assert "toolsets" in fix("mobile_device toolset not loaded")
    assert "sign in" in fix("no device paired to this profile (open the Hermes app)")
    assert "queue" in fix("device stale (no recent sync)")
    out = fix("Push is not configured; Hermes Agent will fetch this request when opened.")
    assert "no enable path" in out
    assert "configure push" not in out.lower()


def test_test_t5_verified_clears_degrade_and_stamps_ritual(monkeypatch, tmp_path):
    conn = _fresh(monkeypatch, tmp_path)
    queue.kv_set(conn, "escalation_degraded", "device stale (old)")
    monkeypatch.setattr(plugin.escalate, "push_ready", lambda d: (True, "device fresh"))
    monkeypatch.setattr(plugin.escalate, "fire_alarm",
                        lambda *a, **k: {"ok": True, "verified": True})
    out = plugin._cmd("test T5")
    assert "ok" in out
    assert not queue.kv_get(conn, "escalation_degraded")
    assert queue.kv_get(conn, "ritual_done_at")


def test_test_t5_unverified_keeps_degrade(monkeypatch, tmp_path):
    conn = _fresh(monkeypatch, tmp_path)
    queue.kv_set(conn, "escalation_degraded", "device stale (old)")
    monkeypatch.setattr(plugin.escalate, "push_ready", lambda d: (True, "device fresh"))
    monkeypatch.setattr(plugin.escalate, "fire_alarm",
                        lambda *a, **k: {"ok": True, "verified": False})
    plugin._cmd("test T5")
    assert queue.kv_get(conn, "escalation_degraded")  # a weaker/unverified arm never clears it


def test_test_blocked_names_cause_and_reachable_fix(monkeypatch, tmp_path):
    conn = _fresh(monkeypatch, tmp_path)
    monkeypatch.setattr(plugin.escalate, "push_ready",
                        lambda d: (False, "device stale (no recent sync)"))
    out = plugin._cmd("test T5")
    assert out.startswith("push-gate blocked")
    assert "device stale" in out
    assert "open the Hermes app" in out
    assert "configure push" not in out.lower()


def test_setup_chain_step_sets_both_tiers_idempotent(monkeypatch, tmp_path):
    conn = _fresh(monkeypatch, tmp_path)
    plugin._cmd("setup needs_human")
    plugin._cmd("setup 23:00-08:00")
    plugin._cmd("setup T5")
    plugin._cmd("setup yes")
    out = plugin._cmd("setup telegram")  # digest
    assert "[chain]" in out  # the walk reaches the chain question
    out = plugin._cmd("setup telegram,photon:any;-;+15555550100")
    assert "[daytime]" in out
    # one-shot chain is idempotent: re-applying changes no row count
    plugin._cmd("setup apply chain=telegram,photon:any;-;+15555550100")
    plugin._cmd("setup apply chain=telegram,photon:any;-;+15555550100")
    assert queue.kv_get(conn, "target:T4") == "telegram,photon:any;-;+15555550100"
    assert queue.kv_get(conn, "target:T5") == "telegram,photon:any;-;+15555550100"


def test_fire_alarm_idempotency_retry_guidance():
    def dispatch(tool, args):
        return {"error": "Idempotency key already belongs to a different action"}

    res = plugin.escalate.fire_alarm(dispatch, "x",
                                     at_iso="2026-11-11T12:00:00",
                                     idempotency_key="sa-retry-1")
    assert not res["ok"]
    assert "fresh idempotency key" in res["reason"]


def test_fire_alarm_normalizes_naive_timestamp():
    captured = {}

    def dispatch(tool, args):
        if tool == "mobile_alarm_create":
            captured.update(args)
            return {"ok": True}
        return {"alarms": [{"label": "SIEVE: x"}]}

    plugin.escalate.fire_alarm(dispatch, "x", at_iso="2026-11-11T12:00:00",
                               idempotency_key="sa-tz-1")
    assert captured["at"] != "2026-11-11T12:00:00"
    assert "+" in captured["at"][10:] or "-" in captured["at"][10:]
