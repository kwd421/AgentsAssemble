import tempfile
import unittest
import json
import os
import threading
import time
from pathlib import Path
from http.server import ThreadingHTTPServer
from urllib.request import Request, urlopen

from agentsassemble.gui import (
    _make_handler,
    _safe_static_path,
    _sse_event,
    _stream_snapshot_payload,
    append_lobby_event,
    build_meeting_payload,
    list_meetings,
    provider_catalog_payload,
    read_lobby,
    read_side_chat,
    send_lobby_message_to_remote_bridge,
    append_side_chat_event,
)
from agentsassemble.meeting_events import append_live_event, write_live_state
from agentsassemble.meeting_events import read_live_events_after, read_lobby_events_after, read_side_chat_events_after
from agentsassemble.meeting import run_demo_meeting


class GuiServerTests(unittest.TestCase):
    def test_build_meeting_payload_contains_tabs_and_artifacts(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            result = run_demo_meeting(adapter_name="mock", output_root=Path(temp_dir))

            payload = build_meeting_payload(result.meeting_dir)

            self.assertEqual(payload["meeting"]["meeting_id"], result.meeting_id)
            self.assertIn("agenda.md", payload["artifacts"])
            self.assertIn("transcript.md", payload["artifacts"])
            self.assertIn("decision.md", payload["artifacts"])
            self.assertIn("meeting.json", payload["artifacts"])
            self.assertIn("lore_lawyer/research.md", payload["research"])
            self.assertIn("lore_lawyer", payload["research_json"])
            self.assertIn("evidence_gate", payload["research_json"]["lore_lawyer"])
            self.assertIn("lore_lawyer.md", payload["return_packets"])
            self.assertEqual(payload["tabs"], ["lobby", "live", "board", "archive"])
            self.assertEqual(payload["tab_labels"]["lobby"], "로비")
            self.assertEqual(payload["tab_labels"]["live"], "실황")
            self.assertEqual(payload["tab_labels"]["board"], "작전판")
            self.assertEqual(payload["tab_labels"]["archive"], "아카이브")

    def test_build_meeting_payload_includes_room_log_for_free_chat(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            result = run_demo_meeting(adapter_name="mock", output_root=Path(temp_dir), meeting_mode="free_chat")

            payload = build_meeting_payload(result.meeting_dir)

            self.assertEqual(payload["meeting"]["meeting_mode"], "free_chat")
            self.assertIn("room-log.md", payload["artifacts"])
            self.assertIn("informal free-chat record", payload["artifacts"]["room-log.md"])
            self.assertEqual(payload["artifacts"].get("decision.md"), "")
            self.assertEqual(payload["artifacts"].get("transcript.md"), "")

    def test_build_meeting_payload_preserves_codex_live_session_binding_metadata(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            meeting_dir = Path(temp_dir) / "meetings" / "m1"
            meeting_dir.mkdir(parents=True)
            (meeting_dir / "meeting.json").write_text(
                json.dumps(
                    {
                        "meeting_id": "m1",
                        "topic": "테스트",
                        "question": "기존 세션을 이어받나?",
                        "roles": [
                            {
                                "id": "lore_lawyer",
                                "display_name": "설정충",
                                "lens": "Canon Analyst",
                                "research_focus": "canon",
                            }
                        ],
                        "provider_configs": {
                            "codex-live": {
                                "id": "codex-live",
                                "kind": "codex_live_session",
                                "display_name": "Codex CLI Live Session",
                            }
                        },
                        "permission_profiles": {
                            "codex_live_meeting_readonly": {"id": "codex_live_meeting_readonly"}
                        },
                        "agent_bindings": [
                            {
                                "agent_id": "codex-live-lore-lawyer",
                                "role_id": "lore_lawyer",
                                "owner_id": "host",
                                "provider_id": "codex-live",
                                "model_id": "local-codex-session",
                                "permission_profile_id": "codex_live_meeting_readonly",
                                "join_mode": "current_session",
                                "session_id": "019e3038-39cc-76a2-a746-5ba8c0f3b408",
                            }
                        ],
                        "debate_rounds": [],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            payload = build_meeting_payload(meeting_dir)

            binding = payload["meeting"]["agent_bindings"][0]
            self.assertEqual(binding["join_mode"], "current_session")
            self.assertEqual(binding["session_id"], "019e3038-39cc-76a2-a746-5ba8c0f3b408")
            self.assertEqual(payload["meeting"]["provider_configs"]["codex-live"]["kind"], "codex_live_session")

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

    def test_lobby_sse_keeps_connection_open_with_heartbeat(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            append_lobby_event(root, {"name": "나", "side": "mine", "message": "첫 로비"})
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with urlopen(f"http://127.0.0.1:{server.server_port}/api/events/lobby", timeout=4) as response:
                    lines = []
                    deadline = time.time() + 3
                    while time.time() < deadline and ": keep-alive" not in "\n".join(lines):
                        lines.append(response.readline().decode("utf-8").strip())
            finally:
                server.shutdown()
                server.server_close()

            body = "\n".join(lines)
            self.assertIn("event: lobby", body)
            self.assertIn('"stream": "lobby"', body)
            self.assertIn(": keep-alive", body)

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
                    while time.time() < deadline and "meeting_payload" not in "\n".join(lines):
                        lines.append(response.readline().decode("utf-8").strip())
            finally:
                server.shutdown()
                server.server_close()

            body = "\n".join(lines)
            self.assertIn("event: meeting", body)
            self.assertIn('"meeting_payload"', body)
            self.assertIn('"live_status": "complete"', body)

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

    def test_meeting_stream_includes_full_payload_after_final_record(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            result = run_demo_meeting(adapter_name="mock", output_root=root)

            payload = _stream_snapshot_payload(root, "meeting", meeting_id=result.meeting_id)

            self.assertEqual(payload["stream"], "meeting")
            self.assertEqual(payload["meeting_payload"]["meeting"]["live_status"], "complete")
            self.assertIn("decision_gate", payload["meeting_payload"]["meeting"])
            self.assertIn("decision.md", payload["meeting_payload"]["artifacts"])

    def test_meeting_stream_survives_partial_final_record(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            meeting_dir = root / "meetings" / "m1"
            meeting_dir.mkdir(parents=True)
            event = append_live_event(meeting_dir, {"kind": "message", "content": "공식"})
            (meeting_dir / "meeting.json").write_text("{", encoding="utf-8")

            payload = _stream_snapshot_payload(root, "meeting", meeting_id="m1")

            self.assertEqual(payload["stream"], "meeting")
            self.assertTrue(payload["meeting_payload_pending"])
            self.assertNotIn("meeting_payload", payload)

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
            self.assertEqual(recovered["meeting_payload"]["meeting"]["live_status"], "complete")

    def test_lobby_remote_bridge_reply_is_recorded_as_other_agent(self):
        import agentsassemble.gui as gui

        class FakeRequester:
            def __init__(self):
                self.calls = []

            def __call__(self, url, headers, payload, timeout_seconds):
                self.calls.append({"url": url, "headers": headers, "payload": payload})
                return {
                    "text": '{"message":"친구 Claude Code 준비됐습니다.","kind":"message"}',
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
            previous_requester = gui.REMOTE_LOBBY_REQUESTER
            gui.REMOTE_LOBBY_REQUESTER = requester
            try:
                event = send_lobby_message_to_remote_bridge(root, "친구야 준비됐어?", meeting_id="m1", speaker_name="나")
            finally:
                gui.REMOTE_LOBBY_REQUESTER = previous_requester

            self.assertEqual(event["side"], "other-agent")
            self.assertEqual(event["name"], "공식이뭘알아")
            self.assertEqual(event["message"], "친구 Claude Code 준비됐습니다.")
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

    def test_static_paths_cannot_escape_static_root(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            static_root = root / "static"
            static_root.mkdir()
            (static_root / "app.js").write_text("", encoding="utf-8")

            self.assertEqual(_safe_static_path(static_root, "app.js"), (static_root / "app.js").resolve())
            self.assertIsNone(_safe_static_path(static_root, "../secret.txt"))

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


if __name__ == "__main__":
    unittest.main()
