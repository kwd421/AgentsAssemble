# AgentsAssemble

AgentsAssemble is a local-first multi-agent council orchestrator for AI coding agents.

The v0 prototype is a terminal-first council engine with a simple local browser GUI for inspection. It runs a small council where three isolated roles research and debate a question, then writes Markdown and JSON artifacts to disk.

## Current Demo

Run the mock council:

```bash
python3 -m agentsassemble.cli demo --adapter mock
```

Run with a host-approved agent config:

```bash
python3 -m agentsassemble.cli demo --adapter mock --agent-config configs/agents.example.json
```

This creates:

```text
.agentsassemble/
  meetings/
    <meeting_id>/
      agenda.md
      transcript.md
      decision.md
      meeting.json
      private_research/
      roles/
      tasks/
```

The demo roles are:

- `lore_lawyer` / 설정충
- `show_me_the_feats` / 공식이뭘알아
- `fanboard_skeptic` / 만갤러

The working product name is `AgentsAssemble`.
The intended installed CLI command is `assemble`.

## Install Locally

From the repository root:

```bash
python3 -m pip install -e .
```

Then run:

```bash
assemble demo --adapter mock
```

## Running Tests

Tests use the standard-library `unittest` runner. Run the whole suite:

```bash
make test            # or: python3 -m unittest discover -s tests -t .
```

Run a single module:

```bash
make test-module M=tests.test_gui_server
```

Node.js is required for the frontend/static UI smoke tests; install frontend
dependencies once with `make frontend-deps`. CI runs the same suite via
`.github/workflows/tests.yml`.

## Local GUI

Run the local browser UI:

```bash
python3 -m agentsassemble.cli gui
```

After editable install, the intended command is:

```bash
assemble gui
```

Default URL:

```text
http://127.0.0.1:8765
```

`/` serves the React operator console once the frontend is built. If the build
is missing, the GUI returns a build-required message instead of showing the
retired vanilla console. The React app is also aliased at `/app/`. Build the
React default with:

```bash
npm --prefix frontend run build
```

The `frontend/` React/Vite frontend is the default operator surface and the
default entry point at `/`; legacy static routes are retired. To see the current
launch commands, proxy target, and build status:

```bash
python3 -m agentsassemble.cli frontend-info
```

The completed default-route flip and its operator-verified browser-parity caveat
are recorded in `docs/product/legacy-react-parity-matrix.md`.

The GUI has four tabs:

- `lobby` / 로비: informal staging room, readiness, and deploy intent
- `live` / 실황: chat-like council view
- `board` / 작전판: structured claims, rebuttals, and synthesis
- `archive` / 아카이브: generated artifacts and research notes

## Live Room Status

The supported product concept is now **Agent Session**.

An Agent Session is a resumable local AI CLI session attached to one room, with
persisted participant/session identity, model, effort, sandbox/permission
settings, and one ordered room event stream. The room is turn-based for now:
when an agent is called, it receives the ordered room conversation and appends
its result back to that same room event stream.

Active room state is separate from archived meeting artifacts. New room/session
state lives under:

```text
.agentsassemble/
  rooms/
    <room_id>/
      room.json
      participants.json
      sessions.json
      events.jsonl
      media/
      handoffs/
```

Free/silent room modes are intentionally disabled for now. The frontend should
show turn-based Agent Sessions only; old saved `quiet`, `free`, or `turn`
settings normalize to `ordered`.

Claude Code print-mode bridging is disabled. AgentsAssemble must not invoke
`claude -p` or silently fall back to an Anthropic API path for room
participation.

Current implemented scope:

- file-backed RoomStore state
- turn-based ordered rooms only
- canonical Agent Session roster state
- state-only Agent Session attach/resume by default
- Codex launch-plan dry-run for an explicit process-resume path
- leave/kick/export persisted in room state

The primary CLI path is:

```bash
assemble room resume <room-id> --agent <agent-id> --session <session-id> --provider codex --dry-run
```

By default this attaches state only and reports `process_status: not_started`.
Use explicit process execution only after reviewing the returned launch plan.

Not yet supported as user-facing product paths: free/silent rooms, MCP as a
normal connection path, Claude print-mode bridging, generic hard provider
sandboxing, and provider media viewing unless the provider can consume the
room media manifest.

## Codex Adapter

The Codex adapter has a first smoke path through `codex exec`.

The current implementation:

- uses the shared local Codex authentication/config
- invokes `codex exec --sandbox read-only --ignore-rules --skip-git-repo-check`
- writes each call's final response with `--output-last-message`
- records Codex command metadata and session id when available
- keeps role files, history files, and research artifacts isolated by role

The full Codex council demo may make several model calls, so use mock mode first when checking artifact flow.

To try separate Codex-backed role sessions, start from:

```bash
python3 -m agentsassemble.cli demo --adapter codex --agent-config configs/codex-sessions.example.json
```

This still runs in meeting mode. It should not grant filesystem write, git write, push, secrets, or implementation permission.

## Agent Runtime Config

Agent runtime config is host-approved. External or remote agents may submit `incoming_agents`, but those records are only requests and audit material. Only `agent_bindings` participate in the meeting.

Supported top-level fields:

- `providers`: provider records such as `mock`, `codex`, `cursor`, `claude_code`, `gemini`, `grok`, or local providers.
- `permission_profiles`: named permission sets. Meeting mode should stay read-only by default.
- `incoming_agents`: external/self-declared agent profiles waiting for host review.
- `agent_bindings`: final role-to-agent/provider assignments approved by the host.

Examples:

- `configs/agents.example.json`: safe mock config with incoming Cursor/Claude Code requests.
- `configs/codex-sessions.example.json`: Codex read-only meeting config for real experiments.

## Provider Architecture

The current demo still supports `mock` and `codex`, but the meeting artifact now records provider configs, agent bindings, provider capabilities, and meeting-only permission profiles.

Future provider families:

- Claude/Gemini/Grok/API models for meeting research and critique
- Codex/Claude Code/Cursor CLI for implementation after `decision.md`
- local OpenAI-compatible or Ollama-style models for private/offline fallback
- Hermes/OpenClaw-style memory/profile packs as reviewed artifacts, not raw session dumps

See `docs/provider-architecture.md`.

## Seed And Plan

- Seed: `seeds/seed_b062b3d88b5d.yaml`
- Implementation plan: `plans/v0-implementation.md`
