# Hosted Identity, Invite, And Release-Hygiene Refactor Report

Status: implemented and verified

Date: 2026-07-15

Branch: `codex/risuai-character-personas`

Starting commit: `cf4c47c`

Plan: `docs/plans/2026-07-15-hosted-identity-invite-refactor.md`

## 1. Scope And Result

This slice completed the maintainability work that followed the browser
identity and invite-admission correction. It did not change the shared-room
product model and did not resume the frozen ambient-participation work.

The result is:

- invite and short-lived room-session persistence now have explicit repository
  contracts;
- room-session issuance no longer shares persistence and token-lifecycle code
  with invite policy;
- hosted PostgreSQL mode has identity, invite, replay, and room-session parity
  instead of retaining local JSON or SQLite authorities;
- local mode retains the existing SQLite/JSON-compatible behavior and files;
- owned SQLite connections that produced `ResourceWarning` output are closed at
  their test lifecycle boundary;
- seven unused legacy HTTP operations now return an explicit `410 Gone` instead
  of remaining live compatibility implementations;
- React static transport and request-boundary plumbing are outside `gui.py`;
- six non-core frontend views are lazy-loaded while room chat, roster,
  admission, composer, and Agent Session controls stay in the initial bundle;
- current-HEAD Codex Luna and Claude Code Haiku sessions were exercised through
  the actual browser UI, including pause, queued backlog, resume, and stop;
- a stale-room read discovered during that browser smoke now returns a normal
  `404` instead of printing an uncaught request-thread traceback.

The canonical room authority remains `RoomRepository`/`RoomStore`, and browser
room traffic remains on `/ws?ticket=...`. No provider-specific room transport,
parallel event log, polling path, or API-style provider fallback was added.

## 2. Repository And Service Boundaries

### 2.1 Invite and session persistence

`room_invite_repository.py` now defines separate `InviteRepository` and
`SessionRepository` contracts, with the combined
`InviteSessionRepository` lifecycle used by the application. Local persistence
is implemented by `JsonInviteSessionRepository`; the in-memory implementation
remains available for explicitly non-persistent test/runtime use.

The repository owns:

- invite signing-secret persistence;
- pending invite records and join-code fingerprints;
- atomic invite consumption and nonce replay protection;
- invite revocation by invite or room;
- fingerprint-keyed room-session records;
- session revocation by token, participant, or room.

`room_invite.py` remains a compatibility facade for existing callers, but no
longer owns the JSON file, process-wide lock, and every lifecycle concern in one
module.

### 2.2 Room-session issuance

`RoomSessionIssuer` owns short-lived session-token creation, fingerprinting,
lookup, expiry, and revocation. Admission policy can ask it to issue or resolve
a session without reaching into invite persistence internals.

This split is intentionally narrow. It names a security-sensitive lifecycle
boundary and makes expiry/revocation tests independent from invite parsing.
It is not a pass-through service added only to reduce line count.

### 2.3 Application lifecycle

`GuiApplicationServices` constructs the room, identity, invite/session, and
related services from one explicit repository configuration. The same owner
closes those resources. Route modules receive these dependencies through
`GuiDeps`/`RequestContext` rather than importing hidden global persistence.

## 3. PostgreSQL Hosted-Mode Parity

Two migrations extend the existing PostgreSQL schema chain:

- `0005_invite_sessions` adds invite, used-nonce, and room-session storage;
- `0006_identity_authority` adds users, credentials, pairings, memberships,
  preferences, usage, and operator-pairing state.

`PostgresInviteSessionRepository` implements atomic invite consumption and
session revocation. `PostgresIdentityRepository` composes focused user,
roster, preference, and usage persistence modules behind the same
`IdentityBackend` contract used locally.

Repository selection is explicit:

- SQLite room mode selects local identity and JSON invite/session storage;
- PostgreSQL room mode requires a PostgreSQL DSN and optional PostgreSQL
  dependencies;
- missing configuration or adapter dependencies raise a clear startup error;
- hosted mode never silently substitutes SQLite or JSON storage.

Local PostgreSQL 17 verification used a fresh UTF-8 temporary cluster. The
targeted identity/invite repository tests passed 12 of 12, and
`python3 -m tests.run_postgres_contracts` passed 68 of 68. Alembic reported one
head, `0006_identity_authority`.

The existing GitHub PostgreSQL contract job invokes the expanded contract
runner, so the new authorities are included in the required hosted-mode gate.
This report records local evidence only; remote CI after the final push is a
separate result.

## 4. Local Compatibility And Security Properties

The local persistence path and public compatibility facades remain available.
Existing invite JSON schema and token behavior were preserved while ownership
moved behind the repository contract.

Security-sensitive behavior verified by repository contracts includes:

- room sessions are indexed by token fingerprint rather than raw token;
- one-use invite nonce replay state survives repository restart;
- reusable invite use limits are consumed atomically;
- concurrent consumers cannot exceed the configured admission count;
- session expiry and revocation have local/PostgreSQL parity;
- PostgreSQL configuration failure is explicit and has no local fallback.

This slice does not add account login, OAuth, account recovery, or identity
continuity between separate AgentsAssemble servers.

