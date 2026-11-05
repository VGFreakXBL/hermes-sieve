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

## Design language notes

- The host app's Desktop SDK IS the design system: native components, theme
  vars, one accent (intensity carries tier, never a rainbow), semantic badges
  only, dense-but-breathing layout, functional labels.
- The 24h policy clock is the signature visual; the context grid is the
  centerpiece. Everything else supports those two.
