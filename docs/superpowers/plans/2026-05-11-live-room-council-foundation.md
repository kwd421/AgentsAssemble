# Live Room Council Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the first file-backed live room foundation: typed room events, SSE GUI updates, moderator turn metadata, and engagement modes while keeping informal chat separate from official council records.

**Architecture:** Keep the existing dependency-light Python HTTP server and static JS GUI. Add small focused modules and tests around the existing `meeting_events.py` and `gui.py` boundaries rather than replacing the app with a new framework. Treat SSE as a transport over already-public event data; artifacts remain file-based.

**Tech Stack:** Python standard library HTTP server, JSONL file persistence, static ES modules, Python `unittest`.

---

## Current Status

The implementation slice has been executed through commit `da39a98 Harden live room recovery refresh paths`.

The active completion gate is still human review. Do not mark the thread goal complete until the user has inspected the GUI and accepted the foundation.

Latest verification evidence:

```bash
python3 -m unittest discover -s tests
# Ran 145 tests - OK

python3 -m compileall -q agentsassemble
node --check agentsassemble/static/app.js
node --check agentsassemble/static/lobby.js
node --check agentsassemble/static/meeting-views.js
git diff --check
```

Latest review fixes:

- Partial `meeting.json` now falls back to `live_state.json` for normal meeting list/payload APIs.
- Polling fallback side-chat updates now refresh only the side-chat feed instead of forcing the official transcript to jump.
- Side-chat drafts are preserved across side-chat feed refresh even when the input is not focused.

## File Structure

- Modify `agentsassemble/meeting_events.py`: extend event typing with room channel, audience, official-record eligibility, sequence helpers, and engagement-mode normalization.
- Modify `agentsassemble/gui.py`: add SSE endpoints for lobby, side chat, and meeting live events; keep JSON endpoints as fallback.
- Modify `agentsassemble/static/app.js`: subscribe to SSE streams and update local state without full polling churn.
- Modify `agentsassemble/static/lobby.js`: preserve input focus after sends and avoid full re-render side effects where possible.
- Modify `agentsassemble/static/meeting-views.js`: show official/side-chat separation and engagement metadata where already available.
- Modify `agentsassemble/models.py`: add engagement mode literal/default to `AgentBinding`.
- Modify `agentsassemble/config.py`: parse `engagement_mode` from runtime config.
- Modify `agentsassemble/meeting.py` and/or `agentsassemble/meeting_phases.py`: record moderator control metadata and binding engagement snapshots in `meeting.json`.
- Modify `docs/live-session-room-model.md`, `docs/provider-architecture.md`, `docs/roadmap.md`, `docs/research-log.md`: record Stoops/Claude Channels lessons and the council-workflow distinction.
- Test `tests/test_gui_server.py`: SSE formatting, event stream content, informal/official separation.
- Test `tests/test_config.py`: engagement mode config parsing and defaults.
- Test `tests/test_demo_meeting.py`: meeting artifact includes moderator turn-control and engagement snapshots.
- Test `tests/test_static_ui_assets.py`: static JS contains SSE subscription and official/free-chat separation hooks.

## Task 1: Document The Product Boundary

**Files:**
- Modify: `docs/live-session-room-model.md`
- Modify: `docs/provider-architecture.md`
- Modify: `docs/roadmap.md`
- Modify: `docs/research-log.md`
- Already added: `docs/superpowers/specs/2026-05-11-live-room-council-foundation-design.md`
- Already added: `docs/superpowers/plans/2026-05-11-live-room-council-foundation.md`

- [ ] **Step 1: Add roadmap/research tests if existing docs tests need explicit assertions**

Run:

```bash
python3 -m unittest tests.test_docs_architecture -v
```

Expected before edits: current tests pass, but they do not assert the new Stoops/Channels distinction.

- [ ] **Step 2: Update docs**

Add concise sections documenting:

- Stoops as live-room infrastructure reference.
- Claude Code Channels as official external-event push reference.
- AgentsAssemble as governed council workflow.
- Free chat is included but not official by default.
- Engagement modes prevent agent loops.

- [ ] **Step 3: Run docs tests**

Run:

