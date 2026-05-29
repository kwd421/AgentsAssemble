# Legacy React Parity Matrix

## Purpose And Non-Goals

This matrix tracks the evidence required before a future slice may make the
React/Vite frontend the default entry point for the local GUI room. The Python
GUI may serve the built React app at `/app/` as an opt-in preview route, but
that route is not the default flip.

Filled rows are necessary, but filled rows are not sufficient for defaulting React.
A route flip still needs an explicit product/operator decision, fresh
verification, and the legacy fallback at `/legacy/` to remain reachable.

This is not a design document, not a route-flip approval, and not a request to
build every React feature in one pass.

## Default-Flip Preconditions

Every precondition must be proven by current evidence before `/` can move away
from the vanilla console:

- API/SSE parity is verified for the operator flows the React UI claims to own.
- Room-event contracts are stable for lobby, side-chat, live meeting, cursor,
  attachment, and lifecycle reads.
- The legacy fallback at `/legacy/` and `/legacy/static/*` is reachable,
  isolated, and tested.
- The React preview route at `/app/` is reachable only when `frontend/dist`
  exists, rejects traversal, rewrites Vite `/assets/*` references under
  `/app/assets/*`, and reports static availability through `frontend-info`.
- `frontend-info` still reports `is_default_entry_point: false` until the
  future default flip is explicitly approved.
- Play Mode, Work Mode, official records, and provider startup approval remain
  separated on both surfaces.

## Surface Inventory

Status values:

- `verified`: covered by a named current test or command.
- `partial`: represented in code or docs, but not enough to approve a default
  route flip.
- `unverified`: known requirement with no current proof.

