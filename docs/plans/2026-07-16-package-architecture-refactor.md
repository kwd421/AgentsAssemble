# Package Architecture And Correctness Residue Refactor

Status: active

Started: 2026-07-16

Branch: `codex/risuai-character-personas`

Starting commit: `29a7e0fa`

External review:
`agentsassemble_package_refactor_review.md`, Google Drive file
`1eqyqTcvEkTQuy3ycFZAnGyNZsa-8bRD8`

## Goal

Finish the remaining admission/application-lifecycle correctness work, then
replace the flat `agentsassemble/` module search space with a small number of
predictable domain packages without changing product behavior.

The success criterion is not a cosmetically low file count. A future human or
AI maintainer should be able to infer the owner, allowed dependencies, primary
tests, and compatibility status of a module from its import path and the
package map.

## Sources Of Truth

Read in this order when resuming:

1. `docs/product/CURRENT_SYSTEM.md`
2. this plan
3. `docs/product/GUI_COMPOSITION.md` when changing GUI ownership or lifecycle
4. `docs/product/ROOM_REPOSITORY.md` when changing persistence or transactions
5. `docs/product/OPERATING_MODEL.md` when a security or context boundary moves
6. the closest implementation and behavioral tests

The external review is evidence and a proposed target map. It is not authority
over verified product behavior or module ownership.

## Verified Starting State

- The package currently has 302 top-level Python modules, plus the existing
  `adapters`, `bridges`, and `migrations` subpackages.
- Current `/api/room-invite/join` passes an empty `request_id` to
  `RoomAdmissionCoordinator`; the coordinator generates a random
  `legacy-<hex>` identity. A lost-response retry can therefore create a
  different workflow.
- The maintained browser and native WebSocket client already generate UUID
  request IDs. The current route is the missing fail-closed boundary.
- Current invite join and leave call the legacy `connect_live_agent()`
  projection directly and silently ignore `ValueError`.
- `GuiApplicationServices.start()` records `start_failed` but does not roll
  back a process monitor, session monitor, or tunnel already started by the
  same call.
- Admission workflows have create/read/update operations but no bounded purge
  or room-scoped removal contract.

## Decisions That Deliberately Differ From The Review

### Retention is not automatic yet

The review proposed startup bounded maintenance. No product retention duration
exists yet, and deleting `failed_retryable` state can remove the evidence
needed to resume or compensate a partial admission. This refactor will first
add:

- typed purge criteria and report;
- repository parity for memory/JSON/PostgreSQL;
- room-delete cleanup for terminal or room-owned records;
- an explicit maintenance command with dry-run by default;
- bounded diagnostics.

No startup deletion will be enabled. Automatic retention requires a later,
explicit duration and recovery-policy decision.

### The proposed directory tree is provisional

`PACKAGE_MAP.md` and an AST ownership inventory will be created before module
moves. Filename prefixes do not decide ownership. A module moves only after its
current callers, side effects, public import surface, primary tests, and target
dependency direction are known.

The review's “20 top-level modules” target is a useful aspiration, not a reason
to create wildcard re-export shims or move unstable modules into arbitrary
folders. The hard gates are predictable ownership, no forbidden dependency,
no new top-level product modules, no new cycle, and explicit compatibility
shims.

### Frozen conversation modules are not churned for appearance

Stable Room contracts, repository types, transaction ownership, and realtime
transport may move once their inventory is proven. Modules whose primary
reason to change is unsettled attention, routing, speaker selection, semantic
silence, sequence behavior, or media delivery remain in place unless a move is
strictly mechanical and clearly reduces an existing dependency cycle. No
behavior or policy decision is made by this refactor.

### Tests follow ownership, not one mass hierarchy rewrite

Tests may move after their production owner moves, in a separate commit when
useful. The refactor will not relocate every source and test file in one wave,
nor preserve low-value source-string assertions merely to prove a pathname.

## Frozen Product Areas

Do not redesign or “fix” these areas during this plan unless direct evidence
shows a security boundary failure, data loss/corruption, server outage,
deadlock, uncontrolled resource leak, or a regression introduced by this
refactor:

- autonomous conversation and speaker selection;
- semantic silence, reaction, handoff, defer, scheduled wakeup;
- token budget, pair cooldown, sequence mode;
- the known old `@opencode` backlog candidate defect;
- media understanding and provider-native image/PDF/audio delivery;
- LISTEN/NOTIFY, Redis, Kafka, WebRTC, or voice.

Non-critical findings in these areas are reproduced, classified, and reported,
not patched with filters, fallbacks, or unapproved policy.

## Non-Negotiable Compatibility

- Keep `agentsassemble.cli:main` and `agentsassemble.gui:serve_gui` stable.
- Keep one canonical `RoomRepository` and `/ws?ticket=...` transport.
- Do not change SQL schema, transaction semantics, provider commands, model
  aliases, provider lifecycle, output parsers, or frontend UX during package
  moves.
- Do not add a one-shot provider or model fallback.
- Do not use `claude -p` or `codex exec resume --last`.
- Keep secret, path, PID, token, and provider-private data boundaries intact.
- Preserve current error codes and payloads except for the explicitly approved
  current-join request ID validation.
- Do not touch `.superpowers/` or `docs/plan-room-hygiene-bugfixes.md`; they are
  untracked user-owned work at the starting commit.

## Milestone 0 - Correctness Residue

### Commit 0.1 - Require a current admission request ID

- Parse and validate a canonical UUID at `/api/room-invite/join`.
- Missing ID: HTTP 400 with `request_id_required`.
- Invalid ID: HTTP 400 with `request_id_invalid`.
- Make the maintained frontend invite API type require the ID.
- Keep random legacy IDs only for direct compatibility-facade callers.
- Add route, browser API, local retry, and PostgreSQL parity tests.

### Commit 0.2 - Inject the compatibility admission projection

- Add a typed `LegacyAdmissionProjection` boundary with joined/left methods.
- Current routes call the injected object instead of importing
  `connect_live_agent()`.
- Canonical admission remains committed if projection fails.
- Projection failures are bounded, redacted diagnostics with count, operation,
  error type, participant ID, and timestamp.
- Do not silently ignore projection errors.

### Commit 0.3 - Roll back partial GUI startup

- Track only services started by the current `start()` invocation.
- On failure, stop them in reverse order.
- Preserve the original startup exception and attach cleanup failures as notes.
- Leave the container in an explicit terminal `start_failed` state.
- Keep `shutdown()` idempotent after failed startup.

### Commit 0.4 - Add non-destructive workflow maintenance contracts

- Add typed workflow selection and `PurgeReport` contracts.
- Implement memory/JSON/PostgreSQL dry-run selection and explicit apply.
- Never purge non-terminal retryable/compensating workflows by default.
- Remove room-owned terminal workflows as part of room deletion cleanup.
- Add an explicit CLI maintenance command; no background polling or startup
  purge.

