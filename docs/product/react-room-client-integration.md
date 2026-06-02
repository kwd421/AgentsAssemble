# React Room Client Integration Matrix

## Purpose And Non-Goals

This matrix tracks the evidence that the React/Vite Discord-style room client is
the single served frontend for the local GUI room. `/` serves the built React
client when `frontend/dist` exists, `/app/` is the same React client alias, and
retired legacy frontend paths no longer serve the old vanilla console.

Filled rows record API/SSE coverage, route behavior, and retired-legacy
boundaries. Browser-rendered parity for the default React surface stays
operator-verified: confirm the room surfaces in a real browser after a build.

This is not a design document and not a request to build every React feature in
one pass.

## React-Only Preconditions

These preconditions keep the React client as the canonical frontend:

- API/SSE parity is verified for the operator flows the React UI owns.
- Room-event contracts are stable for lobby, side-chat, live meeting, cursor,
  attachment, and lifecycle reads.
- The React route serves `frontend/dist` only when it exists, rejects traversal,
  rewrites Vite `/assets/*` references under `/app/assets/*`, and reports static
  availability through `frontend-info`.
- `frontend-info` reports `is_default_entry_point: true`; `/` serves React when
  built and reports a missing React build otherwise.
- `/legacy/`, `/legacy/static/*`, and `/static/*` are retired frontend paths and
  do not expose the old vanilla console.
- `/` is documented as the React room client default entry point, with `/app/`
  as the React alias.
- Play Mode, Work Mode, official records, and provider startup approval remain
  separated on the React surface.
- Browser-rendered parity for the React surfaces stays operator-verified
  after each build; it is not asserted headlessly.

## Surface Inventory

Status values:

- `verified`: covered by a named current test or command.
- `partial`: represented in code or docs, but not enough to approve a default
  route flip.
- `unverified`: known requirement with no current proof.

