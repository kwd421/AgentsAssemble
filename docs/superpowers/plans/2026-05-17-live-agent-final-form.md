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

### Task 9: GUI Smoke Diagnostic Control

**Files:**
- Modify: `agentsassemble/gui.py`
- Modify: `agentsassemble/static/lobby.js`
- Modify: `docs/live-agent-ops.md`
- Test: `tests/test_gui_server.py`
- Test: `tests/test_static_ui_assets.py`
- Test: `tests/test_docs_architecture.py`

- [x] **Step 1: Add RED coverage for the GUI smoke endpoint and button**

Cover `POST /api/live-agent-smoke` against a temporary GUI room and static UI hooks for `runLiveAgentSmoke`, `/api/live-agent-smoke`, and the `live-agent-process-smoke` button.

- [x] **Step 2: Expose the existing credential-free smoke through the GUI control plane**

Call `run_live_agent_smoke()` from the GUI endpoint using the bound local server address as the room server. The endpoint preserves the CLI smoke behavior: seed cursors, post one probe event, start a temporary supervised fake group, accept only replies whose `source_event_id` matches the probe, and leave the process group stopped.

- [x] **Step 3: Add the operator UI action**

Add the `진단` button to the existing "상주 실행" panel. On success it refreshes the lobby, live-agent roster, and process records, then reports the smoke group id in the panel status.

- [x] **Step 4: Document the GUI diagnostic**

Document that the GUI `진단` button calls `POST /api/live-agent-smoke` and uses the same credential-free fake `local_cli` plus JSONL `live_session` path as the CLI smoke command.

---

### Task 10: Operator Readiness Doctor

**Files:**
- Modify: `agentsassemble/gui.py`
- Modify: `agentsassemble/cli.py`
- Modify: `agentsassemble/static/shared.js`
- Modify: `agentsassemble/static/lobby.js`
- Modify: `docs/live-agent-ops.md`
- Test: `tests/test_gui_server.py`
- Test: `tests/test_cli_timeout.py`
- Test: `tests/test_gui_server.py`
- Test: `tests/test_static_ui_assets.py`
- Test: `tests/test_docs_architecture.py`

- [x] **Step 1: Add RED coverage for combined readiness**

Cover `POST /api/live-agent-readiness`, `live-agent doctor`, and the GUI `점검` button. The endpoint test proves readiness uses pre-smoke health, because a successful smoke leaves stopped/offline smoke artifacts behind.

- [x] **Step 2: Implement readiness as a thin orchestration layer**

Compute health first, run the existing credential-free smoke second, and return `ready`, `degraded`, or `failed` with `checks`, `health`, and `smoke` payloads. Keep the endpoint local-control-plane only; do not accept config paths, command overrides, provider ids, or server overrides.

- [x] **Step 3: Add CLI and GUI operator controls**

Add `assemble live-agent doctor` with `--json`, `--group-id`, and `--timeout`, plus a `점검` button in the GUI process controls. Exit `0` only for `ready`, `1` for reached-but-not-ready, and `2` for connection/argument failures through the existing CLI error wrapper.

- [x] **Step 4: Document the doctor contract**

Document why health is captured before smoke, the `/api/live-agent-readiness` path, the GUI `점검` button, and the exit-code contract.

---

### Task 11: Diagnostic Artifact Health Isolation

**Files:**
- Modify: `agentsassemble/live_agents.py`
- Modify: `agentsassemble/live_agent_processes.py`
- Modify: `agentsassemble/live_agent_smoke.py`
- Modify: `agentsassemble/gui.py`
- Modify: `docs/live-agent-ops.md`
- Test: `tests/test_gui_server.py`
- Test: `tests/test_live_agents.py`
- Test: `tests/test_live_agent_processes.py`
- Test: `tests/test_docs_architecture.py`

- [x] **Step 1: Add RED coverage for repeated doctor and diagnostic flags**

Cover repeated `POST /api/live-agent-readiness` calls, diagnostic agent/process records, and the health endpoint. The same smoke group id can run twice without the first run's offline/stopped diagnostic artifacts degrading the second run.

- [x] **Step 2: Preserve diagnostic metadata on live-agent presence and process records**

Store a boolean `diagnostic` flag on live-agent presence rows and supervised process group records while keeping the records inspectable for operator debugging.

- [x] **Step 3: Mark smoke artifacts diagnostic**

The credential-free smoke path tags pre-registered agents, heartbeat updates, and the supervised fake group as diagnostic.

- [x] **Step 4: Exclude diagnostic records from health and readiness summaries**

`/api/live-agent-health` ignores diagnostic presence rows and process groups before computing counts, attention lists, and the overall status. It also recognizes legacy smoke artifacts by the built-in smoke agent identities so pre-flag fake smoke runs do not permanently degrade an existing room. Readiness keeps using the pre-smoke health snapshot and can be repeated without self-inflicted degradation.

- [x] **Step 5: Document the diagnostic isolation contract**

Document that diagnostic artifacts remain in runtime files for inspection but do not contaminate later health checks or repeated readiness checks.

---

### Task 12: Backend Supervisor Monitor Loop

**Files:**
- Modify: `agentsassemble/live_agent_processes.py`
- Modify: `agentsassemble/gui.py`
- Modify: `docs/live-agent-ops.md`
- Test: `tests/test_live_agent_processes.py`
- Test: `tests/test_gui_server.py`
- Test: `tests/test_docs_architecture.py`

- [x] **Step 1: Add RED coverage for browser-independent supervision**

Cover a crashed auto-restart group and a delayed backoff restart advancing through a backend monitor loop without calling `list_groups()`. Keep `snapshot_groups()` read-only so health checks still cannot mutate process state.

- [x] **Step 2: Implement an idempotent monitor lifecycle**

Add `start_monitor(interval_seconds=2.0)` and `stop_monitor()` to `LiveAgentProcessSupervisor`. The monitor owns a daemon thread that periodically refreshes running groups and starts due auto-restarts under the existing supervisor lock.

- [x] **Step 3: Wire monitor to GUI server lifecycle**

Start the monitor when `serve_gui()` starts and stop it through `process_supervisor.close()` in the existing shutdown path.

- [x] **Step 4: Document backend supervision semantics**

Document that auto-restart and crash detection continue while the GUI server is running, even without an open browser or `/api/live-agent-processes` polling client.

---

### Task 13: Live-Agent Config Preflight

**Files:**
- Create: `agentsassemble/live_agent_preflight.py`
- Modify: `agentsassemble/cli.py`
- Modify: `agentsassemble/gui.py`
- Modify: `agentsassemble/static/shared.js`
- Modify: `agentsassemble/static/lobby.js`
- Modify: `docs/live-agent-ops.md`
- Test: `tests/test_live_agent_preflight.py`
- Test: `tests/test_cli_timeout.py`
- Test: `tests/test_gui_server.py`
- Test: `tests/test_static_ui_assets.py`
- Test: `tests/test_docs_architecture.py`

- [x] **Step 1: Add RED coverage for no-execution config checks**

Cover `live-agent preflight --config`, `POST /api/live-agent-preflight`, and the GUI `예비점검` button. The checks must read and normalize the group config without starting supervised processes or executing provider commands.

- [x] **Step 2: Implement safe preflight checks**

Validate readable JSON config, duplicate `agent_id` values, resident-supported `connection_kind`, and command executable presence through PATH or explicit executable paths. Return a machine-readable report with `status`, `summary`, top-level `checks`, and per-agent check results.

- [x] **Step 3: Add CLI and GUI operator controls**

Expose `assemble live-agent preflight --config ... [--server ...] [--json]`, `POST /api/live-agent-preflight`, and a GUI `예비점검` button beside start/smoke/readiness controls.

- [x] **Step 4: Document the preflight contract**

Document that preflight is credential-free and does not execute provider commands, so it can prove config shape and command availability but not Claude/Gemini login, billing, or model readiness.

---

### Task 14: Provider Runtime Health

**Files:**
- Create: `agentsassemble/provider_health.py`
- Modify: `agentsassemble/cli.py`
- Modify: `agentsassemble/gui.py`
- Modify: `agentsassemble/static/shared.js`
- Modify: `agentsassemble/static/lobby.js`
- Modify: `docs/live-agent-ops.md`
- Modify: `docs/provider-architecture.md`
- Modify: `docs/research-log.md`
- Test: `tests/test_provider_health.py`
- Test: `tests/test_cli_timeout.py`
- Test: `tests/test_gui_server.py`
- Test: `tests/test_static_ui_assets.py`
- Test: `tests/test_docs_architecture.py`

- [x] **Step 1: Add RED coverage for static provider readiness**

Cover `providers health --config`, `POST /api/provider-health`, and the GUI `Provider 점검` hook. The report must parse runtime provider config without starting a meeting, executing provider commands, or making model/network calls.

- [x] **Step 2: Implement side-effect-free provider health**

Validate provider registry availability, duplicate provider/permission/agent/role ids, required auth_ref presence without leaking values, endpoint requirements, local command executable presence, and meeting-only binding permission compatibility. Return a machine-readable report with `probe_mode: none`.

- [x] **Step 3: Add CLI, API, and GUI operator controls**

Expose `assemble providers health --config ... [--json]`, `POST /api/provider-health`, and a GUI `Provider 점검` action for meetings created from an agent runtime config.

