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
- 2026-07-15: Milestone 0.3 implemented. Session issuance now calls one atomic
  participant-session replacement operation. Memory and JSON repositories
  replace under their persistence lock, existing duplicate local sessions fail
  closed, and PostgreSQL revision `0007_unique_room_access_session` plus a
  conflict upsert enforce one active token per room participant under races.
- 2026-07-15: Milestone 0.3 follow-up removed the legacy pre-revocation from
  invite rejoin and operator pairing. A failed replacement now rolls back and
  leaves the previously valid bearer token usable instead of disconnecting the
  participant before the new token is durable.
- 2026-07-15: Milestone 0.4 implemented. Reusable invite consumption now
  returns `invite_not_found` when its durable policy row is absent in memory,
  JSON, and PostgreSQL implementations. The code no longer invents an
  unlimited use count after losing the authority needed to enforce it.
- 2026-07-15: Milestone 0.5 implemented. Operator pairing redemption now
  requires and trusts only the normalized HTTP `Origin` header; body origin is
  ignored. Default ports and IPv6 normalize consistently. React bootstrap HTML
  sends and declares `Referrer-Policy: no-referrer`, while the existing
  one-time URL consumer removes the token from browser history immediately.
- 2026-07-15: Milestone 0.6 implemented. Pairing UI state now distinguishes
  active redemption, retryable transport/server failure, terminal token or
  authorization failure, and successful pairing. Retryable failures retain the
  in-memory token behind an explicit retry button; terminal failures discard it
  and direct the user to obtain a new link instead of remaining in a false
  pending state.
- 2026-07-15: Milestone 1.1 implemented. `InviteApplicationService` now owns
  invite creation, side-effect-free inspection, pending summaries, revocation,
  and repository signing-secret access for one injected repository. The old
  module functions remain compatibility entry points backed by the same
  service, while direct service instances are isolated and testable without
  changing process-global state.
- 2026-07-15: Milestone 1.2 implemented. The GUI server now composes one invite
  service, room-session service, admission preflight, and mutating
  `RoomAdmissionCoordinator`. The current join route validates the room before
  consuming admission authority, then resolves identity, issues one session,
  and commits canonical participant and membership state through that owner.
  The legacy join facade remains available only to compatibility callers.
- 2026-07-15: Milestone 1.3 implemented. Browser joins now carry a secure UUID
  that survives a retry in session storage. The repository persists a bounded
  fingerprint-only workflow, atomically couples invite consumption to its
  consumed phase, reconstructs the same HMAC-derived bounded session bearer,
  and resumes idempotent participant/membership writes after failure or
  restart. Same-request payload changes return `idempotency_conflict`; raw
  invite, device, and session credentials are rejected by the workflow storage
  allowlist. Failure injection, restart, concurrency, route compatibility, and
  frontend retry coverage passed. The refactor also restored the pre-existing
  `410 Gone` response for an invite targeting a deleted room; this was a
  Milestone 1.2 compatibility regression, not a conversation-policy change.
- 2026-07-15: Milestone 1.4 implemented. Operator pairing redemption now
  persists its consuming credential fingerprint, claiming/retryable/completed
  phase, and completed session fingerprint. The bound device resumes after a
  participant write failure, process/service reconstruction, or a lost
  completion response and receives the same still-active bearer; another
  device remains rejected. Local SQLite/JSON failure injection, HTTP behavior,
  identity contracts, and schema checks passed. PostgreSQL integration cases
  remained environment-skipped because no test DSN was configured, so real
  hosted concurrency evidence remains part of Milestone 5.
- 2026-07-15: Milestone 1.5 implemented. GUI composition now owns one operator
  pairing service beside invite, session, and admission services. Current
  invite/admission/pairing routes resolve creator and operator identity through
  `ctx.deps.identities` and use `ctx.deps.pairing`; they no longer construct a
  request-local pairing service or import room-user global facade functions.
  The generic `RequestContext` host/session compatibility facade remains for
  legacy routes and is explicitly deferred to Milestone 3 rather than mixed
  into this current-route change.
- 2026-07-15: Milestone 2.1 introduced `PostgresApplicationDatabase`. It checks
  the activated schema head before opening one bounded pool, owns redacted
  readiness/diagnostics and idempotent shutdown, and exposes a context-local
  transaction connection that nested repository calls can share. This commit
  deliberately does not wire repositories yet; ownership transfer and removal
  of repository-level pool closes are Milestone 2.2. Fake-pool lifecycle,
  transaction reuse, schema-failure, secret-redaction, and close tests passed.
- 2026-07-15: Milestone 2.2 injected the same application database into the
  room, identity, and invite/session PostgreSQL repositories. Borrowing
  repositories no longer close the shared pool; standalone repository
  construction still owns and closes only its private test/migration pool. GUI
  startup now creates one verified database owner and shutdown closes it once,
  after all repository adapters. Factory, ownership, startup-failure, GUI
  lifecycle, and optional-driver tests passed. Real PostgreSQL transaction
  integration remains gated on `AGENTSASSEMBLE_TEST_POSTGRES_DSN` and is part of
  Milestones 2.3 and 5; no frozen conversation or media policy changed.
- 2026-07-15: Milestone 2.3 added an application transaction boundary to hosted
  admission and pairing. Admission keeps its durable request workflow, then
  commits identity resolution, invite consumption, bearer replacement,
  participant, membership, and completion on one PostgreSQL connection.
  Pairing deliberately keeps the prior same-device claim as its resumable
  security lease, then atomically commits participant, membership, bearer, and
  completion. Retryable failure state is written only after rollback. A local
  temporary PostgreSQL 17 instance ran all 79 strict PostgreSQL contracts with
  zero skips, including injected failures proving rollback and same-device
  recovery. Local JSON/SQLite behavior remained the existing saga and frozen
  conversation/media policy was not changed.
