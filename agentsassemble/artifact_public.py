from __future__ import annotations

from typing import Any

from agentsassemble.meeting_context import public_caveats, public_synthesis


def render_agenda(meeting: dict[str, Any]) -> str:
    agenda = [
        "# Agenda",
        "",
        f"Question: {meeting.get('display_question', meeting['question'])}",
        f"Research depth: {meeting.get('research_depth', {}).get('name', 'unknown')}",
        f"Research steering: {meeting.get('research_steering', {}).get('prompt') or 'open'}",
        f"Meeting template: {meeting.get('meeting_template', {}).get('display_name', 'default')}",
        f"Recent episodes loaded: {len(meeting.get('memory_context', {}).get('recent_episodes', []))}",
    ]
    follow_up = meeting.get("follow_up", {})
    if follow_up.get("parent_meeting_id"):
        agenda.extend(
            [
                f"Follow-up of: {follow_up.get('parent_meeting_id')}",
                f"Follow-up note: {follow_up.get('note') or 'none'}",
            ]
        )
    agenda.extend(["", "1. Independent research"])
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
    return "\n".join(agenda) + "\n"


def render_transcript(meeting: dict[str, Any]) -> str:
    synthesis = public_synthesis(meeting["moderator_synthesis"])
    transcript_lines = [
        "# Transcript",
        "",
        "Informal lobby and side chat are excluded from this official transcript unless explicitly promoted.",
        "",
    ]
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
    transcript_lines.extend(["## Moderator Synthesis", "", synthesis["summary"], ""])
    return "\n".join(transcript_lines)


def render_decision(meeting: dict[str, Any]) -> str:
    synthesis = public_synthesis(meeting["moderator_synthesis"])
    decision = [
        "# Decision",
        "",
        f"Winner: {synthesis['winner']}",
        "",
        "## Decision Status",
        f"Status: {meeting.get('decision_status', {}).get('status', 'unknown')}",
        "Next actions:",
        *[f"- {action}" for action in meeting.get("decision_status", {}).get("next_actions", [])],
        "",
        "## Decision Gate",
        f"Status: {meeting.get('decision_gate', {}).get('status', 'unknown')}",
        f"Can finalize: {meeting.get('decision_gate', {}).get('can_finalize', False)}",
        f"Required action: {meeting.get('decision_gate', {}).get('required_action', 'unknown')}",
        "Reasons:",
        *[f"- {reason}" for reason in meeting.get("decision_gate", {}).get("reasons", [])],
        "",
        "## Ranking",
        *[f"{index + 1}. {name}" for index, name in enumerate(synthesis["ranking"])],
        "",
        "## Confidence",
        synthesis["confidence"],
        "",
        "## Caveats",
        *[f"- {caveat}" for caveat in public_caveats(synthesis)],
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
    return "\n".join(decision) + "\n"
