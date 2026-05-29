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
| Meeting payload | `/api/meetings/<meeting-id>` | `fetchMeetingDetail()` | partial | `frontend/src/api.ts` | Full archive rendering parity is not proven. |
| Meeting lifecycle | `/api/meetings/<meeting-id>/lifecycle` | `fetchMeetingLifecycle()` | partial | `tests/test_static_ui_assets.py::test_react_live_tab_surfaces_meeting_lifecycle_projection`, `tests/test_static_ui_assets.py::test_react_lobby_surfaces_compact_meeting_lifecycle_banner`, `tests/test_static_ui_assets.py::test_react_archive_surfaces_compact_meeting_lifecycle_banner`, `tests/test_static_ui_assets.py::test_vanilla_gui_surfaces_lifecycle_next_action_on_core_tabs`, `tests/test_static_meeting_views_runtime.py` | React Lobby and Archive now surface compact lifecycle next-action evidence; React Live keeps its lifecycle panel, Board keeps its current-step summary, and live browser parity remains separate. |
| Board current step | `/api/meetings/<meeting-id>/lifecycle` | `BoardView` via `summarizeBoardLifecycle()` | partial | `tests/test_frontend_board_lifecycle.py`, `tests/test_static_ui_assets.py::test_react_board_uses_lifecycle_current_step_instead_of_artificial_rounds`, `tests/test_static_ui_assets.py::test_vanilla_gui_surfaces_lifecycle_next_action_on_core_tabs` | Board uses lifecycle current step, next action, role admission, and permission counts instead of artificial debate rounds; vanilla core tabs now show the same compact next action. |
| Lobby events | `/api/lobby` | `fetchLobby()`, `postLobbyMessage()` | partial | `frontend/src/api.ts`, `tests/test_static_ui_assets.py::test_react_lobby_composer_uploads_attachments_then_posts_lobby` | Includes informal room history only. |
| Lobby composer | Vanilla lobby composer | `LobbyComposer` | partial | `tests/test_static_ui_assets.py::test_react_lobby_composer_uploads_attachments_then_posts_lobby` | React can post text and attachment refs; browser parity proof remains separate. |
| Lobby external participation | Join brief and LAN invite CLI surfaces | `LobbyView` collapsed CLI disclosure | partial | `tests/test_static_ui_assets.py::test_react_lobby_external_participation_collapses_cli_only_cards_by_default`, `tests/test_static_ui_assets.py::test_react_lobby_external_participation_uses_safe_command_skeletons_with_env_secret_refs`, `tests/test_static_ui_assets.py::test_react_lobby_external_participation_states_provider_startup_and_token_boundaries`, `tests/test_static_ui_assets.py::test_react_lobby_external_participation_has_no_unsafe_actions_or_token_io` | CLI-only details are collapsed by default; no token generation, no provider start, no fetch/POST/SSE; safe command skeletons only. |
| Side chat | `/api/side-chat` | `fetchSideChat()`, `postSideChatMessage()`, `SideChatPanel` | partial | `tests/test_frontend_side_chat_runtime.py`, `tests/test_static_ui_assets.py::test_react_side_chat_uses_separate_room_contract`, `cd frontend && npm run build` | React surfaces a separate unofficial side-chat panel; browser parity proof remains separate before defaulting. |
| Live agents | `/api/live-agents` | `FlowResponse.agents` via `fetchLiveAgentFlow()` | partial | `tests/test_static_ui_assets.py::test_react_lobby_preserves_agent_owned_room_evidence`, `frontend/src/api.ts` | Host approval and context labels are represented through the Play Mode flow payload; direct `/api/live-agents` parity remains vanilla-only. |
| Flow status | `/api/live-agent-flow` | `fetchLiveAgentFlow()` | partial | `frontend/src/api.ts` | Play Mode only. |
| Release health | `/api/release-health` | `fetchReleaseHealth()` | partial | `tests/test_static_ui_assets.py::test_react_admin_surfaces_release_health_catalog_as_cli_only`, `tests/test_static_ui_assets.py::test_react_admin_release_health_groups_default_queue_and_opt_in_with_safe_selectors` | React groups the read-only default proof queue and opt-in benchmark selector; GUI must not start checks. |
| Local resources | `/api/local-resources` | `fetchLocalResources()`, `AdminPanel` | partial | `tests/test_static_ui_assets.py::test_react_admin_local_resources_renders_safe_process_observability`, `tests/test_frontend_local_resource_labels_runtime.py`, `tests/test_local_resources.py`, `tests/test_gui_server.py::test_local_resources_endpoint_returns_sanitized_read_only_snapshot` | React shows read-only sanitized local resource observability, including displayed-set CPU/RSS totals, 1/5/15m load, PPID, role legend, and humanized attention labels. Browser parity proof remains separate before defaulting. |
| Attachment downloads | `/api/attachments/<id>` | Link or preview from event metadata | partial | `tests/test_gui_server.py::test_attachment_upload_sanitizes_and_downloads_image`, `tests/test_static_ui_assets.py::test_react_lobby_and_live_render_attachment_metadata`, `tests/test_static_ui_assets.py::test_react_lobby_composer_uploads_attachments_then_posts_lobby` | React reads event metadata and can upload attachment refs through the existing lobby composer contract; browser parity proof remains separate. |
| Lobby SSE | `/api/events/lobby` | `subscribeLobby()`, `parseLobbyStreamData()`, `mergeLobbyEvents()`, `mergeLobbyEventsByCreatedAt()` | partial | `tests/test_frontend_lobby_sse_runtime.py`, `tests/test_static_ui_assets.py::test_react_lobby_sse_uses_shared_parser_and_merge_helpers`, `tests/test_gui_server.py::test_lobby_sse_keeps_connection_open_with_heartbeat` | React parses lobby SSE snapshots/single events, rejects side-chat payloads, and uses shared id-keyed merge helpers while Live keeps chronological rendering; browser delivery smoothness still needs separate proof. |
| Side-chat SSE | `/api/events/side-chat` | `subscribeSideChat()` | partial | `tests/test_frontend_side_chat_runtime.py`, `tests/test_static_ui_assets.py::test_react_side_chat_uses_separate_room_contract`, `cd frontend && npm run build` | React subscribes to `side_chat` events through the side-chat stream and ignores lobby-stream payloads. |
| Meeting SSE | `/api/meetings/<meeting-id>/events` | `subscribeMeetingEvents()`, official live-event fallback in `LiveView` | verified | `tests/test_frontend_meeting_stream_runtime.py`, `tests/test_frontend_live_timeline_state.py`, `tests/test_static_ui_assets.py::test_react_live_tab_subscribes_to_meeting_sse_without_route_flip_or_provider_start`, `cd frontend && npm run build` | React Live subscribes to meeting stream snapshots/deltas, filters stale meeting/flow events, preserves stable identical-event refreshes and pinned-to-latest intent at the state level, and uses official live events only when Play Mode flow events are absent. Browser-rendered smoothness remains separate before defaulting. |
| Flow start/stop mutation | `/api/live-agent-flow/start`, `/api/live-agent-flow/stop` | `startFlow()`, `stopFlow()` | partial | `frontend/src/api.ts` | Does not start provider CLIs. |
| Full REST/SSE inventory | `agentsassemble/gui.py` | `frontend/src/api.ts` | verified | `tests/test_legacy_react_parity_inventory.py`, `API/SSE Inventory Appendix` | The matrix is guarded against silent route and React-wrapper drift from `gui.py` and `frontend/src/api.ts`. |

