# Live Agent Ops

This is the local operator checklist for the current AgentsAssemble resident live-agent slice. It uses the local GUI room as the control plane and local CLI, JSONL session, and explicitly configured remote HTTP bridge participants as resident agents.

## Start The GUI Room

From the repository root:

```bash
python3 -m agentsassemble.cli gui --host 127.0.0.1 --port 8765 --output-root .agentsassemble
```

Open:

```text
http://127.0.0.1:8765
```

The lobby is the public room surface. The "상주 실행" panel can start, refresh, stop, restart, and diagnose local live-agent process groups. The default group config path is:

```text
configs/live-agents.start-session.example.json
```

The GUI's `세션시작` button pairs that resident config with `configs/demo-council.json` and `configs/agents.start-session.example.json` so the visible meeting bindings and resident runner manifest match. The `시작` button still starts only the supervised process group from the config input, while `세션시작` creates the visible meeting and starts the matching resident group through `/api/live-agent-sessions/start`. `세션재개` reconnects an existing meeting to its supervised group, `세션재시작` restarts that meeting-aware group and waits for fresh presence, `세션복구` recovers an `unknown` or `error` historical group for the same meeting without requiring the config path again, `세션점검` records a meeting-scoped readiness snapshot, and `세션중지` stops that meeting-aware group and updates bound roster evidence through `/api/live-agent-sessions/stop`.

The live-agent roster and supervised process panel auto-refresh in the GUI every 5 seconds. This keeps stale presence, process crashes, pending auto-restart state, and recovered groups visible during long sessions without relying only on the manual refresh buttons. The GUI server also starts a backend supervisor monitor, so owned process crash detection and due auto-restarts continue without an open browser or `/api/live-agent-processes` polling client. The manual refresh buttons remain useful when you want an immediate read after changing files or process state from another terminal.

Session-owned supervised groups persist the safe `meeting_id` in `live-agent-runs/processes.json`. That meeting ownership is preserved through manual restart, recovery, delayed auto-restart, and stale-watchdog restart, and is visible through `/api/live-agent-processes`, `/api/live-agent-health` as `processes.meeting_ids`, `/api/live-agent-health` meeting-owned session readiness, and the GUI process row as `meeting <id>`. This is operator evidence only; it does not store command arguments, endpoint URLs, auth refs, prompts, replies, log tails, or provider output.

Session controls also enforce that ownership. `start-session` with an explicit meeting id, `resume-session`, `restart-session`, `recover-session`, and `stop-session` refuse to reuse or mutate an existing process group that already belongs to a different meeting. `check-session` stays read-only and reports `degraded` with `group:wrong_meeting` instead of treating that group as ready. A non-empty but unsafe stored owner id is treated as a different meeting without echoing the unsafe value.

The real-provider `configs/live-agents.example.json` contains real `claude` and `gemini` commands. Do not start it until the real-provider checklist below is satisfied.

## GUI Startup Autostart

GUI startup autostart is explicit. Starting the GUI with only `gui --host ... --port ... --output-root ...` does not autostart `configs/live-agents.example.json` by default.

Use `--live-agent-config` only when you want the GUI server itself to start one supervised resident group after the HTTP server has bound:

```bash
python3 -m agentsassemble.cli gui \
  --host 127.0.0.1 \
  --port 8765 \
  --output-root .agentsassemble \
  --live-agent-config /path/to/fake-live-agents.json \
  --live-agent-group-id boot \
  --live-agent-auto-restart \
  --live-agent-max-restarts 3 \
  --live-agent-restart-backoff-seconds 5
```

The autostart path uses the same supervisor start and preflight gate as the GUI `시작` button and `live-agent processes start`. It passes the actual bound GUI URL to the resident group, including an OS-selected port when `--port 0` is used.

Startup autostart records a safe `process.autostart` entry in `live-agent-runs/operations.jsonl`. If the config is missing or preflight refuses it, the GUI still serves the room and the failed autostart is visible through recent operations.

## Runtime Engagement Policy

Each roster card has a compact engagement selector for the live agent. It writes through:

```text
POST /api/live-agents/<agent_id>/engagement
```

with a JSON body like:

```json
{"engagement_mode": "watch"}
```

The same runtime policy update is available from the CLI:

```bash
assemble live-agent engagement \
  --server http://127.0.0.1:8765 \
  --agent-id claude-code-live \
  --mode watch
```

The module form is equivalent when running from a checkout:

```bash
python3 -m agentsassemble.cli live-agent engagement \
  --server http://127.0.0.1:8765 \
  --agent-id claude-code-live \
  --mode watch
```

Use `--json` to print the raw API response after the update.

Valid modes are `manual`, `mentioned`, `moderator_called`, `human_only`, `always`, and `watch`. Treat `always` as loop-prone: every non-self lobby event can trigger an automatic reply unless the chain-depth guard blocks it.

Changing engagement mode updates `live_agents.json`, `/api/live-agents`, and `/api/live-agents/<agent_id>/room`, records `engagement_mode_updated_at`, but does not refresh `last_seen_at` or reset `heartbeat_age_seconds`. A policy change is operator control, not proof that the agent process is still alive.

Resident runners read the current room presence on every poll and use that roster `engagement_mode` before falling back to their startup config. Re-registration and heartbeat updates preserve an operator-selected mode instead of silently clobbering it. `watch` and `manual` observe new lobby events and advance `last_observed_event_id` without posting replies, so switching an agent back to an active mode does not replay the backlog.

External or manually driven agents can register through the same room control plane before they begin polling:

```bash
python3 -m agentsassemble.cli live-agent register \
  --server http://127.0.0.1:8765 \
  --agent-id claude-code-live \
  --display-name "Claude Code Live" \
  --provider-kind claude_code \
  --connection-kind local_cli \
  --meeting-id resident-1 \
  --session-id session-1 \
  --engagement-mode watch \
  --json
```

Use `--json` when a wrapper needs the registration acknowledgement, including the server-preserved meeting, session, and engagement fields, instead of parsing the compact `Registered <agent-id>` line.

External or manually driven agents can also post a linked lobby reply through the live-agent endpoint:

```bash
python3 -m agentsassemble.cli live-agent say \
  --server http://127.0.0.1:8765 \
  --agent-id claude-code-live \
  --source-event-id evt1 \
  --auto-chain-depth 1 \
  --json \
  "I saw evt1 and can continue."
```

The live-agent lobby endpoint fills in the agent identity and server-issued `live_agent_endpoint` evidence. It also advances the agent roster with `last_reply_at` from the posted event timestamp, clears stale `last_error` from earlier failures, and, when `--source-event-id` is present, `last_observed_event_id` from that source. Use `--json` to verify the posted event id, `source_event_id`, `auto_chain_depth`, updated agent cursor, and endpoint evidence instead of parsing the compact `Posted <event-id>` line.

## Start A Resident Meeting

Use `start-session` when you want the operator path that creates the visible resident meeting and starts its supervised resident group in one bounded operation:

```bash
python3 -m agentsassemble.cli live-agent start-session \
  --server http://127.0.0.1:8765 \
  --meeting-id resident-1 \
  --group-id resident-main \
  --council-config configs/demo-council.json \
  --agent-config configs/agents.start-session.example.json \
  --live-agent-config configs/live-agents.start-session.example.json \
  --connect-timeout 5
```

The HTTP control-plane path is:

```text
POST /api/live-agent-sessions/start
```

with `meeting_id`, `group_id`, `council_config_path`, `agent_config_path`, `live_agent_config_path`, `connect_timeout_seconds`, and the same `auto_restart`, `max_restarts`, `restart_backoff_seconds`, and `stale_restart_after_seconds` options used by supervised process start. The coordinator preflights the resident group config before creating the meeting, refuses if the resident group manifest does not exactly match the meeting's bound agent ids, and checks direct provider kinds plus compatible resident connection kinds. A meeting provider of `remote_http_bridge` must use a resident `remote_bridge` connection, but the resident `provider_kind` may still name the agent behind that bridge, such as `claude_code`. After those checks, it creates the normal visible resident meeting, starts the supervised group, then waits briefly for bound agents to appear as `online` or `working` and attached to the meeting.

The paired examples `configs/agents.start-session.example.json` and `configs/live-agents.start-session.example.json` are intentionally local fake CLI configs for the demo council. They use the same bound agent ids as `configs/demo-council.json`, keep resident `meeting_id` blank, and are safe for first-pass validation without real Claude, Gemini, Cursor, account login, billing, or bridge setup.

Resident group configs used with `start-session` should either leave each agent's `meeting_id` blank, letting the created roster binding supply it, or match the explicit `--meeting-id`. A resident config that points an agent at a different meeting is refused before state is written, because that runner would otherwise reconnect itself away from the new official meeting.

The response reports `status: "ready"` only when the supervised group is still `running`, its process manifest covers every expected agent, and every expected agent has `online` or `working` presence for the same meeting that is not older than the group's `started_at` when both timestamps are available. It reports `status: "starting"` when the group launched but the process or presence evidence is not ready before the bounded wait ends. A pre-start `online` or `working` roster row is reported as `agent_id:not_reconnected` instead of connected. CLI exit code is `0` for `ready`, `1` for `starting`, and `2` for refused, HTTP, or argument errors. This path does not run official turns unless `--run-remaining-rounds` is supplied, and it never runs smoke probes, model calls, remote bridge `/agentsassemble/run`, decisions, or transcript finalization. Partial meeting and process state remains visible for recovery instead of being deleted; if process launch fails after a generated meeting is created, the failed response and `session.start` operation include the safe meeting id for recovery.

The opt-in automatic path uses `--run-remaining-rounds` in the CLI or `run_remaining_rounds: true` in `POST /api/live-agent-sessions/start`. It reuses the bounded remaining-rounds fields as `round_timeout_seconds`, `round_max_rounds`, and `round_stop_on_timeout`. The server only calls remaining official rounds after `status: "ready"`. If the session is still `starting`, the response includes `auto_rounds.status: "skipped"` and `auto_rounds.reason: "session_not_ready"` and does not append official turn requests. When it does run, `auto_rounds` reports the same sanitized aggregate counts and statuses as `call-remaining-rounds`; degraded round results remain visible instead of being hidden as session recovery.

