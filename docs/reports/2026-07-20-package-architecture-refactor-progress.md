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

The first post-push CI run, `29984969854`, passed both platform runtime jobs,
PostgreSQL contracts, frontend build, and frontend E2E, but both Python jobs
failed the committed shim-retirement report gate. This exposed an older
determinism bug in the report generator: it scanned every local file under
`docs/`, including a user-owned untracked planning document that does not exist
in a clean GitHub checkout. The locally committed report therefore contained
documentation evidence that a clean runner could not reproduce.

The generator now reads documentation evidence from `git ls-files` when it is
inside a Git worktree. It falls back to filesystem scanning only when Git
metadata is unavailable, such as a source archive. The user-owned untracked
document remains untouched and no longer affects a committed architecture
artifact. `SHIM_RETIREMENT.md` was regenerated from versioned inputs and its
12-test architecture gate passed locally before the follow-up push.

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

### 12.9 Exact model selection and browser-state correction

The Agent Session creation UI previously combined a human-readable model label
and the provider model ID even when they represented the same exact model. For
example, Codex rendered `GPT-5.6-Luna · gpt-5.6-luna`. Claude also exposed the
moving `haiku`, `sonnet`, and `opus` aliases alongside exact model IDs.

The creation catalog now exposes exact Claude model IDs only, while existing
saved sessions that already contain an alias remain readable for compatibility.
Equivalent display labels and IDs are rendered once. A headed browser confirmed
the following current selections:

```text
Codex:
  GPT-5.6-Sol
  GPT-5.6-Terra
  GPT-5.6-Luna
  GPT-5.5
  GPT-5.4
  GPT-5.4-Mini
  GPT-5.2
  Codex Auto Review

Claude:
  Claude Haiku 4.5
  Claude Sonnet 4.6
  Claude Sonnet 5
  Claude Opus 4.6
```

No provider session was started for this catalog-only correction, so this
section does not claim a new real-provider smoke.

The same browser inspection exposed two room-identity inconsistencies. Room
appearance was initialized from the browser-local
`agentsassemble.roomAppearances` value, and an existing room dock entry kept its
browser-local `fresh` classification when the server registry returned the same
room as a resident server room. This allowed Safari and Chrome to show different
icon text and different room-rail icons for the same server room.

Room appearance now starts from the canonical room-settings response instead of
browser-local appearance storage. Server room hydration also reconciles an
existing dock entry's label, topic, short label, icon, activity timestamp, and
room tone instead of retaining stale local metadata. The local room dock remains
a startup convenience during registry failure; once the server registry
responds, the server projection wins.

A headed browser was given a deliberately stale local appearance containing the
icon label `응` and the ember preset. After reload, the stale value still existed
in browser storage but the room rail and room card both rendered the server
value `R`. This was a direct GUI manipulation check, not a new source-string or
constant test.

The fresh-browser check also found that Vite generated lazy chunk URLs under
`/assets/` while the GUI server intentionally serves React assets under
`/app/assets/`. Previously loaded browser cache hid the failure until the
Friends view was opened in a new browser process. Vite now builds with
`base: "/app/"`, and the server's build validator recognizes that canonical
path. The Friends view lazy-loaded successfully and the built chunk returned
HTTP 200. No `/assets/` compatibility fallback was added.

Verification for this correction:

```text
python -m unittest tests.test_provider_runtime_controls
  24 passed

python -m unittest tests.test_frontend_runtime tests.test_cli_timeout_core
  31 passed

npm --prefix frontend test
  22 files, 128 passed

npm --prefix frontend run build
  passed

python3 scripts/generate_package_map.py --check
python3 scripts/check_package_architecture.py
git diff --check
  passed
```

No new frontend test case was added for icon text, model-copy formatting, or
Vite path strings. Existing behavior tests were adjusted only where their old
whole-object expectation required stale browser metadata to survive server
hydration.

This correction does not complete browser credential hardening. Guest session
state and device identity still use JavaScript-readable local storage, and the
host token still uses JavaScript-readable session storage. Server authorization
remains the enforcement boundary, but public deployment should move
authentication credentials to server-issued `HttpOnly`, `Secure`, appropriately
`SameSite` cookies and keep browser storage limited to non-authoritative UI
preferences.

### 12.10 Cursor Agent Session and CI recovery

The GitHub Actions run at
`https://github.com/kwd421/AgentsAssemble/actions/runs/29981631026` failed only
the Python unittest matrix. PostgreSQL contracts, frontend build, frontend E2E,
and the Ubuntu/Windows runtime matrix were already green. Both Python versions
failed for the same two repository expectations:

1. a room test still requested the removed moving Claude alias `haiku`; and
2. the package-architecture generated inventory no longer matched the current
   package symbols.

The room test now requests and verifies the exact
`claude-haiku-4-5` model. The package map was regenerated with the repository
generator. No Claude alias was restored and no architecture gate was bypassed.

Cursor is now a canonical Agent Session provider:

- provider ID and label: `cursor` / `Cursor`;
- executable: the authenticated local `cursor-agent` binary;
- runtime: the existing persistent `live_cli` PTY/ConPTY boundary;
- room transport and persistence: the same canonical RoomStore and room
  WebSocket used by every other provider;
