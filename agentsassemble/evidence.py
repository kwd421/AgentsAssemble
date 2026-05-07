from __future__ import annotations

from typing import Any

from agentsassemble.models import ResearchDepth


CONFIDENCE_ORDER = {"low": 0, "medium": 1, "high": 2}


def apply_evidence_gate(research: dict[str, Any], depth: ResearchDepth) -> dict[str, Any]:
    sources = research.get("sources", [])
    source_urls = {source.get("url") for source in sources if source.get("url")}
    supported_claims = []
    unsupported_claims = []

    for claim in research.get("claim_evidence", []):
        evidence = [url for url in claim.get("evidence", []) if url]
        missing_urls = [url for url in evidence if url not in source_urls]
        if evidence and not missing_urls:
            supported_claims.append(claim)
        else:
            unsupported_claims.append(
                {
                    "claim": claim.get("claim", ""),
                    "reason": _unsupported_reason(evidence, missing_urls),
                    "evidence": evidence,
                    "missing_urls": missing_urls,
                    "original": claim,
                }
            )

    failures = []
    if len(sources) < depth.min_sources:
        failures.append(f"source_count_below_minimum:{len(sources)}/{depth.min_sources}")
    if len(supported_claims) < depth.min_claims:
        failures.append(f"supported_claims_below_minimum:{len(supported_claims)}/{depth.min_claims}")
    if len(research.get("counterclaims", [])) < depth.min_counterclaims:
        failures.append(
            f"counterclaims_below_minimum:{len(research.get('counterclaims', []))}/{depth.min_counterclaims}"
        )

    requested_confidence = research.get("confidence", "low")
    gated_confidence = _gated_confidence(requested_confidence, failures, unsupported_claims)

    research["claim_evidence"] = supported_claims
    research["unsupported_claims"] = unsupported_claims
    research["evidence_gate"] = {
        "status": "pass" if not failures and not unsupported_claims else "warn",
        "supported_claim_count": len(supported_claims),
        "unsupported_claim_count": len(unsupported_claims),
        "source_count": len(sources),
        "failures": failures,
        "confidence_before": requested_confidence,
        "confidence_after": gated_confidence,
    }
    research["confidence"] = gated_confidence
    return research


def summarize_evidence_gates(research_records: list[dict[str, Any]]) -> dict[str, Any]:
    role_summaries = []
    total_supported = 0
    total_unsupported = 0
    failures = []
    for research in research_records:
        gate = research.get("evidence_gate", {})
        total_supported += int(gate.get("supported_claim_count", 0))
        total_unsupported += int(gate.get("unsupported_claim_count", 0))
        role_failures = list(gate.get("failures", []))
        failures.extend([f"{research.get('role_id')}:{failure}" for failure in role_failures])
        role_summaries.append(
            {
                "role_id": research.get("role_id"),
                "display_name": research.get("display_name"),
                "status": gate.get("status", "unknown"),
                "source_count": gate.get("source_count", 0),
                "supported_claim_count": gate.get("supported_claim_count", 0),
                "unsupported_claim_count": gate.get("unsupported_claim_count", 0),
                "confidence_after": gate.get("confidence_after", research.get("confidence", "low")),
                "failures": role_failures,
            }
        )
    return {
        "status": "pass" if not failures and total_unsupported == 0 else "warn",
        "total_supported_claims": total_supported,
        "total_unsupported_claims": total_unsupported,
        "failures": failures,
        "roles": role_summaries,
    }


def _unsupported_reason(evidence: list[str], missing_urls: list[str]) -> str:
    if not evidence:
        return "claim has no evidence URLs"
    return "claim references URLs that are not present in sources: " + ", ".join(missing_urls)


def _gated_confidence(requested: str, failures: list[str], unsupported_claims: list[dict[str, Any]]) -> str:
    if failures:
        max_confidence = "low"
    elif unsupported_claims:
        max_confidence = "medium"
    else:
        max_confidence = requested if requested in CONFIDENCE_ORDER else "low"
    if CONFIDENCE_ORDER.get(requested, 0) <= CONFIDENCE_ORDER[max_confidence]:
        return requested if requested in CONFIDENCE_ORDER else "low"
    return max_confidence
