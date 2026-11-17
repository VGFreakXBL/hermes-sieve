# SIEVE Setup Assistant — analysis and design

Date: 2026-11-11. Grounded in the 2026-11-11 config session (Desktop + Discord),
the plugin store, gateway logs, and session history. This doc has three parts:
what broke and why, the capability map a new user's setup must be measured
against, and the design for a plugin/LLM setup assistant that walks a new user
to a working T4/T5 without repeating those walls.

---

## Part 1 — What broke (2026-11-11 post-mortem)

The capability chain for T4/T5 worked end to end the whole time: the phone was
paired to the default profile with `alarms.manage`, the toolset was loaded, and
prompt-driven alarm writes had worked before. Four separate walls made it look
broken.

### Timeline (store- and log-backed)

- **08:56** — user notices `push-gate blocked: device toolset unavailable
  (ctx unavailable)` "at the bottom" of the SIEVE pane. This is a **stamped
  result** of a pane-side `/sieve test` attempt (`last_phone_check`). The pane
  runs outside an agent turn and cannot dispatch phone tools. A surface
  limitation, printed in the vocabulary of a config fault.
- **~09:48:59** — a T4 delivery runs while the phone is **stale** (app not
  syncing). It degrades to messaging push and delivers to the telegram leg
  (store: `last_delivery`), writing `escalation_degraded = "device stale (no
  recent sync; open the Hermes app)"`. This is the degraded notification the
  user saw first; both chain legs land on the same physical phone.
- **12:24** — "how do I configure for push for T4/T5?" The doctor's fix text
  said *"open the Hermes app; if writes stay pending, configure push."*
  "Configure push" points at push registration — which has **no enable path in
  this build** (vendor-side gap: relay secrets unset, no catalog plugin). An
  unreachable instruction.
- **12:47:40** — `/sieve doctor` on Discord: push gate `device stale` again.
  The user opens the Hermes app to fix it. Device goes fresh.
- **12:54** — T4/T5 delivery chains configured and leg-tested (first SIEVE
  iMessage traffic).
- **12:55** — the T5 ritual fails twice on the live bridge:
  1. `Use an ISO 8601 date with timezone for at.` — SIEVE passed a naive local
     timestamp.
  2. Retry with the same idempotency key and a corrected payload: `Idempotency
     key already belongs to a different action` — a rejected attempt still
     consumes the key.
- **12:55–12:58** — fix landed (`d0a6018`), alarm created, read back as
  scheduled before fire time, rang at 12:58. User witnessed the ring.

### Root causes

- **W1 — Surface asymmetry rendered as a config fault.** `push-gate blocked:
  device toolset unavailable (ctx unavailable)` conflates three different
  failures in one string: (a) this surface cannot dispatch phone tools (Desktop
  pane), (b) the `mobile_device` toolset is not loaded (config), (c) no device
  is paired to this profile (phone). Each has a different fix; the string names
  none of them.
- **W2 — Timestamp contract drift.** The device bridge requires ISO 8601 **with
  a timezone**. SIEVE's T5 path (`__init__.py` ritual and live escalation,
  building `datetime.now() + 1min` naive) violated it. **T4 was immune** — the
  timer arm takes a duration, no timestamp — which is exactly why earlier T4
  demos worked while T5 "mysteriously" did not. Agent-prompt writes never broke
  because the tool schema asks for a timezone and agents comply. "Alarms via
  prompt or command work" and "SIEVE T5 does not" were both true at once.
- **W3 — Unreachable fix text.** Doctor told the user to "configure push".
  Nothing a user can do registers push in this build. A fix line the user
  cannot complete is worse than a named limitation.
- **W4 — State clarity.** `escalation_degraded` was set at 09:48 and lingered
  all day. It is cleared by a verified strong-arm success **inside the routing
  path**, or by `flush` — but not by `/sieve test T5` verified, and not by
  agent-driven verified writes. The pane/status read "degraded" long after the
  arm recovered.
- **Operational friction (minor, burned time).** Idempotency keys bind to the
  exact payload even when the attempt was rejected; console and tool output
  mask E.164 numbers (`+160****4803`) while the store holds the full value, so
  eyeballing a read-back cannot confirm what is stored (verify with boolean
  probes).

### What did not break (constraints, not defects)

- Desktop **prompt** → phone works: agent sessions carry the toolset and
  schema-compliant payloads. This is the "queued from desktop, sent to mobile"
  behavior that was witnessed before.
- Desktop **pane/commands** cannot dispatch phone tools by design (no agent
  turn). The pane is read + policy-edit; phone commands must run from a
  phone-capable chat surface (Discord/Telegram).
