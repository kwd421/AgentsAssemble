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
