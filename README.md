# SIEVE

A user-owned notification boundary for Hermes Agent: event classes × escalation tiers (T0 silent digest → T5 siren through Silent+Focus) × quiet hours with breach floors. Context-aware via Device Access (location/calendar, grant-gated); escalates via mobile alarms (push-gated opt-in).

Status: under construction for the Hermes Plugin Hackathon. Disclosures and screenshots land here before the catalog PR.

### Two axes: what you asked for, and what actually carried it

A tier is an **intent**. An arm is the **primitive that executed it**. SIEVE
reports both, always, in the same message:

```
SIEVE [service_down] T5 top rung: alarm (same tone as T4 today)
arm: phone alarm (rings through Silent + Focus) [verified]
```

When the alarm arm is unavailable, the requested tier is not demoted. It stays
T5, and the message names the standing it reached instead:

```
SIEVE [service_down] T5 top rung: alarm (same tone as T4 today)
arm: messaging push — max interrupt available (alarm arm unavailable)
[escalation degraded: device stale (no recent sync; open the Hermes app)]
```

Three states, not two:

| State | Tier line | Arm line |
|---|---|---|
| full | T5 siren | `phone alarm ... [verified]` |
| accepted but unconfirmed | T5 siren | `phone alarm ... [write unverified]` |
| degraded | T5 siren | `messaging push — max interrupt available (alarm unavailable)` |

The reason for the split: relabelling T5 as "max interrupt" destroys the
distinction the ladder exists to make, and silently demoting to T3 destroys the
user's stated priority. Keeping both facts lets the alert read as *this was
siren-class urgency, delivered by the strongest arm currently standing.*

That is the middle ground. "Best available arm" is the framing; the degrade note
is the receipt.

## The escalation ladder, and what each rung can physically do

SIEVE's job is not to deliver a message. It is to decide how hard to hit the
user, and to be honest about which of those hits actually landed.

| Tier | Intent | Primitive | Requires |
|---|---|---|---|
| T0 | silent, digest only | none | nothing |
| T1 | desktop chip, no message | chip | nothing |
| T2 | one notification | messaging push | a configured delivery target |
| T3 | interrupt now | messaging push | a configured delivery target |
| T4 | alarm | phone alarm (timer) | phone synced to this profile |
| T5 | alarm, maximum priority | phone alarm (scheduled) | phone synced to this profile |

**T4 and T5 are the same physical arm today.** The device API takes a label and a
time; it has no sound, volume, or critical-alert parameter, so both rungs ring
with the same tone. T5 differs only in intent and in how the policy uses it. Do
not describe T5 as a siren until the arm can actually vary its output.

The planned T5 differentiation is persistence, not loudness: alarm, then a
bounded number of repeats until it is dismissed. Dismissal is observable (a
fired-and-dismissed alarm leaves `mobile_alarms_list`), so the acknowledgement is
the natural gesture rather than a command the user has to learn. It stays
**bounded** on purpose: if the dismissal signal ever fails, the cost is one extra
ring, not an alarm that will not stop.

T4/T5 need the mobile arm: the phone must be reachable from this profile and a
device write must be readable back. When that arm is unavailable, SIEVE does
**not** drop the alert and does **not** claim it fired an alarm. It sends the
event through the tier's chain with an explicit `[escalation degraded: ...]`
reason, records the same reason in state, and reports `verified: false`.

That distinction is the point. A notification that arrives is not the same thing
as an alarm that rings, and a boundary tool that blurs them is worse than no
boundary at all.

### Target chains (delivery while the alarm arm is unavailable)

Delivery is per tier, and each tier can carry a chain:

```
/sieve target telegram                       # global default
/sieve target t4 telegram,photon:any;-;+155****4567
/sieve target t5 telegram,photon:any;-;+155****4567
/sieve target t5 clear                       # fall back to the global target
/sieve target                                # show global + per-tier chains
```

A chain is redundant delivery, not failover. Every target is attempted and the
event counts as delivered if at least one succeeded. For T4/T5 that is
deliberate: reaching the user on either of two independent pipes is worth a
duplicate, and the result reports which targets took it and which did not.

This is the supported answer for "the closed-app alarm arm is not available
yet": point the high tiers at the messaging platforms that already push to a
locked phone. Telegram and iMessage (via Photon) both wake a locked device as
ordinary notifications.

**What that does not buy you.** A messaging push is not a siren. It is silenced
by Focus unless the sender is allowed to break through, it can be muted per
conversation, and it does not ring. So a chain on T5 is a real interrupt but a
weaker class of interrupt than the mobile arm. SIEVE labels the tier it actually
achieved, never the tier you hoped for. When the phone arm is live, T5 is a
siren; when it is not, T5 is a loud notification carrying a degrade note.

### Moving policy between profiles

Policy travels as a file, and the user moves it:

```
/sieve export [path]                 # writes ~/.hermes/sieve-policy.json by default
/sieve import <path>                 # merge into this profile
/sieve import <path> replace         # replace this profile's user policy first
```

The file carries user rules only. Seeded defaults live in code, so importing a
policy never freezes a stale copy of them. Operational state stays behind:
`paused_until` and `escalation_degraded` describe this machine right now, not
policy, and are refused if present in the file.

**There is deliberately no cross-profile write.** A plugin running in one profile
does not reach into another profile's store to install policy. Profiles are a
boundary, and a boundary tool that violates one has no standing to enforce
anything. Export on the profile that has it right, import in the profile that
should own it, one command each.

Import validates the whole file before writing anything, so a malformed policy is
refused instead of half-applied.

## Install (once cataloged)

```
hermes plugins install sieve
hermes plugins enable sieve
```

## Dev

```
hermes plugins doctor ~/.hermes/plugins/sieve
hermes plugins validate ~/.hermes/plugins/sieve --install-deps
pytest ~/.hermes/plugins/sieve/tests -v
# drive every desktop route and every /sieve subcommand against a temp store
~/.hermes/hermes-agent/venv/bin/python ~/.hermes/plugins/sieve/scripts/click_pass.py
```

## License

MIT