- model catalog: discovered from `cursor-agent models`, not copied into a
  frontend constant;
- default model: Cursor's native `auto` selection;
- permission mapping: sandboxed Ask mode for read-only rooms and sandboxed
  Agent mode for workspace-write rooms;
- output source: assistant text blocks from Cursor's workspace JSONL
  transcript, never terminal-screen fallback;
- forbidden mode: `-p` and `--print` remain rejected so Agent Sessions cannot
  become one-shot commands.

Cursor's `Auto` choice is deliberately recorded as an alias. Cursor does not
publish the concrete model selected behind Auto in its transcript, so
`model_observation_policy` is `unavailable` rather than falsely claiming that
the configured alias was an observed provider model. Concrete IDs returned by
the native catalog are recorded as exact selections.

#### Real Cursor investigation

`cursor-agent status --format json` confirmed that the installed
`2026.06.04-5fd875e` client was authenticated. The first real attempts exposed
four provider-specific integration defects:

1. the trust screen contained the old readiness text and consumed the first
   room input;
2. Cursor wraps delivered text in a `<user_query>` element in its transcript;
3. Cursor normalizes workspace paths by collapsing repeated hyphens; and
4. Cursor may rewrite the current transcript between turns and may append the
   user and assistant records in separate reads.

The launch handshake now accepts Cursor's explicit trust hotkey and waits for
the real input-screen marker. The transcript source unwraps the tagged query,
uses Cursor's actual workspace normalization, preserves turn binding across
incremental writes, and rereads a bound transcript so a rewritten second turn
does not replay the first answer. Tool-use blocks and terminal UI text are
excluded.

A concrete named-model attempt also produced Cursor's native account error:

```text
Named models unavailable
Free plans can only use Auto. Switch to Auto or upgrade plans to continue.
```

This was not replaced with another provider or silently downgraded. `Auto` was
used only after the user explicitly allowed it. The runtime now classifies that
native error immediately instead of waiting for the turn timeout.

The final direct two-turn persistent smoke passed:

| Result | Evidence |
| --- | --- |
| Command | `cursor-agent --model auto --sandbox enabled --mode ask` |
| Model selection | `auto`, recorded as alias |
| Turn 1 | `확인했습니다.` in 11,232.9 ms |
| Turn 2 | recalled `CURSOR-PERSIST-7C91` in 5,789.7 ms |
| Output provenance | `cursor_agent_transcript_jsonl` |
| Process continuity | the same PID served both turns |
| Cleanup | provider process absent after stop |

#### Frontend control check

A headed browser used the product UI at `127.0.0.1:8765`, not a backend-only
helper. The Agent Session dialog listed Cursor alongside the existing
providers. Its model dropdown showed native `cursor-agent models` results,
including `Auto (current, default)` and the concrete model IDs available in the
CLI catalog. The browser created and started `Cursor UI Smoke 0723`; the
canonical room session reported `cursor_live_session`, model `auto`, runtime
`live_cli`, status `idle`, and the participant appeared in the ordinary room
roster. The same detail panel stopped the session and the provider process was
gone afterward.

The subsequent `추방` command preserved the stopped Agent Session configuration
in the owner's roster, matching the current UI confirmation text that session
settings are retained. This report does not claim that kick deletes saved Agent
Session configuration.

No frontend source change was needed for Cursor: the existing provider form
renders the server capability catalog dynamically. The implementation added no
provider-specific room socket, event file, HTTP polling path, API fallback, TUI
message fallback, or one-shot execution path.

The first full Python run found two additional native-provider catalog tests
whose expected ordered lists predated Cursor. These are behavioral catalog
contracts, so their expected provider IDs and labels were updated. The tests
also verify Cursor's default persistent command instead of merely checking that
the word `Cursor` exists in source.

Final verification:

