import argparse
import unittest
from unittest.mock import patch

from agentsassemble.cli import build_parser, run_room_command


class AgentSessionCliTests(unittest.TestCase):
    def test_room_resume_uses_agent_session_resume_endpoint(self):
        args = build_parser().parse_args(
            [
                "room",
                "resume",
                "room-a",
                "--agent",
                "agent-1",
                "--session",
                "session-1",
                "--server",
                "http://127.0.0.1:8765",
                "--model",
                "gpt-5.5",
                "--effort",
                "high",
                "--sandbox",
                "read-only",
                "--permissions",
                "prompt",
                "--json",
            ]
        )

        with patch("agentsassemble.cli._request_json") as request_json, patch("builtins.print"):
            request_json.return_value = {"status": "resumed", "participant": {"participant_id": "agent-1"}}
            exit_code = run_room_command(args)

        self.assertEqual(exit_code, 0)
        self.assertEqual(request_json.call_args.args[0], "http://127.0.0.1:8765/api/agent-sessions/resume")
        self.assertEqual(request_json.call_args.kwargs["method"], "POST")
        self.assertEqual(
            request_json.call_args.kwargs["payload"],
            {
                "room_id": "room-a",
                "agent_id": "agent-1",
                "session_id": "session-1",
                "model": "gpt-5.5",
                "effort": "high",
                "sandbox": "read-only",
                "permissions": "prompt",
            },
        )

    def test_room_leave_uses_persisted_participant_endpoint(self):
        args = argparse.Namespace(
            room_command="leave",
            room_id="room-a",
            agent="agent-1",
            server="http://127.0.0.1:8765",
            as_json=True,
        )

        with patch("agentsassemble.cli._request_json") as request_json, patch("builtins.print"):
            request_json.return_value = {"status": "left"}
            exit_code = run_room_command(args)

        self.assertEqual(exit_code, 0)
        self.assertEqual(request_json.call_args.args[0], "http://127.0.0.1:8765/api/room-participants/leave")
        self.assertEqual(request_json.call_args.kwargs["method"], "POST")
        self.assertEqual(
            request_json.call_args.kwargs["payload"],
            {"room_id": "room-a", "participant_id": "agent-1"},
        )


if __name__ == "__main__":
    unittest.main()
