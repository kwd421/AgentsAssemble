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

### Webdesign Inspiration Visit-Site References

- Link: https://www.webdesign-inspiration.com/web-designs/style/dark
- Type: design reference directory
- Important note:
  - The directory itself is not the design target. The useful references are the external `Visit Site` targets curated there.
- External sites inspected:
  - UI Magic: https://uimagic.co/
  - Postiz: https://postiz.com/
  - Topview AI: https://www.topview.ai/
  - Q Industrial: https://www.q-industrial.com/
  - Igloo: https://www.igloo.inc/
- Observed design patterns:
  - Strong first-viewport focal object: a product surface, generated scene, oversized tool input, or physical object gives the page a visual anchor.
  - Mobile views are recomposed, not merely scaled down. The hero object, headline, primary action, and navigation are reprioritized.
  - Navigation collapses early on narrow screens; secondary controls are hidden or deferred.
  - High-quality dark designs use fewer stronger surfaces, not many similar cards.
  - Typography carries much of the polish: large confident headings, disciplined body widths, and compact utility labels.
  - Visual depth comes from one large scene or object plus restrained overlays, not from repeating glows and borders everywhere.
  - Tool/product pages often put the main interactive surface in the first viewport, while brand/editorial pages use a full-scene object.
- Implementation mechanics observed:
  - UI Magic uses Framer-hosted assets and script output rather than a small hand-authored static page; the polished feel comes from large typography, cropped mockup imagery, and responsive composition.
  - Postiz uses a Next.js static bundle with many SVG/icon assets; the desktop nav collapses to a compact mobile menu and the hero preserves headline/CTA/social icon priority.
  - Topview uses a Next.js/Vercel bundle and CDN-hosted media, including video assets; the first viewport is a product-like prompt surface plus category launcher.
  - Q Industrial uses Webflow CSS/JS, external animation libraries, and video/image assets; the desktop hero relies on a large product object, while mobile re-centers that object and defers secondary navigation.
  - Igloo uses a minimal HTML shell with a bundled JS app and a heavy full-viewport 3D/scene treatment; it is useful as atmosphere reference, but risky as a direct implementation target for a lightweight local app.
- Implementation constraints for AgentsAssemble:
  - Do not copy external CSS/JS. Translate patterns into local, dependency-light HTML/CSS/JS.
  - Prefer a small number of optimized local bitmap assets over CDN-heavy or framework-generated asset piles.
  - Use CSS grid/flex, responsive containment, and `clamp()` before adding frontend dependencies.
  - Keep motion subtle and transform/opacity-based; avoid expensive WebGL-style scene work until the product deserves that cost.
  - Maintain existing asset budget tests and add tests when new visual hooks become part of the product contract.
- What this explains about the earlier AgentsAssemble GUI:
  - It looked prototype-like because `topbar + tabs + bordered panels + repeated cards` dominated the screen.
  - The lobby, live, board, and archive tabs had different purposes but too similar a container model.
  - The UI had role colors and panels, but lacked a single strong stage/focal object per tab.
  - Mobile behavior stacked sections, but did not sufficiently recompose each tab around its primary job.
  - The chat and roster surfaces looked functional, but not yet like a commercial messaging or operations UI.
- Translation for AgentsAssemble:
  - Treat each tab as a scene with one primary visual job:
    - Lobby: staging room and deploy readiness.
    - Live: official council stage and transcript.
    - Board: stance/evidence map.
    - Archive: owner-separated document vault.
  - Reduce repeated card clutter; reserve bordered panels for actual tools, rosters, records, and documents.
  - Keep shell controls quieter than the active tab's primary scene.
  - Recompose mobile views around the active tab's most important surface.
  - Test visual work with desktop and mobile screenshots, not only unit tests.
- Implementation status:
  - First applied to the lobby staging room and common shell.
- Current files:
  - `agentsassemble/static/app.js`
  - `agentsassemble/static/styles.css`
  - `tests/test_static_ui_assets.py`
- Commit: `27e4001 Refine lobby staging room layout`

### Deeper Frontend Mechanics Notes

- Date: 2026-05-09
- Reason:
  - The first pass was too visual and not implementation-specific enough. This pass records concrete frontend mechanics that should influence AgentsAssemble GUI work.
- Sites re-inspected:
  - UI Magic: https://uimagic.co/
  - Postiz: https://postiz.com/
  - Topview AI: https://www.topview.ai/
  - Q Industrial: https://www.q-industrial.com/
  - Igloo: https://www.igloo.inc/
