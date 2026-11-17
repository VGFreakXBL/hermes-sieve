# SIEVE seam reconnaissance (Task 1.2)

Status: DONE — live recon closed 2026-11-17. The log-only observers were retired
before release: they persisted hook args (and, on `pre_gateway_dispatch`, the raw
inbound message) to `docs/spike-events.jsonl`. No payload log ships.

## Event class → hook mapping (source: hermes docs, `hermes_cli.plugins.VALID_HOOKS` = 27 hooks)

| SIEVE event class | Hook(s) | Status | Notes |
|---|---|---|---|
| needs-human gates (clarify/approval/sudo) | `on_human_input_request` (`kind`: sudo\|clarify\|approval) + `on_human_input_resolved` (outcome) | CONFIRMED | Fires on CLI, Ink TUI/Desktop, ACP AND gateway platforms. Observer, return ignored. Prompt force-redacted; answer never passed. |
| approvals | `pre_approval_request` + `post_approval_response` | CONFIRMED | Before shown/sent (incl. smart approval); after decision/timeout/gateway-notify-failure. Observer. `command` may carry secrets → redact. |
| kanban blocked-on-human | `kanban_task_blocked` | CONFIRMED in hook list | Payload shape to capture via spike. |
| subagent/background completions | `subagent_start` / `subagent_stop`, `agent_loop_stopped` | CONFIRMED in hook list | Basis for "agent finished X" events (delegation notifications). |
| cron completions | `on_session_end` (fires from cron completion per plugins/AGENTS.md) | CONFIRMED (indirect) | Spike must verify the session_end kwargs distinguish a cron-scope run (profile_cron_scope). Fallback: custodian-pattern scan of cron output. |
| inbound platform messages | `gateway_platform_event` | CONFIRMED (partial) | Docs note: currently Telegram reactions only at gateway boundary. Spike: what inbound message visibility exists (`pre_llm_call` user_message is the reliable fallback). |
| OUTBOUND agent messages (hold/queue target) | `pre_gateway_dispatch` | OUT OF SCOPE (not subscribed) | Carries the raw inbound message object; logging or routing it would put message text in the boundary. SIEVE v1 governs notification events + `hermes send` flows, not live chat replies. |
| agent-initiated messages | `on_interim_message`, `on_stream_end` | UNVERIFIED | Spike for `hermes send`-style flows. |

## Category context (VALID_HOOKS, 27 total)
- Directive/control: `pre_tool_call`, `pre_llm_call`, `pre_verify`, `pre_gateway_dispatch`
- Transform: `transform_tool_result`, `transform_terminal_output`, `transform_llm_output`, `pre_transcription`
- Observer: `post_tool_call`, `post_llm_call`, `pre_api_request`, `post_api_request`, `api_request_error`, `pre_auxiliary_call`, `post_auxiliary_call`, `on_stream_start`, `on_stream_delta`, `on_stream_end`, `on_interim_message`, `on_session_start`, `on_session_end`, `on_session_finalize`, `on_session_reset`, `agent_loop_stopped`, `on_skill_lifecycle`, `subagent_start`, `subagent_stop`, `pre_approval_request`, `post_approval_response`, `on_human_input_request`, `on_human_input_resolved`, `pre_command`, `kanban_task_claimed`, `kanban_task_completed`, `kanban_task_blocked`

## Gateway hooks (HOOK.yaml + handler.py in ~/.hermes/hooks/) — NOT our seam
Load once at gateway startup only; CLI/TUI/Desktop/cron never load them. Plugin `ctx.register_hook` is the correct registry for SIEVE (works everywhere the plugin loads).

## Live spike protocol (historical)
Scaffold registered log-only observers for the 9 SPIKE_EVENTS (see `__init__.py`)
writing to `docs/spike-events.jsonl`. That log recorded positional args unredacted,
which meant inbound message text from `pre_gateway_dispatch` hit disk for every
sender. **The observers and the log are removed from the shipped build.** The
results below are kept as the recon record only.

## Live spike results (Wed Nov 4, 23:03 — first one-shot run after `hermes plugins enable sieve`)

Pipeline verified: hooks fire and write `docs/spike-events.jsonl` (secret keys redacted). From one trivial CLI turn:
- `on_interim_message` FIRED — kwargs: turn_id, iteration, session_id, model, provider, surface, already_streamed. Candidate for "agent-initiated message" class.
- `pre_approval_request` FIRED **even on the smart-auto-approve path** (a flagged terminal command was auto-approved) — kwargs: description, pattern_key(s), session_key, surface, turn_id, tool_call_id. So this hook = ALL approval activity.
- `post_approval_response` FIRED — adds `choice` + `decided_by` (here: smart). Exactly the attribution SIEVE logs.
- `on_session_end` FIRED — adds completed/failed/interrupted/**turn_exit_reason** → the candidate signal for identifying cron-completion runs (spike question 2: test a cron job and compare turn_exit_reason).
- `pre_gateway_dispatch` did NOT fire (CLI surface, not gateway) — spike question 1 needs a **gateway message** test (send via Telegram/Discord, watch the log). Top priority for Day 1 AM.
- `on_human_input_request` did NOT fire — smart approval does not block on a person; this hook is the true "blocking on human" event (clarify/sudo/manual approval). Confirms SIEVE's T4 needs-human class maps HERE, while `pre_approval_request` is the broader activity feed.

Operational finding: new plugin = `not enabled` until `hermes plugins enable sieve`; hooks then activate in the running gateway immediately ("gateway_transforms, hooks" live; tools deferred to next session). Also: `toolsets: [hermes-cli, mobile_device]` gates tool loading — SIEVE's future tools will need their toolset listed (hooks are unaffected).

## Spike questions (resolved)
1. `pre_gateway_dispatch` — resolved by scope decision: not subscribed. It is the
   inbound-message seam, and SIEVE's boundary is notification routing, not chat.
2. `on_session_end` — yes: `task_id` present means a task/cron run, absent means an
   ordinary turn end. Implemented in `hook_session_end`.
3. Payload shapes — captured for the hooks still subscribed; recorded above.