## Milestone 1 - Inventory And Architecture Firewall

### Commit 1.1 - Generate the package map

Add an AST-based inventory tool and commit `docs/product/PACKAGE_MAP.md` with:

- module path and line count;
- domain and current/optional/compatibility/legacy classification;
- imports and reverse-import count;
- import-time side-effect markers;
- known public import or monkeypatch path;
- primary tests;
- proposed package and migration status.

The generated content must be deterministic and checked by a test.

### Commit 1.2 - Prevent new flat modules

Add a gate that rejects new top-level product modules. Allowed roots are:

- `__init__.py`, `cli.py`, and `gui.py`;
- an explicit, documented compatibility-shim allowlist;
- existing modules not yet migrated, tracked in the package map.

The gate prevents growth; it does not demand a 302-file rename in one commit.

### Commit 1.3 - Enforce dependency direction

Add AST rules for packages once they exist:

```text
room, admission, identity, providers -> no web or legacy import
web                              -> no concrete sqlite/postgres import
current core                     -> no legacy import
legacy                           -> may use current contracts
application                      -> may compose concrete implementations
```

Generate a cycle report and fail only on new or migrated-package cycles. Do not
grandfather a newly introduced cycle.

## Milestone 2 - Stable Persistence Packages

Move leaf infrastructure first, one backend family per commit:

1. PostgreSQL application database and connection pool to
   `persistence/postgres/`.
2. PostgreSQL room query and repository implementation to
   `persistence/postgres/room/`.
3. PostgreSQL identity implementation to `persistence/postgres/identity/`.
4. PostgreSQL invite/session implementation to
   `persistence/postgres/admission/`.
5. Local SQLite/JSON implementations only after their contracts are separated
   from domain types.

Root shims are explicit and temporary. Each shim records known callers,
replacement import, introduction commit, and removal gate. No wildcard export.

## Milestone 3 - Admission And Identity Packages

Move stable contracts and services in dependency order:

1. admission models and preflight;
2. invite and session application services;
3. coordinator and saga, kept together where state transitions require local
   reading;
4. operator pairing;
5. identity contracts, models, and service.

Current application code imports the new owner. Root shims remain only for
verified compatibility callers and patch seams.

## Milestone 4 - Web And Application Packages

Move framework/transport concerns after domain packages are stable:

1. router, response, request security, static transport, WebSocket upgrade;
2. current route registrars grouped by domain under `web/routes/`;
3. GUI application services and transaction composition under `application/`;
4. keep `gui.py` and `serve_gui` as the stable entrypoint shim.

The route inventory in `GUI_COMPOSITION.md` remains the authority. No endpoint
is deleted or reclassified merely because its file moves.

## Milestone 5 - Provider Packages

Move stable provider contracts, catalog/profile controls, bridge protocol,
runtime factory/process ownership, then provider-specific adapters. Provider
behavior is frozen during these moves.

Required wave verification:

- adapter and fake persistent-provider tests;
- full Python suite;
- exact model/profile verification;
- real two-turn Codex, Claude, and Grok smoke through the frontend;
- same process/session across turns and pause/resume;
- duplicate final, secret/path leak, TUI debris, and orphan process counts.

## Milestone 6 - Stable Room And Legacy Boundaries

- Move stable Room types, repository protocol, projection, commands,
  transactions, lifecycle, moderation, and realtime controller only after the
  dependency inventory proves their owner.
- Defer unstable attention/routing/policy modules as described above.
- Move verified compatibility-only live-agent, meeting, and HTTP modules under
  `legacy/` in small families.
- Move Mafia, social, and side-chat only when current optional callers and
  frontend routes remain explicit.

Current packages must not import legacy implementations. Legacy code may depend
on current contracts.

## Milestone 7 - Shims, Tests, And Final Cleanup

- Commit a compatibility shim inventory with known caller, replacement import,
  introduction commit, and removal release/gate.
- Remove a shim only after caller evidence and the existing compatibility
  window permit it.
- Move tests into domain directories only where it improves ownership without
  hiding shared integration coverage.
- Update `CURRENT_SYSTEM.md`, topic documents, and package-local `AGENTS.md`
  only for boundaries that actually exist after the moves.

## Verification

### Every coherent commit

```text
python3 -m compileall -q agentsassemble tests
targeted unittest or Vitest
git diff --check
```

### Every package wave

```text
python3 -m unittest discover -s tests -t .
python3 -W error::ResourceWarning -m unittest discover -s tests -t .
npm --prefix frontend test
npm --prefix frontend run build
```

Run the real PostgreSQL contract runner for persistence, admission, identity,
room, and application-composition waves.

### Final release evidence

- full Python and strict ResourceWarning suites;
- PostgreSQL contracts with zero skips;
- frontend Vitest, build, and Playwright E2E;
- real frontend-driven Codex `gpt-5.6-luna`, Claude
  `claude-sonnet-4-6`, and Grok `grok-4.5` smoke at the lowest supported
  reasoning setting;
- `git diff --check`;
- remote GitHub Actions on the final pushed commit.

## Reporting Contract

The final report must separate:

1. correctness fixes;
2. package moves and compatibility shims;
3. behavior intentionally unchanged;
4. defects discovered and either fixed or deferred;
5. exact local, PostgreSQL, frontend, provider-smoke, and remote-CI evidence;
6. every difference from the external review plan and the reason.

In particular, it must state clearly that automatic startup purge, forced
top-level-file-count reduction, mass test movement, and unsettled conversation
module churn were deliberately avoided rather than forgotten.

## Progress Log

- 2026-07-16: External review was grounded against commit `29a7e0fa`.
  Its request-ID, legacy-projection, startup-rollback, retention, and flat-module
  findings were confirmed. The top-level count was 302 rather than the review's
  306. The execution plan adopted the domain-package direction while making
  retention non-destructive and inventory-driven, and while freezing unsettled
  conversation/media behavior.
- 2026-07-16: Milestone 0 correctness work completed in four reviewable slices:
  canonical UUID request IDs, an injected/redacted legacy admission projection,
  reverse-order GUI startup rollback, and explicit terminal-workflow maintenance.
  The maintenance contract supports dry-run/apply parity for memory, local JSON,
  and PostgreSQL, rejects retryable/compensating states, and has no startup purge.
  A proposed `agentsassemble/admission/` directory was not introduced early
  because it would shadow the existing public `agentsassemble/admission.py`
  module; that collision must be handled by the inventoried Milestone 3 shim.
- 2026-07-16: Milestone 1.1 added a deterministic AST inventory and generated
  `docs/product/PACKAGE_MAP.md`. It records 330 Python modules and 304 top-level
  modules (the 302-module starting point plus the two explicit workflow
  maintenance modules), internal and reverse imports, import-time call signals,
  test/monkeypatch evidence, proposed owners, and migration status. The
  committed map is checked byte-for-byte by a unit test before any module move.
