# CLI Evidence Survey

This file records local CLI contract evidence for provider-owned live-room
participation beyond Codex and Kiro. It is intentionally conservative: help and
version output can identify a candidate session surface, but only an approved
two-turn continuity probe can prove provider-owned context continuity.

Grok is still the only non-Codex/Kiro provider with both a passing two-turn
continuity probe and a passing bounded room start/probe/stop smoke in this
survey. Cursor and Antigravity now have narrower positive continuity probes, and
Hermes has an ambiguous resume probe contaminated by a fresh no-resume recall
control. None of those three has a checked-in resident runner, room
start/probe/stop evidence, or official-turn quality evidence. Discovery,
executable presence, config
generation, help text, and one-shot `local_cli` output are not evidence of
resident participation or provider-owned context continuity.

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
Status/account inspection is not safe public evidence and is not used for these
continuity verdicts.

## Current Rows

| Provider kind | Evidence status | Contract evidence | Current conclusion | Next evidence needed |
| --- | --- | --- | --- | --- |
| `claude_code` | absent in PATH | `claude` was not found by PATH discovery in this slice. | Planned only. Do not present one-shot `local_cli` as a resident Claude Code session. | Inspect real CLI/channel behavior, then prove provider-owned room loop or native resume. |
| `cursor` | continuity-proven-limited-no-runner | `cursor-agent --version` returned `2026.05.24-dda726e`; `cursor-agent --help` exposes `--print`, `--output-format text|json|stream-json`, `--resume`, `--continue`, `--mode ask|plan`, `--sandbox enabled|disabled`, `--workspace`, and `create-chat`. `cursor-agent create-chat --help` says it creates an empty chat id. The app `cursor` CLI is editor/open-file oriented, not the agent surface. | Earlier approved explicit-chat-id and workspace-continue probes failed, but a later approved fresh `create-chat` id plus `--resume <chat_id> --print --mode ask --sandbox enabled --trust --workspace <tmp>` probe recalled the previous-turn codename exactly. This proves a narrow provider-owned chat-id resume surface on this local install without writing files into the throwaway workspace. | Do not add a Cursor runner yet. Next slice should prove a bounded room start/probe/stop path or an agent-owned self-service loop, and must keep sandbox/tool behavior `advisory` until a constrained launcher exists. |
| `antigravity_cli` | continuity-proven-limited-no-runner | `agy --version` returned `1.0.2`; `agy --help` exposes `--print`, `--prompt-interactive`, `--continue`, `--conversation`, `--sandbox`, and `--dangerously-skip-permissions`. | A later approved `agy --print` plus `--continue` probe recalled the previous-turn codename from the provider-owned Antigravity store. This is real continuity evidence for the local `--continue` surface, but the output also showed Antigravity inspecting its global local store, the throwaway cwd gained a `.antigravitycli` symlink to user config, and an explicit `--conversation <id>` follow-up did not recall the codename. | Do not add an Antigravity runner yet. Revisit with a documented conversation-id flow, a bounded output contract, and a self-service room loop that owns its own room polling without exposing global store paths or raw provider output. |
| `grok_live_session` | room-smoke-proven-limited | `grok --version` returned `grok 0.2.3 (14d81fd875e)`; `grok --help` exposes `--single`, `--prompt-file`, `--prompt-json`, `--resume`, `--continue`, output formats, `--sandbox`, `--no-memory`, `--no-subagents`, and session listing. Running bare `grok` starts a TUI and timed out under bounded non-interactive discovery. | Approved isolated-git-cwd probes using JSON stdout and explicit `--resume <sessionId>` recalled the suffix exactly from the JSON `text` field. The checked-in runner uses the safer `--prompt-file` JSON-output shape and later `--resume <sessionId>`. An approved Grok-only generated room bundle passed the bounded start/probe/stop smoke with safe counts: start ready, connected 1/1, reply probe 1/1, stop stopped, post-stop stopped. A deeper approved smoke with one official round plus restart returned safe counts: start ready, connected 1/1, initial reply probe 1/1, official round timeout 0/1 answered, restart ready, post-restart reply probe 1/1, stop stopped, post-stop stopped. A checked-in fake Grok lifecycle regression now proves `official_turn_timeout_seconds` applies only to official command calls and preserves timeout error categorization. A 2026-05-28 approved rerun prepared a Grok-only bundle with that dedicated official-turn budget, but the same-day strict continuity baseline failed `first_reply_not_ready` before official-turn smoke could be measured; safe fields still showed session capture and suffix recall. Full process output can echo prompt material in stderr, so raw stderr/stdout logs must not be persisted as proof artifacts. | Grok may be used for controlled local resident lobby/restart experiments with explicit approval. Do not claim official-turn quality yet: the first deeper smoke disproved it for this local path by timing out, and the dedicated timeout rerun was blocked by a strict continuity baseline failure. Next prove a stable strict continuity baseline or redesign that proof contract, then rerun official-turn quality; recover behavior, tool safety, and sandboxing remain unproven. |
| `grok_build_cli` | contract-known-but-not-runner | `grok agent --help` exposes `stdio`, `headless`, `serve`, and `leader` modes. | These surfaces may become useful later, but they are not the checked-in continuity runner. | Do not route resident continuity through `grok_build_cli` yet; use `grok_live_session` for the narrow JSON stdout resume path. |
| `hermes_cli` | continuity-ambiguous-no-runner | `hermes --version` returned `Hermes Agent v0.11.0 (2026.4.23)`; the version output also includes a local project path that is intentionally not copied into committed evidence. `hermes --help` exposes `chat`, `sessions`, `mcp`, and related commands. `hermes chat --help` exposes `--query`, `--resume`, `--continue`, `--quiet`, `--pass-session-id`, `--source`, `--ignore-user-config`, `--ignore-rules`, and `--max-turns`. `hermes sessions --help` exposes list/export/delete/rename tooling. | An approved `hermes chat --query` seed returned a session id and ready marker; `hermes chat --query --resume <session_id>` recalled the prior codeword, but a fresh no-resume control also recalled it. `hermes sessions list --source tool` showed no rows while `hermes sessions export --session-id <session_id>` exported one session record. This proves a provider-owned context surface exists, but it does not prove deterministic session-id-only continuity. See `docs/provider-continuity/hermes.md`. | Do not add a Hermes runner yet. Revisit only with a clean session-id-only recall path whose fresh no-resume control fails, or with a self-service loop that owns room polling without relying on hidden global recall. |
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
| `cursor` | passed limited chat-id resume recall after earlier failures | `cursor-agent create-chat`, then `cursor-agent --resume <chat_id> --print --mode ask --sandbox enabled --trust --workspace <tmp>` for both turns | a fresh empty chat id was created; first resumed turn returned exactly the expected ready marker; second resumed turn recalled the prior codename exactly; the throwaway workspace stayed empty and the repo remained unchanged except pre-existing untracked local residue | no runner yet |
| `antigravity_cli` | passed limited `--continue` recall, failed explicit conversation recall | isolated-cwd `agy --print --continue`; follow-up `agy --print --conversation <id>` | first call returned exactly the expected ready marker; second call recalled the prior codename without the second prompt repeating it; process output showed global Antigravity store inspection and the throwaway cwd gained a `.antigravitycli` symlink to user config; explicit conversation-id resume did not recall the codename | no runner yet |
| `hermes_cli` | ambiguous session-id resume recall | isolated-cwd `hermes chat --query --pass-session-id`, then `hermes chat --query --resume <session_id>`, plus a fresh no-resume control | seed returned a session id and ready marker; resume recalled the prior codeword; fresh no-resume control also recalled the prior codeword; `sessions export --session-id` exported one record, but `sessions list --source tool` returned no rows | no runner yet |
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

