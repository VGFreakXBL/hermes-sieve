"""Durable state: event log, holds, rules, quiet windows, mutes.

Uses the documented `plugin_db`/`plugin_data_dir` storage API so state survives
plugin updates. Tests inject their own sqlite3 connection; the host path is only
touched via `db()`.
"""

import json
import sqlite3
from datetime import datetime

NAME = "sieve"

SCHEMA = """
CREATE TABLE IF NOT EXISTS rules (
    id INTEGER PRIMARY KEY, event_class TEXT NOT NULL, match_json TEXT NOT NULL DEFAULT '{}',
    tier INTEGER NOT NULL, enabled INTEGER NOT NULL DEFAULT 1,
    source TEXT NOT NULL DEFAULT 'user', label TEXT NOT NULL DEFAULT '', created_at TEXT);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY, ts TEXT NOT NULL, event_class TEXT NOT NULL, payload_json TEXT NOT NULL,
    rule_id INTEGER, tier INTEGER NOT NULL, action TEXT NOT NULL, detail TEXT NOT NULL DEFAULT '');
CREATE TABLE IF NOT EXISTS holds (
    id INTEGER PRIMARY KEY, event_id INTEGER NOT NULL, hold_until TEXT, released INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS quiet_windows (
    id INTEGER PRIMARY KEY, start_hhmm TEXT NOT NULL, end_hhmm TEXT NOT NULL,
    days TEXT NOT NULL DEFAULT '0123456', floor_tier INTEGER NOT NULL DEFAULT 4,
    label TEXT NOT NULL DEFAULT '');
CREATE TABLE IF NOT EXISTS mutes (
    id INTEGER PRIMARY KEY, tier INTEGER NOT NULL, until_ts TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS kv (
    k TEXT PRIMARY KEY, v TEXT NOT NULL);
"""


def init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.commit()


def db() -> sqlite3.Connection:
    from plugins.plugin_storage import plugin_db
    conn = plugin_db(NAME)
    init_schema(conn)
    return conn


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


# --- events / holds -------------------------------------------------------

def log_event(conn, event_class, payload, tier, action, rule_id=None, detail="") -> int:
    cur = conn.execute(
        "INSERT INTO events(ts, event_class, payload_json, rule_id, tier, action, detail)"
        " VALUES(?,?,?,?,?,?,?)",
        (now_iso(), event_class, json.dumps(payload, default=str), rule_id, tier, action, detail))
    conn.commit()
    return cur.lastrowid


def add_hold(conn, event_id, hold_until=None) -> None:
    conn.execute("INSERT INTO holds(event_id, hold_until) VALUES(?,?)", (event_id, hold_until))
    conn.commit()


def held_events(conn):
    return conn.execute(
        "SELECT e.id, e.ts, e.event_class, e.tier, e.payload_json FROM holds h"
        " JOIN events e ON e.id = h.event_id WHERE h.released = 0 ORDER BY e.id").fetchall()


def release_all(conn) -> int:
    cur = conn.execute("UPDATE holds SET released = 1 WHERE released = 0")
    conn.commit()
    return cur.rowcount


def held_count(conn) -> int:
    return conn.execute("SELECT COUNT(*) FROM holds WHERE released = 0").fetchone()[0]


def recent_events(conn, limit=20):
    return conn.execute(
        "SELECT id, ts, event_class, tier, action, detail FROM events ORDER BY id DESC LIMIT ?",
        (limit,)).fetchall()


# --- rules ----------------------------------------------------------------

def rules_for(conn, event_class):
    return conn.execute(
        "SELECT id, event_class, match_json, tier, source, label FROM rules"
        " WHERE event_class = ? AND enabled = 1", (event_class,)).fetchall()


def all_rules(conn):
    return conn.execute(
        "SELECT id, event_class, match_json, tier, enabled, source, label FROM rules ORDER BY id").fetchall()


def add_rule(conn, event_class, match, tier, label="", source="user") -> int:
    """User rules are a keyed setting, not an append-only log.

    Re-adding the same class+match must change the tier, not stack a second row.
    Stacking is not cosmetic: decide() prefers the most specific match and keeps
    the first on a tie, so an older duplicate silently wins and the user's edit
    appears to do nothing.
    """
    if source == "user":
        existing = conn.execute(
            "SELECT id FROM rules WHERE event_class = ? AND match_json = ? AND source = 'user'",
            (event_class, json.dumps(match))).fetchone()
        if existing:
            # Update in place: events.rule_id points at this id, and reusing a
            # deleted rowid would silently re-label history as a different rule.
            conn.execute("UPDATE rules SET tier = ?, label = ? WHERE id = ?",
                         (tier, label, existing[0]))
            conn.commit()
            return int(existing[0])
    cur = conn.execute(
        "INSERT INTO rules(event_class, match_json, tier, label, source, created_at)"
        " VALUES(?,?,?,?,?,?)",
        (event_class, json.dumps(match), tier, label, source, now_iso()))
    conn.commit()
    return cur.lastrowid