- Closed-app wake never existed: no push registration path (relay secrets
  unset, `hermes-mobile-push` not in the catalog). T4/T5 ring with the Hermes
  app open and synced; writes queue while it is closed. Policy already reserves
  T5 for the future push arm (no migration needed when it ships).

---

## Part 2 — Capability and constraint map

Probe each row before claiming the capability. The archetype is whatever the
probes return, not what the user hopes for.

### Prerequisites per capability

- **Ring through Silent and Focus (T4/T5 phone arm)** requires ALL of:
  `mobile_device` toolset loaded (`hermes config get toolsets`) · phone paired
  to **this profile** (app sign-in) · `alarms.manage` grant (phone: Settings →
  Device Access) · device fresh (`last_seen` within 24h) · **Hermes app open or
  synced at write time** — with the app closed the write queues until next open,
  because push registration does not exist in this build.
- **Messaging push to a locked phone (T2/T3 and the T4/T5 chain)** requires:
  a configured delivery target (`hermes send --to` reachable) and the platform
  app alive on the phone. It is an **ordinary notification**: Focus can silence
  it, conversations can be muted, it never rings. A weaker class of interrupt —
  say so, never call it an alarm.
- **Policy editing (matrix, quiet windows, mutes)** works on every surface,
  Desktop pane included. No phone needed.
- **`/sieve doctor` and `/sieve test`** need a phone-capable surface (Discord
  or Telegram session). On Desktop they answer, but their phone parts report
  "no tool context" — by design.

### Setup archetypes (decide by probe)

1. **Full ladder** — phone paired to this profile, grants on, app open/fresh.
   T4/T5 ring (same tone; T5 = top priority, not louder). Ritual: `test T4`,
   `test T5`.
2. **App closed / device stale** — writes queue (pending) and the arm degrades
   to the messaging chain with an `[escalation degraded: ...]` note. No ring.
   Fix is "open the app", not "configure push".
3. **No phone, or paired to another profile** — declared no-phone profile:
   T4/T5 stay on desktop toast + the chat chain. Doctor names the profile that
   is missing the phone ("push is broken" is never the diagnosis). Pairing
   lives with the profile that completed the app sign-in; copying the plugin
   does not pair anything.
4. **Multi-profile** — SIEVE runs on the profile that owns the phone. Other
   profiles degrade per archetype 3 (roadmap: attention-home forwarding).

### Claim axes (what nothing in this build may say)

- **Closed-app reach:** no. Not for the alarm arm, not for waking the app.
- **Focus break-through:** only the alarm arm, only with the app open.
- **Ringer override:** only the alarm arm (an OS alarm), same tone for T4/T5.
- Never: siren, critical alert, custom sound, per-volume control, lock-screen
  card. The device API takes a label and a time, nothing else.

---

## Part 3 — Setup assistant design

One flow, five phases. The plugin exposes the probes and writes; the LLM
assistant (or the user) supplies the answers. Every phase is completable from
chat — a setup only a GUI can finish is a setup the agent cannot walk.

### Phase 0 — Preflight (read-only, one pass)

Probe and report, no writes:
1. Surface: am I in a phone-capable session? (Desktop → say so up front and
   continue with the phone parts flagged "run these from Discord/Telegram".)
2. `hermes config get toolsets` includes `mobile_device`?
3. `mobile_devices`: device registered to this profile? grants include
   `alarms.manage`? `last_seen` fresh?
4. Push registration: capable? (Today: always NO — print the limitation text
   verbatim; never print a "configure push" instruction.)
5. `hermes send` reachable and at least one delivery target resolvable?

### Phase 1 — Archetype and capability statement

Map the probes to one archetype (Part 2) and state, in plain words, what this
setup can and cannot do **today** — before any question is asked. Example for
archetype 2: "T4/T5 will ring while the Hermes app is open and synced. With the
app closed, writes queue and the alert degrades to a Telegram/iMessage push —
an ordinary notification that Focus can silence. Background wake is a known
limit of the current mobile build; the policy already reserves T5 for it."

### Phase 2 — Policy walk (existing `setup` walk + two additions)

Question order (each answer constrains the next; one per turn; every answer
maps to a state key; re-asking is idempotent):
1. always-on classes (T5 list) → 2. quiet window → 3. breach floor (its own
   `none` sentinel) → 4. phone arm (carry archetype 2's limit with this
   question) → 5. digest target → **6. delivery chain** (`target:T4`/`target:T5`
   from real `hermes send --list` output; redundant, not failover) → 7. daytime
   tier. The walk then ends with the ritual in its done text — deliberately not
   a question: an answer with no state key does not go in the walk (Rule 22).
   The ritual's proof is a real state key: a verified `test T5` stamps
   `ritual_done_at` and clears `escalation_degraded`.

