# Live-Agent Process Recover Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a first-class, operator-visible recover action for historical or orphaned resident live-agent process groups.

**Architecture:** Keep process recovery inside `LiveAgentProcessSupervisor` and expose it through the existing GUI/API/CLI process control surface. Recovery reuses the persisted group config/server/options, refuses owned running groups, records a distinct safe lifecycle/operation event, and avoids touching unrelated static `base.css` or `index.html` dirty work.

**Tech Stack:** Python stdlib supervisor, unittest, existing local HTTP GUI server, static ES modules, Node smoke tests.

---

### Task 1: Supervisor Recovery Primitive

**Files:**
- Modify: `agentsassemble/live_agent_processes.py`
- Test: `tests/test_live_agent_processes.py`

- [x] Add failing tests for `recover_group()` relaunching an `unknown` historical group from persisted config/server/options.
- [x] Assert the returned record is `running`, has `recovered_from_status: "unknown"`, resets manual restart budget like `restart_group()`, and appends a safe `recovered` lifecycle event with `previous_status`.
- [x] Add a failing test that an owned still-running group refuses recovery.
- [x] Implement `LiveAgentProcessSupervisor.recover_group(group_id)` using the same preflight and launch path as `restart_group()`.
- [x] Run targeted process supervisor tests.

### Task 2: API And CLI Surface

**Files:**
- Modify: `agentsassemble/gui.py`
- Modify: `agentsassemble/cli.py`
- Test: `tests/test_gui_server.py`
- Test: `tests/test_cli_timeout.py`

- [x] Add failing HTTP test for `POST /api/live-agent-processes/<group_id>/recover` returning the recovered group and recording sanitized `process.recover`.
- [x] Add failing CLI parser/request tests for `assemble live-agent processes recover <group_id>`.
- [x] Implement `recover_live_agent_process_payload()`, API route handling, safe operation details, and CLI action output.
- [x] Run targeted GUI and CLI tests.

### Task 3: Lobby Control And Docs

**Files:**
- Modify: `agentsassemble/static/lobby.js`
- Modify: `tests/static_lobby_runtime_smoke.mjs`
- Modify: `tests/test_static_ui_assets.py`
- Modify: `docs/live-agent-ops.md`
- Modify: `docs/roadmap.md`

- [x] Add failing static smoke coverage for an `unknown` process row posting `/recover` and showing a recovery status message.
- [x] Render a `복구` button for `unknown` and `error` groups, keeping `재시작` for stopped groups and `중지` for running/restarting groups.
- [x] Document recover vs restart and the sanitized `process.recover` evidence.
- [x] Run Node smoke, static UI assertions, and docs assertions.

### Task 4: Review And Verification

**Files:**
- Update: `docs/superpowers/plans/2026-05-19-process-recover.md`

- [x] Request xhigh review-only subagent review.
- [x] Fix blocking/important feedback with RED tests first.
- [x] Run `python3 -m unittest discover -s tests`.
- [x] Run `python3 -m compileall -q agentsassemble`.
- [x] Run `node --check agentsassemble/static/*.js` and `node --check tests/static_lobby_runtime_smoke.mjs`.
- [x] Run `git diff --check`.
- [x] Commit only this slice, excluding pre-existing dirty `agentsassemble/static/base.css` and `agentsassemble/static/index.html`, then push.
