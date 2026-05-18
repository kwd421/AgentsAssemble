# AgentsAssemble Roadmap

This roadmap freezes the current product direction so new features do not sprawl faster than the council engine can support them.

## Product North Star

AgentsAssemble is a local-first multi-agent council orchestrator for AI coding agents.

The product should make AI agents behave more like a durable software team:

- Agents have roles, providers, permissions, memory, and work contexts.
- Meetings are separate from implementation.
- Meetings produce durable artifacts before implementation begins.
- Evidence, decisions, tasks, and handoffs are written to files.
- Agents can return to their own sessions/worktrees with assigned scope.
- Returning agents must be able to explain the meeting afterward from their own perspective, including what they argued, what changed, why they won or lost, and what they should do next.
- Long-running team context survives session saturation.

The near-term product is not a polished chat app. It is a reliable local council engine with simple terminal and browser surfaces.

## Current Implemented State

- `assemble demo` runs a canned three-agent council.
- Roles are loaded from config with Korean display names and stable English ids.
- Mock adapter supports deterministic local demos.
- Codex adapter supports web-search research calls and non-search debate/synthesis calls.
- Codex adapter has basic structured-output hardening: exact evidence URL instructions, wrapped JSON extraction, synthesis repair, and conservative local fallback.
- Per-role research is isolated before Round 1.
- Meeting artifacts are written as Markdown and JSON.
- GUI prototype has lightweight verification-console tabs: `로비`, `실황`, `작전판`, and `아카이브`.
- GUI board shows Evidence Gate totals and per-role evidence tables from structured research JSON.
- GUI lobby v0 stores unofficial owner/agent chatter, ready events, and deploy intent in `lobby.jsonl` through a typed event model, separately from meeting artifacts.
- GUI size controls let users tune interface and text density for denser meeting review.
- Meetings generate per-agent return packets that explain each agent's stance, outcome, evidence state, and next task.
- Research depth profiles exist: `smoke`, `standard`, `deep`.
- Research steering allows user-preferred angles without forcing conclusions.
- Demo meeting rounds are defined through an internal template structure instead of being fully inline in the meeting runner.
- Meeting runs record a structured `event_log` for major lifecycle events such as start, role session preparation, research, debate, synthesis, and artifact writing.
- Debate messages track position, stance status, and change conditions so agents do not silently collapse into consensus.
- Evidence Gate separates supported and unsupported claims by evidence URL presence.
- Decision Gate v0 records whether a meeting is decided, split, blocked, invalid, still lacking consensus, or needs more research before treating a result as final.
- Claim Verifier v0 classifies explicit claim/source relations as supported, weak, contradictory, or irrelevant without pretending to do semantic verification.
- File-based Memory Layer v0 writes project, agent, episode, and reflection memory.
- Research influences are recorded in `docs/research-log.md`.
- Repository operating rules are recorded in `AGENTS.md`.
- Long-term memory/context engineering research notes now compare MemGPT-style memory tiers, Reflexion-style verbal reflection, LongMemEval-style evaluation questions, and LangGraph Store-style namespaced memory.
- Provider binding groundwork records provider configs, agent bindings, capabilities, and meeting-only permissions in `meeting.json`.
- Room Event Log direction is documented: agents should join one shared room event stream rather than receive isolated interview prompts.
- Live room infrastructure and council workflow are now explicitly separated: free chat and live presence are supported, but official turns, evidence, decisions, tasks, return packets, and memory remain the product core.
- The provider registry now includes live HTTP meeting adapters for Claude/Anthropic, Gemini, Grok, and local OpenAI-compatible providers such as LM Studio. Cursor, Claude Code, and Hermes/OpenClaw-style memory packs remain planned or memory-pack providers with explicit capability records.
- Provider catalog data is available from the registry and GUI API so future UI surfaces can show which providers are available, planned, searchable, filesystem-capable, or memory-pack-only.
- Runtime agent config can be loaded from JSON with host-approved providers, permission profiles, agent bindings, and incoming external agent requests. Incoming agents are recorded for audit but only approved bindings execute.
- Incoming external agents now produce explicit admission decisions in `meeting.json`, separating requested role/provider/permissions from the host-approved binding that actually executes.
- Remote HTTP bridge providers allow a friend-owned Claude Code session to join a meeting as a read-only participant through an audited `/agentsassemble/run` bridge.
- The GUI Lobby can start a resident live-agent session, call one moderator-controlled official round, or run bounded remaining template rounds from the same `상주 실행` panel, using the real live-agent official-turn paths and reporting sanitized reply counts.
- Codex multi-role smoke has been exercised through `configs/codex-sessions.example.json`; distinct role sessions were recorded, but synthesis reliability still needs hardening for real provider output.

## V0 Remaining

V0 is complete enough when the demo can be trusted as an auditable meeting prototype, not merely as a scripted transcript.

Remaining v0 work:

