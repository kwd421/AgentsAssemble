# Live Agent Session Smoke Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. In this repository, subagents are limited to planning and review only; the main Codex session performs implementation. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a credential-free operator smoke that proves the resident session control path, not just isolated runner/process pieces.

**Architecture:** Reuse the existing GUI room and public APIs. The smoke creates temporary fake council/agent/resident configs for `local_cli`, JSONL `live_session`, and loopback `remote_bridge`; calls `start-session`; runs one bounded official round through the public rounds endpoint; posts one human lobby event; waits for every fake resident agent to auto-reply with matching `source_event_id`; calls `check-session`; exercises `restart-session`; then calls `stop-session` for cleanup. It records and returns only safe ids/counts/statuses.

**Tech Stack:** Python stdlib HTTP helpers, existing `live_agent_smoke` fake command patterns, `unittest`, docs architecture assertions.

---

### Task 1: Backend Session Smoke Primitive

**Files:**
- Modify: `agentsassemble/live_agent_smoke.py`
- Test: `tests/test_live_agent_smoke.py`

- [x] **Step 1: Write failing tests**

Add tests for `run_live_agent_session_smoke()` proving:
- it posts to `/api/live-agent-sessions/start` with temporary config paths, `meeting_id`, `group_id`, and `connect_timeout_seconds`;
- it runs one official round after the session is ready;
- it posts one human lobby probe and waits for all expected fake replies whose `source_event_id` matches that probe;
- it calls `/api/live-agent-sessions/check` after replies;
- it calls `/api/live-agent-sessions/stop` in cleanup;
- it returns `status: "ok"` only when start is ready, replies match, check is ready, and stop is stopped;
- it redacts config paths, commands, provider output, endpoints, auth refs, prompts, and reply text from its public result.

- [x] **Step 2: Verify RED**

Run:

```bash
python3 -m unittest tests.test_live_agent_smoke.LiveAgentSmokeTests.test_session_smoke_runs_start_reply_check_and_stop_sequence
```

Expected: import or attribute failure because `run_live_agent_session_smoke()` does not exist.

- [x] **Step 3: Implement primitive**

Add `run_live_agent_session_smoke(server, group_id="", meeting_id="", timeout_seconds=12.0, request_json, sleep_fn=time.sleep, python_executable=sys.executable, temp_dir_factory=tempfile.TemporaryDirectory)`. Build three fake resident agents for `local_cli`, JSONL `live_session`, and loopback `remote_bridge`. Use temporary council and agent runtime configs whose bindings match the resident config. Seed no stale cursor before session start; run one official round while agents are in moderator-called mode; post the probe only after switching agents to `always`. Reuse existing waiting helpers where possible.

- [x] **Step 4: Verify GREEN**

Run the targeted backend tests.

Expected: session smoke tests pass.

### Task 2: API And Operation Surface

**Files:**
- Modify: `agentsassemble/gui.py`
- Test: `tests/test_gui_server.py`

- [x] **Step 1: Write failing tests**

Add tests for `POST /api/live-agent-session-smoke`: success returns safe counts/statuses and records one sanitized `session.smoke` operation; failure records a sanitized failed operation and does not leak temporary config paths, commands, reply text, endpoints, auth refs, or log tails.

- [x] **Step 2: Implement endpoint**

Expose `live_agent_session_smoke_payload()` and `/api/live-agent-session-smoke`. The operation details should include `result_status`, `meeting_id`, `group_id`, official-round counts, expected/replied counts, start/check/stop statuses, and safe agent ids only.

- [x] **Step 3: Verify GREEN**

Run targeted GUI tests.

Expected: endpoint and operation tests pass.

### Task 3: CLI And Docs

**Files:**
- Modify: `agentsassemble/cli.py`
- Modify: `docs/live-agent-ops.md`
- Modify: `docs/roadmap.md`
- Modify: `tests/test_cli_timeout.py`
- Modify: `tests/test_docs_architecture.py`

- [x] **Step 1: Write failing parser/request/docs tests**

Add `live-agent session-smoke --server http://room.local --group-id session-smoke --meeting-id smoke-meeting --timeout 8` parser coverage. Assert the CLI posts to `/api/live-agent-session-smoke`, prints a compact summary, exits `0` for `ok`, and exits `1` otherwise.

- [x] **Step 2: Implement CLI and docs**

Add parser and `_run_live_agent_session_smoke()`. Document it as the strongest credential-free proof after the GUI starts, distinct from real Claude/Gemini smoke and from the lower-level `smoke`/`official-round-smoke` diagnostics.

- [x] **Step 3: Verify GREEN**

Run targeted CLI/docs tests.

Expected: CLI/docs tests pass.

### Task 4: Review And Verification

**Files:**
- This plan file

- [x] **Step 1: Request review-only subagent**

Ask an existing subagent to review the diff for correctness, privacy leaks, cleanup guarantees, and whether this smoke actually proves the resident session final-form path.

- [x] **Step 2: Fix Critical or Important findings**

Apply fixes locally; subagents do not implement.

- [x] **Step 3: Full verification**

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

- [x] **Step 4: Commit and authorized push**

Commit only the coherent session-smoke slice, excluding pre-existing dirty `agentsassemble/static/base.css` and `agentsassemble/static/index.html`. Push to `origin/codex/live-room-council-foundation`.
