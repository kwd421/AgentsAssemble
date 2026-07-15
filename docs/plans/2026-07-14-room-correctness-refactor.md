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

### Current observed gap

On 2026-07-14 the actual browser UI still showed historical messages authored
as `Antigravity CLI` after the Agent Session profile was saved as `Makima` with
a new avatar. This is direct evidence that the acceptance contract below is
not yet satisfied, even though earlier hook/backend tests and progress entries
described it as covered. Treat those entries as evidence for narrower paths,
not as completion of the browser behavior.

The next identity repair must use the real frontend settings controls and the
real chat history path. It must inspect the stable author key carried by those
older events, the participant/session merge used by `useCanonicalRoom`, history
pagination, and message grouping/render memoization. Do not repair it by
rewriting old event rows or by matching provider labels, session names, or
display names. The only valid join key is canonical `participant_id`.

### 2026-07-14 real-path investigation

The reported room was inspected through all three relevant layers rather than
from helper tests alone:

1. `.agentsassemble/rooms/rooms.sqlite3` stores participant and Agent Session
   `antigravity-antigravity-cli` as `Makima`. Historical messages retain the
   event-time label `Antigravity CLI`, but their actor key is the same stable
   participant ID. This is the intended durable shape.
2. A newly authenticated canonical `/ws?ticket=...` snapshot exposes the
   participant and session as `Makima` while preserving the old event author
   snapshots. The server is therefore not losing the renamed participant.
3. A freshly loaded production frontend bundle renders the old messages, the
   roster row, and the open detail dialog as `Makima`. The screenshot that
   still rendered `Antigravity CLI` came from a browser tab that was running an
   older hashed JavaScript bundle. A long-lived SPA tab does not replace its
   already executing bundle merely because `frontend/dist` was rebuilt.
4. The current canonical database has an empty avatar for this participant.
   The avatar visible in the earlier dialog is not evidence that the final
   profile-save command persisted it; an uploaded/cropped image can be visible
   as a local draft before the explicit profile Save action. Avatar completion
   therefore remains unproven until the actual upload, crop, canonical save,
   old-message reprojection, and reload path is exercised.

This narrows the remaining work without weakening the product contract. Do not
add a second local identity authority or rewrite historical events to compensate
for an old tab. Strengthen the browser test so a future bundle cannot regress
the live save path, and report stale-bundle evidence separately from canonical
state defects.

The remaining avatar defect was then reproduced in code: both the timeline and
member panel used truthy fallback. A current canonical avatar of `""` could
therefore revive either an event-time avatar or a legacy localStorage avatar.
The typing projection also preferred a potentially stale Agent Session label
over the current participant. The repair treats the presence of a canonical
participant profile as authoritative even when its avatar is empty, keeps
legacy profile data only as an explicit edit-form migration draft, and gives
the participant name precedence in the typing indicator.

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

The browser scenario must use the profile image file input and crop/apply UI,
then assert the resulting attachment URL on the already-rendered reply, roster,
and detail dialog before and after reload. A test that injects a profile object
directly is useful unit coverage but is not sufficient evidence for this path.

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
- 2026-07-14: `room.delete` now stops owned provider sessions with stable nested
  lifecycle operation IDs, then atomically deletes canonical room state while
  retaining the command principal, request ID, payload hash, room name, pending
  ACK, and cleanup status in `deleted_rooms`. Post-delete invite, identity,
  listener, provider registry, file, and socket cleanup is idempotent. If its
  final tombstone update fails, only the same command can resume cleanup; it
  does not revisit provider stop. Different payload reuse is an
  `idempotency_conflict`, while another request receives `room_deleted`.
  SQLite schema v4 and PostgreSQL revision `0003_deleted_room_commands` share
  this contract, with repository parity and v3 migration coverage. Commit 1.6
  is complete; the next slice is Phase 2.1, binding attention selection and
  pending session input in one transaction.
- 2026-07-14: active ambient attention now records the evaluation, advances all
  candidate evaluation cursors, claims the selected lease, and appends the
  source event plus pending attention identifiers to the selected Agent Session
  in one room transaction. Assignment to the bridge happens only after that
  commit. SQLite failure injection and the backend-neutral repository contract
  prove a failed session write leaves no job, lease, cursor advance, or pending
  input. The next slice is Phase 2.2, expiry-aware lease claims.
- 2026-07-14: SQLite and PostgreSQL lease claims now compare the active lease's
  persisted expiry at the transaction checkpoint. An elapsed lease transitions
  to `expired`, its job returns to `pending`, and the replacement lease is
  inserted before the transaction commits. Backend-neutral tests prove an
  injected rollback preserves the old lease and that a successful retry gives
  one new owner the lease; an unexpired different owner still conflicts. The
  next slice is Phase 2.3, bounded orphan-attention reconciliation.
- 2026-07-14: `RoomAttentionReconciler` now performs bounded startup repair for
  elapsed leases, active jobs without pending/active session references,
  session references to missing or terminal jobs, and work selected for removed
  participants. Every repaired room commits one `attention_reconciled` audit
  event, and diagnostics expose counts plus truncation. The reconciler shares
  backend-neutral job/lease query and cancellation operations; startup tests
  prove the controller invokes it. Valid unexpired leases owned by another
  generation remain exclusive. The next slice is Phase 2.4, durable monotonic
  `room.observed` checkpoints.
- 2026-07-14: `room.observed` now bypasses the general command-result table and
  writes a backend-neutral monotonic checkpoint. SQLite serializes it in the
  room write transaction; PostgreSQL uses an atomic `GREATEST` upsert. Equal or
  lower reports are no-ops and future reports are rejected. Agent Bridges batch
  up to 20 events or one second, wait for the correlated ACK before advancing
  their local cursor, and force-flush before graceful disconnect. The former
  0.25-second receive loop was removed; the one-second blocking socket timeout
  only services the local flush deadline and sends no poll. The next slice is
  Phase 2.5, provider-sync cursor authority and compatibility parity.
