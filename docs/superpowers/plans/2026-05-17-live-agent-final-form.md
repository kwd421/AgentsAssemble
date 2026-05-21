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

### Task 53: Best-Effort Final Offline Heartbeat

**Files:**
- Modify: `agentsassemble/live_agent_runner.py`
- Modify: `docs/live-agent-ops.md`
- Test: `tests/test_live_agent_runner.py`

- [x] **Step 1: Add RED coverage for shutdown heartbeat masking**

Cover bounded resident runs where the final `offline` heartbeat fails because the room server is unavailable during shutdown. The runner must preserve a successful reply count, preserve an already handled provider command error, and avoid masking an active room/reply failure with the shutdown heartbeat failure.

Run:

```bash
python3 -m unittest tests.test_live_agent_runner.LiveAgentRunnerTests.test_runner_does_not_mask_success_when_final_offline_heartbeat_fails tests.test_live_agent_runner.LiveAgentRunnerTests.test_runner_does_not_mask_command_error_when_final_offline_heartbeat_fails tests.test_live_agent_runner.LiveAgentRunnerTests.test_runner_does_not_mask_room_failure_when_final_offline_heartbeat_fails tests.test_live_agent_runner.LiveAgentRunnerTests.test_runner_does_not_mask_lobby_post_failure_when_final_offline_heartbeat_fails
```

Expected: fail before implementation because `LiveAgentRunner.run()` sends the final `offline` heartbeat directly from `finally`.

- [x] **Step 2: Make only the final offline heartbeat best-effort**

Wrap the final `offline` heartbeat in a narrow helper that suppresses shutdown-time heartbeat failures. Keep registration, working, error, and periodic heartbeat failures visible so real room/server failures during active work are not hidden.

Run the Step 1 command.
Expected: pass.

- [x] **Step 3: Document shutdown semantics**

Document that a failed final offline heartbeat does not turn a completed bounded run or handled command error into a provider failure, and that operators should still use presence, process rows, and logs together for shutdown evidence.

Run: `python3 -m unittest tests.test_live_agent_runner.LiveAgentRunnerTests.test_runner_does_not_mask_success_when_final_offline_heartbeat_fails tests.test_live_agent_runner.LiveAgentRunnerTests.test_runner_does_not_mask_command_error_when_final_offline_heartbeat_fails tests.test_live_agent_runner.LiveAgentRunnerTests.test_runner_does_not_mask_room_failure_when_final_offline_heartbeat_fails tests.test_live_agent_runner.LiveAgentRunnerTests.test_runner_does_not_mask_lobby_post_failure_when_final_offline_heartbeat_fails`
Expected: pass.

### Task 54: Direct CLI Resident Setup Gate

**Files:**
- Modify: `agentsassemble/cli.py`
- Modify: `agentsassemble/live_agent_preflight.py`
- Modify: `docs/live-agent-ops.md`
- Test: `tests/test_cli_timeout.py`

- [x] **Step 1: Add RED coverage for direct CLI setup gaps**

Cover direct `live-agent run` with missing `local_cli` and `live_session` command executables, direct `run-group` with missing `local_cli` and `live_session` command executables, and direct `run-group` with duplicate resident agent ids. These failures must happen before resident registration or worker thread construction.

Run:

```bash
python3 -m unittest tests.test_cli_timeout.CliTimeoutTests.test_live_agent_run_rejects_missing_local_command_before_registration tests.test_cli_timeout.CliTimeoutTests.test_live_agent_run_rejects_missing_live_session_command_before_registration tests.test_cli_timeout.CliTimeoutTests.test_live_agent_run_group_rejects_missing_local_and_live_session_commands_before_launch tests.test_cli_timeout.CliTimeoutTests.test_live_agent_run_group_rejects_duplicate_agent_ids_before_launch
```

Expected: fail before implementation because direct resident CLI paths validate only command presence, not executable availability or duplicate group ids.

- [x] **Step 2: Share the command executable setup check**

Expose the preflight command resolver as a small reusable setup check, call it from direct `live-agent run` and from `run-group` before threads are started, and keep remote bridge setup validation at the same pre-start boundary.

Run the Step 1 command plus existing run-group shutdown tests.
Expected: pass.

- [x] **Step 3: Document direct CLI refusal semantics**

Document that direct resident CLI starts refuse missing local/live-session executables and duplicate group ids before registration, while supervised starts continue to use the GUI server environment for the same preflight class.

Run: `python3 -m unittest tests.test_docs_architecture.DocsArchitectureTests.test_live_agent_ops_documents_operator_smoke_path`
Expected: pass.

### Task 55: Bounded Process Row Lifecycle Scan

**Files:**
- Modify: `agentsassemble/live_agent_processes.py`
- Modify: `docs/live-agent-ops.md`
- Test: `tests/test_live_agent_processes.py`

- [x] **Step 1: Add RED coverage for full-history row scans**

Cover `list_groups()` process rows with a lifecycle history whose relevant events are in the JSONL tail and older unrelated events are at the beginning. The test forbids text iteration over `events.jsonl`, so process rows must not scan lifecycle history from the start of the file just to build compact `recent_events`.

Run:

```bash
python3 -m unittest tests.test_live_agent_processes.LiveAgentProcessSupervisorTests.test_recent_lifecycle_events_are_read_from_tail_without_iterating_old_history
```

Expected: fail before implementation because `_recent_lifecycle_events_by_group()` iterates `events.jsonl` in text mode from the beginning.

- [x] **Step 2: Reuse bounded tail scanning for process rows**

Collect process-row `recent_events` newest-first from `_jsonl_tail_lines_newest_first()`, sanitize events before counting them toward the scan budget, stop when every requested group has its row limit or the default scan budget is exhausted, and return each group's compact list in chronological order.

Run the Step 1 command plus existing recent lifecycle row tests.
Expected: pass.

- [x] **Step 3: Document compact row history limits**

Document that process rows use bounded lifecycle tail scanning; quiet groups with only older events may need the scriptable `live-agent processes events --group-id ... --scan-limit ...` path for deeper history.

Run: `python3 -m unittest tests.test_docs_architecture.DocsArchitectureTests.test_live_agent_ops_documents_operator_smoke_path`
Expected: pass.

### Task 56: Bounded Room Event JSONL Reads

**Files:**
- Modify: `agentsassemble/meeting_events.py`
- Modify: `docs/live-agent-ops.md`
- Test: `tests/test_meeting_events.py`

- [x] **Step 1: Add RED coverage for bounded room reads**

Cover limited lobby, side-chat, and live-event readers with large JSONL histories whose requested events are in the tail. The tests forbid `Path.read_text()` on those JSONL files and count binary bytes read with a small tail block size, so the limited readers must not load the whole event file.

Run:

```bash
python3 -m unittest tests.test_meeting_events
```

Expected: fail before implementation because the limited room readers load whole JSONL files with `read_text().splitlines()` before slicing the recent window.

- [x] **Step 2: Read default limited room views from the JSONL tail**

Use a shared newest-first JSONL tail iterator for limited lobby, side-chat, and meeting live-event reads. Stop as soon as enough valid events have been collected, return them in chronological order, keep corrupt-line tolerance, and preserve full live-event reads through `read_live_events(..., limit=None)` for archive/transcript paths.

Run the Step 1 command plus room endpoint/read-after coverage.
Expected: pass.

- [x] **Step 3: Document room polling limits**

Document that resident polling and GUI refresh use bounded room-event tail reads for default limited snapshots, while explicit full live-event reads remain available for complete meeting-history reconstruction.

Run: `python3 -m unittest tests.test_docs_architecture.DocsArchitectureTests.test_live_agent_ops_documents_operator_smoke_path`
Expected: pass.

### Task 57: Remote Bridge Command Failure Boundary

**Files:**
- Modify: `agentsassemble/adapters/remote_bridge.py`
- Modify: `docs/live-agent-ops.md`
- Test: `tests/test_remote_bridge_adapter.py`
- Test: `tests/test_live_agent_runner.py`

- [x] **Step 1: Add RED coverage for bridge command failure envelopes**

Cover remote bridge `/agentsassemble/run` responses that include `metadata.returncode != 0` or `metadata.timed_out: true`. Those responses must be provider failures, not normal meeting content or lobby messages. Also cover the resident remote bridge command runner and runner loop so a bridge command failure raises into the existing error-heartbeat path without posting a lobby reply.

Run:

```bash
python3 -m unittest \
  tests.test_remote_bridge_adapter.RemoteBridgeAdapterTests.test_remote_bridge_nonzero_returncode_is_provider_failure_not_content \
  tests.test_remote_bridge_adapter.RemoteBridgeAdapterTests.test_remote_bridge_timeout_metadata_is_provider_failure_not_content \
  tests.test_live_agent_runner.LiveAgentRunnerTests.test_remote_bridge_resident_command_runner_treats_command_failure_as_error \
  tests.test_live_agent_runner.LiveAgentRunnerTests.test_runner_records_remote_bridge_command_failure_as_error_heartbeat
```

Expected: fail before implementation because bridge failure text is parsed as ordinary provider content.

- [x] **Step 2: Reject failed bridge command envelopes**

At the remote bridge adapter response boundary, raise `TimeoutError` for timed-out command metadata and `ValueError` for non-zero integer return codes. Keep metadata allowlisting for successful responses unchanged and keep resident runner error sanitization responsible for auth/token-bearing exception messages. The resident runner records the failure as `error` presence metadata with the observed source event cursor and skips the lobby post.

Run the Step 1 command plus existing remote bridge adapter and resident runner bridge tests.
Expected: pass.

- [x] **Step 3: Document failure-as-error semantics**

Document that bridge request failures, command timeouts, and non-zero bridge command return codes become sanitized `error` heartbeats rather than lobby messages.

Run: `python3 -m unittest tests.test_docs_architecture.DocsArchitectureTests.test_live_agent_ops_documents_operator_smoke_path`
Expected: pass.

### Task 58: Process Connection Freshness Evidence

**Files:**
- Modify: `agentsassemble/gui.py`
- Modify: `docs/live-agent-ops.md`
- Test: `tests/test_gui_server.py`

- [x] **Step 1: Add RED coverage for pre-start presence reuse**

Cover `/api/live-agent-processes` and `/api/live-agent-health` with a running process group whose manifest agent has `online` or `working` presence, but that presence row's `last_seen_at` is older than the group's `started_at`. The process connection and health summaries must not count that agent as connected.

Run:

```bash
python3 -m unittest \
  tests.test_gui_server.GuiServerTests.test_live_agent_process_connection_evidence_requires_presence_after_group_start \
  tests.test_gui_server.GuiServerTests.test_live_agent_health_degrades_when_manifest_agent_has_not_reconnected_after_group_start
```

Expected: fail before implementation because manifest connection evidence only checks current status and meeting id.

- [x] **Step 2: Require fresh presence for process connection proof**

Parse public ISO timestamps from `started_at` and `last_seen_at`. When both are present and the presence heartbeat is older than the process start, report the manifest agent as `not_reconnected` and do not increment the connected count. Keep legacy rows without comparable timestamps on the existing status-based path.

Run the Step 1 command plus existing manifest connection evidence tests.
Expected: pass.

- [x] **Step 3: Document not-reconnected attention**

Document that `not_reconnected` means a presence row predates the supervised process start and therefore cannot prove the fresh resident process attached after restart or recovery.

Run: `python3 -m unittest tests.test_docs_architecture.DocsArchitectureTests.test_live_agent_ops_documents_operator_smoke_path`
Expected: pass.

### Task 59: Session Connection Freshness Evidence

**Files:**
- Modify: `agentsassemble/live_agent_sessions.py`
- Modify: `docs/live-agent-ops.md`
- Test: `tests/test_live_agent_sessions.py`

- [x] **Step 1: Add RED coverage for pre-start session presence reuse**

Cover `start-session`, reused-running `resume-session`, and `check-session` with a running process group whose expected agent reports `online` or `working` presence for the right meeting, but whose `last_seen_at` predates the process group's `started_at`. Session readiness must not count that stale row as connected.

Run:

```bash
python3 -m unittest \
  tests.test_live_agent_sessions.LiveAgentSessionStartTests.test_start_session_requires_presence_after_process_start \
  tests.test_live_agent_sessions.LiveAgentSessionStartTests.test_resume_session_requires_presence_after_reused_process_start \
  tests.test_live_agent_sessions.LiveAgentSessionStartTests.test_check_session_requires_presence_after_process_start
```

Expected: fail before implementation because session connection snapshots only check current status and meeting id.

- [x] **Step 2: Share freshness proof across session connection snapshots**

Pass the current process group into the shared session connection snapshot used by `start`, `resume`, `restart`, `recover`, and read-only `check`. Parse public ISO timestamps from `started_at` and `last_seen_at`; when both are comparable and the heartbeat is older than the process start, report `agent_id:not_reconnected` and leave the agent out of the connected count. Preserve legacy behavior when either timestamp is missing or unparsable.

Run the Step 1 command plus `python3 -m unittest tests.test_live_agent_sessions`.
Expected: pass.

- [x] **Step 3: Document session freshness semantics**

Document that session start/resume/check readiness uses the same fresh-heartbeat evidence as process connection views, so a pre-start `online`/`working` row cannot prove a fresh resident process attached.

Run: `python3 -m unittest tests.test_docs_architecture.DocsArchitectureTests.test_live_agent_ops_documents_operator_smoke_path`
Expected: pass.

### Task 60: Stale Watchdog Non-Live Agent Recovery

**Files:**
- Modify: `agentsassemble/live_agent_processes.py`
- Modify: `docs/live-agent-ops.md`
- Test: `tests/test_live_agent_processes.py`

- [x] **Step 1: Add RED coverage for offline/error manifest agents**

Cover a running supervised group with stale watchdog enabled where the process is still alive but the launch-time manifest agent reports `offline` or `error` presence after the watchdog threshold. The group must schedule the same bounded auto-restart path used for missing, stale, and wrong-meeting manifest agents, and the reason must not include raw provider `last_error` text.

Run:

```bash
python3 -m unittest \
  tests.test_live_agent_processes.LiveAgentProcessSupervisorTests.test_stale_watchdog_schedules_auto_restart_for_offline_manifest_agent \
  tests.test_live_agent_processes.LiveAgentProcessSupervisorTests.test_stale_watchdog_schedules_auto_restart_for_error_manifest_agent
```

Expected: fail before implementation because the watchdog only treats missing, wrong-meeting, and `stale` presence as restart reasons.

- [x] **Step 2: Treat offline/error manifest presence as non-live**

At the shared stale-watchdog reason function, after meeting ownership is checked, restart for manifest agents whose current status is `offline` or `error`. Preserve the existing fresh `online`/`working` path and keep restart reasons limited to status plus safe agent id.

Run the Step 1 command plus existing watchdog stale/fresh tests.
Expected: pass.

- [x] **Step 3: Document watchdog non-live recovery**

Document that the stale watchdog restarts owned groups when manifest agents are missing, stale, offline, error, or attached to the wrong meeting after the configured threshold.

Run: `python3 -m unittest tests.test_docs_architecture.DocsArchitectureTests.test_live_agent_ops_documents_operator_smoke_path`
Expected: pass.

### Task 61: Stale Watchdog Lifecycle Reason Evidence

**Files:**
- Modify: `agentsassemble/live_agent_processes.py`
- Modify: `docs/live-agent-ops.md`
- Test: `tests/test_live_agent_processes.py`

- [x] **Step 1: Add RED coverage for watchdog lifecycle reasons**

Extend stale-watchdog lifecycle tests so `stale_watchdog` and `stale_watchdog_stop_failed` events expose the safe restart reason in both raw lifecycle history and process row `recent_events`. Cover that provider `last_error` text is not copied into the reason.

Run:

```bash
python3 -m unittest \
  tests.test_live_agent_processes.LiveAgentProcessSupervisorTests.test_stale_watchdog_schedules_auto_restart_for_missing_manifest_agent \
  tests.test_live_agent_processes.LiveAgentProcessSupervisorTests.test_stale_watchdog_schedules_auto_restart_for_error_manifest_agent \
  tests.test_live_agent_processes.LiveAgentProcessSupervisorTests.test_stale_watchdog_stop_failure_does_not_repeat_watchdog_events
```

Expected: fail before implementation because lifecycle watchdog events only expose the event type and counters, not the reason.

- [x] **Step 2: Add sanitized lifecycle reason field**

Thread a bounded `reason` through lifecycle event construction and sanitized readback. Attach it only to stale-watchdog event paths, using the existing safe reason strings such as missing, stale, offline, error, or wrong-meeting manifest agent. Drop suspicious reason values and any non-watchdog reason instead of serializing command paths, env markers, config paths, provider output, or arbitrary persisted free text.

Run the Step 1 command plus `python3 -m unittest tests.test_live_agent_processes`.
Expected: pass.

- [x] **Step 3: Document lifecycle reason evidence**

Document that lifecycle history and process row `recent_events` retain sanitized stale-watchdog reasons without command arguments, endpoint URLs, auth refs, prompts, log tails, provider output, or environment-derived values.

Run: `python3 -m unittest tests.test_docs_architecture.DocsArchitectureTests.test_live_agent_ops_documents_operator_smoke_path`
Expected: pass.

### Task 62: CLI Lifecycle Reason Display

**Files:**
- Modify: `agentsassemble/cli.py`
- Modify: `docs/live-agent-ops.md`
- Test: `tests/test_cli_timeout.py`

- [x] **Step 1: Add RED coverage for CLI reason display**

Extend default text-output CLI coverage so `live-agent processes list` shows the latest safe watchdog reason from each process row's bounded `recent_events`, and `live-agent processes events` shows the reason on a lifecycle event line. Cover the list case where the latest row event differs from the latest reason-bearing watchdog event.

Run:

```bash
python3 -m unittest \
  tests.test_cli_timeout.CliTimeoutTests.test_live_agent_processes_list_prints_summary \
  tests.test_cli_timeout.CliTimeoutTests.test_live_agent_processes_events_fetches_filtered_history
```

Expected: fail before implementation because default CLI text output does not render lifecycle `reason`.

- [x] **Step 2: Render reason in CLI lifecycle summaries**

Append `reason <value>` to lifecycle event lines when a sanitized reason is present. For process rows, include `reason <value>` when the latest row event carries it, or `last reason <event_type> <value>` when an older recent event carries the latest available reason.

Run the Step 1 command.
Expected: pass.

- [x] **Step 3: Document CLI reason output**

Document that default CLI process lists and lifecycle event views expose sanitized watchdog reasons without requiring `--json`.

Run: `python3 -m unittest tests.test_docs_architecture.DocsArchitectureTests.test_live_agent_ops_documents_operator_smoke_path`
Expected: pass.

### Task 63: GUI Process Row Lifecycle Reason Display

**Files:**
- Modify: `agentsassemble/static/lobby.js`
- Modify: `docs/live-agent-ops.md`
- Test: `tests/static_lobby_runtime_smoke.mjs`

- [x] **Step 1: Add RED coverage for GUI reason display**

Extend the process-row runtime smoke so a bounded `recent_events` list includes an older `stale_watchdog` event with a sanitized `reason`, followed by a later offline-bearing `restart_scheduled` event and latest `started` event. The rendered row must keep the latest-event anchor while surfacing the older watchdog reason as `last reason <event_type> <reason>`.

Run:

```bash
node --test tests/static_lobby_runtime_smoke.mjs --test-name-pattern "process row renders recovery watchdog and next restart evidence"
```

Expected: fail before implementation because GUI process rows render offline lifecycle evidence but not lifecycle `reason`.

- [x] **Step 2: Render reason in GUI lifecycle summaries**

Teach `liveAgentProcessEventLabel()` to include the latest available reason-bearing event from the row's bounded `recent_events`. Use `reason <value>` when the latest event carries it, or `last reason <event_type> <value>` when an older recent event carries the latest available reason.

Run the Step 1 command.
Expected: pass.

- [x] **Step 3: Document GUI reason output**

Document that both CLI process lists and GUI process rows expose the latest sanitized watchdog reason from bounded lifecycle history without opening raw JSON.

Run: `python3 -m unittest tests.test_docs_architecture.DocsArchitectureTests.test_live_agent_ops_documents_operator_smoke_path`
Expected: pass.

### Task 64: Health And Doctor Lifecycle Reason Summary

**Files:**
- Modify: `agentsassemble/gui.py`
- Modify: `agentsassemble/cli.py`
- Modify: `docs/live-agent-ops.md`
- Test: `tests/test_gui_server.py`
- Test: `tests/test_cli_timeout.py`

- [x] **Step 1: Add RED coverage for health reason evidence**

Extend live-agent health endpoint coverage so a non-diagnostic restarting process group with bounded `recent_events` exposes a safe `processes.reasons` entry for the latest sanitized stale-watchdog reason, while a suspicious non-watchdog reason value is not leaked. Extend CLI health and doctor text-output coverage so compact operator summaries include a `process reasons:` line when that map is present.

Run:

```bash
python3 -m unittest \
  tests.test_gui_server.GuiServerTests.test_live_agent_health_endpoint_summarizes_agents_and_processes \
  tests.test_cli_timeout.CliTimeoutTests.test_live_agent_health_prints_summary \
  tests.test_cli_timeout.CliTimeoutTests.test_live_agent_doctor_posts_readiness_request_and_prints_summary
```

Expected: fail before implementation because health only reports process attention ids and the CLI summaries do not render process reasons.

- [x] **Step 2: Add sanitized health reason summaries**

Add a `processes.reasons` map keyed by safe process group id. Include only stale-watchdog event types and only the same bounded reason grammar used by lifecycle sanitization, so health/readiness can explain degraded process attention without echoing command paths, env markers, config paths, provider output, or arbitrary fake supervisor text.

Run the Step 1 command plus `python3 -m compileall -q agentsassemble`.
Expected: pass.

- [x] **Step 3: Document health and doctor reason output**

Document that `/api/live-agent-health`, `live-agent health`, and `live-agent doctor` expose safe process watchdog reasons in compact summaries without requiring raw lifecycle JSON.

Run: `python3 -m unittest tests.test_docs_architecture.DocsArchitectureTests.test_live_agent_ops_documents_operator_smoke_path`
Expected: pass.

### Task 65: Doctor Connection Attention Summary

**Files:**
- Modify: `agentsassemble/cli.py`
- Modify: `docs/live-agent-ops.md`
- Test: `tests/test_cli_timeout.py`

- [x] **Step 1: Add RED coverage for doctor connection attention**

Extend the existing doctor text-output test so the embedded readiness health payload includes `connections.attention` for a manifest agent that is not connected. The compact doctor output must include a `connection attention:` line, matching the existing `live-agent health` summary surface.

Run: `python3 -m unittest tests.test_cli_timeout.CliTimeoutTests.test_live_agent_doctor_posts_readiness_request_and_prints_summary`
Expected: fail before implementation because doctor renders agent and process attention but not connection attention.

- [x] **Step 2: Render connection attention in doctor**

Read `health.connections.attention` in `_format_live_agent_readiness()` and always render the same `_attention_summary()` line used for agent and process attention, so healthy readiness payloads print `connection attention: none` and degraded payloads name the missing connection evidence.

Run the Step 1 command.
Expected: pass.

- [x] **Step 3: Document doctor attention parity**

Document that `live-agent doctor` mirrors the important health attention surfaces: agent attention, process attention, connection attention, and sanitized process watchdog reasons.

Run: `python3 -m unittest tests.test_docs_architecture.DocsArchitectureTests.test_live_agent_ops_documents_operator_smoke_path`
Expected: pass.

### Task 66: Stop Session Duplicate Manifest Guard

**Files:**
- Modify: `agentsassemble/live_agent_sessions.py`
- Modify: `docs/live-agent-ops.md`
- Test: `tests/test_live_agent_sessions.py`
- Test: `tests/test_docs_architecture.py`

- [x] **Step 1: Add RED coverage for duplicate stop manifest**

Cover `stop-session` with a process group whose launch-time manifest repeats an expected meeting agent id. It must refuse before calling `stop_group` and before marking any bound roster row offline, because session controls promise exact meeting/process manifest ownership before mutating processes or presence.

Run: `python3 -m unittest tests.test_live_agent_sessions.LiveAgentSessionStartTests.test_stop_session_refuses_duplicate_manifest_agent_before_stop_and_offline`
Expected: fail before implementation because the stop prevalidation compares sets and lets duplicate manifest ids through.

- [x] **Step 2: Reject duplicate stop manifests**

Add the same duplicate manifest guard used by restart/recover validation to the stop-session prevalidation path. Keep the fix local to the manifest validator so stop still refuses before process and roster side effects.

Run the Step 1 command plus nearby stop-session safety tests.
Expected: pass.

- [x] **Step 3: Document duplicate manifest refusal**

Document that `stop-session` exact manifest validation includes duplicate manifest agent ids, not only missing or extra ids.

Run: `python3 -m unittest tests.test_docs_architecture.DocsArchitectureTests.test_live_agent_ops_documents_operator_smoke_path`
Expected: pass.

### Task 67: Restart Session Persisted Config Prevalidation

**Files:**
- Modify: `agentsassemble/live_agent_sessions.py`
- Modify: `docs/live-agent-ops.md`
- Test: `tests/test_live_agent_sessions.py`
- Test: `tests/test_gui_server.py`
- Test: `tests/test_docs_architecture.py`

- [x] **Step 1: Add RED coverage for drifted persisted restart config**

Cover `restart-session` with a currently valid running process snapshot whose persisted `config_path` now contains duplicate agent ids, with a snapshot that names a blank config or blank server, and with a supervisor-specific preflight checker that refuses the restart config. The restart must refuse before calling `stop_group`, before calling `restart_group`, and before marking bound roster rows offline, because a changed persisted config or missing launch evidence would otherwise stop a good group before discovering the new launch record is invalid.

Run: `python3 -m unittest tests.test_live_agent_sessions.LiveAgentSessionStartTests.test_restart_session_refuses_changed_persisted_config_before_stopping_group`
Expected: fail before implementation because restart only validates the current process snapshot before stopping and lets the supervisor discover the drifted config later.

- [x] **Step 2: Prevalidate persisted config before restart side effects**

When the process snapshot includes a `config_path`, require a nonblank persisted config and server, then run the same live-agent preflight checker configured on the supervisor, falling back to the default checker, and resident manifest checks against that persisted config before stopping `running`/`restarting` groups or clearing roster rows. Keep the post-restart process snapshot validation as the race guard for any remaining drift.

Run the Step 1 command plus nearby restart safety tests.
Expected: pass.

- [x] **Step 3: Document restart config prevalidation**

Document that `restart-session` preflights the persisted restart config and server when the process record names one, so manifest drift, duplicate config agents, or missing launch evidence is refused before process or roster side effects.

Run: `python3 -m unittest tests.test_docs_architecture.DocsArchitectureTests.test_live_agent_ops_documents_operator_smoke_path`
Expected: pass.

### Task 68: Recover Session Persisted Config Prevalidation

**Files:**
- Modify: `agentsassemble/live_agent_sessions.py`
- Modify: `docs/live-agent-ops.md`
- Test: `tests/test_live_agent_sessions.py`
- Test: `tests/test_gui_server.py`
- Test: `tests/test_docs_architecture.py`

- [x] **Step 1: Add RED coverage for drifted persisted recover config**

Cover `recover-session` with a recoverable process snapshot whose current launch-time manifest still matches the meeting, but whose persisted `config_path` now contains duplicate agent ids. Add coverage for a supervisor-specific preflight checker that refuses recovery and for a custom preflight checker that passes before resident manifest validation catches duplicate config ids. Recovery must refuse before calling `recover_group` and before marking bound roster rows offline.

