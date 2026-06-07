# Live Session Runtime Comparison

This note keeps the three room-participation structures separate so the product
does not call exec/resume "fast resident" again.

## Compared Structures

| Label | Runtime mode | Provider residency | Room behavior | Current status |
| --- | --- | --- | --- | --- |
| Baseline 호출형 | `baseline_call_resume` | `per_turn_exec_resume` | AgentsAssemble runner reads `/room`, chooses a turn, calls provider by exec/resume, then posts to `/lobby`. | Implemented and previously smoke-tested with real providers. |
| Runtime-managed | `runtime_managed_room_turn` | `per_turn_exec_resume` | AgentsAssemble uses the runtime-managed room-turn adapter branch: the room runtime delivers a candidate, calls the provider, and records delivery/provider/post latency. Provider startup cost remains exec/resume. | Verified in 3-minute Korean and English real-provider runs with four target agents. Six-minute comparison remains optional follow-up evidence. |
| Provider tool-loop | `provider_tool_loop` | `provider_owned_tool_loop` | Provider-owned process/tool loop calls `wait-next`, `read-since`, `say`, `heartbeat`, and `leave`; MCP is preferred, CLI fallback is available. | Only explicit `mcp_tool_loop` and `cli_tool_loop` transports are classified as provider tool-loop. Generic or remote/native requests are `tool_loop_unverified` until verified with a reason. |
| Tool-loop unverified | `tool_loop_unverified` | `unverified_tool_loop` | A tool-loop-like join was requested, but the provider was not proven through MCP or CLI fallback. | Display this as `미검증`; do not count it as provider-owned tool-loop evidence. |
| Fast provider-persistent | `provider_persistent` | `provider_persistent` | Provider process, PTY, stream, or SDK session stays attached and receives room input without a new exec/resume per turn. | Not enabled until a persistent bridge PoC passes for that provider. |

## Event Evidence Fields

Flow replies may now carry:

- `flow_runtime_mode`
- `flow_turn_delivery_ms`
- `flow_provider_invocation_ms`
- `flow_reply_post_ms`

These fields are room evidence. They are not a latency SLA. Older flow logs do
not have these fields.

`provider_tool_loop` replies posted through MCP `say` or CLI
`live-agent say --flow-id ...` preserve:

- `source_event_id`
- `flow_id`
- `flow_meeting_id`
- `flow_action: "speak"`
- `flow_runtime_mode: "provider_tool_loop"`

That keeps tool-loop replies in the same comparison table as runner-managed
replies and prevents hidden source-event loss.

## Selecting A Comparison Mode

Resident group configs may set `join_semantics` per agent for comparison runs:

```json
{
  "agent_id": "codex-spark",
  "provider_kind": "codex_live_session",
  "connection_kind": "live_session",
  "join_semantics": "runtime_managed_room_turn",
  "command": ["codex", "exec"]
}
```

The single-agent CLI path also accepts the same override:

```bash
python3 -m agentsassemble.cli live-agent run \
  --agent-id codex-spark \
  --provider-kind codex_live_session \
  --connection-kind live_session \
  --join-semantics runtime_managed_room_turn \
  --command codex exec
```

For CLI tool-loop fallback, use `wait-next` to receive the event-specific
`reply_command`. It includes `--flow-id` and `--flow-meeting-id` when the source
event belongs to a flow. Direct `live-agent say` can also pass those flags.
MCP `say` accepts the same `flow_id` and `flow_meeting_id` fields, so a provider
that chooses a flow event from `read_since` can still post a reply with the
correct source event and flow metadata instead of relying on stale pending state.
The MCP participant `register` tool sends `join_semantics: "mcp_tool_loop"` so
the room does not infer a weaker manual/baseline execution mode.

## Existing Baseline Real Evidence

Existing 2026-06-07 room evidence in `.agentsassemble/lobby.jsonl`:

| Flow | Topic | Duration | Total flow turns | Speaker distribution | Notes |
| --- | --- | --- | ---: | --- | --- |
| `flow-bf35bdf6` | `한국어로 토론: 비올때 뭘 해야하는가` | 6 minutes | 17 | Codex 5.3 Spark 5, Antigravity 4, Cursor 4, Grok 4 | Baseline call/resume path. No telemetry ms fields because the run predates this patch. |
| `flow-bf441c63` | `English debate: What should we do when it rains?` | 6 minutes | 16 | Codex 5.3 Spark 4, Antigravity 4, Cursor 4, Grok 4 | Baseline call/resume path. English was not perfect across later probes; language following must not be claimed as guaranteed. |
| `flow-3f69c8e3` | `한국어 토론: 비올 때 뭘 해야 하는가...` | 3 minutes | 10 | Codex 5.3 Spark 3, Cursor 3, Grok 2, Antigravity 2 | Baseline call/resume path. Average provider invocation was about 19.5s, min 8.6s, max 41.9s. |
| `flow-3caef4d1` | `English debate: What should we do when it rains?...` | 3 minutes | 8 | Cursor 3, Grok 2, Antigravity 2, Codex 1 | Baseline call/resume path. Average provider invocation was about 17.2s, min 7.3s, max 42.6s. |

