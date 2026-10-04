from __future__ import annotations

import json
import subprocess
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import anyio
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

from agentsassemble.providers.openai_compatible_room_tools import execute_room_tool
from agentsassemble.providers.room_portal import RoomPortal
from agentsassemble.providers.room_portal_mcp import room_portal_mcp_settings
from agentsassemble.providers.room_portal_search import RoomPortalSearchBroker


class _SearchHandler(BaseHTTPRequestHandler):
    requests: list[dict[str, object]] = []

    def do_GET(self) -> None:  # noqa: N802 - stdlib handler API
        parsed = urlsplit(self.path)
        query = parse_qs(parsed.query)
        self.__class__.requests.append(
            {
                "path": parsed.path,
                "query": query,
                "authorization": self.headers.get("Authorization", ""),
            }
        )
        if parsed.path == "/api/room-search":
            payload = {
                "results": [
                    {
                        "event_id": "event-older",
                        "channel_id": "lobby",
                        "content": "older deployment failure",
                    }
                ],
                "next_cursor": "page-two",
            }
        elif parsed.path == "/api/room-search/context":
            payload = {
                "channel_id": "lobby",
                "event_id": "event-older",
                "events": [
                    {"id": "event-older", "content": "older deployment failure"}
                ],
            }
        else:
            self.send_error(404)
            return
        body = json.dumps(payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, _format: str, *args: object) -> None:
        del args


class RoomPortalSearchTests(unittest.TestCase):
    def test_portal_and_terminal_helper_share_authenticated_search_without_token_exposure(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            portal = RoomPortal(root / "portal", participant_id="codex")
            portal.prepare()
            portal.begin_observation("wake-search", input_up_to_seq=4)
            _SearchHandler.requests = []
            server = ThreadingHTTPServer(("127.0.0.1", 0), _SearchHandler)
            server_thread = threading.Thread(target=server.serve_forever, daemon=True)
            server_thread.start()
            token = "private-session-token"
            broker = RoomPortalSearchBroker(
                portal.root,
                server_url=f"http://127.0.0.1:{server.server_port}",
                session_token=token,
                room_id="room-a",
                tool_allowed=portal.tool_allowed,
            )
            broker.start()
            try:
                tool_name, tool_result = execute_room_tool(
                    portal,
                    {
                        "function": {
                            "name": "search_messages",
                            "arguments": json.dumps(
                                {
                                    "query": "deployment failure",
                                    "channel_id": "all",
                                }
                            ),
                        }
                    },
                )
                page = json.loads(tool_result)
                mcp_page = anyio.run(
                    self._mcp_search,
                    room_portal_mcp_settings(portal.root),
                )
                helper = subprocess.run(
                    [
                        str(portal.helper_path),
                        "search-context",
                        "lobby",
                        str(page["results"][0]["event_id"]),
                    ],
                    text=True,
                    capture_output=True,
                    timeout=10,
                    check=True,
                )
            finally:
                broker.stop()
                server.shutdown()
                server.server_close()
                server_thread.join(timeout=2)

            context = json.loads(helper.stdout)
            self.assertEqual(tool_name, "search_messages")
            self.assertEqual(page["results"][0]["content"], "older deployment failure")
            self.assertEqual(
                mcp_page["results"][0]["event_id"],
                "event-older",
            )
            self.assertEqual(context["events"][0]["id"], "event-older")
            self.assertEqual(
                [request["path"] for request in _SearchHandler.requests],
                [
                    "/api/room-search",
                    "/api/room-search",
                    "/api/room-search/context",
                ],
            )
            self.assertTrue(
                all(
                    request["authorization"] == f"Bearer {token}"
                    for request in _SearchHandler.requests
                )
            )
            self.assertEqual(
                _SearchHandler.requests[0]["query"],
                {
                    "room_id": ["room-a"],
                    "channel_id": ["all"],
                    "q": ["deployment failure"],
                },
            )
            portal_files = [path for path in portal.root.rglob("*") if path.is_file()]
            self.assertFalse(
                any(token in path.read_text(encoding="utf-8") for path in portal_files)
            )

    async def _mcp_search(self, settings: dict[str, object]) -> dict[str, object]:
        parameters = StdioServerParameters(
            command=str(settings["command"]),
            args=list(settings["args"]),
            cwd=str(settings["cwd"]),
        )
        with tempfile.TemporaryFile(mode="w+") as stderr:
            async with stdio_client(parameters, errlog=stderr) as streams:
                read_stream, write_stream = streams
                async with ClientSession(read_stream, write_stream) as session:
                    await session.initialize()
                    result = await session.call_tool(
                        "search_messages",
                        {"query": "deployment failure", "channel_id": "all"},
                    )
        self.assertFalse(result.isError)
        return dict(result.structuredContent or {})


if __name__ == "__main__":
    unittest.main()
