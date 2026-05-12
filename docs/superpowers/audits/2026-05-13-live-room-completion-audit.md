# Live Room Completion Audit

Date: 2026-05-13
Branch: `codex/live-room-council-foundation`
Status: review pending, not goal-complete until human GUI review passes

## Objective Restatement

Implement an AgentsAssemble live-room/council-workflow foundation with these success criteria:

1. Document the Stoops and Claude Code Channels lessons.
2. Add SSE room event streaming for lobby, side chat, and official meeting events.
3. Introduce moderator turn-control semantics.
4. Add engagement modes that separate free chat from official meeting turns.
5. Verify the implementation with tests and syntax/build checks.
6. Commit coherent slices.
7. Do not mark the active goal complete until the user reviews the GUI.

## Prompt To Artifact Checklist

| Requirement | Concrete Evidence | Verification State |
| --- | --- | --- |
| Stoops lessons documented | `docs/live-session-room-model.md`, `docs/provider-architecture.md`, `docs/roadmap.md`, `docs/research-log.md`, `docs/superpowers/specs/2026-05-11-live-room-council-foundation-design.md` | Covered by `tests/test_docs_architecture.py` checking Stoops and Claude Code Channels references. |
| Claude Code Channels lessons documented | `docs/provider-architecture.md` section "Claude Code Channels And Custom Channels"; roadmap notes sender-gated custom channels and read-only semantics | Covered by docs architecture test plus manual audit. |
| Live room is not only a chatroom | `docs/live-session-room-model.md` separates live-room transport from official council workflow; `docs/provider-architecture.md` frames Stoops as infrastructure reference | Covered by audit mapping, not a runtime behavior test. |
| Lobby event stream exists | `agentsassemble/gui.py` exposes `/api/events/lobby`; `_sse_event`, `_stream_snapshot_payload`, and `_send_sse_stream` support the stream | Covered by `tests/test_gui_server.py` SSE formatting, snapshot, and heartbeat tests. |
| Side-chat event stream exists | `agentsassemble/gui.py` exposes `/api/events/side-chat`; browser subscribes through `EventSource` | Covered by `tests/test_gui_server.py` snapshot tests and `tests/test_static_ui_assets.py` EventSource assertions. |
| Official meeting event stream exists | `agentsassemble/gui.py` exposes `/api/meetings/<meeting_id>/events`; browser subscribes through `EventSource` | Covered by `tests/test_gui_server.py` meeting stream tests and static asset tests. |
| Lobby, side chat, and official events are separable | `agentsassemble/meeting_events.py` records `channel`, `audience`, and `official_record`; lobby and side chat are unofficial | Covered by `tests/test_gui_server.py::test_room_events_record_channel_audience_and_official_record_boundary`. |
| Free chat does not enter official record by default | `agentsassemble/artifact_public.py` states informal lobby and side chat are excluded; event metadata marks them non-official | Covered by event boundary tests and artifact wording checks. |
| Moderator turn-control model exists | `agentsassemble/models.py` defines `RoundTurnControl`; `agentsassemble/config.py` parses it | Covered by `tests/test_debate_turn_control.py` and `tests/test_config.py`. |
| Official turns carry turn metadata | `agentsassemble/meeting_phases.py` stamps `turn_id`, `turn_index`, and `engagement_mode` | Covered by `tests/test_debate_turn_control.py` and `tests/test_demo_meeting.py`. |
| Engagement modes are configurable | `agentsassemble/models.py` defines `EngagementMode`; `agentsassemble/config.py` normalizes and parses `engagement_mode` | Covered by `tests/test_config.py::test_agent_binding_engagement_mode_can_be_configured`. |
| Meeting stays read-only by default | Provider registry rejects implementation-side permissions during meeting-only runs | Covered by provider registry, local CLI, and remote bridge adapter tests listed in the foundation audit. |
| Browser updates avoid full refresh churn | `agentsassemble/static/app.js` uses EventSource streams and payload signatures; side-chat polling refresh updates only side chat | Covered by `tests/test_static_ui_assets.py` static hooks and GUI server stream tests. |
| Lobby review regression fixed | `agentsassemble/static/lobby.css` reserves a stable scrollbar gutter and owner-bubble right margin | Covered by `tests/test_static_ui_assets.py` CSS hook assertions; needs human visual review. |
| Side-chat Enter regression fixed | `agentsassemble/static/meeting-views.js` clears the input before awaiting `/api/side-chat` and restores only on send failure | Covered by `tests/test_static_ui_assets.py` ordering assertions; needs human visual review. |
| Coherent commits exist | Recent commits include `7279698 Fix lobby bubble and side chat input regressions` and `4920a44 Update live room review audit after fixes`, after earlier focused foundation commits | Covered by `git log --oneline -12` audit output. |

## Latest Verification Commands

```text
python3 -m unittest discover -s tests
Ran 145 tests
OK
```

Also previously verified after the latest code changes:

```text
python3 -m compileall -q agentsassemble
node --check agentsassemble/static/app.js
node --check agentsassemble/static/lobby.js
node --check agentsassemble/static/meeting-views.js
git diff --check
```

## Local Runtime Smoke

Latest local server smoke against `http://127.0.0.1:8765/` verified:

- `GET /api/meetings` returns meeting records.
- `GET /api/lobby` returns lobby events with `channel: "lobby"` and `official_record: false`.
- `GET /static/lobby.css` serves the latest owner-bubble gutter/margin fix.
- `GET /static/meeting-views.js` serves the latest side-chat optimistic-clear logic.
- `GET /api/events/lobby` returns an SSE `event: lobby` payload followed by `: keep-alive`.
- `GET /api/events/side-chat` returns an SSE `event: side_chat` payload followed by `: keep-alive`.
- `GET /api/meetings/<meeting_id>/events` returns an SSE `event: meeting` payload followed by `: keep-alive`.

## Completion Gate

Do not mark the active goal complete yet.

The implementation has evidence for the requested foundation, but the user has not yet passed final GUI review after commit `7279698`. Required human checks are listed in `docs/live-room-review-checklist.md`, especially:

- Lobby owner bubbles no longer clip at the right edge.
- Live side-chat Enter submissions clear the visible input.
- Lobby and meeting streams do not repeatedly show reconnect churn during idle use.
- Official transcript and unofficial side-chat remain visually and behaviorally distinct.

## Known Limits

- The room transport is file-backed SSE, not provider-native live session attachment.
- Real Claude Code Channels integration remains future work.
- Friend-hosted remote bridge behavior still needs external network validation.
- Meeting read-only guarantees are policy/audit metadata unless paired with OS-level sandboxing.
