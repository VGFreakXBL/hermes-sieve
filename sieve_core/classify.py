"""Model fallback classifier (SOFT TASK — off by default).

Rules-first is the design: the boundary is user-owned config. This bounded
classifier only sorts the UNCATEGORIZED remainder, and only into T0/T2 — it can
never raise an alarm (fail-safe on the interrupt axis). Enable with:
    /sieve llm_fallback on
"""

PROMPT = (
    "You sort one agent-notification event for a personal attention policy. "
    "Event class: {event_class}. Summary: {summary}\n"
    "Reply with exactly: T0 (routine, digest is fine) or T2 (worth a normal "
    "notification now) and one word for why, separated by a space."
)


def classify(dispatch_llm, event_class: str, summary: str, timeout: int = 20):
    """Returns (tier in {0,2}, reason) or None on any failure (caller then uses T2)."""
    if dispatch_llm is None:
        return None
    try:
        raw = dispatch_llm(PROMPT.format(event_class=event_class, summary=summary[:400]))
        raw = str(raw).strip().upper()
        if raw.startswith("T0"):
            return 0, raw[:60]
        if raw.startswith("T2"):
            return 2, raw[:60]
    except Exception:
        pass
    return None
