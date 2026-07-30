from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from agentsassemble.room.errors import RoomCommandRejected
from agentsassemble.room.global_settings import room_settings_revision
from agentsassemble.room.realtime import RoomRealtimeController
from tests.room_realtime_test_support import memory_room_access_services


HOST = {
    "agent_id": "operator-local",
    "display_name": "Host",
    "participant_type": "human",
    "client_type": "browser",
    "invite_scope": "read_write",
    "meeting_id": "general",
    "operator": True,
}


class _Catalog:
    def snapshot(self, *, refresh: bool = False) -> dict[str, object]:
        del refresh
        return {
            "status": "ready",
            "catalog_revision": "settings-concurrency",
            "providers": [],
        }

    def subscribe(self, _listener):
        return lambda: None


class RoomSettingsConcurrencyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        room_access = memory_room_access_services()
        self.controller = RoomRealtimeController(
            self.root,
            **room_access.controller_kwargs(),
            providers=[],
            provider_catalog=_Catalog(),
        )

    def tearDown(self) -> None:
        self.controller.close()
        self.temporary_directory.cleanup()

    def _command(self, request_id: str, payload: dict[str, object]):
        return self.controller.handle_command(
            HOST,
            {
                "op": "command",
                "request_id": request_id,
                "action": "room.settings.update",
                "payload": payload,
            },
        )

    def test_stale_revision_cannot_overwrite_an_already_committed_setting(self):
        original_revision = room_settings_revision(
            self.controller.store.room_settings("general")
        )

        first = self._command(
            "settings-first-writer",
            {
                "expected_revision": original_revision,
                "conversation_mode": "ambient",
            },
        )
        with self.assertRaises(RoomCommandRejected) as stale:
            self._command(
                "settings-stale-writer",
                {
                    "expected_revision": original_revision,
                    "topic": "stale overwrite",
                },
            )

        settings = self.controller.store.room_settings("general")
        settings_events = [
            event
            for event in self.controller.store.read_events("general")
            if event.get("type") == "room_settings_updated"
        ]
        self.assertEqual(stale.exception.code, "settings_conflict")
        self.assertEqual(settings["conversation_mode"], "ambient")
        self.assertEqual(settings["topic"], "")
        self.assertEqual(
            [event["id"] for event in settings_events],
            [first["result"]["event"]["id"]],
        )


if __name__ == "__main__":
    unittest.main()