- 2026-07-15: Milestone 2.4 made local admission compensation explicit. If a
  room disappears after a workflow has issued a bearer or committed identity
  membership, the workflow now records `compensating`, revokes only its
  deterministic bearer, removes only its matching identity membership, and
  persists each completed step. Invite consumption remains retained for replay
  safety. A failed side effect or final compensation record remains
  `failed_retryable` and resumes after process/repository reconstruction; a
  completed cleanup becomes terminal `room_unavailable`. Memory and real JSON
  restart failure injection passed, the adjacent local admission/invite/session
  suite passed 156 tests, and a temporary UTF-8 PostgreSQL 17 instance passed
  all 80 strict contracts with zero skips. No frozen conversation, scheduling,
  or media policy changed.
- 2026-07-15: Milestone 3.1 began by moving repository-injected invite policy,
  validation, workflow persistence, and safe usage-guide projection into
  `room_invite_application.py`. Admission, compensation, and GUI application
  composition now import that owned service directly; request session
  verification uses the injected `RoomSessionService` instead of silently
  consulting facade state. The old `room_invite.py` API remains as an explicit
  compatibility facade, and host/public runtime configuration plus remaining
  legacy session callers are intentionally still outstanding. An AST boundary
  test prevents the already-extracted current modules from importing the
  global facade again. This is a behavior-preserving first slice, not a claim
  that all compatibility ownership has been removed.
- 2026-07-15: Milestone 3.1 session-authority follow-up removed current roster,
  flow-status, WebSocket validation, moderation, Agent Session lifecycle, and
  room deletion calls to the process-global session facade. The canonical
  realtime controller now requires the application-owned invite and session
  services, and room deletion revokes both authorities through those exact
  instances. Test/smoke controller overrides now explicitly share the same
  invite repository with their HTTP application, preventing false kick/revoke
  evidence from two unrelated in-memory stores. Legacy frontend-created agent
  admin and route re-export modules retain compatibility calls for now; public
  host/tunnel configuration ownership remains the next Milestone 3.1 slice.
- 2026-07-15: Milestone 3.1 public-runtime follow-up introduced one
  server-scoped `PublicInviteRuntime` for host-token comparison, generated
  credentials, validated public URLs, request trust, CORS, and tunnel URL
  rotation. `GuiDeps`, public invite routes, and `PublicTunnelManager` now use
  that injected instance; separate server/runtime instances no longer share
  mutable host or URL state. The module-level `room_invite` functions delegate
  to a compatibility runtime for retained callers and `_make_handler`'s direct
  compatibility/test construction only. Normal `serve_gui` composition owns a
  fresh runtime. `gui.py` still installs the invite repository facade so
  retained legacy routes use the same durable authority; moving that bundle is
  Milestone 3.3 rather than hiding it in this slice.
- 2026-07-15: Milestone 3.2 moved current room routes, admission, pairing, and
  request identity checks onto the injected `IdentityBackend`. Stateless
  device-credential and participant-type normalization now belongs to the
  identity domain rather than the mutable `room_users` facade. The server
  binds its lobby writes and frontend-room registration to the same identity
  backend, while `room_users.py` remains available only to verified legacy and
  compatibility callers. An AST boundary test prevents current modules from
  importing that registry again, and an isolation test proves `/api/rooms`
  ignores a separately configured compatibility store. The related GUI, WS,
  realtime, and native-CLI suites passed 578 tests. Conversation scheduling,
  autonomous participation, and media policy remained unchanged.
- 2026-07-15: Milestone 3.3 moved retained meeting, lobby, resident-agent,
  smoke, process, and session-run route composition into one owned
  `LegacyGuiApplication`. `gui.py` now supplies explicit compatibility hooks
  and registers the bundle at the prior route-order boundaries instead of
  constructing each legacy service itself. An AST ownership test prevents the
  main GUI module from importing the individual legacy HTTP route modules
  again. Initial integrated verification exposed a refactor regression where
  the local Agent Session turn adapter was captured at handler construction
  instead of resolved when the lobby event arrived; that bypassed the verified
  patch seam and left a background turn running after its test workspace was
  removed. The bundle now resolves that adapter at delivery time, restoring
  the prior behavior without adding a fallback. Focused route tests passed 64
  cases, patch/session/process tests passed 162 cases, and the full GUI server
  suite passed 433 cases. Frozen conversation, autonomous-attention, sequence,
  and media behavior was not changed.
- 2026-07-15: Milestone 3.4 made the seven retired-route compatibility decision
  explicit. All seven `410 legacy_route_retired` tombstones remain through the
  first tagged `v0.1.x` release; because the repository currently has no tags,
  that window has not elapsed. They may be removed in `v0.2` or later only
  after a fresh supported-caller audit, and no implementation or new caller may
  be added behind them during the retention period. This is documentation of
  the existing fail-visible contract, not a behavior or conversation-policy
  change.
- 2026-07-15: Milestone 4.1 replaced the invite admission hook's independent
  resolved, busy, requested, expired, session, and pairing flags with one
  discriminated reducer state: `idle`, `preflighting`, `profile_required`,
  `joining`, `joined`, `pairing`, `failed`, or `expired`. Existing component
  selectors remain derived compatibility projections, while failures now carry
  their operation and retryability in the authoritative state. Automatic known
  user admission, remembered-profile prefill, existing-session preservation,
  pairing retry, stable join request IDs, and lost-response session recovery
  retain their prior behavior and copy. The focused 10 admission tests, all 116
  frontend tests, and the production TypeScript/Vite build passed. No room
  conversation, sequence, autonomous-attention, or media behavior changed.