- [x] **Step 4: Document the static readiness contract**

Document that provider health proves local config coherence only. It does not prove account login, billing, model availability, network reachability, bridge reachability, or real provider behavior until an explicit probe mode is designed and verified.

---

### Task 15: Local OpenAI-Compatible Provider Probe

**Files:**
- Modify: `agentsassemble/provider_health.py`
- Modify: `agentsassemble/cli.py`
- Modify: `agentsassemble/gui.py`
- Modify: `docs/live-agent-ops.md`
- Modify: `docs/provider-architecture.md`
- Modify: `docs/research-log.md`
- Test: `tests/test_provider_health.py`
- Test: `tests/test_cli_timeout.py`
- Test: `tests/test_gui_server.py`

- [x] **Step 1: Add RED coverage for opt-in local probe**

Cover `providers health --probe local --probe-timeout`, GUI payload forwarding, loopback OpenAI-compatible `/models` probing, `probe_mode: none` staying network-free, non-applicable provider kinds being skipped, and failures for non-loopback, malformed, empty, or unreachable model endpoints.

- [x] **Step 2: Implement loopback-only probe mode**

Add `probe_mode: local` while preserving `none` as the default. Only `local_openai_compatible` providers may be probed, and only through loopback `http` `/models` endpoints. Do not follow redirects or environment proxies, call `/chat/completions`, execute CLI providers, contact remote bridges, read secret values, or include endpoint/query/userinfo/requester exception text in the report.

- [x] **Step 3: Document the operator contract**

Document that `--probe local` proves only local OpenAI-compatible model-list reachability. It does not prove generation quality, prompt compliance, billing, remote API access, bridge readiness, or real provider meeting behavior.

---

### Task 16: Remote Bridge Health Probe

**Files:**
- Modify: `agentsassemble/bridges/claude_code_bridge.py`
- Modify: `agentsassemble/provider_health.py`
- Modify: `agentsassemble/cli.py`
- Modify: `agentsassemble/gui.py`
- Modify: `docs/live-agent-ops.md`
- Modify: `docs/provider-architecture.md`
- Modify: `docs/research-log.md`
- Test: `tests/test_claude_code_bridge.py`
- Test: `tests/test_provider_health.py`
- Test: `tests/test_cli_timeout.py`
- Test: `tests/test_gui_server.py`
- Test: `tests/test_docs_architecture.py`

- [x] **Step 1: Add RED coverage for bridge-only health**

Cover authenticated `GET /agentsassemble/health`, `providers health --probe bridge`, GUI provider-health payload forwarding, non-bridge skip behavior, missing auth, auth rejection, malformed endpoints, redirects toward `/agentsassemble/run`, environment proxy bypass, and health response contract validation.

- [x] **Step 2: Implement command-free bridge probe**

Add bridge `GET /agentsassemble/health` and provider `probe_mode: bridge`. The probe only calls the health route for `remote_http_bridge` providers, sends bearer auth from explicit `auth_ref`, follows no redirects or environment proxies, sends no prompt or meeting payload, never calls `/agentsassemble/run`, and executes no Claude/provider command.

- [x] **Step 3: Document bridge readiness limits**

Document that `--probe bridge` proves only bridge HTTP reachability and token acceptance. It does not prove the friend's Claude login, billing, model availability, command execution, prompt compliance, or read-only behavior.

---

### Task 17: Resident Remote Bridge Runner

**Files:**
- Modify: `agentsassemble/live_agent_runner.py`
- Modify: `agentsassemble/adapters/remote_bridge.py`
- Modify: `agentsassemble/cli.py`
- Modify: `agentsassemble/live_agent_preflight.py`
- Modify: `docs/live-agent-ops.md`
- Modify: `docs/provider-architecture.md`
- Modify: `docs/research-log.md`
- Add: `configs/live-agents.remote-bridge.example.json`
- Test: `tests/test_live_agent_runner.py`
- Test: `tests/test_live_agent_preflight.py`
- Test: `tests/test_cli_timeout.py`
- Test: `tests/test_remote_bridge_adapter.py`

- [x] **Step 1: Add RED coverage for resident remote bridge execution**

Cover `live-agent run --connection-kind remote_bridge` parsing without local `--command`, group config loading without local commands, preflight endpoint/auth checks, remote bridge reply generation, sanitized auth failures, and an end-to-end fake GUI room plus fake bridge resident reply.

- [x] **Step 2: Implement remote bridge reply runner behind existing resident loop**

Add a remote bridge command runner chosen from the full `ResidentAgentConfig`. Keep polling, cursor restore, cooldown, failure backoff, self-loop skipping, chain-depth guards, and lobby posting in `LiveAgentRunner`, so remote replies still use `actor_id`, `source_event_id`, and `auto_chain_depth` metadata. Harden startup so invalid bridge setup stops the resident group, redacted auth values are unavailable, and unsafe bridge endpoints are rejected before request or presence persistence.

- [x] **Step 3: Document execution-vs-health boundary**

Document that bridge health probes call only `/agentsassemble/health`, while resident remote bridge agents intentionally call `/agentsassemble/run` after the operator starts `live-agent run` or `run-group` with endpoint and auth_ref.

---

### Task 18: Supervised Process Preflight Gate

**Files:**
- Modify: `agentsassemble/live_agent_processes.py`
- Modify: `docs/live-agent-ops.md`
- Test: `tests/test_live_agent_processes.py`
- Test: `tests/test_gui_server.py`

- [x] **Step 1: Add RED coverage for refused invalid launches**

Cover supervised start, manual restart, immediate auto-restart, delayed auto-restart, API start, and API restart with failed preflight. The tests assert that failed configs raise before `command_factory`, before log creation, and before new process records are written.

- [x] **Step 2: Gate the shared launch boundary**

Run `preflight_live_agent_config()` inside `LiveAgentProcessSupervisor._start_group_unlocked()` after duplicate/existence checks and before log/process side effects. Keep GUI and CLI paths thin so start, restart, immediate auto-restart, and delayed monitor restart all share the same guard.

- [x] **Step 3: Surface concise operator failures**

Format failed top-level and per-agent checks into a short `ValueError` message. GUI/API/CLI callers receive an immediate refused-start error, the GUI status line surfaces the API refusal reason, and auto-restart records the failure in `last_error` while leaving the group in `error`.

- [x] **Step 4: Document start/restart preflight semantics**

Document that supervised start and restart now run preflight automatically in the GUI server environment before opening logs or launching `run-group`.

---

### Task 19: Resident Connection Kind Runtime Gate

**Files:**
- Modify: `agentsassemble/live_agent_runner.py`
- Modify: `agentsassemble/live_agent_preflight.py`
- Modify: `agentsassemble/cli.py`
- Modify: `docs/live-agent-ops.md`
- Test: `tests/test_live_agent_runner.py`
- Test: `tests/test_live_agent_preflight.py`
- Test: `tests/test_cli_timeout.py`
- Test: `tests/test_docs_architecture.py`

- [x] **Step 1: Add RED coverage for runtime/preflight vocabulary drift**

Cover `live-agent run --connection-kind manual` as a parser error, and `run-group` config loading with `connection_kind: manual` plus a command as a `ValueError`. This proves unsupported resident kinds cannot silently fall back to the local CLI runner.

- [x] **Step 2: Share the supported resident connection kind vocabulary**

Define the resident runtime support set as `local_cli`, `live_session`, and `remote_bridge`, then use it from CLI parser choices, direct resident validation, group config loading, and preflight reporting.

- [x] **Step 3: Document registration-vs-resident boundaries**

Document that `manual` and `codex_resume` are roster/registration kinds, not resident process connection kinds. Direct CLI run, run-group, supervised start, and preflight now reject them as resident configs.

---

### Task 20: Supervised Group Agent Manifest

**Files:**
- Modify: `agentsassemble/live_agent_processes.py`
- Modify: `agentsassemble/cli.py`
- Modify: `agentsassemble/static/lobby.js`
- Modify: `docs/live-agent-ops.md`
- Test: `tests/test_live_agent_processes.py`
- Test: `tests/test_cli_timeout.py`
- Test: `tests/test_static_ui_assets.py`
- Test: `tests/test_docs_architecture.py`

- [x] **Step 1: Add RED coverage for safe launch-time manifests**

Cover supervised start and restart persisting an `agents` manifest from the group config, historical records defaulting missing manifests to `[]`, API payloads preserving the manifest, CLI process list output including agent labels, and GUI process rows having a manifest rendering hook.

- [x] **Step 2: Persist only safe manifest fields**

Extract `agent_id`, `display_name`, `provider_kind`, and `connection_kind` from the same normalized group config that `run-group` consumes at the supervised launch boundary. Do not persist commands, command paths, endpoints, auth refs, prompts, or environment-derived values.

- [x] **Step 3: Surface manifests to operators**

Return manifest entries through the process API, show them in the CLI process list, render them on GUI process rows, and document that the manifest is launch-time observability rather than a secret-bearing config dump.

---

### Task 21: Supervised Process Lifecycle Event History

**Files:**
- Modify: `agentsassemble/live_agent_processes.py`
- Modify: `agentsassemble/cli.py`
- Modify: `agentsassemble/static/lobby.js`
- Modify: `agentsassemble/static/lobby.css`
- Modify: `docs/live-agent-ops.md`
- Test: `tests/test_live_agent_processes.py`
- Test: `tests/test_cli_timeout.py`
- Test: `tests/test_gui_server.py`
- Test: `tests/test_static_ui_assets.py`
- Test: `tests/test_docs_architecture.py`

