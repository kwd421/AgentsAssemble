# Hosted Identity, Invite, And Release-Hygiene Refactor

Status: implemented and verified

Completed: 2026-07-15

Starting commit: `cf4c47c`

Completion report:
`docs/reports/2026-07-15-hosted-identity-invite-refactor.md`

Implementation commits:

- `f438c19` Extract room invite and session repositories
- `0213135` Separate room session issuance
- `9c2ce1f` Add PostgreSQL invite and session storage
- `d91cad6` Add PostgreSQL identity authority
- `d570f38` Close SQLite test connections
- `b4b8567` Retire unused legacy GUI routes
- `393573b` Extract React static transport
- `ca8010a` Clarify GUI request boundaries
- `2552c25` Split non-core frontend views
- `d92b19f` Relax brittle frontend source assertions
- `2929938` Preserve GUI operation error details
- `6470096` Return not found for stale room reads

## Goal

Finish the remaining maintainability work identified after the browser identity
and admission slice, without changing the room product model or reviving the
frozen ambient-participation work.

The completed browser identity work remains the behavioral baseline. This plan
extracts persistence and lifecycle ownership, adds hosted-mode parity, removes
known resource leaks, isolates obsolete HTTP compatibility routes, reduces the
initial frontend bundle, and then verifies the current product through the real
browser UI and real provider sessions.

## Sources Of Truth

Read in this order when resuming this work:

1. `docs/product/CURRENT_SYSTEM.md`
2. `docs/product/ROOM_REPOSITORY.md`
3. `docs/product/GUI_COMPOSITION.md`
4. `docs/product/OPERATING_MODEL.md` only for security or authority boundaries
5. `/Users/seinel/Downloads/review.md`, section "그 이후의 리팩터링 계획"

Reports are evidence, not architecture authority.

## Non-Goals

- Account login or third-party OAuth
- Ambient/autonomous room participation
- Semantic silence, reactions, scheduled wakeups, or model speaker selection
- A second room transport or provider-specific event log
- Provider fallbacks that hide unavailable binaries, credentials, or quotas
- UI redesign beyond loading boundaries needed for bundle splitting
- Removing supported public APIs without caller evidence and a compatibility
  decision

## Invariants

- `RoomStore`/`RoomRepository` remains the canonical room authority.
- Canonical room clients continue to use `/ws?ticket=...`.
- Raw invite/session tokens are never persisted.
- Invite use limits and replay protection are atomic within their repository.
- Local mode remains SQLite/JSON-compatible and keeps existing on-disk state.
- Hosted mode does not silently fall back to local storage.
- Claude real-provider verification uses interactive Claude Code with Haiku;
  `claude -p` is forbidden.
- Real smoke is driven through the product frontend, not private backend calls.
- Existing user-owned untracked files are not staged or modified.

## Commit Slices

### 1. Extract invite and room-session repositories

Add explicit repository contracts for:

- invite signing secret and pending invite records
- atomic invite consumption/replay protection
- active room-session records and revocation

Move JSON persistence and server-lifetime locking out of `room_invite.py` into a
local repository implementation. Keep the public functions in `room_invite.py`
as a compatibility facade while moving crypto, validation, and admission policy
onto an injected service. Preserve the existing JSON schema and token behavior.

Verification:

- repository contract tests
- existing room invite/admission/operator pairing tests
- restart durability and concurrent consume tests
- no raw token at rest

### 2. Add PostgreSQL identity, invite, and session parity

Add PostgreSQL-backed implementations selected explicitly by configuration.
Use migrations for identity, credential, pairing, membership, preference,
usage, invite, replay, and session records. Keep repository table names
separate from canonical room tables where ownership differs.

The PostgreSQL contract runner must exercise both room and identity/invite
contracts. Missing PostgreSQL support or a failed connection must fail clearly;
it must not select SQLite/JSON as a fallback.

Verification:

- SQLite/PostgreSQL identity contract parity
- local/PostgreSQL invite/session contract parity
- concurrent invite consume permits only the configured number of joins
- session revoke and expiry parity
- mandatory GitHub PostgreSQL job

### 3. Remove actionable resource leaks

Run the test suite with `ResourceWarning` promoted to errors. Fix owned SQLite
connections, file handles, sockets, and subprocess pipes at their lifecycle
boundary. Do not suppress warnings globally.

Verification:

- targeted leak regressions
- full suite under `PYTHONWARNINGS=error::ResourceWarning`

### 4. Isolate seven legacy HTTP routes

Reconfirm runtime callers for the seven review-listed endpoints:

- `POST /api/demo`
- `GET /api/provider-sessions`
- `GET /api/codex-sessions`
- `GET /api/live-agent-create/options`
- `POST /api/live-agent-create/check`
- `POST /api/live-agent-create`
- `POST /api/live-agent-room/expel`

If production frontend/CLI code has no caller, replace implementation ownership
with an explicit `410 Gone` compatibility tombstone and delete now-unreachable
handler wiring where safe. Do not pretend an obsolete route succeeded.

Verification:

- route ownership tests
- explicit 410 response tests
- frontend/CLI source has no caller

### 5. Shrink the GUI compatibility seam

After repository injection and route isolation, remove only imports, callbacks,
and facade wiring made unreachable by those changes. Keep request parsing in
route modules, policy in services, persistence in repositories, and lifecycle
ownership in `GuiApplicationServices`.

Verification:

- GUI application lifecycle tests
- route ownership tests
- full backend suite

### 6. Split the initial frontend bundle

Use `React.lazy`/dynamic imports for infrequently opened surfaces such as Admin,
Mafia, Board, Records, and remaining legacy views. Keep room chat, roster,
composer, admission, and agent controls in the initial path. Preserve visible
copy, state, and accessibility behavior.

Verification:

- Vitest behavior tests
- production build and chunk inspection
- Playwright desktop and mobile room workflows

### 7. Current-HEAD real-provider frontend smoke

From a clean temporary workspace, use the browser UI to reuse or create the
actual Agent Sessions and exercise:

- Codex: two warm turns
- Claude Code Haiku: two warm turns, never `-p`
- pause, bounded backlog, resume, and stop
- same provider PID/session across warm turns where the provider supports it
- observed model matches the requested model
- duplicate final messages: zero
- TUI residue in room messages: zero
- local path, secret, and hidden backend detail exposure: zero
- orphan provider processes after stop/server shutdown: zero

Record direct evidence and latency; unavailable credentials, quota, or binaries
are reported as blocked rather than substituted. The browser workflow is the
acceptance surface.

## Final Verification

Run:

```text
PYTHONWARNINGS=error::ResourceWarning python3 -m unittest discover -s tests -t .
npm --prefix frontend test
npm --prefix frontend run build
npm --prefix frontend run test:e2e
python3 -m tests.run_postgres_contracts
git diff --check
```

The PostgreSQL command requires `AGENTSASSEMBLE_TEST_POSTGRES_DSN`. If no local
PostgreSQL is available, CI evidence is required and the local gap must be
reported explicitly.

## Reporting

The completion report must separate:

- repository and service boundaries introduced
- local compatibility preserved
- PostgreSQL behavior and contract evidence
- resource warnings fixed
- legacy routes tombstoned or retained, with caller evidence
- frontend chunks and visible behavior
- real provider smoke evidence and exact blockers
- commits and push status
