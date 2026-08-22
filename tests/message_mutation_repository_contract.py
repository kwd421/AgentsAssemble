from __future__ import annotations

from typing import cast
from unittest import TestCase

from agentsassemble.room.repository import RoomRepository


class MessageMutationRepositoryContractMixin:
    """Backend-neutral persistence required by canonical message mutation."""

    repository: RoomRepository

    def test_event_payload_updates_are_durable_and_transactional(self) -> None:
        self.repository.create_room("event-update")
        with self.repository.transaction("event-update") as transaction:
            event = transaction.append_event(
                "message_final",
                participant_id="human-a",
                participant_type="human",
                content="draft",
            )
            updated = transaction.update_event_fields(
                str(event["id"]),
                content="final",
                edited_at="2026-08-22T00:00:00Z",
            )

        case = cast(TestCase, self)
        case.assertEqual(updated["content"], "final")
        case.assertEqual(
            self.repository.event_by_id("event-update", str(event["id"]))["content"],
            "final",
        )

        with case.assertRaisesRegex(RuntimeError, "abort event update"):
            with self.repository.transaction("event-update") as transaction:
                transaction.update_event_fields(str(event["id"]), content="rollback")
                raise RuntimeError("abort event update")

        stored = self.repository.event_by_id("event-update", str(event["id"]))
        case.assertEqual(stored["content"], "final")
        case.assertEqual(stored["seq"], event["seq"])
        case.assertEqual(stored["actor"], event["actor"])

    def test_vote_ballot_identity_can_only_be_redacted_with_its_tombstone(self) -> None:
        self.repository.create_room("vote-redaction")
        case = cast(TestCase, self)
        with self.repository.transaction("vote-redaction") as transaction:
            poll = transaction.append_event(
                "message_final",
                participant_id="host-a",
                participant_type="human",
                message_kind="vote",
                vote_question="Ship?",
                vote_options=["Yes", "No"],
            )
            ballot = transaction.append_event(
                "message_final",
                participant_id="guest-a",
                participant_type="human",
                message_kind="vote_cast",
                vote_id=poll["id"],
                vote_choice="Yes",
            )
            case.assertEqual(
                [event["id"] for event in transaction.vote_events(str(poll["id"]))],
                [poll["id"], ballot["id"]],
            )
            with case.assertRaisesRegex(ValueError, "actor cannot be changed"):
                transaction.update_event_fields(str(ballot["id"]), actor={})
            transaction.update_event_fields(
                str(ballot["id"]),
                actor={},
                actor_id="",
                participant_id="",
                vote_id="",
                vote_choice="",
                message_deleted=True,
            )
            transaction.update_event_fields(
                str(poll["id"]),
                vote_question="",
                vote_options=[],
                message_deleted=True,
            )

        case.assertEqual(self.repository.vote_events("vote-redaction", str(poll["id"])), [])
        stored = self.repository.event_by_id("vote-redaction", str(ballot["id"]))
        case.assertEqual(stored["actor"], {})
        case.assertEqual(stored["vote_id"], "")
        case.assertTrue(stored["message_deleted"])


__all__ = ["MessageMutationRepositoryContractMixin"]
