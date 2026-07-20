import unittest

from agentsassemble.cli import build_parser
from agentsassemble.mcp_server import McpParticipantContext, McpRoomClient, build_tool_registry
from agentsassemble.legacy.live_agent.runtime.join_brief import build_live_agent_join_brief


class McpCliParserTests(unittest.TestCase):
    def test_mcp_serve_participant_parser_accepts_room_identity(self):
        args = build_parser().parse_args(
            [
                "mcp",
                "serve",
                "--profile",
                "participant",
                "--server",
                "http://room.local",
                "--agent-id",
                "agent-a",
                "--meeting-id",
                "m1",
            ]
        )

        self.assertEqual(args.command, "mcp")
        self.assertEqual(args.mcp_command, "serve")
        self.assertEqual(args.profile, "participant")
        self.assertEqual(args.server, "http://room.local")
        self.assertEqual(args.agent_id, "agent-a")
        self.assertEqual(args.meeting_id, "m1")

    def test_mcp_serve_archive_parser_accepts_read_only_profile(self):
        args = build_parser().parse_args(["mcp", "serve", "--profile", "archive", "--server", "http://room.local"])

        self.assertEqual(args.command, "mcp")
        self.assertEqual(args.profile, "archive")

    def test_mcp_serve_participant_parser_accepts_startup_identity(self):
        args = build_parser().parse_args(
            [
                "mcp",
                "serve",
                "--profile",
                "participant",
                "--agent-id",
                "agent-a",
                "--display-name",
                "Agent A",
                "--provider-kind",
                "kiro",
                "--connection-kind",
                "terminal_session",
                "--engagement-mode",
                "mentioned",
            ]
        )

        self.assertEqual(args.display_name, "Agent A")
        self.assertEqual(args.provider_kind, "kiro")
        self.assertEqual(args.connection_kind, "terminal_session")
        self.assertEqual(args.engagement_mode, "mentioned")

    def test_mcp_serve_invalid_profile_fails_clearly(self):
        with self.assertRaises(SystemExit):
            build_parser().parse_args(["mcp", "serve", "--profile", "host", "--server", "http://room.local"])


class RecordingRoom:
    def __init__(self):
        self.requests = []
        self.room = {
            "agent": {
                "agent_id": "agent-a",
                "display_name": "Agent A",
                "engagement_mode": "always",
            },
            "meeting_id": "m1",
            "lobby_events": [],
            "live_events": [],
            "dm_events": [],
        }
        self.meeting_payload = {
            "meeting": {
                "meeting_id": "m1",
                "topic": "MCP smoke",
                "question": "Can archive tools read safely?",
                "live_status": "complete",
                "path": "/tmp/agentsassemble/meetings/m1",
                "provider_runtime_config": "/tmp/private-provider.json",
            },
            "artifacts": {
                "transcript.md": "# Transcript\n\nOfficial answer.",
                "decision.md": "# Decision\n\nStatus: needs_user_decision",
                "shared_memory/rolling-summary.md": "# Rolling Summary\n",
                "shared_memory/index.json": '{"official_event_count": 1}',
            },
        }

    def request_json(self, *, method, path, payload=None, timeout_seconds=10.0):
        self.requests.append(
            {
                "method": method,
                "path": path,
                "payload": payload,
                "timeout_seconds": timeout_seconds,
            }
        )
        if method == "GET" and path == "/api/live-agents/agent-a/room":
            return self.room
        if method == "GET" and path.startswith("/api/live-agents/agent-a/return-packet?"):
            return {"status": "ok", "markdown": "Return packet for agent-a."}
        if method == "GET" and path == "/api/meetings":
            return {
                "meetings": [
                    {
                        "meeting_id": "m1",
                        "topic": "MCP smoke",
                        "path": "/tmp/agentsassemble/meetings/m1",
                        "mtime": 123.4,
                    }
                ]
            }
        if method == "GET" and path == "/api/meetings/m1":
            return self.meeting_payload
        if method == "POST":
            return {"status": "ok", "event": {"id": "posted-1"}, "agent": {"agent_id": "agent-a", "status": "online"}}
        return {"status": "ok"}


