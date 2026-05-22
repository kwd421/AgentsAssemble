import unittest

from agentsassemble.adapters.base import ProviderAdapter
from agentsassemble.adapters.registry import ProviderRegistry, default_provider_registry
from agentsassemble.adapters.http_llm import (
    AnthropicMessagesAdapter,
    GeminiGenerateContentAdapter,
    GrokChatAdapter,
    LocalOpenAICompatibleAdapter,
)
from agentsassemble.adapters.codex_live import CodexLiveSessionAdapter
from agentsassemble.adapters.local_cli import LocalCliAdapter
from agentsassemble.adapters.remote_bridge import RemoteBridgeAdapter
from agentsassemble.adapters.unsupported import UnsupportedProviderAdapter
from agentsassemble.models import AgentBinding, PermissionProfile, ProviderCapabilities, ProviderConfig, Role


class FakeAdapter(ProviderAdapter):
    name = "fake"

    def start_session(self, role: Role, meeting_context: dict):
        return {"role_id": role.id, "meeting_dir": meeting_context.get("meeting_dir")}

    def run_research(self, role, session, question, depth, steering):
        return {}

    def run_round(self, role, session, round_name, prompt, public_context):
        return {}

    def synthesize(self, session, question, public_context):
        return {}


class ProviderRegistryTests(unittest.TestCase):
    def test_registry_resolves_external_provider_binding(self):
        registry = ProviderRegistry()
        registry.register(
            "anthropic",
            lambda provider: FakeAdapter(),
            ProviderCapabilities(
                supports_research=True,
                supports_web_search=True,
                supports_tools=True,
                supports_filesystem=False,
                supports_session_resume=True,
                supports_structured_output=True,
                context_window=200_000,
                cost_class="paid_api",
            ),
        )
        provider = ProviderConfig(
            id="claude-design",
            kind="anthropic",
            display_name="Claude Design Reviewer",
            default_model="claude-sonnet",
            auth_ref="env:ANTHROPIC_API_KEY",
            search_enabled=True,
        )
        permission = PermissionProfile(id="meeting_review", web_search=True)
        binding = AgentBinding(
            agent_id="designer-agent",
            role_id="design_reviewer",
            owner_id="local-user",
            provider_id="claude-design",
            model_id="claude-sonnet",
            permission_profile_id="meeting_review",
        )

        resolved = registry.resolve(
            binding,
            {"claude-design": provider},
            {"meeting_review": permission},
        )

        self.assertEqual(resolved.provider.kind, "anthropic")
        self.assertEqual(resolved.binding.agent_id, "designer-agent")
        self.assertTrue(resolved.capabilities.supports_web_search)
        self.assertIsInstance(resolved.adapter, FakeAdapter)

    def test_meeting_registry_rejects_implementation_permissions(self):
        registry = ProviderRegistry()
        registry.register(
            "local_openai_compatible",
            lambda provider: FakeAdapter(),
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
        provider = ProviderConfig(
            id="local-hermes",
            kind="local_openai_compatible",
            display_name="Local Hermes-style Model",
            endpoint="http://127.0.0.1:11434/v1",
        )
        binding = AgentBinding(
            agent_id="local-agent",
            role_id="memory_skeptic",
            owner_id="local-user",
            provider_id="local-hermes",
            model_id="local-model",
            permission_profile_id="unsafe",
        )
        unsafe_permission = PermissionProfile(id="unsafe", implementation=True, filesystem_write=True)

        with self.assertRaisesRegex(ValueError, "implementation-side permissions"):
            registry.resolve(binding, {"local-hermes": provider}, {"unsafe": unsafe_permission})

    def test_default_registry_creates_gemini_provider_adapter(self):
        registry = default_provider_registry()
        provider = ProviderConfig(
            id="gemini-research",
            kind="gemini",
            display_name="Gemini Research",
            default_model="gemini-pro",
            search_enabled=True,
        )

        capabilities = registry.capabilities_for(provider)
        adapter = registry.create(provider)

        self.assertTrue(capabilities.supports_research)
        self.assertTrue(capabilities.supports_web_search)
        self.assertTrue(capabilities.supports_structured_output)
        self.assertIsInstance(adapter, GeminiGenerateContentAdapter)

    def test_default_codex_registry_preserves_unlimited_timeout(self):
        registry = default_provider_registry(codex_timeout_seconds=None)
        adapter = registry.create(
            ProviderConfig(
                id="codex",
                kind="codex",
                display_name="Codex CLI",
                timeout_seconds=None,
            )
        )

        self.assertIsNone(adapter.timeout_seconds)

    def test_provider_specific_timeout_overrides_default(self):
        registry = default_provider_registry(codex_timeout_seconds=None)
        adapter = registry.create(
            ProviderConfig(
                id="codex",
                kind="codex",
                display_name="Codex CLI",
                timeout_seconds=900,
            )
        )

        self.assertEqual(adapter.timeout_seconds, 900)

    def test_default_registry_allows_coding_agents_as_read_only_meeting_participants(self):
        registry = default_provider_registry()
        provider = ProviderConfig(id="cursor", kind="cursor", display_name="Cursor Agent")
        permission = PermissionProfile(id="meeting_turn", tool_use=True, filesystem_read=True)
        binding = AgentBinding(
            agent_id="cursor-agent",
            role_id="implementer",
            owner_id="local-user",
            provider_id="cursor",
            model_id=None,
            permission_profile_id="meeting_turn",
        )

        resolved = registry.resolve(binding, {"cursor": provider}, {"meeting_turn": permission})

        self.assertEqual(resolved.provider.kind, "cursor")
        self.assertTrue(resolved.permissions.official_turn)
        self.assertFalse(resolved.permissions.filesystem_write)
        self.assertFalse(resolved.permissions.implementation)
        self.assertTrue(resolved.capabilities.supports_research)

    def test_default_registry_rejects_coding_agents_when_implementation_permissions_are_enabled(self):
        registry = default_provider_registry()
        provider = ProviderConfig(id="claude-code", kind="claude_code", display_name="Claude Code")
        permission = PermissionProfile(id="implementation", implementation=True, filesystem_write=True)
        binding = AgentBinding(
            agent_id="claude-code-agent",
            role_id="implementer",
            owner_id="local-user",
            provider_id="claude-code",
            model_id=None,
            permission_profile_id="implementation",
        )

        with self.assertRaisesRegex(ValueError, "implementation-side permissions"):
            registry.resolve(binding, {"claude-code": provider}, {"implementation": permission})

    def test_default_registry_catalog_exposes_available_and_planned_providers(self):
        registry = default_provider_registry(codex_search_enabled=True)

        catalog = {entry["kind"]: entry for entry in registry.catalog()}

        self.assertEqual(catalog["mock"]["status"], "available")
        self.assertEqual(catalog["codex"]["status"], "available")
        self.assertEqual(catalog["codex_live_session"]["status"], "available")
        self.assertEqual(catalog["gemini"]["status"], "available")
        self.assertEqual(catalog["grok"]["status"], "available")
        self.assertEqual(catalog["remote_http_bridge"]["status"], "available")
        self.assertEqual(catalog["local_cli"]["status"], "available")
        self.assertEqual(catalog["cursor"]["status"], "planned")
        self.assertEqual(catalog["claude_code"]["status"], "planned")
        self.assertEqual(catalog["anthropic"]["status"], "available")
        self.assertEqual(catalog["local_openai_compatible"]["status"], "available")
        self.assertTrue(catalog["gemini"]["capabilities"]["supports_web_search"])
        self.assertTrue(catalog["cursor"]["capabilities"]["supports_filesystem"])
        self.assertEqual(catalog["codex_live_session"]["capabilities"]["sandbox_enforcement"], "codex_readonly")
        self.assertEqual(catalog["local_cli"]["capabilities"]["sandbox_enforcement"], "advisory")

    def test_default_registry_creates_http_provider_adapters(self):
        registry = default_provider_registry()

        self.assertIsInstance(
            registry.create(ProviderConfig(id="claude", kind="anthropic", display_name="Claude")),
            AnthropicMessagesAdapter,
        )
        self.assertIsInstance(
            registry.create(ProviderConfig(id="grok", kind="grok", display_name="Grok")),
            GrokChatAdapter,
        )
        self.assertIsInstance(
            registry.create(
                ProviderConfig(
                    id="lmstudio",
                    kind="local_openai_compatible",
                    display_name="LM Studio",
                    endpoint="http://127.0.0.1:1234/v1",
                )
            ),
            LocalOpenAICompatibleAdapter,
        )
        self.assertIsInstance(
            registry.create(
                ProviderConfig(
                    id="friend-claude-code",
                    kind="remote_http_bridge",
                    display_name="Friend Claude Code",
                    endpoint="http://100.64.0.10:8777",
                )
            ),
            RemoteBridgeAdapter,
        )
        self.assertIsInstance(
            registry.create(
                ProviderConfig(
                    id="gemini-cli",
                    kind="local_cli",
                    display_name="Gemini CLI",
                    command=["gemini", "--prompt"],
                )
            ),
            LocalCliAdapter,
        )
        self.assertIsInstance(
            registry.create(
                ProviderConfig(
                    id="codex-live",
                    kind="codex_live_session",
                    display_name="Codex Live Session",
                    timeout_seconds=240,
                )
            ),
            CodexLiveSessionAdapter,
        )


if __name__ == "__main__":
    unittest.main()