| Surface | Room/backend path | React equivalent | Status | Evidence | Notes |
| --- | --- | --- | --- | --- | --- |
| Default entry | `/` | React build served at `/` | verified | `tests/test_gui_server.py::test_root_and_app_serve_react_when_build_available`, `tests/test_gui_server.py::test_root_reports_missing_react_build_without_vanilla_fallback` | `/` serves React when `frontend/dist` exists, else reports the missing React build. |
| Retired legacy UI paths | `/legacy/`, `/legacy/static/*`, `/static/*` | React client or 404 | verified | `tests/test_gui_server.py::test_root_reports_missing_react_build_without_vanilla_fallback`, `tests/test_gui_server.py::test_root_and_app_serve_react_when_build_available` | `/legacy/` resolves to the React client; old static asset paths return 404. |
| React assets | `/app/*`, root React assets via `/app/assets/*` | Built Vite assets | verified for React serving, browser parity operator-verified | `tests/test_gui_server.py::test_root_and_app_serve_react_when_build_available`, `npm run build` when React changes | React index served at `/`, `/legacy/`, and `/app/` rewrites `/assets/*` to `/app/assets/*`. |
| Vite dev surface | Not applicable | `http://127.0.0.1:5173`, `/app/` built preview | partial | `frontend/README.md`, `frontend/vite.config.ts`, `python3 -m agentsassemble.cli frontend-info --json` | The Vite dev server stays a development proxy. The built React app is the default at `/` (and alias `/app/`); `frontend-info` recommends `/` once a build exists. |
| Discord shell | No retired-GUI equivalent | `App.tsx` room shell | partial | `tests/test_react_ui_contracts.py::test_react_discord_shell_internal_scroll_and_no_default_clutter`, `tests/test_react_ui_contracts.py::test_react_app_surfaces_single_channel_navigation_without_duplicate_strip`, `cd frontend && npm run build` | React owns the room rail, channel sidebar, channel body, and member panel without a second navigation strip. Admin/release-health/resource inspection stays behind the topbar management button; the default shell must not start providers, run release checks, close turns, promote chatter, or expose private session/prompt/path/credential fields. |
| Meeting list | `/api/meetings` | `fetchMeetings()` | partial | `frontend/src/api.ts`, `tests/test_react_ui_contracts.py` | Needs browser parity proof before flip. |
| Meeting payload | `/api/meetings/<meeting-id>` | `fetchMeetingDetail()` | partial | `frontend/src/api.ts` | Full archive rendering parity is not proven. |
| Archive canonical artifacts | `/api/meetings/<meeting-id>` artifact map | `RecordsView` final-artifact checklist | partial | `tests/test_frontend_archive_artifacts.py`, `tests/test_react_ui_contracts.py::test_react_archive_surfaces_final_artifacts_without_lifecycle_jargon`, `npm run build` | React Archive highlights `transcript.md`, `decision.md`, and shared-memory summary/action/question artifacts with generated/missing states before secondary artifacts; no download/export API is added. |
| Meeting lifecycle | `/api/meetings/<meeting-id>/lifecycle` | `fetchMeetingLifecycle()` | partial | `tests/test_react_ui_contracts.py::test_react_live_drops_lifecycle_panel_but_keeps_projection_plumbing`, `tests/test_react_ui_contracts.py::test_react_lobby_is_clean_chat_without_lifecycle_exposition`, `tests/test_react_ui_contracts.py::test_react_board_uses_lifecycle_current_step_instead_of_artificial_rounds` | Lobby and Live stay chat-first without lifecycle exposition panels; Board keeps the safe lifecycle synthesis for current step, admission, and permission review. |
| Board current step | `/api/meetings/<meeting-id>/lifecycle` | `BoardView` via `summarizeBoardLifecycle()` | partial | `tests/test_frontend_board_lifecycle.py`, `tests/test_react_ui_contracts.py::test_react_board_uses_lifecycle_current_step_instead_of_artificial_rounds` | Board uses lifecycle current step, next action, role admission, and permission counts instead of artificial debate rounds. |
| Workroom queue | `/api/meetings/<meeting-id>/workroom-queue` | `fetchWorkroomQueueEvidence()`, `WorkroomQueuePanel` | partial | `tests/test_frontend_workroom_queue.py`, `tests/test_gui_server.py::test_workroom_queue_endpoint_returns_safe_presence_without_artifact_bodies`, `cd frontend && npm run build` | React Board reads a safe projection of lifecycle, final artifact availability, return-packet count, review-checkpoint count, and task-scope overlap evidence; when return packets exist without a review checkpoint it shows a read-only review-needed warning. It does not poll full meeting detail or expose raw artifact/review/packet bodies, raw task bodies, provider output, absolute paths, URLs, or prompts. |
| Meeting stream snapshot | `/api/meetings/<meeting-id>/events` | `subscribeMeetingEvents()` | partial | `tests/test_gui_server.py::test_meeting_sse_payload_excludes_private_review_events_and_raw_fields`, `tests/test_frontend_meeting_stream_runtime.py`, `tests/test_frontend_live_timeline_state.py` | Meeting SSE carries a safe `meeting_stream_snapshot` plus projected live events. Full archive/detail payloads stay on explicit archive fetches. |
| Lobby events | `/api/lobby` | `fetchLobby()`, `postLobbyMessage()` | partial | `frontend/src/api.ts`, `tests/test_react_ui_contracts.py::test_react_lobby_composer_uploads_attachments_then_posts_lobby` | Includes informal room history only. |
| Lobby composer | Retired lobby composer | `LobbyComposer` | partial | `tests/test_react_ui_contracts.py::test_react_lobby_composer_uploads_attachments_then_posts_lobby` | React can post text and attachment refs; browser parity proof remains separate. |
| Lobby external participation | Join brief and LAN invite CLI surfaces | `LobbyView` collapsed join-brief generator plus CLI disclosure | partial | `tests/test_react_ui_contracts.py::test_react_lobby_external_participation_collapses_cli_only_cards_by_default`, `tests/test_react_ui_contracts.py::test_react_lobby_external_participation_wraps_safe_join_brief_endpoint`, `tests/test_react_ui_contracts.py::test_react_lobby_external_participation_uses_safe_command_skeletons_with_env_secret_refs`, `tests/test_react_ui_contracts.py::test_react_lobby_external_participation_states_provider_startup_and_token_boundaries`, `tests/test_react_ui_contracts.py::test_react_lobby_external_participation_has_no_unsafe_actions_or_token_io` | React can request the read-only `/api/live-agent-join-brief` entry packet for a manual external resident and display the packet without registering, starting providers, generating LAN tokens, touching clipboard/storage, or opening SSE. LAN invite token creation remains CLI-only. |
| Side chat | `/api/side-chat` | `fetchSideChat()`, `postSideChatMessage()`, `SideChatPanel` | partial | `tests/test_frontend_side_chat_runtime.py`, `tests/test_react_ui_contracts.py::test_react_side_chat_uses_separate_room_contract`, `cd frontend && npm run build` | React surfaces a separate unofficial side-chat panel; browser parity proof remains separate before defaulting. |
| Live agents | `/api/live-agents` | `FlowResponse.agents` via `fetchLiveAgentFlow()` | partial | `tests/test_frontend_roster_truth.py`, `tests/test_frontend_agent_labels.py`, `tests/test_react_ui_contracts.py::test_react_lobby_preserves_agent_owned_room_evidence`, `frontend/src/api.ts` | Host approval, context durability, join semantics, sandbox truth, character-mode badge state, and cursor/reply evidence are rendered on the active Lobby, Live, Board, and Records participant surfaces. The old standalone React `Roster.tsx` side panel was removed because it had no `App.tsx` consumer; direct `/api/live-agents` parity remains backend-only. |
| Flow status | `/api/live-agent-flow` | `fetchLiveAgentFlow()` | partial | `frontend/src/api.ts` | Play Mode only. |
| Release health | `/api/release-health`, `/api/release-health/queue` | `fetchReleaseHealth()`, `fetchReleaseHealthQueue()` | partial | `tests/test_react_ui_contracts.py::test_react_admin_surfaces_release_health_catalog_as_cli_only`, `tests/test_react_ui_contracts.py::test_react_admin_release_health_groups_default_queue_and_opt_in_with_safe_selectors`, `tests/test_frontend_release_health_queue.py`, `tests.test_gui_server.GuiServerTests.test_release_health_queue_endpoint_returns_safe_latest_projection`, `tests.test_gui_server.GuiServerTests.test_release_health_queue_endpoint_projects_safe_room_benchmark_summary` | React groups the read-only default proof queue and opt-in benchmark selector through shared catalog helpers that mirror `default_run`, safety class, safe `--check <id>` selectors, latest saved status/duration/counts, and safe room-benchmark scheduler signals from a stripped queue projection; GUI must not start checks or expose stdout/stderr, paths, argv/cwd/env, prompts, provider output, or session ids. |
| Local resources | `/api/local-resources` | `fetchLocalResources()`, `AdminPanel` | partial | `tests/test_react_ui_contracts.py::test_react_admin_local_resources_renders_safe_process_observability`, `tests/test_frontend_local_resource_labels_runtime.py`, `tests/test_local_resources.py`, `tests/test_gui_server.py::test_local_resources_endpoint_returns_sanitized_read_only_snapshot` | React shows read-only sanitized local resource observability, including displayed-set CPU/RSS totals, 1/5/15m load, PPID, role legend, and humanized attention labels. Browser parity proof remains separate before defaulting. |
| Attachment downloads | `/api/attachments/<id>` | Link or preview from event metadata | partial | `tests/test_gui_server.py::test_attachment_upload_sanitizes_and_downloads_image`, `tests/test_react_ui_contracts.py::test_react_lobby_and_live_render_attachment_metadata`, `tests/test_react_ui_contracts.py::test_react_lobby_composer_uploads_attachments_then_posts_lobby` | React reads event metadata and can upload attachment refs through the existing lobby composer contract; browser parity proof remains separate. |
| Lobby SSE | `/api/events/lobby` | `subscribeLobby()`, `parseLobbyStreamData()`, `mergeLobbyEvents()`, `mergeLobbyEventsByCreatedAt()` | partial | `tests/test_frontend_lobby_sse_runtime.py`, `tests/test_react_ui_contracts.py::test_react_lobby_sse_uses_shared_parser_and_merge_helpers`, `tests/test_gui_server.py::test_lobby_sse_keeps_connection_open_with_heartbeat` | React parses lobby SSE snapshots/single events, rejects side-chat payloads, and uses shared id-keyed merge helpers while Live keeps chronological rendering; browser delivery smoothness still needs separate proof. |
| Side-chat SSE | `/api/events/side-chat` | `subscribeSideChat()` | partial | `tests/test_frontend_side_chat_runtime.py`, `tests/test_react_ui_contracts.py::test_react_side_chat_uses_separate_room_contract`, `cd frontend && npm run build` | React subscribes to `side_chat` events through the side-chat stream and ignores lobby-stream payloads. |
| Meeting SSE | `/api/meetings/<meeting-id>/events` | `subscribeMeetingEvents()`, official live-event fallback in `LiveView` | verified | `tests/test_frontend_meeting_stream_runtime.py`, `tests/test_frontend_live_timeline_state.py`, `tests/test_react_ui_contracts.py::test_react_live_tab_subscribes_to_meeting_sse_without_route_flip_or_provider_start`, `cd frontend && npm run build` | React Live subscribes to meeting stream snapshots/deltas, filters stale meeting/flow events, preserves stable identical-event refreshes and pinned-to-latest intent at the state level, and uses official live events only when Play Mode flow events are absent. Browser-rendered smoothness remains separate before defaulting. |
| Flow start/stop mutation | `/api/live-agent-flow/start`, `/api/live-agent-flow/stop` | `startFlow()`, `stopFlow()` | partial | `frontend/src/api.ts` | Does not start provider CLIs. |
| Full REST/SSE inventory | `agentsassemble/gui.py` | `frontend/src/api.ts` | verified | `tests/test_react_room_client_inventory.py`, `API/SSE Inventory Appendix` | The matrix is guarded against silent route and React-wrapper drift from `gui.py` and `frontend/src/api.ts`. |