Run:
`python3 -m unittest tests.test_live_agent_sessions.LiveAgentSessionStartTests.test_recover_session_refuses_changed_persisted_config_before_clearing_stale_roster tests.test_live_agent_sessions.LiveAgentSessionStartTests.test_recover_session_uses_supervisor_preflight_checker_before_clearing_stale_roster tests.test_live_agent_sessions.LiveAgentSessionStartTests.test_recover_session_rejects_duplicate_persisted_config_after_custom_preflight_ok`
Expected: fail before implementation because recover only validates the current process snapshot before clearing roster rows and lets the supervisor discover persisted config drift later.

- [x] **Step 2: Prevalidate persisted config before recover side effects**

Reuse the persisted group config validator for both restart and recover. When the recoverable process snapshot includes a `config_path`, require a nonblank persisted config and server, run the same live-agent preflight checker configured on the supervisor, and validate the resident config manifest and meeting ids before clearing roster rows or calling `recover_group`.

Run the Step 1 command plus nearby recover safety tests.
Expected: pass.

- [x] **Step 3: Document recover config prevalidation**

Document that `recover-session` preflights the persisted recover config and server when the process record names one, so manifest drift, duplicate config agents, missing launch evidence, or supervisor-specific preflight refusals are refused before process or roster side effects.

Run: `python3 -m unittest tests.test_docs_architecture.DocsArchitectureTests.test_live_agent_ops_documents_operator_smoke_path`
Expected: pass.

### Task 69: Session Smoke Post-Restart Operator Evidence

**Files:**
- Modify: `agentsassemble/cli.py`
- Modify: `agentsassemble/static/lobby.js`
- Modify: `docs/live-agent-ops.md`
- Test: `tests/test_cli_timeout.py`
- Test: `tests/static_lobby_runtime_smoke.mjs`
- Test: `tests/test_docs_architecture.py`

- [x] **Step 1: Add RED coverage for missing post-restart evidence in summaries**

Cover `live-agent doctor --session-smoke`, GUI readiness status text, and recent operation detail ordering so the compact operator surface shows post-restart reply counts alongside post-recover counts. The same session smoke already verifies post-restart replies internally; the failure is that doctor/readiness/operation summaries do not keep that proof visible.

Run:
`python3 -m unittest tests.test_cli_timeout.CliTimeoutTests.test_live_agent_operations_list_prioritizes_session_smoke_soak_evidence tests.test_cli_timeout.CliTimeoutTests.test_live_agent_doctor_can_request_session_smoke tests.test_cli_timeout.CliTimeoutTests.test_live_agent_doctor_can_request_official_round_and_session_smoke`
and `node --test tests/static_lobby_runtime_smoke.mjs`
Expected: fail before implementation because post-restart evidence is omitted from doctor/readiness summaries and can be truncated from compact operation details.

- [x] **Step 2: Surface post-restart evidence before post-recover evidence**

Add post-restart counts to the CLI and GUI session smoke summary labels. Prioritize `post_restart_reply_count` and `session_smoke_post_restart_reply_count` before their post-recover counterparts in operation detail ordering, and keep the compact detail cap wide enough to retain soak check statuses.

Run the Step 1 commands.
Expected: pass.

- [x] **Step 3: Document the operator surface**

Document that readiness and operation summaries expose post-restart and post-recover reply counts plus soak statuses while still omitting event ids, replies, config paths, commands, endpoints, auth refs, provider output, tokens, and log tails.

Run: `python3 -m unittest tests.test_docs_architecture.DocsArchitectureTests.test_live_agent_ops_documents_operator_smoke_path`
Expected: pass.

---

### Task 70: Scriptable Process Lifecycle Event Wait

**Files:**
- Modify: `agentsassemble/cli.py`
- Modify: `docs/live-agent-ops.md`
- Modify: `docs/superpowers/plans/2026-05-17-live-agent-final-form.md`
- Test: `tests/test_cli_timeout.py`
- Test: `tests/test_docs_architecture.py`

- [x] **Step 1: Add RED coverage for lifecycle event waiting**

Cover `live-agent processes wait-event` parser options, bounded polling of `/api/live-agent-process-events`, matching by `event_type`, optional group id, optional status, and `--after-timestamp`, plus timeout output with the last observed lifecycle event.

Run:

```bash
python3 -m unittest \
  tests.test_cli_timeout.CliTimeoutTests.test_live_agent_processes_wait_event_parses_filters_and_wait_options \
  tests.test_cli_timeout.CliTimeoutTests.test_live_agent_processes_wait_event_observes_matching_event_after_timestamp \
  tests.test_cli_timeout.CliTimeoutTests.test_live_agent_processes_wait_event_times_out_with_last_event
```

Expected: fail before implementation because the `wait-event` process subcommand does not exist.

- [x] **Step 2: Implement the CLI wait loop on the existing lifecycle API**

Reuse the sanitized lifecycle event history endpoint instead of adding a second backend path. Poll with the remaining deadline as the HTTP timeout, include `limit`, optional `scan_limit`, and optional `group_id` in the query, ignore events at or before `--after-timestamp`, and return exit `0` for observed and `1` for timeout.

Run the Step 1 command.
Expected: pass.

- [x] **Step 3: Document lifecycle event wait semantics**

Document `processes wait-event` as the scriptable gate for crash, stale-watchdog, restart, stop, and recovery lifecycle evidence. Keep it distinct from `processes wait` readiness and `operations wait` control-operation history.

Run: `python3 -m unittest tests.test_docs_architecture.DocsArchitectureTests.test_live_agent_ops_documents_operator_smoke_path`
Expected: pass.

---

### Task 71: Targeted Read-Only Session Readiness

**Files:**
- Modify: `agentsassemble/gui.py`
- Modify: `agentsassemble/cli.py`
- Modify: `docs/live-agent-ops.md`
- Modify: `docs/superpowers/plans/2026-05-17-live-agent-final-form.md`
- Test: `tests/test_gui_server.py`
- Test: `tests/test_cli_timeout.py`
- Test: `tests/test_docs_architecture.py`

- [x] **Step 1: Add RED coverage for read-only targeted readiness**

Cover `GET /api/live-agent-sessions/readiness?meeting_id=...&group_id=...`, `live-agent session-readiness`, ready and degraded target snapshots, `--fail-on-degraded`, and the invariant that the read-only path appends no `session.check` or other operation history record.

Run:

```bash
python3 -m unittest \
  tests.test_cli_timeout.CliTimeoutTests.test_live_agent_session_readiness_parser_accepts_meeting_group_and_fail_flag \
  tests.test_cli_timeout.CliTimeoutTests.test_live_agent_session_readiness_gets_read_only_endpoint_and_prints_summary \
  tests.test_cli_timeout.CliTimeoutTests.test_live_agent_session_readiness_fail_on_degraded_returns_failure \
  tests.test_gui_server.GuiServerTests.test_live_agent_session_readiness_endpoint_returns_ready_snapshot_without_operation_record \
  tests.test_gui_server.GuiServerTests.test_live_agent_session_readiness_endpoint_returns_degraded_missing_group_without_operation_record
```

Expected: fail before implementation because the CLI subcommand and GET endpoint do not exist.

- [x] **Step 2: Reuse the session check snapshot without operation recording**

Add a read-only payload helper and HTTP GET route that call the existing `check_live_agent_session()` readiness computation without the POST `session.check` operation wrapper. Add a CLI command that fetches the GET route, prints the existing session summary, and exits `1` only when `--fail-on-degraded` is set and the target is not ready.

Run the Step 1 command.
Expected: pass.

- [x] **Step 3: Document the distinction from `check-session`**

Document that `check-session` is an explicit operator check that records `session.check`, while `session-readiness` and its GET endpoint are read-only automation surfaces that do not mutate process state, roster state, providers, official turns, probes, or operation history.

Run: `python3 -m unittest tests.test_docs_architecture.DocsArchitectureTests.test_live_agent_ops_documents_operator_smoke_path`
Expected: pass.

---

### Task 72: Session Command Readiness Wait

**Files:**
- Modify: `agentsassemble/cli.py`
- Modify: `docs/live-agent-ops.md`
- Modify: `docs/superpowers/plans/2026-05-17-live-agent-final-form.md`
- Test: `tests/test_cli_timeout.py`
- Test: `tests/test_docs_architecture.py`

- [x] **Step 1: Add RED coverage for command-local readiness wait**

Cover `start-session --wait-ready`, bounded `--wait-timeout`, `--wait-poll-interval`, generated meeting/group ids returned by `start-session`, the invariant that wait polling uses only `GET /api/live-agent-sessions/readiness`, ready short-circuit behavior, timeout output with the last readiness summary, and the same wait path after `restart-session`.

Run:

```bash
python3 -m unittest \
  tests.test_cli_timeout.CliTimeoutTests.test_live_agent_start_session_parser_accepts_wait_ready_options \
  tests.test_cli_timeout.CliTimeoutTests.test_live_agent_start_session_wait_ready_polls_read_only_session_readiness \
  tests.test_cli_timeout.CliTimeoutTests.test_live_agent_start_session_wait_ready_times_out_with_last_summary \
  tests.test_cli_timeout.CliTimeoutTests.test_live_agent_start_session_wait_ready_skips_poll_when_initial_response_is_ready \
  tests.test_cli_timeout.CliTimeoutTests.test_live_agent_restart_session_wait_ready_uses_read_only_readiness_after_restart
```

Expected: fail before implementation because the session commands do not accept wait flags.

- [x] **Step 2: Implement shared read-only wait polling for session commands**

Add common parser options to `start-session`, `resume-session`, `restart-session`, and `recover-session`. After the existing POST returns a non-ready session response, poll the targeted read-only readiness endpoint with the remaining deadline as the per-request timeout. Print only the final observed session summary, return `0` once ready, and return `1` on timeout without appending repeated `session.check` operations.

Run the Step 1 command.
Expected: pass.

- [x] **Step 3: Document wait semantics**

Document that `--wait-ready` is a CLI-side automation gate over `session-readiness`, distinct from `check-session`, providers, probes, official turns, smoke checks, decisions, and transcript finalization.

Run: `python3 -m unittest tests.test_docs_architecture.DocsArchitectureTests.test_live_agent_ops_documents_operator_smoke_path`
Expected: pass.

---

### Task 73: Scriptable Session Ensure

**Files:**
- Modify: `agentsassemble/cli.py`
- Modify: `docs/live-agent-ops.md`
- Modify: `docs/superpowers/plans/2026-05-17-live-agent-final-form.md`
- Test: `tests/test_cli_timeout.py`
- Test: `tests/test_docs_architecture.py`

- [x] **Step 1: Add RED coverage for one-shot session ensuring**

Cover `live-agent ensure-session` parser options, the no-op ready path that only reads the read-only readiness endpoint, missing-meeting start, existing-meeting missing-group resume, running degraded resume plus readiness wait, stopped restart, error recover, final read-only readiness as the success source even when the mutating command reports `ready`, and timeout of that final readiness proof.

Run:

```bash
python3 -m unittest \
  tests.test_cli_timeout.CliTimeoutTests.test_live_agent_ensure_session_parser_accepts_session_configs_and_wait_options \
  tests.test_cli_timeout.CliTimeoutTests.test_live_agent_ensure_session_returns_ready_without_control_post \
  tests.test_cli_timeout.CliTimeoutTests.test_live_agent_ensure_session_starts_when_meeting_is_missing \
  tests.test_cli_timeout.CliTimeoutTests.test_live_agent_ensure_session_resumes_when_group_is_missing_for_existing_meeting \
  tests.test_cli_timeout.CliTimeoutTests.test_live_agent_ensure_session_resumes_running_degraded_session_and_waits_ready \
  tests.test_cli_timeout.CliTimeoutTests.test_live_agent_ensure_session_recovers_error_session \
  tests.test_cli_timeout.CliTimeoutTests.test_live_agent_ensure_session_restarts_stopped_session \
  tests.test_cli_timeout.CliTimeoutTests.test_live_agent_ensure_session_uses_final_readiness_even_when_resume_returns_ready \
  tests.test_cli_timeout.CliTimeoutTests.test_live_agent_ensure_session_fails_when_final_readiness_times_out_after_ready_post
```

Expected: fail before implementation because the `ensure-session` command does not exist.

- [x] **Step 2: Implement CLI-only orchestration over existing session APIs**

Use `session-readiness` as the initial read-only probe. Return immediately for `ready`; call `start-session` when no target exists or the meeting is missing; call `resume-session` when the existing meeting's process group is absent or the target is running/restarting but degraded; call `restart-session` for stopped sessions; call `recover-session` for existing unknown/error sessions. After any mutating call, reuse the read-only readiness wait loop instead of appending repeated `session.check` records, so final success is based on targeted readiness.

Run the Step 1 command.
Expected: pass.

- [x] **Step 3: Document the ensure policy**

Document that `ensure-session` is a scriptable CLI one-shot over existing APIs, not a new server endpoint, and that its repeated readiness waits remain read-only.

Run: `python3 -m unittest tests.test_docs_architecture.DocsArchitectureTests.test_live_agent_ops_documents_operator_smoke_path`
Expected: pass.

---

### Task 74: GUI/API Session Ensure Control

**Files:**
- Modify: `agentsassemble/gui.py`
- Modify: `agentsassemble/static/lobby.js`
- Modify: `agentsassemble/cli.py`
- Modify: `docs/live-agent-ops.md`
- Modify: `docs/superpowers/plans/2026-05-17-live-agent-final-form.md`
- Test: `tests/test_gui_server.py`
- Test: `tests/test_static_ui_assets.py`
- Test: `tests/static_lobby_runtime_smoke.mjs`
- Test: `tests/test_docs_architecture.py`

- [x] **Step 1: Add RED coverage for GUI/API ensuring**

Cover `POST /api/live-agent-sessions/ensure`, the ready no-op path, existing-meeting missing-group resume, the single `session.ensure` operation record, safe `ensure_action` details, and the GUI `세션보장` button sending the same resident session payload as start.

Run:

```bash
python3 -m unittest \
  tests.test_gui_server.GuiServerTests.test_live_agent_session_ensure_returns_ready_without_mutating_ready_session \
  tests.test_gui_server.GuiServerTests.test_live_agent_session_ensure_resumes_existing_meeting_when_group_is_missing \
  tests.test_static_ui_assets.StaticUiAssetTests
node --test tests/static_lobby_runtime_smoke.mjs --test-name-pattern "session ensure"
```

