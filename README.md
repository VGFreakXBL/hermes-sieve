# SIEVE

A user-owned notification boundary for Hermes Agent: event classes × escalation tiers (T0 silent digest → T5 top-priority alarm, same tone as T4 until the push arm ships) × quiet hours with breach floors. Context-aware via Device Access (location/calendar, grant-gated); escalates through the mobile alarm arm (push-gated opt-in) or a messaging chain.

Status: hackathon release candidate for the Hermes Plugin Hackathon. Screenshots are captured separately and are not in this tree yet. What this build does and does not do is listed under [Disclosures](#disclosures).

### Two axes: what you asked for, and what actually carried it

A tier is an **intent**. An arm is the **primitive that executed it**. Whenever
SIEVE sends a message, it reports both, in the same message:

```
SIEVE [service_down] T5 alarm, top priority (push arm reserved)
arm: phone alarm (rings through Silent + Focus) [verified]
service down
```

When the alarm arm is unavailable, the requested tier is not demoted. It stays
T5, and the message names the standing it reached instead:

```
SIEVE [service_down] T5 alarm, top priority (push arm reserved)
arm: messaging push — max interrupt available (alarm arm unavailable)
service down
[escalation degraded: device stale (no recent sync; open the Hermes app)]
```

Three states, not two:

| State | Tier line | Arm line |
|---|---|---|
| full | T5 top priority (push arm reserved) | `phone alarm ... [verified]` |
| accepted but unconfirmed | T5 top priority (push arm reserved) | `phone alarm ... [write unverified]` |
| degraded | T5 top priority (push arm reserved) | `messaging push — max interrupt available (alarm unavailable)` |

The reason for the split: relabelling T5 as "max interrupt" destroys the
distinction the ladder exists to make, and silently demoting to T3 destroys the
user's stated priority. Keeping both facts lets the alert read as *top-priority
urgency, delivered by the strongest arm currently standing.*

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

**T4 and T5 share the alarm arm today.** The device API takes a label and a time;
it has no sound, volume, or critical-alert parameter, so both rungs ring with the
same tone. T5 means top priority in policy, not a louder sound. T5 is the
reserved rung for a push-based arm: when the app exposes push that reaches a
closed app or a locked phone, T5 upgrades to it with no policy migration, because
the tier never changed, only the primitive. Do not describe T5 as a siren: no
parameter this arm accepts can produce one. A push is still an ordinary
notification unless it carries a Time Sensitive level or an Apple critical-alert
entitlement, and the arm label has to say which of those it achieved.

The reserved T5 differentiation is persistence, not loudness: alarm, then a
bounded number of repeats until it is dismissed, plus an explicit `/sieve ack`.
**That is not in this build.** Nothing in this release repeats an alert, and
`/sieve ack` does not exist yet. A T5 fires one scheduled alarm and stops. The
note stays here because it is the intended flip, and because it explains why T5
is reserved rather than a louder rung.

T4/T5 need the mobile arm: the phone must be registered to this profile and it
must have synced recently. A T5 alarm write is verified by reading the alarm list
back; an empty list falsifies the write. A T4 timer write is reported as accepted
by the device API, but this build does not read the timer back, so T4 is
unverified by construction. When the arm is unavailable (toolset missing, no
registered device, or no sync in 24 hours), SIEVE does **not** drop the alert and
does **not** claim it fired an alarm. It sends the event through the tier's chain
with an explicit `[escalation degraded: ...]` reason, records the same reason in
state, and reports `verified: false`.

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
yet": point the high tiers at the messaging platforms that already deliver to a
locked phone. Telegram and iMessage (via Photon) both arrive on a locked device
as ordinary notifications.

**What that does not buy you.** A messaging push is not an alarm. It is silenced
by Focus unless the sender is allowed to break through, it can be muted per
conversation, and it does not ring. So a chain on T5 is a real interrupt but a
weaker class of interrupt than the mobile arm. SIEVE states both the tier you set
and the arm that carried it, never just the tier you hoped for. With the phone arm
live, T5 rings the same tone as T4; without it, T5 is a notification carrying a
degrade note.

### Mutes are ceilings, not points

`/sieve mute T3 until 9am` silences T0 through T3. Nobody wants to mute each lower
tier separately, so one command quiets a band. A mute never covers a tier above
the one named: `mute T3` cannot silence a T4 alarm or a T5. `/sieve pause` is the
other shape: it holds everything, alarms included, until the deadline, and the
reply says so.

### Held is not dropped: the digest timer

Held items resurface on a timer, not only when the user remembers to ask.
`_maybe_digest` runs at session end (never mid-turn), sends one digest for
everything held, releases those items, and stamps `last_digest_at`.

```
/sieve digest            # show interval, held count, last send
/sieve digest 4           # every 4 hours
/sieve digest off         # only on /sieve flush
```

Default is every 8 hours. Without this, a T0 "digest by design" event could sit
forever, which would make "held never means dropped" a claim rather than a
behavior.

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

## Disclosures

Verified against this source tree at release-candidate time. Where a limit
exists, the limit is stated rather than rounded off.

**State and storage.** Policy and event history live in one SQLite database in
this profile's plugin data directory: `<hermes home>/plugin-data/sieve/data.db`,
opened through the host's `plugin_db("sieve")`. Tables: `rules`, `events`,
`holds`, `quiet_windows`, `mutes`, `kv`. There is no shared store and no
cross-profile database.

**What a T4/T5 fire writes on the phone.** Both go through the host's
`mobile_device` toolset. T5 calls `mobile_alarm_create` with the label
`SIEVE: <event class>` scheduled one minute out, then reads `mobile_alarms_list`
back and looks for that label: a matching entry is `[verified]`, no match is
`[write unverified]`. A refused create is not labeled as an alarm at all: the
tier stays, the arm line says messaging push, and the refusal reason is the
degrade note. T4 calls `mobile_timer_start` for 60 seconds with the same
label; the create is reported as accepted, and this build does not read the
timer back. A refused timer write takes the same degrade path.

**When the phone arm is unavailable.** Missing toolset, no registered device, or
no sync in 24 hours: the alert is not dropped. The requested tier stays as it is,
the message names the arm that actually ran (`messaging push — max interrupt
available`), carries `[escalation degraded: <reason>]`, and the reason is written
to state. The result reports `verified: false`.

**Same tone, and no closed-app wake.** The device API accepts a label and a time;
it has no sound, volume, or critical-alert parameter, so T4 and T5 ring
identically. Closed-app wake is not available in the current official iOS beta,
and this build does not claim it. Messaging push is a weaker class in the same
breath: an ordinary notification, silenced by Focus unless the sender is allowed
through, mutable per conversation, and it does not ring.

**No model call.** Classification is user rules, then seeded defaults, then a
fixed T2 fallback. `ctx.llm` is never used and no `/sieve` subcommand invokes a
model. (`sieve_core/classify.py` is an unreferenced stub: it is imported at
module load and never called.)

**Network, shell, files.** SIEVE makes no HTTP requests of its own. Delivery
shells out to the local `hermes send` CLI via `subprocess.run`; that CLI carries
the platform credentials and the network call. The desktop status poll and
`/sieve probe` also run `tailscale status --json` (3 second timeout). That is
local IPC to the Tailscale daemon, not an HTTP call from SIEVE. A transition
from up to down routes `service_down` once. A missing binary, a timeout, and
the first reading are not an outage. The probe does not run when Desktop is
closed or the Mac is asleep. `scripts/watch.py` is the path that still runs
then: launchd starts it every 120 seconds, with no model and no Hermes cron.
It calls the same route a hook would have called. It does not emit a host
hook, because a dead gateway has no hook table. A dead gateway cannot ring
the phone. `hermes send` still carries the chat chain, and it does not need
the gateway process. A digest, a notice, and a
degraded T4/T5 all use the chain you set (`telegram`, `discord:#ops`,
`slack:C012`, `photon:...`). If that chain is empty, SIEVE uses any per-tier
chain you already named, and only then `telegram`. It does not hunt platforms
you did not name. A failed send keeps the hold and `/sieve status` records
which legs failed. SIEVE does not read `.env`,
`auth.json`, the credential store, or another profile's data, and it has no
`os.environ` access. The files it writes are the SQLite database above, a policy
file when you run `/sieve export`, and a local observe-only hook log at
`docs/spike-events.jsonl` inside the plugin directory (gitignored, so it is not
part of the published tree). That log records the hook
name plus string arguments truncated to 200 characters; the kwargs `command`,
`prompt`, `password`, `answer`, `content`, and `text` are redacted, while values
passed positionally are truncated but not key-filtered.

**No telemetry.** Nothing is uploaded. There is no analytics, crash, or usage
reporting anywhere in this tree.

**Export/import carries policy only.** `paused_until` and `escalation_degraded`
are refused by name: export omits them, and an import file containing either key
is rejected whole. Seeded defaults live in code and never travel. Import
validates the entire file before writing any of it.

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