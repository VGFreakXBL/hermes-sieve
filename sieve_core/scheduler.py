"""Scheduling: quiet hours with breach floors, one-shot mutes, pause.

Semantics (locked in the design doc):
- A quiet window holds every tier BELOW its floor (default floor=T4: T0-T3 held,
  T4/T5 breach). floor=5 -> sirens only; floor=0 -> nothing held by the window.
- 'sieve mute <tier> until <when>' holds that tier AND everything below it until
  the deadline (mute T3 = T0-T3). Muting never covers an alarm above the named
  tier, so 'mute T3' cannot silence a T5.
- 'sieve pause 2h' holds EVERYTHING (including alarms) until the deadline.
- Held never means dropped: held events land in the digest.
"""

from datetime import datetime, timedelta

_WEEKDAY_NUMS = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}
_WEEKDAY_SETS = {
    "weekdays": "01234", "weekends": "56", "daily": "0123456", "all": "0123456",
}


def parse_hhmm(s: str) -> tuple:
    s = s.strip().lower()
    if ":" in s:
        h, m = s.split(":", 1)
    else:
        h, m = s, "00"
        if h.endswith("am") or h.endswith("pm"):
            pass
    ampm = None
    if h.endswith("am") or h.endswith("pm"):
        ampm = h[-2:]
        h = h[:-2]
    hh, mm = int(h), int(m)
    if ampm == "pm" and hh != 12:
        hh += 12
    if ampm == "am" and hh == 12:
        hh = 0
    return hh % 24, mm % 60


def parse_window(spec: str) -> tuple:
    """'23:00-08:00' -> ((23,0),(8,0))."""
    start, end = spec.split("-", 1)
    return parse_hhmm(start), parse_hhmm(end)


def parse_days(spec: str) -> str:
    """'weekdays' | 'weekends' | 'daily' | 'mon,wed,fri' -> '0123456' subset string."""
    spec = (spec or "daily").strip().lower()
    if spec in _WEEKDAY_SETS:
        return _WEEKDAY_SETS[spec]
    nums = []
    for tok in spec.replace(" ", "").split(","):
        if tok in _WEEKDAY_NUMS:
            nums.append(_WEEKDAY_NUMS[tok])
        elif tok.isdigit() and 0 <= int(tok) <= 6:
            nums.append(int(tok))
    return "".join(str(n) for n in sorted(set(nums))) or "0123456"


def parse_until(spec: str, now: datetime = None) -> datetime:
    """'9am' -> next 9am; '2h' / '45m' -> now + duration; '23:30' -> today/tomorrow 23:30."""
    now = now or datetime.now()
    spec = spec.strip().lower()
    if spec.endswith("h") and spec[:-1].strip().isdigit():
        return now + timedelta(hours=int(spec[:-1]))
    if spec.endswith("m") and spec[:-1].strip().isdigit():
        return now + timedelta(minutes=int(spec[:-1]))
    if spec.endswith("d") and spec[:-1].strip().isdigit():
        return now + timedelta(days=int(spec[:-1]))
    hh, mm = parse_hhmm(spec)
    target = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return target


def _in_window(now: datetime, start: tuple, end: tuple, days: str) -> bool:
    days = days or "0123456"
    cur = now.hour * 60 + now.minute
    s = start[0] * 60 + start[1]
    e = end[0] * 60 + end[1]
    if s == e:
        return str(now.weekday()) in days  # 24h window
    if s < e:
        return str(now.weekday()) in days and s <= cur < e
    # Overnight span (e.g. 23:00-08:00). Before the end time we are still inside
    # the night that STARTED the previous day, so the day selector must be
    # attributed to the start, not to this morning. Otherwise a Friday-night
    # window stops being quiet at midnight and the 2am-7am tail rings.
    if cur >= s:
        return str(now.weekday()) in days
    if cur < e:
        start_day = (now.weekday() - 1) % 7
        return str(start_day) in days
    return False  # between the end and the start: outside the window


def is_held(tier: int, windows, mutes, paused_until: str, now: datetime = None) -> tuple:
    """Returns (held: bool, reason: str). windows: queue.windows() rows;
    mutes: queue.active_mutes() rows; paused_until: ISO string or None."""
    now = now or datetime.now()
    now_iso = now.isoformat(timespec="seconds")

    if paused_until and paused_until > now_iso:
        return True, f"paused until {paused_until}"

    for (_id, mut_tier, until_ts) in mutes:
        # Mute is a ceiling, not a point: "mute T3" means T3 and below, because
        # nobody wants to mute T0, T1, T2 and T3 separately to get quiet.
        if tier <= mut_tier and until_ts > now_iso:
            band = f"T0-T{mut_tier}" if mut_tier else "T0"
            return True, f"{band} muted until {until_ts}"

    for (_id, start_hhmm, end_hhmm, days, floor_tier, label) in windows:
        try:
            start, end = parse_window(f"{start_hhmm}-{end_hhmm}")
        except Exception:
            continue
        if tier < floor_tier and _in_window(now, start, end, days):
            name = label or f"{start_hhmm}-{end_hhmm}"
            return True, f"quiet window {name} (floor T{floor_tier})"

    return False, ""
