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

### Remote CI

GitHub Actions run
`https://github.com/kwd421/AgentsAssemble/actions/runs/29726198019` on commit
`70b6d8ba71479f8103487c2d65137200171d3f23` completed successfully. All seven
jobs passed: Python 3.11, Python 3.13, mandatory PostgreSQL contracts, frontend
build, frontend E2E, Ubuntu runtime contracts, and Windows runtime contracts.

The only commit after that run is the documentation-only commit that records
this result. Its workflow result cannot be embedded into the same commit
without creating another documentation commit and another workflow run; the
delivery report records the final pushed commit's status separately.

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

## 12. Entrypoint And Compatibility Refactor Completion (2026-07-23)

The follow-up plan in
`docs/plans/2026-07-22-entrypoint-compatibility-refactor.md` is complete through
its shim-readiness assessment and final verification gates. The work preserved
the canonical room protocol and provider behavior; it did not implement or
redesign the deferred autonomous-conversation policy.

### 12.1 Ownership result

The large compatibility entrypoints now retain composition and measured export
seams while workflows live under their owning packages:

| Boundary | Before | Current | Current responsibility |
| --- | ---: | ---: | --- |
| `agentsassemble/cli.py` | 5,837 | 828 | parser composition, dispatch, compatibility exports |
| `agentsassemble/gui.py` | 3,504 | 2,036 | server composition and measured compatibility patch seams |
| `application/agent_sessions.py` | 2,298 | 440-line package facade | stable exports over focused process, turn, command, queue, service, and compatibility owners |
| `persona_cards.py` | 1,660 | 75-line package facade | stable exports over models, values, rendering, storage, Risu codec, bounded assets, and import orchestration |
| `check_package_architecture.py` | 2,499 | 433 | checker logic over data-driven shim metadata |

`character_mode.py`, `config.py`, and `models.py` were inspected and retained.
Their remaining code is cohesive policy/configuration/legacy DTO behavior with
one validation boundary; splitting them only to lower line counts would scatter
the next change. Persona handling was split because model normalization, lore
rendering, JSON persistence, Risu decoding, and archive asset extraction have
different side effects and failure modes.

Current routes and services no longer own process-global invite state.
`admission.compat` owns the historical singleton, while current composition
injects repositories and the root GUI/legacy facade performs explicit
compatibility wiring. A reset-order defect discovered during the move was fixed
at its source: runtime URL state is reset before the compatibility application
is rebuilt, so the rebuilt service cannot capture a stale URL callback.

### 12.2 Compatibility retirement decision

The generated retirement report currently records:

```text
tracked shims:          281
zero-code-caller:        88
caller-blocked:         193
unexpected callers:      0
```

No shim was removed. This intentionally differs from a cosmetic root-file
cleanup: every zero-caller candidate still requires one measured compatibility
window. This report starts that window; it does not retroactively satisfy it.
Deleting those modules in the same branch would violate the published removal
gate.

### 12.3 Defects found by the final gate

The first full Python run found three failures with one cause. The legacy React
parity appendix described `POST /api/room-invite/agent-join` as both
`native attendee only` and React-wired. The actual endpoint is an exact POST
used by the Python native attendee client, and browser consumption is forbidden.
The appendix now records `exact`, no React wrapper, and `react_wired=no`.
Adding a browser wrapper would have weakened the signed invite client-scope
boundary, so no fallback route was added.

The first real smoke invocation also failed and is not counted as success. It
used `--observe-gui-port`, which deliberately reuses the persisted default
`.agentsassemble` state so a person can inspect the room later. Existing
participants with the same provider IDs caused the supplied smoke specs to be
skipped and `agent.start` returned `Unknown configured agent: codex`. The plan
requires isolated room state, so the valid run omitted the persistence/observer
option. The harness still started a real HTTP server and used its canonical
ticket-authenticated `/ws` path; only the state lifetime and fixed observer port
differed.

### 12.4 Final automated evidence

