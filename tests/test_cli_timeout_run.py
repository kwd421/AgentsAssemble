import unittest
import json
import shlex
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import StringIO
from unittest.mock import patch

from agentsassemble import cli as cli_module
from agentsassemble.cli import build_parser, main
from agentsassemble.gui import _make_handler, append_lobby_event, read_lobby
from agentsassemble.live_agent_runner import ResidentAgentConfig




class CliTimeoutRunTests(unittest.TestCase):

    def test_live_agent_run_accepts_remote_bridge_without_local_command(self):
        args = build_parser().parse_args(
            [
                "live-agent",
                "run",
                "--agent-id",
                "friend-claude",
                "--connection-kind",
                "remote_bridge",
                "--endpoint",
                "https://friend.local:8777",
                "--auth-ref",
                "env:BRIDGE_TOKEN",
                "--max-ticks",
                "1",
            ]
        )

        self.assertEqual(args.live_agent_command, "run")
        self.assertEqual(args.connection_kind, "remote_bridge")
        self.assertEqual(args.endpoint, "https://friend.local:8777")
        self.assertEqual(args.auth_ref, "env:BRIDGE_TOKEN")
        self.assertEqual(args.resident_command, [])

    def test_live_agent_run_parser_defaults_to_resident_always_policy(self):
        args = build_parser().parse_args(
            [
                "live-agent",
                "run",
                "--agent-id",
                "claude-code-live",
                "--display-name",
                "Claude Code Live",
                "--command",
                "claude",
                "-p",
            ]
        )

        self.assertEqual(args.live_agent_command, "run")
        self.assertEqual(args.engagement_mode, "always")
        self.assertEqual(args.poll_interval, 0.25)
        self.assertEqual(args.heartbeat_interval, 30.0)
        self.assertEqual(args.cooldown, 5.0)
        self.assertEqual(args.max_chain_depth, 1)
        self.assertEqual(args.max_ticks, 0)
        self.assertEqual(args.official_turn_timeout, 0)
        self.assertEqual(args.resident_command, ["claude", "-p"])

    def test_live_agent_run_parser_accepts_official_turn_timeout(self):
        args = build_parser().parse_args(
            [
                "live-agent",
                "run",
                "--agent-id",
                "grok-live",
                "--official-turn-timeout",
                "240",
                "--command",
                "grok",
            ]
        )

        self.assertEqual(args.official_turn_timeout, 240)

    def test_live_agent_run_parser_rejects_invalid_resident_bounds(self):
        stderr = StringIO()
        with patch("sys.stderr", stderr):
            with self.assertRaises(SystemExit) as raised:
                build_parser().parse_args(["live-agent", "run", "--agent-id", "agent-a", "--max-ticks", "-1"])

        self.assertEqual(raised.exception.code, 2)
        self.assertIn("value must be non-negative", stderr.getvalue())

    def test_live_agent_run_parser_rejects_non_finite_timing(self):
        stderr = StringIO()
        with patch("sys.stderr", stderr):
            with self.assertRaises(SystemExit) as raised:
                build_parser().parse_args(["live-agent", "run", "--agent-id", "agent-a", "--poll-interval", "nan"])

        self.assertEqual(raised.exception.code, 2)
        self.assertIn("value must be a finite non-negative number", stderr.getvalue())

    def test_live_agent_run_accepts_live_session_connection_kind(self):
        args = build_parser().parse_args(
            [
                "live-agent",
                "run",
                "--agent-id",
                "local-session",
                "--connection-kind",
                "live_session",
                "--command",
                "python3",
                "-u",
                "fake_session.py",
            ]
        )

        self.assertEqual(args.connection_kind, "live_session")

    def test_live_agent_run_accepts_self_service_connection_kind(self):
        args = build_parser().parse_args(
            [
                "live-agent",
                "run",
                "--agent-id",
                "antigravity-live",
                "--provider-kind",
                "antigravity_cli",
                "--connection-kind",
                "self_service",
                "--command",
                "antigravity",
            ]
        )

        self.assertEqual(args.connection_kind, "self_service")
        self.assertEqual(args.provider_kind, "antigravity_cli")
        self.assertEqual(args.resident_command, ["antigravity"])

    def test_live_agent_run_self_service_starts_process_without_prompt_injection(self):
        class FakeSelfServiceProcess:
            pid = 4321
            returncode = 0

            def poll(self):
                return self.returncode

            def wait(self, timeout=None):
                return self.returncode

            def terminate(self):
                self.returncode = -15

            def kill(self):
                self.returncode = -9

        calls = []
        popen_calls = []

        def request_json(url, *, method="GET", payload=None, **kwargs):
            del kwargs
            calls.append({"url": url, "method": method, "payload": payload})
            if url.endswith("/api/live-agents") and method == "POST":
                return {"agent": {"agent_id": "selfer", "status": "online"}}
            return {"agent": {"agent_id": "selfer", "status": (payload or {}).get("status", "online")}}

        def fake_popen(command, **kwargs):
            popen_calls.append({"command": command, "kwargs": kwargs})
            return FakeSelfServiceProcess()

        with patch("agentsassemble.cli._request_json", side_effect=request_json):
            with patch("agentsassemble.cli.subprocess.Popen", side_effect=fake_popen):
                with patch("agentsassemble.cli.LiveAgentRunner", side_effect=AssertionError("prompt-injection runner used")):
                    exit_code = main(
                        [
                            "live-agent",
                            "run",
                            "--server",
                            "http://room.local",
                            "--agent-id",
                            "selfer",
                            "--display-name",
                            "Self Service",
                            "--provider-kind",
                            "antigravity_cli",
                            "--connection-kind",
                            "self_service",
                            "--meeting-id",
                            "resident-m1",
                            "--persona-card-id",
                            "yanagi",
                            "--character-mode",
                            "on",
                            "--max-ticks",
                            "1",
                            "--command",
                            sys.executable,
                            "-c",
                            "pass",
                        ]
                    )

        self.assertEqual(exit_code, 0)
        self.assertEqual(len(popen_calls), 1)
        register_payload = calls[0]["payload"]
        self.assertEqual(register_payload["persona_card_id"], "yanagi")
        self.assertEqual(register_payload["character_mode"], "on")
        self.assertEqual(popen_calls[0]["kwargs"]["stdin"], subprocess.DEVNULL)
        self.assertEqual(popen_calls[0]["kwargs"]["env"]["AGENTSASSEMBLE_SERVER"], "http://room.local")
        self.assertEqual(popen_calls[0]["kwargs"]["env"]["AGENTSASSEMBLE_AGENT_ID"], "selfer")
        self.assertEqual(popen_calls[0]["kwargs"]["env"]["AGENTSASSEMBLE_CONNECTION_KIND"], "self_service")
        env = popen_calls[0]["kwargs"]["env"]
        wait_next = shlex.split(env["AGENTSASSEMBLE_WAIT_NEXT_COMMAND"])
        self.assertEqual(wait_next[:4], [sys.executable, "-m", "agentsassemble.cli", "live-agent"])
        self.assertIn("wait-next", wait_next)
        self.assertIn("--agent-id", wait_next)
        self.assertIn("selfer", wait_next)
        self.assertIn("--max-chain-depth", wait_next)
        self.assertIn("1", wait_next)
        self.assertIn("--json", wait_next)
        self.assertIn("wait-room-event", shlex.split(env["AGENTSASSEMBLE_WAIT_ROOM_EVENT_COMMAND"]))
        self.assertIn("wait-official-turn", shlex.split(env["AGENTSASSEMBLE_WAIT_OFFICIAL_TURN_COMMAND"]))
        say_template = shlex.split(env["AGENTSASSEMBLE_SAY_COMMAND_TEMPLATE"])
        self.assertIn("say", say_template)
        self.assertIn("{source_event_id}", say_template)
        self.assertIn("{auto_chain_depth}", say_template)
        self.assertIn("{message}", say_template)
        official_template = shlex.split(env["AGENTSASSEMBLE_OFFICIAL_REPLY_COMMAND_TEMPLATE"])
        self.assertIn("official-reply", official_template)
        self.assertIn("{meeting_id}", official_template)
        self.assertIn("{source_event_id}", official_template)
        self.assertIn("{message}", official_template)
        heartbeat_template = shlex.split(env["AGENTSASSEMBLE_HEARTBEAT_COMMAND_TEMPLATE"])
        self.assertIn("heartbeat", heartbeat_template)
        self.assertIn("{status}", heartbeat_template)
        self.assertIn("--last-error={last_error}", heartbeat_template)
        self.assertIn("--last-attention={last_attention}", heartbeat_template)
        self.assertIn("--last-reply-at={last_reply_at}", heartbeat_template)
        self.assertIn("--last-observed-event-id={last_observed_event_id}", heartbeat_template)
        self.assertIn("--last-observed-live-event-id={last_observed_live_event_id}", heartbeat_template)
        leave_command = shlex.split(env["AGENTSASSEMBLE_LEAVE_COMMAND"])
        self.assertIn("leave", leave_command)
        self.assertIn("--agent-id", leave_command)
        self.assertIn("selfer", leave_command)
        self.assertIn("--json", leave_command)
        self.assertFalse(any(call["url"].endswith("/room") for call in calls))

    def test_live_agent_run_uses_codex_resident_runner_for_codex_live_session_provider(self):
        args = build_parser().parse_args(
            [
                "live-agent",
                "run",
                "--agent-id",
                "codex-live",
                "--provider-kind",
                "codex_live_session",
                "--connection-kind",
                "live_session",
                "--session-id",
                "019e3038-39cc-76a2-a746-5ba8c0f3b408",
                "--max-ticks",
                "1",
            ]
        )

        config = cli_module.config_from_args(args)
        runner = cli_module._command_runner_for_config(config)
        try:
            self.assertEqual(config.command, ["codex"])
            self.assertEqual(runner.__class__.__name__, "CodexResidentCommandRunner")
        finally:
            cli_module._close_command_runner(runner)

    def test_live_agent_run_uses_grok_resident_runner_for_grok_live_session_provider(self):
        args = build_parser().parse_args(
            [
                "live-agent",
                "run",
                "--agent-id",
                "grok-live",
                "--provider-kind",
                "grok_live_session",
                "--connection-kind",
                "live_session",
                "--session-id",
                "grok-session-abc123",
                "--max-ticks",
                "1",
            ]
        )

        config = cli_module.config_from_args(args)
        runner = cli_module._command_runner_for_config(config)
        try:
            self.assertEqual(config.command, ["grok"])
            self.assertEqual(runner.__class__.__name__, "GrokResidentCommandRunner")
        finally:
            cli_module._close_command_runner(runner)

    def test_live_agent_run_uses_cursor_resident_runner_for_cursor_live_session_provider(self):
        args = build_parser().parse_args(
            [
                "live-agent",
                "run",
                "--agent-id",
                "cursor-live",
                "--provider-kind",
                "cursor_live_session",
                "--connection-kind",
                "live_session",
                "--session-id",
                "cursor-chat-abc123",
                "--max-ticks",
                "1",
            ]
        )

        config = cli_module.config_from_args(args)
        runner = cli_module._command_runner_for_config(config)
        try:
            self.assertEqual(config.command, ["cursor-agent"])
            self.assertEqual(runner.__class__.__name__, "CursorResidentCommandRunner")
        finally:
            cli_module._close_command_runner(runner)

    def test_live_agent_run_rejects_superseded_cursor_terminal_session(self):
        args = build_parser().parse_args(
            [
                "live-agent",
                "run",
                "--agent-id",
                "cursor-agent-live",
                "--provider-kind",
                "cursor",
                "--connection-kind",
                "terminal_session",
                "--command",
                "cursor-agent",
                "--max-ticks",
                "1",
            ]
        )

        config = cli_module.config_from_args(args)

        with self.assertRaisesRegex(ValueError, "cursor-agent-live-session"):
            cli_module._validate_resident_config(config)

    def test_live_agent_run_rejects_generic_cursor_live_session(self):
        args = build_parser().parse_args(
            [
                "live-agent",
                "run",
                "--agent-id",
                "cursor-generic-live",
                "--provider-kind",
                "cursor",
                "--connection-kind",
                "live_session",
                "--command",
                "cursor-agent",
                "--max-ticks",
                "1",
            ]
        )

        config = cli_module.config_from_args(args)

        with self.assertRaisesRegex(ValueError, "cursor_live_session"):
            cli_module._validate_resident_config(config)

    def test_live_agent_run_rejects_generic_cursor_terminal_session_wrapper(self):
        args = build_parser().parse_args(
            [
                "live-agent",
                "run",
                "--agent-id",
                "cursor-wrapper-live",
                "--provider-kind",
                "cursor",
                "--connection-kind",
                "terminal_session",
                "--command",
                "custom-cursor-wrapper",
                "--max-ticks",
                "1",
            ]
        )

        config = cli_module.config_from_args(args)

        with self.assertRaisesRegex(ValueError, "cursor_live_session"):
            cli_module._validate_resident_config(config)

    def test_live_agent_run_rejects_non_resident_connection_kind(self):
        stderr = StringIO()
        with patch("sys.stderr", stderr):
            with self.assertRaises(SystemExit) as raised:
                build_parser().parse_args(
                    [
                        "live-agent",
                        "run",
                        "--agent-id",
                        "manual-agent",
                        "--connection-kind",
                        "manual",
                        "--command",
                        "python3",
                        "-c",
                        "print('should not run')",
                    ]
                )

        self.assertEqual(raised.exception.code, 2)
        self.assertIn("invalid choice", stderr.getvalue())

    def test_live_agent_run_posts_fake_cli_reply_with_tick_bound(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "room"
            root.mkdir()
            source_event = append_lobby_event(root, {"name": "나", "side": "mine", "message": "상주 에이전트 응답해"})
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                stdout = StringIO()
                with patch("sys.stdout", stdout):
                    exit_code = main(
                        [
                            "live-agent",
                            "run",
                            "--server",
                            f"http://127.0.0.1:{server.server_port}",
                            "--agent-id",
                            "agent-single",
                            "--display-name",
                            "Single Agent",
                            "--poll-interval",
                            "0",
                            "--cooldown",
                            "0",
                            "--max-chain-depth",
                            "0",
                            "--max-ticks",
                            "2",
                            "--command",
                            sys.executable,
                            "-c",
                            "import sys; sys.stdin.read(); print('Single reply')",
                        ]
                    )
            finally:
                server.shutdown()
                server.server_close()

            self.assertEqual(exit_code, 0)
            events = read_lobby(root)
            replies = [event for event in events if event["actor_id"] == "agent-single"]
            self.assertEqual(len(replies), 1)
            self.assertEqual(replies[0]["message"], "Single reply")
            self.assertEqual(replies[0]["source_event_id"], source_event["id"])
            self.assertEqual(replies[0]["auto_chain_depth"], 1)
            self.assertIn("Resident agent stopped after posting 1 replies", stdout.getvalue())

    def test_live_agent_run_remote_bridge_posts_reply_with_tick_bound(self):
        bridge_calls = []

        class FakeBridgeHandler(BaseHTTPRequestHandler):
            def do_POST(self):
                if self.path != "/agentsassemble/run":
                    self.send_response(404)
                    self.end_headers()
                    return
                length = int(self.headers.get("Content-Length", "0") or "0")
                payload = json.loads(self.rfile.read(length).decode("utf-8"))
                bridge_calls.append({"authorization": self.headers.get("Authorization"), "payload": payload})
                body = json.dumps(
                    {
                        "text": '{"message":"Remote bridge resident reply","kind":"message"}',
                        "metadata": {"bridge": "fake"},
                    }
                ).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, format, *args):
                return

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "room"
            root.mkdir()
            source_event = append_lobby_event(root, {"name": "나", "side": "mine", "message": "원격 친구 살아있어?"})
            room_server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            bridge_server = ThreadingHTTPServer(("127.0.0.1", 0), FakeBridgeHandler)
            room_thread = threading.Thread(target=room_server.serve_forever, daemon=True)
            bridge_thread = threading.Thread(target=bridge_server.serve_forever, daemon=True)
            room_thread.start()
            bridge_thread.start()
            try:
                stdout = StringIO()
                with patch("sys.stdout", stdout):
                    exit_code = main(
                        [
                            "live-agent",
                            "run",
                            "--server",
                            f"http://127.0.0.1:{room_server.server_port}",
                            "--agent-id",
                            "friend-claude",
                            "--display-name",
                            "Friend Claude",
                            "--provider-kind",
                            "claude_code",
                            "--connection-kind",
                            "remote_bridge",
                            "--endpoint",
                            f"http://127.0.0.1:{bridge_server.server_port}",
                            "--auth-ref",
                            "literal:bridge-token",
                            "--poll-interval",
                            "0",
                            "--cooldown",
                            "0",
                            "--max-chain-depth",
                            "0",
                            "--max-ticks",
                            "1",
                        ]
                    )
            finally:
                room_server.shutdown()
                bridge_server.shutdown()
                room_server.server_close()
                bridge_server.server_close()

            self.assertEqual(exit_code, 0)
            self.assertEqual(bridge_calls[0]["authorization"], "Bearer bridge-token")
            self.assertEqual(bridge_calls[0]["payload"]["step"], "lobby")
            self.assertEqual(bridge_calls[0]["payload"]["role"]["id"], "friend-claude")
            self.assertIn("원격 친구 살아있어?", bridge_calls[0]["payload"]["prompt"])
            self.assertNotIn("command", bridge_calls[0]["payload"])
            events = read_lobby(root)
            replies = [event for event in events if event["actor_id"] == "friend-claude"]
            self.assertEqual(len(replies), 1)
            self.assertEqual(replies[0]["message"], "Remote bridge resident reply")
            self.assertEqual(replies[0]["source_event_id"], source_event["id"])
            self.assertEqual(replies[0]["auto_chain_depth"], 1)
            self.assertIn("Resident agent stopped after posting 1 replies", stdout.getvalue())

    def test_live_agent_run_rejects_missing_local_command_before_registration(self):
        calls = []

        def request_json(url, *, method="GET", payload=None):
            calls.append((url, method, payload))
            return {"agent": {"agent_id": "agent-single", "status": "online"}, "lobby_events": []}

        stdout = StringIO()
        stderr = StringIO()
        with (
            patch("agentsassemble.cli._request_json", side_effect=request_json),
            patch("sys.stdout", stdout),
            patch("sys.stderr", stderr),
        ):
            exit_code = main(
                [
                    "live-agent",
                    "run",
                    "--server",
                    "http://room.local",
                    "--agent-id",
                    "agent-single",
                    "--display-name",
                    "Single Agent",
                    "--poll-interval",
                    "0",
                    "--max-ticks",
                    "1",
                    "--command",
                    "definitely-missing-agentsassemble-command",
                ]
            )

        self.assertEqual(exit_code, 2)
        self.assertEqual(calls, [])
        self.assertIn("agent-single", stderr.getvalue())
        self.assertIn("Command not found", stderr.getvalue())
        self.assertNotIn("Resident agent stopped", stdout.getvalue())

    def test_live_agent_run_rejects_missing_live_session_command_before_registration(self):
        calls = []

        def request_json(url, *, method="GET", payload=None):
            calls.append((url, method, payload))
            return {"agent": {"agent_id": "agent-session", "status": "online"}, "lobby_events": []}

        stderr = StringIO()
        with (
            patch("agentsassemble.cli._request_json", side_effect=request_json),
            patch("sys.stdout", StringIO()),
            patch("sys.stderr", stderr),
        ):
            exit_code = main(
                [
                    "live-agent",
                    "run",
                    "--server",
                    "http://room.local",
                    "--agent-id",
                    "agent-session",
                    "--display-name",
                    "Agent Session",
                    "--connection-kind",
                    "live_session",
                    "--poll-interval",
                    "0",
                    "--max-ticks",
                    "1",
                    "--command",
                    "definitely-missing-live-session-command",
                ]
            )

        self.assertEqual(exit_code, 2)
        self.assertEqual(calls, [])
        self.assertIn("agent-session", stderr.getvalue())
        self.assertIn("Command not found", stderr.getvalue())

    def test_live_agent_run_rejects_codex_safety_probe_failure_before_registration(self):
        calls = []

        def request_json(url, *, method="GET", payload=None):
            calls.append((url, method, payload))
            return {"agent": {"agent_id": "codex-live", "status": "online"}, "lobby_events": []}

        stderr = StringIO()
        with (
            patch("agentsassemble.cli._request_json", side_effect=request_json),
            patch(
                "agentsassemble.cli.resident_config_setup_error",
                return_value="Codex command does not accept required live-session safety flags.",
            ),
            patch("sys.stdout", StringIO()),
            patch("sys.stderr", stderr),
        ):
            exit_code = main(
                [
                    "live-agent",
                    "run",
                    "--server",
                    "http://room.local",
                    "--agent-id",
                    "codex-live",
                    "--provider-kind",
                    "codex_live_session",
                    "--connection-kind",
                    "live_session",
                    "--poll-interval",
                    "0",
                    "--max-ticks",
                    "1",
                ]
            )

        self.assertEqual(exit_code, 2)
        self.assertEqual(calls, [])
        self.assertIn("codex-live", stderr.getvalue())
        self.assertIn("required live-session safety flags", stderr.getvalue())

    def test_live_agent_run_restores_persisted_cursor_over_http(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "room"
            root.mkdir()
            first_event = append_lobby_event(root, {"name": "나", "side": "mine", "message": "이미 본 이벤트"})
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                server_url = f"http://127.0.0.1:{server.server_port}"
                with patch("sys.stdout", StringIO()):
                    heartbeat_exit = main(
                        [
                            "live-agent",
                            "heartbeat",
                            "--server",
                            server_url,
                            "--agent-id",
                            "agent-single",
                            "--status",
                            "online",
                            "--last-observed-event-id",
                            first_event["id"],
                        ]
                    )
                persisted_before = json.loads((root / "live_agents.json").read_text(encoding="utf-8"))
                second_event = append_lobby_event(root, {"name": "나", "side": "mine", "message": "재접속 후 새 이벤트"})
                stdout = StringIO()
                with patch("sys.stdout", stdout):
                    run_exit = main(
                        [
                            "live-agent",
                            "run",
                            "--server",
                            server_url,
                            "--agent-id",
                            "agent-single",
                            "--display-name",
                            "Single Agent",
                            "--poll-interval",
                            "0",
                            "--cooldown",
                            "0",
                            "--max-chain-depth",
                            "0",
                            "--max-ticks",
                            "1",
                            "--command",
                            sys.executable,
                            "-c",
                            "import sys; sys.stdin.read(); print('Recovered cursor reply')",
                        ]
                    )
            finally:
                server.shutdown()
                server.server_close()

            self.assertEqual(heartbeat_exit, 0)
            self.assertEqual(run_exit, 0)
            persisted_agent = next(agent for agent in persisted_before["agents"] if agent["agent_id"] == "agent-single")
            self.assertEqual(persisted_agent["last_observed_event_id"], first_event["id"])
            events = read_lobby(root)
            replies = [event for event in events if event["actor_id"] == "agent-single"]
            self.assertEqual(len(replies), 1)
            self.assertEqual(replies[0]["message"], "Recovered cursor reply")
            self.assertEqual(replies[0]["source_event_id"], second_event["id"])
            self.assertIn("Resident agent stopped after posting 1 replies", stdout.getvalue())

    def test_live_agent_run_self_service_shutdown_signal_closes_supervisor_cleanly(self):
        installed_shutdown = {}
        restored = threading.Event()
        supervisors = []

        class SignalAwareSupervisor:
            def __init__(self, *args, **kwargs):
                del args, kwargs
                self.closed = False
                supervisors.append(self)

            def run(self):
                installed_shutdown["callback"]()
                raise KeyboardInterrupt()

            def close(self):
                self.closed = True

        def install_shutdown_handler(callback):
            installed_shutdown["callback"] = callback
            return lambda: restored.set()

        with (
            patch("agentsassemble.cli.resident_config_setup_error", return_value=""),
            patch("agentsassemble.cli._SelfServiceResidentSupervisor", SignalAwareSupervisor),
            patch("agentsassemble.cli._install_resident_shutdown_signal_handlers", side_effect=install_shutdown_handler, create=True),
            patch("sys.stdout", StringIO()),
            patch("sys.stderr", StringIO()),
        ):
            exit_code = main(
                [
                    "live-agent",
                    "run",
                    "--server",
                    "http://room.local",
                    "--agent-id",
                    "self-service-signal",
                    "--display-name",
                    "Self Service Signal",
                    "--connection-kind",
                    "self_service",
                    "--poll-interval",
                    "0",
                    "--command",
                    "fake-provider",
                ]
            )

        self.assertEqual(exit_code, 0)
        self.assertTrue(supervisors)
        self.assertTrue(supervisors[0].closed)
        self.assertTrue(restored.is_set())

    def test_live_agent_run_local_cli_shutdown_signal_closes_runner_cleanly(self):
        config = ResidentAgentConfig(
            server="http://room.local",
            agent_id="local-signal",
            display_name="Local Signal",
            provider_kind="local_cli",
            connection_kind="local_cli",
            session_id="",
            endpoint="",
            auth_ref="",
            meeting_id="",
            engagement_mode="always",
            command=["fake"],
            timeout_seconds=30,
            poll_interval=0,
            heartbeat_interval=30,
            cooldown=0,
            max_chain_depth=1,
            max_ticks=0,
        )
        installed_shutdown = {}
        restored = threading.Event()

        class CloseRecordingRunner:
            def __init__(self):
                self.closed = False

            def close(self):
                self.closed = True

        command_runner = CloseRecordingRunner()

        class SignalAwareRunner:
            def __init__(self, *args, **kwargs):
                del args, kwargs

            def run(self):
                installed_shutdown["callback"]()
                raise KeyboardInterrupt()

        def install_shutdown_handler(callback):
            installed_shutdown["callback"] = callback
            return lambda: restored.set()

        with (
            patch("agentsassemble.cli.config_from_args", return_value=config),
            patch("agentsassemble.cli.resident_config_setup_error", return_value=""),
            patch("agentsassemble.cli._command_runner_for_config", return_value=command_runner),
            patch("agentsassemble.cli._install_resident_shutdown_signal_handlers", side_effect=install_shutdown_handler, create=True),
            patch("agentsassemble.cli.LiveAgentRunner", SignalAwareRunner),
            patch("sys.stdout", StringIO()),
            patch("sys.stderr", StringIO()),
        ):
            exit_code = main(
                [
                    "live-agent",
                    "run",
                    "--server",
                    "http://room.local",
                    "--agent-id",
                    "local-signal",
                    "--display-name",
                    "Local Signal",
                    "--connection-kind",
                    "local_cli",
                    "--poll-interval",
                    "0",
                    "--command",
                    "fake-provider",
                ]
            )

        self.assertEqual(exit_code, 0)
        self.assertTrue(command_runner.closed)
        self.assertTrue(restored.is_set())

    def test_live_agent_run_live_session_reuses_one_process_for_multiple_events(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "room"
            root.mkdir()
            first_event = append_lobby_event(root, {"name": "나", "side": "mine", "message": "첫 이벤트"})
            session_script = "\n".join(
                [
                    "import json, sys",
                    "count = 0",
                    "for line in sys.stdin:",
                    "    payload = json.loads(line)",
                    "    count += 1",
                    "    print(json.dumps({'request_id': payload['request_id'], 'message': f'Live session state {count}'}), flush=True)",
                ]
            )
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            result = {}

            def run_resident():
                stdout = StringIO()
                with patch("sys.stdout", stdout):
                    result["exit_code"] = main(
                        [
                            "live-agent",
                            "run",
                            "--server",
                            f"http://127.0.0.1:{server.server_port}",
                            "--agent-id",
                            "stateful-session",
                            "--display-name",
                            "Stateful Session",
                            "--connection-kind",
                            "live_session",
                            "--poll-interval",
                            "0.05",
                            "--cooldown",
                            "0",
                            "--max-chain-depth",
                            "0",
                            "--max-ticks",
                            "50",
                            "--command",
                            sys.executable,
                            "-u",
                            "-c",
                            session_script,
                        ]
                    )
                result["stdout"] = stdout.getvalue()

            resident_thread = threading.Thread(target=run_resident)
            resident_thread.start()
            try:
                deadline = time.time() + 5
                while time.time() < deadline:
                    replies = [event for event in read_lobby(root) if event.get("actor_id") == "stateful-session"]
                    if replies:
                        break
                    time.sleep(0.05)
                else:
                    self.fail("live session resident did not post the first reply")
                second_event = append_lobby_event(root, {"name": "나", "side": "mine", "message": "두 번째 이벤트"})
                resident_thread.join(timeout=6)
            finally:
                server.shutdown()
                server.server_close()
            self.assertFalse(resident_thread.is_alive())

            self.assertEqual(result.get("exit_code"), 0)
            replies = [event for event in read_lobby(root) if event.get("actor_id") == "stateful-session"]
            self.assertEqual([event["message"] for event in replies], ["Live session state 1", "Live session state 2"])
            self.assertEqual([event["source_event_id"] for event in replies], [first_event["id"], second_event["id"]])
            self.assertIn("Resident agent stopped after posting 2 replies", result.get("stdout", ""))

    def test_live_agent_run_terminal_session_reuses_one_pty_process_for_multiple_events(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "room"
            root.mkdir()
            first_event = append_lobby_event(root, {"name": "나", "side": "mine", "message": "첫 이벤트"})
            terminal_script = "\n".join(
                [
                    "import sys",
                    "count = 0",
                    "for line in sys.stdin:",
                    "    count += 1",
                    "    print(f'Terminal session state {count}', flush=True)",
                ]
            )
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            result = {}

            def run_resident():
                stdout = StringIO()
                with patch("sys.stdout", stdout):
                    result["exit_code"] = main(
                        [
                            "live-agent",
                            "run",
                            "--server",
                            f"http://127.0.0.1:{server.server_port}",
                            "--agent-id",
                            "terminal-session",
                            "--display-name",
                            "Terminal Session",
                            "--connection-kind",
                            "terminal_session",
                            "--terminal-idle-timeout",
                            "0.05",
                            "--poll-interval",
                            "0.05",
                            "--cooldown",
                            "0",
                            "--max-chain-depth",
                            "0",
                            "--max-ticks",
                            "50",
                            "--command",
                            sys.executable,
                            "-u",
                            "-c",
                            terminal_script,
                        ]
                    )
                result["stdout"] = stdout.getvalue()

            resident_thread = threading.Thread(target=run_resident)
            resident_thread.start()
            try:
                deadline = time.time() + 5
                while time.time() < deadline:
                    replies = [event for event in read_lobby(root) if event.get("actor_id") == "terminal-session"]
                    if replies:
                        break
                    time.sleep(0.05)
                else:
                    self.fail("terminal session resident did not post the first reply")
                second_event = append_lobby_event(root, {"name": "나", "side": "mine", "message": "두 번째 이벤트"})
                resident_thread.join(timeout=6)
            finally:
                server.shutdown()
                server.server_close()
            self.assertFalse(resident_thread.is_alive())

            self.assertEqual(result.get("exit_code"), 0)
            replies = [event for event in read_lobby(root) if event.get("actor_id") == "terminal-session"]
            self.assertEqual([event["message"] for event in replies], ["Terminal session state 1", "Terminal session state 2"])
            self.assertEqual([event["source_event_id"] for event in replies], [first_event["id"], second_event["id"]])

    def test_live_agent_run_live_session_restarts_after_process_failure_for_new_event(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "room"
            root.mkdir()
            first_event = append_lobby_event(root, {"name": "나", "side": "mine", "message": "첫 이벤트는 실패"})
            marker_path = Path(temp_dir) / "failed-once.txt"
            session_script = "\n".join(
                [
                    "import json, pathlib, sys",
                    f"marker = pathlib.Path({str(marker_path)!r})",
                    "for line in sys.stdin:",
                    "    payload = json.loads(line)",
                    "    if not marker.exists():",
                    "        marker.write_text('failed', encoding='utf-8')",
                    "        sys.exit(9)",
                    "    print(json.dumps({'request_id': payload['request_id'], 'message': 'Recovered live session'}), flush=True)",
                ]
            )
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            result = {}

            def run_resident():
                stdout = StringIO()
                with patch("sys.stdout", stdout):
                    result["exit_code"] = main(
                        [
                            "live-agent",
                            "run",
                            "--server",
                            f"http://127.0.0.1:{server.server_port}",
                            "--agent-id",
                            "recovering-session",
                            "--display-name",
                            "Recovering Session",
                            "--connection-kind",
                            "live_session",
                            "--poll-interval",
                            "0.05",
                            "--cooldown",
                            "0",
                            "--max-chain-depth",
                            "0",
                            "--max-ticks",
                            "60",
                            "--command",
                            sys.executable,
                            "-u",
                            "-c",
                            session_script,
                        ]
                    )
                result["stdout"] = stdout.getvalue()

            resident_thread = threading.Thread(target=run_resident)
            resident_thread.start()
            try:
                deadline = time.time() + 5
                while time.time() < deadline:
                    if marker_path.exists():
                        break
                    time.sleep(0.05)
                else:
                    self.fail("live session resident did not reach the first failing event")
                second_event = append_lobby_event(root, {"name": "나", "side": "mine", "message": "두 번째 이벤트는 복구"})
                resident_thread.join(timeout=6)
            finally:
                server.shutdown()
                server.server_close()
            self.assertFalse(resident_thread.is_alive())

            self.assertEqual(result.get("exit_code"), 0)
            replies = [event for event in read_lobby(root) if event.get("actor_id") == "recovering-session"]
            self.assertEqual([event["message"] for event in replies], ["Recovered live session"])
            self.assertEqual([event["source_event_id"] for event in replies], [second_event["id"]])
            self.assertNotEqual(first_event["id"], replies[0]["source_event_id"])
            self.assertIn("Resident agent stopped after posting 1 replies", result.get("stdout", ""))
