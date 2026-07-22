# Entrypoint And Compatibility Refactor

Status: approved, in progress

Date: 2026-07-22

Branch: `codex/risuai-character-personas`

Starting commit: `647864b5079ecc34cc70ec0627368244a4e9d007`

Source of truth: `docs/product/CURRENT_SYSTEM.md`

Review input: `/Users/seinel/Downloads/review.md` (local review artifact, not
committed)

## Goal

Finish the maintainability work left after the package-ownership migration.
The current domain owners are correct, but compatibility shims and three large
entrypoints still make safe changes hard to locate and review.

The work proceeds in this order:

1. close the two public correctness boundaries identified by review;
2. make compatibility retirement measurable;
3. make `cli.py` a stable parser/dispatch entrypoint;
4. make `gui.py` a stable server entrypoint;
5. split Agent Session application responsibilities;
6. isolate current code from compatibility state;
7. prepare evidence-based shim retirement;
8. run the complete automated and real-provider verification gates.

## Starting Evidence

At the starting commit:

```text
agentsassemble/cli.py                         5,837 lines
agentsassemble/gui.py                         3,504 lines
agentsassemble/application/agent_sessions.py  2,290 lines
scripts/check_package_architecture.py          2,499 lines

Python modules                                 692
root agentsassemble modules                    303
compatibility modules                          295
import cycles                                    0
```

The previous package report records 3,834 Python tests, 86 zero-skip
PostgreSQL contracts, 127 frontend tests, four Playwright scenarios, and a
real Codex/Claude/Grok room smoke. Those values are a regression baseline, not
hardcoded future test counts.

## Non-Goals

This refactor does not decide or redesign:

- autonomous participation or semantic silence;
- speaker selection, reactions, handoff, defer, scheduled wakeup, token
  budgets, or pair cooldown;
- sequence/continuous/ambient product semantics;
- provider-native image, PDF, audio, or video understanding;
- login/account identity;
- LISTEN/NOTIFY, Redis, Kafka, WebRTC, or voice;
- provider model, reasoning, or approval behavior.

No one-shot provider path, `claude -p`, `codex exec resume --last`, silent
provider/model fallback, or second room authority may be introduced.

## Safety And Commit Rules

- Preserve CLI command names, flags, defaults, stdout/stderr copy, timeouts,
  and exit codes unless a separately identified correctness defect requires a
  behavior fix.
- Preserve HTTP routes, CORS, WebSocket protocol, event schemas, browser copy,
  persistence schemas, and public compatibility imports during mechanical
  moves.
- Make behavior fixes and mechanical moves separate commits.
- Run the cheapest reliable targeted checks after every move and complete
  gates at phase boundaries.
- A newly discovered P0/P1 defect pauses the move. Fix its root cause and add a
  regression test in a separate commit. Record lower-priority defects without
  widening the current commit.
- Do not modify or commit the pre-existing untracked `.superpowers/` directory
  or `docs/plan-room-hygiene-bugfixes.md`.

## Phase 0: Public Correctness Boundaries

### 0.1 Agent-only invite consumption

Enforce the invite's signed client scope before any consume/admission mutation:

```text
browser admission + browser/human invite -> allowed
native attendee + agent_bridge invite     -> allowed
browser admission + agent_bridge invite   -> non-consuming rejection
native attendee + browser/human invite    -> rejection
```

Opening an agent invite in a browser returns a safe explanation and does not
change nonce use count, workflow state, identity, membership, participant, or
session state. The native attendee uses an explicit admission path and the
provider kind remains bound to the signed invite.

Acceptance evidence:

- mismatch is rejected before `consume` or workflow creation;
- a browser rejection leaves a one-use agent invite usable by the attendee;
- attendee mismatch cannot create a human or guest participant;
- existing human invite and stable-rejoin behavior remains unchanged.

### 0.2 Strict current repository injection

Current Agent Session services must receive a `RoomRepository`. They must not
construct `RoomStore` when a caller omits the repository. Explicit local
compatibility wrappers may construct `RoomStore` and must be named and tested
as local compatibility paths.

Acceptance evidence:

- hosted/current calls cannot create a second SQLite authority;
- local compatibility behavior remains available explicitly;
- current GUI composition injects its selected repository;
- PostgreSQL contracts pass without fallback or skip.

## Phase 1: Compatibility Retirement Infrastructure

Move compatibility metadata from the architecture checker's large Python
dictionary to `docs/product/compatibility_shims.toml`. Keep replacement,
introduction wave, removal condition, allowed callers, and export policy as
data.

The checker calculates actual production imports, test imports, monkeypatch
paths, and documentation references. It rejects new current-production root
shim imports and generates `docs/product/SHIM_RETIREMENT.md` with zero-caller
and blocked entries.

