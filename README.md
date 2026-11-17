# SIEVE

A user-owned notification boundary for Hermes Agent: event classes × escalation tiers (T0 silent digest → T5 top-priority alarm) × quiet hours with breach floors. Context slots such as meeting or sleeping are set by you (`/sieve context`), not read from location or calendar. T5 tries a phone alarm only when the Hermes iOS app is open and synced. Otherwise it stays on an in-app desktop toast plus the chat chain you set.

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
| T2 | one notification | messaging push | a delivery target, else telegram |
| T3 | interrupt now | messaging push | a delivery target, else telegram |
| T4 | alarm | phone alarm (timer) | phone synced to this profile |
| T5 | alarm, maximum priority | phone alarm (scheduled) | phone synced to this profile |

**T4 is a timer. T5 is an alarm.** T4 calls `mobile_timer_start` for 60 seconds. T5 calls `mobile_alarm_create` one minute out. Neither call accepts a sound, volume, or critical-alert parameter, so the two cannot differ in tone. T5 means top priority in policy, not a louder sound. Closed-app wake is not in this build. Do not describe T5 as a siren.

`/sieve arm no` keeps T4 and T5 on the desktop toast and the chat chain. `/sieve doctor` reports whether `hermes send` and a synced phone are actually present. `/sieve budget N` caps T2 and T3 interrupts per hour. `/sieve promote N` raises a held item one tier after N minutes. A T2 class re-alerts at most once per 6 hours unless you change that limit.

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
/sieve export [path]                 # writes <plugin-data>/sieve/policy.json by default
/sieve import [path]                 # merge into this profile (default file if omitted)
/sieve import [path] replace         # replace this profile's user policy first
```

Policy paths are confined to this profile's own plugin data directory:
`<hermes home>/plugin-data/sieve/`. A relative path resolves there, the default
is `policy.json`, and a path that escapes the directory is refused. To move
policy between profiles, export on the profile that has it right, copy
`plugin-data/sieve/policy.json` into the other profile's
`plugin-data/sieve/` directory, then run `/sieve import` there.

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

## Setup by archetype

Run `/sieve doctor` from **Discord or Telegram** first — the Desktop pane
cannot write the phone by design, and its phone answers say so. Doctor probes
your setup and prints a reachable fix for anything missing. Then match what it
found:

- **Phone paired to this profile, Hermes app open and synced** — the full
  ladder. `arm=yes`; `/sieve test T4` then `/sieve test T5` verifies the ring
  (the alarm fires about a minute out; a verified read-back clears any
  escalation-degraded stamp). T5 is top priority with the same tone as T4.
- **Hermes app closed, or the device shows stale** — writes queue until the
  app opens, and T4/T5 degrade to the chat chain (`[escalation degraded: ...]`
  says so in the message). The fix is opening the app. There is no push to
  configure: closed-app wake is a known limit of the current mobile build.
- **No phone, or paired to another profile** — a declared no-phone profile, not
  a broken one. `arm=no`; T4/T5 stay on the in-app desktop toast plus the chat
  chain. Pairing follows whichever profile completed the app sign-in.
- **Desktop pane** — policy editing, status, and the matrix work everywhere.
  `/sieve doctor` and `/sieve test` need a chat surface for their phone parts.

Nothing in this build may claim: closed-app reach, Focus break-through outside
the alarm arm, a siren, critical alerts, or custom sounds. A messaging push
(Telegram/iMessage/Discord) is an ordinary notification: Focus can silence it,
and it never rings.

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
to state. The result reports `verified: false`. The Desktop pane cannot run this
check. Desktop chat may refuse `/sieve` with `permission_denied` before the plugin
starts. A Discord or Telegram `/sieve doctor` is the check that can see the phone,
and that result is kept for the pane.

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
then: it calls the same route a hook would have called, with no model and no
Hermes cron, so a dead gateway cannot ring the phone but `hermes send` still
carries the chat chain. **It does not start itself.** A shipped plugin cannot
register a launchd job, and the plist is a template so it does not carry one
machine's absolute paths. Render and install it once, from a terminal in the
plugin directory:

```
bash scripts/install-watchdog.sh    # renders the plist into ~/Library/LaunchAgents
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.vgfreak.sieve-watch.plist
```

The template at `scripts/com.vgfreak.sieve-watch.plist.template` runs the
watcher every 120 seconds. The install script substitutes `$HOME` and
`$HERMES_HOME`; `scripts/watch.py` resolves the Hermes home the same way, so a
non-default profile root works without editing the script.
An unloaded watcher is why a real Tailscale drop can pass unnoticed. Its own
limit: `StartInterval` only fires while the Mac is awake, so an outage that
coincides with the Mac sleeping is still unobserved. The plist also carries
`WatchPaths` on the network config so a wake or reconnect re-runs the probe,
which narrows that window for a traveling machine, though only a real sleep
cycle proves it. When a probe gap happens, SIEVE does **not** claim the link
stayed up: `/sieve status` reports the gap and that the link state for it is
unknown, because a drop and recovery across a sleep leaves no edge to catch. A dead gateway cannot ring
the phone. `hermes send` still carries the chat chain, and it does not need
the gateway process. A digest uses the global chain, else any per-tier chain
you named, else telegram. A T4/T5 or a notice uses its per-tier chain first,
else the global chain, else telegram. It does not hunt platforms
you did not name. A failed send keeps the hold and `/sieve status` records
which legs failed. SIEVE does not read `.env`,
`auth.json`, the credential store, or another profile's data, and it has no
`os.environ` access. The files it writes are the SQLite database above and a
policy file when you run `/sieve export`, both inside this profile's
`plugin-data/sieve/` directory. **It writes no hook log.** No event payload,
message text, prompt, or command is copied to disk by this plugin: the
seam-recon observer log that earlier builds kept at
`docs/spike-events.jsonl` is removed, and `pre_gateway_dispatch` is no longer
subscribed at all, so inbound chat never reaches SIEVE.

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