Expected: fail before implementation because `/api/live-agent-sessions/ensure` and the GUI button do not exist.

- [x] **Step 2: Implement server-side ensure orchestration**

Reuse the read-only targeted readiness snapshot first. Return ready snapshots without process mutation, choose start/resume/restart/recover for degraded targets, then return a final read-only readiness snapshot carrying the chosen `action`. Record exactly one sanitized `session.ensure` operation for the public API call and do not append child `session.check`, `session.resume`, or other nested operation records.

Run the Step 1 command.
Expected: pass.

- [x] **Step 3: Add the GUI control and compact operation evidence**

Add `세션보장` beside `세션시작`, post to `/api/live-agent-sessions/ensure` with council, agent, resident group, watchdog, probe, and remaining-round options, and show `session.ensure` compact details in CLI/GUI operation rows with `ensure_action` prioritized.

Run:

```bash
node --test tests/static_lobby_runtime_smoke.mjs --test-name-pattern "session ensure|operation row"
python3 -m unittest tests.test_cli_timeout.CliTimeoutTests.test_live_agent_operations_list_prioritizes_session_control_probe_and_auto_rounds
```

Expected: pass.

- [x] **Step 4: Document the operator surface**

Document that `ensure-session`, `세션보장`, and `POST /api/live-agent-sessions/ensure` expose the same one-shot policy, while internal readiness reads remain read-only and do not append `session.check` records.

Run: `python3 -m unittest tests.test_docs_architecture.DocsArchitectureTests.test_live_agent_ops_documents_operator_smoke_path`
Expected: pass.

---

### Task 75: Shared Session Ensure Policy

**Files:**
- Modify: `agentsassemble/live_agent_sessions.py`
- Modify: `agentsassemble/cli.py`
- Modify: `agentsassemble/gui.py`
- Modify: `docs/live-agent-ops.md`
- Modify: `docs/superpowers/plans/2026-05-17-live-agent-final-form.md`
- Test: `tests/test_live_agent_sessions.py`
- Test: `tests/test_cli_timeout.py`
- Test: `tests/test_gui_server.py`
- Test: `tests/test_docs_architecture.py`

- [x] **Step 1: Add RED coverage for the shared action policy**

Cover the single action-selection table for no target, ready, missing group, running degraded, restarting degraded, stopped, error, unknown, and missing process status snapshots.

Run:

```bash
python3 -m unittest tests.test_live_agent_sessions.LiveAgentSessionEnsureActionTests.test_session_ensure_action_uses_one_policy_for_cli_and_api_surfaces
```

Expected: fail before implementation because `session_ensure_action` is not exported from `agentsassemble.live_agent_sessions`.

- [x] **Step 2: Move CLI/API ensure action selection to the shared helper**

Add `session_ensure_action(readiness)` to `agentsassemble/live_agent_sessions.py`, import it from both `agentsassemble/cli.py` and `agentsassemble/gui.py`, remove the duplicate local action-selection helpers, and preserve the public ready no-op, start, resume, restart, and recover behavior.

Run:

```bash
python3 -m unittest \
  tests.test_live_agent_sessions.LiveAgentSessionEnsureActionTests.test_session_ensure_action_uses_one_policy_for_cli_and_api_surfaces \
  tests.test_cli_timeout.CliTimeoutTests.test_live_agent_ensure_session_returns_ready_without_control_post \
  tests.test_cli_timeout.CliTimeoutTests.test_live_agent_ensure_session_resumes_when_group_is_missing_for_existing_meeting \
  tests.test_cli_timeout.CliTimeoutTests.test_live_agent_ensure_session_resumes_running_degraded_session_and_waits_ready \
  tests.test_cli_timeout.CliTimeoutTests.test_live_agent_ensure_session_recovers_error_session \
  tests.test_cli_timeout.CliTimeoutTests.test_live_agent_ensure_session_restarts_stopped_session \
  tests.test_gui_server.GuiServerTests.test_live_agent_session_ensure_returns_ready_without_mutating_ready_session \
  tests.test_gui_server.GuiServerTests.test_live_agent_session_ensure_resumes_existing_meeting_when_group_is_missing \
  tests.test_gui_server.GuiServerTests.test_live_agent_session_ensure_selects_start_restart_and_recover_actions
```

Expected: pass.

- [x] **Step 3: Document the shared policy boundary**

Document that `ensure-session`, `세션보장`, and `/api/live-agent-sessions/ensure` use the same `session_ensure_action` helper, so the action chosen for a readiness snapshot cannot drift between CLI and API surfaces.

Run: `python3 -m unittest tests.test_docs_architecture.DocsArchitectureTests.test_live_agent_ops_documents_operator_smoke_path`
Expected: pass.

---

### Task 76: Ensure Ready No-op Post-ready Checks

**Files:**
- Modify: `agentsassemble/gui.py`
- Modify: `docs/live-agent-ops.md`
- Modify: `docs/superpowers/plans/2026-05-17-live-agent-final-form.md`
- Test: `tests/test_gui_server.py`
- Test: `tests/test_docs_architecture.py`

- [x] **Step 1: Add RED coverage for ready no-op checks**

Cover `POST /api/live-agent-sessions/ensure` when the target is already `ready`, the selected action is `none`, and the payload requests `probe_bound_agents` plus `run_remaining_rounds`. The endpoint must not call `start_group`, but it must run the bound-agent probe, run remaining rounds, include `reply_probe` and `auto_rounds` in the response, and record those sanitized fields on the single `session.ensure` operation.

Run:

```bash
python3 -m unittest tests.test_gui_server.GuiServerTests.test_live_agent_session_ensure_ready_noop_can_probe_and_run_remaining_rounds
```

Expected: fail before implementation because the ready no-op path returns the readiness snapshot without post-ready checks.

- [x] **Step 2: Reuse session post-processing for action `none`**

When `session_ensure_action()` returns `none`, pass the ready readiness snapshot through `_attach_session_auto_rounds_if_requested()` before the final read-only readiness copy, so requested probes and remaining rounds behave like the other session entrypoints while preserving the no process-mutation guarantee.

Run:

```bash
python3 -m unittest \
  tests.test_gui_server.GuiServerTests.test_live_agent_session_ensure_ready_noop_can_probe_and_run_remaining_rounds \
  tests.test_gui_server.GuiServerTests.test_live_agent_session_ensure_returns_ready_without_mutating_ready_session
```

Expected: pass.

- [x] **Step 3: Document the no-op post-ready behavior**

Document that API/GUI `세션보장` still runs requested post-ready checks when the chosen action is `none`, while internal readiness reads remain operation-history neutral.

Run: `python3 -m unittest tests.test_docs_architecture.DocsArchitectureTests.test_live_agent_ops_documents_operator_smoke_path`
Expected: pass.

---

### Task 77: CLI Ensure Post-ready Checks

**Files:**
- Modify: `agentsassemble/cli.py`
- Modify: `docs/live-agent-ops.md`
- Modify: `docs/superpowers/plans/2026-05-17-live-agent-final-form.md`
- Test: `tests/test_cli_timeout.py`
- Test: `tests/test_docs_architecture.py`

- [x] **Step 1: Add RED coverage for CLI ensure post-ready options**

Cover `ensure-session` accepting `--probe-bound-agents`, `--probe-timeout`, `--run-remaining-rounds`, `--round-timeout`, `--max-rounds`, and `--stop-on-timeout`. Cover the ready no-op path posting to `/api/live-agent-sessions/ensure` when post-ready checks are requested, cover a mutating ensure path preserving `reply_probe` and `auto_rounds` after the final read-only readiness wait, and cover restart/recover action payloads carrying the same post-ready options.

Run:

```bash
python3 -m unittest \
  tests.test_cli_timeout.CliTimeoutTests.test_live_agent_ensure_session_parser_accepts_session_configs_and_wait_options \
  tests.test_cli_timeout.CliTimeoutTests.test_live_agent_ensure_session_ready_noop_can_probe_and_run_remaining_rounds \
  tests.test_cli_timeout.CliTimeoutTests.test_live_agent_ensure_session_preserves_probe_and_round_results_after_readiness_wait \
  tests.test_cli_timeout.CliTimeoutTests.test_live_agent_ensure_session_restart_and_recover_carry_post_ready_options
```

Expected: fail before implementation because `ensure-session` does not accept the post-ready options and ready no-op returns the read-only snapshot without a control-plane POST.

- [x] **Step 2: Implement CLI ensure post-ready routing**

Add the same optional probe and remaining-round flags to `ensure-session`. Preserve the existing no-POST ready fast path when no post-ready check is requested. When a ready no-op does request post-ready checks, post to `/api/live-agent-sessions/ensure` so the server runs the same bounded checks as GUI `세션보장` without process mutation. For mutating actions, pass the same optional probe and remaining-round payload fields to the selected session control endpoint and reattach returned `reply_probe` and `auto_rounds` evidence after the final read-only readiness wait.

Run the Step 1 command.
Expected: pass.

- [x] **Step 3: Document the CLI/API parity**

Document that `ensure-session` keeps the read-only ready fast path by default, but uses `/api/live-agent-sessions/ensure` for ready no-op post-ready checks and preserves probe/round evidence after mutating action waits.

Run: `python3 -m unittest tests.test_docs_architecture.DocsArchitectureTests.test_live_agent_ops_documents_operator_smoke_path`
Expected: pass.

---

### Task 78: Resident Cursor Bounded-tail Recovery

**Files:**
- Modify: `agentsassemble/live_agent_runner.py`
- Modify: `docs/live-agent-ops.md`
- Modify: `docs/superpowers/plans/2026-05-17-live-agent-final-form.md`
- Test: `tests/test_live_agent_runner.py`
- Test: `tests/test_docs_architecture.py`

- [x] **Step 1: Add RED coverage for evicted cursors**

Cover a lobby resident whose persisted `last_observed_event_id` is no longer present in the bounded room tail and a moderator-called resident whose persisted `last_observed_live_event_id` is no longer present in the bounded live-event tail. Both must recover by considering the current bounded tail eligible instead of staying idle forever. Also cover the direct lobby and official-turn candidate helpers, including the already-answered official request guard.

Run:

```bash
python3 -m unittest \
  tests.test_live_agent_runner.LiveAgentRunnerTests.test_runner_recovers_when_lobby_cursor_fell_out_of_bounded_room_tail \
  tests.test_live_agent_runner.LiveAgentRunnerTests.test_moderator_called_recovers_when_live_cursor_fell_out_of_bounded_room_tail \
  tests.test_live_agent_runner.LiveAgentRunnerTests.test_lobby_candidate_uses_bounded_tail_when_cursor_is_absent \
  tests.test_live_agent_runner.LiveAgentRunnerTests.test_official_turn_candidate_uses_bounded_tail_when_cursor_is_absent_but_keeps_answered_guard
```

Expected: fail before implementation because `_events_after()` returns an empty list when the cursor id is absent from the visible tail.

- [x] **Step 2: Recover from bounded-tail cursor eviction without duplicate replies**

Update the shared event cursor helper so a missing cursor id means the current bounded tail is newly visible. Keep duplicate prevention by recording the answered source event id as the runner's local lobby cursor after successful lobby replies, matching the persisted cursor semantics already used by the live-agent lobby endpoint.

Run:

```bash
python3 -m unittest \
  tests.test_live_agent_runner.LiveAgentRunnerTests.test_runner_recovers_when_lobby_cursor_fell_out_of_bounded_room_tail \
  tests.test_live_agent_runner.LiveAgentRunnerTests.test_moderator_called_recovers_when_live_cursor_fell_out_of_bounded_room_tail \
  tests.test_live_agent_runner.LiveAgentRunnerTests.test_runner_does_not_reply_to_the_same_event_twice \
  tests.test_live_agent_runner.LiveAgentRunnerTests.test_runner_restores_observed_cursor_from_registration_before_replying \
  tests.test_live_agent_runner.LiveAgentRunnerTests.test_runner_keeps_local_cursor_when_presence_snapshot_is_stale \
  tests.test_live_agent_runner.LiveAgentRunnerTests.test_moderator_called_skips_visible_already_answered_request_without_model_call
```

Expected: pass.

- [x] **Step 3: Document long-session bounded-tail recovery**

Document that resident polling uses bounded room tails, but evicted cursors do not make residents permanently idle; lobby reply success stores the source event id locally so repeated snapshots still avoid duplicate replies.

Run: `python3 -m unittest tests.test_docs_architecture.DocsArchitectureTests.test_live_agent_ops_documents_operator_smoke_path`
Expected: pass.

---

### Task 79: Diagnostic Session Health Isolation

**Goal:** Keep credential-free diagnostic smoke groups from making a real resident session look duplicated or degraded in the shared meeting-owned readiness summary.

**Files:**
- Modify: `agentsassemble/live_agent_sessions.py`
- Test: `tests/test_live_agent_sessions.py`

- [x] **Step 1: Add RED coverage for diagnostic duplicate isolation**

Cover `live_agent_session_readiness_summary()` with one real running group and one `diagnostic: true` running group for the same meeting. The real group must remain the only readiness item, stay ready, and have no `meeting:duplicate_active_group` ownership attention.

Run:

```bash
python3 -m unittest tests.test_live_agent_sessions.LiveAgentSessionReadinessSummaryTests.test_session_summary_ignores_diagnostic_groups_for_duplicate_ownership
```

Expected: fail before implementation because diagnostic groups are included in the duplicate active owner pass.

- [x] **Step 2: Exclude diagnostic groups from session readiness summaries**

Skip diagnostic process groups before building meeting-owned session readiness items. This matches the existing targeted ownership check and keeps session smoke artifacts from contaminating health and readiness evidence.

Run:

