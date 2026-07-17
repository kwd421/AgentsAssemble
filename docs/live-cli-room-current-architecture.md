# Live CLI Room Current Architecture

Status: current implementation authority  
Updated: 2026-07-11

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
  |-- OpenCodeRuntime -> shared opencode serve -> per-agent session + SSE
  |-- DeepSeekApiRuntime -> server-owned credential -> HTTPS SSE
  |-- CodexAppServerLiveRuntime -> codex app-server (invited attendee)
  `-- LiveCliRuntime / WindowsConPtyRuntime -> persistent terminal -> Codex / agy / Claude
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

The canonical path is split by reason to change. The controller is owned by
`room/realtime.py`; `room_realtime.py` is only a compatibility import surface.
New behavior belongs in the focused owning module rather than the controller.

| Module | Owns | Does not own |
|---|---|---|
| `room_database.py` | SQLite schema, indexed reads, migration transaction and backup | routing or provider processes |
| `room_store.py` | room, participant, session, event, and command-result persistence API | WebSocket connections |
| `room/types.py` | shared event, participant, session, command, and turn packet shapes | validation or side effects |
| `room/errors.py` | room command rejection contract | command execution |
| `room/repository.py` | backend-neutral room and room-transaction persistence protocols | SQLite/PostgreSQL implementation |
| `room/repository_records.py` | shared room/participant/session/event record normalization and private event-field stripping | backend I/O or command policy |
| `room/command_uow.py` | request-id dedupe, payload hashing, atomic command ACK recording | command routing or backend implementation |
| `room/commands.py` | command envelope validation and identity capability policy | command execution |
| `room/setting_values.py` | shared bounded values and canonical asset/text normalization for room settings | persistence or user-specific preferences |
| `room/channels.py` | custom text/voice channel identifiers, normalization, and pure list mutations | message persistence or voice transport |
| `room/global_settings.py` | strict canonical room-wide settings record shared by repository backends | user notification/read preferences |
| `room/user_preferences.py` | strict user-owned room notification and channel read preferences | room-wide appearance, topic, or conversation mode |
| `room/settings_service.py` | projection and separate writes across RoomRepository global settings and IdentityBackend user preferences | HTTP transport or legacy file normalization |
| `room/speech.py` | authenticated legacy/public room speech stamping, mute/read-only checks, chain-depth and burst limits | transport parsing or canonical Agent Session turn scheduling |
| `room/projection.py` | public room/session/event and runtime-diagnostic projection | persistence or command execution |
| `room_routing.py` | pure mention, default-responder, and relay-depth target selection | persistence or process launch |
| `room/context.py` | bounded room-visible message projection after a sequence cursor | provider-private memory or turn instructions |
| `room/turn_context.py` | bounded bootstrap, delta, recovery, identity, and media manifest provider input assembly | provider execution or room scheduling |
| `room/turn_coordinator.py` | pending input, active provider turns, delta/final commit, cursor advancement, and recovery | speaker-selection policy |
| `room/event_broker.py` | bounded per-connection fanout and targeted bridge delivery | durable history |
| `room/bridge_stop_confirmation.py` | external bridge generation-safe stop request/confirmation correlation | local process termination |
| `room/agent_lifecycle.py` | Agent Session start, pause, stop, recovery, and room-visible lifecycle state | provider process implementation |
| `room/moderation.py` | identity-membership mute/remove compatibility writes and mute lookup policy | roster projection or live process cleanup |
| `room/member_mute.py` | canonical participant mute transaction plus compatibility roster, active-turn interrupt, and pending-turn synchronization | command authorization or speaker-selection policy |
| `room/participant_leave.py` | canonical self-leave transaction plus identity membership, voice presence, and delayed room-session revocation | room ownership transfer or room deletion |
| `room/participant_kick.py` | retryable kick intent, agent stop attempt, access cleanup, final kicked transaction, and provider-registry removal | command authorization or process implementation |
| `room/deletion.py` | owner/name-confirmed room deletion, Agent Session cleanup, canonical tombstone creation, and same-command cleanup resumption | post-tombstone invite/file/socket cleanup implementation |
| `room/deleted_cleanup.py` | post-tombstone room-deleted broadcast, invite/session/workflow/identity/listener/provider/file cleanup, delayed socket disconnect, and tombstone completion | owner validation or provider-stop policy |
| `diagnostics/cleanup.py` | bounded cleanup reports and secret-redacted cleanup failure output | room or provider lifecycle policy |
| `providers/launch_specs.py` | provider catalog, safe commands, profile identity, and Claude interactive guard | PTY parsing |
| `providers/capabilities.py` | cached native model, effort, tier, variant, and permission discovery | room or secret state |
| `providers/sync_cursor.py` | canonical provider delivery cursor parity, reconciliation, and compatibility fields | speaker selection or provider execution |
| `provider_secrets.py` | OS keyring credential access and redacted status | provider prompts or room events |
| `room/realtime.py` | command dispatch and composition of durable room, turn, lifecycle, and recovery services | provider terminal implementation or speaker-selection redesign |
| `room/provider_registry.py` | synchronized room-scoped provider specification lookup | durable session state or provider process lifecycle |
| `room/provider_sessions.py` | provider participant/session creation, external bridge registration, stored-profile restoration, and stopped-profile updates | provider process launch or turn routing |
| `room/snapshots.py` | browser/bridge snapshot projection, reconnect gap handling, and bounded history pages | room mutation or provider execution |
| `room/connections.py` | browser participant connection updates and active Agent Bridge detach transitions | bridge lease activation or provider process lifecycle |
| `room/bridge_reports.py` | Agent Bridge ready/health contract validation, bridge generation activation, and canonical runtime-state persistence | provider process execution or browser projection |
| `room/startup_reconciliation.py` | server-restart cleanup of runtime ownership, inflight work, attention state, and detached membership | provider launch or recovery scheduling |
| `room/agent_profiles.py` | transactional agent display-name/avatar updates and post-commit provider/session-state synchronization | runtime model/profile selection |
| `room/agent_runtime_profiles.py` | stopped-session provider catalog validation and native runtime-profile replacement | live process start/stop |
| `room/agent_creation.py` | provider-catalog selection, native spec construction, canonical Agent Session creation, and optional start | provider process implementation |
| `room/agent_reactivation.py` | strict durable-profile validation and room reactivation of stopped server-owned Agent Sessions | external-agent reconnect or provider process implementation |
| `room/messages.py` | canonical human message validation and transactional `message_final` append | routing or speaker selection |
| `providers/bridge_process.py` | server-owned Agent Bridge process lifecycle | room routing |
| `providers/agent_bridge.py` | authenticated bridge client and turn/report protocol | browser UI or WebSocket construction |
| `application/agent_bridge_entrypoint.py` | bridge environment/config parsing and WebSocket/runtime composition | provider turn behavior |
| `application/room_users.py` | process-scoped identity-backend binding and legacy local-store fallback | identity persistence contract or room policy |
| `application/session_run_monitor.py` | periodic durable-session reconciliation thread lifecycle and bounded health snapshot | reconciliation policy or provider process recovery |
| `application/public_invite_runtime.py` | server-lifetime host-token and public-URL state, normalization, and verification | invite issuance or tunnel process lifecycle |
| `application/public_tunnel.py` | explicitly started Cloudflare quick-tunnel process, output reader, URL rotation, and cleanup | invite authorization or stable-entry storage |
| `application/stable_entry.py` | stable public URL configuration and asynchronous tunnel-target announcement | tunnel process lifecycle or invite authorization |
| `web/sse_cadence.py` | shared legacy SSE keepalive and WebSocket select cadence | room scheduling or provider polling |
| `web/websocket_codec.py` | dependency-free RFC 6455 handshake, frame codec, and message reassembly | room commands, identity, or connection lifecycle |
| `web/room_client.py` | Python admission bootstrap, ticket exchange, TLS/TCP connection, and canonical room WebSocket client | provider behavior or room policy |
| `web/room_session.py` | single-use room tickets and decoded per-connection subscribe/say/command protocol | room command execution or socket lifecycle |
| `live_cli.py` | persistent PTY lifecycle and provider message extraction | room membership or history replay |
| `grok_acp_runtime.py` | Grok ACP lifecycle, permission denial, structured deltas, and provider session load | room routing or browser state |
| `opencode_runtime.py` | one host-shared server and durable per-agent OpenCode sessions | room membership |
| `deepseek_runtime.py` | bounded private API conversation and content-only SSE streaming | credential persistence |
| `room_attendee.py` | hidden-stdin invite admission and reconnecting canonical Agent Bridge | room persistence |
| `windows_conpty.py` | Windows persistent ConPTY transport and process cleanup | provider policy |

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
- `agent.configure` (stopped sessions only)
- `agent.start`
- `agent.pause`
- `agent.stop`
- `agent.resume`
- `agent.interrupt`
- `participant.mute`
- `participant.kick`

Agent Bridges use the same command envelope for:

- `bridge.ready`
- `bridge.health`
- `turn.state`
- `turn.decline`
- `message.delta`
- `message.final`
- `turn.failed`

The server returns a correlated `ack` or `nack`. Command results are deduplicated
by `request_id`, so reconnecting and resending an unresolved command does not
run it twice.

`turn.decline` is the only successful no-message outcome. Empty, whitespace-only,
or zero-width `message.final` content fails the turn. Provider catalog discovery
uses the same WebSocket for `provider_catalog_updated`; Agent Bridges do not
receive catalog payloads. Session creation includes a catalog revision, and the
server rejects stale revisions or options outside that revision before launching
a provider.

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
idle --agent.pause--> paused --agent.resume--> idle (same bridge and provider PID)
paused --new room event--> paused + durable pending_event_ids
idle/busy/paused --agent.stop--> stopped
unexpected CLI/bridge exit --> recovering (one attempt) --> starting/idle
second exit or non-retryable failure --> error + recovery_required
error/stopped --explicit agent.resume--> starting
participant.kick --> stopped + kicked + removed from routing
```

