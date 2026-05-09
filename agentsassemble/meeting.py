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
from agentsassemble.meeting_setup import (
    default_agent_bindings,
    default_permissions,
    get_adapter,
    prepare_meeting_setup,
    provider_config_for_adapter,
)
from agentsassemble.memory import load_memory_context, write_memory_artifacts
from agentsassemble.models import (
    AgentBinding,
    MeetingResult,
    PermissionProfile,
    ProviderConfig,
    ResearchDepthName,
    ResearchSteering,
    get_research_depth,
)


def _provider_config_for_adapter(
    adapter_name: str,
    codex_timeout_seconds: int,
    codex_search_enabled: bool,
) -> ProviderConfig:
    return provider_config_for_adapter(adapter_name, codex_timeout_seconds, codex_search_enabled)


def _default_permissions(adapter_name: str, codex_search_enabled: bool) -> dict[str, PermissionProfile]:
    return default_permissions(adapter_name, codex_search_enabled)


def _default_agent_bindings(config_roles: list[object], provider_id: str) -> list[AgentBinding]:
    return default_agent_bindings(config_roles, provider_id)


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
    research_records, evidence_gate = run_research_phase(
        config,
        meeting_dir,
        sessions,
        setup.resolved_agents,
        depth,
        steering,
        report,
    )
    debate_rounds = run_debate_phase(
        config,
        sessions,
        setup.resolved_agents,
        research_records,
        evidence_gate,
        report,
    )
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
    )
    meeting["memory_artifacts"] = write_memory_artifacts(root, meeting)
    meeting["artifacts"]["memory"] = "memory/"
    write_public_artifacts(meeting_dir, meeting)
    report(f"Decision: {synthesis['winner']} ({synthesis['confidence']} confidence)")
    report(f"Artifacts: {meeting_dir}")
    return MeetingResult(meeting_id=meeting_id, meeting_dir=meeting_dir)
