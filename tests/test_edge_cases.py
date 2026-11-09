"""Edge cases: the places a notification boundary is most likely to be wrong.

Written as "what should happen", not "what currently happens". Each failure here
is a real gap, not a broken test.
"""

import importlib.util
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from sieve_core import matrix, queue, scheduler

_ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("sieve_plugin_edge", _ROOT / "__init__.py")
plugin = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(plugin)


def _fresh(monkeypatch, tmp_path):
    conn = sqlite3.connect(str(tmp_path / "edge.db"))
    queue.init_schema(conn)
    monkeypatch.setattr(queue, "db", lambda: conn)
    monkeypatch.setattr(plugin.delivery, "send", lambda *a, **k: True)
    return conn


# --- quiet windows ---------------------------------------------------------

def test_overnight_window_covers_the_next_morning():
    start, end = scheduler.parse_window("23:00-08:00")
    saturday_night = datetime(2026, 11, 7, 23, 30)   # Sat
    sunday_morning = datetime(2026, 11, 8, 7, 30)    # Sun, still the same night
    assert scheduler._in_window(saturday_night, start, end, "0123456")
    assert scheduler._in_window(sunday_morning, start, end, "0123456")


def test_overnight_window_respects_the_start_day():
    """A Friday-night window with days=fri must still be quiet at 2am Saturday."""
    start, end = scheduler.parse_window("23:00-08:00")
    sat_2am = datetime(2026, 11, 7, 2, 0)            # Saturday 02:00 = Friday night
    assert scheduler._in_window(sat_2am, start, end, "4"), \
        "the tail of an overnight window belongs to the day it started"


def test_window_floor_zero_holds_nothing():
    held, _ = scheduler.is_held(1, [(1, "23:00", "08:00", "0123456", 0, "off")], [],
                                None, datetime(2026, 11, 9, 23, 30))
    assert held is False


def test_window_floor_five_holds_t4_but_not_t5():
    wins = [(1, "23:00", "08:00", "0123456", 5, "deep")]
    night = datetime(2026, 11, 9, 23, 30)
    assert scheduler.is_held(4, wins, [], None, night)[0] is True
    assert scheduler.is_held(5, wins, [], None, night)[0] is False


def test_malformed_window_does_not_break_the_check():
    bad = [(1, "nonsense", "08:00", "0123456", 4, "broken")]
    held, _ = scheduler.is_held(2, bad, [], None, datetime(2026, 11, 9, 23, 30))
    assert held is False, "a corrupt row must be skipped, not raise"


def test_full_day_window_is_24h():
    start, end = scheduler.parse_window("00:00-00:00")
    assert scheduler._in_window(datetime(2026, 11, 9, 3, 0), start, end, "0123456")


# --- pause and mute boundaries -------------------------------------------

def test_mute_at_exact_deadline_expires():
    now = datetime(2026, 11, 9, 9, 0, 0)
    mutes = [(1, 3, now.isoformat(timespec="seconds"))]
    assert scheduler.is_held(3, [], mutes, None, now)[0] is False


def test_pause_at_exact_deadline_expires():
    now = datetime(2026, 11, 9, 9, 0, 0)
    assert scheduler.is_held(5, [], [], now.isoformat(timespec="seconds"), now)[0] is False


def test_mute_covers_lower_tiers_but_never_alarms_above():
    now = datetime(2026, 11, 9, 8, 0, 0)
    mutes = [(1, 3, (now + timedelta(hours=1)).isoformat(timespec="seconds"))]
    assert scheduler.is_held(3, [], mutes, None, now)[0] is True
    assert scheduler.is_held(1, [], mutes, None, now)[0] is True, "T1 is under a T3 ceiling"
    assert scheduler.is_held(4, [], mutes, None, now)[0] is False, "T4 must not inherit a T3 mute"
    assert scheduler.is_held(5, [], mutes, None, now)[0] is False


# --- time parsing ---------------------------------------------------------

@pytest.mark.parametrize("spec,expected", [
    ("9am", (9, 0)), ("9pm", (21, 0)), ("12am", (0, 0)), ("12pm", (12, 0)),
    ("23:30", (23, 30)), ("7", (7, 0)), ("00:05", (0, 5)),
])
def test_parse_hhmm(spec, expected):
    assert scheduler.parse_hhmm(spec) == expected


def test_parse_until_rolls_to_tomorrow():
    now = datetime(2026, 11, 9, 10, 0)
    assert scheduler.parse_until("9am", now).day == 10


