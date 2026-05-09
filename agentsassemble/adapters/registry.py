from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from agentsassemble.adapters.base import ProviderAdapter
from agentsassemble.adapters.codex import CodexAdapter
from agentsassemble.adapters.mock import MockAdapter
from agentsassemble.models import AgentBinding, PermissionProfile, ProviderCapabilities, ProviderConfig


AdapterFactory = Callable[[ProviderConfig], ProviderAdapter]


@dataclass(frozen=True)
class ResolvedAgentAdapter:
    binding: AgentBinding
    provider: ProviderConfig
    permissions: PermissionProfile
    capabilities: ProviderCapabilities
    adapter: ProviderAdapter


class ProviderRegistry:
    def __init__(self) -> None:
        self._factories: dict[str, AdapterFactory] = {}
        self._capabilities: dict[str, ProviderCapabilities] = {}

    def register(
        self,
        kind: str,
        factory: AdapterFactory,
        capabilities: ProviderCapabilities,
    ) -> None:
        self._factories[kind] = factory
        self._capabilities[kind] = capabilities

    def create(self, provider: ProviderConfig) -> ProviderAdapter:
        try:
            factory = self._factories[provider.kind]
        except KeyError as error:
            raise ValueError(f"Provider kind is not implemented yet: {provider.kind}") from error
        return factory(provider)

    def capabilities_for(self, provider: ProviderConfig) -> ProviderCapabilities:
        try:
            return self._capabilities[provider.kind]
        except KeyError as error:
            raise ValueError(f"Provider kind has no registered capabilities: {provider.kind}") from error

    def resolve(
        self,
        binding: AgentBinding,
        providers: dict[str, ProviderConfig],
        permissions: dict[str, PermissionProfile],
    ) -> ResolvedAgentAdapter:
        try:
            provider = providers[binding.provider_id]
        except KeyError as error:
            raise ValueError(f"Unknown provider id for agent {binding.agent_id}: {binding.provider_id}") from error
        try:
            permission = permissions[binding.permission_profile_id]
        except KeyError as error:
            raise ValueError(
                f"Unknown permission profile for agent {binding.agent_id}: {binding.permission_profile_id}"
            ) from error
        capabilities = self.capabilities_for(provider)
        validate_binding(binding, provider, permission, capabilities)
        return ResolvedAgentAdapter(
            binding=binding,
            provider=provider,
            permissions=permission,
            capabilities=capabilities,
            adapter=self.create(provider),
        )


def default_provider_registry(
    codex_timeout_seconds: int = 240,
    codex_search_enabled: bool = True,
) -> ProviderRegistry:
    registry = ProviderRegistry()
    registry.register(
        "mock",
        lambda provider: MockAdapter(),
        ProviderCapabilities(
            supports_research=True,
            supports_web_search=False,
            supports_tools=False,
            supports_filesystem=False,
            supports_session_resume=False,
            supports_structured_output=True,
            cost_class="free",
        ),
    )
    registry.register(
        "codex",
        lambda provider: CodexAdapter(
            timeout_seconds=provider.timeout_seconds or codex_timeout_seconds,
            search_enabled=provider.search_enabled and codex_search_enabled,
        ),
        ProviderCapabilities(
            supports_research=True,
            supports_web_search=codex_search_enabled,
            supports_tools=True,
            supports_filesystem=True,
            supports_session_resume=True,
            supports_structured_output=True,
            cost_class="subscription",
        ),
    )
    return registry


def validate_binding(
    binding: AgentBinding,
    provider: ProviderConfig,
    permission: PermissionProfile,
    capabilities: ProviderCapabilities,
) -> None:
    if permission.web_search and not capabilities.supports_web_search:
        raise ValueError(f"Agent {binding.agent_id} requests web_search but provider {provider.id} cannot search.")
    if permission.tool_use and not capabilities.supports_tools:
        raise ValueError(f"Agent {binding.agent_id} requests tools but provider {provider.id} has no tool support.")
    if permission.filesystem_read and not capabilities.supports_filesystem:
        raise ValueError(
            f"Agent {binding.agent_id} requests filesystem_read but provider {provider.id} has no filesystem support."
        )
    if permission.filesystem_write or permission.git_write or permission.push or permission.implementation:
        raise ValueError(
            f"Agent {binding.agent_id} requests implementation-side permissions during a meeting-only run."
        )

