from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from agentsassemble.adapters.base import ProviderAdapter
from agentsassemble.adapters.codex import CodexAdapter
from agentsassemble.adapters.codex_live import CodexLiveSessionAdapter
from agentsassemble.adapters.http_llm import (
    AnthropicMessagesAdapter,
    GeminiGenerateContentAdapter,
    GrokChatAdapter,
    LocalOpenAICompatibleAdapter,
)
from agentsassemble.adapters.local_cli import LocalCliAdapter
from agentsassemble.adapters.mock import MockAdapter
from agentsassemble.adapters.remote_bridge import RemoteBridgeAdapter
from agentsassemble.adapters.unsupported import UnsupportedProviderAdapter
from agentsassemble.models import AgentBinding, PermissionProfile, ProviderCapabilities, ProviderConfig


AdapterFactory = Callable[[ProviderConfig], ProviderAdapter]
REMOTE_BRIDGE_REQUESTER = None


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
        self._catalog: dict[str, dict[str, object]] = {}

    def register(
        self,
        kind: str,
        factory: AdapterFactory,
        capabilities: ProviderCapabilities,
        status: str = "available",
        reason: str | None = None,
        preferred_phase: str = "meeting",
    ) -> None:
        self._factories[kind] = factory
        self._capabilities[kind] = capabilities
        self._catalog[kind] = {
            "kind": kind,
            "status": status,
            "reason": reason,
            "preferred_phase": preferred_phase,
            "capabilities": capabilities.to_dict(),
        }

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

    def catalog(self) -> list[dict[str, object]]:
        return [self._catalog[kind] for kind in sorted(self._catalog)]

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
            sandbox_enforcement="codex_readonly",
        ),
    )
    registry.register(
        "codex_live_session",
        lambda provider: CodexLiveSessionAdapter(
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
            sandbox_enforcement="codex_readonly",
        ),
    )
    register_http_provider_kinds(registry)
    register_planned_provider_kinds(registry)
    return registry


def register_http_provider_kinds(registry: ProviderRegistry) -> None:
    registry.register(
        "anthropic",
        lambda provider: AnthropicMessagesAdapter(provider),
        ProviderCapabilities(
            supports_research=True,
            supports_web_search=False,
            supports_tools=True,
            supports_filesystem=False,
            supports_session_resume=False,
            supports_structured_output=True,
            context_window=200_000,
            cost_class="paid_api",
        ),
    )
    registry.register(
        "gemini",
        lambda provider: GeminiGenerateContentAdapter(provider),
        ProviderCapabilities(
            supports_research=True,
            supports_web_search=True,
            supports_tools=True,
            supports_filesystem=False,
            supports_session_resume=False,
            supports_structured_output=True,
            context_window=1_000_000,
            cost_class="paid_api",
        ),
    )
    registry.register(
        "grok",
        lambda provider: GrokChatAdapter(provider),
        ProviderCapabilities(
            supports_research=True,
            supports_web_search=True,
            supports_tools=True,
            supports_filesystem=False,
            supports_session_resume=False,
            supports_structured_output=True,
            cost_class="paid_api",
        ),
    )
    registry.register(
        "local_openai_compatible",
        lambda provider: LocalOpenAICompatibleAdapter(provider),
        ProviderCapabilities(
            supports_research=True,
            supports_web_search=False,
            supports_tools=False,
            supports_filesystem=False,
            supports_session_resume=False,
            supports_structured_output=True,
            cost_class="local",
        ),
    )
    registry.register(
        "remote_http_bridge",
        lambda provider: RemoteBridgeAdapter(provider, requester=REMOTE_BRIDGE_REQUESTER),
        ProviderCapabilities(
            supports_research=True,
            supports_web_search=False,
            supports_tools=False,
            supports_filesystem=False,
            supports_session_resume=False,
            supports_structured_output=True,
            cost_class="remote_user_session",
        ),
    )
    registry.register(
        "local_cli",
        lambda provider: LocalCliAdapter(provider),
        ProviderCapabilities(
            supports_research=True,
            supports_web_search=False,
            supports_tools=False,
            supports_filesystem=False,
            supports_session_resume=False,
            supports_structured_output=True,
            cost_class="local_cli",
        ),
    )


def register_planned_provider_kinds(registry: ProviderRegistry) -> None:
    planned: dict[str, tuple[ProviderCapabilities, str]] = {
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
        "kiro_live_session": (
            ProviderCapabilities(
                supports_research=True,
                supports_web_search=False,
                supports_tools=True,
                supports_filesystem=True,
                supports_session_resume=True,
                supports_structured_output=False,
                cost_class="subscription",
            ),
            "Kiro live-session residents are supported through kiro chat --resume-id; implementation permissions stay gated until after explicit approval.",
        ),
        "antigravity_cli": (
            ProviderCapabilities(
                supports_research=True,
                supports_web_search=True,
                supports_tools=True,
                supports_filesystem=True,
                supports_session_resume=True,
                supports_structured_output=False,
                cost_class="subscription",
            ),
            "Antigravity CLI meeting-mode integration is planned through self-service resident sessions.",
        ),
        "gemini_cli_legacy": (
            ProviderCapabilities(
                supports_research=True,
                supports_web_search=True,
                supports_tools=True,
                supports_filesystem=True,
                supports_session_resume=True,
                supports_structured_output=False,
                cost_class="subscription",
            ),
            "Legacy Gemini CLI resident integration is supported only for explicit compatibility testing.",
        ),
        "grok_build_cli": (
            ProviderCapabilities(
                supports_research=True,
                supports_web_search=True,
                supports_tools=True,
                supports_filesystem=True,
                supports_session_resume=True,
                supports_structured_output=False,
                cost_class="subscription",
            ),
            "Grok Build CLI resident integration is planned through terminal prompt bridge discovery.",
        ),
        "hermes_cli": (
            ProviderCapabilities(
                supports_research=True,
                supports_web_search=False,
                supports_tools=True,
                supports_filesystem=True,
                supports_session_resume=True,
                supports_structured_output=False,
                cost_class="subscription",
            ),
            "Hermes CLI resident integration is planned through terminal prompt bridge discovery.",
        ),
        "openclaw_cli": (
            ProviderCapabilities(
                supports_research=True,
                supports_web_search=False,
                supports_tools=True,
                supports_filesystem=True,
                supports_session_resume=True,
                supports_structured_output=False,
                cost_class="subscription",
            ),
            "OpenClaw CLI resident integration is planned through terminal prompt bridge discovery.",
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
        registry.register(
            kind,
            lambda provider, reason=reason: UnsupportedProviderAdapter(provider.kind, reason),
            capabilities,
            status="planned",
            reason=reason,
            preferred_phase="implementation"
            if kind
            in {
                "cursor",
                "claude_code",
                "kiro_live_session",
                "antigravity_cli",
                "gemini_cli_legacy",
                "grok_build_cli",
                "hermes_cli",
                "openclaw_cli",
            }
            else "memory",
        )


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
