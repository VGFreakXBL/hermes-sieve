# SIEVE

A user-owned notification boundary for Hermes Agent: event classes × escalation tiers (T0 silent digest → T5 siren through Silent+Focus) × quiet hours with breach floors. Context-aware via Device Access (location/calendar, grant-gated); escalates via mobile alarms (push-gated opt-in).

Status: under construction for the Hermes Plugin Hackathon. Disclosures and screenshots land here before the catalog PR.

### Two axes: what you asked for, and what actually carried it

A tier is an **intent**. An arm is the **primitive that executed it**. SIEVE
reports both, always, in the same message:

```
SIEVE [service_down] T5 siren (through Silent+Focus)
arm: phone alarm (rings through Silent + Focus) [verified]
```

When the alarm arm is unavailable, the requested tier is not demoted. It stays
T5, and the message names the standing it reached instead:

```
SIEVE [service_down] T5 siren (through Silent+Focus)
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

| Tier | Intent | Requires |
|---|---|---|
| T0 | silent, digest only | nothing |
| T1 | desktop chip, no message | nothing |
| T2 | one notification | a configured delivery target |
| T3 | interrupt now | a configured delivery target |
| T4 | pleasant alarm (timer chime) | phone synced to this profile |
| T5 | siren through Silent + Focus | phone synced to this profile |

T4/T5 need the mobile arm: the phone must be reachable from this profile and a
device write must be readable back. When that arm is unavailable, SIEVE does
**not** drop the alert and does **not** claim it fired an alarm. It sends the
event at T3 with an explicit `[escalation degraded: ...]` reason, records the
same reason in state, and reports `verified: false`.

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
```

## License

MIT
