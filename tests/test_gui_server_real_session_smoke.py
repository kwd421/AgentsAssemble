from tests.gui_server_test_support import (
    HTTPError,
    Path,
    Request,
    ThreadingHTTPServer,
    _make_handler,
    _redact_real_session_smoke_lobby_events,
    append_lobby_event,
    connect_live_agent,
    json,
    live_agent_lobby_message_payload,
    live_agent_room_payload,
    patch,
    read_lobby,
    tempfile,
    threading,
    unittest,
    urlopen,
)
from agentsassemble.gui import LIVE_AGENT_LOBBY_LOCK


class GuiServerProcessSmokeTests(unittest.TestCase):

    def test_lobby_append_waits_for_smoke_redaction_lock(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            appended = threading.Event()

            def append_message() -> None:
                append_lobby_event(root, {"name": "human", "message": "serialized"})
                appended.set()

            with LIVE_AGENT_LOBBY_LOCK:
                thread = threading.Thread(target=append_message)
                thread.start()
                self.assertFalse(appended.wait(0.05))
            thread.join(timeout=1)

        self.assertTrue(appended.is_set())
        self.assertFalse(thread.is_alive())

    def test_live_agent_real_session_smoke_endpoint_rejects_missing_approval_without_launching(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "room"
            root.mkdir()
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/live-agent-real-session-smoke",
                    data=json.dumps(
                        {
                            "group_id": "real-smoke",
                            "meeting_id": "real-smoke-meeting",
                            "live_agent_config_path": "/Users/me/private/live-agents.real.json",
                        }
                    ).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with patch("agentsassemble.gui.run_live_agent_real_session_smoke") as real_smoke:
                    real_smoke.side_effect = AssertionError("missing approval must not start a real smoke")
                    with self.assertRaises(HTTPError) as raised:
                        urlopen(request, timeout=4)
                body = raised.exception.read().decode("utf-8")
                error_payload = json.loads(body)
                raised.exception.close()
                with urlopen(
                    f"http://127.0.0.1:{server.server_port}/api/live-agent-operations?limit=20",
                    timeout=4,
                ) as response:
                    operations = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

        self.assertEqual(raised.exception.code, 400)
        self.assertIn("requires current operator approval", error_payload["error"])
        serialized = json.dumps({"error": error_payload, "operations": operations}, ensure_ascii=False)
        self.assertNotIn("/Users/me", serialized)
        self.assertNotIn("live-agents.real.json", serialized)


    def test_live_agent_real_session_smoke_endpoint_rejects_string_false_approval(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "room"
            root.mkdir()
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/live-agent-real-session-smoke",
                    data=json.dumps(
                        {
                            "group_id": "real-smoke",
                            "meeting_id": "real-smoke-meeting",
                            "live_agent_config_path": "/Users/me/private/live-agents.real.json",
                            "approve_real_providers": "false",
                        }
                    ).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with patch("agentsassemble.gui.run_live_agent_real_session_smoke") as real_smoke:
                    real_smoke.side_effect = AssertionError("string false approval must not start a real smoke")
                    with self.assertRaises(HTTPError) as raised:
                        urlopen(request, timeout=4)
                raised.exception.close()
            finally:
                server.shutdown()
                server.server_close()

        self.assertEqual(raised.exception.code, 400)


    def test_live_agent_real_session_smoke_endpoint_requires_matching_configs(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "room"
            root.mkdir()
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/live-agent-real-session-smoke",
                    data=json.dumps(
                        {
                            "group_id": "real-smoke",
                            "meeting_id": "real-smoke-meeting",
                            "live_agent_config_path": "/Users/me/private/live-agents.real.json",
                            "approve_real_providers": True,
                        }
                    ).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with patch("agentsassemble.gui.run_live_agent_real_session_smoke") as real_smoke:
                    real_smoke.side_effect = AssertionError("missing matching configs must not start a real smoke")
                    with self.assertRaises(HTTPError) as raised:
                        urlopen(request, timeout=4)
                body = raised.exception.read().decode("utf-8")
                error_payload = json.loads(body)
                raised.exception.close()
                with urlopen(
                    f"http://127.0.0.1:{server.server_port}/api/live-agent-operations?limit=20",
                    timeout=4,
                ) as response:
                    operations = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

        self.assertEqual(raised.exception.code, 400)
        self.assertIn("requires explicit", error_payload["error"])
        serialized = json.dumps({"error": error_payload, "operations": operations}, ensure_ascii=False)
        self.assertNotIn("/Users/me", serialized)
        self.assertNotIn("live-agents.real.json", serialized)


    def test_live_agent_real_session_smoke_endpoint_returns_safe_approved_result(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "room"
            root.mkdir()
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            smoke_result = {
                "status": "ok",
                "meeting_id": "real-smoke-meeting",
                "group_id": "real-smoke",
                "approval_required": True,
                "approved": True,
                "diagnostic": True,
                "start_status": "ready",
                "expected_agent_count": 2,
                "connected_agent_count": 2,
                "reply_probe_status": "ok",
                "reply_probe_count": 2,
                "reply_probe_ok_count": 2,
                "stop_status": "stopped",
                "post_stop_process_status": "stopped",
                "live_agent_config_path": "/Users/me/private/live-agents.real.json",
                "reply_probe": {"replies": [{"message": "secret provider output"}]},
                "process": {"config_path": "/Users/me/private/live-agents.real.json"},
            }
            try:
                with patch("agentsassemble.gui.run_live_agent_real_session_smoke", return_value=smoke_result) as real_smoke:
                    request = Request(
                        f"http://127.0.0.1:{server.server_port}/api/live-agent-real-session-smoke",
                        data=json.dumps(
                            {
                                "group_id": "real-smoke",
                                "meeting_id": "real-smoke-meeting",
                                "live_agent_config_path": "/Users/me/private/live-agents.real.json",
                                "council_config_path": "/Users/me/private/council.json",
                                "agent_config_path": "/Users/me/private/agents.json",
                                "timeout": 9,
                                "approve_real_providers": True,
                            }
                        ).encode("utf-8"),
                        headers={"Content-Type": "application/json"},
                        method="POST",
                    )
                    with urlopen(request, timeout=4) as response:
                        payload = json.loads(response.read().decode("utf-8"))
                with urlopen(
                    f"http://127.0.0.1:{server.server_port}/api/live-agent-operations?limit=20",
                    timeout=4,
                ) as response:
                    operations = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

        real_smoke.assert_called_once()
        kwargs = real_smoke.call_args.kwargs
        self.assertEqual(kwargs["server"], f"http://127.0.0.1:{server.server_port}")
        self.assertEqual(kwargs["group_id"], "real-smoke")
        self.assertEqual(kwargs["meeting_id"], "real-smoke-meeting")
        self.assertEqual(kwargs["timeout_seconds"], 9.0)
        self.assertTrue(kwargs["approve_real_providers"])
        self.assertFalse(kwargs["official_round_smoke"])
        self.assertFalse(kwargs["restart_smoke"])
        self.assertEqual(kwargs["output_root"], root)
        self.assertEqual(
            payload,
            {
                "status": "ok",
                "meeting_id": "real-smoke-meeting",
                "group_id": "real-smoke",
                "approval_required": True,
                "approved": True,
                "diagnostic": True,
                "start_status": "ready",
                "expected_agent_count": 2,
                "connected_agent_count": 2,
                "reply_probe_status": "ok",
                "reply_probe_count": 2,
                "reply_probe_ok_count": 2,
                "official_round_smoke": False,
                "official_rounds_status": "unknown",
                "official_round_count": 0,
                "official_answered_round_count": 0,
                "official_timeout_round_count": 0,
                "official_skipped_round_count": 0,
                "restart_smoke": False,
                "restart_status": "unknown",
                "post_restart_expected_agent_count": 0,
                "post_restart_connected_agent_count": 0,
                "post_restart_reply_probe_status": "unknown",
                "post_restart_reply_probe_count": 0,
                "post_restart_reply_probe_ok_count": 0,
                "stop_status": "stopped",
                "post_stop_process_status": "stopped",
            },
        )
        operations_for_smoke = [
            operation for operation in operations["operations"] if operation["operation"] == "session.real_smoke"
        ]
        self.assertEqual(operations_for_smoke[-1]["status"], "success")
        self.assertEqual(operations_for_smoke[-1]["details"]["reply_probe_ok_count"], 2)
        serialized = json.dumps({"payload": payload, "operations": operations}, ensure_ascii=False)
        self.assertNotIn("/Users/me", serialized)
        self.assertNotIn("private", serialized)
        self.assertNotIn("secret provider output", serialized)
        self.assertNotIn("config_path", serialized)


    def test_real_session_smoke_lobby_redaction_removes_probe_and_reply_text(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            probe = append_lobby_event(
                root,
                {
                    "name": "Smoke",
                    "side": "mine",
                    "message": "probe contains private prompt text",
                    "actor_id": "human",
                },
            )
            reply = append_lobby_event(
                root,
                {
                    "name": "Claude",
                    "side": "other-agent",
                    "message": "provider reply contains private output",
                    "actor_id": "claude-live",
                    "source_event_id": probe["id"],
                },
                live_agent_endpoint=True,
            )
            unrelated = append_lobby_event(
                root,
                {
                    "name": "User",
                    "side": "mine",
                    "message": "keep this visible",
                    "actor_id": "human",
                },
            )

            result = _redact_real_session_smoke_lobby_events(root, [str(probe["id"])])
            events = read_lobby(root, limit=None)

        self.assertEqual(result["probe_event_count"], 1)
        self.assertEqual(result["reply_event_count"], 1)
        by_id = {str(event["id"]): event for event in events}
        self.assertEqual(by_id[str(probe["id"])]["message"], "[redacted real session smoke probe]")
        self.assertEqual(by_id[str(reply["id"])]["message"], "[redacted real session smoke reply]")
        self.assertEqual(by_id[str(reply["id"])]["source_event_id"], probe["id"])
        self.assertTrue(by_id[str(reply["id"])]["live_agent_endpoint"])
        self.assertEqual(by_id[str(unrelated["id"])]["message"], "keep this visible")


    def test_real_session_smoke_late_reply_is_redacted_at_append_time(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            connect_live_agent(
                root,
                {
                    "agent_id": "claude-live",
                    "display_name": "Claude Live",
                    "provider_kind": "claude_code",
                    "connection_kind": "live_session",
                    "status": "online",
                },
            )
            probe = append_lobby_event(
                root,
                {
                    "name": "Smoke",
                    "side": "mine",
                    "message": "probe contains private prompt text",
                    "actor_id": "human",
                },
            )
            _redact_real_session_smoke_lobby_events(root, [str(probe["id"])])

            result = live_agent_lobby_message_payload(
                root,
                "claude-live",
                {
                    "message": "late provider reply contains private output",
                    "source_event_id": probe["id"],
                },
            )
            events = read_lobby(root, limit=None)

        self.assertEqual(result["event"]["message"], "[redacted real session smoke reply]")
        serialized = json.dumps({"result": result, "events": events}, ensure_ascii=False)
        self.assertNotIn("late provider reply contains private output", serialized)


    def test_live_agent_lobby_payload_preserves_long_provider_reply(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            connect_live_agent(
                root,
                {
                    "agent_id": "kiro-live",
                    "display_name": "Kiro Opus 4.7",
                    "provider_kind": "kiro_live_session",
                    "connection_kind": "live_session",
                    "status": "online",
                },
            )
            long_reply = (
                "Kiro Opus 4.7은 0.5와 80kg 같은 토큰을 살려서 말해야 하고, 모르겠다... 같은 말줄임도 "
                "중간에서 끊기면 안 됩니다. "
                + "실제 회의 발언이 길어지는 상황을 검증하기 위한 문장입니다. " * 10
            )

            result = live_agent_lobby_message_payload(
                root,
                "kiro-live",
                {
                    "message": long_reply,
                    "source_event_id": "human-event",
                    "flow_id": "flow-demo",
                    "flow_action": "speak",
                },
            )
            events = read_lobby(root, limit=None)

        self.assertGreater(len(long_reply), 240)
        self.assertEqual(result["event"]["message"], long_reply.strip()[:2000])
        self.assertEqual(events[0]["message"], long_reply.strip()[:2000])
        self.assertTrue(events[0]["live_agent_endpoint"])
        self.assertEqual(events[0]["flow_id"], "flow-demo")
        self.assertIn("Kiro Opus 4.7", events[0]["message"])
        self.assertIn("0.5", events[0]["message"])
        self.assertIn("80kg", events[0]["message"])
        self.assertIn("모르겠다...", events[0]["message"])


    def test_live_agent_room_payload_scopes_lobby_events_to_agent_meeting(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            connect_live_agent(
                root,
                {
                    "agent_id": "codex-room",
                    "display_name": "Codex Room QA",
                    "provider_kind": "codex_live_session",
                    "connection_kind": "live_session",
                    "meeting_id": "room-a",
                    "status": "online",
                },
            )
            append_lobby_event(root, {"name": "old", "side": "mine", "message": "global old"})
            append_lobby_event(
                root,
                {"name": "room b", "side": "mine", "message": "other room", "flow_meeting_id": "room-b"},
                allow_flow_metadata=True,
            )
            room_event = append_lobby_event(
                root,
                {"name": "room a", "side": "mine", "message": "current room", "flow_meeting_id": "room-a"},
                allow_flow_metadata=True,
            )

            payload = live_agent_room_payload(root, "codex-room")

        self.assertEqual([event["id"] for event in payload["lobby_events"]], [room_event["id"]])


    def test_live_agent_lobby_payload_preserves_agent_meeting_scope_without_flow_id(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            connect_live_agent(
                root,
                {
                    "agent_id": "codex-room",
                    "display_name": "Codex Room QA",
                    "provider_kind": "codex_live_session",
                    "connection_kind": "live_session",
                    "meeting_id": "room-a",
                    "status": "online",
                },
            )

            result = live_agent_lobby_message_payload(
                root,
                "codex-room",
                {"message": "방 안에서만 보이는 답", "source_event_id": "human-event"},
            )

        self.assertEqual(result["event"]["flow_meeting_id"], "room-a")