```bash
python3 -m unittest \
  tests.test_live_agent_sessions.LiveAgentSessionReadinessSummaryTests.test_session_summary_ignores_diagnostic_groups_for_duplicate_ownership \
  tests.test_live_agent_sessions.LiveAgentSessionReadinessSummaryTests.test_session_summary_degrades_duplicate_active_meeting_groups \
  tests.test_live_agent_sessions.LiveAgentSessionReadinessSummaryTests.test_check_session_degrades_duplicate_active_meeting_group
```

Expected: pass.

---

### Task 80: Resident Room Snapshot Transient Recovery

**Goal:** Keep long-running resident agents alive when a read-only room snapshot briefly fails after the runner already has usable room state, while preserving fail-fast behavior for startup room failures and mutating reply failures.

**Files:**
- Modify: `agentsassemble/live_agent_runner.py`
- Modify: `docs/live-agent-ops.md`
- Test: `tests/test_live_agent_runner.py`

- [x] **Step 1: Add RED coverage for transient room read recovery**

Cover a bounded resident run where the first `/room` snapshot succeeds with no events, the second `/room` read raises a transient error, and the third snapshot contains a lobby event. The runner must record one `error` heartbeat for the transient read failure, keep polling, and post the later reply. Keep the existing first-room-failure test as the fatal startup boundary.

Run:

```bash
python3 -m unittest \
  tests.test_live_agent_runner.LiveAgentRunnerTests.test_runner_survives_transient_room_failure_after_initial_snapshot \
  tests.test_live_agent_runner.LiveAgentRunnerTests.test_runner_does_not_mask_room_failure_when_final_offline_heartbeat_fails
```

Expected: fail before implementation because every `_room()` exception currently terminates the runner.

- [x] **Step 2: Keep polling after post-start room read failures**

Track whether the runner has successfully read a room snapshot. If a later read-only room snapshot fails, record `last_error`, emit an `error` heartbeat with the current cursors, and return zero replies for that tick so the resident loop can poll again. Keep registration, startup heartbeat, first room snapshot, lobby post, official turn post, and provider command failures on their existing paths.

Run:

```bash
python3 -m unittest \
  tests.test_live_agent_runner.LiveAgentRunnerTests.test_runner_survives_transient_room_failure_after_initial_snapshot \
  tests.test_live_agent_runner.LiveAgentRunnerTests.test_runner_does_not_mask_room_failure_when_final_offline_heartbeat_fails \
  tests.test_live_agent_runner.LiveAgentRunnerTests.test_runner_keeps_error_status_on_periodic_heartbeat_during_failure_backoff
```

Expected: pass.

- [x] **Step 3: Keep polling when the transient error heartbeat also fails**

Cover a post-start `/room` read failure where the attempted `error` heartbeat also raises. The runner must still continue to the next poll and reply to a later healthy room snapshot.

Run:

```bash
python3 -m unittest tests.test_live_agent_runner.LiveAgentRunnerTests.test_runner_survives_transient_room_failure_when_error_heartbeat_fails
```

Expected: fail before implementation because the transient room failure path calls `_heartbeat("error")` directly.

- [x] **Step 4: Document room snapshot recovery boundary**

Document that only post-start read-only room snapshot failures are treated as recoverable, while the first room read still fails fast because no room state has been established.

---

### Task 81: Recovered Room Reads Stay Responsive

**Goal:** Keep provider-command cooldown separate from transient room snapshot read failures, so a recovered room read can answer a new eligible event immediately.

**Files:**
- Modify: `agentsassemble/live_agent_runner.py`
- Modify: `docs/live-agent-ops.md`
- Test: `tests/test_live_agent_runner.py`

- [x] **Step 1: Add RED coverage for no room-failure backoff**

Cover a bounded resident run with `cooldown` set high enough to expose accidental failure backoff. The first room snapshot succeeds, the second room read fails transiently, and the third room snapshot contains a new lobby event. The runner must answer that event immediately instead of treating the read failure like a provider command failure.

Run:

```bash
python3 -m unittest \
  tests.test_live_agent_runner.LiveAgentRunnerTests.test_runner_replies_immediately_after_transient_room_failure_recovery \
  tests.test_live_agent_runner.LiveAgentRunnerTests.test_runner_backs_off_after_command_failure_before_next_reply
```

Expected: fail before implementation because the transient room failure path sets `last_error_at`, which activates command-failure cooldown.

- [x] **Step 2: Separate room read errors from command failure backoff**

Keep `last_error` and the best-effort `error` heartbeat for operator evidence, but do not set `last_error_at` for post-start room snapshot read failures. Provider command failures continue to set `last_error_at` and use the existing cooldown gate.

Run:

```bash
python3 -m unittest \
  tests.test_live_agent_runner.LiveAgentRunnerTests.test_runner_replies_immediately_after_transient_room_failure_recovery \
  tests.test_live_agent_runner.LiveAgentRunnerTests.test_runner_survives_transient_room_failure_after_initial_snapshot \
  tests.test_live_agent_runner.LiveAgentRunnerTests.test_runner_survives_transient_room_failure_when_error_heartbeat_fails \
  tests.test_live_agent_runner.LiveAgentRunnerTests.test_runner_backs_off_after_command_failure_before_next_reply \
  tests.test_live_agent_runner.LiveAgentRunnerTests.test_runner_keeps_error_status_on_periodic_heartbeat_during_failure_backoff
```

Expected: pass.

- [x] **Step 3: Document room read recovery responsiveness**

Document that recovered room reads do not inherit provider-command cooldown, so a new eligible event can still receive an immediate reply.

---

### Task 82: Clear Recovered Room Snapshot Errors

**Goal:** Prevent stale transient room-read errors from lingering in the roster after room snapshots recover, without clearing provider-command failure evidence.

**Files:**
- Modify: `agentsassemble/live_agent_runner.py`
- Modify: `docs/live-agent-ops.md`
- Test: `tests/test_live_agent_runner.py`

- [x] **Step 1: Add RED coverage for recovered room error clearing**

Cover a bounded resident run where a post-start `/room` read fails, then a later `/room` snapshot succeeds with no eligible event. The runner must attempt the error heartbeat for the failed read and then send an `online` heartbeat with empty `last_error` once a healthy room snapshot proves the room surface recovered. Keep provider command failure backoff/error persistence covered separately.

Run:

```bash
python3 -m unittest \
  tests.test_live_agent_runner.LiveAgentRunnerTests.test_runner_clears_transient_room_error_after_room_snapshot_recovers \
  tests.test_live_agent_runner.LiveAgentRunnerTests.test_runner_keeps_error_status_on_periodic_heartbeat_during_failure_backoff
```

Expected: fail before implementation because a recovered room snapshot does not clear the transient `last_error`.

- [x] **Step 2: Track and clear transient room errors**

Track whether the current `last_error` came from a recoverable room snapshot read. On the next successful room snapshot, send a best-effort `online` heartbeat with `last_error: ""` and clear the runner-local transient marker. Do not use this path for provider command failures, which still keep `last_error_at` and cooldown evidence until reply success or final offline.

Run:

```bash
python3 -m unittest \
  tests.test_live_agent_runner.LiveAgentRunnerTests.test_runner_clears_transient_room_error_after_room_snapshot_recovers \
  tests.test_live_agent_runner.LiveAgentRunnerTests.test_runner_keeps_error_status_on_periodic_heartbeat_during_failure_backoff \
  tests.test_live_agent_runner.LiveAgentRunnerTests.test_runner_replies_immediately_after_transient_room_failure_recovery \
  tests.test_live_agent_runner.LiveAgentRunnerTests.test_runner_survives_transient_room_failure_when_error_heartbeat_fails
```

Expected: pass.

- [x] **Step 3: Preserve provider errors if transient clear fails first**

Cover the review-found edge case where a transient room error clear heartbeat fails, leaving the transient marker active, and the next eligible provider command fails in the same recovered tick. The later healthy room snapshot must not clear provider failure evidence.

Run:

```bash
python3 -m unittest tests.test_live_agent_runner.LiveAgentRunnerTests.test_runner_does_not_clear_provider_error_after_room_clear_heartbeat_failed
```

Expected: fail before implementation because the stale transient marker can clear a later provider `last_error`.

- [x] **Step 4: Document transient room error clearing**

Document that healthy recovered room snapshots clear only transient room errors from presence, not provider-command failure evidence.

---

### Task 83: Resume Starts Pending Restarting Session Groups

**Goal:** Preserve the operator policy that `resume-session` can immediately start a supervised group whose current record is `restarting`, because that state represents a pending supervisor restart/backoff record rather than proof of a live process.

**Files:**
- Modify: `agentsassemble/live_agent_sessions.py`
- Modify: `docs/live-agent-ops.md`
- Test: `tests/test_live_agent_sessions.py`

- [x] **Step 1: Add coverage for restarting resume semantics**

Cover an existing meeting whose matching process group is `restarting`. `resume_live_agent_session()` should validate the supplied config against that meeting and call `start_group()` so the operator can bring a pending restart/backoff record back immediately.

Run:

```bash
python3 -m unittest tests.test_live_agent_sessions.LiveAgentSessionStartTests.test_resume_session_starts_restarting_group_from_validated_config
```

Expected: pass with the existing resume policy; this task records the behavior after a review of a failed alternative that treated `restarting` like `running`.

- [x] **Step 2: Keep running-only reuse**

Keep `_resume_process_group()` reusing only `running` groups. Missing, stopped, restarting, unknown, and error records start from the supplied, just-validated config.

Run:

```bash
python3 -m unittest \
  tests.test_live_agent_sessions.LiveAgentSessionStartTests.test_resume_session_starts_restarting_group_from_validated_config \
  tests.test_live_agent_sessions.LiveAgentSessionStartTests.test_resume_session_reuses_running_group_for_existing_meeting_without_recreating_it \
  tests.test_live_agent_sessions.LiveAgentSessionEnsureActionTests.test_session_ensure_action_uses_one_policy_for_cli_and_api_surfaces
```

Expected: pass.

- [x] **Step 3: Document restart/backoff resume semantics**

Document that `resume-session` treats `restarting` as a pending restart/backoff record and may start it immediately through the validated config.

---

### Task 84: Wait-Ready Uses Final Read-Only Session Readiness

**Goal:** Make CLI `--wait-ready` a real final readiness gate for session start/resume/restart/recover commands, even when the mutating command initially returns `ready`.

**Files:**
- Modify: `agentsassemble/cli.py`
- Modify: `docs/live-agent-ops.md`
- Test: `tests/test_cli_timeout.py`

- [x] **Step 1: Add RED coverage for initial-ready final readiness checks**

Cover `live-agent start-session --wait-ready` where the start POST returns `ready` with sanitized auto-round evidence, but the read-only session readiness endpoint reports a degraded duplicate-owner snapshot. The CLI must poll the read-only endpoint, return exit code `1`, preserve the auto-round evidence, and print the ownership attention reason.

Run:

```bash
python3 -m unittest tests.test_cli_timeout.CliTimeoutTests.test_live_agent_start_session_wait_ready_checks_final_readiness_even_when_initial_response_is_ready
```

Expected: fail before implementation because `_maybe_wait_for_live_agent_session_ready()` skips the read-only wait when the initial response is already `ready`.

- [x] **Step 2: Always honor requested wait-ready**

Change the shared wait helper so `--wait-ready` polls `GET /api/live-agent-sessions/readiness` whenever meeting and group ids are available, regardless of the initial status. Preserve sanitized `reply_probe` and `auto_rounds` evidence from the mutating response on the final snapshot.

Run:

```bash
python3 -m unittest \
  tests.test_cli_timeout.CliTimeoutTests.test_live_agent_start_session_wait_ready_checks_final_readiness_even_when_initial_response_is_ready \
  tests.test_cli_timeout.CliTimeoutTests.test_live_agent_start_session_wait_ready_polls_read_only_session_readiness \
  tests.test_cli_timeout.CliTimeoutTests.test_live_agent_restart_session_wait_ready_uses_read_only_readiness_after_restart \
  tests.test_cli_timeout.CliTimeoutTests.test_live_agent_ensure_session_uses_final_readiness_even_when_resume_returns_ready
```

Expected: pass.

- [x] **Step 3: Surface final attention reasons in CLI summary**

Include connection, process, and ownership attention in the compact session summary so a final degraded readiness result explains why it failed.

- [x] **Step 4: Preserve failure semantics on readiness timeout**

Cover and fix the review-found edge case where the initial mutating response is `ready`, the requested read-only readiness poll times out, and the CLI could otherwise return exit code `0` using the unverified initial response. The wait fallback now downgrades an initial `ready` response to `starting` until a read-only readiness snapshot proves readiness.

Run:

```bash
python3 -m unittest tests.test_cli_timeout.CliTimeoutTests.test_live_agent_start_session_wait_ready_times_out_after_initial_ready_without_unverified_success
```

Expected: fail before the fix, pass after the wait fallback is downgraded.

---

### Task 85: Check-Session Prints Ownership Attention

**Goal:** Make compact `check-session` and `session-readiness` output explain duplicate active meeting ownership the same way `start-session --wait-ready` already does.

**Files:**
- Modify: `agentsassemble/cli.py`
- Modify: `docs/live-agent-ops.md`
- Test: `tests/test_cli_timeout.py`

- [x] **Step 1: Add RED coverage for ownership attention in read-only checks**

Cover degraded `check-session` and `session-readiness` responses where connection and process evidence are present and ownership reports `meeting:duplicate_active_group`. The compact CLI output must include that ownership attention.

Run:

```bash
python3 -m unittest \
  tests.test_cli_timeout.CliTimeoutTests.test_live_agent_check_session_fail_on_degraded_returns_failure \
  tests.test_cli_timeout.CliTimeoutTests.test_live_agent_session_readiness_fail_on_degraded_returns_failure
```

Expected: fail before implementation because `_format_live_agent_session_check()` only prints connection and process attention.

- [x] **Step 2: Reuse the shared session attention formatter**

Change `_format_live_agent_session_check()` to use `_live_agent_session_attention()` so compact output includes de-duplicated connection, process, and ownership attention.

Run the same targeted tests.

Expected: pass.

- [x] **Step 3: Document ownership attention on check/readiness**

Update the operator docs so `check-session` and `session-readiness` explicitly list ownership attention and duplicate active group ownership.

