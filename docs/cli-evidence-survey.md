# CLI Evidence Survey

This file records real local CLI evidence for provider-owned live-room
participation beyond Codex and Kiro. It is intentionally empty of positive
claims until a provider has been tested through a real CLI/session surface.

No real CLI continuity evidence has been recorded yet for the providers in this
survey. Discovery, executable presence, config generation, and one-shot
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

## Current Rows

| Provider kind | Evidence status | Current conclusion | Next evidence needed |
| --- | --- | --- | --- |
| `claude_code` | unverified | Planned only. Do not present one-shot `local_cli` as a resident Claude Code session. | Inspect real CLI/channel behavior, then prove provider-owned room loop or native resume. |
| `cursor` | unverified | Planned only. No Cursor Composer/session continuity proof is recorded. | Inspect real CLI entrypoint and prove context continuity before adding a runner. |
| `antigravity_cli` | unverified | Candidate only. Self-service is preferred if the CLI can own its own loop. | Record real command contract and two-turn continuity behavior. |
| `grok_build_cli` | unverified | Candidate only. Actual CLI contract is not documented here yet. | Record installed command/version, completion boundary, and continuity behavior. |
| `hermes_cli` | unverified | Memory/profile inspiration only until a live CLI contract is proven. | Gate memory artifacts separately, then inspect any CLI session behavior. |
| `openclaw_cli` | unverified | Memory/profile inspiration only until a live CLI contract is proven. | Gate memory artifacts separately, then inspect any CLI session behavior. |

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