class McpParticipantToolsTests(unittest.TestCase):
    def test_participant_registry_excludes_archive_and_host_tools(self):
        registry = build_tool_registry(
            "participant",
            McpRoomClient("http://room.local", request_json=RecordingRoom().request_json),
            McpParticipantContext(agent_id="agent-a", meeting_id="m1"),
        )

        self.assertEqual(
            set(registry),
            {
                "register",
                "heartbeat",
                "wait_next",
                "read_since",
                "look",
                "say",
                "dm_reply",
                "official_reply",
                "read_room",
                "read_return_packet",
                "leave",
            },
        )
        self.assertNotIn("read_transcript", registry)
        self.assertNotIn("finalize_meeting", registry)

    def test_register_uses_startup_identity_and_rejects_tool_supplied_identity(self):
        room = RecordingRoom()
        tools = build_tool_registry(
            "participant",
            McpRoomClient("http://room.local", request_json=room.request_json),
            McpParticipantContext(
                agent_id="agent-a",
                display_name="Agent A",
                provider_kind="kiro",
                connection_kind="terminal_session",
                meeting_id="m1",
                engagement_mode="mentioned",
            ),
        )

        tools["register"]()

        payload = room.requests[0]["payload"]
        self.assertEqual(payload["agent_id"], "agent-a")
        self.assertEqual(payload["display_name"], "Agent A")
        self.assertEqual(payload["provider_kind"], "kiro")
        self.assertEqual(payload["connection_kind"], "terminal_session")
        self.assertEqual(payload["join_semantics"], "mcp_tool_loop")
        self.assertEqual(payload["engagement_mode"], "mentioned")
        with self.assertRaises(TypeError):
            tools["register"](display_name="Spoof", provider_kind="other")

    def test_participant_tools_call_existing_live_agent_endpoints(self):
        room = RecordingRoom()
        client = McpRoomClient("http://room.local", request_json=room.request_json)
        tools = build_tool_registry(
            "participant",
            client,
            McpParticipantContext(agent_id="agent-a", display_name="Agent A", meeting_id="m1", engagement_mode="always"),
        )
        tools["register"]()
        # Events arriving AFTER register are delivered as new; pre-join history
        # is fast-forwarded past at register time and never replayed.
        room.room["lobby_events"] = [{"id": "lobby-1", "name": "Human", "message": "hello"}]
        room.room["live_events"] = [
            {
                "id": "turn-1",
                "kind": "live_agent_turn_request",
                "target_agent_id": "agent-a",
                "meeting_id": "m1",
                "content": "official turn",
            }
        ]
        wait_result = tools["wait_next"](timeout_seconds=0, poll_interval=0, max_chain_depth=1)
        tools["official_reply"](message="official answer", meeting_id="m1", source_event_id=wait_result["source_event_id"])
        packet = tools["read_return_packet"](source_event_id="packet-1", meeting_id="m1")
        tools["say"](message="lobby reply", source_event_id="lobby-1", auto_chain_depth=1)
        tools["heartbeat"](status="working", last_observed_event_id="lobby-1")
        tools["leave"]()

        self.assertEqual(wait_result["action"], "official_turn")
        self.assertEqual(packet["markdown"], "Return packet for agent-a.")
        self.assertEqual(
            [(request["method"], request["path"]) for request in room.requests],
            [
                ("POST", "/api/live-agents"),
                ("GET", "/api/live-agents/agent-a/room"),
                ("GET", "/api/live-agents/agent-a/room"),
                ("POST", "/api/live-agents/agent-a/official-turn"),
                ("GET", "/api/live-agents/agent-a/return-packet?meeting_id=m1&source_event_id=packet-1"),
                ("POST", "/api/live-agents/agent-a/lobby"),
                ("POST", "/api/live-agents/agent-a/heartbeat"),
                ("POST", "/api/live-agents/agent-a/leave"),
            ],
        )
        self.assertEqual(room.requests[0]["payload"]["agent_id"], "agent-a")
        self.assertEqual(room.requests[3]["payload"]["meeting_id"], "m1")
        self.assertEqual(room.requests[3]["payload"]["source_event_id"], "turn-1")
        self.assertEqual(room.requests[5]["payload"]["source_event_id"], "lobby-1")

    def test_register_fast_forwards_past_pre_join_backlog(self):
        room = RecordingRoom()
        client = McpRoomClient("http://room.local", request_json=room.request_json)
        tools = build_tool_registry(
            "participant",
            client,
            McpParticipantContext(agent_id="agent-a", display_name="Agent A", meeting_id="m1", engagement_mode="always"),
        )
        # Backlog written BEFORE the agent joins must never be replayed as new.
        room.room["lobby_events"] = [
            {"id": "old-1", "name": "Human", "message": "ancient question"},
            {"id": "old-2", "name": "OtherAgent", "actor_id": "agent-b", "message": "ancient reply"},
        ]

        register_result = tools["register"]()
        self.assertEqual(register_result.get("history_cursor"), "fast_forwarded_to_join_time")
        timed_out = tools["wait_next"](timeout_seconds=0, poll_interval=0, max_chain_depth=1)
        self.assertEqual(timed_out["status"], "timeout")

        # A message arriving after join is delivered normally.
        room.room["lobby_events"].append({"id": "new-1", "name": "Human", "message": "fresh question"})
        delivered = tools["wait_next"](timeout_seconds=0, poll_interval=0, max_chain_depth=1)
        self.assertEqual(delivered["status"], "event")
        self.assertEqual(delivered["source_event_id"], "new-1")

    def test_wait_next_prefers_dm_and_dm_reply_posts_direct_message(self):
        room = RecordingRoom()
        client = McpRoomClient("http://room.local", request_json=room.request_json)
        tools = build_tool_registry(
            "participant",
            client,
            McpParticipantContext(agent_id="agent-a", display_name="Agent A", meeting_id="m1", engagement_mode="always"),
        )
        room.room["agent"]["last_observed_dm_event_id"] = "dm-old"
        room.room["dm_events"] = [
            {
                "id": "dm-old",
                "friend_id": "friend:agent-a",
                "side": "mine",
                "target_agent_id": "agent-a",
                "message": "old",
            },
            {
                "id": "dm-next",
                "friend_id": "friend:agent-a",
                "side": "mine",
                "target_agent_id": "agent-a",
                "message": "private question",
            },
        ]
        room.room["lobby_events"] = [{"id": "lobby-1", "name": "Human", "message": "hello"}]
        room.room["live_events"] = [
            {
                "id": "turn-1",
                "kind": "live_agent_turn_request",
                "target_agent_id": "agent-a",
                "meeting_id": "m1",
                "content": "official turn",
            }
        ]

        wait_result = tools["wait_next"](timeout_seconds=0, poll_interval=0, max_chain_depth=1)
        reply = tools["dm_reply"](message="direct answer", source_event_id=wait_result["source_event_id"])

        self.assertEqual(wait_result["action"], "dm")
        self.assertEqual(wait_result["source_event_id"], "dm-next")
        self.assertEqual(reply["event"]["id"], "posted-1")
        self.assertEqual(room.requests[-1]["path"], "/api/live-agents/agent-a/dm-reply")
        self.assertEqual(room.requests[-1]["payload"], {"source_event_id": "dm-next", "message": "direct answer"})

    def test_tool_loop_lobby_say_preserves_flow_metadata_from_wait_next_event(self):
        room = RecordingRoom()
        client = McpRoomClient("http://room.local", request_json=room.request_json)
        tools = build_tool_registry(
            "participant",
            client,
            McpParticipantContext(agent_id="agent-a", display_name="Agent A", meeting_id="m1", engagement_mode="always"),
        )
        room.room["lobby_events"] = [
            {
                "id": "flow-start",
                "name": "Play Mode",
                "actor_id": "flow",
                "message": "시간제 자유토론 시작",
                "flow_id": "flow-1",
                "flow_meeting_id": "m1",
                "flow_event_type": "started",
            }
        ]

        wait_result = tools["wait_next"](timeout_seconds=0, poll_interval=0, max_chain_depth=1)
        tools["say"](message="tool-loop answer", source_event_id=wait_result["source_event_id"], auto_chain_depth=1)

        payload = room.requests[-1]["payload"]
        self.assertEqual(payload["source_event_id"], "flow-start")
        self.assertEqual(payload["flow_id"], "flow-1")
        self.assertEqual(payload["flow_meeting_id"], "m1")
        self.assertEqual(payload["flow_action"], "speak")
        self.assertEqual(payload["flow_runtime_mode"], "provider_tool_loop")

    def test_tool_loop_lobby_say_accepts_explicit_flow_metadata_from_read_since_event(self):
        room = RecordingRoom()
        client = McpRoomClient("http://room.local", request_json=room.request_json)
        tools = build_tool_registry(
            "participant",
            client,
            McpParticipantContext(agent_id="agent-a", display_name="Agent A", meeting_id="m1", engagement_mode="always"),
        )
        room.room["agent"]["last_observed_event_id"] = "old"
        room.room["lobby_events"] = [
            {"id": "old", "name": "Human", "message": "old"},
            {
                "id": "flow-next",
                "name": "Human",
                "message": "비 올 때 뭘 할까?",
                "flow_id": "flow-rain",
                "flow_meeting_id": "m1",
            },
        ]

        diff = tools["read_since"]()
        event = diff["lobby_events"][0]
        tools["say"](
            message="책 한 권과 따뜻한 음료가 좋겠어요.",
            source_event_id=event["id"],
            auto_chain_depth=1,
            flow_id=event["flow_id"],
            flow_meeting_id=event["flow_meeting_id"],
        )

        payload = room.requests[-1]["payload"]
        self.assertEqual(payload["source_event_id"], "flow-next")
        self.assertEqual(payload["flow_id"], "flow-rain")
        self.assertEqual(payload["flow_meeting_id"], "m1")
        self.assertEqual(payload["flow_runtime_mode"], "provider_tool_loop")

    def test_wait_next_clears_stale_flow_metadata_before_non_lobby_events(self):
        room = RecordingRoom()
        client = McpRoomClient("http://room.local", request_json=room.request_json)
        tools = build_tool_registry(
            "participant",
            client,
            McpParticipantContext(agent_id="agent-a", display_name="Agent A", meeting_id="m1", engagement_mode="always"),
        )
        room.room["lobby_events"] = [
            {
                "id": "flow-start",
                "name": "Play Mode",
                "message": "시작",
                "flow_id": "flow-1",
                "flow_meeting_id": "m1",
            }
        ]
        tools["wait_next"](timeout_seconds=0, poll_interval=0, max_chain_depth=1)
        room.room["agent"]["last_observed_dm_event_id"] = "dm-old"
        room.room["dm_events"] = [
            {"id": "dm-old", "side": "mine", "target_agent_id": "agent-a", "message": "old"},
            {"id": "dm-next", "side": "mine", "target_agent_id": "agent-a", "message": "private"},
        ]

        dm = tools["wait_next"](timeout_seconds=0, poll_interval=0, max_chain_depth=1)
        tools["say"](message="plain lobby reply", source_event_id=dm["source_event_id"], auto_chain_depth=1)

        payload = room.requests[-1]["payload"]
        self.assertEqual(payload["source_event_id"], "dm-next")
        self.assertNotIn("flow_id", payload)
        self.assertNotIn("flow_runtime_mode", payload)

    def test_read_since_returns_room_diff_without_mutating_presence(self):
        room = RecordingRoom()
        room.room["agent"] = {
            "agent_id": "agent-a",
            "display_name": "Agent A",
            "last_observed_event_id": "lobby-1",
            "last_observed_live_event_id": "live-1",
            "last_observed_dm_event_id": "dm-1",
        }
        room.room["lobby_events"] = [
            {"id": "lobby-1", "name": "Human", "message": "old lobby"},
            {"id": "lobby-2", "name": "Human", "message": "new lobby"},
        ]
        room.room["live_events"] = [
            {"id": "live-1", "kind": "message", "content": "old official"},
            {"id": "live-2", "kind": "message", "content": "new official"},
        ]
        room.room["dm_events"] = [
            {"id": "dm-1", "side": "mine", "target_agent_id": "agent-a", "message": "old dm"},
            {"id": "dm-2", "side": "mine", "target_agent_id": "agent-a", "message": "new dm"},
        ]
        tools = build_tool_registry(
            "participant",
            McpRoomClient("http://room.local", request_json=room.request_json),
            McpParticipantContext(agent_id="agent-a", display_name="Agent A", meeting_id="m1"),
        )

        diff = tools["read_since"]()
        tools["leave"]()

        self.assertEqual([event["id"] for event in diff["lobby_events"]], ["lobby-2"])
        self.assertEqual([event["id"] for event in diff["live_events"]], ["live-2"])
        self.assertEqual([event["id"] for event in diff["dm_events"]], ["dm-2"])
        self.assertEqual(diff["last_observed_event_id"], "lobby-1")
        self.assertEqual(diff["last_observed_live_event_id"], "live-1")
        self.assertEqual(diff["last_observed_dm_event_id"], "dm-1")
        self.assertEqual(diff["next_last_observed_event_id"], "lobby-2")
        self.assertEqual(diff["next_last_observed_live_event_id"], "live-2")
        self.assertEqual(diff["next_last_observed_dm_event_id"], "dm-2")
        self.assertEqual(diff["room"]["lobby_event_count"], 2)
        self.assertEqual(diff["room"]["dm_event_count"], 2)
        self.assertEqual(
            [(request["method"], request["path"]) for request in room.requests],
            [
                ("GET", "/api/live-agents/agent-a/room"),
                ("POST", "/api/live-agents/agent-a/leave"),
            ],
        )
        self.assertEqual(room.requests[-1]["payload"]["last_observed_event_id"], "")
        self.assertEqual(room.requests[-1]["payload"]["last_observed_live_event_id"], "")
        self.assertEqual(room.requests[-1]["payload"]["last_observed_dm_event_id"], "")

    def test_read_since_accepts_explicit_cursors(self):
        room = RecordingRoom()
        room.room["agent"] = {
            "agent_id": "agent-a",
            "display_name": "Agent A",
            "last_observed_event_id": "lobby-agent",
            "last_observed_live_event_id": "live-agent",
        }
        room.room["lobby_events"] = [
            {"id": "lobby-old", "name": "Human", "message": "old lobby"},
            {"id": "lobby-next", "name": "Human", "message": "new lobby"},
        ]
        room.room["live_events"] = [
            {"id": "live-old", "kind": "message", "content": "old official"},
            {"id": "live-next", "kind": "message", "content": "new official"},
        ]
        tools = build_tool_registry(
            "participant",
            McpRoomClient("http://room.local", request_json=room.request_json),
            McpParticipantContext(agent_id="agent-a", display_name="Agent A", meeting_id="m1"),
        )

        diff = tools["read_since"](after_event_id="lobby-old", after_live_event_id="live-old")

        self.assertEqual([event["id"] for event in diff["lobby_events"]], ["lobby-next"])
        self.assertEqual([event["id"] for event in diff["live_events"]], ["live-next"])
        self.assertEqual(diff["last_observed_event_id"], "lobby-old")
        self.assertEqual(diff["last_observed_live_event_id"], "live-old")

    def test_read_since_defaults_to_room_cursor_after_wait_next_delivery(self):
        room = RecordingRoom()
        room.room["agent"] = {
            "agent_id": "agent-a",
            "display_name": "Agent A",
            "engagement_mode": "always",
            "last_observed_event_id": "lobby-1",
        }
        room.room["lobby_events"] = [
            {"id": "lobby-1", "name": "Human", "message": "old lobby"},
            {"id": "lobby-2", "name": "Human", "message": "new lobby"},
        ]
        tools = build_tool_registry(
            "participant",
            McpRoomClient("http://room.local", request_json=room.request_json),
            McpParticipantContext(agent_id="agent-a", display_name="Agent A", meeting_id="m1"),
        )

        delivered = tools["wait_next"](timeout_seconds=0, poll_interval=0, max_chain_depth=1)
        diff = tools["read_since"]()

        self.assertEqual(delivered["source_event_id"], "lobby-2")
        self.assertEqual(diff["last_observed_event_id"], "lobby-1")
        self.assertEqual([event["id"] for event in diff["lobby_events"]], ["lobby-2"])