- 2026-07-16: Milestones 1.2 and 1.3 froze the historical package-root module
  list, require metadata for any future root compatibility shim, and added
  dependency-direction and import-cycle gates. The two exact starting cycles
  are reported in `PACKAGE_CYCLES.md`; a changed or new cycle fails, while a
  removed cycle needs no baseline edit. Migrated core packages cannot import
  legacy or web implementations, and migrated web code cannot import concrete
  SQLite/PostgreSQL adapters.
- 2026-07-16: Milestone 2 moved PostgreSQL application database/pool ownership,
  room persistence, identity persistence, and admission persistence under
  `persistence/postgres/`, then moved the already contract-separated local
  SQLite room implementation under `persistence/local/room/`. Root paths are
  explicit compatibility exports with removal metadata, and current production
  imports use the owned paths. Shared room text normalization and event
  visibility no longer require a persistence adapter to import legacy meeting
  code or another concrete backend. Local identity and invite JSON
  implementations intentionally remain in their current modules because their
  contracts and implementations are still mixed; Milestone 3 must separate
  those contracts before moving the adapters. SQLite repository, migration,
  attention, realtime, Agent Session, package-map, dependency-direction, and
  cycle checks passed after the move.
- 2026-07-16: Milestone 3 began by resolving the `admission.py` package-name
  collision. Legacy meeting-mode admission decisions moved to
  `legacy/meeting_admission.py`; `agentsassemble.admission` preserves those two
  historical exports lazily without loading the legacy module for current
  submodule imports. Side-effect-free browser invite preflight moved to
  `admission/preflight.py`, while `room_admission.py` remains an explicit
  compatibility export. Production callers now import the owned paths. This
  slice does not move invite/session contracts or mutation services yet.
- 2026-07-16: Invite/session persistence contracts, repository errors, and the
  fail-closed unconfigured repository moved to `admission/repository.py`.
  Admission workflow persistence validation moved to
  `admission/workflow_record.py`, preserving its strict field allowlist while
  removing its dependency on legacy meeting text helpers. Current production
  services import the owned contracts directly. The memory and JSON adapters
  intentionally remain in `room_invite_repository.py` for this commit so the
  contract move and concrete local persistence move can be verified and
  reverted independently.
- 2026-07-16: Local memory and JSON invite/session adapters moved to
  `persistence/local/admission/` without changing the JSON schema, atomic
  rollback behavior, expiry filtering, or workflow purge semantics.
  `room_invite_repository.py` is now an explicit compatibility export with
  removal metadata. Production composition and smoke code import the owned
  local adapter path directly; only two focused compatibility tests retain the
  root import.
- 2026-07-16: Room session token issuance, fingerprint verification, expiry
  cleanup, revocation, idempotent request tokens, and redacted active-session
  summaries moved to `admission/session_issuer.py` and
  `admission/session_service.py`. Current callers import those owners directly;
  `room_session_issuer.py` and `room_session_service.py` remain explicit
  compatibility exports. The summary normalizer now depends on current
  `room.text` rather than legacy meeting helpers without changing output.
- 2026-07-16: Invite creation, signed-token and short-code validation, reusable
  invite limits, Agent Bridge provider validation, admission preparation,
  usage-guide projection, and workflow maintenance delegation moved to
  `admission/invite_service.py`. The service now uses current `room.text`
  normalization instead of legacy meeting helpers. Production callers import
  the owned path; `room_invite_application.py` remains an explicit compatibility
  export with the same constants, types, and functions.
- 2026-07-16: The resume-safe admission mutation coordinator and its durable
  compensation saga moved together to `admission/coordinator.py` and
  `admission/saga.py`. This preserves idempotency keys, deterministic session
  recovery, invite-consumption ordering, membership writes, and retryable
  compensation as one local state-machine boundary. Current callers import the
  owned paths, while the two root modules remain explicit compatibility
  exports. Both modules now use current `room.text` normalization.
- 2026-07-16: Terminal admission workflow selection, bounded purge reports, and
  the explicit dry-run/apply CLI command moved to `admission/maintenance.py`
  and `admission/maintenance_command.py`. Local and PostgreSQL adapters now
  import the owned maintenance contract directly. No startup purge or implicit
  retention behavior was added; the two root modules remain compatibility
  exports for one removal window.
- 2026-07-16: Explicit cross-origin local-operator pairing moved to
  `identity/pairing.py`, bootstrapping the identity domain package without
  prematurely splitting the still-coupled `identity_store.py` contract and
  local SQLite implementation. Pairing token hashing, origin validation,
  one-time redemption, transaction handling, membership writes, resumable
  session recovery, and revocation behavior are unchanged. Current production
  callers use the owned identity path; `operator_pairing.py` remains an
  explicit compatibility export for one removal window. The service now names
  its existing current-room text dependency directly instead of retaining the
  old lobby-oriented alias.
- 2026-07-16: The storage-independent `IdentityBackend` protocol, canonical
  local-operator identifiers, participant-type normalization, pairing
  redemption states, and device credential fingerprinting moved to
  `identity/repository.py`. Current domain, GUI composition, and PostgreSQL
  adapter code imports this owner directly. `identity_store.py` still owns the
  local SQLite schema, implementation, registry, and legacy JSON migration, and
  temporarily re-exports the contract names; that implementation move is kept
  separate so persistence behavior and registry lifetime can be verified in
  their own commits.
- 2026-07-16: The local identity SQLite schema and repository moved to
  `persistence/local/identity/repository.py`. Cached local construction and
  explicit output-root authority binding moved to `registry.py`; the one-time
  legacy member/user JSON imports moved to `migration.py`. Current production
  and implementation tests use those owners directly. `identity_store.py` is
  now an explicit compatibility export only. The move also removes the local
  adapter's dependency on legacy meeting text helpers while preserving the
  same normalization behavior, SQLite filename/schema, additive migrations,
  cache identity, hosted-backend binding guard, and one-time import rules.
- 2026-07-16: Identity-owned room preferences were split at the persistence
  boundary. Canonical `user_id` validation now lives in
  `identity/preferences.py`; SQLite schema, JSON encoding, migration markers,
  and CRUD helpers live in `persistence/local/identity/preferences.py`.
  PostgreSQL preferences depend only on the shared identity rule, while the
  explicit legacy preference migration depends directly on the local adapter.
  `identity_room_preferences.py` remains a compatibility export for one
  removal window. Preference data, table names, validation, and error behavior
  are unchanged.
- 2026-07-16: Identity backend selection moved to `identity/factory.py`.
  SQLite output-root binding, PostgreSQL DSN validation, application-database
  injection, lazy optional-dependency loading, and fail-closed errors are
  unchanged. GUI composition now imports the owned path directly;
  `identity_repository_factory.py` is an explicit compatibility export for one
  removal window.
