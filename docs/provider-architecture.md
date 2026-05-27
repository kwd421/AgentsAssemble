# Provider Architecture

AgentsAssemble should support many providers without making the meeting runner know each provider's internal rules.

The core boundary is:

```text
Role profile -> Agent binding -> Provider config -> Adapter session -> Permission/capability snapshot
```

## Principles

- A role is not a provider. A role describes what the agent is responsible for and how it behaves.
- A binding says which concrete provider/model/session will play that role in this meeting.
- The host/orchestrator owns final role assignment. Incoming external agent profiles are requests, not authority.
- Meeting mode is read-only by default. Implementation-side permissions are rejected during meeting runs.
- Provider capability is explicit. Search, tools, filesystem access, session resume, and structured output cannot be assumed.
- External sessions, memory packs, remote users, and imported context are untrusted until reviewed.
- Meeting artifacts must record provider, permission, capability, and session snapshots for audit and handoff.

## Shared Room Event Stream

Provider adapters should eventually attach participants to a shared room event stream instead of simulating isolated interview prompts. The working product rule is:

> Agents do not receive isolated interview prompts; they join a shared room and respond to the same ordered event stream.

The full room and `live_session` direction is documented in `docs/live-session-room-model.md`.

Stoops is a reference for live room infrastructure: a small room server, SSE event stream, MCP tools, and tmux-backed CLI session injection. Claude Code Channels is the official Claude Code reference for pushing Discord, Telegram, iMessage, or custom webhook events into an already-running Claude Code session.

AgentsAssemble should use these patterns carefully. A provider can attach to the room transport, but the council workflow still decides what becomes an official turn, what reaches transcript and Decision Gate, and what remains free chat.

## Current Implemented Slice

Meeting execution records provider structure:

- `provider_configs`
- `permission_profiles`
- `agent_bindings`
- `provider_capabilities`
- per-role `isolation.*.agent_binding`
- per-role `isolation.*.provider`
- per-role `isolation.*.permissions`
- per-role `isolation.*.capabilities`

The default demo still assigns all roles to one provider unless an agent runtime config is supplied. The point of this slice is to make role-by-role routing auditable.

The default registry exposes provider kinds with explicit capability snapshots:

- `mock`
- `codex`
- `codex_live_session`
- `kiro_live_session`
- `anthropic`
- `gemini`
- `grok`
- `grok_build_cli`
- `local_openai_compatible`
- `remote_http_bridge`
- `local_cli`
- `cursor`
- `claude_code`
- `antigravity_cli`
- `gemini_cli_legacy`
- `hermes_cli`
- `openclaw_cli`
- `hermes_memory`
- `openclaw_memory`
- `memory_pack`

`anthropic`, `gemini`, `grok`, `local_openai_compatible`, `remote_http_bridge`, `local_cli`, and `codex_live_session` have meeting adapters. `kiro_live_session` is a resident live-agent adapter, not a meeting adapter: it joins through the resident runner and Kiro's own chat resume store. `cursor`, `claude_code`, `antigravity_cli`, `gemini_cli_legacy`, `grok_build_cli`, `hermes_cli`, and `openclaw_cli` remain implementation-phase planned providers unless they are launched through the resident live-agent runner's explicit connection-kind contract; meeting-time validation still rejects implementation-side permissions such as filesystem write, git write, push, or implementation mode.

Imported memory/profile packs now have a safe inspection surface before they can
affect meeting context. `assemble memory-capsule gate --path <capsule-dir>`
produces a memory/profile capsule gate report with redacted local path evidence,
required-file status, JSON-object validation, permission-policy checks, raw
session dump detection, and compact evidence-index counts. The report never
executes providers, starts resident sessions, imports the pack, or prints raw
persona, memory, handoff, evidence, provenance, or permission body text.

## Provider Families

### API Meeting Providers

Use these for read-only council meetings, research, critique, and synthesis.

- Anthropic / Claude API
- Google / Gemini API
- xAI / Grok API
- OpenAI-compatible local or hosted APIs
- Ollama or other local OpenAI-compatible servers

These should implement `ProviderAdapter`.

### Coding Agent Providers

Use these after `decision.md` exists and implementation is explicitly approved.

- Codex CLI
- Claude Code CLI
- Cursor agent CLI
- Other shell-driven coding agents

These should eventually be separated into a `CodingAgentAdapter` or implementation-phase adapter rather than being treated as ordinary meeting chat providers.

### Remote HTTP Bridge Providers

Use `remote_http_bridge` when another person owns the AI session and wants that session to join a meeting without giving the host raw local access.

The remote owner runs a bridge on their machine:

```bash
python -m agentsassemble.bridges.claude_code_bridge --host 0.0.0.0 --port 8777 --token "$AGENTSASSEMBLE_BRIDGE_TOKEN"
```

