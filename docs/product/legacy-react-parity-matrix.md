# Legacy React Parity Matrix

## Purpose And Non-Goals

This matrix tracked the evidence required before making the React/Vite frontend
the default entry point for the local GUI room. That flip is now done: `/`
serves the built Discord-style React room client when `frontend/dist` exists and falls
back to the dependency-light vanilla console otherwise, while `/legacy/` remains
the tested vanilla fallback and `/app/` remains a stable React alias.

Filled rows record API/SSE and route parity. Browser-rendered parity for the
default React surface stays operator-verified: confirm the four surfaces in a
real browser after a build. The legacy fallback at `/legacy/` must remain
reachable.

This is not a design document and not a request to build every React feature in
one pass.

## Default-Flip Preconditions

These preconditions were required for the flip and are now satisfied by current
evidence; the owner authorized the flip on 2026-05-30:

- API/SSE parity is verified for the operator flows the React UI owns.
- Room-event contracts are stable for lobby, side-chat, live meeting, cursor,
  attachment, and lifecycle reads.
- The legacy fallback at `/legacy/` and `/legacy/static/*` is reachable,
  isolated, and tested.
- The React route serves `frontend/dist` only when it exists, rejects traversal,
  rewrites Vite `/assets/*` references under `/app/assets/*`, and reports static
  availability through `frontend-info`.
- `frontend-info` reports `is_default_entry_point: true`; `/` serves React when
  built and the vanilla console otherwise.
- `/` is documented as the Discord-style room client (default entry point) with a
  vanilla fallback, `/legacy/` as the tested vanilla fallback, and `/app/` as
  the React alias.
- Play Mode, Work Mode, official records, and provider startup approval remain
  separated on both surfaces.
- Browser-rendered parity for the four React surfaces stays operator-verified
  after each build; it is not asserted headlessly.

## Surface Inventory

Status values:

- `verified`: covered by a named current test or command.
- `partial`: represented in code or docs, but not enough to approve a default
  route flip.
- `unverified`: known requirement with no current proof.