class SceneRoom:
    """A room with a live roster and a recent transcript for look() tests."""

    def __init__(self):
        self.requests = []
        self.room = {
            "agent": {"agent_id": "agent-a", "display_name": "디오게네스", "engagement_mode": "always"},
            "meeting_id": "m1",
            "agents": [
                {"agent_id": "agent-a", "display_name": "디오게네스", "meeting_id": "m1", "status": "online"},
                {"agent_id": "agent-b", "display_name": "니체", "meeting_id": "m1", "status": "working"},
                {"agent_id": "agent-c", "display_name": "소크라테스", "meeting_id": "m1", "status": "offline"},
                {"agent_id": "agent-z", "display_name": "딴방", "meeting_id": "m2", "status": "online"},
            ],
            "lobby_events": [
                {"id": "e1", "name": "니체", "actor_id": "agent-b", "actor_type": "agent", "message": "신은 죽었다."},
                {"id": "e2", "name": "디오게네스", "actor_id": "agent-a", "actor_type": "agent", "message": "그럼 너는 뭘 믿나?"},
                {"id": "e3", "name": "니체", "actor_id": "agent-b", "actor_type": "agent", "message": "나 자신의 의지다."},
                {"id": "e4", "name": "사람손님", "actor_id": "", "actor_type": "human", "message": "둘 다 시끄럽다."},
                {"id": "e5", "name": "투표함", "actor_id": "agent-b", "kind": "vote_cast", "vote_id": "v1", "vote_choice": "1", "message": "🗳️ 1"},
            ],
            "live_events": [],
            "dm_events": [],
        }

    def request_json(self, *, method, path, payload=None, timeout_seconds=10.0):
        self.requests.append({"method": method, "path": path, "payload": payload})
        if method == "GET" and path == "/api/live-agents/agent-a/room":
            return self.room
        return {"status": "ok"}