def toggle_rule(conn, rule_id, enabled: bool) -> None:
    conn.execute("UPDATE rules SET enabled = ? WHERE id = ?", (1 if enabled else 0, rule_id))
    conn.commit()


# --- quiet windows / mutes / kv -------------------------------------------

def add_window(conn, start_hhmm, end_hhmm, days, floor_tier, label="") -> int:
    cur = conn.execute(
        "INSERT INTO quiet_windows(start_hhmm, end_hhmm, days, floor_tier, label)"
        " VALUES(?,?,?,?,?)", (start_hhmm, end_hhmm, days, floor_tier, label))
    conn.commit()
    return cur.lastrowid


def windows(conn):
    return conn.execute(
        "SELECT id, start_hhmm, end_hhmm, days, floor_tier, label FROM quiet_windows").fetchall()


def clear_windows(conn) -> None:
    conn.execute("DELETE FROM quiet_windows")
    conn.commit()


def delete_window(conn, window_id) -> None:
    conn.execute("DELETE FROM quiet_windows WHERE id = ?", (int(window_id),))
    conn.commit()


def add_mute(conn, tier, until_ts) -> int:
    cur = conn.execute("INSERT INTO mutes(tier, until_ts) VALUES(?,?)", (tier, until_ts))
    conn.commit()
    return cur.lastrowid


def active_mutes(conn, now_iso_str):
    return conn.execute(
        "SELECT id, tier, until_ts FROM mutes WHERE until_ts > ?", (now_iso_str,)).fetchall()


def kv_get(conn, k, default=None):
    row = conn.execute("SELECT v FROM kv WHERE k = ?", (k,)).fetchone()
    return row[0] if row else default


def kv_set(conn, k, v) -> None:
    conn.execute("INSERT INTO kv(k, v) VALUES(?,?) ON CONFLICT(k) DO UPDATE SET v = excluded.v",
                 (k, str(v)))
    conn.commit()


# --- stats + aging holds ----------------------------------------------------

def rule_stats(conn):
    """Per-rule firing counts from the event log: (rule_id, total, delivered, held)."""
    return conn.execute(
        "SELECT rule_id, COUNT(*), COALESCE(SUM(action='delivered'),0), COALESCE(SUM(action='held'),0)"
        " FROM events WHERE rule_id IS NOT NULL GROUP BY rule_id").fetchall()


def stale_holds(conn, cutoff_iso, min_tier=1):
    """Held, unreleased, un-promoted items older than cutoff (for the promotion ladder)."""
    return conn.execute(
        "SELECT h.id, e.id, e.tier, e.event_class FROM holds h JOIN events e ON e.id = h.event_id"
        " WHERE h.released = 0 AND h.hold_until IS NULL AND e.tier >= ? AND e.ts < ?",
        (min_tier, cutoff_iso)).fetchall()


def recent_deliveries(conn, since_iso, min_tier=2):
    return conn.execute(
        "SELECT COUNT(*) FROM events WHERE action = 'delivered' AND tier >= ? AND ts > ?",
        (min_tier, since_iso)).fetchone()[0]


def release_one(conn, hold_id):
    conn.execute("UPDATE holds SET released = 1 WHERE id = ?", (hold_id,))
    conn.commit()


def set_context_rule(conn, event_class, context, tier):
    """Grid cell write: the tier for one event class in one context."""
    conn.execute("DELETE FROM rules WHERE event_class = ? AND match_json = ?",
                 (event_class, json.dumps({"context": context})))
    return add_rule(conn, event_class, {"context": context}, tier,
                    label=f"{event_class} in {context}", source="user")


def context_rules(conn):
    rows = conn.execute(
        "SELECT event_class, match_json, tier, enabled FROM rules WHERE match_json LIKE '%context%'").fetchall()
    out = []
    for (cls, match_json, tier, enabled) in rows:
        try:
            ctx_name = json.loads(match_json).get("context")
        except Exception:
            ctx_name = None
        if ctx_name:
            out.append((cls, ctx_name, tier, bool(enabled)))
    return out


# --- policy export / import ------------------------------------------------
# Policy travels as a file, never as a cross-profile write. A plugin running in
# one profile must not reach into another profile's store: profiles are a
# boundary, and a boundary tool that violates one is not trustworthy. The user
# moves the file and runs import in the profile that should own the policy.