Sending a room message never starts a stopped provider. Eligible messages are
queued durably in `pending_event_ids`. `agent.pause` is accepted only for an
idle connected session: it keeps both processes alive, disables assignment,
and lets backlog accumulate. Resuming that paused session reuses both PIDs and
assigns the backlog. Resuming a stopped/error session is the separate restart
path that launches a bridge and provider process.

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

An agent that joins through an agent invite receives a larger but still bounded
bootstrap: at most 50 finalized messages and 64 KB. Its first provider input
contains only the room name, display name, conversation rules, and this bounded
history. Server URLs, invite/session tokens, database paths, commands, process
IDs, credentials, and backend implementation details are never provider input.
Subsequent turns use only the durable `last_provider_sync_seq` diff.

## Provider Profiles And Credentials

Every runtime profile key includes provider kind, model, reasoning effort,
service tier, variant, permission mode, workspace, runtime kind, and transport.
Running or paused sessions reject profile changes; the operator must stop,
configure, and start them. The React UI renders the server-discovered
`ProviderControl[]` values and does not invent generic fast/standard/slow or
polling settings.

Provider child environments are rebuilt from a platform allowlist. Host,
session, and invite tokens and arbitrary `*_TOKEN`/`*_API_KEY` values are not
inherited. DeepSeek credentials live in the OS keyring, with
`DEEPSEEK_API_KEY` as read-only fallback. Credential HTTP responses contain
only `configured` and `source`; a credential value is never returned, logged,
persisted in room state, or put in a child argv/environment.