```bash
python3 -m unittest tests.test_docs_architecture -v
```

Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add docs
git commit -m "Document live room council boundary"
```

## Task 2: Add Typed Room Events And Engagement Modes

**Files:**
- Modify: `agentsassemble/meeting_events.py`
- Modify: `agentsassemble/models.py`
- Modify: `agentsassemble/config.py`
- Test: `tests/test_config.py`
- Test: `tests/test_gui_server.py`

- [ ] **Step 1: Write failing event tests**

Add tests proving:

- lobby events have `channel: "lobby"` and `official_record: false`.
- side chat events have `channel: "side_chat"` and `official_record: false`.
- live official events have `channel: "official"` and `official_record: true` when `kind` is `message` or `synthesis`.
- invalid engagement modes normalize to `manual`.
- event reads can resume after a known event id without replaying the whole feed.

Run:

```bash
python3 -m unittest tests.test_gui_server tests.test_config -v
```

Expected: FAIL because the new fields and parser are missing.

- [ ] **Step 2: Implement minimal event fields**

Add fields without changing existing consumers:

- `LobbyEvent.to_dict()` includes `channel`, `official_record`, `audience`.
- `append_side_chat_event_to_file()` writes side-chat events with `channel: "side_chat"`.
- `append_live_event()` includes `channel`, `official_record`, `audience`.
- `read_lobby_events_after()`, `read_side_chat_events_after()`, and `read_live_events_after()` reuse existing readers and filter by event id for SSE resume.
- Add `EngagementMode` literal and normalization.
- `AgentBinding.to_dict()` includes `engagement_mode`.

- [ ] **Step 3: Parse engagement mode**

`agent_bindings_from_config()` should accept `engagement_mode`, defaulting to `moderator_called` for agent bindings unless the config explicitly says otherwise.

- [ ] **Step 4: Run targeted tests**

Run:

```bash
python3 -m unittest tests.test_gui_server tests.test_config -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add agentsassemble/meeting_events.py agentsassemble/models.py agentsassemble/config.py tests/test_gui_server.py tests/test_config.py
git commit -m "Add room event metadata"
```

## Task 3: Add SSE Room Event Endpoints

**Files:**
- Modify: `agentsassemble/gui.py`
- Test: `tests/test_gui_server.py`

- [ ] **Step 1: Write failing SSE tests**

Add tests for helper functions rather than opening a long-lived server:

- `_sse_event("lobby", {"id": "1"})` returns `event: lobby` and `data: {"id": "1"}` lines ending with a blank line.
- `_sse_event("lobby", {"id": "1"}, event_id="1")` includes `id: 1`.
- `_stream_snapshot_payload(output_root, "lobby")` returns current lobby events.
- `_stream_snapshot_payload(output_root, "side_chat")` returns current side-chat events.
- `_stream_snapshot_payload(output_root, "meeting", meeting_id)` returns live events and a payload signature.
- `_stream_snapshot_payload(..., last_event_id="abc")` returns only events after `abc` when the id exists.

Run:

```bash
python3 -m unittest tests.test_gui_server -v
```

Expected: FAIL because helpers are missing.

- [ ] **Step 2: Implement SSE helpers**

Add:

- `_sse_event(event_name: str, payload: dict[str, object]) -> bytes`
- `_stream_snapshot_payload(output_root: Path, stream: str, meeting_id: str | None = None, last_event_id: str | None = None) -> dict[str, object]`
- `_send_sse_snapshot(self, event_name: str, payload: dict[str, object])`

Do not implement infinite loops in tests. The endpoint can send one snapshot then close for v0, or be structured so a later loop can be added safely.

- [ ] **Step 3: Add GET routes**

Routes:

- `/api/events/lobby`
- `/api/events/side-chat`
- `/api/meetings/<meeting_id>/events`

Each returns `text/event-stream`, `Cache-Control: no-cache`, and only public payloads. V0 may send a snapshot and close; helpers should still accept `Last-Event-ID` so a later long-lived stream can reuse the same contract.

- [ ] **Step 4: Run targeted tests**

Run:

```bash
python3 -m unittest tests.test_gui_server -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add agentsassemble/gui.py tests/test_gui_server.py
git commit -m "Add room event SSE snapshots"
```

## Task 4: Subscribe The Browser To Room Events

**Files:**
- Modify: `agentsassemble/static/app.js`
- Modify: `agentsassemble/static/lobby.js`
- Modify: `agentsassemble/static/meeting-views.js`
- Test: `tests/test_static_ui_assets.py`

- [ ] **Step 1: Write failing static asset assertions**

Assert static JS contains:

- `new EventSource("/api/events/lobby")`
- `new EventSource("/api/events/side-chat")`
- `new EventSource(\`/api/meetings/${encodeURIComponent(meetingId)}/events\`)`
- `function connectRoomStreams`
- `function applyLobbyStreamPayload`
- `function applySideChatStreamPayload`
- `function applyMeetingStreamPayload`

Run:

```bash
python3 -m unittest tests.test_static_ui_assets -v
```

Expected: FAIL.

- [ ] **Step 2: Implement browser subscriptions**

Add `connectRoomStreams()` in `app.js`.

Rules:

- Close and replace the meeting `EventSource` when meeting selection changes.
- Keep existing polling as fallback, but slow it down or avoid duplicate render when stream signatures match.
- Update lobby/side-chat state through existing setters.
- Preserve text input focus after sends.

- [ ] **Step 3: Run static and JS syntax tests**

Run:

```bash
python3 -m unittest tests.test_static_ui_assets -v
rg --files agentsassemble/static -g '*.js' | xargs -n1 node --check
```

Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add agentsassemble/static tests/test_static_ui_assets.py
git commit -m "Subscribe GUI to room event streams"
```

## Task 5: Record Moderator Turn-Control Semantics

**Files:**
- Modify: `agentsassemble/meeting.py`
- Modify: `agentsassemble/meeting_phases.py`
- Modify: `agentsassemble/artifact_public.py`
- Test: `tests/test_demo_meeting.py`
- Test: `tests/test_static_ui_assets.py`

- [ ] **Step 1: Write failing meeting artifact tests**

Assert demo meeting JSON includes:

- `moderator_control.default_official_engagement == "moderator_called"`
- `moderator_control.informal_default_engagement == "mentioned"`
- `moderator_control.official_record_channels` contains `official`
- each binding snapshot has `engagement_mode`
- each debate message has `turn_id`, `turn_index`, and orchestrator-stamped `engagement_mode`
- each debate round records deterministic `turn_control`

Run:

```bash
python3 -m unittest tests.test_demo_meeting -v
```

Expected: FAIL.

- [ ] **Step 2: Implement metadata**

Add `moderator_control` to the meeting payload before artifacts are written:

```json
{
  "moderator_id": "moderator",
  "official_channel": "official",
  "informal_channels": ["lobby", "side_chat"],
  "default_official_engagement": "moderator_called",
  "informal_default_engagement": "mentioned",
  "official_record_channels": ["official"],
  "host_approval_required_for": ["implementation", "commit", "push", "pr", "deploy", "release"]
}
```

Add deterministic round turn metadata without changing adapter APIs:

- `debate_rounds[].turn_control.selection` defaults to `all_roles`.
- `debate_rounds[].turn_control.non_speaker_mode` defaults to `watch`.
- each official message gets `turn_id`, `turn_index`, and `engagement_mode: "moderator_called"`.
- skipped or observing roles should not look like adapter failures.

- [ ] **Step 3: Surface in decision/transcript where concise**

Add a short artifact note that informal chat is not official evidence unless promoted.

- [ ] **Step 4: Run targeted tests**

Run:

```bash
python3 -m unittest tests.test_demo_meeting tests.test_static_ui_assets -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add agentsassemble/meeting.py agentsassemble/meeting_phases.py agentsassemble/artifact_public.py tests/test_demo_meeting.py tests/test_static_ui_assets.py
git commit -m "Record moderator turn control"
```

## Task 6: Final Verification And Review

**Files:**
- All touched files.

- [ ] **Step 1: Run full verification**

Run:

```bash
python3 -m unittest discover -s tests
python3 -m compileall -q agentsassemble
rg --files agentsassemble/static -g '*.js' | xargs -n1 node --check
git diff --check
```

Expected: PASS.

- [ ] **Step 2: Run mock smoke**

Run:

```bash
python3 -m agentsassemble.cli demo --adapter mock --output-root /tmp/agentsassemble-live-room-council-smoke
```

Expected: creates meeting artifacts with no Python traceback.

- [ ] **Step 3: Request subagent review**

Ask a reviewer subagent to inspect the branch for:

- informal/official event leakage
- false claims of live-session support
- security leaks in SSE payloads
- overbroad refactors

- [ ] **Step 4: Fix review findings, rerun verification, commit if needed**

If review finds actionable issues, fix them in the smallest coherent commit and rerun the relevant checks.

Do not push unless the user explicitly asks.
