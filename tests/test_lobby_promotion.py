import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from agentsassemble.legacy.meeting.core.events import (
    append_live_event,
    append_lobby_event_to_file,
    append_side_chat_event_to_file,
    read_live_events,
    write_live_state,
)


def _meeting_record(meeting_id: str = "resident-m1") -> dict[str, object]:
    return {
        "meeting_id": meeting_id,
        "question": "Which lobby context should become official?",
        "topic": "promotion boundary",
        "roles": [],
        "agent_bindings": [],
        "provider_configs": {},
        "permission_profiles": {},
        "debate_rounds": [],
        "room_chat": [],
        "live_status": "running",
    }


class LobbyPromotionTests(unittest.TestCase):
    def test_promote_appends_official_context_event_without_attachments_or_sensitive_text(self):
        from agentsassemble.legacy.meeting.support.live_transcript import render_live_transcript
        from agentsassemble.legacy.meeting.support.lobby_promotion import promote_lobby_events_to_official

        with tempfile.TemporaryDirectory() as temp_dir:
            output_root = Path(temp_dir)
            meeting_dir = output_root / "meetings" / "resident-m1"
            meeting_dir.mkdir(parents=True)
            write_live_state(meeting_dir, _meeting_record())
            lobby_event = append_lobby_event_to_file(
                output_root / "lobby.jsonl",
                {
                    "name": "owner",
                    "actor_id": "owner",
                    "message": "Decision: keep this. See http://secret.example/path and /Users/me/private.json",
                    "attachments": [
                        {
                            "id": "att-secret",
                            "filename": "../secret.png",
                            "content_type": "image/png",
                            "size": 12,
                            "is_image": True,
                            "url": "/api/attachments/att-secret?view=1",
                            "download_url": "/api/attachments/att-secret?download=1",
                        }
                    ],
                    "flow_id": "flow-private",
                    "target_agent_id": "agent-secret",
                    "auto_chain_depth": 3,
                },
                allow_flow_metadata=True,
            )

            result = promote_lobby_events_to_official(
                output_root,
                "resident-m1",
                [lobby_event["id"]],
                reason="operator selected during review",
            )

            self.assertEqual(result["status"], "promoted")
            self.assertEqual(result["meeting_id"], "resident-m1")
            self.assertEqual(result["source_event_ids"], [lobby_event["id"]])
            events = read_live_events(meeting_dir, limit=None)
            promoted = events[-1]
            self.assertEqual(promoted["kind"], "promoted_context")
            self.assertEqual(promoted["channel"], "official")
            self.assertTrue(promoted["official_record"])
            self.assertEqual(promoted["source_event_id"], lobby_event["id"])
            self.assertEqual(promoted["actor_id"], "moderator")
            self.assertEqual(promoted["promoted_from"], "lobby")
            self.assertEqual(promoted["promoted_reason"], "operator selected during review")
            self.assertIn("Decision: keep this.", promoted["content"])

            serialized_event = json.dumps(promoted, ensure_ascii=False)
            self.assertNotIn("http://secret.example", serialized_event)
            self.assertNotIn("/Users/me", serialized_event)
            self.assertNotIn("attachments", serialized_event)
            self.assertNotIn("att-secret", serialized_event)
            self.assertNotIn("flow-private", serialized_event)
            self.assertNotIn("agent-secret", serialized_event)
            self.assertNotIn("auto_chain_depth", serialized_event)

            transcript = render_live_transcript(events, meeting=_meeting_record())
            self.assertIn("Promoted from: lobby", transcript)
            self.assertIn("Decision: keep this.", transcript)
            self.assertNotIn("http://secret.example", transcript)
            self.assertNotIn("/Users/me", transcript)

            operation = result["operation"]
            operation_text = json.dumps(operation, ensure_ascii=False)
            self.assertEqual(operation["operation"], "lobby.promote_to_official")
            self.assertEqual(operation["target_id"], "resident-m1")
            self.assertIn(lobby_event["id"], operation["details"]["source_event_ids"])
            self.assertNotIn("Decision: keep this.", operation_text)
            self.assertNotIn("http://secret.example", operation_text)
            self.assertNotIn("/Users/me", operation_text)

    def test_promote_rejects_side_chat_event_id_without_appending(self):
        from agentsassemble.legacy.meeting.support.lobby_promotion import promote_lobby_events_to_official

        with tempfile.TemporaryDirectory() as temp_dir:
            output_root = Path(temp_dir)
            meeting_dir = output_root / "meetings" / "resident-m1"
            meeting_dir.mkdir(parents=True)
            write_live_state(meeting_dir, _meeting_record())
            side_event = append_side_chat_event_to_file(
                output_root / "side_chat.jsonl",
                {"name": "owner", "message": "private side chat"},
            )

            with self.assertRaisesRegex(ValueError, "Lobby event id not found"):
                promote_lobby_events_to_official(output_root, "resident-m1", [side_event["id"]])

            self.assertEqual(read_live_events(meeting_dir, limit=None), [])

    def test_promote_refuses_duplicate_using_full_live_event_scan(self):
        from agentsassemble.legacy.meeting.support.lobby_promotion import promote_lobby_events_to_official

        with tempfile.TemporaryDirectory() as temp_dir:
            output_root = Path(temp_dir)
            meeting_dir = output_root / "meetings" / "resident-m1"
            meeting_dir.mkdir(parents=True)
            write_live_state(meeting_dir, _meeting_record())
            lobby_event = append_lobby_event_to_file(
                output_root / "lobby.jsonl",
                {"name": "owner", "message": "Important lobby note."},
            )
            promote_lobby_events_to_official(output_root, "resident-m1", [lobby_event["id"]])
            for index in range(250):
                append_live_event(meeting_dir, {"kind": "status", "content": f"status {index}"})

            with self.assertRaisesRegex(ValueError, "already promoted"):
                promote_lobby_events_to_official(output_root, "resident-m1", [lobby_event["id"]])

            promoted = [
                event for event in read_live_events(meeting_dir, limit=None) if event.get("kind") == "promoted_context"
            ]
            self.assertEqual(len(promoted), 1)

    def test_promote_caps_event_count_per_call(self):
        from agentsassemble.legacy.meeting.support.lobby_promotion import promote_lobby_events_to_official

        with tempfile.TemporaryDirectory() as temp_dir:
            output_root = Path(temp_dir)
            meeting_dir = output_root / "meetings" / "resident-m1"
            meeting_dir.mkdir(parents=True)
            write_live_state(meeting_dir, _meeting_record())
            ids = [
                append_lobby_event_to_file(
                    output_root / "lobby.jsonl",
                    {"name": "owner", "message": f"note {index}"},
                )["id"]
                for index in range(21)
            ]

            with self.assertRaisesRegex(ValueError, "at most 20"):
                promote_lobby_events_to_official(output_root, "resident-m1", ids)

            self.assertEqual(read_live_events(meeting_dir, limit=None), [])

    def test_promote_batch_refuses_atomically_when_any_id_is_invalid(self):
        from agentsassemble.legacy.meeting.support.lobby_promotion import promote_lobby_events_to_official

        with tempfile.TemporaryDirectory() as temp_dir:
            output_root = Path(temp_dir)
            meeting_dir = output_root / "meetings" / "resident-m1"
            meeting_dir.mkdir(parents=True)
            write_live_state(meeting_dir, _meeting_record())
            valid_event = append_lobby_event_to_file(
                output_root / "lobby.jsonl",
                {"name": "owner", "message": "Valid note should not partially promote."},
            )

            with self.assertRaisesRegex(ValueError, "Lobby event id not found"):
                promote_lobby_events_to_official(output_root, "resident-m1", [valid_event["id"], "missing-event-id"])

            self.assertEqual(read_live_events(meeting_dir, limit=None), [])


class LobbyPromotionCliTests(unittest.TestCase):
    def test_cli_lobby_promote_appends_official_event_offline(self):
        from agentsassemble.cli import main

        with tempfile.TemporaryDirectory() as temp_dir:
            output_root = Path(temp_dir)
            meeting_dir = output_root / "meetings" / "resident-m1"
            meeting_dir.mkdir(parents=True)
            write_live_state(meeting_dir, _meeting_record())
            lobby_event = append_lobby_event_to_file(
                output_root / "lobby.jsonl",
                {"name": "owner", "message": "Promote this note."},
            )

            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                code = main(
                    [
                        "lobby",
                        "promote",
                        "--output-root",
                        str(output_root),
                        "--meeting-id",
                        "resident-m1",
                        "--lobby-event-id",
                        str(lobby_event["id"]),
                        "--json",
                    ]
                )

            self.assertEqual(code, 0)
            payload = json.loads(stdout.getvalue())
            self.assertEqual(payload["status"], "promoted")
            self.assertEqual(read_live_events(meeting_dir, limit=None)[-1]["kind"], "promoted_context")


if __name__ == "__main__":
    unittest.main()
