# SIEVE matrix design: parameters and roadmap

The matrix is event class x CONTEXT -> tier, plus dynamics. This file is the
design record for the dashboard and the parameter space behind it.

## Locked in the dashboard (v1)

- Context columns: base, after_hours (clock), meeting (calendar), commuting
  (location), vacation (all-day event), streaming (fullscreen or screen-share),
  sleeping (Health or manual), deep_work (manual). A context rule overrides the
  base row while that context is active. `/sieve context <name>` forces one;
  auto-detection sources land incrementally (clock is free via quiet windows).
- Dynamics: attention budget (max interrupts/hour, overflow auto-holds),
  promotion ladder (held items escalate one tier after N minutes), one-shot
  mutes, recurring quiet hours with breach floors, pause.
- Simulator: dry-run any event class x context through the real decision path.
- Rule stats: total / delivered / held per rule (attribution feed).

## Designed, not yet built (roadmap order)

1. Auto context detection: calendar busy -> meeting, location bands ->
   commuting, screen-share/fullscreen -> streaming (the streamer case), Health
   sleep -> sleeping. Each is a small detector writing `active_context`.
2. VIP lanes: sender allowlist that always breaches (boss, family, on-call).
3. Per-class delivery targets and digest cadence (work -> one surface,
   personal -> another; hourly vs daily digests).
4. New event classes: spend threshold (run cost > $X), loop detection (same
   tool failing 3x), service probes (Tailscale, NAS, CI), deadline scanner.
5. Morning briefing: structured digest at wake time.
6. Suggestion chips from stats (patterns in release/snooze behavior) with
   one-tap accept.
7. Shareable rule packs: export/import rules as JSON ("streamer pack",
   "on-call pack"); marketplace angle.

## Profile and push scope (2026-11-05)

Device Access, push, and portal connections are per profile, not per machine.
Enabling `hermes-mobile-device-access` in a profile's plugin list does not
pair the phone to that profile. The phone grant lives with the profile that
completed the app sign-in.

This install: the iPhone is paired to **default** only (alarms, calendar,
location, reminders, contacts granted; health not granted). SIEVE stays on
default. Do not copy it onto flooringinc or glyphstack. Specialist profiles
do not need a phone.

Other setups: SIEVE degrades per profile. No device connection means T4/T5
stay off and Doctor says which profile is missing the phone, not "push is
broken." A later option is an attention-home: other profiles forward sirens
to the one profile that owns the phone, instead of pairing every profile.

Push is a second gate on top of the alarm grant. Grants can be on while
writes still sit pending until the app is opened, if push is not registered
for that profile.

Florian (Hermes mobile, 2026-11-09): background push already exists in the
official iOS beta, but it is legacy Cadu code he plans to clean up. He did
not give an enable path. The name hermes-mobile-push is from device-access
source, not public docs, and it is not in the catalog. TestFlight push being
on does not register the phone. Closed-app wake stays blocked until he names
the current install. Hackathon demo fallback: film the siren with the Hermes
app open, and say closed-app wake is a known beta limit.

Dig (2026-11-09): closed-app wake is NOT possible with current access.
HERMES_MOBILE_PUSH_RELAY_URL and HERMES_MOBILE_PUSH_INSTALL_KEY are unset.
hermes-mobile-push is not installed, not in the catalog, and the GitHub repo
404s. Desktop and hermes-agent source have no setup UI or alternate wake path.
Device-access wake() returns "Push is not configured" without those secrets.
TestFlight's push toggle does not register the phone. Florian confirmed the
capability exists in their tree (legacy Cadu code) but did not expose an
enable path. Do not invent a relay URL. Demo path: alarm with the app open,
or deliver the interrupt through Telegram/Discord, which already works.

Follow-up DM (not sent by the agent; CJ sends it):