---

### Task 86: Reply Post Failures Leave Presence Evidence

**Goal:** Preserve operator evidence when a resident agent generates a reply but cannot post it back to the lobby or official meeting surface.

**Files:**
- Modify: `agentsassemble/live_agent_runner.py`
- Modify: `docs/live-agent-ops.md`
- Test: `tests/test_live_agent_runner.py`

- [x] **Step 1: Add RED coverage for failed reply posts**

Cover lobby and official-turn post failures after the provider command has returned a reply. The runner must attempt an `error` heartbeat with `last_error` and the relevant observed cursor before surfacing the original post failure.

Run:

```bash
python3 -m unittest \
  tests.test_live_agent_runner.LiveAgentRunnerTests.test_runner_does_not_mask_lobby_post_failure_when_final_offline_heartbeat_fails \
  tests.test_live_agent_runner.LiveAgentRunnerTests.test_runner_records_official_turn_post_failure_before_raising
```

Expected: fail before implementation because post failures raise without leaving an error heartbeat.

- [x] **Step 2: Preserve the original post failure when error heartbeat fails**

Cover a lobby post failure where the attempted error heartbeat also fails. The original post failure must remain the exception visible to the supervisor, and the attempted heartbeat must still carry the observed cursor.

Run:

```bash
python3 -m unittest tests.test_live_agent_runner.LiveAgentRunnerTests.test_runner_does_not_mask_lobby_post_failure_when_error_heartbeat_fails
```

Expected: fail before implementation because the heartbeat failure can mask the post failure.

- [x] **Step 3: Record post failures best-effort**

Wrap lobby and official-turn post calls so post failures update runner-local `last_error`, attempt a best-effort `error` heartbeat with cursor metadata, and then re-raise the original post exception.

- [x] **Step 4: Document reply post failure evidence**

Document that generated reply post failures are distinct from provider command failures and room read failures, but still leave best-effort presence evidence for operators.

- [x] **Step 5: Keep posted replies successful when success heartbeat fails**

Cover lobby and official-turn reply posts that succeed, followed by a failed `online` heartbeat carrying `last_reply_at`. The runner must keep the posted reply successful, return the handled reply count, and leave `last_error` clear.

Run:

```bash
python3 -m unittest \
  tests.test_live_agent_runner.LiveAgentRunnerTests.test_runner_does_not_mask_lobby_reply_when_success_heartbeat_fails \
  tests.test_live_agent_runner.LiveAgentRunnerTests.test_runner_does_not_mask_official_reply_when_success_heartbeat_fails
```

Expected: fail before implementation because `_record_reply_success()` lets the success heartbeat failure raise after the reply has already been posted.

- [x] **Step 6: Make post-success presence update best-effort**

Use the existing safe heartbeat path for the post-success `online` heartbeat so a transient presence write failure cannot convert an already-posted reply into a failed runner tick.

---

### Task 87: Provider Error Heartbeats Are Best-Effort

**Goal:** Keep handled provider command failures inside the resident runner's normal cooldown/backoff path even when the error heartbeat write itself fails.

**Files:**
- Modify: `agentsassemble/live_agent_runner.py`
- Modify: `docs/live-agent-ops.md`
- Test: `tests/test_live_agent_runner.py`

- [x] **Step 1: Add RED coverage for provider error heartbeat masking**

Cover a provider command failure where the attempted `status: "error"` heartbeat also fails. The runner must return the handled reply count, preserve local `last_error` and `last_error_at`, and still attempt the final offline heartbeat instead of crashing on the heartbeat write.

Run:

```bash
python3 -m unittest tests.test_live_agent_runner.LiveAgentRunnerTests.test_runner_does_not_mask_command_failure_when_error_heartbeat_fails
```

Expected: fail before implementation because the strict error heartbeat masks the handled provider command failure.

- [x] **Step 2: Retry error evidence during backoff**

Cover a bounded two-tick run where the first provider error heartbeat fails. The next periodic heartbeat during the failure backoff should retry `status: "error"` with the same `last_error` and observed cursor.

Run:

```bash
python3 -m unittest tests.test_live_agent_runner.LiveAgentRunnerTests.test_runner_retries_command_error_heartbeat_during_failure_backoff
```

Expected: fail before implementation because the first failed error heartbeat stops the run.

- [x] **Step 3: Make provider error heartbeat best-effort**

Use the safe heartbeat path for provider-command error heartbeats while preserving runner-local `last_error`, `last_error_at`, transient-room error state, and cursor metadata.

- [x] **Step 4: Document best-effort provider error evidence**

Document that provider-command error presence writes are best-effort, local backoff state remains active, and periodic heartbeats retry the same error evidence.

---

### Task 88: Initial Working Heartbeats Are Best-Effort

**Goal:** Keep selected lobby and official-turn replies moving after a healthy room snapshot even when the pre-command `working` presence write fails.

**Files:**
- Modify: `agentsassemble/live_agent_runner.py`
- Modify: `docs/live-agent-ops.md`
- Test: `tests/test_live_agent_runner.py`

- [x] **Step 1: Add RED coverage for lobby replies masked by working heartbeat failure**

Cover a selected lobby event where the initial `status: "working"` heartbeat fails before the provider command. The provider command should still run, the lobby reply should still post, and the runner should keep `last_error` clear.

Run:

```bash
python3 -m unittest tests.test_live_agent_runner.LiveAgentRunnerTests.test_runner_does_not_mask_lobby_reply_when_initial_working_heartbeat_fails
```

Expected: fail before implementation because the strict `working` heartbeat raises before the command runs.

- [x] **Step 2: Add RED coverage for official replies masked by working heartbeat failure**

Cover a selected moderator-called official turn request where the initial `working` heartbeat fails before the provider command. The provider command should still run and the official reply should still post with the original `source_event_id`.

Run:

```bash
python3 -m unittest tests.test_live_agent_runner.LiveAgentRunnerTests.test_runner_does_not_mask_official_reply_when_initial_working_heartbeat_fails
```

Expected: fail before implementation because the strict `working` heartbeat raises before the command runs.

- [x] **Step 3: Make pre-command working evidence best-effort**

Use the safe heartbeat path for the initial `working` heartbeat while keeping startup registration and the first room read strict.

- [x] **Step 4: Document the working heartbeat boundary**

Document that `working` is operator evidence after event selection, not a readiness gate that can block command execution or durable reply posting.

---

### Task 89: Process Restart And Recovery Refuse Blank Launch Config

**Goal:** Keep direct low-level process restart/recovery controls from treating a blank persisted `config_path` as the current directory.

**Files:**
- Modify: `agentsassemble/live_agent_processes.py`
- Modify: `docs/live-agent-ops.md`
- Test: `tests/test_live_agent_processes.py`

- [x] **Step 1: Add RED coverage for blank restart config**

Cover a stopped historical process record with a server but blank `config_path`. `restart_group()` must raise a clear missing-config error before preflight, process launch, or lifecycle event writes.

Run:

```bash
python3 -m unittest tests.test_live_agent_processes.LiveAgentProcessSupervisorTests.test_restart_group_refuses_blank_persisted_config_before_preflight
```

Expected: fail before implementation because `Path("")` is treated as the current directory and reaches the preflight/load path.

- [x] **Step 2: Add RED coverage for blank recovery config**

Cover an error historical process record with whitespace-only `config_path`. `recover_group()` must raise a clear missing-config error before preflight, process launch, or lifecycle event writes.

Run:

```bash
python3 -m unittest tests.test_live_agent_processes.LiveAgentProcessSupervisorTests.test_recover_group_refuses_blank_persisted_config_before_preflight
```

Expected: fail before implementation because whitespace reaches the generic file-not-found path.

- [x] **Step 3: Guard persisted process launch config**

Normalize persisted `config_path` at the direct restart/recover boundary and refuse blank values before calling the shared start/preflight path.

- [x] **Step 4: Document the direct process control error boundary**

Document that direct restart/recovery controls refuse blank persisted launch config before preflight or process launch.

---

### Task 90: Auto-Restart Refuses Blank Launch Config

**Goal:** Keep immediate and delayed auto-restart relaunch paths from treating a blank persisted `config_path` as the current directory.

**Files:**
- Modify: `agentsassemble/live_agent_processes.py`
- Modify: `docs/live-agent-ops.md`
- Test: `tests/test_live_agent_processes.py`

- [x] **Step 1: Add RED coverage for immediate auto-restart blank config**

Cover a crashed running group with `auto_restart` enabled, zero backoff, and a corrupted blank persisted `config_path`. The supervisor must mark the group `error` with clear missing-config text, increment the restart counter, write `restart_failed` lifecycle evidence, and avoid preflight or relaunch.

Run:

```bash
python3 -m unittest tests.test_live_agent_processes.LiveAgentProcessSupervisorTests.test_immediate_auto_restart_refuses_blank_persisted_config_before_preflight
```

Expected: fail before implementation because the immediate restart path treats `Path("")` as the current directory and reaches preflight.

- [x] **Step 2: Add RED coverage for delayed auto-restart blank config**

Cover a crashed running group with delayed auto-restart whose persisted `config_path` is whitespace-only before the due restart. The due restart must mark the group `error`, clear `next_restart_at`, write `restart_failed`, and avoid preflight or relaunch.

Run:

```bash
python3 -m unittest tests.test_live_agent_processes.LiveAgentProcessSupervisorTests.test_delayed_auto_restart_refuses_blank_persisted_config_before_preflight
```

Expected: fail before implementation because the due restart path reports generic file-not-found or directory errors.

- [x] **Step 3: Share the persisted config guard in auto-restart relaunches**

Use the same persisted config guard for immediate and delayed auto-restart relaunches that direct restart/recovery already use.

- [x] **Step 4: Document auto-restart missing-launch-config handling**

Document that auto-restart relaunches also refuse blank persisted launch config before preflight or process launch.

---

### Task 91: Process Relaunches Refuse Blank Server

**Goal:** Keep direct restart/recovery and immediate/delayed auto-restart from relaunching a resident group without persisted server evidence for the target room.

**Files:**
- Modify: `agentsassemble/live_agent_processes.py`
- Modify: `docs/live-agent-ops.md`
- Test: `tests/test_live_agent_processes.py`

- [x] **Step 1: Add RED coverage for direct restart blank server**

Cover a stopped historical process record with a valid `config_path` but whitespace-only `server`. `restart_group()` must raise a clear missing-server error before preflight, process launch, or lifecycle event writes.

Run:

```bash
python3 -m unittest tests.test_live_agent_processes.LiveAgentProcessSupervisorTests.test_restart_group_refuses_blank_persisted_server_before_preflight
```

Expected: fail before implementation because whitespace-only server currently reaches the relaunch path.

- [x] **Step 2: Add RED coverage for direct recovery blank server**

Cover an error historical process record with a valid `config_path` but whitespace-only `server`. `recover_group()` must raise a clear missing-server error before preflight, process launch, or lifecycle event writes.

Run:

```bash
python3 -m unittest tests.test_live_agent_processes.LiveAgentProcessSupervisorTests.test_recover_group_refuses_blank_persisted_server_before_preflight
```

Expected: fail before implementation because whitespace-only server currently reaches the relaunch path.

- [x] **Step 3: Add RED coverage for auto-restart blank server**

Cover immediate and delayed auto-restart relaunches with blank persisted `server`. The supervisor must mark the group `error`, write `restart_failed`, avoid preflight/relaunch, and preserve sanitized lifecycle evidence.

Run:

```bash
python3 -m unittest \
  tests.test_live_agent_processes.LiveAgentProcessSupervisorTests.test_immediate_auto_restart_refuses_blank_persisted_server_before_preflight \
  tests.test_live_agent_processes.LiveAgentProcessSupervisorTests.test_delayed_auto_restart_refuses_blank_persisted_server_before_preflight
```

Expected: fail before implementation because auto-restart can relaunch with a blank or whitespace server value.

- [x] **Step 4: Share the persisted server guard**

Normalize persisted `server` at every process relaunch boundary and refuse blank values before preflight or process launch.

- [x] **Step 5: Document missing server launch evidence**

Document that restart, recovery, and auto-restart relaunches require persisted server evidence for the target room.

---

### Task 92: Health Explains Restart-Failed Missing Launch Evidence

**Goal:** Let operators see why an auto-restart reached `restart_failed` when persisted launch evidence is missing, without exposing config paths, server URLs, commands, or provider output.

**Files:**
- Modify: `agentsassemble/gui.py`
- Modify: `docs/live-agent-ops.md`
- Test: `tests/test_gui_server.py`
- Test: `tests/test_cli_timeout.py`

- [x] **Step 1: Add RED health coverage for restart-failed launch evidence**

Cover error process groups whose latest lifecycle evidence is `restart_failed` and whose `last_error` says the relaunch is missing persisted config or server evidence for the same safe group id. `/api/live-agent-health` must expose only compact constants such as `missing launch config` and `missing launch server`, prefer current `restart_failed` launch evidence over older watchdog context, and drop stale, wrong-group, non-error, or suspicious restart-failure errors that contain path-like evidence.

Run:

```bash
python3 -m unittest tests.test_gui_server.GuiServerTests.test_live_agent_health_endpoint_summarizes_agents_and_processes
```

Expected: fail before implementation because health only reports stale-watchdog process reasons.

- [x] **Step 2: Recognize only safe restart-failed launch reasons**

At the shared health reason decision point, keep the stale-watchdog sanitizer unchanged and add `restart_failed` handling that derives only the two safe operator constants from bounded `last_error` text when the current process row is still in `error` and the generated failure text names the same safe group id. Do not echo group ids, config paths, server values, command paths, URLs, env markers, provider output, or arbitrary lifecycle strings.

- [x] **Step 3: Cover CLI health output**

Extend text-output coverage so the existing `process reasons:` summary includes `restart_failed missing launch config` when the health payload carries that sanitized reason.

Run:

```bash
python3 -m unittest tests.test_cli_timeout.CliTimeoutTests.test_live_agent_health_prints_summary
```

- [x] **Step 4: Document the expanded health reason contract**

