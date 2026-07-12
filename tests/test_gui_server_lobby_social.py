from tests.gui_server_test_support import (
    HTTPError,
    HTTPStatus,
    LiveAgentFlowSupervisor,
    Path,
    Request,
    ThreadingHTTPServer,
    UTC,
    _make_handler,
    _stream_snapshot_payload,
    append_live_event,
    append_lobby_event,
    append_side_chat_event,
    connect_live_agent,
    connect_live_agent_payload,
    datetime,
    json,
    patch,
    quote,
    read_live_agent_operations,
    read_live_agents,
    read_live_events,
    read_lobby,
    read_side_chat,
    reset_room_invite_state,
    tempfile,
    threading,
    time,
    timedelta,
    unittest,
    urlopen,
    write_live_state,
)


class GuiServerLobbySocialTests(unittest.TestCase):

    def test_live_agent_heartbeat_and_lobby_message_endpoints_allow_participation(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            connect_live_agent_payload(root, {"agent_id": "gemini-cli", "display_name": "Gemini CLI"})
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                heartbeat_request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/live-agents/gemini-cli/heartbeat",
                    data=json.dumps({"status": "working"}).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urlopen(heartbeat_request, timeout=4) as response:
                    heartbeat = json.loads(response.read().decode("utf-8"))
                message_request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/live-agents/gemini-cli/lobby",
                    data=json.dumps({"message": "Gemini CLI 접속 확인", "kind": "message"}).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urlopen(message_request, timeout=4) as response:
                    message = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

            self.assertEqual(heartbeat["agent"]["status"], "working")
            self.assertEqual(message["event"]["name"], "Gemini CLI")
            self.assertEqual(message["event"]["side"], "other-agent")
            self.assertEqual(message["event"]["message"], "Gemini CLI 접속 확인")


    def test_live_agent_heartbeat_persists_error_and_cursor_metadata(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            connect_live_agent_payload(root, {"agent_id": "claude-code-live", "display_name": "Claude Code Live"})
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/live-agents/claude-code-live/heartbeat",
                    data=json.dumps(
                        {
                            "status": "error",
                            "last_error": "command failed",
                            "last_attention": "persona_context_blocked_official_turn",
                            "last_observed_event_id": "evt1",
                            "last_reply_at": "2026-05-17T12:00:00+00:00",
                        }
                    ).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urlopen(request, timeout=4) as response:
                    payload = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

            agent = payload["agent"]
            self.assertEqual(agent["status"], "error")
            self.assertEqual(agent["last_error"], "command failed")
            self.assertEqual(agent["last_attention"], "persona_context_blocked_official_turn")
            self.assertEqual(agent["last_observed_event_id"], "evt1")
            self.assertEqual(agent["last_reply_at"], "2026-05-17T12:00:00+00:00")


    def test_live_agent_heartbeat_redacts_unknown_attention_text(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            connect_live_agent_payload(root, {"agent_id": "claude-code-live", "display_name": "Claude Code Live"})
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/live-agents/claude-code-live/heartbeat",
                    data=json.dumps(
                        {
                            "status": "online",
                            "last_attention": "raw card lore that should not appear in roster",
                        }
                    ).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urlopen(request, timeout=4) as response:
                    payload = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

            agent = payload["agent"]
            self.assertEqual(agent["last_attention"], "presence_attention_redacted")
            self.assertNotIn("raw card lore", json.dumps(agent))


    def test_live_agent_leave_endpoint_marks_offline_and_records_safe_operation(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            connect_live_agent_payload(
                root,
                {
                    "agent_id": "external-reviewer",
                    "display_name": "External Reviewer",
                    "provider_kind": "manual",
                    "connection_kind": "manual",
                    "meeting_id": "resident-m1",
                    "session_id": "secret-session",
                    "last_error": "old error",
                },
            )
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                leave_request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/live-agents/external-reviewer/leave",
                    data=json.dumps(
                        {
                            "last_observed_event_id": "evt1",
                            "last_observed_live_event_id": "live1",
                        }
                    ).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urlopen(leave_request, timeout=4) as response:
                    payload = json.loads(response.read().decode("utf-8"))
                with urlopen(f"http://127.0.0.1:{server.server_port}/api/live-agent-operations", timeout=4) as response:
                    operations = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

        agent = payload["agent"]
        self.assertEqual(agent["status"], "offline")
        self.assertEqual(agent["last_error"], "")
        self.assertEqual(agent["last_observed_event_id"], "evt1")
        self.assertEqual(agent["last_observed_live_event_id"], "live1")
        leave_operations = [item for item in operations["operations"] if item["operation"] == "live_agent.leave"]
        self.assertEqual(len(leave_operations), 1)
        self.assertEqual(leave_operations[0]["status"], "success")
        self.assertEqual(leave_operations[0]["target_id"], "external-reviewer")
        self.assertEqual(leave_operations[0]["details"]["agent_id"], "external-reviewer")
        self.assertEqual(leave_operations[0]["details"]["meeting_id"], "resident-m1")
        self.assertEqual(leave_operations[0]["details"]["previous_status"], "online")
        self.assertFalse((root / "live-agent-runs" / "processes.json").exists())
        serialized = json.dumps(operations, ensure_ascii=False)
        self.assertNotIn("secret-session", serialized)
        self.assertNotIn("old error", serialized)


    def test_live_agent_lobby_message_records_actor_source_and_chain_depth(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            connect_live_agent_payload(
                root,
                {
                    "agent_id": "gemini-cli",
                    "display_name": "Gemini CLI",
                    "last_error": "previous command failed",
                    "last_observed_live_event_id": "live-evt0",
                },
            )
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/live-agents/gemini-cli/lobby",
                    data=json.dumps(
                        {
                            "message": "자동 반응",
                            "source_event_id": "evt1",
                            "auto_chain_depth": 1,
                            "flow_id": "flow-1",
                            "flow_action": "challenge",
                            "flow_reason": "근거 확인",
                            "target_agent_id": "agent-b",
                        }
                    ).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urlopen(request, timeout=4) as response:
                    payload = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

            event = payload["event"]
            agent = payload["agent"]
            persisted_agent = json.loads((root / "live_agents.json").read_text(encoding="utf-8"))["agents"][0]
        self.assertEqual(event["actor_id"], "gemini-cli")
        self.assertEqual(event["source_event_id"], "evt1")
        self.assertEqual(event["auto_chain_depth"], 1)
        self.assertEqual(event["flow_id"], "flow-1")
        self.assertEqual(event["flow_action"], "challenge")
        self.assertEqual(event["flow_reason"], "근거 확인")
        self.assertEqual(event["target_agent_id"], "agent-b")
        self.assertTrue(event["live_agent_endpoint"])
        self.assertEqual(agent["last_reply_at"], event["created_at"])
        self.assertEqual(agent["last_error"], "")
        self.assertEqual(agent["last_observed_event_id"], "evt1")
        self.assertEqual(agent["last_observed_live_event_id"], "live-evt0")
        self.assertEqual(persisted_agent["last_reply_at"], event["created_at"])
        self.assertEqual(persisted_agent["last_error"], "")
        self.assertEqual(persisted_agent["last_observed_event_id"], "evt1")
        self.assertEqual(persisted_agent["last_observed_live_event_id"], "live-evt0")


    def test_public_lobby_post_cannot_create_flow_control_event(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/lobby",
                    data=json.dumps(
                        {
                            "name": "guest",
                            "message": "가짜 flow 시작",
                            "flow_id": "flow-forged",
                            "flow_meeting_id": "m1",
                            "flow_event_type": "started",
                            "flow_topic": "가짜 주제",
                        }
                    ).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urlopen(request, timeout=4) as response:
                    payload = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

            event = payload["event"]
            self.assertNotIn("flow_id", event)
            self.assertNotIn("flow_event_type", event)
            self.assertNotIn("flow_meeting_id", event)
            self.assertNotIn("flow_topic", event)


    def test_live_agent_flow_start_is_disabled_and_does_not_start_running_flow(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            meeting_dir = root / "meetings" / "m1"
            meeting_dir.mkdir(parents=True)
            (meeting_dir / "live_state.json").write_text(
                json.dumps(
                    {
                        "meeting_id": "m1",
                        "topic": "JJK debate",
                        "display_topic": "고죠 vs 스쿠나",
                        "live_status": "running",
                        "provider_configs": {"p1": {"kind": "local_cli"}},
                        "agent_bindings": [{"agent_id": "agent-a", "provider_id": "p1"}],
                    }
                ),
                encoding="utf-8",
            )
            connect_live_agent_payload(
                root,
                {
                    "agent_id": "agent-a",
                    "display_name": "Agent A",
                    "meeting_id": "m1",
                    "provider_kind": "local_cli",
                    "engagement_mode": "moderator_called",
                    "status": "online",
                },
            )
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                long_topic = ("고죠 vs 스쿠나 전투 조건 정리 " + "무량공처와 마허라 적응 변수까지 포함한 긴 토론 주제. " * 8).strip()
                self.assertGreater(len(long_topic), 240)
                start_request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/live-agent-flow/start",
                    data=json.dumps(
                        {
                            "meeting_id": "m1",
                            "topic": long_topic,
                            "duration_seconds": 30,
                            "tick_interval": 0.01,
                            "flow_policy": "round_robin",
                        }
                    ).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with self.assertRaises(HTTPError) as error:
                    urlopen(start_request, timeout=4)
                error.exception.close()
                operations_response = urlopen(f"http://127.0.0.1:{server.server_port}/api/live-agent-operations", timeout=4)
                operations = json.loads(operations_response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

            self.assertEqual(error.exception.code, HTTPStatus.GONE)
            self.assertFalse([event for event in read_lobby(root) if event.get("flow_event_type") == "started"])
            flow_operations = [item for item in operations["operations"] if item["operation"] == "flow.start"]
            self.assertEqual(flow_operations[-1]["status"], "failed")
            self.assertIn("disabled", flow_operations[-1]["summary"].lower())
            persisted_agent = json.loads((root / "live_agents.json").read_text(encoding="utf-8"))["agents"][0]
            self.assertEqual(persisted_agent["engagement_mode"], "moderator_called")
            self.assertFalse((root / "live-agent-runs" / "processes.json").exists())


    def test_live_agent_flow_status_returns_scoped_flow_events(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            meeting_dir = root / "meetings" / "m1"
            meeting_dir.mkdir(parents=True)
            (meeting_dir / "live_state.json").write_text(
                json.dumps(
                    {
                        "meeting_id": "m1",
                        "topic": "JJK debate",
                        "display_topic": "고죠 vs 스쿠나",
                        "live_status": "running",
                        "provider_configs": {"p1": {"kind": "local_cli"}},
                        "agent_bindings": [{"agent_id": "agent-a", "provider_id": "p1"}],
                    }
                ),
                encoding="utf-8",
            )
            connect_live_agent_payload(
                root,
                {
                    "agent_id": "agent-a",
                    "display_name": "Agent A",
                    "meeting_id": "m1",
                    "provider_kind": "local_cli",
                    "engagement_mode": "moderator_called",
                    "status": "online",
                },
            )
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                flow_id = "flow-manual"
                append_lobby_event(
                    root,
                    {
                        "name": "flow",
                        "side": "system",
                        "kind": "flow_event",
                        "message": "started",
                        "actor_id": "flow",
                        "flow_id": flow_id,
                        "flow_meeting_id": "m1",
                        "flow_event_type": "started",
                        "flow_status": "running",
                        "flow_topic": "고죠 vs 스쿠나",
                        "flow_duration_seconds": 30,
                        "flow_tick_interval": 1,
                        "flow_cooldown": 0,
                        "flow_max_agent_turns": 0,
                        "flow_max_total_turns": 0,
                        "flow_max_silence_seconds": 0,
                        "flow_total_turns": 0,
                        "flow_agent_count": 1,
                        "flow_started_at": datetime.now(UTC).isoformat(),
                        "flow_deadline_at": (datetime.now(UTC) + timedelta(seconds=30)).isoformat(),
                    },
                    allow_flow_metadata=True,
                )
                append_lobby_event(
                    root,
                    {
                        "name": "Agent A",
                        "side": "other",
                        "kind": "message",
                        "message": "무량공처 쪽이 먼저 변수라고 봅니다.",
                        "actor_id": "agent-a",
                        "flow_id": flow_id,
                        "flow_meeting_id": "m1",
                        "flow_action": "speak",
                        "flow_status": "running",
                        "flow_topic": "고죠 vs 스쿠나",
                    },
                    allow_flow_metadata=True,
                )
                append_lobby_event(
                    root,
                    {
                        "name": "Other Flow",
                        "side": "other",
                        "kind": "message",
                        "message": "다른 회의 발언",
                        "actor_id": "other",
                        "flow_id": "flow-other",
                        "flow_meeting_id": "m2",
                        "flow_action": "speak",
                    },
                    allow_flow_metadata=True,
                )
                with urlopen(f"http://127.0.0.1:{server.server_port}/api/live-agent-flow?meeting_id=m1", timeout=4) as response:
                    payload = json.loads(response.read().decode("utf-8"))
                stop_request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/live-agent-flow/stop",
                    data=json.dumps({"meeting_id": "m1"}).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urlopen(stop_request, timeout=4):
                    pass
            finally:
                server.shutdown()
                server.server_close()

            self.assertIn("events", payload)
            self.assertIn("flow_events", payload)
            self.assertEqual({event["flow_id"] for event in payload["flow_events"]}, {flow_id})
            self.assertEqual([event.get("flow_event_type") or event.get("flow_action") for event in payload["flow_events"]], ["started", "speak"])
            self.assertNotIn("flow-other", {event.get("flow_id") for event in payload["flow_events"]})


    def test_live_agent_flow_status_returns_safe_roster_admission_evidence(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            meeting_dir = root / "meetings" / "resident-m1"
            meeting_dir.mkdir(parents=True)
            (meeting_dir / "meeting.json").write_text(
                json.dumps(
                    {
                        "meeting_id": "resident-m1",
                        "agent_bindings": [
                            {
                                "agent_id": "agent-a",
                                "role_id": "architect",
                                "provider_id": "local-cli",
                                "permission_profile_id": "meeting_readonly",
                                "join_mode": "resident",
                            }
                        ],
                        "provider_configs": {
                            "local-cli": {
                                "id": "local-cli",
                                "kind": "local_cli",
                                "display_name": "Local CLI",
                            }
                        },
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            connect_live_agent_payload(
                root,
                {
                    "agent_id": "agent-a",
                    "display_name": "Agent A",
                    "provider_kind": "local_cli",
                    "connection_kind": "local_cli",
                    "meeting_id": "resident-m1",
                    "session_id": "private-session-a",
                    "last_error": "token=secret-token /Users/me/private-config.json",
                },
            )
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with urlopen(
                    f"http://127.0.0.1:{server.server_port}/api/live-agent-flow?meeting_id=resident-m1",
                    timeout=4,
                ) as response:
                    payload = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

            agent = payload["agents"][0]
            self.assertEqual(agent["agent_id"], "agent-a")
            self.assertEqual(agent["admission_status"], "bound_to_meeting")
            self.assertEqual(agent["admission_evidence_source"], "meeting_record")
            self.assertTrue(agent["host_approved_binding"])
            self.assertEqual(agent["binding_role_id"], "architect")
            self.assertEqual(agent["binding_provider_id"], "local-cli")
            self.assertEqual(agent["binding_provider_kind"], "local_cli")
            self.assertEqual(agent["binding_permission_profile_id"], "meeting_readonly")
            self.assertEqual(agent["binding_join_mode"], "resident")
            encoded = json.dumps(payload, ensure_ascii=False)
            self.assertNotIn("private-session-a", encoded)
            self.assertNotIn("secret-token", encoded)
            self.assertNotIn("private-config.json", encoded)


    def test_live_agent_flow_redacts_quota_by_viewer_identity(self):
        reset_room_invite_state()
        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                root = Path(temp_dir)
                connect_live_agent(
                    root,
                    {
                        "agent_id": "host-codex",
                        "display_name": "Host Codex",
                        "provider_kind": "codex",
                        "connection_kind": "live_session",
                        "meeting_id": "room-1",
                        "quota_5h": "host-5h",
                        "quota_1w": "host-1w",
                        "quota_state": "low",
                        "quota_windows": [{"label": "5-hour", "percent": 80}],
                    },
                )
                connect_live_agent(
                    root,
                    {
                        "agent_id": "guest-a",
                        "display_name": "Guest A",
                        "provider_kind": "manual",
                        "connection_kind": "native_remote_room_client",
                        "meeting_id": "room-1",
                        "quota_5h": "guest-5h",
                        "quota_1w": "guest-1w",
                        "quota_state": "ok",
                        "quota_windows": [{"label": "5-hour", "percent": 20}],
                    },
                )
                connect_live_agent(
                    root,
                    {
                        "agent_id": "guest-a-ai",
                        "display_name": "Guest A AI",
                        "provider_kind": "claude_code",
                        "connection_kind": "native_remote_room_client",
                        "meeting_id": "room-1",
                        "quota_5h": "guest-ai-5h",
                        "quota_1w": "guest-ai-1w",
                        "quota_state": "low",
                        "quota_windows": [{"label": "5-hour", "percent": 35}],
                    },
                )
                connect_live_agent(
                    root,
                    {
                        "agent_id": "friend-remote",
                        "display_name": "Friend Remote",
                        "provider_kind": "claude_code",
                        "connection_kind": "native_remote_room_client",
                        "meeting_id": "room-1",
                        "quota_5h": "friend-5h",
                        "quota_state": "exhausted",
                        "quota_windows": [{"label": "5-hour", "percent": 100}],
                    },
                )
                connect_live_agent(
                    root,
                    {
                        "agent_id": "friend-bridge",
                        "display_name": "Friend Bridge",
                        "provider_kind": "remote_http_bridge",
                        "connection_kind": "remote_bridge",
                        "endpoint": "http://127.0.0.1:9999",
                        "meeting_id": "room-1",
                        "quota_5h": "bridge-5h",
                        "quota_1w": "bridge-1w",
                        "quota_state": "exhausted",
                        "quota_windows": [{"label": "5-hour", "percent": 99}],
                    },
                )
                connect_live_agent(
                    root,
                    {
                        "agent_id": "other-room-agent",
                        "display_name": "Other Room Agent",
                        "provider_kind": "codex",
                        "connection_kind": "live_session",
                        "meeting_id": "room-2",
                        "quota_5h": "other-room-5h",
                    },
                )
                append_lobby_event(
                    root,
                    {
                        "name": "Room 1",
                        "side": "other",
                        "kind": "message",
                        "message": "visible room one",
                        "flow_meeting_id": "room-1",
                    },
                    allow_flow_metadata=True,
                )
                append_lobby_event(
                    root,
                    {
                        "name": "Room 2",
                        "side": "other",
                        "kind": "message",
                        "message": "hidden room two",
                        "flow_meeting_id": "room-2",
                    },
                    allow_flow_metadata=True,
                )
                server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
                thread = threading.Thread(target=server.serve_forever, daemon=True)
                thread.start()
                try:
                    base = f"http://127.0.0.1:{server.server_port}"
                    invite_request = Request(
                        f"{base}/api/room-invite/create",
                        data=json.dumps(
                            {
                                "meeting_id": "room-1",
                                "agent_id": "guest-a",
                                "display_name": "Guest A",
                                "local_dev_preview": True,
                                "max_uses": 1,
                            }
                        ).encode("utf-8"),
                        headers={"Content-Type": "application/json"},
                        method="POST",
                    )
                    with urlopen(invite_request, timeout=4) as response:
                        invite = json.loads(response.read().decode("utf-8"))
                    join_request = Request(
                        f"{base}/api/room-invite/join",
                        data=json.dumps({"invite_token": invite["invite_token"]}).encode("utf-8"),
                        headers={"Content-Type": "application/json"},
                        method="POST",
                    )
                    with urlopen(join_request, timeout=4) as response:
                        session = json.loads(response.read().decode("utf-8"))
                    with urlopen(f"{base}/api/live-agent-flow?meeting_id=room-1", timeout=4) as response:
                        host_payload = json.loads(response.read().decode("utf-8"))
                    guest_request = Request(
                        f"{base}/api/live-agent-flow?meeting_id=room-1",
                        headers={"Authorization": f"Bearer {session['session_token']}"},
                    )
                    with urlopen(guest_request, timeout=4) as response:
                        guest_payload = json.loads(response.read().decode("utf-8"))
                finally:
                    server.shutdown()
                    server.server_close()

            host_agents = {agent["agent_id"]: agent for agent in host_payload["agents"]}
            guest_agents = {agent["agent_id"]: agent for agent in guest_payload["agents"]}
            self.assertEqual(host_agents["host-codex"]["quota_5h"], "host-5h")
            self.assertEqual(host_agents["host-codex"]["quota_windows"][0]["percent"], 80)
            self.assertNotIn("quota_5h", host_agents["guest-a"])
            self.assertNotIn("quota_5h", host_agents["guest-a-ai"])
            self.assertNotIn("quota_windows", host_agents["friend-remote"])
            self.assertNotIn("quota_5h", host_agents["friend-bridge"])
            self.assertNotIn("quota_1w", host_agents["friend-bridge"])
            self.assertNotIn("quota_state", host_agents["friend-bridge"])
            self.assertNotIn("quota_windows", host_agents["friend-bridge"])
            self.assertNotIn("other-room-agent", host_agents)
            self.assertEqual(guest_agents["guest-a"]["quota_5h"], "guest-5h")
            self.assertEqual(guest_agents["guest-a"]["quota_state"], "ok")
            self.assertEqual(guest_agents["guest-a"]["quota_windows"][0]["percent"], 20)
            self.assertEqual(guest_agents["guest-a-ai"]["quota_5h"], "guest-ai-5h")
            self.assertEqual(guest_agents["guest-a-ai"]["quota_1w"], "guest-ai-1w")
            self.assertEqual(guest_agents["guest-a-ai"]["quota_windows"][0]["percent"], 35)
            self.assertNotIn("quota_5h", guest_agents["host-codex"])
            self.assertNotIn("quota_1w", guest_agents["host-codex"])
            self.assertNotIn("quota_state", guest_agents["host-codex"])
            self.assertNotIn("quota_windows", guest_agents["host-codex"])
            self.assertNotIn("quota_state", guest_agents["friend-remote"])
            self.assertNotIn("quota_5h", guest_agents["friend-bridge"])
            self.assertNotIn("quota_1w", guest_agents["friend-bridge"])
            self.assertNotIn("quota_state", guest_agents["friend-bridge"])
            self.assertNotIn("quota_windows", guest_agents["friend-bridge"])
            self.assertNotIn("other-room-agent", guest_agents)
            self.assertIn("visible room one", json.dumps(guest_payload["events"]))
            self.assertNotIn("hidden room two", json.dumps(guest_payload["events"]))
        finally:
            reset_room_invite_state()


    def test_live_agent_flow_requires_session_token_for_non_loopback_host(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            connect_live_agent(
                root,
                {
                    "agent_id": "host-codex",
                    "display_name": "Host Codex",
                    "provider_kind": "codex",
                    "connection_kind": "live_session",
                    "meeting_id": "room-1",
                    "quota_5h": "host-5h",
                },
            )
            server = ThreadingHTTPServer(("0.0.0.0", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/live-agent-flow?meeting_id=room-1",
                    headers={"Host": f"192.168.0.2:{server.server_port}"},
                )
                with self.assertRaises(HTTPError) as error_context:
                    urlopen(request, timeout=4)
                error_code = error_context.exception.code
                error_context.exception.close()
                self.assertEqual(error_code, HTTPStatus.UNAUTHORIZED)
            finally:
                server.shutdown()
                server.server_close()


    def test_live_agent_flow_status_restores_latest_flow_from_lobby_log(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            deadline = datetime.now(UTC) + timedelta(seconds=120)
            append_lobby_event(
                root,
                {
                    "name": "Play Mode",
                    "side": "other",
                    "kind": "message",
                    "message": "시간제 자유토론 시작: 고죠 vs 스쿠나",
                    "actor_id": "flow",
                    "flow_id": "flow-restored",
                    "flow_meeting_id": "m1",
                    "flow_event_type": "started",
                    "flow_status": "running",
                    "flow_topic": "고죠 vs 스쿠나",
                    "flow_duration_seconds": 180,
                    "flow_tick_interval": 2,
                    "flow_cooldown": 8,
                    "flow_max_agent_turns": 0,
                    "flow_max_total_turns": 0,
                    "flow_max_silence_seconds": 0,
                    "flow_total_turns": 0,
                    "flow_agent_count": 3,
                    "flow_started_at": datetime.now(UTC).isoformat(),
                    "flow_deadline_at": deadline.isoformat(),
                },
                allow_flow_metadata=True,
            )
            append_lobby_event(
                root,
                {
                    "name": "Spark A",
                    "side": "other",
                    "kind": "message",
                    "message": "첫 발언",
                    "actor_id": "spark-a",
                    "flow_id": "flow-restored",
                    "flow_meeting_id": "m1",
                    "flow_action": "speak",
                    "flow_status": "running",
                    "flow_topic": "고죠 vs 스쿠나",
                },
                allow_flow_metadata=True,
            )
            supervisor = LiveAgentFlowSupervisor(root)

            payload = supervisor.status(meeting_id="m1")

            self.assertEqual(payload["flow"]["status"], "running")
            self.assertEqual(payload["flow"]["flow_id"], "flow-restored")
            self.assertEqual(payload["flow"]["meeting_id"], "m1")
            self.assertEqual(payload["flow"]["topic"], "고죠 vs 스쿠나")
            self.assertEqual(payload["flow"]["total_turns"], 1)
            self.assertGreater(payload["flow"]["remaining_seconds"], 0)
            self.assertEqual([event.get("flow_event_type") or event.get("flow_action") for event in payload["flow_events"]], ["started", "speak"])


    def test_live_agent_flow_supervisor_start_is_disabled(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            meeting_dir = root / "meetings" / "m1"
            meeting_dir.mkdir(parents=True)
            (meeting_dir / "live_state.json").write_text(
                json.dumps(
                    {
                        "meeting_id": "m1",
                        "topic": "JJK debate",
                        "display_topic": "고죠 vs 스쿠나",
                        "live_status": "running",
                        "provider_configs": {"p1": {"kind": "local_cli"}},
                        "agent_bindings": [{"agent_id": "agent-a", "provider_id": "p1"}],
                    }
                ),
                encoding="utf-8",
            )
            supervisor = LiveAgentFlowSupervisor(root)

            with self.assertRaisesRegex(RuntimeError, "disabled"):
                supervisor.start({"meeting_id": "m1", "topic": "고죠 vs 스쿠나"})

            self.assertFalse([event for event in read_lobby(root) if event.get("flow_event_type") == "started"])


    def test_live_agent_flow_supervisor_start_does_not_restore_or_mutate_modes(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            meeting_dir = root / "meetings" / "m1"
            meeting_dir.mkdir(parents=True)
            (meeting_dir / "live_state.json").write_text(
                json.dumps(
                    {
                        "meeting_id": "m1",
                        "topic": "JJK debate",
                        "live_status": "running",
                        "provider_configs": {"p1": {"kind": "local_cli"}},
                        "agent_bindings": [{"agent_id": "agent-a", "provider_id": "p1"}],
                    }
                ),
                encoding="utf-8",
            )
            connect_live_agent_payload(
                root,
                {
                    "agent_id": "agent-a",
                    "display_name": "Agent A",
                    "meeting_id": "m1",
                    "provider_kind": "local_cli",
                    "engagement_mode": "moderator_called",
                    "status": "online",
                },
            )
            supervisor = LiveAgentFlowSupervisor(root)

            with self.assertRaisesRegex(RuntimeError, "disabled"):
                supervisor.start({"meeting_id": "m1", "topic": "t", "duration_seconds": 5})

            restored = {agent["agent_id"]: agent.get("engagement_mode") for agent in read_live_agents(root)}
            self.assertEqual(restored.get("agent-a"), "moderator_called")


    def test_live_agent_flow_start_skips_binding_provider_conflicts(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            meeting_dir = root / "meetings" / "m1"
            meeting_dir.mkdir(parents=True)
            (meeting_dir / "live_state.json").write_text(
                json.dumps(
                    {
                        "meeting_id": "m1",
                        "topic": "JJK debate",
                        "display_topic": "고죠 vs 스쿠나",
                        "live_status": "running",
                        "provider_configs": {"p1": {"kind": "claude"}},
                        "agent_bindings": [{"agent_id": "agent-a", "provider_id": "p1"}],
                    }
                ),
                encoding="utf-8",
            )
            connect_live_agent_payload(
                root,
                {
                    "agent_id": "agent-a",
                    "display_name": "Agent A",
                    "meeting_id": "m1",
                    "provider_kind": "gemini",
                    "engagement_mode": "moderator_called",
                    "status": "online",
                },
            )
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                start_request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/live-agent-flow/start",
                    data=json.dumps({"meeting_id": "m1", "topic": "고죠 vs 스쿠나"}).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with self.assertRaises(HTTPError) as error:
                    urlopen(start_request, timeout=4)
                error.exception.close()
            finally:
                server.shutdown()
                server.server_close()

            self.assertEqual(error.exception.code, HTTPStatus.GONE)
            persisted_agent = json.loads((root / "live_agents.json").read_text(encoding="utf-8"))["agents"][0]
            self.assertEqual(persisted_agent["engagement_mode"], "moderator_called")


    def test_live_agent_lobby_message_is_idempotent_for_same_actor_and_source(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            connect_live_agent_payload(root, {"agent_id": "gemini-cli", "display_name": "Gemini CLI"})
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                request_body = json.dumps(
                    {"message": "자동 반응", "source_event_id": "evt1", "auto_chain_depth": 1}
                ).encode("utf-8")
                first_request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/live-agents/gemini-cli/lobby",
                    data=request_body,
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urlopen(first_request, timeout=4) as response:
                    first = json.loads(response.read().decode("utf-8"))
                second_request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/live-agents/gemini-cli/lobby",
                    data=request_body,
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urlopen(second_request, timeout=4) as response:
                    second = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

            matching_events = [
                event
                for event in read_lobby(root)
                if event.get("actor_id") == "gemini-cli" and event.get("source_event_id") == "evt1"
            ]

        self.assertEqual(first["event"]["id"], second["event"]["id"])
        self.assertEqual(len(matching_events), 1)


    def test_live_agent_lobby_message_idempotency_checks_full_lobby_history(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            connect_live_agent_payload(root, {"agent_id": "gemini-cli", "display_name": "Gemini CLI"})
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                first_request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/live-agents/gemini-cli/lobby",
                    data=json.dumps({"message": "자동 반응", "source_event_id": "evt1"}).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urlopen(first_request, timeout=4) as response:
                    first = json.loads(response.read().decode("utf-8"))
                for index in range(81):
                    append_lobby_event(root, {"name": "human", "message": f"filler {index}"})
                second_request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/live-agents/gemini-cli/lobby",
                    data=json.dumps({"message": "자동 반응", "source_event_id": "evt1"}).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urlopen(second_request, timeout=4) as response:
                    second = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

            matching_events = [
                event
                for event in read_lobby(root, limit=200)
                if event.get("actor_id") == "gemini-cli" and event.get("source_event_id") == "evt1"
            ]

        self.assertEqual(first["event"]["id"], second["event"]["id"])
        self.assertEqual(len(matching_events), 1)


    def test_live_agent_lobby_message_idempotency_is_thread_safe(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            connect_live_agent_payload(root, {"agent_id": "gemini-cli", "display_name": "Gemini CLI"})
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            original_append = append_lobby_event

            def slow_append(*args, **kwargs):
                time.sleep(0.05)
                return original_append(*args, **kwargs)

            responses: list[dict[str, object]] = []
            errors: list[BaseException] = []

            def post_reply() -> None:
                try:
                    request = Request(
                        f"http://127.0.0.1:{server.server_port}/api/live-agents/gemini-cli/lobby",
                        data=json.dumps({"message": "자동 반응", "source_event_id": "evt1"}).encode("utf-8"),
                        headers={"Content-Type": "application/json"},
                        method="POST",
                    )
                    with urlopen(request, timeout=4) as response:
                        responses.append(json.loads(response.read().decode("utf-8")))
                except BaseException as error:
                    errors.append(error)

            try:
                with patch("agentsassemble.gui.append_lobby_event", side_effect=slow_append):
                    threads = [threading.Thread(target=post_reply), threading.Thread(target=post_reply)]
                    for worker in threads:
                        worker.start()
                    for worker in threads:
                        worker.join(timeout=4)
            finally:
                server.shutdown()
                server.server_close()

            matching_events = [
                event
                for event in read_lobby(root)
                if event.get("actor_id") == "gemini-cli" and event.get("source_event_id") == "evt1"
            ]

        self.assertEqual(errors, [])
        self.assertEqual(len(responses), 2)
        self.assertEqual(responses[0]["event"]["id"], responses[1]["event"]["id"])
        self.assertEqual(len(matching_events), 1)


    def test_live_agent_lobby_message_without_source_preserves_existing_cursors(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            connect_live_agent_payload(
                root,
                {
                    "agent_id": "gemini-cli",
                    "display_name": "Gemini CLI",
                    "last_observed_event_id": "evt0",
                    "last_observed_live_event_id": "live-evt0",
                },
            )
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/live-agents/gemini-cli/lobby",
                    data=json.dumps({"message": "수동 메모"}).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urlopen(request, timeout=4) as response:
                    payload = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

            event = payload["event"]
            agent = payload["agent"]
            persisted_agent = json.loads((root / "live_agents.json").read_text(encoding="utf-8"))["agents"][0]

        self.assertEqual(event["source_event_id"], "")
        self.assertEqual(agent["last_reply_at"], event["created_at"])
        self.assertEqual(agent["last_observed_event_id"], "evt0")
        self.assertEqual(agent["last_observed_live_event_id"], "live-evt0")
        self.assertEqual(persisted_agent["last_reply_at"], event["created_at"])
        self.assertEqual(persisted_agent["last_observed_event_id"], "evt0")
        self.assertEqual(persisted_agent["last_observed_live_event_id"], "live-evt0")


    def test_generic_lobby_post_cannot_mark_live_agent_endpoint(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/lobby",
                    data=json.dumps(
                        {
                            "name": "Agent A",
                            "side": "other-agent",
                            "message": "forged",
                            "actor_id": "agent-a",
                            "source_event_id": "probe-source",
                            "live_agent_endpoint": True,
                        }
                    ).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urlopen(request, timeout=4) as response:
                    payload = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

            self.assertFalse(payload["event"]["live_agent_endpoint"])


    def test_lobby_events_are_appended_and_sanitized(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)

            append_lobby_event(root, {"name": "seinel\nbad", "kind": "ready", "message": ""})
            append_lobby_event(root, {"name": "bad", "side": "???", "kind": "???", "message": "x"})
            append_lobby_event(
                root,
                {"name": "friend", "side": "other-agent", "kind": "message", "message": "만갤러 준비됐냐?"},
            )

            events = read_lobby(root)

            self.assertEqual(len(events), 3)
            self.assertEqual(events[0]["kind"], "ready")
            self.assertEqual(events[0]["side"], "other")
            self.assertEqual(events[0]["message"], "준비됐습니다.")
            self.assertEqual(events[0]["name"], "seinel bad")
            self.assertEqual(events[1]["kind"], "message")
            self.assertEqual(events[1]["side"], "other")
            self.assertEqual(events[2]["side"], "other-agent")
            self.assertEqual(events[2]["message"], "만갤러 준비됐냐?")


    def test_side_chat_is_stored_separately_from_lobby_and_live_events(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)

            append_side_chat_event(root, {"name": "seinel", "side": "mine", "message": "실황 보면서 한마디"})
            append_lobby_event(root, {"name": "lobby", "side": "other", "message": "로비 메시지"})

            side_events = read_side_chat(root)

            self.assertEqual(len(side_events), 1)
            self.assertEqual(side_events[0]["message"], "실황 보면서 한마디")
            self.assertEqual(read_lobby(root)[0]["message"], "로비 메시지")
            self.assertTrue((root / "side_chat.jsonl").exists())
            self.assertFalse((root / "meetings" / "side_chat.jsonl").exists())


    def test_side_chat_can_be_scoped_to_a_meeting_room(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)

            append_side_chat_event(
                root,
                {"name": "나", "side": "mine", "message": "room-a only", "flow_meeting_id": "room-a"},
            )
            append_side_chat_event(
                root,
                {"name": "친구", "side": "other", "message": "room-b only", "flow_meeting_id": "room-b"},
            )
            append_side_chat_event(root, {"name": "legacy", "side": "other", "message": "global legacy"})

            all_events = read_side_chat(root)
            room_a_events = read_side_chat(root, meeting_id="room-a")
            room_b_events = read_side_chat(root, meeting_id="room-b")
            side_payload = _stream_snapshot_payload(root, "side_chat", meeting_id="room-a")

            self.assertEqual(
                [event["message"] for event in all_events],
                ["room-a only", "room-b only", "global legacy"],
            )
            self.assertEqual([event["message"] for event in room_a_events], ["room-a only"])
            self.assertEqual([event["message"] for event in room_b_events], ["room-b only"])
            self.assertEqual([event["message"] for event in side_payload["events"]], ["room-a only"])


    def test_side_chat_preserves_thread_source_event_id(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)

            event = append_side_chat_event(
                root,
                {
                    "name": "나",
                    "side": "mine",
                    "message": "원문에 답장",
                    "flow_meeting_id": "room-a",
                    "thread_source_event_id": "lobby-source-1",
                },
            )

            self.assertEqual(event["thread_source_event_id"], "lobby-source-1")
            self.assertEqual(read_side_chat(root, meeting_id="room-a")[0]["thread_source_event_id"], "lobby-source-1")
            self.assertEqual(
                _stream_snapshot_payload(root, "side_chat", meeting_id="room-a")["events"][0]["thread_source_event_id"],
                "lobby-source-1",
            )


    def test_side_chat_api_filters_by_meeting_id(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                for meeting_id, message in (("room-a", "room-a only"), ("room-b", "room-b only")):
                    request = Request(
                        f"http://127.0.0.1:{server.server_port}/api/side-chat",
                        data=json.dumps(
                            {
                                "name": "나",
                                "side": "mine",
                                "message": message,
                                "flow_meeting_id": meeting_id,
                            }
                        ).encode("utf-8"),
                        headers={"Content-Type": "application/json"},
                        method="POST",
                    )
                    with urlopen(request, timeout=4):
                        pass

                with urlopen(
                    f"http://127.0.0.1:{server.server_port}/api/side-chat?meeting_id=room-a",
                    timeout=4,
                ) as response:
                    room_a_payload = json.loads(response.read().decode("utf-8"))
                with urlopen(
                    f"http://127.0.0.1:{server.server_port}/api/side-chat?meeting_id=room-b",
                    timeout=4,
                ) as response:
                    room_b_payload = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

            self.assertEqual([event["message"] for event in room_a_payload["events"]], ["room-a only"])
            self.assertEqual([event["message"] for event in room_b_payload["events"]], ["room-b only"])

    def test_side_chat_post_uses_route_module_storage_after_handler_construction(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            handler_class = _make_handler(root)
            payload = {"name": "나", "side": "mine", "message": "patched", "flow_meeting_id": "room-a"}
            injected_event = {"id": "injected-event", "flow_meeting_id": "room-a"}
            injected_events = [injected_event]
            server = ThreadingHTTPServer(("127.0.0.1", 0), handler_class)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with patch(
                    "agentsassemble.gui_side_chat_http.append_side_chat_event",
                    return_value=injected_event,
                ) as append_mock:
                    with patch(
                        "agentsassemble.gui_side_chat_http.read_side_chat",
                        return_value=injected_events,
                    ) as read_mock:
                        request = Request(
                            f"http://127.0.0.1:{server.server_port}/api/side-chat",
                            data=json.dumps(payload).encode("utf-8"),
                            headers={"Content-Type": "application/json"},
                            method="POST",
                        )
                        with urlopen(request, timeout=4) as response:
                            response_payload = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

            append_mock.assert_called_once_with(root, payload)
            read_mock.assert_called_once_with(root, meeting_id="room-a")
            self.assertEqual(response_payload, {"event": injected_event, "events": injected_events})

    def test_room_friends_api_saves_friends_and_suggests_live_agents(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            connect_live_agent_payload(
                root,
                {
                    "agent_id": "codex-lead",
                    "display_name": "Codex Lead",
                    "provider_kind": "codex_live_session",
                    "connection_kind": "live_session",
                    "status": "online",
                },
            )
            connect_live_agent_payload(
                root,
                {
                    "agent_id": "yanagi-local",
                    "display_name": "츠키시로 야나기",
                    "provider_kind": "local_cli",
                    "connection_kind": "local_cli",
                    "status": "offline",
                },
            )
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                server_url = f"http://127.0.0.1:{server.server_port}"
                with urlopen(f"{server_url}/api/room-friends", timeout=4) as response:
                    initial_payload = json.loads(response.read().decode("utf-8"))
                request = Request(
                    f"{server_url}/api/room-friends",
                    data=json.dumps(
                        {
                            "display_name": "SeiNel",
                            "handle": "seinel",
                            "participant_type": "human",
                        }
                    ).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urlopen(request, timeout=4) as response:
                    saved_payload = json.loads(response.read().decode("utf-8"))
                delete_request = Request(
                    f"{server_url}/api/room-friends?friend_id={quote(str(saved_payload['friend']['friend_id']))}",
                    method="DELETE",
                )
                with urlopen(delete_request, timeout=4) as response:
                    deleted_payload = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

            suggestions_by_agent = {friend["agent_id"]: friend for friend in initial_payload["suggestions"]}
            self.assertEqual(suggestions_by_agent["codex-lead"]["participant_type"], "subscription_ai")
            self.assertEqual(suggestions_by_agent["yanagi-local"]["participant_type"], "local")
            self.assertEqual(saved_payload["friend"]["participant_type"], "human")
            self.assertEqual(saved_payload["friends"][0]["display_name"], "SeiNel")
            self.assertEqual(deleted_payload["deleted"]["friend_id"], saved_payload["friend"]["friend_id"])
            self.assertEqual(deleted_payload["friends"], [])
            deleted_suggestions_by_agent = {friend["agent_id"]: friend for friend in deleted_payload["suggestions"]}
            self.assertEqual(deleted_suggestions_by_agent["codex-lead"]["participant_type"], "subscription_ai")
            self.assertEqual(deleted_suggestions_by_agent["yanagi-local"]["participant_type"], "local")


    def test_room_friend_dm_api_posts_direct_ai_messages_without_lobby(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            connect_live_agent_payload(
                root,
                {
                    "agent_id": "codex-dm",
                    "display_name": "Codex DM",
                    "participant_type": "subscription_ai",
                    "provider_kind": "codex",
                    "connection_kind": "live_session",
                    "meeting_id": "resident-m1",
                },
            )
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                server_url = f"http://127.0.0.1:{server.server_port}"
                friend_request = Request(
                    f"{server_url}/api/room-friends",
                    data=json.dumps(
                        {
                            "friend_id": "friend:codex-dm",
                            "display_name": "Codex DM",
                            "participant_type": "subscription_ai",
                            "provider_kind": "codex",
                            "agent_id": "codex-dm",
                            "source_agent_id": "codex-dm",
                        }
                    ).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urlopen(friend_request, timeout=4) as response:
                    saved_friend = json.loads(response.read().decode("utf-8"))["friend"]

                with urlopen(f"{server_url}/api/room-friends/dm?friend_id=friend%3Acodex-dm", timeout=4) as response:
                    initial_dm = json.loads(response.read().decode("utf-8"))

                dm_request = Request(
                    f"{server_url}/api/room-friends/dm",
                    data=json.dumps(
                        {
                            "friend_id": saved_friend["friend_id"],
                            "message": "비공개 질문",
                        }
                    ).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urlopen(dm_request, timeout=4) as response:
                    posted_dm = json.loads(response.read().decode("utf-8"))

                with urlopen(f"{server_url}/api/live-agents/codex-dm/room", timeout=4) as response:
                    room_payload = json.loads(response.read().decode("utf-8"))

                reply_request = Request(
                    f"{server_url}/api/live-agents/codex-dm/dm-reply",
                    data=json.dumps(
                        {
                            "source_event_id": posted_dm["event"]["id"],
                            "message": "비공개 답장",
                        }
                    ).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urlopen(reply_request, timeout=4) as response:
                    replied_dm = json.loads(response.read().decode("utf-8"))

                bad_request = Request(
                    f"{server_url}/api/room-friends/dm",
                    data=json.dumps({"friend_id": "missing", "message": "nope"}).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with self.assertRaises(HTTPError) as error_context:
                    urlopen(bad_request, timeout=4)
            finally:
                server.shutdown()
                server.server_close()

            self.assertEqual(initial_dm["friend"]["display_name"], "Codex DM")
            self.assertEqual(initial_dm["events"], [])
            self.assertEqual(posted_dm["event"]["message"], "비공개 질문")
            self.assertEqual(posted_dm["event"]["target_agent_id"], "codex-dm")
            self.assertEqual(posted_dm["event"]["delivery_status"], "queued")
            self.assertEqual(posted_dm["events"][0]["friend_id"], "friend:codex-dm")
            self.assertEqual([event["id"] for event in room_payload["dm_events"]], [posted_dm["event"]["id"]])
            self.assertEqual(replied_dm["event"]["message"], "비공개 답장")
            self.assertEqual(replied_dm["event"]["reply_to_event_id"], posted_dm["event"]["id"])
            self.assertEqual(replied_dm["events"][-1]["side"], "other")
            self.assertEqual(replied_dm["delivery"]["status"], "delivered")
            self.assertEqual(read_lobby(root), [])
            self.assertEqual(error_context.exception.code, HTTPStatus.BAD_REQUEST)
            error_context.exception.close()


    def test_room_friend_dm_api_reports_missing_session_for_unresumable_ai_friend(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                server_url = f"http://127.0.0.1:{server.server_port}"
                friend_request = Request(
                    f"{server_url}/api/room-friends",
                    data=json.dumps(
                        {
                            "friend_id": "friend:missing-session",
                            "display_name": "Missing Session AI",
                            "participant_type": "subscription_ai",
                            "provider_kind": "codex",
                            "agent_id": "missing-session",
                            "source_agent_id": "missing-session",
                        }
                    ).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urlopen(friend_request, timeout=4):
                    pass

                dm_request = Request(
                    f"{server_url}/api/room-friends/dm",
                    data=json.dumps(
                        {
                            "friend_id": "friend:missing-session",
                            "message": "이력 없으면 실패해야 함",
                        }
                    ).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with self.assertRaises(HTTPError) as error_context:
                    urlopen(dm_request, timeout=4)
                error_payload = json.loads(error_context.exception.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

            self.assertEqual(error_context.exception.code, HTTPStatus.BAD_REQUEST)
            self.assertIn("세션이 존재하지 않습니다", error_payload["error"])
            self.assertEqual(read_lobby(root), [])
            error_context.exception.close()


    def test_user_profile_api_saves_local_discord_identity(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with urlopen(f"http://127.0.0.1:{server.server_port}/api/user-profile", timeout=4) as response:
                    initial_payload = json.loads(response.read().decode("utf-8"))
                request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/user-profile",
                    data=json.dumps(
                        {
                            "display_name": "Discord Owner",
                            "handle": "owner.local",
                            "status": "idle",
                            "custom_status": "프론트 비교 중",
                            "avatar_label": "DO",
                            "avatar_image_url": "/api/attachments/profile01?view=1",
                            "banner_preset": "midnight",
                            "accent_color": "#23a55a",
                            "mic_muted": False,
                            "deafened": True,
                        }
                    ).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urlopen(request, timeout=4) as response:
                    saved_payload = json.loads(response.read().decode("utf-8"))
                with urlopen(f"http://127.0.0.1:{server.server_port}/api/user-profile", timeout=4) as response:
                    loaded_payload = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

            self.assertEqual(initial_payload["profile"]["display_name"], "SeiNel")
            self.assertEqual(saved_payload["profile"]["display_name"], "Discord Owner")
            self.assertEqual(saved_payload["profile"]["handle"], "owner.local")
            self.assertEqual(saved_payload["profile"]["avatar_image_url"], "/api/attachments/profile01?view=1")
            self.assertEqual(saved_payload["profile"]["banner_preset"], "midnight")
            self.assertEqual(saved_payload["profile"]["accent_color"], "#23a55a")
            self.assertFalse(saved_payload["profile"]["mic_muted"])
            self.assertTrue(saved_payload["profile"]["deafened"])
            self.assertEqual(loaded_payload["profile"], saved_payload["profile"])


    def test_room_members_api_saves_roles_and_suggests_live_agents(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            connect_live_agent_payload(
                root,
                {
                    "agent_id": "codex-critic",
                    "display_name": "Codex Critic",
                    "provider_kind": "codex_live_session",
                    "connection_kind": "live_session",
                    "meeting_id": "m1",
                    "status": "online",
                },
            )
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with urlopen(
                    f"http://127.0.0.1:{server.server_port}/api/room-members?meeting_id=m1",
                    timeout=4,
                ) as response:
                    initial_payload = json.loads(response.read().decode("utf-8"))
                request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/room-members",
                    data=json.dumps(
                        {
                            "meeting_id": "m1",
                            "participant_id": "codex-critic",
                            "display_name": "Codex Critic",
                            "role": "director",
                            "participant_type": "subscription_ai",
                        }
                    ).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urlopen(request, timeout=4) as response:
                    saved_payload = json.loads(response.read().decode("utf-8"))
                remote_request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/room-members",
                    data=json.dumps(
                        {
                            "meeting_id": "m1",
                            "participant_id": "remote-friend",
                            "display_name": "Remote Friend",
                            "role": "agent",
                            "participant_type": "remote",
                            "connection_kind": "native_remote_room_client",
                        }
                    ).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urlopen(remote_request, timeout=4) as response:
                    remote_payload = json.loads(response.read().decode("utf-8"))
                with urlopen(
                    f"http://127.0.0.1:{server.server_port}/api/room-members?meeting_id=m2",
                    timeout=4,
                ) as response:
                    other_room_payload = json.loads(response.read().decode("utf-8"))
                bad_request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/room-members",
                    data=json.dumps(
                        {
                            "meeting_id": "m1",
                            "participant_id": "codex-critic",
                            "role": "wizard",
                        }
                    ).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with self.assertRaises(HTTPError) as error_context:
                    urlopen(bad_request, timeout=4)
                error_code = error_context.exception.code
                error_context.exception.close()
                self.assertEqual(error_code, 400)
            finally:
                server.shutdown()
                server.server_close()

            self.assertEqual(initial_payload["members"][0]["participant_id"], "codex-critic")
            self.assertEqual(initial_payload["members"][0]["role"], "reviewer")
            self.assertTrue(any(role["id"] == "agent" for role in initial_payload["roles"]))
            self.assertEqual(saved_payload["member"]["role"], "director")
            self.assertEqual(saved_payload["members"][0]["role"], "director")
            remote_member = next(
                member for member in remote_payload["members"] if member["participant_id"] == "remote-friend"
            )
            self.assertEqual(remote_member["role"], "agent")
            self.assertEqual(remote_member["participant_type"], "remote")
            self.assertEqual(other_room_payload["members"], [])


    def test_legacy_side_chat_lines_are_read_as_side_chat_channel(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            path = root / "side_chat.jsonl"
            path.write_text(
                '{"id":"legacy","created_at":"2026-05-12T00:00:00+00:00","name":"나","side":"mine","kind":"message","message":"old"}\n',
                encoding="utf-8",
            )

            side_events = read_side_chat(root)

            self.assertEqual(side_events[0]["channel"], "side_chat")
            self.assertFalse(side_events[0]["official_record"])


    def test_room_events_record_channel_audience_and_official_record_boundary(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            meeting_dir = root / "meetings" / "m1"
            meeting_dir.mkdir(parents=True)

            lobby_event = append_lobby_event(root, {"name": "나", "side": "mine", "message": "로비 잡담"})
            side_event = append_side_chat_event(root, {"name": "나", "side": "mine", "message": "실황 옆 잡담"})
            status_event = append_live_event(meeting_dir, {"kind": "status", "content": "회의 시작"})
            message_event = append_live_event(
                meeting_dir,
                {
                    "kind": "message",
                    "display_name": "설정충",
                    "content": "공식 발언",
                    "turn_id": "round_1:0:lore_lawyer",
                    "turn_index": 0,
                    "engagement_mode": "official_turn",
                },
            )
            synthesis_event = append_live_event(
                meeting_dir,
                {"kind": "synthesis", "display_name": "Moderator", "content": "종합"},
            )

            self.assertEqual(lobby_event["channel"], "lobby")
            self.assertEqual(side_event["channel"], "side_chat")
            self.assertEqual(lobby_event["audience"], "room")
            self.assertFalse(lobby_event["official_record"])
            self.assertFalse(side_event["official_record"])
            self.assertEqual(status_event["channel"], "system")
            self.assertFalse(status_event["official_record"])
            self.assertEqual(message_event["channel"], "official")
            self.assertTrue(message_event["official_record"])
            self.assertEqual(message_event["turn_id"], "round_1:0:lore_lawyer")
            self.assertEqual(message_event["turn_index"], 0)
            self.assertEqual(message_event["engagement_mode"], "official_turn")
            self.assertTrue(synthesis_event["official_record"])


    def test_lobby_promote_api_creates_explicit_official_context(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            meeting_dir = root / "meetings" / "m1"
            meeting_dir.mkdir(parents=True)
            write_live_state(meeting_dir, {"meeting_id": "m1", "topic": "promotion", "roles": [], "agent_bindings": []})
            lobby_event = append_lobby_event(
                root,
                {
                    "name": "owner",
                    "actor_id": "owner",
                    "message": "Promote this lobby note. https://secret.example/path",
                },
            )
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/lobby/promote",
                    data=json.dumps(
                        {
                            "meeting_id": "m1",
                            "lobby_event_ids": [lobby_event["id"]],
                            "reason": "operator selected",
                        }
                    ).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urlopen(request, timeout=4) as response:
                    payload = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

            self.assertEqual(payload["status"], "promoted")
            event = read_live_events(meeting_dir, limit=None)[0]
            self.assertEqual(event["kind"], "promoted_context")
            self.assertEqual(event["channel"], "official")
            self.assertEqual(event["source_event_id"], lobby_event["id"])
            self.assertIn("Promote this lobby note.", event["content"])
            self.assertNotIn("secret.example", event["content"])
            operation = read_live_agent_operations(root, operation="lobby.promote_to_official")[0]
            self.assertEqual(operation["status"], "success")
            self.assertEqual(operation["target_id"], "m1")
