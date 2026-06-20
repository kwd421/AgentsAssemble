import tempfile
import unittest
from pathlib import Path

from agentsassemble.agent_sessions import (
    build_agent_session_launch_plan,
    build_room_turn_packet,
    resume_agent_session_payload,
    room_sse_frames_after_cursor,
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


if __name__ == "__main__":
    unittest.main()
