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

The lobby is the public room surface. The "상주 실행" panel can start, refresh, stop, and restart local live-agent process groups. The default group config path is:

```text
configs/live-agents.example.json
```

That example config contains real `claude` and `gemini` commands. Do not start it until the real-provider checklist below is satisfied.

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

The supervisor only stops group ids it launched in the current GUI process. Historical records from a previous GUI process are shown as `unknown`, `stopped`, or `error`, but are not treated as externally stoppable PIDs. Restarting a historical record starts a fresh local process from the saved config and server instead of attaching to the old PID.

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

- old `running` records in `.agentsassemble/live-agent-runs/processes.json` become `unknown`;
- old `stopped` and `error` records remain listed;
- previous logs remain inspectable through their `log_path`;
- the new GUI supervisor does not claim it can stop PIDs it did not launch;
- existing presence rows in `.agentsassemble/live_agents.json` can remain until heartbeat age makes them `stale`; restarting the GUI does not resume old resident agents except pending auto-restart records, which can start a fresh process after `next_restart_at`.

This slice is not native Claude Code Channels, Gemini SDK sessions, Cursor PTY persistence, or OS-level sandboxing. Those are future backend variants behind the same room and supervisor shape.
