# CLI Evidence Survey

This file records local CLI contract evidence for provider-owned live-room
participation beyond Codex and Kiro. It is intentionally conservative: help and
version output can identify a candidate session surface, but only an approved
two-turn continuity probe can prove provider-owned context continuity.

Grok and Cursor are now the non-Codex/Kiro providers with both a passing
two-turn continuity probe and a passing bounded room start/probe/stop smoke in
this survey. Grok has the deeper official-turn/restart smoke evidence; Cursor's
room evidence is only the initial one-resident start/probe/stop shape.
Antigravity has a non-isolated `--continue` recall but the isolated
disambiguation failed to find a deterministic handle and still showed generated
symlinks resolving outside the proof root. Hermes has an ambiguous resume probe
contaminated by a fresh no-resume recall control. Discovery, executable
presence, config
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

`live-agent continuity-proof` treats the first-turn ready marker as a narrow
protocol token: after trimming whitespace, `READY` may be followed by at most
one terminal punctuation mark. The strict `first_reply_is_ready` field still
records exact `READY`, while `first_reply_ready_normalized` records the narrow
protocol match. Session capture, first-reply code or suffix leak checks,
second-prompt replay rejection, and suffix recall remain strict.

## Current Rows

| Provider kind | Evidence status | Contract evidence | Current conclusion | Next evidence needed |
| --- | --- | --- | --- | --- |
| `claude_code` | absent in PATH | `claude` was not found by PATH discovery in this slice. | Planned only. Do not present one-shot `local_cli` as a resident Claude Code session. | Inspect real CLI/channel behavior, then prove provider-owned room loop or native resume. |
| `cursor` / `cursor_live_session` | room-smoke-proven-limited | `cursor-agent --version` returned `2026.05.24-dda726e`; `cursor-agent --help` exposes `--print`, `--output-format text|json|stream-json`, `--resume`, `--continue`, `--mode ask|plan`, `--sandbox enabled|disabled`, `--workspace`, and `create-chat`. `cursor-agent create-chat --help` says it creates an empty chat id. The app `cursor` CLI is editor/open-file oriented, not the agent surface. | Earlier approved explicit-chat-id and workspace-continue probes failed, but later approved `create-chat` plus `--resume <chat_id> --print --mode ask --sandbox enabled --trust --workspace <tmp>` probes recalled the previous-turn suffix in the same chat and same workspace. A fresh-chat negative control did not recall the suffix, which avoids the Hermes-style global-recall ambiguity for this probe. A same-chat different-workspace control also did not recall the suffix, so the continuity surface appears tied to preserving both chat id and workspace. The checked-in `cursor_live_session` runner now preserves both values, has fake continuity/preflight/discovery tests, passed an approved real `continuity-proof` through the runner, and then passed an approved real room smoke with one resident: start ready, connected 1/1, reply probe 1/1, stop stopped, post-stop stopped. | Cursor may be used for controlled local resident lobby start/probe/stop experiments with explicit approval on this local install. Do not present one-shot `--print` as resident participation, and do not claim official-turn quality, restart, recover, tool safety, future billing stability, production readiness, or sandboxing; launch enforcement remains `advisory`. |
| `antigravity_cli` | global-store-contaminated-no-runner | `agy --version` returned `1.0.2`; `agy --help` exposes `--print`, `--prompt-interactive`, `--continue`, `--conversation`, `--sandbox`, and `--dangerously-skip-permissions`. The help surface does not expose a session or conversation listing subcommand; attempted `sessions --help` and `conversations --help` returned the top-level help shape. | A prior approved non-isolated `agy --print` plus `--continue` probe recalled the previous-turn codename from the provider-owned Antigravity store. A later approved isolated-HOME disambiguation did not reproduce suffix recall through `--continue`, `--conversation <candidate>`, a same-candidate different-cwd call, or a fresh no-resume control. The isolated run also created `.antigravitycli` symlinks that resolved outside the temporary proof root. | Do not add an Antigravity runner. Revisit only with a documented conversation-id flow whose negative controls are clean, or use a self-service room loop where Antigravity owns room polling without exposing global store paths or raw provider output. |
| `grok_live_session` | room-smoke-proven-limited | `grok --version` returned `grok 0.2.3 (14d81fd875e)`; `grok --help` exposes `--single`, `--prompt-file`, `--prompt-json`, `--resume`, `--continue`, output formats, `--sandbox`, `--no-memory`, `--no-subagents`, and session listing. Running bare `grok` starts a TUI and timed out under bounded non-interactive discovery. | Approved isolated-git-cwd probes using JSON stdout and explicit `--resume <sessionId>` recalled the suffix exactly from the JSON `text` field. The checked-in runner uses the safer `--prompt-file` JSON-output shape and later `--resume <sessionId>`. An approved Grok-only generated room bundle passed bounded start/probe/stop smoke. A deeper approved smoke first timed out on one official round, then a fake lifecycle regression proved `official_turn_timeout_seconds` applies only to official command calls. After ready-marker normalization, a 2026-05-28 approved rerun restored the continuity baseline and an approved Grok-only official-round plus restart smoke returned safe counts: start ready, connected 1/1, initial reply probe 1/1, official round answered 1/1 with 0 timeouts, restart ready, post-restart reply probe 1/1, stop stopped, post-stop stopped. Full process output can echo prompt material in stderr, so raw stderr/stdout logs must not be persisted as proof artifacts. | Grok may be used for controlled local resident lobby, official-turn, and restart experiments with explicit approval on this local install. Do not claim recover behavior, tool safety, future billing stability, production readiness, or sandboxing; launch enforcement remains `advisory`. |
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
the current staging config it reports only static approval or unsupported
statuses unless a checked-in provider-specific runner is both configured and
approved for execution.

