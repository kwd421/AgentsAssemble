import json
import tempfile
import unittest
from pathlib import Path

from agentsassemble.application.agent_sessions import (
    resume_agent_session_payload,
    run_agent_session_turn_payload,
)
from agentsassemble.persistence.local.room.repository import RoomStore


class RuntimeDiagnosticPersistenceTests(unittest.TestCase):
    def test_failed_agent_turn_redacts_nested_provider_credentials_before_persistence(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            output_root = Path(temp_dir)
            store = RoomStore(output_root)
            store.create_room("room-a")
            resume_agent_session_payload(
                output_root,
                {
                    "room_id": "room-a",
                    "agent_id": "agent-a",
                    "session_id": "session-a",
                    "provider_kind": "codex_live_session",
                },
                repository=store,
            )
            failed = run_agent_session_turn_payload(
                output_root,
                {
                    "room_id": "room-a",
                    "agent_id": "agent-a",
                    "session_id": "session-a",
                    "instruction": "Fail.",
                },
                turn_runner=lambda _packet: [
                    {
                        "type": "error",
                        "diagnostics": [
                            {
                                "setting": "stderr",
                                "status": "failed",
                                "message": "api_key=agent-turn-diagnostic-secret",
                                "credential_details": {
                                    "token": "opaque-provider-token"
                                },
                            }
                        ],
                    }
                ],
                repository=store,
            )
            durable_state = json.dumps(
                {
                    "session": RoomStore(output_root).session("room-a", "session-a"),
                    "events": RoomStore(output_root).read_events("room-a"),
                },
                ensure_ascii=False,
            )
            response = json.dumps(failed, ensure_ascii=False)

        for secret in ("agent-turn-diagnostic-secret", "opaque-provider-token"):
            self.assertNotIn(secret, durable_state)
            self.assertNotIn(secret, response)


if __name__ == "__main__":
    unittest.main()
