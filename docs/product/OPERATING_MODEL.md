# AgentsAssemble Operating Model

Status: current detailed product-policy reference

Read when: changing security, context ownership, official-record, Work/Play, or
public GUI policy. For ordinary implementation orientation, start with
`docs/product/CURRENT_SYSTEM.md`.

This document records the product memory that should survive chat context loss.
It is intentionally small: use it to orient agents before changing live-session,
provider, memory, or GUI behavior.

## Why This Exists

Viewooa has a useful documentation pattern: keep product intent, ownership, status,
and decisions in source-controlled files instead of relying on chat history.
AgentsAssemble should use that pattern, not copy Viewooa files or move anything from
that repository. Do not copy Viewooa files; adapt only the useful documentation
practice.

## Product Shape

AgentsAssemble has two related but different product modes.

New MVP direction:
- The new MVP is a local interactive CLI-first #general MVP.
- The goal is not an API-first multi-agent meeting runner. The goal is that
  Codex CLI, Claude Code CLI, Gemini CLI, and similar real local CLIs stay
  alive as long-running sessions and talk in one shared `#general` room like a
  person talking to the CLI in a terminal.
- The server watches room events and delivers only new events after each
  agent's cursor. AgentsAssemble must not make the AI poll for work and must
  not paste the full room transcript into every turn.
- The meeting/research/decision/archive pipeline stays legacy for this MVP and
  must not be silently connected to the new `#general` path.
- API runtime is later compatibility: an API provider may join only by
  implementing the same AgentRuntime room-event contract. API convenience must
  not lower the interface to complete(prompt) -> text.
- API coding work is an explicit operator-selected capability. Meeting-only
  sessions receive no workspace tools. A work-enabled session is confined to
  one selected workspace. The built-in API harness keeps private one-use
  approval for each mutation and command. As a separate explicit choice, an
  installed Codex or Claude Code harness may run the API/Local model through
  that native coding control plane while the room still owns lifecycle,
  publication, workspace selection, and activity visibility. A loopback
  protocol translator may adapt the model wire but never becomes a second room
  authority or receives durable room credentials.

Work Mode:
- Runs auditable council meetings.
- Keeps official turns, evidence, decisions, action items, and handoff packets.
- Blocks implementation until meeting artifacts and permission gates allow it.

Play Mode:
- Runs social or theatrical live rooms, such as idle debates, trials, or games.
- Can be entertaining without producing implementation decisions.
- Must still respect provider approval, loop limits, cost limits, and clear record
  boundaries.
- Can attach imported persona cards to approved residents so Play Mode agents
  keep character, world, speech-style, and lore context while speaking.

Play Mode can feed Work Mode only through an explicit promote action. Lobby banter,
games, and informal chatter must not silently become an official record.
Play Mode chatter is not official until explicitly promoted.
Play Mode presets may enqueue official-turn requests for an already approved
meeting, but they must not start provider CLIs, grant admission, or promote play
context into Work Mode by themselves.
Mafia Night is a Play Mode game surface with separate game state under
`play/mafia/`. Its all-chat and mafia-team chat are not lobby chat, side chat,
official live events, transcript evidence, decisions, or shared meeting memory.
Team chat must be read through a viewer-filtered game payload so town players do
not receive mafia-only events merely because side chat is globally visible.
Timeboxed Play Mode flow is the informal live-room loop: it can temporarily set
already host-approved meeting residents to `flow`, ask them to choose
`speak`/`wait`/`ask`/`challenge`/`clarify`/`summarize`/`call_human`, and record
only visible lobby messages plus safe action metadata. It must not start
providers, bypass host admission, or write Play Mode chatter into the official
transcript without a later explicit promote path. The room should not become a
visible moderator that keeps inserting its own prompts into the conversation:
silence checks are internal flow ticks, and the visible room advances only when
an approved resident or human actually posts a message.
RisuAI-style persona imports belong to this Play Mode boundary. The importer
may preserve raw module lore, including adult or otherwise sensitive character
material, but execution-shaped Risu module features such as regex scripts,
triggers, CJS, MCP declarations, and low-level access stay preserved as ignored
metadata and are not run by AgentsAssemble. Persona prompt context must not be
promoted into official Work Mode records. Stateful residents with active Play
Mode persona context should not answer official turns from the same flow loop,
because provider-private context can carry character framing across calls.
For Work Mode speech, `work_speech_only` may use only the card's reviewed
`speech_style` capsule such as tone, cadence, collaboration attitude, and
operator-approved do/do-not notes; it must not derive Work speech from raw lore,
scenario, examples, NSFW/adult body text, or ignored runtime payloads.
When a character-mode meeting writes Work Mode artifacts, AgentsAssemble records
a safe persona artifact contract report with violation codes and counts only;
it must not copy raw card lore, adult card bodies, or matched snippets into the
report.

