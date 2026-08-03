"""Shared command-boundary contract for room tool-mode enforcement."""

from agentsassemble.room.realtime import RoomCommandRejected


class RoomToolModeRealtimeContract:
    controller: object

    def test_random_room_command_requires_tabletop_mode_before_publishing_result(self):
        before = self.controller.store.latest_event_sequence("general")
        with self.assertRaises(RoomCommandRejected) as unavailable:
            self._command(
                "chat-dice",
                "room.random.roll",
                {"notation": "1d6", "reason": "initiative"},
            )

        self.assertEqual(unavailable.exception.code, "room_tool_unavailable")
        self.assertEqual(self.controller.store.latest_event_sequence("general"), before)

        self._command(
            "enable-tabletop",
            "room.settings.update",
            self._settings_update(tool_mode="tabletop"),
        )
        rolled = self._command(
            "tabletop-dice",
            "room.random.roll",
            {"notation": "1d6", "reason": "initiative"},
        )

        event = rolled["result"]["event"]
        self.assertEqual(event["type"], "message_final")
        self.assertEqual(event["message_kind"], "system")
        self.assertEqual(event["metadata"]["operation"], "roll_dice")
        self.assertEqual(event["metadata"]["details"]["notation"], "1d6")


__all__ = ["RoomToolModeRealtimeContract"]
