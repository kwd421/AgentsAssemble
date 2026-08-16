import tempfile
import unittest
from pathlib import Path

from agentsassemble.providers.capabilities import ProviderCapabilityCatalog
from agentsassemble.room.errors import RoomCommandRejected
from agentsassemble.room.global_settings import room_settings_revision
from agentsassemble.room.realtime import RoomRealtimeController
from tests.room_realtime_test_support import FakeBridgeManager, memory_room_access_services


def _identity(room_id: str, *, operator: bool) -> dict[str, object]:
    return {
        "agent_id": "same-browser-user",
        "display_name": "Same Browser User",
        "participant_type": "human",
        "client_type": "browser",
        "invite_scope": "read_write",
        "meeting_id": room_id,
        "principal_user_id": "user-shared-across-rooms",
        "principal_is_operator": operator,
        # A stale or client-controlled legacy flag must never override the
        # server-resolved room-scoped principal flag.
        "operator": True,
    }


def _provider_catalog() -> ProviderCapabilityCatalog:
    return ProviderCapabilityCatalog(
        runner=lambda _command, _timeout: (1, "", "unavailable in isolation test"),
        resolver=lambda _executable: None,
    )


class RoomScopeIsolationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        access = memory_room_access_services()
        self.controller = RoomRealtimeController(
            Path(self.temp.name),
            **access.controller_kwargs(),
            providers=[],
            bridge_manager=FakeBridgeManager(),
            provider_catalog=_provider_catalog(),
        )
        self.controller.ensure_room("room-a")
        self.controller.ensure_room("room-b")

    def tearDown(self) -> None:
        self.controller.close()
        self.temp.cleanup()

    def test_host_privilege_is_resolved_per_room_for_the_same_principal(self) -> None:
        host_in_room_a = _identity("room-a", operator=True)
        member_in_room_b = _identity("room-b", operator=False)

        updated = self.controller.handle_command(
            host_in_room_a,
            {
                "op": "command",
                "request_id": "settings-room-a",
                "action": "room.settings.update",
                "payload": {
                    "expected_revision": room_settings_revision(
                        self.controller.store.room_settings("room-a")
                    ),
                    "conversation_mode": "ambient",
                },
            },
        )

        self.assertTrue(updated["accepted"])
        self.assertTrue(self.controller.capabilities(host_in_room_a)["room.manage"])
        self.assertFalse(self.controller.capabilities(member_in_room_b)["room.manage"])
        with self.assertRaises(RoomCommandRejected) as rejected:
            self.controller.handle_command(
                member_in_room_b,
                {
                    "op": "command",
                    "request_id": "settings-smuggled-room-a",
                    "action": "room.settings.update",
                    "payload": {
                        "meeting_id": "room-a",
                        "expected_revision": room_settings_revision(
                            self.controller.store.room_settings("room-a")
                        ),
                        "conversation_mode": "continuous",
                    },
                },
            )
        self.assertEqual(rejected.exception.code, "permission_denied")
        self.assertEqual(
            self.controller.store.room_settings("room-a")["conversation_mode"],
            "ambient",
        )
        self.assertNotEqual(
            self.controller.store.room_settings("room-b")["conversation_mode"],
            "continuous",
        )

    def test_payload_cannot_redirect_a_member_write_to_another_room(self) -> None:
        member_in_room_b = _identity("room-b", operator=False)

        response = self.controller.handle_command(
            member_in_room_b,
            {
                "op": "command",
                "request_id": "message-room-b",
                "action": "message.send",
                "payload": {
                    "meeting_id": "room-a",
                    "room_id": "room-a",
                    "content": "room-b only",
                },
            },
        )

        self.assertEqual(response["result"]["event"]["room_id"], "room-b")
        self.assertTrue(
            any(
                event.get("content") == "room-b only"
                for event in self.controller.store.read_events("room-b")
            )
        )
        self.assertFalse(
            any(
                event.get("content") == "room-b only"
                for event in self.controller.store.read_events("room-a")
            )
        )


if __name__ == "__main__":
    unittest.main()