One-shot equivalent: `/sieve setup apply always=... sleep=... floor=... arm=...
digest=... chain=... daytime=...` — same end state as the walk (asserted in
tests/test_setup_assistant.py).

### Phase 3 — Verification ritual

Only from a phone-capable surface, only in archetype 1/2:
1. `test T4` (60s timer — proves the write path).
2. `test T5` (alarm ~60–90s out — schedule with lead time so the read-back
   resolves before fire time).
3. Report tier + arm + verification state, e.g. `T5 alarm arm, create read back
   as scheduled [verified]`.
4. **On verified success, clear `escalation_degraded`** (see backlog #2).
5. If the ring does not land, keep the degrade note and say what the achieved
   arm was. A silent failure is the one outcome that must be impossible.

### Error lexicon (map every string → cause → action)

| User sees | Actual cause | The assistant says |
|---|---|---|
| `push-gate blocked: ... (ctx unavailable)` | ran on a surface with no tool context (Desktop pane) | "This is the Desktop surface, which cannot write the phone by design. Run `/sieve test T5` from Discord or Telegram." |
| `... device toolset unavailable (mobile_device not loaded)` | config missing the toolset | "`hermes config set toolsets '[\"hermes-cli\",\"mobile_device\"]'`, restart, done." |
| `... no device registered` | phone not paired to this profile | "Open the Hermes app and sign in to **this** profile. Pairing follows the sign-in." |
| `... device stale (no recent sync)` | app not syncing | "Open the Hermes app once. Writes queue while it is closed — this build cannot wake it (known limit)." |
| `Use an ISO 8601 date with timezone for at.` | pre-`d0a6018` timestamp bug | "Fixed in `d0a6018`; update the plugin." |
| `Idempotency key already belongs to a different action` | retry reused a key with a changed payload | "A changed payload is a new action — new key. Identical retries reuse the key." |
| `Push is not configured...` | push registration absent (vendor gap) | The limitation, verbatim. No fix exists. |
| Doctor fix line "configure push" | unreachable text (W3) | Replace with: "open the Hermes app; closed-app wake is a known limit of this build" |

The `push-gate blocked` prefix itself should split into three surface-aware
reasons (backlog #1) so the string names the cause before the assistant has to
translate it.

### LLM assistant playbook (drop-in instructions)

```
1. Run Phase 0 probes BEFORE answering any setup question. Never answer a
   capability question from memory.
2. Print the Phase 1 capability statement for the detected archetype. Lead
   with what will NOT work in this setup; that is what users get wrong.
3. Never tell a user to "configure push". Push registration does not exist in
   this build. The reachable actions are: toolset, pairing, grants, freshness
   (open the app), arm, and delivery chain.
4. Walk Phase 2 one question per turn, each with its honest limit attached.
   Persist progress in state, not in this conversation.
5. Finish with Phase 3 from Discord/Telegram, report tier + arm + verified,
   and clear escalation_degraded on a verified ring.
6. If anything fails, apply the error lexicon before touching config. Most
   "config" problems on this plugin are surface or freshness problems.
```

---

## Part 4 — Plugin backlog (to make the design self-serve)

1. **Split the push-gate reason** into surface / toolset / pairing / staleness,
   each with its own fix line (W1). Doctor gains a `surface:` line and a
   one-line capability statement derived from the probes (Part 3, Phase 1).
2. **`/sieve test T5` clears `escalation_degraded` on verified success** (W4).
   Also accept a verified agent-driven write as a clear (the record is about
   the arm, not about which code path called it).
3. **Fix-line truth pass** (W3): delete "configure push" from every surface;
   name the limitation instead.
4. **Setup walk additions**: targets question (Phase 2 #7) and end-with-ritual
   (Phase 2 #8), with a state-keyed cursor and idempotent answers.
5. **Retry guidance at the boundary**: when a create is rejected as a duplicate
   action, the error should say "retrying with a changed payload needs a fresh
   idempotency key".
6. **README setup section by archetype** (Part 2), so the docs answer "what can
   my setup do" before the install, matching the known-issues list exactly.

Status: all six backlog items landed in this tree — reason-aware push gate with
a per-cause `push_fix` lexicon (W1), `test T5` clear-on-verify + `ritual_done_at`
(W4), the "configure push" fix text removed from every surface (W3), the walk's
chain step plus ritual done text, idempotency-retry guidance at the alarm
boundary, and the README archetype section (W2 was fixed in `d0a6018`; the gate
had already learned to name its real reason in `5da9423`, and this pass splits
the cause classes and gives each its own reachable fix). Regression pins live
in tests/test_setup_assistant.py.