- 2026-07-16: The first full Milestone 3 regression run exposed an existing
  ordering race in legacy live-agent operation auditing: malformed JSON could
  receive HTTP 400 before its failed operation record was durable. The shared
  request-body reader now offers an opt-in pre-error hook, and the operation
  route records the failure before sending the response. Other JSON routes keep
  their prior behavior.
- 2026-07-16: Milestone 3's stable admission and identity package wave is
  complete. Current production code owns admission contracts, local and hosted
  persistence, session/invite services, coordination, compensation, and
  maintenance under `admission/`; pairing, identity contracts, preference
  rules, and backend selection live under `identity/`; concrete local identity
  persistence lives under `persistence/local/identity/`. Root modules retained
  by this wave are explicit compatibility exports with caller and removal
  metadata, not parallel implementations.

  Final local evidence for this wave:

  - `python3 -m compileall -q agentsassemble tests`: passed.
  - targeted GUI composition/factory/architecture run: 490 tests passed.
  - operation-audit regression run: 60 tests passed, including ten repeated
    invalid UTF-8 requests before the full rerun.
  - `python3 -m unittest discover -s tests -t .`: 3,586 tests passed,
    74 skipped, in 419.531 seconds.
  - `python3 -W error::ResourceWarning -m unittest discover -s tests -t .`:
    3,586 tests passed, 74 skipped, in 418.702 seconds.
  - `npm --prefix frontend test`: 22 files and 126 tests passed.
  - `npm --prefix frontend run build`: passed.
  - package-map check and `git diff --check`: passed.

  The real PostgreSQL contract runner was not available locally:
  `AGENTSASSEMBLE_TEST_POSTGRES_DSN`, `alembic`, `psycopg`, `psycopg_pool`,
  and `sqlalchemy` are absent. Real provider and frontend Playwright smoke were
  not run because this wave did not change provider or rendered UI behavior;
  those remain required for their later package waves and final release
  evidence. Remote CI also remains unverified until a pushed commit is checked.
- 2026-07-16: Milestone 4 began with the request-security boundary.
  Host-header, origin, CORS, loopback, and public-invite route policy moved to
  `web/security.py` without changing its allowlist or trust decisions.
  `gui.py` and the request context import the owned path directly;
  `gui_request_security.py` remains an explicit compatibility export.
- 2026-07-16: HTTP response writing and React static delivery moved to
  `web/response.py` and `web/static.py`. Existing SSE framing, attachment
  security headers, cache policy, index rewriting, path containment, bootstrap
  routes, and disconnect behavior are unchanged. The two root modules remain
  explicit compatibility exports.
- 2026-07-16: The shared route table, `GuiDeps`, `RequestContext`, server URL
  helpers, and dynamic path validation moved intact to `web/router.py`.
  Production route modules and behavior tests now import the owned path;
  `gui_router.py` remains an explicit compatibility export. Invite and identity
  boundary tests inspect the owned file so the move does not create a vacuous
  shim-only pass. The architecture gate exposed that the admission projection
  protocol still lived beside its legacy implementation; the protocol and DTO
  moved to `admission/projection.py`, while
  `legacy/admission_projection.py` retains the actual roster mirror and
  compatibility exports.
- 2026-07-16: WebSocket ticket registration and HTTP upgrade lifecycle moved
  intact to `web/websocket.py`; `gui_ws_http.py` remains an explicit
  compatibility export. Route parity and ownership inventories now inspect
  both retained root route modules and owned `web/` modules rather than
  depending on the old filename location.
- 2026-07-16: The existing `GuiApplicationServices` lifecycle container moved
  intact to `application/gui.py`, and the shared transaction protocol moved to
  `application/transaction.py`. Current composition and domain callers import
  the owned paths; the two root modules remain explicit compatibility exports.
  The concrete `_build_gui_application_services()` factory stays in `gui.py`
  for this commit so ownership relocation is verified separately from changing
  construction order or rollback behavior. The dependency gate also removed
  two type-only imports of legacy process/session implementations: application
  lifecycle names the three process-monitor methods it owns through a Protocol
  and keeps the forwarded session-run controller opaque.
- 2026-07-16: The first current route registrar family moved to `web/routes/`:
  attachments, providers, public invite administration, room
  invite/admission, and room settings. Root modules remain explicit
  compatibility exports; production composition, behavior tests, monkeypatch
  targets, and invite-boundary tests use the owned paths. Route parity now
  inventories `web/routes/` recursively instead of relying only on root
  filenames.
- 2026-07-16: Agent Session create/resume/turn HTTP registration moved intact
  to `web/routes/agent_sessions.py`. The room coordinator imports the owned
  registrar while preserving its existing local process and turn adapter patch
  seams; `gui_room_agent_http.py` remains an explicit compatibility export.
- 2026-07-16: Room history/registry/message/vote routes and canonical
  participant/room lifecycle routes moved to `web/routes/room_history.py` and
  `web/routes/room_lifecycle.py`. The legacy file-backed `/api/room/ensure`
  endpoint remains isolated in `gui_room_lifecycle_http.py`; its historical
  combined registrar still registers ensure before current lifecycle routes.
  Current web modules therefore do not import the legacy frontend-meeting
  implementation.
- 2026-07-16: Roster streaming/read and member upsert/mute routes moved to
  `web/routes/room_members.py`. The retained resident kick path and optional
  custom channel/voice routes stay in `gui_room_moderation_media_http.py`;
  both production composition and the historical combined registrar register
  current member routes before the retained routes, preserving route order and
  behavior.
- 2026-07-16: Optional Mafia, side-chat, and room-social route registrars moved
  to `features/mafia/routes.py`, `features/side_chat/routes.py`, and
  `features/social/routes.py`. Production composition and behavior tests use
  the owned feature paths; the three root files are explicit compatibility
  exports. Route parity inventories now include feature route packages. The
  generated package-map classifier also recognizes existing `application/`
  and `features/` ownership so already-moved modules are not reported as
  planned moves.
- 2026-07-16: Before relocating read-only observability routes, the historical
  `gui -> observability -> release_health -> room_event_benchmark -> gui`
  import cycle was removed. The room-event benchmark now accepts an explicit
  HTTP handler factory for optional SSE measurement; the CLI composition root
  supplies the existing GUI handler only when SSE samples are requested.
  Enabling SSE samples without a factory fails immediately instead of silently
  skipping or substituting another transport.
- 2026-07-16: Read-only local-resource, release-health, and moderator-only
  legacy-admission diagnostic routes moved to `web/routes/observability.py`.
  Production composition and the local-resource monkeypatch test use the owned
  path; `gui_observability_http.py` is now an explicit compatibility export.
  No route, payload, authorization check, or release-health policy changed.
- 2026-07-16: The seven explicit `410 legacy_route_retired` endpoint
  tombstones moved to `web/routes/retired.py`. Production composition imports
  the owned registrar, while `gui_retired_http.py` remains a compatibility
  export through the documented release audit. Status codes, error code,
  replacement details, and route inventory are unchanged.
