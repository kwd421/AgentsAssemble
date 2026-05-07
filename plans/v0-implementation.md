# AgentCouncil v0 Implementation Plan

## Objective

Build the first terminal-first demo for a local-first multi-agent council orchestrator. The demo should run one canned council meeting where three isolated roles independently research "Who is the strongest One Piece admiral?", debate evidence and reasoning, produce a moderated synthesis and task-style next steps, and persist both human-readable and machine-readable artifacts.

Seed source: `seeds/seed_b062b3d88b5d.yaml`

## V0 Product Boundary

In scope:

- One terminal command for the canned demo.
- File-based local persistence.
- Human-readable Markdown artifacts.
- Machine-readable YAML or JSON meeting record.
- Three role definitions with English ids and Korean display names.
- Independent per-role research artifacts.
- Provider adapter interface.
- Mock adapter for deterministic development and testing.
- Codex adapter path as the first real-provider integration.
- Structured meeting rounds: agenda, independent research, Round 1, Round 2, moderator synthesis, decision, task assignment.

Out of scope:

- Implementation agents editing code after the meeting.
- Git worktree creation.
- Commits, pushes, PRs, release gates.
- Blocker-triggered follow-up meetings.
- Full handoff/resume validation.
- Rich TUI or web UI.
- Cost dashboard and provider capability dashboard.
- Remote/federated councils where other users' AI agents join over the network.

## Proposed Stack

Use a small Python CLI for the first version.

Rationale:

- Easy file and process orchestration.
- Good fit for YAML/JSON/Markdown artifact generation.
- Simple to test with mock adapters.
- Can wrap shell-based provider adapters such as Codex CLI.

Suggested dependencies:

- Standard library first.
- Optional `pyyaml` or TOML/JSON-only config if avoiding dependencies is preferred.
- `pytest` for tests once the package skeleton exists.

## Repository Shape

```text
agentcouncil/
  __init__.py
  cli.py
  config.py
  models.py
  meeting.py
  artifacts.py
  research.py
  adapters/
    __init__.py
    base.py
    mock.py
    codex.py
configs/
  demo-council.yaml
seeds/
  seed_b062b3d88b5d.yaml
plans/
  v0-implementation.md
tests/
  test_demo_meeting.py
```

Generated runtime artifacts should live under:

```text
.agentsassemble/
  meetings/
    <meeting_id>/
      agenda.md
      transcript.md
      decision.md
      meeting.json
      tasks/
        lore_lawyer.md
        show_me_the_feats.md
        fanboard_skeptic.md
      private_research/
        lore_lawyer/
          research.md
          research.json
        show_me_the_feats/
          research.md
          research.json
        fanboard_skeptic/
          research.md
          research.json
      roles/
        lore_lawyer/
          role.md
          persona.md
          memory.md
          history.jsonl
        show_me_the_feats/
          role.md
          persona.md
          memory.md
          history.jsonl
        fanboard_skeptic/
          role.md
          persona.md
          memory.md
          history.jsonl
```

## Role Defaults

### `lore_lawyer` / 설정충

Focus: official/canonical statements, source hierarchy, internal consistency.

Default personality: precise, lore-obsessed, stubborn about source hierarchy, with restrained humor.

### `show_me_the_feats` / 공식이뭘알아

Focus: demonstrated combat performance, fight scenes, matchups, observed ability use.

Default personality: direct, practical, feat-first, impatient with unsupported statements.

### `fanboard_skeptic` / 만갤러

Focus: fandom claims, weak evidence, overinterpretation, counterexamples, uncertainty.

Default personality: skeptical, playful, community-aware, good at spotting overclaims.

All roles may choose any final conclusion after research. They are not assigned advocacy positions.

Role/lens and personality/style must be modeled separately so users can keep the same analytical role while changing tone, seniority, humor, catchphrases, or directness.

## Adapter Contract

Define a common provider adapter interface with methods similar to:

```python
class ProviderAdapter:
    def start_session(self, role, meeting_context): ...
    def run_research(self, session, prompt): ...
    def run_round(self, session, prompt): ...
    def synthesize(self, session, prompt): ...
```

Adapter outputs should include:

- text
- adapter name
- model/provider metadata if available
- session id if available
- command metadata if shell-backed
- error/timeout state

## Mock Adapter

Purpose:

- Prove orchestration and artifact flow.
- Support tests.
- Allow demo without external provider availability.

Constraint:

- Mock outputs must be generated from role/question inputs, not static pasted transcript blocks.
- The demo must clearly indicate when mock mode is being used.

## Codex Adapter

Purpose:

- Provide the first real-provider path.
- Keep the same adapter contract as mock.

V0 minimum:

- Shell-backed Codex invocation path or documented smoke path.
- Separate role session metadata where Codex supports it.
- Separate history/context files regardless of Codex session support.
- Errors/timeouts recorded in `meeting.json` and transcript.

Open implementation question:

- Confirm the local Codex CLI invocation pattern and whether it exposes stable session ids.

## Meeting Flow

1. Load demo council config.
2. Create meeting directory and machine-readable initial record.
3. Write `agenda.md`.
4. Initialize three isolated role sessions.
5. Run independent research for each role without exposing other roles' sources, notes, or conclusions.
6. Persist each role's private `research.md` and `research.json`.
7. Run Round 1 opening arguments using each role's own research only.
8. Run Round 2 rebuttal/evidence comparison with access to public Round 1 statements.
9. Run moderator synthesis.
10. Write `transcript.md`, `decision.md`, `meeting.json`, and `tasks/*.md`.
11. Stream concise role-by-role progress and final artifact paths to the terminal.

