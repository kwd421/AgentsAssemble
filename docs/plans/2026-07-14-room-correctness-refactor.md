# Room Correctness And Maintainability Refactor

Status: active execution plan

Created: 2026-07-14

Starting branch: `codex/risuai-character-personas`

Starting commit: `cbdfca1a5961b5befc33cb575af540d63eb130f2`

## Immediate Goal

Freeze autonomous-conversation feature growth and finish the correctness and
ownership boundaries underneath the canonical shared-room product.

The first user-visible defect in this plan is:

> Changing an Agent Session's display name or avatar in the agent settings must
> immediately re-render every message by that participant, including already
> loaded history and history loaded later, with the current canonical room
> identity.

The broader goal is to remove known crash windows between command idempotency,
visible messages, turn completion, attention leases, and provider session state.

## Sources Of Truth

Read these before continuing this plan after a context reset:

1. `docs/product/CURRENT_SYSTEM.md`
2. This plan
3. The closest implementation and behavioral tests for the current phase
4. `docs/product/ATTENTION_MODEL.md` only for attention phases
5. `docs/product/ROOM_REPOSITORY.md` for repository and transaction phases
6. `docs/product/RUNTIME_OWNERSHIP.md` for process lifecycle or recovery phases
7. `docs/product/OPERATING_MODEL.md` for identity, security, or deployment authority changes

The review that motivated this plan is `/Users/seinel/Downloads/review.md`. It
is evidence, not repository authority.

## Current Product Decisions

- The canonical room path remains `RoomRepository` plus the ticket-authenticated
  `/ws?ticket=...` WebSocket.
- Provider-specific browser sockets, parallel event stores, polling live UI,
  one-shot provider execution, `claude -p`, and `codex exec resume --last` remain
  forbidden.
- Existing `ambient` behavior remains opt-in and experimental.
- Do not add semantic silence, reaction, handoff, defer, token budgets,
  scheduled wakeups, media attention, or a model-based speaker selector during
  this plan.
- Do not split `RoomTurnCoordinator` merely because it is long. Its turn,
  inflight, cursor, and lease invariants belong together until a different
  reason to change is proven.
- Do not migrate unused legacy routes into new modules. Inventory them first;
  remove only routes proven unused and only in a separate compatibility change.
- Full identity/invite/media migration to a hosted deployment is a separate
  architectural decision, not an automatic consequence of room refactoring.
- Keep unrelated untracked `.superpowers/` and
  `docs/plan-room-hygiene-bugfixes.md` untouched.
- Commit coherent verified slices. Push only on explicit user request.

## Confirmed Baseline

The following review findings were checked against the starting commit:

1. `RoomRealtimeController.handle_command()` executes a durable action before
   recording its command result. A crash between those operations can replay a
   visible mutation for the same request ID.
2. Provider `message_final` and turn/session/attention completion currently use
   two transactions. A crash between them can leave a visible answer with a
   busy session, active lease, or inflight source.
3. Active attention evaluation commits its job and lease before the selected
   event is connected to the Agent Session pending queue.
4. Room-global settings still live in `room_settings.json`, outside
   `RoomRepository` and the SQLite-to-PostgreSQL migration contract.
5. `PostgresRoomRepository` opens a fresh `psycopg` connection for each
   repository operation.
6. Non-ambient rooms currently persist shadow attention data for every final
   message.
7. Ambient routing is triggered from every committed `message_final` before a
   narrow user-text trigger policy is applied.

## Identity Defect: Required Contract

### Canonical identity

`participant_id` is the stable message author key. A message event may retain
the display name and avatar used when it was written as audit information, but
the normal room timeline must render the current canonical participant profile.

Resolution order for a participant still present in the room:

```text
event actor participant_id
-> current canonical participant
-> current canonical Agent Session only if the participant projection is absent
-> event author snapshot only for imported/deleted/unavailable participants
```

Local profile overrides must not replace canonical Agent Session identity.

### Update behavior

`agent.configure` profile-only updates must atomically update:

- canonical participant display name and avatar;
- canonical Agent Session display name and avatar;
- one `participant_updated` event;
- the command result once command atomicity is available.

The WebSocket client must apply `participant_updated` to the participant
projection and then recompute all loaded timeline messages. Loading an older
history page later must use that same current profile.

### Acceptance tests

- Backend: profile update changes participant and session, publishes one
  `participant_updated`, and the next provider assignment contains the new room
  name.
- Frontend hook: a stale historical event is reprojected after a live profile
  update without reconnecting.
- Frontend hook: history loaded after the update also uses the current profile.
- Component/browser: change name and avatar through the actual agent settings
  controls; the roster, open detail modal, old messages, newly loaded messages,
  typing state, and next new message all show the new identity.
