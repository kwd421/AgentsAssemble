import tempfile
import threading
import unittest
from pathlib import Path

from agentsassemble.legacy.live_agent.speech import (
    LegacyLiveAgentLobbySpeechDeps,
    LegacyLiveAgentSpeechService,
)
from agentsassemble.live_agents import connect_live_agent
from agentsassemble.legacy.meeting.core.events import append_lobby_event_to_file, read_lobby_events


class LegacyLiveAgentSpeechServiceTests(unittest.TestCase):
    def test_lobby_reply_keeps_identity_cursor_and_idempotency(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            connect_live_agent(root, {"agent_id": "agent-a", "display_name": "Agent A"})
            service = _speech_service(root)

            first = service.post_lobby_message(
                "agent-a",
                {"message": "hello", "source_event_id": "source-a"},
            )
            second = service.post_lobby_message(
                "agent-a",
                {"message": "hello again", "source_event_id": "source-a"},
            )
            events = read_lobby_events(root / "lobby.jsonl")

        self.assertEqual(first["event"]["actor_id"], "agent-a")
        self.assertEqual(first["event"]["source_event_id"], "source-a")
        self.assertEqual(first["event"]["id"], second["event"]["id"])
        self.assertEqual(first["agent"]["last_observed_event_id"], "source-a")
        self.assertEqual(len(events), 1)

    def test_empty_lobby_reply_fails_before_append(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            connect_live_agent(root, {"agent_id": "agent-a"})

            with self.assertRaisesRegex(ValueError, "Message is required"):
                _speech_service(root).post_lobby_message("agent-a", {"message": "  "})

        self.assertFalse((root / "lobby.jsonl").exists())


def _speech_service(root: Path) -> LegacyLiveAgentSpeechService:
    return LegacyLiveAgentSpeechService(
        root,
        lobby=LegacyLiveAgentLobbySpeechDeps(
            append_lobby_event=lambda output_root, event, **kwargs: append_lobby_event_to_file(
                output_root / "lobby.jsonl",
                event,
                **kwargs,
            ),
            public_lobby_allows_room_scope=lambda payload: True,
            is_muted=lambda *args, **kwargs: False,
            lobby_lock=threading.RLock(),
            is_smoke_source_redacted=lambda source_event_id: False,
            redact_smoke_events=lambda output_root, source_event_ids: {},
            smoke_reply_message=lambda source_event_id, message: message,
            smoke_reply_redaction="[redacted]",
        ),
    )


if __name__ == "__main__":
    unittest.main()