- [x] **Step 1: Add RED coverage for lifecycle evidence**

Cover start/stop lifecycle events, auto-restart scheduling and relaunch events, stale `running` record recovery to `unknown`, repeated polling without duplicate exit events, output-only `recent_events`, CLI summaries, API payload propagation, GUI rendering hooks, and docs.

- [x] **Step 2: Persist safe JSONL lifecycle history**

Append safe lifecycle events under `live-agent-runs/events.jsonl` while keeping `processes.json` as the latest-state source of truth. Events contain bounded operator facts such as `event_type`, timestamp, group id, status, pid, return code, and restart counters, and do not include command arguments, endpoints, auth refs, command paths, prompts, log tails, or environment-derived values.

- [x] **Step 3: Surface bounded recent history to operators**

Expose each group's bounded `recent_events` through the existing process API responses, show the latest event in CLI process list output, render the latest lifecycle event in GUI process rows, and document the lifecycle history artifact for long-running operations and recovery debugging.

---

### Task 22: Live-Agent Heartbeat Freshness Evidence

**Files:**
- Modify: `agentsassemble/live_agents.py`
- Modify: `agentsassemble/static/lobby.js`
- Modify: `docs/live-agent-ops.md`
- Test: `tests/test_live_agents.py`
- Test: `tests/test_gui_server.py`
- Test: `tests/test_static_ui_assets.py`
- Test: `tests/test_docs_architecture.py`

- [x] **Step 1: Add RED coverage for output-only freshness evidence**

Cover stale inference including `heartbeat_age_seconds` and `stale_after_seconds`, fresh online agents with age evidence, API propagation, GUI rendering hooks, and operator docs. Assert the freshness fields are not persisted in `live_agents.json`.

- [x] **Step 2: Infer freshness at the read boundary**

Add freshness evidence in `read_live_agents()` output by comparing `last_seen_at` with the read timestamp. Preserve persisted presence shape for connect and heartbeat writes, and scrub any stale output-only freshness keys from loaded or rewritten presence rows.

- [x] **Step 3: Surface freshness to operators**

Render compact heartbeat age in live-agent cards and document that roster freshness evidence explains `stale` status without becoming durable state.

---

### Task 23: Interruptible Local CLI Resident Shutdown

**Files:**
- Modify: `agentsassemble/cli.py`
- Modify: `docs/live-agent-ops.md`
- Modify: `docs/superpowers/plans/2026-05-17-live-agent-final-form.md`
- Test: `tests/test_cli_timeout.py`
- Test: `tests/test_docs_architecture.py`

- [x] **Step 1: Add RED coverage for local CLI cancellation**

Cover resident `local_cli` command execution through a closeable runner that can terminate an active provider subprocess before its normal timeout.

- [x] **Step 2: Add RED coverage for shutdown error hygiene**

Cover `run-group` suppressing secondary worker errors that happen only after the group stop flag is set, while preserving the original failing agent error.

- [x] **Step 3: Implement closeable local CLI resident execution**

Use a `Popen`-based resident command runner for `local_cli` while keeping one-shot delegate command semantics plain. Close active local provider processes during SIGINT/shutdown through the same command-runner hook already used by `live_session`, and avoid writing a misleading error heartbeat for command failures caused after the stop flag is set.

- [x] **Step 4: Document stop semantics**

Document that supervised stop can interrupt active local provider commands and that shutdown-only secondary errors do not pollute the operator-facing process result.

---

### Task 24: Resident Local CLI Process Group Cleanup

**Files:**
- Modify: `agentsassemble/cli.py`
- Modify: `docs/live-agent-ops.md`
- Modify: `docs/superpowers/plans/2026-05-17-live-agent-final-form.md`
- Test: `tests/test_cli_timeout.py`
- Test: `tests/test_docs_architecture.py`

- [x] **Step 1: Add RED coverage for child-process cleanup**

Cover a resident `local_cli` provider wrapper that spawns a long-running child process. Closing the resident command runner must stop the child as well as the direct provider process.

- [x] **Step 2: Start resident commands in a stoppable process group**

On POSIX hosts, launch resident local CLI provider commands in a new session and send stop signals to the process group. Keep fake-process and non-POSIX behavior on the existing direct-process fallback.

- [x] **Step 3: Document subprocess-tree stop semantics**

Document that resident local CLI stop semantics cover ordinary provider child processes on POSIX hosts while one-shot delegate semantics remain unchanged.

---

### Task 25: Resident Local CLI Timeout Subprocess Cleanup Evidence

**Files:**
- Modify: `docs/live-agent-ops.md`
- Modify: `docs/superpowers/plans/2026-05-17-live-agent-final-form.md`
- Test: `tests/test_cli_timeout.py`
- Test: `tests/test_docs_architecture.py`

- [x] **Step 1: Add coverage for timeout subprocess cleanup**

Cover a resident `local_cli` provider wrapper that spawns a long-running child process and then exceeds its timeout. The runner must finish with `TimeoutExpired` and stop the spawned child process on POSIX hosts.

- [x] **Step 2: Reuse existing process-group cleanup**

The new coverage passed against the Task 24 implementation, so no production code change was required. Timeout, close, and interruption cleanup all route through the same resident command-runner termination boundary.

- [x] **Step 3: Document timeout cleanup semantics**

Document that provider command timeouts use the same cleanup path as supervised stop for resident `local_cli` workers.

---

### Task 26: Supervised Run-Group Process Tree Cleanup

**Files:**
- Modify: `agentsassemble/live_agent_processes.py`
- Modify: `docs/live-agent-ops.md`
- Modify: `docs/superpowers/plans/2026-05-17-live-agent-final-form.md`
- Test: `tests/test_live_agent_processes.py`
- Test: `tests/test_docs_architecture.py`

- [x] **Step 1: Add RED coverage for supervised child-process cleanup**

Cover a supervised `run-group` process that spawns a long-running child process and ignores SIGINT. Stopping the group must escalate cleanup and stop the child process on POSIX hosts.

- [x] **Step 2: Start supervised groups in a stoppable process group**

On POSIX hosts, launch supervised resident `run-group` processes in a new session and remember the process group only when the child is actually the group leader. Send SIGINT, SIGTERM, and SIGKILL through that group before falling back to direct process termination.

- [x] **Step 3: Document outer supervisor stop semantics**

Document that the GUI/API supervisor cleanup boundary covers ordinary child processes created by the supervised resident group when the group fails to exit after SIGINT.

---

### Task 27: Live Session Subprocess Tree Cleanup

**Files:**
- Modify: `agentsassemble/live_session_transport.py`
- Modify: `docs/live-agent-ops.md`
- Modify: `docs/superpowers/plans/2026-05-17-live-agent-final-form.md`
- Test: `tests/test_live_session_transport.py`
- Test: `tests/test_docs_architecture.py`

- [x] **Step 1: Add RED coverage for JSONL session child cleanup**

Cover a JSONL `live_session` subprocess that spawns a long-running child. Closing the session and timing out while waiting for a reply must stop the child process on POSIX hosts.

- [x] **Step 2: Start JSONL sessions in a stoppable process group**

On POSIX hosts, launch the JSONL live-session subprocess in a new session and remember the process group only when the child is actually the group leader. The close path sends SIGTERM and SIGKILL through that group before falling back to direct process termination.

- [x] **Step 3: Document live-session cleanup semantics**

Document that close, reply timeout, and blocked-write timeout all use the same JSONL session cleanup boundary for ordinary child processes created by wrappers.

---

### Task 28: Runtime Engagement Policy Control

**Files:**
- Modify: `agentsassemble/live_agent_runner.py`
- Modify: `agentsassemble/live_agents.py`
- Modify: `agentsassemble/gui.py`
- Modify: `agentsassemble/static/lobby.js`
- Modify: `agentsassemble/static/lobby.css`
- Modify: `docs/live-agent-ops.md`
- Modify: `docs/superpowers/plans/2026-05-17-live-agent-final-form.md`
- Test: `tests/test_live_agent_runner.py`
- Test: `tests/test_live_agents.py`
- Test: `tests/test_gui_server.py`
- Test: `tests/test_static_ui_assets.py`
- Test: `tests/test_docs_architecture.py`

- [x] **Step 1: Add RED coverage for runtime policy overrides**

Cover a runner whose startup config is active but whose current room presence is `watch`, a runner whose startup config is passive but whose room presence is `always`, and invalid or wrong-agent room presence that must fall back to startup config.

- [x] **Step 2: Add explicit engagement update persistence**

Add `update_live_agent_engagement()` and `POST /api/live-agents/<agent_id>/engagement` so operator policy changes persist with `engagement_mode_updated_at` without refreshing `last_seen_at`. Re-registration and heartbeats preserve the operator-selected engagement mode instead of clobbering it, while placeholder/default or invalid legacy rows can still be replaced by a resident runner's startup mode.

- [x] **Step 3: Wire GUI runtime control**

Add a compact roster selector with all supported modes. The selector posts to the explicit engagement endpoint, labels `always` as loop-prone, and leaves freshness evidence untouched.

- [x] **Step 4: Document operator semantics**

