from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from agentsassemble.models import Role


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_role_files(meeting_dir: Path, role: Role) -> None:
    role_dir = meeting_dir / "roles" / role.id
    role_dir.mkdir(parents=True, exist_ok=True)
    (role_dir / "role.md").write_text(
        f"# {role.display_name}\n\nLens: {role.lens}\n\nFocus: {role.research_focus}\n\n"
        f"Source preferences: {role.source_preferences or []}\n",
        encoding="utf-8",
    )
    (role_dir / "persona.md").write_text(
        f"# Persona\n\nMaintain the {role.display_name} perspective without converging into the other roles.\n\n"
        f"{role.personality or {}}\n",
        encoding="utf-8",
    )
    (role_dir / "memory.md").write_text(
        "# Memory\n\nNo prior meetings recorded yet.\n",
        encoding="utf-8",
    )
    (role_dir / "history.jsonl").write_text("", encoding="utf-8")


def write_research(meeting_dir: Path, research: dict[str, Any]) -> None:
    research_dir = meeting_dir / "private_research" / research["role_id"]
    research_dir.mkdir(parents=True, exist_ok=True)
    write_json(research_dir / "research.json", research)

    depth = research.get("research_depth", {})
    lines = [
        f"# Research: {research['display_name']}",
        "",
        "## Depth",
        f"- Name: {depth.get('name', 'unknown')}",
        f"- Target sources: {depth.get('target_sources', 'unknown')}",
        f"- Minimum claims: {depth.get('min_claims', 'unknown')}",
        f"- Minimum counterclaims: {depth.get('min_counterclaims', 'unknown')}",
        "",
        "## Queries",
        *[f"- {query}" for query in research["queries"]],
        "",
        "## Sources",
    ]
    for source in research["sources"]:
        lines.extend(
            [
                f"- {source['url']}",
                f"  - Title: {source.get('title', '')}",
                f"  - Type: {source.get('source_type', 'unknown')}",
                f"  - Quality: {source.get('quality', 'unknown')}",
                f"  - Note: {source.get('note', '')}",
                f"  - Snippet: {source.get('snippet', '')}",
            ]
        )
        for note in source.get("extracted_notes", []):
            lines.append(f"  - Extracted: {note}")
    lines.extend(
        [
            "",
            "## Summary",
            research["summary"],
            "",
            "## Confidence",
            research["confidence"],
            "",
            "## Uncertainty",
            research["uncertainty"],
            "",
            "## Coverage Gaps",
            *[f"- {gap}" for gap in research.get("coverage_gaps", [])],
            "",
            "## Claim Evidence",
        ]
    )
    for claim in research.get("claim_evidence", []):
        lines.extend(
            [
                f"- Claim: {claim.get('claim', '')}",
                f"  - Confidence: {claim.get('confidence', '')}",
                f"  - Source quality: {claim.get('source_quality', '')}",
                f"  - Interpretation: {claim.get('interpretation', '')}",
            ]
        )
        for url in claim.get("evidence", []):
            lines.append(f"  - Evidence: {url}")
    lines.extend(["", "## Counterclaims"])
    for claim in research.get("counterclaims", []):
        lines.extend(
            [
                f"- Claim: {claim.get('claim', '')}",
                f"  - Confidence: {claim.get('confidence', '')}",
                f"  - Why it matters: {claim.get('why_it_matters', '')}",
            ]
        )
        for url in claim.get("evidence", []):
            lines.append(f"  - Evidence: {url}")
    lines.extend(["", "## Rejected Claims"])
    for claim in research.get("rejected_claims", []):
        lines.extend(
            [
                f"- Claim: {claim.get('claim', '')}",
                f"  - Reason: {claim.get('reason', '')}",
            ]
        )
        for url in claim.get("sources", []):
            lines.append(f"  - Source: {url}")
    (research_dir / "research.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_public_artifacts(meeting_dir: Path, meeting: dict[str, Any]) -> None:
    agenda = [
        "# Agenda",
        "",
        f"Question: {meeting.get('display_question', meeting['question'])}",
        f"Research depth: {meeting.get('research_depth', {}).get('name', 'unknown')}",
        "",
        "1. Independent research",
        "2. Round 1: opening positions",
        "3. Round 2: rebuttal and evidence comparison",
        "4. Moderator synthesis",
        "5. Decision and task assignment",
    ]
    (meeting_dir / "agenda.md").write_text("\n".join(agenda) + "\n", encoding="utf-8")

    transcript_lines = ["# Transcript", ""]
    for round_record in meeting["debate_rounds"]:
        transcript_lines.extend([f"## {round_record['title']}", ""])
        for message in round_record["messages"]:
            transcript_lines.extend([f"### {message['display_name']}", "", message["content"], ""])
    transcript_lines.extend(["## Moderator Synthesis", "", meeting["moderator_synthesis"]["summary"], ""])
    (meeting_dir / "transcript.md").write_text("\n".join(transcript_lines), encoding="utf-8")

    synthesis = meeting["moderator_synthesis"]
    decision = [
        "# Decision",
        "",
        f"Winner: {synthesis['winner']}",
        "",
        "## Ranking",
        *[f"{index + 1}. {name}" for index, name in enumerate(synthesis["ranking"])],
        "",
        "## Confidence",
        synthesis["confidence"],
        "",
        "## Caveats",
        *[f"- {caveat}" for caveat in synthesis["caveats"]],
        "",
        "## Rationale",
        synthesis["summary"],
    ]
    (meeting_dir / "decision.md").write_text("\n".join(decision) + "\n", encoding="utf-8")

    tasks_dir = meeting_dir / "tasks"
    tasks_dir.mkdir(exist_ok=True)
    for role_id, task in synthesis["tasks"].items():
        (tasks_dir / f"{role_id}.md").write_text(f"# Task\n\n{task}\n", encoding="utf-8")

    write_json(meeting_dir / "meeting.json", meeting)