Behavior tests migrate to owned imports. A small dedicated compatibility suite
keeps the historical paths under contract.

No shim is deleted merely to lower a count. Removal requires zero measured
callers and the recorded compatibility window.

## Phase 2: Thin `cli.py`

Extend the existing `agentsassemble/application/cli/` and
`agentsassemble/legacy/live_agent/cli/` owners instead of creating parallel
layers.

Move one command family at a time:

1. shared formatting and HTTP client behavior;
2. current core and provider diagnostics;
3. room and Agent Session commands;
4. migration commands;
5. retained live-agent command execution and process helpers;
6. retained meeting/session execution.

Root `cli.py` finishes with parser composition, dispatch, `main()`, and
temporary compatibility exports. Completion is based on ownership, not a line
target: root must not implement command workflows or provider/process logic.

Each wave preserves parser/help snapshots, golden output, error text, timeout,
and exit-code behavior.

## Phase 3: Thin `gui.py`

Use the following ownership split:

```text
application/gui_runtime.py  service composition and lifecycle
web/gui_server.py           HTTP handler, bind, CORS, WS upgrade, static dispatch
web/routes/                 current route registration
legacy/gui_hooks.py         retained monkeypatch/hook composition
legacy/http/sse_transport.py retained SSE compatibility loops
```

This intentionally differs from placing the HTTP server under `application`:
transport belongs to `web`, while application owns service lifetime.

Root `gui.py` finishes with `serve_gui` and explicitly documented temporary
patch seams. Route inventory, HTTP behavior, public-invite CORS, WebSocket,
static delivery, and Playwright behavior must remain exact.

## Phase 4: Agent Session Application Split

Convert `agentsassemble.application.agent_sessions` from one file to a package
without changing the import path:

```text
application/agent_sessions/
  __init__.py
  service.py
  process.py
  turns.py
  commands.py
  auto_queue.py
  compatibility.py

diagnostics/codex_app_server_smoke.py
```

Responsibilities:

- `service.py`: create/resume application flow with required repository;
- `process.py`: provider process start/resume plan and owned lifecycle;
- `turns.py`: bounded packet execution and result projection;
- `commands.py`: canonical command adapters;
- `auto_queue.py`: queue and worker lifetime;
- `compatibility.py`: explicit local/legacy wrappers only;
- diagnostics: real-provider smoke code, not production orchestration.

Reduce underscore/private exports from the root compatibility module. Run real
provider tests because this phase touches turn and process boundaries.

## Phase 5: Compatibility State Isolation

Move process-global invite repository/service/runtime state out of current
`admission.invite` into `admission.compat`. Current routes and services may not
import the compatibility facade. Historical root `room_invite.py` remains the
temporary owner-facing compatibility export.

After entrypoints and compatibility state are stable, inspect
`character_mode`, `config`, `models`, and `persona_cards` by caller and
contract. Split only proven current application/provider, persona, and retained
meeting responsibilities. Do not move code based only on names.

## Phase 6: Shim Retirement Readiness

Generate an evidence-backed domain order:

1. persistence;
2. provider utilities;
3. web routes and transport;
4. admission and identity;
5. room and application;
6. retained meeting and live-agent paths.

The current branch may remove only entries whose measured callers are zero and
whose recorded compatibility window permits deletion. Other entries remain
with a concrete blocker and target release rather than being silently retained.

## Verification Gates

After relevant phases:

```text
python3 -m unittest <targeted modules>
python3 scripts/generate_package_map.py --check
python3 scripts/check_package_architecture.py
git diff --check
```

Final automated gate:

```text
python3 -m unittest discover -s tests -t .
python3 -W error::ResourceWarning -m unittest discover -s tests -t .
python3 -m tests.run_postgres_contracts        # isolated configured PostgreSQL, zero skip
npm --prefix frontend test
npm --prefix frontend run build
npm --prefix frontend run test:e2e
python3 scripts/generate_package_map.py --check
python3 scripts/check_package_architecture.py
git diff --check
```

Final real smoke uses an isolated room state and workspace through the actual
React/canonical WebSocket path:

```text
Codex gpt-5.6-luna low
Claude claude-sonnet-4-6 low
Grok grok-4.5 low
```

It verifies two warm turns and a 60-second shared-room conversation, same
PID/provider session, private-memory continuity, pause/backlog/resume, browser
streaming, no duplicate finals, no fallback, no TUI debris, no secret/path
leak, and zero orphan provider or bridge processes.

## Final Report

Update `docs/reports/2026-07-20-package-architecture-refactor-progress.md` or
add a dated successor with:

- commit-by-commit ownership changes;
- intentional differences from this plan;
- defects found and their root-cause fixes;
- remaining compatibility blockers;
- exact automated and real-smoke evidence;
- explicit unverified or environment-blocked checks.
