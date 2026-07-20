"""Local provider-login command execution and operation auditing."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from agentsassemble.live_agent_frontend_create import frontend_live_agent_login_payload
from agentsassemble.legacy.live_agent.runtime.operations import append_live_agent_operation
from agentsassemble.legacy.meeting.core.events import clean_lobby_text


ProviderLoginLauncher = Callable[[list[str]], object]
ProviderLoginResolver = Callable[[str], str | None]


@dataclass(frozen=True)
class ProviderLoginService:
    """Launch one allowlisted provider login command from the local GUI."""

    output_root: Path
    command_launcher: ProviderLoginLauncher | None = None
    command_resolver: ProviderLoginResolver | None = None

    def start(self, payload: dict[str, object]) -> dict[str, object]:
        provider_id = clean_lobby_text(payload.get("provider_id"), limit=64)
        try:
            result = frontend_live_agent_login_payload(
                payload,
                command_launcher=self.command_launcher,
                command_resolver=self.command_resolver,
            )
        except (OSError, ValueError) as error:
            self._record(
                status="failed",
                target_id=provider_id,
                error=str(error),
            )
            raise
        self._record(
            status="success",
            target_id=provider_id,
            summary="started provider login from frontend agent modal",
            details={"provider_id": provider_id},
        )
        return result

    def record_invalid_json(self) -> None:
        self._record(status="failed", target_id="", error="Invalid JSON")

    def _record(
        self,
        *,
        status: str,
        target_id: str,
        summary: str = "",
        error: str = "",
        details: dict[str, object] | None = None,
    ) -> None:
        append_live_agent_operation(
            self.output_root,
            operation="frontend_agent.login",
            status=status,
            target_id=target_id,
            summary=summary,
            error=error,
            details=details or {},
        )