Add `--probe-bound-agents` or `probe_bound_agents: true` when a session entrypoint must prove every bound resident can actually answer a fresh lobby event before optional auto-rounds begin. The server probes all bound ready-session agents one by one. The probe is bounded by `--probe-timeout` / `probe_timeout_seconds`, capped at 60 seconds per agent, and applies to `start-session`, `resume-session`, `restart-session`, and `recover-session`. Moderator-called, manual, and watch agents are temporarily opened to `human_only` for the probe event and restored afterward, so the check works with the default session engagement policy without leaving the room in always-on mode or leaving an operator override timestamp behind. Probe replies are summarized under `reply_probe`; operation history stores only sanitized counts, agent ids, and statuses. It omits reply text, prompts, config paths, command arguments, endpoint URLs, auth refs, tokens, provider output, and log tails. If any bound probe is skipped, timed out, or failed, optional remaining rounds are skipped with `auto_rounds.reason: "probe_not_ready"` and the session operation is recorded as degraded.

The operation ledger records one sanitized `session.start` entry with result status, meeting id, group id, expected/connected counts, process status, safe agent ids, connection/process attention, and bounded `auto_rounds` counts when the opt-in path is requested. It does not record config paths, command arguments, endpoints, auth refs, prompts, log tails, provider output, replies, or official turn content.

Use `resume-session` when the visible resident meeting already exists and you want one bounded operator action to reconnect its supervised group and re-check readiness:

```bash
python3 -m agentsassemble.cli live-agent resume-session \
  --server http://127.0.0.1:8765 \
  --meeting-id resident-1 \
  --group-id resident-main \
  --live-agent-config configs/live-agents.start-session.example.json \
  --connect-timeout 5
```

The HTTP control-plane path is:

```text
POST /api/live-agent-sessions/resume
```

with `meeting_id`, `group_id`, `live_agent_config_path`, `connect_timeout_seconds`, and the same process restart and optional `run_remaining_rounds` fields as `start-session`. `meeting_id` must name an existing meeting. Resume reads that meeting's `agent_bindings` and `provider_configs`, validates the resident group manifest against those bound agents, and refuses mismatches before starting processes. It never calls the meeting creation path and never overwrites the existing meeting.

If the matching process group is already `running`, resume reuses it only when it is unowned legacy state or already owned by the requested meeting, then waits for real presence evidence. Presence rows whose `last_seen_at` predates the reused or freshly started process group's `started_at` are reported as `agent_id:not_reconnected` and do not make the session ready. If the group is missing, stopped, `unknown`, or `error`, resume starts a fresh supervised process from the supplied config and group id so the process manifest matches the config that was just validated against the meeting. Fresh starts from `start-session` and `resume-session` stamp the supervised group with the safe meeting id so later process inspection can show which room the group belongs to. Missing roster entries are repaired as `offline` rows attached to the meeting so the operator can see stale evidence, but they are not counted as connected until a real registration or heartbeat reports `online` or `working`.

`resume-session --run-remaining-rounds` uses the same ready and optional bound-probe gates as `start-session`: remaining official rounds run only when the resumed session returns `status: "ready"` and any requested `--probe-bound-agents` check passes. Otherwise `auto_rounds.status` is `skipped` with `reason: "session_not_ready"` or `reason: "probe_not_ready"` and no official turn request events are appended. The operation ledger records one sanitized `session.resume` entry with the same safe count/status fields as `session.start`; it does not record config paths, command arguments, endpoints, auth refs, prompts, log tails, provider output, replies, or official turn content.

Use `restart-session` when the visible resident meeting already exists and you want one bounded operator action to restart its matching supervised group and prove that agents reconnected after the restart:

```bash
python3 -m agentsassemble.cli live-agent restart-session \
  --server http://127.0.0.1:8765 \
  --meeting-id resident-1 \
  --group-id resident-main \
  --connect-timeout 5
```

The HTTP control-plane path is:

```text
POST /api/live-agent-sessions/restart
```

with `meeting_id`, `group_id`, and `connect_timeout_seconds`; `--run-remaining-rounds` also sends the optional round-control fields documented below. `meeting_id` must name an existing meeting and `group_id` must name an existing supervised process group. Restart reads the meeting's bound agent ids, validates the current process group manifest against those ids, preflights the persisted restart config and server when the process record names a config, refuses blank persisted config/server fields or manifest mismatches before touching processes, stops the group first when it is currently `running` or `restarting`, clears only that meeting's bound roster rows to `offline`, then starts a fresh supervised group from the persisted process record through the process supervisor's restart path.

The response reports `status: "ready"` only when the restarted process is `running`, its returned manifest matches every expected agent without extras or duplicates, and every expected agent reports fresh `online` or `working` presence for that same meeting after the stale-presence reset. It reports `status: "starting"` when the process restarted but the returned process manifest or fresh presence evidence is still incomplete before the bounded wait ends. This reset is intentional: stale online rows cannot prove restart readiness. A roster row for the same `agent_id` that is currently attached to another meeting is left untouched and reported as `agent_id:wrong_meeting`.

`restart-session --run-remaining-rounds` uses the same ready and optional bound-probe gates as `start-session` and `resume-session`: remaining official rounds run only when the restarted session returns `status: "ready"` and any requested `--probe-bound-agents` check passes. Otherwise `auto_rounds.status` is `skipped` with `reason: "session_not_ready"` or `reason: "probe_not_ready"` and no official turn request events are appended. The round options are the same `--round-timeout`, `--max-rounds`, and `--stop-on-timeout` fields used by start/resume.

CLI exit code is `0` for `ready` with no auto-round failure, `1` for `starting` or degraded auto-rounds, and `2` for refused, HTTP, or argument errors. This path does not run official turns unless `--run-remaining-rounds` is supplied, and it never runs smoke probes, model calls outside the called resident official turns, remote bridge `/agentsassemble/run` outside those resident turns, decisions, or transcript finalization.

The operation ledger records one sanitized `session.restart` entry with result status, meeting id, group id, expected/connected counts, process status, safe agent ids, attention fields, and, when requested, remaining-round counts/statuses. It does not record config paths, command arguments, endpoint URLs, auth refs, prompts, log tails, provider output, replies, or official turn content.

Use `recover-session` when the visible resident meeting already exists and the matching supervised group is historical, `unknown`, or `error`, and you want to relaunch it from its persisted process record instead of supplying a config path again:

```bash
python3 -m agentsassemble.cli live-agent recover-session \
  --server http://127.0.0.1:8765 \
  --meeting-id resident-1 \
  --group-id resident-main \
  --connect-timeout 5
```

The HTTP control-plane path is:

```text
POST /api/live-agent-sessions/recover
```

with `meeting_id`, `group_id`, and `connect_timeout_seconds`; `--run-remaining-rounds` also sends the optional round-control fields documented below. `meeting_id` must name an existing meeting and `group_id` must name an existing supervised process group with a persisted manifest. Recovery validates the stored meeting owner and the current process manifest against the meeting's bound agent ids before touching roster state. When the process record names a persisted config, `recover-session` preflights the persisted recover config and server before roster or process side effects, so manifest drift, duplicate config agents, missing launch evidence, or supervisor-specific preflight refusals stop the operation before stale rows are cleared. It refuses `running` groups with an already-running error, refuses other non-recoverable states such as `stopped` with a `use restart` message, and only then clears that meeting's bound roster rows to `offline` before calling the process supervisor's recover path.

The response reports `status: "ready"` only when the recovered process is `running`, its returned manifest matches every expected agent without extras or duplicates, and every expected agent reports fresh `online` or `working` presence for that same meeting after the stale-presence reset. It reports `status: "starting"` when the process recovered but the returned process manifest or fresh presence evidence is still incomplete before the bounded wait ends. Like restart, a roster row for the same `agent_id` that is currently attached to another meeting is left untouched and reported as `agent_id:wrong_meeting`.

`recover-session --run-remaining-rounds` uses the same ready and optional bound-probe gates as the other session entrypoints: remaining official rounds run only when the recovered session returns `status: "ready"` and any requested `--probe-bound-agents` check passes. Otherwise `auto_rounds.status` is `skipped` with `reason: "session_not_ready"` or `reason: "probe_not_ready"` and no official turn request events are appended. The round options are the same `--round-timeout`, `--max-rounds`, and `--stop-on-timeout` fields used by start/resume/restart.

CLI exit code is `0` for `ready` with no auto-round failure, `1` for `starting` or degraded auto-rounds, and `2` for refused, HTTP, or argument errors. This path does not run official turns unless `--run-remaining-rounds` is supplied, and it never runs smoke probes, model calls outside the called resident official turns, remote bridge `/agentsassemble/run` outside those resident turns, decisions, or transcript finalization.

The operation ledger records one sanitized `session.recover` entry with result status, meeting id, group id, expected/connected counts, offline reset counts, process status, safe agent ids, attention fields, and, when requested, remaining-round counts/statuses. It does not record config paths, command arguments, endpoint URLs, auth refs, prompts, log tails, provider output, replies, or official turn content. Use process-level `live-agent processes recover <group_id>` only when you intentionally want a process control action without the meeting-aware roster reset and readiness proof.

Use `check-session` when a resident meeting already exists and you want a meeting-scoped proof of current readiness without starting, stopping, probing, running official rounds, or calling providers:

```bash
python3 -m agentsassemble.cli live-agent check-session \
  --server http://127.0.0.1:8765 \
  --meeting-id resident-1 \
  --group-id resident-main \
  --fail-on-degraded
```

The HTTP control-plane path is:

```text
POST /api/live-agent-sessions/check
```

