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

## 2026-06-08 Invalid Claude Haiku Runs

The 2026-06-08 comparison attempts that included `Claude Haiku` are invalid as
provider-live comparison evidence.

Invalid flow ids:

- `flow-9a176598`
- `flow-5be9c34a`
- `flow-7683a50d`
- `flow-6374362e`

Reason: the temporary Claude config used `claude -p --model haiku`, which is
Claude Code print/non-interactive one-shot mode. That violates the product
boundary in `docs/provider-architecture.md`: `local_cli` one-shot/delegate
style must not be presented as the final live teammate experience, and
one-shot print calls must not be counted as live-session evidence.

Action taken: the comparison configs no longer contain `claude -p`, and tracked
preflight/run-group validation now rejects `claude_code` resident configs that
use Claude Code print/non-interactive flags such as `-p` or `--print`. Claude
Haiku is only a `terminal_session` candidate until a real provider-owned
session path is proven. Do not reuse the invalid flow ids above as success
evidence.

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

## 2026-06-09 Claude/Codex 1:1 And 3-Minute Rerun Evidence

Evidence root:
`.agentsassemble/runtime-comparison-rerun/20260609T0101KST`.

The 1:1 Claude/Codex checks used `flow_events` from the isolated meeting
`verify-claude-codex-isolated-20260608T163547Z`. Claude was launched through
`TerminalLiveSession` with `claude --model haiku --effort xhigh`; no
`claude -p` or `claude --print` command was used. The same direct PTY check is
recorded in
`.agentsassemble/runtime-comparison-rerun/20260609T0101KST/claude-terminal-session-trust-check.log`
and failed with:

```text
Terminal session requires workspace trust before it can answer. Open Claude once in this folder, accept the workspace trust prompt, then resume the agent.
```

| Structure | Flow | Duration | Result | Speaker distribution | Provider invocation |
| --- | --- | ---: | --- | --- | --- |
| Baseline 호출형 | `flow-a69834d8` | 1 minute | Codex answered; Claude did not answer because workspace trust blocked the terminal session. | Codex 5.3 Spark 1v1 1, Claude Haiku 1v1 0 | Codex 8.293s |
| Runtime-managed | `flow-1ddf3fc2` | 1 minute | Codex answered through `runtime_managed_room_turn`; Claude stayed blocked by workspace trust. | Codex 5.3 Spark 1v1 1, Claude Haiku 1v1 0 | Codex 8.438s |
| Provider tool-loop | not run as real provider | 1 minute target | No real Claude/Codex provider-owned MCP or CLI tool-loop attachment exists in the current code path. Manual host calls to `wait-next`/`say` would fake the provider, so they are not counted. | 0 verified | blocked |
| Provider-persistent | direct PTY probe only | 1 minute target | Claude PTY is blocked by workspace trust. A direct `codex` PTY probe exited with return code 1, so Codex persistent provider input is not proven. | 0 verified | blocked |

The 3-minute rerun included Codex 5.3 Spark, Antigravity Flash 3.5, Cursor
auto, Grok, and Claude Haiku where the configured provider path could start.
The tests used 0.25s flow tick interval and 0s cooldown; the remaining latency
is provider invocation time, not a room cooldown.

| Structure | Language | Flow | Turns | Speaker distribution | Avg/min/max provider invocation | Resource peak |
| --- | --- | --- | ---: | --- | --- | --- |
| Baseline 호출형 | Korean | `flow-ed5c53c3` | 4 | Grok 1, Cursor 1, Antigravity 1, Codex Spark 1, Claude 0 | 14.265s / 5.231s / 27.913s | supervised RSS 39.8 MB, total RSS 1780.4 MB, total CPU 60.7% |
| Baseline 호출형 | English | `flow-f1b20455` | 4 | Grok 1, Cursor 1, Antigravity 1, Codex Spark 1, Claude 0 | 15.026s / 8.966s / 25.311s | supervised RSS 39.7 MB, total RSS 1527.5 MB, total CPU 86.6% |
| Runtime-managed | Korean | `flow-754bd314` | 3 | Grok 1, Antigravity 1, Codex Spark 1, Cursor 0, Claude 0 | 17.674s / 13.185s / 26.407s | supervised RSS 39.6 MB, total RSS 1108.5 MB, total CPU 42.7% |
| Runtime-managed | English | `flow-e3c4e423` | 3 | Grok 1, Antigravity 1, Codex Spark 1, Cursor 0, Claude 0 | 14.411s / 7.978s / 23.064s | supervised RSS 39.8 MB, total RSS 1114.3 MB, total CPU 82.5% |

Observed failures in the same evidence:

- Claude Haiku produced no room replies because the `terminal_session` path
  reached the local Claude workspace-trust gate before it could answer.
- Cursor answered in the baseline reruns, but the runtime-managed reruns left
  Cursor in `error` with `Cursor live session command failed with return code
  1.`
- Provider tool-loop remains unverified for these real providers. The current
  MCP server exposes `register`, `wait_next`, `read_since`, `say`, `heartbeat`,
  and `leave`, but no real Codex, Claude, Cursor, Grok, or Antigravity process
  was proven to own that MCP/tool loop in this rerun.
- A raw `mcp serve --profile participant` process without an attached MCP
  client exited after stdio closed. A previous connected/held MCP resource
  sample for 20.779 seconds measured peak RSS 104.0 MB, average RSS 98.8 MB,
  peak CPU 39.1%, and average CPU 2.0%.

Conclusion: baseline and runtime-managed are currently real provider
exec/resume structures, not fast provider-resident structures. Claude Haiku is
not verified until the local workspace-trust prompt is accepted and rerun.
Provider tool-loop and provider-persistent modes remain blocked/unverified for
the target providers until a real provider-owned MCP/PTY/stream bridge is
attached and measured.

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

## Remaining Verification Matrix

The current 2026-06-09 rerun completed a 3-minute baseline/runtime-managed
comparison, but it did not prove every requested structure. The remaining
matrix is:

- Accept or otherwise resolve the local Claude workspace-trust gate, then rerun
  Claude Haiku in the 1:1 and multi-agent flows without `-p`/`--print`.
- Reproduce and fix the Cursor `runtime_managed_room_turn` return-code-1 error,
  then rerun the runtime-managed Korean and English 3-minute flows.
- Attach a real provider-owned MCP or CLI tool-loop for each target provider
  before marking `provider_tool_loop` as verified. Host-side manual
  `wait-next`/`say` calls are not valid provider evidence.
- Prove a provider-persistent channel separately for each provider before
  calling it fast resident. A PTY/stream/process must accept repeated room
  inputs and return clean one-reply-per-turn outputs without per-turn
  exec/resume.

When rerunning, use:

- Korean: `비올때 뭘 해야하는가`
- English: `What should we do when it rains?`
- 3 minutes per language unless a longer run is explicitly requested

Record:

- first response latency
- average turn latency
- total turns
- speaker distribution
- language compliance
- self-loop or duplicate post
- visible runner/control prompt leakage
- naturalness notes
- local provider and MCP resource samples

Do not mark `provider_tool_loop` as real-provider verified until those runs
exist for the target providers or the failure reason is recorded beside each
provider. Do not mark `provider_persistent` as verified from baseline or
runtime-managed exec/resume evidence.
