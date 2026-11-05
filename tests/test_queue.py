"""Queue + delivery-format tests (in-memory sqlite; no host deps)."""

import sqlite3

from sieve_core import delivery, queue


def _conn():
    conn = sqlite3.connect(":memory:")
    queue.init_schema(conn)
    return conn


def test_schema_idempotent():
    conn = _conn()
    queue.init_schema(conn)  # twice is fine


def test_event_roundtrip_and_hold_release():
    conn = _conn()
    eid = queue.log_event(conn, "needs_human", {"kind": "clarify"}, 4, "delivered", 1, "test")
    queue.add_hold(conn, eid)
    assert queue.held_count(conn) == 1
    held = queue.held_events(conn)
    assert held[0][0] == eid
    assert queue.release_all(conn) == 1
    assert queue.held_count(conn) == 0


def test_kv_roundtrip():
    conn = _conn()
    queue.kv_set(conn, "target", "discord:#digests")
    assert queue.kv_get(conn, "target") == "discord:#digests"
    assert queue.kv_get(conn, "missing", "dflt") == "dflt"


def test_rules_crud():
    conn = _conn()
    rid = queue.add_rule(conn, "world_message", {"sender": "x"}, 3, "lbl")
    rows = queue.rules_for(conn, "world_message")
    assert rows[0][0] == rid
    queue.toggle_rule(conn, rid, False)
    assert queue.rules_for(conn, "world_message") == []


def test_format_digest_groups_classes():
    rows = [(1, "t", "cron_done", 0, "{}"), (2, "t", "cron_done", 0, "{}"), (3, "t", "task_failed", 2, "{}")]
    text = delivery.format_digest(rows)
    assert "3 held" in text
    assert "cron_done: 2" in text
    assert "task_failed: 1" in text


def test_format_event_carries_tier_and_label():
    text = delivery.format_event("needs_human", 4, "needs-human gate -> pleasant alarm", "x" * 500)
    assert "T4" in text and "pleasant" in text
    assert len(text) < 600  # detail truncated