- 2026-07-14: `agent_attention_state.last_provider_sync_seq` is now the sole
  provider-context read authority. Packet construction and assignment require
  parity with both Agent Session compatibility cursor fields; mismatches fail
  closed instead of selecting a convenient copy. New local and external
  sessions initialize canonical and compatibility state in one transaction,
  and both canonical and compatibility turn-completion paths advance them
  together. A bounded startup reconciler restores missing copies, audits every
  repaired room, marks true nonzero divergence `recovery_required`, and leaves
  future or nonexistent cursors blocked. SQLite repository, controller,
  packet/finalization, rollback, and migration tests cover the contract;
  PostgreSQL contract tests compile but require a configured test DSN for real
  execution. Phase 2 is complete; the next slice is Phase 3.1, defining the
  canonical room-global settings record.
- 2026-07-14: full-suite verification exposed a deterministic remote-stop
  deadlock left by Phase 2.4: a Bridge flushed `room.observed` before sending
  `bridge.stopped`, while implicit `ensure_room()` waited on the lifecycle lock
  held by the kick command. `room.observed` now runs before implicit room
  creation and relies on repository atomicity plus bridge-generation checks.
  The invited external CLI kick E2E and an explicit held-lifecycle-lock test
  prove the final observation ACK no longer blocks shutdown confirmation.
- 2026-07-14: Phase 3.1 defines a strict canonical room-global settings record
  containing only label, topic, appearance, conversation mode, bounded relay
  count, and custom channels. Invalid modes, relay counts, asset URLs, and
  channel shapes now have a fail-closed domain validator for repository writes.
  User notification/read state and participant roles are explicitly rejected
  at this boundary. The legacy JSON path remains behaviorally unchanged until
  both repository backends and the explicit migration exist. The next slice is
  Phase 3.2, adding this record to SQLite and PostgreSQL.
- 2026-07-14: Phase 3.2 makes the strict room-global settings record
  authoritative in both SQLite schema v5 and PostgreSQL revision
  `0004_room_global_settings`. Repository transactions create, validate, and
  update the settings row while keeping `rooms.label` synchronized; missing or
  corrupt rows fail closed. Canonical HTTP, invite, channel, and realtime
  routing paths now use the injected repository. `room_settings.json` is kept
  only as temporary user-preference compatibility storage, and schema upgrades
  intentionally backfill defaults rather than guessing legacy global values.
  Backend-neutral contracts cover update, rollback, validation, and label
  projection. Full GUI regression also exposed a legacy invite that referenced
  no canonical room. Invite creation now requires an existing repository room;
  joining a stale deleted-room invite revokes the issued session and returns an
  explicit error instead of auto-creating a room or crashing the request. The
  next slice is Phase 3.3: an explicit dry-run/backup/fingerprint migration for
  existing legacy room-global settings.
- 2026-07-14: Phase 3.3 adds `assemble room migrate-room-settings`, scoped to
  the canonical SQLite source before any PostgreSQL authority transfer. A
  dry-run writes a reviewable plan with separate source-global and target
  fingerprints. Apply requires that unchanged plan, takes consistent backups
  of the legacy JSON and SQLite database under the database write lock, updates
  all eligible settings and room-label projections in one transaction, verifies
  the committed rows, and stores an applied fingerprint so stale JSON cannot be
  replayed over later canonical edits. Legacy source interpretation is isolated
  from database effects. Invalid modes/relay values, alias conflicts, malformed
  channels, and orphan room entries produce blocking per-room repair issues;
  no defaults or fallback values are substituted. Eleven behavior tests cover
  CLI dispatch, backup, fingerprint changes, preference-only changes, rollback,
  and replay prevention. A dry-run against a v5 copy of current local data found
  15 candidate entries, 6 eligible changes, and 10 blocking issues: four invalid
  `free`/`quiet` modes and six orphan room entries, with one room in both groups.
  No live user data was applied. The next slice is Phase 3.4, moving user
  preferences to their owning repository.
- 2026-07-14: Phase 3.4 moves room notification mode, per-channel notification
  mode, and read cursors into strict user-scoped rows in `identity.db`. Browser
  settings requests carry their session identity or hashed device credential;
  reads and writes are isolated by `(user_id, room_id)`. Participant roles now
  render from canonical participant rows and are no longer copied through room
  settings. Room-global updates and user-preference updates are separate HTTP
  writes, and the server rejects a mixed write instead of risking partial
  success across the room and identity databases. Legacy preferences use a
  separate `migrate-room-preferences` dry-run/apply command whose plan binds an
  explicitly chosen existing user, source fingerprint, and target fingerprint;
  it never guesses an owner. Apply is transactional, backed up, verified, and
  replay-marked. A dry-run against copies of current local data found 15 legacy
  preference entries: one real change, eight unchanged canonical-room targets,
  and six orphan-room blockers. No live data was applied. The next slice is
  Phase 4.1, introducing bounded PostgreSQL connection pooling. Final
  verification passed all 3,223 Python tests with 34 environment-dependent
  skips, all 104 frontend tests, the production frontend build, `compileall`,
  and `git diff --check`. The full suite also found 17 legacy HTTP/WebSocket
  fixtures that relied on invite or channel requests implicitly creating a
  missing room. Those fixtures now create their canonical room explicitly; the
  removed product fallback was not restored.
