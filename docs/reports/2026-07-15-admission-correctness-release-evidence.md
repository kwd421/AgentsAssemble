# Admission Correctness Refactor Release Evidence

Status: release evidence complete

Date: 2026-07-15

Branch: `codex/risuai-character-personas`

Range: `b2cccf11..a096210`

Plan: `docs/plans/2026-07-15-admission-correctness-refactor.md`

External review source: `review.md` supplied outside the repository

## 1. Scope And Verdict

This range completes the planned identity, invite, admission, pairing,
persistence, and application-composition correctness refactor. Local and
remote release gates pass.

The result is:

- local authority fails closed instead of inventing empty invite/session state;
- one participant has at most one active room bearer under the current
  contract;
- invite admission and operator pairing are retry-safe and idempotent;
- hosted PostgreSQL admission shares one application database and one
  cross-authority transaction connection;
- local SQLite/JSON admission has an explicit durable workflow and bounded
  compensation rather than a pretend cross-store transaction;
- current HTTP and WebSocket paths receive explicit services and repositories;
- mutable compatibility globals remain only behind identified legacy seams;
- frontend admission and settings have explicit state owners;
- browser identity recovery, real PostgreSQL races, real provider continuity,
  cleanup, and full local gates were exercised.

No second room transport, provider-specific browser socket, parallel event
log, polling path, one-shot provider fallback, `claude -p`, or
`codex exec resume --last` was introduced.

## 2. Frozen Product Areas

This refactor intentionally did not decide or redesign:

- autonomous conversation and speaker selection;
- semantic silence, reaction, handoff, defer, or scheduled wakeups;
- token budgets, pair cooldowns, or sequence-mode semantics;
- media understanding or provider-native image/PDF/audio delivery;
- LISTEN/NOTIFY, Redis, Kafka, WebRTC, or voice.

A non-critical defect found in those areas is recorded in Section 9. The
threshold for changing frozen behavior was security failure, data loss or
corruption, server outage, deadlock, uncontrolled resource leak, or a
regression caused by this range. That threshold was not met.

## 3. Implementation Summary

### 3.1 Fail-closed authority and session invariants

Local invite/session persistence now rejects unreadable, malformed,
wrong-schema, structurally invalid, or unwritable state. Mutations restore the
prior in-memory value when the durable write fails. Process-local memory mode
must be configured explicitly.

Session issuance uses one atomic participant-session replacement operation.
Memory, JSON, and PostgreSQL implementations enforce one active room bearer
per participant. A failed replacement leaves the previous bearer valid. A
reusable invite with no durable policy row now fails with `invite_not_found`
instead of becoming effectively unlimited.

Operator pairing trusts the normalized HTTP `Origin` header, not a body value.
The frontend removes URL credentials before external navigation/referral and
represents pairing as active, retryable failure, terminal failure, or paired.

### 3.2 One admission workflow owner

`InviteApplicationService`, `RoomAdmissionCoordinator`, `RoomSessionService`,
and the pairing service now own the current paths. Browser admission carries a
secure UUID request ID. Durable workflow state stores bounded fingerprints,
not raw invite, device, or bearer credentials.

Retries converge on the same participant, membership, invite consumption, and
session. Payload changes under the same request ID fail with
`idempotency_conflict`. Pairing redemption is resumable for the same device;
another device cannot take over the claim.

### 3.3 PostgreSQL ownership and local compensation

`PostgresApplicationDatabase` owns schema-head validation, a bounded shared
pool, transaction context, safe diagnostics, and shutdown. Room, identity,
and invite/session repositories borrow it and do not close the shared pool.

Hosted admission commits identity resolution, invite consumption, bearer
replacement, participant, membership, and completion on one PostgreSQL
connection. Pairing retains the same-device claim as a resumable security
lease, then atomically commits the remaining state.

Local SQLite plus JSON cannot provide one database transaction. Its durable
workflow therefore records phases and bounded compensation. If the room
disappears after partial work, it revokes only the deterministic bearer and
removes only the matching membership, records each cleanup step, and resumes
after restart. Invite consumption remains retained for replay safety.

### 3.4 Compatibility and frontend ownership

Current routes use injected identity, invite, session, pairing, and public
runtime services. `room_invite.py` and `room_users.py` remain compatibility
facades for verified legacy callers. Retained meeting/process/session routes
are composed by `LegacyGuiApplication`, not individually by `gui.py`.

