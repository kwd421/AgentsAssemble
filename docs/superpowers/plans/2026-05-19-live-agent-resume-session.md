# Live Agent Resume Session Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. In this repository, subagents are limited to planning and review only; the main Codex session performs implementation. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a first-class `resume-session` path that reconnects or restarts a resident live-agent process group for an existing meeting without creating or overwriting the meeting.

**Architecture:** `agentsassemble.live_agent_sessions` owns the session-level orchestration. The GUI composes that orchestration with existing operation logging and optional remaining-round execution. CLI and lobby JS expose the same payload shape as `start-session`, while preserving safe operation details and current dirty-file exclusions.

**Tech Stack:** Python standard library HTTP/CLI, existing AgentsAssemble live-agent process supervisor, existing unittest and Node smoke tests.

---

### Task 1: Session Service

**Files:**
- Modify: `agentsassemble/live_agent_sessions.py`
- Test: `tests/test_live_agent_sessions.py`

- [ ] **Step 1: Write failing tests**

Add tests that prove `resume_live_agent_session()` requires an existing meeting, validates the resident manifest against that meeting, preserves offline roster rows without faking online readiness, reuses already running process groups, and starts a missing process group from the supplied config.

- [ ] **Step 2: Verify RED**

Run: `python3 -m unittest tests.test_live_agent_sessions.LiveAgentSessionStartTests`

Expected: fails because `resume_live_agent_session` is not implemented.

- [ ] **Step 3: Implement minimal service**

Add `resume_live_agent_session()` beside `start_live_agent_session()`. It must load `live_state.json`/`meeting.json` for `meeting_id`, derive expected agents from `agent_bindings` and `provider_configs`, validate the resident group config, ensure offline roster rows exist for missing bound agents, reuse an already-running normalized group id, and otherwise call `start_group()` with the supplied, just-validated config.

- [ ] **Step 4: Verify GREEN**

Run: `python3 -m unittest tests.test_live_agent_sessions.LiveAgentSessionStartTests`

Expected: all tests in the class pass.

### Task 2: API And Operation Logging

**Files:**
- Modify: `agentsassemble/gui.py`
- Test: `tests/test_gui_server.py`

- [ ] **Step 1: Write failing tests**

Add tests for `POST /api/live-agent-sessions/resume`: success records a sanitized `session.resume` operation, missing meeting returns a safe error, and `run_remaining_rounds` is skipped unless the resumed session is `ready`.

- [ ] **Step 2: Verify RED**

Run: targeted GUI resume tests with `python3 -m unittest tests.test_gui_server.GuiServerTests.<test_name>`.

Expected: 404/400 or missing route failure.

- [ ] **Step 3: Implement route and payload wrapper**

Add `live_agent_session_resume_payload()` and route `/api/live-agent-sessions/resume`, reusing the existing auto-round options and operation detail sanitizers.

- [ ] **Step 4: Verify GREEN**

Run the targeted GUI tests.

Expected: targeted GUI tests pass.

### Task 3: CLI Surface

**Files:**
- Modify: `agentsassemble/cli.py`
- Test: `tests/test_cli_timeout.py`

- [ ] **Step 1: Write failing tests**

Add parser and request tests for `assemble live-agent resume-session`, including restart options and optional remaining rounds.

- [ ] **Step 2: Verify RED**

Run: `python3 -m unittest tests.test_cli_timeout.CliTimeoutTests`

Expected: fails because `resume-session` is not parsed.

- [ ] **Step 3: Implement CLI**

Add parser entries and `_run_live_agent_resume_session()` that posts to `/api/live-agent-sessions/resume`, prints the same summary format as `start-session`, and returns nonzero unless the response is `ready` and requested auto-rounds are successful.

- [ ] **Step 4: Verify GREEN**

Run the targeted CLI tests.

Expected: targeted CLI tests pass.

### Task 4: Lobby Runtime Control

**Files:**
- Modify: `agentsassemble/static/lobby.js`
- Test: `tests/static_lobby_runtime_smoke.mjs`
- Test: `tests/test_static_ui_assets.py`

- [ ] **Step 1: Write failing tests**

Add a smoke test that clicks `세션재개` and expects a POST to `/api/live-agent-sessions/resume` with the same meeting/config/group/auto-round fields used by `세션시작`.

- [ ] **Step 2: Verify RED**

Run: `node tests/static_lobby_runtime_smoke.mjs`

Expected: fails because the button/function does not exist.

- [ ] **Step 3: Implement UI wiring**

Add a `세션재개` button and `resumeLiveAgentSession()` function. Do not touch `agentsassemble/static/base.css` or `agentsassemble/static/index.html`.

- [ ] **Step 4: Verify GREEN**

Run: `node tests/static_lobby_runtime_smoke.mjs` and `python3 -m unittest tests.test_static_ui_assets.StaticUiAssetTests`.

Expected: both pass.

### Task 5: Docs, Review, Commit

**Files:**
- Modify: `docs/live-agent-ops.md`
- Modify: `docs/roadmap.md`

- [ ] **Step 1: Update docs**

Document `resume-session`, its non-goals, sanitized evidence, and relation to process recover/restart.

- [ ] **Step 2: Request xhigh review**

Dispatch a review-only subagent with vowline instructions and this plan as context.

- [ ] **Step 3: Fix Important or Critical findings**

Apply fixes locally; do not ask subagents to implement.

- [ ] **Step 4: Full verification**

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

- [ ] **Step 5: Commit and push**

Commit only the coherent resume-session slice, excluding the pre-existing dirty `agentsassemble/static/base.css` and `agentsassemble/static/index.html`, then push the branch.
