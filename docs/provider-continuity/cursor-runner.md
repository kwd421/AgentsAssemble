# Cursor Agent Runner Continuity Evidence

Date: 2026-05-28

This note records the first checked-in Cursor live-session runner slice. It is
implementation evidence for the narrow adapter shape only. A later approved
one-resident room smoke proved bounded start/probe/stop for this local install,
but this note does not prove official-turn quality, restart behavior, recover
behavior, future billing/model availability, tool safety, or OS-level
sandboxing.

## Runner Contract

- Provider kind: `cursor_live_session`.
- Connection kind: `live_session`.
- Command surface: `cursor-agent`.
- Fresh session: `cursor-agent create-chat`.
- Resident turns: `cursor-agent --resume <chat_id> --print --mode ask
  --sandbox enabled --trust --workspace <runner-owned-workspace>`.
- Prompt delivery: stdin only; the runner does not write raw prompts to
  intermediate prompt files.
- Context key: both the Cursor chat id and the runner-owned workspace directory.
- Sandbox label: `advisory`. Cursor's `--sandbox enabled` flag is
  provider-owned behavior, not AgentsAssemble hard sandbox evidence.

The runner ignores configured arguments after the executable, so caller-supplied
resume, workspace, print, mode, sandbox, or trust flags cannot change the
resume key. The runner owns one temporary workspace for its lifetime and reuses
it across turns. That matches the earlier Cursor continuity negative control:
the same chat id recalled only when the workspace also stayed the same.

## Safe Verification

Checked-in fake tests verify fresh chat creation, stable workspace reuse,
stdin prompt delivery, command defaults, preflight checks, continuity-proof
support, categorized safe failures, and the absence of any automatic
OS-sandbox claim.

An approved real continuity proof was then run through the checked-in runner
from an isolated temporary git workspace:

```bash
python3 -m agentsassemble.cli live-agent continuity-proof \
  --provider-kind cursor_live_session \
  --connection-kind live_session \
  --approve-real-providers \
  --json \
  --command cursor-agent
```

It returned `status: "ok"`, captured a safe chat-id suffix, kept the first
reply length at `5` and the second reply length at `4`, required the first reply
to be exactly `READY`, did not reveal the continuity code or suffix in the first
reply, did not replay the code in the second prompt, and matched the expected
suffix. This remains only two-turn provider-owned resume evidence.

Public artifacts must not store raw prompts, raw replies, full chat ids,
continuity codes, suffix values, absolute workspace paths, account data,
billing data, or Cursor stdout/stderr logs.

## Remaining Proof

An operator-approved real-room smoke later started one approved Cursor resident,
posted a redacted lobby probe, verified one safe reply count, stopped the group,
and recorded only safe counts: start ready, connected 1/1, reply probe 1/1,
stop stopped, and post-stop stopped.

Official-turn quality, restart, recover, production safety, and sandboxing stay
separate proof slices.
