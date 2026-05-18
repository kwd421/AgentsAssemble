# Start Session Auto Rounds Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an opt-in bounded path that starts a resident live-agent session and, only when it is ready, immediately runs remaining official template rounds.

**Architecture:** Keep orchestration at the GUI/API control-plane layer and reuse the existing `start_live_agent_session()` and `live_agent_turn_rounds_payload()` primitives. Do not move GUI route composition into lower-level session modules, and do not touch unrelated dirty `agentsassemble/static/base.css` or `agentsassemble/static/index.html`.

**Tech Stack:** Python stdlib HTTP server and unittest, existing AgentsAssemble live-agent modules, static ES modules, Node smoke tests.

---

### Task 1: API Auto-Rounds Composition

**Files:**
- Modify: `agentsassemble/gui.py`
- Test: `tests/test_gui_server.py`

- [x] Add failing HTTP tests proving `POST /api/live-agent-sessions/start` with `run_remaining_rounds: true` runs remaining rounds only after a `ready` session, includes sanitized `auto_rounds` evidence, and records a bounded operation detail.
- [x] Add failing HTTP test proving a `starting` session skips auto-rounds and does not append official turn requests.
- [x] Implement payload parsing for `run_remaining_rounds`, `round_timeout_seconds`, `round_max_rounds`, and `round_stop_on_timeout`.
- [x] Reuse `live_agent_turn_rounds_payload()` only when `session.status == "ready"`.
- [x] Run the targeted GUI server tests.

### Task 2: CLI Surface

**Files:**
- Modify: `agentsassemble/cli.py`
- Test: `tests/test_cli_timeout.py`

- [x] Add failing parser/payload tests for `live-agent start-session --run-remaining-rounds --round-timeout --max-rounds --stop-on-timeout`.
- [x] Add failing exit-code tests proving a degraded auto-round result exits `1` and a ready answered/complete result exits `0`.
- [x] Implement CLI args, bounded max-round validation, payload fields, HTTP timeout sizing, and summary rendering.
- [x] Run targeted CLI tests.

### Task 3: Lobby Opt-In Control And Docs

**Files:**
- Modify: `agentsassemble/static/lobby.js`
- Modify: `tests/static_lobby_runtime_smoke.mjs`
- Modify: `tests/test_static_ui_assets.py`
- Modify: `docs/live-agent-ops.md`
- Modify: `docs/roadmap.md`

- [x] Add failing static smoke coverage for an opt-in session auto-rounds checkbox posting the new fields.
- [x] Render the checkbox in the existing `상주 실행` form, using existing timeout/max-round/stop-on-timeout fields.
- [x] Update status text to show both session connection and auto-round result when present.
- [x] Document the opt-in automatic path and its `starting` skip behavior.
- [x] Run Node static smoke, static UI assertions, and docs assertions.

### Task 4: Review And Verification

**Files:**
- Update: `docs/superpowers/plans/2026-05-19-start-session-auto-rounds.md`

- [x] Request xhigh review-only subagent review.
- [x] Fix blocking/important review feedback with RED tests first.
- [x] Run `python3 -m unittest discover -s tests`.
- [x] Run `python3 -m compileall -q agentsassemble`.
- [x] Run JS `node --check` for touched static/test modules.
- [x] Run `git diff --check`.
- [x] Commit only this slice, excluding pre-existing dirty `agentsassemble/static/base.css` and `agentsassemble/static/index.html`, then push.
