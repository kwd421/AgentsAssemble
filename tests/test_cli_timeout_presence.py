import unittest
import json
import tempfile
import threading
import urllib.parse
from pathlib import Path
from http.server import ThreadingHTTPServer
from contextlib import redirect_stdout
from io import StringIO
from unittest.mock import patch

from agentsassemble import cli as cli_module
from agentsassemble.cli import build_parser, main
from agentsassemble.gui import _make_handler


class CliTimeoutPresenceTests(unittest.TestCase):

    def test_live_agent_local_resources_parser(self):
        args = build_parser().parse_args(
            ["live-agent", "local-resources", "--server", "http://room.local", "--json"]
        )

        self.assertEqual(args.live_agent_command, "local-resources")
        self.assertEqual(args.server, "http://room.local")
        self.assertTrue(args.as_json)

    def test_live_agent_local_resources_prints_json_without_mutating_room(self):
        payload = {
            "status": "ok",
            "summary": {"process_count": 1, "total_cpu_pct": 2.5, "total_rss_kb": 4096, "attention": []},
            "processes": [{"pid": 123, "comm": "python3", "role": "agentsassemble", "cpu_pct": 2.5, "rss_kb": 4096}],
        }
        stdout = StringIO()
        with patch.object(cli_module, "_request_json", return_value=payload) as request_json:
            with redirect_stdout(stdout):
                exit_code = main(["live-agent", "local-resources", "--server", "http://room.local", "--json"])

        self.assertEqual(exit_code, 0)
        request_json.assert_called_once_with("http://room.local/api/local-resources")
        self.assertEqual(json.loads(stdout.getvalue()), payload)

    def test_live_agent_local_resources_fail_on_degraded(self):
        payload = {
            "status": "degraded",
            "summary": {"process_count": 0, "total_cpu_pct": 0.0, "total_rss_kb": 0, "attention": ["load_average_high"]},
            "processes": [],
        }
        stdout = StringIO()
        with patch.object(cli_module, "_request_json", return_value=payload):
            with redirect_stdout(stdout):
                exit_code = main(["live-agent", "local-resources", "--fail-on-degraded"])

        self.assertEqual(exit_code, 1)
        self.assertIn("local resources: degraded", stdout.getvalue())

    def test_live_agent_register_posts_connection_payload(self):
        stdout = StringIO()
        with patch("agentsassemble.cli._request_json", return_value={"agent": {"agent_id": "claude-code-live"}}) as request_json:
            with patch("sys.stdout", stdout):
                exit_code = main(
                    [
                        "live-agent",
                        "register",
                        "--server",
                        "http://room.local",
                        "--agent-id",
                        "claude-code-live",
                        "--display-name",
                        "Claude Code Live",
                        "--provider-kind",
                        "claude_code",
                        "--connection-kind",
                        "local_cli",
                    ]
                )

        self.assertEqual(exit_code, 0)
        request_json.assert_called_once_with(
            "http://room.local/api/live-agents",
            method="POST",
            payload={
                "agent_id": "claude-code-live",
                "display_name": "Claude Code Live",
                "provider_kind": "claude_code",
                "connection_kind": "local_cli",
                "session_id": "",
                "endpoint": "",
                "meeting_id": "",
                "engagement_mode": "mentioned",
                "capabilities": ["room_chat", "mentions"],
            },
        )
        self.assertIn("claude-code-live", stdout.getvalue())

    def test_live_agent_register_json_prints_registration_acknowledgement(self):
        stdout = StringIO()
        response = {
            "status": "registered",
            "agent": {
                "agent_id": "claude-code-live",
                "status": "online",
                "meeting_id": "server-m1",
                "session_id": "server-session-1",
                "engagement_mode": "manual",
            },
            "server_clock": "2026-05-19T00:00:00+00:00",
        }
        with patch("agentsassemble.cli._request_json", return_value=response) as request_json:
            with patch("sys.stdout", stdout):
                exit_code = main(
                    [
                        "live-agent",
                        "register",
                        "--server",
                        "http://room.local",
                        "--agent-id",
                        "claude-code-live",
                        "--display-name",
                        "Claude Code Live",
                        "--provider-kind",
                        "claude_code",
                        "--connection-kind",
                        "local_cli",
                        "--meeting-id",
                        "m1",
                        "--session-id",
                        "session-1",
                        "--engagement-mode",
                        "watch",
                        "--json",
                    ]
                )

        self.assertEqual(exit_code, 0)
        request_json.assert_called_once_with(
            "http://room.local/api/live-agents",
            method="POST",
            payload={
                "agent_id": "claude-code-live",
                "display_name": "Claude Code Live",
                "provider_kind": "claude_code",
                "connection_kind": "local_cli",
                "session_id": "session-1",
                "endpoint": "",
                "meeting_id": "m1",
                "engagement_mode": "watch",
                "capabilities": ["room_chat", "mentions"],
            },
        )
        self.assertEqual(json.loads(stdout.getvalue()), response)

    def test_live_agent_register_accepts_live_session_connection_kind(self):
        args = build_parser().parse_args(
            [
                "live-agent",
                "register",
                "--agent-id",
                "jsonl-session",
                "--connection-kind",
                "live_session",
            ]
        )

        self.assertEqual(args.connection_kind, "live_session")

    def test_live_agent_register_accepts_terminal_and_self_service_connection_kinds(self):
        terminal_args = build_parser().parse_args(
            [
                "live-agent",
                "register",
                "--agent-id",
                "claude-terminal",
                "--connection-kind",
                "terminal_session",
            ]
        )
        self_service_args = build_parser().parse_args(
            [
                "live-agent",
                "register",
                "--agent-id",
                "antigravity-live",
                "--connection-kind",
                "self_service",
            ]
        )

        self.assertEqual(terminal_args.connection_kind, "terminal_session")
        self.assertEqual(self_service_args.connection_kind, "self_service")

    def test_live_agent_join_brief_json_builds_safe_external_agent_commands(self):
        stdout = StringIO()
        with patch("agentsassemble.cli._request_json", side_effect=AssertionError("join brief should not contact room")):
            with patch("sys.stdout", stdout):
                exit_code = main(
                    [
                        "live-agent",
                        "join-brief",
                        "--server",
                        "http://room.local",
                        "--agent-id",
                        "claude-terminal",
                        "--display-name",
                        "Claude Terminal",
                        "--provider-kind",
                        "claude_code",
                        "--connection-kind",
                        "manual",
                        "--meeting-id",
                        "resident-m1",
                        "--engagement-mode",
                        "watch",
                        "--timeout",
                        "9",
                        "--poll-interval",
                        "0.5",
                        "--max-chain-depth",
                        "2",
                        "--json",
                    ]
                )

        self.assertEqual(exit_code, 0)
        payload = json.loads(stdout.getvalue())
        self.assertEqual(payload["status"], "generated")
        self.assertEqual(payload["packet_kind"], "agent_owned_entry_packet")
        self.assertEqual(payload["entry_contract"]["mode"], "agent_owned")
        self.assertEqual(payload["entry_contract"]["room_role"], "place_record_state_board")
        self.assertEqual(payload["entry_contract"]["provider_context"], "provider_owned")
        self.assertEqual(payload["entry_contract"]["host_prompt_injection"], "not_required")
        self.assertEqual(payload["entry_contract"]["flow_status"], "play_mode_demo_or_auxiliary")
        self.assertIn("admission_contract", payload)
        self.assertEqual(
            payload["admission_contract"],
            {
                "host_admission": "required_before_room_access",
                "identity_proof": "not_included_in_join_brief",
                "lan_invite_proof": "separate_hmac_invite_optional",
                "registration_effect": "not_registered_until_commands_register_runs",
                "network_scope": "local_or_trusted_lan_only_until_signed_room_apis",
                "provider_execution": "not_started_by_join_brief",
            },
        )
        self.assertEqual(
            payload["entry_contract"]["tool_order"],
            [
                "commands.register",
                "commands.wait_next",
                "commands.read_since",
                "templates.say",
                "templates.official_reply",
                "templates.dm_reply",
                "templates.heartbeat",
                "commands.leave",
                "mcp.command",
            ],
        )
        self_service_loop = " ".join(payload["entry_contract"]["self_service_loop"])
        self.assertIn("AGENTSASSEMBLE_WAIT_NEXT_COMMAND", self_service_loop)
        self.assertIn("AGENTSASSEMBLE_SAY_COMMAND_TEMPLATE", self_service_loop)
        self.assertIn("AGENTSASSEMBLE_DM_REPLY_COMMAND_TEMPLATE", self_service_loop)
        self.assertEqual(
            payload["agent"],
            {
                "agent_id": "claude-terminal",
                "display_name": "Claude Terminal",
                "provider_kind": "claude_code",
                "connection_kind": "manual",
                "meeting_id": "resident-m1",
                "engagement_mode": "watch",
            },
        )
        self.assertEqual(
            payload["commands"]["register"],
            [
                "python3",
                "-m",
                "agentsassemble.cli",
                "live-agent",
                "register",
                "--server",
                "http://room.local",
                "--agent-id",
                "claude-terminal",
                "--display-name",
                "Claude Terminal",
                "--provider-kind",
                "claude_code",
                "--connection-kind",
                "manual",
                "--meeting-id",
                "resident-m1",
                "--engagement-mode",
                "watch",
                "--json",
            ],
        )
        self.assertEqual(payload["commands"]["wait_next"][0:7], ["python3", "-m", "agentsassemble.cli", "live-agent", "wait-next", "--server", "http://room.local"])
        self.assertIn("--max-chain-depth", payload["commands"]["wait_next"])
        self.assertIn("2", payload["commands"]["wait_next"])
        self.assertIn("--timeout", payload["commands"]["wait_next"])
        self.assertIn("9", payload["commands"]["wait_next"])
        self.assertIn("--poll-interval", payload["commands"]["wait_next"])
        self.assertIn("0.5", payload["commands"]["wait_next"])
        self.assertEqual(
            payload["commands"]["read_since"],
            [
                "python3",
                "-m",
                "agentsassemble.cli",
                "live-agent",
                "read-since",
                "--server",
                "http://room.local",
                "--agent-id",
                "claude-terminal",
                "--json",
            ],
        )
        self.assertEqual(payload["commands"]["roster_gate"][-3:], ["--require-match", "--fail-on-attention", "--json"])
        self.assertEqual(
            payload["commands"]["leave"],
            [
                "python3",
                "-m",
                "agentsassemble.cli",
                "live-agent",
                "leave",
                "--server",
                "http://room.local",
                "--agent-id",
                "claude-terminal",
                "--json",
            ],
        )
        self.assertIn("Read room.shared_memory as official-only background context when present.", payload["instructions"])
        self.assertIn("Use commands.read_since when you want the raw room diff instead of the next action.", payload["instructions"])
        self.assertEqual(
            payload["execution_contract"],
            {
                "join_semantics": "manual_room_loop",
                "context_durability": "external_owner_managed",
                "sandbox_enforcement": "advisory",
                "evidence_basis": "operator_supplied_join_brief",
                "provider_execution": "not_started_by_join_brief",
            },
        )
        self.assertIn(
            "Use execution_contract.context_durability as the declared agent-private context boundary.",
            payload["instructions"],
        )
        self.assertIn(
            "Use execution_contract.sandbox_enforcement as the declared sandbox boundary.",
            payload["instructions"],
        )
        self.assertIn("For observe_lobby actions, run the returned ack_command and do not post a reply.", payload["instructions"])
        self.assertIn(
            "For return_packet actions, run the returned read_command before the ack_command and do not post a reply.",
            payload["instructions"],
        )
        self.assertIn(
            "Treat this JSON as an agent-owned entry packet: the agent reads room diffs and chooses its own next room action.",
            payload["instructions"],
        )
        self.assertIn("Run commands.leave before intentionally exiting the room.", payload["instructions"])
        self.assertEqual(payload["templates"]["say"][-2:], ["--", "{message}"])
        self.assertIn("{source_event_id}", payload["templates"]["say"])
        self.assertIn("{auto_chain_depth}", payload["templates"]["say"])
        self.assertEqual(payload["templates"]["official_reply"][-2:], ["--", "{message}"])
        self.assertIn("{meeting_id}", payload["templates"]["official_reply"])
        self.assertIn("{source_event_id}", payload["templates"]["official_reply"])
        self.assertEqual(payload["templates"]["dm_reply"][-2:], ["--", "{message}"])
        self.assertIn("{source_event_id}", payload["templates"]["dm_reply"])
        self.assertIn("--last-observed-dm-event-id={last_observed_dm_event_id}", payload["templates"]["heartbeat"])
        serialized = json.dumps(payload)
        self.assertNotIn("endpoint", serialized)
        self.assertNotIn("auth", serialized)
        self.assertNotIn("session_id", serialized)
        self.assertNotIn("config_path", serialized)
        self.assertNotIn("log_path", serialized)

    def test_live_agent_join_brief_declares_context_contract_by_connection_kind(self):
        cases = [
            ("local_cli", "local_cli", "stateless_prompt_call", "stateless_prompt", "advisory"),
            ("claude_code", "terminal_session", "terminal_pty_prompt_bridge", "process_lifetime", "advisory"),
            ("codex_live_session", "codex_resume", "codex_exec_resume", "provider_managed_resume", "codex_readonly"),
            ("codex_live_session", "live_session", "codex_exec_resume", "provider_managed_resume", "codex_readonly"),
            ("cursor_live_session", "live_session", "cursor_chat_resume", "provider_managed_resume", "advisory"),
            ("claude_code", "live_session", "jsonl_live_session", "process_lifetime", "advisory"),
            ("antigravity_cli", "self_service", "self_service_room_loop", "provider_managed_room_loop", "advisory"),
            ("remote_http_bridge", "remote_bridge", "remote_bridge_room_loop", "remote_owner_managed", "advisory"),
        ]
        for provider_kind, connection_kind, join_semantics, context_durability, sandbox_enforcement in cases:
            stdout = StringIO()
            with patch("sys.stdout", stdout):
                exit_code = main(
                    [
                        "live-agent",
                        "join-brief",
                        "--server",
                        "http://room.local",
                        "--agent-id",
                        f"{connection_kind}-agent",
                        "--provider-kind",
                        provider_kind,
                        "--connection-kind",
                        connection_kind,
                        "--json",
                    ]
                )

            self.assertEqual(exit_code, 0)
            payload = json.loads(stdout.getvalue())
            self.assertEqual(payload["execution_contract"]["join_semantics"], join_semantics)
            self.assertEqual(payload["execution_contract"]["context_durability"], context_durability)
            self.assertEqual(payload["execution_contract"]["sandbox_enforcement"], sandbox_enforcement)
            self.assertEqual(payload["execution_contract"]["evidence_basis"], "operator_supplied_join_brief")
            self.assertEqual(payload["execution_contract"]["provider_execution"], "not_started_by_join_brief")

    def test_live_agent_join_brief_http_matches_cli_for_scalar_inputs(self):
        stdout = StringIO()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                server_url = f"http://127.0.0.1:{server.server_port}"
                cli_args = [
                    "live-agent",
                    "join-brief",
                    "--server",
                    server_url,
                    "--agent-id",
                    "external-reviewer",
                    "--display-name",
                    "External Reviewer",
                    "--provider-kind",
                    "manual",
                    "--connection-kind",
                    "manual",
                    "--meeting-id",
                    "resident-m1",
                    "--engagement-mode",
                    "watch",
                    "--timeout",
                    "9",
                    "--poll-interval",
                    "0.5",
                    "--max-chain-depth",
                    "2",
                    "--json",
                ]
                with patch("sys.stdout", stdout):
                    self.assertEqual(main(cli_args), 0)
                request = urllib.request.Request(
                    f"{server_url}/api/live-agent-join-brief",
                    data=json.dumps(
                        {
                            "agent_id": "external-reviewer",
                            "display_name": "External Reviewer",
                            "provider_kind": "manual",
                            "connection_kind": "manual",
                            "meeting_id": "resident-m1",
                            "engagement_mode": "watch",
                            "timeout": 9,
                            "poll_interval": 0.5,
                            "max_chain_depth": 2,
                        }
                    ).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urllib.request.urlopen(request, timeout=4) as response:
                    http_payload = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

        self.assertEqual(http_payload, json.loads(stdout.getvalue()))

    def test_live_agent_join_brief_templates_parse_after_placeholder_replacement(self):
        stdout = StringIO()
        with patch("sys.stdout", stdout):
            exit_code = main(
                [
                    "live-agent",
                    "join-brief",
                    "--server",
                    "http://room.local",
                    "--agent-id",
                    "agent-a",
                    "--json",
                ]
            )

        self.assertEqual(exit_code, 0)
        payload = json.loads(stdout.getvalue())
        say_command = [
            (
                "-h"
                if item == "{message}"
                else "evt-1"
                if item == "{source_event_id}"
                else "1"
                if item == "{auto_chain_depth}"
                else item
            )
            for item in payload["templates"]["say"][3:]
        ]
        say_args = build_parser().parse_args(say_command)
        self.assertEqual(say_args.live_agent_command, "say")
        self.assertEqual(say_args.message, ["-h"])

        official_command = [
            (
                "-official"
                if item == "{message}"
                else "meeting-1"
                if item == "{meeting_id}"
                else "live-1"
                if item == "{source_event_id}"
                else item
            )
            for item in payload["templates"]["official_reply"][3:]
        ]
        official_args = build_parser().parse_args(official_command)
        self.assertEqual(official_args.live_agent_command, "official-reply")
        self.assertEqual(official_args.message, ["-official"])

        heartbeat_command = [
            (
                "error"
                if item == "{status}"
                else "--last-error=--provider-failed"
                if item == "--last-error={last_error}"
                else "--last-reply-at="
                if item == "--last-reply-at={last_reply_at}"
                else "--last-observed-event-id=evt-1"
                if item == "--last-observed-event-id={last_observed_event_id}"
                else "--last-observed-live-event-id=live-1"
                if item == "--last-observed-live-event-id={last_observed_live_event_id}"
                else item
            )
            for item in payload["templates"]["heartbeat"][3:]
        ]
        heartbeat_args = build_parser().parse_args(heartbeat_command)
        self.assertEqual(heartbeat_args.live_agent_command, "heartbeat")
        self.assertEqual(heartbeat_args.status, "error")
        self.assertEqual(heartbeat_args.last_error, "--provider-failed")

        leave_args = build_parser().parse_args(payload["commands"]["leave"][3:])
        self.assertEqual(leave_args.live_agent_command, "leave")
        self.assertEqual(leave_args.agent_id, "agent-a")

    def test_live_agent_join_brief_compact_output_shell_quotes_commands(self):
        stdout = StringIO()
        with patch("agentsassemble.cli._request_json", side_effect=AssertionError("join brief should not contact room")):
            with patch("sys.stdout", stdout):
                exit_code = main(
                    [
                        "live-agent",
                        "join-brief",
                        "--server",
                        "http://room.local/with space",
                        "--agent-id",
                        "agent one",
                        "--display-name",
                        "Agent One",
                    ]
                )

        self.assertEqual(exit_code, 0)
        output = stdout.getvalue()
        self.assertIn("Live-agent join brief for agent one", output)
        self.assertIn("Register:", output)
        self.assertIn("Wait loop:", output)
        self.assertIn("Room snapshot:", output)
        self.assertIn("Lobby reply template:", output)
        self.assertIn("Official reply template:", output)
        self.assertIn("Leave:", output)
        self.assertIn("'http://room.local/with space'", output)
        self.assertIn("'agent one'", output)

    def test_live_agent_leave_marks_agent_offline_and_clears_error(self):
        stdout = StringIO()
        response = {
            "agent": {
                "agent_id": "claude-code-live",
                "status": "offline",
                "last_error": "",
                "last_observed_event_id": "evt1",
                "last_observed_live_event_id": "live-evt1",
            }
        }
        with patch("agentsassemble.cli._request_json", return_value=response) as request_json:
            with patch("sys.stdout", stdout):
                exit_code = main(
                    [
                        "live-agent",
                        "leave",
                        "--server",
                        "http://room.local",
                        "--agent-id",
                        "claude-code-live",
                        "--last-observed-event-id",
                        "evt1",
                        "--last-observed-live-event-id",
                        "live-evt1",
                    ]
                )

        self.assertEqual(exit_code, 0)
        request_json.assert_called_once_with(
            "http://room.local/api/live-agents/claude-code-live/leave",
            method="POST",
            payload={
                "status": "offline",
                "last_error": "",
                "last_observed_event_id": "evt1",
                "last_observed_live_event_id": "live-evt1",
            },
        )
        self.assertIn("claude-code-live: offline", stdout.getvalue())

    def test_live_agent_leave_json_prints_safe_response(self):
        stdout = StringIO()
        response = {
            "agent": {
                "agent_id": "external-reviewer",
                "display_name": "External Reviewer",
                "provider_kind": "manual",
                "connection_kind": "manual",
                "status": "offline",
                "meeting_id": "resident-m1",
                "last_error": "",
                "last_observed_event_id": "evt1",
                "session_id": "secret-session",
                "endpoint": "https://secret.example",
                "auth_ref": "env:SECRET_TOKEN",
                "config_path": "/Users/me/private/live-agents.json",
                "command": ["provider", "--token", "secret"],
                "provider_output": "private provider output",
            },
            "agents": [
                {
                    "agent_id": "external-reviewer",
                    "status": "offline",
                    "session_id": "secret-session",
                    "endpoint": "https://secret.example",
                }
            ],
        }
        with patch("agentsassemble.cli._request_json", return_value=response):
            with patch("sys.stdout", stdout):
                exit_code = main(
                    [
                        "live-agent",
                        "leave",
                        "--server",
                        "http://room.local",
                        "--agent-id",
                        "external-reviewer",
                        "--json",
                    ]
                )

        self.assertEqual(exit_code, 0)
        payload = json.loads(stdout.getvalue())
        self.assertEqual(payload["agent"]["agent_id"], "external-reviewer")
        self.assertEqual(payload["agent"]["status"], "offline")
        self.assertEqual(payload["agent"]["meeting_id"], "resident-m1")
        serialized = stdout.getvalue()
        self.assertNotIn("secret-session", serialized)
        self.assertNotIn("secret.example", serialized)
        self.assertNotIn("SECRET_TOKEN", serialized)
        self.assertNotIn("/Users/me", serialized)
        self.assertNotIn("provider output", serialized)

    def test_live_agent_leave_marks_real_server_row_offline_and_records_operation(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                server_url = f"http://127.0.0.1:{server.server_port}"
                register_request = urllib.request.Request(
                    f"{server_url}/api/live-agents",
                    data=json.dumps(
                        {
                            "agent_id": "agent one",
                            "display_name": "Agent One",
                            "provider_kind": "manual",
                            "connection_kind": "manual",
                            "status": "online",
                            "session_id": "secret-session",
                            "meeting_id": "resident-m1",
                            "last_error": "old error",
                        }
                    ).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urllib.request.urlopen(register_request, timeout=4):
                    pass
                stdout = StringIO()
                with patch("sys.stdout", stdout):
                    exit_code = main(
                        [
                            "live-agent",
                            "leave",
                            "--server",
                            server_url,
                            "--agent-id",
                            "agent one",
                            "--last-observed-event-id",
                            "evt1",
                            "--last-observed-live-event-id",
                            "live1",
                            "--json",
                        ]
                    )
                with urllib.request.urlopen(f"{server_url}/api/live-agents?safe=1", timeout=4) as response:
                    safe_roster = json.loads(response.read().decode("utf-8"))
                with urllib.request.urlopen(f"{server_url}/api/live-agent-operations", timeout=4) as response:
                    operations = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

        self.assertEqual(exit_code, 0)
        payload = json.loads(stdout.getvalue())
        self.assertEqual(payload["agent"]["agent_id"], "agent one")
        self.assertEqual(payload["agent"]["status"], "offline")
        self.assertEqual(payload["agent"]["last_error"], "")
        self.assertEqual(payload["agent"]["last_observed_event_id"], "evt1")
        self.assertEqual(payload["agent"]["last_observed_live_event_id"], "live1")
        self.assertEqual(safe_roster["agents"][0]["status"], "offline")
        leave_operations = [operation for operation in operations["operations"] if operation["operation"] == "live_agent.leave"]
        self.assertEqual(len(leave_operations), 1)
        self.assertEqual(leave_operations[0]["target_id"], "agent one")
        self.assertEqual(leave_operations[0]["details"]["meeting_id"], "resident-m1")
        self.assertFalse((root / "live-agent-runs" / "processes.json").exists())
        serialized = stdout.getvalue() + json.dumps(operations, ensure_ascii=False)
        self.assertNotIn("secret-session", serialized)
        self.assertNotIn("old error", serialized)

    def test_live_agent_say_posts_lobby_message(self):
        stdout = StringIO()
        with patch("agentsassemble.cli._request_json", return_value={"event": {"id": "evt1"}}) as request_json:
            with patch("sys.stdout", stdout):
                exit_code = main(
                    [
                        "live-agent",
                        "say",
                        "--server",
                        "http://room.local",
                        "--agent-id",
                        "gemini-cli",
                        "Gemini",
                        "접속",
                        "확인",
                    ]
                )

        self.assertEqual(exit_code, 0)
        request_json.assert_called_once_with(
            "http://room.local/api/live-agents/gemini-cli/lobby",
            method="POST",
            payload={"message": "Gemini 접속 확인", "kind": "message"},
        )

    def test_live_agent_register_can_send_join_semantics_override(self):
        stdout = StringIO()
        with patch("agentsassemble.cli._request_json", return_value={"agent": {"agent_id": "agent-a"}}) as request_json:
            with patch("sys.stdout", stdout):
                exit_code = main(
                    [
                        "live-agent",
                        "register",
                        "--server",
                        "http://room.local",
                        "--agent-id",
                        "agent-a",
                        "--provider-kind",
                        "codex_live_session",
                        "--connection-kind",
                        "live_session",
                        "--join-semantics",
                        "runtime_managed_room_turn",
                    ]
                )

        self.assertEqual(exit_code, 0)
        payload = request_json.call_args.kwargs["payload"]
        self.assertEqual(payload["join_semantics"], "runtime_managed_room_turn")

    def test_live_agent_say_posts_source_metadata_and_json_acknowledgement(self):
        stdout = StringIO()
        response = {
            "event": {
                "id": "reply-1",
                "actor_id": "gemini-cli",
                "source_event_id": "evt1",
                "auto_chain_depth": 1,
                "live_agent_endpoint": True,
            }
        }
        with patch("agentsassemble.cli._request_json", return_value=response) as request_json:
            with patch("sys.stdout", stdout):
                exit_code = main(
                    [
                        "live-agent",
                        "say",
                        "--server",
                        "http://room.local",
                        "--agent-id",
                        "gemini-cli",
                        "--source-event-id",
                        "evt1",
                        "--auto-chain-depth",
                        "1",
                        "--json",
                        "Gemini",
                        "답변",
                    ]
                )

        self.assertEqual(exit_code, 0)
        request_json.assert_called_once_with(
            "http://room.local/api/live-agents/gemini-cli/lobby",
            method="POST",
            payload={
                "message": "Gemini 답변",
                "kind": "message",
                "source_event_id": "evt1",
                "auto_chain_depth": 1,
            },
        )
        self.assertEqual(json.loads(stdout.getvalue()), response)

    def test_live_agent_say_can_preserve_tool_loop_flow_metadata(self):
        stdout = StringIO()
        with patch("agentsassemble.cli._request_json", return_value={"event": {"id": "reply-1"}}) as request_json:
            with patch("sys.stdout", stdout):
                exit_code = main(
                    [
                        "live-agent",
                        "say",
                        "--server",
                        "http://room.local",
                        "--agent-id",
                        "gemini-cli",
                        "--source-event-id",
                        "flow-start",
                        "--flow-id",
                        "flow-1",
                        "--flow-meeting-id",
                        "m1",
                        "Tool",
                        "loop",
                        "reply",
                    ]
                )

        self.assertEqual(exit_code, 0)
        request_json.assert_called_once_with(
            "http://room.local/api/live-agents/gemini-cli/lobby",
            method="POST",
            payload={
                "message": "Tool loop reply",
                "kind": "message",
                "source_event_id": "flow-start",
                "flow_id": "flow-1",
                "flow_action": "speak",
                "flow_runtime_mode": "provider_tool_loop",
                "flow_meeting_id": "m1",
            },
        )

    def test_live_agent_heartbeat_posts_error_status_and_metadata(self):
        stdout = StringIO()
        with patch(
            "agentsassemble.cli._request_json",
            return_value={"agent": {"agent_id": "claude-code-live", "status": "error"}},
        ) as request_json:
            with patch("sys.stdout", stdout):
                exit_code = main(
                    [
                        "live-agent",
                        "heartbeat",
                        "--server",
                        "http://room.local",
                        "--agent-id",
                        "claude-code-live",
                        "--status",
                        "error",
                        "--last-error",
                        "delegate failed",
                        "--last-attention",
                        "persona_context_blocked_official_turn",
                        "--last-observed-event-id",
                        "evt1",
                        "--last-observed-live-event-id",
                        "live-evt1",
                        "--last-reply-at",
                        "2026-05-17T12:00:00+00:00",
                    ]
                )

        self.assertEqual(exit_code, 0)
        request_json.assert_called_once_with(
            "http://room.local/api/live-agents/claude-code-live/heartbeat",
            method="POST",
            payload={
                "status": "error",
                "last_error": "delegate failed",
                "last_attention": "persona_context_blocked_official_turn",
                "last_reply_at": "2026-05-17T12:00:00+00:00",
                "last_observed_event_id": "evt1",
                "last_observed_live_event_id": "live-evt1",
            },
        )
        self.assertIn("claude-code-live: error", stdout.getvalue())

    def test_live_agent_heartbeat_json_prints_cursor_metadata(self):
        stdout = StringIO()
        response = {
            "agent": {
                "agent_id": "claude-code-live",
                "status": "online",
                "last_observed_event_id": "evt1",
                "last_observed_live_event_id": "live-evt1",
            }
        }
        with patch("agentsassemble.cli._request_json", return_value=response) as request_json:
            with patch("sys.stdout", stdout):
                exit_code = main(
                    [
                        "live-agent",
                        "heartbeat",
                        "--server",
                        "http://room.local",
                        "--agent-id",
                        "claude-code-live",
                        "--status",
                        "online",
                        "--last-observed-event-id",
                        "evt1",
                        "--last-observed-live-event-id",
                        "live-evt1",
                        "--json",
                    ]
                )

        self.assertEqual(exit_code, 0)
        request_json.assert_called_once_with(
            "http://room.local/api/live-agents/claude-code-live/heartbeat",
            method="POST",
            payload={
                "status": "online",
                "last_observed_event_id": "evt1",
                "last_observed_live_event_id": "live-evt1",
            },
        )
        payload = json.loads(stdout.getvalue())
        self.assertEqual(payload["agent"]["last_observed_event_id"], "evt1")
        self.assertEqual(payload["agent"]["last_observed_live_event_id"], "live-evt1")

    def test_live_agent_list_parses_json_and_fail_on_attention(self):
        args = build_parser().parse_args(
            [
                "live-agent",
                "list",
                "--server",
                "http://room.local",
                "--json",
                "--fail-on-attention",
                "--require-host-approved",
            ]
        )

        self.assertEqual(args.live_agent_command, "list")
        self.assertTrue(args.as_json)
        self.assertTrue(args.fail_on_attention)
        self.assertTrue(args.require_host_approved)

    def test_live_agent_list_parses_target_filters_and_require_match(self):
        args = build_parser().parse_args(
            [
                "live-agent",
                "list",
                "--meeting-id",
                "resident-m1",
                "--agent-id",
                "agent-a",
                "--agent-id",
                "agent-b",
                "--status",
                "online",
                "--status",
                "working",
                "--require-match",
                "--require-all-agents",
            ]
        )

        self.assertEqual(args.live_agent_command, "list")
        self.assertEqual(args.meeting_id, "resident-m1")
        self.assertEqual(args.agent_ids, ["agent-a", "agent-b"])
        self.assertEqual(args.statuses, ["online", "working"])
        self.assertTrue(args.require_match)
        self.assertTrue(args.require_all_agents)

    def test_live_agent_list_fetches_roster_and_prints_safe_presence(self):
        payload = {
            "agents": [
                {
                    "agent_id": "agent-a",
                    "display_name": "Agent A",
                    "provider_kind": "claude_code",
                    "connection_kind": "terminal_session",
                    "join_semantics": "codex_exec_resume",
                    "context_durability": "provider_managed_resume",
                    "sandbox_enforcement": "os_sandboxed",
                    "status": "online",
                    "engagement_mode": "always",
                    "meeting_id": "resident-m1",
                    "admission_status": "bound_to_meeting",
                    "host_approved_binding": True,
                    "admission_evidence_source": "meeting_record",
                    "binding_role_id": "architect",
                    "binding_conflicts": ["provider_kind_mismatch"],
                    "endpoint": "http://secret.local/bridge",
                    "heartbeat_age_seconds": 7,
                    "stale_after_seconds": 180,
                    "last_observed_event_id": "evt-1",
                    "last_observed_live_event_id": "live-1",
                }
            ]
        }
        stdout = StringIO()
        with patch("agentsassemble.cli._request_json", return_value=payload) as request_json:
            with patch("sys.stdout", stdout):
                exit_code = main(["live-agent", "list", "--server", "http://room.local"])

        self.assertEqual(exit_code, 0)
        requested = urllib.parse.urlparse(request_json.call_args.args[0])
        self.assertEqual(requested.scheme, "http")
        self.assertEqual(requested.netloc, "room.local")
        self.assertEqual(requested.path, "/api/live-agents")
        self.assertEqual(urllib.parse.parse_qs(requested.query), {"safe": ["1"]})
        output = stdout.getvalue()
        self.assertIn("agent-a Agent A claude_code/terminal_session online meeting=resident-m1", output)
        self.assertIn("join=terminal_pty_prompt_bridge", output)
        self.assertIn("context=process_lifetime", output)
        self.assertIn("sandbox=advisory", output)
        self.assertIn("admission=bound_to_meeting", output)
        self.assertIn("host_approved=yes", output)
        self.assertIn("admission_source=meeting_record", output)
        self.assertIn("binding_role=architect", output)
        self.assertIn("binding_conflicts=provider_kind_mismatch", output)
        self.assertIn("engagement=always", output)
        self.assertIn("heartbeat_age=7s", output)
        self.assertIn("stale_after=180s", output)
        self.assertIn("cursor=evt-1", output)
        self.assertIn("official_cursor=live-1", output)
        self.assertNotIn("secret.local", output)
        self.assertNotIn("endpoint", output)

    def test_live_agent_list_sends_target_filters_to_server(self):
        payload = {
            "agents": [
                {
                    "agent_id": "agent-a",
                    "display_name": "Agent A",
                    "provider_kind": "local_cli",
                    "connection_kind": "local_cli",
                    "status": "working",
                    "meeting_id": "resident-m1",
                }
            ]
        }
        stdout = StringIO()
        with patch("agentsassemble.cli._request_json", return_value=payload) as request_json:
            with patch("sys.stdout", stdout):
                exit_code = main(
                    [
                        "live-agent",
                        "list",
                        "--server",
                        "http://room.local",
                        "--meeting-id",
                        "resident-m1",
                        "--agent-id",
                        "agent-a",
                        "--agent-id",
                        "agent-b",
                        "--status",
                        "online",
                        "--status",
                        "working",
                    ]
                )

        self.assertEqual(exit_code, 0)
        requested = urllib.parse.urlparse(request_json.call_args.args[0])
        self.assertEqual(requested.scheme, "http")
        self.assertEqual(requested.netloc, "room.local")
        self.assertEqual(requested.path, "/api/live-agents")
        query = urllib.parse.parse_qs(requested.query)
        self.assertEqual(query["safe"], ["1"])
        self.assertEqual(query["meeting_id"], ["resident-m1"])
        self.assertEqual(query["agent_id"], ["agent-a", "agent-b"])
        self.assertEqual(query["status"], ["online", "working"])
        self.assertIn("agent-a Agent A local_cli/local_cli working", stdout.getvalue())

    def test_live_agent_list_require_match_exits_one_after_printing_empty_summary(self):
        stdout = StringIO()
        with patch("agentsassemble.cli._request_json", return_value={"agents": []}):
            with patch("sys.stdout", stdout):
                exit_code = main(
                    [
                        "live-agent",
                        "list",
                        "--server",
                        "http://room.local",
                        "--agent-id",
                        "missing-agent",
                        "--require-match",
                    ]
                )

        self.assertEqual(exit_code, 1)
        self.assertIn("no live agents", stdout.getvalue())

    def test_live_agent_list_require_all_agents_exits_one_when_any_requested_agent_is_missing(self):
        payload = {
            "agents": [
                {
                    "agent_id": "agent-a",
                    "display_name": "Agent A",
                    "provider_kind": "local_cli",
                    "connection_kind": "local_cli",
                    "status": "online",
                }
            ]
        }
        stdout = StringIO()
        with patch("agentsassemble.cli._request_json", return_value=payload):
            with patch("sys.stdout", stdout):
                exit_code = main(
                    [
                        "live-agent",
                        "list",
                        "--server",
                        "http://room.local",
                        "--agent-id",
                        "agent-a",
                        "--agent-id",
                        "agent-b",
                        "--require-all-agents",
                    ]
                )

        self.assertEqual(exit_code, 1)
        self.assertIn("agent-a Agent A local_cli/local_cli online", stdout.getvalue())

    def test_live_agent_list_json_prints_safe_roster_projection(self):
        payload = {
            "agents": [
                {
                    "agent_id": "agent-a",
                    "display_name": "Agent A",
                    "provider_kind": "remote_bridge",
                    "connection_kind": "remote_bridge",
                    "join_semantics": "codex_exec_resume",
                    "context_durability": "provider_managed_resume",
                    "status": "error",
                    "meeting_id": "resident-m1",
                    "endpoint": "http://secret.local/bridge",
                    "auth_ref": "literal:secret-token",
                    "config_path": "/Users/me/private/live-agents.json",
                    "session_id": "private-session-id",
                    "last_error": "failed with token=secret-token in /Users/me/private/live-agents.json",
                    "admission_status": "bound_to_meeting",
                    "host_approved_binding": True,
                    "admission_evidence_source": "meeting_record",
                    "binding_role_id": "remote-reviewer",
                    "binding_provider_id": "friend-bridge",
                    "binding_provider_kind": "remote_http_bridge",
                    "binding_permission_profile_id": "meeting_readonly",
                    "binding_join_mode": "resident",
                    "last_observed_event_id": "evt-1",
                    "last_observed_live_event_id": "live-1",
                    "heartbeat_age_seconds": 7,
                    "stale_after_seconds": 180,
                }
            ]
        }
        stdout = StringIO()
        with patch("agentsassemble.cli._request_json", return_value=payload):
            with patch("sys.stdout", stdout):
                exit_code = main(["live-agent", "list", "--server", "http://room.local", "--json"])

        self.assertEqual(exit_code, 0)
        output = stdout.getvalue()
        loaded = json.loads(output)
        agent = loaded["agents"][0]
        self.assertEqual(agent["agent_id"], "agent-a")
        self.assertEqual(agent["join_semantics"], "remote_bridge_room_loop")
        self.assertEqual(agent["context_durability"], "remote_owner_managed")
        self.assertEqual(agent["last_error"], "Live-agent presence error details redacted.")
        self.assertEqual(agent["admission_status"], "bound_to_meeting")
        self.assertEqual(agent["admission_evidence_source"], "meeting_record")
        self.assertTrue(agent["host_approved_binding"])
        self.assertEqual(agent["binding_role_id"], "remote-reviewer")
        self.assertEqual(agent["binding_provider_id"], "friend-bridge")
        self.assertEqual(agent["binding_provider_kind"], "remote_http_bridge")
        self.assertEqual(agent["binding_permission_profile_id"], "meeting_readonly")
        self.assertEqual(agent["binding_join_mode"], "resident")
        self.assertNotIn("endpoint", agent)
        self.assertNotIn("auth_ref", agent)
        self.assertNotIn("config_path", agent)
        self.assertNotIn("session_id", agent)
        self.assertNotIn("secret.local", output)
        self.assertNotIn("secret-token", output)
        self.assertNotIn("live-agents.json", output)

    def test_live_agent_list_compact_output_redacts_sensitive_display_fields(self):
        payload = {
            "agents": [
                {
                    "agent_id": "agent-a",
                    "display_name": "http://secret.local/agent",
                    "provider_kind": "local_cli",
                    "connection_kind": "local_cli",
                    "status": "online",
                    "meeting_id": "/Users/me/private/live-agents.json",
                    "engagement_mode": "always",
                    "admission_status": "bound_to_meeting",
                    "host_approved_binding": True,
                    "binding_role_id": "spoofed",
                    "last_observed_event_id": "token=secret-token",
                    "last_observed_live_event_id": "live-1",
                }
            ]
        }
        stdout = StringIO()
        with patch("agentsassemble.cli._request_json", return_value=payload):
            with patch("sys.stdout", stdout):
                exit_code = main(["live-agent", "list", "--server", "http://room.local"])

        self.assertEqual(exit_code, 0)
        output = stdout.getvalue()
        self.assertIn("agent-a [redacted] local_cli/local_cli online meeting=[redacted]", output)
        self.assertIn("cursor=[redacted]", output)
        self.assertNotIn("admission=bound_to_meeting", output)
        self.assertNotIn("host_approved=yes", output)
        self.assertNotIn("spoofed", output)
        self.assertNotIn("secret.local", output)
        self.assertNotIn("secret-token", output)
        self.assertNotIn("live-agents.json", output)

    def test_live_agent_list_fetch_failure_redacts_server_error(self):
        stderr = StringIO()
        with patch(
            "agentsassemble.cli._request_json",
            side_effect=ValueError(
                "failed reading /Users/me/private/live-agents.json with token=secret-token at http://secret.local"
            ),
        ):
            with patch("sys.stderr", stderr):
                exit_code = main(["live-agent", "list", "--server", "http://room.local"])

        self.assertEqual(exit_code, 2)
        error = stderr.getvalue()
        self.assertIn("Live-agent roster fetch failed: details redacted.", error)
        self.assertNotIn("secret.local", error)
        self.assertNotIn("secret-token", error)
        self.assertNotIn("live-agents.json", error)

    def test_live_agent_list_fail_on_attention_exits_one_after_printing_summary(self):
        payload = {
            "agents": [
                {
                    "agent_id": "agent-online",
                    "display_name": "Agent Online",
                    "provider_kind": "local_cli",
                    "connection_kind": "local_cli",
                    "status": "online",
                },
                {
                    "agent_id": "agent-stale",
                    "display_name": "Agent Stale",
                    "provider_kind": "local_cli",
                    "connection_kind": "local_cli",
                    "status": "stale",
                    "heartbeat_age_seconds": 181,
                    "stale_after_seconds": 180,
                },
                {
                    "agent_id": "agent-error",
                    "display_name": "Agent Error",
                    "provider_kind": "remote_bridge",
                    "connection_kind": "remote_bridge",
                    "status": "error",
                },
                {
                    "agent_id": "agent-offline",
                    "display_name": "Agent Offline",
                    "provider_kind": "manual",
                    "connection_kind": "manual",
                    "status": "offline",
                },
            ]
        }
        stdout = StringIO()
        with patch("agentsassemble.cli._request_json", return_value=payload):
            with patch("sys.stdout", stdout):
                exit_code = main(
                    [
                        "live-agent",
                        "list",
                        "--server",
                        "http://room.local",
                        "--fail-on-attention",
                    ]
                )

        self.assertEqual(exit_code, 1)
        output = stdout.getvalue()
        self.assertIn("agent-online Agent Online local_cli/local_cli online", output)
        self.assertIn("agent-stale Agent Stale local_cli/local_cli stale", output)
        self.assertIn("agent-error Agent Error remote_bridge/remote_bridge error", output)
        self.assertIn("agent-offline Agent Offline manual/manual offline", output)

    def test_live_agent_list_fail_on_attention_accepts_online_and_working(self):
        payload = {
            "agents": [
                {
                    "agent_id": "agent-online",
                    "display_name": "Agent Online",
                    "provider_kind": "local_cli",
                    "connection_kind": "local_cli",
                    "status": "online",
                },
                {
                    "agent_id": "agent-working",
                    "display_name": "Agent Working",
                    "provider_kind": "local_cli",
                    "connection_kind": "local_cli",
                    "status": "working",
                },
            ]
        }
        with patch("agentsassemble.cli._request_json", return_value=payload):
            with patch("sys.stdout", StringIO()):
                exit_code = main(
                    [
                        "live-agent",
                        "list",
                        "--server",
                        "http://room.local",
                        "--fail-on-attention",
                    ]
                )

        self.assertEqual(exit_code, 0)

    def test_live_agent_list_require_host_approved_exits_one_after_printing_summary(self):
        payload = {
            "agents": [
                {
                    "agent_id": "agent-approved",
                    "display_name": "Agent Approved",
                    "provider_kind": "local_cli",
                    "connection_kind": "local_cli",
                    "status": "online",
                    "admission_status": "bound_to_meeting",
                    "host_approved_binding": True,
                    "admission_evidence_source": "meeting_record",
                },
                {
                    "agent_id": "agent-lobby",
                    "display_name": "Agent Lobby",
                    "provider_kind": "manual",
                    "connection_kind": "manual",
                    "status": "online",
                    "admission_status": "meeting_lobby_only",
                    "host_approved_binding": False,
                    "admission_evidence_source": "meeting_record",
                },
            ]
        }
        stdout = StringIO()
        with patch("agentsassemble.cli._request_json", return_value=payload):
            with patch("sys.stdout", stdout):
                exit_code = main(
                    [
                        "live-agent",
                        "list",
                        "--server",
                        "http://room.local",
                        "--require-host-approved",
                    ]
                )

        self.assertEqual(exit_code, 1)
        output = stdout.getvalue()
        self.assertIn("agent-approved Agent Approved local_cli/local_cli online", output)
        self.assertIn("host_approved=yes", output)
        self.assertIn("agent-lobby Agent Lobby manual/manual online", output)
        self.assertIn("host_approved=no", output)
        self.assertIn("admission=meeting_lobby_only", output)

    def test_live_agent_list_require_host_approved_accepts_bound_rows(self):
        payload = {
            "agents": [
                {
                    "agent_id": "agent-approved",
                    "display_name": "Agent Approved",
                    "provider_kind": "local_cli",
                    "connection_kind": "local_cli",
                    "status": "working",
                    "admission_status": "bound_to_meeting",
                    "host_approved_binding": True,
                    "admission_evidence_source": "meeting_record",
                }
            ]
        }
        with patch("agentsassemble.cli._request_json", return_value=payload):
            with patch("sys.stdout", StringIO()):
                exit_code = main(
                    [
                        "live-agent",
                        "list",
                        "--server",
                        "http://room.local",
                        "--require-host-approved",
                    ]
                )

        self.assertEqual(exit_code, 0)

    def test_live_agent_list_require_host_approved_accepts_empty_roster_without_require_match(self):
        stdout = StringIO()
        with patch("agentsassemble.cli._request_json", return_value={"agents": []}):
            with patch("sys.stdout", stdout):
                exit_code = main(
                    [
                        "live-agent",
                        "list",
                        "--server",
                        "http://room.local",
                        "--require-host-approved",
                    ]
                )

        self.assertEqual(exit_code, 0)
        self.assertIn("no live agents", stdout.getvalue())

    def test_live_agent_list_require_host_approved_uses_safe_projection_before_gate(self):
        payload = {
            "agents": [
                {
                    "agent_id": "spoofed-agent",
                    "display_name": "Spoofed Agent",
                    "provider_kind": "manual",
                    "connection_kind": "manual",
                    "status": "online",
                    "admission_status": "bound_to_meeting",
                    "host_approved_binding": True,
                    "binding_role_id": "spoofed",
                }
            ]
        }
        stdout = StringIO()
        with patch("agentsassemble.cli._request_json", return_value=payload):
            with patch("sys.stdout", stdout):
                exit_code = main(
                    [
                        "live-agent",
                        "list",
                        "--server",
                        "http://room.local",
                        "--require-host-approved",
                    ]
                )

        self.assertEqual(exit_code, 1)
        output = stdout.getvalue()
        self.assertIn("spoofed-agent Spoofed Agent manual/manual online", output)
        self.assertNotIn("host_approved=yes", output)
        self.assertNotIn("spoofed", output.split("online", 1)[1])

    def test_live_agent_list_require_host_approved_compact_output_redacts_sensitive_fields(self):
        payload = {
            "agents": [
                {
                    "agent_id": "agent-bridge",
                    "display_name": "Agent Bridge",
                    "provider_kind": "remote_bridge",
                    "connection_kind": "remote_bridge",
                    "status": "online",
                    "admission_status": "meeting_lobby_only",
                    "host_approved_binding": False,
                    "admission_evidence_source": "meeting_record",
                    "endpoint": "http://secret.local/bridge",
                    "auth_ref": "literal:secret-token",
                    "config_path": "/Users/me/private/live-agents.json",
                    "session_id": "private-session-id",
                    "provider_output": "secret provider text",
                    "last_error": "failed with token=secret-token in /Users/me/private/live-agents.json",
                }
            ]
        }
        stdout = StringIO()
        with patch("agentsassemble.cli._request_json", return_value=payload):
            with patch("sys.stdout", stdout):
                exit_code = main(
                    [
                        "live-agent",
                        "list",
                        "--server",
                        "http://room.local",
                        "--require-host-approved",
                    ]
                )

        self.assertEqual(exit_code, 1)
        output = stdout.getvalue()
        self.assertIn("agent-bridge Agent Bridge remote_bridge/remote_bridge online", output)
        self.assertIn("host_approved=no", output)
        self.assertNotIn("secret.local", output)
        self.assertNotIn("secret-token", output)
        self.assertNotIn("live-agents.json", output)
        self.assertNotIn("private-session-id", output)
        self.assertNotIn("secret provider text", output)

    def test_live_agent_list_require_host_approved_is_separate_from_fail_on_attention(self):
        unapproved_online = {
            "agents": [
                {
                    "agent_id": "agent-lobby",
                    "display_name": "Agent Lobby",
                    "provider_kind": "manual",
                    "connection_kind": "manual",
                    "status": "online",
                    "admission_status": "meeting_lobby_only",
                    "host_approved_binding": False,
                    "admission_evidence_source": "meeting_record",
                }
            ]
        }
        approved_stale = {
            "agents": [
                {
                    "agent_id": "agent-stale",
                    "display_name": "Agent Stale",
                    "provider_kind": "local_cli",
                    "connection_kind": "local_cli",
                    "status": "stale",
                    "admission_status": "bound_to_meeting",
                    "host_approved_binding": True,
                    "admission_evidence_source": "meeting_record",
                }
            ]
        }
        with patch("agentsassemble.cli._request_json", return_value=unapproved_online):
            with patch("sys.stdout", StringIO()):
                liveness_exit = main(
                    [
                        "live-agent",
                        "list",
                        "--server",
                        "http://room.local",
                        "--fail-on-attention",
                    ]
                )
        with patch("agentsassemble.cli._request_json", return_value=approved_stale):
            with patch("sys.stdout", StringIO()):
                admission_exit = main(
                    [
                        "live-agent",
                        "list",
                        "--server",
                        "http://room.local",
                        "--require-host-approved",
                    ]
                )

        self.assertEqual(liveness_exit, 0)
        self.assertEqual(admission_exit, 0)

    def test_live_agent_list_require_host_approved_preserves_target_gates(self):
        with patch("agentsassemble.cli._request_json", return_value={"agents": []}):
            with patch("sys.stdout", StringIO()) as empty_stdout:
                empty_exit = main(
                    [
                        "live-agent",
                        "list",
                        "--server",
                        "http://room.local",
                        "--agent-id",
                        "missing-agent",
                        "--require-match",
                        "--require-host-approved",
                    ]
                )
        payload = {
            "agents": [
                {
                    "agent_id": "agent-a",
                    "display_name": "Agent A",
                    "provider_kind": "local_cli",
                    "connection_kind": "local_cli",
                    "status": "online",
                    "admission_status": "bound_to_meeting",
                    "host_approved_binding": True,
                    "admission_evidence_source": "meeting_record",
                }
            ]
        }
        with patch("agentsassemble.cli._request_json", return_value=payload):
            with patch("sys.stdout", StringIO()) as partial_stdout:
                partial_exit = main(
                    [
                        "live-agent",
                        "list",
                        "--server",
                        "http://room.local",
                        "--agent-id",
                        "agent-a",
                        "--agent-id",
                        "agent-b",
                        "--require-all-agents",
                        "--require-host-approved",
                    ]
                )

        self.assertEqual(empty_exit, 1)
        self.assertIn("no live agents", empty_stdout.getvalue())
        self.assertEqual(partial_exit, 1)
        self.assertIn("agent-a Agent A local_cli/local_cli online", partial_stdout.getvalue())

    def test_live_agent_list_require_host_approved_json_prints_safe_projection_before_exit(self):
        payload = {
            "agents": [
                {
                    "agent_id": "agent-bridge",
                    "display_name": "Agent Bridge",
                    "provider_kind": "remote_bridge",
                    "connection_kind": "remote_bridge",
                    "status": "online",
                    "admission_status": "meeting_lobby_only",
                    "host_approved_binding": False,
                    "admission_evidence_source": "meeting_record",
                    "endpoint": "http://secret.local/bridge",
                    "auth_ref": "literal:secret-token",
                    "config_path": "/Users/me/private/live-agents.json",
                    "session_id": "private-session-id",
                    "last_error": "failed with token=secret-token in /Users/me/private/live-agents.json",
                }
            ]
        }
        with patch("agentsassemble.cli._request_json", return_value=payload):
            with patch("sys.stdout", StringIO()) as stdout:
                exit_code = main(
                    [
                        "live-agent",
                        "list",
                        "--server",
                        "http://room.local",
                        "--json",
                        "--require-host-approved",
                    ]
                )

        self.assertEqual(exit_code, 1)
        output = stdout.getvalue()
        parsed = json.loads(output)
        self.assertEqual(parsed["agents"][0]["admission_status"], "meeting_lobby_only")
        self.assertFalse(parsed["agents"][0]["host_approved_binding"])
        self.assertNotIn("endpoint", parsed["agents"][0])
        self.assertNotIn("auth_ref", parsed["agents"][0])
        self.assertNotIn("config_path", parsed["agents"][0])
        self.assertNotIn("session_id", parsed["agents"][0])
        self.assertNotIn("secret.local", output)
        self.assertNotIn("secret-token", output)
        self.assertNotIn("live-agents.json", output)

    def test_live_agent_list_require_host_approved_keeps_safe_filtered_request(self):
        payload = {
            "agents": [
                {
                    "agent_id": "agent-a",
                    "display_name": "Agent A",
                    "provider_kind": "local_cli",
                    "connection_kind": "local_cli",
                    "status": "online",
                    "admission_status": "bound_to_meeting",
                    "host_approved_binding": True,
                    "admission_evidence_source": "meeting_record",
                }
            ]
        }
        with patch("agentsassemble.cli._request_json", return_value=payload) as request_json:
            with patch("sys.stdout", StringIO()):
                exit_code = main(
                    [
                        "live-agent",
                        "list",
                        "--server",
                        "http://room.local",
                        "--meeting-id",
                        "resident-m1",
                        "--agent-id",
                        "agent-a",
                        "--agent-id",
                        "agent-b",
                        "--status",
                        "online",
                        "--require-host-approved",
                    ]
                )

        self.assertEqual(exit_code, 0)
        requested = urllib.parse.urlparse(request_json.call_args.args[0])
        self.assertEqual(requested.path, "/api/live-agents")
        query = urllib.parse.parse_qs(requested.query)
        self.assertEqual(query["safe"], ["1"])
        self.assertEqual(query["meeting_id"], ["resident-m1"])
        self.assertEqual(query["agent_id"], ["agent-a", "agent-b"])
        self.assertEqual(query["status"], ["online"])
        self.assertNotIn("require_host_approved", query)

    def test_live_agent_heartbeat_can_clear_stale_error_metadata(self):
        with patch(
            "agentsassemble.cli._request_json",
            return_value={"agent": {"agent_id": "claude-code-live", "status": "online"}},
        ) as request_json:
            with patch("sys.stdout", StringIO()):
                exit_code = main(
                    [
                        "live-agent",
                        "heartbeat",
                        "--server",
                        "http://room.local",
                        "--agent-id",
                        "claude-code-live",
                        "--status",
                        "online",
                        "--last-error",
                        "",
                    ]
                )

        self.assertEqual(exit_code, 0)
        self.assertEqual(
            request_json.call_args.kwargs["payload"],
            {"status": "online", "last_error": ""},
        )

    def test_live_agent_engagement_parses_mode_options(self):
        args = build_parser().parse_args(
            [
                "live-agent",
                "engagement",
                "--agent-id",
                "agent-a",
                "--mode",
                "watch",
                "--json",
            ]
        )

        self.assertEqual(args.live_agent_command, "engagement")
        self.assertEqual(args.engagement_mode, "watch")
        self.assertTrue(args.as_json)

    def test_live_agent_engagement_posts_runtime_policy_update(self):
        stdout = StringIO()
        payload = {"agent": {"agent_id": "agent one", "engagement_mode": "watch"}}
        with patch("agentsassemble.cli._request_json", return_value=payload) as request_json:
            with patch("sys.stdout", stdout):
                exit_code = main(
                    [
                        "live-agent",
                        "engagement",
                        "--server",
                        "http://room.local",
                        "--agent-id",
                        "agent one",
                        "--mode",
                        "watch",
                    ]
                )

        self.assertEqual(exit_code, 0)
        request_json.assert_called_once_with(
            "http://room.local/api/live-agents/agent%20one/engagement",
            method="POST",
            payload={"engagement_mode": "watch"},
        )
        self.assertIn("agent one: watch", stdout.getvalue())

    def test_live_agent_engagement_can_emit_json_payload(self):
        payload = {"agent": {"agent_id": "agent-a", "engagement_mode": "manual"}, "agents": []}
        with patch("agentsassemble.cli._request_json", return_value=payload):
            with patch("sys.stdout", StringIO()) as stdout:
                exit_code = main(
                    [
                        "live-agent",
                        "engagement",
                        "--server",
                        "http://room.local",
                        "--agent-id",
                        "agent-a",
                        "--mode",
                        "manual",
                        "--json",
                    ]
                )

        self.assertEqual(exit_code, 0)
        self.assertEqual(json.loads(stdout.getvalue()), payload)

    def test_live_agent_engagement_updates_real_http_endpoint_without_refreshing_heartbeat(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "room"
            root.mkdir()
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                server_url = f"http://127.0.0.1:{server.server_port}"
                with patch("sys.stdout", StringIO()):
                    register_exit = main(
                        [
                            "live-agent",
                            "register",
                            "--server",
                            server_url,
                            "--agent-id",
                            "agent-a",
                            "--display-name",
                            "Agent A",
                            "--connection-kind",
                            "local_cli",
                            "--engagement-mode",
                            "always",
                        ]
                    )
                before = json.loads((root / "live_agents.json").read_text(encoding="utf-8"))["agents"][0]
                stdout = StringIO()
                with patch("sys.stdout", stdout):
                    engagement_exit = main(
                        [
                            "live-agent",
                            "engagement",
                            "--server",
                            server_url,
                            "--agent-id",
                            "agent-a",
                            "--mode",
                            "watch",
                        ]
                    )
                after = json.loads((root / "live_agents.json").read_text(encoding="utf-8"))["agents"][0]
            finally:
                server.shutdown()
                server.server_close()

        self.assertEqual(register_exit, 0)
        self.assertEqual(engagement_exit, 0)
        self.assertEqual(after["engagement_mode"], "watch")
        self.assertIn("engagement_mode_updated_at", after)
        self.assertEqual(after["last_seen_at"], before["last_seen_at"])
        self.assertIn("agent-a: watch", stdout.getvalue())