The host config points a provider at that bridge:

```json
{
  "id": "friend-claude-code",
  "kind": "remote_http_bridge",
  "display_name": "Friend Claude Code Bridge",
  "endpoint": "http://100.64.0.10:8777",
  "auth_ref": "env:AGENTSASSEMBLE_BRIDGE_TOKEN"
}
```

Bridge requests go to `POST /agentsassemble/run` and include:

- provider identity.
- meeting id, agent id, owner id, join mode, and session id when available.
- role, step, prompt, research depth, and public context.
- an explicit meeting-read-only permission envelope.

Bridge responses return:

- `text`: JSON text requested by the meeting adapter.
- `metadata`: optional bridge diagnostics. Public artifacts only retain safe scalar fields such as bridge label, role id, step, return code, and timeout state. Commands, headers, stderr, nested objects, and raw diagnostic payloads must stay out of public meeting artifacts.

The bridge is a meeting adapter, not an implementation adapter. It instructs the remote Claude Code session to avoid shell commands, file reads, edits, credentials, commits, pushes, deploys, and implementation work during meeting turns.

Bridge readiness has three verification levels:

- Health readiness: `GET /agentsassemble/health` can verify bridge HTTP reachability and token acceptance without calling `POST /agentsassemble/run`, sending prompts, or executing Claude.
- Local execution readiness: unit tests and a local smoke server can verify token auth, `POST /agentsassemble/run`, prompt delivery, command execution, response parsing, lobby messages, research, rounds, and synthesis envelopes.
- Real friend readiness: only a real remote machine can verify the friend's Claude Code login state, reachable Tailscale/LAN/port-forward address, firewall rules, token sharing, latency, and whether the friend's live model follows the read-only meeting contract.

Do not report a friend bridge as fully verified unless the health probe, local execution readiness, and real friend readiness have all been checked.

### Native Remote Room Clients

Use `native_remote_room_client` for the planned bridge-free path where the remote
machine joins the AgentsAssemble room API directly. This is not the same as
`remote_http_bridge`: the host is not calling `/agentsassemble/run`, and the
remote owner is not exposing a prompt execution endpoint to the host. The remote
client owns its provider session, watches room events, and posts replies as a
host-admitted live-agent identity.

The first no-Tailscale slice is documented in
`docs/no-tailscale-multi-host.md`. `live-agent lan-invite` can create and verify
an expiring HMAC-SHA256 LAN invite packet with
`client_kind: "native_remote_room_client"`, scoped to one room URL, meeting id,
and agent id. Invite verification is admission evidence only: it does not start
provider CLIs, approve real provider execution, prove context quality, solve NAT
traversal, or provide relay/WebRTC readiness.

### Local CLI Meeting Providers

Use `local_cli` when a local command can accept a prompt on stdin and return a JSON response on stdout. This is the local "delegate session" foundation for Codex-like, Claude Code-like, legacy Gemini CLI-like, or other shell-driven participants when they are used as read-only council speakers.

Example provider config:

```json
{
  "id": "custom-cli",
  "kind": "local_cli",
  "display_name": "Custom CLI",
  "command": ["python3", "scripts/provider_prompt.py"],
  "timeout_seconds": 300
}
```

Local readiness can be verified with a fake command runner or a local smoke script. Real provider readiness still requires the user's actual CLI installation, login state, model availability, and billing/subscription state.

`local_cli` is one-shot/delegate style. It can test provider connectivity and opinion mode, but it should not be presented as the final live teammate experience.

Resident `terminal_session` is the first local PTY-backed slice for Claude-like or legacy Gemini-like CLIs. It keeps one interactive terminal process alive, injects each room prompt as a terminal submission, and captures output after the terminal has been idle. This is closer to the Stoops-style live room shape than one-shot `local_cli`, but it is still not Claude Code Channels, Antigravity native sessions, tmux ownership, or OS-level sandboxing.

Resident `self_service` is the first local process-supervision slice that stops AgentsAssemble from injecting each room prompt into the provider process. The supervisor registers the live agent, starts the configured command with `stdin` closed, exports `AGENTSASSEMBLE_*` environment variables plus shell-escaped room command templates such as `AGENTSASSEMBLE_WAIT_NEXT_COMMAND`, `AGENTSASSEMBLE_SAY_COMMAND_TEMPLATE`, `AGENTSASSEMBLE_OFFICIAL_REPLY_COMMAND_TEMPLATE`, `AGENTSASSEMBLE_HEARTBEAT_COMMAND_TEMPLATE`, and `AGENTSASSEMBLE_LEAVE_COMMAND`, and lets the child call `wait-next`, `say`, `official-reply`, `heartbeat`, and intentional `leave` itself after splitting those templates into argv and replacing placeholders. Use it for Antigravity CLI or custom wrappers that can own their own room loop. `scripts/my_self_service_agent.py` is the runnable local example for that wrapper shape.

