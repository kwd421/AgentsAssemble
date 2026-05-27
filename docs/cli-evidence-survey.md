# CLI Evidence Survey

This file records local CLI contract evidence for provider-owned live-room
participation beyond Codex and Kiro. It is intentionally conservative: help and
version output can identify a candidate session surface, but only an approved
two-turn continuity probe can prove provider-owned context continuity.

Grok is the only non-Codex/Kiro provider with a passing two-turn continuity
probe and a passing bounded room start/probe/stop smoke in this survey.
Discovery, executable presence, config generation, help text, and one-shot
`local_cli` output are not evidence of resident participation or provider-owned
context continuity.

## Method

For a provider to move out of `unverified`, record evidence for all of these:

- The exact CLI command and version inspected.
- Whether the CLI can keep provider-private context across at least two turns
  without replaying the first-turn secret in the second prompt.
- Whether the CLI can register once, read the room, reply, heartbeat, and leave
  through agent-owned room tools.
- Whether the path is `self_service`, `terminal_session`, or a provider-native
  resume/session runner.
- What sandbox, credential, and approval boundary actually constrained the run.

Safe discovery commands are limited to bounded `command -v`, `--help`, `help`,
`--version`, and version subcommands that do not start chats, log in, mutate
accounts, index workspaces, or call a model. Status, auth, login, chat, prompt,
run, build, agent, update, install, session creation, and model-inference
commands require explicit real-provider approval before they can be used as
continuity evidence. Public docs must not record account identifiers, tokens,
raw prompts, raw provider output, local config paths, or provider log tails.

## Current Rows

| Provider kind | Evidence status | Contract evidence | Current conclusion | Next evidence needed |
| --- | --- | --- | --- | --- |
| `claude_code` | absent in PATH | `claude` was not found by PATH discovery in this slice. | Planned only. Do not present one-shot `local_cli` as a resident Claude Code session. | Inspect real CLI/channel behavior, then prove provider-owned room loop or native resume. |
| `cursor` | continuity-probe-failed | `cursor-agent --version` returned `2026.05.24-dda726e`; `cursor-agent --help` exposes `--print`, `--output-format text|json|stream-json`, `--resume`, `--continue`, `--mode ask|plan`, `--sandbox enabled|disabled`, and `create-chat`. The app `cursor` CLI is editor/open-file oriented, not the agent surface. | Cursor Agent has a plausible session surface, but approved explicit-chat-id and workspace-continue probes both returned successfully without recalling the suffix. This is a failed probe, not proof that Cursor can never preserve context. | Do not add a Cursor runner yet. A later slice needs a better provider-documented resume path or an agent-owned self-service loop before room participation. |
| `antigravity_cli` | continuity-probe-failed | `agy --version` returned `1.0.1`; `agy --help` exposes `--print`, `--prompt-interactive`, `--continue`, `--conversation`, `--sandbox`, and `--dangerously-skip-permissions`. | The approved `agy --print` plus `--continue` probe returned successfully but did not recall the suffix; sandbox/permission text appeared in the process output, so the failure is kept conservative. | Do not add an Antigravity runner yet. Revisit only with a documented conversation id flow or a self-service loop that owns its own room polling. |
| `grok_live_session` | room-smoke-proven-limited | `grok --version` returned `grok 0.2.3 (14d81fd875e)`; `grok --help` exposes `--single`, `--prompt-file`, `--prompt-json`, `--resume`, `--continue`, output formats, `--sandbox`, `--no-memory`, `--no-subagents`, and session listing. Running bare `grok` starts a TUI and timed out under bounded non-interactive discovery. | Approved isolated-git-cwd probes using JSON stdout and explicit `--resume <sessionId>` recalled the suffix exactly from the JSON `text` field. The checked-in runner uses the safer `--prompt-file` JSON-output shape and later `--resume <sessionId>`. An approved Grok-only generated room bundle then passed `real-session-smoke` with safe counts: start ready, connected 1/1, reply probe 1/1, stop stopped, post-stop stopped. Full process output can echo prompt material in stderr, so raw stderr/stdout logs must not be persisted as proof artifacts. | Grok may be used for controlled local resident experiments with explicit approval. Still prove official-turn quality, restart/recover behavior, tool safety, and sandboxing before treating it as production-ready. |
| `grok_build_cli` | contract-known-but-not-runner | `grok agent --help` exposes `stdio`, `headless`, `serve`, and `leader` modes. | These surfaces may become useful later, but they are not the checked-in continuity runner. | Do not route resident continuity through `grok_build_cli` yet; use `grok_live_session` for the narrow JSON stdout resume path. |
| `hermes_cli` | continuity-probe-failed | `hermes --help` exposes `chat`, `sessions`, `mcp`, and related commands. `hermes chat --help` exposes `--query`, `--resume`, `--continue`, `--quiet`, `--source`, `--ignore-user-config`, and `--ignore-rules`. `hermes sessions --help` exposes list/export/delete/rename tooling. A bounded `hermes version` invocation did not return before timeout in this environment. | The approved `hermes chat --query` plus continue probe returned successfully but the second turn reported no usable prior-session memory for the token. Status/account inspection is not safe public evidence and is not used here. | Do not add a Hermes runner yet. Revisit only with a documented session id resume flow that avoids hidden session dumps and account/status leakage. |
| `openclaw_cli` | absent in PATH | `openclaw` was not found by PATH discovery in this slice. | Memory/profile inspiration only until a live CLI contract is proven. | Gate memory artifacts separately, then inspect any CLI session behavior if an executable appears. |