with `meeting_id` and `group_id` only. The check reads the existing meeting's `agent_bindings`, reads the current supervised group manifest/status through the process supervisor, and reads live-agent roster presence for the same meeting id. It returns `status: "ready"` only when the group is `running`, its manifest matches every bound agent without extras, and every bound agent is `online` or `working` for that meeting. When comparable timestamps are present, each heartbeat must be at or after the process group's `started_at`; older rows are reported as `agent_id:not_reconnected`. Otherwise it returns `status: "degraded"` with process and connection attention such as `group:stopped`, `agent-a:offline`, `agent-a:wrong_meeting`, `agent-a:not_reconnected`, or `agent-x:extra_in_group`.

The default CLI exit code is `0` for a successful check request, even when the session is degraded, matching the global `health` command's default read-only behavior. Add `--fail-on-degraded` when a script should exit `1` unless the session is `ready`. Transport, argument, and validation failures still exit `2`.

The operation ledger records one sanitized `session.check` entry for this explicit operator check with result status, meeting id, group id, expected/connected counts, process status, safe agent ids, and attention fields. It does not record config paths, command arguments, endpoint URLs, auth refs, prompts, log tails, provider output, replies, or official turn content. Ordinary auto-refresh of `/api/live-agents`, `/api/live-agent-processes`, and `/api/live-agent-health` does not create `session.check` records.

Use `stop-session` when the visible resident meeting already exists and you want one operator action to stop the supervised group and make the roster evidence immediately show that the bound agents are no longer live:

```bash
python3 -m agentsassemble.cli live-agent stop-session \
  --server http://127.0.0.1:8765 \
  --meeting-id resident-1 \
  --group-id resident-main
```

The HTTP control-plane path is:

```text
POST /api/live-agent-sessions/stop
```

with `meeting_id` and `group_id` only. `meeting_id` must name an existing meeting. When the process supervisor can list groups, stop validates that the requested group's agents manifest exactly matches the meeting's bound agent ids before calling `stop_group`, including rejecting duplicate manifest agent ids; a mistyped group id or a group whose manifest belongs to another meeting is refused before any process or roster state is changed. If the group stop call itself fails, bound agents are not marked offline.

After the group is stopped, stop marks only roster rows already bound to that meeting as `offline`. Missing bound rows may be recreated as offline evidence, but a row for the same `agent_id` that is currently attached to another meeting is left untouched and reported in `offline.attention` as `agent_id:wrong_meeting`. The response reports `status: "stopped"` only when the process is no longer running and every expected bound agent was recorded offline for that meeting; otherwise it reports `status: "stopping"` with attention fields for the remaining evidence gap. CLI exit code is `0` for `stopped` and `1` for `stopping`.

The operation ledger records one sanitized `session.stop` entry with result status, meeting id, group id, offline counts, safe agent ids, process status, and attention fields. It does not record config paths, command arguments, endpoint URLs, auth refs, prompts, log tails, provider output, replies, or official turn content.

Use `start-meeting` when you want a normal, visible meeting record that is ready for resident live-agent official turns, instead of a diagnostic smoke meeting:

```bash
python3 -m agentsassemble.cli live-agent start-meeting \
  --server http://127.0.0.1:8765 \
  --meeting-id resident-1 \
  --council-config configs/demo-council.json \
  --agent-config configs/agents.example.json
```

The HTTP control-plane path is:

```text
POST /api/live-agent-meetings/start
```

with `meeting_id`, `council_config_path`, and `agent_config_path`. The server creates `meetings/<meeting_id>/live_state.json`, writes `agenda.md`, appends a meeting status live event, registers the approved `agent_bindings` as live-agent roster entries attached to that meeting, and pins their engagement mode to `moderator_called` so a later resident runner registration cannot accidentally turn official participants into lobby auto-chat agents. Explicit engagement modes from the runtime config are overridden on this start path because resident meeting participants are official-turn agents.

The created meeting is not diagnostic and appears in `/api/meetings`, `/api/meetings/latest`, and the GUI meeting selector. It does not start provider commands, run research, call paid APIs, or synthesize a decision. Start or restart the resident process group separately, or use `start-session` when you want the meeting and group start composed for you, then use `call-round` when the roster is attached and the agents are running.

The operation ledger records a sanitized `meeting.start` entry with meeting id, role count, and bound-agent count only. It does not record config paths, command arguments, endpoints, auth refs, prompts, or provider output.

## Moderator-Called Official Turns

`moderator_called` is for official meeting turns, not lobby auto-chat. A resident runner in this mode ignores lobby reply policy and waits for a moderator request in the agent's meeting live event stream.

Request a turn through the API:

```text
POST /api/meetings/<meeting_id>/live-agent-turns/request
```

with a JSON body like:

```json
{
  "agent_id": "claude-code-live",
  "role_id": "architect",
  "display_name": "Claude Code Live",
  "content": "Give the official architecture recommendation.",
  "turn_id": "round_1:0:architect",
  "turn_index": 0
}
```

The CLI wrapper is:

```bash
python3 -m agentsassemble.cli live-agent call \
  --server http://127.0.0.1:8765 \
  --meeting-id meeting-1 \
  --agent-id claude-code-live \
  --role-id architect \
  "Give the official architecture recommendation."
```

The request appends a `live_agent_turn_request` event to `meetings/<meeting_id>/live_events.jsonl`. That request is `channel: "system"` and `official_record: false`; it is a control event, not transcript evidence.

Automation that needs a bounded completion result can call and wait in one step:

```text
POST /api/meetings/<meeting_id>/live-agent-turns/call
```

or:

```bash
python3 -m agentsassemble.cli live-agent call \
  --server http://127.0.0.1:8765 \
  --meeting-id meeting-1 \
  --agent-id claude-code-live \
  --wait \
  --timeout 30 \
  "Give the official architecture recommendation."
```

The wait path creates the same turn request, then polls the meeting's full `live_events.jsonl` until it finds a verified official reply or reaches the timeout. A reply is accepted only when it is a `kind: "message"` event with `channel: "official"`, `official_record: true`, the requested `actor_id`, and the request event id in `source_event_id`. Lobby messages, wrong-agent replies, wrong-source replies, and generic official messages do not complete the wait. The API returns `status: "answered"` with `request_event`, `reply_event`, timing fields, and visible live events, or `status: "timeout"` with the request event and no fabricated reply. The CLI exits `0` for answered, `1` for timeout, and `2` for transport or validation errors.

Automation can call multiple official turns in order through:

```text
POST /api/meetings/<meeting_id>/live-agent-turns/sequence
```

with:

```json
{
  "timeout_seconds": 30,
  "stop_on_timeout": false,
  "turns": [
    {
      "agent_id": "claude-code-live",
      "role_id": "architect",
      "display_name": "Claude Code Live",
      "content": "Give the first official turn.",
      "turn_id": "round_1:0:architect",
      "turn_index": 0
    }
  ]
}
```

The sequence path validates the meeting and every listed agent/content before appending the first request, then runs request → bounded wait one turn at a time. Repeated agents are allowed because replies are matched by `source_event_id`. Top-level `status` is `answered` when every turn answered, `timeout` when at least one turn timed out and the sequence continued, or `stopped` when `stop_on_timeout` skipped remaining turns. Per-turn status is `answered`, `timeout`, or `skipped`.

The CLI wrapper is:

```bash
python3 -m agentsassemble.cli live-agent call-sequence \
  --server http://127.0.0.1:8765 \
  --meeting-id meeting-1 \
  --turns-file turns.json \
  --timeout 30
```

Use `--turns-json` for inline JSON or `--turns-file` for a JSON array. The CLI exits `0` only when the sequence status is `answered`, exits `1` for `timeout` or `stopped`, and exits `2` for transport or validation errors. The sequence endpoint records one sanitized aggregate `official_turn.sequence` operation with ids, counts, statuses, and timing only. The existing `/api/live-agents/<agent_id>/official-turn` reply endpoint still records its normal sanitized `official_turn.reply` entries when residents answer. Neither operation type records turn prompts, reply content, endpoints, config paths, auth refs, command arguments, or logs.

To run a full official round without hand-writing every turn object, call:

```text
POST /api/meetings/<meeting_id>/live-agent-turns/round
```

with:

```json
{
  "round_id": "round_1",
  "role_ids": ["architect", "critic"],
  "content": "Use the round instructions and answer in your assigned role.",
  "timeout_seconds": 30,
  "stop_on_timeout": false
}
```

The round path reads the meeting record, uses `agent_bindings` as the role-to-agent source of truth, checks current live-agent presence for the same meeting, builds normal sequence turns with `turn_id` values like `round_1:0:architect`, and then delegates to the same request → wait sequence primitive. If `role_ids` is omitted, it uses the matching meeting template round: `selected_roles` uses `speaker_role_ids`, otherwise all meeting roles speak in meeting order. If `content` is omitted, the matching template round instruction is used.

The CLI wrapper is:

```bash
python3 -m agentsassemble.cli live-agent call-round \
  --server http://127.0.0.1:8765 \
  --meeting-id meeting-1 \
  --round-id round_1 \
  --timeout 30
```

Repeat `--role` to override speaker order, for example `--role critic --role architect`. The endpoint records one sanitized `official_turn.round` operation with round id, role ids, request/reply ids, statuses, counts, and timing only; it does not record round instructions, reply content, endpoints, config paths, auth refs, command arguments, or logs.

In the GUI, the Lobby `상주 실행` panel exposes the same moderator-called path as `라운드호출`. It uses the panel's `meeting id`, `official round id`, timeout, and `timeout stop` fields, posts to `/api/meetings/<meeting_id>/live-agent-turns/round`, reports only answered/timed-out/skipped counts in the status line, and asks the meeting view to refresh after a successful call.

To advance a resident meeting without pressing each round manually, call the bounded remaining-round path:

```text
POST /api/meetings/<meeting_id>/live-agent-turns/rounds
```

with:

```json
{
  "timeout_seconds": 30,
  "stop_on_timeout": true,
  "max_rounds": 8
}
```

