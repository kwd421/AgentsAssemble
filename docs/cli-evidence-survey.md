# CLI Evidence Survey

This file records local CLI contract evidence for provider-owned live-room
participation beyond Codex and Kiro. It is intentionally conservative: help and
version output can identify a candidate session surface, but only an approved
two-turn continuity probe can prove provider-owned context continuity.

No real non-Codex/Kiro CLI continuity evidence has been recorded yet in this
survey. Discovery, executable presence, config generation, help text, and
one-shot `local_cli` output are not evidence of resident participation or
provider-owned context continuity.

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
| `cursor` | contract-known, continuity-unproven | `cursor-agent --version` returned `2026.05.24-dda726e`; `cursor-agent --help` exposes `--print`, `--output-format text|json|stream-json`, `--resume`, `--continue`, `--mode ask|plan`, `--sandbox enabled|disabled`, and `create-chat`. The app `cursor` CLI is editor/open-file oriented, not the agent surface. | Cursor Agent has a plausible provider-owned resume/session surface, but no two-turn recall or room-loop proof is recorded. Treat it as unproven, not resident-ready. | With approval, run a nonce continuity probe that creates/resumes a chat without replaying turn 1; only then design a `cursor_agent` runner or self-service wrapper. |
| `antigravity_cli` | contract-known, continuity-unproven | `agy --version` returned `1.0.1`; `agy --help` exposes `--print`, `--prompt-interactive`, `--continue`, `--conversation`, `--sandbox`, and `--dangerously-skip-permissions`. | Antigravity CLI has single-prompt and resume-looking flags. Context durability is unproven; do not classify it as provider-owned resident participation yet. | With approval, test `--print` plus `--continue`/`--conversation` continuity under `--sandbox` where possible, then decide whether a provider-specific runner is justified. |
| `grok_build_cli` | contract-known, continuity-unproven | `grok --version` returned `grok 0.2.2 (c9b7cdec23a)`; `grok --help` exposes `--single`, `--prompt-file`, `--prompt-json`, `--resume`, `--continue`, output formats, `--sandbox`, `--no-memory`, `--no-subagents`, and session listing. `grok agent --help` exposes `stdio`, `headless`, `serve`, and `leader` modes. Running bare `grok` starts a TUI and timed out under bounded non-interactive discovery. | Grok is the strongest non-Codex/Kiro candidate for a native-ish bridge because it exposes stdio/leader/session surfaces. It is still continuity-unproven. | With approval, run a no-tool two-turn `--single`/`--resume` or `agent stdio` probe, then decide whether to add a Grok-specific resident adapter. |
| `hermes_cli` | contract-known, continuity-unproven | `hermes --help` exposes `chat`, `sessions`, `mcp`, and related commands. `hermes chat --help` exposes `--query`, `--resume`, `--continue`, `--quiet`, `--source`, `--ignore-user-config`, and `--ignore-rules`. `hermes sessions --help` exposes list/export/delete/rename tooling. A bounded `hermes version` invocation did not return before timeout in this environment. | Hermes has chat/session surfaces, but status/account inspection is not safe public evidence and no continuity proof is recorded. Treat it as unproven. | With approval, use `hermes chat --query --quiet --source tool` and a resume flag only after confirming it will not expose account data or hidden session dumps. |
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