Document valid modes, the engagement API, no heartbeat freshness bump, per-poll runner behavior, and `watch`/`manual` cursor advancement without backlog replay.

---

### Task 29: CLI Runtime Engagement Policy Control

**Files:**
- Modify: `agentsassemble/cli.py`
- Modify: `docs/live-agent-ops.md`
- Modify: `docs/superpowers/plans/2026-05-17-live-agent-final-form.md`
- Test: `tests/test_cli_timeout.py`
- Test: `tests/test_docs_architecture.py`

- [x] **Step 1: Add RED coverage for scriptable engagement control**

Cover `live-agent engagement --agent-id ... --mode ...`, endpoint quoting, JSON output, and a real GUI-server update that proves the CLI changes `engagement_mode` without refreshing `last_seen_at`.

- [x] **Step 2: Wire the CLI to the explicit engagement endpoint**

Add `assemble live-agent engagement` with valid mode choices, `--json`, and a `POST /api/live-agents/<agent_id>/engagement` request body of `{"engagement_mode": mode}`.

- [x] **Step 3: Document CLI operator usage**

Document that runtime engagement policy can be changed through GUI, API, or CLI, and that the CLI supports raw JSON output for automation.

---

### Task 30: Meeting SSE Runtime Error Boundary

**Files:**
- Modify: `agentsassemble/gui.py`
- Modify: `docs/live-agent-ops.md`
- Modify: `docs/superpowers/plans/2026-05-17-live-agent-final-form.md`
- Test: `tests/test_gui_server.py`
- Test: `tests/test_docs_architecture.py`

- [x] **Step 1: Add RED coverage for meeting stream disappearance**

Cover a `/api/meetings/<meeting_id>/events` SSE connection that starts while the meeting exists, then loses the meeting directory while the stream is still open. The server must emit an `event: error` SSE payload instead of leaking a handler traceback.

- [x] **Step 2: Bound runtime stream errors after headers are sent**

Catch runtime meeting-stream failures inside `_send_sse_stream()`, including missing meetings and file-read races after headers have already been sent. Write one `event: error` payload containing `stream`, `meeting_id`, and the error message, flush it, and close the connection. Preserve the existing pre-stream JSON `404` for meetings that are missing before the request enters the SSE loop.

- [x] **Step 3: Document stream failure semantics**

Document that GUI event streams close gracefully with a bounded SSE error event when a meeting disappears during an already-open connection.

---

### Task 31: Live-Agent Control Operation History

**Files:**
- Create: `agentsassemble/live_agent_operations.py`
- Modify: `agentsassemble/gui.py`
- Modify: `agentsassemble/cli.py`
- Modify: `agentsassemble/static/shared.js`
- Modify: `agentsassemble/static/lobby.js`
- Modify: `agentsassemble/static/lobby.css`
- Modify: `docs/live-agent-ops.md`
- Modify: `docs/superpowers/plans/2026-05-17-live-agent-final-form.md`
- Test: `tests/test_live_agent_operations.py`
- Test: `tests/test_gui_server.py`
- Test: `tests/test_cli_timeout.py`
- Test: `tests/test_static_ui_assets.py`
- Test: `tests/test_docs_architecture.py`

- [x] **Step 1: Add RED coverage for a safe operation ledger**

Cover safe append/read behavior for `.agentsassemble/live-agent-runs/operations.jsonl`, including bounded records, corrupt-line tolerance, recent-limit reads, and removal of sensitive detail keys such as command args, endpoint URLs, auth references, prompts, log tails, config paths, and environment-derived values.

- [x] **Step 2: Record API control operations**

Record bounded success and failure entries for process start, stop, restart, engagement update, preflight, smoke, and readiness operations. Keep ordinary heartbeat polling and health reads out of the ledger so the history stays useful as operator evidence rather than noisy runtime telemetry.

- [x] **Step 3: Expose API, CLI, and GUI history surfaces**

Add `GET /api/live-agent-operations?limit=N`, `assemble live-agent operations list --limit N [--json]`, and a compact GUI "최근 작업" list inside the existing "상주 실행" panel. Keep lifecycle events separate from operation history.

- [x] **Step 4: Document operator semantics**

Document `operations.jsonl`, API and CLI usage, safe redaction limits, and the distinction between control operations and process lifecycle events.

---

### Task 32: Explicit GUI Startup Autostart For Resident Groups

**Files:**
- Modify: `agentsassemble/cli.py`
- Modify: `agentsassemble/gui.py`
- Modify: `docs/live-agent-ops.md`
- Modify: `docs/superpowers/plans/2026-05-17-live-agent-final-form.md`
- Test: `tests/test_cli_timeout.py`
- Test: `tests/test_gui_server.py`
- Test: `tests/test_docs_architecture.py`

- [x] **Step 1: Add RED coverage for explicit GUI autostart**

Cover new `assemble gui --live-agent-config ...` parser options, the default no-autostart path, bound-server URL handoff for `--port 0`, safe `process.autostart` success history, and failed autostart history that still leaves the GUI serving.

- [x] **Step 2: Wire explicit GUI autostart through the supervisor**

Pass the optional GUI autostart flags into `serve_gui()`, start one supervised group only when a config path is explicitly provided, use the actual bound server URL, and keep startup failures inside a bounded operation record instead of preventing the GUI room from serving.

- [x] **Step 3: Document operator semantics**

Document that GUI startup autostart is explicit, does not start `configs/live-agents.example.json` by default, uses the existing supervised preflight/start path, records `process.autostart`, and preserves GUI availability after autostart refusal or failure.

---

### Task 33: Manifest-Aware Resident Connection Evidence

**Files:**
- Modify: `agentsassemble/gui.py`
- Modify: `agentsassemble/cli.py`
- Modify: `agentsassemble/static/lobby.js`
- Modify: `agentsassemble/static/lobby.css`
- Modify: `docs/live-agent-ops.md`
- Modify: `docs/superpowers/plans/2026-05-17-live-agent-final-form.md`
- Test: `tests/test_gui_server.py`
- Test: `tests/test_cli_timeout.py`
- Test: `tests/test_static_ui_assets.py`
- Test: `tests/test_docs_architecture.py`

- [x] **Step 1: Add RED coverage for manifest-to-presence evidence**

Cover `/api/live-agent-processes` output-only `agent_connection`, health degradation when a running non-diagnostic group has missing or stale manifest agents, diagnostic group exclusion, CLI health/process summaries, GUI rendering hooks, and operator docs.

- [x] **Step 2: Join process manifests to live-agent presence at read boundaries**

Add read-only connection evidence with `expected`, `connected`, and bounded attention entries. Count only current `online` and `working` presence as connected; report `missing`, `stale`, `offline`, or `error` for manifest agents that need operator attention. Do not persist connection evidence into `processes.json`.

- [x] **Step 3: Surface connection evidence to operators**

Expose connection health through `/api/live-agent-health`, `live-agent health`, `live-agent processes list`, and GUI process rows. Keep health read-only by using supervisor snapshots and preserve diagnostic smoke isolation.

- [x] **Step 4: Document operator semantics**

Document manifest-aware connection evidence, health degradation rules, diagnostic exclusions, and the `agent_connection` output-only API field.

---

### Task 34: Targeted Resident Reply Probe

**Files:**
- Create: `agentsassemble/live_agent_probe.py`
- Modify: `agentsassemble/gui.py`
- Modify: `agentsassemble/cli.py`
- Modify: `agentsassemble/static/shared.js`
- Modify: `agentsassemble/static/lobby.js`
- Modify: `agentsassemble/static/lobby.css`
- Modify: `docs/live-agent-ops.md`
- Modify: `docs/superpowers/plans/2026-05-17-live-agent-final-form.md`
- Test: `tests/test_live_agent_probe.py`
- Test: `tests/test_gui_server.py`
- Test: `tests/test_cli_timeout.py`
- Test: `tests/test_static_ui_assets.py`
- Test: `tests/test_docs_architecture.py`

- [x] **Step 1: Add RED coverage for a targeted reply proof**

Cover a probe that appends one diagnostic lobby event, succeeds only when the target agent replies with matching `actor_id` and `source_event_id`, ignores old/wrong/unlinked replies, skips non-live agents without appending a probe event, and rejects unknown agents.

- [x] **Step 2: Expose probe through API, CLI, and GUI**

Add `POST /api/live-agents/<agent_id>/probe`, `assemble live-agent probe --agent-id ...`, and a per-agent GUI `probe` button. The probe waits for the resident runner to answer through the ordinary room path; it does not call providers directly, start processes, or change engagement mode.

- [x] **Step 3: Record safe operation evidence**

Record bounded `probe.run` operation entries with agent id, result status, timeout, source event id, and reply event id. Do not record probe or reply message text in `operations.jsonl`.

- [x] **Step 4: Document operator semantics**

Document source-event reply proof, exit codes, GUI button behavior, and timeout limitations for passive policy modes, cooldown, provider failures, and remote bridge failures.

- [x] **Review hardening: Align timeout proof and endpoint evidence**

After xhigh review, tightened the probe so CLI HTTP timeout outlives the probe wait window, GUI operation history records the same effective timeout cap the probe used, and probe success requires the server-issued `live_agent_endpoint` flag from `/api/live-agents/<agent_id>/lobby` instead of accepting generic lobby posts with matching metadata.

---

### Task 35: Remote Bridge Credential-Free Smoke Coverage