| Surface | Vanilla path/file | React equivalent | Status | Evidence | Notes |
| --- | --- | --- | --- | --- | --- |
| Default entry | `/` | Future React build | verified for vanilla only | `tests/test_gui_server.py::test_legacy_console_namespace_serves_vanilla_console_without_changing_default_routes` | `/` still serves vanilla. |
| Legacy fallback | `/legacy/` | Not applicable | verified | `tests/test_gui_server.py::test_legacy_console_namespace_serves_vanilla_console_without_changing_default_routes` | Required before any route flip. |
| Static assets | `/static/*` | `/legacy/static/*`, `/app/*`, Vite dev assets | verified for vanilla fallback and React preview serving, partial for React parity | `tests/test_static_ui_assets.py`, `tests/test_gui_server.py::test_react_app_preview_route_serves_dist_without_changing_default_routes`, `npm run build` when React changes | `/app/` serves ignored build output only when `frontend/dist` exists; `/` is still vanilla. |
| Vite dev surface | Not applicable | `http://127.0.0.1:5173`, `/app/` built preview | partial | `frontend/README.md`, `frontend/vite.config.ts`, `python3 -m agentsassemble.cli frontend-info --json` | Dev proxy and built preview only; neither is the default entry point. |
| Meeting list | `/api/meetings` | `fetchMeetings()` | partial | `frontend/src/api.ts`, `tests/test_static_ui_assets.py` | Needs browser parity proof before flip. |
| Meeting payload | `/api/meetings/<meeting-id>` | `fetchMeeting()` | partial | `frontend/src/api.ts` | Full archive rendering parity is not proven. |
| Meeting lifecycle | `/api/meetings/<meeting-id>/lifecycle` | `fetchMeetingLifecycle()` | partial | `tests/test_static_ui_assets.py::test_react_live_tab_surfaces_meeting_lifecycle_projection`, `tests/test_static_ui_assets.py::test_vanilla_gui_surfaces_lifecycle_next_action_on_core_tabs`, `tests/test_static_meeting_views_runtime.py` | Both surfaces have lifecycle labels/next-action copy; live browser parity remains separate. |
| Board current step | `/api/meetings/<meeting-id>/lifecycle` | `BoardView` via `summarizeBoardLifecycle()` | partial | `tests/test_frontend_board_lifecycle.py`, `tests/test_static_ui_assets.py::test_react_board_uses_lifecycle_current_step_instead_of_artificial_rounds`, `tests/test_static_ui_assets.py::test_vanilla_gui_surfaces_lifecycle_next_action_on_core_tabs` | Board uses lifecycle current step, next action, role admission, and permission counts instead of artificial debate rounds; vanilla core tabs now show the same compact next action. |
| Lobby events | `/api/lobby` | `fetchLobby()`, `postLobbyMessage()` | partial | `frontend/src/api.ts`, `tests/test_static_ui_assets.py::test_react_lobby_composer_uploads_attachments_then_posts_lobby` | Includes informal room history only. |
| Lobby composer | Vanilla lobby composer | `LobbyComposer` | partial | `tests/test_static_ui_assets.py::test_react_lobby_composer_uploads_attachments_then_posts_lobby` | React can post text and attachment refs; browser parity proof remains separate. |
| Lobby external participation | Join brief and LAN invite CLI surfaces | `LobbyView` read-only cards | partial | `tests/test_static_ui_assets.py::test_react_lobby_external_participation_renders_cli_only_cards_without_interactive_controls`, `tests/test_static_ui_assets.py::test_react_lobby_external_participation_uses_safe_command_skeletons_with_env_secret_refs`, `tests/test_static_ui_assets.py::test_react_lobby_external_participation_states_provider_startup_and_token_boundaries`, `tests/test_static_ui_assets.py::test_react_lobby_external_participation_has_no_unsafe_actions_or_token_io` | CLI-only read-only cards; no token generation, no provider start, no fetch/POST/SSE; safe command skeletons only. |
| Side chat | `/api/side-chat` | Not fully surfaced | unverified | `agentsassemble/gui.py` | Must stay separate from lobby and official records. |
| Live agents | `/api/live-agents` | `fetchLiveAgents()` | partial | `tests/test_static_ui_assets.py::test_react_lobby_preserves_agent_owned_room_evidence` | Host approval and context labels are represented. |
| Flow status | `/api/live-agent-flow` | `fetchFlow()` | partial | `frontend/src/api.ts` | Play Mode only. |
| Release health | `/api/release-health` | `fetchReleaseHealth()` | partial | `tests/test_static_ui_assets.py::test_react_admin_surfaces_release_health_catalog_as_cli_only`, `tests/test_static_ui_assets.py::test_react_admin_release_health_groups_default_queue_and_opt_in_with_safe_selectors` | React groups the read-only default proof queue and opt-in benchmark selector; GUI must not start checks. |
| Local resources | `/api/local-resources` | `fetchLocalResources()`, `AdminPanel` | partial | `tests/test_static_ui_assets.py::test_react_admin_local_resources_renders_safe_process_observability`, `tests/test_frontend_local_resource_labels_runtime.py`, `tests/test_local_resources.py`, `tests/test_gui_server.py::test_local_resources_endpoint_returns_sanitized_read_only_snapshot` | React shows read-only sanitized local resource observability, including displayed-set CPU/RSS totals, 1/5/15m load, PPID, role legend, and humanized attention labels. Browser parity proof remains separate before defaulting. |
| Attachment downloads | `/api/attachments/<id>` | Link or preview from event metadata | partial | `tests/test_gui_server.py::test_attachment_upload_sanitizes_and_downloads_image`, `tests/test_static_ui_assets.py::test_react_lobby_and_live_render_attachment_metadata`, `tests/test_static_ui_assets.py::test_react_lobby_composer_uploads_attachments_then_posts_lobby` | React reads event metadata and can upload attachment refs through the existing lobby composer contract; browser parity proof remains separate. |
| Lobby SSE | `/api/events/lobby` | `subscribeLobby()` | partial | `frontend/src/api.ts`, `tests/test_gui_server.py::test_lobby_sse_keeps_connection_open_with_heartbeat` | Browser delivery smoothness needs separate proof. |
| Side-chat SSE | `/api/events/side-chat` | Not fully surfaced | unverified | `agentsassemble/gui.py` | Must not collapse into lobby. |
| Meeting SSE | `/api/meetings/<meeting-id>/events` | Not fully surfaced | unverified | `agentsassemble/gui.py` | Required before Live defaulting. |
| Flow start/stop mutation | `/api/live-agent-flow/start`, `/api/live-agent-flow/stop` | `startFlow()`, `stopFlow()` | partial | `frontend/src/api.ts` | Does not start provider CLIs. |
| Full REST/SSE inventory | `agentsassemble/gui.py` | `frontend/src/api.ts` | unverified | Manual diff required | The matrix must not silently drift from `gui.py`. |

