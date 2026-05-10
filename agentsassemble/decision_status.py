from __future__ import annotations

from typing import Any


def derive_decision_status(synthesis: dict[str, Any], evidence_gate: dict[str, Any]) -> dict[str, Any]:
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


def _dedupe(items: list[str]) -> list[str]:
    deduped = []
    for item in items:
        if item not in deduped:
            deduped.append(item)
    return deduped