- 2026-07-16: GUI service construction ownership moved from the root entrypoint
  to `application/gui_factory.py`. The owned builder now enforces the shared
  repository instance, constructs the server-scoped ownership graph, and
  preserves reverse-order cleanup on partial construction failure. `gui.py`
  retains a thin `_build_gui_application_services()` wrapper so existing
  runtime monkeypatch seams remain stable. Legacy admission projection and
  legacy room-directory backfill are injected by that root composition wrapper
  rather than imported by `application/`; this differs from a literal full
  factory move to preserve the current-core-to-legacy dependency firewall.
- 2026-07-16: Milestone 5 began with the dependency-free provider runtime
  result and health contracts. Their owner is now
  `providers/runtime_contracts.py`; production bridge, realtime, turn
  coordinator, and Grok runtime imports use the owned path.
  `provider_runtime_contracts.py` remains an explicit compatibility export
  with a one-window removal gate. No provider command, model profile, process
  lifecycle, result parser behavior, or fallback policy changed.
- 2026-07-16: The optional API-provider model catalog moved intact to
  `providers/catalog.py`. CLI validation, the OpenAI-compatible API lane, and
  provider HTTP routes now import the owned path; `provider_catalog.py` is an
  explicit compatibility export. Static model data, secret presence
  projection, cost-owner resolution, and the existing optional API-lane
  fallback list are unchanged.
- 2026-07-16: Provider runtime launch configuration and profile parsing moved
  to `providers/runtime_config.py`. The owned module now imports the current
  `room.text` normalizer directly instead of reaching through legacy
  `meeting_events`; this preserves behavior because `clean_lobby_text` was
  already an exact delegation to `clean_room_text`. Production bridge,
  realtime, attendee, process-manager, and runtime-factory imports now use the
  owned path. `provider_runtime_config.py` remains an explicit compatibility
  export; field requirements, limits, error codes, command preservation, and
  runtime-profile semantics are unchanged.
- 2026-07-16: Provider runtime construction moved to
  `providers/runtime_factory.py`. The owned factory still selects the same
  DeepSeek, OpenCode, Grok ACP, POSIX PTY, and Windows ConPTY implementations
  from the same strict provider/transport pairs. Room bridge and attendee
  callers now use the owned path, while `provider_runtime_factory.py` remains
  an explicit compatibility export. Runtime arguments, platform selection,
  mismatch errors, and the no-PTY-fallback rule for Grok are unchanged.
  During verification, the package inventory incorrectly classified
  `windows_conpty.py` as web code because the substring `ws_` appears across
  the end of `windows` and its underscore. The generator now recognizes
  ConPTY as a provider transport before WebSocket naming rules, with a
  regression assertion; no runtime behavior changed.
- 2026-07-16: Agent Bridge turn-assignment and ACK/NACK contracts moved to
  `providers/bridge_protocol.py`, with correlated report deadline ownership in
  `providers/bridge_report_tracker.py`. The protocol now imports the current
  `room.text` normalizer directly; this is behavior-equivalent to the former
  legacy helper delegation. `room_agent_bridge.py` and protocol behavior tests
  use the owned paths, while both root modules remain explicit compatibility
  exports. Identity checks, fatal/error codes, request correlation, timeout
  behavior, and tracker synchronization are unchanged.
- 2026-07-16: Provider credential storage and child-process environment
  sanitation moved to `providers/secrets.py` and
  `providers/process_environment.py`. Server, room bridge, provider runtime,
  capability discovery, and provider HTTP callers now use the owned paths;
  root modules remain explicit compatibility exports. The keyring singleton,
  environment fallback, status-only secret projection, platform allowlist,
  provider secret-name filtering, and explicit-extra behavior are unchanged.
- 2026-07-16: The DeepSeek HTTPS/SSE adapter moved to
  `providers/deepseek.py`. Runtime construction and behavior tests use the
  owned path, while `deepseek_runtime.py` remains an explicit compatibility
  export. The adapter now imports `room.text` directly instead of the legacy
  delegation; API payloads, supported model/effort validation, private
  reasoning suppression, bounded conversation memory, interruption, key
  clearing, and diagnostics are unchanged.
- 2026-07-16: The Windows persistent terminal adapter moved to
  `providers/windows_conpty.py`. The provider factory and ConPTY behavior tests
  use the owned path, while `windows_conpty.py` remains an explicit
  compatibility export. The adapter now imports `room.text` directly;
  pywinpty spawning, injected process seams, startup readiness, persistent
  process reuse, output limits, delta/final extraction, interrupt, cleanup,
  profile diagnostics, and platform environment sanitation are unchanged.
- 2026-07-16: OpenCode shared-server and per-agent session ownership moved to
  `providers/opencode.py`. Runtime construction, attendee, server-owned bridge
  process, and behavior tests now use the owned path; `opencode_runtime.py`
  remains an explicit compatibility export. Direct `room.text` normalization
  replaces the legacy delegation without changing values. Shared server
  startup/cleanup, model and permission validation, persisted session reuse,
  directory scoping, SSE ordering, reasoning/tool activity projection, text
  delta/final selection, interruption, and diagnostics are unchanged.
- 2026-07-16: The structured Grok ACP stdio adapter moved to
  `providers/grok_acp.py`. Runtime construction and ACP lifecycle tests now use
  the owned path, while `grok_acp_runtime.py` remains an explicit
  compatibility export. Direct `room.text` normalization replaces the legacy
  delegation. Exact `grok agent stdio` selection, JSON-RPC correlation,
  persistent session load/save, permission denial, yolo rejection, structured
  delta filtering, bounded stderr drain, notification backpressure failure,
  process-group cleanup, provider error classification, and diagnostics are
  unchanged.
- 2026-07-16: Live terminal output extraction moved to
  `providers/live_cli_output.py`. POSIX PTY, Windows ConPTY, transcript
  adapters, and output behavior tests now use the owned path;
  `live_cli_output.py` remains an explicit compatibility export. ANSI/screen
  rendering, provider answer marker detection, startup readiness matching,
  terminal chrome filtering, reasoning/status suppression, fallback scoring,
  and message cleanup rules are unchanged.
- 2026-07-17: Provider-native structured transcript adapters moved to
  `providers/live_cli_transcripts.py`. POSIX PTY and Windows ConPTY runtimes,
  transcript behavior tests, and the Antigravity request-normalization test
  now use the owned path; `live_cli_transcripts.py` remains an explicit
  compatibility export. Direct `room.text` normalization replaces the legacy
  delegation without changing values. Provider session discovery, exact-turn
  binding, partial JSONL handling, Markdown preservation, model observation,
  strict extraction errors, and terminal-capture behavior are unchanged.
