from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from agentsassemble.persistence.local.room.repository import RoomStore
from agentsassemble.room.connections import RoomConnectionService
from agentsassemble.room.event_broker import RoomEventBroker
from agentsassemble.room.provider_requests import fail_pending_provider_request
from agentsassemble.web.routes.runtime import rolling_restart_blockers


class RoomConnectionServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.store = RoomStore(self.root)
        self.broker = RoomEventBroker()
        self.external_bridges: list[tuple[str, dict[str, object]]] = []
        self.published_sessions: list[tuple[str, dict[str, object]]] = []
        self.service = RoomConnectionService(
            store=self.store,
            broker=self.broker,
            ensure_room=lambda room_id: self.store.create_room(room_id),
            ensure_external_bridge_session=lambda room_id, identity: self.external_bridges.append(
                (room_id, dict(identity))
            ),
            reconcile_session_attention=lambda _room_id, _session, *, pending_event_ids: {
                "pending_event_ids": list(pending_event_ids)
            },
            publish_session_state=lambda room_id, session: self.published_sessions.append(
                (room_id, dict(session))
            ),
            fail_pending_provider_request=lambda room_id, session_id, *, reason_code: (
                fail_pending_provider_request(
                    self.store,
                    room_id,
                    session_id,
                    reason_code=reason_code,
                )
            ),
        )

    def tearDown(self) -> None:
        self.broker.close()
        self.store.close()
        self.temporary_directory.cleanup()

    def test_browser_connect_creates_and_then_updates_one_human_participant(self) -> None:
        identity = {
            "meeting_id": "general",
            "agent_id": "member",
            "display_name": "First Name",
            "client_type": "browser",
            "operator": False,
        }

        first = self.service.connect(identity)
        second = self.service.connect({**identity, "display_name": "Second Name"})

        self.assertEqual(
            self.store.participant("general", "member")["display_name"],
            "Second Name",
        )
        self.assertEqual(
            len(
                [
                    participant
                    for participant in self.store.participants("general")
                    if participant["participant_id"] == "member"
                ]
            ),
            1,
        )
        self.service.disconnect(first)
        self.service.disconnect(second)

    def test_agent_bridge_connect_registers_external_session_before_broker_connection(self) -> None:
        identity = {
            "meeting_id": "general",
            "agent_id": "bridge",
            "session_id": "bridge",
            "client_type": "agent_bridge",
            "bridge_generation": 1,
        }

        channel = self.service.connect(identity)

        self.assertEqual(self.external_bridges[0][0], "general")
        self.assertNotIn("connection_id", self.external_bridges[0][1])
        self.assertIs(self.broker.channel(channel.connection_id), channel)
        self.service.disconnect(channel)

    def test_active_bridge_disconnect_marks_the_session_and_participant_detached(self) -> None:
        self.store.create_room("general")
        self.store.upsert_participant(
            "general",
            {
                "participant_id": "bridge",
                "display_name": "Bridge",
                "participant_type": "agent",
                "status": "joined",
            },
        )
        self.store.upsert_session(
            "general",
            {
                "session_id": "bridge",
                "participant_id": "bridge",
                "display_name": "Bridge",
                "enabled": True,
                "runtime_status": "busy",
                "provider_session_active": True,
                "active_turn_id": "turn-active",
                "active_source_event_id": "event-active",
                "turn_phase": "thinking",
                "input_up_to_event_id": "event-active",
                "input_up_to_seq": 4,
                "inflight_event_ids": ["event-active"],
                "pending_event_ids": ["event-pending"],
                "pending_provider_request": {
                    "provider_request_id": "approval-1",
                    "participant_id": "bridge",
                    "owner_id": "operator-local",
                    "status": "open",
                },
            },
        )
        identity = {
            "meeting_id": "general",
            "agent_id": "bridge",
            "session_id": "bridge",
            "client_type": "agent_bridge",
            "bridge_generation": 1,
        }
        channel = self.service.connect(identity)
        self.broker.activate_bridge(channel)

        self.service.disconnect(channel)

        session = self.store.session("general", "bridge")
        self.assertEqual(session["runtime_status"], "disconnected")
        self.assertFalse(session["provider_session_active"])
        self.assertEqual(session["active_turn_id"], "")
        self.assertEqual(session["active_source_event_id"], "")
        self.assertEqual(session["turn_phase"], "")
        self.assertEqual(session["input_up_to_event_id"], "")
        self.assertEqual(session["input_up_to_seq"], 0)
        self.assertEqual(session["inflight_event_ids"], [])
        self.assertEqual(
            session["pending_event_ids"],
            ["event-active", "event-pending"],
        )
        self.assertEqual(session["pending_provider_request"], {})
        self.assertEqual(rolling_restart_blockers(self.store), [])
        self.assertEqual(
            self.store.participant("general", "bridge")["status"],
            "detached",
        )
        self.assertEqual(self.published_sessions[-1][1]["session_id"], "bridge")
        self.assertIn(
            "session_detached",
            [event["type"] for event in self.store.read_events("general")],
        )
        failed = next(
            event
            for event in self.store.read_events("general")
            if event["type"] == "provider_request_resolved"
        )
        self.assertEqual(failed["provider_request"]["status"], "failed")
        self.assertEqual(
            failed["reason_code"],
            "provider_request_bridge_disconnected",
        )


if __name__ == "__main__":
    unittest.main()
