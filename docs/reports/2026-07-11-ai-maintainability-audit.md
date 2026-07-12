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

## Refactor Outcome

The first verified refactor pass completed across 2026-07-11 and 2026-07-12.
It preserved product behavior while moving independently owned concerns behind
explicit modules.

| Baseline hotspot | Before | After | New owner boundary |
| --- | ---: | ---: | --- |
| `agent_sessions.py` | 3,513 | 2,207 | `room_turn_context.py`, `codex_app_server_runtime.py` |
| `cli.py` | 8,732 | 7,097 | `cli_parser_common.py`, domain `cli_parser_*.py` modules |
| `frontend/src/api.ts` | 2,421 | 990 | `frontend/src/api/` domain clients and shared HTTP adapter |
| `MemberList.tsx` | 1,741 | 494 | typed member rows, diagnostics, identity and session-control components |
| `App.tsx` | 2,988 | 2,841 | guest admission and friends/DM lifecycles in focused `app/` hooks |
| `test_cli_timeout.py` | 16,248 | 45 | 15 domain suites; legacy direct command still runs all 404 tests |
| `test_gui_server.py` | 22,491 | 56 | 22 domain suites; compatibility loader runs all 393 current tests |

Additional results:

- canonical room HTTP registration is now split across four domain registrars;
- provider catalogs and DeepSeek credential GET/POST/DELETE now use a focused
  HTTP registrar while `provider_secrets.py` remains the secret owner;
- HTTP response delivery and WebSocket upgrade lifecycle are isolated from the
  large GUI request handler;
- the frontend API compatibility barrel retains the same 169 exports;
- moved Python runtime/context definitions are AST-identical to the baseline;
- compatibility imports for moved Agent Session runtime/context names are
  regression-tested, while shared provider normalization has one implementation;
- split GUI tests retain 383/383 test bodies and split CLI tests retain
  404/404, including the original platform guards; one GUI compatibility test
  was added after the split;
- the frontend TypeScript runtime-test compiler now resolves `.tsx` and
  directory `index.ts` imports and keeps output inside its temporary root;
- one real defect found during refactoring was fixed: frontend API requests and
  guest-session handling now use one shared `ApiError`, so a 401 can actually
  expire the guest session;
- remembered-profile auto-join is tested for success, persisted-session
  recovery and failure; a token-level guard now prevents failed auto-joins from
  retrying forever;
- one `useFriendsDirectory` instance now owns friend loading, mutations,
  selection, DM state and category transitions for the sidebar, directory and
  invite UI; the controlled `FriendsView` retains presentation-only form and
  search state;
- friends/DM behavior tests cover disabled guest projection, refresh/mutation
  races, serialized mutations, exact filter transitions, stale selection
  reconciliation and delete fallback selection;
- credential route tests cover local lifecycle, remote moderator rejection,
  remote HTTP rejection, forwarded HTTPS acceptance and key non-disclosure;
- Agent Session resume labels and room-channel wire normalization each have one
  shared implementation instead of drifting copies;
- `MemberList` now has a behavioral render test that follows the real parent
  wiring into the extracted detail modal and Agent Session controls.

Verification evidence:

- `python3 -m unittest discover -s tests -t .`: 2,825 passed;
- final Agent Session and CLI regression pass after deduplication: 491 passed;
- narrow compatibility-loader discovery: CLI 404 passed; GUI 393 passed;
- `npm --prefix frontend test`: 39 passed;
- `npm --prefix frontend run build`: passed;
- canonical desktop/mobile Playwright flow: passed;
- `git diff --check`: passed.

The suite still emits pre-existing resource warnings for some unclosed test
HTTP errors, SQLite connections and temporary directories. The Vite build also
reports a 689.03 kB JavaScript chunk. These are explicit follow-up debt, not
silently treated as clean.

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

## Baseline Highest-Risk Production Files

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

The current file is 11,646 lines after room, transport and provider
catalog/credential routes were extracted. It remains the highest-risk module.

Recommended split, preserving URLs and behavior:

1. Keep server construction, dependency wiring and fallback response handling in
   `gui.py`.