- 2026-07-14: Phase 4.1 replaces per-operation PostgreSQL connects with one
  repository-owned bounded `psycopg_pool`. Startup waits at most 10 seconds for
  a minimum connection; the pool permits 1-8 connections, at most 32 queued
  borrowers, and a 5-second acquisition timeout. Repository and GUI shutdown
  close it idempotently, including startup failure after repository creation.
  Public diagnostics use a numeric allowlist and cannot expose a DSN or
  arbitrary driver values. Unit fakes prove bounded construction, reuse,
  timeout propagation, partial-start cleanup, closed-state rejection, and
  secret redaction; the optional `psycopg_pool 3.3.1` integration path also
  passes, including construction against the installed library API without
  opening a network connection. Final verification passed all 3,232 Python
  tests with 36 environment-dependent skips, all 104 frontend tests, the
  production frontend build, `compileall`, and `git diff --check`. The 28 real
  PostgreSQL backend contracts remain deliberately unclaimed because this host
  has no test DSN; Phase 4.3 will supply one through a CI service. The next
  slice is Phase 4.2, reusing a command unit's checked-out transaction
  connection for all reads in that unit.
- 2026-07-14: Phase 4.2 binds a PostgreSQL transaction's checked-out connection
  to its synchronous execution context. Repository-level reads reached from a
  command helper now reuse that connection and transaction snapshot instead of
  borrowing a nested pool connection; the binding is cleared before
  post-commit publication. Nested repository write transactions fail before a
  second checkout and direct callers must use the active `RoomTransaction`.
  Optional-driver tests prove one checkout covers command lookup, a top-level
  room read, and ACK storage, then prove the first read after commit borrows
  normally. A real-DSN contract records `psycopg_pool.requests_num` and requires
  an exact delta of one; it remains pending execution until Phase 4.3 provides
  PostgreSQL in CI. Final verification passed all 3,235 Python tests with 39
  environment-dependent skips, all 104 frontend tests, the production frontend
  build, `compileall`, and `git diff --check`. The next slice is Phase 4.3,
  making the real backend contracts mandatory in GitHub Actions.
- 2026-07-14: Phase 4.3 adds a dedicated PostgreSQL 16 GitHub Actions service
  job and a strict `python -m tests.run_postgres_contracts` entrypoint. The
  runner checks the DSN and complete PostgreSQL extra before loading tests,
  fails when discovery finds zero tests, and treats any skip in the selected
  repository, migration, schema, or pool contracts as a failed job. This keeps
  the ordinary portable suite's environment-dependent skips from being
  mistaken for PostgreSQL parity evidence. The strict entrypoint passed all 54
  selected contracts with zero skips against an isolated UTF-8 PostgreSQL 17
  instance on this host. The official service job uses PostgreSQL 16 and cannot
  be claimed as hosted CI evidence until the commit is pushed and that job
  completes. Final local verification passed all 3,241 Python tests with 34
  environment-dependent skips, all 104 frontend tests, the production frontend
  build, workflow YAML parsing, `compileall`, and `git diff --check`. The next
  slice is Phase 5.1, inventorying the remaining `gui.py` routes and services
  before moving any composition boundary.
- 2026-07-14: Phase 5.1 records the 9,525-line GUI server's actual ownership in
  `docs/product/GUI_COMPOSITION.md`. The guarded parity matrix remains the exact
  authority for all 159 API/SSE method-path rows; the new inventory classifies
  route families as current core, active optional, compatibility, or deletion
  candidate and maps each retained family to behavioral tests. It also records
  split construction/lifetime for the repository, identity and invite stores,
  process/session monitors, tunnel and ticket services, bridge manager, and
  realtime controller. Seven exact routes have no production caller, but none
  is deleted or moved in this phase. The document fixes the Phase 5.2 service
  container boundary, startup/close order, and the canonical name/avatar
  history reprojection invariant so a context reset cannot turn this refactor
  into a redesign. Route-ownership, parity-inventory, and architecture-doc
  verification passed (31 tests) before commit.
- 2026-07-14: Phase 5.2 now has one typed `GuiApplicationServices` lifetime
  boundary shared by `serve_gui()` and `_make_handler()`. It retains the exact
  room repository, identity backend, invite-state configuration path,
  `FileAttachmentStore`, process/session supervisors, tunnel, WebSocket ticket
  store, native bridge manager, and canonical realtime controller. Post-bind
  `start(server_url)` preserves legacy autostart ordering. Idempotent shutdown
  stops background services, closes the HTTP transport, and closes the owned
  repository last; cleanup continues after an individual failure, while
  borrowed resources are never closed. `GuiDeps` receives the same explicit
  identity and media-store instances. Production shutdown no longer locates
  the realtime controller through a generated handler class. The invite token
  implementation remains honestly documented as server-global state; this
  refactor centralizes its configuration but does not invent a pass-through
  abstraction or migrate hosted authority. Final GUI discovery passed all 470
  tests after the media-store injection; the focused lifecycle, concurrency,
  repository, attachment, room-route, ownership, and documentation subset also
  passed all 132 tests. Name/avatar history
  reprojection remains keyed by canonical `participant_id` and is unchanged.
  Phase 5.3 is next: move only retained legacy read/diagnostic families whose
  responsibility and behavioral tests are already identified.
- 2026-07-14: Phase 5.3 step 1 moves legacy meeting list/detail, lifecycle,
  workroom queue, and meeting SSE reads out of the generated handler and onto
  `Router`. `LegacyMeetingQueryService` owns the read-only use cases;
  `legacy_meeting_queries.py` owns archive/workroom/SSE redaction projections;
  `legacy_meeting_records.py` owns safe path resolution, final/live record
  selection and merge semantics, and host-approved resident binding evidence.
  The historical query names remain re-exported from `agentsassemble.gui`, so
  compatibility imports and patch points keep working without duplicate
  implementations. Meeting payload, lifecycle, workroom, stream, traversal,
  route-ownership, and parity tests pass; the full GUI discovery also passes
  all 470 tests in 228.982 seconds. The user-visible Agent Session
  identity contract remains unchanged and covered: canonical name/avatar
  changes reproject already loaded messages and later history by
  `participant_id`. The next slice is Phase 5.3 step 2, classifying and moving
  only the retained resident-agent read/diagnostic routes while leaving all
  seven deletion candidates in the legacy handler chain.
