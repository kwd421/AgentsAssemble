import unittest

from agentsassemble.room_projection import (
    merged_latency,
    public_activity,
    public_event,
    public_runtime_diagnostics,
    public_session,
    runtime_diagnostic_fields,
)


class RoomProjectionTests(unittest.TestCase):
    def test_public_session_keeps_room_state_and_removes_runtime_secrets(self):
        session = {
            "session_id": "agent-1",
            "display_name": "Agent One",
            "runtime_status": "idle",
            "model": "model-1",
            "pid": 123,
            "reported_provider_pid": 456,
            "bridge_handle_id": "private-handle",
            "provider_session_id": "private-provider-session",
            "workspace": "/private/workspace",
            "command_configured": ["provider", "--secret"],
            "stderr_tail": "private stderr",
            "terminal_tail": "private terminal output",
        }

        projected = public_session(session)

        self.assertEqual(
            projected,
            {
                "session_id": "agent-1",
                "display_name": "Agent One",
                "runtime_status": "idle",
                "model": "model-1",
            },
        )
        self.assertEqual(session["bridge_handle_id"], "private-handle")

    def test_public_event_removes_private_fields_at_every_nested_level(self):
        event = {
            "id": "evt-1",
            "type": "message_final",
            "content": "visible",
            "legacy_source_path": "/private/legacy.jsonl",
            "media": {
                "media_id": "media-1",
                "filename": "image.png",
                "path": "/private/image.png",
            },
            "items": [
                {
                    "label": "visible item",
                    "argv": ["provider", "--token", "secret"],
                    "provider_session_id": "private-session",
                }
            ],
        }

        projected = public_event(event)

        self.assertEqual(projected["content"], "visible")
        self.assertEqual(projected["media"], {"media_id": "media-1", "filename": "image.png"})
        self.assertEqual(projected["items"], [{"label": "visible item"}])
        self.assertNotIn("legacy_source_path", projected)

    def test_runtime_diagnostics_store_bounded_tails_but_public_projection_omits_them(self):
        raw = {
            "stderr_drained": True,
            "stderr_byte_count": 70001,
            "stderr_warning_count": 4,
            "stderr_tail": "s" * 20000,
            "terminal_tail": "t" * 20000,
            "provider_session_reused": True,
            "message_source": "provider_protocol",
        }

        stored = runtime_diagnostic_fields(raw)
        public = public_runtime_diagnostics(raw)

        self.assertEqual(len(stored["stderr_tail"]), 16000)
        self.assertEqual(len(stored["terminal_tail"]), 16000)
        self.assertEqual(public["stderr_byte_count"], 70001)
        self.assertEqual(public["stderr_warning_count"], 4)
        self.assertTrue(public["provider_session_reused"])
        self.assertEqual(public["message_source"], "provider_protocol")
        self.assertNotIn("stderr_tail", public)
        self.assertNotIn("terminal_tail", public)

    def test_activity_and_latency_projection_preserve_existing_room_contract(self):
        self.assertEqual(public_activity("reasoning", "running"), ("생각 정리 중", "reasoning"))
        self.assertEqual(public_activity("command", "completed"), ("명령 실행 완료", "tool"))
        self.assertEqual(
            merged_latency(
                {"ttfo_ms": 120, "total_turn_ms": 500},
                {"ttfo_ms": None, "total_turn_ms": 650, "stream_ms": 200},
            ),
            {"ttfo_ms": 120, "total_turn_ms": 650, "stream_ms": 200},
        )


if __name__ == "__main__":
    unittest.main()
