from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from agentsassemble.adapters.base import ProviderAdapter
from agentsassemble.adapters.codex import CodexAdapter
from agentsassemble.adapters.mock import MockAdapter
from agentsassemble.adapters.unsupported import UnsupportedProviderAdapter
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
    codex_timeout_seconds: int | None = None,
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
            timeout_seconds=provider.timeout_seconds if provider.timeout_seconds is not None else codex_timeout_seconds,
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
    register_planned_provider_kinds(registry)
    return registry


def register_planned_provider_kinds(registry: ProviderRegistry) -> None:
    planned: dict[str, tuple[ProviderCapabilities, str]] = {
        "anthropic": (
            ProviderCapabilities(
                supports_research=True,
                supports_web_search=False,
                supports_tools=True,
                supports_filesystem=False,
                supports_session_resume=True,
                supports_structured_output=True,
                context_window=200_000,
                cost_class="paid_api",
            ),
            "Claude API integration is planned; configure as an external meeting provider before enabling calls.",
        ),
        "gemini": (
            ProviderCapabilities(
                supports_research=True,
                supports_web_search=True,
                supports_tools=True,
                supports_filesystem=False,
                supports_session_resume=True,
                supports_structured_output=True,
                context_window=1_000_000,
                cost_class="paid_api",
            ),
            "Gemini API integration is planned; grounding/search behavior must be wired explicitly.",
        ),
        "grok": (
            ProviderCapabilities(
                supports_research=True,
                supports_web_search=True,
                supports_tools=True,
                supports_filesystem=False,
                supports_session_resume=True,
                supports_structured_output=True,
                cost_class="paid_api",
            ),
            "Grok API integration is planned; evidence provenance must be strict before live use.",
        ),
        "local_openai_compatible": (
            ProviderCapabilities(
                supports_research=True,
                supports_web_search=False,
                supports_tools=False,
                supports_filesystem=False,
                supports_session_resume=False,
                supports_structured_output=True,
                cost_class="local",
            ),
            "Local OpenAI-compatible provider integration is planned; endpoint calls are not wired yet.",
        ),
        "cursor": (
            ProviderCapabilities(
                supports_research=True,
                supports_web_search=False,
                supports_tools=True,
                supports_filesystem=True,
                supports_session_resume=True,
                supports_structured_output=False,
                cost_class="subscription",
            ),
            "Cursor meeting-mode integration is planned; implementation permissions stay gated until after a decision artifact exists.",
        ),
        "claude_code": (
            ProviderCapabilities(
                supports_research=True,
                supports_web_search=False,
                supports_tools=True,
                supports_filesystem=True,
                supports_session_resume=True,
                supports_structured_output=False,
                cost_class="subscription",
            ),
            "Claude Code meeting-mode integration is planned; implementation permissions stay gated until after a decision artifact exists.",
        ),
        "hermes_memory": (
            ProviderCapabilities(
                supports_research=False,
                supports_web_search=False,
                supports_tools=False,
                supports_filesystem=False,
                supports_session_resume=True,
                supports_structured_output=True,
                cost_class="memory_pack",
            ),
            "Hermes-style memory is treated as an importable profile pack, not a live meeting adapter.",
        ),
        "openclaw_memory": (
            ProviderCapabilities(
                supports_research=False,
                supports_web_search=False,
                supports_tools=False,
                supports_filesystem=False,
                supports_session_resume=True,
                supports_structured_output=True,
                cost_class="memory_pack",
            ),
            "OpenClaw-style memory is treated as an importable profile pack, not a live meeting adapter.",
        ),
        "memory_pack": (
            ProviderCapabilities(
                supports_research=False,
                supports_web_search=False,
                supports_tools=False,
                supports_filesystem=False,
                supports_session_resume=True,
                supports_structured_output=True,
                cost_class="memory_pack",
            ),
            "Memory/profile packs must pass a memory gate before influencing meetings.",
        ),
    }
    for kind, (capabilities, reason) in planned.items():
        registry.register(kind, lambda provider, reason=reason: UnsupportedProviderAdapter(provider.kind, reason), capabilities)


def validate_binding(
    binding: AgentBinding,
    provider: ProviderConfig,
    permission: PermissionProfile,
    capabilities: ProviderCapabilities,
) -> None:
    if permission.web_search and not capabilities.supports_web_search:
        raise ValueError(f"Agent {binding.agent_id} requests web_search but provider {provider.id} cannot search.")
    if permission.official_turn and not capabilities.supports_research:
        raise ValueError(
            f"Agent {binding.agent_id} requests official meeting turns but provider {provider.id} cannot run research."
        )
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
