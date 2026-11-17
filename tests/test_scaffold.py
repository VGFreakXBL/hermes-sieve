"""Scaffold tests (Task 1.1) — no network, no live Hermes ctx needed."""

import importlib.util
import sqlite3
from pathlib import Path

import pytest

PLUGIN_DIR = Path(__file__).resolve().parent.parent


def _load_plugin_module():
    spec = importlib.util.spec_from_file_location("sieve_plugin", PLUGIN_DIR / "__init__.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class _FakeCtx:
    """Captures what `register` subscribes, without a live host."""

    def __init__(self):
        self.hooks = {}
        self.tools = []
        self.commands = []

    def register_hook(self, name, fn):
        self.hooks[name] = fn

    def register_tool(self, **kwargs):
        self.tools.append(kwargs.get("name"))

    def register_command(self, *args, **kwargs):
        self.commands.append(args[0] if args else kwargs.get("name"))


def _provided_hooks():
    """Hook names declared in plugin.yaml, so the manifest and code cannot drift."""
    lines = (PLUGIN_DIR / "plugin.yaml").read_text().splitlines()
    out, inside = [], False
    for line in lines:
        if line.startswith("provides_hooks:"):
            inside = True
            continue
        if inside:
            if line.startswith("  - "):
                out.append(line.strip()[2:].strip())
            elif line.strip() and not line.startswith(" "):
                break
    return set(out)


def test_plugin_yaml_exists_and_names_sieve():
    text = (PLUGIN_DIR / "plugin.yaml").read_text()
    assert "name: sieve" in text
    assert "version:" in text


def test_register_is_importable():
    mod = _load_plugin_module()
    assert hasattr(mod, "register")


def test_registered_hooks_match_plugin_yaml(monkeypatch, tmp_path):
    mod = _load_plugin_module()
    conn = sqlite3.connect(":memory:")
    mod.queue.init_schema(conn)
    monkeypatch.setattr(mod.queue, "db", lambda: conn)
    ctx = _FakeCtx()
    mod.register(ctx)
    assert set(ctx.hooks) == _provided_hooks()
    assert "sieve_ping" in ctx.tools


def test_hooks_never_persist_payloads(monkeypatch):
    """No event spy log: driving every hook with a sentinel must not write it anywhere.

    This is the blocking review item. Observers used to append hook args (and on
    `pre_gateway_dispatch` the raw inbound message) to docs/spike-events.jsonl.
    """
    mod = _load_plugin_module()
    conn = sqlite3.connect(":memory:")
    mod.queue.init_schema(conn)
    monkeypatch.setattr(mod.queue, "db", lambda: conn)
    monkeypatch.setattr(mod.delivery, "send", lambda *a, **k: True)
    ctx = _FakeCtx()
    mod.register(ctx)
    sentinel = "SENTINEL-INBOUND-TEXT-9f2c"
    for name, fn in ctx.hooks.items():
        try:
            fn(sentinel, event=sentinel, prompt=sentinel, summary=sentinel,
               text=sentinel, kind="needs_human")
        except Exception:
            pass
    leaked = []
    for path in PLUGIN_DIR.rglob("*"):
        if ".git" in path.parts or not path.is_file():
            continue
        if path.suffix not in {".jsonl", ".json", ".log", ".txt"}:
            continue
        try:
            if sentinel in path.read_text(errors="ignore"):
                leaked.append(path)
        except OSError:
            continue
    assert leaked == [], f"hook payload persisted to {leaked}"
    assert not hasattr(mod, "SPIKE_EVENTS")


def test_policy_path_stays_inside_plugin_data_dir(monkeypatch, tmp_path):
    mod = _load_plugin_module()
    monkeypatch.setattr(mod, "_policy_dir", lambda: tmp_path)
    assert mod._policy_path() == (tmp_path / "policy.json").resolve()
    assert mod._policy_path("mine.json") == (tmp_path / "mine.json").resolve()
    with pytest.raises(ValueError):
        mod._policy_path("../escape.json")
    with pytest.raises(ValueError):
        mod._policy_path("/etc/passwd")
