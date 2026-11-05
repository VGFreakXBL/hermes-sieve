"""SIEVE — notification boundary for Hermes Agent.

Hackathon scaffold: sieve_ping tool + log-only seam spikes (Task 1.2).
All spike hooks are OBSERVERS (return values ignored) — they never block,
never modify, never route around the approval system (catalog rule 12).
"""

import json
import time
from pathlib import Path

_SPIKE_LOG = Path(__file__).parent / "docs" / "spike-events.jsonl"

# Payload keys that may carry secrets/answers — never written to the spike log.
_REDACT_KEYS = {"command", "prompt", "password", "answer", "content", "text"}


def _spike(event, args, kwargs):
    rec = {"ts": time.time(), "event": event, "args": [str(a)[:200] for a in args]}
    rec.update({k: str(v)[:200] for k, v in kwargs.items() if k not in _REDACT_KEYS})
    try:
        with _SPIKE_LOG.open("a") as f:
            f.write(json.dumps(rec, default=str) + "\n")
    except Exception:
        pass


def _make_spiker(event):
    def spiker(*args, **kwargs):
        _spike(event, args, kwargs)
    return spiker


# Seam-recon spike targets: confirmed present in hermes_cli.plugins.VALID_HOOKS.
# Purpose: capture real fire timing + payload shape for SIEVE's six event classes.
SPIKE_EVENTS = (
    "pre_gateway_dispatch",      # candidate outbound-message seam (UNVERIFIED — top spike priority)
    "on_human_input_request",    # needs-human gates (kind values in docs/seams.md) (CONFIRMED)
    "on_human_input_resolved",   # gate outcomes (CONFIRMED)
    "pre_approval_request",      # before prompted/smart approval (CONFIRMED)
    "post_approval_response",    # after decision/timeout (CONFIRMED)
    "kanban_task_blocked",       # card blocked on a person (CONFIRMED in hook list)
    "subagent_stop",             # background/subagent completion (CONFIRMED in hook list)
    "on_session_end",            # cron completion fires this too (per plugins/AGENTS.md)
    "on_interim_message",        # candidate for agent-initiated message flow
)


def register(ctx):
    # --- Tool: sieve_ping (health probe) ---
    schema = {
        "name": "sieve_ping",
        "description": "Health probe for the SIEVE plugin. Returns plugin status.",
        "parameters": {"type": "object", "properties": {}, "required": []},
    }

    def handle_ping(params, **kwargs):
        del params, kwargs
        return json.dumps({"success": True, "plugin": "sieve", "version": "0.1.0"})

    ctx.register_tool(name="sieve_ping", toolset="sieve", schema=schema, handler=handle_ping)

    # --- Seam spikes (Task 1.2): log-only observers ---
    for event in SPIKE_EVENTS:
        ctx.register_hook(event, _make_spiker(event))
