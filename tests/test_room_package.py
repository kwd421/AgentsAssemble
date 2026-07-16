from __future__ import annotations

import unittest

import agentsassemble.room_commands as compatibility_commands
import agentsassemble.room_errors as compatibility_errors
import agentsassemble.room_projection as compatibility_projection
import agentsassemble.room_types as compatibility_types
from agentsassemble.room import commands as owned_commands
from agentsassemble.room import errors as owned_errors
from agentsassemble.room import projection as owned_projection
from agentsassemble.room import types as owned_types


class RoomPackageTests(unittest.TestCase):
    def test_room_command_root_module_exports_owned_policy(self) -> None:
        for name in (
            "ROOM_COMMAND_ACTIONS",
            "ParsedRoomCommand",
            "RoomCommandValidationError",
            "capabilities_for_identity",
            "parse_room_command",
        ):
            self.assertIs(
                getattr(compatibility_commands, name),
                getattr(owned_commands, name),
            )

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

    def test_room_projection_root_module_exports_owned_projection(self) -> None:
        for name in (
            "PUBLIC_ACTIVITY_LABELS",
            "merged_latency",
            "public_activity",
            "public_event",
            "public_participant",
            "public_runtime_diagnostics",
            "public_session",
            "runtime_diagnostic_fields",
        ):
            self.assertIs(
                getattr(compatibility_projection, name),
                getattr(owned_projection, name),
            )


if __name__ == "__main__":
    unittest.main()
