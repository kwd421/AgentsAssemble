# Room Correctness And Maintainability Refactor Review Brief

Status: ready for external review

Branch: `codex/risuai-character-personas`

Implementation range: `cbdfca1a5961b5befc33cb575af540d63eb130f2..50ec92f7c507cabfe1dafe77b963d41fb7ffe1ec`

Plan: `docs/plans/2026-07-14-room-correctness-refactor.md`

Current architecture: `docs/product/CURRENT_SYSTEM.md`

## Review Request

Please review this range as a correctness and maintainability refactor, not as a
new autonomous-conversation feature release. In particular, inspect:

1. transaction and retry invariants around room commands and provider turns;
2. attention lease, cursor, and startup reconciliation correctness;
3. SQLite/PostgreSQL repository parity and connection ownership;
4. whether the GUI extraction boundaries improve ownership without merely
   moving dead code into more files;
5. whether the remaining compatibility wrappers and seven deletion candidates
   are justified;
6. the explicitly unclosed real-provider UI smoke gap described below.

## Executive Summary

This range contains 60 deliberately small commits. It changes 213 files with
26,736 insertions and 7,616 deletions. The size comes from adding repository
contracts, failure-injection coverage, migration tooling, focused legacy
services, and behavioral tests while removing 5,617 lines from the former GUI
monolith (`agentsassemble/gui.py`: 9,516 to 3,899 lines).

Line reduction was not the acceptance criterion. The intended result was:

- one canonical room/repository/WebSocket authority remains intact;
- Agent Session name and avatar changes become the current display authority
  for old and new chat messages;
- visible room mutations and idempotency ACKs cannot commit independently;
- provider finalization cannot diverge from turn, lease, cursor, or session
  state;
- autonomous attention state survives retries and server restarts without
  orphan work;
- room-global settings and user-owned preferences have separate durable
  authorities;
- PostgreSQL uses a bounded pool and strict backend contracts;
- `gui.py` becomes substantially closer to a composition root while public
  compatibility behavior remains stable.

No provider-specific browser socket, parallel room event store, polling live
UI, one-shot provider path, `claude -p`, or `codex exec resume --last` was
introduced.

## Starting Problems

The plan started from five confirmed classes of risk.

### 1. Stale Agent Session identity in chat

An agent could be renamed and given a new avatar in the settings panel while
already-rendered and later-paged chat messages continued to show an event-time
or local legacy identity. The correct authority is the current canonical
participant identified by stable `participant_id`; event-time author data is
only a fallback for imported, deleted, or unavailable participants.

### 2. Room command crash windows

Several commands performed a domain write, visible event append, state change,
and command-result write as separate steps. A failure between those steps could
leave a visible action without its idempotency record, or a retry could repeat
part of the action.

### 3. Provider-turn state could diverge

A provider final response affected the visible message, turn completion,
attention lease, provider-sync cursor, Agent Session state, inflight input, and
ACK. These fields needed one transaction and publication only after commit.

### 4. Attention and settings had split ownership

Attention jobs, leases, observation checkpoints, and provider-sync cursor
copies could disagree after a crash. Room-global settings and user preferences
also had legacy JSON and database paths that could be mistaken for concurrent
authorities.

### 5. `gui.py` owned too many unrelated concerns

The starting `gui.py` had 9,516 lines and combined dependency lifetime, HTTP
transport, current room routes, legacy meeting policy, resident diagnostics,
provider smoke orchestration, process controls, and audit projection. Direct
route extraction without classification would have risked preserving dead code
as new architecture.

## Plan And Actual Implementation

### Phase 0: Identity repair and ambient freeze

Implemented:

- `336fb13 Keep Agent Session identity current in chat`
- `92e2f54 Label ambient discussion as experimental`
- `6608c96 Make shadow attention recording opt-in`
- `16e592d Restrict ambient room wake events`
- later corrective slice `5e6374e Keep canonical agent avatars current`

The frontend now resolves old and new messages, roster entries, detail views,
and typing labels from the current canonical participant. An explicitly empty
canonical avatar clears event-time and local-profile fallbacks. Profile updates
commit canonical participant and Agent Session identity together with one
`participant_updated` event and ACK.

Ambient conversation remained opt-in and experimental. Shadow attention
recording defaults to `off`, supports deterministic sampling, and no longer
writes diagnostic work for ordinary operation. Empty, lifecycle, system, vote,
and unsupported media-only events do not wake a provider.

### Phase 1: Atomic room commands and provider finalization

Implemented:

