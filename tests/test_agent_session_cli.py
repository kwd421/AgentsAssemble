import argparse
import unittest
from unittest.mock import patch
from io import StringIO
from pathlib import Path

from agentsassemble.cli import build_parser, main
from agentsassemble.application.cli.room_commands import (
    RoomCliRuntime,
    run_room_command,
)


class AgentSessionCliTests(unittest.TestCase):
    def _runtime(
        self,
        *,
        request_json=None,
        run_codex_smoke=None,
        run_native_smoke=None,
    ) -> RoomCliRuntime:
        def unexpected(*args, **kwargs):
            raise AssertionError(f"unexpected runtime call: {args!r} {kwargs!r}")

        return RoomCliRuntime(
            request_json=request_json or unexpected,
            server_url=lambda server, path: f"{server.rstrip('/')}{path}",
            clean_text=lambda value, limit=0: str(value or "")[:limit or None],
            run_codex_smoke=run_codex_smoke or unexpected,
            run_native_smoke=run_native_smoke or unexpected,
            codex_smoke_commands={
                "codex-app-server-same-profile",
                "codex-app-server-profile-isolation",
                "codex-app-server-restart-recovery",
                "codex-app-server-stderr-backpressure",
            },
            default_live_cli_smoke_config=Path("configs/live-cli-providers.example.json"),
        )

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
        with patch("sys.stderr", stderr):
            exit_code = main(["live-agent", "flow", "--meeting-id", "m1", "--topic", "t"])

        self.assertEqual(exit_code, 2)
        self.assertIn("legacy/internal", stderr.getvalue())

    def test_live_agent_session_resume_without_internal_flag_is_disabled(self):
        stderr = StringIO()
        with patch("sys.stderr", stderr):
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

        calls = []

        def request_json(*call_args, **call_kwargs):
            calls.append((call_args, call_kwargs))
            return {"status": "resumed", "participant": {"participant_id": "agent-1"}}

        with patch("sys.stdout", StringIO()):
            exit_code = run_room_command(args, runtime=self._runtime(request_json=request_json))

        self.assertEqual(exit_code, 0)
        self.assertEqual(calls[0][0][0], "http://127.0.0.1:8765/api/agent-sessions/resume")
        self.assertEqual(calls[0][1]["method"], "POST")
        self.assertEqual(
            calls[0][1]["payload"],
            {
                "room_id": "room-a",
                "agent_id": "agent-1",
                "session_id": "session-1",
                "provider_session_id": "",
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

        calls = []

        def request_json(*call_args, **call_kwargs):
            calls.append((call_args, call_kwargs))
            return {
                "state_status": "resumed",
                "process_status": "not_started",
                "participant": {"participant_id": "agent-1"},
            }

        with patch("sys.stdout", StringIO()):
            exit_code = run_room_command(args, runtime=self._runtime(request_json=request_json))

        self.assertEqual(exit_code, 0)
        self.assertEqual(calls[0][1]["payload"]["provider_kind"], "codex_live_session")

    def test_room_leave_uses_persisted_participant_endpoint(self):
        args = argparse.Namespace(
            room_command="leave",
            room_id="room-a",
            agent="agent-1",
            server="http://127.0.0.1:8765",
            as_json=True,
        )

        calls = []

        def request_json(*call_args, **call_kwargs):
            calls.append((call_args, call_kwargs))
            return {"status": "left"}

        with patch("sys.stdout", StringIO()):
            exit_code = run_room_command(args, runtime=self._runtime(request_json=request_json))

        self.assertEqual(exit_code, 0)
        self.assertEqual(calls[0][0][0], "http://127.0.0.1:8765/api/room-participants/leave")
        self.assertEqual(calls[0][1]["method"], "POST")
        self.assertEqual(
            calls[0][1]["payload"],
            {"room_id": "room-a", "participant_id": "agent-1"},
        )

    def test_room_smoke_profile_matrix_commands_are_opt_in(self):
        parser = build_parser()
        for smoke_name in (
            "codex-app-server-same-profile",
            "codex-app-server-profile-isolation",
            "codex-app-server-restart-recovery",
            "codex-app-server-stderr-backpressure",
        ):
            with self.subTest(smoke_name=smoke_name):
                args = parser.parse_args(["room", "smoke", smoke_name, "--json"])
                stdout = StringIO()
                with patch("sys.stdout", stdout):
                    exit_code = run_room_command(args, runtime=self._runtime())

                self.assertEqual(exit_code, 0)
                self.assertIn('"status": "skipped"', stdout.getvalue())

    def test_room_smoke_profile_matrix_dispatches_approved_runner(self):
        args = build_parser().parse_args(
            [
                "room",
                "smoke",
                "codex-app-server-same-profile",
                "--approve-real-provider",
                "--json",
            ]
        )

        calls = []

        def smoke_runner(*call_args, **call_kwargs):
            calls.append((call_args, call_kwargs))
            return {
                "status": "ok",
                "smoke": "codex-app-server-same-profile",
                "metrics": {"runtime_profile_key": ["profile-a"]},
            }

        stdout = StringIO()
        with patch("sys.stdout", stdout):
            exit_code = run_room_command(
                args,
                runtime=self._runtime(run_codex_smoke=smoke_runner),
            )

        self.assertEqual(exit_code, 0)
        self.assertEqual(
            calls,
            [(('codex-app-server-same-profile',), {"approve_real_provider": True})],
        )
        self.assertIn('"status": "ok"', stdout.getvalue())

    def test_room_smoke_legacy_approved_stub_does_not_dispatch_app_server_runner(self):
        args = build_parser().parse_args(
            [
                "room",
                "smoke",
                "fresh-codex",
                "--approve-real-provider",
                "--json",
            ]
        )

        stdout = StringIO()
        with patch("sys.stdout", stdout):
            exit_code = run_room_command(args, runtime=self._runtime())

        self.assertEqual(exit_code, 0)
        self.assertIn('"status": "not_run"', stdout.getvalue())

    def test_room_smoke_live_cli_dispatches_command_config_harness(self):
        args = build_parser().parse_args(
            [
                "room",
                "smoke",
                "--providers",
                "codex,grok",
                "--config",
                "configs/live-cli-providers.example.json",
                "--approve-real-provider",
                "--latency-samples",
                "10",
                "--agent-conversation",
                "--conversation-seconds",
                "300",
                "--conversation-topic",
                "haunted station",
                "--verify-controls",
                "--observe-gui-port",
                "8765",
                "--json",
            ]
        )

        calls = []

        def smoke_runner(*call_args, **call_kwargs):
            calls.append((call_args, call_kwargs))
            return {
                "status": "ok",
                "providers": [{"agent_id": "codex", "status": "ok"}],
            }

        stdout = StringIO()
        with patch("sys.stdout", stdout):
            exit_code = run_room_command(
                args,
                runtime=self._runtime(run_native_smoke=smoke_runner),
            )

        self.assertEqual(exit_code, 0)
        self.assertEqual(
            calls[0][1],
            {
                "config_path": "configs/live-cli-providers.example.json",
                "providers": ["codex", "grok"],
                "approve_real_provider": True,
                "timeout_seconds": 120.0,
                "latency_samples": 10,
                "agent_conversation": True,
                "conversation_seconds": 300.0,
                "conversation_topic": "haunted station",
                "verify_controls": True,
                "observe_gui_port": 8765,
            },
        )
        self.assertIn('"status": "ok"', stdout.getvalue())

    def test_room_smoke_returns_failure_exit_code_for_real_provider_error(self):
        args = build_parser().parse_args(
            ["room", "smoke", "--providers", "grok", "--approve-real-provider", "--json"]
        )

        with patch("sys.stdout", StringIO()):
            exit_code = run_room_command(
                args,
                runtime=self._runtime(
                    run_native_smoke=lambda **kwargs: {
                        "status": "error",
                        "smoke": "room-native-cli",
                        "providers": [],
                    }
                ),
            )

        self.assertEqual(exit_code, 1)


if __name__ == "__main__":
    unittest.main()
