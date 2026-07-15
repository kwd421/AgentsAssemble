# Admission Correctness And Remaining Composition Refactor

Status: in progress

Started: 2026-07-15

Branch: `codex/risuai-character-personas`

Starting commit: `b2cccf11`

External review source: `/Users/seinel/Downloads/review.md`

## Goal

Finish the remaining identity, invite, admission, pairing, persistence, and
composition correctness work without redesigning room conversation policy.

The immediate product risk is not file size. It is a partially applied room
admission: an invite can be consumed, a prior session revoked, or a pairing
claimed without the corresponding participant, membership, and replacement
session reaching one retry-safe outcome.

## Sources Of Truth

Read in this order when resuming:

1. `docs/product/CURRENT_SYSTEM.md`
2. this plan
3. `docs/product/OPERATING_MODEL.md` for security and authority boundaries
4. `docs/product/ROOM_REPOSITORY.md` for transaction contracts
5. the closest implementation and behavioral tests

The external review is evidence and prioritization input. Current product
documents and verified behavior remain authoritative.

## Frozen Product Areas

This refactor does not decide or redesign:

- autonomous conversation policy;
- semantic silence, reaction, handoff, or defer behavior;
- scheduled wakeups, token budgets, pair cooldowns, or speaker selection;
- sequence-mode policy;
- media understanding or provider-native image/PDF/audio delivery;
- LISTEN/NOTIFY, Redis, Kafka, WebRTC, or voice.

Pre-existing non-critical defects in those areas are reproduced and reported,
not opportunistically changed. The exception is a defect that causes a security
boundary failure, data corruption/loss, server outage, deadlock, uncontrolled
resource leak, or a regression introduced by this refactor. Such a defect must
be fixed in a separate corrective commit or the causing change must be reverted.

## Invariants

- Current room, invite, identity, session, and pairing paths fail closed.
- A successful response never hides a failed durability write.
- An existing corrupt security store never becomes a new empty authority.
- One participant has at most one active room access session under the current
  product contract.
- Invite and pairing retries do not duplicate consumption, participants,
  memberships, or sessions.
- Browser pairing requires the actual normalized HTTP `Origin`.
- Current routes use application-owned injected services and repositories;
  module globals remain compatibility-only.
- PostgreSQL hosted mode has one application-owned connection pool and can use
  one connection for cross-authority work.
- Local JSON plus SQLite admission uses an explicit durable workflow rather
  than pretending to have a cross-store database transaction.
- No provider fallback, one-shot CLI path, `claude -p`, or
  `codex exec resume --last` is introduced.
- User-owned untracked files are not staged or modified.

## Commit Sequence

### Milestone 0: Close confirmed correctness and security gaps

1. Fail closed on corrupt or unwritable local invite/session persistence and
   roll in-memory mutation back when persistence fails.
2. Replace the implicit global memory repository with an unconfigured
   fail-closed facade; memory mode must be explicit.
3. Add atomic participant-session replacement and enforce the one-session
   invariant in SQLite-compatible and PostgreSQL repositories.
4. Reject reusable invite consumption when its durable invite row is absent.
5. Require the browser `Origin` header for operator pairing and remove URL
   credentials before external loads/referrers.
6. Make pairing frontend failure states explicit and recoverable or terminal,
   never permanently locked without a next action.

### Milestone 1: Give admission one workflow owner

1. Introduce `InviteApplicationService` for current invite operations.
2. Separate side-effect-free preflight from a mutating
   `RoomAdmissionCoordinator`.
3. Persist an idempotent admission request and resume incomplete phases.
4. Make pairing redemption resumable for the same device credential.
5. Remove current-route calls to `room_invite.py` globals.

### Milestone 2: Share PostgreSQL application ownership

1. Introduce one `PostgresApplicationDatabase` owning the bounded pool,
   revision check, health, transactions, and shutdown.
2. Inject its connection provider into room, identity, and invite/session
   repositories.
3. Execute hosted admission and pairing mutations in a cross-authority unit of
   work where they share one database.
4. Provide equivalent retry semantics in local mode with a durable admission
   workflow and explicit compensation.

### Milestone 3: Limit compatibility ownership

1. Forbid current code from importing the invite global facade.
2. Restrict the global room-user registry to compatibility callers.
3. Move remaining legacy application composition out of `gui.py` as one owned
   bundle, preserving patch-compatible seams only where verified callers need
   them.
4. Keep or remove the existing `410` tombstones according to an explicit
   compatibility-release decision.

### Milestone 4: Clarify frontend state ownership

1. Replace interacting admission booleans with a discriminated reducer state.
2. Extract legacy meeting surfaces from `App.tsx` without moving canonical room
   selection or WebSocket ownership.
3. Represent room settings as loading, ready, saving, stale/error states and
   stop guessing routing defaults after a failed read.
4. Preserve the successful current lazy-load boundary.

### Milestone 5: Release evidence

1. Failure-injection tests at every admission and pairing phase boundary.
2. Concurrent multi-instance PostgreSQL admission and pairing contracts.
3. Browser identity E2E for same-origin, cross-origin, replay, wrong-origin,
   incognito, failure recovery, and stable participant identity.
4. Final frontend-driven Codex and interactive Claude smoke with two turns,
   pause/backlog/resume, continuity, cleanup, and zero duplicate final or
   secret/path leakage.
5. Full Python, frontend unit/build/E2E, PostgreSQL contracts, strict resource
   warnings, `git diff --check`, and remote CI.

## Working And Reporting Rules

- Use small commits with one reason to exist.
- Separate behavior fixes from structural moves.
- Run targeted tests after each slice and broader gates after shared changes.
- Do not weaken or delete a meaningful test to make a refactor pass.
- When an excluded-area defect appears, record reproduction, whether it
  predates the current slice, severity, operational impact, and why it was
  fixed or deferred.
- Do not claim provider or browser behavior without running that surface.
- Update this plan's progress log and the completion report as work proceeds.

## Progress Log

- 2026-07-15: Plan recorded from the post-`b2cccf11` external review. Milestone
  0 begins with local JSON persistence fail-closed behavior.
- 2026-07-15: Milestone 0.1 implemented. Existing unreadable, malformed,
  wrong-schema, and structurally invalid state now aborts repository startup.
  Failed write/chmod/replace mutations restore the prior in-memory state and
  raise a typed durability error. Repository, invite, admission, HTTP, session,
  and GUI composition verification passed 111 tests; a copy of the current
  local store loaded with all 88 invite records under the stricter validator.
- 2026-07-15: Milestone 0.2 implemented. The compatibility facade now starts
  with a fail-closed unconfigured repository rather than silently accepting
  process-local invite and session state. Memory storage remains available
  only through explicit ephemeral/test configuration; normal GUI composition
  continues to install its JSON or PostgreSQL repository before serving.
