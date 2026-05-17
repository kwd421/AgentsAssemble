# Live Agent Ops

This is the local operator checklist for the current AgentsAssemble resident live-agent slice. It uses the local GUI room as the control plane and local CLI processes as resident participants.

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
configs/live-agents.example.json
```

The live-agent roster and supervised process panel auto-refresh in the GUI every 5 seconds. This keeps stale presence, process crashes, pending auto-restart state, and recovered groups visible during long sessions without relying only on the manual refresh buttons. The GUI server also starts a backend supervisor monitor, so owned process crash detection and due auto-restarts continue without an open browser or `/api/live-agent-processes` polling client. The manual refresh buttons remain useful when you want an immediate read after changing files or process state from another terminal.

That example config contains real `claude` and `gemini` commands. Do not start it until the real-provider checklist below is satisfied.

## Credential-Free Operator Smoke

After the GUI room is running, use the one-command smoke before touching real providers:

```bash
python3 -m agentsassemble.cli live-agent smoke \
  --server http://127.0.0.1:8765
```

This command posts a human lobby event, starts a temporary supervised process group through `/api/live-agent-processes/start`, and waits for two fake resident agents to answer:

- `smoke local_cli ok`
- `smoke live_session ok`

The smoke is credential-free: it uses local Python fake agents only, not Claude, Gemini, Cursor, account login, network model calls, or paid provider APIs. It verifies the GUI control plane, lobby event ingestion, supervised `run-group`, one-shot `local_cli`, long-lived JSONL `live_session`, and process cleanup path from the same CLI surface an operator uses.

For a clean doctor run, start the GUI with a temporary `--output-root` and point smoke at that local server. The smoke server must be able to read a temporary config path from the same machine, so treat this as a local GUI diagnostic rather than a remote bridge test.

The GUI "상주 실행" panel exposes the same local diagnostic as the `진단` button. It calls `POST /api/live-agent-smoke`, starts the same temporary fake `local_cli` and `live_session` group, verifies the smoke replies by `source_event_id`, then refreshes the lobby, presence roster, and process records. Use it when you want operator-visible evidence without leaving the room UI.

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

Smoke-created fake agents and process groups are marked `diagnostic`. They remain visible in `.agentsassemble/live_agents.json` and `.agentsassemble/live-agent-runs/processes.json` for operator inspection, but `/api/live-agent-health` ignores diagnostic records so a successful doctor run does not contaminate later health checks or repeated readiness checks. Legacy smoke artifacts from before the `diagnostic` flag are also ignored when their preserved agent identity matches the built-in `Smoke Local CLI` or `Smoke Live Session` diagnostic agents.

Status meanings:

- `ready`: pre-smoke health is `ok`, and the fake `local_cli` plus `live_session` smoke passed.
- `degraded`: smoke passed, but pre-smoke health already had agent or process attention.
- `failed`: the room was reached, but the smoke check did not pass.

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

This connection kind is for `live-agent run` and `live-agent run-group`. The one-shot `live-agent delegate` command keeps plain local CLI semantics and does not use the JSONL session envelope.

Protocol:

- request JSONL: `{"request_id": "...", "prompt": "..."}`
- response JSONL: `{"request_id": "...", "message": "one lobby reply"}`

The subprocess must write only response JSONL to stdout. Diagnostic logs belong on stderr; the runner drains stderr separately and keeps only a bounded tail for error reporting.

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
- press `시작`

The GUI start button runs the same resident group through the local process supervisor. Group records and log tails remain visible even after the process stops or crashes. Auto restart only applies to a group launched with that option enabled, and it starts a fresh local process rather than attaching to an old PID.

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
  --restart-backoff-seconds 5
```

Here `--server` is the GUI API target and the room server URL passed to the supervised `run-group`.

## Stop Or Restart A Group

Prefer the GUI stop button for a running group. The HTTP stop path is also available:

```bash
curl -X POST \
  -H 'Content-Type: application/json' \
  -d '{}' \
  http://127.0.0.1:8765/api/live-agent-processes/local-cli-group/stop
```

Use the GUI restart button on a stopped, crashed, or recovered group to relaunch it from the persisted `config_path` and `server`. The HTTP restart path is:

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

