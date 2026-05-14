from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

from agentsassemble.artifacts import write_agenda, write_public_artifacts, write_room_artifacts
from agentsassemble.config import load_council_config
from agentsassemble.decision_gate import derive_decision_gate
from agentsassemble.decision_status import derive_decision_status
from agentsassemble.meeting_phases import (
    free_chat_synthesis_record,
    moderator_disabled_synthesis_record,
    run_debate_phase,
    run_free_chat_phase,
    run_research_phase,
    start_role_sessions,
    synthesize_meeting,
)
from agentsassemble.meeting_record import assemble_meeting_record, derive_failure_state
from agentsassemble.meeting_setup import prepare_meeting_setup
from agentsassemble.meeting_events import MeetingEventLog, append_live_event, write_live_state
from agentsassemble.memory import load_memory_context, write_memory_artifacts
from agentsassemble.models import (
    MeetingResult,
    ModeratorConfig,
    ResearchDepthName,
    ResearchSteering,
    get_research_depth,
    normalize_meeting_mode,
)
from agentsassemble.templates import DEMO_MEETING_TEMPLATE


def run_demo_meeting(
    adapter_name: str = "mock",
    output_root: Path | None = None,
    reporter: Callable[[str], None] | None = None,
    codex_timeout_seconds: int | None = None,
    codex_search_enabled: bool = True,
    research_depth: ResearchDepthName = "smoke",
    research_steering: str | None = None,
    council_config_path: Path | str | None = None,
    agent_config_path: Path | str | None = None,
    meeting_mode: str | None = None,
    moderator_enabled: bool | None = None,
    follow_up_of: str | None = None,
    follow_up_note: str | None = None,
    follow_up_from: Path | str | None = None,
) -> MeetingResult:
    def report(message: str) -> None:
        if reporter is not None:
            reporter(message)

    config = load_council_config(council_config_path)
    if meeting_mode is not None:
        config = replace(config, meeting_mode=normalize_meeting_mode(meeting_mode))
    if moderator_enabled is not None:
        config = replace(config, moderator=ModeratorConfig(enabled=moderator_enabled))
    depth = get_research_depth(research_depth)
    steering = ResearchSteering(
        stance="user_leaning" if research_steering else "open",
        prompt=research_steering,
    )
    setup = prepare_meeting_setup(
        config.roles,
        adapter_name,
        codex_timeout_seconds,
        codex_search_enabled,
        agent_config_path,
    )
    event_log = MeetingEventLog()
    root = output_root or Path(".agentsassemble")
    memory_context = load_memory_context(root, config.roles)
    meeting_id = f"{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}-{uuid4().hex[:8]}"
    meeting_dir = root / "meetings" / meeting_id
    meeting_dir.mkdir(parents=True, exist_ok=False)
    roles = [role.__dict__ for role in config.roles]
    follow_up = _follow_up_metadata(follow_up_of, follow_up_note, follow_up_from)
    write_live_state(
        meeting_dir,
        {
            "meeting_id": meeting_id,
            "question": config.question,
            "display_question": config.display_question,
            "topic": config.topic,
            "display_topic": config.display_topic,
            "follow_up": follow_up,
            "roles": roles,
            "meeting_template": _meeting_template_snapshot(config),
            "meeting_mode": config.meeting_mode,
            "moderator": config.moderator.to_dict(),
            "moderator_control": _moderator_control_snapshot(config),
            "debate_rounds": [],
            "room_chat": [],
            "moderator_synthesis": {},
            "decision_gate": {},
            "agent_bindings": [binding.to_dict() for binding in setup.agent_bindings],
            "provider_configs": {
                provider_id: provider_config.public_dict()
                for provider_id, provider_config in setup.providers.items()
            },
            "permission_profiles": {
                profile_id: profile.to_dict() for profile_id, profile in setup.permissions.items()
            },
            "live_status": "running",
        },
    )
    write_agenda(
        meeting_dir,
        {
            "meeting_id": meeting_id,
            "question": config.question,
            "display_question": config.display_question,
            "topic": config.topic,
            "display_topic": config.display_topic,
            "follow_up": follow_up,
            "roles": roles,
            "meeting_template": _meeting_template_snapshot(config),
            "meeting_mode": config.meeting_mode,
            "moderator": config.moderator.to_dict(),
            "moderator_control": _moderator_control_snapshot(config),
            "memory_context": memory_context,
            "research_steering": steering.to_dict(),
            "research_depth": {
                "name": depth.name,
                "label": depth.label,
                "target_sources": depth.target_sources,
                "min_claims": depth.min_claims,
                "min_counterclaims": depth.min_counterclaims,
            },
            "live_status": "running",
        },
    )
    live_event = lambda payload: append_live_event(meeting_dir, payload)
    live_event({"kind": "status", "content": "회의가 생성되었습니다."})
    report(f"Meeting {meeting_id}")
    report(f"Question: {config.display_question}")
    report(
        "Research depth: "
        f"{depth.name} (target {depth.target_sources} sources, "
        f"{depth.min_claims} claims, {depth.min_counterclaims} counterclaims per role)"
    )
    if not steering.is_open:
        report(f"Research steering: {steering.prompt}")
    event_log.add(
        "meeting_started",
        "Meeting created.",
        meeting_id=meeting_id,
        adapter=adapter_name,
        research_depth=depth.name,
        research_steering=steering.to_dict(),
        follow_up=follow_up,
    )

    context = {
        "meeting_id": meeting_id,
        "question": config.question,
        "topic": config.topic,
        "display_question": config.display_question,
        "display_topic": config.display_topic,
        "meeting_dir": str(meeting_dir),
        "research_depth": depth.name,
        "research_steering": steering.to_dict(),
        "meeting_mode": config.meeting_mode,
        "moderator": config.moderator.to_dict(),
        "follow_up": follow_up,
        "agent_config_source": setup.config_source,
        "memory_context": memory_context,
    }

    sessions = start_role_sessions(
        config,
        meeting_dir,
        context,
        setup.resolved_agents,
        report,
        live_event,
    )
    event_log.add("role_sessions_started", "Role sessions prepared.", role_count=len(sessions))
    if config.meeting_mode == "free_chat":
        room_chat = run_free_chat_phase(
            config,
            sessions,
            setup.resolved_agents,
            report,
            live_event,
        )
        event_log.add("free_chat_recorded", "Informal room chat recorded.", message_count=len(room_chat))
        synthesis = free_chat_synthesis_record()
        evidence_gate = _empty_evidence_gate()
        meeting = assemble_meeting_record(
            meeting_id=meeting_id,
            adapter_name=adapter_name,
            config=config,
            roles=roles,
            setup=setup,
            sessions=sessions,
            memory_context=memory_context,
            research_records=[],
            debate_rounds=[],
            synthesis=synthesis,
            evidence_gate=evidence_gate,
            depth=depth,
            steering=steering,
            event_log=event_log.to_list(),
        )
        meeting["follow_up"] = follow_up
        meeting["moderator_control"] = _moderator_control_snapshot(config)
        meeting["room_chat"] = room_chat
        meeting["decision_gate"] = _free_chat_decision_gate()
        meeting["decision_status"] = derive_decision_status(synthesis, evidence_gate, meeting["decision_gate"])
        meeting["failure_state"] = derive_failure_state(
            synthesis=synthesis,
            evidence_gate=evidence_gate,
            decision_gate=meeting["decision_gate"],
            research_records=[],
            debate_rounds=[],
            room_chat=room_chat,
        )
        meeting["memory_artifacts"] = {}
        meeting["artifacts"] = {
            "agenda": "agenda.md",
            "room_log": "room-log.md",
            "meeting": "meeting.json",
        }
        event_log.add("artifacts_written", "Room log artifact written.", meeting_dir=str(meeting_dir))
        meeting["event_log"] = event_log.to_list()
        meeting["live_status"] = "complete"
        write_room_artifacts(meeting_dir, meeting)
        write_live_state(meeting_dir, meeting)
        live_event({"kind": "artifact", "content": "자유채팅 기록이 저장되었습니다."})
        report("Decision: no official decision (free chat mode)")
        report(f"Artifacts: {meeting_dir}")
        return MeetingResult(meeting_id=meeting_id, meeting_dir=meeting_dir)

    research_records, evidence_gate = run_research_phase(
        config,
        meeting_dir,
        sessions,
        setup.resolved_agents,
        depth,
        steering,
        report,
        live_event,
    )
    event_log.add(
        "research_completed",
        "Independent research completed.",
        role_count=len(research_records),
        evidence_gate_status=evidence_gate.get("status"),
    )
    debate_rounds = run_debate_phase(
        config,
        sessions,
        setup.resolved_agents,
        research_records,
        evidence_gate,
        report,
        live_event,
    )
    event_log.add("debate_completed", "Debate rounds completed.", round_count=len(debate_rounds))
    if config.moderator.enabled:
        synthesis = synthesize_meeting(
            setup.moderator_adapter,
            meeting_id,
            meeting_dir,
            config.question,
            research_records,
            debate_rounds,
            evidence_gate,
            report,
            live_event,
        )
        event_log.add(
            "synthesis_completed",
            "Moderator synthesis completed.",
            winner=synthesis.get("winner"),
            confidence=synthesis.get("confidence"),
        )
        decision_gate = derive_decision_gate(synthesis, evidence_gate, research_records, debate_rounds)
    else:
        report("Moderator synthesis skipped")
        synthesis = moderator_disabled_synthesis_record()
        decision_gate = _moderator_disabled_decision_gate()
        event_log.add(
            "synthesis_skipped",
            "Moderator synthesis skipped because moderator is disabled.",
            required_action=decision_gate["required_action"],
        )

    meeting = assemble_meeting_record(
        meeting_id=meeting_id,
        adapter_name=adapter_name,
        config=config,
        roles=roles,
        setup=setup,
        sessions=sessions,
        memory_context=memory_context,
        research_records=research_records,
        debate_rounds=debate_rounds,
        synthesis=synthesis,
        evidence_gate=evidence_gate,
        depth=depth,
        steering=steering,
        event_log=event_log.to_list(),
    )
    meeting["follow_up"] = follow_up
    meeting["moderator_control"] = _moderator_control_snapshot(config)
    meeting["decision_gate"] = decision_gate
    meeting["decision_status"] = derive_decision_status(synthesis, evidence_gate, meeting["decision_gate"])
    meeting["failure_state"] = derive_failure_state(
        synthesis=synthesis,
        evidence_gate=evidence_gate,
        decision_gate=meeting["decision_gate"],
        research_records=research_records,
        debate_rounds=debate_rounds,
    )
    meeting["memory_artifacts"] = write_memory_artifacts(root, meeting)
    meeting["artifacts"]["memory"] = "memory/"
    event_log.add("artifacts_written", "Public artifacts written.", meeting_dir=str(meeting_dir))
    meeting["event_log"] = event_log.to_list()
    meeting["live_status"] = "complete"
    write_public_artifacts(meeting_dir, meeting)
    write_live_state(meeting_dir, meeting)
    live_event({"kind": "artifact", "content": "회의 기록과 산출물이 저장되었습니다."})
    report(f"Decision: {synthesis['winner']} ({synthesis['confidence']} confidence)")
    report(f"Artifacts: {meeting_dir}")
    return MeetingResult(meeting_id=meeting_id, meeting_dir=meeting_dir)


