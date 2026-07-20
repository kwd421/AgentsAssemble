# Package Architecture Refactor Final Review Report

Status: complete for the approved refactor scope

Date: 2026-07-20

Branch: `codex/risuai-character-personas`

Plan: `docs/plans/2026-07-16-package-architecture-refactor.md`

Current architecture: `docs/product/CURRENT_SYSTEM.md`

Plan range: `29a7e0fa..HEAD` (189 commits before this report commit)

Final unpublished package wave: `71cc5feca89625f9ff0ea0ee2477b35d2aa8c25b..HEAD`
(21 commits before this report commit)

## Executive Summary

Milestones 0 through 7 are complete for the scope approved in the plan. The
flat `agentsassemble/` namespace is no longer the production owner for code
whose domain was established. Current code is organized under explicit
`admission`, `application`, `diagnostics`, `features`, `identity`,
`persistence`, `providers`, `room`, and `web` packages. Retained meeting and
resident-agent compatibility code is under `legacy`.

The generated package map has zero `planned-move` entries. This does not mean
that every historical root import was deleted. Moved root paths remain explicit
compatibility exports with a replacement import, known-caller evidence,
introduction wave, and removal gate. Nine unsettled conversation-policy modules
are deliberately marked `deferred-policy`, and four cross-domain modules remain
`retained-migration` until a real contract split is approved.

This was primarily an ownership refactor, but full verification found and fixed
several real compatibility defects. Final evidence includes 3,834 Python tests,
the strict ResourceWarning suite, 86 PostgreSQL contracts with zero skips, 127
frontend tests, a production build, four Playwright scenarios, a real
three-provider session smoke, and a 60-second shared-room conversation with
pause/resume and kick checks.

## 1. Correctness Work

Milestone 0 completed four correctness slices before package movement:

- canonical UUID request IDs at current invite admission;
- injected, redacted legacy admission projection diagnostics;
- reverse-order rollback of partially started GUI services;
- explicit, non-destructive admission workflow maintenance with dry-run by
  default and no startup purge.

Later package waves preserved those contracts and the canonical
`RoomStore`/`RoomRepository` plus `/ws?ticket=...` path. No provider-specific
room transport, one-shot provider path, or stateless `complete(prompt)` path was
introduced.

## 2. Package Ownership Result

The completed owner boundaries are:

- `admission/`: invite/session contracts, services, coordination, compensation,
  maintenance, preflight, projection, and repository selection;
- `application/`: GUI lifecycle composition, Agent Session orchestration,
  native room smoke, CLI application services, and transaction boundaries;
- `diagnostics/`: release health, cleanup, package and runtime diagnostics;
- `features/`: optional Mafia, side-chat, and social behavior;
- `identity/`: identity contracts, pairing, preferences, and backend factory;
- `persistence/local/` and `persistence/postgres/`: concrete storage adapters;
- `providers/`: provider definitions, profiles, catalogs, bridges, process
  ownership, transcripts, transport, sandboxing, and provider adapters;
- `room/`: stable room types, commands, lifecycle, moderation, snapshots,
  realtime collaborators, history, participants, settings, media metadata, and
  provider-session coordination;
- `web/`: HTTP/WS transport, request security, static delivery, current route
  registrars, and browser room clients;
- `legacy/live_agent/` and `legacy/meeting/`: retained resident and meeting
  behavior, including their HTTP and CLI surfaces.

The last 21 package commits closed the remaining meeting engine, stable room,
retained HTTP, resident runtime/integration, room migration, provider support,
legacy CLI/diagnostics, shared room support, legacy meeting support, package-map
status, and compatibility seams.

## 3. Compatibility Shims

Root modules that moved are explicit exports, not wildcard forwarding modules.
Architecture checks require metadata for each shim and reject new untracked
flat product modules. Current packages cannot import legacy implementations,
and migrated web code cannot depend on concrete SQLite or PostgreSQL adapters.