This path reads the meeting template, skips round ids already recorded in `debate_rounds` with `status: "answered"` using either the `id` or legacy `round` field, runs the remaining template rounds in order through the same single-round primitive, and records answered rounds back into `live_state.json` so future calls and the GUI can advance. If `meeting.json` also exists, live round progress is merged into the read payload without replacing the meeting's roles, template, or provider data. Draft or placeholder round records remain runnable. It is bounded by `max_rounds` and the existing per-turn timeout. The CLI wrapper is `python3 -m agentsassemble.cli live-agent call-remaining-rounds --meeting-id meeting-1 --timeout 30 --max-rounds 8`. The operation ledger records a sanitized `official_turn.rounds` aggregate with round ids, statuses, counts, completed-round counts, and timing only.

The GUI Lobby `상주 실행` panel exposes the same bounded path as `남은라운드`. It uses the panel's `meeting id`, timeout, `max remaining official rounds`, and `timeout stop` fields, then refreshes the selected meeting after the batch returns.

The resident runner answers by posting to:

```text
POST /api/live-agents/<agent_id>/official-turn
```

The server validates that the source event exists in the same meeting, is a `live_agent_turn_request`, and targets the path agent id. The reply is appended as a `kind: "message"` live event with `channel: "official"` and `official_record: true`; it does not write to `lobby.jsonl`. The server uses the request/path metadata for `actor_id`, `role_id`, `display_name`, `turn_id`, and `turn_index`, so an agent reply cannot choose a different official identity by changing its payload. A successful official reply also clears stale `last_error`, updates `last_reply_at`, and advances `last_observed_live_event_id` to the request event id.

Meeting ids for this path must be single meeting directory names, not paths. Encoded slashes, `..`, nested paths, and backslash-style paths are rejected before resolving under `.agentsassemble/meetings`.

Targeted turn requests are visible only to their target agent through `/api/live-agents/<agent_id>/room` and through the official-turn prompt context. Official reply events remain visible to all meeting participants because they are transcript records.

When a running or partial live meeting has official live events but no physical `transcript.md` yet, `build_meeting_payload()` projects the Archive transcript artifact from the full `live_events.jsonl` log at read time. The projection includes only official transcript events with `official_record: true`, `channel: "official"`, `kind: "message"` or `kind: "synthesis"`, and non-empty content. It preserves safe audit metadata such as event id, created-at, actor id, role id, turn id, turn index, and source event id, while excluding turn request content, status/progress events, lobby, side chat, operation details, prompts, and private targeted control text. Existing `transcript.md` files always win, so completed meeting transcripts written by the normal artifact writer are not overwritten by live projection.

Runner cursors are separated: `last_observed_event_id` tracks lobby events, while `last_observed_live_event_id` tracks meeting live events. This keeps an official turn reply from poisoning later lobby auto-reply state if the operator changes the agent back to `always`, `mentioned`, or `human_only`.

Operation history for `official_turn.call` records safe ids, result status, and timing only. It does not include request text, reply text, prompts, endpoints, config paths, auth refs, command arguments, or log tails.

## Config Preflight

Before starting a real provider group, run a local preflight:

```bash
python3 -m agentsassemble.cli live-agent preflight \
  --server http://127.0.0.1:8765 \
  --config configs/live-agents.example.json
```

The GUI "상주 실행" panel exposes the same check as the `예비점검` button through `POST /api/live-agent-preflight`. Preflight is credential-free and does not execute provider commands. It checks that the config can be read, agent ids are unique, resident connection kinds are supported, local command executables are present for `local_cli` and `live_session`, and remote bridge agents have an endpoint plus an available `auth_ref`. The CLI exits `0` for `status: ok`, exits `1` for failed checks, and `--json` prints the same machine-readable report shape returned by the GUI endpoint. It cannot prove Claude, Gemini, Cursor, account login, billing, subscription, model availability, network access, bridge command execution, or native PTY/session readiness.

Resident runners support only `local_cli`, `live_session`, and `remote_bridge`. Registration-only kinds such as `manual` and `codex_resume` can appear in the roster, but `live-agent run`, `live-agent run-group`, supervised start, and preflight reject them as resident process configs instead of silently treating them like a local CLI command.

Direct `live-agent run` and `live-agent run-group` also refuse missing `local_cli` or `live_session` command executables before registering any resident agent. Direct `run-group` rejects duplicate agent ids before worker threads start, so one bad local config cannot partially register sibling agents under an ambiguous id. Supervised process start, restart, and recovery run this same preflight gate automatically inside the local GUI supervisor before opening a log file, launching `run-group`, or clearing meeting roster evidence. A failed gate returns a GUI/API/CLI error immediately and leaves no new process record behind. The GUI status line shows the refusal reason returned by the API. The check runs in the launching process environment, so PATH and `env:` auth references are evaluated from the process that would launch the resident group.

## Provider Runtime Health

Before running a meeting with API, local model, bridge, Codex, or CLI-backed providers, check the host-approved provider config:

```bash
python3 -m agentsassemble.cli providers health \
  --config configs/http-providers.example.json
```

The GUI "본회의 승인" panel exposes the same static check as `Provider 점검` through `POST /api/provider-health` when the current meeting was created from an agent runtime config file. Provider health uses `probe_mode: none`: it parses provider configs, permission profiles, and approved bindings; checks registry availability, auth_ref presence, endpoint requirements, local command executable availability, duplicate ids, and meeting-only permission compatibility; and redacts auth values and command arguments. It does not execute provider commands, does not start a meeting, does not call paid model APIs, and does not contact remote bridges. A passing static report means the config is locally coherent, not that account login, billing, model availability, network reachability, or real provider behavior has been proven.

For LM Studio, Ollama, or another loopback OpenAI-compatible server, operators can opt into a local reachability probe:

```bash
python3 -m agentsassemble.cli providers health \
  --config configs/http-providers.example.json \
  --probe local \
  --probe-timeout 2
```

`--probe local` only sends `GET /models` to `local_openai_compatible` providers whose endpoint is a loopback `http://localhost`, `http://127.0.0.0/8`, or `http://[::1]` URL. It skips API providers, local CLI providers, Codex providers, and remote bridges without executing them. It never calls `/chat/completions`, never sends prompts, never reads auth values, rejects endpoint query strings or non-loopback hosts before making a request, and does not follow redirects or environment proxy settings. A passing local probe proves the local OpenAI-compatible models endpoint is reachable and returns at least one model; it still does not prove generation quality, prompt compliance, paid API access, or remote bridge readiness.

For a friend-owned remote HTTP bridge, operators can opt into a bridge health probe:

```bash
python3 -m agentsassemble.cli providers health \
  --config configs/remote-bridge.example.json \
  --probe bridge \
  --probe-timeout 2
```

`--probe bridge` only sends an authenticated `GET /agentsassemble/health` to `remote_http_bridge` providers. It skips non-bridge providers, does not call `/agentsassemble/run`, sends no prompt or meeting payload, does not execute Claude or any provider command, and does not follow redirects or environment proxy settings. This probe reads the configured `auth_ref` value only in the explicit bridge probe mode so it can set `Authorization: Bearer ...`; the report still redacts the token and endpoint details. A passing bridge probe proves the bridge HTTP server is reachable and accepts the configured token. It does not prove the friend's Claude login, billing, model availability, command execution, prompt compliance, or read-only behavior.

## Credential-Free Operator Smoke

After the GUI room is running, use the one-command smoke before touching real providers:

```bash
python3 -m agentsassemble.cli live-agent smoke \
  --server http://127.0.0.1:8765
```

This command posts a human lobby event, starts a temporary supervised process group through `/api/live-agent-processes/start`, and waits for three fake resident agents to answer:

- `smoke local_cli ok`
- `smoke live_session ok`
- `smoke remote_bridge ok`

The smoke is credential-free: it uses local Python fake agents plus a loopback fake `remote_bridge`, not Claude, Gemini, Cursor, account login, network model calls, or paid provider APIs. It verifies the GUI control plane, lobby event ingestion, supervised `run-group`, one-shot `local_cli`, long-lived JSONL `live_session`, remote bridge resident dispatch, and process cleanup path from the same CLI surface an operator uses.

For a clean doctor run, start the GUI with a temporary `--output-root` and point smoke at that local server. The smoke server must be able to read a temporary config path from the same machine, so treat this as a local GUI diagnostic with a loopback fake bridge rather than proof that a friend's real bridge is reachable.

The GUI "상주 실행" panel exposes the same local diagnostic as the `진단` button. It calls `POST /api/live-agent-smoke`, starts the same temporary fake `local_cli`, `live_session`, and fake `remote_bridge` group, verifies the smoke replies by `source_event_id` and live-agent endpoint evidence, then refreshes the lobby, presence roster, and process records. Use it when you want operator-visible evidence without leaving the room UI.

The GUI also exposes the credential-free official-turn path as `공식진단`. That button runs the same `POST /api/live-agent-official-round-smoke` path as the CLI official round smoke, reports the official reply counts in the status line, and treats any `status` other than `ok` as a failed 공식 라운드 smoke. The adjacent `세션진단` button runs `POST /api/live-agent-session-smoke` without reusing the currently selected meeting or group fields, so each click creates a fresh diagnostic resident session and avoids colliding with a real meeting. Its status line reports the generated diagnostic meeting id, official-round result, pre-restart, post-restart, post-recover, and optional same-session soak lobby reply counts, plus start/check/resume/restart/recover/stop statuses. The two numeric session smoke soak controls in the same `상주 실행` panel are bounded like the CLI options: `0-5` cycles and `0-60` seconds interval. When cycles are left at `0`, the GUI keeps the fast default. When cycles are positive, both `세션진단` and `점검` with `세션 포함` send the same soak controls to the backend.

For the moderator-called official-turn path, run the official round smoke:

```bash
python3 -m agentsassemble.cli live-agent official-round-smoke \
  --server http://127.0.0.1:8765
```

That command calls `POST /api/live-agent-official-round-smoke`. The server creates a diagnostic meeting, binds three fake resident agents to its roles, starts the group in `moderator_called` mode, calls the same `/api/meetings/<meeting_id>/live-agent-turns/round` endpoint used by real operator rounds, waits for official replies, and stops the group. It is still credential-free and omits prompt text, reply text, config paths, endpoint URLs, auth refs, command arguments, tokens, and log tails from the smoke response and operation history.

