# Local-Verifiable Council Workflow Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Advance AgentsAssemble with locally verifiable council workflow pieces while excluding real friend network and paid provider account validation.

**Architecture:** Add generic local CLI participant support first, then delegate/return packet foundations, then meeting recovery and UI/message model refinements. Keep meeting-time adapters read-only and make every behavior testable with fake command runners or local HTTP smoke checks.

**Tech Stack:** Python stdlib, unittest, subprocess runners, existing AgentsAssemble provider registry, static GUI assets.

---

### Task 1: Generic Local CLI Meeting Adapter

**Files:**
- Create: `agentsassemble/adapters/local_cli.py`
- Modify: `agentsassemble/models.py`
- Modify: `agentsassemble/adapters/registry.py`
- Test: `tests/test_local_cli_adapter.py`
- Test: `tests/test_provider_registry.py`

- [x] Write failing tests for a `local_cli` provider that invokes a configured command with a prompt on stdin, parses JSON research/round/synthesis responses, records read-only metadata, and rejects missing commands.
- [x] Verify the new tests fail before implementation with `python3 -m unittest tests.test_local_cli_adapter`.
- [x] Implement `LocalCliAdapter` with minimal prompt construction and shared JSON fallback behavior.
- [x] Register `local_cli` as an available read-only meeting provider.
- [x] Verify with targeted tests and `python3 -m unittest discover -s tests`.
- [x] Commit as `Add local CLI meeting adapter`.

### Task 2: Delegate Session Packet v0

**Files:**
- Create: `agentsassemble/delegate_packets.py`
- Modify: `agentsassemble/meeting.py`
- Modify: `agentsassemble/artifact_packets.py`
- Test: `tests/test_delegate_packets.py`

- [x] Write failing tests proving each meeting writes delegate input packets and return packets with persona, memory summary, stance fields, permissions, and provenance.
- [x] Verify the tests fail.
- [x] Implement packet generation using existing role, binding, memory, and decision artifacts.
- [x] Verify targeted and full tests.
- [x] Commit as `Add delegate session packets`.

### Task 3: Research Recovery Round Metadata

**Files:**
- Modify: `agentsassemble/meeting_phases.py`
- Modify: `agentsassemble/meeting_record.py`
- Modify: static live rendering only if data already exists.
- Test: `tests/test_partial_failure.py`

- [x] Write failing tests for recovered retry metadata surfacing in meeting records and live events.
- [x] Implement minimal recovery metadata without changing debate semantics.
- [x] Verify targeted and full tests.
- [x] Commit as `Surface research retry recovery`.

### Task 4: Follow-up Meeting Generator v0

**Files:**
- Modify: `agentsassemble/cli.py`
- Modify: `agentsassemble/meeting.py`
- Test: `tests/test_demo_meeting.py`

- [x] Write failing tests for creating a follow-up meeting from an existing meeting directory, carrying parent id, reason, and artifact references.
- [x] Implement CLI option and meeting metadata only; do not auto-run external providers.
- [x] Verify targeted and full tests.
- [x] Commit as `Add follow-up meeting generator`.

### Task 5: Message Model and Speech Quality Tightening

**Files:**
- Modify: `agentsassemble/speech_policy.py`
- Modify: `agentsassemble/static/live.js` or `agentsassemble/static/meeting-views.js` if rendering needs data hooks.
- Test: existing adapter tests plus static UI tests.

- [x] Write failing tests that prompts require speaking from research rather than dumping research, reacting to named prior speakers, and keeping system status separate from visible speech.
- [x] Implement prompt and renderer refinements.
- [x] Verify targeted and full tests.
- [x] Commit as `Tighten council speech quality`.

### Explicitly Excluded From This Goal

- Real friend Tailscale/LAN/port-forward validation.
- Actual Claude Code, Gemini CLI, Grok, Cursor, or paid API account login validation.
- Production relay server deployment.
- Real multi-user internet hosting.
