# AgentsAssemble Roadmap

This roadmap freezes the current product direction so new features do not sprawl faster than the council engine can support them.

The current v0.1 release-hardening bar lives in
`docs/product/V0_1_RELEASE_CHECKLIST.md`. Use that checklist when deciding
whether the core GUI/live-agent/archive flow is ready enough to call usable.
Local stdio MCP now has a checked-in participant/archive adapter boundary; host
control MCP remains a later auth/admission design.

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
- Product operating memory is recorded in `docs/product/OPERATING_MODEL.md`,
  including discovery-versus-execution, host-approved sessions, provider-owned
  agent context, shared meeting memory, Work Mode / Play Mode separation, and
  the decision to defer frontend polish behind stable backend contracts.
- Long-term memory/context engineering research notes now compare MemGPT-style memory tiers, Reflexion-style verbal reflection, LongMemEval-style evaluation questions, and LangGraph Store-style namespaced memory.
- Provider binding groundwork records provider configs, agent bindings, capabilities, and meeting-only permissions in `meeting.json`.
- Room Event Log direction is documented: agents should join one shared room event stream rather than receive isolated interview prompts.
- Live room infrastructure and council workflow are now explicitly separated: free chat and live presence are supported, but official turns, evidence, decisions, tasks, return packets, and memory remain the product core.
- The provider registry now includes live HTTP meeting adapters for Claude/Anthropic, Gemini, Grok, and local OpenAI-compatible providers such as LM Studio. Cursor, Claude Code, and Hermes/OpenClaw-style memory packs remain planned or memory-pack providers with explicit capability records.
- Provider health now has explicit opt-in probe modes: static config-only (`none`), loopback local `/models` (`local`), remote bridge health (`bridge`), and real Anthropic/Gemini/Grok model-list credential checks (`api`). The API probe reads credentials only in that mode, calls no prompt-bearing generation endpoint, and reports only safe model-list reachability evidence.
- Importable memory/profile capsules now have a first safe gate: `memory-capsule gate` validates required capsule files, JSON metadata shape, meeting-safe permissions, raw session dump absence, and compact evidence-index counts without executing providers, starting sessions, importing the pack, or exposing local paths or capsule body text.
- Provider catalog data is available from the registry and GUI API so future UI surfaces can show which providers are available, planned, searchable, filesystem-capable, or memory-pack-only.
- Runtime agent config can be loaded from JSON with host-approved providers, permission profiles, agent bindings, and incoming external agent requests. Incoming agents are recorded for audit but only approved bindings execute.
- Incoming external agents now produce explicit admission decisions in `meeting.json`, separating requested role/provider/permissions from the host-approved binding that actually executes.
- Remote HTTP bridge providers allow a friend-owned Claude Code session to join a meeting as a read-only participant through an audited `/agentsassemble/run` bridge.
- The GUI Lobby can start, resume, restart, recover, check, or stop a resident live-agent session, optionally run bounded remaining template rounds after the session is ready, recover historical resident process groups, call one moderator-controlled official round, run bounded remaining template rounds, run a fresh credential-free session smoke, configure bounded session-smoke soak cycles from the `상주 실행` panel, include full session smoke inside readiness with those same soak controls, and display the backend `/api/live-agent-health` snapshot from the same panel, using the real live-agent official-turn paths and reporting sanitized reply counts plus sanitized readiness, resume, restart, recover, stop/offline, diagnostic-isolated health, manifest connection evidence, and enforced meeting-owned process identity. Discovery can now optionally write a safe session-start bundle beside the discovered resident config, including generated council roles, agent bindings, and an `ensure-session` next command, so detected local CLIs can move from discovery to official resident entry without hand-authoring bindings. CLI `auto-join` can also narrow one-shot real-provider approval to explicit `--approve-agent` or `--approve-command` allowlists, excluding unapproved discovered CLIs from the generated resident config and session bundle before anything can start. A credential-free `live-agent session-smoke` command now verifies the full public resident session flow across `local_cli`, `live_session`, loopback `remote_bridge`, and supervised `self_service`: start-session, one official round, bounded repeated auto lobby replies before restart, after restart, and post-recover, check-session, resume-session, restart-session, recover-session, optional same-session post-recover soak cycles, and stop-session. `live-agent doctor --session-smoke` folds that strongest credential-free session proof into the combined operator readiness answer, with optional namespaced session-smoke soak cycles for slower liveness checks.
- Real resident configs now have an explicit approval smoke surface: `live-agent real-session-smoke` and `POST /api/live-agent-real-session-smoke` require current `approve_real_providers` plus explicit live-agent, council, and agent config paths before any room request, start a diagnostic session from host-approved matching configs, run bounded bound-agent reply probes, redact diagnostic probe/reply text from durable lobby history, stop the exact meeting/group, and report only sanitized status/count evidence. Optional `--official-round-smoke` and `--restart-smoke` extend the same approved diagnostic with one bounded official round and one restart/probe check, still exposing only safe counts/statuses. It does not finalize, recover, create durable session-run retry intent, or persist provider approval.
- Resume-capable real providers now have a narrower direct continuity proof: `live-agent continuity-proof` requires current `--approve-real-providers`, calls the Codex, Kiro, or Grok resident runner for two turns, verifies provider-owned suffix recall without replaying the private continuity code, and reports only safe booleans, lengths, provider kind, and a short session-id suffix. `continuity-proof-group` applies the same diagnostic to resident configs while reporting unsupported local CLI candidates without execution. This complements `real-session-smoke`; it does not prove room admission, cleanup, official-turn quality, or restart behavior.
- Exact auto-join approval decisions are preserved as safe `discovery.run` operation evidence, including approved agent ids plus approved/excluded/unmatched counts, without storing CLI paths, command-name lists, or durable provider approval.
- Ready resident sessions that `ensure-session` upgrades to `restart` now preserve an allowlisted `ensure_reason` for operator evidence: resident session-id drift, stale lobby observation, or stale official/live observation. Durable session-runs store it under `result.ensure_reason`, and session operation details expose the same safe enum without old/new session ids, lobby text, official content, provider output, config paths, endpoints, auth refs, or command arguments.
- Paused durable session-runs now clear any saved retry delay when resumed, while preserving retry failure evidence, so an explicit operator resume is eligible for the next session-run monitor reconcile instead of silently waiting behind stale backoff.
- Health now compares active stored-ready durable session-runs against the current read-only session readiness overlay, so a historical `ready` durable run with no current process/readiness evidence degrades `/api/live-agent-health` instead of masquerading as live resident evidence.
- `readiness.check` operation records now preserve safe long-session health causes from degraded readiness checks, including observation cursor lag, shared-memory attention, durable session-run retry/drift attention, and session-run monitor attention/count evidence, without storing raw health JSON, event text, prompts, replies, config paths, endpoints, auth refs, or provider output.
- GUI durable session-run row retries now honor the current `실사용 CLI 승인` checkbox by sending one-shot `approve_real_providers: true` only for that retry-now request, matching the CLI approval gate without persisting provider approval into durable run state.
- Health and public durable session-run reads now omit or placeholder unsafe roster, process/session owner, reason-label, and nested result-key values across agent, process, session, and durable session-run surfaces, so legacy `env:`, `literal:`, token-like, absolute/relative slash/backslash path-like, URL-like, or JSON-file-shaped values cannot leak through `/api/live-agent-health` or `include_readiness=1` overlays.
- Live-agent admission, safe roster reads, and CLI roster output now expose derived `join_semantics` and `context_durability` labels for each provider/connection pair. These labels distinguish stateless prompt calls, process-lifetime terminal or JSONL sessions, Codex exec resume, self-service room loops, external/manual ownership, and remote-owner-managed bridges without trusting caller-supplied labels or exposing session ids, endpoints, auth refs, config paths, command arguments, prompts, provider output, or log tails. Safe roster reads and CLI roster output also re-derive current host-admission evidence from the meeting record, including `admission_status`, `host_approved_binding`, `admission_evidence_source`, and safe binding/conflict labels, so a spoofed presence row cannot make an unbound participant appear host-approved. `live-agent list --require-host-approved` gives scripts a separate admission gate without changing `--fail-on-attention` liveness semantics.
- `/api/live-agent-health`, `live-agent health`, and the GUI runtime health row now include host-admission evidence in `admission`, reusing the same safe meeting-record-derived roster projection. It reports host-approved/unapproved counts, status counts, and compact admission attention labels without exposing session ids, endpoints, auth refs, config paths, command arguments, prompts, provider output, raw presence errors, or spoofed stored admission fields. Admission evidence remains operator visibility rather than part of overall health degradation; the explicit hard gate is still `live-agent list --require-host-approved`.
- The React/Vite frontend can be built separately and served by the Python GUI
  under `/app/` as an opt-in preview, while `/` remains the dependency-light
  vanilla console and `/legacy/` remains the tested fallback. `frontend-info`
  reports the preview URL and whether the ignored `frontend/dist` build output
  is present.