**Files:**
- Modify: `agentsassemble/live_agent_smoke.py`
- Modify: `docs/live-agent-ops.md`
- Modify: `docs/superpowers/plans/2026-05-17-live-agent-final-form.md`
- Test: `tests/test_live_agent_smoke.py`
- Test: `tests/test_cli_timeout.py`
- Test: `tests/test_gui_server.py`
- Test: `tests/test_docs_architecture.py`

- [x] **Step 1: Add RED coverage for all resident connection kinds**

Cover a credential-free smoke config and cursor seeding path that includes `local_cli`, `live_session`, and `remote_bridge`, plus CLI/GUI smoke expectations for `smoke remote_bridge ok`.

- [x] **Step 2: Add loopback fake remote bridge to smoke**

Start a temporary loopback bridge server inside `run_live_agent_smoke()`, require its bearer token, include a `remote_bridge` resident group member with a literal smoke auth ref, and return `smoke remote_bridge ok` through the normal bridge response envelope.

- [x] **Step 3: Require live-agent endpoint evidence for smoke replies**

Accept smoke replies only when their `actor_id`, message, `source_event_id`, and server-issued `live_agent_endpoint` evidence match the smoke probe event, so generic lobby posts cannot forge the operator smoke.

- [x] **Step 4: Document operator semantics**

Document that `live-agent smoke`, GUI `진단`, and `live-agent doctor` now cover fake `local_cli`, `live_session`, and fake `remote_bridge` without real provider credentials or paid/network model calls.

---

### Task 36: Manifest-Backed Readiness Group Probes

**Files:**
- Modify: `agentsassemble/cli.py`
- Modify: `agentsassemble/gui.py`
- Modify: `docs/live-agent-ops.md`
- Test: `tests/test_cli_timeout.py`
- Test: `tests/test_gui_server.py`
- Test: `tests/test_docs_architecture.py`

- [x] **Step 1: Add RED coverage for group-level readiness probes**

Cover `live-agent doctor --probe-group`, readiness payload `probe_group_ids`, group manifest expansion, explicit-agent/group-agent de-duping, invalid group refusal, and over-cap refusal without running partial real-provider probes.

- [x] **Step 2: Expand groups from the supervisor manifest**

Resolve requested groups from the supervisor snapshot's safe launch-time `agents` manifest, require groups to be currently `running`, and merge manifest agent ids with explicit `probe_agent_ids` before reusing the existing targeted probe path.

- [x] **Step 3: Preserve bounded operator evidence**

Return bounded `probe_groups`, `effective_probe_agent_ids`, and per-agent probe statuses. Record safe operation details containing group ids, effective agent ids, group statuses, and probe statuses only; do not record config paths, endpoints, auth refs, prompts, log tails, or reply text.

- [x] **Step 4: Document side-effect and manifest semantics**

Document `--probe-group` as an explicit real-resident check, explain that it uses launch-time manifest evidence rather than rereading edited config files, and preserve the 10-agent cap after explicit and group-expanded ids are merged.

---

### Task 37: Moderator-Called Official Live-Agent Turns

**Files:**
- Modify: `agentsassemble/meeting_events.py`
- Modify: `agentsassemble/live_agents.py`
- Modify: `agentsassemble/live_agent_runner.py`
- Modify: `agentsassemble/gui.py`
- Modify: `agentsassemble/cli.py`
- Modify: `docs/live-agent-ops.md`
- Modify: `docs/superpowers/plans/2026-05-17-live-agent-final-form.md`
- Test: `tests/test_live_agent_runner.py`
- Test: `tests/test_gui_server.py`
- Test: `tests/test_cli_timeout.py`

- [x] **Step 1: Add RED coverage for official turn request/reply**

Cover `moderator_called` runner behavior, live-room payload `live_events`, `POST /api/meetings/<meeting_id>/live-agent-turns/request`, `POST /api/live-agents/<agent_id>/official-turn`, and `assemble live-agent call`.

- [x] **Step 2: Keep official turns separate from lobby chat**

Append `live_agent_turn_request` as a system/non-official live event, append replies as official `message` live events, and keep both paths out of `lobby.jsonl`.

- [x] **Step 3: Preserve source-event validation and cursor boundaries**

Require the reply source event to exist, be a targeted `live_agent_turn_request`, and match the path agent. Make repeated replies for the same request idempotent, derive official role/turn metadata from the server-side request, and persist `last_observed_live_event_id` separately from lobby `last_observed_event_id`.

- [x] **Step 4: Document operator semantics**

Document the backend API, CLI wrapper, system-vs-official event boundary, id/cursor behavior, and safe operation-history limits.

---

### Task 38: Official Turn Awaiter

**Files:**
- Create: `agentsassemble/live_agent_turns.py`
- Modify: `agentsassemble/gui.py`
- Modify: `agentsassemble/cli.py`
- Modify: `docs/live-agent-ops.md`
- Modify: `docs/superpowers/plans/2026-05-17-live-agent-final-form.md`
- Test: `tests/test_live_agent_turns.py`
- Test: `tests/test_gui_server.py`
- Test: `tests/test_cli_timeout.py`
- Test: `tests/test_docs_architecture.py`

- [x] **Step 1: Add RED coverage for bounded official turn completion**

Cover exact official reply matching, full live-event log lookup beyond the default tail, timeout without fabricated replies, CLI `--wait`, and the backend `POST /api/meetings/<meeting_id>/live-agent-turns/call` path.

- [x] **Step 2: Add a backend wait primitive**

Read the full `live_events.jsonl` and accept only a `kind: "message"`, `channel: "official"`, `official_record: true` reply whose `actor_id` and `source_event_id` match the requested agent and turn request.

- [x] **Step 3: Expose API and CLI completion semantics**

Keep the existing immediate request path unchanged. Add `live-agent call --wait --timeout N`, return `0` for answered and `1` for timeout, and bound the HTTP client timeout around the requested wait window.

- [x] **Step 4: Preserve operation-history hygiene**

Record safe result ids, timing, target, role, and turn metadata only. Do not record request content, reply content, prompts, endpoints, config paths, auth refs, command arguments, or log tails.

---

### Task 39: Official Live Transcript Projection

**Files:**
- Create: `agentsassemble/live_transcript.py`
- Modify: `agentsassemble/gui.py`
- Modify: `docs/live-agent-ops.md`
- Modify: `docs/superpowers/plans/2026-05-17-live-agent-final-form.md`
- Test: `tests/test_live_transcript.py`
- Test: `tests/test_gui_server.py`
- Test: `tests/test_docs_architecture.py`

- [x] **Step 1: Add RED coverage for live transcript projection**

Cover official-event-only filtering, exclusion of turn request/control content, full-log projection beyond the default event tail, empty projection without official events, payload fallback when `transcript.md` is missing, and preservation of existing completed transcripts.

- [x] **Step 2: Add a pure projection renderer**

Render transcript text from `live_events.jsonl` using only official transcript events with `official_record: true`, `channel: "official"`, `kind: "message"` or `kind: "synthesis"`, and non-empty content. Preserve safe event metadata and append-order semantics.

- [x] **Step 3: Surface projection through the shared meeting payload**

When `build_meeting_payload()` has no `transcript.md` file for a running or partial live meeting, fill the artifact payload from the projected live transcript. Existing transcript files remain authoritative and are not overwritten.

- [x] **Step 4: Keep the slice artifact-only**

Do not update `decision.md`, Decision Gate, tasks, memory, `meeting.json`, operation history, or physical transcript files in this slice.

---

### Task 40: Official Turn Sequence Primitive

**Files:**
- Modify: `agentsassemble/gui.py`
- Modify: `agentsassemble/cli.py`
- Modify: `docs/live-agent-ops.md`
- Modify: `docs/superpowers/plans/2026-05-17-live-agent-final-form.md`
- Test: `tests/test_gui_server.py`
- Test: `tests/test_cli_timeout.py`
- Test: `tests/test_docs_architecture.py`

- [x] **Step 1: Add RED coverage for ordered official turn sequences**

Cover the backend sequence endpoint, ordered two-agent success, timeout continuation, stop-on-timeout skipped turns, all-turn validation before the first append, CLI parser, inline JSON/file JSON loading, CLI exit codes, and operation-history privacy.

- [x] **Step 2: Reuse the single-turn request/wait contract**

Add `POST /api/meetings/<meeting_id>/live-agent-turns/sequence` as a server-side loop that validates every requested turn first, then creates one request and waits for its verified official reply before moving to the next turn.

- [x] **Step 3: Expose CLI sequence control**

Add `assemble live-agent call-sequence` with `--turns-json` or `--turns-file`, a per-turn default `--timeout`, `--stop-on-timeout`, JSON output, bounded HTTP timeout, and exit `0` only when every turn answered.

- [x] **Step 4: Preserve artifact and operation boundaries**

Write only the normal live request/reply events, the normal sanitized per-reply `official_turn.reply` entries produced by the reply endpoint, and one sanitized aggregate `official_turn.sequence` operation. Do not write transcript files, decision artifacts, tasks, memory, meeting records, prompts, reply text, endpoints, config paths, auth refs, commands, or logs.

---

### Task 41: Resident Session Start Coordinator