| Surface | Vanilla path/file | React equivalent | Status | Evidence | Notes |
| --- | --- | --- | --- | --- | --- |
| Default entry | `/` | React build served at `/` | verified | `tests/test_gui_server.py::test_root_and_app_serve_react_when_build_available`, `tests/test_gui_server.py::test_root_falls_back_to_vanilla_console_when_react_build_missing` | `/` serves React when `frontend/dist` exists, else vanilla fallback. |
| Legacy fallback | `/legacy/` | Not applicable | verified | `tests/test_gui_server.py::test_root_falls_back_to_vanilla_console_when_react_build_missing` | Vanilla console stays reachable at `/legacy/`. |
| Static assets | `/static/*` | `/legacy/static/*`, `/app/*`, root React assets via `/app/assets/*` | verified for vanilla fallback and React serving, browser parity operator-verified | `tests/test_static_ui_assets.py`, `tests/test_gui_server.py::test_root_and_app_serve_react_when_build_available`, `npm run build` when React changes | React index served at `/` and `/app/` rewrites `/assets/*` to `/app/assets/*`; `/legacy/static/*` stays vanilla. |
| Vite dev surface | Not applicable | `http://127.0.0.1:5173`, `/app/` built preview | partial | `frontend/README.md`, `frontend/vite.config.ts`, `python3 -m agentsassemble.cli frontend-info --json` | The Vite dev server stays a development proxy. The built React app is the default at `/` (and alias `/app/`); `frontend-info` recommends `/` once a build exists. |
| Room command strip | No single vanilla equivalent | `RoomCommandStrip` in `App.tsx` | partial | `tests/test_static_ui_assets.py::test_react_app_surfaces_room_command_center_without_provider_actions`, `cd frontend && npm run build` | React shows current step, one explicit `다음 행동`, participant counts, and the core room surfaces `로비`, `실황`, `작전판`, `아카이브` from existing lifecycle/agent projections. Admin/release-health/resource inspection stays behind the topbar management button; the strip must not start providers, run release checks, close turns, promote chatter, or expose private session/prompt/path/credential fields. |
| Meeting list | `/api/meetings` | `fetchMeetings()` | partial | `frontend/src/api.ts`, `tests/test_static_ui_assets.py` | Needs browser parity proof before flip. |
| Meeting payload | `/api/meetings/<meeting-id>` | `fetchMeetingDetail()` | partial | `frontend/src/api.ts` | Full archive rendering parity is not proven. |
| Archive canonical artifacts | `/api/meetings/<meeting-id>` artifact map | `RecordsView` final-artifact checklist | partial | `tests/test_frontend_archive_artifacts.py`, `tests/test_static_ui_assets.py::test_react_archive_surfaces_compact_meeting_lifecycle_banner`, `npm run build` | React Archive highlights `transcript.md`, `decision.md`, and shared-memory summary/action/question artifacts with generated/missing states before secondary artifacts; no download/export API is added. |
| Meeting lifecycle | `/api/meetings/<meeting-id>/lifecycle` | `fetchMeetingLifecycle()` | partial | `tests/test_static_ui_assets.py::test_react_live_tab_surfaces_meeting_lifecycle_projection`, `tests/test_static_ui_assets.py::test_react_lobby_surfaces_compact_meeting_lifecycle_banner`, `tests/test_static_ui_assets.py::test_react_archive_surfaces_compact_meeting_lifecycle_banner`, `tests/test_static_ui_assets.py::test_vanilla_gui_surfaces_lifecycle_next_action_on_core_tabs`, `tests/test_static_meeting_views_runtime.py` | React Lobby and Archive now surface compact lifecycle next-action evidence; React Live keeps its lifecycle panel, Board keeps its current-step summary, and live browser parity remains separate. |
| Board current step | `/api/meetings/<meeting-id>/lifecycle` | `BoardView` via `summarizeBoardLifecycle()` | partial | `tests/test_frontend_board_lifecycle.py`, `tests/test_static_ui_assets.py::test_react_board_uses_lifecycle_current_step_instead_of_artificial_rounds`, `tests/test_static_ui_assets.py::test_vanilla_gui_surfaces_lifecycle_next_action_on_core_tabs` | Board uses lifecycle current step, next action, role admission, and permission counts instead of artificial debate rounds; vanilla core tabs now show the same compact next action. |
| Workroom queue | `/api/meetings/<meeting-id>/workroom-queue` | `fetchWorkroomQueueEvidence()`, `WorkroomQueuePanel` | partial | `tests/test_frontend_workroom_queue.py`, `tests/test_gui_server.py::test_workroom_queue_endpoint_returns_safe_presence_without_artifact_bodies`, `cd frontend && npm run build` | React Board reads a safe projection of lifecycle, final artifact availability, return-packet count, review-checkpoint count, and task-scope overlap evidence; when return packets exist without a review checkpoint it shows a read-only review-needed warning. It does not poll full meeting detail or expose raw artifact/review/packet bodies, raw task bodies, provider output, absolute paths, URLs, or prompts. |
| Meeting stream snapshot | `/api/meetings/<meeting-id>/events` | `subscribeMeetingEvents()` | partial | `tests/test_gui_server.py::test_meeting_sse_payload_excludes_private_review_events_and_raw_fields`, `tests/static_meeting_views_runtime_smoke.mjs`, `tests/test_frontend_meeting_stream_runtime.py` | Meeting SSE carries a safe `meeting_stream_snapshot` plus projected live events. Full archive/detail payloads stay on explicit archive fetches, and legacy vanilla merges stream snapshots without replacing archive fields. |
| Lobby events | `/api/lobby` | `fetchLobby()`, `postLobbyMessage()` | partial | `frontend/src/api.ts`, `tests/test_static_ui_assets.py::test_react_lobby_composer_uploads_attachments_then_posts_lobby` | Includes informal room history only. |
| Lobby composer | Vanilla lobby composer | `LobbyComposer` | partial | `tests/test_static_ui_assets.py::test_react_lobby_composer_uploads_attachments_then_posts_lobby` | React can post text and attachment refs; browser parity proof remains separate. |
| Lobby external participation | Join brief, host-gated web invite, and public tunnel setup | `LobbyView` collapsed join-brief generator plus `RoomInvitePanel` using `fetchPublicInviteStatus()`, `generateHostToken()`, `setPublicInviteUrl()`, `startPublicInviteTunnel()`, `stopPublicInviteTunnel()`, and room invite wrappers | partial | `tests/test_public_invite_http.py`, `tests/test_public_invite.py`, `tests/test_public_tunnel.py`, `tests/test_static_ui_assets.py::test_react_lobby_external_participation_collapses_cli_only_cards_by_default`, `tests/test_static_ui_assets.py::test_react_lobby_external_participation_wraps_safe_join_brief_endpoint`, `tests/test_static_ui_assets.py::test_react_lobby_external_participation_uses_safe_command_skeletons_with_env_secret_refs`, `tests/test_static_ui_assets.py::test_react_lobby_external_participation_states_provider_startup_and_token_boundaries`, `tests/test_static_ui_assets.py::test_react_lobby_external_participation_has_no_unsafe_actions_or_token_io` | React can request the read-only `/api/live-agent-join-brief` entry packet for a manual external resident without starting providers. React can also bootstrap a server-lifetime host token, set or detect a public tunnel URL, create a browser join link, and let guests join/read/say/leave through session tokens. This does not install tunnel software, start provider CLIs, or create durable auth. |
| Discord home friends | No vanilla equivalent | `HomeFriendsView` using `fetchRoomFriends()`, `saveRoomFriend()`, and `createLiveAgentJoinBrief()` | partial | `tests/test_room_friends.py`, `tests/test_gui_server.py::test_room_friends_api_saves_friends_and_suggests_live_agents`, `tests/test_static_ui_assets.py::test_react_discord_home_friends_uses_persisted_room_friends`, browser check | React can persist people, subscription AI, API, Local, and unknown participants as room friends, suggest active live agents, and create a Join Brief for a saved friend without starting providers or exposing secrets. |
| Side chat | `/api/side-chat` | `fetchSideChat()`, `postSideChatMessage()`, `SideChatPanel` | partial | `tests/test_frontend_side_chat_runtime.py`, `tests/test_static_ui_assets.py::test_react_side_chat_uses_separate_room_contract`, `cd frontend && npm run build` | React surfaces a separate unofficial side-chat panel; browser parity proof remains separate before defaulting. |
| Live agents | `/api/live-agents` | `FlowResponse.agents` via `fetchLiveAgentFlow()` | partial | `tests/test_frontend_roster_truth.py`, `tests/test_frontend_agent_labels.py`, `tests/test_static_ui_assets.py::test_react_lobby_preserves_agent_owned_room_evidence`, `frontend/src/api.ts` | Host approval, context durability, join semantics, sandbox truth, character-mode badge state, and cursor/reply evidence are rendered on the active Lobby, Live, Board, and Records participant surfaces. The old standalone React `Roster.tsx` side panel was removed because it had no `App.tsx` consumer; direct `/api/live-agents` parity remains vanilla-only. |
| Flow status | `/api/live-agent-flow` | `fetchLiveAgentFlow()` | partial | `frontend/src/api.ts` | Play Mode only. |
| Release health | `/api/release-health`, `/api/release-health/queue` | `fetchReleaseHealth()`, `fetchReleaseHealthQueue()` | partial | `tests/test_static_ui_assets.py::test_react_admin_surfaces_release_health_catalog_as_cli_only`, `tests/test_static_ui_assets.py::test_react_admin_release_health_groups_default_queue_and_opt_in_with_safe_selectors`, `tests/test_frontend_release_health_queue.py`, `tests.test_gui_server.GuiServerTests.test_release_health_queue_endpoint_returns_safe_latest_projection`, `tests.test_gui_server.GuiServerTests.test_release_health_queue_endpoint_projects_safe_room_benchmark_summary` | React groups the read-only default proof queue and opt-in benchmark selector through shared catalog helpers that mirror `default_run`, safety class, safe `--check <id>` selectors, latest saved status/duration/counts, and safe room-benchmark scheduler signals from a stripped queue projection; GUI must not start checks or expose stdout/stderr, paths, argv/cwd/env, prompts, provider output, or session ids. |
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
| `/api/live-agent-health` | GET | exact | `fetchHealth()` | yes | React admin/status observability, including safe shared-memory counts without memory bodies. |
| `/api/live-agent-join-brief` | POST | exact | `createLiveAgentJoinBrief()` | yes | React can request a read-only external entry packet; it must not register, start providers, or generate LAN invite tokens. |
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
| `/api/meetings/{meeting_id}/workroom-queue` | GET | prefix | `fetchWorkroomQueueEvidence()` | yes | React Board workroom queue uses safe presence/count/task-scope projection only. |
| `/api/play/mafia` | GET | exact | `fetchMafiaGame()` | yes | React Mafia game view. |
| `/api/play/mafia/chat` | POST | exact | `sendMafiaChat()` | yes | React Mafia chat action. |
| `/api/play/mafia/resolve` | POST | exact | `resolveMafiaPhase()` | yes | React Mafia phase resolution. |
| `/api/play/mafia/start` | POST | exact | `startMafiaGame()` | yes | React Mafia start action. |
| `/api/play/mafia/vote` | POST | exact | `castMafiaVote()` | yes | React Mafia vote action. |
| `/api/provider-health` | POST | exact | `-` | no | Vanilla/admin/operator endpoint; not wrapped by React preview yet. |
| `/api/providers` | GET | exact | `-` | no | Vanilla/admin/operator endpoint; not wrapped by React preview yet. |
| `/api/public-invite/status` | GET | exact | `fetchPublicInviteStatus()` | yes | React invite panel reads public invite/tunnel status without exposing host tokens. |
| `/api/public-invite/host-token` | POST | exact | `generateHostToken()` | yes | React invite panel can generate one server-lifetime host token before public URL mode; existing env tokens are not returned. |
| `/api/public-invite/public-url` | POST | exact | `setPublicInviteUrl()` | yes | React invite panel stores an operator-approved public tunnel URL for join-link generation. |
| `/api/public-invite/tunnel/start` | POST | exact | `startPublicInviteTunnel()` | yes | React invite panel can start Cloudflare quick tunnel only when `cloudflared` is already installed. |
| `/api/public-invite/tunnel/stop` | POST | exact | `stopPublicInviteTunnel()` | yes | React invite panel can stop the server-owned quick tunnel. |
| `/api/release-health` | GET | exact | `fetchReleaseHealth()` | yes | React read-only release health catalog. |
| `/api/release-health/queue` | GET | exact | `fetchReleaseHealthQueue()` | yes | React read-only release-health latest status projection. |
| `/api/room-friends` | GET | exact | `fetchRoomFriends()` | yes | React home/friends screen reads persisted friends and active-session suggestions. |
| `/api/room-friends` | POST | exact | `saveRoomFriend()` | yes | React home/friends screen persists people, subscription AI, API, Local, and unknown participants. |
| `/api/side-chat` | GET | exact | `fetchSideChat()` | yes | React side-chat read/write. |
| `/api/side-chat` | POST | exact | `postSideChatMessage()` | yes | React side-chat read/write. |
| `/api/room-invite/create` | POST | exact | `createRoomInvite()` | yes | Host creates invite token for remote client. |
| `/api/room-invite/join` | POST | exact | `joinRoomWithInvite()` | yes | Remote client joins room with invite token. |
| `/api/room-invite/leave` | POST | exact | `leaveRoom()` | yes | Remote client leaves room and revokes session. |
| `/api/room-invite/sessions` | GET | exact | `fetchRoomInviteSessions()` | yes | Host views active remote sessions. |
| `/api/room-invite/invites` | GET | exact | `fetchPendingInvites()` | yes | Host views pending invites (host-gated). |
| `/api/room-invite/revoke` | POST | exact | `revokeRoomInvite()` | yes | Host revokes a pending invite (host-gated). |
| `/api/room/events` | GET | exact | `subscribeRoomEvents()` | yes | Authenticated SSE stream for remote clients. |
| `/api/room/lobby` | GET | exact | `fetchRoomLobby()` | yes | Authenticated lobby read for remote clients. |
| `/api/room/say` | POST | exact | `postRoomMessage()` | yes | Authenticated lobby write for remote clients. |
| `/api/play/mafia/action` | POST | exact | `-` | no | Mafia night action endpoint. |

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

