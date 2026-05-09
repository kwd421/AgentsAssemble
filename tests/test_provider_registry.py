import unittest

from agentsassemble.adapters.base import ProviderAdapter
from agentsassemble.adapters.registry import ProviderRegistry, default_provider_registry
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

    def test_default_registry_exposes_planned_provider_capabilities(self):
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
        self.assertIsInstance(adapter, UnsupportedProviderAdapter)
        with self.assertRaisesRegex(NotImplementedError, "Gemini API integration is planned"):
            adapter.run_research(
                Role("researcher", "리서처", "Research", "broad research"),
                {"role_id": "researcher"},
                "Question?",
                None,
                None,
            )

    def test_default_registry_rejects_implementation_agent_in_meeting_turns(self):
        registry = default_provider_registry()
        provider = ProviderConfig(id="cursor", kind="cursor", display_name="Cursor Agent")
        permission = PermissionProfile(id="meeting_turn")
        binding = AgentBinding(
            agent_id="cursor-agent",
            role_id="implementer",
            owner_id="local-user",
            provider_id="cursor",
            model_id=None,
            permission_profile_id="meeting_turn",
        )

        with self.assertRaisesRegex(ValueError, "cannot run research"):
            registry.resolve(binding, {"cursor": provider}, {"meeting_turn": permission})


if __name__ == "__main__":
    unittest.main()
