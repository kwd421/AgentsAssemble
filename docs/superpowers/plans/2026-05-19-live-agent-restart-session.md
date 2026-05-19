# Live Agent Restart Session Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. In this repository, subagents are limited to planning and review only; the main Codex session performs implementation. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a meeting-aware resident `restart-session` path that restarts one existing live-agent process group for one existing meeting and returns fresh readiness evidence.

**Architecture:** `agentsassemble.live_agent_sessions` owns the restart policy: validate the existing meeting, validate the current supervised group manifest against the meeting bindings before touching processes, stop currently `running` or `restarting` groups, mark the meeting-bound roster offline so stale online rows cannot prove readiness, restart the supervised group, re-check the returned process manifest for extras or duplicates, then wait briefly for fresh `online` or `working` presence. The GUI API records one sanitized `session.restart` operation, the CLI exposes the same payload, and the lobby adds a `세션재시작` control beside resume/check/stop.

**Tech Stack:** Python standard library HTTP/CLI, existing live-agent supervisor and roster state, unittest, Node runtime smoke tests.

---

### Task 1: Restart Session Service

**Files:**
- Modify: `agentsassemble/live_agent_sessions.py`
- Test: `tests/test_live_agent_sessions.py`

- [x] **Step 1: Write failing tests**

Add tests proving `restart_live_agent_session()`:
- requires an existing meeting and explicit group id;
- validates the existing process group manifest against the meeting's bound agent ids before calling `restart_group()`;
- calls `restart_group(clean_group_id)` exactly once when the manifest matches;
- marks only agents bound to that meeting `offline` before waiting for fresh presence;
- does not create or rewrite the meeting record;
- returns `status: "ready"` only when the restarted process is `running`, the process manifest covers the expected agents, and all expected agents reconnect as `online` or `working`;
- returns `status: "starting"` when the process restarted but fresh presence is still missing or offline;
- refuses manifest mismatch without restarting or marking roster rows offline;
- leaves wrong-meeting roster rows untouched and reports `wrong_meeting` attention.

- [x] **Step 2: Verify RED**

Run:

```bash
python3 -m unittest tests.test_live_agent_sessions.LiveAgentSessionStartTests.test_restart_session_restarts_group_and_requires_fresh_presence tests.test_live_agent_sessions.LiveAgentSessionStartTests.test_restart_session_refuses_manifest_mismatch_before_restart tests.test_live_agent_sessions.LiveAgentSessionStartTests.test_restart_session_leaves_wrong_meeting_roster_row_untouched
```

Expected: import or attribute failure because `restart_live_agent_session()` does not exist.

- [x] **Step 3: Implement minimal service**

Add `restart_live_agent_session(output_root, process_supervisor, *, meeting_id, group_id, connect_timeout_seconds=5.0)`. Reuse `_clean_existing_meeting_id()`, `_read_existing_meeting()`, `_expected_agents_from_meeting()`, `_validate_stop_group_matches_meeting()`, `_mark_bound_agents_offline()`, `_wait_for_connections()`, `_process_snapshot()`, and safe summaries. Add `LiveAgentSessionRestartError` if restart failures need safe meeting id propagation.

The implementation must call `process_supervisor.restart_group(clean_group_id)` only after meeting existence and manifest validation pass.

- [x] **Step 4: Verify GREEN**

Run the targeted service tests from Step 2.

Expected: service restart tests pass.

### Task 2: API And Operation Logging

**Files:**
- Modify: `agentsassemble/gui.py`
- Test: `tests/test_gui_server.py`

- [x] **Step 1: Write failing tests**

Add tests for `POST /api/live-agent-sessions/restart`: success returns the restart snapshot and records one sanitized `session.restart` operation; missing meeting/group errors return safe details; the operation excludes config paths, command args, endpoints, auth refs, prompts, log tails, provider output, replies, and official turn content.

- [x] **Step 2: Verify RED**

Run targeted GUI tests.