## 5. Resource-Lifecycle Cleanup

The strict suite initially exposed SQLite connections retained by tests that
constructed stores outside a context manager. Those tests now close the owned
store at the same lifecycle boundary that created it. No warning filter or
global suppression was added.

The final backend gate is run with:

```text
PYTHONWARNINGS=error::ResourceWarning python3 -m unittest discover -s tests -t .
```

That makes a newly leaked file, socket, SQLite connection, subprocess pipe, or
similar owned resource a test failure rather than log noise.

## 6. Legacy HTTP Decisions

Source and runtime-caller inspection found no production frontend or CLI caller
for these seven operations:

```text
POST /api/demo
GET  /api/provider-sessions
GET  /api/codex-sessions
GET  /api/live-agent-create/options
POST /api/live-agent-create/check
POST /api/live-agent-create
POST /api/live-agent-room/expel
```

They were not silently deleted. Each route now returns:

- HTTP `410 Gone`;
- stable code `legacy_route_retired`;
- a replacement pointing to the canonical room snapshot or WebSocket command.

The old handler wiring and obsolete frontend creation test were removed. This
keeps old clients visibly incompatible while avoiding a false success response.

## 7. GUI And Frontend Maintainability

### 7.1 Python GUI boundary

`gui_static_transport.py` now owns React asset discovery, cache headers, static
path resolution, and byte-range delivery. `RequestContext` exposes named
request/dependency operations used by room route modules, reducing direct
handler coupling.

The compatibility seam in `gui.py` was reduced from 3,902 lines at the starting
commit to 3,699 lines. The reduction is a consequence of moving coherent
transport and request ownership, not a line-count target.

Malformed GUI operation recording retains the pre-existing explicit empty
details object. A full-suite regression caught that call-contract difference,
and commit `2929938` restored it rather than weakening the assertion.

### 7.2 Initial frontend bundle

The following non-core views now load on demand:

- Admin
- Board
- Friends
- Live
- Records
- Custom channel

Core room chat, roster, composer, invite admission, and Agent Session controls
remain eager. Mafia behavior was not split into an invented view boundary; its
current code changes with the core room surface and was left in place.

Production build comparison:

| Build | Main JavaScript | Gzip | Result |
| --- | ---: | ---: | --- |
| Before | 708.19 kB | 206.67 kB | Vite 500 kB warning |
| After | 452.70 kB | 135.13 kB | no 500 kB warning |

Generated lazy chunks include Admin 28.79 kB, Board 15.99 kB, Friends 15.97
kB, Live 13.11 kB, Records 9.35 kB, and Custom channel 5.89 kB.

Two source-string tests that asserted exact import/formatting text were removed
or relaxed. Behavioral Vitest, build, and Playwright gates remain the evidence
for the loading change.

## 8. Real Provider Browser Smoke

### 8.1 Method

The smoke used the current branch in a clean temporary workspace and a fresh
state root on a dedicated loopback port. The existing user server was not
stopped or modified.

Every product action was performed through the browser frontend:

1. create a room;
2. open `Agent add`;
3. select the provider-native model from the dropdown;
4. select low reasoning, default service tier, and read-only permission;
5. start the Agent Session;
6. send room messages through the composer;
7. pause Codex, send a message while paused, and resume from the agent panel;
8. stop Claude and Codex from the agent panel.

Backend/SQLite reads were used only after the UI actions to collect diagnostics
and verify cleanup. They were not used to create, dispatch, pause, resume, or
stop a session.

### 8.2 Requested and observed models

| Participant | Requested | Observed | Verification |
| --- | --- | --- | --- |
| Codex | `gpt-5.6-luna` | `gpt-5.6-luna` | `verified` |
| Claude Code | `claude-haiku-4-5` | `claude-haiku-4-5-20251001` | `verified_provider_revision` |

Claude was launched interactively with `--model claude-haiku-4-5 --effort low`.
No `claude -p` path was used. Codex was launched with Luna, low reasoning,
default service tier, and read-only sandbox.

### 8.3 Turn results and latency

| Provider | Turn | TTFO | Total | Result |
| --- | ---: | ---: | ---: | --- |
| Codex | 1 | 4,377.9 ms | 5,563.8 ms | remembered `17` |
| Codex | 2 | 3,393.3 ms | 4,526.4 ms | recalled `17` and answered the Python question |
| Claude | 1 | 10,404.0 ms | 11,654.5 ms | completed with a provider-authored refusal |
| Claude | 2 | 6,443.7 ms | 7,548.6 ms | recalled that the earlier value was `23`, but refused the requested format |

Codex two-turn median TTFO was 3,885.6 ms and median total time was 5,045.1 ms.
Claude two-turn median TTFO was 8,423.9 ms and median total time was 9,601.6 ms.

Claude's refusal is not rewritten into a passing marker. It is the actual
Claude Code response. The second response explicitly identified the earlier
number `23`, which demonstrates retained context, but declined to append it as
requested because it classified the prompt as a compliance test. Session
transport and completion succeeded; prompt-obedience did not.

### 8.4 Pause, backlog, and process continuity