The director-led team examples exercise that agent-owned path as an
organization template, not as a real-provider launch recipe. The bundle is
`configs/director-led-team.example.json`,
`configs/agents.director-led-team.example.json`, and
`configs/live-agents.director-led-team.self-service.example.json`. It models a
director, product lead, engineering lead, design lead, and implementer with
display/model slots only. v1 verification should use fake or self-service
agents to prove each participant reads room diffs and posts through room tools;
real Opus, Codex, Kiro, Cursor, Antigravity, DeepSeek, or similar providers
remain explicit operator-approved executions.

Play Mode `flow` is a room policy over already-running, host-approved resident
agents. It temporarily changes their engagement mode and appends scoped lobby
events, but it is not a provider adapter and does not launch, resume, approve,
or recover provider CLIs. Flow decisions are provider-owned replies to the room
snapshot, while AgentsAssemble records only the visible lobby message and safe
metadata needed for cursors, cooldown, chain-depth, and audit.

Local stdio MCP is an adapter over the same room contract, not a replacement
control plane. `assemble mcp serve --profile participant` exposes
agent-owned tools for register, heartbeat, wait_next, read_since, say,
official_reply, read_room, read_return_packet, and leave using the existing GUI
HTTP endpoints.
The participant identity comes from the server startup command (`--agent-id`,
`--display-name`, `--provider-kind`, `--connection-kind`, and
`--engagement-mode`), not from later tool-call arguments.
`--profile archive` exposes only `read_transcript`, `read_decision`,
`read_shared_memory`, `list_meetings`, and `read_meeting_summary`; those archive
tools validate meeting ids before calling room endpoints and return sanitized
archive fields rather than local paths or raw meeting records. MCP attachment
does not start provider
CLIs, does not persist real-provider approval, and does not enable host-control
actions such as meeting creation, session start/stop, pending-turn cancellation,
or finalization. Host-control tools stay a later design behind
authentication and admission boundaries.

`codex_live_session` is the first Codex-specific live-session slice. Meeting turns use Codex CLI session ids and `codex exec resume` so repeated turns can continue the same Codex session history. Resident live-agent configs use `provider_kind: "codex_live_session"` with `connection_kind: "live_session"`; both the meeting adapter and resident runner call Codex CLI through `codex exec --sandbox read-only --ignore-rules` / `codex exec --sandbox read-only --ignore-rules resume` rather than through the JSONL fake-session protocol. The explicit `--sandbox read-only` flag is the safety input, `--ignore-rules` keeps repository `.rules` files from participating in the launch, and Codex CLI still owns the actual enforcement. Public artifacts record that path as `sandbox_enforcement: "codex_readonly"`. This is not native Codex/Claude channel injection, OS-level sandboxing, or a substitute for a future constrained launch path for arbitrary CLIs.

`kiro_live_session` is the first Kiro-specific resident slice. It uses
`kiro chat --resume-id <session-id>` so later room turns continue the same Kiro
chat history without AgentsAssemble replaying earlier prompts as hidden
context. When a Kiro resident has no configured `session_id`, the runner lists
Kiro sessions before and after the first `kiro chat` call, captures the new
session id, persists it through the live-agent presence payload, and uses it on
later calls. Public artifacts may record the provider kind, connection kind,
safe join semantics (`kiro_chat_resume`), and context durability
(`provider_managed_resume`), but they must not store prompts, provider output,
command tails, or raw session-list output. Kiro launch safety is currently
`advisory`; unlike Codex, there is no AgentsAssemble-owned read-only sandbox
flag in this adapter. Fresh Kiro session capture is serialized inside one
resident runner process so concurrent `run-group` workers do not race over the
global Kiro session list; separate host processes should still avoid starting
multiple fresh Kiro resident groups at exactly the same time until a provider
scoped session-creation API exists.

For both Codex and Kiro, `live-agent continuity-proof` is the direct
provider-owned context diagnostic. It is intentionally narrower than
`real-session-smoke`: it performs two approved provider turns through the same
resident runner, verifies that turn 2 can recall a suffix from turn 1 without
AgentsAssemble replaying the private continuity code, and reports only safe
booleans, lengths, provider kind, and a short session-id suffix. It does not
join the room, promote evidence, prove stop/restart behavior, or persist
provider approval. `live-agent continuity-proof-group` applies that same proof
to a resident group config, but still refuses to execute provider kinds that do
not have a provider-specific resume adapter.

The resident launch contract now has a small `SandboxLauncher` abstraction. `NoSandboxLauncher` declares `sandbox_enforcement: "advisory"` and does not constrain the child process. Codex uses the Codex read-only launcher and declares `codex_readonly`. Only a provider launched through a verified OS sandbox, restricted worktree, environment scrubber, or equivalent hard boundary may declare `os_sandboxed`.

