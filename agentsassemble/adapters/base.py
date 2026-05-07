from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from agentsassemble.models import Role


class ProviderAdapter(ABC):
    name: str

    @abstractmethod
    def start_session(self, role: Role, meeting_context: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def run_research(
        self,
        role: Role,
        session: dict[str, Any],
        question: str,
    ) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def run_round(
        self,
        role: Role,
        session: dict[str, Any],
        round_name: str,
        prompt: str,
        public_context: dict[str, Any],
    ) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def synthesize(
        self,
        session: dict[str, Any],
        question: str,
        public_context: dict[str, Any],
    ) -> dict[str, Any]:
        raise NotImplementedError