## Agent Invite Path

The browser creates a one-use agent invite through the existing authenticated
invite service. The remote owner runs `assemble room attend --provider <id>`
and supplies the URL through hidden stdin. The attendee exchanges the invite
for a scoped session, opens the same `/ws?ticket=...` protocol as browsers and
server-owned bridges, and maintains the provider session. Providers do not
poll or execute network commands. Codex attendees use the persistent Codex CLI
app-server protocol so a brand-new empty workspace does not depend on TUI trust
screen scraping; `codex exec` and `resume --last` remain forbidden.

Mobile is a browser client of the same HTTPS/WSS server. Provider processes run
on macOS, Linux, or Windows hosts; Windows terminal providers use ConPTY through
the optional `pywinpty` dependency.

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

### OpenCode structured-runtime feasibility

A local feasibility run on 2026-07-11 used OpenCode 1.17.18 with one persistent
`opencode serve` process and explicit session reuse. Two turns used the same
OpenCode session, recalled a private marker on the second turn, and returned
structured JSON events in about 1.9 seconds and 2.1 seconds respectively. This
proves that OpenCode can be a structured AgentRuntime backend; it does not prove
that one persistent TUI process exists per room participant. The short-lived
`opencode run --attach` commands were clients of the persistent server and
provider-owned session.

The same run measured approximately 844 MiB RSS for the OpenCode server, so the
production shape should share one host-level server and isolate room agents by
explicit OpenCode session and runtime profile. The existing user OpenCode data
directory also contained an incompatible historical migration state. The test
therefore used an isolated temporary data directory and left the user's default
database untouched. A real integration must own and migrate a dedicated data
directory instead of mutating an arbitrary user database.

