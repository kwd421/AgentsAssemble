from __future__ import annotations

from typing import Any

from agentsassemble.providers.adapters.base import ProviderAdapter
from agentsassemble.models import ResearchDepth, ResearchSteering, Role


class UnsupportedProviderAdapter(ProviderAdapter):
    def __init__(self, name: str, reason: str) -> None:
        self.name = name
        self.reason = reason

    def start_session(self, role: Role, meeting_context: dict[str, Any]) -> dict[str, Any]:
        return {
            "adapter": self.name,
            "role_id": role.id,
            "session_id": None,
            "status": "unsupported",
            "meeting_id": meeting_context.get("meeting_id"),
            "reason": self.reason,
        }

    def run_research(
        self,
        role: Role,
        session: dict[str, Any],
        question: str,
        depth: ResearchDepth,
        steering: ResearchSteering,
    ) -> dict[str, Any]:
        raise NotImplementedError(self._message("research", role.id))

    def run_round(
        self,
        role: Role,
        session: dict[str, Any],
        round_name: str,
        prompt: str,
        public_context: dict[str, Any],
    ) -> dict[str, Any]:
        raise NotImplementedError(self._message(f"round {round_name}", role.id))

    def synthesize(
        self,
        session: dict[str, Any],
        question: str,
        public_context: dict[str, Any],
    ) -> dict[str, Any]:
        raise NotImplementedError(self._message("synthesis", str(session.get("role_id", "moderator"))))

    def _message(self, step: str, role_id: str) -> str:
        return f"Provider adapter {self.name!r} cannot run {step} for {role_id}: {self.reason}"