def _meeting_template_snapshot(config) -> dict[str, object]:
    rounds = config.rounds or DEMO_MEETING_TEMPLATE["rounds"]
    return {
        "id": config.meeting_template_id,
        "display_name": config.meeting_template_name,
        "rounds": [
            {
                "id": round_definition.id,
                "title": round_definition.title,
                "context_scope": round_definition.context_scope,
                "instruction": round_definition.instruction,
                "turn_control": round_definition.turn_control.to_dict(),
            }
            for round_definition in rounds
        ],
    }


def _moderator_control_snapshot(config) -> dict[str, object]:
    return {
        "enabled": config.moderator.enabled,
        "moderator_id": "moderator",
        "official_channel": "official",
        "informal_channels": ["lobby", "side_chat"],
        "default_official_engagement": "moderator_called",
        "informal_default_engagement": "mentioned",
        "official_record_channels": ["official"],
        "host_approval_required_for": ["implementation", "commit", "push", "pr", "deploy", "release"],
    }


def _free_chat_decision_gate() -> dict[str, Any]:
    return {
        "status": "no_official_decision",
        "can_finalize": False,
        "required_action": "start_debate_mode",
        "reasons": ["free_chat_mode_excludes_official_record"],
        "minority_positions": [],
        "ambiguous_positions": [],
        "final_state": False,
    }


