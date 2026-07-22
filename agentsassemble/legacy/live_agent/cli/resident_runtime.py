"""Validation and non-process runtime support for legacy residents."""
from __future__ import annotations

from pathlib import Path

from agentsassemble.providers.claude_resident import claude_code_print_mode_resident_error
from agentsassemble.providers.cursor_resident import (
    cursor_generic_resident_guard_error,
    cursor_terminal_session_superseded_error,
)
from agentsassemble.live_agent_runner import (
    ResidentAgentConfig,
    SUPPORTED_RESIDENT_CONNECTION_KINDS,
    resident_connection_kind_error,
)


class ApiCatalogCommandRunner:
    """In-process command-runner adapter for the legacy API provider lane."""

    def __init__(self, config: ResidentAgentConfig, *, output_root: str = "", http_post=None) -> None:
        self.config = config
        self.output_root = str(output_root or "")
        self._http_post = http_post

    def _store(self):
        if not self.output_root:
            return None
        try:
            from agentsassemble.persistence.local.identity.registry import (
                identity_store_for_output_root,
            )

            return identity_store_for_output_root(Path(self.output_root))
        except (OSError, ValueError):
            return None

    def __call__(self, command: list[str], prompt: str, *, timeout_seconds: int) -> str:
        from agentsassemble.providers import api as room_api_provider

        try:
            return room_api_provider.run_api_call(
                self.config.provider_kind,
                self.config.model_id,
                prompt,
                store=self._store(),
                participant_id=self.config.agent_id,
                meeting_id=self.config.meeting_id,
                key_source=str(getattr(self.config, "key_source", "") or ""),
                timeout=timeout_seconds,
                http_post=self._http_post,
            )
        except room_api_provider.ApiProviderError as error:
            raise RuntimeError(f"API provider call failed [{error.category}]: {error}") from error

    def close(self) -> None:
        return None


def validate_resident_config(config: ResidentAgentConfig) -> None:
    if config.connection_kind not in SUPPORTED_RESIDENT_CONNECTION_KINDS:
        raise ValueError(resident_connection_kind_error())
    if config.connection_kind == "api_call":
        from agentsassemble.providers import catalog as provider_catalog

        if not provider_catalog.get_provider(config.provider_kind):
            raise ValueError(
                f"api_call resident requires a known catalog provider as --provider-kind; got {config.provider_kind!r}. "
                f"Known: {', '.join(provider_catalog.list_providers())}."
            )
        if not provider_catalog.get_model(config.provider_kind, config.model_id):
            raise ValueError(
                f"api_call resident requires a known --model for provider {config.provider_kind!r}; got {config.model_id!r}."
            )
        return
    live_session_providers = {
        "codex_live_session",
        "kiro_live_session",
        "cursor_live_session",
        "grok_live_session",
        "antigravity_live_session",
        "hermes_live_session",
    }
    if config.provider_kind in live_session_providers and config.connection_kind != "live_session":
        raise ValueError(f"{config.provider_kind} resident requires live_session connection_kind.")
    cursor_superseded_error = cursor_terminal_session_superseded_error(
        config.provider_kind,
        config.connection_kind,
        config.command,
    )
    if cursor_superseded_error:
        raise ValueError(cursor_superseded_error)
    cursor_generic_error = cursor_generic_resident_guard_error(config.provider_kind, config.connection_kind)
    if cursor_generic_error:
        raise ValueError(cursor_generic_error)
    claude_command_error = claude_code_print_mode_resident_error(
        config.provider_kind,
        config.connection_kind,
        config.command,
    )
    if claude_command_error:
        raise ValueError(claude_command_error)
    if config.connection_kind == "remote_bridge":
        if not config.endpoint:
            raise ValueError("Remote bridge resident requires --endpoint.")
        if not config.auth_ref:
            raise ValueError("Remote bridge resident requires --auth-ref.")
        return
    command_required_kinds = {
        "local_cli",
        "live_session",
        "terminal_session",
        "self_service",
        "codex_resume",
        "manual",
    }
    if config.connection_kind in command_required_kinds and not config.command:
        raise ValueError(f"{config.connection_kind} resident requires --command.")


def resident_workspace_cwd(config: ResidentAgentConfig) -> Path:
    workspace_path = str(getattr(config, "workspace_path", "") or "").strip()
    if not workspace_path:
        return Path.cwd()
    path = Path(workspace_path).expanduser()
    if not path.exists() or not path.is_dir():
        raise ValueError("Workspace folder was not found.")
    return path
