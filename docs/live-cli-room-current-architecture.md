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
- one provider process boundary, implemented by an Agent Bridge and one
  persistent provider adapter: a verified structured protocol when available,
  otherwise a strict-transcript `LiveCliRuntime` PTY;
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
  |-- GrokAcpRuntime -> grok agent stdio -> ACP agent_message_chunk
  `-- LiveCliRuntime -> persistent PTY -> Codex / agy / Claude
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
| Provider process and output cursor | provider adapter inside Agent Bridge | bridge lifetime; Grok ACP session marker is durable |
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
| `grok_acp_runtime.py` | Grok ACP lifecycle, permission denial, structured deltas, and provider session load | room routing or browser state |

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

The bridge selects a provider adapter without changing the room protocol.
Codex, Antigravity, and Claude currently run through `LiveCliRuntime` over one
persistent PTY in a new process group. The PTY runtime drains startup output,
handles only configured trust prompts, writes input as a human would, streams
strict transcript observations, and stops the whole process group.

Grok uses its advertised Agent Client Protocol surface, `grok agent stdio`.
`GrokAcpRuntime` keeps one JSON-RPC process alive, accepts only
`agent_message_chunk` as room text, rejects permission requests, uses an
isolated `GROK_HOME`, and fails closed if Grok reports always-approve mode. It
drains stderr continuously into a bounded diagnostic tail. A bridge restart
uses ACP `session/load` with an opaque provider session marker stored mode
`0600`; the UI receives only reuse/failure booleans, never that identifier.

Room messages come from strict provider-owned sources:

| Provider | Interactive command | Strict message source |
|---|---|---|
| Codex Spark | `codex ... --model gpt-5.3-codex-spark` | Codex session JSONL |
| Antigravity | `agy --sandbox` | Antigravity transcript JSONL |
| Grok | `grok agent stdio` | ACP `agent_message_chunk` |
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

The process manager compares `runtime_profile_key` before reusing a live
bridge. An incompatible model, command, workspace, terminal, or timeout profile
is rejected until the old process is stopped. Bridge config, logs, and durable
provider state live under a profile-keyed directory, so stopping and changing a
profile cannot silently load the previous profile's provider session.

Bridge stderr is written to bounded diagnostic files rather than being left as
an unread pipe. Provider runtime diagnostics retain bounded stderr counts/tail,
latency, message-source evidence, permission denials, session continuity, and
process health. Raw stderr, terminal tails, and provider session identifiers
stay server-side; they are not placed in provider prompts, normal chat, or the
public Agent Session projection.

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

Canonical-room frontend assertions belong in Vitest or Playwright. Three
duplicate Python tests that only searched source strings for the old general
socket, manual-turn controls, and Agent Session wiring were removed after the
behavioral coverage existed. Legacy UI source-string tests remain until each
has an equivalent behavior test; do not add new canonical-room contracts to
that suite.

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

Agent-to-agent relay has a separate mode on the same harness:

```bash
assemble room smoke \
  --providers codex,antigravity,claude \
  --config configs/live-cli-providers.example.json \
  --agent-conversation \
  --approve-real-provider
```

The selected providers start together in one canonical room. The harness sends
one directed ring message per provider, verifies that each target turn names the
preceding agent's `message_final` as its `source_event_id`, checks strict
provider message sources, records first-delta latency, and waits for the relay
depth limit to settle before cleanup. The credential-free E2E uses two real
fake PTY processes to cover the same path in normal test runs.

The harness also compares provider-direct first clean output with the first
canonical room delta for ten strict samples. The 2026-07-10 local runs recorded:

| Provider | Strict samples | Direct p50 | Room p50 | Added p50 | Result |
|---|---:|---:|---:|---:|---|
| Codex Spark | 10 | 1702.0 ms | 1727.9 ms | 23.9 ms | exact outputs, same PID, marker recalled, cleanup passed |
| Antigravity `agy` | 10 | 1371.8 ms | 1389.2 ms | 18.9 ms | exact outputs, same PID, marker recalled, cleanup passed |
| Grok ACP | 2 of 10 | 1136.8 ms | 1162.3 ms | 25.5 ms | first two exact; later provider usage balance exhausted |
| Claude Haiku | 10 | 2268.9 ms | 2292.8 ms | 25.1 ms | exact outputs, same PID, marker recalled, cleanup passed |

Grok's partial row is latency evidence, not a passing ten-sample smoke. A final
Grok rerun requires provider balance; the harness reports the external 402 as a
classified provider error instead of treating it as a transport failure. A
separate real no-inference restart probe confirmed `loadSession: true` and a
successful `session/load` into a new Grok process. Claude's row used
`claude --model haiku --permission-mode plan --tools "" --safe-mode`; `-p` and
`--print` were absent. After the local Claude login was refreshed, the two-turn
memory smoke and all ten exact latency samples passed.

The 2026-07-10 three-provider conversation smoke kept Codex Spark,
Antigravity, and Claude Haiku alive together in one room. The directed ring
Codex -> Antigravity -> Claude -> Codex completed all three handoffs, plus one
natural Antigravity -> Codex follow-up, for seven provider turns. Every handoff
matched the preceding agent message ID, all provider messages came from strict
session/transcript sources, and the depth-two relay settled with turn counts
Codex 3, Antigravity 2, Claude 2. All three provider PIDs stayed stable, and no
bridge or provider process remained after stop. The final current-code rerun
recorded TTFO p50/p95 4438.1/6892.2 ms and turn-completion p50/p95
4446.4/6895.8 ms. Evidence:
`native_cli_20260710T105755Z_8c4b71.json`.

Long-room behavior is verified separately without provider calls:

```bash
assemble room benchmark --events 100000 --agent-count 10 --samples 50
```

The latest 2026-07-10 local run held 100,051 canonical events. Latest-window
p50/p95 was 0.671/0.753 ms, reconnect 0.668/0.721 ms, history paging
0.680/0.728 ms, and ten-agent context projection 2.164/2.573 ms. Context stayed
within 12 events and 495 characters, the SQLite query plan used indexes, the
database was 85.7 MB, and process RSS grew by 8.5 MB.

## Remaining Boundaries

- `RoomStore` uses indexed SQLite sequence reads and bounded browser snapshots.
  The 100k-event/10-agent command is implemented and covered at smaller scale
  in unit tests; CI should keep a scheduled or release-tier full-cardinality run
  so later query changes cannot silently reintroduce scans.
- PTY interaction remains sensitive to provider TUI changes. Prefer a
  provider-supported structured interactive protocol behind the same Agent
  Bridge interface when one is verified. Grok is the first such adapter.
- Resume starts a new bridge/provider process and replays pending room delta.
  Grok additionally reloads its provider-owned ACP session; PTY providers retain
  only room-memory recovery across a process restart. Reattaching an existing
  detached OS process is a separate later feature and must be reported as such.
- Legacy meeting, lobby, side-chat, SSE, and provider adapters remain for old
  product paths. They must not become a second execution path for native CLI
  participants in the shared-room MVP.
