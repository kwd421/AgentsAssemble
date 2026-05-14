from __future__ import annotations

from typing import Any

from agentsassemble.stance_match import position_matches_winner, position_opposes_winner


def build_return_packet(meeting: dict[str, Any], role: dict[str, Any]) -> dict[str, Any]:
    role_id = role["id"]
    synthesis = meeting.get("moderator_synthesis", {})
    role_messages = _role_messages(meeting, role_id)
    research = _research_for_role(meeting, role_id)
    task = synthesis.get("tasks", {}).get(role_id, "No task assigned.")
    final_position = role_messages[-1].get("position", "") if role_messages else ""
    stance_status = role_messages[-1].get("stance_status", "unknown") if role_messages else "unknown"
    winner = synthesis.get("winner", "Undetermined")
    decision_gate = meeting.get("decision_gate", {"status": "unknown", "reasons": []})
    outcome = _role_outcome(final_position, winner, decision_gate)
    return {
        "meeting_id": meeting["meeting_id"],
        "role_id": role_id,
        "display_name": role.get("display_name", role_id),
        "lens": role.get("lens", ""),
        "delegate_packet": f"delegate_packets/{role_id}.json",
        "question": meeting.get("display_question") or meeting.get("question", ""),
        "decision": {
            "winner": winner,
            "confidence": synthesis.get("confidence", "low"),
            "outcome_for_role": outcome,
            "rationale": synthesis.get("summary", ""),
            "caveats": synthesis.get("caveats", []),
        },
        "decision_status": meeting.get("decision_status", {"status": "unknown", "next_actions": []}),
        "decision_gate": decision_gate,
        "follow_up": meeting.get("follow_up", {"parent_meeting_id": None, "note": None}),
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
        "research_status": research.get("status", "complete"),
        "evidence": {
            "gate": research.get("evidence_gate", {}),
            "supported_claims": research.get("claim_evidence", []),
            "weak_claims": research.get("weak_claims", []),
            "unsupported_claims": research.get("unsupported_claims", []),
            "verifier_rejected_claims": research.get("verifier_rejected_claims", []),
        },
        "next_task": task,
        "handoff_checklist": _handoff_checklist(meeting, research, task),
        "answer_prompts": {
            "what_happened": "Summarize the meeting from this agent's perspective.",
            "why_win_or_lose": "Explain whether this agent's stance won, lost, partially held, or remained unresolved.",
            "what_changed": "Name the evidence or objections that changed or constrained the stance.",
            "what_next": "State this agent's assigned next task and what to inspect first.",
        },
    }


def render_return_packet_markdown(packet: dict[str, Any]) -> str:
    decision = packet["decision"]
    decision_status = packet.get("decision_status", {})
    follow_up = packet.get("follow_up", {})
    stance = packet["stance"]
    evidence = packet["evidence"]
    lines = [
        f"# Return Packet: {packet['display_name']}",
        "",
        f"- Meeting: {packet['meeting_id']}",
        f"- Question: {packet['question']}",
        f"- Final decision: {decision['winner']} ({decision['confidence']})",
        f"- Decision status: {decision_status.get('status', 'unknown')}",
        f"- Outcome for this role: {decision['outcome_for_role']}",
        f"- Research status: {packet.get('research_status', 'complete')}",
        f"- Final position: {stance['final_position'] or 'Not recorded'}",
        f"- Stance status: {stance['status']}",
        f"- Next task: {packet['next_task']}",
        f"- Follow-up of: {follow_up.get('parent_meeting_id') or 'none'}",
        "",
        "## Decision Status",
        "",
        f"- Status: {decision_status.get('status', 'unknown')}",
        f"- Evidence gate: {decision_status.get('evidence_gate_status', 'unknown')}",
        "- Next actions:",
        *_markdown_items(decision_status.get("next_actions", []), indent="  "),
        "",
        "## Decision Gate",
        "",
        f"- Status: {packet.get('decision_gate', {}).get('status', 'unknown')}",
        f"- Required action: {packet.get('decision_gate', {}).get('required_action', 'unknown')}",
        "- Reasons:",
        *_markdown_items(packet.get("decision_gate", {}).get("reasons", []), indent="  "),
        "",
        "## Handoff Checklist",
        "",
        *[f"- {item}" for item in packet.get("handoff_checklist", [])],
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


def _role_outcome(position: str, winner: str, decision_gate: dict[str, Any]) -> str:
    if decision_gate.get("can_finalize") is False:
        return "unresolved"
    if not position or winner == "Undetermined":
        return "unresolved"
    if position_matches_winner(position, winner):
        return "won_or_partially_supported"
    if position_opposes_winner(position, winner):
        return "lost_or_not_selected"
    return "unresolved"


def _handoff_checklist(meeting: dict[str, Any], research: dict[str, Any], task: str) -> list[str]:
    decision_gate = meeting.get("decision_gate", {})
    checklist = [
        "Review delegate packet before claiming continuity.",
        "Review decision gate before acting.",
        "Review decision status before continuing work.",
        "Read decision.md, transcript.md, and this return packet.",
        "Check evidence gate warnings before trusting claims.",
    ]
    if decision_gate.get("can_finalize") is False:
        checklist.append("Do not start implementation until the decision gate is resolved.")
    if decision_gate.get("status") == "needs_user_decision":
        checklist.append("Wait for a user decision or enable moderator synthesis before acting.")
    if decision_gate.get("status") == "needs_more_research":
        checklist.append("Run the requested research or verifier round before acting.")
    if decision_gate.get("status") == "blocked":
        checklist.append("Rerun failed debate turns or ask the user before deciding.")
    if decision_gate.get("status") == "invalid":
        checklist.append("Rerun moderator synthesis or request user review before acting.")
    if decision_gate.get("status") == "no_consensus":
        checklist.append("Add another round or ask the user to decide before assigning implementation.")
    if decision_gate.get("status") == "no_official_decision":
        checklist.append("Do not treat room-log.md as an official decision.")
    if meeting.get("follow_up", {}).get("parent_meeting_id"):
        checklist.append("Compare this follow-up with its parent meeting before acting.")
    if research.get("status") == "failed":
        checklist.append("Redo failed research before making implementation decisions.")
    if task and task != "No task assigned.":
        checklist.append("Confirm assigned task scope before editing files.")
    return checklist


def _markdown_items(items: list[str], indent: str = "") -> list[str]:
    if not items:
        return [f"{indent}- None recorded."]
    return [f"{indent}- {item}" for item in items]