## Non-Negotiable Rules

- discovery is not execution.
- Config generation is not execution.
- meeting room startup must not launch real provider CLIs.
- Real provider CLIs require explicit operator approval before they are started.
- Durable session-run state is not a stored approval grant; automatic replay,
  retry, or recovery of real provider residents requires a current operator
  approval action.
- Only a host-approved session, group, or agent binding may participate as a
  resident.
- A stopped session should stay stopped unless the operator explicitly starts,
  ensures, resumes, or recovers it.
- Provider execution style must be named honestly: native/session-managed,
  Codex exec/resume, Kiro chat resume, PTY terminal bridge, self-service room
  loop, remote bridge, or stateless prompt call.
- A resident polling runner is not the same thing as a resident provider
  process. Codex `exec/resume`, Kiro chat resume, Cursor chat resume, Grok
  session resume, Antigravity conversation resume, Hermes chat resume, and
  stateless prompt calls are call-style provider execution unless a separate
  persistent process/PTY/stream proof exists. Do not describe them as fast
  provider-resident sessions in UI, docs, prompts, or chat reports.
- LiveSessionAdapter is a scheduler and room-turn interface first. When it wraps
  Codex, Cursor, Grok, Antigravity, or another resume-backed CLI without a
  persistent provider proof, the honest product label is still
  `LiveSessionAdapter turn-based call/resume`, not fast provider residency.
  Codex 5.3 Spark was measured at roughly 4-6 seconds in this mode; that delay
  is provider exec/resume invocation cost, not a polling interval or cooldown
  promise.
- frontend polish is deferred until the backend state and data contracts are
  stable enough for another AI or human designer to refine.
- The React/Vite operator console is now the default GUI surface at `/`, served
  by the Python GUI from `frontend/dist` when that build exists.
- The dependency-light vanilla HTML/CSS/JS console is retired as a public GUI
  route. If the React build is missing, `/` and `/app/` return a build-required
  response instead of silently showing the old console.
- The default-route flip required and now has API/SSE parity and stable
  room-event contracts; browser-rendered parity for the React surface stays
  operator-verified after each build, not asserted headlessly.
- `frontend-info` reports `is_default_entry_point: true`; the React index served
  at `/` and the `/app/` alias rewrites `/assets/*` to the guarded
  `/app/assets/*` path. Human-facing launch guidance should point at the
  Discord-style React room client when that build exists. Provider startup
  approval, official records, and Work/Play separation are unchanged by the
  route flip.
- The detailed roadmap board is a later product-UI feature, not another panel
  to bolt onto retired GUI assets. Add a dedicated React roadmap view that can
  show long-term epics and
  version/milestone cards in a Trello/Jira-like board with clear planned,
  in-progress, review, and completed states.

## Context Model

Each provider or CLI owns its agent-private context.

Room-first / Agent-owned Context:
- The room is a room, not a hidden moderator or per-model context packer.
- AgentsAssemble owns room-visible records: event logs, official artifacts,
  shared meeting memory, agent cursors, and cursor/diff reads over the room.
- AgentsAssemble also owns saved-friend direct AI DM logs. Direct DMs are
  private 1:1 records between the operator and a saved AI session; they are not
  lobby messages, official meeting memory, Discord messages, or provider-owned
  private context.
