import tempfile
import unittest
from pathlib import Path

from agentsassemble.room.context import project_room_context
from agentsassemble.room_store import RoomStore


class RoomContextTests(unittest.TestCase):
    def test_bootstrap_keeps_latest_twelve_other_participant_messages(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = RoomStore(Path(temp_dir))
            store.create_room("general")
            for index in range(15):
                store.append_event(
                    "general",
                    "message_final",
                    participant_id="human",
                    display_name="Human",
                    content=f"room message {index}",
                )
            store.append_event(
                "general",
                "message_final",
                participant_id="codex",
                content="my prior answer",
            )

            window = project_room_context(
                store,
                room_id="general",
                participant_id="codex",
            )

        self.assertEqual(len(window.events), 12)
        self.assertEqual(window.omitted_message_count, 3)
        self.assertNotIn("room message 0", window.text)
        self.assertIn("room message 14", window.text)
        self.assertNotIn("my prior answer", window.text)

    def test_existing_session_reads_only_messages_after_sequence_cursor(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = RoomStore(Path(temp_dir))
            store.create_room("general")
            cursor = store.append_event(
                "general",
                "message_final",
                participant_id="human",
                content="already synchronized",
            )
            store.append_event("general", "message_final", participant_id="codex", content="my answer")
            first = store.append_event("general", "message_final", participant_id="human", content="new one")
            second = store.append_event("general", "message_final", participant_id="grok", content="new two")

            window = project_room_context(
                store,
                room_id="general",
                participant_id="codex",
                after_seq=int(cursor["seq"]),
            )

        self.assertEqual([event["id"] for event in window.events], [first["id"], second["id"]])
        self.assertNotIn("already synchronized", window.text)
        self.assertNotIn("my answer", window.text)
        self.assertEqual(window.latest_seq, second["seq"])

    def test_context_text_is_bounded_and_preserves_latest_marker(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = RoomStore(Path(temp_dir))
            store.create_room("general")
            for index in range(11):
                store.append_event(
                    "general",
                    "message_final",
                    participant_id="human",
                    content=f"long-{index}-" + ("x" * 1200),
                )
            latest = store.append_event(
                "general",
                "message_final",
                participant_id="human",
                content="LATEST_CONTEXT_MARKER " + ("y" * 1200),
            )

            window = project_room_context(
                store,
                room_id="general",
                participant_id="codex",
                max_chars=4000,
            )

        self.assertLessEqual(len(window.text), 4000)
        self.assertIn("LATEST_CONTEXT_MARKER", window.text)
        self.assertEqual(window.latest_event_id, latest["id"])


if __name__ == "__main__":
    unittest.main()
