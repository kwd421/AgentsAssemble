from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

from agentsassemble.adapters import CodexAdapter, MockAdapter, ProviderAdapter
from agentsassemble.artifacts import write_public_artifacts, write_research, write_role_files
from agentsassemble.models import MeetingResult, Role


DEMO_QUESTION = "Who is the strongest One Piece admiral?"

DEMO_ROLES = [
    Role(
        id="lore_lawyer",
        display_name="설정충",
        lens="Canon Analyst",
        research_focus="official statements, canon hierarchy, and internal consistency",
        personality={
            "preset": "pedantic_lore_nerd",
            "tone": "precise, lore-obsessed, stubborn about source hierarchy",
            "directness": "medium",
            "humor": "low",
            "verbosity": "medium",
            "catchphrases": ["공식 설정상", "근거 등급부터 따져야 함"],
        },
    ),
    Role(
        id="show_me_the_feats",
        display_name="공식이뭘알아",
        lens="Feats Analyst",
        research_focus="demonstrated combat performance, fight scenes, and concrete outcomes",
        personality={
            "preset": "feat_first_pragmatist",
            "tone": "direct, practical, impatient with unsupported statements",
            "directness": "high",
            "humor": "medium",
            "verbosity": "medium",
            "catchphrases": ["보여준 걸 가져와", "전투 결과가 말해줌"],
        },
    ),
    Role(
        id="fanboard_skeptic",
        display_name="만갤러",
        lens="Skeptical Critic",
        research_focus="fandom claims, overinterpretation, counterexamples, and uncertainty",
        personality={
            "preset": "fanboard_skeptic",
            "tone": "skeptical, playful, community-aware, sharp about overclaims",
            "directness": "high",
            "humor": "high",
            "verbosity": "medium",
            "catchphrases": ["그거 뇌피셜 아님?", "표본 부족"],
        },
    ),
]


def get_adapter(adapter_name: str) -> ProviderAdapter:
    if adapter_name == "mock":
        return MockAdapter()
    if adapter_name == "codex":
        return CodexAdapter()
    raise ValueError(f"Unknown adapter: {adapter_name}")


def run_demo_meeting(
    adapter_name: str = "mock",
    output_root: Path | None = None,
    reporter: Callable[[str], None] | None = None,
) -> MeetingResult:
    def report(message: str) -> None:
        if reporter is not None:
            reporter(message)

    adapter = get_adapter(adapter_name)
    root = output_root or Path(".agentsassemble")
    meeting_id = f"{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}-{uuid4().hex[:8]}"
    meeting_dir = root / "meetings" / meeting_id
    meeting_dir.mkdir(parents=True, exist_ok=False)
    report(f"Meeting {meeting_id}")
    report(f"Question: {DEMO_QUESTION}")

    context: dict[str, Any] = {
        "meeting_id": meeting_id,
        "question": DEMO_QUESTION,
        "topic": "One Piece admiral strength debate",
        "meeting_dir": str(meeting_dir),
    }

    roles = [role.__dict__ for role in DEMO_ROLES]
    sessions = {}
    for role in DEMO_ROLES:
        report(f"Preparing role: {role.display_name} ({role.id})")
        write_role_files(meeting_dir, role)
        sessions[role.id] = adapter.start_session(role, context)

    research_records = []
    for role in DEMO_ROLES:
        report(f"Research: {role.display_name}")
        research = adapter.run_research(role, sessions[role.id], DEMO_QUESTION)
        research_records.append(research)
        write_research(meeting_dir, research)

    round_one = []
    report("Round 1: opening positions")
    for role, research in zip(DEMO_ROLES, research_records, strict=True):
        round_one.append(
            adapter.run_round(
                role,
                sessions[role.id],
                "round_1",
                "Present your opening position from your private research.",
                {"own_research": research},
            )
        )

    round_two = []
    public_round_one = {"round_1": round_one}
    report("Round 2: rebuttal and evidence comparison")
    for role in DEMO_ROLES:
        round_two.append(
            adapter.run_round(
                role,
                sessions[role.id],
                "round_2",
                "Compare evidence and rebut weak reasoning without reading private research.",
                public_round_one,
            )
        )

    moderator_session = {
        "adapter": adapter.name,
        "role_id": "moderator",
        "session_id": f"{adapter.name}-{meeting_id}-moderator",
    }
    report("Moderator synthesis")
    synthesis = adapter.synthesize(
        moderator_session,
        DEMO_QUESTION,
        {
            "research_summaries": [
                {
                    "role_id": research["role_id"],
                    "summary": research["summary"],
                    "confidence": research["confidence"],
                    "claim_evidence": research["claim_evidence"],
                }
                for research in research_records
            ],
            "round_1": round_one,
            "round_2": round_two,
        },
    )

    meeting = {
        "meeting_id": meeting_id,
        "command": f"assemble demo --adapter {adapter_name}",
        "question": DEMO_QUESTION,
        "topic": "One Piece admiral strength debate",
        "roles": roles,
        "adapter_config": {"name": adapter.name},
        "isolation": {
            role.id: {
                "role_dir": f"roles/{role.id}",
                "private_research_dir": f"private_research/{role.id}",
                "session": sessions[role.id],
            }
            for role in DEMO_ROLES
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
            "reproducibility": "auditable and resumable, not deterministic replay",
        },
        "failure_state": {"status": "none", "failures": []},
    }

    write_public_artifacts(meeting_dir, meeting)
    report(f"Decision: {synthesis['winner']} ({synthesis['confidence']} confidence)")
    report(f"Artifacts: {meeting_dir}")
    return MeetingResult(meeting_id=meeting_id, meeting_dir=meeting_dir)
