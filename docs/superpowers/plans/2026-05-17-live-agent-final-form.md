# Live Agent Final Form Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build AgentsAssemble from one-shot local CLI delegation into an operator-controlled live agent room where multiple resident agents can be launched, observed, stopped, recovered, and governed by explicit engagement policy.

**Architecture:** Keep the current file-backed GUI room and resident runner as the core transport. Add a small local supervisor for process lifecycle, then layer GUI controls, engagement policy, logs, and recovery around that public surface without pretending to provide OS sandboxing or native provider PTY persistence before those exist.

**Tech Stack:** Python stdlib `unittest`, `subprocess.Popen`, `ThreadingHTTPServer`, JSONL room state, static browser UI with existing `fetchJson` helpers.

---

## Scope

This plan targets the local CLI first-class final form. It does not add production deployment, force-push, external messaging, billing changes, or real OS sandbox enforcement. Native Claude Code Channels, Gemini SDK sessions, and Cursor PTY persistence are future backend variants behind the same control-plane shape.

## File Structure

- Create `agentsassemble/live_agent_processes.py`: local process supervisor, group status model, start/stop/list operations, log path handling.
- Modify `agentsassemble/live_agent_runner.py`: keep runner behavior stable; accept server override through group config loading.
- Modify `agentsassemble/cli.py`: add `live-agent run-group --server` override and keep SIGINT cleanup behavior.
- Modify `agentsassemble/gui.py`: expose live-agent process control endpoints and inject a supervisor for tests.
- Modify `agentsassemble/static/shared.js`: add process-control state.
- Modify `agentsassemble/static/lobby.js`: render config-path start form, process cards, stop buttons, and status refresh.
- Modify `agentsassemble/static/lobby.css`: style process control without nested cards.
- Modify tests:
  - `tests/test_live_agent_processes.py`
  - `tests/test_cli_timeout.py`
  - `tests/test_gui_server.py`
  - `tests/test_static_ui_assets.py`

---

### Task 1: Local Supervisor and HTTP Control Plane

**Files:**
- Create: `agentsassemble/live_agent_processes.py`
- Modify: `agentsassemble/live_agent_runner.py`
- Modify: `agentsassemble/cli.py`
- Modify: `agentsassemble/gui.py`
- Test: `tests/test_live_agent_processes.py`
- Test: `tests/test_cli_timeout.py`
- Test: `tests/test_gui_server.py`

- [x] **Step 1: Write failing supervisor tests**

Add tests that start a fake process with a config path, expose status with `pid`, write a log path under `live-agent-runs`, and stop the process with graceful interrupt before force kill.

Run: `python3 -m unittest tests.test_live_agent_processes`
Expected: fail because `agentsassemble.live_agent_processes` does not exist.

- [x] **Step 2: Implement the process supervisor**

Create a supervisor class that owns only processes it launched. `start_group()` should refuse duplicate running group ids, create a log file, launch `python -m agentsassemble.cli live-agent run-group --config <path> --server <server-url>`, and return a serializable group record. `stop_group()` should signal interrupt, wait briefly, then terminate/kill if needed.

Run: `python3 -m unittest tests.test_live_agent_processes`
Expected: pass.

- [x] **Step 3: Add `run-group --server` override**

Extend parser coverage so `assemble live-agent run-group --config configs/live-agents.example.json --server http://127.0.0.1:9999` parses. Update config loading so every loaded resident agent uses the override server.

Run: `python3 -m unittest tests.test_cli_timeout.CliTimeoutTests.test_live_agent_run_group_accepts_server_override`
Expected: pass after a RED failure.

- [x] **Step 4: Add GUI process endpoints**

Expose:

- `GET /api/live-agent-processes`
- `POST /api/live-agent-processes/start`
- `POST /api/live-agent-processes/<group_id>/stop`

Start should derive server URL from the request host unless the payload gives `server`. Stop should return the updated group record.

