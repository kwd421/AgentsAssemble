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


__all__ = ["MessageMutationRepositoryContractMixin"]
