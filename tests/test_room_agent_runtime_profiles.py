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
from agentsassemble.room.agent_runtime_profiles import (
    RoomAgentRuntimeProfileService,
)
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


class RoomAgentRuntimeProfileServiceTests(unittest.TestCase):
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
        )
        self.catalog = _Catalog(self.selection)
        self.configured_specs: list[tuple[str, NativeCliProviderSpec]] = []
        self.service = RoomAgentRuntimeProfileService(
            store=self.store,
            provider_catalog=self.catalog,
            configure_stopped_profile=self._configure,
        )
        self.store.upsert_session(
            "general",
            {
                "session_id": "codex",
                "participant_id": "codex",
                "display_name": "Codex",
                "provider_kind": definition.provider_kind,
                "workspace": str(self.root),
                "model": default.model,
                "reasoning_effort": default.reasoning_effort,
                "service_tier": default.service_tier,
                "variant": default.variant,
                "permission_mode": default.permission_mode,
                "runtime_status": "stopped",
            },
        )

    def tearDown(self) -> None:
        self.store.close()
        self.temporary_directory.cleanup()

    def _configure(
        self,
        room_id: str,
        spec: NativeCliProviderSpec,
    ) -> dict[str, object]:
        self.configured_specs.append((room_id, spec))
        return {
            "session_id": spec.agent_id,
            "model": spec.model,
            "runtime_profile_key": spec.runtime_profile_key(),
        }

    def test_configure_uses_catalog_selection_and_preserves_unspecified_values(self) -> None:
        result = self.service.configure(
            "general",
            "codex",
            {
                "agent_id": "codex",
                "catalog_revision": "revision-1",
                "display_name": "Luna",
            },
        )

        room_id, spec = self.configured_specs[-1]
        self.assertEqual(room_id, "general")
        self.assertEqual(spec.display_name, "Luna")
        self.assertEqual(spec.model, self.selection.model)
        self.assertEqual(
            self.catalog.calls[-1]["values"]["permission_mode"],
            self.selection.permission_mode,
        )
        self.assertEqual(result["status"], "configured")

    def test_running_session_is_rejected_before_catalog_validation(self) -> None:
        self.store.update_session_fields(
            "general",
            "codex",
            runtime_status="idle",
        )

        with self.assertRaises(RoomCommandRejected) as raised:
            self.service.configure(
                "general",
                "codex",
                {"catalog_revision": "revision-1"},
            )

        self.assertEqual(raised.exception.code, "runtime_profile_conflict")
        self.assertEqual(self.catalog.calls, [])

    def test_provider_kind_cannot_change(self) -> None:
        with self.assertRaises(RoomCommandRejected) as raised:
            self.service.configure(
                "general",
                "codex",
                {
                    "provider_id": "claude",
                    "catalog_revision": "revision-1",
                },
            )

        self.assertEqual(raised.exception.code, "provider_mismatch")
        self.assertEqual(self.configured_specs, [])

    def test_catalog_error_preserves_its_command_rejection_code(self) -> None:
        self.catalog.error = ProviderCatalogSelectionError(
            "catalog changed",
            code="catalog_changed",
        )

        with self.assertRaises(RoomCommandRejected) as raised:
            self.service.configure(
                "general",
                "codex",
                {"catalog_revision": "stale"},
            )

        self.assertEqual(raised.exception.code, "catalog_changed")
        self.assertEqual(self.configured_specs, [])


if __name__ == "__main__":
    unittest.main()