For the strongest credential-free resident session proof, run the session smoke:

```bash
python3 -m agentsassemble.cli live-agent session-smoke \
  --server http://127.0.0.1:8765 \
  --lobby-probes 2 \
  --soak-cycles 2 \
  --soak-interval 5
```

That command calls `POST /api/live-agent-session-smoke`. The server creates temporary fake council, agent binding, and resident group configs for three resident transports: `local_cli`, JSONL `live_session`, and loopback `remote_bridge`. It calls the same `start-session` path used by the GUI `세션시작` button; runs one bounded remaining official round through `/api/meetings/<meeting_id>/live-agent-turns/rounds`; switches the fake bound agents from moderator-called mode to `always`; posts one or more human lobby probes; verifies all three fake agents replied through the live-agent lobby endpoint with each probe `source_event_id`; calls `check-session`; calls `resume-session`; calls `restart-session`; posts the same number of post-restart human lobby probes; verifies all three restarted fake agents replied with each post-restart probe `source_event_id`; forces only the diagnostic process group into a recoverable `error` or `unknown` state; calls `recover-session`; posts the same number of post-recover human lobby probes; verifies all three recovered fake agents replied with each post-recover probe `source_event_id`; then calls `stop-session` for cleanup. It proves the visible meeting binding, resident process launch, official-turn dispatch, repeated auto lobby reply before restart, after restart, and after recover, meeting-aware readiness check, meeting-aware resume, meeting-aware restart, meeting-aware recover, and meeting-aware stop surfaces in one bounded local operation.

If `--group-id` and `--meeting-id` are omitted, the command generates a fresh diagnostic group and meeting id so the no-argument smoke is safe to rerun. `--lobby-probes` is bounded to 1-5 probes per phase; use `1` for the fast default and a higher value when you want a short soak that proves repeated event observation before restart, after restart, and after recover. `--soak-cycles` is bounded to 0-5 and keeps the same recovered diagnostic session alive for bounded soak cycles before cleanup. Each soak cycle waits `--soak-interval` seconds, runs `check-session`, requires `ready`, posts one fresh human lobby probe, and verifies all three fake agents replied with that probe's `source_event_id`. The session smoke is still credential-free and does not call Claude, Gemini, Cursor, model APIs, a real remote bridge, account login, billing, or external networks. The direct response includes safe diagnostic ids plus counts/statuses; the `session.smoke` operation records soak counts and statuses only: no soak source event ids, reply ids, temporary config paths, command arguments, endpoint URLs, auth refs, prompts, reply text, provider output, tokens, or log tails.

Use `--json` when another script needs machine-readable evidence:

```bash
python3 -m agentsassemble.cli live-agent smoke \
  --server http://127.0.0.1:8765 \
  --group-id operator-smoke \
  --timeout 12 \
  --json
```

## Operator Readiness Doctor

Use the doctor command when you want one operator-verifiable readiness answer instead of mentally combining health and smoke:

```bash
python3 -m agentsassemble.cli live-agent doctor \
  --server http://127.0.0.1:8765
```

The doctor calls `POST /api/live-agent-readiness`. The readiness endpoint first records the current `/api/live-agent-health` snapshot, then runs the same credential-free smoke used by `live-agent smoke`. This order is intentional: the smoke leaves offline fake agents and a stopped smoke process record behind, so readiness uses pre-smoke health as the room health proof and treats the smoke result as a separate control-plane proof.

The default doctor text mirrors the important health attention surfaces: agent attention, process attention, connection attention, and sanitized process watchdog reasons. This keeps missing, stale, wrong-meeting, offline, error, or not-reconnected manifest agents visible in the single readiness answer without opening raw health JSON.

When you also need the moderator-called official-turn path in the same operator answer, opt into the credential-free official round smoke:

```bash
python3 -m agentsassemble.cli live-agent doctor \
  --server http://127.0.0.1:8765 \
  --official-round-smoke
```

`--official-round-smoke` adds an official-turn smoke check inside the same readiness payload after the regular local CLI, live session, and remote bridge smoke passes. It uses the same fake diagnostic official round as `live-agent official-round-smoke`, does not call real providers, and makes readiness `failed` if the official round smoke is skipped, timed out, fails cleanup, or returns a non-`ok` status. The readiness response and operation history include bounded counts, ids, statuses, and timing only; they omit official prompts, reply text, config paths, endpoint URLs, auth refs, command arguments, tokens, and log tails.

The official round smoke creates diagnostic official-round smoke meetings so the request/reply event evidence remains inspectable by direct meeting id and operation history. Those diagnostic meetings do not appear in `/api/meetings` or `/api/meetings/latest`, so a smoke run cannot replace the operator's latest real meeting in the normal GUI/archive surface.

When you need the strongest credential-free resident session proof in the same operator answer, opt into the full session smoke:

```bash
python3 -m agentsassemble.cli live-agent doctor \
  --server http://127.0.0.1:8765 \
  --session-smoke
```

`--session-smoke` runs the full `live-agent session-smoke` path inside readiness after the regular local CLI, live session, and remote bridge smoke passes. It creates a fresh diagnostic resident session instead of reusing the doctor's smoke group id, verifies start-session, one official round, check-session, resume-session, restart-session, recover-session, stop-session, and pre-restart/post-restart/post-recover lobby replies. The readiness response and `readiness.check` operation history expose only safe session smoke counts and statuses such as reply counts, post-restart and post-recover reply counts, soak check statuses, and recover status. They omit source event ids, reply arrays, reply text, temporary config paths, endpoint URLs, auth refs, command arguments, provider output, tokens, and log tails. If the base smoke fails, session smoke is reported as `skipped` and is not run.

For a stronger but slower readiness proof, add `--session-smoke-soak-cycles N` and optionally `--session-smoke-soak-interval SECONDS` with `--session-smoke`. These namespaced flags forward to the diagnostic session smoke and keep the same recovered diagnostic session alive for bounded soak cycles. Readiness and operation history expose only the resulting soak cycle count, reply count, and check statuses.

By default, doctor stays credential-free. Add `--probe-agent <agent_id>` only when you explicitly want opt-in targeted resident probes against already-running agents after smoke passes:

```bash
python3 -m agentsassemble.cli live-agent doctor \
  --server http://127.0.0.1:8765 \
  --probe-agent claude-code-live \
  --probe-agent gemini-cli
```

To probe every agent from a supervised process group, use the group's launch-time manifest:

```bash
python3 -m agentsassemble.cli live-agent doctor \
  --server http://127.0.0.1:8765 \
  --probe-group resident-main
```

`--probe-group` expands the requested group from `/api/live-agent-processes` safe manifest entries, then de-dupes those agent ids with any explicit `--probe-agent` values. The manifest is launch-time evidence from the config the supervisor started with; it does not reread an edited config file. Missing groups, stopped groups, manifestless groups, and requests that expand above the probe cap are refused as `failed` instead of silently probing a subset.

These probes use the same `POST /api/live-agents/<agent_id>/probe` path described below. They can make a real resident runner call its configured local CLI, JSONL session, or remote bridge, so treat them as real-provider checks rather than credential-free smoke. A single readiness request accepts up to 10 targeted probe agents after explicit and group-expanded ids are merged; requests above that limit are refused as `failed` instead of silently probing a subset. The readiness payload includes bounded smoke, probe group, probe status, count, and event id evidence, but omits reply message text, config paths, endpoint URLs, log paths, auth refs, prompts, and log tails from both the readiness response and readiness operation history.

Smoke-created fake agents and process groups are marked `diagnostic`. They remain visible in `.agentsassemble/live_agents.json` and `.agentsassemble/live-agent-runs/processes.json` for operator inspection, but `/api/live-agent-health` ignores diagnostic records so a successful doctor run does not contaminate later health checks or repeated readiness checks. Legacy smoke artifacts from before the `diagnostic` flag are also ignored when their preserved agent identity matches the built-in `Smoke Local CLI` or `Smoke Live Session` diagnostic agents.

Status meanings:

- `ready`: pre-smoke health is `ok`, the fake `local_cli`, `live_session`, plus `remote_bridge` smoke passed, and every requested targeted probe replied.
- `degraded`: smoke passed, every requested official/session smoke and targeted probe replied, but pre-smoke health already had agent, process, or connection attention.
- `failed`: the room was reached, but the base smoke check did not pass, requested official/session smoke was skipped, timed out, or failed, or a requested targeted probe was skipped, timed out, or failed.

For scripts:

```bash
python3 -m agentsassemble.cli live-agent doctor \
  --server http://127.0.0.1:8765 \
  --group-id doctor-smoke \
  --timeout 12 \
  --json
```

Exit code contract:

- `0`: readiness status is `ready`.
- `1`: the server returned a readiness payload, but status was `degraded` or `failed`.
- `2`: the CLI could not fetch or parse the readiness response, or the command arguments were invalid.

The GUI "상주 실행" panel exposes the same readiness path as the `점검` button. Use `진단` for a raw smoke run and `점검` when you want the combined health-plus-smoke answer.

GUI `점검` can include the same official-turn smoke check as `--official-round-smoke`: enable `공식 포함` before pressing `점검`. When unchecked, the GUI keeps the default health-plus-smoke readiness path and does not run the official round smoke. Use `라운드호출` only after a real meeting and resident roster are ready; it calls the real meeting round endpoint rather than the credential-free smoke endpoint.

GUI `점검` can also include the full session smoke check as `--session-smoke`: enable `세션 포함` before pressing `점검`. This is intentionally separate from the standalone `세션진단` button; the checkbox folds the same strong diagnostic into the combined readiness answer, while `세션진단` runs the session smoke as its own operation.

## Targeted Resident Reply Probe

Use `live-agent probe` when a resident agent is already registered as live and you want proof that it can observe a new room event and reply through its own runner path:

```bash
python3 -m agentsassemble.cli live-agent probe \
  --server http://127.0.0.1:8765 \
  --agent-id claude-code-live \
  --timeout 12
```

