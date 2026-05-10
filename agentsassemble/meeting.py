from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Callable
from uuid import uuid4

from agentsassemble.artifacts import write_agenda, write_public_artifacts
from agentsassemble.config import load_council_config
from agentsassemble.meeting_phases import (
    run_debate_phase,
    run_research_phase,
    start_role_sessions,
    synthesize_meeting,
)
from agentsassemble.meeting_record import assemble_meeting_record
from agentsassemble.meeting_setup import prepare_meeting_setup
from agentsassemble.meeting_events import MeetingEventLog, append_live_event, write_live_state
from agentsassemble.memory import load_memory_context, write_memory_artifacts
from agentsassemble.models import (
    MeetingResult,
    ResearchDepthName,
    ResearchSteering,
    get_research_depth,
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
) -> MeetingResult:
    def report(message: str) -> None:
        if reporter is not None:
            reporter(message)

    config = load_council_config(council_config_path)
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
    write_live_state(
        meeting_dir,
        {
            "meeting_id": meeting_id,
            "question": config.question,
            "display_question": config.display_question,
            "topic": config.topic,
            "display_topic": config.display_topic,
            "roles": roles,
            "debate_rounds": [],
            "moderator_synthesis": {},
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
            "roles": roles,
            "meeting_template": _meeting_template_snapshot(),
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


def _meeting_template_snapshot() -> dict[str, object]:
    return {
        "id": DEMO_MEETING_TEMPLATE["id"],
        "display_name": DEMO_MEETING_TEMPLATE["display_name"],
        "rounds": [
            {
                "id": round_definition.id,
                "title": round_definition.title,
                "context_scope": round_definition.context_scope,
                "instruction": round_definition.instruction,
            }
            for round_definition in DEMO_MEETING_TEMPLATE["rounds"]
        ],
    }
