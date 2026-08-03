import unittest

from agentsassemble.room.projection import (
    merged_latency,
    public_activity,
    public_event,
    public_event_for_identity,
    public_participant,
    public_runtime_diagnostics,
    public_session,
    runtime_diagnostic_fields,
    safe_activity_detail,
)


class RoomProjectionTests(unittest.TestCase):
    def test_owner_only_reasoning_keeps_event_sequence_without_exposing_content(self):
        event = {
            "id": "evt-reasoning",
            "seq": 41,
            "room_id": "general",
            "created_at": "2026-08-03T00:00:00+00:00",
            "type": "activity_delta",
            "participant_id": "agent-1",
            "owner_id": "owner-1",
            "visibility": "owner",
            "category": "reasoning",
            "activity_detail": "provider reasoning",
        }

        owner = public_event_for_identity(event, {"user_id": "owner-1"})
        peer = public_event_for_identity(event, {"user_id": "peer-1"})

        self.assertEqual(owner["activity_detail"], "provider reasoning")
        self.assertEqual(peer["type"], "event_hidden")
        self.assertEqual(peer["seq"], 41)
        self.assertNotIn("activity_detail", peer)

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
            "lifecycle_intent_action": "stop",
            "lifecycle_intent_id": "private-operation",
            "lifecycle_intent_status": "prepared",
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

    def test_public_participant_removes_moderation_workflow_state(self):
        participant = {
            "participant_id": "agent-1",
            "display_name": "Agent One",
            "status": "joined",
            "moderation_intent_action": "kick",
            "moderation_intent_id": "private-operation",
            "moderation_intent_status": "effect_applied",
            "moderation_intent_cleanup_warning": "private cleanup detail",
            "moderation_intent_removed_member": True,
            "moderation_intent_revoked_sessions": 2,
        }

        self.assertEqual(
            public_participant(participant),
            {
                "participant_id": "agent-1",
                "display_name": "Agent One",
                "status": "joined",
            },
        )

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
        self.assertEqual(public_activity("compaction", "started"), ("압축 중...", "compaction"))
        self.assertEqual(public_activity("command", "completed"), ("명령 실행 완료", "tool"))
        self.assertEqual(
            merged_latency(
                {"ttfo_ms": 120, "total_turn_ms": 500},
                {"ttfo_ms": None, "total_turn_ms": 650, "stream_ms": 200},
            ),
            {"ttfo_ms": 120, "total_turn_ms": 650, "stream_ms": 200},
        )

    def test_activity_detail_redacts_credentials_and_local_paths(self):
        detail = (
            'curl -u user:pass https://alice:hunter2@example.test '
            '-d \'{"password":"hunter2"}\' '
            'C:/Users/alice/private.txt /Users/alice/private.txt'
        )

        safe = safe_activity_detail(detail)

        self.assertNotIn("user:pass", safe)
        self.assertNotIn("alice:hunter2", safe)
        self.assertNotIn("hunter2", safe)
        self.assertNotIn("C:/Users/alice", safe)
        self.assertNotIn("/Users/alice", safe)
        self.assertIn("-u [redacted]", safe)
        self.assertIn("https://[redacted]@example.test", safe)
        self.assertEqual(safe.count("[local path]"), 2)

    def test_activity_detail_names_virtual_room_paths_without_exposing_local_paths(self):
        safe = safe_activity_detail(
            "Read /agentsassemble-room/current.md then write "
            "/agentsassemble-room/outbox.txt"
        )

        self.assertEqual(
            safe,
            "Read [room/current.md] then write [room/outbox.txt]",
        )


if __name__ == "__main__":
    unittest.main()
