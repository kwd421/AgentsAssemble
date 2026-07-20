"""Retained resident-agent registration, heartbeat, and leave behavior."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from agentsassemble.legacy.live_agent.presence_projection import (
    leave_operation_details,
    registration_operation_details,
)
from agentsassemble.legacy.live_agent.queries import require_live_agent
from agentsassemble.legacy.live_agent.runtime.operations import append_live_agent_operation
from agentsassemble.legacy.live_agent.state import connect_live_agent, heartbeat_live_agent, read_live_agents
from agentsassemble.legacy.meeting.core.events import clean_lobby_text


@dataclass(frozen=True)
class LegacyLiveAgentPresenceService:
    """Own retained presence state changes and their existing operation audits."""

    output_root: Path

    def register(self, payload: dict[str, object]) -> dict[str, object]:
        agent_id = clean_lobby_text(payload.get("agent_id"), limit=64)
        previous_agent = _agent_from_roster(self.output_root, agent_id)
        try:
            result = connect_live_agent_payload(self.output_root, payload)
        except ValueError as error:
            self._record(
                operation="live_agent.register",
                status="failed",
                target_id=agent_id,
                error=str(error),
                details={"agent_id": agent_id},
            )
            raise
        agent = result.get("agent") if isinstance(result.get("agent"), dict) else {}
        registered_agent_id = str(agent.get("agent_id") or agent_id)
        self._record(
            operation="live_agent.register",
            status="success",
            target_id=registered_agent_id,
            summary="registered live agent",
            details=registration_operation_details(
                self.output_root,
                agent,
                agent_id=agent_id,
                previous_agent=previous_agent,
            ),
        )
        return result

    def heartbeat(self, agent_id: str, payload: dict[str, object]) -> dict[str, object]:
        return live_agent_heartbeat_payload(self.output_root, agent_id, payload)

    def leave(self, agent_id: str, payload: dict[str, object]) -> dict[str, object]:
        clean_agent_id = clean_lobby_text(agent_id, limit=64)
        try:
            previous_agent = require_live_agent(self.output_root, clean_agent_id)
            result = live_agent_leave_payload(self.output_root, clean_agent_id, payload)
        except ValueError as error:
            self._record(
                operation="live_agent.leave",
                status="failed",
                target_id=clean_agent_id,
                error=str(error),
                details={"agent_id": clean_agent_id},
            )
            raise
        agent = result.get("agent") if isinstance(result.get("agent"), dict) else {}
        self._record(
            operation="live_agent.leave",
            status="success",
            target_id=clean_agent_id,
            summary="marked live agent offline",
            details=leave_operation_details(
                agent,
                agent_id=clean_agent_id,
                previous_agent=previous_agent,
            ),
        )
        return result

    def record_invalid_json(self, operation: str, *, agent_id: str = "") -> None:
        clean_agent_id = clean_lobby_text(agent_id, limit=64)
        self._record(
            operation=operation,
            status="failed",
            target_id=clean_agent_id,
            error="Invalid JSON",
            details={"agent_id": clean_agent_id} if operation == "live_agent.leave" else {},
        )

    def _record(
        self,
        *,
        operation: str,
        status: str,
        target_id: str,
        summary: str = "",
        error: str = "",
        details: dict[str, object],
    ) -> None:
        append_live_agent_operation(
            self.output_root,
            operation=operation,
            status=status,
            target_id=target_id,
            summary=summary,
            error=error,
            details=details,
        )


def connect_live_agent_payload(output_root: Path, payload: dict[str, object]) -> dict[str, object]:
    return {
        "agent": connect_live_agent(output_root, payload),
        "agents": read_live_agents(output_root),
    }


def live_agent_heartbeat_payload(
    output_root: Path,
    agent_id: str,
    payload: dict[str, object],
) -> dict[str, object]:
    agent = heartbeat_live_agent(
        output_root,
        agent_id,
        status=str(payload.get("status") or "online"),
        metadata=payload,
    )
    return {"agent": agent, "agents": read_live_agents(output_root)}


def live_agent_leave_payload(
    output_root: Path,
    agent_id: str,
    payload: dict[str, object],
) -> dict[str, object]:
    require_live_agent(output_root, agent_id)
    metadata: dict[str, object] = {"last_error": ""}
    for key in (
        "last_observed_event_id",
        "last_observed_live_event_id",
        "last_observed_dm_event_id",
    ):
        if key in payload:
            metadata[key] = payload.get(key)
    agent = heartbeat_live_agent(
        output_root,
        agent_id,
        status="offline",
        metadata=metadata,
    )
    return {"agent": agent, "agents": read_live_agents(output_root)}


def _agent_from_roster(output_root: Path, agent_id: str) -> dict[str, object]:
    return next(
        (
            agent
            for agent in read_live_agents(output_root)
            if agent.get("agent_id") == agent_id
        ),
        {},
    )