Codex was paused through the UI. A second addressed room message was appended
while paused and produced no agent response. Resume then dispatched the bounded
three-event/994-character delta since its prior cursor. The resulting
`session_resumed` event recorded `process_reused: true`, and the response
correctly used the value retained from the first turn.

Each provider had one live process tree across its two turns. Both canonical
sessions ended with `turn_count: 2`, one stable bridge generation, and advancing
`last_seen_event_id` cursors. No per-turn provider restart was observed.

### 8.5 Output and cleanup checks

Canonical event inspection found:

- four `turn_started` and four completed `turn_finished` events;
- exactly one `message_final` for each provider turn;
- duplicate provider finals: 0;
- TUI/footer/control residue in visible provider messages: 0;
- local workspace path, temporary path, API-key marker, bearer credential, or
  hidden backend detail in visible provider messages: 0;
- context errors: 0;
- stderr bytes: 0 for both sessions;
- stderr warnings: 0 for both sessions.

After both UI stop actions, the roster showed both sessions as `stopped`, the
canonical session records were `detached/stopped`, and all recorded provider
processes were gone. Server shutdown left no Codex or Claude child process from
the smoke workspace.

### 8.6 Additional defect found by the smoke

The browser briefly asks for settings/channels while reconciling a locally
remembered room against the canonical room directory. Missing rooms previously
allowed `RoomStore.room_settings()` to escape as an uncaught request-thread
`ValueError`, producing a traceback and HTTP 500.

Commit `6470096` added an existence check at the channel/settings HTTP boundary.
A malformed room identifier remains `400`; a valid but absent room is now
`404`. This preserves the canonical repository behavior and gives the browser a
normal stale-state signal.

## 9. Plan Deviations And Their Intent

### Legacy routes use tombstones before deletion

The plan allowed retention, tombstoning, or deletion after caller inspection.
The implementation chose `410` tombstones because public callers outside this
repository cannot be disproved. This is safer than keeping obsolete behavior
and clearer than an unexplained `404`.

### Frontend split follows actual view ownership

The plan listed examples rather than a required file count. Six existing view
boundaries were lazy-loaded. No new Mafia wrapper was created solely to satisfy
the list because it would separate code that currently changes with the core
room surface.

### Provider smoke records refusal rather than substituting success

Claude rejected the marker-style prompts. The run was not replaced with another
provider/model and the response was not parsed into a synthetic success. The
report separates runtime/session success from provider instruction adherence.

### No ambient participation or login work was pulled forward

Autonomous attention, semantic silence, reactions, scheduled wakeups, account
login, and cross-server account recovery remain frozen or out of scope. This
slice only establishes maintainable authorities needed before those product
decisions are revisited.

## 10. Verification Record

Completed local verification:

```text
PYTHONWARNINGS=error::ResourceWarning python3 -m unittest tests.test_gui_server
  430 passed

PYTHONWARNINGS=error::ResourceWarning python3 -m unittest discover -s tests -t .
  3422 passed, 53 skipped

python3 -m unittest \
  tests.test_gui_server_room_settings_http tests.test_room_channels_http
  16 passed

npm --prefix frontend test
  21 files, 113 tests passed

npm --prefix frontend run build
  passed; main JS 452.70 kB, no 500 kB warning

npm --prefix frontend run test:e2e
  2 Playwright tests passed

python3 -m tests.run_postgres_contracts
  68 passed against temporary PostgreSQL 17

git diff --check
  passed
```

The full-suite count includes the two stale-room regressions added after the
real browser smoke. Remote GitHub Actions status is intentionally not claimed
until the final branch push starts a new workflow run.

## 11. Commit Record

| Commit | Purpose |
| --- | --- |
| `9e41511` | Record the hosted identity/invite refactor plan. |
| `f438c19` | Extract local invite/session repository ownership. |
| `0213135` | Separate session issuance and revocation. |
| `9c2ce1f` | Add PostgreSQL invite/session schema and adapter. |
| `d91cad6` | Add PostgreSQL identity authority and parity contracts. |
| `d570f38` | Close test-owned SQLite resources. |
| `b4b8567` | Replace seven unused legacy handlers with 410 tombstones. |
| `393573b` | Extract React static transport from `gui.py`. |
| `ca8010a` | Clarify request and dependency boundaries. |
| `2552c25` | Lazy-load six non-core frontend views. |
| `d92b19f` | Remove brittle source-format assertions. |
| `2929938` | Preserve explicit GUI operation error details. |
| `6470096` | Return 404 for stale room settings/channel reads. |

## 12. Remaining Gaps

- Account login, OAuth, account recovery, and cross-server identity are not
  implemented.
- Ambient/autonomous room participation remains intentionally frozen.
- This smoke covered Codex and Claude as required by this plan; it was not a
  multi-provider free-discussion run.
- Claude context retention was observed through its own refusal text, but its
  policy prevented the requested marker-format success.
- Remote CI results after the final push remain to be observed.
- The SQLite/PostgreSQL repository contracts establish storage parity; they do
  not by themselves provide cross-instance event fan-out or deployment
  operations.