**Files:**
- Create: `agentsassemble/live_agent_sessions.py`
- Modify: `agentsassemble/gui.py`
- Modify: `agentsassemble/cli.py`
- Modify: `docs/live-agent-ops.md`
- Test: `tests/test_live_agent_sessions.py`
- Test: `tests/test_gui_server.py`
- Test: `tests/test_cli_timeout.py`
- Test: `tests/test_docs_architecture.py`

- [x] **Step 1: Add RED coverage for safe session composition**

Cover manifest/binding mismatch refusal before meeting or process records are written, API success with a fake supervisor, `starting` status when bound agents have not connected yet, CLI parser/payload/exit semantics, and sanitized operation history.

- [x] **Step 2: Add a backend coordinator**

Compose the existing preflight gate, resident meeting creation, supervised process start, and read-only presence evidence in `start_live_agent_session()`. Preflight and resident manifest coverage checks run before meeting creation; partial meeting/process state remains visible after later launch or connection delays.

- [x] **Step 3: Expose API and CLI controls**

Add `POST /api/live-agent-sessions/start` and `assemble live-agent start-session`. Return `ready` when every bound agent is `online` or `working` for the created meeting, otherwise return `starting`; the CLI exits `0` for `ready`, `1` for `starting`, and `2` for refused/transport/argument failures.

- [x] **Step 4: Preserve privacy and non-goals**

Record one sanitized `session.start` operation with ids, counts, result status, and safe connection attention only. Do not record config paths, command arguments, endpoints, auth refs, prompts, logs, provider output, or official turn content. Do not run official turns, smoke probes, model calls, remote bridge `/agentsassemble/run`, decisions, or transcript finalization.

---

### Task 42: Resident Session Start Review Hardening

**Files:**
- Modify: `agentsassemble/live_agent_sessions.py`
- Modify: `agentsassemble/gui.py`
- Modify: `docs/live-agent-ops.md`
- Test: `tests/test_live_agent_sessions.py`
- Test: `tests/test_gui_server.py`

- [x] **Step 1: Add RED coverage for reviewed risks**

Cover provider-kind mismatch refusal before meeting/process writes, remote-provider connection-kind mismatch refusal, non-running process groups that should not produce `ready`, and post-meeting process launch failure evidence.

- [x] **Step 2: Tighten resident manifest validation**

Require the resident group manifest to match the meeting-bound agent ids and direct provider kinds, and require the resident connection kind to be compatible with the configured provider kind before creating the meeting. For `remote_http_bridge`, validate `remote_bridge` transport compatibility while allowing the resident `provider_kind` to name the bridged agent runtime.

- [x] **Step 3: Tighten readiness evidence**

Return `ready` only when the supervised group reports `running`, its process manifest covers every expected agent, and every expected agent is `online` or `working` for the created meeting. Include sanitized process status and attention in the session payload and operation details.

- [x] **Step 4: Preserve recovery evidence on partial launch failure**

If process launch fails after the visible meeting is created, raise a sanitized session-start error carrying the safe meeting id, and include that id in the failed HTTP response and `session.start` operation so an operator can recover or clean up deliberately.

---

### Task 43: Bounded Resident Session Soak Evidence

**Files:**
- Modify: `agentsassemble/live_agent_smoke.py`
- Modify: `agentsassemble/cli.py`
- Modify: `agentsassemble/gui.py`
- Modify: `docs/live-agent-ops.md`
- Modify: `docs/roadmap.md`
- Test: `tests/test_live_agent_smoke.py`
- Test: `tests/test_cli_timeout.py`
- Test: `tests/test_gui_server.py`
- Test: `tests/test_docs_architecture.py`

- [x] **Step 1: Add RED coverage for same-session soak cycles**

Cover `run_live_agent_session_smoke(..., soak_cycle_count=2, soak_interval_seconds=0.5)` keeping the recovered diagnostic session alive before stop, waiting the bounded interval before each cycle, running `check-session`, preserving `always` engagement, posting one human lobby probe per soak cycle, verifying all three fake resident replies by `source_event_id`, and returning safe `soak_cycle_count`, `soak_interval_seconds`, `soak_check_statuses`, `soak_reply_count`, `soak_source_event_ids`, and `soak_replies` metadata.

Run: `python3 -m unittest tests.test_live_agent_smoke.LiveAgentSmokeTests.test_session_smoke_can_run_same_session_soak_cycles`
Expected: fail because `soak_cycle_count` is not accepted and no soak fields exist.

- [x] **Step 2: Add RED coverage for operator surfaces and bounds**

Cover `live-agent session-smoke --soak-cycles 2 --soak-interval 0.5`, parser rejection for values above the cycle and interval bounds, API payload forwarding through `/api/live-agent-session-smoke`, readiness forwarding through `--session-smoke-soak-cycles` and `--session-smoke-soak-interval`, safe operation details with counts/statuses only, and docs mentioning `--soak-cycles`.

Run:

```bash
python3 -m unittest \
  tests.test_cli_timeout.CliTimeoutTests.test_live_agent_session_smoke_parses_operator_options \
  tests.test_cli_timeout.CliTimeoutTests.test_live_agent_session_smoke_rejects_unbounded_soak_cycles \
  tests.test_cli_timeout.CliTimeoutTests.test_live_agent_session_smoke_rejects_unbounded_soak_interval \
  tests.test_cli_timeout.CliTimeoutTests.test_live_agent_session_smoke_posts_endpoint_and_prints_summary \
  tests.test_cli_timeout.CliTimeoutTests.test_live_agent_doctor_can_request_session_smoke \
  tests.test_gui_server.GuiServerTests.test_live_agent_session_smoke_endpoint_records_safe_operation \
  tests.test_gui_server.GuiServerTests.test_live_agent_readiness_endpoint_runs_opt_in_session_smoke \
  tests.test_docs_architecture.DocsArchitectureTests
```

Expected: fail on missing parser/API/doc fields before implementation.

- [x] **Step 3: Implement bounded same-session soak**

Add small bounded `soak_cycle_count` and `soak_interval_seconds` parsers and validators. Defaults are `0` and `0.0` so current fast smoke and readiness behavior stay unchanged. For each soak cycle after `recover-session` and post-recover reply verification, wait the bounded interval, call `check-session`, require `ready`, set fake agents back to `always`, post one phase-labeled lobby probe, wait for endpoint-backed replies, then continue. Stop still runs in the existing `finally` block.

Run targeted smoke, CLI, GUI, and docs tests from Steps 1-2.
Expected: pass.

- [x] **Step 4: Preserve privacy and operation hygiene**

Return direct `session-smoke` payloads may include safe event/reply ids like the existing direct smoke payload, but `session.smoke` operation details and readiness-safe summaries must include only counts/statuses. Do not record source event ids, reply ids, reply text, temporary config paths, endpoint URLs, auth refs, commands, provider output, tokens, or log tails in operation history.

Run: `python3 -m unittest tests.test_gui_server.GuiServerTests.test_live_agent_session_smoke_endpoint_records_safe_operation`
Expected: pass with no soak source ids or reply ids in operation history.

---

### Task 44: GUI Session Smoke Soak Controls

**Files:**
- Modify: `agentsassemble/static/lobby.js`
- Modify: `docs/live-agent-ops.md`
- Modify: `docs/roadmap.md`
- Test: `tests/static_lobby_runtime_smoke.mjs`
- Test: `tests/test_static_ui_assets.py`
- Test: `tests/test_docs_architecture.py`

- [x] **Step 1: Add RED coverage for GUI soak controls**

Cover bounded GUI fields for session smoke soak cycles and interval, draft preservation across re-render, `세션진단` posting `soak_cycle_count` and `soak_interval_seconds` only when cycles are positive, `점검` with `세션 포함` posting namespaced readiness soak fields, and status summaries that include soak reply counts without exposing ids or reply text.

Run:

```bash
node tests/static_lobby_runtime_smoke.mjs
python3 -m unittest tests.test_static_ui_assets.StaticUiAssetTests.test_lobby_separates_stage_from_activity_feed
```

Expected: fail before the GUI inputs and payload helpers exist.

- [x] **Step 2: Implement GUI payload and summary wiring**

Add the two bounded numeric controls to the existing `상주 실행` form, preserve them in `readLiveAgentProcessDraft()` / `restoreLiveAgentProcessDraft()`, clamp cycles to `0-5`, clamp interval to `0-60`, send direct session-smoke fields for `세션진단`, send readiness namespaced fields only when `세션 포함` is enabled and cycles are positive, and include soak counts in direct/readiness status labels.

Run the Step 1 commands.
Expected: pass.

- [x] **Step 3: Document operator behavior**

Document that GUI soak controls share the same bounds as CLI soak options, keep the fast default when cycles are `0`, and feed both direct session smoke and readiness session smoke when enabled.

Run: `python3 -m unittest tests.test_docs_architecture.DocsArchitectureTests.test_live_agent_ops_documents_operator_smoke_path`
Expected: pass.

---

### Task 45: High-Signal Operation Soak Evidence

**Files:**
- Modify: `agentsassemble/cli.py`
- Modify: `agentsassemble/static/lobby.js`
- Modify: `docs/live-agent-ops.md`
- Test: `tests/test_cli_timeout.py`
- Test: `tests/static_lobby_runtime_smoke.mjs`
- Test: `tests/test_docs_architecture.py`

- [x] **Step 1: Add RED coverage for operation detail priority**

