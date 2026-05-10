from __future__ import annotations

from typing import Any


PUBLIC_MESSAGE_KEYS = (
    "role_id",
    "display_name",
    "round",
    "content",
    "position",
    "stance_status",
    "stance_delta",
    "changed_by",
    "change_reason",
    "remaining_resistance",
    "emotion",
    "change_conditions",
    "confidence",
    "bridge",
)


def build_decision_context(
    research_records: list[dict[str, Any]],
    debate_rounds: list[dict[str, Any]],
    evidence_gate: dict[str, Any],
) -> dict[str, Any]:
    return {
        "research_summaries": [_decision_research_summary(research) for research in research_records],
        "rounds": {
            round_record["id"]: [_public_message(message) for message in round_record.get("messages", [])]
            for round_record in debate_rounds
        },
        "evidence_gate": evidence_gate,
        "moderator_rule": (
            "Base the decision on supported claim_evidence. Unsupported, weak, verifier-rejected, "
            "irrelevant, or contradictory claims may be listed as caveats but must not determine the winner."
        ),
        "stance_rule": (
            "Treat held, revised, and conceded stances as debate state. "
            "Do not collapse disagreement into fake consensus."
        ),
    }


def public_synthesis(synthesis: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in synthesis.items()
        if key not in {"codex", "diagnostics"}
    }


def public_debate_rounds(debate_rounds: list[dict[str, Any]]) -> list[dict[str, Any]]:
    public_rounds = []
    for round_record in debate_rounds:
        public_rounds.append(
            {
                "id": round_record.get("id"),
                "title": round_record.get("title"),
                "context_scope": round_record.get("context_scope"),
                "instruction": round_record.get("instruction"),
                "messages": [_public_message(message) for message in round_record.get("messages", [])],
            }
        )
    return public_rounds


def build_diagnostics(debate_rounds: list[dict[str, Any]], synthesis: dict[str, Any]) -> dict[str, Any]:
    adapter_calls = []
    for round_record in debate_rounds:
        for message in round_record.get("messages", []):
            metadata = message.get("codex")
            if metadata:
                adapter_calls.append(
                    {
                        "role_id": message.get("role_id"),
                        "round": message.get("round") or round_record.get("id"),
                        "adapter": "codex",
                        **_public_adapter_metadata(metadata),
                    }
                )
    if synthesis.get("codex"):
        adapter_calls.append(
            {
                "role_id": "moderator",
                "round": "synthesis",
                "adapter": "codex",
                **_public_adapter_metadata(synthesis.get("codex")),
            }
        )
    return {
        "meeting_status": "degraded" if synthesis.get("fallback") or synthesis.get("status") == "degraded" else "ok",
        "fallback": synthesis.get("fallback"),
        "synthesis": synthesis.get("diagnostics", {}),
        "adapter_calls": adapter_calls,
    }


def public_caveats(synthesis: dict[str, Any]) -> list[str]:
    caveats = []
    for caveat in synthesis.get("caveats", []):
        if _is_internal_diagnostic_text(str(caveat)):
            continue
        caveats.append(str(caveat))
    return caveats


def _decision_research_summary(research: dict[str, Any]) -> dict[str, Any]:
    return {
        "role_id": research.get("role_id"),
        "display_name": research.get("display_name"),
        "summary": research.get("summary", ""),
        "confidence": research.get("confidence", "low"),
        "supported_claims": _limit_items(research.get("claim_evidence", []), 5),
        "weak_claims": _limit_items(research.get("weak_claims", []), 3),
        "unsupported_claims": _limit_items(research.get("unsupported_claims", []), 3),
        "verifier_rejected_claims": _limit_items(research.get("verifier_rejected_claims", []), 3),
        "counterclaims": _limit_items(research.get("counterclaims", []), 3),
        "coverage_gaps": _limit_items(research.get("coverage_gaps", []), 3),
        "evidence_gate": research.get("evidence_gate", {}),
    }


def _public_message(message: dict[str, Any]) -> dict[str, Any]:
    return {key: message.get(key) for key in PUBLIC_MESSAGE_KEYS if key in message}


def _limit_items(items: Any, limit: int) -> list[Any]:
    if not isinstance(items, list):
        return []
    return items[:limit]


def _public_adapter_metadata(metadata: Any) -> dict[str, Any]:
    if not isinstance(metadata, dict):
        return {"returncode": None, "timed_out": False}
    return {
        "returncode": metadata.get("returncode"),
        "timed_out": bool(metadata.get("timed_out")),
        "timeout_seconds": metadata.get("timeout_seconds"),
    }


def _is_internal_diagnostic_text(text: str) -> bool:
    normalized = text.casefold()
    markers = (
        "parseable JSON",
        "moderator JSON",
        "fallback synthesis",
        "fallback winner",
        "Input exceeds",
        "turn/start failed",
    )
    return any(marker.casefold() in normalized for marker in markers)
