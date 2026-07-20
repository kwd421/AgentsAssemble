# Package Architecture Refactor Progress Report

Status: in progress, verified package wave

Date: 2026-07-20

Branch: `codex/risuai-character-personas`

Reviewed range: `origin/codex/risuai-character-personas..305a00af`

Plan: `docs/plans/2026-07-16-package-architecture-refactor.md`

Current architecture: `docs/product/CURRENT_SYSTEM.md`

## Executive Summary

This range contains 38 small commits. It moves the retained resident-agent
implementation and its HTTP registrars out of the flat `agentsassemble/`
namespace and into the explicit legacy owner:

```text
agentsassemble/legacy/live_agent/
agentsassemble/legacy/live_agent/http/
```

The change is deliberately mechanical. It does not redesign room behavior,
provider execution, autonomous conversation, event formats, HTTP payloads, or
the frontend. Production composition and maintained tests now import the
owned package directly. The old root paths remain as explicit, metadata-tracked
compatibility exports for callers that have not migrated yet.

The resident service and HTTP route package wave is complete. The overall
package refactor is not complete. The next coherent work is the retained
meeting/lobby/review HTTP family, followed by GUI composition extraction and
the remaining large CLI and service modules.

## Why This Work Was Done

The repository still had many flat modules whose names encoded ownership only
through long prefixes such as `legacy_live_agent_*` and
`gui_legacy_live_agent_*_http`. A maintainer had to search callers and tests to
learn whether a module was current product code, compatibility code, an HTTP
adapter, or a resident-agent service.

This wave establishes an import path that answers those questions directly:

- `legacy/live_agent/` owns retained resident-agent policy and services;
- `legacy/live_agent/http/` owns the matching compatibility-only HTTP routes;
- root modules are compatibility surfaces, not production owners;
- current `web/routes/` remains free of legacy dependencies.

The move also makes later deletion measurable: a root shim can be removed only
after its documented callers and monkeypatch paths have migrated.

## What Changed

### Resident service ownership

Twenty resident service and facade slices were moved or completed under the
legacy package. They cover:

- preflight, engagement, probe, and presence;
- process control, process mutation, and process projection;
- session policy, session lifecycle, and durable session-run behavior;
- diagnostics, health, observation health, and readiness;
- roster, discovery, room/event queries, and speech;
- official replies and real-provider smoke composition.

The package now contains 43 Python files including its HTTP registrars and
package initializers. Internal imports use colocated owners where appropriate,
and production composition no longer reaches through root compatibility
modules for these responsibilities.

### Resident HTTP route ownership

Sixteen route registrars now live under `legacy/live_agent/http/`:

- engagement and room-session cleanup;
- external join brief and speech;
- official reply and reply probe;
- self-managed control and presence;
- process, discovery, preflight, and readiness;
- session-run, session mutation, read-only queries, and smoke.

Endpoints, validation, status codes, error mapping, approval gates, and
response payloads were preserved. `gui_legacy_application.py` imports these
owned registrars directly.

### Compatibility and architecture enforcement

- Every replaced root module is an explicit export list rather than a wildcard
  re-export.
- Compatibility metadata records the replacement path and keeps the root
  surface visible to the architecture gate.
- The deterministic package map reflects the new owners.
- Route ownership and React parity inventories scan packaged legacy route
  registrars as well as current routes and root compatibility modules.
- The Mafia game service was moved to `features/mafia/game.py` as a separate
  feature-owned slice; its root module remains a compatibility export.

## Important Test Finding

The first full-suite run found two failures in static route inventory tests.
The runtime routes had not disappeared. The scanners only inspected root
`gui_*_http.py` modules, so converting those modules to shims made the scanners
blind to the new owned registrars.

The fix expanded the inventory boundary to
`agentsassemble/legacy/live_agent/http/*.py`. Expected route sets and runtime
registration were left unchanged. This was recorded separately in commit
`305a00af` so the reason for the test change is reviewable.

No fallback route, duplicate registration, or special-case runtime behavior
was added to make the tests pass.