- Implementation signals observed:
  - UI Magic:
    - Framer output, no linked CSS file, heavy inline generated layout.
    - Rough page signals: about 378 KB HTML, 79 images, 68 `srcset` references, 27 lazy images, 12 high-priority image hints.
    - Useful lesson: the premium feel comes from large cropped visual surfaces and responsive image selection, not from many small bordered UI panels.
  - Postiz:
    - Next.js page with a single CSS bundle around 87 KB, many optimized image tags, and compact mobile logo/menu variants.
    - Rough page signals: 175 images, 166 lazy images, 36 `srcset` references, 3 preloads, desktop/mobile logo swap.
    - CSS signals: multiple 767px mobile breakpoints, transform and transition use, limited grid and more flex composition.
    - Useful lesson: commercial app pages make the main communication/product surface visually obvious, then defer secondary details.
  - Topview AI:
    - Next.js/Vercel app with large CSS bundles, many CDN media references, and high-priority hero assets.
    - Rough page signals: about 1.7 MB HTML, 105 images, 1 video, 104 preloads, 3 high-priority image hints.
    - CSS signals: breakpoint layers at 640px, 768px, 1024px, 1280px, 1400px; many transform/transition hooks and named keyframes such as fade/scale/slide/float/shimmer.
    - Useful lesson: motion polish comes from many small transform/opacity transitions, but AgentsAssemble should not import that weight for a local-first tool.
  - Q Industrial:
    - Webflow site with a large shared CSS file around 149 KB, videos, lazy media, and strict breakpoint layers.
    - CSS signals: common Webflow breakpoints at 991px, 767px, 479px; many flex layouts, several grids, and a few transform/transition hooks.
    - Useful lesson: strong hero scenes use viewport-aware sections and large object framing; mobile is a redesigned composition, not a compressed desktop.
  - Igloo:
    - Minimal HTML shell with a bundled JS app and strong full-viewport visual scene.
    - Useful lesson: the perceived quality is scene direction and interaction, not many visible controls. Directly copying this would risk weight and complexity.
- Mechanics to translate into AgentsAssemble:
  - Use one strong visual job per tab:
    - Lobby: assembly staging and participant readiness.
    - Live: official council stage plus transcript.
    - Board: decision map with stance and evidence compression.
    - Archive: durable document vault grouped by owner and artifact type.
  - Use explicit responsive breakpoints around 860px and 560px now, but design each tab to recompose at those points instead of only stacking.
  - Keep premium polish dependency-light:
    - Prefer local optimized bitmap/SVG assets and CSS gradients over framework-heavy bundles.
    - Keep motion to `transform` and `opacity`.
    - Avoid video/WebGL until the product surface justifies it.
  - Avoid the previous prototype failure mode:
    - Do not make every tab `topbar + tabs + bordered panel + repeated cards`.
    - Do not give the shell more visual weight than the active scene.
    - Do not use many similar cards as the primary composition.
    - Do not rely on color accents alone to distinguish ownership, role, or state.
- Open design risk:
  - Current GUI is still below the reference quality bar. The research supports the direction, but each tab still needs browser screenshot review after implementation.

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

## Long-Term Memory And Context Engineering Research

### Initial Source Sweep

- Date: 2026-05-09
- Reason:
  - AgentsAssemble depends on durable agent identity, handoff, meeting recall, and cross-session continuity. The memory layer should not become raw transcript hoarding.
- Sources checked:
  - MemGPT: https://arxiv.org/abs/2310.08560
  - Reflexion: https://arxiv.org/abs/2303.11366
  - LongMemEval: https://arxiv.org/abs/2410.10813
  - LangGraph/LangChain long-term memory docs: https://docs.langchain.com/oss/python/langchain/long-term-memory
  - Generative Agents remains an existing candidate reference from the earlier roadmap.
- What each source contributes:
  - MemGPT:
    - Treats context as virtual memory with tiers rather than trying to stuff everything into the prompt.
    - Useful translation: AgentsAssemble should have explicit hot context, working meeting context, searchable memory, archival artifacts, and interrupt-like handoff moments.
  - Reflexion:
    - Improves future trials through verbal reflection stored in episodic memory rather than model weight updates.
    - Useful translation: after a meeting, implementation attempt, failed test, or lost debate, each agent should write a short reflection with evidence, mistake, and future behavior adjustment.
  - LongMemEval:
    - Evaluates long-term memory through information extraction, multi-session reasoning, temporal reasoning, knowledge updates, and abstention.
    - Useful translation: AgentsAssemble memory should be tested on whether agents can answer "what changed?", "when did we decide this?", "which older fact was superseded?", and "what should I refuse to infer?".
  - LangGraph Store:
    - Models long-term memory as JSON documents organized by namespace and key, separate from short-term thread state.
    - Useful translation: keep the current file-first direction, but structure memory with clear namespaces: project, user, agent, episode, decision, procedure, and imported pack.