Cover `live-agent operations list` and the GUI `최근 작업` row for a `session.smoke` operation whose safe details include many fields. The compact default view must still show `result_status`, base reply count, post-recover reply count, soak cycle count, soak reply count, and soak check statuses before less useful identifiers push them out.

Run:

```bash
python3 -m unittest tests.test_cli_timeout.CliTimeoutTests.test_live_agent_operations_list_prioritizes_session_smoke_soak_evidence
node tests/static_lobby_runtime_smoke.mjs
```

Expected: fail before operation detail priority is implemented because generic insertion order hides the soak evidence behind group/meeting/agent metadata.

- [x] **Step 2: Prioritize safe liveness details in compact operation views**

Keep the JSON operation payload unchanged. In the CLI formatter and GUI operation-row formatter, order details by operation type before applying the existing six-detail compact display. For `session.smoke`, prioritize result status, reply count, post-recover count, soak cycle count, soak reply count, and soak check statuses. For `readiness.check`, prioritize readiness result plus session-smoke reply/soak fields before probe lists.

Run the Step 1 commands.
Expected: pass.

- [x] **Step 3: Document compact operation evidence**

Document that the recent operation GUI list and default CLI output prioritize high-signal session-smoke/readiness liveness evidence while `--json` remains the full sanitized payload.

Run: `python3 -m unittest tests.test_docs_architecture.DocsArchitectureTests.test_live_agent_ops_documents_operator_smoke_path`
Expected: pass.

---

### Task 46: Bulk Stop Running Process Groups

**Files:**
- Modify: `agentsassemble/live_agent_processes.py`
- Modify: `agentsassemble/gui.py`
- Modify: `agentsassemble/cli.py`
- Modify: `agentsassemble/static/lobby.js`
- Modify: `agentsassemble/static/shared.js`
- Modify: `docs/live-agent-ops.md`
- Test: `tests/test_live_agent_processes.py`
- Test: `tests/test_gui_server.py`
- Test: `tests/test_cli_timeout.py`
- Test: `tests/static_lobby_runtime_smoke.mjs`
- Test: `tests/test_static_ui_assets.py`

- [x] **Step 1: Add RED coverage for a safe bulk stop path**

Cover the supervisor stopping owned `running` process groups and canceling `restarting` pending auto-restart groups while skipping stopped or historical records. Cover `assemble live-agent processes stop-running`, `POST /api/live-agent-processes/stop-running`, and the GUI `실행중지` button.

Run:

```bash
python3 -m unittest tests.test_live_agent_processes.LiveAgentProcessSupervisorTests.test_stop_running_groups_stops_owned_running_and_pending_restart_groups
python3 -m unittest tests.test_cli_timeout.CliTimeoutTests.test_live_agent_processes_stop_running_parser_accepts_json tests.test_cli_timeout.CliTimeoutTests.test_live_agent_processes_stop_running_posts_bulk_endpoint
python3 -m unittest tests.test_gui_server.GuiServerTests.test_live_agent_process_stop_running_endpoint_records_sanitized_operation
node tests/static_lobby_runtime_smoke.mjs
```

Expected: fail before implementation because the supervisor method, CLI subcommand, HTTP endpoint, and GUI button do not exist.

- [x] **Step 2: Implement stop-running across supervisor/API/CLI/GUI**

Add `stop_running_groups()` to the local supervisor. It should refresh process state, stop owned running groups, cancel pending `restarting` groups, skip non-running historical records, aggregate failures without aborting the whole operation, and return stopped/failed/skipped counts. Expose the same behavior through `POST /api/live-agent-processes/stop-running`, `assemble live-agent processes stop-running [--json]`, and the GUI `실행중지` button.

Run the Step 1 commands.
Expected: pass.

- [x] **Step 3: Document bulk stop operator behavior**

Document that bulk stop is for currently running or pending-restart groups, does not signal historical `unknown`/`error`/`stopped` records, and records only sanitized counts and group ids in `process.stop_running`.

Run: `python3 -m unittest tests.test_docs_architecture.DocsArchitectureTests.test_live_agent_ops_documents_operator_smoke_path`
Expected: pass.

---

### Task 47: Reconcile Presence On Process Stop

**Files:**
- Modify: `agentsassemble/live_agent_processes.py`
- Modify: `docs/live-agent-ops.md`
- Test: `tests/test_live_agent_processes.py`
- Test: `tests/test_docs_architecture.py`

- [x] **Step 1: Add RED coverage for process-stop presence reconciliation**

Cover a stopped supervised group whose launch-time manifest agents have existing `online` or `working` presence rows. After `stop_group()`, matching rows for the same meeting should be `offline` immediately. Also cover two safety guards: a same agent id attached to another meeting remains untouched, and an agent still expected by another running group for the same meeting remains online.

Run:

```bash
python3 -m unittest tests.test_live_agent_processes.LiveAgentProcessSupervisorTests.test_stop_group_marks_matching_manifest_agents_offline tests.test_live_agent_processes.LiveAgentProcessSupervisorTests.test_stop_group_does_not_offline_manifest_agent_from_another_meeting tests.test_live_agent_processes.LiveAgentProcessSupervisorTests.test_stop_group_does_not_offline_agent_still_owned_by_another_running_group
```

Expected: fail before implementation because process stop updates `processes.json` but leaves matching presence rows `online` or `working`.

- [x] **Step 2: Mark safe manifest presence rows offline on process exit/stop**

When a supervised group is stopped, errors without auto-restart, fails auto-restart, or has a pending restart canceled, mark existing manifest agents `offline` only when their current presence row belongs to the same meeting and no other running or restarting group for that meeting still expects that agent. Do not create missing presence rows or mutate wrong-meeting rows.

Run the Step 1 command and `python3 -m unittest tests.test_live_agent_processes`.
Expected: pass.

- [x] **Step 3: Document stop-time roster reconciliation**

Document that process stop now reconciles existing manifest presence rows immediately, while preserving wrong-meeting and still-owned-by-another-running-group safety boundaries.

Run: `python3 -m unittest tests.test_docs_architecture.DocsArchitectureTests.test_live_agent_ops_documents_operator_smoke_path`
Expected: pass.

### Task 48: Surface Process-Stop Offline Evidence

**Files:**
- Modify: `agentsassemble/live_agent_processes.py`
- Modify: `agentsassemble/gui.py`
- Modify: `agentsassemble/cli.py`
- Modify: `docs/live-agent-ops.md`
- Test: `tests/test_live_agent_processes.py`
- Test: `tests/test_gui_server.py`
- Test: `tests/test_cli_timeout.py`
- Test: `tests/test_docs_architecture.py`

- [x] **Step 1: Add RED coverage for stop-time offline evidence**

Extend process stop and bulk stop tests so `stop_group()` and `stop_running_groups()` must return an output-only `offline` reconciliation summary with expected/offline/skipped counts, safe `offline_agent_ids`, and attention entries for missing, wrong-meeting, or still-owned manifest agents. Extend CLI/API operation tests so the default stop output and sanitized operation details surface those counts without recording config paths, server URLs, command arguments, auth refs, log tails, prompts, or provider output.

Run:

```bash
python3 -m unittest tests.test_live_agent_processes.LiveAgentProcessSupervisorTests.test_stop_group_marks_matching_manifest_agents_offline tests.test_live_agent_processes.LiveAgentProcessSupervisorTests.test_stop_group_does_not_offline_manifest_agent_from_another_meeting tests.test_live_agent_processes.LiveAgentProcessSupervisorTests.test_stop_group_does_not_offline_agent_still_owned_by_another_running_group tests.test_live_agent_processes.LiveAgentProcessSupervisorTests.test_stop_running_groups_stops_owned_running_and_pending_restart_groups
python3 -m unittest tests.test_cli_timeout.CliTimeoutTests.test_live_agent_processes_stop_restart_and_recover_quote_group_id tests.test_cli_timeout.CliTimeoutTests.test_live_agent_processes_stop_running_posts_bulk_endpoint
python3 -m unittest tests.test_gui_server.GuiServerTests.test_live_agent_process_endpoints_start_list_and_stop_group tests.test_gui_server.GuiServerTests.test_live_agent_process_stop_running_endpoint_records_sanitized_operation
python3 -m unittest tests.test_docs_architecture.DocsArchitectureTests.test_live_agent_ops_documents_operator_smoke_path
```

Expected: fail before implementation because the roster reconciliation side effect exists but no public stop payload or operation detail exposes the evidence.

- [x] **Step 2: Attach safe output-only summaries at stop boundaries**

Return the reconciliation summary from the supervisor's manifest-offline helper and attach it only to stop/restart-failure output records, not persisted `processes.json`. Aggregate the summaries for `stop-running`, include concise CLI text such as `offline 2/3`, and record only bounded counts, safe agent ids, and compact attention strings in `process.stop` and `process.stop_running` operation details.

Run the Step 1 commands.
Expected: pass.

- [x] **Step 3: Document operator evidence**

Document that process stop and stop-running responses include the offline reconciliation summary and that operation history records only sanitized counts, safe `offline_agent_ids`, and compact attention.

Run: `python3 -m unittest tests.test_docs_architecture.DocsArchitectureTests.test_live_agent_ops_documents_operator_smoke_path`
Expected: pass.

### Task 49: Surface Crash-Time Offline Evidence In Lifecycle Events