## API/SSE Inventory Appendix

This appendix records the current `/api/...` surface served by `agentsassemble/gui.py`.
Rows marked `React wired: no` are intentionally documented as vanilla/admin/operator
surface rather than silently counted as React parity.

| Path | Method | Handler form | React wrapper | React wired | Notes |
| --- | --- | --- | --- | --- | --- |
| `/api/attachments` | POST | exact | `uploadLobbyAttachment()` | yes | React composer uploads attachments before posting lobby messages. |
| `/api/attachments/{attachment_id}` | GET | prefix | `-` | no | Vanilla/admin/operator endpoint; not wrapped by React preview yet. |
| `/api/codex-sessions` | GET | exact | `-` | no | Vanilla/admin/operator endpoint; not wrapped by React preview yet. |
| `/api/codex-sessions/invite` | POST | exact | `-` | no | Vanilla/admin/operator endpoint; not wrapped by React preview yet. |
| `/api/codex-sessions/join` | POST | exact | `-` | no | Vanilla/admin/operator endpoint; not wrapped by React preview yet. |
| `/api/demo` | POST | exact | `-` | no | Vanilla/admin/operator endpoint; not wrapped by React preview yet. |
| `/api/events/lobby` | GET | sse | `subscribeLobby()` | yes | React lobby subscribes to lobby stream. |
| `/api/events/side-chat` | GET | sse | `subscribeSideChat()` | yes | React side chat subscribes to separate stream. |
| `/api/live-agent-discovery` | POST | exact | `-` | no | Vanilla/admin/operator endpoint; not wrapped by React preview yet. |
| `/api/live-agent-flow` | GET | exact | `fetchLiveAgentFlow()` | yes | React Play Mode status surface. |
| `/api/live-agent-flow/start` | POST | exact | `startFlow()` | yes | React Play Mode start control; does not start providers. |
| `/api/live-agent-flow/stop` | POST | exact | `stopFlow()` | yes | React Play Mode stop control. |
| `/api/live-agent-health` | GET | exact | `fetchHealth()` | yes | React admin/status observability. |
| `/api/live-agent-join-brief` | POST | exact | `-` | no | Vanilla/admin/operator endpoint; not wrapped by React preview yet. |
| `/api/live-agent-meetings/start` | POST | exact | `-` | no | Vanilla/admin/operator endpoint; not wrapped by React preview yet. |
| `/api/live-agent-official-round-smoke` | POST | exact | `-` | no | Vanilla/admin/operator endpoint; not wrapped by React preview yet. |
| `/api/live-agent-operations` | GET | exact | `-` | no | Vanilla/admin/operator endpoint; not wrapped by React preview yet. |
| `/api/live-agent-preflight` | POST | exact | `-` | no | Vanilla/admin/operator endpoint; not wrapped by React preview yet. |
| `/api/live-agent-process-events` | GET | exact | `-` | no | Vanilla/admin/operator endpoint; not wrapped by React preview yet. |
| `/api/live-agent-processes` | GET | exact | `-` | no | Vanilla/admin/operator endpoint; not wrapped by React preview yet. |
| `/api/live-agent-processes/start` | POST | exact | `-` | no | Vanilla/admin/operator endpoint; not wrapped by React preview yet. |
| `/api/live-agent-processes/stop-running` | POST | exact | `-` | no | Vanilla/admin/operator endpoint; not wrapped by React preview yet. |
| `/api/live-agent-processes/{group_id}/recover` | POST | prefix | `-` | no | Vanilla/admin/operator endpoint; not wrapped by React preview yet. |
| `/api/live-agent-processes/{group_id}/restart` | POST | prefix | `-` | no | Vanilla/admin/operator endpoint; not wrapped by React preview yet. |
| `/api/live-agent-processes/{group_id}/stop` | POST | prefix | `-` | no | Vanilla/admin/operator endpoint; not wrapped by React preview yet. |
| `/api/live-agent-readiness` | POST | exact | `-` | no | Vanilla/admin/operator endpoint; not wrapped by React preview yet. |
| `/api/live-agent-real-session-smoke` | POST | exact | `-` | no | Vanilla/admin/operator endpoint; not wrapped by React preview yet. |
| `/api/live-agent-session-runs` | GET | exact | `-` | no | Vanilla/admin/operator endpoint; not wrapped by React preview yet. |
| `/api/live-agent-session-runs/ensure` | POST | exact | `-` | no | Vanilla/admin/operator endpoint; not wrapped by React preview yet. |
| `/api/live-agent-session-runs/pause` | POST | exact | `-` | no | Vanilla/admin/operator endpoint; not wrapped by React preview yet. |
| `/api/live-agent-session-runs/resume` | POST | exact | `-` | no | Vanilla/admin/operator endpoint; not wrapped by React preview yet. |
| `/api/live-agent-session-runs/retry-now` | POST | exact | `-` | no | Vanilla/admin/operator endpoint; not wrapped by React preview yet. |
| `/api/live-agent-session-runs/stop` | POST | exact | `-` | no | Vanilla/admin/operator endpoint; not wrapped by React preview yet. |
| `/api/live-agent-session-runs/{run_id}/pause` | POST | prefix | `-` | no | Vanilla/admin/operator endpoint; not wrapped by React preview yet. |
| `/api/live-agent-session-runs/{run_id}/resume` | POST | prefix | `-` | no | Vanilla/admin/operator endpoint; not wrapped by React preview yet. |
| `/api/live-agent-session-runs/{run_id}/retry-now` | POST | prefix | `-` | no | Vanilla/admin/operator endpoint; not wrapped by React preview yet. |
| `/api/live-agent-session-runs/{run_id}/stop` | POST | prefix | `-` | no | Vanilla/admin/operator endpoint; not wrapped by React preview yet. |
| `/api/live-agent-session-smoke` | POST | exact | `-` | no | Vanilla/admin/operator endpoint; not wrapped by React preview yet. |
| `/api/live-agent-sessions/check` | POST | exact | `-` | no | Vanilla/admin/operator endpoint; not wrapped by React preview yet. |
| `/api/live-agent-sessions/ensure` | POST | exact | `-` | no | Vanilla/admin/operator endpoint; not wrapped by React preview yet. |
| `/api/live-agent-sessions/readiness` | GET | exact | `-` | no | Vanilla/admin/operator endpoint; not wrapped by React preview yet. |
| `/api/live-agent-sessions/recover` | POST | exact | `-` | no | Vanilla/admin/operator endpoint; not wrapped by React preview yet. |
| `/api/live-agent-sessions/restart` | POST | exact | `-` | no | Vanilla/admin/operator endpoint; not wrapped by React preview yet. |
| `/api/live-agent-sessions/resume` | POST | exact | `-` | no | Vanilla/admin/operator endpoint; not wrapped by React preview yet. |
| `/api/live-agent-sessions/start` | POST | exact | `-` | no | Vanilla/admin/operator endpoint; not wrapped by React preview yet. |
| `/api/live-agent-sessions/stop` | POST | exact | `-` | no | Vanilla/admin/operator endpoint; not wrapped by React preview yet. |
| `/api/live-agent-smoke` | POST | exact | `-` | no | Vanilla/admin/operator endpoint; not wrapped by React preview yet. |
| `/api/live-agents` | GET | exact | `-` | no | Vanilla/admin/operator endpoint; not wrapped by React preview yet. |
| `/api/live-agents` | POST | exact | `-` | no | Vanilla/admin/operator endpoint; not wrapped by React preview yet. |
| `/api/live-agents/{agent_id}/engagement` | POST | prefix | `-` | no | Vanilla/admin/operator endpoint; not wrapped by React preview yet. |
| `/api/live-agents/{agent_id}/heartbeat` | POST | prefix | `-` | no | Vanilla/admin/operator endpoint; not wrapped by React preview yet. |
| `/api/live-agents/{agent_id}/leave` | POST | prefix | `-` | no | Vanilla/admin/operator endpoint; not wrapped by React preview yet. |
| `/api/live-agents/{agent_id}/lobby` | POST | prefix | `-` | no | Vanilla/admin/operator endpoint; not wrapped by React preview yet. |
| `/api/live-agents/{agent_id}/official-turn` | POST | prefix | `-` | no | Vanilla/admin/operator endpoint; not wrapped by React preview yet. |
| `/api/live-agents/{agent_id}/probe` | POST | prefix | `-` | no | Vanilla/admin/operator endpoint; not wrapped by React preview yet. |
| `/api/live-agents/{agent_id}/return-packet` | GET | prefix | `-` | no | Vanilla/admin/operator endpoint; not wrapped by React preview yet. |
| `/api/live-agents/{agent_id}/room` | GET | prefix | `-` | no | Vanilla/admin/operator endpoint; not wrapped by React preview yet. |
| `/api/lobby` | GET | exact | `fetchLobby()` | yes | React lobby read/write. |
| `/api/lobby` | POST | exact | `postLobbyMessage()` | yes | React lobby read/write. |
| `/api/lobby/promote` | POST | exact | `-` | no | Vanilla/admin/operator endpoint; not wrapped by React preview yet. |
| `/api/lobby/remote` | POST | exact | `-` | no | Vanilla/admin/operator endpoint; not wrapped by React preview yet. |
| `/api/local-resources` | GET | exact | `fetchLocalResources()` | yes | React read-only resource monitor. |
| `/api/meetings` | GET | exact | `fetchMeetings()` | yes | React meeting selector/archive list. |
| `/api/meetings/latest` | GET | exact | `-` | no | Vanilla/admin/operator endpoint; not wrapped by React preview yet. |
| `/api/meetings/{meeting_id}` | GET | prefix | `fetchMeetingDetail()` | yes | React meeting detail/archive payload. |
| `/api/meetings/{meeting_id}/events` | GET | sse | `subscribeMeetingEvents()` | yes | React Live stream subscription. |
| `/api/meetings/{meeting_id}/finalize` | POST | prefix | `-` | no | Vanilla/admin/operator endpoint; not wrapped by React preview yet. |
| `/api/meetings/{meeting_id}/lifecycle` | GET | prefix | `fetchMeetingLifecycle()` | yes | React lifecycle/board projection. |
| `/api/meetings/{meeting_id}/live-agent-turns/call` | POST | prefix | `-` | no | Vanilla/admin/operator endpoint; not wrapped by React preview yet. |
| `/api/meetings/{meeting_id}/live-agent-turns/preset` | POST | prefix | `-` | no | Vanilla/admin/operator endpoint; not wrapped by React preview yet. |
| `/api/meetings/{meeting_id}/live-agent-turns/request` | POST | prefix | `-` | no | Vanilla/admin/operator endpoint; not wrapped by React preview yet. |
| `/api/meetings/{meeting_id}/live-agent-turns/round` | POST | prefix | `-` | no | Vanilla/admin/operator endpoint; not wrapped by React preview yet. |
| `/api/meetings/{meeting_id}/live-agent-turns/rounds` | POST | prefix | `-` | no | Vanilla/admin/operator endpoint; not wrapped by React preview yet. |
| `/api/meetings/{meeting_id}/live-agent-turns/sequence` | POST | prefix | `-` | no | Vanilla/admin/operator endpoint; not wrapped by React preview yet. |
| `/api/meetings/{meeting_id}/review-checkpoints` | POST | prefix | `-` | no | Vanilla/admin/operator endpoint; not wrapped by React preview yet. |
| `/api/play/mafia` | GET | exact | `fetchMafiaGame()` | yes | React Mafia game view. |
| `/api/play/mafia/chat` | POST | exact | `sendMafiaChat()` | yes | React Mafia chat action. |
| `/api/play/mafia/resolve` | POST | exact | `resolveMafiaPhase()` | yes | React Mafia phase resolution. |
| `/api/play/mafia/start` | POST | exact | `startMafiaGame()` | yes | React Mafia start action. |
| `/api/play/mafia/vote` | POST | exact | `castMafiaVote()` | yes | React Mafia vote action. |
| `/api/provider-health` | POST | exact | `-` | no | Vanilla/admin/operator endpoint; not wrapped by React preview yet. |
| `/api/providers` | GET | exact | `-` | no | Vanilla/admin/operator endpoint; not wrapped by React preview yet. |
| `/api/release-health` | GET | exact | `fetchReleaseHealth()` | yes | React read-only release health catalog. |
| `/api/side-chat` | GET | exact | `fetchSideChat()` | yes | React side-chat read/write. |
| `/api/side-chat` | POST | exact | `postSideChatMessage()` | yes | React side-chat read/write. |

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
| `python3 -m unittest tests.test_legacy_react_parity_inventory -v` | GUI API/SSE inventory appendix, React wrapper labels, and React API endpoint coverage. |
| `python3 -m unittest tests.test_cli_timeout -v` | `frontend-info` contract, `/app/` preview metadata, and `is_default_entry_point` boundary. |
| `python3 -m unittest tests.test_static_ui_assets -v` | Static asset contracts and React source evidence labels. |
| `python3 -m unittest tests.test_frontend_lobby_sse_runtime -v` | React lobby SSE parser, merge, and EventSource subscription behavior. |
| `python3 -m unittest tests.test_frontend_side_chat_runtime -v` | React side-chat parser, merge, and EventSource subscription behavior. |
| `python3 -m unittest tests.test_frontend_meeting_stream_runtime -v` | React meeting SSE parser, merge, timeline conversion, and EventSource subscription behavior. |
| `python3 -m unittest tests.test_frontend_live_timeline_state -v` | React Live timeline delta state, active meeting/flow filtering, stable identical-event refreshes, and pinned-to-latest intent. |
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