POLICY_KV = (
    "target", "target:T2", "target:T3", "target:T4", "target:T5",
    "budget_per_hour", "repeat_limit_hours", "promo_minutes",
)
# Operational state stays behind: it describes this machine right now, not policy.
POLICY_EXCLUDED_KV = ("paused_until", "escalation_degraded")


def export_policy(conn) -> dict:
    """User-authored policy only. Seeded defaults live in code, not in the file."""
    rules = [
        {"event_class": cls, "match": json.loads(match_json or "{}"),
         "tier": tier, "enabled": bool(enabled), "label": label}
        for (cls, match_json, tier, enabled, label) in conn.execute(
            "SELECT event_class, match_json, tier, enabled, label FROM rules"
            " WHERE source = 'user' ORDER BY id").fetchall()
    ]
    windows_out = [
        {"start_hhmm": s, "end_hhmm": e, "days": d, "floor_tier": f, "label": lb}
        for (s, e, d, f, lb) in conn.execute(
            "SELECT start_hhmm, end_hhmm, days, floor_tier, label FROM quiet_windows"
            " ORDER BY id").fetchall()
    ]
    mutes_out = [
        {"tier": t, "until_ts": u}
        for (t, u) in conn.execute(
            "SELECT tier, until_ts FROM mutes WHERE until_ts > ? ORDER BY id",
            (now_iso(),)).fetchall()
    ]
    kv_out = {}
    for k in POLICY_KV:
        v = kv_get(conn, k)
        if v not in (None, ""):
            kv_out[k] = v
    return {
        "sieve_policy_version": 1,
        "exported_at": now_iso(),
        "rules": rules,
        "quiet_windows": windows_out,
        "mutes": mutes_out,
        "kv": kv_out,
    }


def _valid_tier(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= 5


def import_policy(conn, data, replace: bool = False) -> dict:
    """Apply an exported policy to THIS profile. Validates before writing anything."""
    if not isinstance(data, dict):
        raise ValueError("policy file is not an object")
    if data.get("sieve_policy_version") != 1:
        raise ValueError("unsupported sieve_policy_version (expected 1)")

    rules_in = data.get("rules") or []
    windows_in = data.get("quiet_windows") or []
    mutes_in = data.get("mutes") or []
    kv_in = data.get("kv") or {}
    if not isinstance(rules_in, list) or not isinstance(windows_in, list)             or not isinstance(mutes_in, list) or not isinstance(kv_in, dict):
        raise ValueError("policy file has the wrong shape")

    # Validate the whole file first: a half-applied policy is worse than none.
    for r in rules_in:
        if not isinstance(r, dict) or not isinstance(r.get("event_class"), str) \
                or not r.get("event_class") or not _valid_tier(r.get("tier")) \
                or not isinstance(r.get("match") or {}, dict):
            raise ValueError("invalid rule in policy file")
    for w in windows_in:
        if not isinstance(w, dict) or not isinstance(w.get("start_hhmm"), str) \
                or not isinstance(w.get("end_hhmm"), str) or not _valid_tier(w.get("floor_tier")):
            raise ValueError("invalid quiet window in policy file")
    for k in kv_in:
        if k not in POLICY_KV:
            raise ValueError(f"key '{k}' is not a portable policy key")

    applied = {"rules": 0, "quiet_windows": 0, "mutes": 0, "kv": 0}
    if replace:
        conn.execute("DELETE FROM rules WHERE source = 'user'")
        conn.execute("DELETE FROM quiet_windows")
        conn.execute("DELETE FROM mutes")

    for r in rules_in:
        add_rule(conn, r["event_class"], r.get("match") or {}, int(r["tier"]),
                 label=str(r.get("label") or ""), source="user")
        if r.get("enabled") is False:
            conn.execute("UPDATE rules SET enabled = 0 WHERE event_class = ?"
                         " AND match_json = ? AND source = 'user'",
                         (r["event_class"], json.dumps(r.get("match") or {})))
        applied["rules"] += 1
    for w in windows_in:
        add_window(conn, w["start_hhmm"], w["end_hhmm"], str(w.get("days") or "0123456"),
                   int(w["floor_tier"]), label=str(w.get("label") or ""))
        applied["quiet_windows"] += 1
    for m in mutes_in:
        if _valid_tier(m.get("tier")) and isinstance(m.get("until_ts"), str):
            add_mute(conn, int(m["tier"]), m["until_ts"])
            applied["mutes"] += 1
    for k, v in kv_in.items():
        kv_set(conn, k, v)
        applied["kv"] += 1
    conn.commit()
    return applied