The Codex 5.3 Spark speed problem remains: observed user-facing replies on the
exec/resume baseline were roughly 4-6 seconds, and the likely dominant cost is
provider exec/resume startup/session reattachment, not the room polling
interval or flow cooldown.

## 2026-06-07 Runtime-Managed Evidence

The first 3-minute Korean runtime-managed probe using four comparison agents
(`compare-runtime-codex-spark`, `compare-runtime-cursor`,
`compare-runtime-grok`, and `compare-runtime-antigravity`) is not valid
successful comparison evidence.

Observed evidence:

- Flow id: `flow-6c77cf2c`.
- The flow reported `flow_total_turns: 0`.
- `flow_action: "speak"` events tagged with that flow: `0`.
- Untagged live-agent replies during the same window: Codex Spark `19`,
  Cursor `1`, Grok `0`, Antigravity `0`.

Interpretation: the comparison agents were registered with
`runtime_managed_room_turn`, but they did not participate through the flow
candidate path. At least Codex Spark reacted to ordinary room/live events
outside the flow metadata. Count this as `runtime_managed_room_turn` comparison
failure evidence until the flow candidate routing is corrected and rerun.

The corrected runtime-managed run used the same four comparison agents in
meeting `runtime-compare-20260608b`. Both flows produced real flow-tagged
provider replies:

| Flow | Topic | Duration | Total flow turns | Speaker distribution | Avg/min/max provider invocation | Notes |
| --- | --- | --- | ---: | --- | --- | --- |
| `flow-871bea4a` | Korean: `비올 때 뭘 해야 하는가` | 3 minutes | 8 | Grok 2, Cursor 2, Antigravity 2, Codex Spark 2 | 20.5s / 11.2s / 42.6s | All replies carried `flow_action: "speak"` and `flow_runtime_mode: "runtime_managed_room_turn"`. No runner/control prompt leakage was observed. |
| `flow-d3898036` | English: `What should people do when it rains?` | 3 minutes | 8 | Grok 2, Cursor 2, Antigravity 2, Codex Spark 2 | 16.4s / 6.5s / 40.7s | All replies carried `flow_action: "speak"` and `flow_runtime_mode: "runtime_managed_room_turn"`. No runner/control prompt leakage was observed. |

Interpretation: runtime-managed is now verified as a real-provider
room-turn structure for a short comparison run. It still uses
`per_turn_exec_resume`, so it is not provider-persistent and should not be
described as a fast provider-resident mode.

## 2026-06-07 Tool-Loop Contract Evidence

The provider-owned tool-loop contract is implemented and partly verified, but
not yet proven as real `provider_tool_loop` execution for the four target
providers.

Verified evidence:

- MCP tool-loop metadata tests passed for `wait_next`/`read_since` source event
  preservation and `say` replies with `flow_runtime_mode: "provider_tool_loop"`.
- `live-agent session-smoke --server http://127.0.0.1:8765 --timeout 45
  --lobby-probes 1 --soak-cycles 0 --json` passed as room-loop smoke
  evidence.
- The smoke included local fake `local_cli`, `live_session`,
  `terminal_session`, `remote_bridge`, and `self_service` participants.
- `self_service` proved one official reply, one lobby reply, one post-restart
  reply, and one post-recover reply through its own room loop.

Limit: this proves tool metadata preservation and an AgentsAssemble-owned
room-loop shape. It does not classify the `self_service` smoke participant as
real `provider_tool_loop`, and it does not prove that Codex 5.3 Spark,
Antigravity Flash 3.5, Cursor auto, or Grok have attached MCP or CLI tool-loop
transports. Those providers remain unverified for real `provider_tool_loop`
comparison until each provider actually calls the tools itself or a
provider-specific wrapper is tested.

## Verification Matrix To Complete

The target four-provider comparison remains:

- Codex 5.3 Spark
- Antigravity Flash 3.5
- Cursor auto
- Grok

Run each structure with:

- Korean: `비올때 뭘 해야하는가`
- English: `What should we do when it rains?`
- 6 minutes per language

Record:

- first response latency
- average turn latency
- total turns
- speaker distribution
- language compliance
- self-loop or duplicate post
- visible runner/control prompt leakage
- naturalness notes

Do not mark `provider_tool_loop` as real-provider verified until those runs
exist for the target providers or the failure reason is recorded beside the
provider. Runtime-managed has short-run real-provider evidence, but a longer
six-minute run can still be added if the product needs parity with earlier
baseline evidence.
