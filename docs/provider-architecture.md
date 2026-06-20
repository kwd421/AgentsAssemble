# Provider Architecture

AgentsAssemble should support many providers without making the meeting runner know each provider's internal rules.

Current user-facing model: **Agent Session**.

Provider, adapter, runner, bridge, delegate, MCP, one-shot, and baseline are
internal or historical implementation terms. The product surface should expose
one concept: an Agent Session attached to a room, with connection status,
session identity, model, effort, sandbox/permission diagnostics, and joined,
left, detached, kicked, or exported state.

The legacy provider boundary remains useful internally:

```text
Role profile -> Agent binding -> Provider config -> Agent Session -> Permission/capability snapshot
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
- `antigravity_live_session`
- `anthropic`
- `gemini`
- `grok`
- `grok_live_session`
- `grok_build_cli`
- `local_openai_compatible`
- `remote_http_bridge`
- `local_cli`
- `cursor`
- `claude_code`
- `antigravity_cli`
- `gemini_cli_legacy`
- `hermes_cli`
- `hermes_live_session`
- `openclaw_cli`
- `hermes_memory`
- `openclaw_memory`
- `memory_pack`

`anthropic`, `gemini`, `grok`, `local_openai_compatible`, `remote_http_bridge`, `local_cli`, and `codex_live_session` have meeting adapters. `kiro_live_session`, `cursor_live_session`, `grok_live_session`, `antigravity_live_session`, and `hermes_live_session` are resident live-agent adapters, not meeting adapters: Kiro joins through the resident runner and Kiro's own chat resume store, Cursor joins through `cursor-agent create-chat` plus `--resume` with a runner-owned stable workspace, Grok joins through JSON stdout `--resume`, Antigravity joins through `agy --conversation <conversation_id>`, and Hermes joins through `hermes chat --resume <session_id>`. `cursor`, `claude_code`, `antigravity_cli`, `gemini_cli_legacy`, `grok_build_cli`, `hermes_cli`, and `openclaw_cli` remain implementation-phase planned or generic providers unless they are launched through the resident live-agent runner's explicit connection-kind contract; meeting-time validation still rejects implementation-side permissions such as filesystem write, git write, push, or implementation mode.

Antigravity was promoted only for the narrow `antigravity_live_session`
contract. Fresh local evidence on 2026-05-28 showed separate
`agy --conversation <id>` A/B sessions, fresh no-resume controls that did not
recall either private tag, and `.antigravitycli` sidecar state staying inside
the proof root. The proof still records that Antigravity often wraps the
requested suffix in extra text, so continuity proof separates exact formatting
from provider-owned recall.

Hermes was promoted only for the narrow `hermes_live_session` contract. Fresh
local evidence on 2026-05-28 with `hermes chat --ignore-user-config
--ignore-rules --source ... --pass-session-id` showed distinct A/B session ids,
`--resume <a>` recalling only A, `--resume <b>` recalling only B, and fresh
no-resume controls returning no private tag. Hermes may also wrap the recalled
suffix in explanatory text, so continuity proof labels the match mode instead
of pretending exact one-token output was proven.

Discovery now surfaces `agy`/legacy `antigravity` and `hermes` as
approval-required provider-specific live-session residents when those CLIs are
installed. The older `antigravity_cli` and `hermes_cli` kinds remain generic
planned-provider lanes; they are not used to label one-shot local CLI calls as
live sessions.

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

### Disabled Claude Print-Mode Bridge

The old Claude Code print-mode bridge is disabled and must fail closed. It must
not run `claude -p`, must not be documented as a supported path, and must not
fall back to an Anthropic API.

The fail-closed message is:

```text
Claude print-mode bridge is disabled. AgentsAssemble requires resumable local Agent Sessions; do not use claude -p.
```

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

Resident `self_service` is the first local process-supervision slice that stops AgentsAssemble from injecting each room prompt into the provider process. The supervisor registers the live agent, starts the configured command with `stdin` closed, exports `AGENTSASSEMBLE_*` environment variables plus shell-escaped room command templates such as `AGENTSASSEMBLE_WAIT_NEXT_COMMAND`, `AGENTSASSEMBLE_SAY_COMMAND_TEMPLATE`, `AGENTSASSEMBLE_OFFICIAL_REPLY_COMMAND_TEMPLATE`, `AGENTSASSEMBLE_HEARTBEAT_COMMAND_TEMPLATE`, and `AGENTSASSEMBLE_LEAVE_COMMAND`, and lets the child call `wait-next`, `say`, `official-reply`, `heartbeat`, and intentional `leave` itself after splitting those templates into argv and replacing placeholders. Use it for custom wrappers, including a future Antigravity wrapper, only when that wrapper truly owns its own room loop. `scripts/my_self_service_agent.py` is the runnable local example for that wrapper shape.

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

Local stdio MCP is an internal/legacy adapter over the same room contract, not
the current user-facing Agent Session connection model. It is not part of the
normal quickstart path and should not be presented as a provider choice for
joining a room. Its historical participant tools write through existing GUI HTTP
endpoints; `dm_reply` writes only to the saved-friend direct DM log for the
triggering event and does not write lobby or official meeting records.
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

`codex_live_session` is the first Codex-specific live-session slice. Meeting turns use Codex CLI session ids and `codex exec resume` so repeated turns can continue the same Codex session history. Resident live-agent configs use `provider_kind: "codex_live_session"` with `connection_kind: "live_session"`; both the meeting adapter and resident runner call Codex CLI through `codex exec --sandbox read-only --ignore-user-config --ignore-rules` / `codex exec --sandbox read-only --ignore-user-config --ignore-rules resume` rather than through the JSONL fake-session protocol. The explicit `--sandbox read-only` flag is the safety input, `--ignore-user-config` prevents a broken local `config.toml` from killing resident room calls while keeping Codex auth available, `--ignore-rules` keeps repository `.rules` files from participating in the launch, and Codex CLI still owns the actual enforcement. Public artifacts record that path as `sandbox_enforcement: "codex_readonly"`. This is not native Codex/Claude channel injection, OS-level sandboxing, or a substitute for a future constrained launch path for arbitrary CLIs.

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

`grok_live_session` is the first Grok-specific resident continuity slice. It
uses the local `grok` executable with a runner-owned prompt file, JSON stdout,
and explicit `--resume <sessionId>` after the first call captures a safe session
id. The runner reads only JSON stdout `text` as the public reply, ignores stderr
as reply/proof material because Grok process logs can echo prompt text, and
persists only safe session id state inside the runner. Public continuity-proof
artifacts may record provider kind, connection kind, safe join semantics
(`grok_session_resume`), booleans, lengths, and a short session-id suffix, but
must not store raw prompts, provider output, stderr, command tails, account
data, or local prompt-file paths. Grok launch safety is currently `advisory`;
there is no AgentsAssemble-owned hard sandbox for this adapter.

For Codex, Kiro, Cursor, Grok, Antigravity, and Hermes, `live-agent
continuity-proof` is the direct provider-owned context diagnostic. It is
intentionally narrower than
`real-session-smoke`: it performs two approved provider turns through the same
resident runner, verifies that turn 2 can recall a suffix from turn 1 without
AgentsAssemble replaying the private continuity code, and reports only safe
booleans, lengths, provider kind, and a short session-id suffix. It does not
join the room, promote evidence, prove stop/restart behavior, or persist
provider approval. `live-agent continuity-proof-group` applies that same proof
to a resident group config, but still refuses to execute provider kinds that do
not have a provider-specific resume adapter.

For Cursor, the runner creates a fresh Cursor chat id when needed and then calls
`cursor-agent --resume` with one runner-owned workspace directory for its
lifetime. Both the chat id and workspace are part of the proven continuity key.
A later one-resident approved real room smoke proved bounded start/probe/stop
for the local install with safe counts only. This is still not a Cursor
official-turn proof, restart proof, recover proof, tool-safety proof, future
billing proof, production-readiness proof, or sandbox proof.

Discovery keeps the older generic `cursor-agent` terminal-session row visible as
`superseded` evidence/back-compat only. It is not written into generated resident
configs, is not an approval target, and fails closed during preflight or run
validation with a message pointing operators to `cursor-agent-live-session`.

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