- failure-injection repository contracts;
- backend-neutral `RoomCommandUnitOfWork`;
- atomic `message.send`;
- atomic profile, mute, and durable leave commands;
- atomic provider finalization;
- durable and retry-safe start/stop, kick, and room-delete workflows.

The unit of work owns prior-command lookup, canonical payload hashing, domain
mutation, ACK construction, and command-result recording. It does not expose
backend connections or SQL. Event publication and provider routing occur only
after commit.

External process effects deliberately do not run inside a database transaction.
Instead, narrowly scoped durable intents record ownership and progress. A retry
can reuse the server-owned handle or observe an already-applied stop rather
than starting or stopping a second process. External reported PIDs remain
diagnostic values and are not local kill authority.

Room deletion uses tombstone-scoped idempotency because deleting a room also
deletes ordinary room command records. Cleanup is resumable and only the same
principal, request ID, and payload can resume or deduplicate the delete.

### Phase 2: Attention, observation, and provider cursor durability

Implemented:

- atomic attention selection plus pending session input;
- expiry-aware lease reclamation;
- bounded startup reconciliation for orphan jobs, leases, and session refs;
- monotonic, coalesced `room.observed` checkpoints;
- one canonical provider-sync cursor authority with audited compatibility
  reconciliation.

The former 0.25-second receive loop was removed. The bridge uses a one-second
blocking socket deadline only to flush a local observation batch; it does not
poll the room and does not invoke a provider.

Full-suite testing exposed an unplanned remote-stop deadlock: a bridge tried to
flush `room.observed` while implicit room creation waited on the lifecycle lock
held by the same kick operation. The fix made the checkpoint rely on repository
atomicity and generation validation without implicit room creation. The product
fallback was not restored.

### Phase 3: Settings ownership and explicit migration

Implemented:

- strict canonical room-global settings in SQLite and PostgreSQL;
- separate user-owned notification and read preferences in `identity.db`;
- explicit dry-run, fingerprint, backup, apply, and replay-protection commands
  for legacy global settings and user preferences;
- rejection of mixed global/user writes.

No live user data was migrated automatically. Dry runs against copies exposed
invalid legacy modes and orphan rooms; those conditions block apply rather than
being silently replaced with defaults. Tests that had relied on invite or
channel operations implicitly creating a missing room were corrected to create
the canonical room explicitly.

### Phase 4: PostgreSQL operation

Implemented:

- one repository-owned bounded `psycopg_pool`;
- 1-8 connections, bounded queueing, startup and acquisition deadlines;
- one checked-out connection reused by all reads in a command transaction;
- idempotent shutdown and partial-start cleanup;
- redacted numeric pool diagnostics;
- a strict PostgreSQL contract runner and PostgreSQL 16 GitHub Actions service.

The strict contract runner passed 54 selected tests with zero skips against an
isolated local PostgreSQL 17 instance. The normal portable suite still contains
environment-dependent skips by design. The first GitHub Actions run for the
pushed head was still in progress when this report was written, so hosted CI is
not claimed here as completed evidence.

### Phase 5: GUI composition and legacy classification

Implemented in many small commits rather than four large moves:

- exact route and service inventory in `docs/product/GUI_COMPOSITION.md`;
- typed `GuiApplicationServices` lifetime ownership;
- current/optional/compatibility/deletion-candidate classification;
- Router-owned retained legacy reads, diagnostics, discovery, preflight,
  smoke, controls, meeting lifecycle, turn/round policy, presence, speech,
  official replies, provider health, and Codex compatibility;
- focused service and HTTP behavior tests for each moved family;
- AST-based route ownership firewall.

The extraction kept code together by reason to change. For example, ordinary
resident speech and official-record replies remain separate because the latter
mutates meeting artifacts and shared memory. Provider login is separate from
legacy agent creation. Session deletion is separate from pause/resume because
it owns process-group, config, binding, and roster cleanup.

Late-bound compatibility wrappers remain in `gui.py` only where existing CLI,
tests, or integrations patch the historical callable. They preserve the
external seam while orchestration lives in a focused service.

Seven exact handler routes remain as deletion candidates:

GET:

- `/api/live-agent-create/options`
- `/api/provider-sessions`
- `/api/codex-sessions`

POST:

- `/api/demo`
- `/api/live-agent-create/check`
- `/api/live-agent-create`
- `/api/live-agent-room/expel`

They were neither extracted nor deleted because the inventory found no current
production caller but did not establish sufficient compatibility evidence for
a breaking removal. The AST firewall prevents new API ownership from drifting
back into `gui.py` while this decision remains open.

