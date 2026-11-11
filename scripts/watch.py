#!/usr/bin/env python3
"""Local watchdog. No model. No Hermes cron.

Hermes cron dies with the gateway, so it cannot watch the gateway. This
process calls the SIEVE route directly. It does not emit a host hook: a dead
gateway has no hook table to call.

Tailscale down and gateway down are separate edges. A missing binary, a
timeout, and the first reading are not an outage. The phone alarm arm needs
the gateway, so a dead gateway degrades to the chat chain via `hermes send`,
which does not need a model or a running gateway.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
AGENT = Path.home() / ".hermes" / "hermes-agent"
for p in (str(ROOT), str(AGENT)):
    if p not in sys.path:
        sys.path.insert(0, p)


def main() -> int:
    import importlib.util

    from sieve_core import probe, queue

    spec = importlib.util.spec_from_file_location("sieve_watch_plugin", ROOT / "__init__.py")
    if spec is None or spec.loader is None:
        raise RuntimeError("sieve plugin module could not be loaded")
    plugin = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(plugin)
    conn = queue.db()
    home = Path.home() / ".hermes"
    tail = probe.observe(conn, plugin._safe_route)
    gate = probe.observe_gateway(conn, plugin._safe_route, home=home)
    if tail.get("fired") or gate.get("fired"):
        print(f"sieve-watch fired tailscale={bool(tail.get('fired'))} gateway={bool(gate.get('fired'))}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"sieve-watch error: {exc}", file=sys.stderr)
        raise SystemExit(1)