## Deliberate Differences From A Cosmetic File Move

The work did not move every `legacy_*` file at once. Dependencies were moved in
an order that let later modules import stable package owners. This produced
more commits, but each commit retained a narrow rollback and verification
boundary.

Compatibility shims were not deleted merely to reduce top-level file count.
The repository still has tests and possible external callers that depend on
those imports. Shims remain explicit and tracked until their removal gate can
be proven.

The route family was placed under `legacy/live_agent/http/`, not under current
`web/routes/`. Putting compatibility-only routes in the current package would
make the dependency graph look cleaner while obscuring their lifecycle and
allowing current code to depend on legacy implementation.

## Behavior Intentionally Unchanged

This range does not change:

- canonical `RoomStore` or `/ws?ticket=...` ownership;
- room, participant, invite, moderation, or media behavior;
- provider commands, models, reasoning controls, sessions, or output parsing;
- autonomous attention, speaker selection, semantic silence, or relay policy;
- frontend layout, copy, controls, or rendering;
- persistence schemas, event formats, HTTP routes, or payload shapes;
- secret, token, path, PID, and provider-private information boundaries.

No `claude -p`, `codex exec resume --last`, provider fallback, one-shot
provider path, or parallel room transport was introduced.

## Verification Evidence

The completed range was checked with:

```text
python3 -m unittest discover -s tests -t .
  3,815 tests passed, 74 skipped

python3 -W error::ResourceWarning -m unittest discover -s tests -t .
  3,815 tests passed, 74 skipped

npm --prefix frontend test
  22 files, 127 tests passed

npm --prefix frontend run build
  passed

python3 scripts/generate_package_map.py --check
  passed

python3 scripts/check_package_architecture.py
  passed

git diff --check
  passed
```

The Python suite prints expected argparse errors and expected cleanup-failure
diagnostics from negative-path tests; both full runs exited successfully.

No real-provider smoke was run for this range. The changes only move Python
ownership and imports and do not alter provider commands, runtime lifecycle,
WebSocket behavior, frontend behavior, or output parsing. A real-provider smoke
would be required when one of those behavioral boundaries changes.

## Scope And Size

Relative to the previously pushed branch head, this range contains:

```text
38 commits
130 files changed
9,313 insertions
7,366 deletions
```

Most insertions and deletions are paired file moves plus explicit compatibility
exports, package-map updates, architecture metadata, and ownership tests.

## Remaining Work

The overall refactor remains active. The next sequence is:

1. Move the retained meeting, lobby, official-turn, review-checkpoint, and
   related HTTP registrars into explicit legacy owners.
2. Split `gui_legacy_application.py` by composition responsibility after those
   route owners are stable.
3. Refactor the large CLI parser and service modules where responsibility,
   failure mode, or test ownership provides a real boundary.
4. Continue provider, application, and diagnostics package moves in dependency
   order.
5. Remove compatibility shims only after caller and monkeypatch inventories
   prove they are unused.
6. Refresh final architecture and verification documentation after the last
   package wave.

`PACKAGE_MAP.md` currently marks 133 modules as `planned-move`. That count is
an inventory, not 133 equally urgent mandatory edits. Stable or frozen product
areas should move only when ownership is proven and the change remains
mechanical; line count alone is not a reason to split a module.

## Known Deferred Product Work

The package refactor intentionally does not decide or implement:

- autonomous conversation and the agent's freedom not to speak;
- semantic silence, reactions, handoff, scheduled wakeup, or token budgets;
- speaker-selection and sequence-mode redesign;
- provider-native image, PDF, or audio understanding;
- browser consumption restrictions for agent-only invite links.

The last item remains a documented security follow-up in the active plan.
Documentation and UI must not claim that an agent invite is safe to consume
through the ordinary browser flow until that boundary is implemented and
verified.

## Working Tree Boundary

The following untracked paths are user-owned and were not read into, modified,
staged, or committed by this refactor:

```text
.superpowers/
docs/plan-room-hygiene-bugfixes.md
```