The `meeting_read_only` permissions sent to `local_cli` and `remote_http_bridge` are currently advisory unless the caller also provides a real sandbox or constrained execution environment. Public artifacts should record this honestly as `enforcement: advisory` or `sandbox_enforcement: "advisory"` depending on the payload shape. Do not claim filesystem, credential, git, or implementation isolation is enforced for arbitrary CLI or bridge processes until a sandboxed launch path exists and is verified.

### Claude Code Channels And Custom Channels

Claude Code Channels are relevant when AgentsAssemble wants to push room events into an already-running Claude Code session. The channel model is not the same as one-shot bridge execution: an event enters the live session, and a two-way channel can expose a reply tool so Claude can answer through the same channel.

If AgentsAssemble builds a custom channel later, it should start read-only, sender-gated, and scoped to council room events. Permission relay should be disabled until the host explicitly designs and verifies approval semantics.

### Memory/Profile Pack Providers

Hermes/OpenClaw-style systems should not be treated as magic live providers until verified. Start with explicit artifacts:

- `persona.md`
- `memory_summary.md`
- `evidence_index.json`
- `permissions.json`
- `provenance.json`
- `risk_review.md`

Imported packs should pass a memory gate before they influence a meeting.
Use `assemble memory-capsule gate --path <capsule-dir> --json` for that first
gate. A passing report proves only that the pack has the minimum auditable
shape and meeting-safe declared permissions; it is not proof that the claims are
true, current, or suitable for a particular meeting.

## External Provider Notes

- Claude: good candidate for API meeting review; Claude Code may join meetings in read-only opinion mode and later return to implementation work after `decision.md`.
- Gemini: good candidate for broad research and Google Search grounding where available.
- Grok: OpenAI-compatible xAI API provider; useful for skeptical critique, but evidence provenance must be strict.
- Cursor: may join meetings in read-only opinion mode and later return to implementation work after `decision.md`.
- Local/Ollama/LM Studio: useful for offline/private fallback and cheap drafts through OpenAI-compatible `/chat/completions`; web research requires a separate search capability.
- Remote bridge: useful when a friend owns a Claude Code session and wants it to participate through an explicit, audited, read-only bridge. It now has two separate operator surfaces: bridge health probes call only `/agentsassemble/health`, while resident live agents opt into `/agentsassemble/run` so they can poll the lobby and auto-reply through the normal live-agent runner.
- Hermes/OpenClaw: memory/profile inspiration, not raw hidden session import.

## Safety Boundaries

Do not pass these into a meeting provider by default:

- raw local files
- credentials or tokens
- hidden provider/session state
- unrelated project memory
- raw imported memory packs
- implementation write permissions
- git push or release permissions

Remote or imported agents should join through explicit context and memory packets, not raw session dumps.

## Host-Approved Runtime Config

`assemble demo --agent-config path/to/agents.json` can load host-approved runtime structure:

- `providers`: configured provider records.
- `permission_profiles`: named permission sets.
- `agent_bindings`: final role-to-agent/provider assignments approved by the host.
- `incoming_agents`: external/self-declared agent profiles retained for audit, not automatically trusted. Public meeting artifacts scrub credential-like fields, endpoints, notes, headers, and token-like strings before storing them.

The orchestrator executes only `agent_bindings`. An `incoming_agents` entry can request a role, persona, memory pack, or permissions, but it does not participate until the host maps it into an approved binding.

## Codex Multi-Session Smoke

`configs/codex-sessions.example.json` has been exercised with `--adapter codex` in smoke mode. The run produced distinct Codex session ids for the three demo roles and wrote per-role Codex last-message files.

Observed limitation: the final moderator synthesis can still fall back to `Undetermined / low confidence` when real Codex output is incomplete or hard to parse. Treat this as adapter reliability work, not a provider-binding failure.

## Next Implementation Slices

1. Add provider-specific evidence provenance for Gemini/Grok web-grounded outputs.
2. Split meeting adapters from implementation/coding-agent adapters.
3. Wire passing memory/profile capsule reports into explicit host admission
   decisions without treating the pack as an executable participant.
4. Add implementation-phase adapters for Cursor and Claude Code after `decision.md`.
5. Keep generation-quality and billing-sufficiency probes separate from provider health. Static provider health (`probe_mode: none`), local loopback OpenAI-compatible `/models` probes (`probe_mode: local`), remote HTTP bridge health probes (`probe_mode: bridge`), explicit Anthropic/Gemini/Grok model-list credential probes (`probe_mode: api`), and resident remote bridge lobby runners are implemented; prompt-bearing generation probes remain future work and must stay explicit.