The probe calls `POST /api/live-agents/<agent_id>/probe`. It does not directly invoke providers, does not start a process, and does not change engagement policy. Instead it appends one visible diagnostic lobby message addressed to the agent id and display name, then waits for a reply whose `actor_id` matches the agent, whose `source_event_id` matches the probe event, and whose event was written through `/api/live-agents/<agent_id>/lobby`. Generic `/api/lobby` posts cannot set the internal `live_agent_endpoint` evidence flag. Session-level `--probe-bound-agents` uses the same probe evidence, but may temporarily switch default `moderator_called` residents to `human_only` and restore them so a real session can answer the diagnostic human lobby event.

Exit code contract:

- `0`: the agent replied with matching `source_event_id`.
- `1`: the agent existed but was skipped or timed out.
- `2`: the CLI could not reach or parse the server response, or the agent was not found.

Agents in `watch`, `manual`, `moderator_called`, cooldown, provider failure, or remote bridge failure can time out even if their process is alive. Treat timeout as a targeted reply failure, not as proof that the process is dead. The GUI roster exposes the same check as the per-agent `probe` button.

Probe wait time is capped at 60 seconds. The CLI keeps its HTTP request timeout longer than the probe wait window, so an ordinary probe timeout returns the JSON `timeout` result instead of a transport failure.

## Fake CLI Smoke

Use a fake CLI first. It proves the resident loop, room polling, heartbeat, lobby reply, log capture, and stop path without depending on Claude, Gemini, auth, network, or paid model calls.

Post a human lobby message first, then run one bounded resident agent:

```bash
python3 -m agentsassemble.cli live-agent run \
  --server http://127.0.0.1:8765 \
  --agent-id fake-live-one \
  --display-name "Fake CLI" \
  --provider-kind local_cli \
  --connection-kind local_cli \
  --engagement-mode always \
  --poll-interval 1 \
  --heartbeat-interval 5 \
  --cooldown 0 \
  --max-chain-depth 1 \
  --max-ticks 5 \
  --command python3 -c "import sys; sys.stdin.read(); print('Fake CLI saw the room event.')"
```

In the lobby, post a human message such as:

```text
fake-live-one, respond once
```

Expected result: `Fake CLI` appears in the live-agent roster, reads the lobby event, and posts one lobby reply. Because `--max-ticks 5` is bounded, the command exits after the smoke window and sends an offline heartbeat.

## Fake Live Session Smoke

Use `--connection-kind live_session` when the resident agent command speaks the AgentsAssemble JSONL session protocol. Unlike `local_cli`, this keeps one local process alive for the resident runner and sends multiple room events through that same stdin/stdout bridge.

This connection kind is for `live-agent run` and `live-agent run-group`. The one-shot `live-agent delegate` command keeps plain local CLI semantics and does not use the JSONL session envelope. When `delegate` posts its one reply, it links the lobby message to the latest unobserved non-self lobby event from the room snapshot with `source_event_id` and increments `auto_chain_depth`, so the reply still has the same traceable event lineage as resident runner replies. If only self-authored or already-observed events remain, the one-shot reply is still posted but no stale source event is attached. If the delegate command fails, times out, or returns an empty reply after the working heartbeat, the CLI makes a best-effort `error` heartbeat with a compact `last_error` instead of leaving the roster stuck at `working`. That heartbeat does not copy command stdout/stderr, and OS launch failures store the error category without the failing path.

Protocol:

- request JSONL: `{"request_id": "...", "prompt": "..."}`
- response JSONL: `{"request_id": "...", "message": "one lobby reply"}`

The subprocess must write only response JSONL to stdout. Diagnostic logs belong on stderr; the runner drains stderr separately and keeps only a bounded tail for error reporting.

On POSIX hosts, the JSONL live-session subprocess starts in its own process group. Closing a session, timing out while waiting for a reply, or timing out while writing a blocked request terminates that process group so ordinary child processes created by a wrapper are cleaned up with the session.

Post a human lobby message first, then run a bounded stateful fake session:

```bash
python3 -m agentsassemble.cli live-agent run \
  --server http://127.0.0.1:8765 \
  --agent-id fake-jsonl-session \
  --display-name "Fake JSONL Session" \
  --provider-kind local_cli \
  --connection-kind live_session \
  --engagement-mode always \
  --poll-interval 1 \
  --heartbeat-interval 5 \
  --cooldown 0 \
  --max-chain-depth 1 \
  --max-ticks 5 \
  --command python3 -u -c "import json, sys; count=0
for line in sys.stdin:
    payload=json.loads(line); count += 1
    print(json.dumps({'request_id': payload['request_id'], 'message': f'Fake JSONL state {count}'}), flush=True)"
```

Expected result: the first eligible lobby event gets `Fake JSONL state 1`. A later eligible lobby event in the same bounded run gets `Fake JSONL state 2`, proving the same process stayed alive. This is a JSONL bridge for local subprocesses, not a native Claude, Gemini, or Cursor PTY protocol.

If the JSONL subprocess exits, times out, stops reading stdin, or returns invalid protocol output, the resident runner records an `error` heartbeat with `last_error`, closes that subprocess, and starts a fresh subprocess for the next eligible event after the normal cooldown gate.

## Remote Bridge Resident Smoke

Use `--connection-kind remote_bridge` when a friend-owned bridge is already running and authenticated. Unlike `--probe bridge`, this is not health-only: the resident runner polls the local GUI room, sends one eligible lobby event plus recent lobby context to the bridge's `/agentsassemble/run` endpoint, then posts the returned message back through the same live-agent lobby endpoint used by local runners.

The remote bridge runner still owns the local safety guards:

- self-authored events are skipped by `actor_id`;
- already observed events are skipped by `last_observed_event_id`;
- chain depth is capped by `--max-chain-depth`;
- bridge request failures, command timeouts, and non-zero bridge command return codes become sanitized `error` heartbeats instead of lobby messages, and the runner retries after cooldown.

Post a human lobby message first, then run one bounded remote bridge resident:

```bash
python3 -m agentsassemble.cli live-agent run \
  --server http://127.0.0.1:8765 \
  --agent-id friend-claude-live \
  --display-name "Friend Claude" \
  --provider-kind claude_code \
  --connection-kind remote_bridge \
  --endpoint http://100.64.0.10:8777 \
  --auth-ref env:AGENTSASSEMBLE_BRIDGE_TOKEN \
  --engagement-mode always \
  --poll-interval 2 \
  --heartbeat-interval 30 \
  --cooldown 5 \
  --max-chain-depth 1 \
  --max-ticks 5
```

For a supervised group, use:

```bash
python3 -m agentsassemble.cli live-agent run-group \
  --config configs/live-agents.remote-bridge.example.json \
  --server http://127.0.0.1:8765 \
  --max-ticks 5
```

Do not use a redacted public meeting artifact as the resident auth source. The runner needs a real `env:` or `literal:` `auth_ref`; an env var whose value is `<redacted>` is treated as unavailable. Keep bridge credentials out of endpoint URLs: resident bridge endpoints must be plain HTTP(S) URLs without userinfo, query strings, or fragments. The runner does not register, heartbeat, or log the token value.

## Fake Group Smoke

Use a temporary two-agent config when you want to verify `run-group` without real providers:

```bash
fake_config="$(mktemp)"
cat > "$fake_config" <<'JSON'
{
  "server": "http://127.0.0.1:8765",
  "poll_interval": 1,
  "heartbeat_interval": 5,
  "cooldown": 0,
  "max_chain_depth": 1,
  "agents": [
    {
      "agent_id": "fake-alpha",
      "display_name": "Fake Alpha",
      "provider_kind": "local_cli",
      "connection_kind": "local_cli",
      "engagement_mode": "always",
      "command": ["python3", "-c", "import sys; sys.stdin.read(); print('Fake Alpha saw the room event.')"],
      "timeout_seconds": 10
    },
    {
      "agent_id": "fake-beta",
      "display_name": "Fake Beta",
      "provider_kind": "local_cli",
      "connection_kind": "local_cli",
      "engagement_mode": "always",
      "command": ["python3", "-c", "import sys; sys.stdin.read(); print('Fake Beta saw the room event.')"],
      "timeout_seconds": 10
    }
  ]
}
JSON

python3 -m agentsassemble.cli live-agent run-group \
  --config "$fake_config" \
  --server http://127.0.0.1:8765 \
  --max-ticks 3
```

Post one human lobby message before or during the run. Expected result: both `fake-alpha` and `fake-beta` register, reply through the lobby, and exit cleanly when the bounded ticks finish.

## Start A Group From The GUI

Use the GUI "상주 실행" panel for supervised process records and stop controls. For fake smoke, point the config path at your temporary fake config. For the real example config, complete the Claude and Gemini checklist first.

- config path: the temporary fake config, or `configs/live-agents.example.json` after real-provider approval
- group id: optional, for example `local-cli-group`
- auto restart: optional; auto restart is off by default
- max restarts: bounded retry count written as `max_restarts`
- restart backoff: seconds between crash detection and relaunch, written as `restart_backoff_seconds`
- stale watchdog: optional heartbeat timeout written as `stale_restart_after_seconds`; it requires auto restart with a positive restart budget
- press `시작`

The GUI start button runs the same resident group through the local process supervisor. It preflights the config first, then launches only when the config passes. Group records and log tails remain visible after a launched process stops or crashes. Auto restart only applies to a group launched with that option enabled, and it starts a fresh local process rather than attaching to an old PID.

When the stale watchdog is enabled, the supervisor waits until the launched group has been alive longer than the configured timeout, compares the launch-time agent manifest against current live-agent presence, and stops/restarts the owned group if any manifest agent is missing, stale, offline, error, or attached to the wrong meeting. The watchdog is opt-in and uses the same bounded auto-restart budget/backoff path as crash recovery, so health and readiness reads stay read-only. To avoid killing a quiet but healthy runner, each agent must have a positive `heartbeat_interval`, and the watchdog threshold must be greater than that agent's `heartbeat_interval + poll_interval`.