- 2026-07-14: Phase 5.3 step 2 begins by moving the two retained dynamic
  resident reads, `GET /api/live-agents/{agent_id}/room` and
  `GET /api/live-agents/{agent_id}/return-packet`, to Router ownership.
  `LegacyLiveAgentQueryService` now owns agent lookup, per-agent live-event
  visibility, projected return-packet discovery, targeted artifact reads,
  shared-memory/DM/lobby/side-chat aggregation, and meeting-list projection.
  `lobby_queries.py` owns the shared backward-scanning history reads instead
  of making the service import `gui.py`. Historical function names remain
  re-exported from `agentsassemble.gui`. The next slice is the retained
  health/readiness/process diagnostic family; discovery, preflight, and smoke
  remain later, separately verified slices. The seven deletion candidates
  remain untouched. Canonical Agent Session profile updates must still
  reproject every loaded and later-loaded message by `participant_id`, so an
  agent name/avatar change updates all chat history rather than only future
  messages.
- 2026-07-14: The next Phase 5.3 slice moves durable resident diagnostic
  histories into `LegacyLiveAgentDiagnosticQueryService`. It now owns filtered
  operation history, process lifecycle-event history, session-run listing,
  process snapshot acquisition for readiness overlays, and the overlay policy
  used by both session-run responses and health summaries. The read Router
  calls this typed service directly instead of receiving three payload
  callbacks and a separate session-run controller. Existing free-function
  names remain re-exported through `agentsassemble.gui`. Focused process-smoke,
  session-run, monitor, and route tests pass all 60 cases. Process list
  connection evidence, health aggregation, and readiness error mapping remain
  in `gui.py` and are the next slice; discovery/preflight/smoke remain separate
  after that. The seven deletion candidates and canonical name/avatar history
  reprojection are unchanged.
- 2026-07-14: Process-list and process-mutation connection evidence now live
  in `legacy_live_agent_process_projection.py`. The module owns expected-agent
  matching, meeting/provider/connection compatibility, reconnect timestamp
  comparison, status projection, and safe identity labels. Both the diagnostic
  GET service and existing start/stop/restart mutation payloads import this one
  implementation, so the refactor does not create read/write drift. The HTTP
  dependency object no longer receives a process payload callback. Focused
  room/process/session-recovery checks passed 58 tests; process-service,
  mutation-route, health, route-ownership, and parity checks passed 56 tests.
  Readiness and health aggregation remain in `gui.py` and are next. Discovery,
  preflight, smoke, all seven deletion candidates, and canonical name/avatar
  history reprojection remain unchanged.
- 2026-07-14: Resident readiness and session-check projection now belong to
  `LegacyLiveAgentDiagnosticQueryService`. It takes one process snapshot,
  invokes the existing session readiness contract, and enriches the result
  with the same redacted process reason used by health. Existing ensure,
  recovery, POST check, and direct-import callers continue through re-exported
  function names; the GET Router calls the service directly and retains its
  `not_found`, `invalid_request`, and `storage_error` mappings. Focused
  readiness probe, recovery, session-run, and Router checks passed 68 tests.
  Health aggregation is the next slice. Discovery/preflight/smoke, the seven
  deletion candidates, and canonical name/avatar history reprojection remain
  unchanged.
- 2026-07-14: Resident roster filtering and admission projection now belong to
  `LegacyLiveAgentRosterQueryService`. It owns safe/unsafe roster response
  shaping, quota-field removal, removal of untrusted stored admission fields,
  and recomputation from a host-authored meeting record. The read Router calls
  this service directly instead of receiving `live_agents_payload` as an
  untyped callback; legacy flow, registration audit, health, tests, and external
  imports continue through compatibility aliases in `agentsassemble.gui`.
  Focused roster, health, lobby/social, route-ownership, and parity verification
  passed all 105 tests. The next coherent slice is retained health aggregation:
  create a typed health query module that imports roster, process, diagnostic,
  lobby, session-run, and event projections directly; preserve the public
  `live_agent_health_payload` alias; then run every `test_gui_server_health*`
  and session-run monitor family before committing. Discovery, preflight, and
  smoke remain later independent slices. Do not move the seven deletion
  candidates during these extractions. The canonical identity acceptance
  condition is still mandatory: after an agent settings save changes name or
  avatar, roster, open detail UI, typing state, already loaded chat, history
  pages fetched later, and new messages must all resolve the current profile by
  stable `participant_id`; event-time author snapshots are fallback only for
  deleted, imported, or otherwise unavailable participants.
- 2026-07-14: Resident health aggregation now belongs to
  `LegacyLiveAgentHealthQueryService`, which is injected into the read Router
  directly. `legacy_live_agent_health_queries.py` combines agent, admission,
  process, connection, readiness, sandbox, shared-memory, observation, durable
  run, and monitor summaries without importing `gui.py`.
  `legacy_live_agent_observation_health.py` owns lobby/live cursor comparison
  and turn-terminal event interpretation; its five shared observation queries
  are also used explicitly by the session-run stale-observation restart policy.
  This dependency was previously hidden inside `gui.py` and was exposed by the
  first extraction test as a `NameError`; it was fixed at the source by naming
  the shared boundary, not by adding a fallback. `legacy_live_agent_session_run_health.py`
  owns durable run/readiness overlays and monitor sanitization. The Router no
  longer receives process supervisor, monitor, or free-function health
  callbacks. Focused verification passed 61 tests, then the complete process,
  readiness, recovery, session-run, health, route-ownership, and parity group
  passed all 310 tests; the complete GUI discovery then passed all 472 tests in
  224.611 seconds. `gui.py` is now 7,514 lines, down 674 lines in this
  slice; the new modules are 345, 270, and 157 lines and split by data source
  and failure mode rather than arbitrary line targets. Next, inventory and move
  discovery, preflight, and smoke as independent compatibility slices, leaving
  all seven deletion candidates in place until a separate deletion decision.
  No later refactor may regress canonical Agent Session identity reprojection:
  an agent name/avatar save must update old loaded chat, later history pages,
  typing state, roster/detail views, and new chat by `participant_id`.
