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

## Design language notes

- The host app's Desktop SDK IS the design system: native components, theme
  vars, one accent (intensity carries tier, never a rainbow), semantic badges
  only, dense-but-breathing layout, functional labels.
- The 24h policy clock is the signature visual; the context grid is the
  centerpiece. Everything else supports those two.
