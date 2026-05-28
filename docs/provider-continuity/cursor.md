# Cursor Agent Continuity Evidence

Date: 2026-05-28

This note records the bounded Cursor Agent continuity negative-control probe
used for the provider live-session matrix. At the time of the probe it was
evidence only. A later `cursor_live_session` runner used this evidence to
preserve both chat id and workspace, and a later one-resident room smoke proved
bounded start/probe/stop for this local install. Official-turn quality,
restart/recover behavior, tool safety, future billing/model availability, and
sandboxing remain unproven.

## Safe Contract Surface

- `cursor-agent --version` returned `2026.05.24-dda726e`.
- `cursor-agent create-chat --help` says it creates a new empty chat id.
- `cursor-agent --help` exposes `--print`, `--output-format`, `--resume`,
  `--continue`, `--mode ask|plan`, `--sandbox enabled|disabled`, `--trust`, and
  `--workspace`.
- The probe used `--resume <chat_id> --print --mode ask --sandbox enabled
  --trust --workspace <temporary-git-workspace>`.
- `--sandbox enabled` is Cursor-owned behavior and remains
  `sandbox_enforcement: "advisory"` from AgentsAssemble's perspective.

## Probe Summary

Probe A created a fresh chat id and used one temporary git workspace. The seed
turn asked Cursor to store a continuity code and reply with the ready marker.
The follow-up turn used the same chat id and the same workspace, did not repeat
the code, and asked for only the stored suffix.

Safe fields showed:

- `chat_a_captured: true`
- `first_reply_length: 5`
- `second_reply_length: 4`
- `first_reply_is_ready: true`
- `first_reply_ready_normalized: true`
- `first_reply_revealed_code: false`
- `first_reply_revealed_suffix: false`
- `second_prompt_replayed_code: false`
- `baseline_expected_suffix_matched: true`

Probe B created a different fresh chat id and used a different temporary git
workspace. It received the same recall prompt without the continuity code.

Safe fields showed:

- `chat_b_captured: true`
- `chat_ids_distinct: true`
- `negative_reply_length: 89`
- `negative_control_recalled: false`

Probe C reused chat id A with a different temporary git workspace and the same
recall prompt.

Safe fields showed:

- `workspace_changed_between_turns: true`
- `workspace_changed_reply_length: 73`
- `workspace_changed_expected_suffix_matched: false`

The temporary workspaces had no non-git entries after the probe, and no
throwaway workspace global-store symlink was observed.

## Verdict

Cursor Agent is now `room-smoke-proven-limited`.

The local install now has stronger evidence than the earlier positive pair:
the same chat id and same workspace can recall the previous turn's suffix, while
a fresh chat id does not recall it. That supports a narrow provider-owned
chat-id continuity surface and avoids the Hermes-style global-recall ambiguity
for this probe.

The different-workspace control did not recall the suffix. A future Cursor
resident runner must therefore preserve both the chat id and the workspace
directory across resident turns unless a later provider-specific proof shows a
workspace-independent resume path.

The checked-in `cursor_live_session` runner is justified only as a narrow
continuity runner from this evidence. A later approved room smoke adds bounded
room start/probe/stop evidence for one resident, but this probe still does not
prove official-turn quality, restart/recover behavior, tool safety, future
billing/model availability, or OS-level sandboxing.

`provider_kind: "cursor"` remains a generic/planned provider label, not a
runnable resident contract. Hand-authored resident configs that combine
`provider_kind: "cursor"` with `terminal_session` or generic JSONL
`live_session` fail closed and point operators to `cursor-agent-live-session`
with `provider_kind: "cursor_live_session"` and `connection_kind:
"live_session"`. Future `self_service` or `remote_bridge` Cursor designs must
be explicit wrappers that own their room loop; they are not implied by the
generic Cursor label.

Public docs must keep raw prompts, raw replies, full chat ids, continuity codes,
code suffix values, absolute workspace paths, account data, billing data, and
Cursor logs out of committed evidence.
