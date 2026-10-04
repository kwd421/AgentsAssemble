"""Backend-neutral contract for durable channel message pins."""
from __future__ import annotations

from typing import cast
from unittest import TestCase

from agentsassemble.room.repository import RoomRepository


class MessagePinRepositoryContractMixin:
    repository: RoomRepository

    def test_message_pins_are_channel_scoped_durable_pointers(self) -> None:
        self.repository.create_room("pin-contract-room", label="Pins")
        first = self.repository.append_event(
            "pin-contract-room",
            "message_final",
            participant_id="human-a",
            participant_type="human",
            actor_id="human-a",
            actor_type="human",
            display_name="Human A",
            content="keep this message",
            message_kind="message",
        )
        second = self.repository.append_event(
            "pin-contract-room",
            "message_final",
            participant_id="human-b",
            participant_type="human",
            actor_id="human-b",
            actor_type="human",
            display_name="Human B",
            content="pin in another channel",
            message_kind="message",
        )

        self.repository.pin_message(
            "pin-contract-room", "lobby", str(first["id"]), pinned_by="human-a"
        )
        self.repository.pin_message(
            "pin-contract-room", "design", str(second["id"]), pinned_by="human-a"
        )

        case = cast(TestCase, self)
        case.assertEqual(
            [
                pin["event_id"]
                for pin in self.repository.pinned_messages("pin-contract-room", "lobby")
            ],
            [first["id"]],
        )
        case.assertEqual(
            [
                pin["event_id"]
                for pin in self.repository.pinned_messages("pin-contract-room", "design")
            ],
            [second["id"]],
        )
        case.assertTrue(
            self.repository.unpin_message(
                "pin-contract-room", "lobby", str(first["id"])
            )
        )
        case.assertEqual(self.repository.pinned_messages("pin-contract-room", "lobby"), [])


__all__ = ["MessagePinRepositoryContractMixin"]
