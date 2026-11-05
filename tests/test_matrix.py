"""Matrix tests: user rules beat defaults, predicates, fallback, attribution."""

import sqlite3

from sieve_core import matrix, queue


def _conn():
    conn = sqlite3.connect(":memory:")
    queue.init_schema(conn)
    return conn


def test_default_needs_human_is_t4():
    d = matrix.decide("needs_human", {"kind": "clarify"}, [])
    assert d.tier == 4
    assert d.source == "default"
    assert "default:needs_human" in str(d.rule_id)


def test_default_service_down_is_t5_and_breaches():
    d = matrix.decide("service_down", {}, [])
    assert d.tier == 5


def test_user_rule_beats_default():
    conn = _conn()
    queue.add_rule(conn, "needs_human", {"kind": "kanban"}, 2, "kanban gates are cheap")
    d = matrix.decide("needs_human", {"kind": "kanban"}, queue.rules_for(conn, "needs_human"))
    assert d.tier == 2
    assert d.source == "user"


def test_more_specific_user_rule_wins():
    conn = _conn()
    queue.add_rule(conn, "world_message", {}, 1)
    queue.add_rule(conn, "world_message", {"sender": "boss"}, 3, "boss pings interrupt")
    rules = queue.rules_for(conn, "world_message")
    assert matrix.decide("world_message", {"sender": "boss"}, rules).tier == 3
    assert matrix.decide("world_message", {"sender": "randobot"}, rules).tier == 1


def test_keyword_predicate():
    conn = _conn()
    queue.add_rule(conn, "world_message", {"keyword": "tailscale"}, 5)
    d = matrix.decide("world_message", {"text": "ALERT: tailscale is down"}, queue.rules_for(conn, "world_message"))
    assert d.tier == 5


def test_fallback_is_t2_with_label():
    d = matrix.decide("who_knows", {}, [])
    assert d.tier == 2
    assert d.source == "fallback"
    assert "fallback" in d.label


def test_disabled_rule_ignored():
    conn = _conn()
    rid = queue.add_rule(conn, "needs_human", {}, 0)
    queue.toggle_rule(conn, rid, False)
    d = matrix.decide("needs_human", {}, queue.rules_for(conn, "needs_human"))
    assert d.tier == 4  # default applies
