from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Mapping


@dataclass(frozen=True)
class AgentFloorEligibility:
    eligible: bool
    reason_code: str


def evaluate_agent_floor_eligibility(
    participant: Mapping[str, object],
    session: Mapping[str, object],
    *,
    member_muted: bool,
    bridge_connected: bool,
) -> AgentFloorEligibility:
    if not participant or participant.get("status") != "joined":
        return AgentFloorEligibility(False, "participant_not_joined")
    if participant.get("muted") or member_muted:
        return AgentFloorEligibility(False, "participant_muted")
    if not session:
        return AgentFloorEligibility(False, "session_missing")
    if session.get("status") != "attached":
        return AgentFloorEligibility(False, "session_not_attached")
    if not session.get("enabled"):
        return AgentFloorEligibility(False, "session_disabled")
    if session.get("runtime_status") != "idle":
        return AgentFloorEligibility(False, f"runtime_{session.get('runtime_status') or 'unknown'}")
    if not bridge_connected:
        return AgentFloorEligibility(False, "bridge_disconnected")
    return AgentFloorEligibility(True, "eligible")


def continuous_floor_targets(
    *,
    provider_ids: Iterable[str],
    actor_id: str,
    routed_targets: Iterable[str],
    eligible_agent_ids: Iterable[str],
    content: str,
) -> tuple[str, ...]:
    providers = tuple(provider_ids)
    targets = tuple(routed_targets)
    normalized_content = str(content or "").casefold()
    explicitly_routed = "@all" in normalized_content or any(
        f"@{agent_id.casefold()}" in normalized_content for agent_id in providers
    )
    if explicitly_routed:
        return targets

    eligible = set(eligible_agent_ids)
    targets = tuple(agent_id for agent_id in targets if agent_id in eligible)
    if not targets:
        return ()

    ordered = sorted(providers)
    if actor_id in ordered:
        start = (ordered.index(actor_id) + 1) % len(ordered)
        candidates = [ordered[(start + offset) % len(ordered)] for offset in range(len(ordered))]
    else:
        candidates = ordered
    eligible_candidates = [candidate for candidate in candidates if candidate in targets]
    if not eligible_candidates:
        raise RuntimeError("continuous_floor_invariant_violation")
    return (eligible_candidates[0],)