Shims were retained when tests, monkeypatch paths, or possible external callers
still provide compatibility evidence. Removing them merely to lower the root
file count would create a breaking release without product benefit.

## 4. Defects Found During Refactoring

The verification process found and corrected these issues rather than adding
runtime fallbacks:

- broad path replacement could confuse similarly prefixed module names;
- moved service modules still had historical root import or monkeypatch seams;
- release-health repository-root calculation used the old file depth;
- the `room_invite` compatibility export omitted two public session-token
  constants;
- route parity inventories scanned only historical root files and became blind
  after registrars moved under owned packages;
- a malformed legacy operation request could respond before its audit record
  became durable.

The fixes restored explicit compatibility contracts or corrected ownership
inventory boundaries. They did not duplicate routes, suppress errors, change a
provider model, or add a substitute execution path.

## 5. Behavior Intentionally Unchanged

This refactor does not decide or redesign:

- autonomous conversation or an agent's freedom not to speak;
- semantic silence, reactions, handoff, defer, scheduled wakeup, token budgets,
  pair cooldown, or speaker-selection policy;
- sequence-mode product rules;
- provider-native image, PDF, audio, or video understanding;
- LISTEN/NOTIFY, Redis, Kafka, WebRTC, or voice architecture;
- browser versus native-client consumption rules for agent-only invites.

These areas are unsettled product policy, not package-cleanup residue. Moving or
rewriting them during a mechanical refactor would make later design review
harder and could silently change token use or room behavior.

No `claude -p`, `codex exec resume --last`, model fallback, reasoning reduction,
or one-shot provider subprocess was added.

## 6. Differences From The External Review Plan

The following deviations are intentional and should not be read as missed
work:

1. **No automatic startup purge.** No approved retention period exists, and
   retryable workflow records may be required for recovery. Maintenance remains
   explicit and dry-run first.
2. **No forced top-level file-count target.** Ownership, dependency direction,
   and removal evidence are release gates; a cosmetic number is not.
3. **No mass test relocation.** Tests moved only when ownership became clearer.
   Shared integration tests remain shared.
4. **No unsettled conversation-module churn.** Nine modules remain
   `deferred-policy` because their reason to change is the still-open autonomous
   conversation design.
5. **Retained HTTP belongs under `legacy`, not current `web`.** Moving legacy
   endpoints into `web/routes` would make the dependency graph look cleaner
   while hiding their compatibility lifecycle.
6. **Provider login remains legacy behind a current Protocol.** Current web code
   receives an injected `ProviderLogin` contract instead of importing the
   concrete retained service.
7. **Legacy benchmarks remain under legacy diagnostics.** Only their stable
   threshold contract moved to shared diagnostics.
8. **Repository migrations remain legacy while factories are application
   composition.** This keeps one-time transfer behavior separate from current
   persistence ownership.

## 7. Final Automated Verification

### Python

```text
python3 -m unittest discover -s tests -t .
  3,834 tests passed, 74 skipped

python3 -W error::ResourceWarning -m unittest discover -s tests -t .
  3,834 tests passed, 74 skipped
```

The 74 normal-suite skips are optional real PostgreSQL/environment checks, not
hidden failures. They were exercised separately by the mandatory contract
runner below.

### PostgreSQL

An isolated UTF-8 PostgreSQL 17 cluster was created under `/tmp`, with the
project installed into an isolated `.[postgres]` virtual environment. The same
runner used by CI completed with no skips:

```text
AGENTSASSEMBLE_TEST_POSTGRES_DSN=<redacted-local-dsn> \
  python -m tests.run_postgres_contracts
  86 tests passed, 0 skipped
```

The first local attempt used an incorrectly initialized `SQL_ASCII` scratch
database. SQLAlchemy received the server-version value as bytes and raised a
`TypeError`; recreating the scratch database as UTF-8 made all 86 contracts
pass. This was a test-environment encoding error, not a product-code fallback.
The temporary PostgreSQL server was stopped after the run.

