"""Delivery arm: gateway/platform messages via the `hermes send` CLI.

`hermes send` reuses the gateway's platform credentials and needs no LLM and no
running gateway for bot-token platforms — the documented, catalog-safe delivery
path (rule-compliant: no credentials touch our code).
"""

import subprocess


def send(text: str, target: str = None, timeout: int = 30) -> bool:
    """Send text to a platform target ('telegram', 'discord:#digests', 'hermes_app:inbox', ...).
    Returns True on success. Never raises."""
    if not text:
        return False
    cmd = ["hermes", "send"]
    if target:
        cmd += ["--to", target]
    cmd.append(text)
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return proc.returncode == 0
    except Exception:
        return False


def parse_chain(raw) -> list:
    """'telegram, photon:+15551234567' -> ['telegram', 'photon:+15551234567']."""
    if not raw or not isinstance(raw, str):
        return []
    return [p.strip() for p in raw.split(",") if p.strip()]


def send_chain(text: str, targets) -> dict:
    """Send to every target in the chain. Success = at least one delivered.

    Redundant on purpose: a T5 that reaches two independent pipes is worth the
    duplicate. Returns {'delivered': bool, 'ok': [...], 'failed': [...]}.
    """
    targets = [t for t in targets if t]
    if not targets or not text:
        return {"delivered": False, "ok": [], "failed": targets or []}
    ok, failed = [], []
    for t in targets:
        (ok if send(text, t) else failed).append(t)
    return {"delivered": bool(ok), "ok": ok, "failed": failed}


def format_digest(held_rows) -> str:
    """held_rows: queue.held_events() -> (id, ts, event_class, tier, payload_json)."""
    if not held_rows:
        return ""
    by_class = {}
    for (_id, ts, event_class, tier, _payload) in held_rows:
        by_class.setdefault(event_class, []).append((ts, tier))
    lines = [f"SIEVE digest — {len(held_rows)} held item(s)"]
    for event_class, items in sorted(by_class.items()):
        tiers = sorted({t for _ts, t in items})
        lines.append(f"  {event_class}: {len(items)} (T{'/T'.join(str(t) for t in tiers)})")
    lines.append("Reply `/sieve why` for details or `/sieve flush` to release now.")
    return "\n".join(lines)


def format_event(event_class: str, tier: int, label: str, detail: str = "") -> str:
    from .matrix import TIERS
    head = f"SIEVE [{event_class}] T{tier} {TIERS.get(tier, '')}"
    body = f"{head}\n{label}"
    if detail:
        body += f"\n{detail[:300]}"
    return body
