# Council Reliability Gates Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Harden meeting reliability so provider failures, fake consensus, unsafe public artifacts, and future live-room architecture are explicit and testable.

**Architecture:** Keep the current file-based meeting runner. Add small policy/data modules rather than folding more logic into `meeting.py`. Treat provider output as untrusted, derive a separate Decision Gate report after synthesis, and document the room-event/live-session direction without implementing a heavy live runtime yet.

**Tech Stack:** Python standard library, `unittest`, existing JSON/Markdown artifact pipeline.

---

### Task 1: Stability And Artifact Safety

**Files:**
- Modify: `agentsassemble/meeting_phases.py`
- Modify: `agentsassemble/adapters/local_cli.py`
- Modify: `agentsassemble/models.py`
- Modify: `agentsassemble/meeting.py`
- Modify: `agentsassemble/meeting_record.py`
- Test: `tests/test_local_cli_adapter.py`
- Test: `tests/test_demo_meeting.py`
- Test: `tests/test_public_provider_artifacts.py`

- [ ] Write failing tests for reserved retry metadata, local CLI non-zero failure, provider redaction, and follow-up missing refs.
- [ ] Run targeted tests and confirm they fail for the expected reason.
- [ ] Implement minimal fixes: overwrite orchestrator-owned retry metadata, raise `LocalCliError` on failed local commands, redact public provider config fields, and report missing follow-up refs.
- [ ] Run targeted tests and full `python3 -m unittest discover -s tests`.
- [ ] Commit as a coherent reliability fix.

### Task 2: Decision Gate v0

**Files:**
- Create: `agentsassemble/decision_gate.py`
- Modify: `agentsassemble/meeting.py`
- Modify: `agentsassemble/artifact_public.py`
- Modify: `agentsassemble/static/meeting-views.js`
- Test: `tests/test_decision_gate.py`
- Test: `tests/test_demo_meeting.py`
- Test: `tests/test_static_ui_assets.py`

- [ ] Write failing tests for `decided`, `needs_more_research`, `no_consensus`, `blocked`, and `invalid` gate states.
- [ ] Run targeted tests and confirm they fail.
- [ ] Implement the gate from synthesis, evidence gate, debate stance, and research retry/failure state.
- [ ] Surface the gate in `meeting.json`, `decision.md`, and live-state/UI data.
- [ ] Run targeted tests and full suite.
- [ ] Commit as the Decision Gate v0 slice.

### Task 3: Room Event Log And Live Session Direction

**Files:**
- Create: `docs/live-session-room-model.md`
- Modify: `docs/provider-architecture.md`
- Modify: `docs/roadmap.md`
- Test: docs reviewed by grep checks.

- [ ] Document shared-room semantics: agents join one room event stream, not isolated interview prompts.
- [ ] Document participant classes: human, one-shot adapter, remote bridge, local CLI delegate, future live session.
- [ ] Document live-session limits: app sessions are not directly controllable; CLI/SDK/PTYS are the viable path.
- [ ] Document safety, memory capsule, and remote/multiplayer implications.
- [ ] Run docs grep checks and full tests.
- [ ] Commit as architecture documentation.

### Task 4: Whole Project Review And Verification

**Files:**
- Read/review all project files using `rg --files`.
- No production edits unless review finds a must-fix issue.

- [ ] Inspect changed diff and high-risk existing files.
- [ ] Run full `python3 -m unittest discover -s tests`.
- [ ] Run at least one mock smoke meeting if full tests do not already cover artifact generation sufficiently.
- [ ] Request code review with Superpowers and address Critical/Important findings.
- [ ] Report verified commands and any unverified real-world paths, especially real friend Claude Code connectivity.
