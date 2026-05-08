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
- GUI prototype has `실황`, `작전판`, and `아카이브` tabs.
- GUI board shows Evidence Gate totals and per-role evidence tables from structured research JSON.
- Meetings generate per-agent return packets that explain each agent's stance, outcome, evidence state, and next task.
- Research depth profiles exist: `smoke`, `standard`, `deep`.
- Research steering allows user-preferred angles without forcing conclusions.
- Demo meeting rounds are defined through an internal template structure instead of being fully inline in the meeting runner.
- Debate messages track position, stance status, and change conditions so agents do not silently collapse into consensus.
- Evidence Gate separates supported and unsupported claims by evidence URL presence.
- Claim Verifier v0 classifies explicit claim/source relations as supported, weak, contradictory, or irrelevant without pretending to do semantic verification.
- File-based Memory Layer v0 writes project, agent, episode, and reflection memory.
- Research influences are recorded in `docs/research-log.md`.
- Repository operating rules are recorded in `AGENTS.md`.

## V0 Remaining

V0 is complete enough when the demo can be trusted as an auditable meeting prototype, not merely as a scripted transcript.

Remaining v0 work:

- Show Evidence Gate results clearly in the GUI archive view.
- Exercise Claim Verifier v0 with real Codex deep research runs and tune prompt/output reliability.
- Improve Codex deep research timeout and chunking strategy with real runs.
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
- Handoff v0:
  - Generate `handoff.md` from meeting, memory, current tasks, risks, and unresolved questions.
  - Feed per-agent return packets back into persistent sessions when real provider session handoff is implemented.

## V0.2

Goal: make AgentsAssemble useful for real coding workflows while preserving meeting/implementation separation.

- User-defined projects in `projects.yaml`.
- User-defined agents in `agents.yaml`.
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
- Security / abuse resistance:
  - Treat remote users, external agents, tool outputs, retrieved documents, and incoming memory packets as untrusted input.
  - Keep the project defensive-only: security review, permission analysis, prompt-injection checks, context-leak checks, dependency risk review, and patch validation are allowed goals.
  - Do not support credential theft, stealth, persistence, malware deployment, unauthorized exploitation, or third-party system access.
  - Use least-privilege tool grants, explicit permission gates, context firewalls, and audit logs for any remote or multi-user mode.
  - Security-specialized models or trusted cyber access may be used later for defensive review, but they do not remove the need for local permission boundaries and audit artifacts.
- AI roster with durable personalities, memories, styles, and specialties.
- Architecture transfer council between projects.
- Bug war room.
- Refactor debate.
- Multi-candidate implementation tournament.
- Philosophy debate mode using mature agent personas.
- Game-like social deduction or trial themes.
- Rich TUI or polished web UI.
- Cost and usage dashboards.
- Provider capability dashboard.

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
