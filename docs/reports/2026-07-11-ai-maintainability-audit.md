# AI Maintainability Audit

Date: 2026-07-11

## Purpose

This audit identifies code and test structures that make safe changes difficult
to discover, understand and verify for both human and AI maintainers. Line count
is used as a warning signal, not a refactoring target. A long cohesive module may
be safer than many pass-through files; a shorter module can still be harmful if
it mixes unrelated ownership and side effects.

Generated output, dependencies, `.agentsassemble` runtime data and user-owned
untracked files were excluded. The scan covered `agentsassemble`, `frontend/src`,
`tests`, and `scripts`.

## Repository Size

| Area | Files | Lines | Files >= 500 | Files >= 1,000 | Files >= 2,000 |
| --- | ---: | ---: | ---: | ---: | ---: |
| Backend Python | 154 | 81,080 | 33 | 11 | 8 |
| Frontend source | 83 | 27,502 | 8 | 4 | 3 |
| Python tests | 168 | 106,421 | 30 | 16 | 8 |
| Scripts | 1 | 328 | 0 | 0 | 0 |

Tests are almost as large as backend and frontend production source combined.
That amount of coverage is not inherently bad. The maintainability problem is
that a large share is concentrated in a few files and some tests assert source
text rather than behavior.

## Highest-Risk Production Files

### 1. `agentsassemble/gui.py` - 11,847 lines

Evidence:

- 65 AgentsAssemble imports;
- approximately 138 route/path branches;
- `_make_handler()` spans 3,569 lines;
- nested `AgentsAssembleHandler` spans 3,431 lines;
- `do_POST()` alone spans 2,516 lines;
- 487 function definitions in the module.

This is the clearest backend maintenance bottleneck. It combines HTTP server
construction, authentication, room routes, provider controls, legacy meeting
routes, tunnel behavior, process supervision, friends, Mafia and other domains.
A maintainer changing one endpoint must load unrelated route and lifecycle
context.

Recommended split, preserving URLs and behavior:

1. Keep server construction, dependency wiring and fallback response handling in
   `gui.py`.
2. Move canonical room/invite/moderation endpoints into focused route registrars.
3. Move provider credential and process routes behind their owning services.
4. Put legacy meeting, Mafia and old live-agent routes in explicitly named legacy
   route modules.
5. Replace the `do_POST()` conditional chain incrementally, one tested domain at
   a time. Do not rewrite the server framework in the same change.

### 2. `agentsassemble/cli.py` - 8,732 lines

Evidence:

- `build_parser()` spans 1,577 lines;
- 112 `add_parser()` calls;
- 70 `run_*` or handler-style definitions;
- 336 total function definitions.

The CLI owns too many command families in one parser and dispatch module.
Registration and command behavior should be split by product domain while the
top-level CLI keeps shared parser construction and dispatch.

Recommended domains: `room`, `provider`, `legacy_live_agent`, `meeting`,
`persona`, and diagnostics/smoke. Preserve command names, help text and exit
codes.

### 3. `frontend/src/App.tsx` - 2,988 lines

Evidence:

- `App()` spans 2,586 lines;
- 61 `useState` calls;
- 22 `useEffect` calls;
- 18 `useCallback` and 13 `useMemo` calls;
- 45 imports;
- hundreds of props are assembled near the render path.

`App` coordinates room directory, canonical socket state, guest admission,
friends, invites, room settings, shell navigation, provider controls and
responsive panels. The risk is not JSX length but effect ownership: unrelated
state transitions can invalidate each other and are difficult to test alone.

Recommended extraction order:

1. `useRoomDirectory()` for room list, active room and stale dock cleanup.
2. `useRoomConnection(roomId)` for ticket, reconnect and canonical snapshot.
3. `useGuestAdmission()` for join profile and guest session persistence.
4. `useRoomInvites()` and `useFriends()` for those independent lifecycles.
5. Leave top-level layout composition in `App`.

Do not move state into a global store merely to reduce line count. Extract each
hook only when its ownership and test boundary are clear.

### 4. `frontend/src/api.ts` - 2,421 lines

This is a broad client catalog spanning current and legacy endpoints. Split by
server domain and keep shared fetch, error and authentication primitives in one
small base module. The frontend API modules should mirror route ownership so a
room protocol change does not require searching an all-product client file.

### 5. `frontend/src/views/components/MemberList.tsx` - 1,741 lines

Evidence:

- `MemberDetailModal()` spans 763 lines;
- `MemberList()` spans 450 lines;
- participant rows, moderation, provider settings and Agent Session controls are
  coupled in one file.

Split the modal by human/agent detail sections while preserving one canonical
participant model and one moderation command path. Do not fork separate roster
implementations for humans and agents.

### 6. `agentsassemble/room_realtime.py` - 2,193 lines

`RoomRealtimeController` spans 1,927 lines. Individual methods are generally
smaller than the GUI handler, but the class coordinates command execution,
session lifecycle, bridge generations, routing, turn assignment, recovery and
event publication.

Keep it as coordinator and move policies or side-effect owners, not arbitrary
method groups:

- attention/routing policy;
- Agent Session lifecycle service;
- bridge report validation;
- startup reconciliation.

