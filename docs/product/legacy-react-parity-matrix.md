# Legacy React Parity Matrix

## Purpose And Non-Goals

This matrix tracks the evidence required before a future slice may make the
React/Vite frontend the default entry point for the local GUI room.

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
| Static assets | `/static/*` | `/legacy/static/*`, Vite dev assets | verified for vanilla fallback, partial for React | `tests/test_static_ui_assets.py`, `npm run build` when React changes | React dist is not served by Python yet. |
| Vite dev surface | Not applicable | `http://127.0.0.1:5173` | partial | `frontend/README.md`, `frontend/vite.config.ts` | Dev proxy only; not the default entry point. |
| Meeting list | `/api/meetings` | `fetchMeetings()` | partial | `frontend/src/api.ts`, `tests/test_static_ui_assets.py` | Needs browser parity proof before flip. |
| Meeting payload | `/api/meetings/<meeting-id>` | `fetchMeeting()` | partial | `frontend/src/api.ts` | Full archive rendering parity is not proven. |
| Meeting lifecycle | `/api/meetings/<meeting-id>/lifecycle` | `fetchMeetingLifecycle()` | partial | `tests/test_static_ui_assets.py::test_react_live_tab_surfaces_meeting_lifecycle_projection` | Labels are tested; live browser parity remains separate. |
| Lobby events | `/api/lobby` | `fetchLobby()` | partial | `frontend/src/api.ts` | Includes informal room history only. |
| Side chat | `/api/side-chat` | Not fully surfaced | unverified | `agentsassemble/gui.py` | Must stay separate from lobby and official records. |
| Live agents | `/api/live-agents` | `fetchLiveAgents()` | partial | `tests/test_static_ui_assets.py::test_react_lobby_preserves_agent_owned_room_evidence` | Host approval and context labels are represented. |
| Flow status | `/api/live-agent-flow` | `fetchFlow()` | partial | `frontend/src/api.ts` | Play Mode only. |
| Release health | `/api/release-health` | `fetchReleaseHealth()` | partial | `tests/test_static_ui_assets.py::test_react_admin_surfaces_release_health_catalog_as_cli_only` | GUI must not start checks. |
| Local resources | `/api/local-resources` | `fetchLocalResources()` | partial | `frontend/src/api.ts` | Read-only observability. |
| Attachment downloads | `/api/attachments/<id>` | Link or preview from event metadata | verified-read | `tests/test_gui_server.py::test_attachment_upload_sanitizes_and_downloads_image`, `tests/test_static_ui_assets.py::test_react_lobby_and_live_render_attachment_metadata` | React reads event metadata only; upload/composer parity remains a separate write-surface slice. |
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

Evidence:

- `tests/test_gui_server.py::test_legacy_console_namespace_serves_vanilla_console_without_changing_default_routes`
- `python3 -m agentsassemble.cli frontend-info --json`

## Verification Index

Use these checks to support parity rows:

| Check | Supports |
| --- | --- |
| `python3 -m unittest tests.test_docs_architecture -v` | Matrix existence, cross-references, opt-in boundary. |
| `python3 -m unittest tests.test_cli_timeout -v` | `frontend-info` contract and `is_default_entry_point` boundary. |
| `python3 -m unittest tests.test_static_ui_assets -v` | Static asset contracts and React source evidence labels. |
| `python3 -m unittest tests.test_gui_server -v` | Python GUI routes, legacy fallback, REST/SSE safety. |
| `cd frontend && npm run build` | React build health when React source changes. |
| `python3 -m compileall -q agentsassemble` | Python syntax after CLI/server edits. |
| `git diff --check` | Whitespace and patch hygiene. |

## Explicit Non-Goals

- No route flip in this slice.
- No serving `frontend/dist` from the Python GUI server.
- No React component, Tailwind, Vite, or build pipeline change.
- No new REST or SSE endpoint.
- No default-entry-point change.
- No claim that current partial rows prove enough to default React.
