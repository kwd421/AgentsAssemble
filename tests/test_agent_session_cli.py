import argparse
import unittest
from pathlib import Path
from unittest.mock import patch
from io import StringIO

from agentsassemble.cli import build_parser, main, run_room_command


class AgentSessionCliTests(unittest.TestCase):
    def test_demo_free_chat_mode_is_rejected(self):
        with patch("sys.stderr", new_callable=StringIO), self.assertRaises(SystemExit):
            build_parser().parse_args(["demo", "--meeting-mode", "free-chat"])

    def test_default_help_exposes_agent_session_not_legacy_connection_paths(self):
        help_text = build_parser().format_help()

        self.assertIn("Agent Session", help_text)
        self.assertIn("room", help_text)
        self.assertNotIn("mcp", help_text)
        self.assertNotIn("live-agent", help_text)
        self.assertNotIn("claude-bridge", help_text)
        self.assertNotIn("sessions", help_text)

    def test_mcp_serve_without_internal_flag_is_disabled(self):
        stderr = StringIO()
        with patch("sys.stderr", stderr):
            exit_code = main(["mcp", "serve", "--profile", "participant"])

        self.assertEqual(exit_code, 2)
        self.assertIn("legacy/internal", stderr.getvalue())

    def test_live_agent_flow_without_internal_flag_is_disabled_before_http(self):
        stderr = StringIO()
        with patch("sys.stderr", stderr), patch("agentsassemble.cli._request_json") as request_json:
            exit_code = main(["live-agent", "flow", "--meeting-id", "m1", "--topic", "t"])

        self.assertEqual(exit_code, 2)
        self.assertIn("legacy/internal", stderr.getvalue())
        request_json.assert_not_called()

    def test_live_agent_session_resume_without_internal_flag_is_disabled(self):
        stderr = StringIO()
        with patch("sys.stderr", stderr), patch("agentsassemble.cli._request_json") as request_json:
            exit_code = main(
                [
                    "live-agent",
                    "resume-session",
                    "--meeting-id",
                    "m1",
                    "--live-agent-config",
                    "agents.json",
                ]
            )

        self.assertEqual(exit_code, 2)
        self.assertIn("legacy/internal", stderr.getvalue())
        request_json.assert_not_called()

    def test_readme_quickstart_does_not_instruct_legacy_connection_choices(self):
        readme = Path("README.md").read_text(encoding="utf-8")
        live_room = readme.split("## Live Room Status", 1)[1].split("## Codex Adapter", 1)[0]

        self.assertIn("Agent Session", live_room)
        self.assertIn("assemble room resume", live_room)
        self.assertNotIn(" live-agent ", live_room)
        self.assertNotIn("assemble mcp", live_room.lower())
        self.assertNotIn("local_cli", live_room)
        self.assertNotIn("live_session", live_room)
        self.assertNotIn("remote bridge participants", live_room)

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
                "--provider",
                "codex",
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
                "provider_kind": "codex_live_session",
                "start": False,
                "dry_run": False,
            },
        )

    def test_room_resume_maps_provider_kind_alias(self):
        args = build_parser().parse_args(
            [
                "room",
                "resume",
                "room-a",
                "--agent",
                "agent-1",
                "--provider-kind",
                "codex_live_session",
            ]
        )

        with patch("agentsassemble.cli._request_json") as request_json, patch("builtins.print"):
            request_json.return_value = {
                "state_status": "resumed",
                "process_status": "not_started",
                "participant": {"participant_id": "agent-1"},
            }
            exit_code = run_room_command(args)

        self.assertEqual(exit_code, 0)
        self.assertEqual(request_json.call_args.kwargs["payload"]["provider_kind"], "codex_live_session")

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