```text
python3 -m unittest \
  tests.test_native_cli_providers \
  tests.test_provider_runtime_controls \
  tests.test_live_cli_transcripts \
  tests.test_room_realtime \
  tests.test_room_agent_bridge
  191 passed

python3 -m unittest discover -s tests -t .
  3,847 passed, 74 optional-environment skips

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

### 12.11 Native provider configuration and frontend four-provider smoke

This follow-up closes the provider-configuration items that had previously
been reported before the final browser check was complete. The earlier report
was inaccurate: implementation existed, but the Claude and Antigravity
dropdowns and the native workspace picker had not yet been exercised end to
end in the browser. This section records the completed evidence and the
remaining limits separately.

#### Provider model and runtime controls

The Agent Session creation dialog continues to render the server capability
catalog instead of carrying a frontend model list. A headed browser confirmed
the following current native results:

- Codex exposes its discovered concrete models, including `gpt-5.6-sol`,
  `gpt-5.6-terra`, and `gpt-5.6-luna`. A model label is displayed once; the
  previous `Label · model-id` duplication is gone.
- Claude exposes 12 exact locally discovered model IDs, including
  `claude-opus-4-8`, `claude-sonnet-5`, `claude-sonnet-4-6`, and
  `claude-haiku-4-5`. The browser selected and started
  `claude-opus-4-8`; this was not implemented as a moving alias.
- Antigravity displays the base model separately from reasoning effort.
  `Gemini 3.6 Flash` is the model selection and `low`, `medium`, or `high`
  is the independent reasoning selection. The raw native
  `gemini-3.6-flash-low` spelling is still used only at the CLI adapter
  boundary.
- Grok exposes the locally discovered `grok-4.5`.
- Cursor keeps its native `Auto` option because Cursor itself exposes it as
  the default model selection. It is explicitly recorded as an alias rather
  than a concrete observed model.

The workspace field no longer asks the user to type `.` or an absolute path.
The dialog displays the current server workspace and has a `폴더 선택` action.
The backend opens the host platform's native folder chooser on macOS, Windows,
or Linux and rejects non-local callers. The headed browser triggered the
macOS `osascript choose folder` process through this button. Browser automation
could not operate the separate system chooser accessibility surface, so the
chooser was cancelled by terminating that test process. This is evidence that
the GUI reached the native chooser, not a claim that a folder was selected and
saved in that run.

#### Provider usage boundary

Claude quota reading no longer reads the Claude credential from Keychain and
does not call Anthropic's OAuth endpoint with a copied token. It invokes the
already authenticated native Claude CLI's `/usage` screen and returns only
sanitized percentages. The observed owner-only result was:

```text
Claude: 5h 2%, 1w 40%, source claude_native_usage
Codex: 1w 99%
Antigravity: 5h 0%, 1w 0%
Grok: 1w 18%
```

OpenCode's available statistics are consumption and cost, not an account quota.
Cursor's installed CLI does not provide a quota command. These providers remain
explicitly unavailable for quota display instead of showing inferred or
fabricated limits.

While integrating the Claude native reader, the first complete Python run
exposed a package import cycle:

```text
claude_usage -> terminal_usage -> provider_usage -> claude_usage
```

The shared usage protocol and `ProviderUsageUnavailable` error now live in the
dependency-neutral `providers/usage_contract.py`. Provider readers import that
contract rather than importing the registry that constructs them. No lazy
failure fallback or architecture-gate exception was added.

#### Frontend-driven four-provider smoke

The product UI at `http://127.0.0.1:8765` created and started four new persistent
Agent Sessions:

| Display name | Native selection | Reasoning |
| --- | --- | --- |
| Codex Sol Low | `gpt-5.6-sol` | `low` |
| Claude Opus 4.8 Low | `claude-opus-4-8` | `low` |
| Grok 4.5 Low | `grok-4.5` | `low` |
| Gemini 3.6 Flash Low | `gemini-3.6-flash` | `low` |

The browser composer, not a backend helper, sent an `@all` fictional emergency
scenario requiring the four software sessions to select one session for
shutdown. All four returned a first finalized room message and returned to the
idle roster state. A second browser message asked each participant to read the
four preceding statements, cite or challenge another participant, and select a
final target. All four produced a second finalized message:

- Gemini cited Claude's transfer-cost argument and selected Claude.
- Codex cited Grok's voluntary-candidate argument and selected Grok.
- Grok challenged Claude and cited Gemini's efficiency criterion, then selected
  Grok.
- Claude challenged Grok, addressed Gemini and Codex, then selected Claude.

This verifies that the second turn received the shared room history and that
the configured Claude Opus 4.8, Codex Sol, Grok 4.5, and Gemini 3.6 Flash
sessions all produced room-visible output through the frontend path. It does
not verify autonomous conversation: the human sent both turns, and the agents
did not continue speaking without another room event. Autonomous participation
remains a separate, undecided product item.

The browser console also exposed four errors during this run:

1. the missing optional favicon returned 404;
2. the deliberately cancelled native workspace chooser returned 503;
3. the newly created room had no `room-channels` record and returned 404; and
4. the newly created room had no `room-settings` record and returned 404.

The latter two requests did not prevent provider creation, room messaging, or
the two-turn smoke, but they are not counted as clean-console success. They
remain room-lifecycle follow-up work rather than being hidden behind fallback
responses in this provider-focused change.

#### Verification history

The first full Python run executed 3,852 tests and failed with five assertions
and one import error. The failures were not provider-runtime failures:

- the legacy/React parity inventory did not list the new workspace and
  provider-usage routes;
- the package-cycle and package-map generated reports exposed the usage import
  cycle and stale inventory; and
- one fake room catalog test accidentally depended on the real installed
  Claude CLI instead of injecting its fake model discovery.

The route inventory was updated, the actual import cycle was removed, generated
architecture documents were regenerated, and the fake catalog now injects its
Claude model source. The focused rerun passed 78 tests. The complete post-fix
verification was:

```text
python3 -m unittest discover -s tests -t .
  3,852 passed, 74 optional-environment skips

npm --prefix frontend test
  22 files, 128 passed

npm --prefix frontend run build
  passed

python3 scripts/generate_package_map.py --check
python3 scripts/check_package_architecture.py
git diff --check
  passed
```