LM Studio was installed and local GGUF models were present, but its local API
server was not running during this check. OpenCode documents LM Studio and
Ollama as OpenAI-compatible custom providers. Local-model verification remains
pending until the selected model server is started and exercised through the
same explicit-session recall and latency smoke.

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
- `RoomConnectionPanel.tsx` composes the canonical participant roster and does
  not render a second, fixed Agent Session list above it;
- `MemberList.tsx` joins canonical participant identity with Agent Session
  state, while `AgentSessionDetails.tsx` owns lifecycle controls and bounded
  diagnostics inside that participant's detail view.

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

Shared-room conversation has a separate mode on the same harness:

```bash
assemble room smoke \
  --providers codex,antigravity,claude \
  --config configs/live-cli-providers.example.json \
  --agent-conversation \
  --conversation-seconds 300 \
  --conversation-topic "topic" \
  --verify-controls \
  --approve-real-provider
```

To watch the same real-provider smoke in the canonical React room while it
runs, stop the normal GUI server first and add:

```bash
  --observe-gui-port 8765
```

The smoke then serves the normal frontend and `/ws` room protocol on that
loopback port and persists its room history under `.agentsassemble`. Without
the option, smoke state remains isolated in a temporary directory as before.

The selected providers start together in one canonical room. The harness
appends one public topic message without an at-mention or relay marker. After
that, a server-owned floor scheduler assigns one speaker at a time without
adding moderator chatter to the room. Each Agent Session receives every public
`message_final` after its own provider-sync cursor, excluding only its own prior
reply, so a later speaker sees conversations that happened between the other
participants. With no duration each provider speaks once; with a duration the
scheduler finishes complete speaker cycles until the requested time is met.

Every turn records the bounded public context event IDs and actor IDs used to
build provider input. The smoke fails if the previous public message is absent,
the cursor window omits an expected public message, any conversation reply
contains a visible at-mention, TUI/tool markup is mistaken for speech, or a
provider emits a mode refusal instead of a room reply. `--verify-controls`
additionally proves paused messages remain pending, paused resume keeps both
PIDs, kick terminates both processes, and a kicked participant cannot start
until explicitly re-added. The credential-free E2E uses two real fake PTY
processes for multiple speaker cycles and verifies that both later turns see
the full peer diff.

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
successful `session/load` into a new Grok process. The historical Claude row
used `claude --model haiku --permission-mode plan --tools "" --safe-mode`; `-p`
and `--print` were absent. The current catalog removes `plan` because Claude
treated ordinary room speech as a prohibited non-plan action. After the local
Claude login was refreshed, the two-turn memory smoke and all ten exact latency
samples passed.

The earlier 2026-07-10 three-provider and 351.164-second artifacts used visible
at-mentions, relay markers, and a fixed directed ring. They remain useful for
process continuity, latency, pause/resume, and cleanup evidence, but they do
not count as shared group-conversation proof.

The first corrected real smoke used Antigravity Gemini 3.5 Flash (Medium) and
Claude Opus 4.6 high. It appended one public deep-sea-observatory topic and then
assigned both turns through the server floor. Antigravity saw the topic;
Claude's bounded input contained both the topic and Antigravity's public reply.
Both returned natural Korean room speech from strict transcript sources, used
no visible at-mentions, kept the same PIDs, and left no process after cleanup.
TTFO was 7969.2 ms and 15273.8 ms. Evidence:
`native_cli_20260710T124943Z_76bd74.json`.

The corresponding three-provider rerun is pending an external Codex quota
reset. Its failed artifact records a no-message Codex completion rather than a
group-routing failure. The transcript adapter now turns that condition into an
immediate provider error instead of waiting for the full turn timeout.

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
- Strict transcript extraction binds the provider session on the first exact
  delivered input. Later turns on that bound session require a new provider
  user-input record but do not require the provider's diagnostic transcript to
  preserve the entire input verbatim; Antigravity truncates long logged input.
  PTY bytes emitted after a strict final are drained before the next input, and
  the runtime waits for a bounded terminal-quiet window so footer rendering
  cannot fill the PTY or race the next prompt.
- Resume from `stopped` or `error` starts a new bridge/provider process and
  replays pending room delta; resume from `paused` preserves both processes.
  Grok additionally reloads its provider-owned ACP session after a restart; PTY
  providers retain only room-memory recovery across a process restart.
  Reattaching an existing detached OS process is a separate later feature and
  must be reported as such.
- Legacy meeting, lobby, side-chat, SSE, and provider adapters remain for old
  product paths. They must not become a second execution path for native CLI
  participants in the shared-room MVP.