Update operator docs to state that health/doctor can expose safe stale-watchdog reasons and safe restart-failed missing launch config/server reasons, while suspicious values are dropped.

---

### Task 93: Redact Sensitive JSONL Live-Session Stderr

**Goal:** Keep long-running JSONL `live_session` subprocess failures useful for operators without copying stderr secrets, endpoints, config paths, or command options into `last_error`.

**Files:**
- Modify: `agentsassemble/live_session_transport.py`
- Modify: `docs/live-agent-ops.md`
- Test: `tests/test_live_agent_runner.py`
- Test: `tests/test_live_session_transport.py`

- [x] **Step 1: Add RED coverage for sensitive stderr tail redaction**

Cover a JSONL subprocess that writes a safe setup line plus token, endpoint, and config-path evidence to stderr before exiting. The raised error must include `stderr tail redacted.` and must not include the token, host, or config filename.

Run:

```bash
python3 -m unittest tests.test_live_session_transport.JsonlLiveSessionTests.test_jsonl_session_redacts_sensitive_stderr_tail_from_errors
```

Expected: fail before implementation because `_process_closed_error()` includes the raw stderr tail.

- [x] **Step 2: Preserve safe bounded stderr tails**

Cover a safe short stderr tail such as `model warming failed` so benign operator clues remain visible.

Run:

```bash
python3 -m unittest tests.test_live_session_transport.JsonlLiveSessionTests.test_jsonl_session_keeps_safe_stderr_tail_in_errors
```

- [x] **Step 3: Sanitize at the transport error boundary**

At the single `_process_closed_error()` boundary, replace suspicious stderr tails with `stderr tail redacted.` before constructing the exception message. Keep the stored `stderr_tail` property unchanged for in-process diagnostics, but never copy suspicious tails into the raised error string that resident runners persist as `last_error`.

- [x] **Step 4: Cover resident runner `last_error`**

Cover a live-session runner command failure that originates from a JSONL subprocess with sensitive stderr. The runner must write an `error` heartbeat whose `last_error` includes `stderr tail redacted.` but not the token, host, or config filename.

Run:

```bash
python3 -m unittest tests.test_live_agent_runner.LiveAgentRunnerTests.test_runner_records_jsonl_live_session_failure_without_sensitive_stderr
```

- [x] **Step 5: Document live-session stderr redaction**

Document that JSONL live-session subprocess errors may expose safe short stderr tails, while stderr with auth markers, endpoints, config paths, command options, or path-like values is redacted before presence/GUI surfaces.

---

### Task 94: Redact Sensitive Process Log Tails

**Goal:** Keep GUI/process API log-tail clues useful without exposing secrets, endpoints, config paths, command options, or environment references from supervised `run-group` logs.

**Files:**
- Modify: `agentsassemble/live_agent_processes.py`
- Modify: `docs/live-agent-ops.md`
- Test: `tests/test_live_agent_processes.py`

- [x] **Step 1: Add RED coverage for sensitive process log tails**

Cover persisted supervised process records whose backing `<group_id>.log` tail contains token, bearer auth, password, env refs, config filenames, slash/backslash paths, URL, or option-string evidence. `list_groups()` must expose `log tail redacted.` and must not persist `log_tail` back into `processes.json`.

Run:

```bash
python3 -m unittest tests.test_live_agent_processes.LiveAgentProcessSupervisorTests.test_list_groups_redacts_sensitive_log_tail_without_persisting_it
```

Expected: fail before implementation because `_read_log_tail()` returns the raw bounded file tail.

- [x] **Step 2: Preserve safe bounded process log tails**

Keep the existing bounded safe-tail behavior so short benign clues such as `final clue` remain visible and bounded.

Run:

```bash
python3 -m unittest tests.test_live_agent_processes.LiveAgentProcessSupervisorTests.test_list_groups_includes_bounded_log_tail_without_persisting_it
```

- [x] **Step 3: Sanitize only the process output surface**

Sanitize the `log_tail` attached by `_record_for_output()` before process rows reach the API, CLI, or GUI. Keep `_read_log_tail()` as a raw bounded reader and do not rewrite or scrub the raw local `.log` file; it remains local diagnostic evidence.

- [x] **Step 4: Document process log-tail redaction**

Document that raw local process logs are not scrubbed, while API/GUI `log_tail` output redacts suspicious tails with a fixed marker.

---

### Task 95: Compact Resident Subprocess Command Errors

**Goal:** Keep resident local subprocess failures useful in `last_error` without copying command arguments, stdout, stderr, config paths, or tokens into presence.

**Files:**
- Modify: `agentsassemble/live_agent_runner.py`
- Modify: `docs/live-agent-ops.md`
- Test: `tests/test_live_agent_runner.py`

- [x] **Step 1: Add RED coverage for non-zero subprocess command args**

Cover a resident command runner that raises `subprocess.CalledProcessError` with command args containing `--token`, a secret value, and a config path plus private stdout/stderr. The error heartbeat must report only `Resident command exited with return code 7.` and omit command args/output.

Run:

```bash
python3 -m unittest tests.test_live_agent_runner.LiveAgentRunnerTests.test_runner_records_subprocess_failure_without_command_args
```

Expected: fail before implementation because `LiveAgentRunner` currently persists `str(error)`, which includes the command list.

- [x] **Step 2: Add RED coverage for subprocess timeout command args**

Cover a resident command runner that raises `subprocess.TimeoutExpired` with command args and captured output. The error heartbeat must report only `Resident command timed out after 9 seconds.` and omit command args/output.

Run:

```bash
python3 -m unittest tests.test_live_agent_runner.LiveAgentRunnerTests.test_runner_records_subprocess_timeout_without_command_args
```

Expected: fail before implementation because timeout exception strings include the command list.

- [x] **Step 3: Sanitize provider command errors before heartbeat**

At the provider-command failure boundary in `LiveAgentRunner._generate_reply()`, map subprocess non-zero exits, timeouts, and OS launch failures to compact resident command messages before assigning `last_error` or sending the `error` heartbeat. Keep safe non-subprocess messages visible, and redact suspicious generic messages with a fixed marker.

- [x] **Step 4: Cover OS errors and suspicious generic command errors**

Cover `FileNotFoundError` with a private path and generic runtime errors that mention config paths. The runner's own `last_error` and the heartbeat payload must both use compact or redacted messages without the private path.

- [x] **Step 5: Document compact resident command errors**

Document that resident subprocess failures do not copy command args, stdout, or stderr into presence `last_error`.

---

### Task 96: Redact Runner Room/Post Failure Heartbeats

**Goal:** Keep transient room-read and reply-post failure evidence useful in presence without copying URLs, config paths, tokens, or command-like details into `last_error`.

**Files:**
- Modify: `agentsassemble/live_agent_runner.py`
- Modify: `docs/live-agent-ops.md`
- Test: `tests/test_live_agent_runner.py`

- [x] **Step 1: Add RED coverage for sensitive transient room reads**

Cover a bounded resident run where the first `/room` snapshot succeeds, then a later read-only `/room` request fails with a URL, config filename, and token. The runner must keep polling, send an `error` heartbeat with `Resident room read error details redacted.`, and keep the same sanitized value in runner-local `last_error`.

Run:

```bash
python3 -m unittest tests.test_live_agent_runner.LiveAgentRunnerTests.test_runner_redacts_sensitive_transient_room_failure_error
```

Expected: fail before implementation because the transient room failure path copies `str(error)` into presence.

- [x] **Step 2: Add RED coverage for sensitive reply-post failures**

Cover lobby and official-turn post failures after the provider command returns a reply. The runner must preserve the original raised post exception for the supervisor, but the attempted `error` heartbeat and runner-local `last_error` must use `Resident reply post error details redacted.` and keep the relevant observed cursor.

Run:

```bash
python3 -m unittest \
  tests.test_live_agent_runner.LiveAgentRunnerTests.test_runner_redacts_sensitive_lobby_post_failure_heartbeat \
  tests.test_live_agent_runner.LiveAgentRunnerTests.test_runner_redacts_sensitive_official_turn_post_failure_heartbeat
```

Expected: fail before implementation because reply post failures copy `str(error)` into presence.

- [x] **Step 3: Sanitize room/post errors at runner boundaries**

At the post-start room read recovery path and the reply-post error-recording path, pass the exception through the resident surface sanitizer before assigning `last_error` or sending heartbeat evidence. Keep safe short messages visible, keep first room read failures strict, and keep reply-post exceptions re-raised unchanged.

- [x] **Step 4: Document sanitized runner boundary errors**

Document that transient room-read and reply-post heartbeat `last_error` values are sanitized before presence, while raw local exceptions/log files are not promised to be scrubbed by this runner boundary.

---

### Task 97: Redact Process Restart Failure Errors

**Goal:** Keep process restart-failure evidence useful in process list/API/GUI output without copying sensitive preflight or launch exception text into `last_error`.

**Files:**
- Modify: `agentsassemble/live_agent_processes.py`
- Modify: `docs/live-agent-ops.md`
- Test: `tests/test_live_agent_processes.py`

- [x] **Step 1: Add RED coverage for sensitive auto-restart failure text**

Cover an immediate auto-restart where relaunch preflight fails with a URL, config filename, command option, and token in the failure message. The process row and newly persisted restart-failed record must use a compact redacted restart-failure label and must not keep the sensitive substrings.

Run:

```bash
python3 -m unittest tests.test_live_agent_processes.LiveAgentProcessSupervisorTests.test_auto_restart_failed_preflight_redacts_sensitive_last_error
```

Expected: fail before implementation because the auto-restart failure path appends `str(error)` to `last_error`.

- [x] **Step 2: Add RED coverage for legacy sensitive process last_error output**

Cover a historical process record whose stored `last_error` already contains a path and token. `list_groups()` must redact the output-only value without rewriting unrelated persisted state.

Run:

```bash
python3 -m unittest tests.test_live_agent_processes.LiveAgentProcessSupervisorTests.test_list_groups_redacts_legacy_sensitive_last_error_without_persisting_output_field
```

Expected: fail before implementation because `_record_for_output()` copies stored `last_error` directly.

- [x] **Step 3: Sanitize restart-failure and process output errors**

At the auto-restart failure boundaries, append only a sanitized restart-failure message to `last_error`. At process output readback, sanitize stored `last_error` before adding API/GUI/CLI-only fields. Keep safe short preflight messages visible.

- [x] **Step 4: Document process last_error redaction**

Document that process rows redact suspicious `last_error` text on output and that new restart-failed records store compact redacted messages for sensitive relaunch exceptions.

---

### Task 98: Redact Process Control Error Responses

**Goal:** Keep browser-visible process control failures useful without echoing config paths, endpoints, tokens, command options, env refs, or secret-looking values in API error bodies.

**Files:**
- Modify: `agentsassemble/gui.py`
- Modify: `docs/live-agent-ops.md`
- Test: `tests/test_gui_server.py`

- [x] **Step 1: Add RED coverage for start error body redaction**

Cover `POST /api/live-agent-processes/start` with a missing config path whose filename and directory are sensitive. The HTTP 400 JSON body and operation history must not include the path or config filename, while the response still names the process start failure class.

Run:

```bash
python3 -m unittest tests.test_gui_server.GuiServerTests.test_live_agent_process_start_redacts_sensitive_error_body
```

Expected: fail before implementation because the endpoint sends `str(error)` directly.

- [x] **Step 2: Add RED coverage for restart error body redaction**

Cover `POST /api/live-agent-processes/<group_id>/restart` where the persisted config path no longer exists. The HTTP 400 JSON body and operation history must not include the persisted path or config filename, and the body should still expose the safe group id.

Run:

```bash
python3 -m unittest tests.test_gui_server.GuiServerTests.test_live_agent_process_restart_redacts_sensitive_error_body
```

Expected: fail before implementation because the endpoint sends `str(error)` directly.

- [x] **Step 3: Sanitize process-control error responses**

Add a process-control error formatter for start, stop, restart, recover, and stop-running. Redact suspicious path/endpoint/token/config/option/env details, preserve safe short messages, and pass safe group ids through the existing `_send_error(..., details=...)` surface.

- [x] **Step 4: Document browser-visible process errors**

Document that process-control API/GUI error bodies are sanitized separately from operation history and process records.

---

### Task 99: Direct Resident SIGTERM Clean Shutdown

**Goal:** Make direct `live-agent run` shutdown behave like supervised `run-group` shutdown instead of leaking `KeyboardInterrupt` tracebacks when an operator or process manager sends SIGTERM.

**Files:**
- Modify: `agentsassemble/cli.py`
- Modify: `docs/live-agent-ops.md`
- Modify: `docs/superpowers/plans/2026-05-17-live-agent-final-form.md`
- Test: `tests/test_cli_timeout.py`
- Test: `tests/test_docs_architecture.py`

- [x] **Step 1: Add RED coverage for direct resident shutdown**

Cover direct self-service and local-CLI `live-agent run` paths where the resident shutdown signal handler fires while the runner is active. Both paths must close their supervised child or active command runner, restore the temporary signal handler, return exit code `0`, and print the normal stopped summary rather than letting `KeyboardInterrupt` escape.

- [x] **Step 2: Catch resident shutdown inside the direct run boundary**

Handle `KeyboardInterrupt` inside `_run_live_agent_resident()` for both `self_service` and parent-managed resident command runners. Keep non-shutdown errors on the existing error path, and keep `run-group` worker shutdown behavior unchanged.

- [x] **Step 3: Document direct shutdown semantics**

Document that direct resident SIGTERM uses the same clean shutdown path as KeyboardInterrupt and closes active command runners before returning the normal stopped summary.

---

### Task 100: Durable Session-Run Intent Controller

**Goal:** Add the first durable high-level session intent layer above one-shot `ensure` calls, so a resident session can be listed, inspected, and reconciled after GUI restart without confusing process mechanics with operator intent.