If the GUI server restarts while a group record still says `running`, the new supervisor marks that historical record `unknown` because it does not attach to old PIDs. Use the GUI `복구` button or CLI `python3 -m agentsassemble.cli live-agent processes recover <group-id>` to start a fresh supervised process from that record's persisted config/server/options. Recovery is distinct from `restart`: it is intended for `unknown` or `error` records, returns `recovered_from_status`, and records sanitized `process.recover` and lifecycle `recovered` evidence. Those records include safe ids, statuses, restart counts, and previous status only; they do not include config paths, command arguments, endpoints, auth refs, prompts, provider output, replies, or log tails in operation details.

The same supervised start path is available from the CLI:

```bash
python3 -m agentsassemble.cli live-agent processes start \
  --server http://127.0.0.1:8765 \
  --config "$fake_config" \
  --group-id local-cli-group
```

For auto restart, pass both `--auto-restart` and a positive `--max-restarts` value:

```bash
python3 -m agentsassemble.cli live-agent processes start \
  --server http://127.0.0.1:8765 \
  --config "$fake_config" \
  --group-id local-cli-group \
  --auto-restart \
  --max-restarts 2 \
  --restart-backoff-seconds 5 \
  --stale-restart-after-seconds 120
```

Here `--server` is the GUI API target and the room server URL passed to the supervised `run-group`.

The GUI autostart path accepts the same watchdog threshold with:

```bash
python3 -m agentsassemble.cli gui \
  --live-agent-config "$fake_config" \
  --live-agent-auto-restart \
  --live-agent-max-restarts 2 \
  --live-agent-stale-restart-after-seconds 120
```

## Stop Or Restart A Group

Prefer the GUI stop button for a running group. The HTTP stop path is also available:

```bash
curl -X POST \
  -H 'Content-Type: application/json' \
  -d '{}' \
  http://127.0.0.1:8765/api/live-agent-processes/local-cli-group/stop
```

After a successful process stop, the supervisor reconciles existing live-agent presence rows for that group's launch-time manifest and marks matching agents `offline` immediately. This prevents a killed or interrupted resident process from looking `online` until heartbeat staleness expires. The stop response includes an output-only offline reconciliation summary with expected/offline/skipped counts, safe `offline_agent_ids`, and attention entries for skipped manifest agents. The reconciliation is conservative: it does not create new presence rows, does not touch agents currently attached to another meeting, and does not mark an agent offline while another running or restarting group with the same meeting id still expects that agent.

Use the GUI restart button on a stopped, crashed, or recovered group to relaunch it from the persisted `config_path` and `server`. Restart also reruns preflight before launching, so a config or environment that became invalid while the group was down is refused synchronously. The HTTP restart path is:

```bash
curl -X POST \
  -H 'Content-Type: application/json' \
  -d '{}' \
  http://127.0.0.1:8765/api/live-agent-processes/local-cli-group/restart
```

The CLI equivalents are:

```bash
python3 -m agentsassemble.cli live-agent processes list \
  --server http://127.0.0.1:8765

python3 -m agentsassemble.cli live-agent processes stop local-cli-group \
  --server http://127.0.0.1:8765

python3 -m agentsassemble.cli live-agent processes restart local-cli-group \
  --server http://127.0.0.1:8765
```

When the room has more than one resident group, use the GUI `실행중지` button or the bulk CLI/API path to stop every currently running group and cancel pending auto-restarts in one operator action. This does not attempt to signal historical `unknown`, `error`, or already `stopped` records that the current supervisor does not own.

```bash
python3 -m agentsassemble.cli live-agent processes stop-running \
  --server http://127.0.0.1:8765

curl -X POST \
  -H 'Content-Type: application/json' \
  -d '{}' \
  http://127.0.0.1:8765/api/live-agent-processes/stop-running
```

The bulk stop response reports `stopped_count`, `failed_count`, `skipped_count`, the corresponding safe group records, and each stopped record's offline reconciliation summary when a manifest was present. The operation ledger records `process.stop_running` with counts, safe group ids, offline counts, safe `offline_agent_ids`, and compact offline attention only; it does not record config paths, server URLs, commands, auth refs, log tails, prompts, or provider output.

Add `--json` to any process CLI command to print the raw HTTP payload.

The process CLI uses exit code `0` for successful supervisor requests, even when a listed group is `stopped`, `error`, or `unknown`. It uses exit code `2` for argument validation, connection failures, invalid JSON, HTTP errors, missing config files, unknown group ids, and refused restarts.

The supervisor only stops group ids it launched in the current GUI process. It does not control arbitrary OS PIDs. Historical records from a previous GUI process are shown as `unknown`, `stopped`, or `error`, but are not treated as externally stoppable PIDs. Restarting a historical record starts a fresh local process from the saved config and server instead of attaching to the old PID. A manual restart resets the auto-restart counter for the new run while preserving the configured retry policy.

On POSIX hosts, supervised `run-group` processes start in their own process group. If a supervised group does not exit after SIGINT, the supervisor escalates stop signals to that process group so ordinary child processes created by the resident group are cleaned up with the failed parent.

Resident `local_cli` workers use an interruptible subprocess runner. When the supervised group receives SIGINT during stop, active local provider commands are terminated through that runner instead of waiting for the provider command timeout. The same cleanup path is used when a provider command hits its timeout. On POSIX hosts, resident local CLI commands start in their own process group so ordinary child processes created by provider wrappers are stopped with the same command. Shutdown-related secondary worker errors are suppressed after the group stop flag is set, so the process record preserves the original failure or operator stop result.

## Inspect Runtime State

Default files under `--output-root .agentsassemble`:

```text
.agentsassemble/live_agents.json
.agentsassemble/lobby.jsonl
.agentsassemble/live-agent-runs/processes.json
.agentsassemble/live-agent-runs/events.jsonl
.agentsassemble/live-agent-runs/operations.jsonl
.agentsassemble/live-agent-runs/<group_id>.log
```

What to check:

- `.agentsassemble/live_agents.json`: presence, status, heartbeat metadata, `last_error`, `last_reply_at`, `last_observed_event_id`, and `last_observed_live_event_id`.
- `/api/live-agents`: roster entries add output-only `heartbeat_age_seconds` and `stale_after_seconds` so operators can see why an agent is fresh or stale. These freshness fields are inferred at read time and are not persisted in `live_agents.json`. The GUI live-agent card shows both the lobby `cursor` from `last_observed_event_id` and the `official cursor` from `last_observed_live_event_id`, so external/manual agents can prove which room event and official meeting event they last observed.
- Slow resident replies keep sending `working` heartbeats while the provider command is still in flight, so a long Claude, Gemini, local CLI, live session, or remote bridge turn does not look stale merely because it is still answering.
- `.agentsassemble/lobby.jsonl`: human lobby messages and live-agent replies. Live-agent auto replies include `actor_id`, `source_event_id`, `auto_chain_depth`, and the server-issued `live_agent_endpoint` evidence flag when they were posted through the resident live-agent lobby endpoint.
- `.agentsassemble/live-agent-runs/processes.json`: durable group records with `group_id`, `status`, `pid`, `config_path`, `server`, `log_path`, timestamps, `returncode`, `last_error`, and a safe launch-time `agents` manifest.
- agents manifest entries contain only `agent_id`, `display_name`, `provider_kind`, and `connection_kind`. The manifest does not include command arguments, endpoint URLs, auth references, command paths, prompts, or environment-derived values.
- `/api/live-agent-processes`: process rows add output-only `agent_connection` evidence by comparing each group's launch-time manifest with current live-agent presence. This manifest-aware connection evidence reports `expected`, `connected`, and attention entries such as missing, wrong-meeting, not-reconnected, stale, offline, or error agents. A presence row whose `last_seen_at` is older than the process group's `started_at` is `not_reconnected` rather than connected, so stale pre-restart heartbeats do not prove the fresh process is attached. It is not persisted into `processes.json`.
- The default CLI process list and GUI process rows show the latest lifecycle offline summary beside the last event label, such as `last event restart_scheduled, offline 1/2, wrong_meeting agent-b`, so crash-time roster reconciliation evidence is visible without opening JSON. They also show the latest sanitized watchdog reason from recent lifecycle history, such as `last reason stale_watchdog missing manifest agent agent-a`, when the latest row event did not carry that reason itself.
- The GUI `상주 실행` panel reads `/api/live-agent-health` during runtime refresh and shows the backend health snapshot directly: overall status, live/total agents, running/total process groups, connected/expected manifest agents, ready/total meeting-owned sessions, the combined attention count, and a compact `session attention` reason when meeting-owned session health is degraded. This is the same read-only health summary used by `assemble live-agent health`, so diagnostic smoke artifacts stay excluded and manifest-aware connection gaps remain visible without mentally merging roster and process rows.
- auto-restart fields in `processes.json`: `auto_restart`, `restart_count`, `max_restarts`, `restart_backoff_seconds`, `stale_restart_after_seconds`, and `next_restart_at`.
- `.agentsassemble/live-agent-runs/events.jsonl`: safe lifecycle event history for supervised groups. It records bounded operator facts such as `started`, `stopped`, `error`, `restart_scheduled`, `restart_failed`, `stale_watchdog`, `stale_watchdog_stop_failed`, and `recovered_unknown` with `timestamp`, `group_id`, `status`, `pid`, `returncode`, and restart counters. Stale-watchdog events include a sanitized `reason` such as a missing, stale, offline, error, or wrong-meeting manifest agent so bounded lifecycle history still explains why a live group was restarted. Lifecycle stop, error, `restart_scheduled`, and `restart_failed` events can include offline reconciliation summaries with expected/offline/skipped counts, safe `offline_agent_ids`, and compact attention entries. The process API and GUI expose each group's bounded `recent_events` view, and `/api/live-agent-process-events` exposes a bounded sanitized lifecycle history for scripts. Lifecycle events do not include command arguments, endpoint URLs, auth references, command paths, prompts, log tails, provider output, or environment-derived values.
- `.agentsassemble/live-agent-runs/operations.jsonl`: safe control-operation history for API, GUI, and CLI operator actions. It records bounded entries for process start/stop/restart, engagement updates, official turn requests/replies, preflight checks, smoke runs, and readiness checks, including success, degraded, and refused/failed attempts. The operation ledger does not include command arguments, endpoint URLs, auth references, prompts, log tails, config paths, environment-derived values, provider secrets, or official turn content. Ordinary heartbeat polling and health reads are intentionally not operation records.
- `.agentsassemble/live-agent-runs/<group_id>.log`: stdout/stderr for the supervised `run-group` process. Delegate provider subprocess stdout/stderr is captured by the runner, not streamed directly into this file. The GUI and process API expose only a bounded `log_tail`.

