# AgentsAssemble GUI v0 Spec

## Purpose

The GUI should make an AgentsAssemble council feel like an actual meeting room, not just a folder of generated Markdown files.

The first GUI is local-first and browser-based. It reads meeting artifacts produced by the existing Python meeting runner and presents them through three stable tabs:

- `live` / 실황
- `board` / 작전판
- `archive` / 아카이브

## Scope

In scope for GUI v0:

- Run a local browser UI from `assemble gui`.
- Show the latest or selected local meeting.
- Let the user start the mock demo from the UI.
- Display council messages in a chat-like live view.
- Display structured debate flow in a board view.
- Display generated meeting artifacts in an archive view.
- Use the existing file-based artifact model.
- Keep tab ids stable while allowing labels to change later.

Out of scope for GUI v0:

- Multi-user remote councils.
- Real-time multi-process streaming from remote agents.
- Editing generated artifacts in the browser.
- Running implementation agents after the meeting.
- Commits, pushes, PRs, or Git worktree orchestration.
- Authentication, accounts, cloud sync, or hosted deployment.

## Navigation

### `live` / 실황

Primary purpose: show the meeting as a live-feeling conversation.

Content:

- System events such as meeting start, research start, round transitions, synthesis, and artifact creation.
- Agent messages in chronological order.
- User-facing messages should be Korean by default for Korean users.
- Role display names and internal ids.
- Personality/tone labels when configured.
- Distinct visual treatment for:
  - 설정충 / `lore_lawyer`
  - 공식이뭘알아 / `show_me_the_feats`
  - 만갤러 / `fanboard_skeptic`
  - Moderator
- Confidence badges when available.
- Source/citation hints where available.

Initial GUI v0 may render completed meeting data after the run finishes. True token-by-token or event-by-event streaming can be added later.

### `board` / 작전판

Primary purpose: show the debate structure rather than raw chronology.

Content:

- Research summaries by role.
- Round 1 opening claims.
- Round 2 rebuttal/evidence comparison.
- User-facing summaries and debate text should be Korean by default for Korean users.
- Agreement and disagreement markers.
- Claim/evidence/confidence summaries.
- Final synthesis path:
  - winner or ranking
  - caveats
  - confidence
  - why the conclusion followed from the debate

The board should avoid inventing conflict. If all agents reach the same conclusion, it should compare evidence quality, confidence, and uncertainty.

### `archive` / 아카이브

Primary purpose: show the persistent artifacts that make the meeting auditable and handoff-friendly.

Content:

- `agenda.md`
- `transcript.md`
- `decision.md`
- `meeting.json`
- `tasks/*.md`
- `private_research/<role_id>/research.md`

The archive is read-only in GUI v0.

Archive content may preserve original artifact language. It is acceptable for machine-readable files, provider output, source titles, URLs, and handoff artifacts to remain partly or fully English.

## Data Source

GUI v0 reads local files under:

```text
.agentsassemble/meetings/<meeting_id>/
```

Required files:

- `meeting.json`
- `agenda.md`
- `transcript.md`
- `decision.md`

Optional but expected files:

- `tasks/*.md`
- `private_research/*/research.md`
- `private_research/*/research.json`
- `roles/*/memory.md`
- `roles/*/history.jsonl`

## Persona And Style Configuration

AgentsAssemble should separate what a role evaluates from how that role speaks.

- `role` / `lens`: the agent's job, evidence priority, and analysis responsibility.
- `personality` / `style`: the agent's tone, temperament, catchphrases, directness, humor, and verbosity.

GUI v0 should support default personality presets in the meeting config/model even if the first screen only displays them. Later UI can expose preset selection and direct editing.

Example role shape:

```json
{
  "id": "lore_lawyer",
  "display_name": "설정충",
  "lens": "Canon Analyst",
  "research_focus": "official statements, canon hierarchy, and internal consistency",
  "personality": {
    "preset": "pedantic_lore_nerd",
    "tone": "precise, stubborn, slightly smug",
    "directness": "medium",
    "humor": "low",
    "verbosity": "medium",
    "catchphrases": ["공식 설정상", "근거 등급부터 따져야 함"]
  }
}
```

