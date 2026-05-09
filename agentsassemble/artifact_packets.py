from __future__ import annotations

from typing import Any


def build_return_packet(meeting: dict[str, Any], role: dict[str, Any]) -> dict[str, Any]:
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


def render_return_packet_markdown(packet: dict[str, Any]) -> str:
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