- 2026-07-17: The persistent POSIX PTY runtime moved to
  `providers/live_cli.py`. Runtime construction, the opt-in smoke harness, and
  persistent PTY/room behavior tests now use the owned path; `live_cli.py`
  remains an explicit compatibility export for its public runtime contract.
  Direct `room.text` normalization replaces the legacy delegation without
  changing values. Process persistence, startup TUI negotiation, bounded
  terminal capture, transcript-first final extraction, interrupt/restart,
  process-group cleanup, diagnostics, and the unsupported API stub are
  unchanged.
- 2026-07-17: Best-effort local provider session discovery moved to
  `providers/sessions.py`. Its behavior tests use the owned path while
  `provider_sessions.py` remains an explicit compatibility export. Codex UUID
  extraction, Claude workspace scoping, Antigravity conversation labels,
  timestamp ordering, result limits, and the existing empty-on-unreadable
  policy are unchanged.
- 2026-07-17: Provider-reported model identity verification moved to
  `providers/model_verification.py`. Room realtime and turn coordination use
  the owned policy while `provider_model_verification.py` remains an explicit
  compatibility export. Focused tests now document the existing pending,
  unavailable, exact, alias, Claude provider-revision, and mismatch outcomes;
  no model selection or rejection rule changed.
- 2026-07-17: Shared provider authentication error classification moved to
  `providers/auth.py`. Codex, Grok, Antigravity, and Cursor residents now use
  the owned helper while `provider_auth.py` remains an explicit compatibility
  export. Focused tests document the existing case-insensitive login markers,
  Korean login guidance, and unrelated-error behavior; marker and copy values
  are unchanged.
- 2026-07-17: Codex CLI session-id extraction and JSONL event parsing moved to
  `providers/codex_session_ids.py` and `providers/codex_stream.py`. The Codex
  resident, legacy Codex adapter, and focused parser tests now use the owned
  paths while both root modules remain explicit compatibility exports.
  Session UUID recognition, labeled-text fallback, message/command/reasoning
  event classification, ordering, and ignored-event behavior are unchanged.
- 2026-07-17: Claude transcript discovery, JSONL parsing, and incremental
  tailing moved to `providers/claude_transcript.py`. The CLI entrypoint and
  focused transcript tests now use the owned path while
  `claude_transcript.py` remains an explicit compatibility export. Generated
  session IDs, project transcript lookup, assistant-only event filtering,
  tool summaries, partial-line handling, and no-history-replay behavior are
  unchanged.
- 2026-07-17: React build inspection moved to
  `web/frontend_runtime.py`, correcting the generated inventory's former
  provider classification for the root `frontend_runtime.py` name. CLI, GUI,
  and response transport callers now use the web-owned path while the root
  module remains an explicit compatibility export. Missing, incomplete,
  complete, query/fragment asset, and path-traversal reference behavior are
  covered directly; static serving behavior is unchanged.
- 2026-07-17: Claude interactive TUI rendering, answer extraction, prompt-leak
  trimming, and print-mode rejection moved to
  `providers/claude_resident.py`. CLI, preflight, and shared live-output
  callers now use the owned path while `claude_resident.py` remains an
  explicit compatibility export. Real-capture extraction, CJK screen spacing,
  last-answer selection, envelope stripping, and the ban on `claude -p` and
  `--print` are unchanged.
- 2026-07-17: The optional OpenAI-compatible direct API adapter moved to
  `providers/api.py`. CLI API-call entrypoints and behavior tests now use the
  owned path while `room_api_provider.py` remains an explicit compatibility
  export. Catalog lookup, key resolution, HTTP error categories, usage
  estimation and recording, and the existing rate-limit/unavailable-only
  fallback chain are unchanged; no API lane was promoted over native CLI
  sessions.
- 2026-07-17: The Codex app-server room-runtime wrapper moved to
  `providers/codex_app_server_live.py`. Room attendee construction and runtime
  control tests now use the owned path while
  `codex_app_server_live_runtime.py` remains an explicit compatibility export.
  The wrapper now imports the provider runtime directly instead of through
  `agent_sessions`, removing a provider-to-application reverse dependency, and
  uses the current `room.text` normalizer. Sandbox/approval mapping, persistent
  handle reuse, delta/final/error handling, activity categories, and health
  diagnostics are unchanged.
- 2026-07-17: The resident Codex CLI command adapter moved to
  `providers/codex_resident.py`. CLI, preflight, continuity, legacy adapter,
  runner, and focused behavior tests now use the owned path while
  `codex_resident.py` remains an explicit compatibility export. A narrow
  `providers/resident_config.py` protocol replaces the resident adapters'
  former type-only imports from legacy `live_agent_runner`, removing the
  historical resident/runner import cycle and preparing the remaining provider
  moves without changing runtime objects. Exec/resume command construction,
  sandbox and fast overrides, session extraction, streaming thought behavior,
  timeout and login errors, and authentication checks are unchanged.
- 2026-07-17: The resident Antigravity CLI command adapter moved to
  `providers/antigravity_resident.py`. CLI, preflight, continuity, legacy
  runner, and focused behavior tests now use the owned path while
  `antigravity_resident.py` remains an explicit compatibility export.
  Conversation resume, native model and permission flags, backend quota/error
  rejection, real-capture answer extraction, status/meta trimming, timeout and
  login diagnostics, and stateless valid-reply behavior are unchanged.
- 2026-07-17: The resident Grok CLI command adapter moved to
  `providers/grok_resident.py`. CLI, preflight, continuity, legacy runner,
  lifecycle, thought-stream, and focused behavior tests now use the owned path
  while `grok_resident.py` remains an explicit compatibility export. JSON and
  streaming-JSON parsing, session resume, native model/effort/permission flags,
  thought chunking and posting, timeout/auth/error categories, command checks,
  and final answer assembly are unchanged.
- 2026-07-17: The resident Cursor CLI command adapter moved to
  `providers/cursor_resident.py`. CLI, preflight, continuity, legacy runner,
  lifecycle, and focused behavior tests now use the owned path while
  `cursor_resident.py` remains an explicit compatibility export. Chat creation
  and resume, isolated or configured workspace reuse, native model selection,
  command and authentication checks, timeout/error categories, and the
  superseded/generic-provider guards are unchanged. The owned path also
  corrects the adapter's previous application-domain classification without
  adding a new dependency or runtime behavior.
- 2026-07-17: The resident Hermes CLI command adapter moved to
  `providers/hermes_resident.py`. CLI, preflight, continuity, legacy runner,
  and focused behavior tests now use the owned path while
  `hermes_resident.py` remains an explicit compatibility export. Hermes chat
  query construction, provider-managed resume IDs, safe source names, status
  prefix and DSML tool-call removal, timeout/error categories, and command
  validation are unchanged. The move corrects the adapter's former
  application-domain classification without changing room behavior.
- 2026-07-17: The resident Kiro CLI command adapter moved to
  `providers/kiro_resident.py`. CLI, preflight, continuity, legacy runner, and
  focused behavior tests now use the owned path while `kiro_resident.py`
  remains an explicit compatibility export. Fresh-session capture
  serialization, session-list diffing, `--resume-id` reuse, chat command
  normalization, ANSI/status cleanup, timeout and empty-reply behavior, and
  command validation are unchanged. The move corrects the adapter's former
  application-domain classification without changing room behavior.
