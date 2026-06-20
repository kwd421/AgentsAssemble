import tempfile
import unittest
from pathlib import Path

from agentsassemble.agent_sessions import (
    build_agent_session_launch_plan,
    build_room_turn_packet,
    resume_agent_session_payload,
    room_sse_frames_after_cursor,
    stream_room_sse_frames,
)
from agentsassemble.room_store import RoomStore


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
        )

        self.assertEqual(packet["room_id"], "room-a")
        self.assertEqual([event["type"] for event in packet["events"]], ["room_created", "message_final", "media_attached", "participant_joined", "session_attached", "session_resumed"])
        self.assertEqual(packet["media_manifest"][0]["id"], media["id"])
        self.assertNotIn(str(Path.cwd()), str(packet))
        self.assertIn("Do not inspect or edit the project", packet["explicit_non_goals"][0])

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

    def test_session_settings_flow_to_supported_command_or_diagnostics(self):
        codex = build_agent_session_launch_plan(
            {
                "provider_kind": "codex_live_session",
                "session_id": "codex-session",
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

        self.assertEqual(codex["permission_enforcement"], "enforced")
        self.assertIn("--model", codex["command"])
        self.assertIn("gpt-5.5", codex["command"])
        self.assertIn("--sandbox", codex["command"])
        self.assertIn("read-only", codex["command"])
        self.assertIn('model_reasoning_effort="high"', codex["command"])
        self.assertEqual(unsupported["permission_enforcement"], "unsupported")
        self.assertTrue(unsupported["diagnostics"])

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
        self.assertEqual(dry_run["launch_plan"]["command"][:3], ["codex", "exec", "resume"])
        self.assertEqual(launched["process_status"], "resumed")
        self.assertEqual(calls[0][:3], ["codex", "exec", "resume"])

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
        )

        media_path = Path(media["path"])
        self.assertTrue(media_path.exists())
        self.assertEqual(media_path.read_bytes(), b"<svg></svg>")
        self.assertEqual(packet["media_manifest"][0]["path"], str(media_path))
        self.assertEqual(packet["media_manifest"][0]["size"], len(b"<svg></svg>"))
        self.assertFalse(packet["media_manifest"][0]["supported"])
        self.assertIn("unsupported_media", [event["type"] for event in store.read_events("room-a")])
        self.assertNotIn(str(Path.cwd()), str(packet))

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
