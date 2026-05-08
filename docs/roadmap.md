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
- Long-running team context survives session saturation.

The near-term product is not a polished chat app. It is a reliable local council engine with simple terminal and browser surfaces.

## Current Implemented State

- `assemble demo` runs a canned three-agent council.
- Roles are loaded from config with Korean display names and stable English ids.
- Mock adapter supports deterministic local demos.
- Codex adapter supports web-search research calls and non-search debate/synthesis calls.
- Per-role research is isolated before Round 1.
- Meeting artifacts are written as Markdown and JSON.
- GUI prototype has `실황`, `작전판`, and `아카이브` tabs.
- Research depth profiles exist: `smoke`, `standard`, `deep`.
- Research steering allows user-preferred angles without forcing conclusions.
- Evidence Gate separates supported and unsupported claims by evidence URL presence.
- File-based Memory Layer v0 writes project, agent, episode, and reflection memory.
- Research influences are recorded in `docs/research-log.md`.
- Repository operating rules are recorded in `AGENTS.md`.

## V0 Remaining

V0 is complete enough when the demo can be trusted as an auditable meeting prototype, not merely as a scripted transcript.

Remaining v0 work:

- Show Evidence Gate results clearly in the GUI archive and board views.
- Add a basic Claim Verifier design or first implementation.
- Improve Codex deep research prompt/output reliability with real runs.
- Add failure handling policy for role timeout, parse failure, and incomplete research.
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
  - Classify claim/source pairs as `supports`, `contradicts`, `weak`, or `irrelevant`.
  - Lower confidence when a claim is weakly supported.
  - Prevent moderator synthesis from using unsupported or irrelevant claims as decisive evidence.
- GUI evidence tables:
  - Show supported claims, unsupported claims, counterclaims, rejected claims, and coverage gaps by role.
  - Make source quality visible without turning the UI into a raw log dump.
- Memory read path:
  - Inject relevant project/agent memory into role research and debate prompts.
  - Keep memory context compact and auditable.
- Handoff v0:
  - Generate `handoff.md` from meeting, memory, current tasks, risks, and unresolved questions.

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

- Remote/federated councils where another user's AI agent can join.
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

## Recommended Next Step

Implement Claim Verifier v0 or GUI Evidence Tables.

Claim Verifier is the stronger engine step. GUI Evidence Tables are the stronger product-feedback step. If uncertain, implement Claim Verifier first because it improves the quality of data that the UI will display later.