- Proposed memory tiers for AgentsAssemble:
  - Hot context:
    - Current meeting agenda, selected roles, active task, immediate constraints, and a compact source packet.
    - Always small enough to inspect.
  - Working context:
    - Current thread/meeting state, recent turns, open questions, temporary scratch.
    - Expires or gets summarized after the meeting.
  - Semantic memory:
    - Stable facts: project conventions, architecture decisions, user preferences, provider permissions, role definitions.
    - Updated only through a memory gate.
  - Episodic memory:
    - Time-stamped meetings, implementation attempts, failures, wins, handoffs, and conflict resolutions.
    - Supports "why did I lose/win?" and "what happened last time?" questions.
  - Procedural memory:
    - Repeatable workflows: how to run tests, how to review releases, how to prepare packets, how to split work.
  - Reflection memory:
    - Short self-critiques after outcomes, similar to Reflexion, but with evidence and scope.
  - Archive:
    - Full artifacts such as transcript, decision, task files, research JSON, and handoff packets.
    - Not automatically loaded; referenced or summarized.
- Design implications:
  - Memory is not fine-tuning. It changes behavior through retrieval, summaries, reflection, and prompts, while remaining inspectable and editable.
  - The memory system needs write gates:
    - Save facts only when source/evidence is known.
    - Mark superseded facts instead of silently overwriting.
    - Keep private memory, imported packs, and remote user packets separated.
    - Add abstention rules when memory is missing or contradictory.
  - The GUI should eventually show memory provenance:
    - What memory was loaded.
    - Which meeting or source created it.
    - Whether it is stable, stale, disputed, imported, or private.
- Candidate evaluation questions:
  - Can an agent explain a previous meeting outcome from its own perspective?
  - Can a successor session reconstruct what to do next without raw transcript dumping?
  - Can the system identify when an older decision was superseded?
  - Can it refuse to answer when memory is missing or conflicting?
  - Can an imported specialist pack be reviewed before it influences a meeting?
- Open research items:
  - Verify MemPalace from primary sources before treating it as a design reference.
  - Verify Hermes/OpenClaw memory/profile evolution claims from primary sources before including them as concrete product commitments.
  - Compare file-first memory against vector/embedding retrieval only after V0.1 proves manual retrieval is insufficient.

## External Provider And Agent Integration Research

### Initial Provider Sweep

- Date: 2026-05-09
- Reason:
  - The product should eventually summon Claude, Gemini, Grok, Cursor, local models, and memory/profile agents into councils without collapsing meeting, implementation, memory, and permission boundaries.
- Sources checked:
  - Anthropic Claude web search/tool use docs: https://docs.claude.com/en/docs/agents-and-tools/tool-use/web-search-tool
  - Claude Code CLI/headless docs: https://code.claude.com/docs/en/headless
  - Gemini function calling docs: https://ai.google.dev/gemini-api/docs/function-calling
  - Gemini Grounding with Google Search docs: https://ai.google.dev/gemini-api/docs/grounding
  - xAI API docs: https://x.ai/api/
  - xAI function calling docs: https://docs.x.ai/docs/guides/function-calling
  - Cursor CLI docs: https://docs.cursor.com/en/cli/using
  - Ollama API docs: https://docs.ollama.com/api
  - Ollama OpenAI compatibility docs: https://docs.ollama.com/api/openai-compatibility
  - Hermes memory docs: https://hermes-agent.nousresearch.com/docs/user-guide/features/memory/
- Translation for AgentsAssemble:
  - Claude/Gemini/Grok and local OpenAI-compatible APIs are meeting provider candidates.
  - Codex, Claude Code, and Cursor are better modeled as implementation-phase coding agents when write permissions are approved.
  - Hermes/OpenClaw-like systems should start as reviewed memory/profile packets, not raw hidden session imports.
  - External provider support requires explicit provider configs, agent bindings, capability profiles, permission profiles, and session snapshots.
- Implemented slice:
  - Added provider config, agent binding, capability, and permission models.
  - Added provider registry and validation for meeting-only permissions.
  - Meeting artifacts now record provider configs, agent bindings, provider capabilities, and permission profiles while preserving existing mock/codex behavior.
  - Added admission decisions for incoming external agents so host approval, effective bindings, rejected requests, and non-executed requests are auditable.
  - Added HTTP meeting adapters for Anthropic Messages API, Gemini `generateContent`, xAI/OpenAI-compatible chat completions, and local OpenAI-compatible servers such as LM Studio.

## Candidate Memory Layer Translation

These are not yet implemented. They are the current design hypotheses for translating memory research into AgentsAssemble.

- Working context: small, always-loaded meeting instructions, current question, selected relevant memories, and active role persona.
- Semantic memory: stable facts and decisions such as project architecture, user preferences, coding standards, provider permissions, and standing constraints.
- Episodic memory: time-stamped events such as meetings, debugging sessions, handoffs, failed attempts, release decisions, and conflict resolutions.
- Procedural memory: repeatable workflows such as how to run tests, how to cut a release, how to review a PR, or how to migrate architecture patterns.
- Reflection: post-meeting synthesis that extracts lessons, unresolved risks, and behavior changes for each agent.
- Handoff memory: compact successor packet generated when a session is saturated or an agent is replaced.
- Memory gate: future rule that decides what is allowed to enter long-term memory, rejects unsupported claims, and flags contradictions with existing memory.