### Frontend and architecture

```text
npm --prefix frontend test
  22 files, 127 tests passed

npm --prefix frontend run build
  passed

npm --prefix frontend run test:e2e
  4 Playwright scenarios passed

python3 scripts/generate_package_map.py --check
  passed

python3 scripts/check_package_architecture.py
  passed

git diff --check
  passed
```

Playwright covers operator/invite pairing, stable rejoin identity, failed
join/incognito separation, desktop streaming, and mobile Agent Session control.

## 8. Real Provider Session Smoke

A clean temporary provider workspace and isolated room state were served through
the canonical React/HTTP server and `/ws` room protocol. Two turns per provider
verified provider provenance, memory continuity, same PID, strict structured
message source, cleanup, and observed latency.

| Provider | Requested and observed model | Reasoning | Result | TTFO p50 | Total p50 |
| --- | --- | --- | --- | ---: | ---: |
| Codex | `gpt-5.6-luna` | low | passed | 3,035.3 ms | 5,517.8 ms |
| Claude Code | `claude-sonnet-4-6` | low | passed | 1,939.1 ms | 3,819.8 ms |
| Grok | `grok-4.5` | low | passed | 1,714.6 ms | 2,050.1 ms |

For all three providers:

- the second turn used the same provider PID as the first;
- the provider recalled the unique marker from its persistent session;
- message sources were respectively `codex_session_jsonl`,
  `claude_session_jsonl`, and `grok_acp`;
- timeout count was zero;
- context errors and TUI debris were zero;
- stop left no provider or bridge process alive.

An earlier attempt intentionally recorded in the evidence used the default
persisted `.agentsassemble/general` state with `--observe-gui-port`. Existing
Codex and Claude participants conflicted with the smoke's configured IDs, while
a Grok latency probe completed without a room-visible final. That run failed and
was not counted as success. The verified run used the harness's intended clean
workspace and an isolated state root; no provider was substituted and no model
was changed.

## 9. Sixty-Second Shared-Room Smoke

The same three exact providers then discussed this public topic for 60 seconds:

> 백룸에 갇힌 세 에이전트가 자원을 아끼면서 출구를 찾으려면 무엇부터 확인해야 할까?

Results:

- actual duration: 60.357 seconds;
- public conversation turns: 9 over 3 speaker cycles;
- every provider produced 3 conversation messages plus 1 control reply;
- warm cycles showed each provider received both peers' public messages;
- visible at-mention count: 0;
- unexpected extra turns: false;
- canonical turn source, bounded public diff, cursor advancement, clean output,
  and structured-source checks passed for every turn;
- conversation TTFO p50/p95: 5,382.6 / 8,904.9 ms;
- turn-complete p50/p95: 6,495.1 / 8,911.5 ms.

Pause/resume was exercised for every provider. Each remained alive while
paused, did not consume the queued message early, recorded the backlog, reused
the same provider and bridge PID after resume, answered from the queued source
event, and cleared the backlog. Each participant was then kicked; the kick was
acknowledged, the participant became `kicked`, provider and bridge processes
stopped, and restart was rejected until explicit re-add. All checks passed.

This smoke validates the existing server-assigned conversation mode. It is not
a claim that the deferred autonomous-conversation policy has been designed.

## 10. Remaining Work Outside This Milestone

- Design and review autonomous attention and the freedom not to speak.
- Decide the agent-only invite consumption boundary recorded in the plan.
- Design provider-native media understanding and delivery.
- Remove compatibility shims only after their published compatibility window
  and caller evidence permit it.
- Decide whether the observer smoke should support a named isolated state root
  directly from the CLI when a developer wants to avoid existing participant-ID
  conflicts.

These are explicit follow-ups, not incomplete ownership milestones.

## 11. Working Tree Boundary

The following untracked user-owned paths were not modified, staged, or committed:

```text
.superpowers/
docs/plan-room-hygiene-bugfixes.md
```
