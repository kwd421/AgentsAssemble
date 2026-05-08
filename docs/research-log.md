# Research Log

This file records external papers, frameworks, and product references used while designing AgentsAssemble. Each entry should explain what was borrowed, what was not borrowed, and whether the idea has been implemented, deferred, or only discussed.

## Logging Policy

- Record a source whenever it materially shapes product direction, architecture, prompts, evidence rules, UI concepts, or future roadmap.
- Separate implemented influence from speculative inspiration.
- Do not treat papers or frameworks as authority by default. Translate only the useful parts into AgentsAssemble's local-first council model.
- Prefer stable links such as arXiv, ACM/Stanford project pages, official docs, or repository docs.
- When an implementation change is made from a source, include the commit hash after the commit exists.

## Sources

### Generative Agents: Interactive Simulacra of Human Behavior

- Link: https://arxiv.org/abs/2304.03442
- Authors: Joon Sung Park, Joseph C. O'Brien, Carrie J. Cai, Meredith Ringel Morris, Percy Liang, Michael S. Bernstein
- Type: paper
- Used for:
  - Future agent memory model ideas: experience logs, reflection, retrieval, and believable long-lived persona behavior.
  - Future "AI roster" direction where agents can develop personality, memory, work history, and social continuity across meetings.
  - Future playful modes such as philosophy salons, social simulations, and game-like council rooms.
- Not used for:
  - Current v0 execution architecture.
  - Current meeting persistence format.
  - Any claim that AgentsAssemble already simulates autonomous daily life or social emergence.
- Implementation status: discussed only.
- Related roadmap ideas:
  - Agent handoff memory.
  - Long-term role/persona evolution.
  - Per-agent reflection files.
  - AI community or town-like future interface.

### CAMEL Agent Societies

- Link: https://docs.camel-ai.org/key_modules/societies
- Type: framework documentation
- Used for:
  - Role-based agent society vocabulary.
  - Turn-based collaboration as a useful comparison point for meeting rounds.
  - Critic/reviewer roles as a future council pattern.
- Not used for:
  - Runtime dependency.
  - Current adapter implementation.
- Implementation status: partially aligned by design, not imported.
- Related current design:
  - AgentsAssemble roles are fixed during a meeting.
  - Meeting rounds are structured rather than open-ended chat.

### AutoGen: Enabling Next-Gen LLM Applications via Multi-Agent Conversation

- Link: https://arxiv.org/abs/2308.08155
- Type: paper/framework
- Used for:
  - General comparison with multi-agent conversation systems.
  - Future speaker selection and group conversation design.
  - Future tool-using agent orchestration references.
- Not used for:
  - Current local-first artifact model.
  - Current session adapter implementation.
- Implementation status: discussed only.
- Related roadmap ideas:
  - More flexible meeting moderator policies.
  - Dynamic agent join/leave.
  - Tool-using council participants.

### MetaGPT: Meta Programming for A Multi-Agent Collaborative Framework

- Link: https://arxiv.org/abs/2308.00352
- Type: paper/framework
- Used for:
  - Software-company role decomposition as a reference point.
  - Future templates for PM, architect, engineer, QA, release gate, and integrator workflows.
- Not used for:
  - Current v0 fixed One Piece debate demo.
  - Current code generation workflow.
- Implementation status: discussed only.
- Related roadmap ideas:
  - Feature planning council.
  - Architecture council.
  - Release decision council.
  - Implementation task delegation by role.

### MemGPT: Towards LLMs as Operating Systems

- Link: https://arxiv.org/abs/2310.08560
- Type: paper/system architecture
- Used for:
  - Candidate Memory Layer architecture.
  - Treating context as a managed resource rather than a single ever-growing transcript.
  - Separating small always-loaded working context from larger searchable/archival memory.
  - Future handoff and context-saturation handling.
- Not used for:
  - Current v0 runtime.
  - Current prompt assembly.
  - Any automatic memory paging or retrieval.
- Implementation status: candidate for Memory Layer.
- Related roadmap ideas:
  - Project memory, agent memory, episode log, and archival meeting memory.
  - Context budget planning before starting long meetings.
  - Handoff generation when a session becomes saturated.

### Memory Matters: The Need to Improve Long-Term Memory in LLM-Agents

- Link: https://ojs.aaai.org/index.php/AAAI-SS/article/view/27688
- Type: paper
- Used for:
  - Candidate memory taxonomy and long-term agent design.
  - Separating memory into semantic, episodic, and procedural categories.
  - Highlighting metadata and lifetime memory management as design concerns.
- Not used for:
  - Current storage schema.
  - Current retrieval logic.