**Files:**
- Modify: `agentsassemble/live_agent_processes.py`
- Modify: `docs/live-agent-ops.md`
- Test: `tests/test_live_agent_processes.py`
- Test: `tests/test_docs_architecture.py`

- [x] **Step 1: Add RED coverage for lifecycle offline evidence**

Extend lifecycle tests so stop, crash `restart_scheduled`, and failed immediate auto-restart events must include the same safe output-only offline reconciliation summary that process stop responses expose. Keep `processes.json` free of output-only `recent_events` and `offline` payloads.

Run:

```bash
python3 -m unittest tests.test_live_agent_processes.LiveAgentProcessSupervisorTests.test_start_and_stop_write_safe_lifecycle_events tests.test_live_agent_processes.LiveAgentProcessSupervisorTests.test_auto_restart_writes_lifecycle_events_for_crash_and_relaunch tests.test_live_agent_processes.LiveAgentProcessSupervisorTests.test_auto_restart_failure_writes_restart_failed_lifecycle_event
python3 -m unittest tests.test_docs_architecture.DocsArchitectureTests.test_live_agent_ops_documents_operator_smoke_path
```

Expected: fail before implementation because `events.jsonl` records process status/restart facts but drops the offline reconciliation evidence.

- [x] **Step 2: Attach sanitized offline summaries to lifecycle events**

Add an optional `offline` field to lifecycle event construction and safe readback. Include only expected/offline/skipped counts, safe `offline_agent_ids`, and compact attention entries. Attach it to `stopped`, `error`, `restart_scheduled`, and `restart_failed` events only when reconciliation evidence exists.

Run the Step 1 commands.
Expected: pass.

- [x] **Step 3: Document lifecycle evidence**

Document that `events.jsonl` and process `recent_events` can now carry offline reconciliation summaries for stop/crash/restart-failure paths without including command arguments, endpoint URLs, auth refs, prompts, log tails, or environment-derived values.

Run: `python3 -m unittest tests.test_docs_architecture.DocsArchitectureTests.test_live_agent_ops_documents_operator_smoke_path`
Expected: pass.

### Task 50: Show Lifecycle Offline Evidence In Process Rows

**Files:**
- Modify: `agentsassemble/cli.py`
- Modify: `agentsassemble/static/lobby.js`
- Modify: `docs/live-agent-ops.md`
- Test: `tests/test_cli_timeout.py`
- Test: `tests/static_lobby_runtime_smoke.mjs`
- Test: `tests/test_docs_architecture.py`

- [x] **Step 1: Add RED coverage for visible lifecycle offline evidence**

Extend CLI process list and GUI process row tests so a latest `recent_events` item with an `offline` summary must render compact operator text such as `offline 1/2` and `wrong_meeting agent-b` beside the last lifecycle event label.

Run:

```bash
python3 -m unittest tests.test_cli_timeout.CliTimeoutTests.test_live_agent_processes_list_prints_summary
node tests/static_lobby_runtime_smoke.mjs
python3 -m unittest tests.test_docs_architecture.DocsArchitectureTests.test_live_agent_ops_documents_operator_smoke_path
```

Expected: fail before implementation because CLI and GUI rows show only the lifecycle event type/timestamp.

- [x] **Step 2: Render safe summaries in CLI and GUI rows**

Append only expected/offline counts and bounded attention labels from the latest lifecycle event's sanitized `offline` payload. Keep raw JSON available through `--json`; do not expose config paths beyond existing process row behavior, command arguments, endpoints, auth refs, prompts, provider output, or log tails.

Run the Step 1 commands.
Expected: pass.

- [x] **Step 3: Document visible operator behavior**

Document that the default CLI process list and GUI process rows show the latest lifecycle offline summary, so crash-time roster reconciliation can be seen without opening JSON.

Run: `python3 -m unittest tests.test_docs_architecture.DocsArchitectureTests.test_live_agent_ops_documents_operator_smoke_path`
Expected: pass.

### Task 51: Scriptable Process Lifecycle Event History

**Files:**
- Modify: `agentsassemble/live_agent_processes.py`
- Modify: `agentsassemble/gui.py`
- Modify: `agentsassemble/cli.py`
- Modify: `docs/live-agent-ops.md`
- Test: `tests/test_live_agent_processes.py`
- Test: `tests/test_gui_server.py`
- Test: `tests/test_cli_timeout.py`
- Test: `tests/test_docs_architecture.py`

- [x] **Step 1: Add RED coverage for safe lifecycle event queries**

Cover a bounded process lifecycle event reader, `GET /api/live-agent-process-events`, and `assemble live-agent processes events`. The reader must ignore corrupt lines, support an optional group filter, preserve sanitized offline summaries, and avoid returning command arguments, endpoint URLs, config paths, auth refs, prompts, provider output, or log tails. It must read from the JSONL tail instead of loading the whole history file for long sessions.

Run:

```bash
python3 -m unittest tests.test_live_agent_processes.LiveAgentProcessSupervisorTests.test_read_live_agent_process_events_returns_recent_safe_events tests.test_live_agent_processes.LiveAgentProcessSupervisorTests.test_read_live_agent_process_events_does_not_load_whole_history_file_at_once
python3 -m unittest tests.test_gui_server.GuiServerTests.test_live_agent_process_events_endpoint_returns_sanitized_tail_without_operation_record
python3 -m unittest tests.test_cli_timeout.CliTimeoutTests.test_live_agent_processes_events_fetches_filtered_history tests.test_cli_timeout.CliTimeoutTests.test_live_agent_processes_events_json_prints_raw_payload
python3 -m unittest tests.test_docs_architecture.DocsArchitectureTests.test_live_agent_ops_documents_operator_smoke_path
```

Expected: fail before implementation because there is no public lifecycle event reader, endpoint, CLI subcommand, or docs entry.

- [x] **Step 2: Expose sanitized bounded lifecycle history**

Add a public tail-reading process lifecycle event reader that reuses the existing lifecycle event sanitizer and clamps limits. Expose it through `GET /api/live-agent-process-events?limit=N&group_id=...` without recording a control operation, and through `assemble live-agent processes events [--group-id ...] [--limit ...] [--json]`.

Run the Step 1 commands.
Expected: pass.

- [x] **Step 3: Document lifecycle history inspection**

Document that lifecycle event queries read from the JSONL tail, can be filtered by group id, expose only sanitized event facts plus offline reconciliation summaries, and are separate from the control-operation ledger.

Run: `python3 -m unittest tests.test_docs_architecture.DocsArchitectureTests.test_live_agent_ops_documents_operator_smoke_path`
Expected: pass.

### Task 52: Bounded Sparse Lifecycle Event Queries

**Files:**
- Modify: `agentsassemble/live_agent_processes.py`
- Modify: `agentsassemble/gui.py`
- Modify: `agentsassemble/cli.py`
- Modify: `docs/live-agent-ops.md`
- Test: `tests/test_live_agent_processes.py`
- Test: `tests/test_gui_server.py`
- Test: `tests/test_cli_timeout.py`
- Test: `tests/test_docs_architecture.py`

- [x] **Step 1: Add RED coverage for sparse group scans**

Cover filtered lifecycle event queries whose requested group has no recent matches. The query must stop after a bounded recent lifecycle-event scan budget, return safe metadata (`limit`, normalized `group_id`, `scan_limit`, `scanned_event_count`, `truncated`), and make clear when older matches may exist outside the searched tail window. Keep the existing list-returning `read_live_agent_process_events()` compatible.

Run:

```bash
python3 -m unittest tests.test_live_agent_processes.LiveAgentProcessSupervisorTests.test_read_live_agent_process_event_history_caps_sparse_group_scan tests.test_live_agent_processes.LiveAgentProcessSupervisorTests.test_read_live_agent_process_event_history_is_not_truncated_when_result_window_fills tests.test_live_agent_processes.LiveAgentProcessSupervisorTests.test_read_live_agent_process_event_history_preserves_partial_matches_with_truncation
python3 -m unittest tests.test_gui_server.GuiServerTests.test_live_agent_process_events_endpoint_returns_sanitized_tail_without_operation_record
python3 -m unittest tests.test_cli_timeout.CliTimeoutTests.test_live_agent_processes_events_json_prints_raw_payload tests.test_cli_timeout.CliTimeoutTests.test_live_agent_processes_events_warns_when_scan_is_truncated
python3 -m unittest tests.test_docs_architecture.DocsArchitectureTests.test_live_agent_ops_documents_operator_smoke_path
```

Expected: fail before implementation because the public reader returns only events, the API has no metadata, the CLI has no `--scan-limit`, and docs do not explain truncation.

- [x] **Step 2: Add bounded history metadata**

Add `read_live_agent_process_event_history()` as the metadata-bearing helper while preserving `read_live_agent_process_events()` as a list-returning compatibility wrapper. Count sanitized lifecycle events considered from the tail before group filtering. Stop when the requested result window is filled or when the scan budget is exhausted. Expose optional `scan_limit` through API and CLI.

Run the Step 1 commands.
Expected: pass.

- [x] **Step 3: Document truncation semantics**

Document that `truncated: true` means the scan budget was exhausted and older matching lifecycle events may exist. Document CLI warning text and JSON metadata fields.

Run: `python3 -m unittest tests.test_docs_architecture.DocsArchitectureTests.test_live_agent_ops_documents_operator_smoke_path`
Expected: pass.

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
