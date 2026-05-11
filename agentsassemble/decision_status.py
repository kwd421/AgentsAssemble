from __future__ import annotations

from typing import Any


def derive_decision_status(
    synthesis: dict[str, Any],
    evidence_gate: dict[str, Any],
    decision_gate: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if decision_gate:
        return _status_from_gate(synthesis, evidence_gate, decision_gate)
    winner = str(synthesis.get("winner") or "").strip()
    confidence = str(synthesis.get("confidence") or "low").strip().lower()
    caveats = [str(caveat) for caveat in synthesis.get("caveats", []) if str(caveat).strip()]
    gate_status = str(evidence_gate.get("status") or "unknown").strip().lower()
    next_actions = []

    if not winner or winner.casefold() == "undetermined":
        status = "no_consensus"
        next_actions.append("Ask the user to choose, add another round, or assign follow-up research.")
    elif confidence == "low" or caveats:
        status = "partial"
        next_actions.append("Run another round or request a user decision.")
    else:
        status = "resolved"

    if gate_status not in {"pass", "unknown"}:
        if status == "resolved":
            status = "partial"
            next_actions.append("Run another round or request a user decision.")
        next_actions.append(f"Evidence Gate is {gate_status}; review weak or unsupported claims.")

    return {
        "status": status,
        "winner": winner or "Undetermined",
        "confidence": confidence,
        "evidence_gate_status": gate_status,
        "caveat_count": len(caveats),
        "next_actions": _dedupe(next_actions),
    }


def _status_from_gate(
    synthesis: dict[str, Any],
    evidence_gate: dict[str, Any],
    decision_gate: dict[str, Any],
) -> dict[str, Any]:
    gate_status = str(decision_gate.get("status") or "unknown")
    winner = str(synthesis.get("winner") or "").strip() or "Undetermined"
    confidence = str(synthesis.get("confidence") or "low").strip().lower()
    status = {
        "decided": "resolved",
        "split_decision": "partial",
        "no_consensus": "no_consensus",
        "needs_more_research": "partial",
        "blocked": "partial",
        "invalid": "partial",
    }.get(gate_status, "partial")
    next_actions = {
        "decided": [],
        "split_decision": ["Record split decision with minority positions before acting."],
        "no_consensus": ["Ask the user to choose, add another round, or assign follow-up research."],
        "needs_more_research": ["Run research or verifier round before deciding."],
        "blocked": [_blocked_next_action(decision_gate)],
        "invalid": ["Rerun moderator synthesis or request user review before acting."],
    }.get(gate_status, ["Review decision gate before acting."])
    return {
        "status": status,
        "winner": winner,
        "confidence": confidence,
        "evidence_gate_status": str(evidence_gate.get("status") or "unknown").strip().lower(),
        "caveat_count": len([caveat for caveat in synthesis.get("caveats", []) if str(caveat).strip()]),
        "decision_gate_status": gate_status,
        "next_actions": _dedupe(next_actions),
    }


def _dedupe(items: list[str]) -> list[str]:
    deduped = []
    for item in items:
        if item not in deduped:
            deduped.append(item)
    return deduped


def _blocked_next_action(decision_gate: dict[str, Any]) -> str:
    if decision_gate.get("required_action") == "rerun_failed_debate_round":
        return "Rerun failed debate turn before deciding."
    return "Request a user decision or add another round."
