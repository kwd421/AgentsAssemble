# Live Room Council Foundation Audit

Date: 2026-05-11
Branch: `codex/live-room-council-foundation`

## Objective

Implement an AgentsAssemble live-room/council-workflow foundation:

- Document the Stoops and Claude Code Channels lessons.
- Add SSE room event streaming for lobby and official meeting events.
- Introduce moderator turn-control semantics.
- Add engagement modes that separate free chat from official meeting turns.
- Verify with tests.
- Commit coherent slices.
- Do not mark the active goal complete until the user reviews.

## Evidence Checklist

| Requirement | Evidence |
| --- | --- |
| Stoops and Claude Code Channels lessons documented | `docs/live-session-room-model.md`, `docs/provider-architecture.md`, `docs/roadmap.md`, `docs/research-log.md` mention Stoops, Claude Code Channels, live room infrastructure, sender-gated custom channels, and council workflow boundaries. |
| Council workflow remains distinct from chatroom transport | `docs/live-session-room-model.md` and `docs/provider-architecture.md` describe live room infrastructure as transport while official turns, transcript, Decision Gate, tasks, return packets, and memory remain council workflow concerns. |
| Lobby, side chat, and official events are distinguishable | `agentsassemble/meeting_events.py` stamps events with `channel`, `audience`, and `official_record`; tests in `tests/test_gui_server.py` verify lobby/side chat are not official record and official `message`/`synthesis` events are official. |
| SSE endpoints exist | `agentsassemble/gui.py` exposes lobby, side-chat, and meeting event streams using `_sse_event` and `_stream_snapshot_payload`; tests verify SSE formatting and stream separation. |
| Browser subscribes to event streams | `agentsassemble/static/app.js` creates `EventSource` subscriptions for `/api/events/lobby`, `/api/events/side-chat`, and `/api/meetings/<id>/events`; `tests/test_static_ui_assets.py` checks these hooks. |
| Final meeting state reaches GUI after completion | `agentsassemble/gui.py` includes `meeting_payload` after final `meeting.json` exists; `agentsassemble/static/app.js` applies it with `applyFullMeetingPayloadFromStream`; `tests/test_gui_server.py` verifies `live_status`, `decision_gate`, and `decision.md` are present. |
| Engagement modes exist and are configurable | `agentsassemble/models.py` defines `EngagementMode` and `normalize_engagement_mode`; `agentsassemble/config.py` parses `engagement_mode`; `tests/test_config.py` verifies defaults and explicit values. |
| Moderator turn control exists | `agentsassemble/models.py` defines `RoundTurnControl`; `agentsassemble/config.py` parses and validates turn control; `agentsassemble/meeting_phases.py` stamps messages with `turn_id`, `turn_index`, and `engagement_mode`. |
| Selected-role turn order is deterministic | `tests/test_debate_turn_control.py` verifies selected speakers run in declared order and skipped roles are recorded. |
| Meeting mode stays read-only by default | `agentsassemble/adapters/registry.py` rejects implementation-side permissions during meeting-only runs; `tests/test_provider_registry.py`, `tests/test_local_cli_adapter.py`, and `tests/test_remote_bridge_adapter.py` verify read-only permission envelopes. |
| Coherent commits exist | Branch contains focused commits for docs, event metadata, SSE snapshots, GUI subscriptions, moderator turn control, and streamed completion refresh. |

## Verification Run

Latest full verification:

```text
python3 -m unittest discover -s tests
Ran 139 tests
OK
```

Additional checks run during the implementation:

```text
python3 -m compileall -q agentsassemble
node --check agentsassemble/static/app.js
node --check agentsassemble/static/shared.js
node --check agentsassemble/static/meeting-views.js
git diff --check
```

Browser/runtime checks were also performed against a temporary mock meeting. The GUI showed final state in Live, Board, and Archive, and the meeting SSE endpoint returned `meeting_payload` with `decision_gate`, `live_status`, `channel`, and `official_record` fields.

## Known Limits

- The current SSE implementation is a file-backed snapshot/reconnect foundation, not a fully long-lived push loop with tmux or PTY session attachment.
- Real Claude Code Channels integration is documented as future work, not implemented.
- Real friend-hosted remote bridge behavior still needs external machine/Tailscale/network validation.
- Meeting read-only permissions for arbitrary CLI or remote bridge processes are policy and audit metadata unless paired with a real OS-level sandbox.
- The active thread goal remains uncompleted until user review.
