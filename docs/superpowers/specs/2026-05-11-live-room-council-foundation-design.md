# Live Room Council Foundation Design

## Goal

Build the first durable foundation where AgentsAssemble can act like a live shared room without losing its identity as a governed council workflow.

The product must include free chat, but free chat must not become the official meeting record by accident.

## Positioning

Stoops-style live rooms solve the transport problem: humans and agents can join one room, see events in real time, and let long-running CLI sessions receive messages while they work.

AgentsAssemble should use that lesson, but its product center is different:

- Live room infrastructure answers: who is in the room, who receives messages, and how fast events move.
- Council workflow answers: what counts as an official turn, what evidence supports it, what decision was made, what task was assigned, and what context returns with each agent.

AgentsAssemble should not become a generic chatroom. The room exists to support agenda, rounds, moderator control, decisions, tasks, return packets, memory, and later implementation gates.

## Room Model

The room event stream is append-only and typed. It can contain informal and official events together, but each event must carry enough metadata to decide whether it affects official artifacts.

Event channels:

- `lobby`: informal staging-room messages before or around the meeting.
- `side_chat`: informal chat visible beside the official meeting.
- `official`: moderator-controlled meeting turns that may feed `transcript.md`, Decision Gate, and `decision.md`.
- `system`: lifecycle, participant, artifact, and status events.

The first implementation keeps the existing file-based storage:

- `.agentsassemble/lobby.jsonl`
- `.agentsassemble/side_chat.jsonl`
- `.agentsassemble/meetings/<meeting_id>/live_events.jsonl`
- `.agentsassemble/meetings/<meeting_id>/meeting.json`

SSE is the transport layer for the GUI. Polling may remain as a fallback, but the primary browser path should subscribe to event streams and update only the affected UI state.

## Moderator Model

The moderator is a participant role with meeting-control authority, not just another speaker.

Moderator responsibilities:

- Open or resume an official meeting.
- Announce the current phase and round.
- Grant official turns to roles.
- Keep informal chat out of formal artifacts unless promoted.
- Request another round when the decision gate says more debate or research is needed.
- Produce or request synthesis.

AI moderators can draft synthesis, but the host remains the authority for dangerous side effects such as implementation, commit, push, PR, deploy, or release.

## Engagement Modes

Engagement mode means "when should this participant react?"

Initial modes:

- `manual`: does not auto-react. This is the safest default for humans and imported participants.
- `mentioned`: can briefly answer when addressed by name in informal chat.
- `moderator_called`: can make an official turn only when the moderator grants it.
- `human_only`: reacts to human messages but not other agents by default.
- `always`: reacts to all room messages. This is dangerous and should be rare.
- `watch`: read-only observer.

Default policy:

- Free chat defaults to `mentioned` for agents.
- Official meetings default to `moderator_called`.
- Observers default to `watch`.
- `always` should be visually marked as loop-prone.

## Artifact Rules

Informal messages may be stored in room logs, but they do not affect official artifacts by default.

Official turns may feed:

- `transcript.md`
- `decision.md`
- Evidence Gate
- Decision Gate
- return packets
- memory summaries

Promotion from informal chat into official record must be explicit later. V0 can record the policy without building promotion UI.

## Security And Safety

All remote participants, memory packs, channel events, and bridge output are untrusted input.

Meeting mode remains read-only by default. Current CLI and bridge participants still use advisory permission envelopes unless a real sandbox is added.

SSE endpoints must not leak credentials. They should stream only the same public event payloads already available through existing JSON APIs.

## V0 Slice

This slice should deliver:

- Documentation that records the difference between live-room infrastructure and council workflow.
- A file-backed room event model that can represent lobby, side chat, official, and system events.
- SSE endpoints for lobby, side chat, and meeting live events.
- Browser subscriptions that stop depending on full-page/payload polling for lobby and side-chat updates.
- Explicit engagement modes and moderator turn-control metadata in meeting artifacts.
- Tests proving informal and official events stay distinguishable.

This slice should not deliver:

- Full tmux/PTY injection.
- Cloudflare tunnel sharing.
- Real Claude Code Channels integration.
- Implementation agents editing code after meetings.
- Push, PR, deploy, or release behavior.