def test_parse_until_duration_forms():
    now = datetime(2026, 11, 9, 10, 0)
    assert scheduler.parse_until("2h", now) == now + timedelta(hours=2)
    assert scheduler.parse_until("45m", now) == now + timedelta(minutes=45)
    assert scheduler.parse_until("1d", now) == now + timedelta(days=1)


# --- budget and promotion -------------------------------------------------

def test_budget_never_holds_an_alarm(monkeypatch, tmp_path):
    """A capped hour must not swallow a T4 alarm. Budget is for chatter."""
    conn = _fresh(monkeypatch, tmp_path)
    queue.kv_set(conn, "budget_per_hour", "1")
    for _ in range(3):
        queue.log_event(conn, "agent_message", {}, 2, "delivered")
    monkeypatch.setattr(plugin.matrix, "decide",
                        lambda *a, **k: matrix.Decision(tier=4, rule_id="r", label="gate", source="user"))
    monkeypatch.setattr(plugin.escalate, "push_ready", lambda _d: (False, "device stale"))
    out = plugin._route("needs_human", {})
    assert out.get("held") is not True, f"budget held an alarm: {out}"
    assert out.get("delivered") is True


def test_promotion_does_not_bypass_pause(monkeypatch, tmp_path):
    conn = _fresh(monkeypatch, tmp_path)
    queue.kv_set(conn, "promo_minutes", "5")
    queue.kv_set(conn, "paused_until", (datetime.now() + timedelta(hours=2)).isoformat(timespec="seconds"))
    eid = queue.log_event(conn, "task_failed", {}, 2, "held")
    _backdate(conn, eid, minutes=30)
    conn.execute("INSERT INTO holds(event_id, hold_until) VALUES(?,NULL)", (eid,))
    conn.commit()
    fired = []
    monkeypatch.setattr(plugin.delivery, "send", lambda *a, **k: fired.append(a) or True)
    monkeypatch.setattr(plugin.escalate, "push_ready", lambda _d: (True, "fresh"))
    monkeypatch.setattr(plugin.escalate, "fire_timer", lambda *a, **k: {"ok": True, "verified": True})
    plugin._promote_sweep(conn)
    assert fired == [], "pause must suppress promotion, or the ladder defeats the pause"


def test_promotion_respects_quiet_floor(monkeypatch, tmp_path):
    conn = _fresh(monkeypatch, tmp_path)
    queue.kv_set(conn, "promo_minutes", "1")
    now = datetime.now()
    queue.add_window(conn, "00:00-00:00", "", "0123456", 5, "deep silence")
    # rewrite the malformed window into a valid all-day floor-5 window
    conn.execute("UPDATE quiet_windows SET start_hhmm='00:00', end_hhmm='00:00'")
    conn.commit()
    eid = queue.log_event(conn, "task_failed", {}, 3, "held")
    _backdate(conn, eid, minutes=30)
    conn.execute("INSERT INTO holds(event_id, hold_until) VALUES(?,NULL)", (eid,))
    conn.commit()
    fired = []
    monkeypatch.setattr(plugin.delivery, "send", lambda *a, **k: fired.append(a) or True)
    monkeypatch.setattr(plugin.escalate, "push_ready", lambda _d: (True, "fresh"))
    monkeypatch.setattr(plugin.escalate, "fire_timer", lambda *a, **k: {"ok": True, "verified": True})
    monkeypatch.setattr(plugin.escalate, "fire_alarm", lambda *a, **k: {"ok": True, "verified": True})
    plugin._promote_sweep(conn)
    assert fired == [], "a floor-5 window means silence: promotion must not punch through it"


# --- repeat limit ---------------------------------------------------------

def test_repeat_limit_never_suppresses_t3(monkeypatch, tmp_path):
    conn = _fresh(monkeypatch, tmp_path)
    for _ in range(3):
        queue.log_event(conn, "needs_human", {}, 2, "delivered")
    decision = matrix.Decision(tier=3, rule_id="r", label="interrupt", source="user")
    out = plugin._deliver(conn, 99, "needs_human", {}, decision)
    assert out["delivered"] is True


# --- policy transport ----------------------------------------------------

def test_import_twice_does_not_duplicate_windows_or_mutes(monkeypatch, tmp_path):
    src = sqlite3.connect(":memory:")
    queue.init_schema(src)
    queue.add_window(src, "23:00", "08:00", "0123456", 4, label="night")
    queue.add_mute(src, 3, (datetime.now() + timedelta(hours=1)).isoformat(timespec="seconds"))
    data = queue.export_policy(src)

    dst = _fresh(monkeypatch, tmp_path)
    queue.import_policy(dst, data)
    queue.import_policy(dst, data)
    assert conn_count(dst, "quiet_windows") == 1, "re-import must be idempotent"
    assert conn_count(dst, "mutes") == 1


