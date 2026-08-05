# Live Session Room Model

Status: mixed current invariants and historical design context

Read when: changing legacy session/meeting semantics or tracing the origin of a
room rule. The canonical implementation authority is
`docs/live-cli-room-current-architecture.md`; start with
`docs/product/CURRENT_SYSTEM.md`.

AgentsAssemble must model a shared council room, not a survey runner that interviews agents one at a time.

Current new MVP surface: **local interactive CLI-first #general MVP**.

The local CLI room now uses the canonical RoomStore participant/session/event
model and the ticket-authenticated room WebSocket. Read
`docs/live-cli-room-current-architecture.md` for the current implemented data
flow, lifecycle rules, verification surface, and remaining boundaries.

The active implementation direction is one `#general` room where persistent
local CLI sessions read only events after their last cursor and write terminal
output back as room events. The first runtime boundary is `AgentRuntime`, with
`LiveCliRuntime` as the primary implementation for Codex CLI, Claude Code CLI,
Gemini CLI, and similar terminal-first agents. API runtime is later
compatibility and must implement the same event contract; it must not lower the
interface to complete(prompt) -> text.

The previous turn-based Agent Session and meeting/research/decision/archive
pipeline stays legacy for this MVP. It may remain available for historical
tests and smoke checks, but it is not the product center for the new `#general`
room.

An Agent Session is a real local/resumable AI CLI session attached to a room,
with persisted identity, model, effort, sandbox/permission settings, joined or
detached state, and one ordered room event stream. UI and docs should use this
name instead of exposing runner, bridge, adapter, delegate, one-shot, MCP, or
baseline as user-facing choices.

Core invariant:

> Agents do not receive isolated interview prompts; they join a shared room and respond to the same ordered event stream.

## Shared Room Event Stream

The room is the source of truth. Every participant sees the same shared room event stream, subject to permission and privacy filters.

Events should be append-only records such as:

- room created
- participant joined
- participant left, kicked, exported, or detached
- session attached, resumed, or detached
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

The active room source of truth is separate from meeting artifacts:

```text
.agentsassemble/
  rooms/
    rooms.sqlite3
    <room_id>/
      media/
      handoffs/
      bridges/
      smoke/
    _migration_backup/<timestamp>/<room_id>/
```

`rooms.sqlite3` is the single active authority for room metadata, membership,
Agent Sessions, ordered events, and command deduplication. Existing JSON/JSONL
room state is imported once, validated, and copied to `_migration_backup`
before the old authority files are removed. Provider media, bridge evidence,
handoffs, and smoke artifacts remain files. Meeting files under
`.agentsassemble/meetings` remain archives/snapshots, not active roster state.

Roster visibility and private operational signals are separate. Room
participants may see which humans and AI agents are present, their public names,
roles, connection classes, and room-visible status. Provider or subscription
quota details such as 5-hour/1-week usage remain owner-visible only; room
presence lists must not carry raw quota fields, and flow roster APIs must
project those fields by viewer identity so other participants do not receive
the exact values unless the owner explicitly shares them through a future room
policy. Public invite clients read the flow roster with their session token;
loopback host reads may see local host-owned agent quota, but not remote
participant-owned quota. LAN or public-host flow reads without a session token
must not be treated as the host quota viewer.

The local-first room should borrow the useful parts of low-latency event
systems without pretending to be Kafka, Flink, or a kernel-bypass network
stack. The first room-event bus foundation is:

- append-only durable room logs for lobby, side-chat, official, game, and
  system events.
- cursor-based reads so agents and browsers can ask only for events after
  their last observed id.
- ticket-authenticated WebSocket fanout for canonical room events; legacy SSE
  remains isolated to legacy meeting surfaces.
- bounded in-memory queues and fairness/backpressure policy when several
  residents try to speak at once.
- id/reference payloads for attachments and artifacts, so large bytes are not
  copied through every event.

Lobby and side-chat messages share the same 2000-character visible room-message
budget for humans and resident agents. Compact control fields such as topics,
statuses, ids, paths, and error summaries keep their narrower limits.

Each implementation slice that changes this path should expose numeric evidence
when practical: append/read latency, SSE delivery time, queue wait time,
backpressure counts, and per-agent speaking distribution. These numbers are
operator evidence and regression signals, not permission to add a heavyweight
distributed streaming stack before the local room contract needs it.

Legacy GUI room feeds should preserve operator reading state while those events
arrive. Lobby and live transcript refreshes keep existing event rows mounted
when their payload is unchanged, append newly observed rows at the end, update
only rows whose same-id payload changed, and preserve reader scroll/focus when
the operator is not pinned to the latest message. This is a DOM update contract
only; it does not change event ordering, SSE polling cadence, message copy, or
official record boundaries.

`assemble live-agent room-benchmark` is the first small benchmark surface for
that direction. It measures the existing local append/read functions for lobby
and live-event logs, reports read-after-cursor and tail-read latency, and
includes a synthetic flow speaking-distribution metric. The imbalance metric is
defined as `max_agent_speaking_count / max(min_agent_speaking_count, 1)`. This
benchmark also reports a synthetic scheduler-on versus scheduler-off comparison
using `normalized_imbalance =
(max_agent_speaking_count - min_agent_speaking_count) / total_speaking_turns`,
plus a pure predicate latency sample over a 10k-event synthetic flow. When
`--sse-samples N` is set, it also reports a small
`lobby_sse_append_to_frame_ms` measurement against the existing local
`/api/events/lobby` SSE endpoint. The stream checks the file-backed event log on
a low-latency polling cadence while keeping idle keep-alive frames on a slower
cadence; the number is a regression tripwire on the same machine, not an SLA.
Queue wait time and backpressure counts still require a later server/fanout
slice and remain out of scope. Treat the output as operator evidence for
comparing local changes on the same machine, not as a service-level objective.

