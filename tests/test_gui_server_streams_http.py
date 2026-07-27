from tests.gui_server_test_support import (
    HTTPError,
    Path,
    Request,
    StringIO,
    ThreadingHTTPServer,
    _make_handler,
    _read_sse_frame,
    _request_trusted,
    _safe_static_path,
    _sse_event,
    _sse_stream_error_payload,
    _stream_snapshot_payload,
    append_live_event,
    append_lobby_event,
    append_side_chat_event,
    build_meeting_payload,
    json,
    list_meetings,
    os,
    patch,
    provider_catalog_payload,
    read_live_events_after,
    read_lobby,
    read_lobby_events_after,
    read_side_chat_events_after,
    run_demo_meeting,
    send_lobby_message_to_remote_bridge,
    tempfile,
    threading,
    time,
    unittest,
    urlopen,
    write_live_state,
)


class GuiServerStreamsHttpTests(unittest.TestCase):

    def test_room_event_readers_can_resume_after_event_id(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            meeting_dir = root / "meetings" / "m1"
            meeting_dir.mkdir(parents=True)

            first_lobby = append_lobby_event(root, {"name": "나", "side": "mine", "message": "첫 로비"})
            second_lobby = append_lobby_event(root, {"name": "친구", "side": "other", "message": "둘째 로비"})
            first_side = append_side_chat_event(root, {"name": "나", "side": "mine", "message": "첫 비공식"})
            second_side = append_side_chat_event(root, {"name": "친구", "side": "other", "message": "둘째 비공식"})
            first_live = append_live_event(meeting_dir, {"kind": "message", "content": "첫 공식"})
            second_live = append_live_event(meeting_dir, {"kind": "message", "content": "둘째 공식"})

            self.assertEqual(
                [event["id"] for event in read_lobby_events_after(root / "lobby.jsonl", first_lobby["id"])],
                [second_lobby["id"]],
            )
            self.assertEqual(
                [event["id"] for event in read_side_chat_events_after(root / "side_chat.jsonl", first_side["id"])],
                [second_side["id"]],
            )
            self.assertEqual(
                [event["id"] for event in read_live_events_after(meeting_dir, first_live["id"])],
                [second_live["id"]],
            )


    def test_sse_event_formats_id_event_name_and_json_data(self):
        body = _sse_event("lobby", {"id": "abc", "message": "안녕"}, event_id="abc").decode("utf-8")

        self.assertIn("id: abc\n", body)
        self.assertIn("event: lobby\n", body)
        self.assertIn('data: {"id": "abc", "message": "안녕"}\n\n', body)


    def test_sse_stream_error_payload_bounds_error_text(self):
        payload = _sse_stream_error_payload(
            "meeting",
            ValueError("line one\n" + ("x" * 700)),
            meeting_id="m1",
        )

        self.assertEqual(payload["stream"], "meeting")
        self.assertEqual(payload["meeting_id"], "m1")
        self.assertNotIn("\n", payload["error"])
        self.assertLessEqual(len(payload["error"]), 500)


    def test_lobby_sse_keeps_connection_open_with_heartbeat(self):
        stderr = StringIO()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            append_lobby_event(root, {"name": "나", "side": "mine", "message": "첫 로비"})
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            with patch("sys.stderr", stderr):
                thread.start()
                try:
                    with urlopen(f"http://127.0.0.1:{server.server_port}/api/events/lobby", timeout=4) as response:
                        lines = []
                        deadline = time.time() + 3
                        while time.time() < deadline and ": keep-alive" not in "\n".join(lines):
                            lines.append(response.readline().decode("utf-8").strip())
                    time.sleep(0.2)
                finally:
                    server.shutdown()
                    server.server_close()

            body = "\n".join(lines)
            self.assertIn("event: lobby", body)
            self.assertIn('"stream": "lobby"', body)
            self.assertIn(": keep-alive", body)
            self.assertNotIn("ConnectionResetError", stderr.getvalue())


    def test_lobby_sse_delivers_appended_event_below_polling_cadence(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            initial = append_lobby_event(root, {"name": "나", "side": "mine", "message": "첫 로비"})
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with urlopen(
                    f"http://127.0.0.1:{server.server_port}/api/events/lobby?last_event_id={initial['id']}",
                    timeout=4,
                ) as response:
                    _read_sse_frame(response)
                    started = time.perf_counter()
                    event = append_lobby_event(root, {"name": "친구", "side": "other", "message": "빠르게 보여야 함"})
                    frame = ""
                    deadline = time.time() + 2
                    while time.time() < deadline and str(event["id"]) not in frame:
                        frame = _read_sse_frame(response, timeout=2)
                    elapsed_seconds = time.perf_counter() - started
            finally:
                server.shutdown()
                server.server_close()

            self.assertIn(str(event["id"]), frame)
            self.assertLess(elapsed_seconds, 0.7)


    def test_side_chat_sse_streams_only_the_requested_room(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            room_a = append_side_chat_event(
                root,
                {"name": "A", "message": "room-a only", "flow_meeting_id": "room-a"},
            )
            append_side_chat_event(
                root,
                {"name": "B", "message": "room-b only", "flow_meeting_id": "room-b"},
            )
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with urlopen(
                    f"http://127.0.0.1:{server.server_port}/api/events/side-chat?meeting_id=room-a",
                    timeout=4,
                ) as response:
                    frame = _read_sse_frame(response)
                time.sleep(0.2)
            finally:
                server.shutdown()
                server.server_close()

            self.assertIn("event: side_chat", frame)
            self.assertIn(str(room_a["id"]), frame)
            self.assertIn("room-a only", frame)
            self.assertNotIn("room-b only", frame)


    def test_sse_client_disconnect_does_not_log_connection_reset_traceback(self):
        stderr = StringIO()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            append_lobby_event(root, {"name": "나", "side": "mine", "message": "첫 로비"})
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            with patch("sys.stderr", stderr):
                thread.start()
                try:
                    response = urlopen(f"http://127.0.0.1:{server.server_port}/api/events/lobby", timeout=4)
                    try:
                        self.assertIn("event: lobby", _read_sse_frame(response))
                    finally:
                        response.close()
                    time.sleep(0.2)
                finally:
                    server.shutdown()
                    server.server_close()

        self.assertNotIn("ConnectionResetError", stderr.getvalue())


    def test_meeting_sse_sends_final_payload_after_event_cursor_is_current(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            meeting_dir = root / "meetings" / "m1"
            meeting_dir.mkdir(parents=True)
            event = append_live_event(meeting_dir, {"kind": "message", "content": "공식"})
            (meeting_dir / "meeting.json").write_text(
                json.dumps(
                    {
                        "meeting_id": "m1",
                        "topic": "테스트",
                        "question": "완료됐나?",
                        "roles": [],
                        "debate_rounds": [],
                        "live_status": "complete",
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/meetings/m1/events",
                    headers={"Last-Event-ID": event["id"]},
                )
                with urlopen(request, timeout=4) as response:
                    lines = []
                    deadline = time.time() + 3
                    while time.time() < deadline and "meeting_stream_snapshot" not in "\n".join(lines):
                        lines.append(response.readline().decode("utf-8").strip())
            finally:
                server.shutdown()
                server.server_close()

            body = "\n".join(lines)
            self.assertIn("event: meeting", body)
            self.assertIn('"meeting_stream_snapshot"', body)
            self.assertIn('"live_status": "complete"', body)
            self.assertIn('"live_events"', body)


    def test_meeting_sse_payload_excludes_archive_artifact_bodies(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            meeting_dir = root / "meetings" / "m1"
            meeting_dir.mkdir(parents=True)
            (meeting_dir / "return_packets").mkdir()
            (meeting_dir / "review_checkpoints").mkdir()
            (meeting_dir / "private_research" / "architect").mkdir(parents=True)
            append_live_event(meeting_dir, {"kind": "message", "content": "SAFE_OFFICIAL_EVENT"})
            (meeting_dir / "meeting.json").write_text(
                json.dumps(
                    {
                        "meeting_id": "m1",
                        "topic": "테스트",
                        "question": "완료됐나?",
                        "roles": [],
                        "debate_rounds": [],
                        "live_status": "complete",
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            (meeting_dir / "transcript.md").write_text("SECRET_TRANSCRIPT_BODY", encoding="utf-8")
            (meeting_dir / "decision.md").write_text("SECRET_DECISION_BODY", encoding="utf-8")
            (meeting_dir / "return_packets" / "architect.md").write_text(
                "SECRET_RETURN_PACKET",
                encoding="utf-8",
            )
            (meeting_dir / "review_checkpoints" / "checkpoint.md").write_text(
                "SECRET_REVIEW_REPLY",
                encoding="utf-8",
            )
            (meeting_dir / "private_research" / "architect" / "research.md").write_text(
                "SECRET_RESEARCH_BODY",
                encoding="utf-8",
            )

            payload = _stream_snapshot_payload(root, "meeting", meeting_id="m1")
            serialized = json.dumps(payload, ensure_ascii=False)

            self.assertIn("SAFE_OFFICIAL_EVENT", serialized)
            self.assertIn("meeting_stream_snapshot", payload)
            self.assertIn("live_events", payload["meeting_stream_snapshot"])
            for raw_key in (
                "artifacts",
                "return_packets",
                "review_checkpoints",
                "research",
                "tasks",
            ):
                self.assertNotIn(raw_key, payload["meeting_stream_snapshot"])
            for forbidden in (
                "SECRET_TRANSCRIPT_BODY",
                "SECRET_DECISION_BODY",
                "SECRET_RETURN_PACKET",
                "SECRET_REVIEW_REPLY",
                "SECRET_RESEARCH_BODY",
            ):
                self.assertNotIn(forbidden, serialized)


    def test_meeting_sse_payload_excludes_private_review_events_and_raw_fields(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            meeting_dir = root / "meetings" / "m1"
            meeting_dir.mkdir(parents=True)
            (meeting_dir / "meeting.json").write_text(
                json.dumps(
                    {
                        "meeting_id": "m1",
                        "topic": "테스트",
                        "question": "완료됐나?",
                        "roles": [],
                        "debate_rounds": [],
                        "live_status": "running",
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            raw_events = [
                {
                    "id": "public-1",
                    "kind": "message",
                    "channel": "official",
                    "official_record": True,
                    "display_name": "Public Agent",
                    "content": "PUBLIC_OFFICIAL_MESSAGE",
                    "created_at": "2026-01-01T00:00:00+00:00",
                },
                {
                    "id": "review-request",
                    "kind": "live_agent_turn_request",
                    "channel": "review",
                    "audience": "agent:critic",
                    "content": "SECRET_REVIEW_PROMPT_DO_NOT_STREAM",
                    "prompt": "SECRET_PROMPT_FIELD",
                    "stdout": "SECRET_STDOUT",
                    "stderr": "SECRET_STDERR",
                    "session_id": "SECRET_SESSION_ID",
                    "endpoint_url": "https://example.invalid/SECRET_ENDPOINT",
                    "local_path": "/Users/seinel/SECRET_PATH",
                    "created_at": "2026-01-01T00:00:01+00:00",
                },
                {
                    "id": "review-reply",
                    "kind": "message",
                    "channel": "review",
                    "official_record": False,
                    "source_event_id": "review-request",
                    "content": "SECRET_REVIEW_REPLY_DO_NOT_STREAM",
                    "created_at": "2026-01-01T00:00:02+00:00",
                },
            ]
            (meeting_dir / "live_events.jsonl").write_text(
                "\n".join(json.dumps(event, ensure_ascii=False) for event in raw_events) + "\n",
                encoding="utf-8",
            )

            payload = _stream_snapshot_payload(root, "meeting", meeting_id="m1")
            serialized = json.dumps(payload, ensure_ascii=False)

            self.assertIn("PUBLIC_OFFICIAL_MESSAGE", serialized)
            self.assertEqual([event["id"] for event in payload["events"]], ["public-1"])
            self.assertEqual(
                [event["id"] for event in payload["meeting_stream_snapshot"]["live_events"]],
                ["public-1"],
            )
            for forbidden in (
                "SECRET_REVIEW_PROMPT_DO_NOT_STREAM",
                "SECRET_REVIEW_REPLY_DO_NOT_STREAM",
                "SECRET_PROMPT_FIELD",
                "SECRET_STDOUT",
                "SECRET_STDERR",
                "SECRET_SESSION_ID",
                "SECRET_ENDPOINT",
                "SECRET_PATH",
                "prompt",
                "stdout",
                "stderr",
                "session_id",
                "endpoint_url",
                "local_path",
            ):
                self.assertNotIn(forbidden, serialized)


    def test_meeting_sse_reports_error_if_meeting_disappears_during_stream(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            meeting_dir = root / "meetings" / "m1"
            meeting_dir.mkdir(parents=True)
            append_live_event(meeting_dir, {"kind": "message", "content": "공식"})
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with urlopen(f"http://127.0.0.1:{server.server_port}/api/meetings/m1/events", timeout=4) as response:
                    meeting_frame = _read_sse_frame(response)
                    for path in sorted(meeting_dir.glob("*")):
                        path.unlink()
                    meeting_dir.rmdir()
                    error_frame = _read_sse_frame(response)
                    trailing = response.readline()
            finally:
                server.shutdown()
                server.server_close()

            self.assertIn("event: meeting", meeting_frame)
            self.assertIn("event: error", error_frame)
            self.assertEqual(error_frame.count("event: error"), 1)
            self.assertIn('"stream": "meeting"', error_frame)
            self.assertIn('"meeting_id": "m1"', error_frame)
            self.assertIn("Meeting m1 was not found.", error_frame)
            self.assertEqual(trailing, b"")


    def test_meeting_sse_reports_error_if_payload_file_vanishes_after_headers(self):
        stderr = StringIO()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            meeting_dir = root / "meetings" / "m1"
            meeting_dir.mkdir(parents=True)
            append_live_event(meeting_dir, {"kind": "message", "content": "공식"})
            (meeting_dir / "meeting.json").write_text("{}", encoding="utf-8")
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            with patch("agentsassemble.gui.build_meeting_stream_payload", side_effect=FileNotFoundError("lost")):
                with patch("sys.stderr", stderr):
                    thread.start()
                    try:
                        with urlopen(
                            f"http://127.0.0.1:{server.server_port}/api/meetings/m1/events",
                            timeout=4,
                        ) as response:
                            error_frame = _read_sse_frame(response)
                            trailing = response.readline()
                    finally:
                        server.shutdown()
                        server.server_close()

            self.assertIn("event: error", error_frame)
            self.assertEqual(error_frame.count("event: error"), 1)
            self.assertIn('"stream": "meeting"', error_frame)
            self.assertIn('"meeting_id": "m1"', error_frame)
            self.assertIn("Meeting m1 was not found.", error_frame)
            self.assertEqual(trailing, b"")
            self.assertNotIn("FileNotFoundError", stderr.getvalue())


    def test_missing_meeting_sse_returns_json_404_before_stream_opens(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with self.assertRaises(HTTPError) as context:
                    urlopen(f"http://127.0.0.1:{server.server_port}/api/meetings/missing/events", timeout=4)
                content_type = context.exception.headers.get("Content-Type")
                body = json.loads(context.exception.read().decode("utf-8"))
                context.exception.close()
            finally:
                server.shutdown()
                server.server_close()

            self.assertEqual(context.exception.code, 404)
            self.assertEqual(content_type, "application/json; charset=utf-8")
            self.assertEqual(body, {"error": "Meeting not found"})


    def test_stream_snapshot_payload_keeps_lobby_side_chat_and_meeting_distinct(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            meeting_dir = root / "meetings" / "m1"
            meeting_dir.mkdir(parents=True)
            first_lobby = append_lobby_event(root, {"name": "나", "side": "mine", "message": "로비"})
            append_lobby_event(root, {"name": "상대", "side": "other", "message": "로비 둘"})
            append_side_chat_event(root, {"name": "나", "side": "mine", "message": "비공식"})
            append_live_event(meeting_dir, {"kind": "message", "content": "공식"})

            lobby_payload = _stream_snapshot_payload(root, "lobby", last_event_id=first_lobby["id"])
            side_payload = _stream_snapshot_payload(root, "side_chat")
            meeting_payload = _stream_snapshot_payload(root, "meeting", meeting_id="m1")

            self.assertEqual(lobby_payload["stream"], "lobby")
            self.assertEqual([event["message"] for event in lobby_payload["events"]], ["로비 둘"])
            self.assertEqual(side_payload["stream"], "side_chat")
            self.assertEqual(side_payload["events"][0]["message"], "비공식")
            self.assertEqual(meeting_payload["stream"], "meeting")
            self.assertEqual(meeting_payload["meeting_id"], "m1")
            self.assertEqual(meeting_payload["events"][0]["content"], "공식")


    def test_meeting_stream_includes_safe_payload_after_final_record(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            result = run_demo_meeting(adapter_name="mock", output_root=root)

            payload = _stream_snapshot_payload(root, "meeting", meeting_id=result.meeting_id)

            self.assertEqual(payload["stream"], "meeting")
            self.assertEqual(payload["meeting_stream_snapshot"]["meeting"]["live_status"], "complete")
            self.assertIn("lifecycle", payload["meeting_stream_snapshot"])
            self.assertIn("live_events", payload["meeting_stream_snapshot"])
            self.assertNotIn("artifacts", payload["meeting_stream_snapshot"])
            self.assertNotIn("return_packets", payload["meeting_stream_snapshot"])
            self.assertNotIn("review_checkpoints", payload["meeting_stream_snapshot"])
            self.assertNotIn("research", payload["meeting_stream_snapshot"])


    def test_meeting_stream_survives_partial_final_record(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            meeting_dir = root / "meetings" / "m1"
            meeting_dir.mkdir(parents=True)
            event = append_live_event(meeting_dir, {"kind": "message", "content": "공식"})
            (meeting_dir / "meeting.json").write_text("{", encoding="utf-8")

            payload = _stream_snapshot_payload(root, "meeting", meeting_id="m1")

            self.assertEqual(payload["stream"], "meeting")
            self.assertTrue(payload["meeting_stream_snapshot_pending"])
            self.assertNotIn("meeting_stream_snapshot", payload)

            (meeting_dir / "meeting.json").write_text(
                json.dumps(
                    {
                        "meeting_id": "m1",
                        "topic": "테스트",
                        "question": "완료됐나?",
                        "roles": [],
                        "debate_rounds": [],
                        "live_status": "complete",
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            recovered = _stream_snapshot_payload(root, "meeting", meeting_id="m1", last_event_id=event["id"])

            self.assertEqual(recovered["events"], [])
            self.assertEqual(recovered["meeting_stream_snapshot"]["meeting"]["live_status"], "complete")


    def test_lobby_remote_bridge_reply_is_recorded_as_other_agent(self):
        import agentsassemble.gui as gui

        class FakeRequester:
            def __init__(self):
                self.calls = []

            def __call__(self, url, headers, payload, timeout_seconds):
                self.calls.append({"url": url, "headers": headers, "payload": payload})
                return {
                    "text": '{"message":"친구 Claude Code 준비됐습니다.","kind":"deploy","name":"Spoofed Remote"}',
                    "metadata": {"bridge": "friend-mac"},
                }

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            agent_config = root / "agents.json"
            agent_config.write_text(
                json.dumps(
                    {
                        "providers": [
                            {
                                "id": "friend-claude-code",
                                "kind": "remote_http_bridge",
                                "display_name": "Friend Claude Code",
                                "endpoint": "http://friend.local:8777",
                                "auth_ref": "literal:bridge-token",
                            }
                        ]
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            meeting_dir = root / "meetings" / "m1"
            meeting_dir.mkdir(parents=True)
            (meeting_dir / "meeting.json").write_text(
                json.dumps(
                    {
                        "meeting_id": "m1",
                        "topic": "테스트",
                        "question": "준비됐나?",
                        "roles": [
                            {
                                "id": "show_me_the_feats",
                                "display_name": "공식이뭘알아",
                                "lens": "전적/퍼포먼스",
                                "research_focus": "전투 결과",
                            }
                        ],
                        "provider_configs": {
                            "friend-claude-code": {
                                "id": "friend-claude-code",
                                "kind": "remote_http_bridge",
                                "display_name": "Friend Claude Code",
                                "endpoint": "http://friend.local:8777",
                                "auth_ref": "literal:<redacted>",
                            }
                        },
                        "agent_config_source": str(agent_config),
                        "agent_bindings": [
                            {
                                "agent_id": "friend-agent",
                                "role_id": "show_me_the_feats",
                                "owner_id": "friend",
                                "provider_id": "friend-claude-code",
                                "model_id": None,
                                "permission_profile_id": "meeting_read_only",
                                "join_mode": "current_session",
                            }
                        ],
                        "debate_rounds": [],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            requester = FakeRequester()
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            previous_requester = gui.REMOTE_LOBBY_REQUESTER
            gui.REMOTE_LOBBY_REQUESTER = requester
            try:
                request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/lobby/remote",
                    data=json.dumps(
                        {
                            "message": "친구야 준비됐어?",
                            "meeting_id": "m1",
                            "speaker_name": "나",
                        }
                    ).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urlopen(request, timeout=4) as response:
                    response_payload = json.loads(response.read().decode("utf-8"))
            finally:
                gui.REMOTE_LOBBY_REQUESTER = previous_requester
                server.shutdown()
                server.server_close()

            event = response_payload["event"]

            self.assertEqual(event["side"], "other-agent")
            self.assertEqual(event["name"], "공식이뭘알아")
            self.assertEqual(event["actor_id"], "friend-agent")
            self.assertEqual(event["actor_type"], "agent")
            self.assertEqual(event["kind"], "deploy")
            self.assertEqual(event["flow_meeting_id"], "m1")
            self.assertEqual(event["message"], "친구 Claude Code 준비됐습니다.")
            self.assertEqual(response_payload["events"][0]["id"], event["id"])
            self.assertEqual(read_lobby(root)[0]["message"], "친구 Claude Code 준비됐습니다.")
            self.assertEqual(requester.calls[0]["headers"]["Authorization"], "Bearer bridge-token")
            self.assertEqual(requester.calls[0]["payload"]["step"], "lobby")


    def test_lobby_remote_bridge_rejects_redacted_public_literal_without_runtime_config(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            meeting_dir = root / "meetings" / "m1"
            meeting_dir.mkdir(parents=True)
            (meeting_dir / "meeting.json").write_text(
                json.dumps(
                    {
                        "meeting_id": "m1",
                        "roles": [
                            {
                                "id": "show_me_the_feats",
                                "display_name": "공식이뭘알아",
                                "lens": "전적/퍼포먼스",
                                "research_focus": "전투 결과",
                            }
                        ],
                        "provider_configs": {
                            "friend-claude-code": {
                                "id": "friend-claude-code",
                                "kind": "remote_http_bridge",
                                "display_name": "Friend Claude Code",
                                "endpoint": "http://friend.local:8777",
                                "auth_ref": "literal:<redacted>",
                            }
                        },
                        "agent_config_source": "default",
                        "agent_bindings": [
                            {
                                "agent_id": "friend-agent",
                                "role_id": "show_me_the_feats",
                                "owner_id": "friend",
                                "provider_id": "friend-claude-code",
                                "permission_profile_id": "meeting_read_only",
                            }
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "credential is not available"):
                send_lobby_message_to_remote_bridge(root, "친구야 준비됐어?", meeting_id="m1", speaker_name="나")


    def test_list_meetings_orders_latest_first(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            first = run_demo_meeting(adapter_name="mock", output_root=root)
            second = run_demo_meeting(adapter_name="mock", output_root=root)

            meetings = list_meetings(root)

            self.assertEqual(meetings[0]["meeting_id"], second.meeting_id)
            self.assertEqual(meetings[1]["meeting_id"], first.meeting_id)


    def test_diagnostic_meetings_do_not_become_latest_or_listed(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            normal_dir = root / "meetings" / "normal-live"
            normal_dir.mkdir(parents=True)
            write_live_state(
                normal_dir,
                {
                    "meeting_id": "normal-live",
                    "topic": "Normal live",
                    "question": "Visible?",
                    "roles": [],
                    "debate_rounds": [],
                    "moderator_synthesis": {},
                    "live_status": "running",
                },
            )
            diagnostic_dir = root / "meetings" / "official-round-smoke-diag"
            diagnostic_dir.mkdir(parents=True)
            write_live_state(
                diagnostic_dir,
                {
                    "meeting_id": "official-round-smoke-diag",
                    "topic": "Official round smoke",
                    "question": "Diagnostic?",
                    "roles": [],
                    "debate_rounds": [],
                    "moderator_synthesis": {},
                    "live_status": "running",
                    "diagnostic": True,
                    "diagnostic_kind": "official_round_smoke",
                },
            )
            newer = time.time() + 10
            os.utime(diagnostic_dir / "live_state.json", (newer, newer))
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with urlopen(f"http://127.0.0.1:{server.server_port}/api/meetings", timeout=4) as response:
                    meetings_payload = json.loads(response.read().decode("utf-8"))
                with urlopen(f"http://127.0.0.1:{server.server_port}/api/meetings/latest", timeout=4) as response:
                    latest_payload = json.loads(response.read().decode("utf-8"))
                with urlopen(
                    f"http://127.0.0.1:{server.server_port}/api/meetings/official-round-smoke-diag",
                    timeout=4,
                ) as response:
                    direct_payload = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

            self.assertEqual([meeting["meeting_id"] for meeting in meetings_payload["meetings"]], ["normal-live"])
            self.assertEqual(latest_payload["meeting"]["meeting_id"], "normal-live")
            self.assertEqual(direct_payload["meeting"]["meeting_id"], "official-round-smoke-diag")
            self.assertTrue(direct_payload["meeting"]["diagnostic"])


    def test_live_meeting_payload_can_load_before_meeting_json_exists(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            meeting_dir = root / "meetings" / "live-1"
            meeting_dir.mkdir(parents=True)
            write_live_state(
                meeting_dir,
                {
                    "meeting_id": "live-1",
                    "topic": "Live topic",
                    "question": "Live question?",
                    "roles": [],
                    "debate_rounds": [],
                    "moderator_synthesis": {},
                    "live_status": "running",
                },
            )
            append_live_event(meeting_dir, {"kind": "status", "content": "회의 시작"})

            meetings = list_meetings(root)
            payload = build_meeting_payload(meeting_dir)

            self.assertEqual(meetings[0]["meeting_id"], "live-1")
            self.assertEqual(payload["meeting"]["live_status"], "running")
            self.assertEqual(payload["live_events"][0]["content"], "회의 시작")


    def test_live_meeting_payload_uses_live_state_while_final_record_is_partial(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            meeting_dir = root / "meetings" / "live-partial"
            meeting_dir.mkdir(parents=True)
            write_live_state(
                meeting_dir,
                {
                    "meeting_id": "live-partial",
                    "topic": "Partial topic",
                    "question": "Can the room recover?",
                    "roles": [],
                    "debate_rounds": [],
                    "moderator_synthesis": {},
                    "live_status": "running",
                },
            )
            append_live_event(meeting_dir, {"kind": "status", "content": "회의 진행 중"})
            (meeting_dir / "meeting.json").write_text("{", encoding="utf-8")

            meetings = list_meetings(root)
            payload = build_meeting_payload(meeting_dir)

            self.assertEqual(meetings[0]["meeting_id"], "live-partial")
            self.assertEqual(payload["meeting"]["live_status"], "running")
            self.assertEqual(payload["live_events"][0]["content"], "회의 진행 중")


    def test_stale_live_meeting_is_marked_stalled(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            meeting_dir = root / "meetings" / "stale-live"
            meeting_dir.mkdir(parents=True)
            write_live_state(
                meeting_dir,
                {
                    "meeting_id": "stale-live",
                    "topic": "Stalled topic",
                    "question": "Did the meeting stop?",
                    "roles": [],
                    "debate_rounds": [],
                    "moderator_synthesis": {},
                    "live_status": "running",
                },
            )
            append_live_event(meeting_dir, {"kind": "status", "content": "독립 리서치 시작"})
            stale_mtime = time.time() - 3600
            os.utime(meeting_dir / "live_state.json", (stale_mtime, stale_mtime))
            os.utime(meeting_dir / "live_events.jsonl", (stale_mtime, stale_mtime))

            meetings = list_meetings(root, now=time.time())
            payload = build_meeting_payload(meeting_dir, now=time.time())

            self.assertEqual(meetings[0]["live_status"], "stalled")
            self.assertEqual(payload["meeting"]["live_status"], "stalled")
            self.assertIn("stalled_reason", payload["meeting"])


    def test_control_plane_rejects_untrusted_host_and_origin(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                base = f"http://127.0.0.1:{server.server_port}"
                with self.assertRaises(HTTPError) as rebind:
                    urlopen(Request(f"{base}/api/lobby", headers={"Host": "evil.example.com"}), timeout=4)
                rebind_code = rebind.exception.code
                rebind.exception.close()
                self.assertEqual(rebind_code, 403)
                with self.assertRaises(HTTPError) as csrf:
                    urlopen(Request(f"{base}/api/lobby", headers={"Origin": "http://evil.example.com"}), timeout=4)
                csrf_code = csrf.exception.code
                csrf.exception.close()
                self.assertEqual(csrf_code, 403)
                # Loopback host with a non-matching port and loopback origin remain allowed.
                with urlopen(
                    Request(f"{base}/api/lobby", headers={"Host": "localhost:1", "Origin": base}),
                    timeout=4,
                ) as ok:
                    self.assertEqual(ok.status, 200)
            finally:
                server.shutdown()
                server.server_close()


    def test_request_trusted_decision_matrix(self):
        # Loopback bind: strict loopback-only Host/Origin allowlist.
        self.assertFalse(_request_trusted("127.0.0.1", "evil.example.com", None))
        self.assertFalse(_request_trusted("127.0.0.1", "127.0.0.1:8765", "http://evil.example.com"))
        self.assertTrue(_request_trusted("127.0.0.1", "localhost:1", "http://127.0.0.1:8765"))
        self.assertTrue(_request_trusted("127.0.0.1", "127.0.0.1:8765", None))
        public_url = "https://room.example.com"
        with patch.dict(os.environ, {"AGENTSASSEMBLE_PUBLIC_URL": public_url}):
            self.assertTrue(
                _request_trusted(
                    "127.0.0.1",
                    "room.example.com",
                    "https://room.example.com",
                    path="/join",
                    method="GET",
                    public_url=public_url,
                )
            )
            self.assertTrue(
                _request_trusted(
                    "127.0.0.1",
                    "room.example.com",
                    "https://room.example.com",
                    path="/api/room-invite/join",
                    method="POST",
                    public_url=public_url,
                )
            )
            self.assertTrue(
                _request_trusted(
                    "127.0.0.1",
                    "room.example.com",
                    "https://room.example.com",
                    path="/api/attachments",
                    method="POST",
                    public_url=public_url,
                )
            )
            self.assertTrue(
                _request_trusted(
                    "127.0.0.1",
                    "room.example.com",
                    "https://room.example.com",
                    path="/api/attachments/avatar-12345678",
                    method="GET",
                    public_url=public_url,
                )
            )
            self.assertFalse(
                _request_trusted(
                    "127.0.0.1",
                    "room.example.com",
                    "https://room.example.com",
                    path="/api/lobby",
                    method="GET",
                    public_url=public_url,
                )
            )
            self.assertFalse(
                _request_trusted(
                    "127.0.0.1",
                    "room.example.com",
                    "https://evil.example.com",
                    path="/join",
                    method="GET",
                    public_url=public_url,
                )
            )
            self.assertFalse(
                _request_trusted(
                    "127.0.0.1",
                    "other.example.com",
                    "https://room.example.com",
                    path="/join",
                    public_url=public_url,
                )
            )
        # Non-loopback bind (operator-exposed): LAN/Tailscale Host/Origin allowed.
        self.assertTrue(_request_trusted("0.0.0.0", "192.168.0.2:8765", "http://192.168.0.2:8765"))
        self.assertTrue(_request_trusted("192.168.0.2", "192.168.0.2:8765", "http://192.168.0.2:8765"))
        self.assertTrue(_request_trusted("100.64.0.1", "host.tailnet.ts.net", "http://host.tailnet.ts.net"))


    def test_static_paths_cannot_escape_static_root(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            static_root = root / "static"
            static_root.mkdir()
            (static_root / "app.js").write_text("", encoding="utf-8")

            self.assertEqual(_safe_static_path(static_root, "app.js"), (static_root / "app.js").resolve())
            self.assertIsNone(_safe_static_path(static_root, "../secret.txt"))


    def test_root_reports_missing_react_build_without_serving_legacy_console(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            missing_dist = Path(temp_dir) / "missing-dist"
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root, frontend_dist_root=missing_dist))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                base = f"http://127.0.0.1:{server.server_port}"
                with self.assertRaises(HTTPError) as root_raised:
                    urlopen(f"{base}/", timeout=4)
                with self.assertRaises(HTTPError) as app_raised:
                    urlopen(f"{base}/app/", timeout=4)
                with self.assertRaises(HTTPError) as legacy_raised:
                    urlopen(f"{base}/legacy/", timeout=4)
                with self.assertRaises(HTTPError) as root_static_raised:
                    urlopen(f"{base}/static/base.css", timeout=4)
                with self.assertRaises(HTTPError) as legacy_static_raised:
                    urlopen(f"{base}/legacy/static/base.css", timeout=4)
                with self.assertRaises(HTTPError) as escaped:
                    urlopen(f"{base}/legacy/static/../secret.txt", timeout=4)
            finally:
                server.shutdown()
                server.server_close()

        raised_errors = [
            root_raised.exception,
            app_raised.exception,
            legacy_raised.exception,
            root_static_raised.exception,
            legacy_static_raised.exception,
            escaped.exception,
        ]
        try:
            self.assertEqual(root_raised.exception.code, 503)
            self.assertEqual(app_raised.exception.code, 503)
            self.assertIn("npm --prefix frontend run build", root_raised.exception.read().decode("utf-8"))
            self.assertIn("npm --prefix frontend run build", app_raised.exception.read().decode("utf-8"))
            self.assertEqual(legacy_raised.exception.code, 404)
            self.assertEqual(root_static_raised.exception.code, 404)
            self.assertEqual(legacy_static_raised.exception.code, 404)
            self.assertEqual(escaped.exception.code, 404)
        finally:
            for error in raised_errors:
                error.close()


    def test_root_and_app_serve_react_when_build_available(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "room"
            dist = Path(temp_dir) / "dist"
            assets = dist / "assets"
            assets.mkdir(parents=True)
            (dist / "index.html").write_text(
                '<div id="root"></div><script type="module" src="/assets/app.js"></script>'
                '<link rel="stylesheet" href="/assets/app.css">',
                encoding="utf-8",
            )
            (assets / "app.js").write_text("console.log('react preview');", encoding="utf-8")
            (assets / "app.css").write_text("body{color:white}", encoding="utf-8")
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root, frontend_dist_root=dist))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                base = f"http://127.0.0.1:{server.server_port}"
                root_response = urlopen(f"{base}/", timeout=4)
                root_html = root_response.read().decode("utf-8")
                app_html = urlopen(f"{base}/app/", timeout=4).read().decode("utf-8")
                pair_response = urlopen(f"{base}/pair?token=aap1_secret", timeout=4)
                pair_html = pair_response.read().decode("utf-8")
                asset_response = urlopen(f"{base}/app/assets/app.js", timeout=4)
                asset_body = asset_response.read().decode("utf-8")
                with self.assertRaises(HTTPError) as legacy_raised:
                    urlopen(f"{base}/legacy/", timeout=4)
                with self.assertRaises(HTTPError) as escaped:
                    urlopen(f"{base}/app/assets/%2e%2e/%2e%2e/secret.txt", timeout=4)
            finally:
                server.shutdown()
                server.server_close()

        self.assertEqual(root_html, app_html)
        self.assertIn('src="/app/assets/app.js"', root_html)
        self.assertIn('href="/app/assets/app.css"', root_html)
        self.assertEqual(root_response.headers.get("Content-Type"), "text/html; charset=utf-8")
        self.assertEqual(root_response.headers.get("Cache-Control"), "no-cache")
        self.assertEqual(pair_html, root_html)
        self.assertEqual(pair_response.headers.get("Referrer-Policy"), "no-referrer")
        self.assertIn("javascript", asset_response.headers.get("Content-Type", ""))
        self.assertEqual(asset_response.headers.get("Cache-Control"), "no-cache")
        self.assertEqual(asset_body, "console.log('react preview');")
        try:
            self.assertEqual(legacy_raised.exception.code, 404)
            self.assertEqual(escaped.exception.code, 404)
        finally:
            legacy_raised.exception.close()
            escaped.exception.close()


    def test_react_app_preview_route_reports_missing_dist_without_crashing(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "room"
            missing_dist = Path(temp_dir) / "missing-dist"
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root, frontend_dist_root=missing_dist))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                base = f"http://127.0.0.1:{server.server_port}"
                with self.assertRaises(HTTPError) as raised:
                    urlopen(f"{base}/app/", timeout=4)
            finally:
                server.shutdown()
                server.server_close()

        try:
            self.assertEqual(raised.exception.code, 503)
            self.assertIn("npm --prefix frontend run build", raised.exception.read().decode("utf-8"))
        finally:
            raised.exception.close()


    def test_meeting_payload_endpoint_cannot_escape_meetings_root(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "meetings").mkdir()
            escaped_dir = root / "escaped"
            escaped_dir.mkdir()
            (escaped_dir / "meeting.json").write_text(
                json.dumps({"meeting_id": "escaped", "topic": "outside meetings"}),
                encoding="utf-8",
            )
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with self.assertRaises(HTTPError) as raised:
                    urlopen(f"http://127.0.0.1:{server.server_port}/api/meetings/..%2Fescaped", timeout=4)
            finally:
                server.shutdown()
                server.server_close()

        try:
            self.assertEqual(raised.exception.code, 404)
        finally:
            raised.exception.close()


    def test_provider_catalog_payload_lists_planned_integrations(self):
        payload = provider_catalog_payload()
        providers = {provider["kind"]: provider for provider in payload["providers"]}

        self.assertEqual(providers["codex"]["status"], "available")
        self.assertEqual(providers["anthropic"]["status"], "available")
        self.assertEqual(providers["gemini"]["status"], "available")
        self.assertEqual(providers["grok"]["status"], "available")
        self.assertEqual(providers["local_openai_compatible"]["status"], "available")
        self.assertIn("capabilities", providers["claude_code"])
        self.assertTrue(providers["cursor"]["capabilities"]["supports_filesystem"])


    def test_provider_health_endpoint_checks_runtime_config_without_starting_meeting(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = root / "agents.json"
            config_path.write_text(
                json.dumps(
                    {
                        "providers": [
                            {"id": "mock-provider", "kind": "mock", "display_name": "Mock"}
                        ],
                        "permission_profiles": [{"id": "meeting"}],
                        "agent_bindings": [
                            {
                                "agent_id": "mock-agent",
                                "role_id": "lore_lawyer",
                                "provider_id": "mock-provider",
                                "permission_profile_id": "meeting",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/provider-health",
                    data=json.dumps(
                        {
                            "config_path": str(config_path),
                            "probe_mode": "local",
                            "probe_timeout_seconds": 0.75,
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

            self.assertEqual(payload["status"], "ok")
            self.assertEqual(payload["probe_mode"], "local")
            self.assertEqual(payload["config_path"], "[redacted]")
            self.assertEqual(payload["summary"]["providers"], 1)
            self.assertEqual(payload["providers"][0]["provider_id"], "mock-provider")
            self.assertNotIn(str(config_path), json.dumps(payload, ensure_ascii=False))


    def test_provider_health_endpoint_redacts_sensitive_config_failure_payload(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            missing_config = root / "private" / "agents.secret.json"
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/provider-health",
                    data=json.dumps({"config_path": str(missing_config)}).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urlopen(request, timeout=4) as response:
                    payload = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

            self.assertEqual(payload["status"], "failed")
            self.assertEqual(payload["config_path"], "[redacted]")
            self.assertEqual(payload["checks"][0]["message"], "Config load failed: details redacted.")
            serialized_payload = json.dumps(payload, ensure_ascii=False)
            self.assertNotIn(str(missing_config), serialized_payload)
            self.assertNotIn("agents.secret.json", serialized_payload)


    def test_provider_health_endpoint_redacts_malformed_config_failure_payload(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = root / "agents.json"
            config_path.write_text("{", encoding="utf-8")
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/provider-health",
                    data=json.dumps({"config_path": str(config_path)}).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urlopen(request, timeout=4) as response:
                    payload = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

            self.assertEqual(payload["status"], "failed")
            self.assertEqual(payload["config_path"], "[redacted]")
            self.assertEqual(payload["checks"][0]["message"], "Config load failed: details redacted.")
            serialized_payload = json.dumps(payload, ensure_ascii=False)
            self.assertNotIn(str(config_path), serialized_payload)
            self.assertNotIn("Expecting", serialized_payload)
            self.assertNotIn("line 1", serialized_payload)
            self.assertNotIn("char 0", serialized_payload)


    def test_provider_health_endpoint_forwards_bridge_probe_options(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = root / "agents.json"
            config_path.write_text("{}", encoding="utf-8")
            report = {
                "status": "ok",
                "probe_mode": "bridge",
                "summary": {"providers": 0, "failed_providers": 0, "bindings": 0, "failed_bindings": 0, "checks_failed": 0, "warnings": 0},
                "providers": [],
                "bindings": [],
            }
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/provider-health",
                    data=json.dumps(
                        {
                            "config_path": str(config_path),
                            "probe_mode": "bridge",
                            "probe_timeout_seconds": 0.75,
                        }
                    ).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with patch("agentsassemble.gui.provider_health_report", return_value=report) as provider_health:
                    with urlopen(request, timeout=4) as response:
                        payload = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

            self.assertEqual(payload["probe_mode"], "bridge")
            provider_health.assert_called_once_with(
                config_path,
                probe_mode="bridge",
                probe_timeout_seconds=0.75,
            )


    def test_provider_health_endpoint_forwards_api_probe_options(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = root / "agents.json"
            config_path.write_text("{}", encoding="utf-8")
            report = {
                "status": "ok",
                "probe_mode": "api",
                "summary": {"providers": 0, "failed_providers": 0, "bindings": 0, "failed_bindings": 0, "checks_failed": 0, "warnings": 0},
                "providers": [],
                "bindings": [],
            }
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/provider-health",
                    data=json.dumps(
                        {
                            "config_path": str(config_path),
                            "probe_mode": "api",
                            "probe_timeout_seconds": 0.75,
                        }
                    ).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with patch("agentsassemble.gui.provider_health_report", return_value=report) as provider_health:
                    with urlopen(request, timeout=4) as response:
                        payload = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

            self.assertEqual(payload["probe_mode"], "api")
            provider_health.assert_called_once_with(
                config_path,
                probe_mode="api",
                probe_timeout_seconds=0.75,
            )


    def test_local_resources_endpoint_returns_sanitized_read_only_snapshot(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            payload = {
                "status": "ok",
                "summary": {"process_count": 1, "total_cpu_pct": 2.5, "total_rss_kb": 4096, "attention": []},
                "processes": [{"pid": 123, "comm": "python3", "role": "agentsassemble", "cpu_pct": 2.5, "rss_kb": 4096}],
            }
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with patch(
                    "agentsassemble.web.routes.observability.cached_local_resource_snapshot",
                    return_value=payload,
                ) as snapshot_payload:
                    with urlopen(f"http://127.0.0.1:{server.server_port}/api/local-resources", timeout=4) as response:
                        response_payload = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

            self.assertEqual(response_payload, payload)
            snapshot_payload.assert_called_once_with(supervised_pids=set())


    def test_release_health_endpoint_returns_catalog_only(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with urlopen(f"http://127.0.0.1:{server.server_port}/api/release-health", timeout=4) as response:
                    payload = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

            serialized = json.dumps(payload, ensure_ascii=False)
            self.assertEqual(payload["status"], "ok")
            self.assertEqual(payload["schema_version"], 1)
            self.assertIn("frontend_react_build", [check["id"] for check in payload["checks"]])
            first_check = payload["checks"][0]
            self.assertEqual(first_check["order"], 1)
            self.assertTrue(first_check["default_run"])
            self.assertEqual(first_check["safety_class"], "frontend_react_build")
            benchmark = next(check for check in payload["checks"] if check["id"] == "room_event_benchmark")
            self.assertIsNone(benchmark["order"])
            self.assertFalse(benchmark["default_run"])
            self.assertEqual(benchmark["safety_class"], "local_room_benchmark")
            self.assertNotIn("argv", serialized)
            self.assertNotIn("cwd", serialized)
            self.assertNotIn("env", serialized)
            self.assertNotIn("--check", serialized)
            self.assertNotIn(str(root), serialized)
            self.assertNotIn("/Users/", serialized)


    def test_release_health_queue_endpoint_returns_safe_latest_projection(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            latest_dir = root / "release_health"
            latest_dir.mkdir(parents=True)
            (latest_dir / "latest.json").write_text(
                json.dumps(
                    {
                        "status": "failed",
                        "completed_at": "2026-05-29T00:01:20+00:00",
                        "duration_seconds": 12.5,
                        "summary": {"total": 1, "passed": 0, "failed": 1, "skipped": 0, "ok": False},
                        "results": [
                            {
                                "id": "frontend_react_build",
                                "status": "failed",
                                "duration_seconds": 0.7,
                                "stdout_tail": f"private output {root}",
                                "stderr_tail": "SECRET_TOKEN=abc",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with urlopen(f"http://127.0.0.1:{server.server_port}/api/release-health/queue", timeout=4) as response:
                    payload = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

            serialized = json.dumps(payload, ensure_ascii=False)
            self.assertEqual(payload["status"], "ok")
            self.assertTrue(payload["source"]["has_latest_run"])
            self.assertEqual(payload["source"]["latest_status"], "failed")
            by_id = {check["id"]: check for check in payload["checks"]}
            self.assertEqual(by_id["frontend_react_build"]["latest_status"], "failed")
            self.assertEqual(by_id["frontend_react_build"]["latest_duration_seconds"], 0.7)
            self.assertNotIn("stdout_tail", serialized)
            self.assertNotIn("stderr_tail", serialized)
            self.assertNotIn("SECRET_TOKEN", serialized)
            self.assertNotIn(str(root), serialized)
            self.assertNotIn("/Users/", serialized)


    def test_release_health_queue_endpoint_projects_safe_room_benchmark_summary(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            latest_dir = root / "release_health"
            latest_dir.mkdir(parents=True)
            (latest_dir / "latest.json").write_text(
                json.dumps(
                    {
                        "status": "ok",
                        "completed_at": "2026-05-29T00:01:20+00:00",
                        "duration_seconds": float("inf"),
                        "summary": {"total": float("inf"), "passed": 1, "failed": 0, "skipped": 0, "ok": True},
                        "results": [
                            {
                                "id": "frontend_react_build",
                                "status": "passed",
                                "duration_seconds": 0.1,
                                "benchmark_summary": {
                                    "status": "ok",
                                    "metrics_summary": {
                                        "flow_anchor_share_improvement": 0.99,
                                    },
                                },
                            },
                            {
                                "id": "room_event_benchmark",
                                "status": "passed",
                                "duration_seconds": 1.25,
                                "benchmark_summary": {
                                    "status": "ok",
                                    "metrics_summary": {
                                        "flow_anchor_share_off": 0.65,
                                        "flow_anchor_share_on": 0.25,
                                        "flow_anchor_share_improvement": 0.4,
                                        "flow_scheduler_predicate_p99_ms": 12.5,
                                        "secret_metric": "SECRET_TOKEN=abc",
                                    },
                                    "regression_signals": [
                                        {
                                            "name": "flow_scheduler_predicate_p99_ms",
                                            "value_ms": 12.5,
                                            "ceiling_ms": 75.0,
                                            "ok": True,
                                            "raw_path": str(root),
                                        },
                                        {
                                            "name": "flow_anchor_share_improvement",
                                            "value": 0.4,
                                            "floor": 0.25,
                                            "ok": True,
                                            "command": "--warmup-events",
                                        },
                                        {
                                            "name": "SECRET_TOKEN_signal",
                                            "value": 999,
                                            "ok": False,
                                        },
                                    ],
                                    "temporary_root": str(root),
                                    "argv": ["python3", "-m", "agentsassemble.cli"],
                                    "raw_stdout": "SECRET_PROVIDER_OUTPUT",
                                },
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with urlopen(f"http://127.0.0.1:{server.server_port}/api/release-health/queue", timeout=4) as response:
                    payload = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

            serialized = json.dumps(payload, ensure_ascii=False)
            by_id = {check["id"]: check for check in payload["checks"]}
            self.assertNotIn("benchmark_summary", by_id["frontend_react_build"])
            benchmark = by_id["room_event_benchmark"]["benchmark_summary"]
            self.assertEqual(benchmark["status"], "ok")
            self.assertEqual(benchmark["metrics_summary"]["flow_anchor_share_improvement"], 0.4)
            self.assertEqual(benchmark["metrics_summary"]["flow_scheduler_predicate_p99_ms"], 12.5)
            self.assertEqual(
                benchmark["regression_signals"],
                [
                    {
                        "name": "flow_scheduler_predicate_p99_ms",
                        "ok": True,
                        "value_ms": 12.5,
                        "ceiling_ms": 75.0,
                    },
                    {
                        "name": "flow_anchor_share_improvement",
                        "ok": True,
                        "value": 0.4,
                        "floor": 0.25,
                    },
                ],
            )
            self.assertEqual(payload["source"]["latest_duration_seconds"], None)
            self.assertEqual(payload["summary"]["latest_total"], 0)
            for forbidden in (
                "SECRET_TOKEN",
                "SECRET_PROVIDER_OUTPUT",
                "secret_metric",
                "temporary_root",
                "argv",
                "raw_stdout",
                "--warmup-events",
                str(root),
                "/Users/",
                "agentsassemble.cli",
            ):
                self.assertNotIn(forbidden, serialized)