Run: `python3 -m unittest tests.test_gui_server.GuiServerTests.test_live_agent_process_endpoints_start_list_and_stop_group`
Expected: pass after a RED failure.

- [x] **Step 5: Review checkpoint**

Self-review invariants:

- No endpoint can stop an arbitrary external PID; only launched group ids.
- Process records do not claim sandboxing.
- Stopped status is visible even when no live-agent presence row exists.
- Tests use fake processes or bounded fake CLI commands; no real Claude/Gemini process starts in unit tests.

---

### Task 2: GUI Start/Stop Controls

**Files:**
- Modify: `agentsassemble/static/shared.js`
- Modify: `agentsassemble/static/lobby.js`
- Modify: `agentsassemble/static/lobby.css`
- Test: `tests/test_static_ui_assets.py`

- [x] **Step 1: Write static UI hook tests**

Assert the lobby UI contains:

- `/api/live-agent-processes`
- `live-agent-process-form`
- `live-agent-process-start`
- `data-live-agent-process-stop`
- `.live-agent-process-list`

Run: `python3 -m unittest tests.test_static_ui_assets.StaticUiAssetTests.test_lobby_separates_stage_from_activity_feed`
Expected: fail before UI edits.

- [x] **Step 2: Add browser state and fetch helpers**

Add `liveAgentProcesses`, `liveAgentProcessesLoaded`, `liveAgentProcessesLoading`, and `liveAgentProcessStatus` to shared state. In `lobby.js`, add load/start/stop helpers using the existing `fetchJson` pattern.

Run: same static UI test.
Expected: still fail until markup exists.

- [x] **Step 3: Render controls**

Render a compact process control area inside the existing live-agent section:

- config path input defaulting to `configs/live-agents.example.json`
- start button
- refresh button
- process rows with status, pid, config path, and stop button for running groups

Run: same static UI test.
Expected: pass.

- [x] **Step 4: Browser smoke**

Start `assemble gui`, open the lobby, verify the process section renders, and confirm no text overlaps at desktop and mobile widths.

Run: Browser smoke against `http://127.0.0.1:<port>/`.
Expected: lobby renders process controls and existing lobby functions remain reachable.

---

### Task 3: Engagement Policy Beyond `always`

**Files:**
- Modify: `agentsassemble/live_agent_runner.py`
- Test: `tests/test_live_agent_runner.py`

- [x] **Step 1: Write policy tests**

Cover:

- `watch` never replies but advances cursor.
- `human_only` replies to `side: mine` human events and ignores agent chatter.
- `mentioned` replies when the message contains display name or agent id.
- `always` retains current behavior.

Run: `python3 -m unittest tests.test_live_agent_runner`
Expected: fail for new policies.

- [x] **Step 2: Add a small policy function**

Add `should_reply_to_event(config, event)` or equivalent at the event selection boundary. Keep chain-depth/self-loop guards separate from engagement policy.

Run: `python3 -m unittest tests.test_live_agent_runner`
Expected: pass.

- [x] **Step 3: Review checkpoint**

Check the default remains safe: config file examples may use `always`, but manual registration should remain `mentioned` unless the user explicitly starts a resident group.

---

### Task 4: Durable Runtime Logs and Recovery Surface

**Files:**
- Modify: `agentsassemble/live_agent_processes.py`
- Modify: `agentsassemble/gui.py`
- Modify: `agentsassemble/static/lobby.js`
- Test: `tests/test_live_agent_processes.py`
- Test: `tests/test_gui_server.py`

- [x] **Step 1: Write tests for process record persistence**

After a group starts, the supervisor should write a JSON status file under `live-agent-runs`. A new supervisor should list historical stopped/crashed records even though it cannot stop old PIDs.

Run: `python3 -m unittest tests.test_live_agent_processes`
Expected: fail before persistence.

- [x] **Step 2: Persist process state**

Write `live-agent-runs/processes.json` with group records on start/stop/poll. Mark records from a previous GUI process as `unknown` unless the current supervisor owns the handle.

Run: `python3 -m unittest tests.test_live_agent_processes`
Expected: pass.

