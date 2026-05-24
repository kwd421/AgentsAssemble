import importlib.util
import json
import sys
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from agentsassemble.cli import build_parser
from agentsassemble.gui import _make_handler, append_lobby_event, read_lobby
from agentsassemble.live_agent_join_brief import build_live_agent_join_brief
from agentsassemble.live_agents import read_live_agents


MCP_AVAILABLE = importlib.util.find_spec("mcp") is not None


class McpCliParserTests(unittest.TestCase):
    def test_mcp_participant_serve_args_parse(self):
        args = build_parser().parse_args(
            [
                "mcp",
                "serve",
                "--profile",
                "participant",
                "--server",
                "http://127.0.0.1:8765",
                "--agent-id",
                "agent-a",
                "--meeting-id",
                "m1",
            ]
        )

        self.assertEqual(args.command, "mcp")
        self.assertEqual(args.mcp_command, "serve")
        self.assertEqual(args.profile, "participant")
        self.assertEqual(args.server, "http://127.0.0.1:8765")
        self.assertEqual(args.agent_id, "agent-a")
        self.assertEqual(args.meeting_id, "m1")

    def test_mcp_archive_serve_args_parse(self):
        args = build_parser().parse_args(["mcp", "serve", "--profile", "archive", "--meeting-id", "m1"])

        self.assertEqual(args.command, "mcp")
        self.assertEqual(args.mcp_command, "serve")
        self.assertEqual(args.profile, "archive")
        self.assertEqual(args.meeting_id, "m1")

    def test_mcp_rejects_unknown_profile(self):
        with self.assertRaises(SystemExit):
            build_parser().parse_args(["mcp", "serve", "--profile", "host"])


class McpJoinBriefTests(unittest.TestCase):
    def test_join_brief_includes_safe_participant_mcp_command(self):
        payload = build_live_agent_join_brief(
            server="http://127.0.0.1:8765",
            agent_id="external-reviewer",
            display_name="External Reviewer",
            provider_kind="manual",
            connection_kind="manual",
            meeting_id="resident-m1",
            engagement_mode="watch",
            timeout=9,
            poll_interval=0.5,
            max_chain_depth=2,
        )

        self.assertEqual(payload["mcp"]["profile"], "participant")
        self.assertEqual(payload["mcp"]["transport"], "stdio")
        self.assertEqual(
            payload["mcp"]["command"],
            [
                "python3",
                "-m",
                "agentsassemble.cli",
                "mcp",
                "serve",
                "--profile",
                "participant",
                "--server",
                "http://127.0.0.1:8765",
                "--agent-id",
                "external-reviewer",
                "--display-name",
                "External Reviewer",
                "--provider-kind",
                "manual",
                "--connection-kind",
                "manual",
                "--meeting-id",
                "resident-m1",
                "--engagement-mode",
                "watch",
                "--timeout",
                "9",
                "--poll-interval",
                "0.5",
                "--max-chain-depth",
                "2",
            ],
        )
        serialized = json.dumps(payload["mcp"])
        self.assertNotIn("endpoint", serialized)
        self.assertNotIn("auth", serialized)
        self.assertFalse(payload["mcp"]["safety"]["room_contacted"])
        self.assertFalse(payload["mcp"]["safety"]["provider_executed"])


class McpRoomClientTests(unittest.TestCase):
    def test_wait_next_surfaces_targeted_return_packet(self):
        from agentsassemble.mcp_server import McpServerConfig, RoomClient

        requester = _ReturnPacketRequester()
        client = RoomClient(
            McpServerConfig(
                profile="participant",
                server="http://room.test",
                agent_id="agent-a",
                meeting_id="m1",
            ),
            requester=requester,
        )

        payload = client.wait_next(timeout=0, poll_interval=0)

        self.assertEqual(payload["action"], "return_packet")
        self.assertEqual(payload["source_event_id"], "packet-1")
        self.assertEqual(payload["meeting_id"], "m1")
        self.assertEqual(payload["artifact_path"], "return-packets/agent-a.md")
        self.assertEqual(payload["artifact_json_path"], "return-packets/agent-a.json")
        self.assertEqual(payload["room"]["meeting_id"], "m1")