> One bit of context I left out: this is for the Hermes plugin hackathon, due Nov 11. The plugin holds routine Hermes alerts and only breaks through for things the user marked urgent. I need an alarm that still fires when the official app is closed, including through Silent and Focus.
>
> On iOS 1.0.0 (41), TestFlight push is already on, but the app never asks iOS for notification permission, and there is no push control in App Settings or the instance page. hermes-mobile-device-access 1.7.2 only wakes the phone if HERMES_MOBILE_PUSH_RELAY_URL and HERMES_MOBILE_PUSH_INSTALL_KEY are set, and those are empty here.
>
> If the current beta can already do that, what should I set on the Mac today, on the default profile? Messy is fine. If the plugin is no longer called hermes-mobile-push, what is it called now? If it cannot work with the app closed yet, I'll film the siren with the app open and mark closed-app wake as a known limit.

### Reserved rung: T5 becomes the push arm when push ships

T5 stays in the vocabulary and keeps its policy meaning (**top priority**). It
currently resolves to the alarm arm, which is the same tone as T4. When the
Hermes app exposes a push path that reaches a closed app or a locked phone, T5
upgrades to that arm with no policy migration, because the tier never changed,
only the primitive assigned to it.

**Trigger conditions for the flip.** T5 may claim the push arm only when all of
these hold, checked at fire time rather than assumed from a changelog:

1. A push plugin or relay is present for this profile and the device is
   registered against it (the device-access plugin's own readiness signal, not a
   maintainer saying "supported").
2. A device write round-trips: send, then read the result back in the same
   session. Same verification standard as the alarm arm today.
3. The alert actually reaches the phone with the app closed. One live probe with
   the app force-quit, recorded, before any copy claims it.

**What T5 will and will not promise even then.** iOS delivers a normal push as an
ordinary notification: Focus silences it unless the sender is allowed through,
and it can be muted per conversation. A "Time Sensitive" interruption level
breaks through Focus but not Silent. Genuine critical alerts that override the
ringer require an Apple entitlement the app must hold. So the honest ladder after
push ships is:

- closed-app reach: yes, ordinary notification
- Focus break-through: only if the push is Time Sensitive and the user allows it
- ringer override: only with a critical-alert entitlement

Never call that a siren. If the entitlement lands, T5 gets a third arm and the
label becomes "critical alert". Until then the arm label states which of the three
it achieved, exactly as the degrade path does today.

**Acceptance test when push ships** (add to `scripts/click_pass.py` as a gated
case, skipped while push is unavailable):

- force-quit the app, fire T5, confirm delivery, confirm read-back, confirm the
  message states the arm as `push (closed app)` rather than `alarm`
- with the app open, fire T5, confirm it still prefers the push arm only if that
  arm is strictly stronger than an alarm; otherwise keep the alarm, which rings
  through Silent and Focus today

That last point is the decision rule: an arm is preferred only when it is
stronger, not newer. A notification never outranks an alarm that rings through a
silenced phone.

## Design language notes

### Truth constraint: T4 and T5 share one arm

The device API accepts `at`/`time`/`weekdays`/`label` only. No sound, volume, or
critical-alert parameter exists, so a T4 timer and a T5 alarm ring with the same
tone. T5 is therefore "maximum priority" in policy terms, not a distinct audible
primitive. Any copy that calls T5 a siren is wrong until the arm can vary output.

Planned differentiation is bounded persistence, not loudness: alarm, then at most
two repeats, stopping when the alarm leaves `mobile_alarms_list` (dismissal is
observable: a fired-and-dismissed alarm is absent from the list). Bounded so a
failed dismissal signal costs one extra ring instead of an unstoppable alarm.
Explicit escape: `/sieve ack`. Not shipping before the hackathon deadline.

- The host app's Desktop SDK IS the design system: native components, theme
  vars, one accent (intensity carries tier, never a rainbow), semantic badges
  only, dense-but-breathing layout, functional labels.
- The 24h policy clock is the signature visual; the context grid is the
  centerpiece. Everything else supports those two.