- [x] **Step 3: Expose latest log excerpt**

Add an optional `log_tail` field bounded to a small byte limit in the process API so GUI can show failure clues without dumping long logs.

Run: targeted GUI tests.
Expected: pass.

---

### Task 5: Real CLI Smoke Checklist

**Files:**
- Modify: `docs/live-agent-ops.md`
- Test: documentation is verified by local fake commands and optional manual real CLI command.

- [x] **Step 1: Write operator doc**

Document:

- start GUI
- start fake agents
- start Claude/Gemini if installed
- stop group
- inspect `live_agents.json`, `lobby.jsonl`, and `live-agent-runs`

Run: `python3 -m unittest discover -s tests`
Expected: pass; docs are not enough without the executable checks from earlier tasks.

- [ ] **Step 2: Optional real provider smoke**

Only run real Claude/Gemini commands when the local CLI is installed, authenticated, and the user approves any cost or external side effect. Fake CLI remains the required automated verification.

Status: deferred until explicit real-provider approval. The required fake CLI and GUI smoke paths are covered by automated tests and a browser smoke against a temporary local GUI room.

---

### Task 6: GUI Runtime Status Polling

**Files:**
- Modify: `agentsassemble/static/app.js`
- Modify: `agentsassemble/static/lobby.js`
- Modify: `docs/live-agent-ops.md`
- Test: `tests/test_static_ui_assets.py`
- Test: `tests/test_docs_architecture.py`

- [x] **Step 1: Add static coverage for resident status polling**

Assert the frontend starts a background refresh loop for live-agent presence and supervised process groups, and that lobby renders preserve process start and live-agent registration form drafts while a background refresh re-renders the panel.

Run: `python3 -m unittest tests.test_static_ui_assets.StaticUiAssetTests.test_lobby_separates_stage_from_activity_feed`
Expected: fail before JS edits, pass after implementation.

- [x] **Step 2: Implement non-destructive background refresh**

Add an app-level 5-second `refreshLiveAgentRuntimeSurfaces` interval. Fetch `/api/live-agents` and `/api/live-agent-processes` in background mode, re-render only when data changes or a stale load banner is cleared, and preserve the process config/group/restart controls plus live-agent registration controls across re-renders.

Run: `node --check agentsassemble/static/lobby.js` and `node --check agentsassemble/static/app.js`
Expected: pass.

- [x] **Step 3: Browser smoke**

Start a temporary GUI server, register a live agent from another terminal, wait for the background refresh interval, and confirm the lobby roster updates without horizontal overflow.

Evidence: Browser smoke against `http://127.0.0.1:8877/` saw `Polling Smoke Agent` appear through auto-refresh, kept the process form present, and reported no document horizontal overflow.

---

### Task 7: Credential-Free Live Session Transport

**Files:**
- Create: `agentsassemble/live_session_transport.py`
- Modify: `agentsassemble/cli.py`
- Modify: `agentsassemble/live_agents.py`
- Modify: `agentsassemble/static/lobby.js`
- Modify: `docs/live-agent-ops.md`
- Test: `tests/test_live_session_transport.py`
- Test: `tests/test_cli_timeout.py`
- Test: `tests/test_live_agent_runner.py`
- Test: `tests/test_live_agents.py`
- Test: `tests/test_static_ui_assets.py`
- Test: `tests/test_docs_architecture.py`

- [x] **Step 1: Add RED coverage for resident JSONL sessions**

Cover a long-lived fake subprocess that preserves state across two prompts, parser acceptance for `--connection-kind live_session`, and an HTTP resident smoke where two human lobby events are answered by the same process.

- [x] **Step 2: Implement strict JSONL subprocess transport**

Add `JsonlLiveSession` as a small local subprocess adapter. It sends `{"request_id", "prompt"}` JSONL to stdin and requires matching `{"request_id", "message"}` JSONL on stdout. It bounds response size, drains stderr into a bounded tail, and closes timed-out processes.