def _moderator_disabled_decision_gate() -> dict[str, Any]:
    return {
        "status": "needs_user_decision",
        "can_finalize": False,
        "required_action": "user_decision",
        "reasons": ["moderator_disabled"],
        "minority_positions": [],
        "ambiguous_positions": [],
        "final_state": False,
    }


def _empty_evidence_gate() -> dict[str, Any]:
    return {
        "status": "not_applicable",
        "total_supported_claims": 0,
        "total_unsupported_claims": 0,
        "total_weak_claims": 0,
        "total_verifier_rejected_claims": 0,
    }


def _follow_up_metadata(
    parent_meeting_id: str | None,
    note: str | None,
    parent_meeting_dir: Path | str | None = None,
) -> dict[str, object]:
    metadata: dict[str, object] = {
        "parent_meeting_id": parent_meeting_id,
        "note": note,
        "parent_meeting_dir": str(parent_meeting_dir) if parent_meeting_dir else None,
        "artifact_refs": {},
        "missing_refs": [],
    }
    if parent_meeting_dir is None:
        return metadata
    parent_dir = Path(parent_meeting_dir)
    meeting_path = parent_dir / "meeting.json"
    if meeting_path.exists():
        parent_meeting = json.loads(meeting_path.read_text(encoding="utf-8"))
        metadata["parent_meeting_id"] = parent_meeting.get("meeting_id") or parent_meeting_id
    metadata["artifact_refs"] = {
        "agenda": str(parent_dir / "agenda.md"),
        "transcript": str(parent_dir / "transcript.md"),
        "decision": str(parent_dir / "decision.md"),
        "meeting": str(parent_dir / "meeting.json"),
    }
    metadata["missing_refs"] = [
        name for name, artifact_path in metadata["artifact_refs"].items() if not Path(str(artifact_path)).exists()
    ]
    return metadata