- Reload: snapshot plus history still shows the new identity.
- Different participants with similar provider/session labels are never merged.

Do not accept a source-string test or a direct projection helper test as proof
of this user-visible behavior.

## Execution Order

### Phase 0 - Plan, identity repair, and ambient freeze

#### Commit 0.1 - Record the execution plan

- Add this document.
- No product behavior change.
- Verify `git diff --check`.

#### Commit 0.2 - Repair canonical identity reprojection

- Reproduce the real settings-to-history failure before changing code.
- Identify whether the failing boundary is command identity, participant event
  projection, session/participant merge precedence, stale HTTP roster refresh,
  history pagination, or component memoization.
- Fix the owning boundary rather than copying the profile into every old event.
- Add backend, hook, and component/browser behavioral tests described above.
- Preserve author snapshots in durable events for audit/import fallback.

#### Commit 0.3 - Mark ambient discussion experimental

- Label the room setting `자유 토론 (실험적)`.
- State clearly that it is bounded automatic relay, not human-like autonomous
  participation.
- Do not change existing room modes or defaults.

#### Commit 0.4 - Make shadow attention configurable

- Add `off | sample | full` server configuration.
- Default to `off` for ordinary operation.
- Ambient active routing must not depend on shadow persistence.
- `off` must create no shadow job/state writes and must not alter visible room
  output.
- `sample` must use a deterministic, documented sampling rule.

#### Commit 0.5 - Restrict ambient trigger kinds

- Allow plain human text, direct mention/reply, explicit room question, and an
  explicit trusted ambient-trigger marker.
- Reject empty content, room lifecycle/system events, vote events, and media
  that the provider cannot actually consume.
- Use fake persistent providers; no real-provider smoke is required.

### Phase 1 - Command and turn atomicity

#### Commit 1.1 - Add crash-window contract tests

Add backend-neutral repository/controller tests with failure injection after:

- domain mutation;
- event append;
- session update;
- ACK construction;
- command-result write;
- immediately before commit.

Each injected failure must yield either a complete commit or complete rollback.
Retrying the same principal/request ID must not duplicate visible events or state
transitions. Reusing the request ID with a different action/payload remains an
`idempotency_conflict`.

#### Commit 1.2 - Introduce `RoomCommandUnitOfWork`

The unit owns one repository transaction and exposes domain operations, prior
command lookup, ACK finalization, and command-result recording. It must not
expose SQLite connections, PostgreSQL connections, or backend SQL.

Read-only commands and lightweight monotonic checkpoints need not use the full
unit. External process calls must never run while a DB transaction is held.

#### Commit 1.3 - Make `message.send` atomic

One transaction must perform participant/mute validation, append the visible
message, build its ACK payload, and record the command result. Event publication
and routing happen only after commit.

#### Commit 1.4 - Move pure durable commands into the unit

Move profile-only `agent.configure`, participant profile, mute, and the durable
part of leave. Preserve current ACL and NACK behavior.

`room.delete` needs tombstone-scoped idempotency because deleting the room also
deletes ordinary room command results. Do not force it through the generic path.

#### Commit 1.5 - Finalize a provider turn in one transaction

One transaction must include:

```text
message_final
turn_finished
attention lease resolution
last_spoke_seq
last_provider_sync_seq
session idle transition
inflight clear
last visible message identity
```

Only after commit may the controller publish events, publish session state, and
assign the next pending input. Add failure injection proving no duplicate final,
orphan lease, stuck busy session, or cursor mismatch.

Run two real turns each with Codex and Claude Haiku only after fake-provider and
full automated tests pass. Never use `claude -p`.

#### Commit 1.6 - Add durable intents for external process effects

Handle `agent.start`, `agent.stop`, `participant.kick`, and `room.delete` as
separate, narrowly scoped durable workflows. Do not build one generic saga
framework first. Each action must prove that retry cannot start or stop the
provider twice.

### Phase 2 - Attention and observed-cursor durability

#### Commit 2.1 - Bind attention selection to pending session input atomically

An orchestration service, not the pure attention policy, must use one room
transaction for evaluation record, lease claim, selected session pending event,
and pending attention identifiers.

#### Commit 2.2 - Make lease claims expiry-aware

Expired active leases must be expired and reclaimed in one transaction. An
unexpired lease owned by another generation remains a conflict.

#### Commit 2.3 - Reconcile orphan attention records

At startup, audit and repair expired leases, jobs without session references,
session references without jobs, deleted participants, and jobs without a
pending/active turn. Recovery must be bounded and auditable.

