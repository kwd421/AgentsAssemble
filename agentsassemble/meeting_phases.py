from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from agentsassemble.artifacts import write_research, write_role_files
from agentsassemble.evidence import apply_evidence_gate, summarize_evidence_gates
from agentsassemble.models import CouncilConfig, ResearchDepth, ResearchSteering
from agentsassemble.templates import DEMO_MEETING_TEMPLATE


def start_role_sessions(
    config: CouncilConfig,
    meeting_dir: Path,
    context: dict[str, Any],
    resolved_agents: dict[str, Any],
    report: Callable[[str], None],
    live_event: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, dict[str, Any]]:
    sessions = {}
    for role in config.roles:
        report(f"Preparing role: {role.display_name} ({role.id})")
        if live_event is not None:
            live_event({"kind": "status", "role_id": role.id, "display_name": role.display_name, "content": "세션 준비 중"})
        write_role_files(meeting_dir, role)
        sessions[role.id] = resolved_agents[role.id].adapter.start_session(role, context)
        if live_event is not None:
            live_event({"kind": "status", "role_id": role.id, "display_name": role.display_name, "content": "세션 준비 완료"})
    return sessions


def run_research_phase(
    config: CouncilConfig,
    meeting_dir: Path,
    sessions: dict[str, dict[str, Any]],
    resolved_agents: dict[str, Any],
    depth: ResearchDepth,
    steering: ResearchSteering,
    report: Callable[[str], None],
    live_event: Callable[[dict[str, Any]], None] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    research_records = []
    for role in config.roles:
        report(f"Research: {role.display_name}")
        if live_event is not None:
            live_event({"kind": "status", "role_id": role.id, "display_name": role.display_name, "content": "독립 리서치 시작"})
        research = resolved_agents[role.id].adapter.run_research(
            role,
            sessions[role.id],
            config.question,
            depth,
            steering,
        )
        research = apply_evidence_gate(research, depth)
        research_records.append(research)
        write_research(meeting_dir, research)
        if live_event is not None:
            live_event(
                {
                    "kind": "research",
                    "role_id": role.id,
                    "display_name": role.display_name,
                    "content": research.get("summary", "리서치 완료"),
                    "confidence": research.get("confidence"),
                }
            )
    return research_records, summarize_evidence_gates(research_records)


def run_debate_phase(
    config: CouncilConfig,
    sessions: dict[str, dict[str, Any]],
    resolved_agents: dict[str, Any],
    research_records: list[dict[str, Any]],
    evidence_gate: dict[str, Any],
    report: Callable[[str], None],
    live_event: Callable[[dict[str, Any]], None] | None = None,
) -> list[dict[str, Any]]:
    debate_rounds = []
    rounds_by_id: dict[str, list[dict[str, Any]]] = {}
    for round_definition in DEMO_MEETING_TEMPLATE["rounds"]:
        report(round_definition.report_label)
        if live_event is not None:
            live_event({"kind": "status", "round": round_definition.id, "content": round_definition.report_label})
        messages = []
        if round_definition.context_scope == "own_research":
            for role, research in zip(config.roles, research_records, strict=True):
                message = resolved_agents[role.id].adapter.run_round(
                        role,
                        sessions[role.id],
                        round_definition.id,
                        round_definition.instruction,
                        {
                            "own_research": research,
                            "evidence_gate_rule": "Use supported claim_evidence as grounds. Mention unsupported_claims only as discarded or uncertain material.",
                            "stance_rule": "Keep your role's own position distinct. Do not soften your stance just to agree with the room.",
                        },
                    )
                messages.append(message)
                if live_event is not None:
                    live_event({"kind": "message", **message})
        else:
            public_context = {
                **rounds_by_id,
                "evidence_gate": evidence_gate,
                "evidence_gate_rule": "Do not treat unsupported claims as accepted facts.",
                "stance_rule": (
                    "You may revise your position only when another role gives supported evidence that changes your reasoning. "
                    "Otherwise, hold your position and attack the weakest premise."
                ),
            }
            for role in config.roles:
                message = resolved_agents[role.id].adapter.run_round(
                        role,
                        sessions[role.id],
                        round_definition.id,
                        round_definition.instruction,
                        public_context,
                    )
                messages.append(message)
                if live_event is not None:
                    live_event({"kind": "message", **message})
        rounds_by_id[round_definition.id] = messages
        debate_rounds.append(
            {
                "id": round_definition.id,
                "title": round_definition.title,
                "context_scope": round_definition.context_scope,
                "instruction": round_definition.instruction,
                "messages": messages,
            }
        )
    return debate_rounds


def synthesize_meeting(
    moderator_adapter: Any,
    meeting_id: str,
    meeting_dir: Path,
    question: str,
    research_records: list[dict[str, Any]],
    debate_rounds: list[dict[str, Any]],
    evidence_gate: dict[str, Any],
    report: Callable[[str], None],
    live_event: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    moderator_session = {
        "adapter": moderator_adapter.name,
        "role_id": "moderator",
        "session_id": f"{moderator_adapter.name}-{meeting_id}-moderator",
        "meeting_dir": str(meeting_dir),
    }
    report("Moderator synthesis")
    if live_event is not None:
        live_event({"kind": "status", "role_id": "moderator", "display_name": "Moderator", "content": "종합 시작"})
    synthesis = moderator_adapter.synthesize(
        moderator_session,
        question,
        {
            "research_summaries": [
                {
                    "role_id": research["role_id"],
                    "summary": research["summary"],
                    "confidence": research["confidence"],
                    "claim_evidence": research["claim_evidence"],
                    "unsupported_claims": research.get("unsupported_claims", []),
                    "weak_claims": research.get("weak_claims", []),
                    "verifier_rejected_claims": research.get("verifier_rejected_claims", []),
                    "claim_verification": research.get("claim_verification", []),
                    "evidence_gate": research.get("evidence_gate", {}),
                    "counterclaims": research.get("counterclaims", []),
                    "coverage_gaps": research.get("coverage_gaps", []),
                }
                for research in research_records
            ],
            "rounds": {round_record["id"]: round_record["messages"] for round_record in debate_rounds},
            "evidence_gate": evidence_gate,
            "moderator_rule": "Base the decision on supported claim_evidence. Unsupported, weak, verifier-rejected, irrelevant, or contradictory claims may be listed as caveats but must not determine the winner.",
            "stance_rule": "Treat held, revised, and conceded stances as debate state. Do not collapse disagreement into fake consensus.",
        },
    )
    if live_event is not None:
        live_event(
            {
                "kind": "synthesis",
                "role_id": "moderator",
                "display_name": "Moderator",
                "content": synthesis.get("summary", "종합 완료"),
                "position": synthesis.get("winner", ""),
                "confidence": synthesis.get("confidence"),
            }
        )
    return synthesis
