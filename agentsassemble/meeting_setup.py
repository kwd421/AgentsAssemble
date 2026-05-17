from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agentsassemble.admission import build_admission_decisions
from agentsassemble.adapters import default_provider_registry
from agentsassemble.adapters.registry import ProviderRegistry, ResolvedAgentAdapter
from agentsassemble.config import (
    agent_bindings_from_config,
    load_agent_runtime_config,
    permissions_from_config,
    providers_from_config,
)
from agentsassemble.models import AgentBinding, PermissionProfile, ProviderConfig


@dataclass(frozen=True)
class MeetingSetup:
    provider: ProviderConfig
    providers: dict[str, ProviderConfig]
    permissions: dict[str, PermissionProfile]
    agent_bindings: list[AgentBinding]
    registry: ProviderRegistry
    resolved_agents: dict[str, ResolvedAgentAdapter]
    moderator_adapter: ProviderAdapter
    config_source: str
    incoming_agents: list[dict[str, Any]]
    admission_decisions: list[dict[str, Any]]


def provider_config_for_adapter(
    adapter_name: str,
    codex_timeout_seconds: int | None,
    codex_search_enabled: bool,
) -> ProviderConfig:
    if adapter_name == "mock":
        return ProviderConfig(id="mock", kind="mock", display_name="Mock Demo", default_model="deterministic")
    if adapter_name == "codex":
        return ProviderConfig(
            id="codex",
            kind="codex",
            display_name="Codex CLI",
            default_model="local-codex-session",
            timeout_seconds=codex_timeout_seconds,
            search_enabled=codex_search_enabled,
        )
    if adapter_name == "codex-live":
        return ProviderConfig(
            id="codex-live",
            kind="codex_live_session",
            display_name="Codex CLI Live Session",
            default_model="local-codex-session",
            timeout_seconds=codex_timeout_seconds,
            search_enabled=codex_search_enabled,
        )
    raise ValueError(f"Unknown adapter: {adapter_name}")


def default_permissions(adapter_name: str, codex_search_enabled: bool) -> dict[str, PermissionProfile]:
    return {
        "meeting_read_only": PermissionProfile(
            id="meeting_read_only",
            web_search=adapter_name in {"codex", "codex-live"} and codex_search_enabled,
            tool_use=False,
            filesystem_read=False,
            filesystem_write=False,
            git_write=False,
            push=False,
            implementation=False,
        )
    }


def default_agent_bindings(config_roles: list[Any], provider_id: str) -> list[AgentBinding]:
    return [
        AgentBinding(
            agent_id=f"{role.id}-agent",
            role_id=role.id,
            owner_id="local-user",
            provider_id=provider_id,
            model_id=None,
            permission_profile_id="meeting_read_only",
        )
        for role in config_roles
    ]


def prepare_meeting_setup(
    config_roles: list[Any],
    adapter_name: str,
    codex_timeout_seconds: int | None,
    codex_search_enabled: bool,
    agent_config_path: Path | str | None = None,
) -> MeetingSetup:
    provider = provider_config_for_adapter(adapter_name, codex_timeout_seconds, codex_search_enabled)
    providers = {provider.id: provider}
    permissions = default_permissions(adapter_name, codex_search_enabled)
    agent_bindings = default_agent_bindings(config_roles, provider.id)
    incoming_agents: list[dict[str, Any]] = []
    config_source = "default"
    runtime_config = load_agent_runtime_config(agent_config_path)
    if runtime_config is not None:
        config_source = str(agent_config_path)
        providers.update(providers_from_config(runtime_config))
        permissions.update(permissions_from_config(runtime_config))
        configured_bindings = agent_bindings_from_config(runtime_config)
        if configured_bindings:
            agent_bindings = configured_bindings
        incoming_agents = [
            item for item in runtime_config.get("incoming_agents", [])
            if isinstance(item, dict)
        ]
    _validate_role_bindings(config_roles, agent_bindings)
    _validate_live_session_bindings(agent_bindings)
    registry = default_provider_registry(
        codex_timeout_seconds=codex_timeout_seconds,
        codex_search_enabled=codex_search_enabled,
    )
    resolved_agents = {
        binding.role_id: registry.resolve(binding, providers, permissions)
        for binding in agent_bindings
    }
    admission_decisions = build_admission_decisions(
        incoming_agents,
        agent_bindings,
        config_roles,
        providers,
        permissions,
    )
    return MeetingSetup(
        provider=provider,
        providers=providers,
        permissions=permissions,
        agent_bindings=agent_bindings,
        registry=registry,
        resolved_agents=resolved_agents,
        moderator_adapter=registry.create(provider),
        config_source=config_source,
        incoming_agents=incoming_agents,
        admission_decisions=admission_decisions,
    )


def _validate_role_bindings(config_roles: list[Any], agent_bindings: list[AgentBinding]) -> None:
    role_ids = {role.id for role in config_roles}
    bound_role_ids = {binding.role_id for binding in agent_bindings}
    missing = sorted(role_ids - bound_role_ids)
    unknown = sorted(bound_role_ids - role_ids)
    if missing:
        raise ValueError(f"Missing agent binding for role(s): {', '.join(missing)}")
    if unknown:
        raise ValueError(f"Agent binding references unknown role(s): {', '.join(unknown)}")


def _validate_live_session_bindings(agent_bindings: list[AgentBinding]) -> None:
    seen: dict[str, str] = {}
    for binding in agent_bindings:
        if not binding.session_id:
            continue
        if binding.session_id in seen:
            raise ValueError(
                f"Duplicate live session_id {binding.session_id} for roles {seen[binding.session_id]} and {binding.role_id}"
            )
        seen[binding.session_id] = binding.role_id