## API/SSE Inventory Appendix

This appendix records the current `/api/...` surface served by `agentsassemble/gui.py`.
Rows marked `React wired: no` are intentionally documented as backend/admin
surfaces rather than silently counted as React client coverage.

| Path | Method | Handler form | React wrapper | React wired | Notes |
| --- | --- | --- | --- | --- | --- |
| `/api/attachments` | POST | exact | `uploadLobbyAttachment()` | yes | React composer uploads attachments before posting lobby messages. |
| `/api/attachments/{attachment_id}` | GET | prefix | `-` | no | Backend/admin/operator endpoint; not wrapped by React preview yet. |
| `/api/codex-sessions` | GET | exact | `-` | no | Backend/admin/operator endpoint; not wrapped by React preview yet. |
| `/api/codex-sessions/invite` | POST | exact | `-` | no | Backend/admin/operator endpoint; not wrapped by React preview yet. |
| `/api/codex-sessions/join` | POST | exact | `-` | no | Backend/admin/operator endpoint; not wrapped by React preview yet. |
| `/api/demo` | POST | exact | `-` | no | Backend/admin/operator endpoint; not wrapped by React preview yet. |
| `/api/events/lobby` | GET | sse | `subscribeLobby()` | yes | React lobby subscribes to lobby stream. |
| `/api/events/side-chat` | GET | sse | `subscribeSideChat()` | yes | React side chat subscribes to separate stream. |
| `/api/live-agent-discovery` | POST | exact | `-` | no | Backend/admin/operator endpoint; not wrapped by React preview yet. |
| `/api/live-agent-flow` | GET | exact | `fetchLiveAgentFlow()` | yes | React Play Mode status surface. |
| `/api/live-agent-flow/start` | POST | exact | `startFlow()` | yes | React Play Mode start control; does not start providers. |
| `/api/live-agent-flow/stop` | POST | exact | `stopFlow()` | yes | React Play Mode stop control. |
| `/api/live-agent-health` | GET | exact | `fetchHealth()` | yes | React admin/status observability, including safe shared-memory counts without memory bodies. |
| `/api/live-agent-join-brief` | POST | exact | `createLiveAgentJoinBrief()` | yes | React can request a read-only external entry packet; it must not register, start providers, or generate LAN invite tokens. |
| `/api/live-agent-meetings/start` | POST | exact | `-` | no | Backend/admin/operator endpoint; not wrapped by React preview yet. |
| `/api/live-agent-official-round-smoke` | POST | exact | `-` | no | Backend/admin/operator endpoint; not wrapped by React preview yet. |
| `/api/live-agent-operations` | GET | exact | `-` | no | Backend/admin/operator endpoint; not wrapped by React preview yet. |
| `/api/live-agent-preflight` | POST | exact | `-` | no | Backend/admin/operator endpoint; not wrapped by React preview yet. |
| `/api/live-agent-process-events` | GET | exact | `-` | no | Backend/admin/operator endpoint; not wrapped by React preview yet. |
| `/api/live-agent-processes` | GET | exact | `-` | no | Backend/admin/operator endpoint; not wrapped by React preview yet. |
| `/api/live-agent-processes/start` | POST | exact | `-` | no | Backend/admin/operator endpoint; not wrapped by React preview yet. |
| `/api/live-agent-processes/stop-running` | POST | exact | `-` | no | Backend/admin/operator endpoint; not wrapped by React preview yet. |
| `/api/live-agent-processes/{group_id}/recover` | POST | prefix | `-` | no | Backend/admin/operator endpoint; not wrapped by React preview yet. |
| `/api/live-agent-processes/{group_id}/restart` | POST | prefix | `-` | no | Backend/admin/operator endpoint; not wrapped by React preview yet. |
| `/api/live-agent-processes/{group_id}/stop` | POST | prefix | `-` | no | Backend/admin/operator endpoint; not wrapped by React preview yet. |
| `/api/live-agent-readiness` | POST | exact | `-` | no | Backend/admin/operator endpoint; not wrapped by React preview yet. |
| `/api/live-agent-real-session-smoke` | POST | exact | `-` | no | Backend/admin/operator endpoint; not wrapped by React preview yet. |
| `/api/live-agent-session-runs` | GET | exact | `-` | no | Backend/admin/operator endpoint; not wrapped by React preview yet. |
| `/api/live-agent-session-runs/ensure` | POST | exact | `-` | no | Backend/admin/operator endpoint; not wrapped by React preview yet. |
| `/api/live-agent-session-runs/pause` | POST | exact | `-` | no | Backend/admin/operator endpoint; not wrapped by React preview yet. |
| `/api/live-agent-session-runs/resume` | POST | exact | `-` | no | Backend/admin/operator endpoint; not wrapped by React preview yet. |
| `/api/live-agent-session-runs/retry-now` | POST | exact | `-` | no | Backend/admin/operator endpoint; not wrapped by React preview yet. |
| `/api/live-agent-session-runs/stop` | POST | exact | `-` | no | Backend/admin/operator endpoint; not wrapped by React preview yet. |
| `/api/live-agent-session-runs/{run_id}/pause` | POST | prefix | `-` | no | Backend/admin/operator endpoint; not wrapped by React preview yet. |
| `/api/live-agent-session-runs/{run_id}/resume` | POST | prefix | `-` | no | Backend/admin/operator endpoint; not wrapped by React preview yet. |
| `/api/live-agent-session-runs/{run_id}/retry-now` | POST | prefix | `-` | no | Backend/admin/operator endpoint; not wrapped by React preview yet. |
| `/api/live-agent-session-runs/{run_id}/stop` | POST | prefix | `-` | no | Backend/admin/operator endpoint; not wrapped by React preview yet. |
| `/api/live-agent-session-smoke` | POST | exact | `-` | no | Backend/admin/operator endpoint; not wrapped by React preview yet. |
| `/api/live-agent-sessions/check` | POST | exact | `-` | no | Backend/admin/operator endpoint; not wrapped by React preview yet. |
| `/api/live-agent-sessions/ensure` | POST | exact | `-` | no | Backend/admin/operator endpoint; not wrapped by React preview yet. |
| `/api/live-agent-sessions/readiness` | GET | exact | `-` | no | Backend/admin/operator endpoint; not wrapped by React preview yet. |
| `/api/live-agent-sessions/recover` | POST | exact | `-` | no | Backend/admin/operator endpoint; not wrapped by React preview yet. |
| `/api/live-agent-sessions/restart` | POST | exact | `-` | no | Backend/admin/operator endpoint; not wrapped by React preview yet. |
| `/api/live-agent-sessions/resume` | POST | exact | `-` | no | Backend/admin/operator endpoint; not wrapped by React preview yet. |
| `/api/live-agent-sessions/start` | POST | exact | `-` | no | Backend/admin/operator endpoint; not wrapped by React preview yet. |
| `/api/live-agent-sessions/stop` | POST | exact | `-` | no | Backend/admin/operator endpoint; not wrapped by React preview yet. |
| `/api/live-agent-smoke` | POST | exact | `-` | no | Backend/admin/operator endpoint; not wrapped by React preview yet. |
| `/api/live-agents` | GET | exact | `-` | no | Backend/admin/operator endpoint; not wrapped by React preview yet. |
| `/api/live-agents` | POST | exact | `-` | no | Backend/admin/operator endpoint; not wrapped by React preview yet. |
| `/api/live-agents/{agent_id}/engagement` | POST | prefix | `-` | no | Backend/admin/operator endpoint; not wrapped by React preview yet. |
| `/api/live-agents/{agent_id}/heartbeat` | POST | prefix | `-` | no | Backend/admin/operator endpoint; not wrapped by React preview yet. |
| `/api/live-agents/{agent_id}/leave` | POST | prefix | `-` | no | Backend/admin/operator endpoint; not wrapped by React preview yet. |
| `/api/live-agents/{agent_id}/lobby` | POST | prefix | `-` | no | Backend/admin/operator endpoint; not wrapped by React preview yet. |
| `/api/live-agents/{agent_id}/official-turn` | POST | prefix | `-` | no | Backend/admin/operator endpoint; not wrapped by React preview yet. |
| `/api/live-agents/{agent_id}/probe` | POST | prefix | `-` | no | Backend/admin/operator endpoint; not wrapped by React preview yet. |
| `/api/live-agents/{agent_id}/return-packet` | GET | prefix | `-` | no | Backend/admin/operator endpoint; not wrapped by React preview yet. |
| `/api/live-agents/{agent_id}/room` | GET | prefix | `-` | no | Backend/admin/operator endpoint; not wrapped by React preview yet. |
| `/api/lobby` | GET | exact | `fetchLobby()` | yes | React lobby read/write. |
| `/api/lobby` | POST | exact | `postLobbyMessage()` | yes | React lobby read/write. |
| `/api/lobby/promote` | POST | exact | `-` | no | Backend/admin/operator endpoint; not wrapped by React preview yet. |
| `/api/lobby/remote` | POST | exact | `-` | no | Backend/admin/operator endpoint; not wrapped by React preview yet. |
| `/api/local-resources` | GET | exact | `fetchLocalResources()` | yes | React read-only resource monitor. |
| `/api/meetings` | GET | exact | `fetchMeetings()` | yes | React meeting selector/archive list. |
| `/api/meetings/latest` | GET | exact | `-` | no | Backend/admin/operator endpoint; not wrapped by React preview yet. |
| `/api/meetings/{meeting_id}` | GET | prefix | `fetchMeetingDetail()` | yes | React meeting detail/archive payload. |
| `/api/meetings/{meeting_id}/events` | GET | sse | `subscribeMeetingEvents()` | yes | React Live stream subscription. |
| `/api/meetings/{meeting_id}/finalize` | POST | prefix | `-` | no | Backend/admin/operator endpoint; not wrapped by React preview yet. |
| `/api/meetings/{meeting_id}/lifecycle` | GET | prefix | `fetchMeetingLifecycle()` | yes | React lifecycle/board projection. |
| `/api/meetings/{meeting_id}/live-agent-turns/call` | POST | prefix | `-` | no | Backend/admin/operator endpoint; not wrapped by React preview yet. |
| `/api/meetings/{meeting_id}/live-agent-turns/preset` | POST | prefix | `-` | no | Backend/admin/operator endpoint; not wrapped by React preview yet. |
| `/api/meetings/{meeting_id}/live-agent-turns/request` | POST | prefix | `-` | no | Backend/admin/operator endpoint; not wrapped by React preview yet. |
| `/api/meetings/{meeting_id}/live-agent-turns/round` | POST | prefix | `-` | no | Backend/admin/operator endpoint; not wrapped by React preview yet. |
| `/api/meetings/{meeting_id}/live-agent-turns/rounds` | POST | prefix | `-` | no | Backend/admin/operator endpoint; not wrapped by React preview yet. |
| `/api/meetings/{meeting_id}/live-agent-turns/sequence` | POST | prefix | `-` | no | Backend/admin/operator endpoint; not wrapped by React preview yet. |
| `/api/meetings/{meeting_id}/review-checkpoints` | POST | prefix | `-` | no | Backend/admin/operator endpoint; not wrapped by React preview yet. |
| `/api/meetings/{meeting_id}/workroom-queue` | GET | prefix | `fetchWorkroomQueueEvidence()` | yes | React Board workroom queue uses safe presence/count/task-scope projection only. |
| `/api/play/mafia` | GET | exact | `fetchMafiaGame()` | yes | React Mafia game view. |
| `/api/play/mafia/chat` | POST | exact | `sendMafiaChat()` | yes | React Mafia chat action. |
| `/api/play/mafia/resolve` | POST | exact | `resolveMafiaPhase()` | yes | React Mafia phase resolution. |
| `/api/play/mafia/start` | POST | exact | `startMafiaGame()` | yes | React Mafia start action. |
| `/api/play/mafia/vote` | POST | exact | `castMafiaVote()` | yes | React Mafia vote action. |
| `/api/provider-health` | POST | exact | `-` | no | Backend/admin/operator endpoint; not wrapped by React preview yet. |
| `/api/providers` | GET | exact | `-` | no | Backend/admin/operator endpoint; not wrapped by React preview yet. |
| `/api/release-health` | GET | exact | `fetchReleaseHealth()` | yes | React read-only release health catalog. |
| `/api/release-health/queue` | GET | exact | `fetchReleaseHealthQueue()` | yes | React read-only release-health latest status projection. |
| `/api/room-friends` | GET | exact | `fetchRoomFriends()` | yes | React Discord home/friends view lists saved friends and previous-session candidates. |
| `/api/room-friends` | POST | exact | `addRoomFriend()` | yes | React Discord home/friends view stores a friend/session entry without starting providers. |
| `/api/room-settings` | GET | exact | `fetchRoomSettings()` | yes | React room shell reads persisted room labels, topic, icon/banner appearance, invite scope, and member roles. `read_only` invite scope is carried into generated browser guest URLs. |
| `/api/room-settings` | POST | exact | `saveRoomSettings()` | yes | React room settings saves labels, topic, icon/banner appearance, invite scope, and member roles. Browser guests entering with `scope=read_only` see the same room but the lobby composer is disabled. |
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
- Browser guest links are room-scoped. When a saved room setting marks the invite
  scope `read_only`, the generated guest URL carries that scope and the React
  lobby disables posting controls for that guest session instead of pretending
  the link is writable. This is a browser-client constraint, not an authenticated
  server-side write permission; authenticated remote room APIs remain separate
  future work.