## Artifact Requirements

Research artifacts per role:

- search queries
- source URLs
- notes per source
- useful snippets or paraphrased excerpts
- research summary
- confidence and uncertainty
- claim-to-evidence mapping

Public artifacts:

- `agenda.md`
- `transcript.md`
- `decision.md`
- `tasks/<role_id>.md`

Machine-readable artifact:

- `meeting.json`
- should include meeting id, command, question, roles, adapter config, isolation paths, artifact paths, round outputs, audit metadata, and failure state

## Failure Policy For V0

Default behavior:

- If one role fails or times out, record the failure and continue only if at least two roles produced usable research and debate outputs.
- Mark incomplete outputs as low-confidence.
- The final synthesis must mention missing or failed roles.

Demo failure:

- No distinct roles.
- Hardcoded transcript.
- Missing adapter interface.
- Missing decision or task assignment.
- Private research leaks before Round 1.
- Persistent artifacts are missing.
- Machine-readable record cannot explain what happened.

## Implementation Phases

### Phase 1: Skeleton

- Add package structure and CLI entrypoint.
- Add demo config.
- Add dataclasses or typed models for roles, sessions, research, rounds, and meeting record.
- Add artifact writer.

Validation:

- `python -m agentcouncil.cli --help`
- Unit test for config loading and meeting directory creation.

### Phase 2: Mock Council

- Implement mock adapter.
- Run full meeting flow with isolated role directories.
- Write all required Markdown and JSON artifacts.
- Stop first implementation milestone here before attempting real Codex execution.

Validation:

- One command runs the demo in mock mode.
- Tests assert required files exist and include all three role ids/display names.

### Phase 3: Research Interface

- Add research abstraction.
- For mock mode, produce structured pseudo-research from role prompts.
- Prepare real web research adapter boundary for later provider/tool integration.

Validation:

- Each role gets separate private research files.
- Round 1 cannot read other roles' private research.

### Phase 4: Codex Adapter Smoke Path

- Inspect local Codex CLI capabilities. Completed locally with `codex --help` and `codex exec --help`.
- Implement the smallest shell-backed adapter path that can call Codex per role. Completed with `codex exec --skip-git-repo-check --output-last-message`.
- Preserve separate role histories/context files. Implemented through per-role artifact directories.
- Record command/session metadata. Implemented in adapter output metadata.

Validation:

- A real Codex-backed single-role smoke run completed and returned a Codex session id.

### Phase 5: Polish The Demo

- Improve terminal streaming.
- Tighten `decision.md` format.
- Add `tasks/*.md` output.
- Add basic README instructions.

Validation:

- Run the canned demo from a clean checkout.
- Inspect generated artifacts manually.

## Acceptance Test

Command shape is:

```bash
assemble demo --adapter mock
```

Optional real-provider smoke:

```bash
assemble demo --adapter codex
```

Pass condition:

- The command streams an agenda, independent role research progress, Round 1, Round 2, moderator synthesis, decision, and task assignment.
- The meeting directory contains all required public, private, and machine-readable artifacts.
- The transcript visibly shows 설정충, 공식이뭘알아, and 만갤러 contributing distinct evidence or reasoning.
- The decision cites evidence, explains caveats, and states confidence.

## Open Questions Before Coding

- Should machine-readable records use JSON only for dependency-free parsing, or YAML for readability?
- What is the exact local Codex CLI command and session behavior available on this machine?
- Should first real web search be implemented through a provider prompt, a web API, or left behind the adapter boundary for v0 mock mode?

## GUI Direction

Future local GUI should use stable tab ids with Korean display labels. Detailed spec: `docs/gui-v0-spec.md`.

- `live` / 실황: chat-like live council room where agent messages appear in time order.
- `board` / 작전판: structured debate board showing research, claims, rebuttals, conflicts, confidence, and synthesis flow.
- `archive` / 아카이브: meeting artifacts, including agenda, transcript, decision, task files, and per-agent research records.

The tab ids should stay stable while labels remain easy to rename later.

## Future Remote Council Direction

AgentsAssemble should eventually support remote or federated council meetings:

- A local user can invite another person's AI agent into a meeting.
- Remote agents join through an explicit gateway/protocol rather than direct local filesystem access.
- Remote participants have clear identity, provider metadata, and permission boundaries.
- Remote agents are read-only by default and cannot mutate local files, commits, pushes, PRs, credentials, or private project data without explicit approval.
- Shared council events should be represented as structured messages so local, remote, mock, and provider-backed agents can use the same meeting protocol.
- Private research boundaries must remain explicit: each remote agent controls what it discloses to the public transcript.
- A future networked council room can stream agent events through WebSocket or SSE.

This is deliberately out of v0 implementation scope, but v0 models should avoid coupling meeting logic directly to local-only assumptions where a clean event/message boundary is easy to preserve.

## Naming Note

Working product name: `AgentsAssemble`.

Working CLI command: `assemble`.

Working Python package name: `agentsassemble`.

Locked v0 implementation decisions:

- Use `agentsassemble` as the Python package name.
- Use `meeting.json` as the v0 machine-readable meeting record.
- First implementation milestone is the mock adapter demo; Codex adapter should exist as an interface/stub, then become the next smoke-test milestone.

This can still be renamed later, but rename cost increases after package publishing, documentation, generated artifact paths, config names, and user installs begin to depend on it.