@unittest.skipUnless(MCP_AVAILABLE, "mcp SDK is not installed")
class McpServerToolTests(unittest.TestCase):
    def test_participant_profile_exposes_only_participant_tools(self):
        from agentsassemble.mcp_server import McpServerConfig, build_mcp_server

        server = build_mcp_server(
            McpServerConfig(profile="participant", server="http://room.test", agent_id="agent-a", meeting_id="m1"),
            requester=_FakeRequester(),
        )

        names = _tool_names(server)

        self.assertEqual(
            names,
            {
                "register",
                "heartbeat",
                "wait_next",
                "say",
                "official_reply",
                "read_room",
                "read_return_packet",
                "leave",
            },
        )
        self.assertNotIn("read_transcript", names)
        self.assertNotIn("finalize_meeting", names)

    def test_archive_profile_exposes_only_read_only_archive_tools(self):
        from agentsassemble.mcp_server import McpServerConfig, build_mcp_server

        server = build_mcp_server(
            McpServerConfig(profile="archive", server="http://room.test", agent_id="", meeting_id="m1"),
            requester=_FakeRequester(),
        )

        names = _tool_names(server)

        self.assertEqual(
            names,
            {
                "list_meetings",
                "read_meeting_summary",
                "read_transcript",
                "read_decision",
                "read_shared_memory",
            },
        )
        self.assertNotIn("register", names)
        self.assertNotIn("say", names)
        self.assertNotIn("heartbeat", names)

    def test_participant_tools_call_existing_live_agent_endpoints(self):
        from agentsassemble.mcp_server import McpServerConfig, build_mcp_server

        requester = _FakeRequester()
        server = build_mcp_server(
            McpServerConfig(
                profile="participant",
                server="http://room.test",
                agent_id="agent-a",
                display_name="Agent A",
                provider_kind="manual",
                connection_kind="manual",
                meeting_id="m1",
                engagement_mode="always",
            ),
            requester=requester,
        )

        _call_tool(server, "register", {})
        _call_tool(server, "wait_next", {"timeout": 0, "poll_interval": 0, "max_chain_depth": 1})
        _call_tool(server, "say", {"message": "hello", "source_event_id": "evt-1", "auto_chain_depth": 1})
        _call_tool(server, "heartbeat", {"status": "online", "last_observed_event_id": "evt-1"})
        _call_tool(server, "leave", {"last_observed_event_id": "evt-1"})

        self.assertEqual(
            [(call.method, urlparse(call.url).path) for call in requester.calls],
            [
                ("POST", "/api/live-agents"),
                ("GET", "/api/live-agents/agent-a/room"),
                ("POST", "/api/live-agents/agent-a/lobby"),
                ("POST", "/api/live-agents/agent-a/heartbeat"),
                ("POST", "/api/live-agents/agent-a/leave"),
            ],
        )
        self.assertEqual(requester.calls[0].payload["agent_id"], "agent-a")
        self.assertEqual(requester.calls[2].payload["message"], "hello")
        self.assertEqual(requester.calls[2].payload["source_event_id"], "evt-1")
        self.assertEqual(requester.calls[2].payload["auto_chain_depth"], 1)
        self.assertEqual(requester.calls[3].payload["status"], "online")
        self.assertEqual(requester.calls[4].payload["last_observed_event_id"], "evt-1")

    def test_wait_next_tool_surfaces_targeted_return_packet(self):
        from agentsassemble.mcp_server import McpServerConfig, build_mcp_server

        server = build_mcp_server(
            McpServerConfig(profile="participant", server="http://room.test", agent_id="agent-a", meeting_id="m1"),
            requester=_ReturnPacketRequester(),
        )

        payload = json.loads(_tool_text(_call_tool(server, "wait_next", {"timeout": 0, "poll_interval": 0})))

        self.assertEqual(payload["action"], "return_packet")
        self.assertEqual(payload["source_event_id"], "packet-1")
        self.assertEqual(payload["read_return_packet_args"], {"meeting_id": "m1", "source_event_id": "packet-1"})
        self.assertEqual(payload["heartbeat_args"], {"status": "online", "last_observed_live_event_id": "packet-1"})

    def test_official_reply_posts_to_matching_meeting_and_source_event(self):
        from agentsassemble.mcp_server import McpServerConfig, build_mcp_server

        requester = _FakeRequester()
        server = build_mcp_server(
            McpServerConfig(profile="participant", server="http://room.test", agent_id="agent-a", meeting_id="m1"),
            requester=requester,
        )

        _call_tool(
            server,
            "official_reply",
            {"message": "official answer", "meeting_id": "m1", "source_event_id": "live-1"},
        )

        self.assertEqual(urlparse(requester.calls[-1].url).path, "/api/live-agents/agent-a/official-turn")
        self.assertEqual(requester.calls[-1].payload["meeting_id"], "m1")
        self.assertEqual(requester.calls[-1].payload["source_event_id"], "live-1")
        self.assertEqual(requester.calls[-1].payload["content"], "official answer")

    def test_return_packet_tool_has_no_arbitrary_path_input(self):
        from agentsassemble.mcp_server import McpServerConfig, build_mcp_server

        requester = _FakeRequester()
        server = build_mcp_server(
            McpServerConfig(profile="participant", server="http://room.test", agent_id="agent-a", meeting_id="m1"),
            requester=requester,
        )

        tool = _tool_by_name(server, "read_return_packet")
        schema = tool.inputSchema

        self.assertIn("source_event_id", schema["properties"])
        self.assertIn("source_event_id", schema["required"])
        self.assertNotIn("artifact_path", schema["properties"])
        self.assertNotIn("artifact_json_path", schema["properties"])

        _call_tool(server, "read_return_packet", {"meeting_id": "m1", "source_event_id": "packet-1"})

        parsed = urlparse(requester.calls[-1].url)
        self.assertEqual(parsed.path, "/api/live-agents/agent-a/return-packet")
        self.assertEqual(parse_qs(parsed.query), {"meeting_id": ["m1"], "source_event_id": ["packet-1"]})

    def test_archive_tools_read_meeting_payload_without_mutating_presence(self):
        from agentsassemble.mcp_server import McpServerConfig, build_mcp_server

        requester = _FakeRequester()
        server = build_mcp_server(
            McpServerConfig(profile="archive", server="http://room.test", agent_id="", meeting_id="m1"),
            requester=requester,
        )

        transcript = _tool_text(_call_tool(server, "read_transcript", {}))
        decision = _tool_text(_call_tool(server, "read_decision", {"meeting_id": "m1"}))
        memory = json.loads(_tool_text(_call_tool(server, "read_shared_memory", {"meeting_id": "m1"})))

        self.assertIn("Transcript body", transcript)
        self.assertIn("Decision body", decision)
        self.assertEqual(memory["rolling_summary"], "Rolling body")
        self.assertTrue(all(call.method == "GET" for call in requester.calls))
        self.assertFalse(any("/api/live-agents" in call.url for call in requester.calls))

    def test_stdio_participant_smoke_posts_lobby_reply(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                server_url = f"http://127.0.0.1:{server.server_port}"
                source_event = append_lobby_event(root, {"name": "Human", "side": "me", "kind": "message", "message": "ping"})

                result = _run_mcp_stdio_session(
                    [
                        "mcp",
                        "serve",
                        "--profile",
                        "participant",
                        "--server",
                        server_url,
                        "--agent-id",
                        "mcp-agent",
                        "--display-name",
                        "MCP Agent",
                        "--meeting-id",
                        "m1",
                        "--engagement-mode",
                        "always",
                    ],
                    [
                        ("register", {}),
                        ("wait_next", {"timeout": 0, "poll_interval": 0}),
                        (
                            "say",
                            {
                                "message": "pong",
                                "source_event_id": source_event["id"],
                                "auto_chain_depth": 1,
                            },
                        ),
                    ],
                )
            finally:
                server.shutdown()
                server.server_close()

            wait_payload = _mcp_result_json(result[1])
            self.assertEqual(wait_payload["action"], "lobby")
            self.assertEqual(wait_payload["source_event_id"], source_event["id"])
            lobby = read_lobby(root)
            reply = lobby[-1]
            self.assertEqual(reply["actor_id"], "mcp-agent")
            self.assertEqual(reply["source_event_id"], source_event["id"])
            self.assertEqual(reply["auto_chain_depth"], 1)
            self.assertEqual(reply["message"], "pong")
            agents = read_live_agents(root)
            self.assertEqual(agents[0]["last_observed_event_id"], source_event["id"])

    def test_stdio_archive_smoke_reads_without_presence_mutation(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            meeting_dir = root / "meetings" / "m1"
            (meeting_dir / "shared_memory").mkdir(parents=True)
            (meeting_dir / "meeting.json").write_text(
                json.dumps({"meeting_id": "m1", "topic": "MCP topic", "question": "MCP question"}),
                encoding="utf-8",
            )
            (meeting_dir / "transcript.md").write_text("Transcript body", encoding="utf-8")
            (meeting_dir / "decision.md").write_text("Decision body", encoding="utf-8")
            (meeting_dir / "shared_memory" / "rolling-summary.md").write_text("Rolling body", encoding="utf-8")
            (meeting_dir / "shared_memory" / "open-questions.md").write_text("Questions body", encoding="utf-8")
            (meeting_dir / "shared_memory" / "action-items.md").write_text("Actions body", encoding="utf-8")
            (meeting_dir / "shared_memory" / "index.json").write_text('{"official_event_count": 1}', encoding="utf-8")
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                server_url = f"http://127.0.0.1:{server.server_port}"
                result = _run_mcp_stdio_session(
                    [
                        "mcp",
                        "serve",
                        "--profile",
                        "archive",
                        "--server",
                        server_url,
                        "--meeting-id",
                        "m1",
                    ],
                    [
                        ("list_meetings", {}),
                        ("read_transcript", {}),
                        ("read_shared_memory", {"meeting_id": "m1"}),
                    ],
                )
            finally:
                server.shutdown()
                server.server_close()

            meetings = _mcp_result_json(result[0])
            transcript = _mcp_result_text(result[1])
            memory = _mcp_result_json(result[2])
            self.assertEqual(meetings["meetings"][0]["meeting_id"], "m1")
            self.assertIn("Transcript body", transcript)
            self.assertEqual(memory["rolling_summary"], "Rolling body")
            self.assertFalse((root / "live_agents.json").exists())


def _tool_names(server) -> set[str]:
    import anyio

    async def run():
        return {tool.name for tool in await server.list_tools()}

    return anyio.run(run)


def _tool_by_name(server, name: str):
    import anyio

    async def run():
        tools = await server.list_tools()
        return next(tool for tool in tools if tool.name == name)

    return anyio.run(run)


def _call_tool(server, name: str, arguments: dict[str, object]):
    import anyio

    async def run():
        return await server.call_tool(name, arguments)

    return anyio.run(run)


def _tool_text(result) -> str:
    if isinstance(result, tuple):
        result = result[0]
    return "\n".join(str(item.text) for item in result if getattr(item, "type", "") == "text")


def _run_mcp_stdio_session(command_args: list[str], calls: list[tuple[str, dict[str, object]]]):
    import anyio
    from mcp import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client

    async def run():
        params = StdioServerParameters(command=sys.executable, args=["-m", "agentsassemble.cli", *command_args])
        async with stdio_client(params) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                results = []
                for name, arguments in calls:
                    results.append(await session.call_tool(name, arguments))
                return results

    return anyio.run(run)


def _mcp_result_text(result) -> str:
    content = getattr(result, "content", result)
    return _tool_text(content)


def _mcp_result_json(result) -> dict[str, object]:
    structured = getattr(result, "structuredContent", None)
    if isinstance(structured, dict):
        return structured
    return json.loads(_mcp_result_text(result))


class _Call:
    def __init__(self, method: str, url: str, payload: dict[str, object] | None):
        self.method = method
        self.url = url
        self.payload = payload or {}


class _FakeRequester:
    def __init__(self):
        self.calls: list[_Call] = []

    def __call__(
        self,
        url: str,
        *,
        method: str = "GET",
        payload: dict[str, object] | None = None,
        timeout_seconds: float = 10.0,
    ) -> dict[str, object]:
        del timeout_seconds
        self.calls.append(_Call(method, url, payload))
        path = urlparse(url).path
        if method == "POST" and path == "/api/live-agents":
            return {"agent": {"agent_id": payload["agent_id"], "status": "online"}}
        if method == "POST" and path.endswith("/heartbeat"):
            return {"agent": {"agent_id": "agent-a", "status": payload.get("status", "online")}}
        if method == "POST" and path.endswith("/leave"):
            return {"agent": {"agent_id": "agent-a", "status": "offline"}}
        if method == "GET" and path.endswith("/room"):
            return {
                "agent": {"agent_id": "agent-a", "display_name": "Agent A", "engagement_mode": "always"},
                "meeting_id": "m1",
                "lobby_events": [{"id": "evt-1", "name": "Human", "message": "ping"}],
                "live_events": [],
                "shared_memory": {"rolling_summary": "Rolling body"},
            }
        if method == "POST" and path.endswith("/lobby"):
            return {"event": {"id": "reply-1", "message": payload["message"]}}
        if method == "POST" and path.endswith("/official-turn"):
            return {"event": {"id": "official-1", "content": payload["content"]}}
        if method == "GET" and path.endswith("/return-packet"):
            return {"markdown": "Packet body", "json": {"ok": True}}
        if method == "GET" and path == "/api/meetings":
            return {"meetings": [{"meeting_id": "m1", "topic": "Topic", "live_status": "running"}]}
        if method == "GET" and path == "/api/meetings/m1":
            return {
                "meeting": {"meeting_id": "m1", "topic": "Topic", "live_status": "running"},
                "artifacts": {
                    "transcript.md": "Transcript body",
                    "decision.md": "Decision body",
                    "shared_memory/rolling-summary.md": "Rolling body",
                    "shared_memory/open-questions.md": "Questions body",
                    "shared_memory/action-items.md": "Actions body",
                    "shared_memory/index.json": "{\"official_event_count\": 1}",
                },
                "live_events": [],
            }
        raise AssertionError(f"unexpected request {method} {url}")


class _ReturnPacketRequester:
    def __init__(self):
        self.calls: list[_Call] = []

    def __call__(
        self,
        url: str,
        *,
        method: str = "GET",
        payload: dict[str, object] | None = None,
        timeout_seconds: float = 10.0,
    ) -> dict[str, object]:
        del payload, timeout_seconds
        self.calls.append(_Call(method, url, None))
        path = urlparse(url).path
        if method == "GET" and path == "/api/live-agents/agent-a/room":
            return {
                "agent": {
                    "agent_id": "agent-a",
                    "display_name": "Agent A",
                    "last_observed_live_event_id": "live-0",
                },
                "meeting_id": "m1",
                "lobby_events": [],
                "live_events": [
                    {
                        "id": "packet-1",
                        "kind": "artifact",
                        "artifact_kind": "return_packet",
                        "meeting_id": "m1",
                        "target_agent_id": "agent-a",
                        "artifact_path": "return-packets/agent-a.md",
                        "artifact_json_path": "return-packets/agent-a.json",
                    }
                ],
            }
        raise AssertionError(f"unexpected request {method} {url}")


if __name__ == "__main__":
    unittest.main()