- Release-health and room-benchmark catalog visibility is read-only; React may
  show opt-in benchmark evidence but must not start checks or benchmarks.

## React-Only Route Status

The previous vanilla console namespace is retired from the served GUI:

- `/legacy/` resolves to the same React room client as `/` when the build exists
  and reports the missing React build when it does not.
- `/legacy/static/*` and `/static/*` return 404 instead of exposing old vanilla
  CSS/JS assets.
- `/` never falls back to the vanilla console when `frontend/dist` is absent.

The React surface is served by the same backend at `/` and `/app/`:

- `/` serves `frontend/dist/index.html` when the build exists and returns a clear
  build hint when it is absent.
- `/app/` serves `frontend/dist/index.html` when the build exists and returns a
  clear build hint when it is absent.
- `/app/assets/*` serves files from `frontend/dist/assets/*` through the same
  root-resolution guard.
- The React index served at `/`, `/legacy/`, and `/app/` rewrites Vite
  `/assets/*` references to `/app/assets/*`, so the backend serves React assets
  from one guarded path.
- `frontend-info` reports `react_app_url`, `app_static_available`,
  `app_build_status`, the individual index/assets/reference checks, a
  `recommended_ui_url` that points to `/`, and `is_default_entry_point: true`.

Evidence:

- `tests/test_gui_server.py::test_root_and_app_serve_react_when_build_available`
- `tests/test_gui_server.py::test_root_reports_missing_react_build_without_vanilla_fallback`
- `tests/test_gui_server.py::test_react_app_preview_route_reports_missing_dist_without_crashing`
- `python3 -m agentsassemble.cli frontend-info --json`

