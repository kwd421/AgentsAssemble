from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from agentsassemble.adapters import ProviderAdapter, default_provider_registry
from agentsassemble.adapters.registry import ProviderRegistry, ResolvedAgentAdapter
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


def get_adapter(
    adapter_name: str,
    codex_timeout_seconds: int = 240,
    codex_search_enabled: bool = True,
) -> ProviderAdapter:
    provider = provider_config_for_adapter(adapter_name, codex_timeout_seconds, codex_search_enabled)
    return default_provider_registry(
        codex_timeout_seconds=codex_timeout_seconds,
        codex_search_enabled=codex_search_enabled,
    ).create(provider)


def provider_config_for_adapter(
    adapter_name: str,
    codex_timeout_seconds: int,
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
    raise ValueError(f"Unknown adapter: {adapter_name}")


def default_permissions(adapter_name: str, codex_search_enabled: bool) -> dict[str, PermissionProfile]:
    return {
        "meeting_read_only": PermissionProfile(
            id="meeting_read_only",
            web_search=adapter_name == "codex" and codex_search_enabled,
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
    codex_timeout_seconds: int,
    codex_search_enabled: bool,
) -> MeetingSetup:
    provider = provider_config_for_adapter(adapter_name, codex_timeout_seconds, codex_search_enabled)
    providers = {provider.id: provider}
    permissions = default_permissions(adapter_name, codex_search_enabled)
    agent_bindings = default_agent_bindings(config_roles, provider.id)
    registry = default_provider_registry(
        codex_timeout_seconds=codex_timeout_seconds,
        codex_search_enabled=codex_search_enabled,
    )
    resolved_agents = {
        binding.role_id: registry.resolve(binding, providers, permissions)
        for binding in agent_bindings
    }
    return MeetingSetup(
        provider=provider,
        providers=providers,
        permissions=permissions,
        agent_bindings=agent_bindings,
        registry=registry,
        resolved_agents=resolved_agents,
        moderator_adapter=registry.create(provider),
    )