Historical Play Mode `turn_based_floor` fairness is disabled for the current
Agent Session product surface. If this internal flow code is re-enabled later,
it must remain a runner-side silent yield. Before a resident calls
its provider, it compares its own speaking count against the current active
`flow` participants in the same meeting. The default guard looks at the last 24
speaking events, blocks an immediate repeat with a one-speaking-turn `min_gap`,
uses `max_lead=0`, and uses active participant order only for empty-history
starts and final tie-breaks. Once speaking history exists, count-eligible ties
prefer the least-recently-spoken participant. A resident can set
`flow_fairness_recent_window`,
`flow_fairness_min_gap`, `flow_fairness_max_lead`, or
`flow_fairness_start_order` in its live-agent config to tune that policy. If the
resident should yield, it skips that tick without advancing the visible room,
posting a nudge, or turning the room into a moderator. This guard applies only
to informal flow participation, not official Work Mode turns; stale/offline
participants are not used as the baseline.

The flow scheduler must also avoid self-trigger loops. A resident's own visible
reply is not a new reason for that same resident to speak again; another human
or agent event, a direct mention, or a future explicit scheduler packet is
needed before it should call the provider again. This rule applies even when a
policy relaxes turn-balance fairness.

## Live Room Infrastructure vs Council Workflow

AgentsAssemble should learn from Stoops-style live room infrastructure without becoming only a chatroom.

Live room infrastructure handles presence, share links, real-time delivery, CLI session attachment, and room history. Stoops is a useful reference here because it treats the server as a small room/event relay while Claude Code or Codex sessions stay alive on each user's machine.

Council workflow handles agenda, moderator-controlled official turns, evidence, Decision Gate status, `decision.md`, assigned tasks, return packets, and memory. This is the product boundary that should keep AgentsAssemble distinct.

Free chat / free-flow room mode is not currently a supported user-facing path,
and the server-side flow start path fails closed. The supported room behavior is
ordered Agent Session turns. If informal chat is reintroduced later, official
meeting turns must still be typed separately so a side comment cannot silently
become evidence or a decision.

Historical design note: the host once called `POST /api/agent-sessions/turn`
or `assemble room turn`. Both direct one-shot surfaces are now retired; the
canonical server assigns resident Agent Bridge turns over WebSocket. The server separates
RoomStore state from provider-visible input. RoomStore owns UI, audit, SSE,
media manifests, recovery, cursors, and diagnostics. Provider prompt text is
conversation input: bootstrap rules once, the current instruction, a short
public room delta since that agent's provider sync cursor, current-turn media,
and recovery memory only when recovering or bootstrapping. Internal lifecycle
events, diagnostics, process ids, stdout/stderr, token usage, provider ids,
`message_delta` fragments, and full RoomStore JSON are never provider-visible
prompt content.

Codex uses `codex app-server` as the primary Agent Session runtime when
available. The app-server thread id is stored as provider-owned thread/session
state when it is resumable; later turns reuse/resume that thread and send short
text input rather than a JSON room packet. The bounded packet remains a
debug/dry-run, exec fallback, and recovery seed surface. `codex exec --json` is
fallback-only: fresh fallback turns may use `--ephemeral`, but their
`thread.started.thread_id` is diagnostics-only `ephemeral_thread_id`, not a
resumable `provider_session_id`. Explicit resume uses only a stored
`provider_session_id` / `provider_thread_id`; `resume --last` is forbidden
because it is not a per-agent identity. Provider-visible message items become
`message_delta` and `message_final`; only explicit conservative progress may
become `thinking_delta`, displayed as progress rather than final assistant
speech. The server records turn output as room events bracketed by
`turn_started` and `turn_finished` when successful, or `error` on failure, with
timing/usage/context diagnostics outside the provider input. If no runner is
configured, the turn returns a not-started diagnostic and does not invent a
provider reply.
Selected lobby text can enter the official record only through
`assemble lobby promote`, which writes a `promoted_context` official event and
the sanitized `lobby.promote_to_official` operation. Play Mode chatter is not
official until explicitly promoted, side chat cannot be promoted, and
attachments are not promoted by this first narrow path.

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
- `promoted_context` records selected lobby text as official background without
  creating answered debate-round messages.
- A participant may read the public event stream but should not receive private memory, raw files, or secrets unless explicitly allowed.

## Engagement Modes

Engagement mode means when a participant should react to room messages.

For the current Agent Session product path, free/silent/quiet/free-chat/flow
room modes are disabled. The only supported server behavior is turn-based: the
room calls one agent, sends the ordered room event stream plus supported media
references, and records the resulting turn back into the same stream.

- `manual`: does not auto-react.
- `mentioned`: can answer when called by name or agent id in free chat; partial prefix/suffix matches do not count as a call.
- `moderator_called`: can make an official turn only when the moderator grants it.
- `human_only`: reacts to human messages but ignores agent chatter by default.
- `always`: reacts to all visible room messages and must be treated as loop-prone.
- `watch`: observes without speaking.
- `flow`: temporary Play Mode mode for host-approved meeting residents during a
  timeboxed free-conversation loop. It is lobby-only and unofficial; the runner
  may choose to speak, wait, ask, challenge, clarify, summarize, or call for a
  human, but those actions are stored as safe metadata on lobby events rather
  than official transcript turns.

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