```text
python3 -m unittest discover -s tests -t .
  3,845 passed, 74 optional-environment skips

python3 -W error::ResourceWarning -m unittest discover -s tests -t .
  3,845 passed, 74 optional-environment skips

isolated UTF-8 PostgreSQL 17 + tests.run_postgres_contracts
  86 passed, 0 skipped

npm --prefix frontend test
  22 files, 128 passed

npm --prefix frontend run build
  passed

npm --prefix frontend run test:e2e
  4 Playwright scenarios passed

python3 scripts/generate_package_map.py --check
python3 scripts/check_package_architecture.py
git diff --check
  passed
```

The PostgreSQL contract run used a temporary virtual environment and UTF-8
cluster, stopped the server, and removed the temporary workspace afterward.

### 12.5 Final real-provider evidence

The successful isolated run used the actual configured provider commands and
models with the lowest requested reasoning setting:

| Provider | Requested/observed model | Transport | Same provider PID | Cleanup |
| --- | --- | --- | --- | --- |
| Codex | `gpt-5.6-luna` | PTY | yes | provider and bridge stopped |
| Grok | `grok-4.5` | ACP stdio | yes | provider and bridge stopped |
| Claude Code | `claude-sonnet-4-6` | PTY | yes | provider and bridge stopped |

The shared topic was how three agents trapped in the Backrooms should conserve
resources while finding an exit. The room produced 12 finalized public turns
over four speaker cycles in 76.851 seconds. Warm turns showed both peer actors
in every provider context; visible at-mentions and unexpected extra turns were
zero. TTFO was p50 4,619.8 ms / p95 8,176.5 ms and turn completion was p50
6,410.6 ms / p95 9,394.3 ms.

Each provider completed five total turns including its control turn, retained
the same provider and bridge PID through pause/backlog/resume, answered from the
queued source event, cleared its backlog, and passed participant-kick cleanup.
All three reported `alive_after_stop=false`; stderr byte, line, and warning
counts were zero. Message provenance was `codex_session_jsonl`, `grok_acp`, and
`claude_session_jsonl` respectively. No provider, model, reasoning level, or
transport was silently substituted.

### 12.6 Remaining work

- Wait one real compatibility window, regenerate caller evidence, and review
  the 88 zero-caller shim candidates in the documented domain order.
- Decide whether a future observer smoke should accept an explicit isolated
  persisted state root instead of sharing the normal `.agentsassemble` state.
- Design autonomous participation, semantic silence, and provider-native media
  separately; this refactor intentionally did not alter those unsettled rules.

### 12.7 Post-completion corrections

The generic Agent Session room envelope was corrected after completion review.
The room is infrastructure supplied to participants, not a moderator that
rewrites their voice. Automatic delivery now provides canonical room identity,
bounded room events and memory, supported media context, and security
boundaries without prescribing tone, language, reply length, conversational
stance, persona, or a supposedly natural way to continue.

Automatic room delivery also omits `Reply only`, `[Your turn]`, and
`Return only` instructions. Provider output classification, TUI cleanup, and
final-message extraction remain provider-adapter responsibilities. A visible
room message may still request a format or style because that request belongs
to the participant who sent it, not to hidden AgentsAssemble policy. This
correction does not decide autonomous participation or semantic silence.

Review also identified an overuse of tests that restate copy, numeric values,
constants, symbols, exports, filenames, or source strings. Those checks do not
prove that a user workflow works. Future tests require a meaningful behavioral
contract such as persisted state, security, permissions, process lifetime,
protocol compatibility, or a real user-visible workflow. Unit-test success
cannot substitute for GUI, real-provider, or integration-boundary verification.
The durable contributor rule is recorded in `AGENTS.md`.

### 12.8 Latest-envelope real-provider verification

The provider smoke was rerun after commit
`137c35608d81a0f6712d1918d94d273a3237b6a4`, so this evidence covers the
automatic room envelope after removal of the hidden speech and output-format
directives.

