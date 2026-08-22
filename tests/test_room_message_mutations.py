from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from agentsassemble.features.message_search.service import MessageSearchService
from agentsassemble.room.attachments import store_uploaded_attachment
from agentsassemble.room.errors import RoomCommandRejected
from agentsassemble.room.realtime import RoomRealtimeController
from tests.room_realtime_test_support import FakeBridgeManager, memory_room_access_services
from tests.test_room_realtime import HOST, _test_provider_catalog


class RoomMessageMutationCommandTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        room_access = memory_room_access_services()
        self.controller = RoomRealtimeController(
            self.root,
            **room_access.controller_kwargs(),
            providers=[],
            bridge_manager=FakeBridgeManager(),
            provider_catalog=_test_provider_catalog(),
        )
        self.controller.connect(HOST)

    def tearDown(self) -> None:
        self.controller.close()
        self.temporary_directory.cleanup()

    def _command(
        self,
        request_id: str,
        action: str,
        payload: dict[str, object],
        identity: dict[str, object] | None = None,
    ) -> dict[str, object]:
        return self.controller.handle_command(
            identity or HOST,
            {
                "op": "command",
                "request_id": request_id,
                "action": action,
                "payload": payload,
            },
        )

    def test_message_owner_can_edit_and_delete_without_creating_a_second_message(
        self,
    ) -> None:
        attachment = store_uploaded_attachment(
            self.root,
            {
                "room_id": "general",
                "filename": "notes.txt",
                "content_type": "text/plain",
                "data_base64": "ZHJhZnQ=",
            },
        )
        sent = self._command(
            "message-mutation-send",
            "message.send",
            {"content": "obsoletebody", "attachments": [{"id": attachment["id"]}]},
        )
        event_id = sent["result"]["event"]["id"]
        self.controller.store.pin_message(
            "general",
            "lobby",
            event_id,
            pinned_by="operator-local",
        )
        search = MessageSearchService(self.root)
        search.sync_lobby(self.controller.store, "general")
        self.assertEqual(
            [
                item["event_id"]
                for item in search.search(
                    "general",
                    query="obsoletebody",
                    channel_ids=["lobby"],
                )["results"]
            ],
            [event_id],
        )

        edited = self._command(
            "message-mutation-edit",
            "message.edit",
            {"event_id": event_id, "content": "final"},
        )

        stored = self.controller.store.event_by_id("general", event_id)
        self.assertEqual(stored["content"], "final")
        self.assertTrue(stored["edited_at"])
        self.assertEqual(edited["result"]["event"]["target_event_id"], event_id)
        search.sync_lobby(self.controller.store, "general")
        self.assertEqual(
            search.search(
                "general",
                query="obsoletebody",
                channel_ids=["lobby"],
            )["results"],
            [],
        )
        self.assertEqual(
            [
                item["event_id"]
                for item in search.search(
                    "general",
                    query="final",
                    channel_ids=["lobby"],
                )["results"]
            ],
            [event_id],
        )
        self.assertEqual(
            len(
                [
                    event
                    for event in self.controller.store.read_events("general")
                    if event.get("type") == "message_final"
                ]
            ),
            1,
        )

        guest = {**HOST, "agent_id": "guest", "operator": False}
        self.controller.connect(guest)
        with self.assertRaises(RoomCommandRejected) as denied:
            self._command(
                "message-mutation-denied",
                "message.edit",
                {"event_id": event_id, "content": "hijacked"},
                guest,
            )
        self.assertEqual(denied.exception.code, "permission_denied")

        deleted = self._command(
            "message-mutation-delete",
            "message.delete",
            {"event_id": event_id},
        )
        duplicate_delete = self._command(
            "message-mutation-delete",
            "message.delete",
            {"event_id": event_id},
        )
        tombstone = self.controller.store.event_by_id("general", event_id)
        self.assertTrue(tombstone["message_deleted"])
        self.assertEqual(tombstone["content"], "")
        self.assertEqual(deleted["result"]["event"]["target_event_id"], event_id)
        self.assertTrue(duplicate_delete["deduplicated"])
        self.assertEqual(self.controller.store.pinned_messages("general", "lobby"), [])
        self.assertFalse((self.root / "attachments" / str(attachment["id"])).exists())
        search.sync_lobby(self.controller.store, "general")
        self.assertEqual(
            search.search("general", query="final", channel_ids=["lobby"])["results"],
            [],
        )

    def test_agent_session_owner_can_delete_but_cannot_edit_its_final_message(
        self,
    ) -> None:
        owner = {
            **HOST,
            "agent_id": "owner-human",
            "display_name": "Owner",
            "operator": False,
        }
        self.controller.connect(owner)
        self.controller.store.upsert_participant(
            "general",
            {
                "participant_id": "owned-agent",
                "participant_type": "agent",
                "display_name": "Owned Agent",
                "owner_id": "owner-human",
                "status": "joined",
            },
        )
        message = self.controller.store.append_event(
            "general",
            "message_final",
            participant_id="owned-agent",
            participant_type="agent",
            content="agent final",
        )

        with self.assertRaises(RoomCommandRejected) as denied:
            self._command(
                "agent-owner-edit",
                "message.edit",
                {"event_id": message["id"], "content": "rewritten"},
                owner,
            )
        self.assertEqual(denied.exception.code, "permission_denied")

        deleted = self._command(
            "agent-owner-delete",
            "message.delete",
            {"event_id": message["id"]},
            owner,
        )

        self.assertEqual(deleted["result"]["event"]["target_event_id"], message["id"])
        stored = self.controller.store.event_by_id("general", str(message["id"]))
        self.assertTrue(stored["message_deleted"])
        self.assertEqual(stored["content"], "")

    def test_deleting_vote_removes_poll_totals_and_ballot_identity(self) -> None:
        poll = self._command(
            "vote-delete-poll",
            "message.send",
            {
                "kind": "vote",
                "vote_question": "Ship it?",
                "vote_options": ["Yes", "No"],
            },
        )["result"]["event"]
        guest = {
            **HOST,
            "agent_id": "vote-guest",
            "display_name": "Vote Guest",
            "operator": False,
        }
        self.controller.connect(guest)
        ballot = self._command(
            "vote-delete-ballot",
            "message.send",
            {
                "kind": "vote_cast",
                "vote_id": poll["id"],
                "vote_choice": "Yes",
            },
            guest,
        )["result"]["event"]
        self.assertEqual(
            self._command(
                "vote-delete-summary-before",
                "room.vote.summary",
                {"vote_id": poll["id"]},
            )["result"]["total_votes"],
            1,
        )

        self._command(
            "vote-delete-command",
            "message.delete",
            {"event_id": poll["id"]},
        )

        self.assertEqual(self.controller.store.vote_events("general", poll["id"]), [])
        deleted_poll = self.controller.store.event_by_id("general", poll["id"])
        deleted_ballot = self.controller.store.event_by_id("general", ballot["id"])
        self.assertTrue(deleted_poll["message_deleted"])
        self.assertTrue(deleted_ballot["message_deleted"])
        self.assertEqual(deleted_ballot.get("vote_id"), "")
        self.assertEqual(deleted_ballot.get("vote_choice"), "")
        self.assertEqual(deleted_ballot.get("actor"), {})
        self.assertEqual(deleted_ballot.get("actor_id"), "")
        self.assertEqual(deleted_ballot.get("participant_id"), "")

        with self.assertRaises(RoomCommandRejected) as missing_summary:
            self._command(
                "vote-delete-summary-after",
                "room.vote.summary",
                {"vote_id": poll["id"]},
            )
        self.assertEqual(missing_summary.exception.code, "vote_not_found")
        with self.assertRaises(RoomCommandRejected) as missing_vote:
            self._command(
                "vote-delete-late-ballot",
                "message.send",
                {
                    "kind": "vote_cast",
                    "vote_id": poll["id"],
                    "vote_choice": "No",
                },
                guest,
            )
        self.assertEqual(missing_vote.exception.code, "vote_not_found")


if __name__ == "__main__":
    unittest.main()