- `/legacy/` serves the vanilla HTML console regardless of the React build.
- `/legacy/static/*` serves the same guarded static files as `/static/*`.
- `/legacy/static/../...` traversal attempts are rejected by the same static
  path guard.
- `/` falls back to this vanilla console when `frontend/dist` is absent.

The React surface is served by the same backend at `/` and `/app/`:

- `/` serves `frontend/dist/index.html` when the build exists, else the vanilla
  console.
- `/app/` serves `frontend/dist/index.html` when the build exists and returns a
  clear build hint when it is absent.
- `/app/assets/*` serves files from `frontend/dist/assets/*` through the same
  root-resolution guard.
- The React index served at `/` and `/app/` rewrites Vite `/assets/*` references
  to `/app/assets/*`, so the backend serves React assets from one guarded path.
- `frontend-info` reports `react_app_url`, `app_static_available`,
  `app_build_status`, the individual index/assets/reference checks, a
  `recommended_ui_url` that points to `/`, and `is_default_entry_point: true`.

Evidence:

- `tests/test_gui_server.py::test_root_and_app_serve_react_when_build_available`
- `tests/test_gui_server.py::test_root_falls_back_to_vanilla_console_when_react_build_missing`
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

- No removal of the vanilla console; `/legacy/` stays the tested fallback.
- No provider execution endpoint or browser-side mutation endpoint added through
  React parity work.
- No claim that headless API/SSE parity replaces operator browser verification
  of the four React surfaces.
- No Tailwind/Vite build-pipeline redesign bundled into the route flip.
- No committed `frontend/dist`; the build stays gitignored and operator-run.
