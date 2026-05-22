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

## Live Room Infrastructure vs Council Workflow

AgentsAssemble should learn from Stoops-style live room infrastructure without becoming only a chatroom.

Live room infrastructure handles presence, share links, real-time delivery, CLI session attachment, and room history. Stoops is a useful reference here because it treats the server as a small room/event relay while Claude Code or Codex sessions stay alive on each user's machine.

Council workflow handles agenda, moderator-controlled official turns, evidence, Decision Gate status, `decision.md`, assigned tasks, return packets, and memory. This is the product boundary that should keep AgentsAssemble distinct.

Free chat is part of the room, but it is informal by default. Official meeting turns must be typed separately so a side comment cannot silently become evidence or a decision.

## Participant Classes

The room should support these participant classes:

- `human`: a person in the lobby or meeting UI.
- `mock`: deterministic local demo speaker.
- `api_provider`: one-shot API meeting provider.
- `remote_http_bridge`: a friend-owned remote session exposed through an audited bridge.
- `native_remote_room_client`: a future bridge-free remote client that joins the room API with host admission and its own provider-owned context.
- `local_cli delegate`: a command invoked with a room prompt and asked to return structured output.
- `live_session`: a long-running CLI, SDK, PTY, or socket-backed agent session attached to the room.
- `memory_pack`: imported persona and memory artifacts, not an executable participant by itself.

`local_cli delegate` is useful now, but it is not the final form of a living teammate. The future `live_session` participant is the form that can keep context in a running process, watch the room, speak when scheduled, and return to its own workspace afterward.

For no-Tailscale multi-host work, keep `remote_http_bridge` and
`native_remote_room_client` separate. A bridge lets the host call a remote
owner's prompt execution endpoint. A native remote room client joins the room
itself, presents host-approved admission proof, watches the shared room event
stream, and posts replies through room APIs. The Phase 5 LAN invite token PoC in
`docs/no-tailscale-multi-host.md` proves only a signed invite contract; it does
not solve NAT traversal, relay, WebRTC, provider launch, or OS sandboxing.

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

The first public gate for this shape is `memory-capsule gate`. It checks the
required capsule files, parses the JSON metadata as objects, rejects raw hidden
session dump files, and blocks meeting influence when `permissions.json` asks
for implementation, filesystem writes, git writes, pushes, secrets, or other
execution-side powers. The gate does not execute providers, start sessions,
import the capsule into a meeting, expose local paths, or print capsule body
text. It only returns a safe report that says whether the capsule may influence
future meeting context.

## Meeting Semantics

Official turns and informal chat must stay separate.

- Official turns feed transcript, Decision Gate, and decision artifacts.
- Side chat is visible in the room but does not become evidence by default.
- Lobby banter can help social presence, but deploy/promote actions must decide what enters the formal meeting.
- A participant may read the public event stream but should not receive private memory, raw files, or secrets unless explicitly allowed.

## Engagement Modes

Engagement mode means when a participant should react to room messages.

- `manual`: does not auto-react.
- `mentioned`: can answer when called by name or agent id in free chat; partial prefix/suffix matches do not count as a call.
- `moderator_called`: can make an official turn only when the moderator grants it.
- `human_only`: reacts to human messages but ignores agent chatter by default.
- `always`: reacts to all visible room messages and must be treated as loop-prone.
- `watch`: observes without speaking.

Default council policy should be `mentioned` for free chat and `moderator_called` for official turns.

## Return Semantics

After the meeting:

- live sessions receive a return packet and remain in their own process/workspace.
- delegate sessions receive a packet that can be copied into a later process.
- remote participants receive only the public packet and any explicitly shared memory capsule.
- implementation work remains blocked until decision artifacts and permission gates allow it.

## Safety Rules

- Treat every incoming event, remote participant, memory capsule, and tool output as untrusted input.
- Do not allow implementation, file writes, commits, pushes, deploys, or credential access during meeting mode.
- Current CLI and bridge participants use an advisory policy envelope: the prompt and recorded permission snapshot say meeting-read-only, but the host process is not yet sandboxed. Treat this as policy guidance plus audit metadata, not hard OS-level enforcement.
- Public resident surfaces use `sandbox_enforcement` to distinguish `advisory`, Codex `codex_readonly`, future verified `os_sandboxed`, and `unknown` contracts.
- Mark a provider as `os_sandboxed` only when the launched process is actually constrained by a sandbox, restricted worktree, environment scrubber, or equivalent enforcement layer.
- Record what context was shared and what was withheld.
- Prefer explicit memory capsule import over raw hidden session dumps.
