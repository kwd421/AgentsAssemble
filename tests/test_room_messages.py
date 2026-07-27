from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from agentsassemble.persistence.local.room.repository import RoomStore
from agentsassemble.room.attachments import FileAttachmentStore, store_uploaded_attachment
from agentsassemble.room.command_uow import RoomCommandUnitOfWork
from agentsassemble.room.errors import RoomCommandRejected
from agentsassemble.room.messages import RoomMessageService


class _UnitWithoutCanonicalMute:
    def __init__(self) -> None:
        self.room_id = "general"
        self.appended: list[tuple[str, dict[str, object]]] = []

    def participant(self, participant_id: str) -> dict[str, object]:
        return {"participant_id": participant_id, "status": "joined"}

    def append_event(
        self,
        event_type: str,
        **payload: object,
    ) -> dict[str, object]:
        self.appended.append((event_type, dict(payload)))
        return {"id": "event-1", "seq": 1, "type": event_type, **payload}


class RoomMessageServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.store = RoomStore(self.root)
        self.store.create_room("general")
        self.store.upsert_participant(
            "general",
            {
                "participant_id": "host",
                "display_name": "Host",
                "participant_type": "human",
                "status": "joined",
            },
        )
        self.service = RoomMessageService(FileAttachmentStore(self.root))
        self.identity = {
            "agent_id": "host",
            "display_name": "Host",
        }

    def tearDown(self) -> None:
        self.store.close()
        self.temporary_directory.cleanup()

    def _send(self, payload: dict[str, object]) -> dict[str, object]:
        with RoomCommandUnitOfWork(
            self.store,
            room_id="general",
            principal_id="browser:host",
            request_id=str(payload.get("request_id") or "message"),
            action="message.send",
            payload=payload,
        ) as unit:
            result = self.service.send_in_unit(
                self.identity,
                payload,
                unit=unit,
                compatibility_muted=False,
                room_events=self.store.read_events("general"),
            )
            unit.build_ack(result)
            unit.record_ack()
        return result

    def test_message_appends_canonical_human_event_with_media_and_vote_fields(self) -> None:
        attachment = store_uploaded_attachment(
            self.root,
            {
                "room_id": "general",
                "filename": "diagram.png",
                "content_type": "image/png",
                "data_base64": "aW1hZ2U=",
            },
        )
        result = self._send(
            {
                "content": "hello",
                "attachments": [{"id": attachment["id"]}],
                "vote_id": "vote-1",
                "target_agent_id": "codex",
            }
        )

        event = result["event"]
        self.assertEqual(event["type"], "message_final")
        self.assertEqual(event["participant_id"], "host")
        self.assertEqual(event["display_name"], "Host")
        self.assertEqual(event["attachments"][0]["id"], attachment["id"])
        self.assertNotIn("room_id", event["attachments"][0])
        self.assertEqual(event["vote_id"], "vote-1")
        self.assertEqual(event["target_agent_id"], "codex")
        self.assertEqual(event["relay_depth"], 0)

    def test_empty_plain_message_is_rejected_but_vote_event_is_allowed(self) -> None:
        with self.assertRaises(RoomCommandRejected) as raised:
            self._send({"request_id": "empty", "content": ""})

        self.assertEqual(raised.exception.code, "empty")
        vote = self._send(
            {
                "request_id": "vote",
                "kind": "vote",
                "vote_question": "Choose",
                "vote_options": ["A", "B"],
            }
        )
        self.assertEqual(vote["event"]["message_kind"], "vote")

    def test_attachment_only_message_is_allowed_and_must_own_the_attachment(self) -> None:
        attachment = store_uploaded_attachment(
            self.root,
            {
                "room_id": "general",
                "filename": "photo.png",
                "content_type": "image/png",
                "data_base64": "cGhvdG8=",
            },
        )

        result = self._send(
            {
                "request_id": "attachment-only",
                "content": "",
                "attachments": [{"id": attachment["id"]}],
            }
        )

        self.assertEqual(result["event"].get("content", ""), "")
        self.assertEqual(result["event"]["attachments"][0]["id"], attachment["id"])

        foreign = store_uploaded_attachment(
            self.root,
            {
                "room_id": "another-room",
                "filename": "private.png",
                "content_type": "image/png",
                "data_base64": "cHJpdmF0ZQ==",
            },
        )
        with self.assertRaises(RoomCommandRejected) as raised:
            self._send(
                {
                    "request_id": "foreign-attachment",
                    "attachments": [{"id": foreign["id"]}],
                }
            )
        self.assertEqual(raised.exception.code, "invalid_attachment")

    def test_left_participant_cannot_append_a_message(self) -> None:
        self.store.update_participant_fields(
            "general",
            "host",
            status="left",
        )

        with self.assertRaises(RoomCommandRejected) as raised:
            self._send({"content": "blocked"})

        self.assertEqual(raised.exception.code, "session_revoked")

    def test_compatibility_mute_is_used_only_when_canonical_field_is_absent(self) -> None:
        unit = _UnitWithoutCanonicalMute()

        with self.assertRaises(RoomCommandRejected) as raised:
            RoomMessageService(FileAttachmentStore(self.root)).send_in_unit(
                self.identity,
                {"content": "blocked"},
                unit=unit,  # type: ignore[arg-type]
                compatibility_muted=True,
                room_events=[],
            )

        self.assertEqual(raised.exception.code, "muted")
        self.assertEqual(unit.appended, [])


if __name__ == "__main__":
    unittest.main()
