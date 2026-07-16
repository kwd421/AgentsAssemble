from __future__ import annotations

import tempfile
import threading
import unittest
from pathlib import Path

from agentsassemble.persistence.local.room.repository import RoomStore
from agentsassemble.providers.launch_specs import (
    NativeCliProviderSpec,
    default_native_cli_provider_specs,
)
from agentsassemble.room.errors import RoomCommandRejected
from agentsassemble.room.event_broker import RoomEventBroker
from agentsassemble.room.provider_registry import RoomProviderRegistry
from agentsassemble.room.provider_sessions import (
    RoomProviderSessionService,
    restorable_process_ownership,
)


def _spec(agent_id: str = "codex", *, model: str = "") -> NativeCliProviderSpec:
    return NativeCliProviderSpec(
        agent_id=agent_id,
        display_name=agent_id.title(),
        command=(agent_id,),
        cwd=".",
        model=model,
    )


class RoomProviderSessionServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.store = RoomStore(self.root)
        self.broker = RoomEventBroker()
        self.lock = threading.RLock()
        self.registry = RoomProviderRegistry(
            lock=self.lock,
            default_room_id="general",
        )
        self.published_sessions: list[tuple[str, dict[str, object]]] = []
        self.service = self._service(self.registry)

    def tearDown(self) -> None:
        self.broker.close()
        self.store.close()
        self.temporary_directory.cleanup()

    def _service(self, registry: RoomProviderRegistry) -> RoomProviderSessionService:
        return RoomProviderSessionService(
            store=self.store,
            broker=self.broker,
            lock=self.lock,
            registry=registry,
            ensure_room=lambda room_id: self.store.create_room(room_id),
            publish_session_state=lambda room_id, session: self.published_sessions.append(
                (room_id, dict(session))
            ),
        )

    def test_create_provider_session_registers_one_canonical_participant_and_session(self) -> None:
        created = self.service.create_provider_session("general", _spec())
        participant = self.store.participant("general", "codex")
        session = self.store.session("general", "codex")

        self.assertEqual(created["session_id"], "codex")
        self.assertEqual(participant["participant_type"], "agent")
        self.assertEqual(session["process_ownership"], "server")
        self.assertEqual(session["runtime_status"], "stopped")
        self.assertTrue(self.registry.contains("general", "codex"))
        self.assertEqual(self.published_sessions[-1][1]["session_id"], "codex")

    def test_external_bridge_session_preserves_invite_owner_and_external_ownership(self) -> None:
        self.store.create_room("general")

        self.service.ensure_external_bridge_session(
            "general",
            {
                "agent_id": "external",
                "display_name": "External",
                "provider_kind": "custom_external",
                "owner_id": "user-42",
            },
        )

        participant = self.store.participant("general", "external")
        session = self.store.session("general", "external")
        self.assertEqual(participant["owner_id"], "user-42")
        self.assertEqual(session["process_ownership"], "external")
        self.assertEqual(session["transport"], "websocket")
        self.assertEqual(
            self.registry.provider("general", "external").command,
            ("external-attendee",),
        )

    def test_restore_rebuilds_only_complete_server_owned_provider_profiles(self) -> None:
        restorable = default_native_cli_provider_specs(workspace=self.root)[0]
        self.service.create_provider_session("general", restorable)
        restored_registry = RoomProviderRegistry(
            lock=self.lock,
            default_room_id="general",
        )

        self._service(restored_registry).restore_server_owned_providers()

        self.assertTrue(restored_registry.contains("general", restorable.agent_id))

    def test_running_session_rejects_profile_changes_without_replacing_registry_spec(self) -> None:
        original = _spec("codex", model="model-a")
        self.service.create_provider_session("general", original)
        self.store.update_session_fields(
            "general",
            "codex",
            enabled=True,
            runtime_status="idle",
        )

        with self.assertRaises(RoomCommandRejected) as raised:
            self.service.configure_stopped_provider_profile(
                "general",
                _spec("codex", model="model-b"),
            )

        self.assertEqual(raised.exception.code, "runtime_profile_conflict")
        self.assertEqual(self.registry.provider("general", "codex").model, "model-a")

    def test_restorable_process_ownership_requires_the_complete_legacy_profile(self) -> None:
        self.assertEqual(restorable_process_ownership({"external_owned": True}), "external")
        self.assertEqual(restorable_process_ownership({"runtime_kind": "live_cli"}), "")
        self.assertEqual(
            restorable_process_ownership(
                {
                    "runtime_kind": "live_cli",
                    "runtime_profile_key": "profile",
                    "command_configured": ["codex"],
                }
            ),
            "server",
        )


if __name__ == "__main__":
    unittest.main()