- 2026-07-17: Codex app-server process, JSON-RPC, profile isolation, stderr
  drain, diagnostics, and runtime-manager ownership moved to
  `providers/codex_app_server.py`. Application, web, and persistent-room
  callers now use the owned path while `codex_app_server_runtime.py` remains
  an explicit compatibility export. The already-built provider input fallback
  moved to `providers/turn_input.py`, with `room_turn_context._agent_turn_prompt`
  retained as a compatibility wrapper. Direct `room.text` normalization
  replaces the legacy delegation without changing values. Command and
  JSON-RPC settings, thread resume, timeout and inferred-completion handling,
  bounded stderr diagnostics, crash recovery state, runtime sharing, and
  detach behavior are unchanged.
- 2026-07-17: Provider command construction, launch specifications, static
  definitions, stored-profile validation, and migration guards moved to
  `providers/launch_specs.py`. Admission, room lifecycle, realtime, routing,
  smoke, capability, and behavior-test callers now use the owned path while
  `native_cli_providers.py` remains an explicit compatibility export. Direct
  `room.text` normalization replaces the legacy delegation without changing
  values. Codex, Antigravity, Grok, Claude, OpenCode, and DeepSeek defaults;
  runtime profile keys; exact/alias model selection; Grok ACP enforcement;
  Claude interactive-mode enforcement; and legacy stored-profile acceptance
  rules are unchanged.
- 2026-07-17: Provider option discovery, bounded catalog caching, listener
  diagnostics, and session-selection validation moved to
  `providers/capabilities.py`. Realtime orchestration and focused capability
  tests now use the owned path while `provider_capabilities.py` remains an
  explicit compatibility export. Native command probes, OpenCode discovery,
  static Claude and DeepSeek manifests, fail-closed refresh behavior,
  catalog revisions, model/effort/tier relation checks, and sanitized child
  environments are unchanged.
- 2026-07-17: Persistent provider turn delivery and report handling moved to
  `providers/agent_bridge.py`, while environment parsing, credential stdin,
  WebSocket construction, runtime construction, and signal registration moved
  to `application/agent_bridge_entrypoint.py`. Server-owned bridge launches now
  invoke the application entrypoint directly; `room_agent_bridge.py` retains
  runtime exports and the historical module command as a compatibility
  boundary. Shared room-visible text detection moved to `room.text`, with the
  legacy `meeting_events` name preserved. Turn assignment, observed-cursor
  checkpointing, activity redaction, delta/final reporting, stop confirmation,
  cleanup diagnostics, and launch configuration are unchanged.
- 2026-07-17: Server-owned Agent Bridge process handles, launch-config
  persistence, credential stdin handoff, shared OpenCode server ownership,
  stderr drain, exit watching, and process cleanup moved to
  `providers/bridge_process.py`. GUI composition, native CLI smoke, fixtures,
  and E2E tests now use the owned path while `room_bridge_process.py` remains
  an explicit compatibility export. The relocated module computes the
  repository package root from its new depth, with focused assertions for
  subprocess cwd and `PYTHONPATH`; bridge command, secret boundaries, profile
  directories, stderr diagnostics, and stop semantics are unchanged.
- 2026-07-17: Milestone 6 began with dependency-free room contracts.
  `RoomEvent`, participant/session/command/turn typed shapes moved to
  `room/types.py`, and `RoomCommandRejected` moved to `room/errors.py`.
  Realtime, lifecycle, projection, turn coordination, and focused tests now use
  the owned paths while `room_types.py` and `room_errors.py` remain explicit
  compatibility exports. No event fields, exception codes, runtime behavior,
  persistence format, or command handling changed.
- 2026-07-17: Room command-envelope validation, identity capability policy,
  public room/session/event projection, and bounded runtime-diagnostic
  projection moved to `room/commands.py` and `room/projection.py`. Realtime,
  lifecycle, turn coordination, and focused behavior tests now use the owned
  paths while `room_commands.py` and `room_projection.py` remain explicit
  compatibility exports. Both owned modules import `room.text` directly
  instead of the legacy `meeting_events` delegation; normalization output is
  unchanged. Command actions, validation codes, capability decisions, private
  field redaction, activity labels, latency merging, and diagnostic bounds are
  unchanged.
- 2026-07-17: The backend-neutral room persistence and room-transaction
  protocols moved to `room/repository.py`. Application, admission, identity,
  web, room orchestration, local SQLite, hosted PostgreSQL, and contract tests
  now import the owned path while `room_repository.py` remains an explicit
  compatibility export. Record aliases, runtime-checkable protocol behavior,
  transaction atomicity requirements, listener semantics, and attention
  cursor method signatures are unchanged. SQLite/factory/realtime verification
  passed; PostgreSQL behavior tests remained skipped because the current
  environment does not provide the PostgreSQL test extra or configured test
  database.
- 2026-07-17: Room command idempotency, canonical payload hashing, transaction
  lifetime, and durable ACK finalization moved to `room/command_uow.py`.
  Realtime orchestration, turn finalization, and backend-neutral repository
  contract tests now use the owned path while `room_command_uow.py` remains an
  explicit compatibility export. Request-id conflict detection, deduplicated
  ACK shape, rollback-on-unfinalized behavior, transaction proxy methods, and
  hashing output are unchanged.
- 2026-07-17: Canonical room WebSocket channel queues, message-delta
  backpressure, room-event fanout, active bridge generations, and targeted
  bridge delivery moved to `room/event_broker.py`. Realtime, lifecycle, turn
  coordination, and focused broker tests now use the owned path while
  `room_event_broker.py` remains an explicit compatibility export. Direct
  `room.text` normalization replaces the legacy helper delegation without
  changing values. Queue bounds, delta eviction, resync markers, socket wakeup,
  bridge supersession, and disconnect behavior are unchanged.
- 2026-07-17: External Agent Bridge stop request/confirmation correlation
  moved to `room/bridge_stop_confirmation.py`. Room lifecycle and focused
  confirmation tests now use the owned path while
  `bridge_stop_confirmation.py` remains an explicit compatibility export.
  Direct `room.text` normalization replaces the legacy helper delegation.
  Current-generation enforcement, control IDs, timeout and delivery errors,
  effect-before-release callback ordering, and controller-close cancellation
  are unchanged.
- 2026-07-17: Agent Session process lifecycle orchestration moved to
  `room/agent_lifecycle.py`. Realtime composition and focused lifecycle tests
  now use the owned path while `room_agent_lifecycle.py` remains an explicit
  compatibility export. Direct `room.text` normalization replaces the legacy
  helper delegation. Provider lookup, server/external process ownership,
  generation-safe stop confirmation, pause/resume, pending-event preservation,
  recovery scheduling, session-state publication, and cleanup reporting are
  unchanged. Actual OS process handles remain provider-owned.