- Show Evidence Gate results clearly in the GUI archive view.
- Exercise Claim Verifier v0 with real Codex deep research runs and tune prompt/output reliability.
- Improve Codex deep research timeout and chunking strategy with real runs.
- Improve Codex synthesis reliability after multi-role smoke produced a conservative `Undetermined / low confidence` fallback.
- Add failure handling policy for role timeout, parse failure, and incomplete research.
- Expand round templates toward user-defined meeting templates.
- Add CLI help/docs for research depth, steering, memory files, and adapter modes.
- Keep mock mode clearly labeled as demo output.

V0 should still exclude:

- Implementation agents editing project code after meetings.
- Git worktree creation.
- Pushes, PRs, releases, and deploys.
- Remote/federated councils.
- Full user-configured template system.
- Vector database or embedding-based memory retrieval.

## V0.1

Goal: make meetings more trustworthy and inspectable.

- Claim Verifier:
  - Expand beyond explicit metadata into provider-assisted claim verification.
  - Compare verifier decisions against source excerpts rather than URL presence alone.
  - Keep rejected verifier claims separate from research notes that agents themselves rejected.
- GUI evidence tables:
  - Show supported claims, unsupported claims, counterclaims, rejected claims, and coverage gaps by role.
  - Make source quality visible without turning the UI into a raw log dump.
- Memory read path:
  - Inject relevant project/agent memory into role research and debate prompts.
  - Keep memory context compact and auditable.
  - Track which memories were loaded into a meeting so later reviews can distinguish model reasoning from retrieved context.
- Handoff v0:
  - Generate `handoff.md` from meeting, memory, current tasks, risks, and unresolved questions.
  - Feed per-agent return packets back into persistent sessions when real provider session handoff is implemented.

## V0.2

Goal: make AgentsAssemble useful for real coding workflows while preserving meeting/implementation separation.

- User-defined projects in `projects.yaml`.
- User-defined agents in `agents.yaml` or equivalent JSON/YAML config.
- Role-by-role provider bindings for Claude, Gemini, Grok, Codex, Cursor, local models, and memory/profile packs through config and the provider registry rather than hardcoded meeting-runner branches.
- User-defined meeting templates.
- Worktree/branch planning without automatic push.
- Per-agent task scopes with file/module ownership.
- Implementation permission model:
  - read-only meeting mode by default.
  - implementation only after `decision.md`.
  - no main-branch direct push by default.
- Conflict detection when multiple agents plan to touch overlapping files.
- Release gate / integrator review template.

## Later

These are intentional future ideas, not near-term commitments.

- Remote/federated councils:
  - One user can host a council server and invite other users or their AI agents into a meeting.
  - Joining should not require a full shared workspace by default.
  - A participant can join with an already-trained/current agent session, preserving that agent's existing persona, memory, and context.
  - A participant can alternatively define a fresh persona, run meeting-specific research, and then join as a newly prepared agent.
  - The host should be able to invite "join immediately with your current persona/context" agents when the user has already prepared a session elsewhere.
  - Shared context must be explicit: meeting agenda, public evidence packet, decision history, selected memory summaries, and permission boundaries.
  - Private context must stay private by default: raw local files, private memory, credentials, unrelated project history, and hidden provider/session state.
  - Memory exchange should favor auditable handoff packets over raw full-context dumps.
  - The meeting should record which participant supplied which context packet and what was withheld.
  - Multiplayer sessions can include a lobby/staging area where owners and agents can talk before the agents are deployed into the formal meeting.
  - A deploy action should release an agent from the lobby into the meeting with its selected persona, memory packet, permissions, and current readiness state.
  - Before deploy, an agent should normally stay idle, but may briefly answer when explicitly addressed by its owner or by name, such as readiness checks or short pre-meeting banter.
  - Agent-to-agent lobby banter can exist for flavor and social presence, but should remain clearly outside the official transcript unless promoted into the meeting record.
  - The UI should distinguish owner chat, lobby banter, deployed meeting turns, and official decisions so playful interaction does not pollute evidence or decisions.
  - A future `live_session adapter` should keep a CLI, SDK, PTY, or socket-backed agent process attached to the room event stream so a prepared session can participate as itself rather than only through a copied packet.
  - Stoops is a useful reference for live room infrastructure, but AgentsAssemble should preserve council workflow: moderator control, official rounds, evidence gates, decision artifacts, task assignment, and handoff packets.
  - Claude Code Channels is a useful reference for pushing external events into a running Claude Code session, but custom channel use should be sender-gated and read-only until permission relay semantics are explicitly designed.
- Security / abuse resistance:
  - Treat remote users, external agents, tool outputs, retrieved documents, and incoming memory packets as untrusted input.
  - Keep the project defensive-only: security review, permission analysis, prompt-injection checks, context-leak checks, dependency risk review, and patch validation are allowed goals.
  - Do not support credential theft, stealth, persistence, malware deployment, unauthorized exploitation, or third-party system access.
  - Use least-privilege tool grants, explicit permission gates, context firewalls, and audit logs for any remote or multi-user mode.
  - Security-specialized models or trusted cyber access may be used later for defensive review, but they do not remove the need for local permission boundaries and audit artifacts.