- 2026-07-14: Configuration-only resident preflight is now separated from
  discovery and smoke. `LegacyLiveAgentPreflightService` owns payload-to-config
  validation without process startup, and `gui_legacy_live_agent_preflight_http.py`
  owns the existing POST method/path, operation audit, status mapping, and
  response. `diagnostic_report_projection.py` owns the config-path/message
  redaction shared by preflight and provider-health, so moving the route did not
  weaken the information boundary. `agentsassemble.gui` still re-exports
  `live_agent_preflight_payload` and the historical private projection aliases
  for compatibility. Focused service, HTTP behavior, full preflight policy,
  provider-health, route-ownership, and parity verification passed all 120
  tests. Discovery is next because it writes generated configs and therefore
  must not share the preflight service. Credential-free, official-round,
  session, real-session, and readiness smoke remain later, independently
  verified slices. The seven deletion candidates and canonical name/avatar
  history reprojection contract remain unchanged.
- 2026-07-14: Local CLI discovery now belongs to
  `LegacyLiveAgentDiscoveryService`. The service owns command discovery,
  exact agent/command approval filtering, optional generated config writes,
  collision-checked council/agent session bundles, and next-command output.
  `gui_legacy_live_agent_discovery_http.py` owns the existing POST route and
  records counts, join/context/sandbox/evidence values, and bounded agent IDs
  without persisting resolved executable paths or command names. Existing
  `live_agent_discovery_payload` and operation-detail imports remain available
  through `agentsassemble.gui`. Focused payload, actual HTTP, approval,
  route-ownership, and parity verification passed all 51 tests. The remaining
  diagnostic work is smoke: first classify credential-free, official-round,
  session, real-session, and aggregate readiness routes by side effects and
  error/redaction contract, then move one coherent family per commit. The seven
  deletion candidates remain visible in the old handler chain, and name/avatar
  history must continue to resolve through canonical `participant_id`.
- 2026-07-14: Credential-free basic resident smoke and credential-free
  official-round smoke now belong to `LegacyLiveAgentSmokeService` and
  `gui_legacy_live_agent_smoke_http.py`. The service owns loopback JSON calls,
  timeout normalization, and core smoke invocation; the Router module owns the
  two exact POST paths, operation recording, and their intentionally different
  error contracts. Basic smoke still maps a `LiveAgentSmokeFailed` contract
  failure to `409` and transport/value failures to `502`; official-round smoke
  maps all three to `502`. Internal callbacks use the bound loopback server
  URL, never the request `Host` header. The first integration run caught this
  boundary because the test supplies `Host: 127.0.0.1:1`; fixing the source
  restored real credential-free basic and official-round execution. Aggregate
  readiness still calls the historical `agentsassemble.gui` smoke seams, so
  those compatibility wrappers and runner patch points remain explicitly until
  readiness itself moves. Focused service/route, real GUI smoke, readiness,
  core smoke, ownership, and architecture checks pass; session smoke is the
  next slice, followed by real-provider smoke and aggregate readiness. The
  seven deletion candidates remain unmoved. Canonical identity remains a hard
  acceptance contract: an Agent Session name/avatar save must immediately
  reproject old loaded messages, history fetched later, typing state, roster,
  detail UI, and new messages by stable `participant_id`; persisted author
  snapshots are fallback only when no current participant profile exists.
- 2026-07-14: Credential-free durable session smoke now joins the typed smoke
  service and Router without being conflated with real-provider execution.
  `LegacyLiveAgentSmokeService.run_session()` owns timeout/lobby-probe/soak
  normalization and receives its runner as an explicit dependency; GUI
  composition supplies a late-bound compatibility runner so existing test and
  operator patch points remain valid until aggregate readiness moves. Soak
  cycles remain limited to 0-5 and soak interval to 0-60 seconds. The HTTP
  route preserves the fixed `502` response `Session smoke could not be run.`
  for contract, validation, and transport failures and records only bounded
  group/meeting identity rather than raw exceptions, config paths, tokens, or
  smoke transcripts. Its success audit projection moved with the route and
  retains terminal-session, round, reply, lifecycle, and soak counts without
  provider credentials. Focused route, real GUI smoke, readiness, core smoke,
  and ownership verification passed all 90 tests. Real-provider session smoke
  is next and must keep its explicit approval/config gates and stronger result
  redaction separate; aggregate readiness follows after that. The seven
  deletion candidates remain untouched, as does canonical name/avatar history
  reprojection by stable `participant_id`.
- 2026-07-14: Approval-gated real-provider session smoke now uses the same
  typed smoke service and Router while remaining a distinct policy path. The
  HTTP boundary rejects missing or string-false operator approval before any
  runner call, then requires explicit live-agent, council, and agent config
  paths. Config values are used only by the runner and never copied into error
  responses or operation audits. Contract, validation, and transport failures
  return the fixed redacted `502` message; approved results are projected
  through one allowlist shared by the response and audit, preserving only
  status, bounded counts, lifecycle status, and approval/diagnostic booleans.
  `degraded` remains distinct from success and failure. GUI composition uses a
  late-bound runner so the established no-launch gate tests still prove that
  `run_live_agent_real_session_smoke` is not called without approval and all
  three configs. Focused Router, real-session security, direct smoke,
  readiness, and ownership verification passed all 57 tests. Aggregate
  readiness is the last smoke route left in the generated handler and is the
  next extraction. The seven deletion candidates and canonical name/avatar
  reprojection by stable `participant_id` remain unchanged.