## Where Implementation Differed From The Plan

### 1. Identity work required a second corrective commit

The first identity commit fixed current-name projection and immediate ACK
application. A later real screenshot still showed old `Antigravity CLI`
messages. Investigation found two distinct facts:

- that screenshot came from an old browser tab running a pre-fix hashed bundle;
- avatar fallback precedence was still wrong when the canonical avatar was
  explicitly empty.

The second commit fixed avatar, roster/detail, and typing precedence and added
the required real file-upload/crop/reload browser path. This was intentionally
not solved by rewriting every historical event.

### 2. Durable process effects were split by action

The plan named one Phase 1.6 item. The implementation used separate start/stop,
kick, and delete commits instead of a generic saga framework. Each action has a
different ownership, cleanup, retry, and tombstone rule; combining them would
have made the failure states less reviewable.

### 3. Phase 2 included an unplanned deadlock fix

The `room.observed` checkpoint work revealed a real lock cycle during invited
external-agent kick. The fix was necessary to preserve the new checkpoint
contract and was covered by an external CLI kick E2E plus a held-lock regression
test. It did not add a fallback or restore implicit room creation.

### 4. Phase 3 corrected stale implicit-room fixtures

Once repository room existence became authoritative, 17 legacy test fixtures
failed because they depended on invite/channel operations creating rooms as a
side effect. The product fallback was deliberately not restored. The fixtures
now express the required room creation explicitly.

### 5. Phase 5 became much more granular than planned

The plan described inventory, service ownership, retained legacy extraction,
and thin composition as four commits. Actual extraction used 35 small commits
after the inventory because route families had distinct side effects and test
boundaries. This increased commit count but made every move independently
revertible and behaviorally verifiable.

### 6. `gui.py` is not literally a pure composition-only file yet

It is substantially reduced and guarded, but still contains transport/static
handling, compatibility wrappers, and the seven deletion-candidate routes. The
implementation stopped instead of moving unproven dead paths into polished new
modules merely to satisfy a line-count or file-shape goal. A separate
compatibility decision is required before deleting them.

### 7. The post-refactor real-provider smoke required by the plan is missing

The plan requested two real turns each with Codex and Claude Haiku after
provider finalization/lifecycle changes. That exact verification was not run
on this 60-commit head. The final Phase 5 note correctly states that the GUI
composition slices themselves did not change provider runtime behavior, but
that does not retroactively verify the earlier Phase 1 finalization and
lifecycle changes.

There are successful real-provider results from 2026-07-11 for Codex Luna,
Antigravity Flash, and OpenCode GLM-5.2, including a three-minute room run and
invite smoke. Those results predate this implementation range and are baseline
evidence only. They must not be presented as proof of the current head.

## Frontend And Smoke Evidence

### Real frontend controls: performed

The following used the actual product frontend rather than backend-only command
injection:

- a production frontend running on local port 8765 changed all visible old
  `Makima` messages to `Makima UI 확인` immediately and then restored `Makima`;
- Playwright selected a real image file through the profile input, used the
  crop/apply control, saved name and avatar through Agent Session settings,
  verified an already-rendered reply and roster, reloaded, and verified the old
  reply again;
- the canonical-room Playwright scenario used the production build and real
  fixture server to verify desktop streaming and mobile control of the same
  persistent fixture session.

These checks prove the browser flow and canonical room integration. They do not
prove a paid/native provider process on the current head.

### Actual provider CLI through the frontend: not performed on this head

No post-refactor browser session started current Codex and Claude Haiku
providers and completed measured turns through the real provider adapters.
Therefore this report does not claim:

- current-head provider TTFO or total-turn latency;
- same PID/provider session across current-head frontend turns;
- current-head model observation for Codex or Claude Haiku;
- current-head cleanup after a real provider run.

Recommended release-gate smoke:

1. use the real frontend to start one Codex session and one Claude Haiku session;
2. send two short turns to each through the canonical room UI;
3. record requested and observed model, TTFO, total time, errors, and stable
   provider session/PID evidence where that provider exposes it safely;
4. pause/resume once through the UI and verify bounded backlog delivery;
5. stop through the UI and verify no server-owned process remains;
6. inspect visible output for TUI residue, duplicate messages, stale identity,
   secret/path leakage, and fallback substitution.

This should be run only with explicit provider approval and must continue to
forbid `claude -p` and `codex exec resume --last`.

## Automated Verification

Final local checks on `50ec92f7`:

