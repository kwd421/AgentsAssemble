import unittest
import json
import os
import urllib.error
from io import StringIO
from unittest.mock import patch

from agentsassemble import cli as cli_module
from agentsassemble.cli import build_parser, main




class CliTimeoutDelegateTests(unittest.TestCase):

    def test_live_agent_delegate_runs_local_command_and_posts_reply(self):
        stdout = StringIO()
        room_payload = {
            "lobby_events": [
                {
                    "id": "evt-human",
                    "name": "나",
                    "message": "방 상태 어때?",
                    "auto_chain_depth": 2,
                }
            ]
        }
        responses = [
            {"agent": {"agent_id": "claude-code-live"}},
            {"agent": {"agent_id": "claude-code-live", "status": "working"}},
            room_payload,
            {"event": {"id": "evt1"}},
            {"agent": {"agent_id": "claude-code-live", "status": "online"}},
        ]
        with patch("agentsassemble.cli._request_json", side_effect=responses) as request_json:
            with patch("agentsassemble.cli._run_delegate_command", return_value="Claude Code Live 응답") as run_delegate:
                with patch("sys.stdout", stdout):
                    exit_code = main(
                        [
                            "live-agent",
                            "delegate",
                            "--server",
                            "http://room.local",
                            "--agent-id",
                            "claude-code-live",
                            "--display-name",
                            "Claude Code Live",
                            "--provider-kind",
                            "claude_code",
                            "--command",
                            "claude",
                            "-p",
                        ]
                    )

        self.assertEqual(exit_code, 0)
        self.assertEqual(request_json.call_args_list[0].args[0], "http://room.local/api/live-agents")
        self.assertEqual(
            request_json.call_args_list[2].args[0],
            "http://room.local/api/live-agents/claude-code-live/room",
        )
        self.assertEqual(
            request_json.call_args_list[3].kwargs["payload"],
            {
                "message": "Claude Code Live 응답",
                "kind": "message",
                "source_event_id": "evt-human",
                "auto_chain_depth": 3,
            },
        )
        run_delegate.assert_called_once()
        self.assertEqual(run_delegate.call_args.args[0], ["claude", "-p"])
        self.assertIn("방 상태 어때?", run_delegate.call_args.args[1])
        self.assertIn("AgentsAssemble", run_delegate.call_args.args[1])
        self.assertNotIn("AgentCouncil", run_delegate.call_args.args[1])
        self.assertIn("Posted evt1", stdout.getvalue())

    def test_live_agent_delegate_does_not_link_reply_to_self_event(self):
        stdout = StringIO()
        room_payload = {
            "agent": {"agent_id": "claude-code-live", "last_observed_event_id": "evt-human"},
            "lobby_events": [
                {"id": "evt-human", "name": "나", "message": "방 상태 어때?"},
                {
                    "id": "evt-self",
                    "name": "Claude Code Live",
                    "actor_id": "claude-code-live",
                    "message": "이미 답한 내용",
                    "auto_chain_depth": 1,
                },
            ]
        }
        responses = [
            {"agent": {"agent_id": "claude-code-live"}},
            {"agent": {"agent_id": "claude-code-live", "status": "working"}},
            room_payload,
            {"event": {"id": "evt2"}},
            {"agent": {"agent_id": "claude-code-live", "status": "online"}},
        ]
        with patch("agentsassemble.cli._request_json", side_effect=responses) as request_json:
            with patch("agentsassemble.cli._run_delegate_command", return_value="Claude Code Live 응답"):
                with patch("sys.stdout", stdout):
                    exit_code = main(
                        [
                            "live-agent",
                            "delegate",
                            "--server",
                            "http://room.local",
                            "--agent-id",
                            "claude-code-live",
                            "--display-name",
                            "Claude Code Live",
                            "--provider-kind",
                            "claude_code",
                            "--command",
                            "claude",
                            "-p",
                        ]
                    )

        self.assertEqual(exit_code, 0)
        self.assertEqual(
            request_json.call_args_list[3].kwargs["payload"],
            {"message": "Claude Code Live 응답", "kind": "message"},
        )
        self.assertIn("Posted evt2", stdout.getvalue())

    def test_live_agent_delegate_links_reply_to_unobserved_event_after_cursor(self):
        stdout = StringIO()
        room_payload = {
            "agent": {"agent_id": "claude-code-live", "last_observed_event_id": "evt-old"},
            "lobby_events": [
                {"id": "evt-old", "name": "나", "message": "이전 질문", "auto_chain_depth": 0},
                {"id": "evt-new", "name": "나", "message": "새 질문", "auto_chain_depth": 1},
            ],
        }
        responses = [
            {"agent": {"agent_id": "claude-code-live"}},
            {"agent": {"agent_id": "claude-code-live", "status": "working"}},
            room_payload,
            {"event": {"id": "reply-new"}},
            {"agent": {"agent_id": "claude-code-live", "status": "online"}},
        ]
        with patch("agentsassemble.cli._request_json", side_effect=responses) as request_json:
            with patch("agentsassemble.cli._run_delegate_command", return_value="새 질문 응답"):
                with patch("sys.stdout", stdout):
                    exit_code = main(
                        [
                            "live-agent",
                            "delegate",
                            "--server",
                            "http://room.local",
                            "--agent-id",
                            "claude-code-live",
                            "--display-name",
                            "Claude Code Live",
                            "--provider-kind",
                            "claude_code",
                            "--command",
                            "claude",
                            "-p",
                        ]
                    )

        self.assertEqual(exit_code, 0)
        self.assertEqual(
            request_json.call_args_list[3].kwargs["payload"],
            {
                "message": "새 질문 응답",
                "kind": "message",
                "source_event_id": "evt-new",
                "auto_chain_depth": 2,
            },
        )
        self.assertIn("Posted reply-new", stdout.getvalue())

    def test_live_agent_delegate_records_error_heartbeat_on_command_failure(self):
        stderr = StringIO()
        room_payload = {"lobby_events": [{"id": "evt-human", "name": "나", "message": "방 상태 어때?"}]}
        responses = [
            {"agent": {"agent_id": "claude-code-live"}},
            {"agent": {"agent_id": "claude-code-live", "status": "working"}},
            room_payload,
            {"agent": {"agent_id": "claude-code-live", "status": "error"}},
        ]
        command_error = cli_module.subprocess.CalledProcessError(
            7,
            ["claude", "-p"],
            output="private stdout",
            stderr="private stderr",
        )
        with patch("agentsassemble.cli._request_json", side_effect=responses) as request_json:
            with patch("agentsassemble.cli._run_delegate_command", side_effect=command_error):
                with patch("sys.stderr", stderr):
                    exit_code = main(
                        [
                            "live-agent",
                            "delegate",
                            "--server",
                            "http://room.local",
                            "--agent-id",
                            "claude-code-live",
                            "--display-name",
                            "Claude Code Live",
                            "--provider-kind",
                            "claude_code",
                            "--command",
                            "claude",
                            "-p",
                        ]
                    )

        self.assertEqual(exit_code, 2)
        self.assertEqual(len(request_json.call_args_list), 4)
        self.assertEqual(
            request_json.call_args_list[3].args[0],
            "http://room.local/api/live-agents/claude-code-live/heartbeat",
        )
        self.assertEqual(
            request_json.call_args_list[3].kwargs["payload"],
            {"status": "error", "last_error": "Delegate command exited with return code 7."},
        )
        self.assertIn("returned non-zero exit status 7", stderr.getvalue())

    def test_live_agent_delegate_redacts_os_error_path_from_error_heartbeat(self):
        stderr = StringIO()
        room_payload = {"lobby_events": [{"id": "evt-human", "name": "나", "message": "방 상태 어때?"}]}
        responses = [
            {"agent": {"agent_id": "claude-code-live"}},
            {"agent": {"agent_id": "claude-code-live", "status": "working"}},
            room_payload,
            {"agent": {"agent_id": "claude-code-live", "status": "error"}},
        ]
        command_error = OSError(2, "No such file or directory", "/private/token/claude")
        with patch("agentsassemble.cli._request_json", side_effect=responses) as request_json:
            with patch("agentsassemble.cli._run_delegate_command", side_effect=command_error):
                with patch("sys.stderr", stderr):
                    exit_code = main(
                        [
                            "live-agent",
                            "delegate",
                            "--server",
                            "http://room.local",
                            "--agent-id",
                            "claude-code-live",
                            "--display-name",
                            "Claude Code Live",
                            "--provider-kind",
                            "claude_code",
                            "--command",
                            "claude",
                            "-p",
                        ]
                    )

        self.assertEqual(exit_code, 2)
        self.assertEqual(
            request_json.call_args_list[3].kwargs["payload"],
            {"status": "error", "last_error": "Delegate command failed: No such file or directory."},
        )
        self.assertNotIn("/private/token/claude", request_json.call_args_list[3].kwargs["payload"]["last_error"])
        self.assertIn("/private/token/claude", stderr.getvalue())

    def test_live_agent_delegate_records_error_heartbeat_on_empty_reply(self):
        stderr = StringIO()
        room_payload = {"lobby_events": [{"id": "evt-human", "name": "나", "message": "방 상태 어때?"}]}
        responses = [
            {"agent": {"agent_id": "claude-code-live"}},
            {"agent": {"agent_id": "claude-code-live", "status": "working"}},
            room_payload,
            {"agent": {"agent_id": "claude-code-live", "status": "error"}},
        ]
        with patch("agentsassemble.cli._request_json", side_effect=responses) as request_json:
            with patch("agentsassemble.cli._run_delegate_command", return_value="  \n"):
                with patch("sys.stderr", stderr):
                    exit_code = main(
                        [
                            "live-agent",
                            "delegate",
                            "--server",
                            "http://room.local",
                            "--agent-id",
                            "claude-code-live",
                            "--display-name",
                            "Claude Code Live",
                            "--provider-kind",
                            "claude_code",
                            "--command",
                            "claude",
                            "-p",
                        ]
                    )

        self.assertEqual(exit_code, 2)
        self.assertEqual(len(request_json.call_args_list), 4)
        self.assertEqual(
            request_json.call_args_list[3].kwargs["payload"],
            {"status": "error", "last_error": "Delegate command returned an empty reply."},
        )
        self.assertIn("Delegate command returned an empty reply.", stderr.getvalue())

    def test_live_agent_delegate_records_error_heartbeat_on_command_timeout(self):
        stderr = StringIO()
        room_payload = {"lobby_events": [{"id": "evt-human", "name": "나", "message": "방 상태 어때?"}]}
        responses = [
            {"agent": {"agent_id": "claude-code-live"}},
            {"agent": {"agent_id": "claude-code-live", "status": "working"}},
            room_payload,
            {"agent": {"agent_id": "claude-code-live", "status": "error"}},
        ]
        command_error = cli_module.subprocess.TimeoutExpired(
            ["claude", "-p"],
            9,
            output="private stdout",
            stderr="private stderr",
        )
        with patch("agentsassemble.cli._request_json", side_effect=responses) as request_json:
            with patch("agentsassemble.cli._run_delegate_command", side_effect=command_error):
                with patch("sys.stderr", stderr):
                    exit_code = main(
                        [
                            "live-agent",
                            "delegate",
                            "--server",
                            "http://room.local",
                            "--agent-id",
                            "claude-code-live",
                            "--display-name",
                            "Claude Code Live",
                            "--provider-kind",
                            "claude_code",
                            "--command",
                            "claude",
                            "-p",
                        ]
                    )

        self.assertEqual(exit_code, 2)
        self.assertEqual(
            request_json.call_args_list[3].kwargs["payload"],
            {"status": "error", "last_error": "Delegate command timed out after 9 seconds."},
        )
        self.assertNotIn("private stdout", request_json.call_args_list[3].kwargs["payload"]["last_error"])
        self.assertNotIn("private stderr", request_json.call_args_list[3].kwargs["payload"]["last_error"])
        self.assertIn("timed out after 9 seconds", stderr.getvalue())

    def test_live_agent_delegate_error_heartbeat_failure_does_not_mask_command_failure(self):
        stderr = StringIO()
        room_payload = {"lobby_events": [{"id": "evt-human", "name": "나", "message": "방 상태 어때?"}]}
        command_error = cli_module.subprocess.CalledProcessError(7, ["claude", "-p"])
        responses = [
            {"agent": {"agent_id": "claude-code-live"}},
            {"agent": {"agent_id": "claude-code-live", "status": "working"}},
            room_payload,
            urllib.error.URLError("heartbeat down"),
        ]
        with patch("agentsassemble.cli._request_json", side_effect=responses) as request_json:
            with patch("agentsassemble.cli._run_delegate_command", side_effect=command_error):
                with patch("sys.stderr", stderr):
                    exit_code = main(
                        [
                            "live-agent",
                            "delegate",
                            "--server",
                            "http://room.local",
                            "--agent-id",
                            "claude-code-live",
                            "--display-name",
                            "Claude Code Live",
                            "--provider-kind",
                            "claude_code",
                            "--command",
                            "claude",
                            "-p",
                        ]
                    )

        self.assertEqual(exit_code, 2)
        self.assertEqual(len(request_json.call_args_list), 4)
        self.assertEqual(
            request_json.call_args_list[3].kwargs["payload"],
            {"status": "error", "last_error": "Delegate command exited with return code 7."},
        )
        self.assertIn("returned non-zero exit status 7", stderr.getvalue())
        self.assertNotIn("heartbeat down", stderr.getvalue())

    def test_live_agent_lan_invite_create_and_verify_round_trip(self):
        args = build_parser().parse_args(
            [
                "live-agent",
                "lan-invite",
                "create",
                "--server",
                "https://192.168.1.50:8765",
                "--meeting-id",
                "resident-m1",
                "--agent-id",
                "friend-claude",
                "--display-name",
                "Friend Claude",
                "--provider-kind",
                "claude_code",
                "--secret-ref",
                "env:LAN_INVITE_SECRET",
                "--ttl-seconds",
                "60",
                "--json",
            ]
        )

        self.assertEqual(args.live_agent_command, "lan-invite")
        self.assertEqual(args.lan_invite_command, "create")
        self.assertEqual(args.server, "https://192.168.1.50:8765")
        self.assertEqual(args.agent_id, "friend-claude")
        self.assertEqual(args.secret_ref, "env:LAN_INVITE_SECRET")
        self.assertEqual(args.ttl_seconds, 60)

        create_stdout = StringIO()
        with patch.dict(os.environ, {"LAN_INVITE_SECRET": "test-secret"}):
            with patch("sys.stdout", create_stdout):
                exit_code = main(
                    [
                        "live-agent",
                        "lan-invite",
                        "create",
                        "--server",
                        "https://192.168.1.50:8765",
                        "--meeting-id",
                        "resident-m1",
                        "--agent-id",
                        "friend-claude",
                        "--display-name",
                        "Friend Claude",
                        "--provider-kind",
                        "claude_code",
                        "--secret-ref",
                        "env:LAN_INVITE_SECRET",
                        "--ttl-seconds",
                        "60",
                        "--json",
                    ]
                )

        self.assertEqual(exit_code, 0)
        packet = json.loads(create_stdout.getvalue())
        self.assertEqual(packet["client_kind"], "native_remote_room_client")
        self.assertEqual(packet["admission"]["provider_execution"], "not_started_by_invite")
        self.assertNotIn("test-secret", create_stdout.getvalue())

        verify_stdout = StringIO()
        with patch.dict(os.environ, {"LAN_INVITE_SECRET": "test-secret"}):
            with patch("sys.stdout", verify_stdout):
                exit_code = main(
                    [
                        "live-agent",
                        "lan-invite",
                        "verify",
                        "--token",
                        packet["token"],
                        "--secret-ref",
                        "env:LAN_INVITE_SECRET",
                        "--expected-meeting-id",
                        "resident-m1",
                        "--expected-agent-id",
                        "friend-claude",
                        "--json",
                    ]
                )

        self.assertEqual(exit_code, 0)
        verified = json.loads(verify_stdout.getvalue())
        self.assertEqual(verified["status"], "ok")
        self.assertEqual(verified["claims"]["agent"]["agent_id"], "friend-claude")

    def test_live_agent_delegate_rejects_live_session_connection_kind(self):
        stderr = StringIO()
        with patch("sys.stderr", stderr):
            with self.assertRaises(SystemExit) as raised:
                build_parser().parse_args(
                    [
                        "live-agent",
                        "delegate",
                        "--agent-id",
                        "jsonl-session",
                        "--connection-kind",
                        "live_session",
                        "--command",
                        "python3",
                        "-u",
                        "session.py",
                    ]
                )

        self.assertEqual(raised.exception.code, 2)
        self.assertIn("invalid choice", stderr.getvalue())
