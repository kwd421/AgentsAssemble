from __future__ import annotations

import tempfile
import threading
import unittest
from pathlib import Path

from agentsassemble.persistence.local.room.repository import RoomStore
from agentsassemble.providers.launch_specs import native_cli_provider_definition
from agentsassemble.room.agent_reactivation import RoomAgentReactivationService
from agentsassemble.room.errors import RoomCommandRejected
from agentsassemble.room.event_broker import RoomEventBroker
from agentsassemble.room.provider_registry import RoomProviderRegistry
from agentsassemble.room.provider_sessions import RoomProviderSessionService


class RoomAgentReactivationServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.store = RoomStore(self.root)
        self.store.create_room("general")
        self.lock = threading.RLock()
        self.broker = RoomEventBroker()
        self.registry = RoomProviderRegistry(
            lock=self.lock,
            default_room_id="general",
        )
        self.published_sessions: list[tuple[str, dict[str, object]]] = []
        self.provider_sessions = RoomProviderSessionService(
            store=self.store,
            broker=self.broker,
            lock=self.lock,
            registry=self.registry,
            ensure_room=lambda room_id: self.store.create_room(room_id),
            publish_session_state=lambda room_id, session: self.published_sessions.append(
                (room_id, dict(session))
            ),
        )
        definition = native_cli_provider_definition("codex")
        assert definition is not None
        self.spec = definition.make_default_spec(
            agent_id="codex",
            display_name="Codex",
            cwd=self.root,
        )
        self.provider_sessions.ensure_provider_session("general", self.spec)
        self.registry.remove("general", "codex")
        self.store.update_participant_fields(
            "general",
            "codex",
            status="kicked",
        )
        self.store.update_session_fields(
            "general",
            "codex",
            status="available",
            runtime_status="stopped",
            enabled=False,
            active_turn_id="",
            bridge_handle_id="",
        )
        self.start_calls: list[tuple[str, str, str]] = []
        self.service = RoomAgentReactivationService(
            store=self.store,
            broker=self.broker,
            lock=self.lock,
            provider_registry=self.registry,
            ensure_provider_session=self.provider_sessions.ensure_provider_session,
            publish_session_state=lambda room_id, session: self.published_sessions.append(
                (room_id, dict(session))
            ),
            start_agent=self._start_agent,
        )

    def tearDown(self) -> None:
        self.broker.close()
        self.store.close()
        self.temporary_directory.cleanup()

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

    def test_readd_restores_registry_and_can_start_the_session(self) -> None:
        result = self.service.readd(
            "general",
            "codex",
            {"start": True},
            server_url="http://127.0.0.1:8765",
            ticket_issuer=None,
        )

        self.assertEqual(result["status"], "readded")
        self.assertEqual(result["participant"]["status"], "detached")
        self.assertTrue(self.registry.contains("general", "codex"))
        self.assertEqual(result["start"]["status"], "starting")
        self.assertEqual(
            self.start_calls,
            [("general", "codex", "http://127.0.0.1:8765")],
        )
        self.assertIn(
            "agent_session_reactivated",
            [event["type"] for event in self.store.read_events("general")],
        )
        self.assertEqual(self.published_sessions[-1][1]["session_id"], "codex")

    def test_external_session_must_reconnect_instead_of_readd(self) -> None:
        self.store.update_session_fields(
            "general",
            "codex",
            process_ownership="external",
        )

        with self.assertRaises(RoomCommandRejected) as raised:
            self.service.readd(
                "general",
                "codex",
                {},
                server_url="",
                ticket_issuer=None,
            )

        self.assertEqual(raised.exception.code, "runtime_unavailable")
        self.assertFalse(self.registry.contains("general", "codex"))

    def test_incomplete_stored_profile_fails_closed(self) -> None:
        self.store.update_session_fields(
            "general",
            "codex",
            model="",
        )

        with self.assertRaises(RoomCommandRejected) as raised:
            self.service.readd(
                "general",
                "codex",
                {},
                server_url="",
                ticket_issuer=None,
            )

        self.assertEqual(raised.exception.code, "profile_incomplete")
        self.assertFalse(self.registry.contains("general", "codex"))


if __name__ == "__main__":
    unittest.main()
