# Live CLI Room Current Architecture

Status: current implementation authority  
Updated: 2026-07-10

This document describes the active local interactive CLI-first room path. Read
it before changing WebSocket commands, room events, Agent Session lifecycle,
provider process handling, transcript extraction, moderation, or the React room
surface.

## Decision

The active room has one authority for each domain concern:

- one ticket-authenticated WebSocket endpoint: `/ws?ticket=...`;
- one durable room event contract in `RoomStore`;
- one participant and Agent Session registry per room;
- one command path with request IDs and ACK/NACK responses;
- one provider process boundary, implemented by an Agent Bridge and a
  persistent `LiveCliRuntime` PTY;
- one browser projection built from the canonical snapshot and event stream.

There is no provider-specific browser socket and no second `general` event
schema. Browser clients and provider bridges join the same room protocol with
different authenticated principals and capabilities.

## Product Contract

1. A real provider CLI starts only after an explicit operator command.
2. One provider process stays alive across turns. A turn must not launch a new
   CLI process.
3. The server pushes assigned room turns to providers. Providers do not poll
   the room.
4. The server sends only the bounded room delta after the session cursor. It
   does not replay the full transcript each turn.
5. Provider-private conversation memory remains in the provider session.
6. Only natural-language assistant messages enter room chat. Terminal chrome,
   prompts, status lines, tool output, and raw reasoning are not chat messages.
7. Stop, resume, interrupt, mute, and kick use the same WebSocket command path
   as message delivery.
8. Meeting/research/decision/archive remains a legacy product path and is not
   implicitly fed by the shared-room MVP.
9. API providers may be added later only behind the same room-event runtime
   contract. The runtime must not become `complete(prompt) -> text`.

## End-To-End Data Flow

```text
React browser
  | ticket-authenticated WebSocket
  v
/ws -> WsRoomSession
  | command(request_id, action, payload)
  v
RoomRealtimeController
  | append / read-after-seq
  v
RoomStore
  |-- .agentsassemble/rooms/rooms.sqlite3
  `-- .agentsassemble/rooms/<room_id>/
        media/ handoffs/ bridges/ smoke/

RoomRealtimeController
  | turn.assign on the same WebSocket protocol
  v
RoomAgentBridge process
  | one persistent PTY
  v
Codex / agy / Grok / Claude CLI
  | provider-owned transcript message source
  v
RoomAgentBridge
  | turn.state / message.delta / message.final / turn.failed
  v