## Verification Index

Use these checks to support parity rows:

| Check | Supports |
| --- | --- |
| `python3 -m unittest tests.test_docs_architecture -v` | Matrix existence, cross-references, opt-in boundary. |
| `python3 -m unittest tests.test_react_room_client_inventory -v` | GUI API/SSE inventory appendix, React wrapper labels, and React API endpoint coverage. |
| `python3 -m unittest tests.test_cli_timeout -v` | `frontend-info` contract, `/app/` preview metadata, and `is_default_entry_point` boundary. |
| `python3 -m unittest tests.test_react_ui_contracts -v` | React room-client source evidence labels. |
| `python3 -m unittest tests.test_frontend_lobby_sse_runtime -v` | React lobby SSE parser, merge, and EventSource subscription behavior. |
| `python3 -m unittest tests.test_frontend_side_chat_runtime -v` | React side-chat parser, merge, and EventSource subscription behavior. |
| `python3 -m unittest tests.test_frontend_meeting_stream_runtime -v` | React meeting SSE parser, merge, timeline conversion, and EventSource subscription behavior. |
| `python3 -m unittest tests.test_frontend_live_timeline_state -v` | React Live timeline delta state, active meeting/flow filtering, stable identical-event refreshes, and pinned-to-latest intent. |
| `python3 -m unittest tests.test_gui_server -v` | Python GUI routes, retired legacy paths, REST/SSE safety. |
| `cd frontend && npm run build` | React build health when React source changes. |
| `python3 -m compileall -q agentsassemble` | Python syntax after CLI/server edits. |
| `git diff --check` | Whitespace and patch hygiene. |

## Explicit Non-Goals

- No provider-side behavior changes are implied by retiring the old GUI surface.
- No provider execution endpoint or browser-side mutation endpoint added through
  React client integration work.
- No claim that headless API/SSE parity replaces operator browser verification
  of the React surfaces.
- No Tailwind/Vite build-pipeline redesign bundled into the route cleanup.
- No committed `frontend/dist`; the build stays gitignored and operator-run.