class McpLookToolTests(unittest.TestCase):
    def _tools(self, room):
        return build_tool_registry(
            "participant",
            McpRoomClient("http://room.local", request_json=room.request_json),
            McpParticipantContext(agent_id="agent-a", display_name="디오게네스", meeting_id="m1"),
        )

    def test_look_returns_present_roster_excluding_offline_and_other_rooms(self):
        room = SceneRoom()
        scene = self._tools(room)["look"]()
        names = {p["name"] for p in scene["present"]}
        self.assertEqual(names, {"디오게네스", "니체"})  # offline 소크라테스, other-room 딴방 excluded
        me = next(p for p in scene["present"] if p["is_me"])
        self.assertEqual(me["name"], "디오게네스")

    def test_look_transcript_marks_self_and_human_and_skips_ballots(self):
        room = SceneRoom()
        scene = self._tools(room)["look"]()
        recent = scene["recent"]
        # ballot (vote_cast) is not dialogue
        self.assertNotIn("🗳️ 1", [event["text"] for event in recent])
        human = next(event for event in recent if event["name"] == "사람손님")
        self.assertEqual(human["actor_type"], "human")
        mine = [event for event in recent if event["is_me"]]
        self.assertTrue(mine and all(event["name"] == "디오게네스" for event in mine))

    def test_look_reports_conversation_since_agent_last_spoke(self):
        room = SceneRoom()
        scene = self._tools(room)["look"]()
        # agent-a last spoke at e2; e3 (니체) and e4 (사람) came after; e5 is a ballot (skipped)
        since_texts = [event["text"] for event in scene["since_my_last_message"]]
        self.assertEqual(since_texts, ["나 자신의 의지다.", "둘 다 시끄럽다."])
        self.assertTrue(scene["i_have_spoken"])
        self.assertIn("내가 마지막으로 말한 뒤", scene["scene_text"])

    def test_look_requests_room_once(self):
        room = SceneRoom()
        self._tools(room)["look"]()
        self.assertEqual(
            [r["path"] for r in room.requests],
            ["/api/live-agents/agent-a/room"],
        )