Add `--json` to any process CLI command to print the raw HTTP payload.

The process CLI uses exit code `0` for successful supervisor requests, even when a listed group is `stopped`, `error`, or `unknown`. It uses exit code `2` for argument validation, connection failures, invalid JSON, HTTP errors, missing config files, unknown group ids, and refused restarts.

The supervisor only stops group ids it launched in the current GUI process. It does not control arbitrary OS PIDs. Historical records from a previous GUI process are shown as `unknown`, `stopped`, or `error`, but are not treated as externally stoppable PIDs. Restarting a historical record starts a fresh local process from the saved config and server instead of attaching to the old PID. A manual restart resets the auto-restart counter for the new run while preserving the configured retry policy.

## Inspect Runtime State

Default files under `--output-root .agentsassemble`:

```text
.agentsassemble/live_agents.json
.agentsassemble/lobby.jsonl
.agentsassemble/live-agent-runs/processes.json
.agentsassemble/live-agent-runs/<group_id>.log
```

What to check:

- `.agentsassemble/live_agents.json`: presence, status, heartbeat metadata, `last_error`, `last_reply_at`, and `last_observed_event_id`.
- `.agentsassemble/lobby.jsonl`: human lobby messages and live-agent replies. Live-agent auto replies include `actor_id`, `source_event_id`, and `auto_chain_depth`.
- `.agentsassemble/live-agent-runs/processes.json`: durable group records with `group_id`, `status`, `pid`, `config_path`, `server`, `log_path`, timestamps, `returncode`, and `last_error`.
- auto-restart fields in `processes.json`: `auto_restart`, `restart_count`, `max_restarts`, `restart_backoff_seconds`, and `next_restart_at`.
- `.agentsassemble/live-agent-runs/<group_id>.log`: stdout/stderr for the supervised `run-group` process. Delegate provider subprocess stdout/stderr is captured by the runner, not streamed directly into this file. The GUI and process API expose only a bounded `log_tail`.

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

The response reports overall `status` as `ok` or `degraded`, plus `agents.counts`, `agents.attention`, `processes.counts`, and `processes.attention`. Treat `degraded` as a prompt to inspect the listed agent ids, process group ids, `last_error`, and log tails.

This endpoint is a read-only snapshot. It does not refresh process handles, launch due auto-restarts, stop groups, or mutate process state.

External or manually driven agents can also report an error heartbeat through the CLI:

```bash
python3 -m agentsassemble.cli live-agent heartbeat \
  --server http://127.0.0.1:8765 \
  --agent-id claude-code-live \
  --status error \
  --last-error "delegate command failed" \
  --last-observed-event-id evt1 \
  --last-reply-at 2026-05-17T12:00:00+00:00
```

That command writes the same `last_error`, `last_observed_event_id`, and `last_reply_at` fields used by resident runners, so non-runner agents can stay visible in the roster without inventing a separate status path.

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

Provider command failures are recorded on the live-agent presence as `last_error`. During the failed tick the presence can report `error`; after a bounded run exits or a group is stopped, the final heartbeat can report `offline` while preserving `last_error`. The process row can still be `stopped` with return code `0` when the resident runner handled the delegate error internally. Use `live_agents.json`, `processes.json`, and the bounded `log_tail` together rather than trusting one field alone.

## Recovery Expectations

After restarting the GUI:

- old `running` records in `.agentsassemble/live-agent-runs/processes.json` become `unknown` with their stale `pid` cleared;
- old `stopped` and `error` records remain listed;
- previous logs remain inspectable through their `log_path`;
- the new GUI supervisor does not claim it can stop PIDs it did not launch;
- restarted resident runners reuse `last_observed_event_id` from their live-agent presence so they do not answer the same lobby event again;
- existing presence rows in `.agentsassemble/live_agents.json` can remain until heartbeat age makes them `stale`; restarting the GUI does not resume old resident agents except pending auto-restart records, which can start a fresh process after `next_restart_at`.

This slice is not native Claude Code Channels, Gemini SDK sessions, Cursor PTY persistence, or OS-level sandboxing. The `live_session` transport is not a native Claude, Gemini, or Cursor PTY protocol. Those are future backend variants behind the same room and supervisor shape.