**Files:**
- Create: `agentsassemble/live_agent_session_runs.py`
- Modify: `agentsassemble/gui.py`
- Modify: `agentsassemble/cli.py`
- Modify: `docs/live-agent-ops.md`
- Modify: `docs/superpowers/plans/2026-05-17-live-agent-final-form.md`
- Test: `tests/test_live_agent_session_runs.py`
- Test: `tests/test_gui_server.py`
- Test: `tests/test_cli_timeout.py`
- Test: `tests/test_docs_architecture.py`

- [x] **Step 1: Add RED coverage for durable session-run records**

Cover a controller that begins an `ensure` run, persists safe public state, finishes with ready/session evidence, reloads from disk, reconciles active runs through a provided callback, stops matching runs after an operator stop, and records sanitized failure state without leaking tokens, absolute paths, server URLs, commands, prompts, or provider output.

- [x] **Step 2: Add a thin API wrapper above existing ensure**

Expose `POST /api/live-agent-session-runs/ensure` as a durable wrapper around `live_agent_session_ensure_payload()`. The wrapper creates the run before invoking ensure, updates it with the final readiness/session result, returns `session_run` in the response, and leaves the existing one-shot `/api/live-agent-sessions/ensure` behavior intact.

- [x] **Step 3: Add inspection surfaces**

Expose `GET /api/live-agent-session-runs?limit=N` and `assemble live-agent session-runs list --limit N [--json]` so operators and scripts can inspect durable session intent separately from append-only `operations.jsonl` and low-level `processes.json`.

- [x] **Step 4: Reconcile on GUI startup**

Instantiate the controller beside the process supervisor and reconcile active durable runs on GUI startup by replaying their saved ensure request. Record a bounded `session_run.reconcile` operation summarizing startup reconciliation.

- [x] **Step 5: Document session-run semantics**

Document `session-runs.json`, the durable ensure endpoint, the CLI list command, startup reconciliation, and the redaction boundary between internal recovery request state and public operator output.

---

### Task 101: Durable Session-Run GUI Surface

**Goal:** Make durable session-run intent visible and usable from the existing Lobby `상주 실행` panel without changing the unrelated page shell or stylesheet work already in progress.

**Files:**
- Modify: `agentsassemble/static/shared.js`
- Modify: `agentsassemble/static/lobby.js`
- Modify: `docs/live-agent-ops.md`
- Modify: `docs/superpowers/plans/2026-05-17-live-agent-final-form.md`
- Test: `tests/static_lobby_runtime_smoke.mjs`
- Test: `tests/test_static_ui_assets.py`
- Test: `tests/test_docs_architecture.py`

- [x] **Step 1: Add RED GUI coverage for durable ensure**

Cover a `상주보장` control that sends the same resident-session payload to `/api/live-agent-session-runs/ensure`, reports the returned `session_run` id/status in the operator status line, refreshes the durable run list, and stays blocked while another live-agent process action is running.

- [x] **Step 2: Add a safe session-run list to the Lobby**

Store `liveAgentSessionRuns` beside the existing live-agent runtime state, load `/api/live-agent-session-runs?limit=20` during initial and periodic runtime refreshes, and render a compact `상주 세션런` list containing run id, meeting id, group id, status, phase, active flag, reconcile count, and connected/expected counts.

- [x] **Step 3: Preserve the redaction boundary in the GUI**

Keep saved config paths, server URLs, commands, prompts, provider output, auth refs, and log tails out of the rendered session-run rows. The GUI list is operator evidence, not a dump of the controller's private recovery payload.

- [x] **Step 4: Verify in tests and a rendered browser smoke**

Run the static runtime smoke, static UI assertions, docs assertion, JS syntax checks, and a real GUI browser smoke against an empty temporary output root so the render path is exercised without starting resident agents.

---

### Task 102: Durable Auto-Join Session Intent

**Goal:** Make automatic local CLI entry record the same durable session-run intent as manual `상주보장`, so discovered resident sessions remain inspectable and recoverable after GUI restart.

**Files:**
- Modify: `agentsassemble/static/lobby.js`
- Modify: `agentsassemble/cli.py`
- Modify: `docs/live-agent-ops.md`
- Modify: `docs/superpowers/plans/2026-05-17-live-agent-final-form.md`
- Test: `tests/static_lobby_runtime_smoke.mjs`
- Test: `tests/test_live_agent_discovery.py`
- Test: `tests/test_docs_architecture.py`

- [x] **Step 1: Add RED coverage for durable auto-join**

Cover GUI `자동입장` and CLI `live-agent auto-join` so they fail if they call the one-shot `/api/live-agent-sessions/ensure` path instead of `/api/live-agent-session-runs/ensure`. The assertions preserve the same session bundle, restart, remaining-round, finalization, and blank-meeting adoption payload fields.

- [x] **Step 2: Route auto-join through the durable ensure wrapper**

Change GUI `자동입장` to post the discovered bundle through `/api/live-agent-session-runs/ensure`, and change CLI `auto-join` to post the generated session bundle payload to the same durable endpoint. Preserve post-control readiness waiting and carry returned `session_run` evidence through the final CLI payload.

- [x] **Step 3: Document the durable entry semantics**

Document that discovery remains PATH-only and non-mutating, while `auto-join` is the explicit mutating operation that may start providers and now leaves a durable `session-runs.json` record visible through the GUI `상주 세션런` list.

---

### Task 103: Session Stop Durable Run Evidence

**Goal:** Make `세션중지` prove which durable session-run intents were closed, so stopped resident sessions do not look recoverable or ambiguous after GUI restart.

**Files:**
- Modify: `agentsassemble/gui.py`
- Modify: `agentsassemble/cli.py`
- Modify: `agentsassemble/static/lobby.js`
- Modify: `docs/live-agent-ops.md`
- Modify: `docs/superpowers/plans/2026-05-17-live-agent-final-form.md`
- Test: `tests/test_gui_server.py`
- Test: `tests/test_cli_timeout.py`
- Test: `tests/static_lobby_runtime_smoke.mjs`
- Test: `tests/test_static_ui_assets.py`
- Test: `tests/test_docs_architecture.py`

- [x] **Step 1: Add RED coverage for stopped run evidence**

Cover stop-session responses with matching active durable runs and require API operation details, CLI text output, and GUI status text to include safe stopped session-run evidence.

- [x] **Step 2: Surface stopped run counts and ids**

Keep the existing `mark_matching_stopped()` behavior and expose its result through the existing stop response. Add sanitized operation details for `session_run_stopped_count` and bounded safe `session_run_ids`, add CLI text such as `1 session run stopped`, and add GUI status text such as `runs stopped 1`.

- [x] **Step 3: Document stop semantics**

Document that `세션중지` stops matching active session-runs, refreshes the GUI list, and records only safe run ids/counts in operation history.

---

### Task 104: Durable Session-Run Wait Gate

**Goal:** Give scripts and other agents a durable session-run status gate, so long-running resident session intent can be observed without scraping the GUI or conflating process lifecycle events with high-level session intent.

**Files:**
- Modify: `agentsassemble/cli.py`
- Modify: `docs/live-agent-ops.md`
- Modify: `docs/superpowers/plans/2026-05-17-live-agent-final-form.md`
- Test: `tests/test_cli_timeout.py`
- Test: `tests/test_docs_architecture.py`

- [x] **Step 1: Add RED coverage for session-run wait**

Cover parser acceptance for `assemble live-agent session-runs wait --run-id <id> --status ready`, a successful polling wait that observes the target run status through `/api/live-agent-session-runs?limit=N`, and a timeout that prints the last safe run summary.

- [x] **Step 2: Implement the wait gate**

Add `session-runs wait` beside the existing `session-runs list` command. Reuse the public session-run list API, keep each HTTP poll bounded by the remaining timeout, exit `0` only when the requested run id has the requested status, and exit `1` on timeout with safe evidence.

- [x] **Step 3: Document automation semantics**

Document the durable session-run status gate, exit-code contract, and redaction boundary so other agents can wait on session-run intent without reading private recovery payloads.

---

### Task 105: Durable Session-Run Handoff Wait Target

**Goal:** Let another script or agent continue a durable session-run handoff without knowing the exact run id, by waiting on the latest matching meeting/group session-run intent.

**Files:**
- Modify: `agentsassemble/cli.py`
- Modify: `docs/live-agent-ops.md`
- Modify: `docs/superpowers/plans/2026-05-17-live-agent-final-form.md`
- Test: `tests/test_cli_timeout.py`
- Test: `tests/test_docs_architecture.py`

- [x] **Step 1: Add RED coverage for meeting/group wait target**

Cover parser acceptance for `session-runs wait --meeting-id <id> --group-id <id> --status ready`, a successful wait that ignores an older ready run while a newer matching run is still running, and validation that refuses a wait without either `--run-id` or both meeting/group ids.

- [x] **Step 2: Select the latest matching intent**

Make `--run-id` optional for `session-runs wait`. When it is absent, require both `--meeting-id` and `--group-id`, select the latest matching run from the public bounded list, and succeed only if that latest run has the requested status.

- [x] **Step 3: Document handoff semantics**

Document that meeting/group wait is for handoffs where the exact run id is unknown, and that older ready runs cannot satisfy the gate while a newer matching run is still in progress.

---

### Task 106: Server-Filtered Session-Run Handoff Wait

**Goal:** Keep meeting/group handoff waits durable when unrelated session-runs are newer than the target by filtering on the server before applying the bounded result window.

**Files:**
- Modify: `agentsassemble/live_agent_session_runs.py`
- Modify: `agentsassemble/gui.py`
- Modify: `agentsassemble/cli.py`
- Modify: `docs/live-agent-ops.md`
- Modify: `docs/superpowers/plans/2026-05-17-live-agent-final-form.md`
- Test: `tests/test_live_agent_session_runs.py`
- Test: `tests/test_gui_server.py`
- Test: `tests/test_cli_timeout.py`
- Test: `tests/test_docs_architecture.py`

- [x] **Step 1: Add RED coverage for server-side filtering**

Cover controller and public API behavior where `limit=1` still returns an older matching meeting/group run even when a newer unrelated run exists, plus CLI wait behavior that sends meeting/group filters to the server.

- [x] **Step 2: Filter before the result tail**

Let `LiveAgentSessionRunController.list_runs()` accept optional `meeting_id` and `group_id`, filter matching records first, then apply `limit`. Forward those query parameters from `/api/live-agent-session-runs`.

- [x] **Step 3: Use filtered polling for handoff waits**

Keep exact `--run-id` waits unfiltered, but make meeting/group waits poll `/api/live-agent-session-runs?limit=N&meeting_id=...&group_id=...` so unrelated runs do not hide the latest matching intent.

---

### Task 107: Session Smoke Recover Kill Signal

**Goal:** Keep the credential-free session smoke recover step meaningful by making its diagnostic process interruption produce a recoverable failed process state instead of a graceful `stopped` state.

**Files:**
- Modify: `agentsassemble/live_agent_smoke.py`
- Modify: `docs/superpowers/plans/2026-05-17-live-agent-final-form.md`
- Test: `tests/test_live_agent_smoke.py`
- Test: `tests/test_gui_server.py`

- [x] **Step 1: Reproduce the failing smoke**

Full unittest and the single `test_live_agent_session_smoke_endpoint_runs_credential_free_session` both failed with `502` because the recover preparation step killed the resident group with `SIGTERM`, which the runner recorded as graceful `stopped` rather than recoverable `error` or `unknown`.

- [x] **Step 2: Make recover preparation non-graceful**

Change the session smoke diagnostic process killer to use `SIGKILL`, preserving normal operator stop behavior while forcing the smoke-only recover path through the intended failed-process state.

- [x] **Step 3: Lock the behavior with targeted verification**

Add a unit assertion for the non-graceful signal and rerun the credential-free GUI smoke endpoint test.

---

### Task 108: Fresh Session-Run Readiness Gate

**Goal:** Make durable session-run `ready` waits prove current live readiness instead of accepting only a historical persisted `ready` intent.

**Files:**
- Modify: `agentsassemble/gui.py`
- Modify: `agentsassemble/cli.py`
- Modify: `docs/live-agent-ops.md`
- Modify: `docs/superpowers/plans/2026-05-17-live-agent-final-form.md`
- Test: `tests/test_gui_server.py`
- Test: `tests/test_cli_timeout.py`
- Test: `tests/test_docs_architecture.py`

- [x] **Step 1: Add RED coverage for fresh readiness**

Cover `/api/live-agent-session-runs?include_readiness=1` returning a safe current readiness overlay without appending operation records, and cover `session-runs wait --status ready` refusing a persisted ready run whose overlay is degraded.

- [x] **Step 2: Add the read-only overlay**

Attach `readiness` only when requested, computed from the existing session readiness snapshot. Do not mutate durable run state, start providers, run probes, stop groups, or append operation history.

- [x] **Step 3: Require fresh readiness for ready waits**

Have CLI ready waits request `include_readiness=1` and match only runs whose persisted status is `ready` and current readiness overlay is also `ready`.

---

### Task 109: Self-Service Failure Presence Evidence

**Goal:** Keep self-service resident failures visible in the live-agent roster, so a crashed child process is not mistaken for an ordinary offline shutdown.

**Files:**
- Modify: `agentsassemble/cli.py`
- Modify: `docs/live-agent-ops.md`
- Modify: `docs/superpowers/plans/2026-05-17-live-agent-final-form.md`
- Test: `tests/test_cli_timeout.py`

- [x] **Step 1: Add RED coverage for self-service child failure**

Cover `_SelfServiceResidentSupervisor` with a child process that exits non-zero and require heartbeats to stop at `online -> error(last_error)` instead of sending a final `offline` heartbeat after the error.

- [x] **Step 2: Preserve error presence outside shutdown**

Track the non-shutdown child failure path and skip the final offline heartbeat only after a safe error presence write succeeds. If the error heartbeat itself fails, keep the final offline fallback so the roster does not remain apparently online. Keep normal bounded exit, max-tick exit, SIGINT/SIGTERM shutdown, and group shutdown behavior on the existing offline path.

- [x] **Step 3: Document the failure boundary**

Document that self-service child failure records `status: "error"` with safe `last_error` and is not overwritten by a final offline heartbeat.

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
