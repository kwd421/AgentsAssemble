# Cursor Agent Continuity Evidence

Date: 2026-05-28

This note records the bounded Cursor Agent continuity negative-control probe
used for the provider live-session matrix. It is evidence only: no resident
runner, config promotion, room smoke, or sandbox claim was added from this
probe.

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

Cursor Agent remains `continuity-proven-limited-no-runner`.

The local install now has stronger evidence than the earlier positive pair:
the same chat id and same workspace can recall the previous turn's suffix, while
a fresh chat id does not recall it. That supports a narrow provider-owned
chat-id continuity surface and avoids the Hermes-style global-recall ambiguity
for this probe.

The different-workspace control did not recall the suffix. A future Cursor
resident runner must therefore preserve both the chat id and the workspace
directory across resident turns unless a later provider-specific proof shows a
workspace-independent resume path.

Do not add or advertise a Cursor resident runner from this evidence alone. This
probe does not prove room admission, official-turn quality, stop/restart
behavior, tool safety, future billing/model availability, or OS-level
sandboxing.

Public docs must keep raw prompts, raw replies, full chat ids, continuity codes,
code suffix values, absolute workspace paths, account data, billing data, and
Cursor logs out of committed evidence.
