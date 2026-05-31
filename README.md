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

`/` serves the React operator console once the frontend is built; until then it
falls back to the dependency-light vanilla console. The vanilla console always
stays reachable at `/legacy/`, and the React app is also aliased at `/app/`.
Build the React default with:

```bash
npm --prefix frontend run build
```

The `frontend/` React/Vite frontend is the default operator surface and the
default entry point at `/`; the vanilla console is the dependency-light
fallback. To see the current launch commands, proxy target, and build status:

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

The live-room branch now has a local GUI room, file-backed event streams,
live-agent roster and presence records, supervised resident process groups,
moderator-controlled official turns, session start/resume/restart/recover/stop
commands, credential-free smoke checks, shared meeting memory artifacts, and an
experimental Codex CLI resident path based on `codex exec resume`.

This is not yet the final native multi-provider room. Codex remains the most
advanced resident path, Kiro has an experimental `kiro chat --resume-id`
resident path, Cursor has a narrow `cursor-agent create-chat` plus `--resume`
resident path with one approved start/probe/stop room smoke, and Grok has a
narrower experimental JSON stdout `grok --resume` continuity path with deeper
official-turn/restart smoke evidence. The native Claude Code/Antigravity/
Hermes/OpenClaw integrations are not complete, and non-Codex local CLI read-only is not a hard OS sandbox.
Ordinary `local_cli` is a stateless delegate path, not a provider-owned resident
session. `terminal_session`, `self_service`, and remote bridge participants
still rely on policy, approval, and audit metadata unless a real sandboxed
launcher is added and verified.
No-Tailscale multi-host is still a separate product axis: the current Phase 5
slice adds only a LAN invite token PoC for a future
`native_remote_room_client`, documented in `docs/no-tailscale-multi-host.md`.
It does not open the room to the internet, start provider CLIs, or make
relay/WebRTC ready.
Public room surfaces now expose `sandbox_enforcement` alongside join semantics
and context durability. The shared `SandboxLauncher` mapping reports
`codex_readonly` for Codex `codex exec --sandbox read-only --ignore-rules`,
`advisory` for generic local CLI/PTY/self-service/remote bridge paths, and
`os_sandboxed` only for a future provider launched through a verified OS-level
sandbox.

Safe fake resident session quickstart:

Terminal 1:

```bash
python3 -m agentsassemble.cli gui --host 127.0.0.1 --port 8765 --output-root .agentsassemble
```

Terminal 2:

```bash
python3 -m agentsassemble.cli live-agent preflight --config configs/live-agents.start-session.example.json
python3 -m agentsassemble.cli live-agent start-session \
  --server http://127.0.0.1:8765 \
  --meeting-id resident-fake-demo \
  --group-id resident-fake-demo \
  --council-config configs/demo-council.json \
  --agent-config configs/agents.start-session.example.json \
  --live-agent-config configs/live-agents.start-session.example.json \
  --connect-timeout 5 \
  --run-remaining-rounds \
  --round-timeout 8 \
  --max-rounds 2 \
  --finalize-after-rounds \
  --wait-ready
python3 -m agentsassemble.cli live-agent stop-session \
  --server http://127.0.0.1:8765 \
  --meeting-id resident-fake-demo \
  --group-id resident-fake-demo
```

`live-agent session-smoke --server http://127.0.0.1:8765 --json` remains the
stronger diagnostic path for local fake transports, restart/recover, cleanup,
and the same finalization evidence.

Experimental Codex live session quickstart:

```bash
python3 -m agentsassemble.cli live-agent preflight --config configs/live-agents.codex-session.example.json
python3 -m agentsassemble.cli live-agent start-session \
  --server http://127.0.0.1:8765 \
  --council-config configs/demo-council.json \
  --agent-config configs/codex-live-session.example.json \
  --live-agent-config configs/live-agents.codex-session.example.json \
  --meeting-id codex-live-demo \
  --group-id codex-live-demo \
  --wait-ready
```

The Codex example configs intentionally use three fresh `moderator_called`
residents and no checked-in real session ids. To attach an existing Codex CLI
session, use the GUI/CLI Codex invite or join flow so the local generated config
records the current session id outside the checked-in examples.

The no-model fake Codex lifecycle regression covers the same checked-in Codex
configs with a temporary `codex` executable. It proves the control plane can
start three Codex residents, run one official round, restart the resident group,
resume from the captured session ids for the remaining round, finalize, and stop
offline without making real model calls. A real Codex smoke still requires an
explicit operator run.

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
