# Live Agent Stop Session Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. In this repository, subagents are limited to planning and review only; the main Codex session performs implementation. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a first-class resident `stop-session` path that stops a supervised group for an existing meeting and immediately marks its bound agents offline as operator-visible evidence.

**Architecture:** `agentsassemble.live_agent_sessions` owns meeting-aware session orchestration. The GUI API records one sanitized `session.stop` operation, the CLI exposes the same control surface, and the lobby offers a `세션중지` button beside session start/resume without touching the existing dirty static shell files.

**Tech Stack:** Python standard library HTTP/CLI, existing live-agent supervisor and roster state, unittest, Node runtime smoke tests.

---

### Task 1: Session Stop Service

**Files:**
- Modify: `agentsassemble/live_agent_sessions.py`
- Test: `tests/test_live_agent_sessions.py`

- [x] **Step 1: Write failing tests**

Add tests proving `stop_live_agent_session()` requires an existing meeting, calls `stop_group()` for the requested normalized group id, preserves the meeting record, and marks every bound meeting agent offline only after process stop succeeds.

- [x] **Step 2: Verify RED**

Run: `python3 -m unittest tests.test_live_agent_sessions.LiveAgentSessionStartTests`

Expected: fails because `stop_live_agent_session` is not implemented.

- [x] **Step 3: Implement minimal service**

Add `stop_live_agent_session()` beside `start_live_agent_session()` and `resume_live_agent_session()`. It loads the existing meeting, derives bound agents, stops the group through the supervisor, then updates bound roster rows to `offline` with meeting evidence. It returns sanitized meeting, group, process, and offline-count summaries only.

- [x] **Step 4: Verify GREEN**

Run: `python3 -m unittest tests.test_live_agent_sessions.LiveAgentSessionStartTests`

Expected: the expanded service test class passes.

### Task 2: API And Operation Logging

**Files:**
- Modify: `agentsassemble/gui.py`
- Test: `tests/test_gui_server.py`

- [x] **Step 1: Write failing tests**

Add tests for `POST /api/live-agent-sessions/stop`: success stops the group, marks bound agents offline, records a sanitized `session.stop` operation, and missing meeting/group errors do not leak process internals.

- [x] **Step 2: Verify RED**

Run targeted GUI stop-session tests.

Expected: route not found or missing payload wrapper failure.

- [x] **Step 3: Implement route and sanitizer**

Add `live_agent_session_stop_payload()`, `/api/live-agent-sessions/stop`, `_session_stop_operation_details()`, and safe stop-session error handling.

- [x] **Step 4: Verify GREEN**

Run the targeted GUI tests.

Expected: tests pass.

### Task 3: CLI Surface

**Files:**
- Modify: `agentsassemble/cli.py`
- Test: `tests/test_cli_timeout.py`

- [x] **Step 1: Write failing tests**

Add parser and request tests for `live-agent stop-session --meeting-id <id> --group-id <id>`.

- [x] **Step 2: Verify RED**

Run the targeted CLI stop-session tests.

Expected: parser rejects `stop-session`.

- [x] **Step 3: Implement CLI**

Add parser entries and `_run_live_agent_stop_session()` posting to `/api/live-agent-sessions/stop`. Print a compact summary and exit `0` only for `status: "stopped"`.

- [x] **Step 4: Verify GREEN**

Run the targeted CLI tests.

Expected: tests pass.

### Task 4: Lobby Runtime Control

**Files:**
- Modify: `agentsassemble/static/lobby.js`
- Test: `tests/static_lobby_runtime_smoke.mjs`
- Test: `tests/test_static_ui_assets.py`

- [x] **Step 1: Write failing tests**

Add a smoke test that clicks `세션중지` and expects a POST to `/api/live-agent-sessions/stop` with meeting id and group id only.

- [x] **Step 2: Verify RED**

Run: `node tests/static_lobby_runtime_smoke.mjs`

Expected: fails because the button/function does not exist.

- [x] **Step 3: Implement UI wiring**

Add the `세션중지` button and `stopLiveAgentSession()` function. Reuse existing runtime refresh and status rendering.

- [x] **Step 4: Verify GREEN**

Run `node tests/static_lobby_runtime_smoke.mjs` and `python3 -m unittest tests.test_static_ui_assets.StaticUiAssetTests`.

Expected: both pass.

### Task 5: Docs, Review, Commit

**Files:**
- Modify: `docs/live-agent-ops.md`
- Modify: `docs/roadmap.md`

- [x] **Step 1: Update docs**

Document `stop-session`, its safety boundary, offline roster evidence, and sanitized operation details.

- [x] **Step 2: Request xhigh review**

Dispatch a review-only subagent with vowline instructions and this plan as context.

- [x] **Step 3: Fix Important or Critical findings**

Apply fixes locally; subagents do not implement.

- [x] **Step 4: Full verification**

Run:

```bash
python3 -m unittest discover -s tests
python3 -m compileall -q agentsassemble
node --check agentsassemble/static/app.js
node --check agentsassemble/static/archive.js
node --check agentsassemble/static/lobby.js
node --check agentsassemble/static/meeting-views.js
node --check agentsassemble/static/shared.js
node --check tests/static_lobby_runtime_smoke.mjs
git diff --check
```

Expected: all commands exit 0.

- [ ] **Step 5: Commit and authorized push**

Commit only the coherent stop-session slice, excluding the pre-existing dirty `agentsassemble/static/base.css` and `agentsassemble/static/index.html`. Push only when the current session has explicit user authorization to publish the branch.
