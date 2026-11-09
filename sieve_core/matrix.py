"""Boundary matrix: event class + payload -> tier, with attribution.

User rules (stored in plugin_db) beat seeded defaults; the decision always
carries the rule that fired (the `sieve why` contract).
"""

from dataclasses import dataclass

TIERS = {
    0: "silent (digest only)",
    1: "chip (desktop badge)",
    2: "notify (normal notification)",
    3: "interrupt (time-sensitive push)",
    4: "alarm",
    5: "alarm, top priority (push arm reserved)",
}

# Policy contexts: the matrix is event class x context -> tier. A context rule
# (match {"context": "<name>"}) overrides the base rule for that class when the
# named context is active. Detection sources vary (clock, calendar, location,
# screen-share, Health, Home Assistant, manual); "base" is always the fallback.
CONTEXTS = [
    ("base", "always"),
    ("after_hours", "quiet window hours"),
    ("meeting", "calendar shows busy"),
    ("commuting", "location is moving"),
    ("vacation", "all-day PTO event"),
    ("streaming", "fullscreen or screen-share"),
    ("sleeping", "Health sleep or manual"),
    ("deep_work", "manual focus session"),
]

# Seeded defaults (the "matrix starter pack"). User rules override these.
DEFAULT_RULES = [
    ("needs_human", {}, 4, "needs-human gate -> pleasant alarm (default)"),
    ("service_down", {}, 5, "watchdog outage -> siren (default)"),
    ("task_failed", {}, 2, "failed background run -> notify (default)"),
    ("task_done", {}, 0, "background run finished -> digest (default)"),
    ("cron_done", {}, 0, "cron result -> digest (default)"),
    ("agent_message", {}, 2, "agent-initiated message -> notify (default)"),
    ("world_message", {}, 2, "inbound message -> notify (default)"),
    ("approval_activity", {}, 0, "approval telemetry -> log only (default)"),
]


@dataclass
class Decision:
    tier: int
    rule_id: object  # int for user rules, str like "default:needs_human", or None
    label: str
    source: str  # 'user' | 'default' | 'fallback'


def _matches(match: dict, payload: dict) -> bool:
    """Predicate match: channel/sender exact-ish, keyword substring on any text value."""
    for key, want in match.items():
        if key == "keyword":
            hay = " ".join(str(v) for v in payload.values()).lower()
            if str(want).lower() not in hay:
                return False
        else:
            got = str(payload.get(key, "")).lower()
            if str(want).lower() not in got:
                return False
    return True


def decide(event_class: str, payload: dict, user_rules) -> Decision:
    """user_rules: rows from queue.rules_for() -> (id, event_class, match_json, tier, source, label)."""
    import json
    best = None
    for (rid, _cls, match_json, tier, source, label) in user_rules:
        try:
            match = json.loads(match_json or "{}")
        except Exception:
            match = {}
        if _matches(match, payload):
            specificity = len(match)
            if best is None or specificity > best[0]:
                best = (specificity, Decision(tier=tier, rule_id=rid, label=label or f"user rule #{rid}", source=source or "user"))
    if best:
        return best[1]

    for (cls, match, tier, label) in DEFAULT_RULES:
        if cls == event_class and _matches(match, payload):
            return Decision(tier=tier, rule_id=f"default:{cls}", label=label, source="default")

    return Decision(tier=2, rule_id=None, label="no rule matched -> notify (fallback)", source="fallback")
