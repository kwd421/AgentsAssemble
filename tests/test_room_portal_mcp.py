import tempfile
import unittest
from pathlib import Path

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
            portal.begin_observation("wake-a", input_up_to_seq=7)

            tool_names, read_error, publish_error = anyio.run(
                self._call_tools,
                room_portal_mcp_settings(portal.root),
            )
            receipt = portal.observation_receipt("wake-a")
            publication = portal.consume_publication("wake-a")

        self.assertEqual(tool_names, ["read_discussion", "publish_message"])
        self.assertFalse(read_error)
        self.assertFalse(publish_error)
        self.assertEqual(receipt, 7)
        self.assertEqual(publication, "MCP publication")

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
                        {"content": "MCP publication"},
                    )
        return (
            [tool.name for tool in tools.tools],
            read_result.isError,
            publish_result.isError,
        )


if __name__ == "__main__":
    unittest.main()
