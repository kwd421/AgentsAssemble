# Grok Official-Turn Evidence

Date: 2026-05-28

This note records the bounded Grok follow-up that was intended to rerun official
turn quality after `official_turn_timeout_seconds` was added. It is evidence
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

The strict proof returned `status: "failed"` with
`reason: "first_reply_not_ready"`. Safe fields still showed:

- `session_id_captured: true`
- a short safe session-id suffix was captured
- `first_reply_length: 6`
- `second_reply_length: 4`
- `first_reply_revealed_code: false`
- `first_reply_revealed_suffix: false`
- `second_prompt_replayed_code: false`
- `expected_suffix_matched: true`

Because the strict continuity-proof status failed, the official-turn room smoke
with the dedicated official-turn timeout was not run in this slice.

## Verdict

Grok remains `room-smoke-proven-limited` for this local install.

The prior checked-in evidence still proves a narrow Grok JSON stdout resume
runner, prior two-turn suffix recall, bounded start/probe/stop, and
restart/probe behavior. The 2026-05-28 rerun did not prove official-turn
quality: the same-day strict continuity baseline failed before the official
room smoke could be a clean measurement.

Do not claim Grok official-turn quality, recover behavior, tool safety, future
billing stability, or OS-level sandboxing from this result. A later slice should
first restore a passing strict continuity baseline or explicitly redesign the
Grok continuity contract, then rerun the official-turn smoke with safe counts
only.

Public docs must keep raw prompts, raw replies, full session ids, stdout,
stderr, account data, absolute local paths, prompt-file paths, generated config
paths, and provider logs out of committed evidence.
