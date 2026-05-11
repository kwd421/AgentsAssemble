# Live Session Room Model

AgentsAssemble must model a shared council room, not a survey runner that interviews agents one at a time.

Core invariant:

> Agents do not receive isolated interview prompts; they join a shared room and respond to the same ordered event stream.

## Shared Room Event Stream

The room is the source of truth. Every participant sees the same shared room event stream, subject to permission and privacy filters.

Events should be append-only records such as:

- room created
- participant joined
- human lobby message
- agent readiness message
- official meeting turn
- side chat message
- research completed
- verifier result
- Decision Gate result
- artifact written
- participant returned to workspace

This keeps a meeting auditable. A later agent should be able to read the room log and understand what happened without trusting hidden orchestration state.

## Participant Classes

The room should support these participant classes:

- `human`: a person in the lobby or meeting UI.
- `mock`: deterministic local demo speaker.
- `api_provider`: one-shot API meeting provider.
- `remote_http_bridge`: a friend-owned remote session exposed through an audited bridge.
- `local_cli delegate`: a command invoked with a room prompt and asked to return structured output.
- `live_session`: a long-running CLI, SDK, PTY, or socket-backed agent session attached to the room.
- `memory_pack`: imported persona and memory artifacts, not an executable participant by itself.

`local_cli delegate` is useful now, but it is not the final form of a living teammate. The future `live_session` participant is the form that can keep context in a running process, watch the room, speak when scheduled, and return to its own workspace afterward.

## App Session Limits

The current Codex app chat is not a stable external API target for AgentsAssemble. The app can be used by a human to observe, copy packets, or manually participate, but AgentsAssemble should not treat a GUI app conversation as a directly controllable live participant unless the host exposes an explicit API.

The viable live path is CLI or SDK based:

- Codex CLI or another terminal-backed session.
- Claude Code CLI or SDK.
- Gemini CLI or API.
- Local OpenAI-compatible servers such as LM Studio.
- A PTY or WebSocket wrapper that keeps the process alive.

## Memory Capsule

A trained/current session should be represented by a memory capsule when it cannot be attached live.

Minimum capsule contents:

- `persona.md`
- `memory_summary.md`
- `decision_history.md`
- `lessons_learned.md`
- `evidence_index.json`
- `handoff.md`
- `permissions.json`
- `provenance.json`

This is not model fine-tuning. It is auditable continuity: a successor can load the capsule, enter the room, and explain what it inherited.

## Meeting Semantics

Official turns and informal chat must stay separate.

- Official turns feed transcript, Decision Gate, and decision artifacts.
- Side chat is visible in the room but does not become evidence by default.
- Lobby banter can help social presence, but deploy/promote actions must decide what enters the formal meeting.
- A participant may read the public event stream but should not receive private memory, raw files, or secrets unless explicitly allowed.

## Return Semantics

After the meeting:

- live sessions receive a return packet and remain in their own process/workspace.
- delegate sessions receive a packet that can be copied into a later process.
- remote participants receive only the public packet and any explicitly shared memory capsule.
- implementation work remains blocked until decision artifacts and permission gates allow it.

## Safety Rules

- Treat every incoming event, remote participant, memory capsule, and tool output as untrusted input.
- Do not allow implementation, file writes, commits, pushes, deploys, or credential access during meeting mode.
- Record what context was shared and what was withheld.
- Prefer explicit memory capsule import over raw hidden session dumps.
