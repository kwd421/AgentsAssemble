from __future__ import annotations

import tempfile
import threading
import unittest
from contextlib import contextmanager
from pathlib import Path

from agentsassemble.persistence.local.room.repository import RoomStore
from agentsassemble.providers.launch_specs import (
    NativeCliProviderSpec,
    default_native_cli_provider_specs,
    native_cli_provider_definition,
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

    def test_create_provider_session_rolls_back_all_durable_records_when_event_write_fails(self) -> None:
        base_store = self.store

        class FailingEventTransaction:
            def __init__(self, transaction):
                self.transaction = transaction

            def __getattr__(self, name):
                return getattr(self.transaction, name)

            def append_event(self, event_type, **payload):
                self.transaction.append_event(event_type, **payload)
                raise RuntimeError("injected event write failure")

        class FailingEventRepository:
            def __getattr__(self, name):
                return getattr(base_store, name)

            @contextmanager
            def transaction(self, room_id):
                with base_store.transaction(room_id) as transaction:
                    yield FailingEventTransaction(transaction)

            def append_event(self, room_id, event_type, **payload):
                raise RuntimeError("injected event write failure")

        registry = RoomProviderRegistry(
            lock=self.lock,
            default_room_id="general",
        )
        service = RoomProviderSessionService(
            store=FailingEventRepository(),
            broker=self.broker,
            lock=self.lock,
            registry=registry,
            ensure_room=lambda room_id: base_store.create_room(room_id),
            publish_session_state=lambda room_id, session: self.published_sessions.append(
                (room_id, dict(session))
            ),
        )

        with self.assertRaisesRegex(RuntimeError, "injected event write failure"):
            service.create_provider_session("general", _spec())

        self.assertEqual(base_store.participant("general", "codex"), {})
        self.assertEqual(base_store.session("general", "codex"), {})
        self.assertEqual(
            [
                event
                for event in base_store.read_events("general")
                if event.get("type") == "agent_session_created"
            ],
            [],
        )
        self.assertFalse(registry.contains("general", "codex"))
        self.assertEqual(self.published_sessions, [])

        recovered = self.service.create_provider_session("general", _spec())

        self.assertEqual(recovered["session_id"], "codex")
        self.assertTrue(self.registry.contains("general", "codex"))

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

    def test_ensure_existing_api_session_persists_its_native_harness(self) -> None:
        definition = native_cli_provider_definition("deepseek")
        assert definition is not None
        builtin = definition.make_default_spec(
            agent_id="deepseek",
            display_name="DeepSeek",
            cwd=self.root,
        )
        native = definition.make_selected_spec(
            agent_id="deepseek",
            display_name="DeepSeek",
            cwd=self.root,
            model=builtin.model,
            reasoning_effort=builtin.reasoning_effort,
            variant=builtin.variant,
            execution_harness="claude",
            permission_mode=builtin.permission_mode,
            max_output_tokens=builtin.max_output_tokens,
        )
        self.service.create_provider_session("general", builtin)

        self.service.ensure_provider_session("general", native)

        session = self.store.session("general", "deepseek")
        self.assertEqual(session["execution_harness"], "claude")
        self.assertEqual(session["runtime_profile_key"], native.runtime_profile_key())

    def test_restore_repairs_reported_transport_overwrite_and_clears_profile_error(self) -> None:
        definition = native_cli_provider_definition("opencode")
        self.assertIsNotNone(definition)
        spec = definition.make_selected_spec(
            agent_id="opencode",
            display_name="OpenCode",
            cwd=self.root,
            model="opencode-go/glm-5.2",
            permission_mode="meeting_read_only",
        )
        self.service.create_provider_session("general", spec)
        self.store.update_session_fields(
            "general",
            "opencode",
            transport="http_sse",
            status="error",
            runtime_status="error",
            enabled=False,
            recovery_required=True,
            last_error="Stored Agent Session provider definition changed.",
            last_error_code="provider_definition_changed",
        )
        restored_registry = RoomProviderRegistry(
            lock=self.lock,
            default_room_id="general",
        )

        self._service(restored_registry).restore_server_owned_providers()

        restored = self.store.session("general", "opencode")
        self.assertEqual(restored["transport"], "http")
        self.assertEqual(restored["status"], "available")
        self.assertEqual(restored["runtime_status"], "stopped")
        self.assertFalse(restored["recovery_required"])
        self.assertEqual(restored["last_error"], "")
        self.assertEqual(restored["last_error_code"], "")
        self.assertTrue(restored_registry.contains("general", "opencode"))

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

    def test_configuring_a_disconnected_profile_makes_it_explicitly_startable(self) -> None:
        original = _spec("codex", model="model-a")
        self.service.create_provider_session("general", original)
        self.store.update_session_fields(
            "general",
            "codex",
            status="unavailable",
            runtime_status="disconnected",
            enabled=False,
            recovery_required=True,
            last_error="Stored Agent Session profile must be migrated before it can be reused.",
            last_error_code="profile_migration_required",
        )

        configured = self.service.configure_stopped_provider_profile(
            "general",
            _spec("codex", model="model-b"),
        )

        self.assertEqual(configured["status"], "available")
        self.assertEqual(configured["runtime_status"], "stopped")
        self.assertFalse(configured["enabled"])
        self.assertFalse(configured["recovery_required"])
        self.assertEqual(configured["last_error"], "")
        self.assertEqual(configured["last_error_code"], "")
        self.assertEqual(self.registry.provider("general", "codex").model, "model-b")

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
