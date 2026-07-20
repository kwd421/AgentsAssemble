from __future__ import annotations

from typing import Any

from agentsassemble.models import ResearchDepth


CONFIDENCE_ORDER = {"low": 0, "medium": 1, "high": 2}
SUPPORTED_VERDICTS = {"supports", "support", "supported"}
WEAK_VERDICTS = {"weak", "partial", "partially_supports", "partial_support"}
REJECTED_VERDICTS = {"contradicts", "contradict", "irrelevant", "unrelated", "refutes", "refuted"}


def apply_evidence_gate(research: dict[str, Any], depth: ResearchDepth) -> dict[str, Any]:
    sources = research.get("sources", [])
    source_by_url = {source.get("url"): source for source in sources if source.get("url")}
    source_urls = set(source_by_url)
    supported_claims = []
    unsupported_claims = []
    weak_claims = []
    verifier_rejected_claims = []
    claim_verification = []

    for claim in research.get("claim_evidence", []):
        evidence = [url for url in claim.get("evidence", []) if url]
        missing_urls = [url for url in evidence if url not in source_urls]
        if evidence and not missing_urls:
            verification_records = [
                _verify_claim_source(claim, source_by_url[url], url) for url in evidence
            ]
            claim_verification.extend(verification_records)
            verdicts = {record["verdict"] for record in verification_records}
            if verdicts & REJECTED_VERDICTS:
                verifier_rejected_claims.append(
                    {
                        "claim": claim.get("claim", ""),
                        "reason": _verifier_rejected_reason(verification_records),
                        "evidence": evidence,
                        "verifications": verification_records,
                        "original": claim,
                    }
                )
            elif "supports" in verdicts:
                supported_claims.append(claim)
            else:
                weak_claims.append(
                    {
                        "claim": claim.get("claim", ""),
                        "reason": _weak_reason(verification_records),
                        "evidence": evidence,
                        "verifications": verification_records,
                        "original": claim,
                    }
                )
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
    gated_confidence = _gated_confidence(
        requested_confidence,
        failures,
        unsupported_claims,
        weak_claims,
        verifier_rejected_claims,
    )

    research["claim_evidence"] = supported_claims
    research["unsupported_claims"] = unsupported_claims
    research["weak_claims"] = weak_claims
    research["verifier_rejected_claims"] = verifier_rejected_claims
    research["claim_verification"] = claim_verification
    research["evidence_gate"] = {
        "status": "pass"
        if not failures and not unsupported_claims and not weak_claims and not verifier_rejected_claims
        else "warn",
        "supported_claim_count": len(supported_claims),
        "unsupported_claim_count": len(unsupported_claims),
        "weak_claim_count": len(weak_claims),
        "verifier_rejected_claim_count": len(verifier_rejected_claims),
        "claim_verification_count": len(claim_verification),
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
    total_weak = 0
    total_verifier_rejected = 0
    failures = []
    for research in research_records:
        gate = research.get("evidence_gate", {})
        total_supported += int(gate.get("supported_claim_count", 0))
        total_unsupported += int(gate.get("unsupported_claim_count", 0))
        total_weak += int(gate.get("weak_claim_count", 0))
        total_verifier_rejected += int(gate.get("verifier_rejected_claim_count", 0))
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
                "weak_claim_count": gate.get("weak_claim_count", 0),
                "verifier_rejected_claim_count": gate.get("verifier_rejected_claim_count", 0),
                "confidence_after": gate.get("confidence_after", research.get("confidence", "low")),
                "failures": role_failures,
            }
        )
    return {
        "status": "pass"
        if not failures and total_unsupported == 0 and total_weak == 0 and total_verifier_rejected == 0
        else "warn",
        "total_supported_claims": total_supported,
        "total_unsupported_claims": total_unsupported,
        "total_weak_claims": total_weak,
        "total_verifier_rejected_claims": total_verifier_rejected,
        "failures": failures,
        "roles": role_summaries,
    }