- 2026-07-17: The moderation slice intentionally did not move
  `room_members.py` wholesale because that file combines retained roster
  projection, ephemeral Thinking presence, legacy invite deduplication, and
  membership mutations. Canonical mute/remove compatibility writes and mute
  lookup moved instead to `room/moderation.py`; realtime, room HTTP, history,
  GUI composition, and tests use the owned boundary while `room_members.py`
  reexports the three functions for compatibility. Identity membership
  delegation, the transient SQLite fail-open read policy, canonical retry
  behavior, roster output, and Thinking state are unchanged. A focused test
  now covers delegation and the fail-open branch directly.
- 2026-07-17: Shared cleanup report aggregation, orphan-handle evidence,
  secret-redacted failure messages, and stderr emission moved to
  `diagnostics/cleanup.py`. Provider bridges, provider process ownership, room
  lifecycle, room realtime, turn coordination, attendee cleanup, and focused
  tests now use the owned path while `cleanup_report.py` remains an explicit
  compatibility export. The move removes a legacy `meeting_events` dependency
  by importing the same `room.text` normalizer directly; report fields,
  counters, redaction patterns, and output format are unchanged. The
  architecture inventory and dependency gate now recognize `diagnostics/` as
  a current package that cannot import legacy or web implementations.
- 2026-07-17: Canonical provider-delivery cursor validation, compatibility
  cursor fields, divergence reconciliation, bounded reconciliation diagnostics,
  and fail-closed parity errors moved to `providers/sync_cursor.py`.
  Agent Session packet preparation, room context, turn coordination, realtime
  startup reconciliation, and focused tests now use the owned path while
  `room_provider_sync_cursor.py` remains an explicit compatibility export.
  Direct `room.text` normalization replaces the legacy helper delegation;
  cursor authority, monotonic repair rules, recovery-required behavior,
  transaction boundaries, event codes, and diagnostics are unchanged.
- 2026-07-17: Bounded room-visible message projection, omission accounting,
  event filtering counts, and per-message character budgets moved to
  `room/context.py`. Agent Session packet construction, diagnostics benchmarks,
  and focused context tests now use the owned path while `room_context.py`
  remains an explicit compatibility export. Direct `room.text` normalization
  replaces the legacy helper delegation; sequence reads, exclusion of the
  target participant, truncation markers, and output fields are unchanged.
- 2026-07-17: Provider turn packet assembly, bootstrap/delta/recovery text,
  room identity projection, bounded room-memory inclusion, media manifest
  filtering, and prompt-budget fitting moved to `room/turn_context.py`.
  Agent Session execution now uses the owned path while `room_turn_context.py`
  retains the verified compatibility exports and test patch seam. Direct
  `room.text` normalization replaces the legacy helper delegation. The
  optional local `RoomStore` construction remains unchanged for direct
  compatibility callers; canonical realtime and Agent Session execution still
  inject their existing repository instance. Packet fields, prompt text,
  media audit rules, cursor advancement, and failure behavior are unchanged.
- 2026-07-17: Pending-event partitioning, provider turn assignment, bridge
  state validation, activity and delta projection, final-message transaction
  writes, provider cursor advancement, decline/failure handling, and bounded
  recovery moved to `room/turn_coordinator.py`. Realtime composition and
  focused turn tests now use the owned path while `room_turn_coordinator.py`
  remains an explicit compatibility export. The existing
  `room_turn_attention.py` policy dependency remains in place and frozen;
  queueing decisions, phase transitions, model verification, diagnostics,
  event payloads, retry timing, and cleanup behavior are unchanged.
- 2026-07-17: The room-scoped in-memory provider specification registry moved
  behind `room/provider_registry.py`. Realtime creation, restored sessions,
  external bridges, profile display-name updates, kick, room deletion,
  routing snapshots, and controller cleanup now use the synchronized registry
  API instead of mutating one shared dictionary from unrelated code paths.
  Durable Agent Session state, provider launch behavior, room routing policy,
  public controller methods, and the controller's compatibility lookup methods
  are unchanged.
- 2026-07-17: Provider participant and durable Agent Session creation,
  external-bridge registration, stored server-owned profile restoration,
  stopped-profile replacement, cursor initialization, and profile migration
  repair moved to `room/provider_sessions.py`. `room_realtime.py` retains its
  public create/configure methods as delegation points and composes the service
  with the existing repository, broker, registry, lock, room creation, and
  session-state publisher. Strict stored-profile rejection, owner fields,
  cursor parity, model verification, event payloads, and runtime-state guards
  are unchanged.
- 2026-07-17: Capability-projected initial/resume/gap/bridge snapshots and
  bounded history-page reads moved to `room/snapshots.py`.
  `room_realtime.py` retains the same `snapshot()` and `history_page()` methods
  as delegation points and continues to own room creation and command
  orchestration. Event limits, bridge self-only projection, provider catalog
  visibility, active-turn shape, reconnect cursors, raw history-page payloads,
  and capability output are unchanged.
- 2026-07-17: Browser participant connection/update behavior and active Agent
  Bridge disconnect-to-detached transitions moved to `room/connections.py`.
  External bridge session creation remains delegated to
  `room/provider_sessions.py`, while bridge lease activation remains owned by
  the broker/ready command path. `room_realtime.py` keeps the same
  `connect()`/`disconnect()` entrypoints. Connection-id mutation, superseded
  bridge handling, session-detached events, and session-state publication are
  unchanged.
- 2026-07-17: Agent Bridge `ready` and `health` report validation, external
  runtime-profile verification, bridge-generation activation, canonical
  participant/session updates, and bounded runtime diagnostics moved to
  `room/bridge_reports.py`. `room_realtime.py` retains its private command
  handlers as delegation points and composes the service with the existing
  broker, repository, turn coordinator, and session-state publisher. Health
  contract errors, provider/profile mismatch errors, attached/joined events,
  pending-turn assignment, and private PID/executable redaction are unchanged.
- 2026-07-17: Server-restart Agent Session ownership reconciliation moved to
  `room/startup_reconciliation.py`. The service detects the same active runtime
  states, restores inflight event IDs to the ordered pending set, delegates the
  existing attention reset, clears stale process/bridge/turn ownership, marks
  recovery required, and detaches the participant. `room_realtime.py` retains
  its startup method as a delegation point. Stopped-session behavior, error
  text, field values, and provider recovery policy are unchanged.
- 2026-07-17: Transactional agent display-name/avatar updates and their
  post-commit provider-registry/session-state synchronization moved to
  `room/agent_profiles.py`. The command handler still owns capability checks,
  request idempotency, and transaction lifetime; the new service owns only the
  canonical participant/session/event mutation and the non-duplicated
  post-commit projection update. Explicit avatar clearing, rollback behavior,
  next-turn identity, final-message attribution, and public payloads are
  unchanged.
