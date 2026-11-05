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