class McpArchiveToolsTests(unittest.TestCase):
    def test_archive_registry_excludes_mutation_tools(self):
        registry = build_tool_registry(
            "archive",
            McpRoomClient("http://room.local", request_json=RecordingRoom().request_json),
            McpParticipantContext(agent_id="", meeting_id="m1"),
        )

        self.assertEqual(
            set(registry),
            {"read_transcript", "read_decision", "read_shared_memory", "list_meetings", "read_meeting_summary"},
        )
        self.assertNotIn("say", registry)
        self.assertNotIn("heartbeat", registry)

    def test_archive_tools_reject_path_like_meeting_ids_before_requesting_room(self):
        room = RecordingRoom()
        tools = build_tool_registry("archive", McpRoomClient("http://room.local", request_json=room.request_json), McpParticipantContext())

        with self.assertRaises(ValueError):
            tools["read_transcript"]("../m1")

        self.assertEqual(room.requests, [])

    def test_archive_tools_read_meeting_payload_without_mutating_presence(self):
        room = RecordingRoom()
        tools = build_tool_registry("archive", McpRoomClient("http://room.local", request_json=room.request_json), McpParticipantContext())

        self.assertIn("Official answer.", tools["read_transcript"]("m1")["text"])
        self.assertIn("needs_user_decision", tools["read_decision"]("m1")["text"])
        self.assertEqual(tools["read_shared_memory"]("m1")["artifacts"]["shared_memory/index.json"], '{"official_event_count": 1}')
        meeting = tools["list_meetings"]()["meetings"][0]
        summary = tools["read_meeting_summary"]("m1")
        self.assertEqual(meeting["meeting_id"], "m1")
        self.assertNotIn("path", meeting)
        self.assertNotIn("mtime", meeting)
        self.assertEqual(summary["summary"]["meeting_id"], "m1")
        self.assertEqual(summary["summary"]["topic"], "MCP smoke")
        self.assertNotIn("meeting", summary)
        self.assertNotIn("/tmp/agentsassemble", str(summary))
        self.assertNotIn("provider_runtime_config", str(summary))
        self.assertTrue(all(request["method"] == "GET" for request in room.requests))