def _unsupported_reason(evidence: list[str], missing_urls: list[str]) -> str:
    if not evidence:
        return "claim has no evidence URLs"
    return "claim references URLs that are not present in sources: " + ", ".join(missing_urls)


def _verify_claim_source(claim: dict[str, Any], source: dict[str, Any], url: str) -> dict[str, Any]:
    explicit_verdict = _explicit_verdict(claim, url)
    verdict = _normalize_verdict(explicit_verdict) if explicit_verdict else "supports"
    return {
        "claim": claim.get("claim", ""),
        "url": url,
        "verdict": verdict,
        "reason": _verification_reason(claim, source, explicit_verdict),
        "source_quality": claim.get("source_quality") or source.get("quality", "unknown"),
        "source_type": source.get("source_type", "unknown"),
    }


def _explicit_verdict(claim: dict[str, Any], url: str) -> str | None:
    for key in ("verification", "verdict", "evidence_relation", "support", "relationship"):
        value = claim.get(key)
        if isinstance(value, str) and value.strip():
            return value
        if isinstance(value, dict):
            url_value = value.get(url)
            if isinstance(url_value, str) and url_value.strip():
                return url_value
            verdict_value = value.get("verdict") or value.get("relation")
            if isinstance(verdict_value, str) and verdict_value.strip():
                return verdict_value
    relations = claim.get("evidence_relations")
    if isinstance(relations, dict):
        value = relations.get(url)
        if isinstance(value, str) and value.strip():
            return value
    if isinstance(relations, list):
        for relation in relations:
            if not isinstance(relation, dict):
                continue
            if relation.get("url") != url:
                continue
            value = relation.get("verdict") or relation.get("relation")
            if isinstance(value, str) and value.strip():
                return value
    return None


def _normalize_verdict(value: str | None) -> str:
    normalized = (value or "").strip().lower().replace("-", "_").replace(" ", "_")
    if normalized in SUPPORTED_VERDICTS:
        return "supports"
    if normalized in WEAK_VERDICTS:
        return "weak"
    if normalized in REJECTED_VERDICTS:
        if normalized in {"contradict", "refutes", "refuted"}:
            return "contradicts"
        if normalized in {"unrelated"}:
            return "irrelevant"
        return normalized
    return "weak"


def _verification_reason(claim: dict[str, Any], source: dict[str, Any], explicit_verdict: str | None) -> str:
    if explicit_verdict:
        reason = claim.get("verification_reason") or claim.get("relation_reason")
        if isinstance(reason, str) and reason.strip():
            return reason
        return f"explicit relation: {explicit_verdict}"
    return "no explicit relation provided; accepted because the cited URL exists in sources"


def _weak_reason(verifications: list[dict[str, Any]]) -> str:
    verdicts = sorted({record["verdict"] for record in verifications})
    return "claim only has weak explicit support: " + ", ".join(verdicts)


def _verifier_rejected_reason(verifications: list[dict[str, Any]]) -> str:
    rejected = [record["verdict"] for record in verifications if record["verdict"] in REJECTED_VERDICTS]
    return "claim cites sources marked as " + ", ".join(sorted(set(rejected)))


def _gated_confidence(
    requested: str,
    failures: list[str],
    unsupported_claims: list[dict[str, Any]],
    weak_claims: list[dict[str, Any]],
    verifier_rejected_claims: list[dict[str, Any]],
) -> str:
    if failures:
        max_confidence = "low"
    elif verifier_rejected_claims:
        max_confidence = "low"
    elif weak_claims:
        max_confidence = "medium"
    elif unsupported_claims:
        max_confidence = "medium"
    else:
        max_confidence = requested if requested in CONFIDENCE_ORDER else "low"
    if CONFIDENCE_ORDER.get(requested, 0) <= CONFIDENCE_ORDER[max_confidence]:
        return requested if requested in CONFIDENCE_ORDER else "low"
    return max_confidence