- 2026-07-14: Aggregate resident readiness is now the final Phase 5.3 route
  moved from the generated handler to Router ownership.
  `LegacyLiveAgentReadinessService` owns health/smoke/probe orchestration and
  status policy; `legacy_live_agent_readiness_projection.py` separately owns
  bounded public results and redacted operation-audit details. The composition
  shares one health service and one smoke service between direct and aggregate
  routes, while late-bound runner injection preserves established test seams.
  The old readiness implementation and projection helpers were removed from
  `gui.py`, which is now 6,417 lines. Focused route, readiness, smoke,
  ownership, and architecture verification passed all 73 tests. Phase 5.3 is
  complete and Phase 5.4 thin composition is next. Separately, the user's
  browser screenshot proves the Agent Session name/avatar history contract is
  still broken on at least one real path: old `Antigravity CLI` messages did
  not reproject to the saved `Makima` profile. Reproduce and fix that actual
  UI path before any future claim that identity reprojection is complete.
- 2026-07-14: The real-path identity investigation separated stale frontend
  execution from canonical state. SQLite and a directly authenticated room
  snapshot both use participant ID `antigravity-antigravity-cli` and current
  name `Makima`; a freshly loaded production bundle reprojects the same old
  messages and roster/detail UI as `Makima`. The earlier screenshot was an old
  tab still executing a pre-fix hashed bundle. The participant's canonical
  avatar is currently empty, so the image part is not yet accepted. Before
  resuming Phase 5.4, extend the actual Playwright profile flow through image
  upload/crop/save and reload, add a component-level canonical-event rerender
  regression, run the frontend suite/build/E2E, and repeat a reversible profile
  save through the running frontend. Do not mark avatar reprojection complete
  from the dialog draft preview alone.
- 2026-07-14: The remaining identity path is now verified. Timeline projection
  no longer revives an event-time avatar when the current participant cleared
  it; roster/detail projection no longer revives a localStorage avatar when a
  canonical participant or Agent Session exists; and typing names prefer the
  current participant over stale session/progress labels. The local legacy
  profile remains available only inside the edit form so an operator can
  migrate it through `agent.configure`. Focused Vitest passed 24 tests, the
  complete frontend suite passed 106 tests, the two frontend/static Python
  suites passed 53 tests, the production build passed, and the canonical-room
  Playwright flow passed in 4.4 seconds. That browser flow selected an image
  file, used the crop/apply control, saved name and avatar through the real
  Agent Session settings UI, verified the already-rendered reply and roster,
  reloaded the application, and verified the same old reply again. A reversible
  save through the running port-8765 production UI also changed every visible
  old `Makima` message to `Makima UI 확인` immediately and restored it to
  `Makima`. Phase 5.4 thin composition is again the next slice.
- 2026-07-14: Phase 5.4 starts with the two retained legacy lobby commands.
  `LegacyLobbyCommandService` now owns promotion payload policy, failed
  promotion auditing, remote-bridge binding/provider resolution, server-side
  speech identity, mute enforcement, and governed append behavior. The Router
  owns `POST /api/lobby/promote` and `POST /api/lobby/remote`; `gui.py` keeps
  only service construction and a late-bound compatibility wrapper so existing
  requester patch points still work. The remote-bridge test now exercises the
  actual HTTP route and verifies the server-authored participant identity and
  returned lobby history. Promotion, remote policy, route ownership, and
  parity checks pass. The seven deletion candidates remain untouched. The next
  Phase 5.4 slice should move the current provider-login route separately from
  legacy live-agent create/session mutations.
- 2026-07-14: Current provider login is now separate from legacy frontend-agent
  creation. `ProviderLoginService` owns allowlisted command launch and the
  existing success/failure operation audit. `gui_provider_http.py` owns
  `POST /api/live-agent-create/login`, enforces the same local-operator gate,
  parses JSON, and maps command errors to the preserved `400` contract. The
  provider route tests cover registration, local-only denial, invalid JSON
  audit, and delegation; the existing real HTTP test verifies the injected
  launcher receives only the resolved native provider login command and now
  verifies its success audit. Focused provider, frontend-create, ownership, and
  parity checks pass. The legacy check/create deletion candidates remain in
  the handler and were not moved. The next Phase 5.4 slice should classify and
  move the retained self-managed room/session commands without moving
  `/api/live-agent-room/expel`.
- 2026-07-14: Self-managed stop/resume is now a separate retained compatibility
  family. `LegacySelfManagedAgentService` owns the existing command call and
  success/failure operation audit; its Router module owns JSON parsing and the
  preserved `400` response with agent details. Focused service tests still use
  injected signal/launcher seams, while Router tests prove stop and resume
  dispatch, malformed JSON audit, and error mapping without touching a real
  process. `delete-session` was deliberately not bundled into this service:
  it owns server process-group, generated config, meeting binding, and roster
  deletion, which is a different failure and verification boundary. The
  `expel` deletion candidate also remains untouched. The next slice should move
  delete-session behind its own typed service and Router route.
- 2026-07-14: Frontend-created session deletion is now Router-owned without
  being conflated with self-managed process signaling. The existing
  `delete_live_agent_session_payload()` still performs owned-group stop/delete,
  managed-config removal, meeting binding cleanup, and roster deletion in its
  original order. `LegacyLiveAgentRoomSessionService` adds only the preserved
  operation audit and command boundary; its HTTP module owns JSON parsing and
  the existing `400` response shape. Focused tests cover the real deletion
  behavior, service success/failure audit, Router delegation/error mapping,
  frontend API path, route ownership, and parity. `/api/live-agent-room/expel`
  remains an unmoved deletion candidate. The next Phase 5.4 slice should move
  the retained legacy meeting start/finalize/review command family.