Expected: route not found or missing helper failure.

- [x] **Step 3: Implement route and sanitizer**

Add `live_agent_session_restart_payload()`, `/api/live-agent-sessions/restart`, `_session_restart_operation_summary()`, `_session_restart_error_message()`, and reuse `_session_start_operation_status()` plus `_session_start_operation_details()` for ready/starting evidence.

- [x] **Step 4: Verify GREEN**

Run the targeted GUI tests.

Expected: restart API tests pass.

### Task 3: CLI Surface

**Files:**
- Modify: `agentsassemble/cli.py`
- Test: `tests/test_cli_timeout.py`

- [x] **Step 1: Write failing tests**

Add parser and request tests for:

```bash
python3 -m agentsassemble.cli live-agent restart-session \
  --server http://room.local \
  --meeting-id resident-m1 \
  --group-id resident-main \
  --connect-timeout 7
```

The CLI should POST only `meeting_id`, `group_id`, and `connect_timeout_seconds` to `/api/live-agent-sessions/restart`, print the resident session summary, exit `0` for `ready`, and exit `1` for `starting`.

- [x] **Step 2: Verify RED**

Run targeted CLI restart tests.

Expected: parser rejects `restart-session`.

- [x] **Step 3: Implement CLI**

Add parser entries and `_run_live_agent_restart_session(args)`. Reuse `_format_live_agent_session_start()` for its compact summary because restart returns connection/process readiness, not offline counts.

- [x] **Step 4: Verify GREEN**

Run targeted CLI restart tests.

Expected: CLI tests pass.

### Task 4: Lobby Runtime Control

**Files:**
- Modify: `agentsassemble/static/shared.js`
- Modify: `agentsassemble/static/lobby.js`
- Test: `tests/static_lobby_runtime_smoke.mjs`
- Test: `tests/test_static_ui_assets.py`

- [x] **Step 1: Write failing tests**

Add a smoke test that clicks `세션재시작` and expects a POST to `/api/live-agent-sessions/restart` with `meeting_id`, `group_id`, and `connect_timeout_seconds`. Add static assertions for the button, function, endpoint, and dedicated busy flag.

- [x] **Step 2: Verify RED**

Run:

```bash
node tests/static_lobby_runtime_smoke.mjs
python3 -m unittest tests.test_static_ui_assets.StaticUiAssetTests.test_lobby_separates_stage_from_activity_feed
```

Expected: fails because restart UI wiring does not exist.

- [x] **Step 3: Implement UI wiring**

Add `liveAgentSessionRestartRunning`, the `세션재시작` button, `restartLiveAgentSession()`, and a status message using the same status tone and connected-count summary as start/resume. After restart completes, refresh live-agent runtime surfaces because restart intentionally changes process and roster state.

- [x] **Step 4: Verify GREEN**

Run the smoke and static UI tests from Step 2.

Expected: both pass.

### Task 5: Docs, Review, Commit

**Files:**
- Modify: `docs/live-agent-ops.md`
- Modify: `docs/roadmap.md`
- Modify: `tests/test_docs_architecture.py`

- [x] **Step 1: Update docs**

Document `restart-session`, its meeting-aware manifest gate, stale-presence reset, ready/starting status contract, CLI exit codes, GUI control, and sanitized `session.restart` operation logging.

- [x] **Step 2: Request xhigh review**

Dispatch or reuse a review-only subagent with vowline instructions and this plan as context. Subagents must not edit files.

- [x] **Step 3: Fix Critical or Important findings**

Apply fixes locally; subagents do not implement.

Review found two Important issues and no Critical issues. Fixed locally by stopping `restarting` process groups before stale-presence reset and by revalidating the restarted group's returned manifest before reporting `ready`.

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

Commit only the coherent restart-session slice, excluding pre-existing dirty `agentsassemble/static/base.css` and `agentsassemble/static/index.html`. Push to `origin/codex/live-room-council-foundation` because the user already authorized pushing this branch.