The upcoming autonomous-attention work is a good natural boundary for the first
extraction. It should not be added as another large block in this controller.

### 7. `agentsassemble/agent_sessions.py` - 3,513 lines

This file mixes provider runtime implementation, command execution, room turn
packet construction, provider-visible prompt formatting, media manifest
selection and legacy compatibility. `CodexAppServerRuntime` spans 677 lines and
`run_agent_session_turn_payload()` spans 304 lines.

First separate room context/media packet construction from provider process
execution. Do not combine that refactor with changing the provider-visible
contract.

### 8. `frontend/src/index.css` - 6,484 lines

One stylesheet is difficult to navigate, but CSS order and specificity make a
mechanical split risky. Split only after identifying stable layers:

- tokens/reset;
- application shell and room layout;
- messages/composer/media;
- roster/modals/settings;
- feature-specific legacy surfaces;
- responsive overrides kept adjacent to their owning feature where possible.

Use screenshot comparison across desktop and mobile after every extraction.

## Large Files That Need Classification Before Refactoring

| File | Lines | Why not split blindly |
| --- | ---: | --- |
| `live_agent_runner.py` | 2,384 | Primarily legacy resident behavior; may be retired instead of refactored |
| `live_agent_processes.py` | 2,112 | Supervisor ownership is broad but process invariants are tightly coupled |
| `live_agent_smoke.py` | 2,077 | Test harness phases can split, but result schema must stay stable |
| `live_agent_sessions.py` | 2,016 | Legacy lifecycle and current compatibility must be mapped first |
| `persona_cards.py` | 1,660 | Parsing, safety and projection are related; split only at proven boundaries |
| `room_native_cli_smoke.py` | 1,447 | Provider-specific smoke drivers are natural candidates after schema tests |
| `gui_room_http.py` | 1,165 | Already an extraction, but `register_room_routes()` is still 1,016 lines |

Legacy modules should be marked and isolated before investing in internal
cleanup. Deleting a retired path is better than making it beautifully modular.

## Test Maintainability

### Concentration

| Test file | Lines | Test methods |
| --- | ---: | ---: |
| `test_gui_server.py` | 22,491 | 383 |
| `test_cli_timeout.py` | 16,248 | 404 |
| `test_live_agent_runner.py` | 5,352 | many under one 5,248-line class |
| `test_live_agent_processes.py` | 4,487 | many under one 4,394-line class |
| `test_live_agent_sessions.py` | 4,138 | several multi-thousand-line classes |
| `test_static_ui_assets.py` | 2,093 | 52 source-oriented tests |

The two largest test files contain 787 test methods. A maintainer cannot infer
which subset protects one endpoint or command without searching a monolith.

### Low-Signal Assertions

The three static/parity test files contain approximately 1,524 `assertIn` or
`assertNotIn` calls. Some are useful security tripwires, but many verify source
spelling, filenames or JSX strings rather than user behavior. They discourage
safe extraction because moving correct code can fail the test.

Keep source assertions only for narrow forbidden-path and secret-leak rules.
Replace UI-presence and wiring assertions with Vitest component behavior or
Playwright workflows. Replace backend source inspection with command/event
contract tests.

### Recommended Test Layout

- Split `test_gui_server.py` by route domain: room, invite, moderation,
  credentials, provider lifecycle and legacy routes.
- Split `test_cli_timeout.py` by CLI command family and shared timeout policy.
- Keep common fixtures in small helper modules with no test discovery side
  effects.
- Give each production behavior one obvious targeted test command.
- Define fast canonical, frontend and legacy suites instead of requiring every
  small change to discover and run all 2,800+ tests.
- Preserve the full suite as the final shared-path gate until equivalent
  behavioral coverage is proven.

## Advisory Size Signals

These are review prompts, not automatic CI failures:

- production file >= 1,000 lines: check responsibility cohesion;
- production file >= 2,000 lines: require an ownership map or extraction plan;
- function or React component >= 200 lines: inspect mixed state and side effects;
- function or React component >= 500 lines: high-priority extraction candidate;
- test class >= 500 lines: split by behavior domain;
- source-string test: justify it as a security/compatibility tripwire.

No change should split a file solely to satisfy a number. A split is successful
only if it makes the next behavior change easier to locate and test.

## Recommended Sequence

1. Adopt the short current-system document and stop mandatory loading of large
   historical references.
2. Split the two largest test files by domain without changing assertions.
3. Replace low-signal static UI assertions incrementally with behavioral tests.
4. Extract canonical room routes from `gui.py`, then provider and legacy routes.
5. Break CLI parser registration into domain modules without changing commands.
6. Extract React lifecycle hooks from `App.tsx` and detail sections from
   `MemberList.tsx`.
7. Introduce the new attention coordinator as a focused module rather than
   expanding `RoomRealtimeController`.
8. Classify legacy live-agent modules as retained or removable before refactoring
   them.

## Immediate Decision

Do not attempt a repository-wide file split in one change. The safest first code
slice is test-file organization, because it improves targeted verification
without changing product behavior. The first production slice should then be
canonical room route extraction from `gui.py`, protected by those smaller route
tests.