Default demo presets:

- 설정충: precise, lore-obsessed, stubborn about source hierarchy.
- 공식이뭘알아: direct, practical, feat-first, impatient with unsupported statements.
- 만갤러: skeptical, playful, community-aware, good at spotting overclaims.

Future controls:

- Choose from default presets.
- Duplicate and edit a preset.
- Write a custom persona/style block.
- Keep role memory stable while changing style overlays.
- Prevent persona convergence by preserving distinct role and style boundaries.

## Local Server

The GUI should run as a local web server:

```bash
assemble gui
```

Default URL:

```text
http://localhost:8765
```

Implementation preference:

- Keep the first version dependency-light.
- A Python standard-library HTTP server is acceptable for GUI v0 if it can serve static assets and simple JSON endpoints cleanly.
- A heavier frontend framework is not required for the first GUI.

## API Shape

Minimal local endpoints:

- `GET /`
  - Serves the GUI shell.
- `GET /api/meetings`
  - Lists local meetings.
- `GET /api/meetings/latest`
  - Returns latest meeting metadata and parsed artifact content.
- `GET /api/meetings/<meeting_id>`
  - Returns selected meeting metadata and parsed artifact content.
- `POST /api/demo`
  - Runs the mock demo and returns the generated meeting id.

GUI v0 can block until the mock demo finishes. Live event streaming can be added later through SSE or WebSocket.

## Visual Direction

The UI should feel like a compact local operations room for AI agents:

- Dense enough for repeated developer use.
- Clear role colors and labels.
- No marketing hero page.
- No nested card clutter.
- No decorative gradient/orb background.
- Tabs should be prominent and easy to rename.
- The first viewport should immediately show the council UI, not an explanation page.
- The visual system should feel like one continuous council facility across lobby, live, board, and archive, not four unrelated admin screens.
- High-impact bitmap assets are allowed when they materially improve the product feel, but they must be optimized, packaged, and covered by an asset budget test.
- Reuse existing visual assets before adding new ones. Avoid large uncompressed PNGs in the shipped app.
- Prefer responsive surfaces, scroll containment, and compact controls over oversized static panels.

Suggested layout:

```text
AgentsAssemble
[실황] [작전판] [아카이브]        [Run Mock Demo]

<selected tab content>
```

Current screen purposes:

- `로비`: informal staging area for owners and agents before formal deployment. It may show social presence and readiness, but should stay visually distinct from the official meeting record.
- `실황`: cinematic live council room and chronological debate feed. It carries the strongest visual atmosphere.
- `작전판`: dense evidence and stance review surface. It should scale to many roles without hardcoded three-person assumptions.
- `아카이브`: document reader for agenda, transcript, decision, return packets, and research artifacts. It should favor readable documents and independent scrolling over decorative layout.

Current performance guardrails:

- The main council hero image should stay under the tested asset budget.
- Static package data must include any shipped bitmap assets.
- Long repeated lists should use scroll containment or rendering containment where practical.
- Any new visual asset should be justified by visible product value, not decoration alone.

## Acceptance Criteria

- `assemble gui` starts a local server.
- The browser UI opens or prints the local URL.
- The UI can start a mock demo.
- After demo completion, all three tabs show useful content from the generated meeting artifacts.
- `실황` shows chronological council messages/events.
- `작전판` shows structured research, claims, rebuttals, and synthesis.
- `아카이브` shows readable artifact previews.
- Role/personality metadata is available in the meeting model and visible enough that agents do not all feel like the same generic AI voice.
- The GUI uses stable internal tab ids: `lobby`, `live`, `board`, `archive`.
- The GUI keeps shipped bitmap assets packaged and within the tested size budget.
- The GUI includes responsive breakpoints for narrow screens.
- Existing CLI mock demo behavior remains working.

## Future Extensions

- True live event streaming while providers are running.
- Full Codex-backed council runs from the GUI.
- Meeting selector/history sidebar.
- Agent join/leave state.
- Handoff/resume UI.
- Remote/federated councils where another user's AI agent can join through a permissioned gateway.
