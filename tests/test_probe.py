"""Tailscale probe: fire service_down only on an up-to-down edge."""

import sqlite3
from datetime import datetime, timedelta

from sieve_core import probe, queue


def _conn():
    conn = sqlite3.connect(":memory:")
    queue.init_schema(conn)
    return conn


def test_missing_binary_is_not_an_outage(monkeypatch):
    monkeypatch.setattr(probe.shutil, "which", lambda _name: None)
    state = probe.read_status()
    assert state["installed"] is False
    assert state["up"] is None


def test_parse_running_online_is_up():
    raw = '{"BackendState": "Running", "Self": {"Online": true}}'
    assert probe.parse_status(raw)["up"] is True


def test_first_down_does_not_fire():
    conn = _conn()
    fired = []
    out = probe.observe(conn, lambda *a, **k: fired.append(a),
                        runner=lambda: ('{"BackendState": "Stopped", "Self": {"Online": false}}', 0),
                        force=True)
    assert fired == []
    assert out["fired"] is False
    assert queue.kv_get(conn, "tailscale_up") == "no"


def test_up_to_down_routes_once():
    conn = _conn()
    queue.kv_set(conn, "tailscale_up", "yes")
    fired = []
    out = probe.observe(conn, lambda *a, **k: fired.append(a[0]),
                        runner=lambda: ('{"BackendState": "Stopped", "Self": {"Online": false}}', 0),
                        force=True)
    assert out["fired"] is True
    assert fired == ["service_down"]
    again = probe.observe(conn, lambda *a, **k: fired.append(a[0]),
                          runner=lambda: ('{"BackendState": "Stopped", "Self": {"Online": false}}', 0),
                          force=True)
    assert again["fired"] is False
    assert fired == ["service_down"]


def test_interval_skips_without_force():
    conn = _conn()
    now = datetime(2026, 11, 11, 9, 0, 0)
    queue.kv_set(conn, "tailscale_probe_at", now.isoformat(timespec="seconds"))
    queue.kv_set(conn, "tailscale_up", "yes")
    calls = []
    out = probe.observe(conn, lambda *a, **k: None, now=now + timedelta(seconds=10),
                        runner=lambda: calls.append(1) or ("{}", 0))
    assert out["skipped"] is True
    assert calls == []


def test_timeout_does_not_flip_state():
    conn = _conn()
    queue.kv_set(conn, "tailscale_up", "yes")

    def boom():
        raise probe.subprocess.TimeoutExpired(cmd="tailscale", timeout=3)

    out = probe.observe(conn, lambda *a, **k: None, runner=boom, force=True)
    assert out["fired"] is False
    assert out["up"] is None
    assert queue.kv_get(conn, "tailscale_up") == "yes"
