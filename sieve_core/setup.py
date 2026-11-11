"""Chat-side calibration. An agent can set the whole matrix without the pane.

`/sieve setup` walks one question at a time. `/sieve setup apply` takes the
same answers in one line. `/sieve matrix set` writes any single cell.
"""

from sieve_core import matrix, queue

CLASSES = [cls for (cls, _match, _tier, _label) in matrix.DEFAULT_RULES]

STEPS = ("always", "sleep", "floor", "arm", "digest", "daytime")

QUESTIONS = {
    "always": (
        "Which alerts should reach you no matter the hour? "
        "These become T5, so a quiet window cannot hold them unless its floor is none. "
        "A ring through Silent only happens when the Hermes iOS app is open and synced to this profile. "
        "No phone, or the app closed: T5 stays on an in-app desktop toast plus the chat chain, and it does not ring. "
        "A closed app cannot do that. "
        f"Classes: {', '.join(CLASSES)}. "
        "Reply with a comma list, or none."
    ),
    "sleep": (
        "What hours are sleep / do not disturb? "
        "Example: 23:00-08:00. Reply none to skip a sleep window."
    ),
    "floor": (
        "During that window, what may still break through? "
        "T4 is a 60-second timer and T5 is an alarm one minute out. They share one tone. "
        "T4 means those timers still fire. T5 means only the always-list. "
        "none means silence everything, including alarms. Example: T5."
    ),
    "arm": (
        "Should T4 and T5 try the phone on this machine? "
        "A ring only lands if the Hermes iOS app is open and synced, and a phone is registered. "
        "Reply yes or no. Example: no. "
        "no keeps them on an in-app desktop toast plus the chat chain. "
        "Run /sieve doctor to see whether hermes send and a synced phone are actually here."
    ),
    "digest": (
        "Where should held digests be delivered? "
        "T1 is a desktop chip, not a message. This sets the chain for held items. "
        "Examples: telegram, discord:#ops, slack:C012ABCD, photon:+15555550100."
    ),
    "daytime": (
        "For classes you did not mark as always-on, what daytime tier? "
        "T0 digest, T1 chip, T2 notify, or T3 interrupt now."
    ),
}


def _step(conn) -> str:
    raw = queue.kv_get(conn, "setup_step") or "always"
    return raw if raw in STEPS or raw == "done" else "always"


def _set_step(conn, step: str) -> None:
    queue.kv_set(conn, "setup_step", step)


def _next(step: str) -> str:
    if step not in STEPS:
        return "done"
    i = STEPS.index(step) + 1
    return STEPS[i] if i < len(STEPS) else "done"


def _parse_tier(token: str) -> int:
    raw = token.lower().lstrip("t")
    if raw == "none":
        return 6
    tier = int(raw)
    if not 0 <= tier <= 6:
        raise ValueError("tier must be T0-T5, or none")
    return tier


def _classes(raw: str) -> list:
    if not raw or raw.lower() in ("none", "skip", "-"):
        return []
    out = []
    for part in raw.split(","):
        name = part.strip()
        if not name:
            continue
        if name not in CLASSES:
            raise ValueError(f"unknown class {name}. Known: {', '.join(CLASSES)}")
        out.append(name)
    return out


def apply_always(conn, raw: str) -> str:
    names = _classes(raw)
    for name in names:
        queue.add_rule(conn, name, {}, 5, label=f"{name} always-on (setup)", source="user")
    queue.kv_set(conn, "setup_always", ",".join(names))
    shown = ", ".join(names) if names else "none"
    return f"always-on T5: {shown}"


def apply_sleep(conn, raw: str) -> str:
    spec = raw.strip()
    conn.execute("DELETE FROM quiet_windows WHERE label = 'sleep'")
    conn.commit()
    if spec.lower() in ("none", "skip", "-"):
        queue.kv_set(conn, "setup_sleep", "")
        return "no sleep window"
    if "-" not in spec:
        raise ValueError("sleep window must look like 23:00-08:00, or none")
    start, end = spec.split("-", 1)
    floor = 4
    saved = queue.kv_get(conn, "setup_floor")
    if saved:
        floor = _parse_tier(saved)
    queue.add_window(conn, start, end, "0123456", floor, label="sleep")
    queue.kv_set(conn, "setup_sleep", spec)
    return f"sleep window {spec} floor=T{floor}"


def apply_floor(conn, raw: str) -> str:
    floor = _parse_tier(raw)
    queue.kv_set(conn, "setup_floor", "none" if floor == 6 else f"T{floor}")
    spec = queue.kv_get(conn, "setup_sleep") or ""
    if not spec:
        return f"floor remembered as T{floor}. No sleep window yet, so nothing changed."
    start, end = spec.split("-", 1)
    conn.execute("DELETE FROM quiet_windows WHERE label = 'sleep'")
    conn.commit()
    queue.add_window(conn, start, end, "0123456", floor, label="sleep")
    return f"sleep floor set to T{floor}"