- 2026-07-14: Retained meeting start and finalize commands are now one explicit
  lifecycle boundary. `LegacyMeetingLifecycleService` owns domain invocation
  and bounded success/failure operation audits;
  `legacy_meeting_operation_projection.py` owns finalize/shared-memory audit
  projection; and `gui_legacy_meeting_lifecycle_http.py` owns exact and dynamic
  Router transport. `gui.py` retains compatibility imports for direct helper
  callers but no longer parses or executes either HTTP command. Focused Router
  and ownership checks passed 13 tests, the real server meeting-start suite
  passed 26 tests, and real finalize/moderation coverage passed 15 tests.
  Review checkpoints were deliberately not bundled because they own waiting,
  timeout, cancellation, and checkpoint-state policy. The next Phase 5.4 slice
  is that review-checkpoint family; the official-turn request/call/sequence/
  rounds/round/preset family follows as a separate command-policy boundary.
  The seven deletion candidates remain untouched. Canonical Agent Session
  profile updates must continue to resolve all loaded and paged historical
  messages, typing state, roster, and details from current participant identity
  by stable `participant_id`; event-time names and avatars are not historical
  display authority.
- 2026-07-14: Resident review checkpoints are now outside the generated HTTP
  handler. `LegacyReviewCheckpointService` owns readiness gating, validated
  target selection, sequential request/reply waits, non-official artifacts,
  and prompt-free operation audits. Its Router module owns only JSON parsing,
  the dynamic meeting path, and preserved `400` mapping. Shared sequential
  result/status normalization moved to `legacy_turn_results.py`; bounded
  review and turn-sequence audit projections moved beside the existing meeting
  lifecycle projection. The old callable remains as a thin compatibility
  wrapper in `gui.py`, but all orchestration lives in the service module.
  Focused service/Router/result/ownership checks passed 17 tests, checkpoint artifact
  and CLI checks passed 29 tests, and the real server moderation/finalization
  suite passed 15 tests including two-agent replies, degraded readiness,
  private prompt exclusion from official records, and redacted audits. The
  next Phase 5.4 slice is the official-turn request/call/sequence family;
  round/rounds/preset follows separately because it adds scheduling, progress,
  finalization, and preset policy. The seven deletion candidates remain
  untouched, and Agent Session history continues to resolve current profile
  name/avatar by stable `participant_id` rather than event-time display fields.
- 2026-07-14: Official-turn request, call, and sequence are now one retained
  service boundary. `LegacyOfficialTurnService` owns private request creation,
  verified reply waits, ordered sequence validation, timeout/skip/cancel status,
  and prompt-free success/failure audits. Its Router module owns the three
  dynamic paths and preserved invalid-JSON/`400` behavior. The reentrant
  per-meeting lock moved to `legacy_turn_scheduler.py` so request, round,
  remaining-round, and Codex-join paths still share the same lock authority.
  Existing module-level payload names remain import-compatible through
  `gui.py`. Focused service/Router/ownership tests passed 17 tests; the real
  official-turn server suite passed 21 tests; and review/finalization
  integration passed 20 tests after the shared request move. These verify
  private prompt exclusion, official reply provenance, timeout continuation,
  stop-on-timeout skips, cancellation, target normalization, and operation
  audit redaction. The next Phase 5.4 slice is round/rounds/preset as one
  scheduling and progress policy, followed by a fresh inventory of the
  generated handler. The seven deletion candidates remain untouched, and
  Agent Session historical identity still resolves from current canonical
  participant name/avatar by stable `participant_id`.
- 2026-07-15: Official round, remaining-round batch, and play-preset commands
  now form one explicit scheduling boundary. `LegacyOfficialRoundService`
  owns round expansion, the shared per-meeting lock, answered-round progress,
  bounded batch stop/skip policy, optional meeting finalization, and
  prompt-free success/failure audits. Its Router module owns all three dynamic
  HTTP paths and preserves invalid-JSON and `400` behavior. Existing
  `gui.py` payload names remain import-compatible for CLI/session callers, but
  the generated handler no longer parses or executes a legacy meeting mutation
  route. Focused service/Router/ownership tests passed 11 tests; the real
  official-turn server suite passed 21; moderation/preset passed 18; CLI call
  timeout passed 22; session lifecycle passed 26; and session recovery passed
  20. Phase 5.4 now requires a fresh retained-handler inventory before moving
  the next resident-agent or Codex compatibility family. The seven deletion
  candidates remain untouched. Agent Session name/avatar history continues to
  resolve current canonical identity by stable `participant_id`, including an
  explicitly empty current avatar.
- 2026-07-15: Retained resident presence is now a separate compatibility
  boundary. `LegacyLiveAgentPresenceService` owns registration, heartbeat
  metadata, graceful leave, and the existing operation-audit policy;
  `legacy_live_agent_presence_projection.py` owns bounded registration and
  leave details. Heartbeat intentionally remains unaudited, matching the prior
  high-frequency path. The Router owns `POST /api/live-agents` plus dynamic
  heartbeat and leave routes, while `gui.py` keeps import-compatible payload
  names for CLI and test callers. Focused service/Router/ownership tests passed
  11 tests; real roster and lobby-social HTTP coverage passed 46; CLI presence,
  runtime-process, and delegate coverage passed 68; and server lifecycle, MCP,
  and self-service coverage passed 49. Registration admission evidence,
  heartbeat cursor/error metadata, leave cursor persistence, invalid-JSON
  audits, and response shapes remain unchanged. The next slice should keep
  resident speech, probe diagnostics, and engagement settings as separate
  boundaries rather than creating one generic live-agent service. The seven
  deletion candidates and canonical name/avatar history reprojection remain
  unchanged.
- 2026-07-15: Resident engagement-mode updates are now a focused settings
  boundary rather than part of the generated handler or presence service.
  `LegacyLiveAgentEngagementService` owns validation, mutation, previous/current
  mode auditing, and invalid-JSON failure audit; its Router owns the dynamic
  endpoint. Focused service/Router/ownership plus real room-payload coverage
  passed 26 tests, and the complete CLI presence suite passed 46. Unknown modes
  keep the existing fail-closed error and do not mutate the resident record.
  The next slices are probe diagnostics and resident speech. The seven deletion
  candidates and canonical name/avatar history reprojection remain unchanged.
