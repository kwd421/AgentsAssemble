from __future__ import annotations

from typing import Any

from agentsassemble.stance_match import position_matches_winner, position_opposes_winner


FINAL_STATUSES = {"decided", "split_decision"}


def derive_decision_gate(
    synthesis: dict[str, Any],
    evidence_gate: dict[str, Any],
    research_records: list[dict[str, Any]],
    debate_rounds: list[dict[str, Any]],
) -> dict[str, Any]:
    reasons: list[str] = []
    winner = str(synthesis.get("winner") or "").strip()
    confidence = str(synthesis.get("confidence") or "low").strip().lower()
    caveats = [str(caveat) for caveat in synthesis.get("caveats", []) if str(caveat).strip()]
    positions = _latest_positions(debate_rounds)
    minority_positions = _minority_positions(winner, positions)
    ambiguous_positions = _ambiguous_positions(winner, positions)

    if synthesis.get("fallback") or synthesis.get("status") == "degraded":
        reasons.append("moderator_fallback")
        return _gate("invalid", False, "rerun_moderator_or_user_review", reasons, minority_positions, ambiguous_positions)

    reasons.extend(_research_reasons(research_records))
    reasons.extend(_evidence_reasons(evidence_gate))
    reasons.extend(_debate_reasons(debate_rounds))
    if not winner or winner.casefold() == "undetermined":
        reasons.append("winner_undetermined")
    if any(reason.startswith("debate_failed") for reason in reasons):
        return _gate("blocked", False, "rerun_failed_debate_round", reasons, minority_positions, ambiguous_positions)
    if any(reason.startswith(("research_failed", "retry_failed", "evidence_gate", "unsupported", "weak", "verifier_rejected")) for reason in reasons):
        return _gate("needs_more_research", False, "run_research_or_verifier_round", reasons, minority_positions, ambiguous_positions)

    if not winner or winner.casefold() == "undetermined":
        return _gate("no_consensus", False, "add_round_or_user_decision", reasons, minority_positions, ambiguous_positions)

    if confidence == "low":
        reasons.append("low_confidence")
        return _gate("blocked", False, "user_decision_or_add_round", reasons, minority_positions, ambiguous_positions)

    if caveats or minority_positions or ambiguous_positions:
        if caveats:
            reasons.append("open_caveats")
        if minority_positions:
            reasons.append("minority_positions_present")
        if ambiguous_positions:
            reasons.append("ambiguous_positions_present")
        return _gate("split_decision", True, "record_split_decision", reasons, minority_positions, ambiguous_positions)

    return _gate("decided", True, "write_decision", reasons, minority_positions, ambiguous_positions)


def _gate(
    status: str,
    can_finalize: bool,
    required_action: str,
    reasons: list[str],
    minority_positions: list[dict[str, str]],
    ambiguous_positions: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    return {
        "status": status,
        "can_finalize": can_finalize,
        "required_action": required_action,
        "reasons": _dedupe(reasons),
        "minority_positions": minority_positions,
        "ambiguous_positions": ambiguous_positions or [],
        "final_state": status in FINAL_STATUSES,
    }


def _research_reasons(research_records: list[dict[str, Any]]) -> list[str]:
    reasons = []
    for record in research_records:
        role_id = str(record.get("role_id") or "unknown")
        retry = record.get("retry") if isinstance(record.get("retry"), dict) else {}
        if record.get("status") == "failed":
            reasons.append(f"research_failed:{role_id}")
        if retry.get("status") == "failed":
            reasons.append(f"retry_failed:{role_id}")
    return reasons


def _evidence_reasons(evidence_gate: dict[str, Any]) -> list[str]:
    reasons = []
    status = str(evidence_gate.get("status") or "unknown").lower()
    if status != "pass":
        reasons.append(f"evidence_gate:{status}")
    if int(evidence_gate.get("total_unsupported_claims") or 0) > 0:
        reasons.append("unsupported_claims_present")
    if int(evidence_gate.get("total_weak_claims") or 0) > 0:
        reasons.append("weak_claims_present")
    if int(evidence_gate.get("total_verifier_rejected_claims") or 0) > 0:
        reasons.append("verifier_rejected_claims_present")
    return reasons


def _debate_reasons(debate_rounds: list[dict[str, Any]]) -> list[str]:
    reasons = []
    for round_record in debate_rounds:
        round_id = str(round_record.get("id") or round_record.get("round") or "unknown")
        for message in round_record.get("messages", []):
            if not isinstance(message, dict):
                continue
            if message.get("status") == "failed" or message.get("stance_status") == "blocked":
                role_id = str(message.get("role_id") or "unknown")
                message_round = str(message.get("round") or round_id)
                reasons.append(f"debate_failed:{role_id}:{message_round}")
    return reasons


def _latest_positions(debate_rounds: list[dict[str, Any]]) -> dict[str, str]:
    positions: dict[str, str] = {}
    for round_record in debate_rounds:
        for message in round_record.get("messages", []):
            role_id = str(message.get("role_id") or "")
            position = str(message.get("position") or "").strip()
            if role_id and position:
                positions[role_id] = position
    return positions


def _minority_positions(winner: str, positions: dict[str, str]) -> list[dict[str, str]]:
    if not winner:
        return [{"role_id": role_id, "position": position} for role_id, position in positions.items()]
    return [
        {"role_id": role_id, "position": position}
        for role_id, position in positions.items()
        if position_opposes_winner(position, winner)
    ]


def _ambiguous_positions(winner: str, positions: dict[str, str]) -> list[dict[str, str]]:
    if not winner:
        return []
    return [
        {"role_id": role_id, "position": position}
        for role_id, position in positions.items()
        if not position_matches_winner(position, winner) and not position_opposes_winner(position, winner)
    ]


def _dedupe(items: list[str]) -> list[str]:
    result = []
    for item in items:
        if item not in result:
            result.append(item)
    return result
