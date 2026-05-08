from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

from agentsassemble.adapters import CodexAdapter, MockAdapter, ProviderAdapter
from agentsassemble.artifacts import write_public_artifacts, write_research, write_role_files
from agentsassemble.config import load_council_config
from agentsassemble.evidence import apply_evidence_gate, summarize_evidence_gates
from agentsassemble.memory import load_memory_context, write_memory_artifacts
from agentsassemble.models import MeetingResult, ResearchDepthName, ResearchSteering, get_research_depth


def get_adapter(
    adapter_name: str,
    codex_timeout_seconds: int = 240,
    codex_search_enabled: bool = True,
) -> ProviderAdapter:
    if adapter_name == "mock":
        return MockAdapter()
    if adapter_name == "codex":
        return CodexAdapter(timeout_seconds=codex_timeout_seconds, search_enabled=codex_search_enabled)
    raise ValueError(f"Unknown adapter: {adapter_name}")


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
    adapter = get_adapter(
        adapter_name,
        codex_timeout_seconds=codex_timeout_seconds,
        codex_search_enabled=codex_search_enabled,
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

    context: dict[str, Any] = {
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
    sessions = {}
    for role in config.roles:
        report(f"Preparing role: {role.display_name} ({role.id})")
        write_role_files(meeting_dir, role)
        sessions[role.id] = adapter.start_session(role, context)

    research_records = []
    for role in config.roles:
        report(f"Research: {role.display_name}")
        research = adapter.run_research(role, sessions[role.id], config.question, depth, steering)
        research = apply_evidence_gate(research, depth)
        research_records.append(research)
        write_research(meeting_dir, research)
    evidence_gate = summarize_evidence_gates(research_records)

    round_one = []
    report("Round 1: opening positions")
    for role, research in zip(config.roles, research_records, strict=True):
        round_one.append(
            adapter.run_round(
                role,
                sessions[role.id],
                "round_1",
                (
                    "Present your opening position from your private research. Cite your strongest evidence "
                    "and at least one uncertainty. State the position you are defending and the evidence that "
                    "would make you change your mind."
                ),
                {
                    "own_research": research,
                    "evidence_gate_rule": "Use supported claim_evidence as grounds. Mention unsupported_claims only as discarded or uncertain material.",
                    "stance_rule": "Keep your role's own position distinct. Do not soften your stance just to agree with the room.",
                },
            )
        )

    round_two = []
    public_round_one = {
        "round_1": round_one,
        "evidence_gate": evidence_gate,
        "evidence_gate_rule": "Do not treat unsupported claims as accepted facts.",
        "stance_rule": (
            "You may revise your position only when another role gives supported evidence that changes your reasoning. "
            "Otherwise, hold your position and attack the weakest premise."
        ),
    }
    report("Round 2: rebuttal and evidence comparison")
    for role in config.roles:
        round_two.append(
            adapter.run_round(
                role,
                sessions[role.id],
                "round_2",
                (
                    "Compare evidence and rebut weak reasoning without reading private research. Challenge source "
                    "quality, unsupported leaps, and missing counterevidence. Hold your position unless the public "
                    "evidence crosses your stated change conditions; if you revise, say exactly which evidence caused it."
                ),
                public_round_one,
            )
        )

    moderator_session = {
        "adapter": adapter.name,
        "role_id": "moderator",
        "session_id": f"{adapter.name}-{meeting_id}-moderator",
        "meeting_dir": str(meeting_dir),
    }
    report("Moderator synthesis")
    synthesis = adapter.synthesize(
        moderator_session,
        config.question,
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
            "round_1": round_one,
            "round_2": round_two,
            "evidence_gate": evidence_gate,
            "moderator_rule": "Base the decision on supported claim_evidence. Unsupported, weak, verifier-rejected, irrelevant, or contradictory claims may be listed as caveats but must not determine the winner.",
            "stance_rule": "Treat held, revised, and conceded stances as debate state. Do not collapse disagreement into fake consensus.",
        },
    )
    memory_input = {
        "research_summaries": [
            {
                "role_id": research["role_id"],
                "display_name": research["display_name"],
                "summary": research["summary"],
                "confidence": research["confidence"],
                "evidence_gate": research.get("evidence_gate", {}),
            }
            for research in research_records
        ]
    }

    meeting = {
        "meeting_id": meeting_id,
        "command": f"assemble demo --adapter {adapter_name}",
        "question": config.question,
        "display_question": config.display_question,
        "topic": config.topic,
        "display_topic": config.display_topic,
        "roles": roles,
        "adapter_config": {"name": adapter.name},
        "memory_context": memory_context,
        "memory_input": memory_input,
        "research_steering": steering.to_dict(),
        "research_depth": {
            "name": depth.name,
            "label": depth.label,
            "min_sources": depth.min_sources,
            "target_sources": depth.target_sources,
            "min_queries": depth.min_queries,
            "min_claims": depth.min_claims,
            "min_counterclaims": depth.min_counterclaims,
            "notes_per_source": depth.notes_per_source,
            "source_mix": depth.source_mix,
            "instructions": depth.instructions,
        },
        "isolation": {
            role.id: {
                "role_dir": f"roles/{role.id}",
                "private_research_dir": f"private_research/{role.id}",
                "session": sessions[role.id],
            }
            for role in config.roles
        },
        "research_artifacts": [
            {
                "role_id": research["role_id"],
                "path": f"private_research/{research['role_id']}/research.json",
            }
            for research in research_records
        ],
        "debate_rounds": [
            {"id": "round_1", "title": "Round 1", "messages": round_one},
            {"id": "round_2", "title": "Round 2", "messages": round_two},
        ],
        "moderator_synthesis": synthesis,
        "evidence_gate": evidence_gate,
        "artifacts": {
            "agenda": "agenda.md",
            "transcript": "transcript.md",
            "decision": "decision.md",
            "meeting": "meeting.json",
            "tasks": "tasks/",
            "private_research": "private_research/",
        },
        "audit_metadata": {
            "created_at": datetime.now(UTC).isoformat(),
            "adapter": adapter.name,
            "research_depth": depth.name,
            "research_steering": steering.to_dict(),
            "reproducibility": "auditable and resumable, not deterministic replay",
        },
        "failure_state": {"status": "none", "failures": []},
    }

    meeting["memory_artifacts"] = write_memory_artifacts(root, meeting)
    meeting["artifacts"]["memory"] = "memory/"
    write_public_artifacts(meeting_dir, meeting)
    report(f"Decision: {synthesis['winner']} ({synthesis['confidence']} confidence)")
    report(f"Artifacts: {meeting_dir}")
    return MeetingResult(meeting_id=meeting_id, meeting_dir=meeting_dir)
