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


def test_probe_gap_is_not_called_uptime():
    conn = _conn()
    earlier = datetime(2026, 11, 11, 8, 0, 0)
    queue.kv_set(conn, "tailscale_probe_at", earlier.isoformat(timespec="seconds"))
    queue.kv_set(conn, "tailscale_up", "yes")
    now = earlier + timedelta(minutes=45)
    out = probe.observe(conn, lambda *a, **k: None,
                        runner=lambda: ('{"BackendState": "Running", "Self": {"Online": true}}', 0),
                        now=now, force=True)
    assert out["up"] is True
    assert out["fired"] is False
    assert "gap" in out["detail"].lower()
    assert "unknown" in out["detail"].lower()
    assert queue.kv_get(conn, "probe_gap_note")


def test_no_gap_stays_silent():
    conn = _conn()
    earlier = datetime(2026, 11, 11, 8, 0, 0)
    queue.kv_set(conn, "tailscale_probe_at", earlier.isoformat(timespec="seconds"))
    out = probe.observe(conn, lambda *a, **k: None,
                        runner=lambda: ('{"BackendState": "Running", "Self": {"Online": true}}', 0),
                        now=earlier + timedelta(seconds=90), force=True)
    assert "gap" not in out["detail"].lower()


def test_gateway_death_routes_once(tmp_path):
    conn = _conn()
    queue.kv_set(conn, "gateway_up", "yes")
    fired = []
    out = probe.observe_gateway(conn, lambda *a, **k: fired.append(a[0]), home=tmp_path,
                                pid_alive=lambda _pid: False, force=True)
    assert out["fired"] is True
    assert fired == ["service_down"]
    again = probe.observe_gateway(conn, lambda *a, **k: fired.append("again"), home=tmp_path,
                                  pid_alive=lambda _pid: False, force=True)
    assert again["fired"] is False


def test_gateway_first_down_does_not_fire(tmp_path):
    conn = _conn()
    (tmp_path / "gateway.pid").write_text('{"pid": 1}', encoding="utf-8")
    fired = []
    out = probe.observe_gateway(conn, lambda *a, **k: fired.append(1), home=tmp_path,
                                pid_alive=lambda _pid: False, force=True)
    assert fired == []
    assert out["up"] is False


def test_timeout_does_not_flip_state():
    conn = _conn()
    queue.kv_set(conn, "tailscale_up", "yes")

    def boom():
        raise probe.subprocess.TimeoutExpired(cmd="tailscale", timeout=3)

    out = probe.observe(conn, lambda *a, **k: None, runner=boom, force=True)
    assert out["fired"] is False
    assert out["up"] is None
    assert queue.kv_get(conn, "tailscale_up") == "yes"