```text
python3 -m unittest discover -s tests -t .
  3,359 passed; 39 environment-dependent skips; no failures

npm --prefix frontend test -- --run
  20 files; 106 tests passed

npm --prefix frontend run build
  passed

npm --prefix frontend run test:e2e
  canonical-room.spec.ts passed against the real fixture server

python3 -m compileall -q agentsassemble tests
  passed

git diff --check
  passed
```

Visible non-blocking signals:

- existing SQLite tests emit some `ResourceWarning: unclosed database` lines;
- Vite reports a JavaScript chunk around 703 kB, above its 500 kB warning
  threshold;
- the portable suite's 39 skips are environment-dependent and are not treated
  as PostgreSQL proof;

### Hosted CI follow-up

GitHub Actions run
[`29381719149`](https://github.com/kwd421/AgentsAssemble/actions/runs/29381719149)
for `aec29ca2` completed with the main Python 3.11/3.13 suites, PostgreSQL
contracts, frontend build, frontend unit/E2E tests all passing. Both
`runtime-platforms` jobs failed for an obsolete workflow reference to the
already-removed `tests.test_room_prune` module.

The Ubuntu platform job also exposed a deterministic test-fixture error in
`test_expired_catalog_is_visible_but_not_startable_during_refresh`. The test
assigned `catalog._cached_at = 0.0` and assumed that represented an expired
monotonic timestamp. On a newly booted GitHub runner with uptime below the
five-minute catalog TTL, zero was still within the valid interval, so no
background refresh started. The slower full-suite job crossed that uptime
threshold before executing the same test and passed by accident; Windows and
local developer machines also had longer uptime.

The follow-up removes the deleted module from the targeted workflow and sets
the fixture timestamp relative to the current monotonic clock, one TTL plus one
second in the past. No provider catalog production behavior or fallback was
changed. Local verification after the fix:

```text
python3 -m unittest tests.test_provider_runtime_controls
  24 passed

expired-catalog regression repeated five times
  5/5 passed

python3 -m unittest tests.test_provider_runtime_controls tests.test_native_cli_providers
  38 passed

git diff --check
  passed
```

Follow-up run
[`29382941178`](https://github.com/kwd421/AgentsAssemble/actions/runs/29382941178)
for `fa091535` completed successfully. Ubuntu and Windows runtime-platform jobs,
Python 3.11 and 3.13 full suites, PostgreSQL contracts, frontend build, and
frontend unit/E2E jobs all passed. The only hosted annotations were GitHub's
non-blocking Node.js 20 action-runtime deprecation notices for current
`actions/checkout@v4` and setup action versions.

## Intentionally Unchanged Or Out Of Scope

- No semantic provider silence or model-decided refusal protocol was added.
- No reaction, handoff, defer, token budget, scheduled wakeup, media-attention,
  or model-based speaker selection was added.
- `ambient` remains bounded, server-selected, opt-in experimental relay rather
  than a claim of human-like autonomous room watching.
- `RoomTurnCoordinator` was not split solely because it is long; its turn,
  inflight, cursor, and lease invariants still share one reason to change.
- No hosted identity/invite/media authority migration was started.
- No LISTEN/NOTIFY, Redis, Kafka, or multi-instance worker architecture was
  introduced.
- No live legacy settings or user-preference migration was applied.
- No deletion-candidate route was removed without compatibility evidence.

## Remaining Risks And Decisions

1. Run the missing current-head real-provider frontend smoke before treating
   this range as provider-runtime release evidence.
2. Wait for and review the new GitHub Actions result, especially the strict
   PostgreSQL 16 service job.
3. Decide whether the seven legacy routes can be deleted, retained behind an
   explicit compatibility flag, or need a migration path.
4. Address SQLite connection `ResourceWarning` output so real leaks cannot hide
   among accepted test noise.
5. Split or lazy-load the large frontend chunk if startup performance becomes a
   measured product issue.
6. Keep autonomous conversation feature work frozen until silence, media
   observation, wake policy, and cost boundaries receive a separate product
   decision.

## Commit Map

### Identity and attention freeze

`51a955a` through `16e592d`

### Transaction and lifecycle correctness

`14f7d32` through `00868a4`

### Attention and cursor durability

`0a99d04` through `7072259`

### Settings and PostgreSQL authority

`3f5d88c` through `9e6e9cc`

### GUI composition and compatibility extraction

`59c7db1` through `50ec92f`

The detailed per-commit rationale and verification history is retained in the
plan's progress log rather than duplicated in this review brief.