#### Commit 2.4 - Make `room.observed` a monotonic checkpoint

- ACK before the bridge advances its local reported cursor.
- Greater sequence updates; equal/lower is a no-op; greater than room latest is
  rejected.
- Coalesce by bounded event count or timer and flush at disconnect.
- Do not store every checkpoint in the general command-result table.

#### Commit 2.5 - Select one provider-sync cursor authority

Use `agent_attention_state.last_provider_sync_seq` as canonical only after
parity assertions prove it matches the compatibility session fields. Do not
delete compatibility fields in the same commit.

### Phase 3 - Room settings repository authority

#### Commit 3.1 - Define canonical room-global settings

Room-global fields include label, topic, appearance, conversation mode, bounded
relay setting, and channels. Participant/user notification and read state do not
belong in this record. Participant role belongs in the participant record.

#### Commit 3.2 - Add settings to both repository backends

Add SQLite and PostgreSQL schema/migrations plus shared contract tests. Routing
must read injected repository settings rather than `room_settings.json`.

#### Commit 3.3 - Migrate existing settings explicitly

Provide dry-run, backup, fingerprint, apply, and verification. Writes are strict;
legacy reads may be tolerant only while producing an explicit repair report.
Never silently replace invalid persisted mode or relay values.

#### Commit 3.4 - Move user preferences to their owning repository

Move notifications, channel notifications, and read cursors to identity/user
preference storage. Keep this separate from room-global migration.

### Phase 4 - PostgreSQL operation

#### Commit 4.1 - Introduce a bounded `psycopg` connection pool

The repository owns the pool, transactions borrow one connection, shutdown
closes it, timeout is bounded, and diagnostics expose no DSN.

#### Commit 4.2 - Use one connection per command unit

Repository reads inside a command unit reuse its transaction connection rather
than opening nested standalone connections.

#### Commit 4.3 - Run PostgreSQL contracts in CI

Use a service container and run actual backend parity tests. A skipped test due
to missing driver or DSN is not success.

### Phase 5 - GUI composition and legacy classification

#### Commit 5.1 - Inventory remaining `gui.py` routes and services

Classify each as current, compatibility, or candidate for deletion. Record call
sites and behavioral tests. Do not move deletion candidates first.

#### Commit 5.2 - Introduce server-scoped application services

Construct and own room repository, identity repository, invite service,
controller, media store, and shutdown lifecycle in one explicit container.

#### Commit 5.3 - Move retained legacy read/diagnostic behavior

Move behavior only where responsibility and test boundary are clear. Preserve
public routes and payloads.

#### Commit 5.4 - Reduce `gui.py` to composition

Completion is based on ownership and import graph, not a target line count.
Configuration, dependency construction, route registration, and server
start/shutdown remain.

### Decision Gate - Hosted deployment authority

Do not automatically continue into full identity/invite/media PostgreSQL
migration. First report:

- which authorities remain local;
- required deployment topology;
- data migration and rollback requirements;
- source SQLite retirement behavior;
- security and credential boundaries.

Continue only after an explicit product decision. LISTEN/NOTIFY, Redis, Kafka,
and multi-instance autonomous workers remain out of scope until a real second
application instance is required.

## Verification Ladder

For every commit:

1. Run the closest targeted Python or Vitest tests.
2. Run `git diff --check`.
3. Inspect `git status` and the complete commit diff.

For shared transaction or repository changes:

```bash
python3 -m unittest discover -s tests -t .
AGENTSASSEMBLE_TEST_POSTGRES_DSN=... uv run --extra postgres \
  python -m unittest tests.test_postgres_room_repository
npm --prefix frontend test
npm --prefix frontend run build
```

Use the actual PostgreSQL contract command available at that commit; do not
claim PostgreSQL proof from a skipped run.

For user-visible identity, settings, leave/delete, or agent controls:

- start the local GUI on an unused port;
- operate the real frontend controls with the browser;
- verify both desktop and mobile layout when the touched UI is responsive;
- reload and load older history;
- verify the backend state only as supporting evidence, not as a replacement for
  the frontend flow.

Real provider smoke is required only after provider finalization or runtime
lifecycle changes. Claude smoke uses Haiku. Record requested and observed model,
same-session/PID evidence, TTFO, total time, error count, and cleanup.

## Completion Criteria

- Agent name/avatar changes re-render all existing and later-loaded messages.
- Same request retry produces no duplicate visible event.
- Rollback consumes no event sequence.
- Provider final and turn/session/lease completion cannot diverge.
- No orphan attention lease survives reconciliation.
- Room-global settings no longer depend on a local JSON authority.
- PostgreSQL uses a bounded pool and runs real CI contracts.
- `gui.py` is a composition root without moving dead legacy code elsewhere.
- Full automated verification and required browser/real-provider smokes pass.
- Documentation describes current behavior rather than planned behavior.

