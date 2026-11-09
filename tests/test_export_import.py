"""Policy export/import tests.

Policy travels as a file the user moves. It must never jump profiles by itself:
a plugin running in one profile writing into another profile's store breaks the
boundary the tool exists to protect. These tests pin both halves of that rule:
what goes into the file, and what is refused.
"""

import json
import sqlite3

import pytest

from sieve_core import queue


def _db():
    conn = sqlite3.connect(":memory:")
    queue.init_schema(conn)
    return conn


def test_export_carries_only_user_policy():
    conn = _db()
    queue.add_rule(conn, "needs_human", {}, 4, label="gate", source="user")
    queue.add_rule(conn, "task_done", {}, 2, label="seeded", source="default")
    queue.add_window(conn, "23:00", "08:00", "0123456", 4, label="night")
    queue.add_mute(conn, 3, "2999-01-01T09:00:00")
    queue.kv_set(conn, "budget_per_hour", "6")
    queue.kv_set(conn, "target", "telegram,photon")

    data = queue.export_policy(conn)
    assert data["sieve_policy_version"] == 1
    assert [r["event_class"] for r in data["rules"]] == ["needs_human"]
    assert data["quiet_windows"][0]["label"] == "night"
    assert data["kv"]["budget_per_hour"] == "6"
    assert data["kv"]["target"] == "telegram,photon"
    assert len(data["mutes"]) == 1


def test_export_excludes_operational_state():
    conn = _db()
    queue.kv_set(conn, "paused_until", "2999-01-01")
    queue.kv_set(conn, "escalation_degraded", "device stale")
    data = queue.export_policy(conn)
    assert "paused_until" not in data["kv"]
    assert "escalation_degraded" not in data["kv"]


def test_export_skips_expired_mutes():
    conn = _db()
    queue.add_mute(conn, 4, "2000-01-01T00:00:00")
    assert queue.export_policy(conn)["mutes"] == []


def test_roundtrip_reproduces_policy_in_a_fresh_store():
    src = _db()
    queue.add_rule(src, "service_down", {}, 5, label="outage", source="user")
    queue.set_context_rule(src, "agent_message", "meeting", 0)
    queue.add_window(src, "22:30", "07:00", "12345", 5, label="weeknight")
    queue.kv_set(src, "target:T5", "telegram,photon")

    data = json.loads(json.dumps(queue.export_policy(src)))
    dst = _db()
    applied = queue.import_policy(dst, data)

    assert applied["rules"] == 2
    assert applied["quiet_windows"] == 1
    assert applied["kv"] == 1
    tiers = {r[0]: r[1] for r in dst.execute(
        "SELECT event_class, tier FROM rules WHERE match_json = '{}'")}
    assert tiers["service_down"] == 5
    assert queue.kv_get(dst, "target:T5") == "telegram,photon"
    assert queue.windows(dst)[0][1] == "22:30"


def test_import_merge_adds_without_clearing():
    dst = _db()
    queue.add_rule(dst, "task_done", {}, 2, label="existing", source="user")
    queue.import_policy(dst, {"sieve_policy_version": 1, "rules": [
        {"event_class": "needs_human", "match": {}, "tier": 4, "label": "new"}]})
    classes = [r[0] for r in dst.execute("SELECT event_class FROM rules ORDER BY id")]
    assert classes == ["task_done", "needs_human"]


def test_import_replace_clears_user_policy_first():
    dst = _db()
    queue.add_rule(dst, "task_done", {}, 2, label="existing", source="user")
    queue.add_rule(dst, "cron_done", {}, 0, label="seeded", source="default")
    queue.add_window(dst, "01:00", "02:00", "0123456", 3)
    queue.import_policy(dst, {"sieve_policy_version": 1, "rules": [
        {"event_class": "needs_human", "match": {}, "tier": 4, "label": "new"}]}, replace=True)
    user = [r[0] for r in dst.execute("SELECT event_class FROM rules WHERE source='user'")]
    seeded = [r[0] for r in dst.execute("SELECT event_class FROM rules WHERE source='default'")]
    assert user == ["needs_human"]
    assert seeded == ["cron_done"], "replace must not touch seeded defaults"
    assert queue.windows(dst) == []


def test_import_refuses_unknown_version():
    with pytest.raises(ValueError, match="unsupported"):
        queue.import_policy(_db(), {"sieve_policy_version": 99})


def test_import_refuses_bad_tier():
    with pytest.raises(ValueError, match="invalid rule"):
        queue.import_policy(_db(), {"sieve_policy_version": 1, "rules": [
            {"event_class": "x", "match": {}, "tier": 9}]})


def test_import_refuses_non_portable_kv_key():
    with pytest.raises(ValueError, match="not a portable policy key"):
        queue.import_policy(_db(), {"sieve_policy_version": 1, "kv": {"paused_until": "x"}})


def test_bad_file_leaves_store_untouched():
    dst = _db()
    queue.add_rule(dst, "task_done", {}, 2, label="keep", source="user")
    with pytest.raises(ValueError):
        queue.import_policy(dst, {"sieve_policy_version": 1, "rules": [
            {"event_class": "ok", "match": {}, "tier": 2},
            {"event_class": "bad", "match": {}, "tier": 42}]})
    assert [r[0] for r in dst.execute("SELECT event_class FROM rules")] == ["task_done"]

def test_readding_same_rule_updates_instead_of_stacking():
    """A rule is a setting. Duplicates would make the oldest copy win silently."""
    conn = _db()
    first = queue.add_rule(conn, "cron_done", {}, 1, label="old", source="user")
    second = queue.add_rule(conn, "cron_done", {}, 2, label="new", source="user")
    rows = conn.execute("SELECT id, tier FROM rules WHERE event_class='cron_done'").fetchall()
    assert len(rows) == 1, "same class+match must not stack"
    assert rows[0][1] == 2, "the newer tier wins"
    assert first == second, "the id is stable so event history keeps pointing at the same rule"


def test_distinct_matches_still_coexist():
    conn = _db()
    queue.add_rule(conn, "agent_message", {}, 2, source="user")
    queue.add_rule(conn, "agent_message", {"keyword": "urgent"}, 4, source="user")
    assert len(conn.execute("SELECT id FROM rules WHERE event_class='agent_message'").fetchall()) == 2


def test_export_after_readd_carries_one_rule():
    conn = _db()
    queue.add_rule(conn, "cron_done", {}, 1, source="user")
    queue.add_rule(conn, "cron_done", {}, 2, source="user")
    data = queue.export_policy(conn)
    assert [(r["event_class"], r["tier"]) for r in data["rules"]] == [("cron_done", 2)]
