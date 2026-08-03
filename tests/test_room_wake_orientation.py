from __future__ import annotations

import unittest

from agentsassemble.providers.room_portal import room_wake_orientation


class RoomWakeOrientationTests(unittest.TestCase):
    def test_local_openai_runtime_is_told_about_its_actual_room_tools(self) -> None:
        orientation = room_wake_orientation(
            "ollama_api",
            observation_kind="ambient_observation",
        )

        self.assertIn("`read_discussion` MCP tool", orientation)
        self.assertIn("`publish_message` MCP tool", orientation)
        self.assertIn("`roll_dice`", orientation)
        self.assertNotIn("provider's private room read interface", orientation)


if __name__ == "__main__":
    unittest.main()
