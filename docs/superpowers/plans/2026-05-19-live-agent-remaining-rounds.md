# Live Agent Remaining Rounds Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a bounded operator path that runs the remaining moderator-called official rounds for a resident live-agent meeting.

**Architecture:** Reuse the existing single-round primitive and add progress recording into `live_state.json` after answered rounds. Expose the bounded batch through API, CLI, and the existing Lobby `상주 실행` control surface without touching unrelated dirty UI files.

**Tech Stack:** Python stdlib HTTP server and unittest, existing AgentsAssemble live-agent modules, static ES modules, Node smoke tests.

---

### Task 1: Round Progress Helpers

**Files:**
- Modify: `agentsassemble/live_agent_rounds.py`
- Test: `tests/test_live_agent_rounds.py`

- [x] Add failing tests for template round order, completed round ids from `debate_rounds`, and remaining round selection with `max_rounds`.
- [x] Implement `template_round_ids()`, `completed_official_round_ids()`, and `remaining_official_round_ids()`.
- [x] Run `python3 -m unittest tests.test_live_agent_rounds`.

### Task 2: Single-Round Progress Persistence

**Files:**
- Modify: `agentsassemble/gui.py`
- Test: `tests/test_gui_server.py`

- [x] Add a failing test proving an answered `/round` call appends a minimal `debate_rounds` progress record to `live_state.json`.
- [x] Persist sanitized round id, role ids, status, and counts after an answered round while preserving richer existing round fields.
- [x] Run the targeted GUI server round tests.

### Task 3: Bounded Remaining-Rounds API And CLI

**Files:**
- Modify: `agentsassemble/gui.py`
- Modify: `agentsassemble/cli.py`
- Test: `tests/test_gui_server.py`
- Test: `tests/test_cli_timeout.py`

- [x] Add failing API tests for `POST /api/meetings/<meeting_id>/live-agent-turns/rounds` running only remaining template rounds, stopping on timeout, avoiding duplicate concurrent scheduling, and recording sanitized `official_turn.rounds` operation details.
- [x] Add failing CLI parser/post tests for `live-agent call-remaining-rounds`.
- [x] Implement the API payload, operation details, route detection, and CLI wrapper with bounded `--max-rounds`, per-turn timeout, `--stop-on-timeout`, and CLI batch limit rejection.
- [x] Run targeted API/CLI tests.

### Task 4: GUI Control And Docs

**Files:**
- Modify: `agentsassemble/static/lobby.js`
- Modify: `agentsassemble/static/shared.js`
- Modify: `agentsassemble/static/app.js`
- Modify: `tests/static_lobby_runtime_smoke.mjs`
- Modify: `tests/test_static_ui_assets.py`
- Modify: `docs/live-agent-ops.md`
- Modify: `docs/roadmap.md`

- [x] Add failing static smoke coverage for a `남은라운드` button posting the new endpoint and refreshing the meeting selector/view.
- [x] Implement the Lobby button using the existing process status line and busy gate.
- [x] Document the bounded remaining-round flow.
- [x] Run Node static smoke, static UI assertions, and docs assertions.

### Task 5: Review And Verification

**Files:**
- Add/update: `docs/superpowers/plans/2026-05-19-live-agent-remaining-rounds.md`

- [x] Request xhigh review-only subagent review.
- [x] Fix blocking/important review feedback with RED tests first: stopped inner rounds are degraded, completed single rounds return `complete`, draft `debate_rounds` records remain runnable, final-meeting payloads merge live round progress, and `complete` round results are surfaced as success.
- [ ] Run `python3 -m unittest discover -s tests`.
- [ ] Run `python3 -m compileall -q agentsassemble`.
- [ ] Run JS `node --check` for touched static/test modules.
- [ ] Run `git diff --check`.
- [ ] Commit only this slice, excluding pre-existing dirty `agentsassemble/static/base.css` and `agentsassemble/static/index.html`, then push.
