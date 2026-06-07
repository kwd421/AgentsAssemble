# Codex Persistent Bridge Probe

Date: 2026-06-07

This note records the narrow Codex persistent-provider bridge check that followed
the Codex 5.3 Spark room latency issue.

## Observed CLI Surface

Local CLI:

```text
codex-cli 0.130.0
```

Checked commands:

- `codex --help`
- `codex exec --help`
- `codex resume --help`
- `codex mcp-server --help`
- `codex exec-server --help`

The public help surface exposes:

- `codex exec` and `codex exec resume` for non-interactive per-turn execution.
- `codex resume` for interactive TUI resume.
- `codex mcp-server` over stdio.
- experimental `codex exec-server`.

This does not prove a room-safe persistent provider channel where
AgentsAssemble can keep one Codex provider process open, push multiple room
messages into that same process, and receive one clean visible room reply per
message.

## Current Product Classification

Codex resident agents remain `codex_exec_resume` / 호출형:

- AgentsAssemble runner can stay alive and poll or receive room events.
- Codex provider execution still pays a per-turn `exec/resume` invocation cost.
- The 2026-06-06 Codex 5.3 Spark room check measured roughly 4-6 seconds from
  human lobby message to reply with `poll_interval=0.25` and `cooldown=0`.
- That evidence points to provider invocation cost, not polling or cooldown.

Codex must not be shown as 진짜 상주형 until a later checked-in proof demonstrates
a persistent process, PTY, stream, socket, MCP, or app-server contract that can
handle repeated room messages without a new per-turn `exec/resume` call.
