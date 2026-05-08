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
    memory = meeting_dir.parent.parent / "memory" / "agents" / f"{role.id}.md"
    (role_dir / "memory.md").write_text(
        memory.read_text(encoding="utf-8") if memory.exists() else "# Memory\n\nNo prior meetings recorded yet.\n",
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
            "## Evidence Gate",
            f"- Status: {research.get('evidence_gate', {}).get('status', 'unknown')}",
            f"- Supported claims: {research.get('evidence_gate', {}).get('supported_claim_count', 0)}",
            f"- Unsupported claims: {research.get('evidence_gate', {}).get('unsupported_claim_count', 0)}",
            f"- Weak claims: {research.get('evidence_gate', {}).get('weak_claim_count', 0)}",
            f"- Verifier rejected claims: {research.get('evidence_gate', {}).get('verifier_rejected_claim_count', 0)}",
            f"- Confidence after gate: {research.get('evidence_gate', {}).get('confidence_after', research.get('confidence', 'low'))}",
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
    lines.extend(["", "## Claim Verification"])
    for record in research.get("claim_verification", []):
        lines.extend(
            [
                f"- Claim: {record.get('claim', '')}",
                f"  - URL: {record.get('url', '')}",
                f"  - Verdict: {record.get('verdict', '')}",
                f"  - Reason: {record.get('reason', '')}",
                f"  - Source quality: {record.get('source_quality', '')}",
                f"  - Source type: {record.get('source_type', '')}",
            ]
        )
    lines.extend(["", "## Weak Claims"])
    for claim in research.get("weak_claims", []):
        lines.extend(
            [
                f"- Claim: {claim.get('claim', '')}",
                f"  - Reason: {claim.get('reason', '')}",
            ]
        )
        for url in claim.get("evidence", []):
            lines.append(f"  - Evidence: {url}")
    lines.extend(["", "## Verifier Rejected Claims"])
    for claim in research.get("verifier_rejected_claims", []):
        lines.extend(
            [
                f"- Claim: {claim.get('claim', '')}",
                f"  - Reason: {claim.get('reason', '')}",
            ]
        )
        for url in claim.get("evidence", []):
            lines.append(f"  - Evidence: {url}")
    lines.extend(["", "## Unsupported Claims"])
    for claim in research.get("unsupported_claims", []):
        lines.extend(
            [
                f"- Claim: {claim.get('claim', '')}",
                f"  - Reason: {claim.get('reason', '')}",
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
        f"Research steering: {meeting.get('research_steering', {}).get('prompt') or 'open'}",
        f"Meeting template: {meeting.get('meeting_template', {}).get('display_name', 'default')}",
        f"Recent episodes loaded: {len(meeting.get('memory_context', {}).get('recent_episodes', []))}",
        "",
        "1. Independent research",
    ]
    next_step = 2
    for index, round_definition in enumerate(meeting.get("meeting_template", {}).get("rounds", []), start=next_step):
        agenda.append(f"{index}. {round_definition.get('title', round_definition.get('id', 'Round'))}")
        next_step = index + 1
    agenda.extend(
        [
            f"{next_step}. Moderator synthesis",
            f"{next_step + 1}. Decision and task assignment",
        ]
    )
    (meeting_dir / "agenda.md").write_text("\n".join(agenda) + "\n", encoding="utf-8")

    transcript_lines = ["# Transcript", ""]
    for round_record in meeting["debate_rounds"]:
        transcript_lines.extend([f"## {round_record['title']}", ""])
        for message in round_record["messages"]:
            transcript_lines.extend([f"### {message['display_name']}", ""])
            if message.get("position"):
                transcript_lines.append(f"Position: {message.get('position')}")
            if message.get("stance_status"):
                transcript_lines.append(f"Stance: {message.get('stance_status')}")
            if message.get("change_conditions"):
                transcript_lines.extend(
                    [
                        "Change conditions:",
                        *[f"- {condition}" for condition in message.get("change_conditions", [])],
                    ]
                )
            transcript_lines.extend(["", message["content"], ""])
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
        "## Evidence Gate",
        f"Status: {meeting.get('evidence_gate', {}).get('status', 'unknown')}",
        f"Supported claims: {meeting.get('evidence_gate', {}).get('total_supported_claims', 0)}",
        f"Unsupported claims: {meeting.get('evidence_gate', {}).get('total_unsupported_claims', 0)}",
        f"Weak claims: {meeting.get('evidence_gate', {}).get('total_weak_claims', 0)}",
        f"Verifier rejected claims: {meeting.get('evidence_gate', {}).get('total_verifier_rejected_claims', 0)}",
        "",
        "## Rationale",
        synthesis["summary"],
    ]
    (meeting_dir / "decision.md").write_text("\n".join(decision) + "\n", encoding="utf-8")

    tasks_dir = meeting_dir / "tasks"
    tasks_dir.mkdir(exist_ok=True)
    for role_id, task in synthesis["tasks"].items():
        (tasks_dir / f"{role_id}.md").write_text(f"# Task\n\n{task}\n", encoding="utf-8")

    return_packet_dir = meeting_dir / "return_packets"
    return_packet_dir.mkdir(exist_ok=True)
    meeting["artifacts"]["return_packets"] = "return_packets/"
    meeting["return_packets"] = {}
    for role in meeting.get("roles", []):
        packet = _return_packet(meeting, role)
        role_id = role["id"]
        meeting["return_packets"][role_id] = {
            "json": f"return_packets/{role_id}.json",
            "markdown": f"return_packets/{role_id}.md",
        }
        write_json(return_packet_dir / f"{role_id}.json", packet)
        (return_packet_dir / f"{role_id}.md").write_text(_return_packet_markdown(packet), encoding="utf-8")

    write_json(meeting_dir / "meeting.json", meeting)


def _return_packet(meeting: dict[str, Any], role: dict[str, Any]) -> dict[str, Any]:
    role_id = role["id"]
    synthesis = meeting.get("moderator_synthesis", {})
    role_messages = _role_messages(meeting, role_id)
    research = _research_for_role(meeting, role_id)
    task = synthesis.get("tasks", {}).get(role_id, "No task assigned.")
    final_position = role_messages[-1].get("position", "") if role_messages else ""
    stance_status = role_messages[-1].get("stance_status", "unknown") if role_messages else "unknown"
    winner = synthesis.get("winner", "Undetermined")
    outcome = _role_outcome(final_position, winner)
    return {
        "meeting_id": meeting["meeting_id"],
        "role_id": role_id,
        "display_name": role.get("display_name", role_id),
        "lens": role.get("lens", ""),
        "question": meeting.get("display_question") or meeting.get("question", ""),
        "decision": {
            "winner": winner,
            "confidence": synthesis.get("confidence", "low"),
            "outcome_for_role": outcome,
            "rationale": synthesis.get("summary", ""),
            "caveats": synthesis.get("caveats", []),
        },
        "stance": {
            "final_position": final_position,
            "status": stance_status,
            "change_conditions": role_messages[-1].get("change_conditions", []) if role_messages else [],
            "history": [
                {
                    "round": message.get("round", ""),
                    "position": message.get("position", ""),
                    "stance_status": message.get("stance_status", ""),
                    "confidence": message.get("confidence", ""),
                    "content": message.get("content", ""),
                }
                for message in role_messages
            ],
        },
        "evidence": {
            "gate": research.get("evidence_gate", {}),
            "supported_claims": research.get("claim_evidence", []),
            "weak_claims": research.get("weak_claims", []),
            "unsupported_claims": research.get("unsupported_claims", []),
            "verifier_rejected_claims": research.get("verifier_rejected_claims", []),
        },
        "next_task": task,
        "answer_prompts": {
            "what_happened": "Summarize the meeting from this agent's perspective.",
            "why_win_or_lose": "Explain whether this agent's stance won, lost, partially held, or remained unresolved.",
            "what_changed": "Name the evidence or objections that changed or constrained the stance.",
            "what_next": "State this agent's assigned next task and what to inspect first.",
        },
    }


def _role_messages(meeting: dict[str, Any], role_id: str) -> list[dict[str, Any]]:
    messages = []
    for round_record in meeting.get("debate_rounds", []):
        for message in round_record.get("messages", []):
            if message.get("role_id") == role_id:
                messages.append({**message, "round_title": round_record.get("title", message.get("round", ""))})
    return messages


def _research_for_role(meeting: dict[str, Any], role_id: str) -> dict[str, Any]:
    for summary in meeting.get("memory_input", {}).get("research_summaries", []):
        if summary.get("role_id") == role_id:
            return summary
    return {}


def _role_outcome(position: str, winner: str) -> str:
    if not position or winner == "Undetermined":
        return "unresolved"
    normalized_position = position.casefold()
    normalized_winner = winner.casefold()
    winner_terms = set(normalized_winner.replace("/", " ").split())
    winner_terms.update(_winner_aliases(normalized_winner))
    if normalized_winner in normalized_position or any(term and term in normalized_position for term in winner_terms):
        return "won_or_partially_supported"
    return "lost_or_not_selected"


def _winner_aliases(winner: str) -> set[str]:
    aliases = {
        "akainu": {"아카이누", "사카즈키", "sakazuki"},
        "sakazuki": {"아카이누", "akainu"},
        "aokiji": {"아오키지", "쿠잔", "kuzan"},
        "kuzan": {"아오키지", "aokiji"},
        "kizaru": {"키자루", "보르살리노", "borsalino"},
        "borsalino": {"키자루", "kizaru"},
    }
    result = set()
    for key, values in aliases.items():
        if key in winner:
            result.update(value.casefold() for value in values)
    return result


def _return_packet_markdown(packet: dict[str, Any]) -> str:
    decision = packet["decision"]
    stance = packet["stance"]
    evidence = packet["evidence"]
    lines = [
        f"# Return Packet: {packet['display_name']}",
        "",
        f"- Meeting: {packet['meeting_id']}",
        f"- Question: {packet['question']}",
        f"- Final decision: {decision['winner']} ({decision['confidence']})",
        f"- Outcome for this role: {decision['outcome_for_role']}",
        f"- Final position: {stance['final_position'] or 'Not recorded'}",
        f"- Stance status: {stance['status']}",
        f"- Next task: {packet['next_task']}",
        "",
        "## Why",
        "",
        decision["rationale"],
        "",
        "## Evidence State",
        "",
        f"- Gate: {evidence.get('gate', {}).get('status', 'unknown')}",
        f"- Supported: {len(evidence.get('supported_claims', []))}",
        f"- Weak: {len(evidence.get('weak_claims', []))}",
        f"- Unsupported: {len(evidence.get('unsupported_claims', []))}",
        f"- Verifier rejected: {len(evidence.get('verifier_rejected_claims', []))}",
        "",
        "## Stance History",
        "",
    ]
    for message in stance["history"]:
        lines.extend(
            [
                f"### {message.get('round_title', message.get('round', 'Round'))}",
                "",
                f"- Position: {message.get('position', '')}",
                f"- Stance: {message.get('stance_status', '')}",
                f"- Confidence: {message.get('confidence', '')}",
                "",
                message.get("content", ""),
                "",
            ]
        )
    lines.extend(["## Change Conditions", ""])
    lines.extend([f"- {condition}" for condition in stance.get("change_conditions", [])] or ["- Not recorded."])
    lines.extend(["", "## Caveats", ""])
    lines.extend([f"- {caveat}" for caveat in decision.get("caveats", [])] or ["- None recorded."])
    return "\n".join(lines) + "\n"
