from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Callable
from uuid import uuid4

from agentsassemble.artifacts import write_public_artifacts
from agentsassemble.config import load_council_config
from agentsassemble.meeting_phases import (
    run_debate_phase,
    run_research_phase,
    start_role_sessions,
    synthesize_meeting,
)
from agentsassemble.meeting_record import assemble_meeting_record
from agentsassemble.meeting_setup import prepare_meeting_setup
from agentsassemble.meeting_events import MeetingEventLog
from agentsassemble.memory import load_memory_context, write_memory_artifacts
from agentsassemble.models import (
    MeetingResult,
    ResearchDepthName,
    ResearchSteering,
    get_research_depth,
)


def run_demo_meeting(
    adapter_name: str = "mock",
    output_root: Path | None = None,
    reporter: Callable[[str], None] | None = None,
    codex_timeout_seconds: int = 240,
    codex_search_enabled: bool = True,
    research_depth: ResearchDepthName = "smoke",
    research_steering: str | None = None,
) -> MeetingResult:
    def report(message: str) -> None:
        if reporter is not None:
            reporter(message)

    config = load_council_config()
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
    )
    event_log = MeetingEventLog()
    root = output_root or Path(".agentsassemble")
    memory_context = load_memory_context(root, config.roles)
    meeting_id = f"{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}-{uuid4().hex[:8]}"
    meeting_dir = root / "meetings" / meeting_id
    meeting_dir.mkdir(parents=True, exist_ok=False)
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
        "memory_context": memory_context,
    }

    roles = [role.__dict__ for role in config.roles]
    sessions = start_role_sessions(
        config,
        meeting_dir,
        context,
        setup.resolved_agents,
        report,
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
    write_public_artifacts(meeting_dir, meeting)
    report(f"Decision: {synthesis['winner']} ({synthesis['confidence']} confidence)")
    report(f"Artifacts: {meeting_dir}")
    return MeetingResult(meeting_id=meeting_id, meeting_dir=meeting_dir)