- [x] **Step 2b: Cover stuck stdin writes**

Apply the configured timeout to both request writes and response reads, so a child process that stops reading stdin cannot hang a resident worker before error heartbeat and recovery.

- [x] **Step 3: Wire resident run and run-group selection**

Keep `local_cli` as one-shot delegation. Select the long-lived JSONL transport only when `ResidentAgentConfig.connection_kind == "live_session"`, with one subprocess per resident runner or run-group worker.

- [x] **Step 3a: Keep one-shot delegate semantics plain**

Reject `live_session` on `live-agent delegate`, because that command sends one plain prompt to a local CLI and does not speak the JSONL session protocol.

- [x] **Step 3b: Recover from failed live-session subprocesses**

When a JSONL subprocess exits, times out, or violates the protocol, close that process and let the same resident runner start a fresh subprocess for the next eligible event after normal cooldown/error handling.

- [x] **Step 3c: Close live-session workers on group interrupt**

Track active run-group command runners so SIGINT can close long-lived JSONL subprocesses even when a worker is blocked inside a session call.

- [x] **Step 4: Preserve public control-plane metadata**

Allow `live_session` in CLI parser choices, live-agent presence normalization, and the GUI connection-kind selector. Group configs preserve `connection_kind: "live_session"`.

- [x] **Step 5: Document the operator contract**

Add fake live-session smoke instructions to `docs/live-agent-ops.md`, including the JSONL protocol, one-local-process expectation, and the explicit caveat that this is not native Claude/Gemini/Cursor PTY persistence.

---

### Task 8: Credential-Free Operator Smoke Command

**Files:**
- Create: `agentsassemble/live_agent_smoke.py`
- Modify: `agentsassemble/cli.py`
- Modify: `docs/live-agent-ops.md`
- Test: `tests/test_cli_timeout.py`
- Test: `tests/test_live_agent_smoke.py`
- Test: `tests/test_docs_architecture.py`

- [x] **Step 1: Add RED coverage for `live-agent smoke`**

Cover parser options and an end-to-end local HTTP smoke against a temporary GUI room. The smoke must start a supervised fake group, verify one `local_cli` reply and one `live_session` reply, and leave the process group stopped.

- [x] **Step 2: Implement operator smoke through public control-plane APIs**

Add `assemble live-agent smoke` as a credential-free operator command. It posts a human lobby event, writes a temporary fake group config, starts it through `/api/live-agent-processes/start`, waits for `smoke local_cli ok` and `smoke live_session ok`, and cleans up the group.

- [x] **Step 2b: Seed cursors and verify the probe source**

Before posting the probe event, pre-register the smoke agents and heartbeat `last_observed_event_id` to the current latest lobby event. The smoke only accepts replies whose `source_event_id` matches the probe event, so old room chatter cannot produce a false pass.

- [x] **Step 3: Document the smoke path**

Document the command as the first operator check after the GUI starts, including `--json`, `--group-id`, and the fact that it uses no real provider credentials or model calls.

---

## Full Verification

Run after each task that changes code:

```bash
python3 -m unittest discover -s tests
python3 -m compileall -q agentsassemble
node --check agentsassemble/static/lobby.js
node --check agentsassemble/static/shared.js
node --check agentsassemble/static/app.js
node --check agentsassemble/static/archive.js
node --check agentsassemble/static/meeting-views.js
git diff --check
```

## Self-Review

Spec coverage:

- Resident runner exists from the prior slice.
- This plan adds the missing local lifecycle manager, GUI controls, engagement policy, process logs, and operator docs.
- Native PTY/SDK backends remain future variants and must not be described as complete until implemented and verified.

Placeholder scan:

- No task depends on unspecified behavior; every task names files, commands, and expected public behavior.

Type consistency:

- Process group records use `group_id`, `status`, `pid`, `config_path`, `server`, `log_path`, `started_at`, `stopped_at`, `returncode`, and `last_error`.
- Live-agent presence records remain separate from process group records.
