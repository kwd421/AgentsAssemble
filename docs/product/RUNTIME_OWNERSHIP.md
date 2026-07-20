# Runtime Ownership

Status: current ownership map

Evidence baseline: `3d45e67c5e15961e63afc1ccc55579169f85c4d6`

Read when changing Agent Session lifecycle, provider processes, recovery,
turn assignment, or legacy resident controls. Start with `CURRENT_SYSTEM.md`.

## Canonical Shared-Room Runtime

| Concern | Owner |
| --- | --- |
| Room, participant, Agent Session, event and command state | `RoomStore` |
| Browser and Agent Bridge transport | ticket-authenticated canonical `/ws` |
| Room command validation and coordination | `RoomRealtimeController` |
| Provider turn delivery and active bridge lease | `providers/agent_bridge.py`, `room_event_broker.py`; process entrypoint composition in `application/agent_bridge_entrypoint.py` |
| Server-owned provider process handles | `providers/bridge_process.py`; compatibility export in `room_bridge_process.py` |
| Codex app-server process and provider thread | `providers/codex_app_server.py` |
| Provider-visible room context | `room_turn_context.py` |
| Provider-private conversation memory | the provider adapter/session |

Canonical process shutdown uses only a server-owned opaque handle. A PID
reported by an external bridge is diagnostic data and is never a kill target.
Provider-visible packets contain safe room data and media IDs, never local
paths, credentials, raw argv, or backend identifiers.

## Legacy Compatibility Runtime

The following modules support the old meeting/resident control surface. They
are not canonical Agent Session owners and must not receive new shared-room
features.

| Legacy concern | Current owner | Direction |
| --- | --- | --- |
| Safe health status and diagnostic projection policy | `legacy_live_agent_health.py` | retain while legacy controls exist |
| Read-only legacy resident HTTP routes | `legacy/live_agent/http/read.py` | compatibility freeze; root module is an export shim |
| Polling resident loop | `live_agent_runner.py` | inventory and deprecate |
| Run-group process supervision | `live_agent_processes.py` | isolate; do not merge with canonical bridge manager |
| Meeting/config session composition | `live_agent_sessions.py` | compatibility freeze |
| Legacy provider smoke | `live_agent_smoke.py` | replace with canonical smoke before deletion |
| Disabled flow supervisor | `LiveAgentFlowSupervisor` | retain 410 tombstone until callers are removed |
| Legacy lobby HTTP/SSE | `gui_legacy_lobby_http.py` | compatibility freeze |

Deletion requires evidence that the GUI, CLI, release health, documentation,
and tests no longer consume the legacy contract. Do not deeply refactor a
legacy module merely to reduce its line count.

## Refactoring Rule

Keep coordinators responsible for command validation and delegation. Move a
boundary only when the extracted owner has a distinct state lifetime, failure
mode, side effect, or verification path. Preserve compatibility exports only
for existing callers; new code imports the actual owner module directly.