- 2026-07-15: Resident reply probes are now a focused diagnostic boundary.
  `LegacyLiveAgentProbeService` owns finite/default/capped timeout
  normalization, probe execution, and prompt-free success/failure auditing;
  its Router owns the dynamic endpoint and preserves missing-agent `404` versus
  other-domain `400` behavior. Focused service/Router/ownership tests passed 12,
  the real HTTP health-probe suite passed 4, and CLI timeout diagnostics passed
  57. Existing tests can still patch `agentsassemble.gui.run_live_agent_probe`
  because composition uses a late-bound runner. The next slices are resident
  lobby/DM speech and official-record replies, kept separate by persistence and
  artifact side effects. The seven deletion candidates and canonical
  name/avatar history reprojection remain unchanged.
- 2026-07-15: Ordinary resident lobby and friend-DM replies are now a focused
  speech boundary. `LegacyLiveAgentSpeechService` owns lobby identity/mute
  policy, source-event idempotency, flow turn conflict, smoke redaction,
  governed append, reply cursors, DM delivery, and heartbeat projection. The
  shared lobby lock and GUI append/scope callbacks are explicit dependencies,
  so the service does not import `gui.py` and established concurrency patch
  points remain late-bound. Its Router owns both dynamic POST paths and keeps
  invalid JSON and domain failures at `400`. Focused service/Router/turn tests
  plus lobby/social, real-session smoke, and room-route coverage passed 87;
  CLI/MCP coverage passed 58. The next slice is official/review reply recording,
  which remains separate because it mutates meeting events, official artifacts,
  and shared memory. The seven deletion candidates and canonical name/avatar
  history reprojection remain unchanged.
- 2026-07-15: Resident official and review replies are now a separate
  official-record boundary. `LegacyLiveAgentOfficialReplyService` owns request
  validation, cancellation, reply idempotency, governed official append,
  official artifact/shared-memory refresh, heartbeat, and prompt-free audit.
  The audit projection allowlists shared-memory summary keys and never records
  reply content. Its Router owns the dynamic POST path while `gui.py` keeps the
  historical payload import. Focused service/Router/ownership tests passed 10;
  real moderation/official-turn/CLI coverage passed 71; review/MCP coverage
  passed 30. The generated handler now has no retained dynamic resident speech,
  presence, settings, or diagnostic route. Before the next extraction, refresh
  the fixed-path inventory for join brief, provider health, Codex invite/join,
  and the seven deletion candidates. Canonical name/avatar history reprojection
  remains unchanged.
- 2026-07-15: The retained external-resident join brief is now Router-owned.
  Request-to-builder mapping lives beside the existing side-effect-free packet
  builder in `live_agent_join_brief.py`; the thin HTTP route owns JSON parsing,
  request-host defaulting, and the established `400` contract. No registration,
  provider start, room write, operation audit, or token generation was added.
  Focused route/default/security tests and the existing real HTTP/CLI parity
  checks verify that generated packets remain identical and safe. The next
  retained fixed path is provider-health; Codex invite/join remain a separate
  meeting-session compatibility family. The seven deletion candidates and
  canonical name/avatar history reprojection remain unchanged.
- 2026-07-15: Provider-health is now Router-owned without changing its
  diagnostic contract. Request normalization moved beside
  `provider_health_report`; the route injects the late-bound reporter so real
  HTTP tests and integrations can still replace the GUI report runner without
  executing provider commands accidentally. Public-safe projection remains at
  the HTTP response boundary, including config-path and loader-detail
  redaction. The remaining retained fixed compatibility family is Codex
  invite/join. The seven deletion candidates and canonical name/avatar history
  reprojection remain unchanged.
- 2026-07-15: Retained Codex meeting-session invite/join is now Router-owned
  through `LegacyCodexSessionCompatibilityService`. The service owns invite
  config writes, pre-round validation under the shared meeting turn lock,
  resident config projection, session ensure/restart selection, and bounded
  success/failure audit that excludes provider session ids and local config
  paths. `gui.py` keeps only a direct-call compatibility wrapper and late-bound
  session callbacks, preserving existing tests and integration patch points.
  Focused route/service/ownership tests passed 14, the complete meeting-payload
  HTTP suite passed 20, and CLI/document/parity checks passed 53. The generated
  POST handler now contains only explicit deletion candidates; do not migrate
  those candidates before a separate compatibility decision. Canonical
  name/avatar history reprojection remains unchanged.
- 2026-07-15: Phase 5's handler boundary is now enforced by AST ownership
  tests. The complete exact-path inventory is limited to WebSocket/React/join
  shell transport plus the seven documented deletion candidates; adding any
  other exact API branch directly to `gui.py` fails the suite. Prefix-based
  React asset delivery remains an intentional transport responsibility. This
  closes the planned retained-route extraction without disguising deletion
  candidates as new abstractions. Final full Python/frontend/build/browser
  verification remains the next gate before declaring the phase complete.
- 2026-07-15: Phase 5 final verification is complete. Full Python discovery
  passed all 3,359 tests with 39 environment-dependent skips; frontend Vitest
  passed all 106 tests across 20 files; the TypeScript/Vite production build
  completed; and the canonical-room Playwright scenario passed against the
  real fixture server, covering desktop streaming and mobile control of the
  same session. `compileall` and `git diff --check` also passed. Existing
  SQLite `ResourceWarning` output in the Python suite and the Vite 500 kB chunk
  warning remain visible follow-up signals, not hidden failures introduced by
  this refactor. Phase 5 is complete. The seven handler deletion candidates
  still require their separately documented compatibility decision; no real
  provider runtime behavior changed, so no paid/provider smoke was run for
  this behavior-preserving composition slice.