- Resident official replies now refresh deterministic shared meeting memory artifacts under `shared_memory/`: `rolling-summary.md`, `open-questions.md`, `action-items.md`, and `index.json`. These artifacts are derived only from official live transcript events, use explicit markers for action items and open questions, stay projected in the Archive before finalization, are rewritten during finalization, and are surfaced as compact shared memory in live-agent room payloads, resident prompts, and terminal/self-service wait payloads. Room reads treat the current official log as authoritative when official events exist, use a stat-cached compact projection for hot polling, and fall back to durable index or embedded memory only when the current official log has no usable official events.
- Explicit lobby promotion is now a narrow CLI/API path: `assemble lobby promote`
  and `POST /api/lobby/promote` append `promoted_context` official live events
  from selected lobby event ids and record `lobby.promote_to_official` operation
  evidence. Play Mode chatter is not official until explicitly promoted; side
  chat cannot be promoted; attachments are not promoted; promoted context appears
  in transcript/shared memory without creating debate-round messages.
- Resident finalization now announces per-agent return packets as targeted non-official room events. Terminal, self-service, and external/manual agents can receive `action: "return_packet"` from `wait-next`, read only their own packet through the returned agent-scoped `read_command`, and acknowledge the live cursor without posting a lobby reply. If the original event falls out of the bounded room tail during a long session, `/room` reprojects a path-only return-packet event from the public artifacts and agent binding only while that packet remains pending for that agent. The checked-in self-service wrapper and credential-free smoke child now run that read step before acknowledging return packets, and report `error` without acknowledging if the read or ack command is missing, fails, times out, or cannot be launched.
- Terminal and self-service wait commands now return cursor-only observation evidence on JSON timeouts, advancing lobby/live cursors over non-actionable visible events such as self messages, over-depth lobby chains, empty lobby messages, and non-turn live updates. `wait-next` also distinguishes replyable `lobby` actions from non-reply `observe_lobby` actions under the agent's engagement mode and chain guard. The checked-in self-service wrapper and credential-free smoke child heartbeat those cursors or ack commands without replying, report `error` if a lobby/official reply command or observe-lobby ack command cannot run, and reduce false stale-observation restarts during long resident sessions. Parent-managed resident runners also keep polling if a cursor-only observation heartbeat fails transiently while local lobby/live cursor state has advanced.
- The GUI live-agent form can generate the same safe external `join-brief` packet as the CLI/API, so an operator can hand another AI a register/wait-next/leave loop without registering it, starting a provider, writing files, or granting broad automatic entry. The packet carries an `execution_contract` that declares join semantics, context durability, evidence basis, and that the provider was not started by the packet. External/manual agents now also have safe `live_agent.register` and `live_agent.leave` operation records around explicit entry and exit; register records include admission evidence such as `admission_status`, `host_approved_binding`, and safe binding-conflict labels without blocking manual lobby presence, and `live-agent leave` clears stale error text without deleting the roster row or stopping supervised groups.
- Supervised `self_service` children now receive `AGENTSASSEMBLE_LEAVE_COMMAND`, a direct `live-agent leave --json` argv template for intentionally marking their own roster row `offline` before exit without stopping the process group, starting a provider, or relying on prompt injection.
- `/api/live-agent-health` and the GUI runtime health row now surface safe shared meeting memory evidence for ready resident sessions, including official-event, open-question, and action-item counts plus the last official event id, without exposing official reply text or raw memory artifact bodies.
- Resident health and session readiness now treat connection evidence as binding-compatible rather than id-only: a same-id roster row with the wrong provider or connection kind, or a meeting binding whose provider config is missing, degrades readiness with safe mismatch or `binding_provider_missing` attention instead of being counted as a connected resident.
- Resident review checkpoints now write non-official Archive artifacts under `review_checkpoints/`, preserving the review prompt and resident replies for operator inspection while keeping operation history, official transcript, shared memory, decisions, and return packets content-free.
- No-Tailscale multi-host now has a design document and a small LAN invite token PoC. `live-agent lan-invite create/verify` signs and verifies an HMAC-SHA256 admission packet for a future `native_remote_room_client`, scoped to one room URL, meeting id, and agent id, while keeping `remote_http_bridge` separate from bridge-free room participation. This is admission proof only; it does not start provider CLIs, authenticate room endpoints, revoke invites, solve NAT traversal, or make relay/WebRTC ready.
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
- A dedicated roadmap page for the later React/Vite responsive frontend: a
  Trello/Jira-like board that separates long-term epics from version/milestone
  cards, clearly marks planned, in-progress, review, and completed work, and
  links cards back to docs, commits, or meeting records. This is separate from
  the live meeting progress/round view and should not be bolted onto the current
  vanilla operator console.
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
