"""Scaffold tests (Task 1.1) — no network, no live Hermes ctx needed."""

import importlib.util
import json
from pathlib import Path

PLUGIN_DIR = Path(__file__).resolve().parent.parent


def _load_plugin_module():
    spec = importlib.util.spec_from_file_location("sieve_plugin", PLUGIN_DIR / "__init__.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_plugin_yaml_exists_and_names_sieve():
    text = (PLUGIN_DIR / "plugin.yaml").read_text()
    assert "name: sieve" in text
    assert "version:" in text


def test_register_is_importable():
    mod = _load_plugin_module()
    assert hasattr(mod, "register")


def test_spiker_redacts_secret_keys(tmp_path):
    mod = _load_plugin_module()
    mod._SPIKE_LOG = tmp_path / "spike.jsonl"
    mod._spike("pre_approval_request", args=["rm -rf /"], kwargs={
        "command": "SECRET-COMMAND", "prompt": "SECRET-PROMPT", "surface": "gateway",
    })
    line = mod._SPIKE_LOG.read_text()
    assert "SECRET-COMMAND" not in line
    assert "SECRET-PROMPT" not in line
    assert "gateway" in line
    rec = json.loads(line)
    assert rec["event"] == "pre_approval_request"


def test_all_spike_events_registered_are_valid_hook_names():
    mod = _load_plugin_module()
    # Names observed in hermes_cli.plugins.VALID_HOOKS (docs/plugins.md list).
    valid = {
        "pre_gateway_dispatch", "on_human_input_request", "on_human_input_resolved",
        "pre_approval_request", "post_approval_response", "kanban_task_blocked",
        "subagent_stop", "on_session_end", "on_interim_message",
    }
    assert set(mod.SPIKE_EVENTS) <= valid