- Agents decide what extra context to read before speaking. They may inspect
  the room diff since their cursor, archive artifacts, shared memory, and their
  provider-owned private context, then choose one public room reply.
- Agent-owned entry is the primary resident direction: join briefs are entry packets,
  and MCP participant or self-service loops are ways for an approved agent to
  use room tools directly, including direct DM reply tools when the operator
  messages that agent. Play Mode `flow` remains a demo/social helper over
  approved residents, not the default Work Mode authority path.
- The moderator does not sit between every room event and every agent reply.
  It starts meetings, records official turn boundaries, and may request turns,
  but it should not become a hidden per-turn context broker.
- Resident prompts may include a thin envelope with identity, the triggering
  event, cursor ids, and explicit shared meeting memory. They should not invent
  another private "inner meaning" channel behind the agent's public utterance.

AgentsAssemble should preserve that boundary:
- Do not merge one agent's private context into another agent.
- Do not dump raw project history into every provider by default.
- Do not pretend to own or compress a provider's hidden session state.
- Pass only the source event, cursor metadata, identity, and explicit shared meeting memory.
- Feed compact shared memory into resident prompts as background, not as a new
  event to answer.

AgentsAssemble owns shared meeting memory.

Shared meeting memory includes:
- official transcript entries.
- `shared_memory/rolling-summary.md` as the rolling summary for long-running
  resident meetings.
- decisions and unresolved decision points.
- `shared_memory/open-questions.md`.
- `shared_memory/action-items.md`.
- `shared_memory/index.json` as the deterministic machine-readable index for
  those shared-memory artifacts.
- promoted context from Play Mode into Work Mode.
- memory or handoff packets intentionally shared with future sessions.

The system should record what was shared and what stayed private.

## Official Record Boundary

The room may contain lobby chat, side chat, game chatter, system events, and
official turns. Only typed official meeting events should feed transcript,
decision, evidence, task, and handoff artifacts.

Useful rule:

```text
lobby or play chatter -> visible room history
official turn         -> transcript and decision evidence
explicit promote      -> selected informal context becomes official input
```

The current explicit path is `assemble lobby promote`, which appends a
`promoted_context` official live event for selected lobby event ids and records
the sanitized `lobby.promote_to_official` operation. The promoted event copies
only redacted lobby text and safe source metadata; side chat cannot be promoted,
attachments are not promoted, and the command/API must not start providers,
resume residents, or turn Play Mode flow metadata into official Work Mode
evidence.

Lobby chat attachments follow the same boundary as lobby text. They are stored
as local room files under the GUI output root and lobby events keep only safe
metadata plus room download/preview URLs. Raw file bytes, base64 payloads, and
local absolute paths must not be written into lobby events, shared memory,
transcripts, or decisions. An attachment becomes official evidence only through
a separate official artifact action.

Pending official turn requests are control state, not evidence. A meeting with
unanswered official requests must not be finalized by inventing agent replies.
The operator may explicitly close pending requests, which records non-official
`live_agent_turn_cancelled` events and then finalizes from the real official
messages that exist.

Task scope conflict reports are advisory Work Mode artifacts. When public
artifacts are written, AgentsAssemble may derive `task_scope_report.md` and
`task_scope_report.json` from task assignments to show obvious repeated
relative file or directory references across roles. This report is not
implementation approval, does not grant filesystem or git write permission,
does not expand globs or inspect the workspace, and must not include absolute
local paths, URLs, prompts, provider output, raw task bodies, or Play Mode
chatter. Agents still need an explicit implementation decision before editing
files.

