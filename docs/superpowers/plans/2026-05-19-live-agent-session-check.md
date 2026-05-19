# Live Agent Session Check Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. In this repository, subagents are limited to planning and review only; the main Codex session performs implementation. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a read-only resident `check-session` path that proves the current health of one visible resident meeting and its supervised live-agent group without starting, stopping, probing, running rounds, or rewriting runtime state.

**Architecture:** `agentsassemble.live_agent_sessions` owns the meeting-aware check snapshot by composing existing meeting bindings, process group manifest evidence, and live-agent presence. The GUI API records one sanitized `session.check` operation for explicit operator checks, the CLI exposes the same payload, and the lobby adds a `세션점검` control beside start/resume/stop.

**Tech Stack:** Python standard library HTTP/CLI, existing live-agent supervisor and roster state, unittest, Node runtime smoke tests.

---

### Task 1: Session Check Service

**Files:**
- Modify: `agentsassemble/live_agent_sessions.py`
- Test: `tests/test_live_agent_sessions.py`

- [x] **Step 1: Write failing tests**

Add tests proving `check_live_agent_session()`:
- requires an existing meeting and explicit group id;
- returns `status: "ready"` when the requested group is `running`, its manifest matches the meeting's bound agent ids, and all bound agents are `online` or `working` for that meeting;
- returns `status: "degraded"` with process/connection attention when the group is missing, stopped, mismatched, or bound agents are missing/offline/wrong-meeting;
- does not call supervisor start/stop/restart/recover methods and does not create or rewrite roster rows.

- [x] **Step 2: Verify RED**

Run targeted service tests.

Expected: import or behavior failure because `check_live_agent_session()` is not implemented.

- [x] **Step 3: Implement minimal service**

Add `check_live_agent_session()` beside start/resume/stop. It loads the existing meeting, derives expected bound agent ids, looks up the normalized group id with `list_groups()` when available, returns safe meeting/group/process/connection summaries, and sets `ready` only when process and connection evidence are both complete.

- [x] **Step 4: Verify GREEN**

Run the targeted service tests.

Expected: service inspect tests pass.

### Task 2: API And Operation Logging

**Files:**
- Modify: `agentsassemble/gui.py`
- Test: `tests/test_gui_server.py`

- [x] **Step 1: Write failing tests**

Add tests for `POST /api/live-agent-sessions/check`: success returns the read-only snapshot and records one sanitized `session.check` operation; missing meeting/group errors return safe details; the operation excludes config paths, command args, endpoints, auth refs, prompts, log tails, provider output, and replies.

- [x] **Step 2: Verify RED**

Run targeted GUI inspect tests.

Expected: route not found or missing payload wrapper failure.

- [x] **Step 3: Implement route and sanitizer**

Add `live_agent_session_check_payload()`, `/api/live-agent-sessions/check`, `_session_check_operation_details()`, status/summary helpers, and safe check error handling.

- [x] **Step 4: Verify GREEN**

Run the targeted GUI tests.

Expected: tests pass.

### Task 3: CLI Surface

**Files:**
- Modify: `agentsassemble/cli.py`
- Test: `tests/test_cli_timeout.py`

- [x] **Step 1: Write failing tests**

Add parser and request tests for `live-agent check-session --meeting-id <id> --group-id <id> --fail-on-degraded`.

- [x] **Step 2: Verify RED**

Run the targeted CLI inspect tests.

Expected: parser rejects `check-session`.

- [x] **Step 3: Implement CLI**

Add parser entries and `_run_live_agent_check_session()` posting to `/api/live-agent-sessions/check`. Print a compact summary and exit `0` for a successful request by default, or `1` for non-ready status when `--fail-on-degraded` is supplied.

- [x] **Step 4: Verify GREEN**

Run the targeted CLI tests.

Expected: tests pass.

### Task 4: Lobby Runtime Control

**Files:**
- Modify: `agentsassemble/static/shared.js`
- Modify: `agentsassemble/static/lobby.js`
- Test: `tests/static_lobby_runtime_smoke.mjs`
- Test: `tests/test_static_ui_assets.py`

- [x] **Step 1: Write failing tests**

Add a smoke test that clicks `세션점검` and expects a POST to `/api/live-agent-sessions/check` with meeting id and group id only. Add static assertions for the button, function, endpoint, and dedicated busy flag.

- [x] **Step 2: Verify RED**

Run `node tests/static_lobby_runtime_smoke.mjs` and the static UI asset test.

Expected: fails because the button/function/busy flag does not exist.

- [x] **Step 3: Implement UI wiring**

Add `liveAgentSessionCheckRunning`, the `세션점검` button, `checkLiveAgentSession()`, and a status message that reports session status, group id, connected count, and process status. Reuse runtime surface refresh.

- [x] **Step 4: Verify GREEN**

Run `node tests/static_lobby_runtime_smoke.mjs` and `python3 -m unittest tests.test_static_ui_assets.StaticUiAssetTests`.

Expected: both pass.

### Task 5: Docs, Review, Commit

**Files:**
- Modify: `docs/live-agent-ops.md`
- Modify: `docs/roadmap.md`
- Modify: `tests/test_docs_architecture.py`

- [x] **Step 1: Update docs**

Document `check-session`, its read-only boundary, status contract, operation logging, and sanitized evidence.

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

Commit only the coherent check-session slice, excluding the pre-existing dirty `agentsassemble/static/base.css` and `agentsassemble/static/index.html`. Push only when the current session has explicit user authorization to publish the branch.
