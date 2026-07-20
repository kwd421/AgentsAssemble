"""Retained resident engagement-mode mutation and operation audit."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from agentsassemble.legacy.live_agent.runtime.operations import append_live_agent_operation
from agentsassemble.live_agents import (
    read_live_agents,
    update_live_agent_engagement,
)


@dataclass(frozen=True)
class LegacyLiveAgentEngagementService:
    output_root: Path

    def update(self, agent_id: str, payload: dict[str, object]) -> dict[str, object]:
        previous_mode = _agent_engagement(self.output_root, agent_id)
        try:
            result = update_live_agent_engagement_payload(self.output_root, agent_id, payload)
        except ValueError as error:
            self._record(
                status="failed",
                agent_id=agent_id,
                error=str(error),
                details={"engagement_mode": str(payload.get("engagement_mode") or "")},
            )
            raise
        agent = result.get("agent") if isinstance(result.get("agent"), dict) else {}
        self._record(
            status="success",
            agent_id=agent_id,
            summary="updated engagement mode",
            details={
                "previous_engagement_mode": previous_mode,
                "engagement_mode": str(
                    agent.get("engagement_mode")
                    or payload.get("engagement_mode")
                    or ""
                ),
            },
        )
        return result

    def record_invalid_json(self, agent_id: str) -> None:
        self._record(
            status="failed",
            agent_id=agent_id,
            error="Invalid JSON",
            details={},
        )

    def _record(
        self,
        *,
        status: str,
        agent_id: str,
        summary: str = "",
        error: str = "",
        details: dict[str, object],
    ) -> None:
        append_live_agent_operation(
            self.output_root,
            operation="engagement.update",
            status=status,
            target_id=agent_id,
            summary=summary,
            error=error,
            details=details,
        )


def update_live_agent_engagement_payload(
    output_root: Path,
    agent_id: str,
    payload: dict[str, object],
) -> dict[str, object]:
    agent = update_live_agent_engagement(
        output_root,
        agent_id,
        str(payload.get("engagement_mode") or ""),
    )
    return {"agent": agent, "agents": read_live_agents(output_root)}


def _agent_engagement(output_root: Path, agent_id: str) -> str:
    for agent in read_live_agents(output_root):
        if str(agent.get("agent_id") or "") == agent_id:
            return str(agent.get("engagement_mode") or "")
    return ""