## Audit Command

Use the conservative staging bundle for an audit-only baseline:

```bash
python3 -m agentsassemble.cli live-agent continuity-proof-group \
  --config configs/live-agents.provider-staging.example.json \
  --json
```

Without `--approve-real-providers`, this must not start real provider CLIs. With
the current staging config it should report unsupported rows only, because none
of those provider kinds has a checked-in continuity-proof runner.

## Real Continuity Probe Summary

These probes used approved real CLI model calls in isolated temporary
directories. Public records keep only safe booleans and lengths; they do not
store raw prompts, raw provider output, full session ids, account data, config
paths, provider logs, nonce values, or nonce suffixes.

| Provider kind | Probe result | Resume mechanism | Safe evidence | Runner implication |
| --- | --- | --- | --- | --- |
| `cursor` | failed this probe | explicit chat id and workspace-scoped `--continue` | both calls returned successfully; turn 2 prompt contained neither nonce nor suffix; turn 2 output did not contain the expected suffix | no runner yet |
| `antigravity_cli` | failed this probe | isolated-cwd `agy --print --continue` | both calls returned successfully; turn 2 prompt contained neither nonce nor suffix; turn 2 output did not contain the expected suffix | no runner yet |
| `hermes_cli` | failed this probe | isolated-cwd `hermes chat --query --continue` | both calls returned successfully; turn 2 prompt contained neither nonce nor suffix; turn 2 output did not contain the expected suffix | no runner yet |
| `grok_live_session` | passed limited continuity probe | isolated git cwd, `grok --single`, explicit `--resume <sessionId>`, JSON stdout | session id captured; first assistant `text` length was 5 and did not reveal the nonce; turn 2 assistant `text` length was 4 and exactly matched the expected suffix; turn 2 prompt contained neither nonce nor suffix; full process output did contain prompt material in stderr | implemented the narrow Grok runner/proof path, parsing JSON stdout `text` only |

After adding the checked-in `grok_live_session` runner, a second approved real
proof was rerun from an isolated temporary git working directory through
`live-agent continuity-proof --provider-kind grok_live_session --connection-kind
live_session --approve-real-providers`. It returned `status: "ok"`, captured a
safe session-id suffix, kept the first reply length at 5 and the second reply
length at 4, required the first reply to be exactly `READY`, did not reveal the
continuity code or suffix in the first reply, did not replay the code in the
second prompt, and matched the expected suffix. This is still only two-turn
provider-owned resume evidence; it is not room admission, tool-safety,
stop/restart, or official-turn-quality evidence.

After that continuity prerequisite passed, a Grok-only session bundle was
generated through the GUI discovery API with exact approval for `grok-live`.
The generated resident config contained only `grok-live` and passed preflight.
An approved `live-agent real-session-smoke` then returned `status: "ok"` with
`start_status: "ready"`, `expected_agent_count: 1`, `connected_agent_count: 1`,
`reply_probe_status: "ok"`, `reply_probe_count: 1`,
`reply_probe_ok_count: 1`, `stop_status: "stopped"`, and
`post_stop_process_status: "stopped"`. Public evidence keeps only these safe
status/count fields and a short session-id suffix from the separate continuity
proof; it does not store raw prompts, raw Grok replies, full session ids,
stdout/stderr, account data, local prompt-file paths, or generated config paths.