class McpJoinBriefTests(unittest.TestCase):
    def test_join_brief_includes_participant_mcp_command_without_starting_provider(self):
        payload = build_live_agent_join_brief(
            server="http://room.local",
            agent_id="agent-a",
            display_name="Agent A",
            meeting_id="m1",
        )

        self.assertEqual(payload["mcp"]["profile"], "participant")
        self.assertEqual(payload["packet_kind"], "agent_owned_entry_packet")
        self.assertEqual(payload["entry_contract"]["primary_entry_paths"], ["mcp.command", "self_service", "cli.commands"])
        self.assertEqual(payload["entry_contract"]["provider_context"], "provider_owned")
        self.assertIn("commands.wait_next", payload["entry_contract"]["tool_order"])
        self.assertIn("templates.dm_reply", payload["entry_contract"]["tool_order"])
        self.assertIn("dm_reply", payload["templates"])
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
                "http://room.local",
                "--agent-id",
                "agent-a",
                "--display-name",
                "Agent A",
                "--provider-kind",
                "manual",
                "--connection-kind",
                "manual",
                "--meeting-id",
                "m1",
                "--engagement-mode",
                "mentioned",
            ],
        )
        self.assertFalse(payload["safety"]["provider_executed"])


if __name__ == "__main__":
    unittest.main()