def apply_digest(conn, raw: str) -> str:
    from sieve_core import delivery
    chain = delivery.parse_chain(raw.replace(" ", ""))
    if not chain:
        raise ValueError("digest target is empty")
    queue.kv_set(conn, "target", ",".join(chain))
    return f"digest chain: {', '.join(chain)}"


def apply_daytime(conn, raw: str) -> str:
    tier = _parse_tier(raw)
    if tier > 3:
        raise ValueError("daytime tier must be T0-T3. Use the always question for T5.")
    always = set(_classes(queue.kv_get(conn, "setup_always") or "none"))
    touched = []
    for name in CLASSES:
        if name in always:
            continue
        queue.add_rule(conn, name, {}, tier, label=f"{name} daytime (setup)", source="user")
        touched.append(name)
    shown = ", ".join(touched) if touched else "none"
    return f"daytime T{tier}: {shown}"


def apply_arm(conn, raw: str) -> str:
    mode = (raw or "").strip().lower()
    if mode not in ("yes", "no"):
        raise ValueError("arm must be yes or no. Example: no")
    queue.kv_set(conn, "phone_arm", "no" if mode == "no" else "yes")
    if mode == "no":
        return "phone arm off. T4/T5 use an in-app desktop toast plus the chat chain. No ring."
    return "phone arm on. T4/T5 still try the phone, and say so when they cannot."


_APPLY = {
    "always": apply_always,
    "sleep": apply_sleep,
    "floor": apply_floor,
    "arm": apply_arm,
    "digest": apply_digest,
    "daytime": apply_daytime,
}


def question(step: str) -> str:
    if step == "done":
        return ("Setup is complete. Change any cell with /sieve matrix set <class> T<n> [context=name]. "
                "One more thing, outside the plugin: the Tailscale and gateway watchdog does not start "
                "itself. To catch a link drop while Desktop is closed, load it once in a terminal: "
                "launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.vgfreak.sieve-watch.plist "
                "(run it yourself; the agent cannot). It still cannot see an outage while the Mac is asleep.")
    return QUESTIONS[step]


def show() -> str:
    lines = [
        "SIEVE setup. An agent can answer these without opening the pane.",
        "Walk: /sieve setup, then /sieve setup <answer> for each question.",
        "One shot: /sieve setup apply always=needs_human,service_down sleep=23:00-08:00 floor=T5 arm=no digest=telegram daytime=T2",
        "Any cell: /sieve matrix set world_message T0 context=sleeping",
        "Watchdog (opt-in, not started by the plugin): launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.vgfreak.sieve-watch.plist",
    ]
    for step in STEPS:
        lines.append(f"- {step}: {QUESTIONS[step]}")
    return "\n".join(lines)


def handle(conn, rest) -> str:
    if not rest or rest[0] in ("ask", "?"):
        step = _step(conn)
        return f"[{step}] {question(step)}\nAnswer with: /sieve setup <answer>"
    head = rest[0].lower()
    if head == "show":
        return show()
    if head == "reset":
        _set_step(conn, "always")
        return f"[always] {QUESTIONS['always']}"
    if head == "apply":
        if len(rest) < 2:
            return "usage: /sieve setup apply always=needs_human sleep=23:00-08:00 floor=T5 arm=no digest=telegram daytime=T2"
        lines = []
        for tok in rest[1:]:
            if "=" not in tok:
                raise ValueError(f"expected key=value, got {tok}")
            key, val = tok.split("=", 1)
            key = key.lower()
            if key not in _APPLY:
                raise ValueError(f"unknown setup key {key}")
            lines.append(_APPLY[key](conn, val))
        _set_step(conn, "done")
        return "applied:\n" + "\n".join(lines)
    step = _step(conn)
    if step == "done":
        return question("done")
    applied = _APPLY[step](conn, " ".join(rest))
    nxt = _next(step)
    _set_step(conn, nxt)
    return f"{applied}\n[{nxt}] {question(nxt)}"


def matrix_lines(conn) -> str:
    lines = ["SIEVE matrix (base context, user rules beat defaults):"]
    for cls in CLASSES:
        decision = matrix.decide(cls, {"context": "base"}, queue.rules_for(conn, cls))
        lines.append(f"  {cls}: T{decision.tier} ({decision.source})")
    return "\n".join(lines)


def matrix_set(conn, rest) -> str:
    if len(rest) < 2:
        return "usage: /sieve matrix set <class> T<n> [context=name]"
    cls, tier_tok = rest[0], rest[1]
    if cls not in CLASSES:
        return f"unknown class {cls}. Known: {', '.join(CLASSES)}"
    tier = _parse_tier(tier_tok)
    if tier > 5:
        return "a matrix cell is T0-T5. Use /sieve quiet floor=none to silence alarms."
    context = ""
    for tok in rest[2:]:
        if tok.lower().startswith("context="):
            context = tok.split("=", 1)[1]
    if context:
        queue.set_context_rule(conn, cls, context, tier)
        return f"{cls} in {context}: T{tier}"
    queue.add_rule(conn, cls, {}, tier, label=f"{cls} (matrix set)", source="user")
    return f"{cls}: T{tier}"
