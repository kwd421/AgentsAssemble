from __future__ import annotations

import tempfile
import threading
import unittest
from pathlib import Path

from agentsassemble.persistence.local.room.repository import RoomStore
from agentsassemble.providers.launch_specs import NativeCliProviderSpec
from agentsassemble.room.agent_profiles import RoomAgentProfileService
from agentsassemble.room.command_uow import RoomCommandUnitOfWork
from agentsassemble.room.errors import RoomCommandRejected
from agentsassemble.room.provider_registry import RoomProviderRegistry


class RoomAgentProfileServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.store = RoomStore(self.root)
        self.store.create_room("general")
        self.store.upsert_participant(
            "general",
            {
                "participant_id": "codex",
                "display_name": "Codex",
                "avatar_image_url": "/old-avatar",
                "participant_type": "agent",
            },
        )
        self.store.upsert_session(
            "general",
            {
                "session_id": "codex",
                "participant_id": "codex",
                "display_name": "Codex",
                "avatar_image_url": "/old-avatar",
            },
        )
        self.registry = RoomProviderRegistry(
            lock=threading.RLock(),
            default_room_id="general",
        )
        self.registry.register(
            "general",
            NativeCliProviderSpec(
                agent_id="codex",
                display_name="Codex",
                command=("codex",),
                provider_kind="codex",
            ),
        )
        self.published_sessions: list[tuple[str, dict[str, object]]] = []
        self.service = RoomAgentProfileService(
            store=self.store,
            provider_registry=self.registry,
            publish_session_state=lambda room_id, session: self.published_sessions.append(
                (room_id, dict(session))
            ),
        )

    def tearDown(self) -> None:
        self.store.close()
        self.temporary_directory.cleanup()

    def test_profile_update_keeps_participant_session_registry_and_event_aligned(self) -> None:
        payload = {
            "agent_id": "codex",
            "display_name": "Luna",
            "avatar_image_url": "",
        }
        with RoomCommandUnitOfWork(
            self.store,
            room_id="general",
            principal_id="browser:host",
            request_id="profile-update",
            action="agent.configure",
            payload=payload,
        ) as unit:
            result = self.service.update_in_unit("codex", payload, unit=unit)
            unit.build_ack(result)
            unit.record_ack()
        ack = unit.resolved_ack()

        self.service.apply_after_commit("general", ack)

        participant = self.store.participant("general", "codex")
        session = self.store.session("general", "codex")
        self.assertEqual(participant["display_name"], "Luna")
        self.assertEqual(participant["avatar_image_url"], "")
        self.assertEqual(session["display_name"], "Luna")
        self.assertEqual(session["avatar_image_url"], "")
        self.assertEqual(self.registry.provider("general", "codex").display_name, "Luna")
        update = [
            event
            for event in self.store.read_events("general")
            if event["type"] == "participant_updated"
        ][-1]
        self.assertEqual(update["display_name"], "Luna")
        self.assertEqual(update["avatar_image_url"], "")
        self.assertEqual(self.published_sessions[-1][1]["display_name"], "Luna")

    def test_profile_update_rejects_missing_participant_without_partial_write(self) -> None:
        self.store.upsert_session(
            "general",
            {
                "session_id": "orphan",
                "participant_id": "orphan",
                "display_name": "Orphan",
            },
        )
        payload = {"agent_id": "orphan", "display_name": "Luna"}

        with self.assertRaises(RoomCommandRejected) as raised:
            with RoomCommandUnitOfWork(
                self.store,
                room_id="general",
                principal_id="browser:host",
                request_id="missing-participant",
                action="agent.configure",
                payload=payload,
            ) as unit:
                self.service.update_in_unit("orphan", payload, unit=unit)

        self.assertEqual(raised.exception.code, "not_found")
        self.assertEqual(self.store.session("general", "orphan")["display_name"], "Orphan")

    def test_deduplicated_ack_does_not_repeat_post_commit_side_effects(self) -> None:
        self.service.apply_after_commit(
            "general",
            {
                "deduplicated": True,
                "result": {
                    "agent_session": {
                        "session_id": "codex",
                        "display_name": "Ignored",
                    }
                },
            },
        )

        self.assertEqual(self.registry.provider("general", "codex").display_name, "Codex")
        self.assertEqual(self.published_sessions, [])


if __name__ == "__main__":
    unittest.main()