Seven retired HTTP operations remain explicit `410 legacy_route_retired`
tombstones through the first tagged `v0.1.x` release. The repository has no
tag yet, so removing them now would shorten the documented compatibility
window.

Frontend admission is one discriminated reducer. Legacy meeting polling/SSE
lifetimes are isolated in `useLegacyMeetingSurfaces`. Room settings expose
`loading`, `ready`, `saving`, `stale`, and `error`; a failed read no longer
guesses conversation routing defaults. Core chat, roster, admission, composer,
and Agent Session controls stay eager, while six non-core views remain lazy.

## 4. Intentional Differences From Review Suggestions

### No independent old-session revoke checkpoint

The review listed old-session revocation as a failure-injection phase. The
implementation removed that independent side effect. Atomic replacement is
safer: a failed new write cannot disconnect the previously valid session.

### Pairing claim survives selected rollback

The same-device pairing claim intentionally survives a retryable failure as a
security lease. Participant, membership, bearer, and completion still commit
atomically. This prevents a different device from stealing a pairing during
recovery while allowing the original device to resume.

### Local mode uses a saga, not a fake transaction

SQLite identity and JSON invite/session data cannot participate in one native
transaction. The implementation records an explicit workflow and
compensation state rather than adding a silent fallback or claiming atomicity
that the storage layout cannot provide.

### Loading boundaries were retained

The frontend was not split simply to reduce line count. The existing lazy
boundary already separates infrequently opened views without delaying room
chat or Agent Session controls. The production build is the direct contract;
no new source-string presence test was added.

## 5. Failure-Injection And Concurrency Evidence

Failure injection covers:

- identity creation;
- invite consumption;
- atomic session replacement;
- participant upsert;
- membership upsert;
- pairing consumption and completion;
- local compensation and process/repository reconstruction.

Every retry converged on one participant, one membership, one active bearer,
and the intended invite use count. An ordinary invite never gained operator
authority, and an unrelated pairing device remained unbound.

Real PostgreSQL multi-instance tests used independent application database
pools and independent service graphs sharing only the schema. They proved:

- exactly one winner for a one-use invite;
- exactly two winners among four callers for a two-use invite;
- same-device convergence on one identity, participant, membership, and
  session;
- one winning device for a one-time operator pairing;
- rollback without duplicate state or event-sequence gaps.

Final command:

```text
AGENTSASSEMBLE_TEST_POSTGRES_DSN=<temporary-local-postgres> \
  <isolated-python> -m tests.run_postgres_contracts
```

Result: 84 passed, 0 skipped, PostgreSQL 17.

## 6. Browser Identity Evidence

Playwright exercised the actual HTTP and canonical WebSocket server with
separate browser contexts. Covered behavior includes:

- ordinary invite versus one-time cross-origin operator pairing;
- exact-origin enforcement, replay rejection, and wrong-origin rejection;
- same-origin rejoin with stable participant identity;
- known membership and expired bearer recovery;
- incognito credential separation and equal display-name separation;
- failed join recovery;
- desktop streaming and control of the same canonical session on mobile.

The expired-bearer scenario found a release defect before final verification:
the room connection could expose a stale stored bearer while invite preflight
was still recovering it, and an unauthorized callback then erased the
admission. `useRoomAdmission` now exposes only a reducer-confirmed,
non-expired bearer to authenticated room consumers.

Result: 4 Playwright scenarios passed.

## 7. Frontend-Driven Provider Smoke

The smoke used the browser UI on the dedicated local smoke server. Session
settings, start/resume, pause, queued second message, resume, and stop were
performed through the product controls rather than direct backend mutation.

| Provider | Requested and observed model | Effort | Transport | Recall TTFO | Recall total |
| --- | --- | --- | --- | ---: | ---: |
| Codex | `gpt-5.6-luna` | low | PTY | 1,136.2 ms | 2,244.4 ms |
| Claude Code | `claude-sonnet-4-6` | low | interactive PTY | 1,948.8 ms | 3,066.6 ms |
| Grok | `grok-4.5` | low | ACP stdio | 12,730.5 ms | 12,789.0 ms |