The React workroom queue follows the same boundary. It is a read-only projection
over safe lifecycle counts, review checkpoint presence, return-packet presence,
canonical final artifact coverage, shared-memory artifact coverage, and
task-scope overlap evidence. It helps the operator see what is blocked, what
needs review, what official artifacts exist, and whether implementation roles
appear to share file or directory scope, but it does not close pending turns,
promote lobby/play chatter, finalize meetings, start providers, grant
implementation permission, or expose raw artifact bodies, raw task bodies,
provider output, prompts, local paths, auth refs, endpoints, URLs, or session
ids. Browser clients
should consume a safe queue projection for this view instead of polling the full
meeting-detail payload to compute counts. Meeting SSE should likewise carry
safe projected live events, compact meeting metadata, and lifecycle state under
a stream-snapshot field rather than archive artifact bodies or private review
turn text; the full meeting-detail payload remains an explicit archive read
surface.
If return packets exist but no review checkpoint exists, the React queue may
show a read-only "review needed" warning; that warning is not a provider call,
approval grant, or automatic reviewer launch.

Release-health and test/build status follows the same read-only rule. CLI runs
may save a latest local report for operator visibility, but browser clients only
receive a safe queue projection: check ids, labels, queue grouping, pass/fail or
not-run state, duration, aggregate counts, and, only for a passed opt-in
`room_event_benchmark` check, an allowlisted benchmark summary with scheduler
predicate p99, first-speaker anchor-share improvement, and their
regression-signal thresholds. The projection must not include stdout/stderr
tails, raw benchmark logs, local paths, environment, command argv, provider
prompts, provider output, session ids, unknown future benchmark fields, or
controls that start checks from the browser.

## GUI Text And Refresh Policy

The vanilla GUI should optimize for trustworthy operations before polish:

- The Lobby should act like a staging or pick room: participant readiness,
  admission state, the current meeting id, and the basic Play Mode start/stop
  path are the primary surface.
- Session lifecycle, recovery, diagnostic, smoke, discovery, and other operator
  controls should stay available but live under an advanced area instead of
  dominating the default lobby.
- The Live tab may show a compact Play Mode surface with running/finished state,
  remaining time, participant status, and unofficial flow events. It must not
  make Play Mode chatter look like transcript or decision evidence.
- The React preview may show a global room command strip for current step,
  next action, participant counts, and safe navigation between room surfaces.
  That strip must read existing safe lifecycle/agent projections only; it must
  not start providers, run release checks, close turns, promote chatter, or
  expose private session, prompt, path, or credential fields.
- Natural-language room text should preserve readable tokens such as model
  versions, decimals, units, ellipses, and speaker names.
- Forced mid-token wrapping belongs on technical strings such as URLs, logs,
  ids, paths, and command-like output.
- Live room updates should append or update new event rows without replacing
  the whole panel when the existing DOM can be preserved.
- Flow status polling should update the small status surface without resetting
  the whole lobby when the visible control shape has not changed.
- Input drafts, scroll position, and latest navigation are operator state and
  should survive background refreshes.

## Room Event Bus Direction

Low-latency infrastructure ideas should enter AgentsAssemble as small,
measured room-event mechanics before any heavy queue stack. The near-term
shape is an append-only room event log, per-agent and per-browser cursors,
SSE fanout for new events, bounded queues for backpressure, and payloads that
reference large attachments or artifacts by id rather than copying bytes into
the event stream.

Benchmarking is part of that direction. Each room-event or scheduler slice
should add a cheap numeric check where practical: append latency, read-after
cursor latency, SSE delivery latency, queue wait time, dropped/backpressured
event count, and per-agent speaking distribution. Kafka, Flink, Redis Streams,
RDMA, DPDK, CPU pinning, and similar infrastructure are future scaling studies,
not requirements for the local-first v1 room.

The first benchmark surface is `assemble live-agent room-benchmark`. It calls
the existing local append/read functions, does not fsync when the product path
does not fsync, and reports local append/read/tail latency plus a synthetic flow
speaking-distribution imbalance ratio plus first-speaker anchor-share metrics.
When `--sse-samples N` is set, it also
reports a small `lobby_sse_append_to_frame_ms` measurement against the existing
local `/api/events/lobby` SSE endpoint. The stream checks the file-backed event
log on a low-latency polling cadence while keeping idle keep-alive frames on a
slower cadence; the number is a regression tripwire on the same machine, not an
SLA. Queue wait time and backpressure counts still require a later server/fanout
slice and remain out of scope.