- AI roster with durable personalities, memories, styles, and specialties.
- Shareable agent memory/profile packs:
  - A well-trained specialist agent, such as a benchmark expert, can later be exported as a reusable persona plus memory/experience package.
  - Other users should be able to import that package and summon the agent's accumulated specialty, sources, habits, and judgment style without retraining from zero.
  - Packs must be explicit artifacts, not raw hidden session dumps: include persona, specialty, memory summaries, source/evidence index, experience log, version, author, trust level, and permission boundaries.
  - Imported packs should be treated as untrusted input until reviewed by a memory gate, especially for prompt injection, stale claims, private data leakage, and unsupported expertise claims.
  - Reference candidates to study later: Hermes-style persistent memory, user modeling, and experience-to-skill accumulation; OpenClaw-style memory/profile sharing if verified from primary sources.
  - Agents may evolve toward a user's workflow over time, but evolution should preserve the agent's recognizable role identity and remain inspectable, editable, resettable, and exportable.
- Memory evaluation suite:
  - Test whether agents can answer prior-meeting questions, temporal update questions, successor handoff questions, and abstention questions without loading raw transcripts wholesale.
  - Use LongMemEval-like categories as inspiration: information extraction, multi-session reasoning, temporal reasoning, knowledge updates, and abstention.
  - Treat memory quality as a product behavior, not only a storage feature.
- Architecture transfer council between projects.
- Bug war room.
- Refactor debate.
- Multi-candidate implementation tournament.
- Philosophy debate mode using mature agent personas.
- Game-like social deduction or trial themes.
- Rich TUI or polished web UI.
- React/Three.js product UI as a separate future track, rather than making the dependency-light GUI v0 carry cinematic staging.
- Cost and usage dashboards.
- Provider capability dashboard.

## Provider Architecture Notes

Detailed provider architecture lives in `docs/provider-architecture.md`.

Current direction:

- API providers such as Claude, Gemini, Grok, and OpenAI-compatible local models are meeting/research providers.
- Shell-driven coding agents such as Claude Code and Cursor should be implementation-phase providers after `decision.md`; Codex is currently usable as a meeting adapter and later implementation provider.
- Hermes/OpenClaw-style systems are memory/profile pack inspirations first, not raw session import mechanisms.
- External or remote agents must join with explicit provider, permission, context, and memory packet metadata.
- A host admission decision must separate an incoming agent's requested identity from the effective role, provider, and permission profile that are allowed to execute.

## Non-Goals For Now

- Do not build a general chatroom.
- Do not make implementation happen inside the meeting by default.
- Do not optimize for theatrical UI before evidence, memory, and handoff are trustworthy.
- Do not add external services or databases until file-based storage is insufficient.
- Do not treat long transcripts as memory. Summaries, decisions, evidence maps, and reflections are the memory units.
- Do not let research steering become forced advocacy.
- Do not let unsupported claims silently influence decisions.

## Research Threads

Current research references live in `docs/research-log.md`.

Important threads:

- Generative Agents: memory, reflection, durable persona behavior.
- MemGPT: managed context and memory tiers.
- AutoGen/CAMEL/MetaGPT: multi-agent roles, group conversation, software-team workflows.
- Memory surveys: semantic, episodic, procedural memory and memory governance.
- Future debate/verification papers: claim verification and moderator design.

## Acceptance Criteria For The Next Slice

The next implementation slice should satisfy all of these:

- It advances v0 or v0.1 rather than a later playful mode.
- It keeps file-based persistence.
- It includes tests for the changed behavior.
- It records any new research influence in `docs/research-log.md`.
- It keeps meeting and implementation separate.
- It does not introduce push, PR, release, or deploy behavior.

## Roadmap Maintenance

Update this roadmap when a change alters implemented capability, priority, non-goals, acceptance criteria, or recommended next steps.

Do not update it for mechanical refactors, formatting, tests, or internal cleanup unless they change product direction or delivery confidence.

Prefer updating the roadmap in the same commit as the capability change, or in the next dedicated documentation commit.

User requests are not automatically roadmap items. Promote a request into the roadmap only when it changes product direction, priority, non-goals, acceptance criteria, or a committed future slice.

If concrete observed issues start competing with roadmap work, create `docs/known-issues.md`. Until then, keep issue tracking inside this roadmap or the current task context.

When the user asks what to do next, brief from this roadmap, any known-issues file if it exists, recent commits, and the current worktree state.

## Recommended Next Step

Implement GUI Evidence Tables or run Codex deep research against Claim Verifier v0.

GUI Evidence Tables are the stronger product-feedback step. A Codex deep research run is the stronger reliability step because it tests whether real adapter output gives the verifier useful explicit relations.