## Real Continuity Probe Summary

These probes used approved real CLI model calls in isolated temporary
directories. Public records keep only safe booleans and lengths; they do not
store raw prompts, raw provider output, full session ids, account data, config
paths, provider logs, nonce values, or nonce suffixes.

| Provider kind | Probe result | Resume mechanism | Safe evidence | Runner implication |
| --- | --- | --- | --- | --- |
| `cursor_live_session` | passed limited chat-id resume recall with workspace control; runner and room smoke implemented | `cursor-agent create-chat`, then `cursor-agent --resume <chat_id> --print --mode ask --sandbox enabled --trust --workspace <tmp>` for bounded same-chat and control turns; later `live-agent real-session-smoke` for a one-resident room probe | a fresh empty chat id was created; first resumed turn returned exactly the expected ready marker; second resumed turn recalled the prior suffix exactly; a fresh-chat negative control did not recall; a same-chat different-workspace control did not recall; the checked-in runner preserves both chat id and workspace in fake tests; an approved real `continuity-proof` returned `status: "ok"`; an approved real room smoke returned start ready, connected 1/1, reply probe 1/1, stop stopped, and post-stop stopped | use for controlled local lobby start/probe/stop experiments only; official-turn quality, restart, recover, tool safety, future billing, and sandboxing remain unproven |
| `antigravity_cli` | passed limited non-isolated `--continue` recall, failed isolated disambiguation | non-isolated `agy --print --continue`; later temporary git cwd with isolated `HOME`/`XDG_*` for seed, `--continue`, `--conversation <candidate>`, same-candidate different-cwd, and fresh no-resume control | the earlier non-isolated call recalled a prior codename without the second prompt repeating it, but process output showed global Antigravity store inspection; the isolated run returned `0` for every call but did not produce the ready marker or expected suffix through any resume path; generated `.antigravitycli` symlinks resolved outside the temporary proof root | no runner; use self-service only after a wrapper owns room polling |
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

An approved Antigravity refresh on 2026-05-27 used local `agy` 1.0.2 from a
temporary cwd, but it did not isolate the provider home/config store. `agy
--print --continue` recalled the prior codename, so the current most-recent
conversation surface can preserve provider-owned context across two calls. That
positive result was deliberately not promoted to a resident runner and is not a
resident-room runner: the process output described inspecting global
Antigravity/Gemini local stores, the throwaway directory gained only a
`.antigravitycli` symlink to user config, and an explicit `--conversation <id>`
call did not recall the codename.

A later approved Antigravity disambiguation on 2026-05-28 used a temporary git
cwd plus isolated `HOME`, `XDG_CONFIG_HOME`, `XDG_DATA_HOME`, and
`XDG_CACHE_HOME`. The seed, `--continue`, `--conversation <candidate>`,
same-candidate different-cwd, and fresh no-resume control calls all returned
`0`, but none produced the expected suffix and the seed did not produce the
ready marker. The run found one UUID-shaped candidate from generated project
metadata, not a proven conversation id, and created cwd `.antigravitycli`
symlinks that resolved outside the temporary proof root. Public evidence keeps
only these booleans, counts, and outcome descriptions; it does not store raw
prompts, raw provider replies, full conversation or project ids, absolute
config paths, account data, provider logs, or symlink targets. See
`docs/provider-continuity/antigravity.md`.

Approved Cursor refreshes used local `cursor-agent` `2026.05.24-dda726e`.
`create-chat` minted fresh empty chat ids, then bounded
`--resume <chat_id> --print --mode ask --sandbox enabled --trust --workspace
<tmp>` calls proved that Cursor Agent could recall the previous-turn suffix
from the same chat id and same workspace. A fresh-chat negative control did not
recall the suffix. A same-chat different-workspace control also did not recall
the suffix, so a future runner must preserve both the Cursor chat id and
workspace path unless later proof shows a workspace-independent resume surface.
The temporary workspaces remained empty outside `.git`. The checked-in
`cursor_live_session` runner preserves both values, and an approved real
`live-agent continuity-proof` through that runner later returned `status: "ok"`
with the same safe marker, length, prompt-replay, and suffix-match properties.
A follow-up approved real room smoke then used one Cursor resident and returned
safe counts only: start ready, connected 1/1, reply probe 1/1, stop stopped,
and post-stop stopped. This proves bounded room admission, one lobby probe, and
cleanup for the local install; it is still not official-turn-quality proof,
restart proof, recover proof, tool-safety proof, future billing proof, or
sandbox proof. Public evidence keeps only the verdict, safe command shape,
lengths, booleans, counts, and outcome descriptions; it does not store raw
prompts, raw Cursor replies, full chat ids, codewords, suffix values, account
data, workspace paths, or provider logs. See
`docs/provider-continuity/cursor.md` and
`docs/provider-continuity/cursor-runner.md`.

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
one `grok-live` resident. The first same-day strict continuity baseline returned
`status: "failed"` with `reason: "first_reply_not_ready"` while safe fields
still showed session capture and suffix recall. After the ready-marker contract
was narrowed, a rerun returned `status: "ok"` with exact ready marker, normalized
ready marker, session capture, no prompt replay, no code or suffix leak, and
expected suffix recall. The following approved Grok-only official-round plus
restart smoke returned `status: "ok"` with safe counts: start ready, connected
1/1, lobby probe 1/1, official round answered 1/1 with 0 timeouts, restart
ready, post-restart probe 1/1, stop stopped, and post-stop process stopped. See
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