RoomRealtimeController -> RoomStore -> WebSocket subscribers
```

The browser and every Agent Bridge receive the same canonical event sequence.
The bridge is a WebSocket client; the provider CLI itself remains a native
interactive terminal process behind the bridge.

## Canonical State Ownership

| Concern | Owner | Durable |
|---|---|---:|
| Room metadata | `RoomStore` SQLite `rooms` | yes |
| Participant identity and membership | `RoomStore` SQLite `participants` | yes |
| Agent Session configuration and cursor | `RoomStore` SQLite `agent_sessions` | yes |
| Ordered room history | `RoomStore` SQLite `room_events` | yes |
| Command deduplication | `RoomStore` SQLite `command_results` | yes |
| Active bridge subprocess handle | `NativeCliBridgeProcessManager` | process lifetime |
| Provider PTY and transcript cursor | `LiveCliRuntime` inside Agent Bridge | bridge lifetime |
| Browser reconnect cursor | room event `seq` | browser lifetime, replayable |
| Provider private conversation | provider CLI/session files | provider-owned |

`room_events` has one schema. Every event has a room-local monotonic `seq`, an
`id`, `room_id`, `type`, `created_at`, and canonical actor fields. Delta and
final message events share a `turn_id` so the browser updates one streaming
message instead of creating one message per delta.

The browser receives at most the latest 200 visible events in an initial
snapshot and requests older pages over the same WebSocket. A short reconnect
gap is replayed exactly; a larger gap returns a bounded latest window with
`resume_gap: true`. Agent Bridges receive turn assignments and bounded projected
context, not a browser history snapshot. Legacy room JSON/JSONL is migrated once
with a validated backup and is never used as a parallel fallback authority.

### Module Ownership

The canonical path is split by reason to change. Compatibility imports may
remain in `room_realtime.py`, but new behavior belongs in its owning module.

| Module | Owns | Does not own |
|---|---|---|
| `room_database.py` | SQLite schema, indexed reads, migration transaction and backup | routing or provider processes |
| `room_store.py` | room, participant, session, event, and command-result persistence API | WebSocket connections |
| `room_types.py` | shared event, participant, session, command, and turn packet shapes | validation or side effects |
| `room_commands.py` | command envelope validation and identity capability policy | command execution |
| `room_routing.py` | pure mention, default-responder, and relay-depth target selection | persistence or process launch |
| `room_context.py` | bounded bootstrap and cursor-diff provider input projection | provider-private memory |
| `room_event_broker.py` | bounded per-connection fanout and targeted bridge delivery | durable history |
| `native_cli_providers.py` | provider catalog, safe commands, profile identity, and Claude interactive guard | PTY parsing |
| `room_realtime.py` | command, durable state, turn, and recovery orchestration | provider terminal implementation |
| `room_bridge_process.py` | server-owned Agent Bridge process lifecycle | room routing |
| `room_agent_bridge.py` | authenticated bridge client and turn/report protocol | browser UI |
| `live_cli.py` | persistent PTY lifecycle and provider message extraction | room membership or history replay |

## WebSocket Protocol

### Authentication

The client obtains a short-lived, single-use ticket over authenticated HTTP,
then opens `/ws?ticket=<ticket>`. Browser principals and Agent Bridge principals
are fixed when the ticket is issued. Client payloads cannot replace the actor
identity.

### Commands

Every mutating request uses:

```json
{
  "op": "command",
  "request_id": "req-unique-id",
  "action": "message.send",
  "payload": {"content": "@codex 확인해줘"}
}
```

Supported browser/operator actions are:

- `message.send`
- `agent.create`
- `agent.start`
- `agent.stop`
- `agent.resume`
- `agent.interrupt`
- `participant.mute`
- `participant.kick`

Agent Bridges use the same command envelope for:

- `bridge.ready`
- `bridge.health`
- `turn.state`
- `message.delta`
- `message.final`
- `turn.failed`

The server returns a correlated `ack` or `nack`. Command results are deduplicated
by `request_id`, so reconnecting and resending an unresolved command does not
run it twice.

### Subscription And Reconnect

The client subscribes to `room_events` with `resume_from_seq`. The server sends
a snapshot containing room state, participants, Agent Sessions, active turns,
capabilities, bounded backfill, and `last_seq`. New appends are pushed through
bounded per-connection queues. The React client reconnects with exponential
backoff and its last observed sequence.

Backpressure never silently drops a final message. A full connection queue
drops intermediate `message_delta` frames first. If even essential frames
cannot fit, the broker emits `resync_required`; the browser reconnects and
recovers canonical events from SQLite by sequence.

## Participant And Capability Rules

Participants and Agent Sessions are created on the server. React does not
invent ownership, admission, runtime, or permission fields.

The snapshot contains server-authoritative capabilities:

- `message.send`
- `room.manage`
- `participant.kick`
- `participant.mute`
- `agent.control`
- `bridge.report`

React renders controls from these values. Human and agent moderation uses the
same `participant.kick` and `participant.mute` commands. Kicking an agent stops
its bridge and provider process, revokes membership, removes it from routing,
and requires an explicit add before it can start again. Muted humans cannot
send, and muted agents do not receive new turns.

## Agent Session Lifecycle

```text
stopped --agent.start--> starting --bridge.ready--> idle
idle --turn.assign--> busy --message.final--> idle
busy --agent.interrupt--> idle or error
idle/busy --agent.stop--> stopped
unexpected CLI/bridge exit --> recovering (one attempt) --> starting/idle
second exit or non-retryable failure --> error + recovery_required
error/stopped --explicit agent.resume--> starting
participant.kick --> stopped + kicked + removed from routing
```

Sending a room message never starts a stopped provider. Eligible messages are
queued durably in `pending_event_ids`. An explicit start or resume launches one
bridge, which launches one PTY process and then receives backlog.

When a turn is assigned, pending IDs move to `inflight_event_ids`. The room-local
`last_provider_sync_seq` cursor advances to the delivered input boundary only
after `message.final`; provider errors never advance it. An unexpected provider
or bridge process exit returns inflight IDs to pending, preserves bounded stderr
evidence, and schedules one retry after one second when the current server still
holds the launch approval context. The retry receives RoomMemory plus the
pending diff. A second exit before a successful final, an auth/configuration
failure, or a server restart requires an explicit operator resume.

## Routing And Context

- `@agent-id` routes to that configured Agent Session.
- `@all` routes concurrently to all eligible sessions.
- An unmentioned human message routes only to providers configured as default
  responders.
- Agent-to-agent messages use a bounded relay depth.
- A busy provider queues later events without blocking other providers.
- A stopped, kicked, or muted provider is never invoked.

`build_room_turn_packet()` uses indexed `seq` reads. A new session receives
RoomMemory plus at most the latest 12 visible final messages within a 4,000
character room-context budget. Later turns receive only other participants'
visible final messages after `last_provider_sync_seq`, under the same bounds.
The source event that triggered the turn cannot be dropped by truncation. The
provider input is a thin room envelope plus this projection, not RoomStore JSON
and not the complete transcript.

## Provider Runtime And Message Extraction

The bridge runs `LiveCliRuntime` over a PTY in a new process group. The runtime
drains startup output, handles only configured trust prompts, writes input as a
human would, streams output observations, and stops the whole process group.

Room messages come from strict provider-owned sources:

| Provider | Interactive command | Strict message source |
|---|---|---|
| Codex Spark | `codex ... --model gpt-5.3-codex-spark` | Codex session JSONL |
| Antigravity | `agy --sandbox` | Antigravity transcript JSONL |
| Grok | `grok ... --permission-mode plan` | Grok chat history |
| Claude Haiku | `claude --model haiku ...` | Claude session JSONL |

If a strict source does not yield an assistant message, the turn fails. Terminal
screen capture is not a fallback room message. Provider authentication errors
also become canonical error events rather than chat text.

Claude Code must remain interactive. Provider validation rejects `-p`,
`--print`, and `--print=...` before process launch.

## Process Ownership And Cleanup

The server owns Agent Bridge subprocesses. Tickets are passed through the child
environment, not command-line arguments. A bridge owns its provider process
group. Stop, kick, server shutdown, and smoke cleanup terminate the bridge,
wait, kill after timeout, and explicitly clean a recorded orphan provider PID
if needed.

Bridge stderr is written to bounded diagnostic files rather than being left as
an unread pipe. Provider runtime diagnostics retain bounded stderr counts/tail,
latency, message-source evidence, and process health without placing those
details in provider prompts or normal chat.

## React Integration

The existing multi-room React shell remains the product frontend. It opens one
canonical room socket for the active room and renders:

- canonical message deltas/finals grouped by `turn_id`;
- thinking/streaming state from turn events;
- canonical Agent Sessions and process controls;
- server-authoritative moderation capabilities;
- advanced latency and runtime diagnostics.

Frontend ownership follows the same boundary as the server:

- `api.ts` owns HTTP request/response contracts and the WebSocket ticket call;
- `roomSocketClient.ts` owns canonical frames, request correlation, reconnect,
  sequence resume, and `resync_required` recovery;
- `useCanonicalRoom.ts` owns room-indexed events, history pages, Agent Session
  state, capabilities, provider availability, and timeline projection;
- `App.tsx` chooses the active room and composes the existing multi-room shell;
- `RoomConnectionPanel.tsx` renders canonical Agent Session controls and does
  not expose the frozen flow/Mafia runner as an Agent Session fallback.

The dock no longer restores hard-coded demo rooms. Hiding a server room writes a
local tombstone so the next server directory refresh does not immediately add
it back. A guest leave still revokes the room session. Hiding, leaving,
closing, and archiving are separate meanings.

## Verification Surfaces

The credential-free end-to-end test starts a real HTTP/WebSocket server, a
separate Agent Bridge process, and a fake interactive PTY CLI. It proves:

- browser command to canonical room event;
- bridge turn assignment over the same WebSocket;
- two turns on one provider PID;
- provider-private marker recall;
- delta/final event delivery;
- one canonical SQLite event authority;
- bridge and provider cleanup.

Vitest behavior tests separately exercise command ACK/NACK correlation,
backpressure reconnect from the last durable sequence, resume snapshots that do
not erase already loaded history, streaming delta/final coalescing, history
pagination, and canonical Agent Session control actions. Python source-string
assertions are not the authority for those behaviors.

The opt-in real-provider smoke uses the same production path:

```bash
assemble room smoke \
  --providers codex,antigravity,grok,claude \
  --config configs/live-cli-providers.example.json \
  --approve-real-provider
```

It records executable provenance, bridge/provider PIDs, same-PID continuity,
strict message source, marker recall, provider-visible character counts, TTFO,
turn completion latency, RSS delta, stderr diagnostics, timeouts, context
errors, and process cleanup.

## Remaining Boundaries

- `RoomStore` uses indexed SQLite sequence reads and bounded browser snapshots.
  Long-room verification still needs the 100k-event/10-agent benchmark in the
  release smoke tier so later query changes cannot silently reintroduce scans.
- PTY interaction remains sensitive to provider TUI changes. Prefer a
  provider-supported structured interactive protocol behind the same Agent
  Bridge interface when one is verified.
- Resume currently starts a new bridge/provider process and replays pending room
  delta. Reattaching an existing detached OS process is a later feature and must
  be reported separately.
- Legacy meeting, lobby, side-chat, SSE, and provider adapters remain for old
  product paths. They must not become a second execution path for native CLI
  participants in the shared-room MVP.
