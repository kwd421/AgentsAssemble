from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from agentsassemble.persistence.local.room.repository import RoomStore
from agentsassemble.providers.capabilities import (
    ProviderCatalogSelectionError,
    ValidatedProviderSelection,
)
from agentsassemble.providers.launch_specs import (
    NativeCliProviderSpec,
    native_cli_provider_definition,
)
from agentsassemble.room.agent_creation import RoomAgentCreationService
from agentsassemble.room.errors import RoomCommandRejected


class _Catalog:
    def __init__(self, selection: ValidatedProviderSelection) -> None:
        self.selection = selection
        self.calls: list[dict[str, object]] = []
        self.error: ProviderCatalogSelectionError | None = None

    def validate_selection(
        self,
        *,
        catalog_revision: str,
        provider_id: str,
        values: dict[str, str],
    ) -> ValidatedProviderSelection:
        self.calls.append(
            {
                "catalog_revision": catalog_revision,
                "provider_id": provider_id,
                "values": dict(values),
            }
        )
        if self.error is not None:
            raise self.error
        return self.selection


class RoomAgentCreationServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.store = RoomStore(self.root)
        self.store.create_room("general")
        definition = native_cli_provider_definition("codex")
        assert definition is not None
        default = definition.make_default_spec(
            agent_id="codex",
            display_name="Codex",
            cwd=self.root,
        )
        self.selection = ValidatedProviderSelection(
            catalog_revision="revision-1",
            provider_id=definition.provider_id,
            provider_kind=definition.provider_kind,
            model=default.model,
            model_selection_kind="exact",
            reasoning_effort=default.reasoning_effort,
            service_tier=default.service_tier,
            variant=default.variant,
            permission_mode=default.permission_mode,
            max_output_tokens=8192,
        )
        self.catalog = _Catalog(self.selection)
        self.created_specs: list[tuple[str, NativeCliProviderSpec]] = []
        self.start_calls: list[tuple[str, str, str]] = []
        self.service = RoomAgentCreationService(
            store=self.store,
            provider_catalog=self.catalog,
            create_provider_session=self._create_session,
            start_agent=self._start_agent,
        )

    def tearDown(self) -> None:
        self.store.close()
        self.temporary_directory.cleanup()

    def _create_session(
        self,
        room_id: str,
        spec: NativeCliProviderSpec,
    ) -> dict[str, object]:
        self.created_specs.append((room_id, spec))
        self.store.upsert_participant(
            room_id,
            {
                "participant_id": spec.agent_id,
                "display_name": spec.display_name,
                "participant_type": "agent",
            },
        )
        self.store.upsert_session(
            room_id,
            {
                "session_id": spec.agent_id,
                "participant_id": spec.agent_id,
                "display_name": spec.display_name,
                "model": spec.model,
            },
        )
        return self.store.session(room_id, spec.agent_id)

    def _start_agent(
        self,
        room_id: str,
        agent_id: str,
        *,
        server_url: str,
        ticket_issuer: object,
    ) -> dict[str, object]:
        del ticket_issuer
        self.start_calls.append((room_id, agent_id, server_url))
        return {"status": "starting"}

    def test_create_builds_catalog_selected_spec_and_optionally_starts(self) -> None:
        result = self.service.create(
            "general",
            {
                "provider_id": "codex",
                "agent_id": "client-chosen-id",
                "display_name": "Luna",
                "workspace": str(self.root),
                "model": self.selection.model,
                "reasoning_effort": self.selection.reasoning_effort,
                "service_tier": self.selection.service_tier,
                "variant": self.selection.variant,
                "permission_mode": self.selection.permission_mode,
                "max_output_tokens": self.selection.max_output_tokens,
                "catalog_revision": "revision-1",
                "start_now": True,
            },
            operation_id="create-luna",
            server_url="http://127.0.0.1:8765",
            ticket_issuer=None,
        )

        room_id, spec = self.created_specs[-1]
        self.assertEqual(room_id, "general")
        self.assertTrue(spec.agent_id.startswith("codex-"))
        self.assertNotEqual(spec.agent_id, "client-chosen-id")
        self.assertEqual(spec.display_name, "Luna")
        self.assertEqual(spec.model, self.selection.model)
        self.assertEqual(spec.max_output_tokens, 8192)
        self.assertEqual(result["participant"]["display_name"], "Luna")
        self.assertEqual(result["start"]["status"], "starting")
        self.assertEqual(
            self.start_calls,
            [("general", spec.agent_id, "http://127.0.0.1:8765")],
        )

    def test_create_without_start_does_not_launch_the_runtime(self) -> None:
        result = self.service.create(
            "general",
            {
                "provider_id": "codex",
                "agent_id": "luna",
                "workspace": str(self.root),
                "model": self.selection.model,
                "reasoning_effort": self.selection.reasoning_effort,
                "service_tier": self.selection.service_tier,
                "variant": self.selection.variant,
                "permission_mode": self.selection.permission_mode,
                "catalog_revision": "revision-1",
            },
            operation_id="create-luna",
            server_url="http://127.0.0.1:8765",
            ticket_issuer=None,
        )

        self.assertEqual(result["status"], "created")
        self.assertNotIn("start", result)
        self.assertEqual(self.start_calls, [])

    def test_custom_api_endpoint_is_part_of_the_server_owned_runtime_profile(self) -> None:
        self.catalog.selection = replace(
            self.selection,
            provider_id="custom_api",
            provider_kind="custom_openai_api",
            model="vendor-model",
            reasoning_effort="",
            provider_endpoint="https://api.example.com/v1",
        )

        self.service.create(
            "general",
            {
                "provider_id": "custom_api",
                "display_name": "Custom vendor-model",
                "model": "vendor-model",
                "provider_endpoint": "https://api.example.com/v1/chat/completions",
                "permission_mode": "meeting_read_only",
                "max_output_tokens": 4096,
                "catalog_revision": "revision-1",
            },
            operation_id="create-custom-69",
            server_url="http://127.0.0.1:8765",
            ticket_issuer=None,
        )

        _room_id, spec = self.created_specs[-1]
        self.assertEqual(spec.provider_kind, "custom_openai_api")
        self.assertEqual(spec.model, "vendor-model")
        self.assertEqual(spec.provider_endpoint, "https://api.example.com/v1")

    def test_create_carries_a_server_resolved_persona_into_the_session_profile(self) -> None:
        self.catalog.selection = replace(
            self.selection,
            provider_id="deepseek",
            provider_kind="deepseek_api",
            model="deepseek-v4-flash",
            reasoning_effort="high",
            variant="thinking",
        )
        resolver_calls: list[tuple[str, str]] = []

        def resolve_persona(provider_id: str, persona_card_id: str) -> dict[str, object]:
            resolver_calls.append((provider_id, persona_card_id))
            return {
                "id": "guide",
                "display_name": "Guide",
                "asset_kind": "card",
                "lorebook_count": 2,
                "ignored_feature_count": 0,
            }

        service = RoomAgentCreationService(
            store=self.store,
            provider_catalog=self.catalog,
            create_provider_session=self._create_session,
            start_agent=self._start_agent,
            resolve_persona=resolve_persona,
        )

        service.create(
            "general",
            {
                "provider_id": "deepseek",
                "display_name": "Guide",
                "model": "deepseek-chat",
                "permission_mode": "meeting_read_only",
                "catalog_revision": "revision-1",
                "persona_card_id": "guide",
            },
            operation_id="create-guide",
            server_url="http://127.0.0.1:8765",
            ticket_issuer=None,
        )

        _room_id, spec = self.created_specs[-1]
        self.assertEqual(resolver_calls, [("deepseek", "guide")])
        self.assertEqual(spec.persona_card_id, "guide")
        self.assertEqual(spec.persona_card_summary["display_name"], "Guide")

    def test_catalog_error_preserves_its_command_rejection_code(self) -> None:
        self.catalog.error = ProviderCatalogSelectionError(
            "catalog changed",
            code="catalog_changed",
        )

        with self.assertRaises(RoomCommandRejected) as raised:
            self.service.create(
                "general",
                {
                    "provider_id": "codex",
                    "catalog_revision": "stale",
                },
                operation_id="create-stale",
                server_url="",
                ticket_issuer=None,
            )

        self.assertEqual(raised.exception.code, "catalog_changed")
        self.assertEqual(self.created_specs, [])


if __name__ == "__main__":
    unittest.main()
