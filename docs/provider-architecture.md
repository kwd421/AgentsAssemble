# Provider Architecture

AgentsAssemble should support many providers without making the meeting runner know each provider's internal rules.

The core boundary is:

```text
Role profile -> Agent binding -> Provider config -> Adapter session -> Permission/capability snapshot
```

## Principles

- A role is not a provider. A role describes what the agent is responsible for and how it behaves.
- A binding says which concrete provider/model/session will play that role in this meeting.
- Meeting mode is read-only by default. Implementation-side permissions are rejected during meeting runs.
- Provider capability is explicit. Search, tools, filesystem access, session resume, and structured output cannot be assumed.
- External sessions, memory packs, remote users, and imported context are untrusted until reviewed.
- Meeting artifacts must record provider, permission, capability, and session snapshots for audit and handoff.

## Current Implemented Slice

The current code still runs the same `mock` and `codex` demo paths, but meeting execution now records provider structure:

- `provider_configs`
- `permission_profiles`
- `agent_bindings`
- `provider_capabilities`
- per-role `isolation.*.agent_binding`
- per-role `isolation.*.provider`
- per-role `isolation.*.permissions`
- per-role `isolation.*.capabilities`

The default demo still assigns all roles to one provider. The point of this slice is to make the future role-by-role routing auditable without changing the user-facing demo behavior.

The default registry also exposes planned provider kinds with explicit capability snapshots and unsupported adapters:

- `anthropic`
- `gemini`
- `grok`
- `local_openai_compatible`
- `cursor`
- `claude_code`
- `hermes_memory`
- `openclaw_memory`
- `memory_pack`

These entries make provider intent visible without pretending live integrations exist. Cursor and Claude Code can be represented as read-only meeting participants, but meeting-time validation still rejects implementation-side permissions such as filesystem write, git write, push, or implementation mode.

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

### Memory/Profile Pack Providers

Hermes/OpenClaw-style systems should not be treated as magic live providers until verified. Start with explicit artifacts:

- `persona.md`
- `memory_summary.md`
- `evidence_index.json`
- `permissions.json`
- `provenance.json`
- `risk_review.md`

Imported packs should pass a memory gate before they influence a meeting.

## External Provider Notes

- Claude: good candidate for API meeting review; Claude Code may join meetings in read-only opinion mode and later return to implementation work after `decision.md`.
- Gemini: good candidate for broad research and Google Search grounding where available.
- Grok: optional API provider; useful for skeptical critique, but evidence provenance must be strict.
- Cursor: may join meetings in read-only opinion mode and later return to implementation work after `decision.md`.
- Local/Ollama: useful for offline/private fallback and cheap drafts; web research requires a separate search capability.
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

## Next Implementation Slices

1. Load provider configs and agent bindings from project config files.
2. Allow role-by-role provider routing in `run_demo_meeting`.
3. Add manual external review packets for Claude/Gemini/Grok before full API integrations.
4. Add an importable memory/profile packet schema and memory gate report.
5. Split meeting adapters from implementation/coding-agent adapters.
