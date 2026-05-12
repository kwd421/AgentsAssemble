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
| Lobby, side chat, and official events are distinguishable | `agentsassemble/meeting_events.py` stamps events with `channel`, `audience`, and `official_record`; tests in `tests/test_gui_server.py` verify lobby/side chat are not official record, official `message`/`synthesis` events are official, and official live events retain turn metadata. |
| SSE endpoints exist and keep connections open | `agentsassemble/gui.py` exposes lobby, side-chat, and meeting event streams using `_sse_event`, `_stream_snapshot_payload`, and `_send_sse_stream`; tests verify SSE formatting, stream separation, and lobby stream heartbeat behavior. |
| Browser subscribes to event streams | `agentsassemble/static/app.js` creates `EventSource` subscriptions for `/api/events/lobby`, `/api/events/side-chat`, and `/api/meetings/<id>/events`; `tests/test_static_ui_assets.py` checks these hooks. |
| Final meeting state reaches GUI after completion | `agentsassemble/gui.py` includes `meeting_payload` after final `meeting.json` exists, keeps the SSE stream alive if final JSON is temporarily partial, and falls back to `live_state.json` for normal meeting list/payload APIs while `meeting.json` is incomplete; `agentsassemble/static/app.js` applies full payloads with `applyFullMeetingPayloadFromStream`; `tests/test_gui_server.py` verifies `live_status`, `decision_gate`, `decision.md`, and partial-final-record resilience. |
| Engagement modes exist and are configurable | `agentsassemble/models.py` defines `EngagementMode` and `normalize_engagement_mode`; `agentsassemble/config.py` parses `engagement_mode`; `tests/test_config.py` verifies defaults and explicit values. |
| Moderator turn control exists | `agentsassemble/models.py` defines `RoundTurnControl`; `agentsassemble/config.py` parses and validates turn control; `agentsassemble/meeting_phases.py` stamps messages with `turn_id`, `turn_index`, and `engagement_mode`. |
| Selected-role turn order is deterministic | `tests/test_debate_turn_control.py` verifies selected speakers run in declared order and skipped roles are recorded. |
| Meeting mode stays read-only by default | `agentsassemble/adapters/registry.py` rejects implementation-side permissions during meeting-only runs; `tests/test_provider_registry.py`, `tests/test_local_cli_adapter.py`, and `tests/test_remote_bridge_adapter.py` verify read-only permission envelopes. |
| Side-chat refresh does not disturb official transcript state | `agentsassemble/static/app.js` uses `refreshSideChatFeed()` for polling fallback side-chat updates; `agentsassemble/static/meeting-views.js` preserves side-chat drafts even when focus is outside the input; `tests/test_static_ui_assets.py` checks these hooks. |
| Lobby owner bubbles avoid the scroll edge | `agentsassemble/static/lobby.css` reserves a stable scrollbar gutter and right-side owner bubble margin; `tests/test_static_ui_assets.py` checks the layout hooks. |
| Side-chat Enter submissions clear reliably | `agentsassemble/static/meeting-views.js` clears the draft before awaiting `/api/side-chat` and restores it only on send failure; `tests/test_static_ui_assets.py` checks the ordering. |
| Coherent commits exist | Branch contains focused commits for docs, event metadata, SSE streaming, GUI subscriptions, moderator turn control, streamed completion refresh, live-room UI feedback, SSE heartbeat coverage, and live-room recovery refresh paths. |

## Verification Run

Latest full verification:

```text
python3 -m unittest discover -s tests
Ran 145 tests
OK
```

Additional checks run during the implementation:

```text
python3 -m compileall -q agentsassemble
node --check agentsassemble/static/app.js
node --check agentsassemble/static/lobby.js
node --check agentsassemble/static/meeting-views.js
git diff --check
```

Browser/runtime checks were also performed against local mock meetings. The GUI showed final state in Live, Board, and Archive; lobby and side-chat inputs retained focus across Enter submissions; side-chat accepted consecutive messages without refocusing; reconnect banners stayed hidden after stable streams; Live renders non-official progress/research logs outside the official transcript panel; and the meeting SSE endpoint returned `meeting_payload` with `decision_gate`, `live_status`, `channel`, `official_record`, and turn metadata fields.

Latest local browser check opened `http://127.0.0.1:8765/` and confirmed the current GUI loads with Lobby, Live, and Archive surfaces available.

The latest xhigh-style review found two actionable issues: partial `meeting.json` could still break normal meeting APIs, and polling fallback side-chat refresh could force official transcript scroll. Commit `da39a98 Harden live room recovery refresh paths` fixed both and added regression coverage.

Human GUI review then found two more issues: right-aligned lobby bubbles could still clip near the scroll edge, and side-chat Enter submissions could leave the sent text visible after a refresh race. The pending follow-up fix adds stable lobby gutter/margin coverage and optimistic side-chat draft clearing with failure restore.

## Known Limits

- The current SSE implementation is a file-backed keep-alive stream, not a tmux, PTY, or provider-native live session attachment.
- Real Claude Code Channels integration is documented as future work, not implemented.
- Real friend-hosted remote bridge behavior still needs external machine/Tailscale/network validation.
- Meeting read-only permissions for arbitrary CLI or remote bridge processes are policy and audit metadata unless paired with a real OS-level sandbox.
- The active thread goal remains uncompleted until user review.