Grok's first marker turn was materially faster: TTFO 1,880.3 ms and total
2,056.0 ms. The long recall reflects provider response time after resume. The
roughly 132-second manual pause interval is not included in `total_turn_ms`;
the queued event remained unprocessed until resume.

For every provider:

- requested and observed model IDs matched with `verified` status;
- the first marker final appeared exactly once;
- no response appeared during the two-second paused check;
- the queued recall final appeared exactly once after resume;
- `session_paused` recorded `process_preserved: true`;
- `session_resumed` recorded `process_reused: true`;
- stop produced detached/stopped state, `provider_session_active: false`, and
  the observed provider process was no longer alive;
- `last_error` was empty and `context_error_detected` was false.

Claude ran as the interactive CLI and never used `-p`. Codex and Claude final
messages came from their session JSONL sources. Grok used its ACP message
source. Grok drained one 69-byte update notice from stderr with zero warning
count; no fallback output path was used.

Visible smoke events were scanned after completion:

- duplicate marker finals: 0;
- TUI footer/spinner/control debris: 0;
- project/user path matches: 0;
- bearer/API-key pattern matches: 0;
- invite-token or tunnel-URL matches: 0.

## 8. Final Local Gates

| Check | Result |
| --- | --- |
| `python3 -m unittest discover -s tests -t .` | 3,524 passed, 69 skipped |
| `python3 -W error::ResourceWarning -m unittest discover -s tests -t .` | 3,524 passed, 69 skipped |
| `npm --prefix frontend test` | 126 passed |
| `npm --prefix frontend run build` | passed; 459.62 kB main JS, no 500 kB warning |
| `npm --prefix frontend run test:e2e` | 4 passed |
| PostgreSQL contract runner | 84 passed, 0 skipped |
| `git diff --check` | passed before report commit |

The first full Python run after the refactor exposed four host-account errors
and four stale implementation-location assertions. The host tests were still
constructing a fail-closed `RequestContext` without its now-required
application dependencies. The source assertions expected logic to remain in
`App.tsx` after it had moved behind behavior-tested hooks. Commit `7aaf289`
made dependencies explicit, added a reducer behavior test for expired-session
locking, and removed only the obsolete source-location assertions. The full
gate then passed; no production fallback or warning suppression was added.

## 9. Deferred Finding In A Frozen Area

When Codex was started for the release smoke, it processed one old room event
addressed to `@opencode` before the explicit Codex marker turn. The visible
reply was a benign status acknowledgement, but Codex should not have been a
candidate for that old targeted event.

Classification:

- area: startup backlog and speaker selection;
- severity: non-critical for this refactor;
- observed impact: one unwanted provider turn and token use;
- no security boundary failure, data loss, outage, deadlock, or leaked secret;
- likely predates the admission work and was not introduced by its commits.

Because speaker selection and autonomous conversation policy are explicitly
unsettled, changing candidate/backlog semantics here would silently make a
product decision. The defect is therefore reported for the future
conversation-policy work rather than patched with another filter or fallback.

## 10. Remote GitHub Actions

The first post-report workflow, run
[`29448660921`](https://github.com/kwd421/AgentsAssemble/actions/runs/29448660921),
found one frontend E2E test defect. The paused-session assertion searched the
whole session region for `일시정지`, so Playwright strict mode correctly rejected
the two matches: the visible session status and the pause button. Product state
and pause behavior were correct; the selector was ambiguous.

Commit `a096210` scoped the assertion to
`.dc-member-session-location-head`, the status field the test intended to
verify. It did not add a timeout, retry, fallback, or weaker text match. Two
independent fresh-server local Playwright runs passed after the correction.

Replacement workflow
[`29449003915`](https://github.com/kwd421/AgentsAssemble/actions/runs/29449003915)
then passed every required job:

- Python 3.11 full unit suite;
- Python 3.13 full unit suite;
- PostgreSQL contracts;
- Ubuntu runtime/platform contracts;
- Windows runtime/platform contracts;
- frontend Vitest and canonical-room Playwright E2E;
- production frontend build.

GitHub emitted non-failing Node 20 deprecation annotations for the current
official `actions/checkout`, `actions/setup-python`, and `actions/setup-node`
versions, which GitHub ran on Node 24. These are dependency-maintenance notices,
not a product test failure or a suppressed warning.
