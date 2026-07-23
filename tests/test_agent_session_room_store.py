import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from agentsassemble.agent_sessions import (
    AgyAgentSessionAdapter,
    AgentSessionProcessService,
    ClaudeAgentSessionAdapter,
    CodexAppServerRuntime,
    CodexAppServerRuntimeManager,
    GrokAgentSessionAdapter,
    agent_session_command_turn_runner,
    agent_session_codex_jsonl_turn_runner,
    agent_session_streaming_command_turn_runner,
    build_provider_recovery_input,
    build_provider_turn_input,
    build_agent_session_launch_plan,
    build_agent_session_turn_command,
    build_room_turn_packet,
    create_agent_session_payload,
    enqueue_agent_session_auto_turn_for_lobby_event,
    resume_agent_session_payload,
    runtime_profile_key,
    run_next_agent_session_turn_payload,
    run_agent_session_turn_payload,
    room_sse_frames_after_cursor,
    stream_room_sse_frames,
)
from agentsassemble.room_store import RoomStore
from agentsassemble.room_turn_context import _agent_turn_prompt


class AgentSessionRoomStoreTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.output_root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def test_room_state_persists_left_participant_after_reload(self):
        store = RoomStore(self.output_root)
        store.create_room("room-a", label="Room A")
        resume_agent_session_payload(
            self.output_root,
            {
                "room_id": "room-a",
                "agent_id": "agent-1",
                "session_id": "session-1",
                "display_name": "Agent One",
            },
        )
        store.set_participant_status("room-a", "agent-1", "left")

        reloaded = RoomStore(self.output_root)
        participant = reloaded.participant("room-a", "agent-1")

        self.assertEqual(participant["status"], "left")
        self.assertEqual(reloaded.active_participants("room-a"), [])
        event_types = [event["type"] for event in reloaded.read_events("room-a")]
        self.assertIn("participant_left", event_types)

    def test_same_agent_session_resume_deduplicates_roster_and_records_history(self):
        first = resume_agent_session_payload(
            self.output_root,
            {
                "room_id": "room-a",
                "agent_id": "agent-1",
                "session_id": "session-1",
                "display_name": "Agent One",
                "model": "gpt-5.5",
                "effort": "high",
                "sandbox": "read-only",
                "permissions": "prompt",
            },
        )
        second = resume_agent_session_payload(
            self.output_root,
            {
                "room_id": "room-a",
                "agent_id": "agent-1",
                "session_id": "session-1",
                "display_name": "Agent One",
                "model": "gpt-5.5",
                "effort": "high",
                "sandbox": "read-only",
                "permissions": "prompt",
            },
        )

        store = RoomStore(self.output_root)
        self.assertEqual(first["participant"]["participant_id"], second["participant"]["participant_id"])
        self.assertEqual(len(store.active_participants("room-a")), 1)
        self.assertEqual(len(store.sessions("room-a")), 1)
        self.assertEqual(
            [event["type"] for event in store.read_events("room-a")].count("session_resumed"),
            2,
        )

    def test_create_agent_session_payload_is_roomstore_source_of_truth(self):
        result = create_agent_session_payload(
            self.output_root,
            {
                "room_id": "room-a",
                "agent_id": "agent-1",
                "display_name": "Agent One",
                "owner_id": "user-local",
                "created_by": "user-local",
                "provider_kind": "codex_live_session",
                "model": "gpt-5.3-codex-spark",
                "effort": "medium",
                "sandbox": "read-only",
                "permissions": "never",
                "runtime_sharing_policy": "isolated_session",
            },
        )

        store = RoomStore(self.output_root)
        participant = store.participant("room-a", "agent-1")
        session = store.session("room-a", "agent-1")

        self.assertEqual(result["status"], "created")
        self.assertEqual(participant["owner_id"], "user-local")
        self.assertEqual(participant["created_by"], "user-local")
        self.assertEqual(participant["display_name"], "Agent One")
        self.assertEqual(session["provider_kind"], "codex_live_session")
        self.assertEqual(session["model"], "gpt-5.3-codex-spark")
        self.assertEqual(session["runtime_sharing_policy"], "isolated_session")
        self.assertIn("agent_session_created", [event["type"] for event in store.read_events("room-a")])

    def test_room_message_schedules_next_agent_session_turn_without_side_instruction(self):
        store = RoomStore(self.output_root)
        create_agent_session_payload(
            self.output_root,
            {"room_id": "room-a", "agent_id": "agent-a", "display_name": "Agent A"},
        )
        create_agent_session_payload(
            self.output_root,
            {"room_id": "room-a", "agent_id": "agent-b", "display_name": "Agent B"},
        )
        user_event = store.append_event("room-a", "message_final", actor_id="human-1", content="방 메시지에 답해줘.")
        seen_packets: list[dict[str, object]] = []

        def fake_runner(packet: dict[str, object]):
            seen_packets.append(packet)
            yield {"type": "message_final", "content": "Agent reply"}

        result = run_next_agent_session_turn_payload(
            self.output_root,
            {"room_id": "room-a", "trigger_event_id": user_event["id"]},
            turn_runner=fake_runner,
        )

        events = store.read_events("room-a")
        self.assertEqual(result["turn_status"], "finished")
        self.assertEqual(result["participant_id"], "agent-a")
        self.assertEqual(seen_packets[0]["current_turn_instruction"], "Respond to the latest room message.")
        self.assertIn("방 메시지에 답해줘.", seen_packets[0]["provider_input"])
        self.assertIn("turn_queued", [event["type"] for event in events])
        self.assertIn("turn_assigned", [event["type"] for event in events])

        second_user_event = store.append_event("room-a", "message_final", actor_id="human-1", content="다음 응답.")
        second = run_next_agent_session_turn_payload(
            self.output_root,
            {"room_id": "room-a", "trigger_event_id": second_user_event["id"]},
            turn_runner=fake_runner,
        )
        self.assertEqual(second["participant_id"], "agent-b")

    def test_human_lobby_event_auto_enqueues_agent_session_turn(self):
        create_agent_session_payload(
            self.output_root,
            {"room_id": "room-a", "agent_id": "agent-a", "display_name": "Agent A"},
        )
        seen_packets: list[dict[str, object]] = []

        def fake_runner(packet: dict[str, object]):
            seen_packets.append(packet)
            yield {"type": "message_final", "content": "자동 응답"}

        result = enqueue_agent_session_auto_turn_for_lobby_event(
            self.output_root,
            {
                "id": "lobby-1",
                "kind": "message",
                "message": "방에 쓴 말에 답해줘.",
                "flow_meeting_id": "room-a",
                "actor_id": "human-1",
                "actor_type": "human",
            },
            turn_runner=fake_runner,
            run_background=False,
        )

        events = RoomStore(self.output_root).read_events("room-a")
        self.assertEqual(result["status"], "finished")
        self.assertEqual(result["participant_id"], "agent-a")
        self.assertEqual(result["room_event"]["type"], "message_final")
        self.assertIn("방에 쓴 말에 답해줘.", seen_packets[0]["provider_input"])
        self.assertIn("turn_queued", [event["type"] for event in events])
        self.assertEqual(events[-2]["content"], "자동 응답")

    def test_agent_lobby_event_does_not_auto_chain_agent_session_turn(self):
        create_agent_session_payload(
            self.output_root,
            {"room_id": "room-a", "agent_id": "agent-a", "display_name": "Agent A"},
        )

        result = enqueue_agent_session_auto_turn_for_lobby_event(
            self.output_root,
            {
                "id": "lobby-agent",
                "kind": "message",
                "message": "agent reply",
                "flow_meeting_id": "room-a",
                "actor_id": "agent-a",
                "actor_type": "agent",
            },
            run_background=False,
        )

        self.assertEqual(result["status"], "ignored")
        self.assertNotIn("turn_queued", [event["type"] for event in RoomStore(self.output_root).read_events("room-a")])

    def test_export_marks_participant_and_session_and_writes_handoff_packet(self):
        resume_agent_session_payload(
            self.output_root,
            {"room_id": "room-a", "agent_id": "agent-1", "session_id": "session-1"},
        )
        store = RoomStore(self.output_root)
        result = store.export_participant("room-a", "agent-1", reason="return to owner")
        reloaded = RoomStore(self.output_root)

        self.assertEqual(reloaded.participant("room-a", "agent-1")["status"], "exported")
        self.assertEqual(reloaded.session("room-a", "session-1")["status"], "detached")
        self.assertEqual(reloaded.active_participants("room-a"), [])
        self.assertTrue(Path(result["handoff_packet_path"]).exists())
        self.assertIn("agent-1", Path(result["handoff_packet_path"]).read_text(encoding="utf-8"))
        self.assertIn("participant_exported", [event["type"] for event in reloaded.read_events("room-a")])

    def test_turn_packet_uses_room_events_and_media_without_repo_context(self):
        store = RoomStore(self.output_root)
        store.create_room("room-a")
        store.append_event("room-a", "message_final", actor_id="human-1", content="Please compare these images.")
        media = store.attach_media(
            "room-a",
            filename="diagram.png",
            content_type="image/png",
            size=123,
            supported=True,
        )
        session_result = resume_agent_session_payload(
            self.output_root,
            {"room_id": "room-a", "agent_id": "agent-1", "session_id": "session-1"},
        )

        packet = build_room_turn_packet(
            self.output_root,
            room_id="room-a",
            participant_id=session_result["participant"]["participant_id"],
            session_id="session-1",
            instruction="It is your turn.",
            media_ids=[media["id"]],
        )

        self.assertEqual(packet["room_id"], "room-a")
        self.assertEqual([event["type"] for event in packet["events"]], ["message_final"])
        self.assertEqual(packet["media_manifest"][0]["id"], media["id"])
        self.assertNotIn(str(Path.cwd()), str(packet))
        self.assertIn("Do not inspect or edit the project", packet["provider_input"])

    def test_turn_packet_uses_injected_repository_without_opening_sqlite_again(self):
        store = RoomStore(self.output_root)
        store.create_room("room-a")
        store.upsert_participant(
            "room-a",
            {
                "participant_id": "agent-1",
                "display_name": "Agent One",
                "participant_type": "agent",
            },
        )
        store.upsert_session(
            "room-a",
            {
                "session_id": "session-1",
                "participant_id": "agent-1",
                "status": "attached",
            },
        )
        store.append_event("room-a", "message_final", actor_id="human-1", content="hello")

        with patch(
            "agentsassemble.room.turn_context.RoomStore",
            side_effect=AssertionError("unexpected SQLite repository construction"),
        ):
            packet = build_room_turn_packet(
                self.output_root,
                room_id="room-a",
                participant_id="agent-1",
                session_id="session-1",
                instruction="Reply once.",
                repository=store,
            )

        self.assertEqual(packet["provider_visible_event_count"], 1)
        self.assertIn("hello", packet["provider_input"])

    def test_sse_replays_missed_events_and_emits_heartbeat_when_idle(self):
        store = RoomStore(self.output_root)
        store.create_room("room-a")
        first_id = store.read_events("room-a")[-1]["id"]
        store.append_event("room-a", "message_final", actor_id="human-1", content="hello")

        frames = room_sse_frames_after_cursor(self.output_root, "room-a", cursor=first_id)
        heartbeat = room_sse_frames_after_cursor(self.output_root, "room-a", cursor=store.read_events("room-a")[-1]["id"])

        self.assertEqual(len(frames), 1)
        self.assertIn("event: message_final", frames[0])
        self.assertIn("data:", frames[0])
        self.assertEqual(heartbeat, ["event: heartbeat\ndata: {}\n\n"])

    def test_fake_turn_runner_receives_packet_and_appends_turn_events(self):
        store = RoomStore(self.output_root)
        store.create_room("room-a")
        store.append_event("room-a", "message_final", actor_id="human-1", content="Please inspect the media.")
        supported = store.attach_media(
            "room-a",
            filename="diagram.png",
            content_type="image/png",
            data=b"image",
            supported=True,
        )
        unsupported = store.attach_media(
            "room-a",
            filename="notes.bin",
            content_type="application/octet-stream",
            data=b"binary",
            supported=False,
        )
        resume_agent_session_payload(
            self.output_root,
            {
                "room_id": "room-a",
                "agent_id": "agent-1",
                "session_id": "session-1",
                "provider_kind": "codex_live_session",
            },
        )
        packets = []

        def fake_runner(packet):
            packets.append(packet)
            yield {"type": "thinking_delta", "content": "checking context"}
            yield {"type": "message_delta", "content": "draft"}
            yield {"type": "message_final", "content": "Final answer"}

        result = run_agent_session_turn_payload(
            self.output_root,
            {
                "room_id": "room-a",
                "agent_id": "agent-1",
                "session_id": "session-1",
                "instruction": "Answer now.",
                "media_ids": [supported["id"], unsupported["id"]],
            },
            turn_runner=fake_runner,
        )

        self.assertEqual(result["turn_status"], "finished")
        self.assertEqual(packets[0]["current_turn_instruction"], "Answer now.")
        self.assertEqual([event["type"] for event in packets[0]["events"]], ["message_final"])
        self.assertEqual([media["id"] for media in packets[0]["media_manifest"]], [supported["id"], unsupported["id"]])
        self.assertIn("Unsupported media", packets[0]["media_notes"][0])
        self.assertIn("Unsupported media", packets[0]["provider_input"])
        self.assertNotIn(str(Path.cwd()), str(packets[0]))
        events = store.read_events("room-a")
        event_types = [event["type"] for event in events]
        self.assertIn("turn_started", event_types)
        self.assertIn("thinking_delta", event_types)
        self.assertIn("message_delta", event_types)
        self.assertIn("message_final", event_types)
        self.assertIn("turn_finished", event_types)
        self.assertEqual(events[-2]["content"], "Final answer")

    def test_resume_without_turn_and_turn_dry_run_do_not_append_message_final(self):
        resume_agent_session_payload(
            self.output_root,
            {
                "room_id": "room-a",
                "agent_id": "agent-1",
                "session_id": "session-1",
                "provider_kind": "codex_live_session",
            },
        )
        store = RoomStore(self.output_root)
        before = [event["type"] for event in store.read_events("room-a")]
        self.assertNotIn("message_final", before)

        result = run_agent_session_turn_payload(
            self.output_root,
            {
                "room_id": "room-a",
                "agent_id": "agent-1",
                "session_id": "session-1",
                "instruction": "Dry run.",
                "dry_run": True,
            },
            turn_runner=lambda packet: [{"type": "message_final", "content": "not appended"}],
        )

        self.assertEqual(result["turn_status"], "not_started")
        self.assertNotIn("message_final", [event["type"] for event in store.read_events("room-a")])

    def test_process_service_turn_uses_same_runtime_path(self):
        resume_agent_session_payload(
            self.output_root,
            {
                "room_id": "room-a",
                "agent_id": "agent-1",
                "session_id": "session-1",
                "provider_kind": "codex_live_session",
            },
        )
        service = AgentSessionProcessService(
            turn_runner=lambda packet: [{"type": "message_final", "content": "via service"}]
        )

        result = service.run_turn(
            self.output_root,
            {
                "room_id": "room-a",
                "agent_id": "agent-1",
                "session_id": "session-1",
                "instruction": "Use shared service.",
            },
        )

        self.assertEqual(result["turn_status"], "finished")
        self.assertEqual(RoomStore(self.output_root).read_events("room-a")[-2]["content"], "via service")

    def test_command_turn_runner_passes_packet_through_stdin_and_captures_stdout(self):
        resume_agent_session_payload(
            self.output_root,
            {
                "room_id": "room-a",
                "agent_id": "agent-1",
                "session_id": "session-1",
                "provider_kind": "codex_live_session",
                "sandbox": "read-only",
            },
        )
        calls = []

        class Completed:
            returncode = 0
            stdout = "command answer"
            stderr = ""

        def fake_command_runner(command, prompt, timeout_seconds):
            calls.append((command, prompt, timeout_seconds))
            return Completed()

        result = run_agent_session_turn_payload(
            self.output_root,
            {
                "room_id": "room-a",
                "agent_id": "agent-1",
                "session_id": "session-1",
                "instruction": "Use command.",
                "timeout_seconds": 12,
            },
            turn_command_runner=fake_command_runner,
        )

        self.assertEqual(result["turn_status"], "finished")
        command, prompt, timeout_seconds = calls[0]
        self.assertEqual(command[-1], "-")
        self.assertIn("codex", command[0])
        self.assertIn("[Your turn]\nUse command.", prompt)
        self.assertNotIn('"session_id"', prompt)
        self.assertEqual(timeout_seconds, 12)
        self.assertEqual(RoomStore(self.output_root).read_events("room-a")[-2]["content"], "command answer")

    def test_command_turn_runner_reports_nonzero_without_final_message(self):
        session = {
            "provider_kind": "codex_live_session",
            "session_id": "session-1",
            "sandbox": "read-only",
        }

        class Completed:
            returncode = 7
            stdout = "ignored"
            stderr = "safe stderr"

        runner = agent_session_command_turn_runner(
            session,
            command_runner=lambda command, prompt, timeout_seconds: Completed(),
        )

        chunks = list(runner({"room_id": "room-a", "current_turn_instruction": "x"}))

        self.assertEqual(chunks[0]["type"], "error")
        self.assertIn("provider command exited 7", str(chunks[0]["diagnostics"]))
        self.assertIn("safe stderr", str(chunks[0]["diagnostics"]))
        self.assertEqual(build_agent_session_turn_command(session)[-1], "-")

    def test_streaming_command_runner_appends_message_delta_before_final(self):
        resume_agent_session_payload(
            self.output_root,
            {
                "room_id": "room-a",
                "agent_id": "agent-1",
                "session_id": "session-1",
                "provider_kind": "codex_live_session",
            },
        )

        def fake_streamer(command, prompt, timeout_seconds):
            self.assertEqual(command[-1], "-")
            self.assertIn("[Your turn]\nStream.", prompt)
            self.assertNotIn('"session_id"', prompt)
            yield {"type": "message_delta", "content": "hello "}
            yield {"type": "message_delta", "content": "world"}
            yield {"type": "message_final", "content": "hello world"}

        result = run_agent_session_turn_payload(
            self.output_root,
            {
                "room_id": "room-a",
                "agent_id": "agent-1",
                "session_id": "session-1",
                "instruction": "Stream.",
            },
            turn_command_streamer=fake_streamer,
        )

        events = RoomStore(self.output_root).read_events("room-a")
        self.assertEqual(result["turn_status"], "finished")
        self.assertIn("codex_jsonl_command", str(result["diagnostics"]))
        self.assertEqual([event["type"] for event in events][-4:], [
            "message_delta",
            "message_delta",
            "message_final",
            "turn_finished",
        ])

    def test_streaming_command_runner_yields_safe_thinking_delta(self):
        session = {"provider_kind": "codex_live_session", "session_id": "session-1"}
        runner = agent_session_streaming_command_turn_runner(
            session,
            command_streamer=lambda command, prompt, timeout_seconds: [
                {"type": "thinking_delta", "content": "reading room events"},
                {"type": "message_final", "content": "done"},
            ],
        )

        chunks = list(runner({"room_id": "room-a", "current_turn_instruction": "x"}))

        self.assertEqual(chunks[0]["type"], "thinking_delta")
        self.assertEqual(chunks[1]["type"], "message_final")

    def test_streaming_command_error_does_not_append_message_final(self):
        resume_agent_session_payload(
            self.output_root,
            {
                "room_id": "room-a",
                "agent_id": "agent-1",
                "session_id": "session-1",
                "provider_kind": "codex_live_session",
            },
        )

        result = run_agent_session_turn_payload(
            self.output_root,
            {
                "room_id": "room-a",
                "agent_id": "agent-1",
                "session_id": "session-1",
                "instruction": "Fail.",
            },
            turn_command_streamer=lambda command, prompt, timeout_seconds: [
                {"type": "error", "diagnostics": [{"setting": "turn_command", "status": "failed", "message": "provider command exited 7"}]}
            ],
        )

        event_types = [event["type"] for event in RoomStore(self.output_root).read_events("room-a")]
        self.assertEqual(result["turn_status"], "error")
        self.assertIn("error", event_types)
        self.assertNotIn("turn_finished", event_types)
        self.assertNotIn("message_final", event_types)

    def test_streaming_timeout_produces_error_event(self):
        resume_agent_session_payload(
            self.output_root,
            {
                "room_id": "room-a",
                "agent_id": "agent-1",
                "session_id": "session-1",
                "provider_kind": "codex_live_session",
            },
        )

        result = run_agent_session_turn_payload(
            self.output_root,
            {
                "room_id": "room-a",
                "agent_id": "agent-1",
                "session_id": "session-1",
                "instruction": "Timeout.",
            },
            turn_command_streamer=lambda command, prompt, timeout_seconds: [
                {"type": "error", "diagnostics": [{"setting": "turn_command", "status": "timeout", "message": "provider command timed out"}]}
            ],
        )

        self.assertEqual(result["turn_status"], "error")
        self.assertIn("timeout", str(result["diagnostics"]))

    def test_session_settings_flow_to_supported_command_or_diagnostics(self):
        codex = build_agent_session_launch_plan(
            {
                "provider_kind": "codex_live_session",
                "session_id": "codex-session",
                "provider_session_id": "codex-provider-session",
                "model": "gpt-5.5",
                "effort": "high",
                "sandbox": "read-only",
                "permissions": "prompt",
            }
        )
        unsupported = build_agent_session_launch_plan(
            {
                "provider_kind": "unknown_cli",
                "session_id": "session-1",
                "model": "model-x",
                "effort": "low",
                "sandbox": "strict",
                "permissions": "auto",
            }
        )

        self.assertEqual(codex["permission_enforcement"], "codex_readonly")
        self.assertIn("--json", codex["command"])
        self.assertIn("--model", codex["command"])
        self.assertIn("gpt-5.5", codex["command"])
        self.assertIn("--sandbox", codex["command"])
        self.assertIn("read-only", codex["command"])
        self.assertEqual(codex["command"].count("--sandbox"), 1)
        self.assertIn("--ignore-rules", codex["command"])
        self.assertIn('model_reasoning_effort="high"', codex["command"])
        self.assertIn("resume", codex["command"])
        self.assertIn("codex-provider-session", codex["command"])
        self.assertNotIn("--last", codex["command"])
        self.assertEqual(unsupported["permission_enforcement"], "unsupported")
        self.assertTrue(unsupported["diagnostics"])

    def test_missing_provider_session_id_uses_fresh_ephemeral_not_last(self):
        codex = build_agent_session_launch_plan(
            {
                "provider_kind": "codex_live_session",
                "session_id": "room-session",
                "model": "gpt-5.3-codex-spark",
                "sandbox": "read-only",
            }
        )
        forbidden = build_agent_session_launch_plan(
            {
                "provider_kind": "codex_live_session",
                "session_id": "room-session",
                "provider_session_id": "--last",
            }
        )

        self.assertIn("--ephemeral", codex["command"])
        self.assertNotIn("resume", codex["command"])
        self.assertNotIn("--last", codex["command"])
        self.assertNotIn("--last", forbidden["command"])
        self.assertEqual(forbidden["resume_mode"], "last_forbidden")

    def test_two_agents_keep_distinct_provider_session_ids(self):
        first = resume_agent_session_payload(
            self.output_root,
            {
                "room_id": "room-a",
                "agent_id": "agent-1",
                "session_id": "session-1",
                "provider_kind": "codex_live_session",
                "provider_session_id": "provider-a",
            },
        )
        second = resume_agent_session_payload(
            self.output_root,
            {
                "room_id": "room-a",
                "agent_id": "agent-2",
                "session_id": "session-2",
                "provider_kind": "codex_live_session",
                "provider_session_id": "provider-b",
            },
        )

        self.assertEqual(first["session"]["provider_session_id"], "provider-a")
        self.assertEqual(second["session"]["provider_session_id"], "provider-b")
        self.assertNotEqual(first["session"]["provider_session_id"], second["session"]["provider_session_id"])

    def test_codex_jsonl_runner_captures_provider_session_and_final_message(self):
        lines = [
            {"type": "thread.started", "thread_id": "thread-123"},
            {"type": "item.completed", "item": {"type": "error", "message": "Skill descriptions were shortened."}},
            {"type": "item.completed", "item": {"type": "agent_message", "text": "hello"}},
            {"type": "turn.completed", "usage": {"input_tokens": 10, "cached_input_tokens": 2, "output_tokens": 3, "reasoning_output_tokens": 1}},
        ]
        runner = agent_session_codex_jsonl_turn_runner(
            {"provider_kind": "codex_live_session", "session_id": "session-1"},
            command_streamer=lambda command, prompt, timeout_seconds: [
                {"type": "jsonl_line", "content": json_line} for json_line in [__import__("json").dumps(item) for item in lines]
            ],
        )

        chunks = list(runner({"room_id": "room-a", "current_turn_instruction": "x"}))

        self.assertEqual(chunks[0]["type"], "provider_session")
        self.assertEqual(chunks[0]["provider_session_id"], "thread-123")
        self.assertEqual(chunks[1]["type"], "message_delta")
        self.assertNotIn("Skill descriptions", str(chunks))
        self.assertEqual(chunks[-1]["type"], "message_final")
        self.assertIn("usage", str(chunks))

    def test_ephemeral_jsonl_thread_id_is_diagnostic_not_persisted_provider_session(self):
        resume_agent_session_payload(
            self.output_root,
            {
                "room_id": "room-a",
                "agent_id": "agent-1",
                "session_id": "session-1",
                "provider_kind": "codex_live_session",
            },
        )

        result = run_agent_session_turn_payload(
            self.output_root,
            {
                "room_id": "room-a",
                "agent_id": "agent-1",
                "session_id": "session-1",
                "instruction": "Answer.",
            },
            turn_command_streamer=lambda command, prompt, timeout_seconds: [
                {"type": "jsonl_line", "content": __import__("json").dumps({"type": "thread.started", "thread_id": "ephemeral-thread"})},
                {"type": "jsonl_line", "content": __import__("json").dumps({"type": "item.completed", "item": {"type": "agent_message", "text": "ok"}})},
                {"type": "jsonl_line", "content": __import__("json").dumps({"type": "turn.completed"})},
            ],
        )

        session = RoomStore(self.output_root).session("room-a", "session-1")
        self.assertEqual(result["turn_status"], "finished")
        self.assertNotEqual(session.get("provider_session_id"), "ephemeral-thread")
        self.assertIn("ephemeral_thread_id", str(result["diagnostics"]))

    def test_explicit_resume_keeps_provider_session_id(self):
        resume_agent_session_payload(
            self.output_root,
            {
                "room_id": "room-a",
                "agent_id": "agent-1",
                "session_id": "session-1",
                "provider_kind": "codex_live_session",
                "provider_session_id": "resumable-thread",
            },
        )

        result = run_agent_session_turn_payload(
            self.output_root,
            {
                "room_id": "room-a",
                "agent_id": "agent-1",
                "session_id": "session-1",
                "instruction": "Answer.",
            },
            turn_command_streamer=lambda command, prompt, timeout_seconds: [
                {"type": "jsonl_line", "content": __import__("json").dumps({"type": "thread.started", "thread_id": "resumable-thread"})},
                {"type": "jsonl_line", "content": __import__("json").dumps({"type": "item.completed", "item": {"type": "agent_message", "text": "ok"}})},
                {"type": "jsonl_line", "content": __import__("json").dumps({"type": "turn.completed"})},
            ],
        )

        session = RoomStore(self.output_root).session("room-a", "session-1")
        self.assertEqual(result["turn_status"], "finished")
        self.assertEqual(session.get("provider_session_id"), "resumable-thread")

    def test_codex_app_server_runtime_maps_notifications(self):
        class Pipe:
            def __init__(self, lines=None):
                self.lines = list(lines or [])
                self.writes = []

            def write(self, value):
                self.writes.append(value)

            def flush(self):
                return None

            def readline(self):
                if not self.lines:
                    return ""
                return self.lines.pop(0)

        class FakeProcess:
            pid = 123

            def __init__(self):
                self.stdin = Pipe()
                self.stdout = Pipe(
                    [
                        '{"jsonrpc":"2.0","id":1,"result":{}}\n',
                        '{"jsonrpc":"2.0","id":2,"result":{"thread":{"id":"thread-1"},"model":"gpt-5.6-luna"}}\n',
                        '{"jsonrpc":"2.0","id":3,"result":{"turn":{"id":"turn-a"}}}\n',
                        '{"jsonrpc":"2.0","method":"turn/started","params":{"threadId":"thread-1","turn":{"id":"turn-a"}}}\n',
                        '{"jsonrpc":"2.0","method":"model/rerouted","params":{"threadId":"thread-1","turnId":"turn-a","fromModel":"gpt-5.6-luna","toModel":"gpt-5.6-luna-safe","reason":"highRiskCyberActivity"}}\n',
                        '{"jsonrpc":"2.0","method":"item/agentMessage/delta","params":{"threadId":"thread-1","turnId":"turn-a","itemId":"item-a","delta":"hello"}}\n',
                        '{"jsonrpc":"2.0","method":"item/completed","params":{"threadId":"thread-1","turnId":"turn-a","completedAtMs":1,"item":{"id":"item-a","type":"agentMessage","text":"hello"}}}\n',
                        '{"jsonrpc":"2.0","method":"turn/completed","params":{"threadId":"thread-1","turn":{"id":"turn-a"}}}\n',
                    ]
                )

        runtime = CodexAppServerRuntime(process_factory=FakeProcess)
        chunks = list(runtime.send_turn({"provider_session_id": ""}, {"room_id": "room-a", "current_turn_instruction": "x"}))

        self.assertEqual(chunks[0]["type"], "provider_session")
        self.assertEqual(chunks[0]["provider_thread_id"], "thread-1")
        self.assertIn("message_delta", [chunk["type"] for chunk in chunks])
        self.assertIn("message_final", [chunk["type"] for chunk in chunks])
        self.assertEqual(runtime.diagnose({})["observed_model_id"], "gpt-5.6-luna-safe")
        process = runtime.process
        self.assertIsNotNone(process)
        writes = "".join(process.stdin.writes)
        self.assertIn('"method": "initialize"', writes)
        self.assertIn('"method": "initialized"', writes)
        self.assertIn('"method": "thread/start"', writes)
        self.assertIn('"method": "turn/start"', writes)
        self.assertIn('"input": [{"type": "text"', writes)
        self.assertNotIn('"provider_session_id"', writes)

    def test_app_server_provider_session_is_persisted_as_resumable(self):
        resume_agent_session_payload(
            self.output_root,
            {
                "room_id": "room-a",
                "agent_id": "agent-1",
                "session_id": "session-1",
                "provider_kind": "codex_live_session",
            },
        )

        result = run_agent_session_turn_payload(
            self.output_root,
            {
                "room_id": "room-a",
                "agent_id": "agent-1",
                "session_id": "session-1",
                "instruction": "Answer.",
            },
            turn_adapter=lambda session, packet: [
                {"type": "provider_session", "provider_session_id": "app-thread", "provider_thread_id": "app-thread"},
                {"type": "message_delta", "content": "ok"},
                {"type": "message_final", "content": "ok"},
            ],
        )

        session = RoomStore(self.output_root).session("room-a", "session-1")
        self.assertEqual(result["turn_status"], "finished")
        self.assertEqual(session.get("provider_session_id"), "app-thread")
        self.assertEqual(session.get("provider_thread_id"), "app-thread")

    def test_app_server_adapter_input_preserves_turn_timeout(self):
        resume_agent_session_payload(
            self.output_root,
            {
                "room_id": "room-a",
                "agent_id": "agent-1",
                "session_id": "session-1",
                "provider_kind": "codex_live_session",
            },
        )
        packets = []

        result = run_agent_session_turn_payload(
            self.output_root,
            {
                "room_id": "room-a",
                "agent_id": "agent-1",
                "session_id": "session-1",
                "instruction": "Answer.",
                "timeout_seconds": 12,
            },
            turn_adapter=lambda session, packet: packets.append(packet) or [{"type": "message_final", "content": "ok"}],
        )

        self.assertEqual(result["turn_status"], "finished")
        self.assertEqual(packets[0]["timeout_seconds"], 12)

    def test_app_server_completion_signal_is_recorded_in_turn_diagnostics(self):
        resume_agent_session_payload(
            self.output_root,
            {
                "room_id": "room-a",
                "agent_id": "agent-1",
                "session_id": "session-1",
                "provider_kind": "codex_live_session",
            },
        )

        result = run_agent_session_turn_payload(
            self.output_root,
            {
                "room_id": "room-a",
                "agent_id": "agent-1",
                "session_id": "session-1",
                "instruction": "Answer.",
            },
            turn_adapter=lambda session, packet: [
                {
                    "type": "diagnostics",
                    "app_server_completion_signal": "agent_message_final_thread_idle",
                    "app_server_completion_inferred": True,
                },
                {"type": "message_final", "content": "ok"},
            ],
        )

        diagnostics = {item["setting"]: item["status"] for item in result["diagnostics"] if isinstance(item, dict)}
        self.assertEqual(diagnostics.get("app_server_completion_signal"), "agent_message_final_thread_idle")
        self.assertEqual(diagnostics.get("app_server_completion_inferred"), "True")

    def test_app_server_turn_sends_provider_input_not_debug_packet(self):
        class Pipe:
            def __init__(self, lines=None):
                self.lines = list(lines or [])
                self.writes = []

            def write(self, value):
                self.writes.append(value)

            def flush(self):
                return None

            def readline(self):
                if not self.lines:
                    return ""
                return self.lines.pop(0)

        class FakeProcess:
            pid = 125

            def __init__(self):
                self.stdin = Pipe()
                self.stdout = Pipe(
                    [
                        '{"jsonrpc":"2.0","id":1,"result":{}}\n',
                        '{"jsonrpc":"2.0","id":2,"result":{"thread":{"id":"thread-1"}}}\n',
                        '{"jsonrpc":"2.0","id":3,"result":{"turn":{"id":"turn-a"}}}\n',
                        '{"jsonrpc":"2.0","method":"turn/started","params":{"threadId":"thread-1","turn":{"id":"turn-a"}}}\n',
                        '{"jsonrpc":"2.0","method":"item/completed","params":{"threadId":"thread-1","turnId":"turn-a","completedAtMs":1,"item":{"id":"item-a","type":"agentMessage","text":"ok"}}}\n',
                        '{"jsonrpc":"2.0","method":"turn/completed","params":{"threadId":"thread-1","turn":{"id":"turn-a"}}}\n',
                    ]
                )

        runtime = CodexAppServerRuntime(process_factory=FakeProcess)
        list(
            runtime.send_turn(
                {},
                {
                    "provider_input": "[Your turn]\nShort input.\n",
                    "room_id": "room-a",
                    "current_turn_instruction": "Short input.",
                    "diagnostics": [{"setting": "must_not", "status": "leak"}],
                },
            )
        )

        writes = "".join(runtime.process.stdin.writes)
        self.assertIn("Short input.", writes)
        self.assertNotIn("must_not", writes)
        self.assertNotIn("current_turn_instruction", writes)

    def test_app_server_reuses_attached_thread_without_resume(self):
        class Pipe:
            def __init__(self, lines=None):
                self.lines = list(lines or [])
                self.writes = []

            def write(self, value):
                self.writes.append(value)

            def flush(self):
                return None

            def readline(self):
                if not self.lines:
                    return ""
                return self.lines.pop(0)

        class FakeProcess:
            pid = 127

            def __init__(self):
                self.stdin = Pipe()
                self.stdout = Pipe(
                    [
                        '{"jsonrpc":"2.0","id":1,"result":{}}\n',
                        '{"jsonrpc":"2.0","id":2,"result":{"thread":{"id":"thread-1"}}}\n',
                        '{"jsonrpc":"2.0","id":3,"result":{"turn":{"id":"turn-a"}}}\n',
                        '{"jsonrpc":"2.0","method":"turn/completed","params":{"threadId":"thread-1","turn":{"id":"turn-a"}}}\n',
                        '{"jsonrpc":"2.0","id":4,"result":{"turn":{"id":"turn-b"}}}\n',
                        '{"jsonrpc":"2.0","method":"turn/completed","params":{"threadId":"thread-1","turn":{"id":"turn-b"}}}\n',
                    ]
                )

        runtime = CodexAppServerRuntime(process_factory=FakeProcess)
        list(runtime.send_turn({"session_id": "session-1"}, {"provider_input": "first"}))
        list(runtime.send_turn({"session_id": "session-1"}, {"provider_input": "second"}))

        writes = "".join(runtime.process.stdin.writes)
        self.assertEqual(writes.count('"method": "thread/start"'), 1)
        self.assertNotIn('"method": "thread/resume"', writes)
        self.assertEqual(writes.count('"method": "turn/start"'), 2)
        self.assertTrue(runtime.diagnostics["thread_reused"])
        self.assertTrue(runtime.diagnostics["thread_resume_skipped"])

    def test_app_server_pending_notifications_survive_request_response_wait(self):
        class Pipe:
            def __init__(self, lines=None):
                self.lines = list(lines or [])
                self.writes = []

            def write(self, value):
                self.writes.append(value)

            def flush(self):
                return None

            def readline(self):
                if not self.lines:
                    return ""
                return self.lines.pop(0)

        class FakeProcess:
            pid = 128

            def __init__(self):
                self.stdin = Pipe()
                self.stdout = Pipe(
                    [
                        '{"jsonrpc":"2.0","id":1,"result":{}}\n',
                        '{"jsonrpc":"2.0","id":2,"result":{"thread":{"id":"thread-1"}}}\n',
                        '{"jsonrpc":"2.0","method":"turn/started","params":{"threadId":"thread-1","turn":{"id":"turn-a"}}}\n',
                        '{"jsonrpc":"2.0","method":"item/agentMessage/delta","params":{"delta":"early"}}\n',
                        '{"jsonrpc":"2.0","id":3,"result":{"turn":{"id":"turn-a"}}}\n',
                        '{"jsonrpc":"2.0","method":"item/completed","params":{"item":{"type":"agentMessage","text":"early final"}}}\n',
                        '{"jsonrpc":"2.0","method":"turn/completed","params":{"threadId":"thread-1","turn":{"id":"turn-a"}}}\n',
                    ]
                )

        runtime = CodexAppServerRuntime(process_factory=FakeProcess)
        chunks = list(runtime.send_turn({}, {"provider_input": "answer"}))

        self.assertIn("message_delta", [chunk["type"] for chunk in chunks])
        self.assertEqual([chunk.get("content") for chunk in chunks if chunk.get("type") == "message_delta"], ["early"])
        self.assertIn("message_final", [chunk["type"] for chunk in chunks])

    def test_app_server_restart_resumes_existing_provider_thread(self):
        class Pipe:
            def __init__(self, lines=None):
                self.lines = list(lines or [])
                self.writes = []

            def write(self, value):
                self.writes.append(value)

            def flush(self):
                return None

            def readline(self):
                if not self.lines:
                    return ""
                return self.lines.pop(0)

        class FakeProcess:
            pid = 129

            def __init__(self):
                self.stdin = Pipe()
                self.stdout = Pipe(
                    [
                        '{"jsonrpc":"2.0","id":1,"result":{}}\n',
                        '{"jsonrpc":"2.0","id":2,"result":{"thread":{"id":"thread-1"}}}\n',
                        '{"jsonrpc":"2.0","id":3,"result":{"turn":{"id":"turn-a"}}}\n',
                        '{"jsonrpc":"2.0","method":"turn/completed","params":{"threadId":"thread-1","turn":{"id":"turn-a"}}}\n',
                    ]
                )

        runtime = CodexAppServerRuntime(process_factory=FakeProcess)
        list(runtime.send_turn({"provider_session_id": "thread-1"}, {"provider_input": "resume"}))

        writes = "".join(runtime.process.stdin.writes)
        self.assertIn('"method": "thread/resume"', writes)
        self.assertFalse(runtime.diagnostics["thread_reused"])
        self.assertFalse(runtime.diagnostics["thread_resume_skipped"])

    def test_app_server_crash_becomes_room_error_event(self):
        resume_agent_session_payload(
            self.output_root,
            {"room_id": "room-a", "agent_id": "agent-1", "session_id": "session-1", "provider_kind": "codex_live_session"},
        )

        result = run_agent_session_turn_payload(
            self.output_root,
            {"room_id": "room-a", "agent_id": "agent-1", "session_id": "session-1", "instruction": "Answer."},
            turn_adapter=lambda session, packet: (_ for _ in ()).throw(RuntimeError("Codex app-server stopped before completing the request.")),
        )

        events = RoomStore(self.output_root).read_events("room-a")
        self.assertEqual(result["turn_status"], "error")
        self.assertIn("error", [event["type"] for event in events])
        self.assertIn("stopped before completing", str(events[-1].get("diagnostics")))

    def test_runtime_manager_uses_isolated_session_by_default_and_shuts_down_unused(self):
        manager = CodexAppServerRuntimeManager()
        session_a = {"session_id": "a", "provider_kind": "codex_live_session", "model": "gpt-5.3", "sandbox": "read-only"}
        session_b = {"session_id": "b", "provider_kind": "codex_live_session", "model": "gpt-5.3", "sandbox": "read-only"}
        session_c = {"session_id": "c", "provider_kind": "codex_live_session", "model": "gpt-5.3", "sandbox": "workspace-write"}

        runtime_a = manager.runtime_for(session_a, {})
        runtime_b = manager.runtime_for(session_b, {})
        runtime_c = manager.runtime_for(session_c, {})

        self.assertIsNot(runtime_a, runtime_b)
        self.assertIsNot(runtime_a, runtime_c)
        self.assertEqual(runtime_profile_key(session_a, {}), runtime_a.runtime_profile_key)
        manager.detach_session(session_a)
        self.assertIn(runtime_profile_key(session_b, {}), manager._runtimes)
        self.assertNotIn(runtime_profile_key(session_a, {}), manager._runtimes)
        manager.detach_session(session_b)
        self.assertNotIn(runtime_profile_key(session_b, {}), manager._runtimes)

    def test_runtime_manager_shared_profile_policy_reuses_runtime(self):
        manager = CodexAppServerRuntimeManager()
        session_a = {
            "session_id": "a",
            "provider_kind": "codex_live_session",
            "model": "gpt-5.3",
            "sandbox": "read-only",
            "runtime_sharing_policy": "shared_profile",
        }
        session_b = {**session_a, "session_id": "b"}

        runtime_a = manager.runtime_for(session_a, {})
        runtime_b = manager.runtime_for(session_b, {})

        self.assertIs(runtime_a, runtime_b)
        self.assertIn("runtime_sharing_policy=shared_profile", runtime_a.runtime_profile_key)

    def test_same_session_reuses_isolated_runtime(self):
        manager = CodexAppServerRuntimeManager()
        session = {"session_id": "a", "provider_kind": "codex_live_session", "model": "gpt-5.3", "sandbox": "read-only"}

        runtime_a = manager.runtime_for(session, {})
        runtime_b = manager.runtime_for(session, {})

        self.assertIs(runtime_a, runtime_b)
        self.assertIn("runtime_sharing_policy=isolated_session", runtime_a.runtime_profile_key)
        self.assertIn("session_id=a", runtime_a.runtime_profile_key)

    def test_runtime_profile_key_includes_workspace_permissions_and_codex_home(self):
        base = {
            "session_id": "a",
            "provider_kind": "codex_live_session",
            "model": "gpt-5.3-codex-spark",
            "effort": "medium",
            "sandbox": "read-only",
            "permissions": "never",
            "workspace": "/tmp/work-a",
            "codex_home": "/tmp/codex-home-a",
        }

        self.assertNotEqual(runtime_profile_key(base, {}), runtime_profile_key({**base, "workspace": "/tmp/work-b"}, {}))
        self.assertNotEqual(runtime_profile_key(base, {}), runtime_profile_key({**base, "permissions": "on-request"}, {}))
        self.assertNotEqual(runtime_profile_key(base, {}), runtime_profile_key({**base, "codex_home": "/tmp/codex-home-b"}, {}))
        self.assertNotEqual(runtime_profile_key(base, {}), runtime_profile_key({**base, "session_id": "b"}, {}))
        self.assertEqual(
            runtime_profile_key({**base, "runtime_sharing_policy": "shared_profile"}, {}),
            runtime_profile_key({**base, "session_id": "b", "runtime_sharing_policy": "shared_profile"}, {}),
        )

    def test_resume_persists_profile_isolation_fields_on_session(self):
        result = resume_agent_session_payload(
            self.output_root,
            {
                "room_id": "room-a",
                "agent_id": "spark-a",
                "session_id": "spark-a",
                "provider_kind": "codex_live_session",
                "model": "gpt-5.3-codex-spark",
                "effort": "medium",
                "sandbox": "read-only",
                "permissions": "never",
                "workspace": "/tmp/work-a",
                "codex_home": "/tmp/codex-home-a",
                "runtime_sharing_policy": "shared_profile_serial",
            },
        )

        session = result["session"]
        key = runtime_profile_key(session, {})

        self.assertEqual(session["workspace"], "/tmp/work-a")
        self.assertEqual(session["codex_home"], "/tmp/codex-home-a")
        self.assertEqual(session["runtime_sharing_policy"], "shared_profile_serial")
        self.assertIn("workspace=/tmp/work-a", key)
        self.assertIn("codex_home=/tmp/codex-home-a", key)
        self.assertIn("runtime_sharing_policy=shared_profile_serial", key)

    def test_app_server_runtime_command_uses_session_settings(self):
        manager = CodexAppServerRuntimeManager()
        session = {
            "session_id": "spark-a",
            "provider_kind": "codex_live_session",
            "model": "gpt-5.3-codex-spark",
            "effort": "low",
            "sandbox": "read-only",
            "permissions": "never",
        }

        runtime = manager.runtime_for(session, {"settings": {"effort": "medium"}})

        self.assertEqual(
            runtime.command,
            [
                "codex",
                "app-server",
                "-c",
                'model="gpt-5.3-codex-spark"',
                "-c",
                'model_reasoning_effort="medium"',
                "-c",
                'sandbox_mode="read-only"',
                "-c",
                'approval_policy="never"',
                "--stdio",
            ],
        )
        self.assertEqual(runtime.diagnostics["model"], "gpt-5.3-codex-spark")
        self.assertEqual(runtime.diagnostics["effort"], "medium")
        self.assertEqual(runtime.diagnostics["sandbox"], "read-only")
        self.assertEqual(runtime.diagnostics["permissions"], "never")

    def test_codex_app_server_drains_stderr_and_reports_bounded_tail(self):
        class Pipe:
            def __init__(self, lines=None):
                self.lines = list(lines or [])
                self.writes = []
                self.closed = False

            def write(self, value):
                self.writes.append(value)

            def flush(self):
                return None

            def readline(self):
                if not self.lines:
                    return ""
                return self.lines.pop(0)

            def close(self):
                self.closed = True

        warning_lines = [f"warning {index} " + ("x" * 80) + "\n" for index in range(1000)]

        class FakeProcess:
            pid = 130

            def __init__(self):
                self.stdin = Pipe()
                self.stdout = Pipe(
                    [
                        '{"jsonrpc":"2.0","id":1,"result":{}}\n',
                        '{"jsonrpc":"2.0","id":2,"result":{"thread":{"id":"thread-1"}}}\n',
                        '{"jsonrpc":"2.0","id":3,"result":{"turn":{"id":"turn-a"}}}\n',
                        '{"jsonrpc":"2.0","method":"turn/completed","params":{"threadId":"thread-1","turn":{"id":"turn-a"}}}\n',
                    ]
                )
                self.stderr = Pipe(warning_lines)
                self.terminated = False
                self.waited = False

            def terminate(self):
                self.terminated = True

            def wait(self, timeout=None):
                self.waited = True
                return 0

        runtime = CodexAppServerRuntime(process_factory=FakeProcess)
        chunks = list(runtime.send_turn({}, {"provider_input": "answer"}))
        for _ in range(100):
            if runtime.diagnose({}).get("stderr_line_count") == len(warning_lines):
                break
            time.sleep(0.01)

        diagnostics = runtime.diagnose({})
        self.assertIn("provider_session", [chunk["type"] for chunk in chunks])
        self.assertEqual(diagnostics.get("stderr_drained"), True)
        self.assertEqual(diagnostics.get("stderr_line_count"), len(warning_lines))
        self.assertGreater(diagnostics.get("stderr_byte_count") or 0, 64000)
        self.assertEqual(diagnostics.get("stderr_tail_truncated"), True)
        self.assertEqual(diagnostics.get("stderr_warning_count"), len(warning_lines))
        self.assertEqual(len(diagnostics.get("stderr_tail", "").splitlines()), 50)
        self.assertTrue(diagnostics.get("stderr_tail", "").splitlines()[0].startswith("warning 950"))

        process = runtime.process
        runtime.detach({})

        self.assertTrue(process.terminated)
        self.assertTrue(process.waited)
        self.assertTrue(process.stdin.closed)
        self.assertTrue(process.stdout.closed)
        self.assertTrue(process.stderr.closed)
        self.assertIsNone(runtime.process)
        self.assertEqual(runtime.diagnose({})["stderr_line_count"], len(warning_lines))
        self.assertIsNone(runtime._stderr_thread)

        runtime.detach({})
        self.assertIsNone(runtime.process)

    def test_stderr_tail_truncates_by_char_and_counts_non_warning_lines(self):
        runtime = CodexAppServerRuntime()

        for index in range(40):
            prefix = "warning" if index % 2 == 0 else "info"
            runtime._record_stderr_line(f"{prefix} {index} " + ("x" * 900) + "\n")

        diagnostics = runtime.diagnose({})

        self.assertEqual(diagnostics["stderr_line_count"], 40)
        self.assertGreater(diagnostics["stderr_byte_count"], 0)
        self.assertEqual(diagnostics["stderr_warning_count"], 20)
        self.assertLessEqual(len(diagnostics["stderr_tail"]), 16000)
        self.assertLess(len(diagnostics["stderr_tail"].splitlines()), 40)
        self.assertTrue(diagnostics["stderr_tail_truncated"])

    def test_app_server_turn_diagnostics_do_not_reuse_previous_completion_latency(self):
        class Pipe:
            def __init__(self, lines=None):
                self.lines = list(lines or [])
                self.writes = []

            def write(self, value):
                self.writes.append(value)

            def flush(self):
                return None

            def readline(self):
                if not self.lines:
                    return ""
                return self.lines.pop(0)

            def close(self):
                return None

        class FakeProcess:
            pid = 131

            def __init__(self):
                self.stdin = Pipe()
                self.stdout = Pipe(
                    [
                        json.dumps({"jsonrpc": "2.0", "id": 1, "result": {}}) + "\n",
                        json.dumps({"jsonrpc": "2.0", "id": 2, "result": {"thread": {"id": "thread-1"}}}) + "\n",
                        json.dumps({"jsonrpc": "2.0", "id": 3, "result": {"turn": {"id": "turn-a"}}}) + "\n",
                        json.dumps({"jsonrpc": "2.0", "method": "turn/completed", "params": {"threadId": "thread-1"}}) + "\n",
                        json.dumps({"jsonrpc": "2.0", "id": 4, "result": {"turn": {"id": "turn-b"}}}) + "\n",
                    ]
                )
                self.stderr = Pipe()

            def terminate(self):
                return None

            def wait(self, timeout=None):
                return 0

        runtime = CodexAppServerRuntime(process_factory=FakeProcess)

        first = list(runtime.send_turn({"session_id": "session-1"}, {"provider_input": "first"}))
        second = list(runtime.send_turn({"session_id": "session-1"}, {"provider_input": "second"}))
        error_chunk = next(chunk for chunk in second if chunk["type"] == "error")
        error_settings = {
            item.get("setting")
            for item in error_chunk["diagnostics"]
            if isinstance(item, dict) and item.get("status") not in ("", None)
        }

        self.assertIn("diagnostics", [chunk["type"] for chunk in first])
        self.assertNotIn("turn_completed_ms", error_settings)
        self.assertNotIn("time_to_first_agent_delta_ms", error_settings)
        self.assertIn("app_server", error_settings)

    def test_app_server_buffers_unmatched_thread_and_turn_notifications(self):
        class Pipe:
            def __init__(self, lines=None):
                self.lines = list(lines or [])
                self.writes = []

            def write(self, value):
                self.writes.append(value)

            def flush(self):
                return None

            def readline(self):
                if not self.lines:
                    return ""
                return self.lines.pop(0)

        class FakeProcess:
            pid = 132

            def __init__(self):
                self.stdin = Pipe()
                self.stdout = Pipe(
                    [
                        json.dumps({"jsonrpc": "2.0", "id": 1, "result": {}}) + "\n",
                        json.dumps({"jsonrpc": "2.0", "id": 2, "result": {"thread": {"id": "thread-current"}}}) + "\n",
                        json.dumps({"jsonrpc": "2.0", "id": 3, "result": {"turn": {"id": "turn-current"}}}) + "\n",
                        json.dumps({"jsonrpc": "2.0", "method": "turn/completed", "params": {"threadId": "thread-other", "turnId": "turn-other"}}) + "\n",
                        json.dumps({"jsonrpc": "2.0", "method": "agent_message/delta", "params": {"threadId": "thread-current", "turnId": "turn-old", "delta": "old"}}) + "\n",
                        json.dumps({"jsonrpc": "2.0", "method": "agent_message/delta", "params": {"threadId": "thread-current", "turnId": "turn-current", "delta": "current"}}) + "\n",
                        json.dumps({"jsonrpc": "2.0", "method": "turn/completed", "params": {"threadId": "thread-current", "turnId": "turn-current"}}) + "\n",
                    ]
                )
                self.stderr = Pipe()

        runtime = CodexAppServerRuntime(process_factory=FakeProcess)

        chunks = list(runtime.send_turn({}, {"provider_input": "answer"}))
        diagnostics = runtime.diagnose({})

        self.assertIn({"type": "message_delta", "content": "current"}, chunks)
        self.assertNotIn({"type": "message_delta", "content": "old"}, chunks)
        self.assertEqual(diagnostics.get("unmatched_notification_count"), 2)
        self.assertEqual(diagnostics.get("pending_notification_count"), 2)
        self.assertIn("turn/completed", diagnostics.get("app_server_method_tail", ""))
        self.assertEqual(diagnostics.get("provider_turn_id"), "turn-current")
        self.assertEqual(diagnostics.get("provider_thread_id"), "thread-current")

    def test_app_server_turns_on_same_runtime_are_serialized(self):
        class SerialRuntime(CodexAppServerRuntime):
            def __init__(self):
                super().__init__()
                self.active_turns = 0
                self.max_active_turns = 0
                self.starts: list[str] = []

            def attach(self, ids):
                return {"provider_thread_id": str(ids.get("session_id") or "thread")}

            def _send_request(self, method, params, *, timeout_deadline=None):
                if method != "turn/start":
                    return {"result": {}}
                self.active_turns += 1
                self.max_active_turns = max(self.max_active_turns, self.active_turns)
                self.starts.append(str(params.get("threadId")))
                time.sleep(0.05)
                return {"result": {"turn": {"id": f"turn-{params.get('threadId')}"}}}

            def _read_messages_until_turn_done(self, *, thread_id="", turn_id="", timeout_deadline=None):
                try:
                    time.sleep(0.05)
                    yield {"method": "turn/completed", "params": {"threadId": self.starts[-1], "turnId": f"turn-{self.starts[-1]}"}}
                finally:
                    self.active_turns -= 1

        runtime = SerialRuntime()
        results: list[list[dict[str, object]]] = []
        threads = [
            threading.Thread(target=lambda sid=sid: results.append(list(runtime.send_turn({"session_id": sid}, {"provider_input": sid}))))
            for sid in ("a", "b")
        ]

        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        self.assertEqual(runtime.max_active_turns, 1)
        self.assertEqual(len(results), 2)

    def test_app_server_timeout_reports_method_tail_and_status(self):
        class TimeoutRuntime(CodexAppServerRuntime):
            def attach(self, ids):
                return {"provider_thread_id": "thread-1"}

            def _send_request(self, method, params, *, timeout_deadline=None):
                return {"result": {"turn": {"id": "turn-1"}}}

            def _read_json_line(self, *, timeout_deadline=None):
                if not hasattr(self, "_sent_status"):
                    self._sent_status = True
                    return {
                        "method": "thread/status/changed",
                        "params": {
                            "threadId": "thread-1",
                            "turnId": "turn-1",
                            "thread": {"status": "busy"},
                            "turn": {"status": "running"},
                        },
                    }
                raise TimeoutError("Codex app-server timed out before completing the request.")

        runtime = TimeoutRuntime()

        chunks = list(runtime.send_turn({}, {"provider_input": "answer"}))
        diagnostics = chunks[-1]["diagnostics"]
        settings = {item["setting"]: item["status"] for item in diagnostics if isinstance(item, dict)}

        self.assertEqual(chunks[-1]["type"], "error")
        self.assertEqual(settings["app_server_last_method"], "thread/status/changed")
        self.assertEqual(settings["app_server_last_thread_status"], "busy")
        self.assertEqual(settings["app_server_last_turn_status"], "running")
        self.assertIn("thread/status/changed", settings["app_server_method_tail"])
        self.assertEqual(settings["provider_thread_id"], "thread-1")
        self.assertEqual(settings["provider_turn_id"], "turn-1")

    def test_app_server_infers_turn_completion_from_final_agent_message_and_idle_thread(self):
        class IdleAfterFinalRuntime(CodexAppServerRuntime):
            def __init__(self):
                super().__init__()
                self.messages = [
                    {
                        "method": "turn/started",
                        "params": {"threadId": "thread-1", "turnId": "turn-1", "turn": {"status": "inProgress"}},
                    },
                    {
                        "method": "item/agentMessage/delta",
                        "params": {"threadId": "thread-1", "turnId": "turn-1", "delta": "ok"},
                    },
                    {
                        "method": "item/completed",
                        "params": {
                            "threadId": "thread-1",
                            "turnId": "turn-1",
                            "item": {"type": "agentMessage", "text": "ok"},
                        },
                    },
                    {
                        "method": "thread/status/changed",
                        "params": {"threadId": "thread-1", "thread": {"status": {"type": "idle"}}},
                    },
                ]

            def attach(self, ids):
                return {"provider_thread_id": "thread-1"}

            def _send_request(self, method, params, *, timeout_deadline=None):
                return {"result": {"turn": {"id": "turn-1"}}}

            def _read_json_line(self, *, timeout_deadline=None):
                if self.messages:
                    return self.messages.pop(0)
                raise TimeoutError("quiet after final agent message and idle thread")

        runtime = IdleAfterFinalRuntime()

        chunks = list(runtime.send_turn({}, {"provider_input": "answer"}))
        diagnostics = runtime.diagnose({})

        self.assertIn({"type": "message_final", "content": "ok"}, chunks)
        self.assertNotIn("error", [chunk.get("type") for chunk in chunks])
        self.assertEqual(diagnostics.get("app_server_completion_signal"), "agent_message_final_thread_idle")
        self.assertTrue(diagnostics.get("app_server_completion_inferred"))
        self.assertEqual(diagnostics.get("turn_completed_ms"), 0)

    def test_diagnostics_snapshot_is_consistent_while_stderr_updates(self):
        runtime = CodexAppServerRuntime()
        errors: list[Exception] = []

        def write_stderr() -> None:
            try:
                for index in range(250):
                    runtime._record_stderr_line(f"warn concurrent {index}\n")
            except Exception as error:  # pragma: no cover - assertion records thread failures
                errors.append(error)

        def read_diagnostics() -> None:
            try:
                previous_lines = 0
                for _ in range(250):
                    snapshot = runtime.diagnose({})
                    line_count = int(snapshot.get("stderr_line_count") or 0)
                    byte_count = int(snapshot.get("stderr_byte_count") or 0)
                    self.assertGreaterEqual(line_count, previous_lines)
                    self.assertGreaterEqual(byte_count, line_count)
                    previous_lines = line_count
            except Exception as error:  # pragma: no cover - assertion records thread failures
                errors.append(error)

        threads = [threading.Thread(target=write_stderr), threading.Thread(target=read_diagnostics)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        self.assertEqual(errors, [])
        snapshot = runtime.diagnose({})
        self.assertEqual(snapshot["stderr_line_count"], 250)
        self.assertEqual(snapshot["stderr_warning_count"], 250)

    def test_app_server_passes_runtime_settings_to_thread_and_turn_start(self):
        class Pipe:
            def __init__(self, lines=None):
                self.lines = list(lines or [])
                self.writes = []

            def write(self, value):
                self.writes.append(value)

            def flush(self):
                return None

            def readline(self):
                if not self.lines:
                    return ""
                return self.lines.pop(0)

        class FakeProcess:
            pid = 131

            def __init__(self):
                self.stdin = Pipe()
                self.stdout = Pipe(
                    [
                        '{"jsonrpc":"2.0","id":1,"result":{}}\n',
                        '{"jsonrpc":"2.0","id":2,"result":{"thread":{"id":"thread-1"}}}\n',
                        '{"jsonrpc":"2.0","id":3,"result":{"turn":{"id":"turn-a"}}}\n',
                        '{"jsonrpc":"2.0","method":"turn/completed","params":{"threadId":"thread-1","turn":{"id":"turn-a"}}}\n',
                    ]
                )
                self.stderr = Pipe()

        runtime = CodexAppServerRuntime(
            process_factory=FakeProcess,
            runtime_profile_key="profile",
            profile_settings={
                "provider_kind": "codex_live_session",
                "workspace": "/tmp/agentsassemble-workspace",
                "model": "gpt-5.3-codex-spark",
                "effort": "medium",
                "sandbox": "read-only",
                "permissions": "never",
                "codex_home": "",
            },
        )
        list(runtime.send_turn({}, {"provider_input": "answer"}))

        requests = [__import__("json").loads(line) for line in runtime.process.stdin.writes]
        thread_start = next(request for request in requests if request.get("method") == "thread/start")
        turn_start = next(request for request in requests if request.get("method") == "turn/start")

        self.assertEqual(thread_start["params"].get("cwd"), "/tmp/agentsassemble-workspace")
        self.assertEqual(thread_start["params"].get("model"), "gpt-5.3-codex-spark")
        self.assertEqual(thread_start["params"].get("approvalPolicy"), "never")
        self.assertEqual(thread_start["params"].get("sandbox"), "read-only")
        self.assertEqual(turn_start["params"].get("cwd"), "/tmp/agentsassemble-workspace")
        self.assertEqual(turn_start["params"].get("model"), "gpt-5.3-codex-spark")
        self.assertEqual(turn_start["params"].get("effort"), "medium")
        self.assertEqual(turn_start["params"].get("approvalPolicy"), "never")
        self.assertEqual(turn_start["params"].get("sandboxPolicy"), {"type": "readOnly", "networkAccess": False})

    def test_non_codex_provider_adapters_are_explicitly_unsupported(self):
        for adapter in (GrokAgentSessionAdapter(), ClaudeAgentSessionAdapter(), AgyAgentSessionAdapter()):
            handle = adapter.attach({"session_id": "session-1"})
            chunks = list(adapter.send_turn(handle, {"provider_input": "hello"}))
            self.assertFalse(handle["resumable"])
            self.assertEqual(chunks[0]["type"], "error")
            self.assertNotIn("provider_session_id", handle)
        self.assertIn("not wired into Agent Session runtime yet", str(GrokAgentSessionAdapter().diagnose({})))
        self.assertIn("Agent SDK only", str(ClaudeAgentSessionAdapter().diagnose({})))
        self.assertIn("claude -p", str(ClaudeAgentSessionAdapter().diagnose({})))
        self.assertNotIn("command", ClaudeAgentSessionAdapter().diagnose({}))
        self.assertIn("unavailable until protocol verified", str(AgyAgentSessionAdapter().diagnose({})))

    def test_app_server_crash_marks_session_error_and_next_turn_restarts_with_recovery_memory(self):
        class Pipe:
            def __init__(self, lines=None):
                self.lines = list(lines or [])
                self.writes = []
                self.closed = False

            def write(self, value):
                self.writes.append(value)

            def flush(self):
                return None

            def readline(self):
                if not self.lines:
                    return ""
                return self.lines.pop(0)

            def close(self):
                self.closed = True

        class FakeProcess:
            def __init__(self, pid, stdout_lines, stderr_lines=None):
                self.pid = pid
                self.stdin = Pipe()
                self.stdout = Pipe(stdout_lines)
                self.stderr = Pipe(stderr_lines or [])
                self.terminated = False

            def terminate(self):
                self.terminated = True

            def wait(self, timeout=None):
                return 0

            def poll(self):
                return None

        processes = []

        def process_factory():
            if not processes:
                process = FakeProcess(
                    201,
                    [
                        json.dumps({"jsonrpc": "2.0", "id": 1, "result": {}}) + "\n",
                        json.dumps({"jsonrpc": "2.0", "id": 2, "result": {"thread": {"id": "thread-1"}}}) + "\n",
                        json.dumps({"jsonrpc": "2.0", "id": 3, "result": {"turn": {"id": "turn-a"}}}) + "\n",
                    ],
                    ["warning crash tail\n"],
                )
            else:
                process = FakeProcess(
                    202,
                    [
                        json.dumps({"jsonrpc": "2.0", "id": 4, "result": {}}) + "\n",
                        json.dumps({"jsonrpc": "2.0", "id": 5, "result": {"thread": {"id": "thread-1"}}}) + "\n",
                        json.dumps({"jsonrpc": "2.0", "id": 6, "result": {"turn": {"id": "turn-b"}}}) + "\n",
                        json.dumps({"jsonrpc": "2.0", "method": "item/agentMessage/delta", "params": {"delta": "recovered"}}) + "\n",
                        json.dumps({"jsonrpc": "2.0", "method": "item/completed", "params": {"item": {"type": "agentMessage", "text": "recovered"}}}) + "\n",
                        json.dumps({"jsonrpc": "2.0", "method": "turn/completed", "params": {"threadId": "thread-1"}}) + "\n",
                    ],
                )
            processes.append(process)
            return process

        runtime = CodexAppServerRuntime(process_factory=process_factory)
        resume_agent_session_payload(
            self.output_root,
            {"room_id": "room-a", "agent_id": "agent-1", "session_id": "session-1", "provider_kind": "codex_live_session"},
        )
        store = RoomStore(self.output_root)
        store.upsert_session(
            "room-a",
            {
                **store.session("room-a", "session-1"),
                "room_memory": {"summary": "Recovered room summary.", "decisions": ["Keep stderr bounded"], "up_to_event_id": "evt-1"},
            },
        )

        first = run_agent_session_turn_payload(
            self.output_root,
            {"room_id": "room-a", "agent_id": "agent-1", "session_id": "session-1", "instruction": "First."},
            turn_adapter=lambda session, packet: runtime.send_turn(session, packet),
        )
        failed_session = RoomStore(self.output_root).session("room-a", "session-1")
        captured_packets = []
        second = run_agent_session_turn_payload(
            self.output_root,
            {"room_id": "room-a", "agent_id": "agent-1", "session_id": "session-1", "instruction": "Recover."},
            turn_adapter=lambda session, packet: captured_packets.append(packet) or runtime.send_turn(session, packet),
        )

        self.assertEqual(first["turn_status"], "error")
        self.assertEqual(failed_session["status"], "error")
        self.assertTrue(failed_session["recovery_required"])
        self.assertIn("warning crash tail", str(first["diagnostics"]))
        self.assertEqual(second["turn_status"], "finished")
        self.assertEqual(len(processes), 2)
        self.assertIn('"method": "thread/resume"', "".join(processes[1].stdin.writes))
        self.assertEqual(captured_packets[0]["input_mode"], "recovery")
        self.assertIn("Recovered room summary.", captured_packets[0]["provider_input"])
        self.assertIn("Keep stderr bounded", captured_packets[0]["provider_input"])
        self.assertNotIn("provider_session_id", captured_packets[0]["provider_input"])
        self.assertNotIn("diagnostics", captured_packets[0]["provider_input"])

    def test_app_server_resume_failure_marks_recovery_required(self):
        class Pipe:
            def __init__(self, lines=None):
                self.lines = list(lines or [])
                self.writes = []

            def write(self, value):
                self.writes.append(value)

            def flush(self):
                return None

            def readline(self):
                if not self.lines:
                    return ""
                return self.lines.pop(0)

        class FakeProcess:
            pid = 203

            def __init__(self):
                self.stdin = Pipe()
                self.stdout = Pipe(
                    [
                        json.dumps({"jsonrpc": "2.0", "id": 1, "result": {}}) + "\n",
                        json.dumps({"jsonrpc": "2.0", "id": 2, "error": {"message": "thread missing"}}) + "\n",
                    ]
                )
                self.stderr = Pipe(["warning resume failed\n"])

            def wait(self, timeout=None):
                return 0

            def terminate(self):
                return None

        runtime = CodexAppServerRuntime(process_factory=FakeProcess)
        resume_agent_session_payload(
            self.output_root,
            {
                "room_id": "room-a",
                "agent_id": "agent-1",
                "session_id": "session-1",
                "provider_kind": "codex_live_session",
                "provider_session_id": "missing-thread",
            },
        )

        result = run_agent_session_turn_payload(
            self.output_root,
            {"room_id": "room-a", "agent_id": "agent-1", "session_id": "session-1", "instruction": "Resume."},
            turn_adapter=lambda session, packet: runtime.send_turn(session, packet),
        )

        session = RoomStore(self.output_root).session("room-a", "session-1")
        self.assertEqual(result["turn_status"], "error")
        self.assertEqual(session["status"], "error")
        self.assertTrue(session["recovery_required"])
        self.assertIn("resume", str(result["diagnostics"]).lower())
        self.assertIn("warning resume failed", str(result["diagnostics"]))

    def test_smoke_result_classifies_provider_unsupported_separately(self):
        from agentsassemble import agent_sessions

        diagnostics = [
            {
                "setting": "app_server",
                "status": "failed",
                "message": "The 'gpt-5.3-codex' model is not supported when using Codex with a ChatGPT account.",
            }
        ]

        self.assertEqual(agent_sessions._codex_app_server_smoke_turn_failure_kind(diagnostics), "provider_unsupported")
        self.assertFalse(agent_sessions._diagnostics_indicate_timeout(diagnostics))

    def test_smoke_summary_counts_provider_unsupported_and_context_errors(self):
        from agentsassemble import agent_sessions

        metrics = agent_sessions._empty_codex_app_server_smoke_metrics()
        metrics["turn_status"] = ["finished", "provider_unsupported", "error"]
        metrics["failure_kind"] = ["provider_unsupported", "context_error"]

        summary = agent_sessions._finalize_codex_app_server_smoke_metrics(metrics, total_turns=3)

        self.assertEqual(summary["finished_turns"], 1)
        self.assertEqual(summary["provider_unsupported_count"], 1)
        self.assertEqual(summary["context_error_count"], 1)
        self.assertEqual(summary["timeout_count"], 0)

    def test_codex_app_server_approval_and_context_error_surface(self):
        class Pipe:
            def __init__(self, lines=None):
                self.lines = list(lines or [])
                self.writes = []

            def write(self, value):
                self.writes.append(value)

            def flush(self):
                return None

            def readline(self):
                if not self.lines:
                    return ""
                return self.lines.pop(0)

        class FakeProcess:
            pid = 124

            def __init__(self):
                self.stdin = Pipe()
                self.stdout = Pipe(
                    [
                        '{"jsonrpc":"2.0","id":1,"result":{}}\n',
                        '{"jsonrpc":"2.0","id":2,"result":{"thread":{"id":"thread-2"}}}\n',
                        '{"jsonrpc":"2.0","id":3,"result":{"turn":{"id":"turn-a"}}}\n',
                        '{"jsonrpc":"2.0","method":"command_execution/request_approval","params":{"threadId":"thread-2","turnId":"turn-a"}}\n',
                        '{"jsonrpc":"2.0","method":"turn/error","params":{"message":"contextWindowExceeded"}}\n',
                    ]
                )

        runtime = CodexAppServerRuntime(process_factory=FakeProcess)
        chunks = list(runtime.send_turn({}, {"room_id": "room-a", "current_turn_instruction": "x"}))

        self.assertIn("approval_requested", [chunk["type"] for chunk in chunks])
        self.assertEqual(chunks[-1]["type"], "error")

    def test_codex_app_server_surfaces_safe_reasoning_and_tool_progress(self):
        class Pipe:
            def __init__(self, lines=None):
                self.lines = list(lines or [])
                self.writes = []

            def write(self, value):
                self.writes.append(value)

            def flush(self):
                return None

            def readline(self):
                if not self.lines:
                    return ""
                return self.lines.pop(0)

        class FakeProcess:
            pid = 126

            def __init__(self):
                self.stdin = Pipe()
                self.stdout = Pipe(
                    [
                        '{"jsonrpc":"2.0","id":1,"result":{}}\n',
                        '{"jsonrpc":"2.0","id":2,"result":{"thread":{"id":"thread-3"}}}\n',
                        '{"jsonrpc":"2.0","id":3,"result":{"turn":{"id":"turn-a"}}}\n',
                        '{"jsonrpc":"2.0","method":"turn/started","params":{"threadId":"thread-3","turn":{"id":"turn-a"}}}\n',
                        '{"jsonrpc":"2.0","method":"item/started","params":{"threadId":"thread-3","turnId":"turn-a","startedAtMs":1,"item":{"id":"r","type":"reasoning"}}}\n',
                        '{"jsonrpc":"2.0","method":"item/started","params":{"threadId":"thread-3","turnId":"turn-a","startedAtMs":2,"item":{"id":"c","type":"commandExecution","command":"pwd"}}}\n',
                        '{"jsonrpc":"2.0","method":"item/completed","params":{"threadId":"thread-3","turnId":"turn-a","completedAtMs":3,"item":{"id":"c","type":"commandExecution","command":"pwd"}}}\n',
                        '{"jsonrpc":"2.0","method":"item/completed","params":{"threadId":"thread-3","turnId":"turn-a","completedAtMs":4,"item":{"id":"a","type":"agentMessage","text":"ok"}}}\n',
                        '{"jsonrpc":"2.0","method":"turn/completed","params":{"threadId":"thread-3","turn":{"id":"turn-a"}}}\n',
                    ]
                )

        runtime = CodexAppServerRuntime(process_factory=FakeProcess)
        chunks = list(runtime.send_turn({}, {"provider_input": "[Your turn]\nAnswer.\n"}))
        thinking = [chunk.get("content") for chunk in chunks if chunk.get("type") == "thinking_delta"]

        self.assertIn("Thinking.", thinking)
        self.assertIn("Using tool: pwd", thinking)
        self.assertIn("Tool finished: pwd", thinking)
        self.assertIn("message_final", [chunk["type"] for chunk in chunks])

    def test_codex_jsonl_error_and_malformed_line_are_diagnostics(self):
        runner = agent_session_codex_jsonl_turn_runner(
            {"provider_kind": "codex_live_session", "session_id": "session-1"},
            command_streamer=lambda command, prompt, timeout_seconds: [
                {"type": "jsonl_line", "content": "{not-json"},
                {"type": "jsonl_line", "content": "{\"type\":\"turn.failed\",\"message\":\"bad\"}"},
            ],
        )

        chunks = list(runner({"room_id": "room-a", "current_turn_instruction": "x"}))

        self.assertEqual(chunks[0]["type"], "diagnostics")
        self.assertEqual(chunks[1]["type"], "error")
        self.assertNotIn("message_final", [chunk["type"] for chunk in chunks])

    def test_resume_defaults_to_state_only_and_returns_launch_diagnostics(self):
        result = resume_agent_session_payload(
            self.output_root,
            {
                "room_id": "room-a",
                "agent_id": "agent-1",
                "session_id": "session-1",
                "provider_kind": "codex_live_session",
                "model": "gpt-5.5",
                "effort": "high",
                "sandbox": "read-only",
            },
        )

        self.assertEqual(result["state_status"], "resumed")
        self.assertEqual(result["process_status"], "not_started")
        self.assertEqual(result["launch_plan"]["provider_kind"], "codex_live_session")
        self.assertIn("codex", result["launch_plan"]["command"])
        self.assertTrue(any(item["status"] == "not_started" for item in result["diagnostics"]))

    def test_resume_can_dry_run_or_fake_launch_without_running_real_provider(self):
        dry_run = resume_agent_session_payload(
            self.output_root,
            {
                "room_id": "room-a",
                "agent_id": "agent-1",
                "session_id": "session-1",
                "provider_kind": "codex_live_session",
                "model": "gpt-5.5",
                "effort": "high",
                "sandbox": "read-only",
                "start": True,
                "dry_run": True,
            },
        )
        calls: list[list[str]] = []

        launched = resume_agent_session_payload(
            self.output_root,
            {
                "room_id": "room-a",
                "agent_id": "agent-1",
                "session_id": "session-1",
                "provider_kind": "codex_live_session",
                "start": True,
            },
            command_runner=lambda command: calls.append(command) or {"returncode": 0},
        )

        self.assertEqual(dry_run["process_status"], "not_started")
        self.assertEqual(dry_run["launch_plan"]["command"][:2], ["codex", "exec"])
        self.assertIn("--ephemeral", dry_run["launch_plan"]["command"])
        self.assertEqual(launched["process_status"], "resumed")
        self.assertEqual(calls[0][:2], ["codex", "exec"])
        self.assertNotIn("--last", calls[0])

    def test_process_service_fake_runner_records_resumed(self):
        calls: list[list[str]] = []
        result = resume_agent_session_payload(
            self.output_root,
            {
                "room_id": "room-a",
                "agent_id": "agent-1",
                "session_id": "session-1",
                "provider_kind": "codex_live_session",
                "start": True,
            },
            process_service=AgentSessionProcessService(
                command_runner=lambda command: calls.append(command) or {"returncode": 0}
            ),
        )

        self.assertEqual(result["process_status"], "resumed")
        self.assertEqual(calls[0][:2], ["codex", "exec"])
        self.assertNotIn("--last", calls[0])

    def test_room_turn_packet_is_bounded_and_keeps_instruction_and_media_warning(self):
        store = RoomStore(self.output_root)
        store.create_room("room-a")
        for index in range(15):
            store.append_event("room-a", "message_final", actor_id="human", content=f"event {index} " + ("x" * 200))
        media = store.attach_media("room-a", filename="notes.bin", content_type="application/octet-stream", data=b"data", supported=False)
        result = resume_agent_session_payload(
            self.output_root,
            {"room_id": "room-a", "agent_id": "agent-1", "session_id": "session-1", "provider_kind": "codex_live_session"},
        )

        packet = build_room_turn_packet(
            self.output_root,
            room_id="room-a",
            participant_id=result["participant"]["participant_id"],
            session_id="session-1",
            instruction="Important current instruction.",
            media_ids=[media["id"]],
            max_recent_events=20,
            max_prompt_chars=2500,
        )

        self.assertLessEqual(packet["provider_visible_chars"], 2500)
        self.assertIn("Important current instruction.", packet["current_turn_instruction"])
        self.assertIn("Unsupported media", packet["media_notes"][0])
        self.assertNotIn(str(Path.cwd()), str(packet))

    def test_room_turn_packet_excludes_internal_runtime_events(self):
        store = RoomStore(self.output_root)
        store.create_room("room-a")
        store.append_event("room-a", "turn_started", participant_id="agent-1", diagnostics=[{"setting": "secret", "status": "x", "message": "x"}])
        store.append_event("room-a", "message_delta", participant_id="agent-1", content="partial")
        store.append_event("room-a", "message_final", participant_id="agent-1", content="final answer")
        store.append_event("room-a", "turn_finished", participant_id="agent-1", diagnostics=[{"setting": "timing", "status": "slow", "message": "slow"}])
        result = resume_agent_session_payload(
            self.output_root,
            {"room_id": "room-a", "agent_id": "agent-1", "session_id": "session-1", "provider_kind": "codex_live_session"},
        )

        packet = build_room_turn_packet(
            self.output_root,
            room_id="room-a",
            participant_id=result["participant"]["participant_id"],
            session_id="session-1",
            instruction="Continue.",
        )

        self.assertEqual([event["type"] for event in packet["events"]], [])
        self.assertNotIn("diagnostics", str(packet))
        self.assertNotIn("partial", str(packet))

    def test_provider_turn_input_includes_other_public_delta_not_own_prior_message(self):
        store = RoomStore(self.output_root)
        store.create_room("room-a", label="Night Council")
        resume_agent_session_payload(
            self.output_root,
            {
                "room_id": "room-a",
                "agent_id": "agent-a",
                "session_id": "session-a",
                "display_name": "Luna",
                "provider_kind": "codex_live_session",
            },
        )
        store.append_event("room-a", "message_final", participant_id="agent-a", content="my previous answer")
        store.append_event("room-a", "message_final", participant_id="agent-b", content="other agent update")
        store.upsert_session("room-a", {**store.session("room-a", "session-a"), "bootstrap_done": True})

        packet = build_room_turn_packet(
            self.output_root,
            room_id="room-a",
            participant_id="agent-a",
            session_id="session-a",
            instruction="Reply briefly.",
        )

        self.assertEqual(packet["input_mode"], "delta")
        self.assertIn("other agent update", packet["provider_input"])
        self.assertNotIn("my previous answer", packet["provider_input"])
        self.assertNotIn("provider_session_id", packet["provider_input"])
        self.assertNotIn("diagnostics", packet["provider_input"])
        self.assertIn("Your display name in this room is: Luna", packet["provider_input"])
        self.assertIn("The room name is: Night Council", packet["provider_input"])

    def test_provider_delta_truncates_long_message_and_advances_cursor(self):
        store = RoomStore(self.output_root)
        store.create_room("room-a")
        store.append_event("room-a", "message_final", participant_id="agent-b", content="x" * 5000)
        final_id = store.read_events("room-a")[-1]["id"]
        resume_agent_session_payload(
            self.output_root,
            {"room_id": "room-a", "agent_id": "agent-a", "session_id": "session-a", "provider_kind": "codex_live_session"},
        )
        store.upsert_session("room-a", {**store.session("room-a", "session-a"), "bootstrap_done": True})

        packet = build_room_turn_packet(
            self.output_root,
            room_id="room-a",
            participant_id="agent-a",
            session_id="session-a",
            instruction="Reply.",
        )

        self.assertIn("[truncated]", packet["provider_input"])
        self.assertEqual(packet["last_provider_sync_event_id_after"], final_id)
        self.assertLessEqual(packet["provider_visible_chars"], 20000)

    def test_provider_delta_keeps_latest_source_message_when_room_history_is_truncated(self):
        store = RoomStore(self.output_root)
        store.create_room("room-a")
        resume_agent_session_payload(
            self.output_root,
            {"room_id": "room-a", "agent_id": "agent-a", "session_id": "session-a", "provider_kind": "grok_live_session"},
        )
        for index in range(15):
            store.append_event("room-a", "message_final", participant_id="human", content=f"old room update {index}")
        source = store.append_event(
            "room-a",
            "message_final",
            participant_id="human",
            content="@agent-a AGENTSASSEMBLE_SESSION_MARKER=latest-source",
        )

        packet = build_room_turn_packet(
            self.output_root,
            room_id="room-a",
            participant_id="agent-a",
            session_id="session-a",
            instruction="Reply.",
        )

        self.assertIn("AGENTSASSEMBLE_SESSION_MARKER=latest-source", packet["provider_input"])
        self.assertNotIn("old room update 0", packet["provider_input"])
        self.assertIn("omitted 4 earlier room update", packet["provider_input"])
        self.assertEqual(packet["provider_visible_event_count"], 12)
        self.assertEqual(packet["last_provider_sync_event_id_after"], source["id"])

    def test_prompt_budget_does_not_advance_cursor_past_omitted_events(self):
        store = RoomStore(self.output_root)
        store.create_room("room-a")
        resume_agent_session_payload(
            self.output_root,
            {
                "room_id": "room-a",
                "agent_id": "agent-a",
                "session_id": "session-a",
                "provider_kind": "codex_live_session",
            },
        )
        store.upsert_session(
            "room-a",
            {
                **store.session("room-a", "session-a"),
                "bootstrap_done": True,
                "recovery_required": True,
                "room_memory": {"summary": "memory " * 900},
            },
        )
        first = store.append_event(
            "room-a",
            "message_final",
            participant_id="human",
            content="FIRST-INCLUDED " + ("a" * 300),
        )
        later = [
            store.append_event(
                "room-a",
                "message_final",
                participant_id="human",
                content=f"LATER-{index} " + (character * 300),
            )
            for index, character in enumerate(("b", "c", "d"), start=1)
        ]

        packet = build_room_turn_packet(
            self.output_root,
            room_id="room-a",
            participant_id="agent-a",
            session_id="session-a",
            instruction="Reply to what you can see.",
            max_prompt_chars=600,
        )

        included_ids = [event["id"] for event in packet["events"]]
        self.assertTrue(included_ids)
        self.assertEqual(included_ids[0], first["id"])
        self.assertLess(len(included_ids), 1 + len(later))
        self.assertIn("FIRST-INCLUDED", packet["provider_input"])
        self.assertNotIn("LATER-3", packet["provider_input"])
        self.assertEqual(packet["provider_visible_event_count"], len(included_ids))
        self.assertEqual(packet["last_provider_sync_event_id_after"], included_ids[-1])
        self.assertEqual(
            packet["last_provider_sync_seq_after"],
            packet["events"][-1]["seq"],
        )
        self.assertLess(packet["last_provider_sync_seq_after"], later[-1]["seq"])

    def test_media_is_not_implicitly_sent_without_selection_or_room_reference(self):
        store = RoomStore(self.output_root)
        store.create_room("room-a")
        store.append_event("room-a", "message_final", participant_id="human", content="hello")
        media = store.attach_media("room-a", filename="diagram.png", content_type="image/png", data=b"image", supported=True)
        result = resume_agent_session_payload(
            self.output_root,
            {"room_id": "room-a", "agent_id": "agent-a", "session_id": "session-a", "provider_kind": "codex_live_session"},
        )

        packet = build_room_turn_packet(
            self.output_root,
            room_id="room-a",
            participant_id=result["participant"]["participant_id"],
            session_id="session-a",
            instruction="Reply.",
        )
        selected_packet = build_room_turn_packet(
            self.output_root,
            room_id="room-a",
            participant_id=result["participant"]["participant_id"],
            session_id="session-a",
            instruction="Reply.",
            media_ids=[media["id"]],
        )

        self.assertEqual(packet["media_manifest"], [])
        self.assertEqual([item["id"] for item in selected_packet["media_manifest"]], [media["id"]])

    def test_provider_media_manifest_projects_only_allowlisted_keys(self):
        store = RoomStore(self.output_root)
        store.create_room("room-a")
        result = resume_agent_session_payload(
            self.output_root,
            {
                "room_id": "room-a",
                "agent_id": "agent-a",
                "session_id": "session-a",
                "provider_kind": "codex_live_session",
            },
        )
        store.append_event(
            "room-a",
            "media_attached",
            media={
                "id": "media-safe",
                "filename": "/private/folder/safe.png",
                "content_type": {"unexpected": "image/png"},
                "size": -12,
                "supported": "yes",
                "representation": {"unexpected": "native"},
                "path": "/private/secret.png",
                "local_path": "/private/secret-2.png",
                "signed_url": "https://example.invalid/private-token",
                "storage_locator": "bucket/private",
            },
        )

        packet = build_room_turn_packet(
            self.output_root,
            room_id="room-a",
            participant_id=result["participant"]["participant_id"],
            session_id="session-a",
            instruction="Describe it.",
            media_ids=["media-safe"],
        )

        self.assertEqual(
            set(packet["media_manifest"][0]),
            {"id", "filename", "content_type", "size", "supported"},
        )
        self.assertEqual(packet["media_manifest"][0]["filename"], "safe.png")
        self.assertEqual(packet["media_manifest"][0]["content_type"], "application/octet-stream")
        self.assertEqual(packet["media_manifest"][0]["size"], 0)
        self.assertFalse(packet["media_manifest"][0]["supported"])
        self.assertNotIn("/private/", str(packet))
        self.assertNotIn("private-token", str(packet))

    def test_implicit_media_is_removed_when_its_source_event_does_not_fit(self):
        store = RoomStore(self.output_root)
        store.create_room("room-a")
        result = resume_agent_session_payload(
            self.output_root,
            {
                "room_id": "room-a",
                "agent_id": "agent-a",
                "session_id": "session-a",
                "provider_kind": "codex_live_session",
            },
        )
        store.upsert_session(
            "room-a",
            {**store.session("room-a", "session-a"), "bootstrap_done": True},
        )
        store.append_event(
            "room-a",
            "media_attached",
            media={
                "id": "media-deferred",
                "filename": "deferred.png",
                "content_type": "image/png",
                "size": 10,
                "supported": True,
            },
        )
        store.append_event(
            "room-a",
            "message_final",
            participant_id="human",
            content="first message " + ("a" * 4000),
        )
        store.append_event(
            "room-a",
            "message_final",
            participant_id="human",
            content="look at media-deferred " + ("b" * 4000),
        )

        packet = build_room_turn_packet(
            self.output_root,
            room_id="room-a",
            participant_id=result["participant"]["participant_id"],
            session_id="session-a",
            instruction="Reply.",
            max_prompt_chars=500,
        )

        self.assertNotIn("media-deferred", packet["provider_input"])
        self.assertEqual(packet["media_manifest"], [])

    def test_too_small_prompt_budget_never_sends_an_empty_instruction(self):
        store = RoomStore(self.output_root)
        store.create_room("room-a")
        result = resume_agent_session_payload(
            self.output_root,
            {
                "room_id": "room-a",
                "agent_id": "agent-a",
                "session_id": "session-a",
                "provider_kind": "codex_live_session",
            },
        )

        with self.assertRaisesRegex(ValueError, "too small for required provider context"):
            build_room_turn_packet(
                self.output_root,
                room_id="room-a",
                participant_id=result["participant"]["participant_id"],
                session_id="session-a",
                instruction="Required instruction.",
                max_prompt_chars=100,
            )

        with self.assertRaisesRegex(ValueError, "instruction is required"):
            build_room_turn_packet(
                self.output_root,
                room_id="room-a",
                participant_id=result["participant"]["participant_id"],
                session_id="session-a",
                instruction="",
            )

    def test_provider_sync_cursor_advances_after_success_and_not_after_failure(self):
        store = RoomStore(self.output_root)
        store.create_room("room-a")
        source = store.append_event("room-a", "message_final", participant_id="human", content="hello")
        resume_agent_session_payload(
            self.output_root,
            {"room_id": "room-a", "agent_id": "agent-a", "session_id": "session-a", "provider_kind": "codex_live_session"},
        )

        success = run_agent_session_turn_payload(
            self.output_root,
            {"room_id": "room-a", "agent_id": "agent-a", "session_id": "session-a", "instruction": "Answer."},
            turn_runner=lambda packet: [{"type": "message_final", "content": "ok"}],
        )

        session_after_success = RoomStore(self.output_root).session("room-a", "session-a")
        self.assertEqual(success["turn_status"], "finished")
        self.assertTrue(session_after_success.get("bootstrap_done"))
        self.assertEqual(session_after_success.get("last_spoke_event_id"), success["events"][-2]["id"])
        cursor_after_success = session_after_success.get("last_provider_sync_event_id")
        cursor_seq_after_success = session_after_success.get("last_provider_sync_seq")
        self.assertEqual(cursor_after_success, source["id"])
        self.assertEqual(cursor_seq_after_success, source["seq"])

        store = RoomStore(self.output_root)
        store.append_event("room-a", "message_final", participant_id="human", content="new human update")
        failed = run_agent_session_turn_payload(
            self.output_root,
            {"room_id": "room-a", "agent_id": "agent-a", "session_id": "session-a", "instruction": "Fail."},
            turn_runner=lambda packet: [{"type": "error", "diagnostics": [{"setting": "x", "status": "failed", "message": "boom"}]}],
        )

        self.assertEqual(failed["turn_status"], "error")
        self.assertEqual(RoomStore(self.output_root).session("room-a", "session-a").get("last_provider_sync_event_id"), cursor_after_success)
        self.assertEqual(RoomStore(self.output_root).session("room-a", "session-a").get("last_provider_sync_seq"), cursor_seq_after_success)

    def test_normal_provider_input_omits_summary_but_recovery_includes_it(self):
        normal = build_provider_turn_input(instruction="Answer.", room_delta="")
        recovery = build_provider_recovery_input(
            instruction="Answer.",
            room_memory={"summary": "Known room summary.", "decisions": ["Ship app-server"], "open_questions": [], "up_to_event_id": "evt-1"},
        )

        self.assertNotIn("Known room summary", normal)
        self.assertIn("Known room summary", recovery)
        self.assertIn("Ship app-server", recovery)

    def test_new_session_without_provider_is_state_only_and_not_launchable(self):
        result = resume_agent_session_payload(
            self.output_root,
            {"room_id": "room-a", "agent_id": "agent-1", "session_id": "session-1"},
        )

        self.assertEqual(result["state_status"], "resumed")
        self.assertEqual(result["process_status"], "not_started")
        self.assertEqual(result["launch_plan"]["permission_enforcement"], "unsupported")
        self.assertTrue(any("provider" in item["message"].lower() for item in result["diagnostics"]))

    def test_reresume_without_provider_reuses_persisted_provider_kind(self):
        resume_agent_session_payload(
            self.output_root,
            {
                "room_id": "room-a",
                "agent_id": "agent-1",
                "session_id": "session-1",
                "provider_kind": "codex_live_session",
            },
        )

        result = resume_agent_session_payload(
            self.output_root,
            {"room_id": "room-a", "agent_id": "agent-1", "session_id": "session-1"},
        )

        self.assertEqual(result["session"]["provider_kind"], "codex_live_session")
        self.assertEqual(result["launch_plan"]["provider_kind"], "codex_live_session")

    def test_attach_media_writes_bytes_and_reports_unsupported_media(self):
        store = RoomStore(self.output_root)
        store.create_room("room-a")
        media = store.attach_media(
            "room-a",
            filename="../diagram.svg",
            content_type="image/svg+xml",
            data=b"<svg></svg>",
            supported=False,
        )
        result = resume_agent_session_payload(
            self.output_root,
            {"room_id": "room-a", "agent_id": "agent-1", "session_id": "session-1"},
        )

        packet = build_room_turn_packet(
            self.output_root,
            room_id="room-a",
            participant_id=result["participant"]["participant_id"],
            session_id="session-1",
            instruction="Describe attached media.",
            media_ids=[media["id"]],
        )

        media_path = Path(media["path"])
        self.assertTrue(media_path.exists())
        self.assertEqual(media_path.read_bytes(), b"<svg></svg>")
        self.assertNotIn("path", packet["media_manifest"][0])
        self.assertEqual(packet["media_manifest"][0]["size"], len(b"<svg></svg>"))
        self.assertFalse(packet["media_manifest"][0]["supported"])
        self.assertIn("Unsupported media", packet["media_notes"][0])
        self.assertIn("unsupported_media", [event["type"] for event in store.read_events("room-a")])
        self.assertNotIn(str(Path.cwd()), str(packet))

    def test_provider_prompt_bound_applies_to_actual_fallback_text(self):
        store = RoomStore(self.output_root)
        store.create_room("room-a", label="A" * 500)
        result = resume_agent_session_payload(
            self.output_root,
            {
                "room_id": "room-a",
                "agent_id": "agent-1",
                "session_id": "session-1",
                "display_name": "B" * 500,
                "provider_kind": "codex_live_session",
            },
        )
        store.append_event("room-a", "message_final", actor_id="human", content="C" * 5000)

        packet = build_room_turn_packet(
            self.output_root,
            room_id="room-a",
            participant_id=result["participant"]["participant_id"],
            session_id="session-1",
            instruction="Important final instruction.",
            max_prompt_chars=800,
        )

        prompt = _agent_turn_prompt(packet)
        self.assertLessEqual(len(prompt), 800)
        self.assertEqual(packet["provider_visible_chars"], len(prompt))
        self.assertIn("Important final instruction.", prompt)
        self.assertNotIn(str(self.output_root), prompt)

    def test_sse_stream_yields_new_appended_event_after_idle_heartbeat(self):
        store = RoomStore(self.output_root)
        store.create_room("room-a")
        cursor = store.read_events("room-a")[-1]["id"]
        polls = {"count": 0}

        def wait_once() -> None:
            polls["count"] += 1
            if polls["count"] == 1:
                store.append_event("room-a", "message_final", content="after connect")

        frames = list(
            stream_room_sse_frames(
                self.output_root,
                "room-a",
                cursor=cursor,
                max_iterations=2,
                wait=wait_once,
            )
        )

        self.assertIn("event: heartbeat", frames[0])
        self.assertTrue(any("event: message_final" in frame for frame in frames))


if __name__ == "__main__":
    unittest.main()
