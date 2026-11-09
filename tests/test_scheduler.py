"""Scheduler tests: overnight spans, day scoping, breach floors, mutes, pause."""

from datetime import datetime

from sieve_core import scheduler

WED_2330 = datetime(2026, 11, 4, 23, 30)   # Wednesday (weekday 2)
WED_1200 = datetime(2026, 11, 4, 12, 0)


def test_quiet_window_holds_below_floor():
    windows = [(1, "23:00", "08:00", "0123456", 4, "night")]
    held, reason = scheduler.is_held(2, windows, [], None, WED_2330)
    assert held and "quiet" in reason


def test_quiet_window_floor_allows_breach():
    windows = [(1, "23:00", "08:00", "0123456", 4, "night")]
    assert scheduler.is_held(4, windows, [], None, WED_2330)[0] is False
    assert scheduler.is_held(5, windows, [], None, WED_2330)[0] is False


def test_overnight_span_outside_hours():
    windows = [(1, "23:00", "08:00", "0123456", 4, "night")]
    assert scheduler.is_held(2, windows, [], None, WED_1200)[0] is False


def test_day_scoping():
    windows = [(1, "00:00", "23:59", "56", 4, "weekend")]  # sat+sun only
    assert scheduler.is_held(2, windows, [], None, WED_1200)[0] is False
    SAT = datetime(2026, 11, 7, 12, 0)
    assert scheduler.is_held(2, windows, [], None, SAT)[0] is True


def test_floor_none_holds_alarms_too():
    windows = [(1, "23:00", "08:00", "0123456", 6, "total silence")]  # floor=none -> 6: hold all
    assert scheduler.is_held(5, windows, [], None, WED_2330)[0] is True
    assert scheduler.is_held(0, windows, [], None, WED_2330)[0] is True


def test_mute_is_a_ceiling_not_a_point():
    """'mute T3' means T3 and below: nobody mutes each lower tier separately."""
    mutes = [(1, 4, "2099-01-01T00:00:00")]
    assert scheduler.is_held(4, [], mutes, None, WED_1200)[0] is True
    assert scheduler.is_held(5, [], mutes, None, WED_1200)[0] is False, "never covers a tier above the ceiling"
    assert scheduler.is_held(3, [], mutes, None, WED_1200)[0] is True, "T3 is below a T4 ceiling"
    assert scheduler.is_held(0, [], mutes, None, WED_1200)[0] is True


def test_pause_holds_everything():
    assert scheduler.is_held(5, [], [], "2099-01-01T00:00:00", WED_1200)[0] is True


def test_expired_pause_releases():
    assert scheduler.is_held(5, [], [], "2020-01-01T00:00:00", WED_1200)[0] is False


def test_parse_until_relative():
    t = scheduler.parse_until("2h", WED_1200)
    assert t.hour == 14


def test_parse_until_clock_rolls_to_tomorrow():
    t = scheduler.parse_until("9am", WED_1200)
    assert t.hour == 9 and t.day == 5
    t2 = scheduler.parse_until("9am", WED_2330)
    assert t2.hour == 9 and t2.day == 5


def test_parse_window_and_days():
    (s, e) = scheduler.parse_window("23:00-08:00")
    assert s == (23, 0) and e == (8, 0)
    assert scheduler.parse_days("weekdays") == "01234"
    assert scheduler.parse_days("mon,wed,fri") == "024"