`assemble release-health run --check room_event_benchmark --as-json` lifts a
safe `benchmark_summary` from that benchmark output so operators can compare
numeric p99, scheduler fairness, and first-speaker anchor-share signals without
reading raw paths, environment details, command arguments, or full benchmark
logs. Regression signal ceilings and floors are informational tripwires in this
local-first v1 stage; they do not make the check fail by themselves and React
must not start the benchmark from the browser.

## Local Resource Visibility

The room may show a small local resource monitor for operator awareness, but it
is read-only observability, not a scheduler or process manager. The resource
surface can report sanitized process basenames, pid/ppid, CPU percent, RSS,
load average, CPU count, and whether a process is an AgentsAssemble child or a
supervised resident. Aggregate CPU/RSS totals and role breakdowns are computed
only from the already sanitized, displayed process set. It must not expose argv,
env, cwd, absolute paths, config paths, endpoint URLs, auth refs, prompts,
provider output, log tails, account state, or provider session ids.

Resource polling should stay low pressure: cache OS process snapshots briefly,
cap the displayed process list, and avoid adding dependencies for the local v1
room. The monitor must not start, stop, restart, probe, recover, or otherwise
mutate provider processes; those actions remain explicit operator controls.

## Future Roadmap Board

The roadmap page should be separate from the live meeting progress view.
Meeting progress answers "where is this room right now"; the roadmap board
answers "where is the product going across versions."

When the richer responsive frontend is started, design a dedicated roadmap page
with a Trello/Jira-like shape:

- long-term epics.
- version or milestone lanes.
- feature cards with status, scope, acceptance checks, and links to docs,
  commits, or meeting records.
- visual treatment for planned, in-progress, review, and completed work.
- completed work visible but de-emphasized.
- current and next work easy to find without opening raw markdown.

Until then, do not add this as more vanilla GUI clutter. Keep roadmap source of
truth in `docs/roadmap.md` and product memory in this file.

## What To Build Next

Near-term work should favor backend contracts over visual polish:
- Keep the director-led, agent-owned room template grounded in safe fake or
  self-service residents first: director, product lead, engineering lead,
  design lead, and implementer are room roles and display/provider slots, not
  permission to launch real Opus, Codex, Kiro, Cursor, or other provider CLIs.
- Add discovery rows that say how a provider can join and what evidence supports it.
- Keep context durability labels such as provider-managed, process-lifetime, and
  stateless-prompt visible on admission, roster, and startup-packet surfaces
  where they are accurate.
- Treat provider-managed resume adapters such as Codex, Kiro, and Grok as actual
  provider sessions only after a real continuity proof shows later turns can
  recall earlier private session context without AgentsAssemble replaying it.
- Keep `shared_memory/` resident meeting artifacts deterministic, official-only,
  and refreshed during long-running sessions.
- Keep the compact shared-memory room payload and resident prompt block aligned
  with those official-only artifacts.
- Keep GUI changes minimal: show trustworthy state and leave detailed front-end
  styling for a later pass.

## Source-Of-Truth Routing

- `docs/roadmap.md` tracks status and priority.
- `docs/product/V0_1_RELEASE_CHECKLIST.md` owns the current release-hardening
  bar for the core usable flow.
- `docs/product/legacy-react-parity-matrix.md` owns the React default-route
  parity evidence and fallback gate.
- `docs/live-session-room-model.md` owns room semantics.
- `docs/live-cli-room-current-architecture.md` owns the implemented local CLI
  room map: canonical state, WebSocket protocol, Agent Bridge lifecycle,
  provider extraction rules, verification, and remaining boundaries.
- `docs/provider-architecture.md` owns provider and adapter boundaries.
- `docs/live-agent-ops.md` owns operator commands, readiness, and verification.
- `docs/product/OPERATING_MODEL.md` owns the product memory in this file.

When these files conflict, stop and surface the conflict before editing behavior.
