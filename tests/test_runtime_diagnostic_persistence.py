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
        jwt = (
            "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
            "eyJzdWIiOiJkaWFnbm9zdGljLXVzZXIifQ."
            "m7p4g8h2k6n9q3s5v1x8z0a2c4e6g8i0"
        )
        pem_secret = "diagnostic-private-key-material"
        cookie_secret = "diagnostic-session-cookie"
        refresh_cookie_secret = "diagnostic-refresh-cookie"
        google_key = "AIza" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r"
        aws_key = "AKIA" + "A1B2C3D4E5F6G7H8"
        slack_key = "xoxb-12345678901234567890"
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
                                "message": (
                                    "api_key=agent-turn-diagnostic-secret\n"
                                    f"opaque JWT {jwt}\n"
                                    "-----BEGIN PRIVATE KEY-----\n"
                                    f"{pem_secret}\n"
                                    "-----END PRIVATE KEY-----\n"
                                    f"Cookie: session={cookie_secret}; theme=dark\n"
                                    f"Set-Cookie: refresh={refresh_cookie_secret}; HttpOnly; Secure\n"
                                    f"bare credentials {google_key} {aws_key} {slack_key}"
                                ),
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

        for secret in (
            "agent-turn-diagnostic-secret",
            "opaque-provider-token",
            jwt,
            pem_secret,
            cookie_secret,
            refresh_cookie_secret,
            google_key,
            aws_key,
            slack_key,
        ):
            self.assertNotIn(secret, durable_state)
            self.assertNotIn(secret, response)


if __name__ == "__main__":
    unittest.main()