An approved Antigravity refresh on 2026-05-27 used local `agy` 1.0.2 from an
isolated temporary directory. `agy --print --continue` recalled the prior
codename, so the current most-recent conversation surface can preserve
provider-owned context across two calls. That positive result is deliberately
not promoted to a resident runner: the process output described inspecting
global Antigravity/Gemini local stores, the throwaway directory gained only a
`.antigravitycli` symlink to user config, and an explicit `--conversation <id>`
call did not recall the codename. Public evidence keeps only these booleans and
outcome descriptions; it does not store raw prompts, raw provider replies, full
conversation ids, absolute config paths, account data, or provider logs.

An approved Cursor refresh on 2026-05-27 used local `cursor-agent`
`2026.05.24-dda726e`. `create-chat` minted a fresh empty chat id, then two
bounded `--resume <chat_id> --print --mode ask --sandbox enabled --trust
--workspace <tmp>` calls proved that Cursor Agent could recall the previous-turn
codename from that chat id. The temporary workspace remained empty. This is
provider-owned continuity evidence for the chat-id resume surface, but it is
not a resident-room runner, room admission proof, official-turn-quality proof,
tool-safety proof, or sandbox proof. Public evidence keeps only the verdict,
safe command shape, and outcome descriptions; it does not store raw prompts, raw
Cursor replies, full chat ids, account data, workspace paths, or provider logs.

