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
