from __future__ import annotations

import unittest

import agentsassemble.room_errors as compatibility_errors
import agentsassemble.room_types as compatibility_types
from agentsassemble.room import errors as owned_errors
from agentsassemble.room import types as owned_types


class RoomPackageTests(unittest.TestCase):
    def test_room_error_root_module_exports_owned_errors(self) -> None:
        self.assertIs(
            compatibility_errors.RoomCommandRejected,
            owned_errors.RoomCommandRejected,
        )

    def test_room_type_root_module_exports_owned_shapes(self) -> None:
        for name in (
            "AgentSession",
            "RoomActor",
            "RoomCommand",
            "RoomEvent",
            "RoomParticipant",
            "TurnAssignment",
        ):
            self.assertIs(
                getattr(compatibility_types, name),
                getattr(owned_types, name),
            )


if __name__ == "__main__":
    unittest.main()