An approved Hermes refresh on 2026-05-27 used local `hermes` v0.11.0. A seed
turn returned a session id, and a `--resume <session_id>` turn recalled the
prior codeword. The fresh no-resume control also recalled the codeword, so this
is not clean session-id-specific continuity evidence. The run is recorded as
`continuity-ambiguous-no-runner`; no raw prompts, raw Hermes replies, full
session ids, codewords, account data, local paths, provider logs, or exported
transcript bodies are committed.

An approved Grok official-turn follow-up on 2026-05-28 first generated a
Grok-only temporary resident bundle from discovery, kept `timeout_seconds` at
240, added `official_turn_timeout_seconds` 360, and passed preflight for exactly
one `grok-live` resident. The required same-day strict continuity baseline then
returned `status: "failed"` with `reason: "first_reply_not_ready"`. Safe fields
still showed `session_id_captured: true`, `first_reply_length: 6`,
`second_reply_length: 4`, no prompt replay, no code or suffix leak in the first
reply, and `expected_suffix_matched: true`. Because the strict proof status
failed, the official-turn room smoke was skipped; this preserves the prior
official-turn timeout verdict instead of mixing it with a shaky baseline. See
`docs/provider-continuity/grok-official-turn.md`.

Earlier, after the previous continuity prerequisite passed, a Grok-only session
bundle was generated through the GUI discovery API with exact approval for
`grok-live`.
The generated resident config contained only `grok-live` and passed preflight.
An approved `live-agent real-session-smoke` then returned `status: "ok"` with
`start_status: "ready"`, `expected_agent_count: 1`, `connected_agent_count: 1`,
`reply_probe_status: "ok"`, `reply_probe_count: 1`,
`reply_probe_ok_count: 1`, `stop_status: "stopped"`, and
`post_stop_process_status: "stopped"`.

A later deeper approved smoke used `--official-round-smoke --restart-smoke`.
It returned `status: "failed"` because the official round timed out:
`official_rounds_status: "timeout"`, `official_round_count: 1`,
`official_answered_round_count: 0`, and `official_timeout_round_count: 1`.
The same run still proved the restart/probe surface with
`restart_status: "ready"`, `post_restart_connected_agent_count: 1`,
`post_restart_reply_probe_status: "ok"`,
`post_restart_reply_probe_count: 1`,
`post_restart_reply_probe_ok_count: 1`, `stop_status: "stopped"`, and
`post_stop_process_status: "stopped"`. Public evidence keeps only these safe
status/count fields and short session-id suffixes from separate continuity
proofs; it does not store raw prompts, raw Grok replies, full session ids,
stdout/stderr, account data, local prompt-file paths, or generated config paths.
