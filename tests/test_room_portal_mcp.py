import tempfile
import unittest
from pathlib import Path
import re

import anyio
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

from agentsassemble.providers.room_portal import RoomPortal
from agentsassemble.providers.room_portal_mcp import room_portal_mcp_settings


class RoomPortalMcpTests(unittest.TestCase):
    def test_stdio_tools_read_and_publish_through_private_portal(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            portal = RoomPortal(Path(temp_dir) / "portal", participant_id="codex")
            portal.prepare()
            portal.ingest_frame(
                {
                    "room_settings": {"tool_mode": "tabletop"},
                    "participants": [
                        {
                            "participant_id": "sonnet",
                            "participant_type": "agent",
                            "display_name": "Sonnet",
                        }
                    ],
                }
            )
            portal.begin_observation("wake-a", input_up_to_seq=7)

            tool_names, read_error, publish_error, roll, choice = anyio.run(
                self._call_tools,
                room_portal_mcp_settings(portal.root),
            )
            receipt = portal.observation_receipt("wake-a")
            room_results = portal.observation_results("wake-a")
            publication = portal.consume_publication_result("wake-a")
            activity = portal.activity_path.read_text(encoding="utf-8")

        self.assertEqual(
            tool_names,
            [
                "read_discussion",
                "list_participants",
                "publish_message",
                "decline_to_speak",
                "create_vote",
                "cast_vote",
                "vote_summary",
                "roll_dice",
                "choose_random",
                "rimworld_observe",
                "rimworld_inspect",
                "rimworld_act",
                "rimworld_speak",
            ],
        )
        self.assertFalse(read_error)
        self.assertFalse(publish_error)
        self.assertFalse(roll.isError)
        self.assertFalse(choice.isError)
        self.assertEqual(receipt, 7)
        self.assertEqual(publication.content, "MCP publication")
        self.assertEqual(publication.target_agent_id, "sonnet")
        self.assertIn('"operation": "roll_dice"', activity)
        self.assertIn('"operation": "choose_random"', activity)
        self.assertEqual(
            [result["operation"] for result in room_results],
            ["roll_dice", "choose_random"],
        )
        self.assertTrue(
            all(
                re.fullmatch(r"result-[a-f0-9]{32}", str(result["result_id"]))
                for result in room_results
            )
        )
        self.assertEqual(room_results[0]["details"]["notation"], "2d6+1")
        self.assertEqual(room_results[0]["details"]["reason"], "damage")
        self.assertEqual(room_results[1]["details"]["reason"], "route")
        self.assertEqual(room_results[1]["details"]["options"], ["north", "south"])
        self.assertIn(room_results[1]["details"]["choice"], {"north", "south"})

    async def _call_tools(self, settings):
        parameters = StdioServerParameters(
            command=settings["command"],
            args=settings["args"],
            cwd=settings["cwd"],
        )
        with tempfile.TemporaryFile(mode="w+") as stderr:
            async with stdio_client(parameters, errlog=stderr) as (read_stream, write_stream):
                async with ClientSession(read_stream, write_stream) as session:
                    await session.initialize()
                    tools = await session.list_tools()
                    read_result = await session.call_tool("read_discussion", {})
                    publish_result = await session.call_tool(
                        "publish_message",
                        {
                            "content": "MCP publication",
                            "next_agent_id": "sonnet",
                        },
                    )
                    roll_result = await session.call_tool(
                        "roll_dice",
                        {"notation": "2d6+1", "reason": "damage"},
                    )
                    choice_result = await session.call_tool(
                        "choose_random",
                        {"options": ["north", "south"], "reason": "route"},
                    )
        return (
            [tool.name for tool in tools.tools],
            read_result.isError,
            publish_result.isError,
            roll_result,
            choice_result,
        )


if __name__ == "__main__":
    unittest.main()