def conn_count(conn, table):
    return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


# --- delivery honesty ----------------------------------------------------

def test_failed_send_is_not_logged_as_delivered(monkeypatch, tmp_path):
    """The event log is what /sieve why and the stats read. A failed send that
    says 'delivered' poisons every analytic built on it."""
    conn = _fresh(monkeypatch, tmp_path)
    monkeypatch.setattr(plugin.delivery, "send", lambda *a, **k: False)
    monkeypatch.setattr(plugin.matrix, "decide",
                        lambda *a, **k: matrix.Decision(tier=2, rule_id="r", label="l", source="user"))
    out = plugin._route("agent_message", {})
    assert out["delivered"] is False
    action = conn.execute("SELECT action FROM events ORDER BY id DESC LIMIT 1").fetchone()[0]
    assert action != "delivered", f"log says {action} but nothing reached the user"


# --- digest timer --------------------------------------------------------

def test_digest_fires_when_held_and_overdue(monkeypatch, tmp_path):
    conn = _fresh(monkeypatch, tmp_path)
    sent = []
    monkeypatch.setattr(plugin.delivery, "send", lambda text, target=None, timeout=30: sent.append(text) or True)
    eid = queue.log_event(conn, "task_done", {}, 0, "held")
    queue.add_hold(conn, eid)
    out = plugin._maybe_digest(conn)
    assert out.startswith("digest sent"), out
    assert sent and "SIEVE digest" in sent[0]
    assert queue.held_count(conn) == 0, "a sent digest releases the items it covers"


def test_digest_waits_inside_the_interval(monkeypatch, tmp_path):
    conn = _fresh(monkeypatch, tmp_path)
    sent = []
    monkeypatch.setattr(plugin.delivery, "send", lambda *a, **k: sent.append(a) or True)
    queue.kv_set(conn, "digest_every_hours", "8")
    queue.kv_set(conn, "last_digest_at", datetime.now().isoformat(timespec="seconds"))
    eid = queue.log_event(conn, "task_done", {}, 0, "held")
    queue.add_hold(conn, eid)
    assert plugin._maybe_digest(conn) == ""
    assert sent == []


def test_digest_off_leaves_items_held(monkeypatch, tmp_path):
    conn = _fresh(monkeypatch, tmp_path)
    monkeypatch.setattr(plugin.delivery, "send", lambda *a, **k: True)
    queue.kv_set(conn, "digest_every_hours", "0")
    eid = queue.log_event(conn, "task_done", {}, 0, "held")
    queue.add_hold(conn, eid)
    assert plugin._maybe_digest(conn) == ""
    assert queue.held_count(conn) == 1


def test_digest_noop_when_nothing_held(monkeypatch, tmp_path):
    conn = _fresh(monkeypatch, tmp_path)
    monkeypatch.setattr(plugin.delivery, "send", lambda *a, **k: True)
    assert plugin._maybe_digest(conn) == ""


# --- hook safety ---------------------------------------------------------

def test_hook_never_raises_into_the_host(monkeypatch, tmp_path):
    """A boundary plugin must fail closed and quiet, not break the user's turn."""
    def boom(*_a, **_k):
        raise RuntimeError("store exploded")

    monkeypatch.setattr(plugin, "_route", boom)
    plugin._safe_route("needs_human", {})  # must not raise


# --- simulate honesty ----------------------------------------------------

def test_simulate_marks_phone_ringing_only_for_alarm_tiers(monkeypatch, tmp_path):
    conn = _fresh(monkeypatch, tmp_path)
    import importlib.util as _u
    spec = _u.spec_from_file_location("sieve_api_edge", _ROOT / "dashboard" / "plugin_api.py")
    api = _u.module_from_spec(spec)
    spec.loader.exec_module(api)
    queue.import_policy(conn, {"sieve_policy_version": 1, "rules": [
        {"event_class": "probe", "match": {}, "tier": t, "label": f"T{t}"} for t in ()]})
    for tier in (0, 1, 2, 3, 4, 5):
        conn.execute("DELETE FROM rules")
        queue.add_rule(conn, "probe", {}, tier, label=f"probe T{tier}")
        out = api._simulate("probe", "base", None)
        assert out["rings_phone"] is (tier >= 4), f"T{tier} reported rings_phone={out['rings_phone']}"


def _backdate(conn, event_id, minutes=30):
    ts = (datetime.now() - timedelta(minutes=minutes)).isoformat(timespec="seconds")
    conn.execute("UPDATE events SET ts=? WHERE id=?", (ts, event_id))
    conn.commit()