2. Move canonical room/invite/moderation endpoints into focused route registrars.
3. Move provider credential and process routes behind their owning services.
4. Put legacy meeting, Mafia and old live-agent routes in explicitly named legacy
   route modules.
5. Replace the `do_POST()` conditional chain incrementally, one tested domain at
   a time. Do not rewrite the server framework in the same change.

### 2. `agentsassemble/cli.py` - 8,732 baseline lines

Evidence:

- `build_parser()` spans 1,577 lines;
- 112 `add_parser()` calls;
- 70 `run_*` or handler-style definitions;
- 336 total function definitions.

Parser registration has now been split by product domain without circular
imports. The remaining 7,097-line file is still a command-execution monolith;
future extraction should follow execution side-effect ownership rather than
re-splitting the parser.

Recommended domains: `room`, `provider`, `legacy_live_agent`, `meeting`,
`persona`, and diagnostics/smoke. Preserve command names, help text and exit
codes.

### 3. `frontend/src/App.tsx` - 2,988 baseline lines

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

Guest admission and the friends/DM directory are now isolated and
behavior-tested. Recommended remaining
extraction order:

1. `useRoomDirectory()` for room list, active room and stale dock cleanup.
2. `useRoomConnection(roomId)` for ticket, reconnect and canonical snapshot.
3. `useRoomInvites()` for the invite lifecycle.
4. Leave top-level layout composition in `App`.

Do not move state into a global store merely to reduce line count. Extract each
hook only when its ownership and test boundary are clear.

### 4. `frontend/src/api.ts` - 2,421 baseline lines

Completed: domain modules now own room, history, Agent Session, invite and
moderation calls. `api.ts` remains a compatibility barrel plus legacy APIs.

### 5. `frontend/src/views/components/MemberList.tsx` - 1,741 baseline lines

Evidence:

- `MemberDetailModal()` spans 763 lines;
- `MemberList()` spans 450 lines;
- participant rows, moderation, provider settings and Agent Session controls are
  coupled in one file.

Completed: one canonical participant entry type remains, while row rendering,
diagnostics, identity settings and session controls have focused owners.

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

### 7. `agentsassemble/agent_sessions.py` - 3,513 baseline lines

This file mixes provider runtime implementation, command execution, room turn
packet construction, provider-visible prompt formatting, media manifest
selection and legacy compatibility. `CodexAppServerRuntime` spans 677 lines and
`run_agent_session_turn_payload()` spans 304 lines.

Completed: room context/media packet construction and Codex app-server process
lifecycle are separate modules with compatibility exports. Their moved ASTs
match the baseline.

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
| `gui_room_http.py` | 213 now | Domain registrars own behavior; coordinator keeps explicit historical import compatibility |

Legacy modules should be marked and isolated before investing in internal
cleanup. Deleting a retired path is better than making it beautifully modular.

## Test Maintainability

### Concentration

| Test file | Lines | Test methods |
| --- | ---: | ---: |
| `test_gui_server.py` | 56 compatibility loader | 393 current tests across domain suites |
| `test_cli_timeout.py` | 45 compatibility loader | 404 across domain suites |
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

1. Completed: adopt the short current-system orientation document.
2. Completed: split the two largest test files without changing test bodies.
3. In progress: replace low-signal static UI assertions with behavioral tests;
   the guest 401 lifecycle now has a real React hook test.
4. In progress: canonical room routes, response delivery and WebSocket upgrade
   are extracted; legacy GUI routes remain in `gui.py`.
5. Completed: break CLI parser registration into domain modules without
   changing commands.
6. In progress: guest admission, friends/DM, and member detail responsibilities
   are extracted; `App.tsx` still owns room directory, invite and shell
   lifecycles.
7. Introduce the new attention coordinator as a focused module rather than
   expanding `RoomRealtimeController`.
8. Classify legacy live-agent modules as retained or removable before refactoring
   them.

## Next Decision

Do not continue splitting solely to reduce line counts. The next safe slices are
legacy GUI route isolation and one additional `App.tsx` lifecycle hook, each in
its own behavior-preserving commit. Before that work, clean the suite's resource
warnings and replace the highest-churn source-string tests so future refactors
fail on behavior rather than file placement.