## Room-Event Contract Signals

React defaulting must preserve these room-event contracts:

- Cursor fields: `last_observed_event_id`, `last_observed_live_event_id`, and
  reply timestamps stay visible without exposing provider-private state.
- Attachments are referenced by id, preview URL, download URL, safe filename,
  size, content type, and image flag. Raw bytes, base64 payloads, and local
  absolute paths must not enter lobby events.
- Lobby, side chat, Play Mode game records, official live events, transcript
  artifacts, decisions, and shared memory stay separate unless a future explicit
  promote action is implemented.
- Provider startup approval remains explicit; UI parity does not grant provider
  execution permission.
- Release-health and room-benchmark catalog visibility is read-only; React may
  show opt-in benchmark evidence but must not start checks or benchmarks.

## Legacy Fallback Status

The legacy console namespace is the fallback for future React work:

- `/legacy/` serves the same vanilla HTML as `/`.
- `/legacy/static/*` serves the same guarded static files as `/static/*`.
- `/legacy/static/../...` traversal attempts are rejected by the same static
  path guard.
- The default entry point is unchanged until the future flip is approved.

The React preview namespace is the same-backend opt-in route:

- `/app/` serves `frontend/dist/index.html` when the ignored build output
  exists.
- `/app/assets/*` serves files from `frontend/dist/assets/*` through the same
  root-resolution guard.
- `/app/` returns a clear build hint instead of crashing when `frontend/dist`
  is absent.
- `/app/` rewrites Vite `/assets/*` references to `/app/assets/*`, so the
  backend route does not need to expose root-level `/assets/*`.
- `frontend-info` reports `react_app_url`, `app_static_available`, and the
  individual index/assets checks.

Evidence:

- `tests/test_gui_server.py::test_legacy_console_namespace_serves_vanilla_console_without_changing_default_routes`
- `tests/test_gui_server.py::test_react_app_preview_route_serves_dist_without_changing_default_routes`
- `tests/test_gui_server.py::test_react_app_preview_route_reports_missing_dist_without_crashing`
- `python3 -m agentsassemble.cli frontend-info --json`

## Verification Index

Use these checks to support parity rows:

| Check | Supports |
| --- | --- |
| `python3 -m unittest tests.test_docs_architecture -v` | Matrix existence, cross-references, opt-in boundary. |
| `python3 -m unittest tests.test_cli_timeout -v` | `frontend-info` contract, `/app/` preview metadata, and `is_default_entry_point` boundary. |
| `python3 -m unittest tests.test_static_ui_assets -v` | Static asset contracts and React source evidence labels. |
| `python3 -m unittest tests.test_gui_server -v` | Python GUI routes, legacy fallback, REST/SSE safety. |
| `cd frontend && npm run build` | React build health when React source changes. |
| `python3 -m compileall -q agentsassemble` | Python syntax after CLI/server edits. |
| `git diff --check` | Whitespace and patch hygiene. |

## Explicit Non-Goals

- No route flip in this slice.
- No default-serving React from `/`.
- No React component, Tailwind, Vite, or build pipeline change.
- No new REST or SSE endpoint.
- No default-entry-point change.
- No claim that current partial rows prove enough to default React.