The first three-provider attempt failed on the first Codex turn when the
interactive process exited with return code 0 before a structured assistant
message was observed. This was recorded as a failed attempt, not success. A
one-provider reproduction using the same executable, model, PTY settings, and
neutral room packet then completed two warm turns with the same PID and clean
`codex_session_jsonl` messages. A second three-provider run reached clean Codex
and Grok room messages, then stopped because the local Claude CLI reported:

```text
Login expired · Please run /login
```

`claude auth status` independently reported `loggedIn: false`. No alternate
Claude model, provider, API, or one-shot command was substituted. The runtime
was corrected to classify both `login expired` and `please run /login` as
provider authentication failures. A fresh real Claude attempt after the fix
created only a `provider_turn_failed` error event; the login screen text was no
longer emitted as an agent `message_final`.

Before Claude was reauthenticated, the complete conversation/control evidence
used Codex and Grok without claiming a three-provider pass:

| Result | Evidence |
| --- | --- |
| Providers | Codex `gpt-5.6-luna` low/default; Grok `grok-4.5` low |
| Public conversation | 10 finalized turns, 5 speaker cycles, 62.127 seconds |
| Context | both agents saw the full bounded peer diff after warmup |
| Routing | 0 visible at-mentions, no unexpected extra turns |
| PID/session continuity | same provider PID for both agents |
| Controls | pause/backlog/resume and participant kick passed for both |
| Cleanup | both provider and bridge processes stopped; `alive_after_stop=false` |
| TTFO | p50 4,074.4 ms; p95 7,402.2 ms |
| Turn completion | p50 6,730.9 ms; p95 7,407.6 ms |
| stderr | 0 bytes, 0 lines, 0 warnings for both |

A headed browser connected to the smoke server at `127.0.0.1:8877`, selected
`#general`, and observed canonical room messages from both agents, Grok's
paragraph rendering, and the next Codex typing state while the real run was in
progress. The browser did not drive a parallel debug transport.

After the operator completed Claude's interactive login, `claude auth status`
reported `loggedIn: true` with the first-party Claude subscription. The final
three-provider run then passed:

| Result | Final evidence |
| --- | --- |
| Providers | Codex `gpt-5.6-luna`, Grok `grok-4.5`, Claude `claude-sonnet-4-6`; all low reasoning |
| Public conversation | 9 finalized turns, 3 speaker cycles, 68.973 seconds |
| Context | all three agents saw the full bounded peer diff after warmup |
| Routing | 0 visible at-mentions, no unexpected extra turns |
| PID/session continuity | same provider PID for all three agents |
| Controls | pause/backlog/resume and participant kick passed for all three |
| Cleanup | every provider and bridge stopped; `alive_after_stop=false` |
| TTFO | p50 5,367.6 ms; p95 9,506.1 ms |
| Turn completion | p50 8,085.5 ms; p95 9,626.2 ms |
| stderr | 0 bytes, 0 lines, 0 warnings for all three |

The headed browser observed the final run through the same canonical room UI,
including Codex and Grok finalized messages followed by Claude Sonnet 4.6's
live typing state. Requested and provider-reported model IDs matched for all
three providers. No fallback provider, model, transport, or one-shot mode was
used.

The full Python gate initially found two stale assertions left by the envelope
change. The prompt-budget behavior test still had enough budget for two events
after the envelope became shorter, so its fixture budget was reduced until it
again exercised the intended omitted-event cursor boundary. A package test that
asserted the removed English fallback phrase was deleted while retaining the
JSON payload assertion. No new copy, source-string, constant, or numeric-value
test was added.

Final verification after those corrections:

```text
python3 -m unittest tests.test_live_cli
  11 passed

python3 -m unittest discover -s tests -t .
  3,845 passed, 60 optional-environment skips

npm --prefix frontend test
  22 files, 128 passed

npm --prefix frontend run build
  passed

npm --prefix frontend run test:e2e
  4 Playwright scenarios passed

python3 scripts/generate_package_map.py --check
python3 scripts/check_package_architecture.py
git diff --check
  passed
```