## Progress Log

- 2026-07-14: plan created from review of `cbdfca1a`; autonomous feature growth
  frozen; identity-history defect added as the first behavior fix.
- 2026-07-14: identity repair implemented locally. `agent.configure` now applies
  the canonical participant/session returned in its ACK immediately instead of
  relying only on a later WebSocket broadcast. `participant_updated` preserves
  an explicit empty avatar so another client can observe avatar removal. Hook,
  projection, component, backend, build, and real frontend rename/reload checks
  cover visible and later-loaded history. Committed as `336fb13`.
- 2026-07-14: ambient discussion UI relabeled as experimental bounded server
  relay, explicitly distinguishing it from human-like autonomous room watching.
- 2026-07-14: shadow attention recording made an explicit server setting.
  Ordinary operation defaults to `off`; deterministic `sample` records only
  canonical source sequences divisible by 16, while `full` preserves the
  previous diagnostic behavior. Ambient active routing remains independent.
- 2026-07-14: ambient trigger eligibility restricted to committed human/agent
  text and server-trusted trigger metadata. Votes, system/lifecycle kinds,
  empty text, and unsupported media-only events persist as silent decisions
  without waking a provider.
- 2026-07-14: repository contract coverage now injects failures after domain
  mutation, event append, session update, ACK construction, command-result
  write, and immediately before commit. Every backend must roll each window
  back without publishing an event or consuming a sequence, then accept one
  duplicate-free retry.
- 2026-07-14: `RoomCommandUnitOfWork` added as a backend-neutral room command
  transaction boundary. It owns in-transaction prior-command lookup, canonical
  payload hashing, ACK construction, result recording, and selected domain
  operations. A new command that exits without a durable ACK is rolled back;
  backend connections and SQL remain private to repository adapters.
- 2026-07-14: canonical `message.send` moved into the command unit of work.
  Participant state, visible event, ACK, and idempotency result now commit
  together; listeners broadcast and route only after commit. Failure injection
  at ACK recording proves rollback leaves no message, pending provider input,
  command record, or duplicate on retry.
- 2026-07-14: profile-only `agent.configure`, participant mute, and the durable
  part of participant leave moved into `RoomCommandUnitOfWork`. Name/avatar
  updates now commit participant, Agent Session, `participant_updated`, and ACK
  atomically, while in-memory provider labels and session-state publication run
  after commit. Mute compatibility-roster synchronization and leave token/voice
  cleanup also run after commit. Failure-injection tests prove rollback and
  exactly-once retry behavior; canonical mute state now overrides a stale
  compatibility copy. The next implementation slice is Commit 1.5, provider
  turn finalization in one transaction.
- 2026-07-14: successful provider `message.final` now uses one command unit for
  the visible final, `turn_finished`, `last_spoke_seq`, provider-sync cursor,
  active attention lease release, idle/inflight session transition, observed
  model fields, and durable ACK. Publication and next-pending assignment occur
  after commit. A failure injected at ACK recording leaves the original busy
  turn and active lease intact with no consumed event sequence; retry commits
  one final and one finish, and later duplicates return the stored ACK before
  active-turn validation. The next slice is Commit 1.6, narrowly scoped durable
  intents for external process effects.
- 2026-07-14: Commit 1.6 began with Agent Session start/stop. A private durable
  lifecycle intent is written before the process effect. An incomplete start
  retries through the bridge manager, which reuses the existing session-owned
  process and returns its opaque handle; an applied stop records
  `effect_applied` before final session cleanup, so retry does not stop the
  process twice. External bridge confirmation persists that applied marker
  before waking the waiting stop command. Failure-injection tests cover both
  process-started/handle-write-failed and process-stopped/final-write-failed
  windows. Lifecycle fields and owned handles are excluded from public session
  projection. The next slice is the participant-specific kick workflow.
- 2026-07-14: `participant.kick` now uses a participant-scoped private intent.
  Provider stop, credential revocation, socket disconnect, compatibility roster
  cleanup, and voice cleanup complete before one command unit commits canonical
  `kicked` state, one `participant_kicked` event, and its ACK. An ACK failure
  leaves an explicit detached/effect-applied intermediate state; retry skips the
  already-applied cleanup and cannot stop the provider twice. Agents with a
  pending kick cannot be restarted, kicked agents are not restored into the
  in-memory provider registry on controller startup, and moderation intent
  fields are removed from browser and bridge participant snapshots. The next
  slice is tombstone-scoped `room.delete` idempotency.
