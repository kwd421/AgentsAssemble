# Grok Official-Turn Evidence

Date: 2026-05-28

This note records the bounded Grok follow-up that reran official turn quality
after `official_turn_timeout_seconds` was added and the continuity proof ready
marker was narrowed to accept harmless terminal punctuation. It is evidence
only: no resident runner, config promotion, or sandbox claim was added from this
probe.

## Safe Contract Surface

- `grok --version` returned `grok 0.2.3 (14d81fd875e) [stable]`.
- Discovery found `grok-live` as `provider_kind: "grok_live_session"` with
  `connection_kind: "live_session"`, `join_semantics: "grok_session_resume"`,
  `context_durability: "provider_managed_resume"`, and
  `sandbox_enforcement: "advisory"`.
- The Grok-only temporary resident bundle contained exactly one approved agent:
  `grok-live`.
- The generated Grok resident config kept `timeout_seconds: 240` and added
  `official_turn_timeout_seconds: 360`, so ordinary lobby/probe command budget
  was not raised for this probe.
- `live-agent preflight` returned `status: "ok"` for that single Grok resident.

## Probe Summary

Before running the official-turn room smoke, a same-day continuity baseline was
required so the smoke would measure official-turn quality rather than a broken
provider-owned resume surface.

The approved command used `live-agent continuity-proof` with
`provider_kind: "grok_live_session"`, `connection_kind: "live_session"`,
`--approve-real-providers`, and the local `grok` command.

The first strict proof returned `status: "failed"` with
`reason: "first_reply_not_ready"`. Safe fields still showed:

- `session_id_captured: true`
- a short safe session-id suffix was captured
- `first_reply_length: 6`
- `second_reply_length: 4`
- `first_reply_revealed_code: false`
- `first_reply_revealed_suffix: false`
- `second_prompt_replayed_code: false`
- `expected_suffix_matched: true`

After the ready-marker contract was narrowed to accept exact `READY` plus at
most one terminal punctuation mark, the same approved continuity proof was rerun
from an isolated temporary git working directory. The rerun returned:

- `status: "ok"`
- `reason: "ok"`
- `session_id_captured: true`
- a short safe session-id suffix was captured
- `first_reply_length: 5`
- `second_reply_length: 4`
- `first_reply_is_ready: true`
- `first_reply_ready_normalized: true`
- `first_reply_revealed_code: false`
- `first_reply_revealed_suffix: false`
- `second_prompt_replayed_code: false`
- `expected_suffix_matched: true`

Because the continuity baseline passed, the official-turn room smoke was rerun
with the Grok-only bundle, `--official-round-smoke`, `--restart-smoke`, and the
dedicated official-turn timeout. Safe counts returned:

- `status: "ok"`
- `start_status: "ready"`
- `expected_agent_count: 1`
- `connected_agent_count: 1`
- `reply_probe_status: "ok"`
- `reply_probe_count: 1`
- `reply_probe_ok_count: 1`
- `official_rounds_status: "answered"`
- `official_round_count: 1`
- `official_answered_round_count: 1`
- `official_timeout_round_count: 0`
- `restart_status: "ready"`
- `post_restart_connected_agent_count: 1`
- `post_restart_reply_probe_status: "ok"`
- `post_restart_reply_probe_count: 1`
- `post_restart_reply_probe_ok_count: 1`
- `stop_status: "stopped"`
- `post_stop_process_status: "stopped"`

## Verdict

Grok remains `room-smoke-proven-limited` for this local install, but the
official-turn quality evidence is now positive for the bounded local smoke above.

The checked-in evidence now proves a narrow Grok JSON stdout resume runner,
two-turn suffix recall, bounded start/probe/stop, one official answered round
with the dedicated official-turn timeout, and restart/probe behavior for this
local install.

Do not claim recover behavior, tool safety, future billing stability,
production readiness, or OS-level sandboxing from this result. Grok launch
safety remains `advisory`, and future provider billing/model availability can
still change.

Public docs must keep raw prompts, raw replies, full session ids, stdout,
stderr, account data, absolute local paths, prompt-file paths, generated config
paths, and provider logs out of committed evidence.