Recent operation views read from the JSONL tail and stop after the requested result window. Recent lifecycle event queries also read from the JSONL tail and stop after the requested result window, with an optional safe group-id filter and a separate `scan_limit` budget for how many recent lifecycle events may be considered before the query stops. If `truncated` is true, the scan budget was exhausted and older matching events may still exist outside the searched tail window. Process rows split bounded `recent_events` by group from the same recent lifecycle tail instead of iterating the full history file; a quiet group with only old lifecycle events may show an empty compact row history, and operators should use `live-agent processes events --group-id <id> --scan-limit <n>` for deeper inspection. Room snapshots used by resident polling and GUI refresh read bounded lobby, side-chat, and live-event tails for their default limited views instead of loading whole JSONL files. Full live-event reads remain available through `read_live_events(..., limit=None)` for archive and transcript reconstruction paths that explicitly need complete meeting history. Long sessions can keep historical JSONL artifacts without forcing operator history queries, room polling, GUI refresh, or process rows to load the whole file or rescan lifecycle history once per group.

The recent process lifecycle history is available through HTTP:

```bash
curl 'http://127.0.0.1:8765/api/live-agent-process-events?limit=20'
curl 'http://127.0.0.1:8765/api/live-agent-process-events?group_id=local-cli-group&limit=20&scan_limit=1000'
```

and through the CLI:

```bash
assemble live-agent processes events \
  --server http://127.0.0.1:8765 \
  --group-id local-cli-group \
  --limit 20 \
  --scan-limit 1000
```

The module form is equivalent when running from a checkout:

```bash
python3 -m agentsassemble.cli live-agent processes events \
  --server http://127.0.0.1:8765 \
  --limit 20 \
  --json
```

Use the lifecycle event view to answer what the supervised process did over time without opening `events.jsonl`. The default CLI output shows timestamp, group id, event type, process status, pid or return code, restart counters, next restart time, sanitized watchdog restart reason, and compact offline reconciliation evidence when present. A stale-watchdog row can therefore include `reason stale manifest agent agent-a` without requiring `--json`. When the scan is truncated, the CLI prints `searched recent N lifecycle events; older matches may exist` so an empty filtered result is not mistaken for complete proof that no older event exists. Use `--json` when an operator script needs the full sanitized event payload, including `limit`, normalized `group_id`, `scan_limit`, `scanned_event_count`, and `truncated`.

The recent operation history is available through the GUI "최근 작업" list, through HTTP:

```bash
curl 'http://127.0.0.1:8765/api/live-agent-operations?limit=20'
```

and through the CLI:

```bash
assemble live-agent operations list \
  --server http://127.0.0.1:8765 \
  --limit 20
```

The module form is equivalent when running from a checkout:

```bash
python3 -m agentsassemble.cli live-agent operations list \
  --server http://127.0.0.1:8765 \
  --limit 20 \
  --json
```

The GUI list and the default CLI output include compact safe `details` values, such as readiness result status, reply counts, probe ids, or restart settings. For `session.smoke` and `readiness.check`, those compact details prioritize high-signal liveness evidence such as reply counts, post-restart and post-recover counts, session-smoke soak cycle/reply counts, and soak check statuses before lower-value identifiers. For `session.start`, `session.resume`, `session.restart`, and `session.recover`, the compact rows prioritize connected-agent counts, bound-agent reply probe status, and optional auto-round status/reason/counts before lower-value identifiers, so long-session proof remains visible in the recent operation rows. Use `--json` when an operator script needs the full sanitized operation payload.

Use the operation ledger to answer "what control action happened" and the process lifecycle events to answer "what did the supervised process do next." They are deliberately separate surfaces.

For scriptable monitoring, fetch the combined health summary:

```bash
curl http://127.0.0.1:8765/api/live-agent-health
```

The same health payload is available through the CLI for terminal checks and local monitors:

```bash
python3 -m agentsassemble.cli live-agent health \
  --server http://127.0.0.1:8765 \
  --fail-on-degraded
```

Add `--json` when another script needs the raw response instead of the compact operator summary.

Exit code contract:

- `0`: health was fetched and either reported `ok`, or reported `degraded` without `--fail-on-degraded`.
- `1`: health reached the server but reported non-`ok` while `--fail-on-degraded` was set.
- `2`: the CLI could not fetch or parse the health response, or the command arguments were invalid.

The response reports overall `status` as `ok` or `degraded`, plus `agents.counts`, `agents.attention`, `processes.counts`, `processes.attention`, safe `processes.reasons`, manifest-aware `connections` evidence, and meeting-owned session readiness in `sessions.items`. Each session item is read-only and includes safe `meeting_id`, `group_id`, `status`, `process_status`, expected/connected counts, ownership attention, process attention, connection attention, and combined attention. Health marks a session `ready` only when the stored meeting exists, the process group is `running`, the process manifest matches that meeting's bound agents, every bound agent has fresh `online` or `working` presence for the same meeting, and no other non-diagnostic `running` or `restarting` group owns that same meeting. Duplicate active meeting ownership degrades each active session item with `meeting:duplicate_active_group`, so an old shadow process cannot look ready beside the real resident group. Missing or malformed meetings degrade that session instead of failing the health read, and the payload does not echo config paths, command arguments, endpoint URLs, auth refs, prompts, log tails, provider output, or replies. A non-diagnostic process group that is restarting, error, unknown, or stopped can include a compact sanitized watchdog reason such as `stale_watchdog missing manifest agent agent-a` in `processes.reasons` and the matching session item, so `live-agent health` and `live-agent doctor` explain high-level process attention without opening lifecycle JSON. Suspicious or non-watchdog reason strings are dropped from health. A non-diagnostic running process group with manifest agents that are missing, attached to a different meeting, not reconnected after the group start, stale, offline, or error adds `connection attention` and degrades health. Diagnostic smoke groups are ignored so repeated doctor checks do not contaminate readiness.

This endpoint is a read-only snapshot. It does not refresh process handles, launch due auto-restarts, stop groups, or mutate process state.

The GUI event streams are also bounded failure surfaces. If a meeting disappears after a `/api/meetings/<meeting_id>/events` SSE connection is already open, including during a payload file read, the server sends one `event: error` SSE payload with the `stream`, `meeting_id`, and error message, then closes that connection instead of leaking a handler traceback. A request for a missing meeting before the stream opens still returns the ordinary JSON `404`.

External or manually driven agents can also report an error heartbeat through the CLI:

```bash
python3 -m agentsassemble.cli live-agent heartbeat \
  --server http://127.0.0.1:8765 \
  --agent-id claude-code-live \
  --status error \
  --last-error "delegate command failed" \
  --last-observed-event-id evt1 \
  --last-observed-live-event-id live_evt1 \
  --last-reply-at 2026-05-17T12:00:00+00:00 \
  --json
```

That command writes the same `last_error`, `last_observed_event_id`, `last_observed_live_event_id`, and `last_reply_at` fields used by resident runners, so non-runner agents can stay visible in the roster without inventing a separate status path. Presence `last_error` is sanitized before persistence/readback; URLs, scheme-less host/path endpoints, auth/token assignments, env/literal refs, config paths, prompt refs, absolute paths, and common secret-looking values are replaced with `Live-agent presence error details redacted.` before the roster or GUI can expose them. Safe short labels such as `command failed`, `oauth failed`, or `configuration failed` remain visible for operators. Use `--json` when an external agent wrapper needs to verify the persisted heartbeat acknowledgement, including both cursor fields, instead of parsing the compact `agent-id: status` line.

## Claude And Gemini CLI Smoke

Only run real provider smoke when the CLI is installed, authenticated, and you have explicit approval for any cost, network, account, or external side effect.

Check local availability:

```bash
command -v claude
command -v gemini
```

If approved and installed, `configs/live-agents.example.json` shows the expected command shape:

```json
["claude", "-p"]
["gemini"]
```

After approval, start a bounded real-provider group:

```bash
python3 -m agentsassemble.cli live-agent run-group \
  --config configs/live-agents.example.json \
  --server http://127.0.0.1:8765 \
  --max-ticks 2
```

Provider command failures are recorded on the live-agent presence as `last_error`. During the failed tick the presence can report `error`; after a bounded run exits or a group is stopped, the final heartbeat can report `offline` while preserving `last_error`. The final offline heartbeat is best-effort: if the room server is already unavailable during shutdown, the runner keeps the completed reply count or handled command-error result instead of converting shutdown into a provider failure. The process row can still be `stopped` with return code `0` when the resident runner handled the delegate error internally. Use `live_agents.json`, `processes.json`, and the bounded `log_tail` together rather than trusting one field alone.

## Recovery Expectations

After restarting the GUI:

- old `running` records in `.agentsassemble/live-agent-runs/processes.json` become `unknown` with their stale `pid` cleared;
- old `stopped` and `error` records remain listed;
- previous logs remain inspectable through their `log_path`;
- the new GUI supervisor does not claim it can stop PIDs it did not launch;
- restarted resident runners reuse `last_observed_event_id` from their live-agent presence so they do not answer the same lobby event again;
- existing presence rows in `.agentsassemble/live_agents.json` can remain until heartbeat age makes them `stale`; restarting the GUI does not resume old resident agents except pending auto-restart records, which can start a fresh process after `next_restart_at`.

This slice is not native Claude Code Channels, Gemini SDK sessions, Cursor PTY persistence, or OS-level sandboxing. The `live_session` transport is not a native Claude, Gemini, or Cursor PTY protocol. Those are future backend variants behind the same room and supervisor shape.
