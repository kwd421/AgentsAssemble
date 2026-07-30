from __future__ import annotations

import tempfile
import unittest
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
                "agent_id": "luna",
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
            server_url="http://127.0.0.1:8765",
            ticket_issuer=None,
        )

        room_id, spec = self.created_specs[-1]
        self.assertEqual(room_id, "general")
        self.assertEqual(spec.agent_id, "luna")
        self.assertEqual(spec.display_name, "Luna")
        self.assertEqual(spec.model, self.selection.model)
        self.assertEqual(spec.max_output_tokens, 8192)
        self.assertEqual(result["participant"]["display_name"], "Luna")
        self.assertEqual(result["start"]["status"], "starting")
        self.assertEqual(
            self.start_calls,
            [("general", "luna", "http://127.0.0.1:8765")],
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
            server_url="http://127.0.0.1:8765",
            ticket_issuer=None,
        )

        self.assertEqual(result["status"], "created")
        self.assertNotIn("start", result)
        self.assertEqual(self.start_calls, [])

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
                server_url="",
                ticket_issuer=None,
            )

        self.assertEqual(raised.exception.code, "catalog_changed")
        self.assertEqual(self.created_specs, [])


if __name__ == "__main__":
    unittest.main()