- Implementation status: candidate for Memory Layer.
- Related roadmap ideas:
  - Semantic memory: stable project facts, preferences, architecture decisions.
  - Episodic memory: meetings, incidents, handoffs, debates, failed attempts.
  - Procedural memory: repeatable workflows, testing rituals, release gates.

### A Survey on the Memory Mechanism of LLM-based Agents

- Link: https://arxiv.org/abs/2404.13501
- Type: survey paper
- Used for:
  - Candidate overview of memory mechanisms for LLM agents.
  - Future comparison of file-based memory, vector retrieval, summarization, and reflection.
- Not used for:
  - Current implementation.
- Implementation status: candidate for Memory Layer.
- Related roadmap ideas:
  - Choosing a minimal file-first memory architecture before adding embeddings or a database.
  - Evaluating whether memory improves handoff quality and reduces repeated mistakes.

### Memory for Autonomous LLM Agents: Mechanisms, Evaluation, and Emerging Frontiers

- Link: https://arxiv.org/abs/2603.07670
- Type: survey paper
- Used for:
  - Candidate guide for production-grade memory concerns.
  - Write-path filtering, contradiction handling, latency budgets, privacy governance, and multi-agent teamwork memory.
  - Connecting Evidence Gate with future Memory Gate behavior.
- Not used for:
  - Current Evidence Gate implementation.
  - Current meeting artifact schema.
- Implementation status: candidate for Memory Layer.
- Related roadmap ideas:
  - Memory write approval/gating.
  - Contradiction detection between old memory and new meeting decisions.
  - Memory privacy boundaries per project, role, and provider.
  - Evaluation metrics for whether memory actually improves future meetings.

## Implemented Research-Informed Decisions

### Structured Council Rounds

- Influence: General multi-agent collaboration patterns from CAMEL, AutoGen, MetaGPT, and ChatDev-like software-team framing discussed during product interview.
- Implemented as:
  - Round 1 independent opinion.
  - Round 2 rebuttal and evidence comparison.
  - Moderator synthesis.
  - Persistent Markdown/JSON artifacts.
- Current files:
  - `agentsassemble/meeting.py`
  - `agentsassemble/artifacts.py`
- Commit: pre-log v0 bootstrap commits.

### Evidence Gate

- Influence: Debate/review-oriented product reasoning discussed with the user, plus broader research habit from evidence-aware multi-agent systems.
- Implemented as:
  - Claims without evidence URLs are moved to `unsupported_claims`.
  - Evidence URLs must appear in the role's `sources` list.
  - Depth requirements can lower confidence when source, claim, or counterclaim counts are insufficient.
  - Moderator context is told not to base decisions on unsupported claims.
- Current files:
  - `agentsassemble/evidence.py`
  - `agentsassemble/meeting.py`
  - `agentsassemble/artifacts.py`
- Commit: `791d55f Add evidence gate and research steering`

### Research Depth Profiles

- Influence: Comparison with commercial deep research products and the user's requirement for dense, auditable investigation.
- Implemented as:
  - `smoke`, `standard`, and `deep` research profiles.
  - Source, query, claim, counterclaim, and note-count targets.
  - Codex adapter prompts that request dense evidence archives.
- Current files:
  - `agentsassemble/models.py`
  - `agentsassemble/adapters/codex.py`
  - `agentsassemble/adapters/mock.py`
  - `agentsassemble/meeting.py`
- Commit: `b778276 Add research depth profiles`

## Future Research Topics

- Debate and deliberation papers for claim verification, adversarial review, and judge/moderator design.
- Long-term memory and reflection architectures for durable agent identity and handoff, especially MemGPT-style virtual context and Generative Agents-style reflection.
- Human-computer interaction papers on meeting support, decision logs, and collaborative sensemaking.
- Game UI and social deduction design references for playful council themes.

## Candidate Memory Layer Translation

These are not yet implemented. They are the current design hypotheses for translating memory research into AgentsAssemble.

- Working context: small, always-loaded meeting instructions, current question, selected relevant memories, and active role persona.
- Semantic memory: stable facts and decisions such as project architecture, user preferences, coding standards, provider permissions, and standing constraints.
- Episodic memory: time-stamped events such as meetings, debugging sessions, handoffs, failed attempts, release decisions, and conflict resolutions.
- Procedural memory: repeatable workflows such as how to run tests, how to cut a release, how to review a PR, or how to migrate architecture patterns.
- Reflection: post-meeting synthesis that extracts lessons, unresolved risks, and behavior changes for each agent.
- Handoff memory: compact successor packet generated when a session is saturated or an agent is replaced.
- Memory gate: future rule that decides what is allowed to enter long-term memory, rejects unsupported claims, and flags contradictions with existing memory.
