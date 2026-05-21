import unittest
import json
import os
import shlex
import signal
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import StringIO
from unittest.mock import patch

from agentsassemble import cli as cli_module
from agentsassemble.cli import build_parser, main
from agentsassemble.gui import _make_handler, append_lobby_event, read_lobby
from agentsassemble.live_agent_operations import append_live_agent_operation
from agentsassemble.live_agent_runner import ResidentAgentConfig
from agentsassemble.live_agent_smoke import LiveAgentSmokeFailed


class _FailingSelfServiceProcess:
    pid = 4321
    returncode = 7

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        del timeout
        return self.returncode

    def terminate(self):
        self.returncode = -15

    def kill(self):
        self.returncode = -9


def _self_service_resident_config(**overrides) -> ResidentAgentConfig:
    data = {
        "server": "http://room.local",
        "agent_id": "selfer",
        "display_name": "Self Service",
        "provider_kind": "antigravity_cli",
        "connection_kind": "self_service",
        "session_id": "",
        "endpoint": "",
        "auth_ref": "",
        "meeting_id": "resident-m1",
        "engagement_mode": "always",
        "command": ["agent"],
        "timeout_seconds": 120,
        "poll_interval": 0,
        "heartbeat_interval": 30,
        "cooldown": 5,
        "max_chain_depth": 1,
    }
    data.update(overrides)
    return ResidentAgentConfig(**data)


def _pid_exists(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def _wait_for_pid_exit(pid: int, timeout: float = 2.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not _pid_exists(pid):
            return True
        time.sleep(0.01)
    return not _pid_exists(pid)


def _kill_pid(pid: int) -> None:
    stop_signal = getattr(signal, "SIGKILL", getattr(signal, "SIGTERM", None))
    if stop_signal is None:
        return
    os.kill(pid, stop_signal)


class CliTimeoutTests(unittest.TestCase):
    def test_codex_timeout_can_be_disabled(self):
        args = build_parser().parse_args(["demo", "--adapter", "codex", "--codex-timeout", "none"])

        self.assertIsNone(args.codex_timeout)

    def test_demo_accepts_codex_live_adapter(self):
        args = build_parser().parse_args(["demo", "--adapter", "codex-live"])

        self.assertEqual(args.adapter, "codex-live")

    def test_demo_accepts_council_config_path(self):
        args = build_parser().parse_args(["demo", "--council-config", "configs/silly-fake-expert.json"])

        self.assertEqual(args.council_config, "configs/silly-fake-expert.json")

    def test_demo_accepts_meeting_mode_and_moderator_options(self):
        args = build_parser().parse_args(["demo", "--meeting-mode", "free-chat", "--moderator", "off"])

        self.assertEqual(args.meeting_mode, "free-chat")
        self.assertEqual(args.moderator, "off")

    def test_demo_passes_meeting_mode_and_moderator_to_runner(self):
        with patch("agentsassemble.cli.run_demo_meeting") as run_demo:
            exit_code = main(["demo", "--meeting-mode", "free-chat", "--moderator", "off", "--output-root", "out"])

        self.assertEqual(exit_code, 0)
        run_demo.assert_called_once()
        kwargs = run_demo.call_args.kwargs
        self.assertEqual(kwargs["meeting_mode"], "free_chat")
        self.assertFalse(kwargs["moderator_enabled"])
        self.assertEqual(kwargs["output_root"], Path("out"))

    def test_demo_accepts_follow_up_metadata(self):
        args = build_parser().parse_args(
            [
                "demo",
                "--follow-up-of",
                "meeting-1",
                "--follow-up-from",
                ".agentsassemble/meetings/meeting-1",
                "--follow-up-note",
                "reopen unresolved caveat",
            ]
        )

        self.assertEqual(args.follow_up_of, "meeting-1")
        self.assertEqual(args.follow_up_from, ".agentsassemble/meetings/meeting-1")
        self.assertEqual(args.follow_up_note, "reopen unresolved caveat")

    def test_deep_codex_defaults_to_no_timeout(self):
        args = build_parser().parse_args(["demo", "--adapter", "codex", "--research-depth", "deep"])

        self.assertIsNone(args.codex_timeout)

    def test_claude_bridge_parses_bridge_command_without_overwriting_subcommand(self):
        args = build_parser().parse_args(["claude-bridge", "--token", "bridge-token", "--command", "claude"])

        self.assertEqual(args.command, "claude-bridge")
        self.assertEqual(args.bridge_command, "claude")

    def test_gui_accepts_live_agent_autostart_options(self):
        with patch("agentsassemble.cli.serve_gui") as serve_gui:
            exit_code = main(
                [
                    "gui",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    "0",
                    "--output-root",
                    "out",
                    "--live-agent-config",
                    "configs/fake-live-agents.json",
                    "--live-agent-group-id",
                    "boot",
                    "--live-agent-auto-restart",
                    "--live-agent-max-restarts",
                    "3",
                    "--live-agent-restart-backoff-seconds",
                    "1.5",
                    "--live-agent-stale-restart-after-seconds",
                    "120",
                ]
            )

        self.assertEqual(exit_code, 0)
        serve_gui.assert_called_once()
        kwargs = serve_gui.call_args.kwargs
        self.assertEqual(kwargs["host"], "127.0.0.1")
        self.assertEqual(kwargs["port"], 0)
        self.assertEqual(kwargs["output_root"], Path("out"))
        self.assertEqual(kwargs["live_agent_config"], Path("configs/fake-live-agents.json"))
        self.assertEqual(kwargs["live_agent_group_id"], "boot")
        self.assertTrue(kwargs["live_agent_auto_restart"])
        self.assertEqual(kwargs["live_agent_max_restarts"], 3)
        self.assertEqual(kwargs["live_agent_restart_backoff_seconds"], 1.5)
        self.assertEqual(kwargs["live_agent_stale_restart_after_seconds"], 120.0)

    def test_sessions_list_outputs_codex_session_index_as_json(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            codex_home = Path(temp_dir) / ".codex"
            codex_home.mkdir()
            (codex_home / "session_index.jsonl").write_text(
                json.dumps(
                    {
                        "id": "019e3038-39cc-76a2-a746-5ba8c0f3b408",
                        "thread_name": "인수인계 받기",
                        "updated_at": "2026-05-16T09:57:44Z",
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            stdout = StringIO()

            with patch.dict("os.environ", {"CODEX_HOME": str(codex_home)}), patch("sys.stdout", stdout):
                exit_code = main(["sessions", "list", "--json"])

            self.assertEqual(exit_code, 0)
            payload = json.loads(stdout.getvalue())
            self.assertEqual(payload[0]["id"], "019e3038-39cc-76a2-a746-5ba8c0f3b408")
            self.assertEqual(payload[0]["thread_name"], "인수인계 받기")

    def test_sessions_invite_writes_gitignored_codex_live_agent_config(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / "codex-live-session.local.json"
            stdout = StringIO()

            with patch("sys.stdout", stdout):
                exit_code = main(
                    [
                        "sessions",
                        "invite",
                        "019e3038-39cc-76a2-a746-5ba8c0f3b408",
                        "--role",
                        "lore_lawyer",
                        "--output",
                        str(output),
                    ]
                )

            self.assertEqual(exit_code, 0)
            self.assertIn(str(output), stdout.getvalue())
            config = json.loads(output.read_text(encoding="utf-8"))
            bindings = {binding["role_id"]: binding for binding in config["agent_bindings"]}
            self.assertEqual(bindings["lore_lawyer"]["join_mode"], "current_session")
            self.assertEqual(bindings["lore_lawyer"]["session_id"], "019e3038-39cc-76a2-a746-5ba8c0f3b408")
            self.assertEqual(bindings["show_me_the_feats"]["join_mode"], "fresh")
            self.assertEqual(bindings["fanboard_skeptic"]["join_mode"], "fresh")

    def test_sessions_invite_can_post_through_room_server(self):
        response = {
            "binding": {
                "role_id": "lore_lawyer",
                "agent_id": "codex-live-lore-lawyer",
                "join_mode": "current_session",
                "provider_id": "codex-live",
            }
        }
        stdout = StringIO()

        with patch("agentsassemble.cli._request_json", return_value=response) as request_json:
            with patch("sys.stdout", stdout):
                exit_code = main(
                    [
                        "sessions",
                        "invite",
                        "019e3038-39cc-76a2-a746-5ba8c0f3b408",
                        "--role",
                        "lore_lawyer",
                        "--server",
                        "http://room.local",
                        "--meeting-id",
                        "resident-m1",
                        "--json",
                    ]
                )

        self.assertEqual(exit_code, 0)
        request_json.assert_called_once_with(
            "http://room.local/api/codex-sessions/invite",
            method="POST",
            payload={
                "session_id": "019e3038-39cc-76a2-a746-5ba8c0f3b408",
                "role_id": "lore_lawyer",
                "meeting_id": "resident-m1",
            },
        )
        self.assertEqual(json.loads(stdout.getvalue()), response)

    def test_sessions_invite_server_compact_output_uses_real_response_binding(self):
        response = {"binding": {"role_id": "lore_lawyer", "agent_id": "codex-live-lore-lawyer"}}
        stdout = StringIO()

        with patch("agentsassemble.cli._request_json", return_value=response):
            with patch("sys.stdout", stdout):
                exit_code = main(
                    [
                        "sessions",
                        "invite",
                        "019e3038-39cc-76a2-a746-5ba8c0f3b408",
                        "--role",
                        "lore_lawyer",
                        "--server",
                        "http://room.local",
                    ]
                )

        self.assertEqual(exit_code, 0)
        self.assertIn("Invited lore_lawyer as codex-live-lore-lawyer", stdout.getvalue())

    def test_sessions_invite_server_transport_error_returns_cli_error(self):
        stderr = StringIO()

        with patch("agentsassemble.cli._request_json", side_effect=urllib.error.URLError("down")):
            with patch("sys.stderr", stderr):
                exit_code = main(
                    [
                        "sessions",
                        "invite",
                        "019e3038-39cc-76a2-a746-5ba8c0f3b408",
                        "--role",
                        "lore_lawyer",
                        "--server",
                        "http://room.local",
                    ]
                )

        self.assertEqual(exit_code, 2)
        self.assertIn("error:", stderr.getvalue())

    def test_sessions_live_agent_config_writes_resident_config_from_invite_file(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            invite_path = root / "codex-live-session.local.json"
            output_path = root / "live-agents.codex-session.local.json"
            invite_path.write_text(
                json.dumps(
                    {
                        "agent_bindings": [
                            {
                                "agent_id": "codex-live-lore",
                                "role_id": "lore_lawyer",
                                "provider_id": "codex-live",
                                "join_mode": "current_session",
                                "session_id": "019e3038-39cc-76a2-a746-5ba8c0f3b408",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            stdout = StringIO()

            with patch("sys.stdout", stdout):
                exit_code = main(
                    [
                        "sessions",
                        "live-agent-config",
                        "--input",
                        str(invite_path),
                        "--output",
                        str(output_path),
                        "--server",
                        "http://room.local",
                        "--meeting-id",
                        "resident-m1",
                        "--engagement-mode",
                        "moderator_called",
                        "--json",
                    ]
                )

            self.assertEqual(exit_code, 0)
            self.assertTrue(output_path.exists())
            written = json.loads(output_path.read_text(encoding="utf-8"))
            self.assertEqual(written["server"], "http://room.local")
            self.assertEqual(written["agents"][0]["agent_id"], "codex-live-lore")
            self.assertEqual(written["agents"][0]["provider_kind"], "codex_live_session")
            self.assertEqual(written["agents"][0]["connection_kind"], "live_session")
            self.assertEqual(written["agents"][0]["meeting_id"], "resident-m1")
            self.assertEqual(written["agents"][0]["engagement_mode"], "moderator_called")
            payload = json.loads(stdout.getvalue())
            self.assertEqual(payload["output"], str(output_path))
            self.assertEqual(
                payload["next_commands"]["preflight"],
                [
                    "python3",
                    "-m",
                    "agentsassemble.cli",
                    "live-agent",
                    "preflight",
                    "--config",
                    str(output_path),
                ],
            )
            self.assertEqual(
                payload["next_commands"]["ensure_session"],
                [
                    "python3",
                    "-m",
                    "agentsassemble.cli",
                    "live-agent",
                    "ensure-session",
                    "--server",
                    "http://room.local",
                    "--meeting-id",
                    "resident-m1",
                    "--group-id",
                    "live-agents.codex-session.local",
                    "--agent-config",
                    str(invite_path),
                    "--live-agent-config",
                    str(output_path),
                ],
            )

    def test_sessions_live_agent_config_compact_output_quotes_next_commands(self):
        with tempfile.TemporaryDirectory(prefix="codex path ") as temp_dir:
            root = Path(temp_dir)
            invite_path = root / "codex invite.json"
            output_path = root / "live agents.json"
            invite_path.write_text(
                json.dumps(
                    {
                        "agent_bindings": [
                            {
                                "agent_id": "codex-live-lore",
                                "role_id": "lore_lawyer",
                                "provider_id": "codex-live",
                                "session_id": "019e3038-39cc-76a2-a746-5ba8c0f3b408",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            stdout = StringIO()

            with patch("sys.stdout", stdout):
                exit_code = main(
                    [
                        "sessions",
                        "live-agent-config",
                        "--input",
                        str(invite_path),
                        "--output",
                        str(output_path),
                        "--server",
                        "http://room.local/with space",
                        "--meeting-id",
                        "resident m1",
                    ]
                )

            self.assertEqual(exit_code, 0)
            output = stdout.getvalue()
            self.assertIn("Next preflight: python3 -m agentsassemble.cli live-agent preflight --config", output)
            self.assertIn(f"'{output_path}'", output)
            self.assertIn(f"'{invite_path}'", output)
            self.assertIn("'http://room.local/with space'", output)
            self.assertIn("'resident m1'", output)
            self.assertIn("--group-id live-agents", output)

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
        self.assertEqual(payload["commands"]["roster_gate"][-3:], ["--require-match", "--fail-on-attention", "--json"])
        self.assertEqual(payload["templates"]["say"][-2:], ["--", "{message}"])
        self.assertIn("{source_event_id}", payload["templates"]["say"])
        self.assertIn("{auto_chain_depth}", payload["templates"]["say"])
        self.assertEqual(payload["templates"]["official_reply"][-2:], ["--", "{message}"])
        self.assertIn("{meeting_id}", payload["templates"]["official_reply"])
        self.assertIn("{source_event_id}", payload["templates"]["official_reply"])
        serialized = json.dumps(payload)
        self.assertNotIn("endpoint", serialized)
        self.assertNotIn("auth", serialized)
        self.assertNotIn("session_id", serialized)
        self.assertNotIn("config_path", serialized)
        self.assertNotIn("log_path", serialized)

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
        self.assertIn("'http://room.local/with space'", output)
        self.assertIn("'agent one'", output)

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
            ]
        )

        self.assertEqual(args.live_agent_command, "list")
        self.assertTrue(args.as_json)
        self.assertTrue(args.fail_on_attention)

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
                    "status": "online",
                    "engagement_mode": "always",
                    "meeting_id": "resident-m1",
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
                    "status": "error",
                    "meeting_id": "resident-m1",
                    "endpoint": "http://secret.local/bridge",
                    "auth_ref": "literal:secret-token",
                    "config_path": "/Users/me/private/live-agents.json",
                    "session_id": "private-session-id",
                    "last_error": "failed with token=secret-token in /Users/me/private/live-agents.json",
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
        self.assertEqual(agent["last_error"], "Live-agent presence error details redacted.")
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

    def test_live_agent_operations_list_parses_limit_and_json(self):
        args = build_parser().parse_args(
            [
                "live-agent",
                "operations",
                "list",
                "--server",
                "http://room.local",
                "--limit",
                "3",
                "--json",
            ]
        )

        self.assertEqual(args.live_agent_command, "operations")
        self.assertEqual(args.live_agent_operations_command, "list")
        self.assertEqual(args.limit, 3)
        self.assertTrue(args.as_json)

    def test_live_agent_operations_list_parses_fail_on_attention(self):
        args = build_parser().parse_args(
            [
                "live-agent",
                "operations",
                "list",
                "--server",
                "http://room.local",
                "--fail-on-attention",
            ]
        )

        self.assertEqual(args.live_agent_command, "operations")
        self.assertEqual(args.live_agent_operations_command, "list")
        self.assertTrue(args.fail_on_attention)

    def test_live_agent_operations_list_parses_filters(self):
        args = build_parser().parse_args(
            [
                "live-agent",
                "operations",
                "list",
                "--server",
                "http://room.local",
                "--operation",
                "session.start",
                "--target-id",
                "resident-m1",
                "--status",
                "success",
                "--scan-limit",
                "1000",
            ]
        )

        self.assertEqual(args.live_agent_command, "operations")
        self.assertEqual(args.live_agent_operations_command, "list")
        self.assertEqual(args.operation, "session.start")
        self.assertEqual(args.target_id, "resident-m1")
        self.assertEqual(args.status, "success")
        self.assertEqual(args.scan_limit, 1000)

    def test_live_agent_session_runs_list_parses_limit_and_json(self):
        args = build_parser().parse_args(
            [
                "live-agent",
                "session-runs",
                "list",
                "--server",
                "http://room.local",
                "--limit",
                "3",
                "--json",
            ]
        )

        self.assertEqual(args.live_agent_command, "session-runs")
        self.assertEqual(args.live_agent_session_runs_command, "list")
        self.assertEqual(args.limit, 3)
        self.assertTrue(args.as_json)

    def test_live_agent_session_runs_list_parses_include_readiness(self):
        args = build_parser().parse_args(
            [
                "live-agent",
                "session-runs",
                "list",
                "--server",
                "http://room.local",
                "--include-readiness",
            ]
        )

        self.assertEqual(args.live_agent_command, "session-runs")
        self.assertEqual(args.live_agent_session_runs_command, "list")
        self.assertTrue(args.include_readiness)

    def test_live_agent_session_runs_list_parses_fail_on_attention(self):
        args = build_parser().parse_args(
            [
                "live-agent",
                "session-runs",
                "list",
                "--server",
                "http://room.local",
                "--fail-on-attention",
            ]
        )

        self.assertEqual(args.live_agent_command, "session-runs")
        self.assertEqual(args.live_agent_session_runs_command, "list")
        self.assertTrue(args.fail_on_attention)

    def test_live_agent_session_runs_list_parses_meeting_group_filters(self):
        args = build_parser().parse_args(
            [
                "live-agent",
                "session-runs",
                "list",
                "--server",
                "http://room.local",
                "--meeting-id",
                "resident-m1",
                "--group-id",
                "resident-main",
            ]
        )

        self.assertEqual(args.live_agent_command, "session-runs")
        self.assertEqual(args.live_agent_session_runs_command, "list")
        self.assertEqual(args.meeting_id, "resident-m1")
        self.assertEqual(args.group_id, "resident-main")

    def test_live_agent_session_runs_list_parses_run_id_filter(self):
        args = build_parser().parse_args(
            [
                "live-agent",
                "session-runs",
                "list",
                "--server",
                "http://room.local",
                "--run-id",
                "run-1",
            ]
        )

        self.assertEqual(args.live_agent_command, "session-runs")
        self.assertEqual(args.live_agent_session_runs_command, "list")
        self.assertEqual(args.run_id, "run-1")

    def test_live_agent_session_runs_retry_now_parses_run_id_and_json(self):
        args = build_parser().parse_args(
            [
                "live-agent",
                "session-runs",
                "retry-now",
                "--server",
                "http://room.local",
                "--run-id",
                "retry-later",
                "--json",
            ]
        )

        self.assertEqual(args.live_agent_command, "session-runs")
        self.assertEqual(args.live_agent_session_runs_command, "retry-now")
        self.assertEqual(args.run_id, "retry-later")
        self.assertTrue(args.as_json)

    def test_live_agent_session_runs_retry_now_parses_meeting_group_target(self):
        args = build_parser().parse_args(
            [
                "live-agent",
                "session-runs",
                "retry-now",
                "--server",
                "http://room.local",
                "--meeting-id",
                "resident-m1",
                "--group-id",
                "resident-main",
                "--json",
            ]
        )

        self.assertEqual(args.live_agent_command, "session-runs")
        self.assertEqual(args.live_agent_session_runs_command, "retry-now")
        self.assertEqual(args.run_id, "")
        self.assertEqual(args.meeting_id, "resident-m1")
        self.assertEqual(args.group_id, "resident-main")
        self.assertTrue(args.as_json)

    def test_live_agent_session_runs_pause_resume_parse_run_id_and_json(self):
        pause_args = build_parser().parse_args(
            [
                "live-agent",
                "session-runs",
                "pause",
                "--server",
                "http://room.local",
                "--run-id",
                "run-paused",
                "--json",
            ]
        )
        resume_args = build_parser().parse_args(
            [
                "live-agent",
                "session-runs",
                "resume",
                "--server",
                "http://room.local",
                "--run-id",
                "run-paused",
                "--json",
            ]
        )

        self.assertEqual(pause_args.live_agent_session_runs_command, "pause")
        self.assertEqual(pause_args.run_id, "run-paused")
        self.assertTrue(pause_args.as_json)
        self.assertEqual(resume_args.live_agent_session_runs_command, "resume")
        self.assertEqual(resume_args.run_id, "run-paused")
        self.assertTrue(resume_args.as_json)

    def test_live_agent_session_runs_pause_resume_parse_meeting_group_target(self):
        pause_args = build_parser().parse_args(
            [
                "live-agent",
                "session-runs",
                "pause",
                "--server",
                "http://room.local",
                "--meeting-id",
                "resident-m1",
                "--group-id",
                "resident-main",
                "--json",
            ]
        )
        resume_args = build_parser().parse_args(
            [
                "live-agent",
                "session-runs",
                "resume",
                "--server",
                "http://room.local",
                "--meeting-id",
                "resident-m1",
                "--group-id",
                "resident-main",
                "--json",
            ]
        )
        stop_args = build_parser().parse_args(
            [
                "live-agent",
                "session-runs",
                "stop",
                "--server",
                "http://room.local",
                "--meeting-id",
                "resident-m1",
                "--group-id",
                "resident-main",
                "--json",
            ]
        )

        self.assertEqual(pause_args.live_agent_session_runs_command, "pause")
        self.assertEqual(pause_args.run_id, "")
        self.assertEqual(pause_args.meeting_id, "resident-m1")
        self.assertEqual(pause_args.group_id, "resident-main")
        self.assertTrue(pause_args.as_json)
        self.assertEqual(resume_args.live_agent_session_runs_command, "resume")
        self.assertEqual(resume_args.run_id, "")
        self.assertEqual(resume_args.meeting_id, "resident-m1")
        self.assertEqual(resume_args.group_id, "resident-main")
        self.assertTrue(resume_args.as_json)
        self.assertEqual(stop_args.live_agent_session_runs_command, "stop")
        self.assertEqual(stop_args.run_id, "")
        self.assertEqual(stop_args.meeting_id, "resident-m1")
        self.assertEqual(stop_args.group_id, "resident-main")
        self.assertTrue(stop_args.as_json)

    def test_live_agent_session_runs_list_fetches_durable_runs(self):
        payload = {
            "runs": [
                {
                    "run_id": "run-1",
                    "action": "ensure",
                    "status": "ready",
                    "active": True,
                    "meeting_id": "resident-m1",
                    "group_id": "resident-main",
                    "phase": "none",
                    "reconcile_count": 1,
                }
            ]
        }
        with patch("agentsassemble.cli._request_json", return_value=payload) as request_json:
            with patch("sys.stdout", StringIO()) as stdout:
                exit_code = main(
                    [
                        "live-agent",
                        "session-runs",
                        "list",
                        "--server",
                        "http://room.local",
                        "--limit",
                        "3",
                    ]
                )

        self.assertEqual(exit_code, 0)
        request_json.assert_called_once_with("http://room.local/api/live-agent-session-runs?limit=3")
        self.assertIn("run-1 ensure ready resident-m1 resident-main active", stdout.getvalue())

    def test_live_agent_session_runs_list_prints_reconcile_backoff_summary(self):
        payload = {
            "runs": [
                {
                    "run_id": "run-1",
                    "action": "ensure",
                    "status": "degraded",
                    "active": True,
                    "meeting_id": "resident-m1",
                    "group_id": "resident-main",
                    "phase": "reconcile_failed",
                    "reconcile_failure_count": 2,
                    "reconcile_backoff_seconds": 120,
                    "next_reconcile_at": "2026-05-21T10:07:00+00:00",
                }
            ]
        }
        with patch("agentsassemble.cli._request_json", return_value=payload):
            with patch("sys.stdout", StringIO()) as stdout:
                exit_code = main(
                    [
                        "live-agent",
                        "session-runs",
                        "list",
                        "--server",
                        "http://room.local",
                    ]
                )

        self.assertEqual(exit_code, 0)
        output = stdout.getvalue()
        self.assertIn("run-1 ensure degraded resident-m1 resident-main active", output)
        self.assertIn("reconcile_failures=2", output)
        self.assertIn("reconcile_backoff=120s", output)
        self.assertIn("next_reconcile=2026-05-21T10:07:00+00:00", output)

    def test_live_agent_session_runs_retry_now_posts_target_run(self):
        payload = {
            "status": "scheduled",
            "session_run": {
                "run_id": "retry-later",
                "action": "ensure",
                "status": "degraded",
                "active": True,
                "meeting_id": "resident-m1",
                "group_id": "resident-main",
                "phase": "retry_requested",
                "reconcile_failure_count": 2,
                "reconcile_backoff_seconds": 0,
                "next_reconcile_at": "",
            },
            "results": [],
        }
        with patch("agentsassemble.cli._request_json", return_value=payload) as request_json:
            with patch("sys.stdout", StringIO()) as stdout:
                exit_code = main(
                    [
                        "live-agent",
                        "session-runs",
                        "retry-now",
                        "--server",
                        "http://room.local",
                        "--run-id",
                        "retry-later",
                    ]
                )

        self.assertEqual(exit_code, 0)
        request_json.assert_called_once_with(
            "http://room.local/api/live-agent-session-runs/retry-later/retry-now",
            method="POST",
            payload={},
            timeout_seconds=10.0,
        )
        self.assertIn("Scheduled live-agent session run retry", stdout.getvalue())
        self.assertIn("retry-later ensure degraded resident-m1 resident-main active", stdout.getvalue())

    def test_live_agent_session_runs_retry_now_posts_meeting_group_target(self):
        payload = {
            "status": "reconciled",
            "session_run": {
                "run_id": "latest-run",
                "action": "ensure",
                "status": "ready",
                "active": True,
                "meeting_id": "resident-m1",
                "group_id": "resident-main",
                "phase": "recover",
            },
            "results": [{"run_id": "latest-run", "status": "ready"}],
        }
        with patch("agentsassemble.cli._request_json", return_value=payload) as request_json:
            with patch("sys.stdout", StringIO()) as stdout:
                exit_code = main(
                    [
                        "live-agent",
                        "session-runs",
                        "retry-now",
                        "--server",
                        "http://room.local",
                        "--meeting-id",
                        "resident-m1",
                        "--group-id",
                        "resident-main",
                    ]
                )

        self.assertEqual(exit_code, 0)
        request_json.assert_called_once_with(
            "http://room.local/api/live-agent-session-runs/retry-now",
            method="POST",
            payload={"meeting_id": "resident-m1", "group_id": "resident-main"},
            timeout_seconds=10.0,
        )
        self.assertIn("Retried live-agent session run retry", stdout.getvalue())
        self.assertIn("latest-run ensure ready resident-m1 resident-main active", stdout.getvalue())

    def test_live_agent_session_runs_retry_now_run_id_takes_precedence_over_meeting_group(self):
        payload = {
            "status": "scheduled",
            "session_run": {
                "run_id": "exact-run",
                "action": "ensure",
                "status": "degraded",
                "active": True,
                "meeting_id": "resident-m2",
                "group_id": "resident-alt",
                "phase": "retry_requested",
            },
            "results": [],
        }
        with patch("agentsassemble.cli._request_json", return_value=payload) as request_json:
            with patch("sys.stdout", StringIO()):
                exit_code = main(
                    [
                        "live-agent",
                        "session-runs",
                        "retry-now",
                        "--server",
                        "http://room.local",
                        "--run-id",
                        "exact-run",
                        "--meeting-id",
                        "resident-m1",
                        "--group-id",
                        "resident-main",
                    ]
                )

        self.assertEqual(exit_code, 0)
        request_json.assert_called_once_with(
            "http://room.local/api/live-agent-session-runs/exact-run/retry-now",
            method="POST",
            payload={},
            timeout_seconds=10.0,
        )

    def test_live_agent_session_runs_retry_now_refuses_missing_target(self):
        with patch("sys.stderr", StringIO()) as stderr:
            exit_code = main(
                [
                    "live-agent",
                    "session-runs",
                    "retry-now",
                    "--server",
                    "http://room.local",
                ]
            )

        self.assertEqual(exit_code, 2)
        self.assertIn("requires --run-id or both --meeting-id and --group-id", stderr.getvalue())

    def test_live_agent_session_runs_retry_now_prints_skipped_result(self):
        payload = {
            "status": "skipped",
            "session_run": {
                "run_id": "ready-run",
                "action": "ensure",
                "status": "ready",
                "active": True,
                "meeting_id": "resident-m1",
                "group_id": "resident-main",
                "phase": "none",
            },
            "results": [],
        }
        with patch("agentsassemble.cli._request_json", return_value=payload):
            with patch("sys.stdout", StringIO()) as stdout:
                exit_code = main(
                    [
                        "live-agent",
                        "session-runs",
                        "retry-now",
                        "--server",
                        "http://room.local",
                        "--run-id",
                        "ready-run",
                    ]
                )

        self.assertEqual(exit_code, 0)
        self.assertIn("Skipped live-agent session run retry", stdout.getvalue())
        self.assertIn("ready-run ensure ready resident-m1 resident-main active", stdout.getvalue())

    def test_live_agent_session_runs_pause_posts_target_run(self):
        payload = {
            "status": "paused",
            "session_run": {
                "run_id": "run-paused",
                "action": "ensure",
                "status": "paused",
                "active": False,
                "meeting_id": "resident-m1",
                "group_id": "resident-main",
                "phase": "paused",
                "paused_status": "degraded",
            },
        }
        with patch("agentsassemble.cli._request_json", return_value=payload) as request_json:
            with patch("sys.stdout", StringIO()) as stdout:
                exit_code = main(
                    [
                        "live-agent",
                        "session-runs",
                        "pause",
                        "--server",
                        "http://room.local",
                        "--run-id",
                        "run-paused",
                    ]
                )

        self.assertEqual(exit_code, 0)
        request_json.assert_called_once_with(
            "http://room.local/api/live-agent-session-runs/run-paused/pause",
            method="POST",
            payload={},
            timeout_seconds=10.0,
        )
        self.assertIn("Paused live-agent session run", stdout.getvalue())
        self.assertIn("run-paused ensure paused resident-m1 resident-main inactive", stdout.getvalue())
        self.assertIn("paused_from=degraded", stdout.getvalue())

    def test_live_agent_session_runs_resume_posts_target_run(self):
        payload = {
            "status": "resumed",
            "session_run": {
                "run_id": "run-paused",
                "action": "ensure",
                "status": "degraded",
                "active": True,
                "meeting_id": "resident-m1",
                "group_id": "resident-main",
                "phase": "resume_requested",
            },
        }
        with patch("agentsassemble.cli._request_json", return_value=payload) as request_json:
            with patch("sys.stdout", StringIO()) as stdout:
                exit_code = main(
                    [
                        "live-agent",
                        "session-runs",
                        "resume",
                        "--server",
                        "http://room.local",
                        "--run-id",
                        "run-paused",
                    ]
                )

        self.assertEqual(exit_code, 0)
        request_json.assert_called_once_with(
            "http://room.local/api/live-agent-session-runs/run-paused/resume",
            method="POST",
            payload={},
            timeout_seconds=10.0,
        )
        self.assertIn("Resumed live-agent session run", stdout.getvalue())
        self.assertIn("run-paused ensure degraded resident-m1 resident-main active", stdout.getvalue())

    def test_live_agent_session_runs_pause_resume_post_meeting_group_target(self):
        pause_payload = {
            "status": "paused",
            "session_run": {
                "run_id": "latest-run",
                "action": "ensure",
                "status": "paused",
                "active": False,
                "meeting_id": "resident-m1",
                "group_id": "resident-main",
                "phase": "paused",
                "paused_status": "degraded",
            },
        }
        resume_payload = {
            "status": "resumed",
            "session_run": {
                "run_id": "latest-run",
                "action": "ensure",
                "status": "degraded",
                "active": True,
                "meeting_id": "resident-m1",
                "group_id": "resident-main",
                "phase": "resume_requested",
            },
        }
        with patch("agentsassemble.cli._request_json", side_effect=[pause_payload, resume_payload]) as request_json:
            with patch("sys.stdout", StringIO()) as stdout:
                pause_exit = main(
                    [
                        "live-agent",
                        "session-runs",
                        "pause",
                        "--server",
                        "http://room.local",
                        "--meeting-id",
                        "resident-m1",
                        "--group-id",
                        "resident-main",
                    ]
                )
                resume_exit = main(
                    [
                        "live-agent",
                        "session-runs",
                        "resume",
                        "--server",
                        "http://room.local",
                        "--meeting-id",
                        "resident-m1",
                        "--group-id",
                        "resident-main",
                    ]
                )

        self.assertEqual(pause_exit, 0)
        self.assertEqual(resume_exit, 0)
        self.assertEqual(
            request_json.call_args_list[0].args,
            ("http://room.local/api/live-agent-session-runs/pause",),
        )
        self.assertEqual(request_json.call_args_list[0].kwargs["method"], "POST")
        self.assertEqual(request_json.call_args_list[0].kwargs["payload"], {"meeting_id": "resident-m1", "group_id": "resident-main"})
        self.assertEqual(
            request_json.call_args_list[1].args,
            ("http://room.local/api/live-agent-session-runs/resume",),
        )
        self.assertEqual(request_json.call_args_list[1].kwargs["method"], "POST")
        self.assertEqual(request_json.call_args_list[1].kwargs["payload"], {"meeting_id": "resident-m1", "group_id": "resident-main"})
        self.assertIn("Paused live-agent session run", stdout.getvalue())
        self.assertIn("Resumed live-agent session run", stdout.getvalue())

    def test_live_agent_session_runs_stop_posts_meeting_group_target(self):
        payload = {
            "status": "stopped",
            "session_run": {
                "run_id": "latest-run",
                "action": "ensure",
                "status": "stopped",
                "active": False,
                "meeting_id": "resident-m1",
                "group_id": "resident-main",
                "phase": "operator_stop",
            },
        }
        with patch("agentsassemble.cli._request_json", return_value=payload) as request_json:
            with patch("sys.stdout", StringIO()) as stdout:
                exit_code = main(
                    [
                        "live-agent",
                        "session-runs",
                        "stop",
                        "--server",
                        "http://room.local",
                        "--meeting-id",
                        "resident-m1",
                        "--group-id",
                        "resident-main",
                    ]
                )

        self.assertEqual(exit_code, 0)
        request_json.assert_called_once_with(
            "http://room.local/api/live-agent-session-runs/stop",
            method="POST",
            payload={"meeting_id": "resident-m1", "group_id": "resident-main"},
            timeout_seconds=10.0,
        )
        self.assertIn("Stopped live-agent session run", stdout.getvalue())
        self.assertIn("latest-run ensure stopped resident-m1 resident-main inactive", stdout.getvalue())

    def test_live_agent_session_runs_pause_run_id_takes_precedence_over_meeting_group(self):
        payload = {
            "status": "paused",
            "session_run": {
                "run_id": "exact-run",
                "action": "ensure",
                "status": "paused",
                "active": False,
                "meeting_id": "resident-m2",
                "group_id": "resident-alt",
                "phase": "paused",
                "paused_status": "degraded",
            },
        }
        with patch("agentsassemble.cli._request_json", return_value=payload) as request_json:
            with patch("sys.stdout", StringIO()):
                exit_code = main(
                    [
                        "live-agent",
                        "session-runs",
                        "pause",
                        "--server",
                        "http://room.local",
                        "--run-id",
                        "exact-run",
                        "--meeting-id",
                        "resident-m1",
                        "--group-id",
                        "resident-main",
                    ]
                )

        self.assertEqual(exit_code, 0)
        request_json.assert_called_once_with(
            "http://room.local/api/live-agent-session-runs/exact-run/pause",
            method="POST",
            payload={},
            timeout_seconds=10.0,
        )

    def test_live_agent_session_runs_resume_run_id_takes_precedence_over_meeting_group(self):
        payload = {
            "status": "resumed",
            "session_run": {
                "run_id": "exact-run",
                "action": "ensure",
                "status": "degraded",
                "active": True,
                "meeting_id": "resident-m2",
                "group_id": "resident-alt",
                "phase": "resume_requested",
            },
        }
        with patch("agentsassemble.cli._request_json", return_value=payload) as request_json:
            with patch("sys.stdout", StringIO()):
                exit_code = main(
                    [
                        "live-agent",
                        "session-runs",
                        "resume",
                        "--server",
                        "http://room.local",
                        "--run-id",
                        "exact-run",
                        "--meeting-id",
                        "resident-m1",
                        "--group-id",
                        "resident-main",
                    ]
                )

        self.assertEqual(exit_code, 0)
        request_json.assert_called_once_with(
            "http://room.local/api/live-agent-session-runs/exact-run/resume",
            method="POST",
            payload={},
            timeout_seconds=10.0,
        )

    def test_live_agent_session_runs_stop_run_id_takes_precedence_over_meeting_group(self):
        payload = {
            "status": "stopped",
            "session_run": {
                "run_id": "exact-run",
                "action": "ensure",
                "status": "stopped",
                "active": False,
                "meeting_id": "resident-m2",
                "group_id": "resident-alt",
                "phase": "operator_stop",
            },
        }
        with patch("agentsassemble.cli._request_json", return_value=payload) as request_json:
            with patch("sys.stdout", StringIO()):
                exit_code = main(
                    [
                        "live-agent",
                        "session-runs",
                        "stop",
                        "--server",
                        "http://room.local",
                        "--run-id",
                        "exact-run",
                        "--meeting-id",
                        "resident-m1",
                        "--group-id",
                        "resident-main",
                    ]
                )

        self.assertEqual(exit_code, 0)
        request_json.assert_called_once_with(
            "http://room.local/api/live-agent-session-runs/exact-run/stop",
            method="POST",
            payload={},
            timeout_seconds=10.0,
        )

    def test_live_agent_session_runs_pause_refuses_missing_target(self):
        with patch("sys.stderr", StringIO()) as stderr:
            exit_code = main(
                [
                    "live-agent",
                    "session-runs",
                    "pause",
                    "--server",
                    "http://room.local",
                ]
            )

        self.assertEqual(exit_code, 2)
        self.assertIn("requires --run-id or both --meeting-id and --group-id", stderr.getvalue())

    def test_live_agent_session_runs_list_include_readiness_fetches_and_prints_current_counts(self):
        payload = {
            "runs": [
                {
                    "run_id": "run-1",
                    "action": "ensure",
                    "status": "ready",
                    "active": True,
                    "meeting_id": "resident-m1",
                    "group_id": "resident-main",
                    "readiness": {
                        "status": "degraded",
                        "expected": 3,
                        "connected": 1,
                    },
                }
            ]
        }
        with patch("agentsassemble.cli._request_json", return_value=payload) as request_json:
            with patch("sys.stdout", StringIO()) as stdout:
                exit_code = main(
                    [
                        "live-agent",
                        "session-runs",
                        "list",
                        "--server",
                        "http://room.local",
                        "--limit",
                        "3",
                        "--include-readiness",
                    ]
                )

        self.assertEqual(exit_code, 0)
        request_json.assert_called_once_with("http://room.local/api/live-agent-session-runs?limit=3&include_readiness=1")
        output = stdout.getvalue()
        self.assertIn("run-1 ensure ready resident-m1 resident-main active", output)
        self.assertIn("readiness=degraded", output)
        self.assertIn("current_connected=1/3", output)

    def test_live_agent_session_runs_list_filters_by_meeting_group_and_readiness(self):
        payload = {
            "runs": [
                {
                    "run_id": "run-target",
                    "action": "ensure",
                    "status": "ready",
                    "active": True,
                    "meeting_id": "resident-m1",
                    "group_id": "resident-main",
                    "readiness": {
                        "status": "ready",
                        "expected": 2,
                        "connected": 2,
                    },
                }
            ]
        }
        with patch("agentsassemble.cli._request_json", return_value=payload) as request_json:
            with patch("sys.stdout", StringIO()) as stdout:
                exit_code = main(
                    [
                        "live-agent",
                        "session-runs",
                        "list",
                        "--server",
                        "http://room.local",
                        "--limit",
                        "5",
                        "--meeting-id",
                        "resident-m1",
                        "--group-id",
                        "resident-main",
                        "--include-readiness",
                    ]
                )

        self.assertEqual(exit_code, 0)
        request_json.assert_called_once_with(
            "http://room.local/api/live-agent-session-runs?limit=5&meeting_id=resident-m1&group_id=resident-main&include_readiness=1"
        )
        output = stdout.getvalue()
        self.assertIn("run-target ensure ready resident-m1 resident-main active", output)
        self.assertIn("readiness=ready", output)
        self.assertIn("current_connected=2/2", output)

    def test_live_agent_session_runs_list_filters_by_run_id_before_meeting_group(self):
        payload = {
            "runs": [
                {
                    "run_id": "run-1",
                    "action": "ensure",
                    "status": "ready",
                    "active": True,
                    "meeting_id": "resident-m1",
                    "group_id": "resident-main",
                    "readiness": {
                        "status": "ready",
                        "expected": 2,
                        "connected": 2,
                    },
                }
            ]
        }
        with patch("agentsassemble.cli._request_json", return_value=payload) as request_json:
            with patch("sys.stdout", StringIO()) as stdout:
                exit_code = main(
                    [
                        "live-agent",
                        "session-runs",
                        "list",
                        "--server",
                        "http://room.local",
                        "--limit",
                        "5",
                        "--run-id",
                        "run-1",
                        "--meeting-id",
                        "resident-m2",
                        "--group-id",
                        "resident-alt",
                        "--include-readiness",
                    ]
                )

        self.assertEqual(exit_code, 0)
        request_json.assert_called_once_with(
            "http://room.local/api/live-agent-session-runs?limit=5&run_id=run-1&include_readiness=1"
        )
        output = stdout.getvalue()
        self.assertIn("run-1 ensure ready resident-m1 resident-main active", output)
        self.assertIn("readiness=ready", output)
        self.assertIn("current_connected=2/2", output)

    def test_live_agent_session_runs_list_fail_on_attention_exits_one_after_printing_summary(self):
        payload = {
            "runs": [
                {
                    "run_id": "run-ready-stale",
                    "action": "ensure",
                    "status": "ready",
                    "active": True,
                    "meeting_id": "resident-m1",
                    "group_id": "resident-main",
                    "readiness": {"status": "degraded", "expected": 2, "connected": 1},
                },
                {
                    "run_id": "run-degraded",
                    "action": "ensure",
                    "status": "degraded",
                    "active": True,
                    "meeting_id": "resident-m1",
                    "group_id": "resident-main",
                },
                {
                    "run_id": "run-failed",
                    "action": "ensure",
                    "status": "failed",
                    "active": False,
                    "meeting_id": "resident-m1",
                    "group_id": "resident-main",
                },
                {
                    "run_id": "run-stopped",
                    "action": "ensure",
                    "status": "stopped",
                    "active": False,
                    "meeting_id": "resident-m1",
                    "group_id": "resident-main",
                },
            ]
        }
        stdout = StringIO()
        with patch("agentsassemble.cli._request_json", return_value=payload) as request_json:
            with patch("sys.stdout", stdout):
                exit_code = main(
                    [
                        "live-agent",
                        "session-runs",
                        "list",
                        "--server",
                        "http://room.local",
                        "--include-readiness",
                        "--fail-on-attention",
                    ]
                )

        self.assertEqual(exit_code, 1)
        request_json.assert_called_once_with("http://room.local/api/live-agent-session-runs?limit=50&include_readiness=1")
        output = stdout.getvalue()
        self.assertIn("run-ready-stale ensure ready resident-m1 resident-main active", output)
        self.assertIn("readiness=degraded", output)
        self.assertIn("run-degraded ensure degraded resident-m1 resident-main active", output)
        self.assertIn("run-failed ensure failed resident-m1 resident-main inactive", output)
        self.assertIn("run-stopped ensure stopped resident-m1 resident-main inactive", output)

    def test_live_agent_session_runs_list_fail_on_attention_accepts_ready_paused_and_stopped(self):
        payload = {
            "runs": [
                {
                    "run_id": "run-ready",
                    "action": "ensure",
                    "status": "ready",
                    "active": True,
                    "meeting_id": "resident-m1",
                    "group_id": "resident-main",
                    "readiness": {"status": "ready", "expected": 2, "connected": 2},
                },
                {
                    "run_id": "run-paused",
                    "action": "ensure",
                    "status": "paused",
                    "active": False,
                    "meeting_id": "resident-m1",
                    "group_id": "resident-main",
                    "readiness": {"status": "degraded", "expected": 2, "connected": 0},
                },
                {
                    "run_id": "run-stopped",
                    "action": "ensure",
                    "status": "stopped",
                    "active": False,
                    "meeting_id": "resident-m1",
                    "group_id": "resident-main",
                    "readiness": {"status": "degraded", "expected": 2, "connected": 0},
                },
            ]
        }
        with patch("agentsassemble.cli._request_json", return_value=payload):
            with patch("sys.stdout", StringIO()):
                exit_code = main(
                    [
                        "live-agent",
                        "session-runs",
                        "list",
                        "--server",
                        "http://room.local",
                        "--include-readiness",
                        "--fail-on-attention",
                    ]
                )

        self.assertEqual(exit_code, 0)

    def test_live_agent_session_runs_list_include_readiness_json_preserves_raw_payload(self):
        payload = {
            "runs": [
                {
                    "run_id": "run-1",
                    "action": "ensure",
                    "status": "ready",
                    "active": True,
                    "meeting_id": "resident-m1",
                    "group_id": "resident-main",
                    "readiness": {
                        "status": "degraded",
                        "expected": 3,
                        "connected": 1,
                    },
                }
            ]
        }
        with patch("agentsassemble.cli._request_json", return_value=payload) as request_json:
            with patch("sys.stdout", StringIO()) as stdout:
                exit_code = main(
                    [
                        "live-agent",
                        "session-runs",
                        "list",
                        "--server",
                        "http://room.local",
                        "--include-readiness",
                        "--json",
                    ]
                )

        self.assertEqual(exit_code, 0)
        request_json.assert_called_once_with("http://room.local/api/live-agent-session-runs?limit=50&include_readiness=1")
        self.assertEqual(json.loads(stdout.getvalue()), payload)

    def test_live_agent_session_runs_wait_parses_target_status_and_wait_options(self):
        args = build_parser().parse_args(
            [
                "live-agent",
                "session-runs",
                "wait",
                "--server",
                "http://room.local",
                "--run-id",
                "run-1",
                "--status",
                "ready",
                "--limit",
                "5",
                "--timeout",
                "3",
                "--poll-interval",
                "0.5",
                "--json",
            ]
        )

        self.assertEqual(args.live_agent_command, "session-runs")
        self.assertEqual(args.live_agent_session_runs_command, "wait")
        self.assertEqual(args.run_id, "run-1")
        self.assertEqual(args.status, "ready")
        self.assertEqual(args.limit, 5)
        self.assertEqual(args.timeout, 3.0)
        self.assertEqual(args.poll_interval, 0.5)
        self.assertTrue(args.as_json)

    def test_live_agent_session_runs_wait_parses_meeting_group_target(self):
        args = build_parser().parse_args(
            [
                "live-agent",
                "session-runs",
                "wait",
                "--server",
                "http://room.local",
                "--meeting-id",
                "resident-m1",
                "--group-id",
                "resident-main",
                "--status",
                "ready",
                "--json",
            ]
        )

        self.assertEqual(args.live_agent_command, "session-runs")
        self.assertEqual(args.live_agent_session_runs_command, "wait")
        self.assertEqual(args.run_id, "")
        self.assertEqual(args.meeting_id, "resident-m1")
        self.assertEqual(args.group_id, "resident-main")
        self.assertEqual(args.status, "ready")

    def test_live_agent_session_runs_wait_observes_matching_run_status(self):
        payloads = [
            {
                "runs": [
                    {
                        "run_id": "run-1",
                        "action": "ensure",
                        "status": "running",
                        "active": True,
                        "meeting_id": "resident-m1",
                        "group_id": "resident-main",
                    }
                ]
            },
            {
                "runs": [
                    {
                        "run_id": "run-1",
                        "action": "ensure",
                        "status": "ready",
                        "active": True,
                        "meeting_id": "resident-m1",
                        "group_id": "resident-main",
                        "readiness": {"status": "ready"},
                    }
                ]
            },
        ]
        stdout = StringIO()
        with patch("agentsassemble.cli._request_json", side_effect=payloads) as request_json:
            with patch("agentsassemble.cli.time.sleep") as sleep:
                with patch("sys.stdout", stdout):
                    exit_code = main(
                        [
                            "live-agent",
                            "session-runs",
                            "wait",
                            "--server",
                            "http://room.local",
                            "--run-id",
                            "run-1",
                            "--status",
                            "ready",
                            "--limit",
                            "5",
                            "--timeout",
                            "3",
                            "--poll-interval",
                            "0.1",
                            "--json",
                        ]
                    )

        self.assertEqual(exit_code, 0)
        self.assertEqual(request_json.call_count, 2)
        self.assertEqual(
            request_json.call_args_list[-1].args,
            ("http://room.local/api/live-agent-session-runs?limit=5&run_id=run-1&include_readiness=1",),
        )
        self.assertIn("timeout_seconds", request_json.call_args_list[-1].kwargs)
        self.assertEqual(sleep.call_count, 1)
        result = json.loads(stdout.getvalue())
        self.assertEqual(result["status"], "observed")
        self.assertEqual(result["run_id"], "run-1")
        self.assertEqual(result["run_status"], "ready")
        self.assertEqual(result["run"]["status"], "ready")

    def test_live_agent_session_runs_wait_by_meeting_group_observes_latest_matching_run(self):
        payloads = [
            {
                "runs": [
                    {
                        "run_id": "run-old",
                        "action": "ensure",
                        "status": "ready",
                        "active": False,
                        "meeting_id": "resident-m1",
                        "group_id": "resident-main",
                    },
                    {
                        "run_id": "run-new",
                        "action": "ensure",
                        "status": "running",
                        "active": True,
                        "meeting_id": "resident-m1",
                        "group_id": "resident-main",
                        "readiness": {"status": "ready"},
                    },
                ]
            },
            {
                "runs": [
                    {
                        "run_id": "run-old",
                        "action": "ensure",
                        "status": "ready",
                        "active": False,
                        "meeting_id": "resident-m1",
                        "group_id": "resident-main",
                    },
                    {
                        "run_id": "run-new",
                        "action": "ensure",
                        "status": "ready",
                        "active": True,
                        "meeting_id": "resident-m1",
                        "group_id": "resident-main",
                        "readiness": {"status": "ready"},
                    },
                ]
            },
        ]
        stdout = StringIO()
        with patch("agentsassemble.cli._request_json", side_effect=payloads):
            with patch("agentsassemble.cli.time.sleep"):
                with patch("sys.stdout", stdout):
                    exit_code = main(
                        [
                            "live-agent",
                            "session-runs",
                            "wait",
                            "--server",
                            "http://room.local",
                            "--meeting-id",
                            "resident-m1",
                            "--group-id",
                            "resident-main",
                            "--status",
                            "ready",
                            "--timeout",
                            "3",
                            "--poll-interval",
                            "0.1",
                            "--json",
                        ]
                    )

        self.assertEqual(exit_code, 0)
        result = json.loads(stdout.getvalue())
        self.assertEqual(result["status"], "observed")
        self.assertEqual(result["run_id"], "run-new")
        self.assertEqual(result["meeting_id"], "resident-m1")
        self.assertEqual(result["group_id"], "resident-main")
        self.assertEqual(result["run"]["run_id"], "run-new")

    def test_live_agent_session_runs_wait_by_meeting_group_requests_server_filters(self):
        payload = {
            "runs": [
                {
                    "run_id": "run-target",
                    "action": "ensure",
                    "status": "ready",
                    "active": True,
                    "meeting_id": "resident-m1",
                    "group_id": "resident-main",
                    "readiness": {"status": "ready"},
                }
            ]
        }
        with patch("agentsassemble.cli._request_json", return_value=payload) as request_json:
            with patch("sys.stdout", StringIO()):
                exit_code = main(
                    [
                        "live-agent",
                        "session-runs",
                        "wait",
                        "--server",
                        "http://room.local",
                        "--meeting-id",
                        "resident-m1",
                        "--group-id",
                        "resident-main",
                        "--status",
                        "ready",
                        "--limit",
                        "5",
                        "--json",
                    ]
                )

        self.assertEqual(exit_code, 0)
        requested_url = request_json.call_args.args[0]
        parsed = urllib.parse.urlparse(requested_url)
        query = urllib.parse.parse_qs(parsed.query)
        self.assertEqual(parsed.path, "/api/live-agent-session-runs")
        self.assertEqual(query["limit"], ["5"])
        self.assertEqual(query["meeting_id"], ["resident-m1"])
        self.assertEqual(query["group_id"], ["resident-main"])

    def test_live_agent_session_runs_wait_by_run_id_requests_server_run_filter(self):
        payload = {
            "runs": [
                {
                    "run_id": "run-1",
                    "action": "ensure",
                    "status": "ready",
                    "active": True,
                    "meeting_id": "resident-m1",
                    "group_id": "resident-main",
                    "readiness": {"status": "ready"},
                }
            ]
        }
        with patch("agentsassemble.cli._request_json", return_value=payload) as request_json:
            with patch("sys.stdout", StringIO()):
                exit_code = main(
                    [
                        "live-agent",
                        "session-runs",
                        "wait",
                        "--server",
                        "http://room.local",
                        "--run-id",
                        "run-1",
                        "--meeting-id",
                        "resident-m2",
                        "--group-id",
                        "resident-alt",
                        "--status",
                        "ready",
                        "--limit",
                        "5",
                        "--json",
                    ]
                )

        self.assertEqual(exit_code, 0)
        requested_url = request_json.call_args.args[0]
        parsed = urllib.parse.urlparse(requested_url)
        query = urllib.parse.parse_qs(parsed.query)
        self.assertEqual(parsed.path, "/api/live-agent-session-runs")
        self.assertEqual(query["limit"], ["5"])
        self.assertEqual(query["run_id"], ["run-1"])
        self.assertEqual(query["include_readiness"], ["1"])
        self.assertNotIn("meeting_id", query)
        self.assertNotIn("group_id", query)

    def test_live_agent_session_runs_wait_ready_requires_current_readiness(self):
        payloads = [
            {
                "runs": [
                    {
                        "run_id": "run-target",
                        "action": "ensure",
                        "status": "ready",
                        "active": True,
                        "meeting_id": "resident-m1",
                        "group_id": "resident-main",
                        "readiness": {"status": "degraded"},
                    }
                ]
            },
            {
                "runs": [
                    {
                        "run_id": "run-target",
                        "action": "ensure",
                        "status": "ready",
                        "active": True,
                        "meeting_id": "resident-m1",
                        "group_id": "resident-main",
                        "readiness": {"status": "ready"},
                    }
                ]
            },
        ]
        stdout = StringIO()
        with patch("agentsassemble.cli._request_json", side_effect=payloads) as request_json:
            with patch("agentsassemble.cli.time.sleep"):
                with patch("sys.stdout", stdout):
                    exit_code = main(
                        [
                            "live-agent",
                            "session-runs",
                            "wait",
                            "--server",
                            "http://room.local",
                            "--meeting-id",
                            "resident-m1",
                            "--group-id",
                            "resident-main",
                            "--status",
                            "ready",
                            "--limit",
                            "5",
                            "--timeout",
                            "3",
                            "--poll-interval",
                            "0.1",
                            "--json",
                        ]
                    )

        self.assertEqual(exit_code, 0)
        self.assertEqual(request_json.call_count, 2)
        first_url = request_json.call_args_list[0].args[0]
        first_query = urllib.parse.parse_qs(urllib.parse.urlparse(first_url).query)
        self.assertEqual(first_query["include_readiness"], ["1"])
        result = json.loads(stdout.getvalue())
        self.assertEqual(result["status"], "observed")
        self.assertEqual(result["run_id"], "run-target")
        self.assertEqual(result["run"]["readiness"]["status"], "ready")

    def test_live_agent_session_runs_wait_ready_times_out_without_current_readiness(self):
        payload = {
            "runs": [
                {
                    "run_id": "run-1",
                    "action": "ensure",
                    "status": "ready",
                    "active": True,
                    "meeting_id": "resident-m1",
                    "group_id": "resident-main",
                    "readiness": {"status": "degraded"},
                }
            ]
        }
        stdout = StringIO()
        with patch("agentsassemble.cli._request_json", return_value=payload) as request_json:
            with patch("agentsassemble.cli.time.monotonic", side_effect=[0.0, 0.0, 1.1, 1.1]):
                with patch("sys.stdout", stdout):
                    exit_code = main(
                        [
                            "live-agent",
                            "session-runs",
                            "wait",
                            "--server",
                            "http://room.local",
                            "--run-id",
                            "run-1",
                            "--status",
                            "ready",
                            "--timeout",
                            "1",
                            "--poll-interval",
                            "0.1",
                            "--json",
                        ]
                    )

        self.assertEqual(exit_code, 1)
        requested_url = request_json.call_args.args[0]
        query = urllib.parse.parse_qs(urllib.parse.urlparse(requested_url).query)
        self.assertEqual(query["include_readiness"], ["1"])
        result = json.loads(stdout.getvalue())
        self.assertEqual(result["status"], "timeout")
        self.assertEqual(result["runs"][0]["readiness"]["status"], "degraded")

    def test_live_agent_session_runs_wait_ready_timeout_prints_current_readiness(self):
        payload = {
            "runs": [
                {
                    "run_id": "run-1",
                    "action": "ensure",
                    "status": "ready",
                    "active": True,
                    "meeting_id": "resident-m1",
                    "group_id": "resident-main",
                    "readiness": {"status": "degraded"},
                }
            ]
        }
        stdout = StringIO()
        with patch("agentsassemble.cli._request_json", return_value=payload):
            with patch("agentsassemble.cli.time.monotonic", side_effect=[0.0, 0.0, 1.1, 1.1]):
                with patch("sys.stdout", stdout):
                    exit_code = main(
                        [
                            "live-agent",
                            "session-runs",
                            "wait",
                            "--server",
                            "http://room.local",
                            "--run-id",
                            "run-1",
                            "--status",
                            "ready",
                            "--timeout",
                            "1",
                            "--poll-interval",
                            "0.1",
                        ]
                    )

        self.assertEqual(exit_code, 1)
        self.assertIn("last run: run-1 ensure ready resident-m1 resident-main active · readiness=degraded", stdout.getvalue())

    def test_live_agent_session_runs_wait_run_id_takes_precedence_over_meeting_group_target(self):
        payload = {
            "runs": [
                {
                    "run_id": "run-by-id",
                    "action": "ensure",
                    "status": "ready",
                    "active": True,
                    "meeting_id": "resident-m1",
                    "group_id": "resident-main",
                    "readiness": {"status": "ready"},
                },
                {
                    "run_id": "run-by-group-latest",
                    "action": "ensure",
                    "status": "running",
                    "active": True,
                    "meeting_id": "resident-m2",
                    "group_id": "resident-alt",
                },
            ]
        }
        stdout = StringIO()
        with patch("agentsassemble.cli._request_json", return_value=payload) as request_json:
            with patch("sys.stdout", stdout):
                exit_code = main(
                    [
                        "live-agent",
                        "session-runs",
                        "wait",
                        "--server",
                        "http://room.local",
                        "--run-id",
                        "run-by-id",
                        "--meeting-id",
                        "resident-m2",
                        "--group-id",
                        "resident-alt",
                        "--status",
                        "ready",
                        "--json",
                    ]
                )

        self.assertEqual(exit_code, 0)
        result = json.loads(stdout.getvalue())
        self.assertEqual(result["run_id"], "run-by-id")
        self.assertEqual(result["run"]["run_id"], "run-by-id")
        requested_url = request_json.call_args.args[0]
        query = urllib.parse.parse_qs(urllib.parse.urlparse(requested_url).query)
        self.assertNotIn("meeting_id", query)
        self.assertNotIn("group_id", query)

    def test_live_agent_session_runs_wait_refuses_missing_target(self):
        stderr = StringIO()
        with patch("sys.stderr", stderr):
            exit_code = main(
                [
                    "live-agent",
                    "session-runs",
                    "wait",
                    "--server",
                    "http://room.local",
                    "--status",
                    "ready",
                ]
            )

        self.assertEqual(exit_code, 2)
        self.assertIn("requires --run-id or both --meeting-id and --group-id", stderr.getvalue())

    def test_live_agent_session_runs_wait_times_out_with_last_run(self):
        payload = {
            "runs": [
                {
                    "run_id": "run-1",
                    "action": "ensure",
                    "status": "running",
                    "active": True,
                    "meeting_id": "resident-m1",
                    "group_id": "resident-main",
                }
            ]
        }
        stdout = StringIO()
        with patch("agentsassemble.cli._request_json", return_value=payload) as request_json:
            with patch("agentsassemble.cli.time.monotonic", side_effect=[0.0, 0.0, 1.1, 1.1]):
                with patch("agentsassemble.cli.time.sleep") as sleep:
                    with patch("sys.stdout", stdout):
                        exit_code = main(
                            [
                                "live-agent",
                                "session-runs",
                                "wait",
                                "--server",
                                "http://room.local",
                                "--run-id",
                                "run-1",
                                "--status",
                                "ready",
                                "--timeout",
                                "1",
                                "--poll-interval",
                                "0.1",
                            ]
                        )

        self.assertEqual(exit_code, 1)
        self.assertEqual(request_json.call_count, 1)
        self.assertEqual(sleep.call_count, 0)
        output = stdout.getvalue()
        self.assertIn("Timed out waiting for live-agent session run run-1 status ready", output)
        self.assertIn("last run: run-1 ensure running resident-m1 resident-main active", output)

    def test_live_agent_session_runs_wait_timeout_prints_latest_safe_run_when_target_absent(self):
        payload = {
            "runs": [
                {
                    "run_id": "run-other",
                    "action": "ensure",
                    "status": "running",
                    "active": True,
                    "meeting_id": "resident-m2",
                    "group_id": "resident-alt",
                }
            ]
        }
        stdout = StringIO()
        with patch("agentsassemble.cli._request_json", return_value=payload):
            with patch("agentsassemble.cli.time.monotonic", side_effect=[0.0, 0.0, 1.1, 1.1]):
                with patch("sys.stdout", stdout):
                    exit_code = main(
                        [
                            "live-agent",
                            "session-runs",
                            "wait",
                            "--server",
                            "http://room.local",
                            "--run-id",
                            "run-missing",
                            "--status",
                            "ready",
                            "--timeout",
                            "1",
                            "--poll-interval",
                            "0.1",
                        ]
                    )

        self.assertEqual(exit_code, 1)
        output = stdout.getvalue()
        self.assertIn("Timed out waiting for live-agent session run run-missing status ready", output)
        self.assertIn("last run: run-other ensure running resident-m2 resident-alt active", output)

    def test_live_agent_session_runs_wait_by_meeting_group_times_out_with_latest_matching_run(self):
        payload = {
            "runs": [
                {
                    "run_id": "run-unrelated",
                    "action": "ensure",
                    "status": "ready",
                    "active": True,
                    "meeting_id": "resident-m2",
                    "group_id": "resident-alt",
                },
                {
                    "run_id": "run-target",
                    "action": "ensure",
                    "status": "running",
                    "active": True,
                    "meeting_id": "resident-m1",
                    "group_id": "resident-main",
                },
            ]
        }
        stdout = StringIO()
        with patch("agentsassemble.cli._request_json", return_value=payload):
            with patch("agentsassemble.cli.time.monotonic", side_effect=[0.0, 0.0, 1.1, 1.1]):
                with patch("sys.stdout", stdout):
                    exit_code = main(
                        [
                            "live-agent",
                            "session-runs",
                            "wait",
                            "--server",
                            "http://room.local",
                            "--meeting-id",
                            "resident-m1",
                            "--group-id",
                            "resident-main",
                            "--status",
                            "ready",
                            "--timeout",
                            "1",
                            "--poll-interval",
                            "0.1",
                        ]
                    )

        self.assertEqual(exit_code, 1)
        output = stdout.getvalue()
        self.assertIn("Timed out waiting for live-agent session run for resident-m1 resident-main status ready", output)
        self.assertIn("last run: run-target ensure running resident-m1 resident-main active", output)

    def test_live_agent_session_runs_wait_json_timeout_returns_runs_tail(self):
        payload = {
            "runs": [
                {
                    "run_id": "run-1",
                    "action": "ensure",
                    "status": "running",
                    "active": True,
                    "meeting_id": "resident-m1",
                    "group_id": "resident-main",
                }
            ]
        }
        stdout = StringIO()
        with patch("agentsassemble.cli._request_json", return_value=payload):
            with patch("agentsassemble.cli.time.monotonic", side_effect=[0.0, 0.0, 1.1, 1.1]):
                with patch("sys.stdout", stdout):
                    exit_code = main(
                        [
                            "live-agent",
                            "session-runs",
                            "wait",
                            "--server",
                            "http://room.local",
                            "--run-id",
                            "run-1",
                            "--status",
                            "ready",
                            "--timeout",
                            "1",
                            "--poll-interval",
                            "0.1",
                            "--json",
                        ]
                    )

        self.assertEqual(exit_code, 1)
        result = json.loads(stdout.getvalue())
        self.assertEqual(result["status"], "timeout")
        self.assertEqual(result["runs"][0]["run_id"], "run-1")

    def test_live_agent_operations_wait_parses_filters_and_wait_options(self):
        args = build_parser().parse_args(
            [
                "live-agent",
                "operations",
                "wait",
                "--server",
                "http://room.local",
                "--operation",
                "session.start",
                "--target-id",
                "resident-m1",
                "--status",
                "success",
                "--after-id",
                "op-before",
                "--limit",
                "5",
                "--scan-limit",
                "1000",
                "--timeout",
                "3",
                "--poll-interval",
                "0.5",
                "--json",
            ]
        )

        self.assertEqual(args.live_agent_command, "operations")
        self.assertEqual(args.live_agent_operations_command, "wait")
        self.assertEqual(args.operation, "session.start")
        self.assertEqual(args.target_id, "resident-m1")
        self.assertEqual(args.status, "success")
        self.assertEqual(args.after_id, "op-before")
        self.assertEqual(args.limit, 5)
        self.assertEqual(args.scan_limit, 1000)
        self.assertEqual(args.timeout, 3.0)
        self.assertEqual(args.poll_interval, 0.5)
        self.assertTrue(args.as_json)

    def test_live_agent_operations_list_rejects_zero_limit(self):
        with patch("sys.stderr", StringIO()):
            with self.assertRaises(SystemExit):
                build_parser().parse_args(
                    [
                        "live-agent",
                        "operations",
                        "list",
                        "--server",
                        "http://room.local",
                        "--limit",
                        "0",
                    ]
                )

    def test_live_agent_operations_list_fetches_recent_operations(self):
        payload = {
            "operations": [
                {
                    "timestamp": "2026-05-18T01:02:03+00:00",
                    "operation": "process.start",
                    "status": "success",
                    "target_id": "crew",
                    "summary": "started live-agent process group",
                    "details": {},
                }
            ]
        }
        stdout = StringIO()
        with patch("agentsassemble.cli._request_json", return_value=payload) as request_json:
            with patch("sys.stdout", stdout):
                exit_code = main(
                    [
                        "live-agent",
                        "operations",
                        "list",
                        "--server",
                        "http://room.local",
                        "--limit",
                        "3",
                    ]
                )

        self.assertEqual(exit_code, 0)
        request_json.assert_called_once_with("http://room.local/api/live-agent-operations?limit=3")
        self.assertIn("process.start", stdout.getvalue())
        self.assertIn("success", stdout.getvalue())
        self.assertIn("crew", stdout.getvalue())

    def test_live_agent_operations_list_fetches_filtered_operations(self):
        payload = {
            "operations": [
                {
                    "timestamp": "2026-05-18T01:02:03+00:00",
                    "operation": "session.start",
                    "status": "success",
                    "target_id": "resident-m1",
                    "summary": "ready",
                    "details": {},
                }
            ]
        }
        stdout = StringIO()
        with patch("agentsassemble.cli._request_json", return_value=payload) as request_json:
            with patch("sys.stdout", stdout):
                exit_code = main(
                    [
                        "live-agent",
                        "operations",
                        "list",
                        "--server",
                        "http://room.local",
                        "--limit",
                        "3",
                        "--operation",
                        "session.start",
                        "--target-id",
                        "resident-m1",
                        "--status",
                        "success",
                        "--scan-limit",
                        "1000",
                    ]
                )

        self.assertEqual(exit_code, 0)
        request_json.assert_called_once_with(
            "http://room.local/api/live-agent-operations?limit=3&operation=session.start&target_id=resident-m1&status=success&scan_limit=1000"
        )
        self.assertIn("session.start", stdout.getvalue())
        self.assertIn("resident-m1", stdout.getvalue())

    def test_live_agent_operations_wait_observes_matching_operation_after_marker(self):
        payloads = [
            {
                "operations": [
                    {
                        "id": "old-match",
                        "timestamp": "2026-05-18T01:02:03+00:00",
                        "operation": "session.start",
                        "status": "success",
                        "target_id": "resident-m1",
                    },
                    {
                        "id": "op-before",
                        "timestamp": "2026-05-18T01:02:04+00:00",
                        "operation": "process.start",
                        "status": "success",
                        "target_id": "resident-m1",
                    },
                ]
            },
            {
                "operations": [
                    {
                        "id": "old-match",
                        "timestamp": "2026-05-18T01:02:03+00:00",
                        "operation": "session.start",
                        "status": "success",
                        "target_id": "resident-m1",
                    },
                    {
                        "id": "op-before",
                        "timestamp": "2026-05-18T01:02:04+00:00",
                        "operation": "process.start",
                        "status": "success",
                        "target_id": "resident-m1",
                    },
                    {
                        "id": "new-match",
                        "timestamp": "2026-05-18T01:02:05+00:00",
                        "operation": "session.start",
                        "status": "success",
                        "target_id": "resident-m1",
                    },
                ]
            },
        ]
        stdout = StringIO()
        with patch("agentsassemble.cli._request_json", side_effect=payloads) as request_json:
            with patch("agentsassemble.cli.time.sleep") as sleep:
                with patch("sys.stdout", stdout):
                    exit_code = main(
                        [
                            "live-agent",
                            "operations",
                            "wait",
                            "--server",
                            "http://room.local",
                            "--operation",
                            "session.start",
                            "--target-id",
                            "resident-m1",
                            "--status",
                            "success",
                            "--after-id",
                            "op-before",
                            "--limit",
                            "5",
                            "--timeout",
                            "3",
                            "--poll-interval",
                            "0.1",
                            "--json",
                        ]
                    )

        self.assertEqual(exit_code, 0)
        self.assertEqual(request_json.call_count, 2)
        self.assertEqual(request_json.call_args_list[-1].args, ("http://room.local/api/live-agent-operations?limit=5",))
        self.assertIn("timeout_seconds", request_json.call_args_list[-1].kwargs)
        self.assertEqual(sleep.call_count, 1)
        result = json.loads(stdout.getvalue())
        self.assertEqual(result["status"], "observed")
        self.assertEqual(result["operation"]["id"], "new-match")

    def test_live_agent_operations_wait_uses_scan_limit_without_server_side_operation_filters(self):
        payload = {
            "operations": [
                {
                    "id": "new-match",
                    "timestamp": "2026-05-18T01:02:05+00:00",
                    "operation": "session.start",
                    "status": "success",
                    "target_id": "resident-m1",
                }
            ],
            "scan_limit": 1000,
            "scanned_operation_count": 1,
            "truncated": False,
        }
        stdout = StringIO()
        with patch("agentsassemble.cli._request_json", return_value=payload) as request_json:
            with patch("sys.stdout", stdout):
                exit_code = main(
                    [
                        "live-agent",
                        "operations",
                        "wait",
                        "--server",
                        "http://room.local",
                        "--operation",
                        "session.start",
                        "--target-id",
                        "resident-m1",
                        "--status",
                        "success",
                        "--limit",
                        "5",
                        "--scan-limit",
                        "1000",
                        "--timeout",
                        "3",
                        "--json",
                    ]
                )

        self.assertEqual(exit_code, 0)
        request_json.assert_called_once()
        self.assertEqual(
            request_json.call_args.args,
            ("http://room.local/api/live-agent-operations?limit=5&scan_limit=1000&scan_tail=1",),
        )
        self.assertNotIn("operation=session.start", request_json.call_args.args[0])
        result = json.loads(stdout.getvalue())
        self.assertEqual(result["status"], "observed")
        self.assertEqual(result["operation"]["id"], "new-match")

    def test_live_agent_operations_wait_scan_limit_finds_match_beyond_result_limit(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            append_live_agent_operation(
                root,
                operation="session.start",
                status="success",
                target_id="resident-m1",
                summary="matching older operation",
            )
            for index in range(205):
                append_live_agent_operation(
                    root,
                    operation="process.start",
                    status="success",
                    target_id="resident-m1",
                    summary=f"newer unrelated operation {index}",
                )
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                stdout = StringIO()
                with patch("sys.stdout", stdout):
                    exit_code = main(
                        [
                            "live-agent",
                            "operations",
                            "wait",
                            "--server",
                            f"http://127.0.0.1:{server.server_port}",
                            "--operation",
                            "session.start",
                            "--target-id",
                            "resident-m1",
                            "--status",
                            "success",
                            "--limit",
                            "1",
                            "--scan-limit",
                            "250",
                            "--timeout",
                            "1",
                            "--json",
                        ]
                    )
            finally:
                server.shutdown()
                server.server_close()

        self.assertEqual(exit_code, 0)
        result = json.loads(stdout.getvalue())
        self.assertEqual(result["status"], "observed")
        self.assertEqual(result["operation"]["summary"], "matching older operation")

    def test_live_agent_operations_wait_remembers_after_marker_across_polls(self):
        payloads = [
            {
                "operations": [
                    {
                        "id": "op-before",
                        "timestamp": "2026-05-18T01:02:04+00:00",
                        "operation": "process.start",
                        "status": "success",
                        "target_id": "resident-m1",
                    }
                ]
            },
            {
                "operations": [
                    {
                        "id": "new-match",
                        "timestamp": "2026-05-18T01:02:05+00:00",
                        "operation": "session.start",
                        "status": "success",
                        "target_id": "resident-m1",
                    }
                ]
            },
        ]
        stdout = StringIO()
        with patch("agentsassemble.cli._request_json", side_effect=payloads) as request_json:
            with patch("agentsassemble.cli.time.sleep") as sleep:
                with patch("sys.stdout", stdout):
                    exit_code = main(
                        [
                            "live-agent",
                            "operations",
                            "wait",
                            "--server",
                            "http://room.local",
                            "--operation",
                            "session.start",
                            "--target-id",
                            "resident-m1",
                            "--after-id",
                            "op-before",
                            "--limit",
                            "1",
                            "--timeout",
                            "3",
                            "--poll-interval",
                            "0.1",
                            "--json",
                        ]
                    )

        self.assertEqual(exit_code, 0)
        self.assertEqual(request_json.call_count, 2)
        self.assertEqual(sleep.call_count, 1)
        result = json.loads(stdout.getvalue())
        self.assertEqual(result["status"], "observed")
        self.assertEqual(result["operation"]["id"], "new-match")

    def test_live_agent_operations_wait_times_out_with_last_operations(self):
        payload = {
            "operations": [
                {
                    "id": "other-op",
                    "timestamp": "2026-05-18T01:02:03+00:00",
                    "operation": "session.resume",
                    "status": "success",
                    "target_id": "resident-m1",
                }
            ]
        }
        stdout = StringIO()
        with patch("agentsassemble.cli._request_json", return_value=payload) as request_json:
            with patch("agentsassemble.cli.time.monotonic", side_effect=[0.0, 0.0, 1.1, 1.1]):
                with patch("agentsassemble.cli.time.sleep") as sleep:
                    with patch("sys.stdout", stdout):
                        exit_code = main(
                            [
                                "live-agent",
                                "operations",
                                "wait",
                                "--server",
                                "http://room.local",
                                "--operation",
                                "session.start",
                                "--target-id",
                                "resident-m1",
                                "--timeout",
                                "1",
                                "--poll-interval",
                                "0.1",
                            ]
                        )

        self.assertEqual(exit_code, 1)
        self.assertEqual(request_json.call_count, 1)
        self.assertEqual(sleep.call_count, 0)
        output = stdout.getvalue()
        self.assertIn("Timed out waiting for live-agent operation session.start", output)
        self.assertIn("last operation: 2026-05-18T01:02:03+00:00 session.resume success resident-m1", output)

    def test_live_agent_operations_wait_timeout_preserves_scan_metadata(self):
        payload = {
            "operations": [
                {
                    "id": "other-op",
                    "timestamp": "2026-05-18T01:02:03+00:00",
                    "operation": "session.resume",
                    "status": "success",
                    "target_id": "resident-m1",
                }
            ],
            "scan_limit": 3,
            "scanned_operation_count": 3,
            "truncated": True,
        }
        stdout = StringIO()
        with patch("agentsassemble.cli._request_json", return_value=payload) as request_json:
            with patch("agentsassemble.cli.time.monotonic", side_effect=[0.0, 0.0, 1.1, 1.1]):
                with patch("agentsassemble.cli.time.sleep"):
                    with patch("sys.stdout", stdout):
                        exit_code = main(
                            [
                                "live-agent",
                                "operations",
                                "wait",
                                "--server",
                                "http://room.local",
                                "--operation",
                                "session.start",
                                "--target-id",
                                "resident-m1",
                                "--timeout",
                                "1",
                                "--poll-interval",
                                "0.1",
                                "--scan-limit",
                                "3",
                                "--json",
                            ]
                        )

        self.assertEqual(exit_code, 1)
        request_json.assert_called_once()
        self.assertEqual(
            request_json.call_args.args,
            ("http://room.local/api/live-agent-operations?limit=50&scan_limit=3&scan_tail=1",),
        )
        result = json.loads(stdout.getvalue())
        self.assertEqual(result["status"], "timeout")
        self.assertTrue(result["truncated"])
        self.assertEqual(result["scan_limit"], 3)
        self.assertEqual(result["scanned_operation_count"], 3)
        self.assertEqual(result["operations"][0]["id"], "other-op")

    def test_live_agent_operations_list_fail_on_attention_exits_one_after_printing_summary(self):
        payload = {
            "operations": [
                {
                    "timestamp": "2026-05-18T01:02:03+00:00",
                    "operation": "process.start",
                    "status": "success",
                    "target_id": "crew",
                    "summary": "started live-agent process group",
                    "details": {},
                },
                {
                    "timestamp": "2026-05-18T01:02:04+00:00",
                    "operation": "session.restart",
                    "status": "degraded",
                    "target_id": "crew",
                    "summary": "",
                    "details": {"result_status": "degraded", "reply_probe_status": "failed"},
                },
            ]
        }
        stdout = StringIO()
        with patch("agentsassemble.cli._request_json", return_value=payload) as request_json:
            with patch("sys.stdout", stdout):
                exit_code = main(
                    [
                        "live-agent",
                        "operations",
                        "list",
                        "--server",
                        "http://room.local",
                        "--limit",
                        "2",
                        "--fail-on-attention",
                    ]
                )

        self.assertEqual(exit_code, 1)
        request_json.assert_called_once_with("http://room.local/api/live-agent-operations?limit=2")
        output = stdout.getvalue()
        self.assertIn("process.start", output)
        self.assertIn("session.restart", output)
        self.assertIn("degraded", output)
        self.assertIn("reply_probe_status=failed", output)

    def test_live_agent_operations_list_fail_on_attention_allows_successful_rows(self):
        payload = {
            "operations": [
                {
                    "timestamp": "2026-05-18T01:02:03+00:00",
                    "operation": "readiness.check",
                    "status": "success",
                    "target_id": "doctor-smoke",
                    "summary": "",
                    "details": {"result_status": "ready"},
                }
            ]
        }
        stdout = StringIO()
        with patch("agentsassemble.cli._request_json", return_value=payload):
            with patch("sys.stdout", stdout):
                exit_code = main(
                    [
                        "live-agent",
                        "operations",
                        "list",
                        "--server",
                        "http://room.local",
                        "--fail-on-attention",
                    ]
                )

        self.assertEqual(exit_code, 0)
        self.assertIn("readiness.check", stdout.getvalue())

    def test_live_agent_operations_list_includes_safe_details_in_default_output(self):
        payload = {
            "operations": [
                {
                    "timestamp": "2026-05-18T01:02:03+00:00",
                    "operation": "readiness.check",
                    "status": "degraded",
                    "target_id": "doctor-smoke",
                    "summary": "",
                    "details": {
                        "result_status": "degraded",
                        "smoke_reply_count": 3,
                        "probe_agent_ids": ["agent-a", "agent-b"],
                    },
                }
            ]
        }
        stdout = StringIO()
        with patch("agentsassemble.cli._request_json", return_value=payload):
            with patch("sys.stdout", stdout):
                exit_code = main(
                    [
                        "live-agent",
                        "operations",
                        "list",
                        "--server",
                        "http://room.local",
                    ]
                )

        self.assertEqual(exit_code, 0)
        output = stdout.getvalue()
        self.assertIn("readiness.check", output)
        self.assertIn("result_status=degraded", output)
        self.assertIn("smoke_reply_count=3", output)
        self.assertIn("probe_agent_ids=agent-a,agent-b", output)

    def test_live_agent_operations_list_prioritizes_readiness_session_smoke_soak_statuses(self):
        payload = {
            "operations": [
                {
                    "timestamp": "2026-05-18T01:02:03+00:00",
                    "operation": "readiness.check",
                    "status": "success",
                    "target_id": "doctor-smoke",
                    "summary": "",
                    "details": {
                        "result_status": "ready",
                        "session_smoke_reply_count": 3,
                        "session_smoke_post_restart_reply_count": 3,
                        "session_smoke_post_recover_reply_count": 3,
                        "session_smoke_soak_cycle_count": 2,
                        "session_smoke_soak_reply_count": 6,
                        "session_smoke_soak_check_statuses": ["ready", "ready"],
                        "probe_statuses": ["agent-a:ok"],
                    },
                }
            ]
        }
        stdout = StringIO()
        with patch("agentsassemble.cli._request_json", return_value=payload):
            with patch("sys.stdout", stdout):
                exit_code = main(["live-agent", "operations", "list", "--server", "http://room.local"])

        self.assertEqual(exit_code, 0)
        output = stdout.getvalue()
        self.assertIn("readiness.check", output)
        self.assertIn("session_smoke_post_restart_reply_count=3", output)
        self.assertLess(
            output.index("session_smoke_post_restart_reply_count=3"),
            output.index("session_smoke_post_recover_reply_count=3"),
        )
        self.assertIn("session_smoke_soak_check_statuses=ready,ready", output)

    def test_live_agent_operations_list_prioritizes_readiness_health_reasons(self):
        payload = {
            "operations": [
                {
                    "timestamp": "2026-05-18T01:02:03+00:00",
                    "operation": "readiness.check",
                    "status": "degraded",
                    "target_id": "doctor-smoke",
                    "summary": "",
                    "details": {
                        "result_status": "degraded",
                        "session_smoke_reply_count": 3,
                        "session_smoke_post_restart_reply_count": 3,
                        "health_process_attention": ["orphan-group"],
                        "health_process_reasons": [
                            "orphan-group recovered_unknown orphan running record marked unknown"
                        ],
                        "probe_statuses": ["agent-a:ok"],
                    },
                }
            ]
        }
        stdout = StringIO()
        with patch("agentsassemble.cli._request_json", return_value=payload):
            with patch("sys.stdout", stdout):
                exit_code = main(["live-agent", "operations", "list", "--server", "http://room.local"])

        self.assertEqual(exit_code, 0)
        output = stdout.getvalue()
        self.assertIn("health_process_reasons=orphan-group recovered_unknown orphan running record marked unknown", output)
        self.assertIn("health_process_attention=orphan-group", output)
        self.assertLess(
            output.index("health_process_reasons="),
            output.index("session_smoke_reply_count=3"),
        )

    def test_live_agent_operations_list_prioritizes_session_smoke_soak_evidence(self):
        payload = {
            "operations": [
                {
                    "timestamp": "2026-05-18T01:02:03+00:00",
                    "operation": "session.smoke",
                    "status": "success",
                    "target_id": "session-smoke",
                    "summary": "ran credential-free resident session smoke",
                    "details": {
                        "group_id": "session-smoke",
                        "meeting_id": "session-smoke",
                        "result_status": "ok",
                        "agent_ids": ["local", "session", "bridge"],
                        "rounds_status": "answered",
                        "round_count": 1,
                        "reply_count": 3,
                        "post_restart_reply_count": 3,
                        "post_recover_reply_count": 3,
                        "soak_cycle_count": 2,
                        "soak_reply_count": 6,
                        "soak_check_statuses": ["ready", "ready"],
                        "post_stop_process_status": "stopped",
                    },
                }
            ]
        }
        stdout = StringIO()
        with patch("agentsassemble.cli._request_json", return_value=payload):
            with patch("sys.stdout", stdout):
                exit_code = main(
                    [
                        "live-agent",
                        "operations",
                        "list",
                        "--server",
                        "http://room.local",
                    ]
                )

        self.assertEqual(exit_code, 0)
        output = stdout.getvalue()
        self.assertIn("session.smoke", output)
        self.assertIn("result_status=ok", output)
        self.assertIn("reply_count=3", output)
        self.assertLess(output.index("post_restart_reply_count=3"), output.index("post_recover_reply_count=3"))
        self.assertIn("post_recover_reply_count=3", output)
        self.assertIn("soak_cycle_count=2", output)
        self.assertIn("soak_reply_count=6", output)
        self.assertIn("soak_check_statuses=ready,ready", output)
        self.assertIn("post_stop_process_status=stopped", output)

    def test_live_agent_operations_list_prioritizes_session_control_probe_and_auto_rounds(self):
        payload = {
            "operations": [
                {
                    "timestamp": "2026-05-18T01:02:03+00:00",
                    "operation": "session.restart",
                    "status": "degraded",
                    "target_id": "council-session",
                    "summary": "",
                    "details": {
                        "result_status": "degraded",
                        "meeting_id": "main-room",
                        "group_id": "council",
                        "expected_agent_count": 3,
                        "connected_agent_count": 2,
                        "agent_ids": ["agent-a", "agent-b", "agent-c"],
                        "connected_agent_ids": ["agent-a", "agent-b"],
                        "reply_probe_status": "failed",
                        "reply_probe_statuses": ["agent-a:ok", "agent-b:timeout"],
                        "auto_rounds_status": "skipped",
                        "auto_rounds_reason": "probe_not_ready",
                        "auto_rounds_round_count": 2,
                        "auto_rounds_answered_round_count": 1,
                    },
                }
            ]
        }
        stdout = StringIO()
        with patch("agentsassemble.cli._request_json", return_value=payload):
            with patch("sys.stdout", stdout):
                exit_code = main(
                    [
                        "live-agent",
                        "operations",
                        "list",
                        "--server",
                        "http://room.local",
                    ]
                )

        self.assertEqual(exit_code, 0)
        output = stdout.getvalue()
        self.assertIn("session.restart", output)
        self.assertIn("result_status=degraded", output)
        self.assertIn("connected_agent_count=2", output)
        self.assertIn("reply_probe_status=failed", output)
        self.assertIn("reply_probe_statuses=agent-a:ok,agent-b:timeout", output)
        self.assertIn("auto_rounds_status=skipped", output)
        self.assertIn("auto_rounds_reason=probe_not_ready", output)
        self.assertIn("auto_rounds_round_count=2", output)
        self.assertIn("auto_rounds_answered_round_count=1", output)
        self.assertNotIn("agent_ids=agent-a,agent-b,agent-c", output)

    def test_live_agent_operations_list_prioritizes_session_finalization_result(self):
        payload = {
            "operations": [
                {
                    "timestamp": "2026-05-18T01:02:03+00:00",
                    "operation": "session.ensure",
                    "status": "degraded",
                    "target_id": "council-session",
                    "summary": "",
                    "details": {
                        "ensure_action": "none",
                        "result_status": "ready",
                        "meeting_id": "main-room",
                        "group_id": "council",
                        "connected_agent_count": 3,
                        "auto_rounds_status": "answered",
                        "auto_rounds_answered_round_count": 2,
                        "auto_rounds_round_count": 2,
                        "finalization_status": "failed",
                        "finalization_reason": "pending_turn_request",
                        "finalization_official_event_count": 0,
                    },
                }
            ]
        }
        stdout = StringIO()
        with patch("agentsassemble.cli._request_json", return_value=payload):
            with patch("sys.stdout", stdout):
                exit_code = main(
                    [
                        "live-agent",
                        "operations",
                        "list",
                        "--server",
                        "http://room.local",
                    ]
                )

        self.assertEqual(exit_code, 0)
        output = stdout.getvalue()
        self.assertIn("session.ensure", output)
        self.assertIn("auto_rounds_status=answered", output)
        self.assertIn("finalization_status=failed", output)
        self.assertIn("finalization_reason=pending_turn_request", output)
        self.assertIn("finalization_official_event_count=0", output)

    def test_live_agent_operations_list_prioritizes_remaining_rounds_finalization_result(self):
        payload = {
            "operations": [
                {
                    "timestamp": "2026-05-18T01:02:03+00:00",
                    "operation": "official_turn.rounds",
                    "status": "degraded",
                    "target_id": "main-room",
                    "summary": "",
                    "details": {
                        "meeting_id": "main-room",
                        "round_count": 1,
                        "answered_round_count": 1,
                        "completed_round_count": 0,
                        "timeout_round_count": 0,
                        "skipped_round_count": 0,
                        "round_ids": ["round_1"],
                        "statuses": ["answered"],
                        "finalization_status": "skipped",
                        "finalization_reason": "rounds_still_remaining",
                        "finalization_official_event_count": 0,
                    },
                }
            ]
        }
        stdout = StringIO()
        with patch("agentsassemble.cli._request_json", return_value=payload):
            with patch("sys.stdout", stdout):
                exit_code = main(
                    [
                        "live-agent",
                        "operations",
                        "list",
                        "--server",
                        "http://room.local",
                    ]
                )

        self.assertEqual(exit_code, 0)
        output = stdout.getvalue()
        self.assertIn("official_turn.rounds", output)
        self.assertIn("finalization_status=skipped", output)
        self.assertIn("finalization_reason=rounds_still_remaining", output)
        self.assertIn("answered_round_count=1", output)

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

    def test_live_agent_health_parses_json_and_fail_on_degraded_options(self):
        args = build_parser().parse_args(["live-agent", "health", "--json", "--fail-on-degraded"])

        self.assertEqual(args.live_agent_command, "health")
        self.assertTrue(args.as_json)
        self.assertTrue(args.fail_on_degraded)

    def test_live_agent_health_parses_wait_ok_options(self):
        args = build_parser().parse_args(
            [
                "live-agent",
                "health",
                "--wait-ok",
                "--timeout",
                "3",
                "--poll-interval",
                "0.5",
            ]
        )

        self.assertEqual(args.live_agent_command, "health")
        self.assertTrue(args.wait_ok)
        self.assertEqual(args.timeout, 3.0)
        self.assertEqual(args.poll_interval, 0.5)

    def test_live_agent_health_parses_wait_session_ready_options(self):
        args = build_parser().parse_args(
            [
                "live-agent",
                "health",
                "--wait-session-ready",
                "--meeting-id",
                "resident-m1",
                "--group-id",
                "resident-main",
                "--timeout",
                "3",
                "--poll-interval",
                "0.5",
            ]
        )

        self.assertEqual(args.live_agent_command, "health")
        self.assertTrue(args.wait_session_ready)
        self.assertEqual(args.meeting_id, "resident-m1")
        self.assertEqual(args.group_id, "resident-main")
        self.assertEqual(args.timeout, 3.0)
        self.assertEqual(args.poll_interval, 0.5)

    def test_live_agent_health_prints_summary(self):
        payload = {
            "status": "degraded",
            "agents": {
                "total": 6,
                "live": 2,
                "counts": {"online": 1, "working": 1, "error": 2, "stale": 0, "offline": 2},
                "attention": ["error-agent", "offline-agent"],
            },
            "processes": {
                "total": 7,
                "counts": {"running": 1, "restarting": 1, "error": 2, "unknown": 2, "stopped": 1},
                "attention": ["crashed-group", "orphan-group"],
                "reasons": {
                    "crashed-group": {
                        "event_type": "stale_watchdog",
                        "reason": "missing manifest agent agent-a",
                    },
                    "missing-config-group": {
                        "event_type": "restart_failed",
                        "reason": "missing launch config",
                    },
                    "orphan-group": {
                        "event_type": "recovered_unknown",
                        "reason": "orphan running record marked unknown",
                    },
                },
            },
            "process_monitor": {
                "running": True,
                "interval_seconds": 2.5,
                "last_tick_at": "2026-05-21T10:09:00+00:00",
                "last_status": "ok",
                "last_group_count": 7,
                "last_error_type": "",
            },
            "connections": {
                "expected": 2,
                "connected": 1,
                "attention": ["crew:friend-b:missing"],
            },
            "sessions": {
                "total": 2,
                "ready": 1,
                "degraded": 1,
                "attention": ["resident-m1:resident-main:agent-b:missing"],
            },
            "session_runs": {
                "total": 2,
                "active": 1,
                "ready": 1,
                "retrying": 1,
                "attention": ["resident-m1:resident-main:run-1:degraded:retrying"],
                "items": [
                    {
                        "run_id": "run-1",
                        "meeting_id": "resident-m1",
                        "group_id": "resident-main",
                        "status": "degraded",
                        "reconcile_failure_count": 2,
                        "reconcile_backoff_seconds": 120,
                        "next_reconcile_at": "2026-05-21T10:07:00+00:00",
                    }
                ],
            },
            "session_run_monitor": {
                "running": True,
                "interval_seconds": 30,
                "last_tick_at": "2026-05-21T10:08:00+00:00",
                "last_status": "ok",
                "last_result_count": 1,
                "last_error_type": "",
            },
            "observations": {
                "ready_agent_count": 2,
                "lobby_behind_count": 1,
                "live_behind_count": 0,
                "error_count": 0,
                "latest_lobby_event_id": "lobby-7",
                "latest_live_request_count": 0,
                "attention": ["resident-m1:resident-main:agent-b:lobby_cursor_behind"],
            },
        }
        stdout = StringIO()
        with patch("agentsassemble.cli._request_json", return_value=payload) as request_json:
            with patch("sys.stdout", stdout):
                exit_code = main(["live-agent", "health", "--server", "http://room.local"])

        self.assertEqual(exit_code, 0)
        request_json.assert_called_once_with("http://room.local/api/live-agent-health")
        output = stdout.getvalue()
        self.assertIn("status: degraded", output)
        self.assertIn("agents: 2 live / 6 total", output)
        self.assertIn("online 1", output)
        self.assertIn("agent attention: error-agent, offline-agent", output)
        self.assertIn("processes: 1 running / 7 total", output)
        self.assertIn("process monitor: running true", output)
        self.assertIn("groups 7", output)
        self.assertIn("last tick 2026-05-21T10:09:00+00:00", output)
        self.assertIn("process attention: crashed-group, orphan-group", output)
        self.assertIn(
            (
                "process reasons: crashed-group stale_watchdog missing manifest agent agent-a, "
                "missing-config-group restart_failed missing launch config, "
                "orphan-group recovered_unknown orphan running record marked unknown"
            ),
            output,
        )
        self.assertIn("connections: 1 connected / 2 expected", output)
        self.assertIn("connection attention: crew:friend-b:missing", output)
        self.assertIn("sessions: 1 ready / 2 total", output)
        self.assertIn("session attention: resident-m1:resident-main:agent-b:missing", output)
        self.assertIn("session runs: 1 active / 2 total", output)
        self.assertIn("ready 1", output)
        self.assertIn("retrying 1", output)
        self.assertIn("retry failures 2", output)
        self.assertIn("retry backoff 120s", output)
        self.assertIn("next retry 2026-05-21T10:07:00+00:00", output)
        self.assertIn("session-run attention: resident-m1:resident-main:run-1:degraded:retrying", output)
        self.assertIn("session-run monitor: running true", output)
        self.assertIn("last ok", output)
        self.assertIn("last tick 2026-05-21T10:08:00+00:00", output)
        self.assertIn("observations: 2 ready agents, lobby behind 1, live behind 0, errors 0", output)
        self.assertIn("observation attention: resident-m1:resident-main:agent-b:lobby_cursor_behind", output)

    def test_live_agent_health_can_emit_json_and_fail_on_degraded(self):
        payload = {"status": "degraded", "agents": {"counts": {}, "attention": []}, "processes": {"counts": {}, "attention": []}}
        stdout = StringIO()
        with patch("agentsassemble.cli._request_json", return_value=payload):
            with patch("sys.stdout", stdout):
                exit_code = main(["live-agent", "health", "--server", "http://room.local", "--json", "--fail-on-degraded"])

        self.assertEqual(exit_code, 1)
        self.assertEqual(json.loads(stdout.getvalue())["status"], "degraded")

    def test_live_agent_health_omits_monitor_summary_when_payload_is_missing(self):
        payload = {"status": "ok", "agents": {"counts": {}, "attention": []}, "processes": {"counts": {}, "attention": []}}
        stdout = StringIO()
        with patch("agentsassemble.cli._request_json", return_value=payload):
            with patch("sys.stdout", stdout):
                exit_code = main(["live-agent", "health", "--server", "http://room.local"])

        self.assertEqual(exit_code, 0)
        self.assertNotIn("session-run monitor:", stdout.getvalue())

    def test_live_agent_health_fail_on_degraded_allows_ok_status(self):
        payload = {"status": "ok", "agents": {"counts": {}, "attention": []}, "processes": {"counts": {}, "attention": []}}
        stdout = StringIO()
        with patch("agentsassemble.cli._request_json", return_value=payload):
            with patch("sys.stdout", stdout):
                exit_code = main(["live-agent", "health", "--server", "http://room.local", "--fail-on-degraded"])

        self.assertEqual(exit_code, 0)
        self.assertIn("status: ok", stdout.getvalue())

    def test_live_agent_health_wait_ok_polls_until_ok(self):
        payloads = [
            {"status": "degraded", "agents": {"counts": {}, "attention": []}, "processes": {"counts": {}, "attention": []}},
            {"status": "ok", "agents": {"counts": {}, "attention": []}, "processes": {"counts": {}, "attention": []}},
        ]
        stdout = StringIO()
        with patch("agentsassemble.cli._request_json", side_effect=payloads) as request_json:
            with patch("agentsassemble.cli.time.sleep") as sleep:
                with patch("sys.stdout", stdout):
                    exit_code = main(
                        [
                            "live-agent",
                            "health",
                            "--server",
                            "http://room.local",
                            "--wait-ok",
                            "--timeout",
                            "3",
                            "--poll-interval",
                            "0.1",
                        ]
                    )

        self.assertEqual(exit_code, 0)
        self.assertEqual(request_json.call_count, 2)
        self.assertEqual(request_json.call_args_list[-1].args, ("http://room.local/api/live-agent-health",))
        self.assertIn("timeout_seconds", request_json.call_args_list[-1].kwargs)
        self.assertEqual(sleep.call_count, 1)
        self.assertIn("status: ok", stdout.getvalue())
        self.assertNotIn("status: degraded", stdout.getvalue())

    def test_live_agent_health_wait_ok_times_out_with_last_summary(self):
        payload = {
            "status": "degraded",
            "agents": {"counts": {}, "attention": ["agent-a"]},
            "processes": {"counts": {}, "attention": []},
        }
        stdout = StringIO()
        with patch("agentsassemble.cli._request_json", return_value=payload) as request_json:
            with patch("agentsassemble.cli.time.monotonic", side_effect=[0.0, 0.0, 1.1, 1.1]):
                with patch("agentsassemble.cli.time.sleep") as sleep:
                    with patch("sys.stdout", stdout):
                        exit_code = main(
                            [
                                "live-agent",
                                "health",
                                "--server",
                                "http://room.local",
                                "--wait-ok",
                                "--timeout",
                                "1",
                                "--poll-interval",
                                "0.1",
                            ]
                        )

        self.assertEqual(exit_code, 1)
        self.assertEqual(request_json.call_count, 1)
        self.assertEqual(sleep.call_count, 0)
        output = stdout.getvalue()
        self.assertIn("status: degraded", output)
        self.assertIn("agent attention: agent-a", output)

    def test_live_agent_health_wait_ok_reports_poll_timeout_with_last_summary(self):
        payload = {
            "status": "degraded",
            "agents": {"counts": {}, "attention": ["agent-a"]},
            "processes": {"counts": {}, "attention": []},
        }
        stdout = StringIO()
        stderr = StringIO()
        with patch("agentsassemble.cli._request_json", side_effect=[payload, TimeoutError("timed out")]) as request_json:
            with patch("agentsassemble.cli.time.sleep") as sleep:
                with patch("sys.stdout", stdout):
                    with patch("sys.stderr", stderr):
                        exit_code = main(
                            [
                                "live-agent",
                                "health",
                                "--server",
                                "http://room.local",
                                "--wait-ok",
                                "--timeout",
                                "3",
                                "--poll-interval",
                                "0.1",
                            ]
                        )

        self.assertEqual(exit_code, 1)
        self.assertEqual(request_json.call_count, 2)
        self.assertEqual(sleep.call_count, 1)
        self.assertEqual(stderr.getvalue(), "")
        output = stdout.getvalue()
        self.assertIn("status: degraded", output)
        self.assertIn("agent attention: agent-a", output)

    def test_live_agent_health_wait_session_ready_polls_target_session_until_ready(self):
        payloads = [
            {
                "status": "degraded",
                "agents": {"counts": {}, "attention": []},
                "processes": {"counts": {}, "attention": ["other-group"]},
                "sessions": {
                    "items": [
                        {"meeting_id": "resident-m1", "group_id": "resident-main", "status": "starting"},
                        {"meeting_id": "other-meeting", "group_id": "other-group", "status": "degraded"},
                    ]
                },
            },
            {
                "status": "degraded",
                "agents": {"counts": {}, "attention": []},
                "processes": {"counts": {}, "attention": ["other-group"]},
                "sessions": {
                    "items": [
                        {"meeting_id": "resident-m1", "group_id": "resident-main", "status": "ready"},
                        {"meeting_id": "other-meeting", "group_id": "other-group", "status": "degraded"},
                    ]
                },
            },
        ]
        stdout = StringIO()
        with patch("agentsassemble.cli._request_json", side_effect=payloads) as request_json:
            with patch("agentsassemble.cli.time.sleep") as sleep:
                with patch("sys.stdout", stdout):
                    exit_code = main(
                        [
                            "live-agent",
                            "health",
                            "--server",
                            "http://room.local",
                            "--wait-session-ready",
                            "--meeting-id",
                            "resident-m1",
                            "--group-id",
                            "resident-main",
                            "--timeout",
                            "3",
                            "--poll-interval",
                            "0.1",
                        ]
                    )

        self.assertEqual(exit_code, 0)
        self.assertEqual(request_json.call_count, 2)
        self.assertEqual(sleep.call_count, 1)
        self.assertIn("status: degraded", stdout.getvalue())
        self.assertIn("session attention: none", stdout.getvalue())

    def test_live_agent_health_wait_session_ready_times_out_with_last_summary(self):
        payload = {
            "status": "degraded",
            "agents": {"counts": {}, "attention": []},
            "processes": {"counts": {}, "attention": []},
            "sessions": {
                "attention": ["resident-m1:resident-main:agent-b:missing"],
                "items": [{"meeting_id": "resident-m1", "group_id": "resident-main", "status": "degraded"}],
            },
        }
        stdout = StringIO()
        with patch("agentsassemble.cli._request_json", return_value=payload) as request_json:
            with patch("agentsassemble.cli.time.monotonic", side_effect=[0.0, 0.0, 1.1, 1.1]):
                with patch("agentsassemble.cli.time.sleep") as sleep:
                    with patch("sys.stdout", stdout):
                        exit_code = main(
                            [
                                "live-agent",
                                "health",
                                "--server",
                                "http://room.local",
                                "--wait-session-ready",
                                "--meeting-id",
                                "resident-m1",
                                "--group-id",
                                "resident-main",
                                "--timeout",
                                "1",
                                "--poll-interval",
                                "0.1",
                            ]
                        )

        self.assertEqual(exit_code, 1)
        self.assertEqual(request_json.call_count, 1)
        self.assertEqual(sleep.call_count, 0)
        output = stdout.getvalue()
        self.assertIn("status: degraded", output)
        self.assertIn("session attention: resident-m1:resident-main:agent-b:missing", output)

    def test_live_agent_health_wait_session_ready_honors_fail_on_degraded(self):
        payloads = [
            {
                "status": "degraded",
                "agents": {"counts": {}, "attention": []},
                "processes": {"counts": {}, "attention": ["other-group"]},
                "sessions": {
                    "items": [{"meeting_id": "resident-m1", "group_id": "resident-main", "status": "ready"}]
                },
            },
            {
                "status": "ok",
                "agents": {"counts": {}, "attention": []},
                "processes": {"counts": {}, "attention": []},
                "sessions": {
                    "items": [{"meeting_id": "resident-m1", "group_id": "resident-main", "status": "ready"}]
                },
            },
        ]
        stdout = StringIO()
        with patch("agentsassemble.cli._request_json", side_effect=payloads) as request_json:
            with patch("agentsassemble.cli.time.sleep") as sleep:
                with patch("sys.stdout", stdout):
                    exit_code = main(
                        [
                            "live-agent",
                            "health",
                            "--server",
                            "http://room.local",
                            "--wait-session-ready",
                            "--meeting-id",
                            "resident-m1",
                            "--group-id",
                            "resident-main",
                            "--fail-on-degraded",
                            "--timeout",
                            "3",
                            "--poll-interval",
                            "0.1",
                        ]
                    )

        self.assertEqual(exit_code, 0)
        self.assertEqual(request_json.call_count, 2)
        self.assertEqual(sleep.call_count, 1)
        self.assertIn("status: ok", stdout.getvalue())
        self.assertNotIn("status: degraded", stdout.getvalue())

    def test_live_agent_health_wait_session_ready_requires_meeting_and_group(self):
        stderr = StringIO()
        with patch("sys.stderr", stderr):
            exit_code = main(["live-agent", "health", "--wait-session-ready"])

        self.assertEqual(exit_code, 2)
        self.assertIn("--wait-session-ready requires --meeting-id and --group-id", stderr.getvalue())

    def test_live_agent_smoke_parses_operator_options(self):
        args = build_parser().parse_args(
            [
                "live-agent",
                "smoke",
                "--server",
                "http://room.local",
                "--group-id",
                "operator-smoke",
                "--timeout",
                "8",
                "--json",
            ]
        )

        self.assertEqual(args.live_agent_command, "smoke")
        self.assertEqual(args.server, "http://room.local")
        self.assertEqual(args.group_id, "operator-smoke")
        self.assertEqual(args.timeout, 8.0)
        self.assertTrue(args.as_json)

    def test_live_agent_official_round_smoke_parses_operator_options(self):
        args = build_parser().parse_args(
            [
                "live-agent",
                "official-round-smoke",
                "--server",
                "http://room.local",
                "--group-id",
                "round-smoke",
                "--timeout",
                "8",
                "--json",
            ]
        )

        self.assertEqual(args.live_agent_command, "official-round-smoke")
        self.assertEqual(args.server, "http://room.local")
        self.assertEqual(args.group_id, "round-smoke")
        self.assertEqual(args.timeout, 8.0)
        self.assertTrue(args.as_json)

    def test_live_agent_session_smoke_parses_operator_options(self):
        args = build_parser().parse_args(
            [
                "live-agent",
                "session-smoke",
                "--server",
                "http://room.local",
                "--group-id",
                "session-smoke",
                "--meeting-id",
                "session-smoke-meeting",
                "--timeout",
                "8",
                "--lobby-probes",
                "2",
                "--soak-cycles",
                "2",
                "--soak-interval",
                "0.5",
                "--json",
            ]
        )

        self.assertEqual(args.live_agent_command, "session-smoke")
        self.assertEqual(args.server, "http://room.local")
        self.assertEqual(args.group_id, "session-smoke")
        self.assertEqual(args.meeting_id, "session-smoke-meeting")
        self.assertEqual(args.timeout, 8.0)
        self.assertEqual(args.lobby_probe_count, 2)
        self.assertEqual(args.soak_cycle_count, 2)
        self.assertEqual(args.soak_interval_seconds, 0.5)
        self.assertTrue(args.as_json)

    def test_live_agent_session_smoke_rejects_unbounded_lobby_probes(self):
        with self.assertRaises(SystemExit):
            build_parser().parse_args(["live-agent", "session-smoke", "--lobby-probes", "6"])

    def test_live_agent_session_smoke_rejects_unbounded_soak_cycles(self):
        with self.assertRaises(SystemExit):
            build_parser().parse_args(["live-agent", "session-smoke", "--soak-cycles", "6"])

    def test_live_agent_session_smoke_rejects_unbounded_soak_interval(self):
        with self.assertRaises(SystemExit):
            build_parser().parse_args(["live-agent", "session-smoke", "--soak-interval", "61"])

    def test_live_agent_doctor_parses_operator_options(self):
        args = build_parser().parse_args(
            [
                "live-agent",
                "doctor",
                "--server",
                "http://room.local",
                "--group-id",
                "doctor-smoke",
                "--timeout",
                "8",
                "--probe-agent",
                "agent-a",
                "--probe-agent",
                "agent-b",
                "--probe-group",
                "resident-main",
                "--session-smoke",
                "--json",
            ]
        )

        self.assertEqual(args.live_agent_command, "doctor")
        self.assertEqual(args.server, "http://room.local")
        self.assertEqual(args.group_id, "doctor-smoke")
        self.assertEqual(args.timeout, 8.0)
        self.assertEqual(args.probe_agent_ids, ["agent-a", "agent-b"])
        self.assertEqual(args.probe_group_ids, ["resident-main"])
        self.assertTrue(args.session_smoke)
        self.assertTrue(args.as_json)

    def test_live_agent_call_parses_operator_options(self):
        args = build_parser().parse_args(
            [
                "live-agent",
                "call",
                "--server",
                "http://room.local",
                "--meeting-id",
                "m1",
                "--agent-id",
                "agent-a",
                "--role-id",
                "architect",
                "--display-name",
                "Agent A",
                "--turn-id",
                "round_1:0:architect",
                "--turn-index",
                "0",
                "--json",
                "공식",
                "발언",
                "요청",
            ]
        )

        self.assertEqual(args.live_agent_command, "call")
        self.assertEqual(args.server, "http://room.local")
        self.assertEqual(args.meeting_id, "m1")
        self.assertEqual(args.agent_id, "agent-a")
        self.assertEqual(args.role_id, "architect")
        self.assertEqual(args.display_name, "Agent A")
        self.assertEqual(args.turn_id, "round_1:0:architect")
        self.assertEqual(args.turn_index, 0)
        self.assertEqual(args.message, ["공식", "발언", "요청"])
        self.assertTrue(args.as_json)
        self.assertFalse(args.wait)
        self.assertEqual(args.timeout, 30.0)

    def test_live_agent_call_parses_wait_options(self):
        args = build_parser().parse_args(
            [
                "live-agent",
                "call",
                "--server",
                "http://room.local",
                "--meeting-id",
                "m1",
                "--agent-id",
                "agent-a",
                "--wait",
                "--timeout",
                "8",
                "공식",
                "발언",
                "요청",
            ]
        )

        self.assertTrue(args.wait)
        self.assertEqual(args.timeout, 8.0)

    def test_live_agent_call_posts_turn_request_and_prints_summary(self):
        response = {"event": {"id": "turn-request-1", "target_agent_id": "agent-a", "meeting_id": "m1"}}
        stdout = StringIO()
        with patch("agentsassemble.cli._request_json", return_value=response) as request_json:
            with patch("sys.stdout", stdout):
                exit_code = main(
                    [
                        "live-agent",
                        "call",
                        "--server",
                        "http://room.local",
                        "--meeting-id",
                        "m1",
                        "--agent-id",
                        "agent-a",
                        "--role-id",
                        "architect",
                        "공식",
                        "발언",
                        "요청",
                    ]
                )

        self.assertEqual(exit_code, 0)
        request_json.assert_called_once_with(
            "http://room.local/api/meetings/m1/live-agent-turns/request",
            method="POST",
            payload={
                "agent_id": "agent-a",
                "role_id": "architect",
                "display_name": "",
                "content": "공식 발언 요청",
                "turn_id": "",
                "turn_index": None,
            },
        )
        self.assertIn("Called agent-a for official turn turn-request-1", stdout.getvalue())

    def test_live_agent_call_waits_for_answered_turn_and_prints_summary(self):
        response = {
            "status": "answered",
            "request_event": {"id": "turn-request-1", "target_agent_id": "agent-a", "meeting_id": "m1"},
            "reply_event": {"id": "reply-1", "actor_id": "agent-a"},
        }
        stdout = StringIO()
        with patch("agentsassemble.cli._request_json", return_value=response) as request_json:
            with patch("sys.stdout", stdout):
                exit_code = main(
                    [
                        "live-agent",
                        "call",
                        "--server",
                        "http://room.local",
                        "--meeting-id",
                        "m1",
                        "--agent-id",
                        "agent-a",
                        "--role-id",
                        "architect",
                        "--wait",
                        "--timeout",
                        "8",
                        "공식",
                        "발언",
                        "요청",
                    ]
                )

        self.assertEqual(exit_code, 0)
        request_json.assert_called_once_with(
            "http://room.local/api/meetings/m1/live-agent-turns/call",
            method="POST",
            payload={
                "agent_id": "agent-a",
                "role_id": "architect",
                "display_name": "",
                "content": "공식 발언 요청",
                "turn_id": "",
                "turn_index": None,
                "timeout_seconds": 8.0,
            },
            timeout_seconds=14.0,
        )
        self.assertIn("Answered agent-a official turn reply-1", stdout.getvalue())

    def test_live_agent_call_wait_returns_one_on_timeout(self):
        response = {
            "status": "timeout",
            "request_event": {"id": "turn-request-1", "target_agent_id": "agent-a"},
            "reply_event": None,
        }
        stdout = StringIO()
        with patch("agentsassemble.cli._request_json", return_value=response):
            with patch("sys.stdout", stdout):
                exit_code = main(
                    [
                        "live-agent",
                        "call",
                        "--server",
                        "http://room.local",
                        "--meeting-id",
                        "m1",
                        "--agent-id",
                        "agent-a",
                        "--wait",
                        "--timeout",
                        "0",
                        "공식",
                        "발언",
                        "요청",
                    ]
                )

        self.assertEqual(exit_code, 1)
        self.assertIn("Timed out waiting for agent-a official turn turn-request-1", stdout.getvalue())

    def test_live_agent_call_sequence_parses_operator_options(self):
        args = build_parser().parse_args(
            [
                "live-agent",
                "call-sequence",
                "--server",
                "http://room.local",
                "--meeting-id",
                "m1",
                "--turns-json",
                '[{"agent_id":"agent-a","content":"A"},{"agent_id":"agent-b","content":"B"}]',
                "--timeout",
                "8",
                "--stop-on-timeout",
                "--json",
            ]
        )

        self.assertEqual(args.live_agent_command, "call-sequence")
        self.assertEqual(args.server, "http://room.local")
        self.assertEqual(args.meeting_id, "m1")
        self.assertEqual(args.timeout, 8.0)
        self.assertTrue(args.stop_on_timeout)
        self.assertTrue(args.as_json)

    def test_live_agent_call_round_parser_accepts_role_filters(self):
        args = build_parser().parse_args(
            [
                "live-agent",
                "call-round",
                "--server",
                "http://room.local",
                "--meeting-id",
                "m1",
                "--round-id",
                "round_1",
                "--role",
                "critic",
                "--role",
                "architect",
                "--timeout",
                "8",
                "--stop-on-timeout",
                "--json",
                "Discuss",
                "this",
                "round",
            ]
        )

        self.assertEqual(args.live_agent_command, "call-round")
        self.assertEqual(args.server, "http://room.local")
        self.assertEqual(args.meeting_id, "m1")
        self.assertEqual(args.round_id, "round_1")
        self.assertEqual(args.role_ids, ["critic", "architect"])
        self.assertEqual(args.timeout, 8.0)
        self.assertTrue(args.stop_on_timeout)
        self.assertTrue(args.as_json)
        self.assertEqual(args.instruction, ["Discuss", "this", "round"])

    def test_live_agent_call_remaining_rounds_parser_accepts_bounds(self):
        args = build_parser().parse_args(
            [
                "live-agent",
                "call-remaining-rounds",
                "--server",
                "http://room.local",
                "--meeting-id",
                "m1",
                "--timeout",
                "8",
                "--max-rounds",
                "2",
                "--stop-on-timeout",
                "--json",
            ]
        )

        self.assertEqual(args.live_agent_command, "call-remaining-rounds")
        self.assertEqual(args.server, "http://room.local")
        self.assertEqual(args.meeting_id, "m1")
        self.assertEqual(args.timeout, 8.0)
        self.assertEqual(args.max_rounds, 2)
        self.assertTrue(args.stop_on_timeout)
        self.assertTrue(args.as_json)

    def test_live_agent_review_checkpoint_parser_accepts_targets(self):
        args = build_parser().parse_args(
            [
                "live-agent",
                "review-checkpoint",
                "--server",
                "http://room.local",
                "--meeting-id",
                "m1",
                "--group-id",
                "resident main",
                "--agent-id",
                "agent-a",
                "--agent-id",
                "agent-b",
                "--timeout",
                "8",
                "--checkpoint-id",
                "checkpoint-1",
                "--json",
                "Review",
                "this",
                "slice",
            ]
        )

        self.assertEqual(args.live_agent_command, "review-checkpoint")
        self.assertEqual(args.server, "http://room.local")
        self.assertEqual(args.meeting_id, "m1")
        self.assertEqual(args.group_id, "resident main")
        self.assertEqual(args.agent_ids, ["agent-a", "agent-b"])
        self.assertEqual(args.timeout, 8.0)
        self.assertEqual(args.checkpoint_id, "checkpoint-1")
        self.assertTrue(args.as_json)
        self.assertEqual(args.message, ["Review", "this", "slice"])

    def test_live_agent_review_checkpoint_posts_request_and_prints_summary(self):
        response = {
            "status": "answered",
            "checkpoint_id": "checkpoint-1",
            "turn_count": 2,
            "answered_count": 2,
            "timeout_count": 0,
            "skipped_count": 0,
            "results": [
                {"agent_id": "agent-a", "status": "answered", "reply_event": {"id": "reply-a"}},
                {"agent_id": "agent-b", "status": "answered", "reply_event": {"id": "reply-b"}},
            ],
        }
        stdout = StringIO()
        with patch("agentsassemble.cli._request_json", return_value=response) as request_json:
            with patch("sys.stdout", stdout):
                exit_code = main(
                    [
                        "live-agent",
                        "review-checkpoint",
                        "--server",
                        "http://room.local",
                        "--meeting-id",
                        "m1",
                        "--group-id",
                        "resident-main",
                        "--agent-id",
                        "agent-a",
                        "--agent-id",
                        "agent-b",
                        "--timeout",
                        "8",
                        "--checkpoint-id",
                        "checkpoint-1",
                        "Review",
                        "this",
                        "slice",
                    ]
                )

        self.assertEqual(exit_code, 0)
        self.assertIn("Review checkpoint checkpoint-1 answered: 2/2 answered, 0 timed out, 0 skipped", stdout.getvalue())
        request_json.assert_called_once()
        url = request_json.call_args.args[0]
        payload = request_json.call_args.kwargs["payload"]
        self.assertEqual(url, "http://room.local/api/meetings/m1/review-checkpoints")
        self.assertEqual(payload["group_id"], "resident-main")
        self.assertEqual(payload["agent_ids"], ["agent-a", "agent-b"])
        self.assertEqual(payload["content"], "Review this slice")
        self.assertEqual(payload["checkpoint_id"], "checkpoint-1")
        self.assertEqual(payload["timeout_seconds"], 8.0)

    def test_live_agent_review_checkpoint_returns_one_when_not_answered(self):
        response = {
            "status": "timeout",
            "checkpoint_id": "checkpoint-1",
            "turn_count": 1,
            "answered_count": 0,
            "timeout_count": 1,
            "skipped_count": 0,
            "results": [{"agent_id": "agent-a", "status": "timeout", "request_event": {"id": "request-a"}}],
        }
        stdout = StringIO()
        with patch("agentsassemble.cli._request_json", return_value=response):
            with patch("sys.stdout", stdout):
                exit_code = main(
                    [
                        "live-agent",
                        "review-checkpoint",
                        "--server",
                        "http://room.local",
                        "--meeting-id",
                        "m1",
                        "--group-id",
                        "resident-main",
                        "--timeout",
                        "0",
                        "Review",
                        "this",
                    ]
                )

        self.assertEqual(exit_code, 1)
        self.assertIn("Review checkpoint checkpoint-1 timeout: 0/1 answered, 1 timed out, 0 skipped", stdout.getvalue())

    def test_live_agent_call_round_posts_request_and_prints_summary(self):
        response = {
            "status": "answered",
            "round_id": "round_1",
            "answered_count": 2,
            "timeout_count": 0,
            "skipped_count": 0,
            "results": [
                {"agent_id": "agent-b", "status": "answered", "reply_event": {"id": "reply-b"}},
                {"agent_id": "agent-a", "status": "answered", "reply_event": {"id": "reply-a"}},
            ],
        }
        stdout = StringIO()
        with patch("agentsassemble.cli._request_json", return_value=response) as request_json:
            with patch("sys.stdout", stdout):
                exit_code = main(
                    [
                        "live-agent",
                        "call-round",
                        "--server",
                        "http://room.local",
                        "--meeting-id",
                        "m1",
                        "--round-id",
                        "round_1",
                        "--role",
                        "critic",
                        "--role",
                        "architect",
                        "--timeout",
                        "8",
                        "--stop-on-timeout",
                        "Discuss",
                        "this",
                        "round",
                    ]
                )

        self.assertEqual(exit_code, 0)
        request_json.assert_called_once_with(
            "http://room.local/api/meetings/m1/live-agent-turns/round",
            method="POST",
            payload={
                "round_id": "round_1",
                "role_ids": ["critic", "architect"],
                "content": "Discuss this round",
                "timeout_seconds": 8.0,
                "stop_on_timeout": True,
            },
            timeout_seconds=22.0,
        )
        self.assertIn("Official round round_1 answered: 2 answered, 0 timed out, 0 skipped", stdout.getvalue())
        self.assertIn("- agent-b: answered reply-b", stdout.getvalue())

    def test_live_agent_call_remaining_rounds_posts_request_and_prints_summary(self):
        response = {
            "status": "answered",
            "round_count": 1,
            "answered_round_count": 1,
            "timeout_round_count": 0,
            "skipped_round_count": 0,
            "results": [{"round_id": "round_2", "status": "answered"}],
        }
        stdout = StringIO()
        with patch("agentsassemble.cli._request_json", return_value=response) as request_json:
            with patch("sys.stdout", stdout):
                exit_code = main(
                    [
                        "live-agent",
                        "call-remaining-rounds",
                        "--server",
                        "http://room.local",
                        "--meeting-id",
                        "m1",
                        "--timeout",
                        "8",
                        "--max-rounds",
                        "2",
                        "--stop-on-timeout",
                    ]
                )

        self.assertEqual(exit_code, 0)
        request_json.assert_called_once_with(
            "http://room.local/api/meetings/m1/live-agent-turns/rounds",
            method="POST",
            payload={
                "timeout_seconds": 8.0,
                "stop_on_timeout": True,
                "max_rounds": 2,
            },
            timeout_seconds=198.0,
        )
        self.assertIn("Official remaining rounds answered: 1 rounds, 1 answered, 0 already complete, 0 timed out, 0 skipped", stdout.getvalue())
        self.assertIn("- round_2: answered", stdout.getvalue())

    def test_live_agent_call_remaining_rounds_can_finalize_after_success(self):
        response = {
            "status": "answered",
            "round_count": 1,
            "answered_round_count": 1,
            "timeout_round_count": 0,
            "skipped_round_count": 0,
            "results": [{"round_id": "round_2", "status": "answered"}],
            "finalization": {
                "status": "finalized",
                "meeting_id": "m1",
                "official_event_count": 2,
            },
        }
        stdout = StringIO()
        with patch("agentsassemble.cli._request_json", return_value=response) as request_json:
            with patch("sys.stdout", stdout):
                exit_code = main(
                    [
                        "live-agent",
                        "call-remaining-rounds",
                        "--server",
                        "http://room.local",
                        "--meeting-id",
                        "m1",
                        "--timeout",
                        "8",
                        "--max-rounds",
                        "1",
                        "--finalize-after-rounds",
                    ]
                )

        self.assertEqual(exit_code, 0)
        request_json.assert_called_once_with(
            "http://room.local/api/meetings/m1/live-agent-turns/rounds",
            method="POST",
            payload={
                "timeout_seconds": 8.0,
                "stop_on_timeout": False,
                "max_rounds": 1,
                "finalize_after_rounds": True,
            },
            timeout_seconds=102.0,
        )
        self.assertIn("finalization finalized: 2 official events", stdout.getvalue())

    def test_live_agent_call_remaining_rounds_finalize_failure_exits_nonzero(self):
        response = {
            "status": "answered",
            "round_count": 1,
            "answered_round_count": 1,
            "timeout_round_count": 0,
            "skipped_round_count": 0,
            "results": [{"round_id": "round_2", "status": "answered"}],
            "finalization": {"status": "failed", "reason": "pending_turn_request"},
        }
        stdout = StringIO()
        with patch("agentsassemble.cli._request_json", return_value=response):
            with patch("sys.stdout", stdout):
                exit_code = main(
                    [
                        "live-agent",
                        "call-remaining-rounds",
                        "--server",
                        "http://room.local",
                        "--meeting-id",
                        "m1",
                        "--finalize-after-rounds",
                    ]
                )

        self.assertEqual(exit_code, 1)
        self.assertIn("finalization failed", stdout.getvalue())

    def test_live_agent_call_remaining_rounds_returns_one_when_partial(self):
        response = {
            "status": "stopped",
            "round_count": 2,
            "answered_round_count": 0,
            "timeout_round_count": 1,
            "skipped_round_count": 1,
            "results": [{"round_id": "round_1", "status": "timeout"}, {"round_id": "round_2", "status": "skipped"}],
        }
        with patch("agentsassemble.cli._request_json", return_value=response):
            exit_code = main(
                [
                    "live-agent",
                    "call-remaining-rounds",
                    "--server",
                    "http://room.local",
                    "--meeting-id",
                    "m1",
                    "--timeout",
                    "0",
                ]
            )

        self.assertEqual(exit_code, 1)

    def test_live_agent_call_remaining_rounds_rejects_more_than_batch_limit(self):
        stderr = StringIO()
        with patch("agentsassemble.cli._request_json") as request_json:
            with patch("sys.stderr", stderr):
                exit_code = main(
                    [
                        "live-agent",
                        "call-remaining-rounds",
                        "--server",
                        "http://room.local",
                        "--meeting-id",
                        "m1",
                        "--max-rounds",
                        "9",
                    ]
                )

        self.assertEqual(exit_code, 2)
        request_json.assert_not_called()
        self.assertIn("--max-rounds supports at most 8", stderr.getvalue())

    def test_live_agent_start_meeting_parser_accepts_config_paths(self):
        args = build_parser().parse_args(
            [
                "live-agent",
                "start-meeting",
                "--server",
                "http://room.local",
                "--meeting-id",
                "resident-m1",
                "--council-config",
                "configs/demo-council.json",
                "--agent-config",
                "configs/agents.example.json",
                "--json",
            ]
        )

        self.assertEqual(args.live_agent_command, "start-meeting")
        self.assertEqual(args.meeting_id, "resident-m1")
        self.assertEqual(args.council_config, "configs/demo-council.json")
        self.assertEqual(args.agent_config, "configs/agents.example.json")
        self.assertTrue(args.as_json)

    def test_live_agent_start_meeting_posts_request_and_prints_summary(self):
        response = {
            "meeting_id": "resident-m1",
            "meeting": {
                "roles": [{"id": "architect"}, {"id": "critic"}],
                "agent_bindings": [{"agent_id": "agent-a"}, {"agent_id": "agent-b"}],
            },
        }
        stdout = StringIO()
        with patch("agentsassemble.cli._request_json", return_value=response) as request_json:
            with patch("sys.stdout", stdout):
                exit_code = main(
                    [
                        "live-agent",
                        "start-meeting",
                        "--server",
                        "http://room.local",
                        "--meeting-id",
                        "resident-m1",
                        "--council-config",
                        "configs/demo-council.json",
                        "--agent-config",
                        "configs/agents.example.json",
                    ]
                )

        self.assertEqual(exit_code, 0)
        request_json.assert_called_once_with(
            "http://room.local/api/live-agent-meetings/start",
            method="POST",
            payload={
                "meeting_id": "resident-m1",
                "council_config_path": "configs/demo-council.json",
                "agent_config_path": "configs/agents.example.json",
            },
        )
        self.assertIn("Started resident live-agent meeting resident-m1", stdout.getvalue())
        self.assertIn("2 roles, 2 bound agents", stdout.getvalue())

    def test_live_agent_start_session_parser_accepts_configs_and_restart_options(self):
        args = build_parser().parse_args(
            [
                "live-agent",
                "start-session",
                "--server",
                "http://room.local",
                "--meeting-id",
                "resident-m1",
                "--group-id",
                "resident-main",
                "--council-config",
                "configs/demo-council.json",
                "--agent-config",
                "configs/agents.example.json",
                "--live-agent-config",
                "configs/live-agents.example.json",
                "--connect-timeout",
                "3",
                "--auto-restart",
                "--max-restarts",
                "2",
                "--restart-backoff-seconds",
                "1",
                "--stale-restart-after-seconds",
                "30",
                "--probe-bound-agents",
                "--probe-timeout",
                "4",
                "--json",
            ]
        )

        self.assertEqual(args.live_agent_command, "start-session")
        self.assertEqual(args.meeting_id, "resident-m1")
        self.assertEqual(args.group_id, "resident-main")
        self.assertEqual(args.council_config, "configs/demo-council.json")
        self.assertEqual(args.agent_config, "configs/agents.example.json")
        self.assertEqual(args.live_agent_config, "configs/live-agents.example.json")
        self.assertEqual(args.connect_timeout, 3.0)
        self.assertTrue(args.auto_restart)
        self.assertEqual(args.max_restarts, 2)
        self.assertEqual(args.restart_backoff_seconds, 1.0)
        self.assertEqual(args.stale_restart_after_seconds, 30.0)
        self.assertTrue(args.probe_bound_agents)
        self.assertEqual(args.probe_timeout, 4.0)
        self.assertTrue(args.as_json)

    def test_live_agent_start_session_parser_accepts_wait_ready_options(self):
        args = build_parser().parse_args(
            [
                "live-agent",
                "start-session",
                "--live-agent-config",
                "configs/live-agents.example.json",
                "--wait-ready",
                "--wait-timeout",
                "9",
                "--wait-poll-interval",
                "0.25",
            ]
        )

        self.assertTrue(args.wait_ready)
        self.assertEqual(args.wait_timeout, 9.0)
        self.assertEqual(args.wait_poll_interval, 0.25)

    def test_live_agent_start_session_parser_accepts_auto_round_options(self):
        args = build_parser().parse_args(
            [
                "live-agent",
                "start-session",
                "--live-agent-config",
                "configs/live-agents.example.json",
                "--run-remaining-rounds",
                "--round-timeout",
                "8",
                "--max-rounds",
                "2",
                "--stop-on-timeout",
            ]
        )

        self.assertTrue(args.run_remaining_rounds)
        self.assertEqual(args.round_timeout, 8.0)
        self.assertEqual(args.max_rounds, 2)
        self.assertTrue(args.stop_on_timeout)

    def test_live_agent_start_session_posts_request_and_uses_status_exit_codes(self):
        response = {
            "status": "starting",
            "meeting_id": "resident-m1",
            "group_id": "resident-main",
            "connection": {
                "expected": 2,
                "connected": 1,
                "attention": ["agent-b:offline"],
            },
        }
        stdout = StringIO()
        with patch("agentsassemble.cli._request_json", return_value=response) as request_json:
            with patch("sys.stdout", stdout):
                exit_code = main(
                    [
                        "live-agent",
                        "start-session",
                        "--server",
                        "http://room.local",
                        "--meeting-id",
                        "resident-m1",
                        "--group-id",
                        "resident-main",
                        "--council-config",
                        "configs/demo-council.json",
                        "--agent-config",
                        "configs/agents.example.json",
                        "--live-agent-config",
                        "configs/live-agents.example.json",
                        "--connect-timeout",
                        "3",
                    ]
                )

        self.assertEqual(exit_code, 1)
        request_json.assert_called_once_with(
            "http://room.local/api/live-agent-sessions/start",
            method="POST",
            payload={
                "meeting_id": "resident-m1",
                "group_id": "resident-main",
                "council_config_path": "configs/demo-council.json",
                "agent_config_path": "configs/agents.example.json",
                "live_agent_config_path": "configs/live-agents.example.json",
                "connect_timeout_seconds": 3.0,
                "auto_restart": False,
                "max_restarts": 0,
                "restart_backoff_seconds": 5.0,
                "stale_restart_after_seconds": 0.0,
            },
            timeout_seconds=9.0,
        )
        self.assertIn("Resident session resident-m1 starting", stdout.getvalue())
        self.assertIn("resident-main", stdout.getvalue())
        self.assertIn("1/2 connected", stdout.getvalue())

    def test_live_agent_start_session_wait_ready_polls_read_only_session_readiness(self):
        start_response = {
            "status": "starting",
            "meeting_id": "generated-m1",
            "group_id": "generated-group",
            "connection": {"expected": 2, "connected": 1, "attention": ["agent-b:offline"]},
            "process": {"status": "running", "attention": []},
        }
        starting_snapshot = {
            "status": "degraded",
            "meeting_id": "generated-m1",
            "group_id": "generated-group",
            "connection": {"expected": 2, "connected": 1, "attention": ["agent-b:offline"]},
            "process": {"status": "running", "attention": []},
        }
        ready_snapshot = {
            "status": "ready",
            "meeting_id": "generated-m1",
            "group_id": "generated-group",
            "connection": {"expected": 2, "connected": 2, "attention": []},
            "process": {"status": "running", "attention": []},
        }
        stdout = StringIO()
        with patch("agentsassemble.cli._request_json", side_effect=[start_response, starting_snapshot, ready_snapshot]) as request_json:
            with patch("agentsassemble.cli.time.sleep") as sleep:
                with patch("sys.stdout", stdout):
                    exit_code = main(
                        [
                            "live-agent",
                            "start-session",
                            "--server",
                            "http://room.local",
                            "--live-agent-config",
                            "configs/live-agents.start-session.example.json",
                            "--wait-ready",
                            "--wait-timeout",
                            "3",
                            "--wait-poll-interval",
                            "0.1",
                        ]
                    )

        self.assertEqual(exit_code, 0)
        self.assertEqual(request_json.call_count, 3)
        request_json.assert_any_call(
            "http://room.local/api/live-agent-sessions/start",
            method="POST",
            payload={
                "meeting_id": "",
                "group_id": "",
                "council_config_path": "",
                "agent_config_path": "",
                "live_agent_config_path": "configs/live-agents.start-session.example.json",
                "connect_timeout_seconds": 5.0,
                "auto_restart": False,
                "max_restarts": 0,
                "restart_backoff_seconds": 5.0,
                "stale_restart_after_seconds": 0.0,
            },
            timeout_seconds=11.0,
        )
        readiness_url = (
            "http://room.local/api/live-agent-sessions/readiness?"
            "meeting_id=generated-m1&group_id=generated-group"
        )
        self.assertEqual(request_json.call_args_list[1].args, (readiness_url,))
        self.assertEqual(request_json.call_args_list[2].args, (readiness_url,))
        self.assertIn("timeout_seconds", request_json.call_args_list[1].kwargs)
        self.assertNotIn("method", request_json.call_args_list[1].kwargs)
        self.assertNotIn("payload", request_json.call_args_list[1].kwargs)
        self.assertEqual(sleep.call_count, 1)
        self.assertIn("Resident session generated-m1 ready", stdout.getvalue())
        self.assertIn("2/2 connected", stdout.getvalue())

    def test_live_agent_start_session_wait_ready_times_out_with_last_summary(self):
        start_response = {
            "status": "starting",
            "meeting_id": "resident-m1",
            "group_id": "resident-main",
            "connection": {"expected": 2, "connected": 1, "attention": ["agent-b:offline"]},
        }
        degraded_snapshot = {
            "status": "degraded",
            "meeting_id": "resident-m1",
            "group_id": "resident-main",
            "connection": {"expected": 2, "connected": 1, "attention": ["agent-b:offline"]},
            "process": {"status": "running", "attention": []},
        }
        stdout = StringIO()
        with patch("agentsassemble.cli._request_json", side_effect=[start_response, degraded_snapshot]) as request_json:
            with patch("agentsassemble.cli.time.monotonic", side_effect=[0.0, 0.0, 1.1, 1.1]):
                with patch("agentsassemble.cli.time.sleep") as sleep:
                    with patch("sys.stdout", stdout):
                        exit_code = main(
                            [
                                "live-agent",
                                "start-session",
                                "--server",
                                "http://room.local",
                                "--live-agent-config",
                                "configs/live-agents.start-session.example.json",
                                "--wait-ready",
                                "--wait-timeout",
                                "1",
                                "--wait-poll-interval",
                                "0.1",
                            ]
                        )

        self.assertEqual(exit_code, 1)
        self.assertEqual(request_json.call_count, 2)
        sleep.assert_not_called()
        self.assertIn("Resident session resident-m1 degraded", stdout.getvalue())
        self.assertIn("agent-b:offline", stdout.getvalue())

    def test_live_agent_start_session_wait_ready_checks_final_readiness_even_when_initial_response_is_ready(self):
        start_response = {
            "status": "ready",
            "meeting_id": "resident-m1",
            "group_id": "resident-main",
            "connection": {"expected": 1, "connected": 1, "attention": []},
            "auto_rounds": {"status": "answered", "round_count": 1, "answered_round_count": 1},
        }
        final_snapshot = {
            "status": "degraded",
            "meeting_id": "resident-m1",
            "group_id": "resident-main",
            "connection": {"expected": 1, "connected": 1, "attention": []},
            "process": {"status": "running", "attention": []},
            "ownership": {"attention": ["meeting:duplicate_active_group"]},
        }
        stdout = StringIO()
        with patch("agentsassemble.cli._request_json", side_effect=[start_response, final_snapshot]) as request_json:
            with patch("sys.stdout", stdout):
                exit_code = main(
                    [
                        "live-agent",
                        "start-session",
                        "--server",
                        "http://room.local",
                        "--live-agent-config",
                        "configs/live-agents.start-session.example.json",
                        "--wait-ready",
                        "--wait-timeout",
                        "0",
                    ]
                )

        self.assertEqual(exit_code, 1)
        self.assertEqual(request_json.call_count, 2)
        self.assertEqual(
            request_json.call_args_list[1].args,
            (
                "http://room.local/api/live-agent-sessions/readiness?"
                "meeting_id=resident-m1&group_id=resident-main",
            ),
        )
        self.assertNotIn("method", request_json.call_args_list[1].kwargs)
        self.assertNotIn("payload", request_json.call_args_list[1].kwargs)
        self.assertIn("Resident session resident-m1 degraded", stdout.getvalue())
        self.assertIn("meeting:duplicate_active_group", stdout.getvalue())
        self.assertIn("rounds answered: 1 rounds, 1 answered", stdout.getvalue())

    def test_live_agent_start_session_wait_ready_times_out_after_initial_ready_without_unverified_success(self):
        start_response = {
            "status": "ready",
            "meeting_id": "resident-m1",
            "group_id": "resident-main",
            "connection": {"expected": 1, "connected": 1, "attention": []},
            "auto_rounds": {"status": "answered", "round_count": 1, "answered_round_count": 1},
        }
        stdout = StringIO()
        with patch("agentsassemble.cli._request_json", side_effect=[start_response, TimeoutError()]) as request_json:
            with patch("sys.stdout", stdout):
                exit_code = main(
                    [
                        "live-agent",
                        "start-session",
                        "--server",
                        "http://room.local",
                        "--live-agent-config",
                        "configs/live-agents.start-session.example.json",
                        "--wait-ready",
                        "--wait-timeout",
                        "1",
                    ]
                )

        self.assertEqual(exit_code, 1)
        self.assertEqual(request_json.call_count, 2)
        self.assertIn("Resident session resident-m1 starting", stdout.getvalue())
        self.assertIn("rounds answered: 1 rounds, 1 answered", stdout.getvalue())

    def test_live_agent_start_session_wait_ready_preserves_finalization_failure(self):
        start_response = {
            "status": "ready",
            "meeting_id": "resident-m1",
            "group_id": "resident-main",
            "connection": {"expected": 1, "connected": 1, "attention": []},
            "auto_rounds": {"status": "answered", "round_count": 1, "answered_round_count": 1},
            "finalization": {"status": "failed", "reason": "pending_turn_request"},
        }
        ready_snapshot = {
            "status": "ready",
            "meeting_id": "resident-m1",
            "group_id": "resident-main",
            "connection": {"expected": 1, "connected": 1, "attention": []},
        }
        stdout = StringIO()
        with patch("agentsassemble.cli._request_json", side_effect=[start_response, ready_snapshot]):
            with patch("sys.stdout", stdout):
                exit_code = main(
                    [
                        "live-agent",
                        "start-session",
                        "--server",
                        "http://room.local",
                        "--live-agent-config",
                        "configs/live-agents.start-session.example.json",
                        "--wait-ready",
                        "--wait-timeout",
                        "0",
                        "--run-remaining-rounds",
                        "--finalize-after-rounds",
                    ]
                )

        self.assertEqual(exit_code, 1)
        self.assertIn("finalization failed", stdout.getvalue())

    def test_live_agent_start_session_can_run_remaining_rounds_after_ready_connection(self):
        response = {
            "status": "ready",
            "meeting_id": "resident-m1",
            "group_id": "resident-main",
            "connection": {
                "expected": 2,
                "connected": 2,
                "attention": [],
            },
            "auto_rounds": {
                "status": "answered",
                "round_count": 1,
                "answered_round_count": 1,
                "completed_round_count": 0,
                "timeout_round_count": 0,
                "skipped_round_count": 0,
                "results": [{"round_id": "round_1", "status": "answered"}],
            },
        }
        stdout = StringIO()
        with patch("agentsassemble.cli._request_json", return_value=response) as request_json:
            with patch("sys.stdout", stdout):
                exit_code = main(
                    [
                        "live-agent",
                        "start-session",
                        "--server",
                        "http://room.local",
                        "--meeting-id",
                        "resident-m1",
                        "--group-id",
                        "resident-main",
                        "--council-config",
                        "configs/demo-council.json",
                        "--agent-config",
                        "configs/agents.example.json",
                        "--live-agent-config",
                        "configs/live-agents.example.json",
                        "--connect-timeout",
                        "3",
                        "--run-remaining-rounds",
                        "--round-timeout",
                        "8",
                        "--max-rounds",
                        "2",
                        "--stop-on-timeout",
                    ]
                )

        self.assertEqual(exit_code, 0)
        request_json.assert_called_once_with(
            "http://room.local/api/live-agent-sessions/start",
            method="POST",
            payload={
                "meeting_id": "resident-m1",
                "group_id": "resident-main",
                "council_config_path": "configs/demo-council.json",
                "agent_config_path": "configs/agents.example.json",
                "live_agent_config_path": "configs/live-agents.example.json",
                "connect_timeout_seconds": 3.0,
                "auto_restart": False,
                "max_restarts": 0,
                "restart_backoff_seconds": 5.0,
                "stale_restart_after_seconds": 0.0,
                "run_remaining_rounds": True,
                "round_timeout_seconds": 8.0,
                "round_max_rounds": 2,
                "round_stop_on_timeout": True,
            },
            timeout_seconds=201.0,
        )
        self.assertIn("Resident session resident-m1 ready", stdout.getvalue())
        self.assertIn("rounds answered: 1 rounds, 1 answered", stdout.getvalue())

    def test_live_agent_start_session_can_finalize_after_remaining_rounds(self):
        response = {
            "status": "ready",
            "meeting_id": "resident-m1",
            "group_id": "resident-main",
            "connection": {"expected": 1, "connected": 1, "attention": []},
            "auto_rounds": {
                "status": "answered",
                "round_count": 1,
                "answered_round_count": 1,
                "completed_round_count": 0,
                "timeout_round_count": 0,
                "skipped_round_count": 0,
            },
            "finalization": {
                "status": "finalized",
                "meeting_id": "resident-m1",
                "official_event_count": 1,
            },
        }
        stdout = StringIO()
        with patch("agentsassemble.cli._request_json", return_value=response) as request_json:
            with patch("sys.stdout", stdout):
                exit_code = main(
                    [
                        "live-agent",
                        "start-session",
                        "--server",
                        "http://room.local",
                        "--meeting-id",
                        "resident-m1",
                        "--group-id",
                        "resident-main",
                        "--live-agent-config",
                        "configs/live-agents.example.json",
                        "--connect-timeout",
                        "3",
                        "--run-remaining-rounds",
                        "--round-timeout",
                        "8",
                        "--max-rounds",
                        "1",
                        "--finalize-after-rounds",
                    ]
                )

        self.assertEqual(exit_code, 0)
        request_json.assert_called_once_with(
            "http://room.local/api/live-agent-sessions/start",
            method="POST",
            payload={
                "meeting_id": "resident-m1",
                "group_id": "resident-main",
                "council_config_path": "",
                "agent_config_path": "",
                "live_agent_config_path": "configs/live-agents.example.json",
                "connect_timeout_seconds": 3.0,
                "auto_restart": False,
                "max_restarts": 0,
                "restart_backoff_seconds": 5.0,
                "stale_restart_after_seconds": 0.0,
                "run_remaining_rounds": True,
                "round_timeout_seconds": 8.0,
                "round_max_rounds": 1,
                "round_stop_on_timeout": False,
                "finalize_after_rounds": True,
            },
            timeout_seconds=105.0,
        )
        self.assertIn("finalization finalized: 1 official events", stdout.getvalue())

    def test_live_agent_start_session_finalize_after_rounds_failure_exits_nonzero(self):
        response = {
            "status": "ready",
            "meeting_id": "resident-m1",
            "group_id": "resident-main",
            "connection": {"expected": 1, "connected": 1, "attention": []},
            "auto_rounds": {"status": "answered", "round_count": 1, "answered_round_count": 1},
            "finalization": {"status": "failed", "reason": "pending_turn_request"},
        }
        stdout = StringIO()
        with patch("agentsassemble.cli._request_json", return_value=response):
            with patch("sys.stdout", stdout):
                exit_code = main(
                    [
                        "live-agent",
                        "start-session",
                        "--server",
                        "http://room.local",
                        "--live-agent-config",
                        "configs/live-agents.example.json",
                        "--run-remaining-rounds",
                        "--finalize-after-rounds",
                    ]
                )

        self.assertEqual(exit_code, 1)
        self.assertIn("finalization failed", stdout.getvalue())

    def test_live_agent_start_session_can_probe_bound_agents_before_rounds(self):
        response = {
            "status": "ready",
            "meeting_id": "resident-m1",
            "group_id": "resident-main",
            "connection": {"expected": 1, "connected": 1, "attention": []},
            "reply_probe": {
                "status": "ok",
                "probe_count": 1,
                "ok_count": 1,
                "timeout_count": 0,
                "failed_count": 0,
                "skipped_count": 0,
                "probes": [{"agent_id": "agent-a", "status": "ok"}],
            },
        }
        stdout = StringIO()
        with patch("agentsassemble.cli._request_json", return_value=response) as request_json:
            with patch("sys.stdout", stdout):
                exit_code = main(
                    [
                        "live-agent",
                        "start-session",
                        "--server",
                        "http://room.local",
                        "--meeting-id",
                        "resident-m1",
                        "--group-id",
                        "resident-main",
                        "--council-config",
                        "configs/demo-council.json",
                        "--agent-config",
                        "configs/agents.example.json",
                        "--live-agent-config",
                        "configs/live-agents.example.json",
                        "--connect-timeout",
                        "3",
                        "--probe-bound-agents",
                        "--probe-timeout",
                        "0.5",
                    ]
                )

        self.assertEqual(exit_code, 0)
        request_json.assert_called_once_with(
            "http://room.local/api/live-agent-sessions/start",
            method="POST",
            payload={
                "meeting_id": "resident-m1",
                "group_id": "resident-main",
                "council_config_path": "configs/demo-council.json",
                "agent_config_path": "configs/agents.example.json",
                "live_agent_config_path": "configs/live-agents.example.json",
                "connect_timeout_seconds": 3.0,
                "auto_restart": False,
                "max_restarts": 0,
                "restart_backoff_seconds": 5.0,
                "stale_restart_after_seconds": 0.0,
                "probe_bound_agents": True,
                "probe_timeout_seconds": 0.5,
            },
            timeout_seconds=21.5,
        )
        self.assertIn("probes ok: 1/1 ok", stdout.getvalue())

    def test_live_agent_start_session_auto_round_degradation_exits_nonzero(self):
        response = {
            "status": "ready",
            "meeting_id": "resident-m1",
            "group_id": "resident-main",
            "connection": {"expected": 1, "connected": 1, "attention": []},
            "auto_rounds": {
                "status": "timeout",
                "round_count": 1,
                "answered_round_count": 0,
                "completed_round_count": 0,
                "timeout_round_count": 1,
                "skipped_round_count": 0,
            },
        }
        stdout = StringIO()
        with patch("agentsassemble.cli._request_json", return_value=response):
            with patch("sys.stdout", stdout):
                exit_code = main(
                    [
                        "live-agent",
                        "start-session",
                        "--server",
                        "http://room.local",
                        "--live-agent-config",
                        "configs/live-agents.example.json",
                        "--run-remaining-rounds",
                    ]
                )

        self.assertEqual(exit_code, 1)
        self.assertIn("rounds timeout", stdout.getvalue())

    def test_live_agent_start_session_rejects_unbounded_auto_round_batch(self):
        stderr = StringIO()
        with patch("agentsassemble.cli._request_json") as request_json:
            with patch("sys.stderr", stderr):
                exit_code = main(
                    [
                        "live-agent",
                        "start-session",
                        "--live-agent-config",
                        "configs/live-agents.example.json",
                        "--run-remaining-rounds",
                        "--max-rounds",
                        "9",
                    ]
                )

        self.assertEqual(exit_code, 2)
        request_json.assert_not_called()
        self.assertIn("--max-rounds supports at most 8", stderr.getvalue())

    def test_live_agent_resume_session_parser_accepts_configs_and_restart_options(self):
        args = build_parser().parse_args(
            [
                "live-agent",
                "resume-session",
                "--server",
                "http://room.local",
                "--meeting-id",
                "resident-m1",
                "--group-id",
                "resident-main",
                "--live-agent-config",
                "configs/live-agents.example.json",
                "--connect-timeout",
                "3",
                "--auto-restart",
                "--max-restarts",
                "2",
                "--restart-backoff-seconds",
                "1",
                "--stale-restart-after-seconds",
                "30",
                "--json",
            ]
        )

        self.assertEqual(args.live_agent_command, "resume-session")
        self.assertEqual(args.meeting_id, "resident-m1")
        self.assertEqual(args.group_id, "resident-main")
        self.assertEqual(args.live_agent_config, "configs/live-agents.example.json")
        self.assertEqual(args.connect_timeout, 3.0)
        self.assertTrue(args.auto_restart)
        self.assertEqual(args.max_restarts, 2)
        self.assertEqual(args.restart_backoff_seconds, 1.0)
        self.assertEqual(args.stale_restart_after_seconds, 30.0)
        self.assertTrue(args.as_json)

    def test_live_agent_resume_session_posts_request_and_uses_status_exit_codes(self):
        response = {
            "status": "ready",
            "meeting_id": "resident-m1",
            "group_id": "resident-main",
            "connection": {
                "expected": 2,
                "connected": 2,
                "attention": [],
            },
        }
        stdout = StringIO()
        with patch("agentsassemble.cli._request_json", return_value=response) as request_json:
            with patch("sys.stdout", stdout):
                exit_code = main(
                    [
                        "live-agent",
                        "resume-session",
                        "--server",
                        "http://room.local",
                        "--meeting-id",
                        "resident-m1",
                        "--group-id",
                        "resident-main",
                        "--live-agent-config",
                        "configs/live-agents.example.json",
                        "--connect-timeout",
                        "3",
                    ]
                )

        self.assertEqual(exit_code, 0)
        request_json.assert_called_once_with(
            "http://room.local/api/live-agent-sessions/resume",
            method="POST",
            payload={
                "meeting_id": "resident-m1",
                "group_id": "resident-main",
                "live_agent_config_path": "configs/live-agents.example.json",
                "connect_timeout_seconds": 3.0,
                "auto_restart": False,
                "max_restarts": 0,
                "restart_backoff_seconds": 5.0,
                "stale_restart_after_seconds": 0.0,
            },
            timeout_seconds=9.0,
        )
        self.assertIn("Resident session resident-m1 ready", stdout.getvalue())
        self.assertIn("resident-main", stdout.getvalue())
        self.assertIn("2/2 connected", stdout.getvalue())

    def test_live_agent_resume_session_can_run_remaining_rounds_after_ready_connection(self):
        response = {
            "status": "ready",
            "meeting_id": "resident-m1",
            "group_id": "resident-main",
            "connection": {"expected": 1, "connected": 1, "attention": []},
            "auto_rounds": {
                "status": "answered",
                "round_count": 1,
                "answered_round_count": 1,
                "completed_round_count": 0,
                "timeout_round_count": 0,
                "skipped_round_count": 0,
            },
        }
        stdout = StringIO()
        with patch("agentsassemble.cli._request_json", return_value=response) as request_json:
            with patch("sys.stdout", stdout):
                exit_code = main(
                    [
                        "live-agent",
                        "resume-session",
                        "--server",
                        "http://room.local",
                        "--meeting-id",
                        "resident-m1",
                        "--group-id",
                        "resident-main",
                        "--live-agent-config",
                        "configs/live-agents.example.json",
                        "--connect-timeout",
                        "3",
                        "--run-remaining-rounds",
                        "--round-timeout",
                        "8",
                        "--max-rounds",
                        "2",
                        "--stop-on-timeout",
                    ]
                )

        self.assertEqual(exit_code, 0)
        request_json.assert_called_once_with(
            "http://room.local/api/live-agent-sessions/resume",
            method="POST",
            payload={
                "meeting_id": "resident-m1",
                "group_id": "resident-main",
                "live_agent_config_path": "configs/live-agents.example.json",
                "connect_timeout_seconds": 3.0,
                "auto_restart": False,
                "max_restarts": 0,
                "restart_backoff_seconds": 5.0,
                "stale_restart_after_seconds": 0.0,
                "run_remaining_rounds": True,
                "round_timeout_seconds": 8.0,
                "round_max_rounds": 2,
                "round_stop_on_timeout": True,
            },
            timeout_seconds=201.0,
        )
        self.assertIn("rounds answered: 1 rounds, 1 answered", stdout.getvalue())

    def test_live_agent_stop_session_parser_accepts_meeting_and_group(self):
        args = build_parser().parse_args(
            [
                "live-agent",
                "stop-session",
                "--server",
                "http://room.local",
                "--meeting-id",
                "resident-m1",
                "--group-id",
                "resident-main",
                "--json",
            ]
        )

        self.assertEqual(args.live_agent_command, "stop-session")
        self.assertEqual(args.meeting_id, "resident-m1")
        self.assertEqual(args.group_id, "resident-main")
        self.assertTrue(args.as_json)

    def test_live_agent_stop_session_posts_request_and_prints_summary(self):
        response = {
            "status": "stopped",
            "meeting_id": "resident-m1",
            "group_id": "resident-main",
            "offline": {
                "expected": 2,
                "offline": 2,
                "attention": [],
            },
            "session_runs": [
                {
                    "run_id": "run-stop-1",
                    "status": "stopped",
                    "active": False,
                }
            ],
        }
        stdout = StringIO()
        with patch("agentsassemble.cli._request_json", return_value=response) as request_json:
            with patch("sys.stdout", stdout):
                exit_code = main(
                    [
                        "live-agent",
                        "stop-session",
                        "--server",
                        "http://room.local",
                        "--meeting-id",
                        "resident-m1",
                        "--group-id",
                        "resident-main",
                    ]
                )

        self.assertEqual(exit_code, 0)
        request_json.assert_called_once_with(
            "http://room.local/api/live-agent-sessions/stop",
            method="POST",
            payload={
                "meeting_id": "resident-m1",
                "group_id": "resident-main",
            },
            timeout_seconds=20.0,
        )
        self.assertIn("Resident session resident-m1 stopped", stdout.getvalue())
        self.assertIn("resident-main", stdout.getvalue())
        self.assertIn("2/2 offline", stdout.getvalue())
        self.assertIn("1 session run stopped", stdout.getvalue())

    def test_live_agent_stop_session_returns_failure_for_degraded_stop(self):
        response = {
            "status": "stopping",
            "meeting_id": "resident-m1",
            "group_id": "resident-main",
            "offline": {
                "expected": 2,
                "offline": 1,
                "attention": ["agent-b:wrong_meeting"],
            },
        }
        stdout = StringIO()
        with patch("agentsassemble.cli._request_json", return_value=response):
            with patch("sys.stdout", stdout):
                exit_code = main(
                    [
                        "live-agent",
                        "stop-session",
                        "--server",
                        "http://room.local",
                        "--meeting-id",
                        "resident-m1",
                        "--group-id",
                        "resident-main",
                    ]
                )

        self.assertEqual(exit_code, 1)
        self.assertIn("Resident session resident-m1 stopping", stdout.getvalue())
        self.assertIn("1/2 offline", stdout.getvalue())
        self.assertIn("agent-b:wrong_meeting", stdout.getvalue())

    def test_live_agent_finalize_meeting_parser_accepts_meeting_id_and_force(self):
        args = build_parser().parse_args(
            [
                "live-agent",
                "finalize-meeting",
                "--server",
                "http://room.local",
                "--meeting-id",
                "resident-m1",
                "--force",
                "--json",
            ]
        )

        self.assertEqual(args.live_agent_command, "finalize-meeting")
        self.assertEqual(args.meeting_id, "resident-m1")
        self.assertTrue(args.force)
        self.assertTrue(args.as_json)

    def test_live_agent_finalize_meeting_posts_request_and_prints_summary(self):
        response = {
            "status": "finalized",
            "meeting_id": "resident-m1",
            "official_event_count": 2,
        }
        stdout = StringIO()
        with patch("agentsassemble.cli._request_json", return_value=response) as request_json:
            with patch("sys.stdout", stdout):
                exit_code = main(
                    [
                        "live-agent",
                        "finalize-meeting",
                        "--server",
                        "http://room.local",
                        "--meeting-id",
                        "resident-m1",
                    ]
                )

        self.assertEqual(exit_code, 0)
        request_json.assert_called_once_with(
            "http://room.local/api/meetings/resident-m1/finalize",
            method="POST",
            payload={"force": False},
            timeout_seconds=20.0,
        )
        self.assertIn("Finalized resident-m1: 2 official events", stdout.getvalue())

    def test_live_agent_check_session_parser_accepts_meeting_group_and_fail_flag(self):
        args = build_parser().parse_args(
            [
                "live-agent",
                "check-session",
                "--server",
                "http://room.local",
                "--meeting-id",
                "resident-m1",
                "--group-id",
                "resident-main",
                "--fail-on-degraded",
                "--json",
            ]
        )

        self.assertEqual(args.live_agent_command, "check-session")
        self.assertEqual(args.meeting_id, "resident-m1")
        self.assertEqual(args.group_id, "resident-main")
        self.assertTrue(args.fail_on_degraded)
        self.assertTrue(args.as_json)

    def test_live_agent_check_session_posts_request_and_prints_summary(self):
        response = {
            "status": "ready",
            "meeting_id": "resident-m1",
            "group_id": "resident-main",
            "process": {"status": "running", "attention": []},
            "connection": {"expected": 2, "connected": 2, "attention": []},
        }
        stdout = StringIO()
        with patch("agentsassemble.cli._request_json", return_value=response) as request_json:
            with patch("sys.stdout", stdout):
                exit_code = main(
                    [
                        "live-agent",
                        "check-session",
                        "--server",
                        "http://room.local",
                        "--meeting-id",
                        "resident-m1",
                        "--group-id",
                        "resident-main",
                    ]
                )

        self.assertEqual(exit_code, 0)
        request_json.assert_called_once_with(
            "http://room.local/api/live-agent-sessions/check",
            method="POST",
            payload={"meeting_id": "resident-m1", "group_id": "resident-main"},
            timeout_seconds=10.0,
        )
        self.assertIn("Resident session resident-m1 ready", stdout.getvalue())
        self.assertIn("resident-main", stdout.getvalue())
        self.assertIn("2/2 connected", stdout.getvalue())
        self.assertIn("process running", stdout.getvalue())

    def test_live_agent_check_session_fail_on_degraded_returns_failure(self):
        response = {
            "status": "degraded",
            "meeting_id": "resident-m1",
            "group_id": "resident-main",
            "process": {"status": "stopped", "attention": ["group:stopped"]},
            "connection": {"expected": 2, "connected": 1, "attention": ["agent-b:offline"]},
            "ownership": {"attention": ["meeting:duplicate_active_group"]},
            "process_reason": {
                "event_type": "recovered_unknown",
                "reason": "orphan running record marked unknown",
            },
        }
        stdout = StringIO()
        with patch("agentsassemble.cli._request_json", return_value=response):
            with patch("sys.stdout", stdout):
                default_exit = main(
                    [
                        "live-agent",
                        "check-session",
                        "--server",
                        "http://room.local",
                        "--meeting-id",
                        "resident-m1",
                        "--group-id",
                        "resident-main",
                    ]
                )
                strict_exit = main(
                    [
                        "live-agent",
                        "check-session",
                        "--server",
                        "http://room.local",
                        "--meeting-id",
                        "resident-m1",
                        "--group-id",
                        "resident-main",
                        "--fail-on-degraded",
                    ]
                )

        self.assertEqual(default_exit, 0)
        self.assertEqual(strict_exit, 1)
        self.assertIn("agent-b:offline", stdout.getvalue())
        self.assertIn("group:stopped", stdout.getvalue())
        self.assertIn("meeting:duplicate_active_group", stdout.getvalue())

    def test_live_agent_session_readiness_parser_accepts_meeting_group_and_fail_flag(self):
        args = build_parser().parse_args(
            [
                "live-agent",
                "session-readiness",
                "--server",
                "http://room.local",
                "--meeting-id",
                "resident-m1",
                "--group-id",
                "resident-main",
                "--fail-on-degraded",
                "--json",
            ]
        )

        self.assertEqual(args.live_agent_command, "session-readiness")
        self.assertEqual(args.meeting_id, "resident-m1")
        self.assertEqual(args.group_id, "resident-main")
        self.assertTrue(args.fail_on_degraded)
        self.assertTrue(args.as_json)

    def test_live_agent_session_readiness_gets_read_only_endpoint_and_prints_summary(self):
        response = {
            "status": "ready",
            "meeting_id": "resident-m1",
            "group_id": "resident-main",
            "process": {"status": "running", "attention": []},
            "connection": {"expected": 2, "connected": 2, "attention": []},
        }
        stdout = StringIO()
        with patch("agentsassemble.cli._request_json", return_value=response) as request_json:
            with patch("sys.stdout", stdout):
                exit_code = main(
                    [
                        "live-agent",
                        "session-readiness",
                        "--server",
                        "http://room.local",
                        "--meeting-id",
                        "resident-m1",
                        "--group-id",
                        "resident-main",
                    ]
                )

        self.assertEqual(exit_code, 0)
        request_json.assert_called_once_with(
            "http://room.local/api/live-agent-sessions/readiness?meeting_id=resident-m1&group_id=resident-main",
            timeout_seconds=10.0,
        )
        self.assertIn("Resident session resident-m1 ready", stdout.getvalue())
        self.assertIn("2/2 connected", stdout.getvalue())

    def test_live_agent_session_readiness_fail_on_degraded_returns_failure(self):
        response = {
            "status": "degraded",
            "meeting_id": "resident-m1",
            "group_id": "resident-main",
            "process": {"status": "stopped", "attention": ["group:stopped"]},
            "connection": {"expected": 2, "connected": 1, "attention": ["agent-b:offline"]},
            "ownership": {"attention": ["meeting:duplicate_active_group"]},
            "process_reason": {
                "event_type": "recovered_unknown",
                "reason": "orphan running record marked unknown",
            },
        }
        stdout = StringIO()
        with patch("agentsassemble.cli._request_json", return_value=response):
            with patch("sys.stdout", stdout):
                exit_code = main(
                    [
                        "live-agent",
                        "session-readiness",
                        "--server",
                        "http://room.local",
                        "--meeting-id",
                        "resident-m1",
                        "--group-id",
                        "resident-main",
                        "--fail-on-degraded",
                    ]
                )

        self.assertEqual(exit_code, 1)
        self.assertIn("agent-b:offline", stdout.getvalue())
        self.assertIn("group:stopped", stdout.getvalue())
        self.assertIn("meeting:duplicate_active_group", stdout.getvalue())
        self.assertIn("reason recovered_unknown orphan running record marked unknown", stdout.getvalue())

    def test_live_agent_restart_session_parser_accepts_meeting_group_and_timeout(self):
        args = build_parser().parse_args(
            [
                "live-agent",
                "restart-session",
                "--server",
                "http://room.local",
                "--meeting-id",
                "resident-m1",
                "--group-id",
                "resident-main",
                "--connect-timeout",
                "7",
                "--run-remaining-rounds",
                "--round-timeout",
                "8",
                "--max-rounds",
                "2",
                "--stop-on-timeout",
                "--probe-bound-agents",
                "--probe-timeout",
                "4",
                "--json",
            ]
        )

        self.assertEqual(args.live_agent_command, "restart-session")
        self.assertEqual(args.meeting_id, "resident-m1")
        self.assertEqual(args.group_id, "resident-main")
        self.assertEqual(args.connect_timeout, 7.0)
        self.assertTrue(args.run_remaining_rounds)
        self.assertEqual(args.round_timeout, 8.0)
        self.assertEqual(args.max_rounds, 2)
        self.assertTrue(args.stop_on_timeout)
        self.assertTrue(args.probe_bound_agents)
        self.assertEqual(args.probe_timeout, 4.0)
        self.assertTrue(args.as_json)

    def test_live_agent_restart_session_wait_ready_uses_read_only_readiness_after_restart(self):
        restart_response = {
            "status": "starting",
            "meeting_id": "resident-m1",
            "group_id": "resident-main",
            "process": {"status": "running", "attention": []},
            "connection": {"expected": 2, "connected": 1, "attention": ["agent-b:offline"]},
        }
        ready_snapshot = {
            "status": "ready",
            "meeting_id": "resident-m1",
            "group_id": "resident-main",
            "process": {"status": "running", "attention": []},
            "connection": {"expected": 2, "connected": 2, "attention": []},
        }
        stdout = StringIO()
        with patch("agentsassemble.cli._request_json", side_effect=[restart_response, ready_snapshot]) as request_json:
            with patch("sys.stdout", stdout):
                exit_code = main(
                    [
                        "live-agent",
                        "restart-session",
                        "--server",
                        "http://room.local",
                        "--meeting-id",
                        "resident-m1",
                        "--group-id",
                        "resident-main",
                        "--wait-ready",
                        "--wait-timeout",
                        "3",
                    ]
                )

        self.assertEqual(exit_code, 0)
        self.assertEqual(request_json.call_count, 2)
        request_json.assert_any_call(
            "http://room.local/api/live-agent-sessions/restart",
            method="POST",
            payload={
                "meeting_id": "resident-m1",
                "group_id": "resident-main",
                "connect_timeout_seconds": 5.0,
            },
            timeout_seconds=11.0,
        )
        self.assertEqual(
            request_json.call_args_list[1].args,
            (
                "http://room.local/api/live-agent-sessions/readiness?"
                "meeting_id=resident-m1&group_id=resident-main",
            ),
        )
        self.assertNotIn("method", request_json.call_args_list[1].kwargs)
        self.assertNotIn("payload", request_json.call_args_list[1].kwargs)
        self.assertIn("Resident session resident-m1 ready", stdout.getvalue())

    def test_live_agent_restart_session_can_run_remaining_rounds_after_ready_connection(self):
        response = {
            "status": "ready",
            "meeting_id": "resident-m1",
            "group_id": "resident-main",
            "process": {"status": "running", "attention": []},
            "connection": {"expected": 2, "connected": 2, "attention": []},
            "auto_rounds": {
                "status": "answered",
                "round_count": 1,
                "answered_round_count": 1,
                "completed_round_count": 0,
                "timeout_round_count": 0,
                "skipped_round_count": 0,
                "results": [{"round_id": "round_1", "status": "answered"}],
            },
        }
        stdout = StringIO()
        with patch("agentsassemble.cli._request_json", return_value=response) as request_json:
            with patch("sys.stdout", stdout):
                exit_code = main(
                    [
                        "live-agent",
                        "restart-session",
                        "--server",
                        "http://room.local",
                        "--meeting-id",
                        "resident-m1",
                        "--group-id",
                        "resident-main",
                        "--connect-timeout",
                        "7",
                        "--run-remaining-rounds",
                        "--round-timeout",
                        "8",
                        "--max-rounds",
                        "2",
                        "--stop-on-timeout",
                    ]
                )

        self.assertEqual(exit_code, 0)
        request_json.assert_called_once_with(
            "http://room.local/api/live-agent-sessions/restart",
            method="POST",
            payload={
                "meeting_id": "resident-m1",
                "group_id": "resident-main",
                "connect_timeout_seconds": 7.0,
                "run_remaining_rounds": True,
                "round_timeout_seconds": 8.0,
                "round_max_rounds": 2,
                "round_stop_on_timeout": True,
            },
            timeout_seconds=205.0,
        )
        self.assertIn("Resident session resident-m1 ready", stdout.getvalue())
        self.assertIn("rounds answered: 1 rounds, 1 answered", stdout.getvalue())

    def test_live_agent_restart_session_posts_request_and_prints_summary(self):
        response = {
            "status": "ready",
            "meeting_id": "resident-m1",
            "group_id": "resident-main",
            "process": {"status": "running", "attention": []},
            "connection": {"expected": 2, "connected": 2, "attention": []},
        }
        stdout = StringIO()
        with patch("agentsassemble.cli._request_json", return_value=response) as request_json:
            with patch("sys.stdout", stdout):
                exit_code = main(
                    [
                        "live-agent",
                        "restart-session",
                        "--server",
                        "http://room.local",
                        "--meeting-id",
                        "resident-m1",
                        "--group-id",
                        "resident-main",
                        "--connect-timeout",
                        "7",
                    ]
                )

        self.assertEqual(exit_code, 0)
        request_json.assert_called_once_with(
            "http://room.local/api/live-agent-sessions/restart",
            method="POST",
            payload={
                "meeting_id": "resident-m1",
                "group_id": "resident-main",
                "connect_timeout_seconds": 7.0,
            },
            timeout_seconds=13.0,
        )
        self.assertIn("Resident session resident-m1 ready", stdout.getvalue())
        self.assertIn("resident-main", stdout.getvalue())
        self.assertIn("2/2 connected", stdout.getvalue())

    def test_live_agent_restart_session_returns_failure_for_starting_status(self):
        response = {
            "status": "starting",
            "meeting_id": "resident-m1",
            "group_id": "resident-main",
            "process": {"status": "running", "attention": []},
            "connection": {"expected": 2, "connected": 1, "attention": ["agent-b:offline"]},
        }
        stdout = StringIO()
        with patch("agentsassemble.cli._request_json", return_value=response):
            with patch("sys.stdout", stdout):
                exit_code = main(
                    [
                        "live-agent",
                        "restart-session",
                        "--server",
                        "http://room.local",
                        "--meeting-id",
                        "resident-m1",
                        "--group-id",
                        "resident-main",
                    ]
                )

        self.assertEqual(exit_code, 1)
        self.assertIn("Resident session resident-m1 starting", stdout.getvalue())
        self.assertIn("1/2 connected", stdout.getvalue())
        self.assertIn("agent-b:offline", stdout.getvalue())

    def test_live_agent_recover_session_parser_accepts_meeting_group_and_timeout(self):
        args = build_parser().parse_args(
            [
                "live-agent",
                "recover-session",
                "--server",
                "http://room.local",
                "--meeting-id",
                "resident-m1",
                "--group-id",
                "resident-main",
                "--connect-timeout",
                "7",
                "--run-remaining-rounds",
                "--round-timeout",
                "8",
                "--max-rounds",
                "2",
                "--stop-on-timeout",
                "--probe-bound-agents",
                "--probe-timeout",
                "4",
                "--json",
            ]
        )

        self.assertEqual(args.live_agent_command, "recover-session")
        self.assertEqual(args.meeting_id, "resident-m1")
        self.assertEqual(args.group_id, "resident-main")
        self.assertEqual(args.connect_timeout, 7.0)
        self.assertTrue(args.run_remaining_rounds)
        self.assertEqual(args.round_timeout, 8.0)
        self.assertEqual(args.max_rounds, 2)
        self.assertTrue(args.stop_on_timeout)
        self.assertTrue(args.probe_bound_agents)
        self.assertEqual(args.probe_timeout, 4.0)
        self.assertTrue(args.as_json)

    def test_live_agent_recover_session_posts_request_and_prints_summary(self):
        response = {
            "status": "ready",
            "meeting_id": "resident-m1",
            "group_id": "resident-main",
            "process": {"status": "running", "attention": []},
            "connection": {"expected": 2, "connected": 2, "attention": []},
            "offline": {"expected": 2, "offline": 2, "attention": []},
        }
        stdout = StringIO()
        with patch("agentsassemble.cli._request_json", return_value=response) as request_json:
            with patch("sys.stdout", stdout):
                exit_code = main(
                    [
                        "live-agent",
                        "recover-session",
                        "--server",
                        "http://room.local",
                        "--meeting-id",
                        "resident-m1",
                        "--group-id",
                        "resident-main",
                        "--connect-timeout",
                        "7",
                    ]
                )

        self.assertEqual(exit_code, 0)
        request_json.assert_called_once_with(
            "http://room.local/api/live-agent-sessions/recover",
            method="POST",
            payload={
                "meeting_id": "resident-m1",
                "group_id": "resident-main",
                "connect_timeout_seconds": 7.0,
            },
            timeout_seconds=13.0,
        )
        self.assertIn("Resident session resident-m1 ready", stdout.getvalue())
        self.assertIn("resident-main", stdout.getvalue())
        self.assertIn("2/2 connected", stdout.getvalue())

    def test_live_agent_recover_session_returns_failure_for_starting_status(self):
        response = {
            "status": "starting",
            "meeting_id": "resident-m1",
            "group_id": "resident-main",
            "process": {"status": "running", "attention": []},
            "connection": {"expected": 2, "connected": 1, "attention": ["agent-b:offline"]},
            "offline": {"expected": 2, "offline": 2, "attention": []},
        }
        stdout = StringIO()
        with patch("agentsassemble.cli._request_json", return_value=response):
            with patch("sys.stdout", stdout):
                exit_code = main(
                    [
                        "live-agent",
                        "recover-session",
                        "--server",
                        "http://room.local",
                        "--meeting-id",
                        "resident-m1",
                        "--group-id",
                        "resident-main",
                    ]
                )

        self.assertEqual(exit_code, 1)
        self.assertIn("Resident session resident-m1 starting", stdout.getvalue())
        self.assertIn("1/2 connected", stdout.getvalue())
        self.assertIn("agent-b:offline", stdout.getvalue())

    def test_live_agent_ensure_session_parser_accepts_session_configs_and_wait_options(self):
        args = build_parser().parse_args(
            [
                "live-agent",
                "ensure-session",
                "--server",
                "http://room.local",
                "--meeting-id",
                "resident-m1",
                "--group-id",
                "resident-main",
                "--council-config",
                "configs/demo-council.json",
                "--agent-config",
                "configs/agents.example.json",
                "--live-agent-config",
                "configs/live-agents.example.json",
                "--connect-timeout",
                "3",
                "--wait-timeout",
                "9",
                "--wait-poll-interval",
                "0.25",
                "--auto-restart",
                "--max-restarts",
                "2",
                "--restart-backoff-seconds",
                "1",
                "--stale-restart-after-seconds",
                "30",
                "--probe-bound-agents",
                "--probe-timeout",
                "0.5",
                "--run-remaining-rounds",
                "--round-timeout",
                "2",
                "--max-rounds",
                "1",
                "--stop-on-timeout",
                "--json",
            ]
        )

        self.assertEqual(args.live_agent_command, "ensure-session")
        self.assertEqual(args.meeting_id, "resident-m1")
        self.assertEqual(args.group_id, "resident-main")
        self.assertEqual(args.council_config, "configs/demo-council.json")
        self.assertEqual(args.agent_config, "configs/agents.example.json")
        self.assertEqual(args.live_agent_config, "configs/live-agents.example.json")
        self.assertEqual(args.connect_timeout, 3.0)
        self.assertEqual(args.wait_timeout, 9.0)
        self.assertEqual(args.wait_poll_interval, 0.25)
        self.assertTrue(args.auto_restart)
        self.assertEqual(args.max_restarts, 2)
        self.assertEqual(args.restart_backoff_seconds, 1.0)
        self.assertEqual(args.stale_restart_after_seconds, 30.0)
        self.assertTrue(args.probe_bound_agents)
        self.assertEqual(args.probe_timeout, 0.5)
        self.assertTrue(args.run_remaining_rounds)
        self.assertEqual(args.round_timeout, 2.0)
        self.assertEqual(args.max_rounds, 1)
        self.assertTrue(args.stop_on_timeout)
        self.assertTrue(args.as_json)

    def test_live_agent_ensure_session_posts_ready_snapshot_to_server_for_drift_check(self):
        ready_snapshot = {
            "status": "ready",
            "meeting_id": "resident-m1",
            "group_id": "resident-main",
            "process": {"status": "running", "attention": []},
            "connection": {"expected": 2, "connected": 2, "attention": []},
        }
        ensured_response = {**ready_snapshot, "action": "none"}
        stdout = StringIO()
        with patch("agentsassemble.cli._request_json", side_effect=[ready_snapshot, ensured_response]) as request_json:
            with patch("sys.stdout", stdout):
                exit_code = main(
                    [
                        "live-agent",
                        "ensure-session",
                        "--server",
                        "http://room.local",
                        "--meeting-id",
                        "resident-m1",
                        "--group-id",
                        "resident-main",
                        "--live-agent-config",
                        "configs/live-agents.example.json",
                    ]
                )

        self.assertEqual(exit_code, 0)
        self.assertEqual(request_json.call_count, 2)
        request_json.assert_any_call(
            "http://room.local/api/live-agent-sessions/readiness?meeting_id=resident-m1&group_id=resident-main",
            timeout_seconds=10.0,
        )
        request_json.assert_any_call(
            "http://room.local/api/live-agent-sessions/ensure",
            method="POST",
            payload={
                "meeting_id": "resident-m1",
                "group_id": "resident-main",
                "council_config_path": "",
                "agent_config_path": "",
                "live_agent_config_path": "configs/live-agents.example.json",
                "connect_timeout_seconds": 5.0,
                "auto_restart": False,
                "max_restarts": 0,
                "restart_backoff_seconds": 5.0,
                "stale_restart_after_seconds": 0.0,
            },
            timeout_seconds=11.0,
        )
        self.assertIn("Ensured via none", stdout.getvalue())
        self.assertIn("Resident session resident-m1 ready", stdout.getvalue())

    def test_live_agent_ensure_session_waits_when_ready_snapshot_restarts_for_drift(self):
        ready_snapshot = {
            "status": "ready",
            "meeting_id": "resident-m1",
            "group_id": "resident-main",
            "process": {"status": "running", "attention": []},
            "connection": {"expected": 2, "connected": 2, "attention": []},
        }
        restart_response = {
            "status": "starting",
            "action": "restart",
            "meeting_id": "resident-m1",
            "group_id": "resident-main",
            "process": {"status": "restarting", "attention": []},
            "connection": {"expected": 2, "connected": 1, "attention": ["agent-a:not_reconnected"]},
        }
        final_ready = {
            "status": "ready",
            "meeting_id": "resident-m1",
            "group_id": "resident-main",
            "process": {"status": "running", "attention": []},
            "connection": {"expected": 2, "connected": 2, "attention": []},
        }
        stdout = StringIO()
        with patch("agentsassemble.cli._request_json", side_effect=[ready_snapshot, restart_response, final_ready]) as request_json:
            with patch("sys.stdout", stdout):
                exit_code = main(
                    [
                        "live-agent",
                        "ensure-session",
                        "--server",
                        "http://room.local",
                        "--meeting-id",
                        "resident-m1",
                        "--group-id",
                        "resident-main",
                        "--live-agent-config",
                        "configs/live-agents.example.json",
                        "--wait-timeout",
                        "3",
                    ]
                )

        self.assertEqual(exit_code, 0)
        self.assertEqual(request_json.call_count, 3)
        self.assertEqual(
            request_json.call_args_list[2].args,
            (
                "http://room.local/api/live-agent-sessions/readiness?"
                "meeting_id=resident-m1&group_id=resident-main",
            ),
        )
        self.assertIn("Ensured via restart", stdout.getvalue())
        self.assertIn("Resident session resident-m1 ready", stdout.getvalue())

    def test_live_agent_ensure_session_ready_noop_can_probe_and_run_remaining_rounds(self):
        ready_snapshot = {
            "status": "ready",
            "meeting_id": "resident-m1",
            "group_id": "resident-main",
            "process": {"status": "running", "attention": []},
            "connection": {"expected": 2, "connected": 2, "attention": []},
        }
        ensured_response = {
            **ready_snapshot,
            "action": "none",
            "reply_probe": {"status": "ok", "agent_count": 2},
            "auto_rounds": {"status": "answered", "round_count": 1},
        }
        stdout = StringIO()
        with patch("agentsassemble.cli._request_json", side_effect=[ready_snapshot, ensured_response]) as request_json:
            with patch("sys.stdout", stdout):
                exit_code = main(
                    [
                        "live-agent",
                        "ensure-session",
                        "--server",
                        "http://room.local",
                        "--meeting-id",
                        "resident-m1",
                        "--group-id",
                        "resident-main",
                        "--live-agent-config",
                        "configs/live-agents.example.json",
                        "--probe-bound-agents",
                        "--probe-timeout",
                        "0.5",
                        "--run-remaining-rounds",
                        "--round-timeout",
                        "2",
                        "--max-rounds",
                        "1",
                        "--stop-on-timeout",
                        "--finalize-after-rounds",
                    ]
                )

        self.assertEqual(exit_code, 0)
        self.assertEqual(request_json.call_count, 2)
        expected_timeout = (
            5.0
            + cli_module._operation_http_timeout(0.5, windows=cli_module.SESSION_BOUND_PROBE_HTTP_WINDOWS)
            + cli_module._operation_http_timeout(2.0, windows=cli_module.MAX_LIVE_AGENT_SEQUENCE_TURNS)
        )
        request_json.assert_any_call(
            "http://room.local/api/live-agent-sessions/ensure",
            method="POST",
            payload={
                "meeting_id": "resident-m1",
                "group_id": "resident-main",
                "council_config_path": "",
                "agent_config_path": "",
                "live_agent_config_path": "configs/live-agents.example.json",
                "connect_timeout_seconds": 5.0,
                "auto_restart": False,
                "max_restarts": 0,
                "restart_backoff_seconds": 5.0,
                "stale_restart_after_seconds": 0.0,
                "probe_bound_agents": True,
                "probe_timeout_seconds": 0.5,
                "run_remaining_rounds": True,
                "round_timeout_seconds": 2.0,
                "round_max_rounds": 1,
                "round_stop_on_timeout": True,
                "finalize_after_rounds": True,
            },
            timeout_seconds=expected_timeout,
        )
        self.assertIn("Ensured via none", stdout.getvalue())
        self.assertIn("Resident session resident-m1 ready", stdout.getvalue())

    def test_live_agent_ensure_session_starts_when_meeting_is_missing(self):
        start_response = {
            "status": "ready",
            "meeting_id": "resident-m1",
            "group_id": "resident-main",
            "connection": {"expected": 2, "connected": 2, "attention": []},
        }
        ready_snapshot = {
            "status": "ready",
            "meeting_id": "resident-m1",
            "group_id": "resident-main",
            "process": {"status": "running", "attention": []},
            "connection": {"expected": 2, "connected": 2, "attention": []},
        }
        stdout = StringIO()
        with patch(
            "agentsassemble.cli._request_json",
            side_effect=[ValueError("Meeting resident-m1 was not found."), start_response, ready_snapshot],
        ) as request_json:
            with patch("sys.stdout", stdout):
                exit_code = main(
                    [
                        "live-agent",
                        "ensure-session",
                        "--server",
                        "http://room.local",
                        "--meeting-id",
                        "resident-m1",
                        "--group-id",
                        "resident-main",
                        "--council-config",
                        "configs/demo-council.json",
                        "--agent-config",
                        "configs/agents.example.json",
                        "--live-agent-config",
                        "configs/live-agents.example.json",
                        "--connect-timeout",
                        "3",
                    ]
                )

        self.assertEqual(exit_code, 0)
        self.assertEqual(request_json.call_count, 3)
        self.assertEqual(
            request_json.call_args_list[0].args,
            ("http://room.local/api/live-agent-sessions/readiness?meeting_id=resident-m1&group_id=resident-main",),
        )
        request_json.assert_any_call(
            "http://room.local/api/live-agent-sessions/start",
            method="POST",
            payload={
                "meeting_id": "resident-m1",
                "group_id": "resident-main",
                "council_config_path": "configs/demo-council.json",
                "agent_config_path": "configs/agents.example.json",
                "live_agent_config_path": "configs/live-agents.example.json",
                "connect_timeout_seconds": 3.0,
                "auto_restart": False,
                "max_restarts": 0,
                "restart_backoff_seconds": 5.0,
                "stale_restart_after_seconds": 0.0,
            },
            timeout_seconds=9.0,
        )
        self.assertIn("Ensured via start", stdout.getvalue())

    def test_live_agent_ensure_session_resumes_when_group_is_missing_for_existing_meeting(self):
        degraded_snapshot = {
            "status": "degraded",
            "meeting_id": "resident-m1",
            "group_id": "resident-main",
            "group": {},
            "process": {"status": "unknown", "attention": ["group:unknown"]},
            "connection": {"expected": 2, "connected": 0, "attention": ["agent-a:offline", "agent-b:offline"]},
        }
        resume_response = {
            "status": "ready",
            "meeting_id": "resident-m1",
            "group_id": "resident-main",
            "connection": {"expected": 2, "connected": 2, "attention": []},
        }
        ready_snapshot = {
            "status": "ready",
            "meeting_id": "resident-m1",
            "group_id": "resident-main",
            "process": {"status": "running", "attention": []},
            "connection": {"expected": 2, "connected": 2, "attention": []},
        }
        stdout = StringIO()
        with patch("agentsassemble.cli._request_json", side_effect=[degraded_snapshot, resume_response, ready_snapshot]) as request_json:
            with patch("sys.stdout", stdout):
                exit_code = main(
                    [
                        "live-agent",
                        "ensure-session",
                        "--server",
                        "http://room.local",
                        "--meeting-id",
                        "resident-m1",
                        "--group-id",
                        "resident-main",
                        "--council-config",
                        "configs/demo-council.json",
                        "--agent-config",
                        "configs/agents.example.json",
                        "--live-agent-config",
                        "configs/live-agents.example.json",
                    ]
                )

        self.assertEqual(exit_code, 0)
        request_json.assert_any_call(
            "http://room.local/api/live-agent-sessions/resume",
            method="POST",
            payload={
                "meeting_id": "resident-m1",
                "group_id": "resident-main",
                "live_agent_config_path": "configs/live-agents.example.json",
                "connect_timeout_seconds": 5.0,
                "auto_restart": False,
                "max_restarts": 0,
                "restart_backoff_seconds": 5.0,
                "stale_restart_after_seconds": 0.0,
            },
            timeout_seconds=11.0,
        )
        self.assertIn("Ensured via resume", stdout.getvalue())

    def test_live_agent_ensure_session_resumes_running_degraded_session_and_waits_ready(self):
        degraded_snapshot = {
            "status": "degraded",
            "meeting_id": "resident-m1",
            "group_id": "resident-main",
            "process": {"status": "running", "attention": []},
            "connection": {"expected": 2, "connected": 1, "attention": ["agent-b:offline"]},
        }
        resume_response = {
            "status": "starting",
            "meeting_id": "resident-m1",
            "group_id": "resident-main",
            "process": {"status": "running", "attention": []},
            "connection": {"expected": 2, "connected": 1, "attention": ["agent-b:offline"]},
        }
        ready_snapshot = {
            "status": "ready",
            "meeting_id": "resident-m1",
            "group_id": "resident-main",
            "process": {"status": "running", "attention": []},
            "connection": {"expected": 2, "connected": 2, "attention": []},
        }
        stdout = StringIO()
        with patch("agentsassemble.cli._request_json", side_effect=[degraded_snapshot, resume_response, ready_snapshot]) as request_json:
            with patch("sys.stdout", stdout):
                exit_code = main(
                    [
                        "live-agent",
                        "ensure-session",
                        "--server",
                        "http://room.local",
                        "--meeting-id",
                        "resident-m1",
                        "--group-id",
                        "resident-main",
                        "--live-agent-config",
                        "configs/live-agents.example.json",
                        "--connect-timeout",
                        "3",
                        "--wait-timeout",
                        "3",
                    ]
                )

        self.assertEqual(exit_code, 0)
        self.assertEqual(request_json.call_count, 3)
        request_json.assert_any_call(
            "http://room.local/api/live-agent-sessions/resume",
            method="POST",
            payload={
                "meeting_id": "resident-m1",
                "group_id": "resident-main",
                "live_agent_config_path": "configs/live-agents.example.json",
                "connect_timeout_seconds": 3.0,
                "auto_restart": False,
                "max_restarts": 0,
                "restart_backoff_seconds": 5.0,
                "stale_restart_after_seconds": 0.0,
            },
            timeout_seconds=9.0,
        )
        self.assertEqual(
            request_json.call_args_list[2].args,
            (
                "http://room.local/api/live-agent-sessions/readiness?"
                "meeting_id=resident-m1&group_id=resident-main",
            ),
        )
        self.assertIn("Ensured via resume", stdout.getvalue())
        self.assertIn("Resident session resident-m1 ready", stdout.getvalue())

    def test_live_agent_ensure_session_preserves_probe_and_round_results_after_readiness_wait(self):
        degraded_snapshot = {
            "status": "degraded",
            "meeting_id": "resident-m1",
            "group_id": "resident-main",
            "process": {"status": "running", "attention": []},
            "connection": {"expected": 2, "connected": 1, "attention": ["agent-b:offline"]},
        }
        resume_response = {
            "status": "ready",
            "meeting_id": "resident-m1",
            "group_id": "resident-main",
            "process": {"status": "running", "attention": []},
            "connection": {"expected": 2, "connected": 2, "attention": []},
            "reply_probe": {"status": "ok", "agent_count": 2},
            "auto_rounds": {"status": "answered", "round_count": 1},
        }
        ready_snapshot = {
            "status": "ready",
            "meeting_id": "resident-m1",
            "group_id": "resident-main",
            "process": {"status": "running", "attention": []},
            "connection": {"expected": 2, "connected": 2, "attention": []},
        }
        stdout = StringIO()
        with patch("agentsassemble.cli._request_json", side_effect=[degraded_snapshot, resume_response, ready_snapshot]) as request_json:
            with patch("sys.stdout", stdout):
                exit_code = main(
                    [
                        "live-agent",
                        "ensure-session",
                        "--server",
                        "http://room.local",
                        "--meeting-id",
                        "resident-m1",
                        "--group-id",
                        "resident-main",
                        "--live-agent-config",
                        "configs/live-agents.example.json",
                        "--probe-bound-agents",
                        "--run-remaining-rounds",
                        "--round-timeout",
                        "2",
                        "--max-rounds",
                        "1",
                    ]
                )

        self.assertEqual(exit_code, 0)
        expected_timeout = (
            5.0
            + cli_module._operation_http_timeout(12.0, windows=cli_module.SESSION_BOUND_PROBE_HTTP_WINDOWS)
            + cli_module._operation_http_timeout(2.0, windows=cli_module.MAX_LIVE_AGENT_SEQUENCE_TURNS)
        )
        request_json.assert_any_call(
            "http://room.local/api/live-agent-sessions/resume",
            method="POST",
            payload={
                "meeting_id": "resident-m1",
                "group_id": "resident-main",
                "live_agent_config_path": "configs/live-agents.example.json",
                "connect_timeout_seconds": 5.0,
                "auto_restart": False,
                "max_restarts": 0,
                "restart_backoff_seconds": 5.0,
                "stale_restart_after_seconds": 0.0,
                "probe_bound_agents": True,
                "probe_timeout_seconds": 12.0,
                "run_remaining_rounds": True,
                "round_timeout_seconds": 2.0,
                "round_max_rounds": 1,
                "round_stop_on_timeout": False,
            },
            timeout_seconds=expected_timeout,
        )
        self.assertIn("Ensured via resume", stdout.getvalue())
        self.assertIn("Resident session resident-m1 ready", stdout.getvalue())

    def test_live_agent_ensure_session_recovers_error_session(self):
        degraded_snapshot = {
            "status": "degraded",
            "meeting_id": "resident-m1",
            "group_id": "resident-main",
            "group": {"group_id": "resident-main", "status": "error"},
            "process": {"status": "error", "attention": ["group:error"]},
            "connection": {"expected": 2, "connected": 0, "attention": ["agent-a:offline", "agent-b:offline"]},
        }
        recover_response = {
            "status": "ready",
            "meeting_id": "resident-m1",
            "group_id": "resident-main",
            "process": {"status": "running", "attention": []},
            "connection": {"expected": 2, "connected": 2, "attention": []},
        }
        ready_snapshot = dict(recover_response)
        stdout = StringIO()
        with patch("agentsassemble.cli._request_json", side_effect=[degraded_snapshot, recover_response, ready_snapshot]) as request_json:
            with patch("sys.stdout", stdout):
                exit_code = main(
                    [
                        "live-agent",
                        "ensure-session",
                        "--server",
                        "http://room.local",
                        "--meeting-id",
                        "resident-m1",
                        "--group-id",
                        "resident-main",
                        "--live-agent-config",
                        "configs/live-agents.example.json",
                    ]
                )

        self.assertEqual(exit_code, 0)
        request_json.assert_any_call(
            "http://room.local/api/live-agent-sessions/recover",
            method="POST",
            payload={
                "meeting_id": "resident-m1",
                "group_id": "resident-main",
                "connect_timeout_seconds": 5.0,
            },
            timeout_seconds=11.0,
        )
        self.assertIn("Ensured via recover", stdout.getvalue())

    def test_live_agent_ensure_session_restarts_stopped_session(self):
        degraded_snapshot = {
            "status": "degraded",
            "meeting_id": "resident-m1",
            "group_id": "resident-main",
            "group": {"group_id": "resident-main", "status": "stopped"},
            "process": {"status": "stopped", "attention": ["group:stopped"]},
            "connection": {"expected": 2, "connected": 0, "attention": ["agent-a:offline", "agent-b:offline"]},
        }
        restart_response = {
            "status": "ready",
            "meeting_id": "resident-m1",
            "group_id": "resident-main",
            "process": {"status": "running", "attention": []},
            "connection": {"expected": 2, "connected": 2, "attention": []},
        }
        ready_snapshot = dict(restart_response)
        stdout = StringIO()
        with patch("agentsassemble.cli._request_json", side_effect=[degraded_snapshot, restart_response, ready_snapshot]) as request_json:
            with patch("sys.stdout", stdout):
                exit_code = main(
                    [
                        "live-agent",
                        "ensure-session",
                        "--server",
                        "http://room.local",
                        "--meeting-id",
                        "resident-m1",
                        "--group-id",
                        "resident-main",
                        "--live-agent-config",
                        "configs/live-agents.example.json",
                    ]
                )

        self.assertEqual(exit_code, 0)
        request_json.assert_any_call(
            "http://room.local/api/live-agent-sessions/restart",
            method="POST",
            payload={
                "meeting_id": "resident-m1",
                "group_id": "resident-main",
                "connect_timeout_seconds": 5.0,
            },
            timeout_seconds=11.0,
        )
        self.assertIn("Ensured via restart", stdout.getvalue())

    def test_live_agent_ensure_session_restart_and_recover_carry_post_ready_options(self):
        scenarios = [
            (
                "restart",
                {
                    "status": "degraded",
                    "meeting_id": "resident-m1",
                    "group_id": "resident-main",
                    "group": {"group_id": "resident-main", "status": "stopped"},
                    "process": {"status": "stopped", "attention": ["group:stopped"]},
                    "connection": {"expected": 2, "connected": 0, "attention": ["agent-a:offline", "agent-b:offline"]},
                },
            ),
            (
                "recover",
                {
                    "status": "degraded",
                    "meeting_id": "resident-m1",
                    "group_id": "resident-main",
                    "group": {"group_id": "resident-main", "status": "error"},
                    "process": {"status": "error", "attention": ["group:error"]},
                    "connection": {"expected": 2, "connected": 0, "attention": ["agent-a:offline", "agent-b:offline"]},
                },
            ),
        ]
        expected_timeout = (
            5.0
            + cli_module._operation_http_timeout(0.5, windows=cli_module.SESSION_BOUND_PROBE_HTTP_WINDOWS)
            + cli_module._operation_http_timeout(2.0, windows=cli_module.MAX_LIVE_AGENT_SEQUENCE_TURNS)
        )
        for action, degraded_snapshot in scenarios:
            with self.subTest(action=action):
                action_response = {
                    "status": "ready",
                    "meeting_id": "resident-m1",
                    "group_id": "resident-main",
                    "process": {"status": "running", "attention": []},
                    "connection": {"expected": 2, "connected": 2, "attention": []},
                    "reply_probe": {"status": "ok", "agent_count": 2},
                    "auto_rounds": {"status": "answered", "round_count": 1},
                }
                ready_snapshot = {
                    "status": "ready",
                    "meeting_id": "resident-m1",
                    "group_id": "resident-main",
                    "process": {"status": "running", "attention": []},
                    "connection": {"expected": 2, "connected": 2, "attention": []},
                }
                stdout = StringIO()
                with patch(
                    "agentsassemble.cli._request_json",
                    side_effect=[degraded_snapshot, action_response, ready_snapshot],
                ) as request_json:
                    with patch("sys.stdout", stdout):
                        exit_code = main(
                            [
                                "live-agent",
                                "ensure-session",
                                "--server",
                                "http://room.local",
                                "--meeting-id",
                                "resident-m1",
                                "--group-id",
                                "resident-main",
                                "--live-agent-config",
                                "configs/live-agents.example.json",
                                "--probe-bound-agents",
                                "--probe-timeout",
                                "0.5",
                                "--run-remaining-rounds",
                                "--round-timeout",
                                "2",
                                "--max-rounds",
                                "1",
                            ]
                        )

                self.assertEqual(exit_code, 0)
                request_json.assert_any_call(
                    f"http://room.local/api/live-agent-sessions/{action}",
                    method="POST",
                    payload={
                        "meeting_id": "resident-m1",
                        "group_id": "resident-main",
                        "connect_timeout_seconds": 5.0,
                        "probe_bound_agents": True,
                        "probe_timeout_seconds": 0.5,
                        "run_remaining_rounds": True,
                        "round_timeout_seconds": 2.0,
                        "round_max_rounds": 1,
                        "round_stop_on_timeout": False,
                    },
                    timeout_seconds=expected_timeout,
                )
                self.assertIn(f"Ensured via {action}", stdout.getvalue())

    def test_live_agent_ensure_session_uses_final_readiness_even_when_resume_returns_ready(self):
        degraded_snapshot = {
            "status": "degraded",
            "meeting_id": "resident-m1",
            "group_id": "resident-main",
            "process": {"status": "running", "attention": []},
            "connection": {"expected": 2, "connected": 2, "attention": []},
            "ownership": {"attention": ["meeting:duplicate_active_group"]},
        }
        resume_response = {
            "status": "ready",
            "meeting_id": "resident-m1",
            "group_id": "resident-main",
            "process": {"status": "running", "attention": []},
            "connection": {"expected": 2, "connected": 2, "attention": []},
        }
        final_snapshot = {
            "status": "degraded",
            "meeting_id": "resident-m1",
            "group_id": "resident-main",
            "process": {"status": "running", "attention": []},
            "connection": {"expected": 2, "connected": 2, "attention": []},
            "ownership": {"attention": ["meeting:duplicate_active_group"]},
        }
        stdout = StringIO()
        with patch("agentsassemble.cli._request_json", side_effect=[degraded_snapshot, resume_response, final_snapshot]) as request_json:
            with patch("agentsassemble.cli.time.monotonic", side_effect=[0.0, 0.0, 1.1, 1.1]):
                with patch("sys.stdout", stdout):
                    exit_code = main(
                        [
                            "live-agent",
                            "ensure-session",
                            "--server",
                            "http://room.local",
                            "--meeting-id",
                            "resident-m1",
                            "--group-id",
                            "resident-main",
                            "--live-agent-config",
                            "configs/live-agents.example.json",
                            "--wait-timeout",
                            "1",
                        ]
                    )

        self.assertEqual(exit_code, 1)
        self.assertEqual(request_json.call_count, 3)
        self.assertIn("Ensured via resume", stdout.getvalue())
        self.assertIn("Resident session resident-m1 degraded", stdout.getvalue())

    def test_live_agent_ensure_session_fails_when_final_readiness_times_out_after_ready_post(self):
        degraded_snapshot = {
            "status": "degraded",
            "meeting_id": "resident-m1",
            "group_id": "resident-main",
            "process": {"status": "running", "attention": []},
            "connection": {"expected": 2, "connected": 1, "attention": ["agent-b:offline"]},
        }
        resume_response = {
            "status": "ready",
            "meeting_id": "resident-m1",
            "group_id": "resident-main",
            "process": {"status": "running", "attention": []},
            "connection": {"expected": 2, "connected": 2, "attention": []},
        }
        stdout = StringIO()
        with patch("agentsassemble.cli._request_json", side_effect=[degraded_snapshot, resume_response, TimeoutError()]):
            with patch("sys.stdout", stdout):
                exit_code = main(
                    [
                        "live-agent",
                        "ensure-session",
                        "--server",
                        "http://room.local",
                        "--meeting-id",
                        "resident-m1",
                        "--group-id",
                        "resident-main",
                        "--live-agent-config",
                        "configs/live-agents.example.json",
                    ]
                )

        self.assertEqual(exit_code, 1)
        self.assertIn("Ensured via resume", stdout.getvalue())
        self.assertIn("Resident session resident-m1 starting", stdout.getvalue())

    def test_live_agent_start_session_cli_redacts_config_load_paths_from_errors(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            live_agent_config = root / "live-agents.json"
            private_council_config = root / "private-council.json"
            live_agent_config.write_text(
                json.dumps(
                    {
                        "agents": [
                            {
                                "agent_id": "agent-a",
                                "display_name": "Agent A",
                                "provider_kind": "local_cli",
                                "connection_kind": "local_cli",
                                "command": [sys.executable, "-c", "print('ok')"],
                            }
                        ]
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            root.mkdir(exist_ok=True)
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            stderr = StringIO()
            try:
                with patch("sys.stderr", stderr):
                    exit_code = main(
                        [
                            "live-agent",
                            "start-session",
                            "--server",
                            f"http://127.0.0.1:{server.server_port}",
                            "--meeting-id",
                            "resident-m1",
                            "--council-config",
                            str(private_council_config),
                            "--live-agent-config",
                            str(live_agent_config),
                        ]
                    )
            finally:
                server.shutdown()
                server.server_close()

        self.assertEqual(exit_code, 2)
        self.assertNotIn(str(private_council_config), stderr.getvalue())
        self.assertNotIn("private-council", stderr.getvalue())
        self.assertIn("details redacted", stderr.getvalue())

    def test_live_agent_call_sequence_posts_turns_and_prints_summary(self):
        response = {
            "status": "answered",
            "answered_count": 2,
            "timeout_count": 0,
            "skipped_count": 0,
            "results": [
                {"agent_id": "agent-a", "status": "answered", "reply_event": {"id": "reply-a"}},
                {"agent_id": "agent-b", "status": "answered", "reply_event": {"id": "reply-b"}},
            ],
        }
        stdout = StringIO()
        turns_json = '[{"agent_id":"agent-a","content":"A"},{"agent_id":"agent-b","content":"B"}]'
        with patch("agentsassemble.cli._request_json", return_value=response) as request_json:
            with patch("sys.stdout", stdout):
                exit_code = main(
                    [
                        "live-agent",
                        "call-sequence",
                        "--server",
                        "http://room.local",
                        "--meeting-id",
                        "m1",
                        "--turns-json",
                        turns_json,
                        "--timeout",
                        "8",
                    ]
                )

        self.assertEqual(exit_code, 0)
        request_json.assert_called_once_with(
            "http://room.local/api/meetings/m1/live-agent-turns/sequence",
            method="POST",
            payload={
                "turns": [{"agent_id": "agent-a", "content": "A"}, {"agent_id": "agent-b", "content": "B"}],
                "timeout_seconds": 8.0,
                "stop_on_timeout": False,
            },
            timeout_seconds=22.0,
        )
        self.assertIn("Official turn sequence answered: 2 answered, 0 timed out, 0 skipped", stdout.getvalue())
        self.assertIn("- agent-a: answered reply-a", stdout.getvalue())

    def test_live_agent_call_sequence_reads_turns_file(self):
        response = {"status": "answered", "answered_count": 1, "timeout_count": 0, "skipped_count": 0, "results": []}
        with tempfile.TemporaryDirectory() as temp_dir:
            turns_path = Path(temp_dir) / "turns.json"
            turns_path.write_text('[{"agent_id":"agent-a","content":"A"}]\n', encoding="utf-8")
            with patch("agentsassemble.cli._request_json", return_value=response) as request_json:
                exit_code = main(
                    [
                        "live-agent",
                        "call-sequence",
                        "--server",
                        "http://room.local",
                        "--meeting-id",
                        "m1",
                        "--turns-file",
                        str(turns_path),
                        "--timeout",
                        "8",
                    ]
                )

        self.assertEqual(exit_code, 0)
        self.assertEqual(
            request_json.call_args.kwargs["payload"]["turns"],
            [{"agent_id": "agent-a", "content": "A"}],
        )

    def test_live_agent_call_sequence_returns_one_when_partial(self):
        response = {
            "status": "timeout",
            "answered_count": 1,
            "timeout_count": 1,
            "skipped_count": 0,
            "results": [
                {"agent_id": "agent-a", "status": "answered", "reply_event": {"id": "reply-a"}},
                {"agent_id": "agent-b", "status": "timeout", "request_event": {"id": "request-b"}, "reply_event": None},
            ],
        }
        stdout = StringIO()
        turns_json = '[{"agent_id":"agent-a","content":"A"},{"agent_id":"agent-b","content":"B"}]'
        with patch("agentsassemble.cli._request_json", return_value=response):
            with patch("sys.stdout", stdout):
                exit_code = main(
                    [
                        "live-agent",
                        "call-sequence",
                        "--server",
                        "http://room.local",
                        "--meeting-id",
                        "m1",
                        "--turns-json",
                        turns_json,
                        "--timeout",
                        "8",
                    ]
                )

        self.assertEqual(exit_code, 1)
        self.assertIn("Official turn sequence timeout: 1 answered, 1 timed out, 0 skipped", stdout.getvalue())
        self.assertIn("- agent-b: timeout request-b", stdout.getvalue())

    def test_live_agent_preflight_parses_operator_options(self):
        args = build_parser().parse_args(
            [
                "live-agent",
                "preflight",
                "--config",
                "configs/live-agents.example.json",
                "--server",
                "http://room.local",
                "--json",
            ]
        )

        self.assertEqual(args.live_agent_command, "preflight")
        self.assertEqual(args.config, "configs/live-agents.example.json")
        self.assertEqual(args.server, "http://room.local")
        self.assertTrue(args.as_json)

    def test_live_agent_preflight_prints_summary_and_exits_nonzero_when_failed(self):
        report = {
            "status": "failed",
            "summary": {"agents": 2, "failed_agents": 1, "checks_failed": 1},
            "agents": [
                {"agent_id": "ok-agent", "status": "ok", "checks": []},
                {
                    "agent_id": "bad-agent",
                    "status": "failed",
                    "checks": [{"id": "command", "status": "failed", "message": "Command not found: missing"}],
                },
            ],
        }
        stdout = StringIO()
        with patch("agentsassemble.cli.preflight_live_agent_config", return_value=report) as preflight:
            with patch("sys.stdout", stdout):
                exit_code = main(
                    [
                        "live-agent",
                        "preflight",
                        "--config",
                        "configs/live-agents.example.json",
                        "--server",
                        "http://room.local",
                    ]
                )

        self.assertEqual(exit_code, 1)
        preflight.assert_called_once_with(Path("configs/live-agents.example.json"), server_override="http://room.local")
        output = stdout.getvalue()
        self.assertIn("preflight: failed", output)
        self.assertIn("agents: 2 checked, 1 failed", output)
        self.assertIn("bad-agent: command: Command not found: missing", output)

    def test_live_agent_preflight_does_not_override_config_server_by_default(self):
        report = {
            "status": "ok",
            "summary": {"agents": 1, "failed_agents": 0, "checks_failed": 0},
            "agents": [],
        }
        with patch("agentsassemble.cli.preflight_live_agent_config", return_value=report) as preflight:
            with patch("sys.stdout", StringIO()):
                exit_code = main(["live-agent", "preflight", "--config", "configs/live-agents.example.json"])

        self.assertEqual(exit_code, 0)
        preflight.assert_called_once_with(Path("configs/live-agents.example.json"), server_override=None)

    def test_providers_health_parses_operator_options(self):
        args = build_parser().parse_args(
            [
                "providers",
                "health",
                "--config",
                "configs/http-providers.example.json",
                "--probe",
                "bridge",
                "--probe-timeout",
                "0.75",
                "--json",
            ]
        )

        self.assertEqual(args.command, "providers")
        self.assertEqual(args.providers_command, "health")
        self.assertEqual(args.config, "configs/http-providers.example.json")
        self.assertEqual(args.probe_mode, "bridge")
        self.assertEqual(args.probe_timeout, 0.75)
        self.assertTrue(args.as_json)

    def test_providers_health_passes_probe_options_to_reporter(self):
        report = {
            "status": "ok",
            "summary": {
                "providers": 1,
                "failed_providers": 0,
                "bindings": 0,
                "failed_bindings": 0,
                "checks_failed": 0,
                "warnings": 0,
            },
            "providers": [],
            "bindings": [],
        }
        stdout = StringIO()
        with patch("agentsassemble.cli.provider_health_report", return_value=report) as provider_health:
            with patch("sys.stdout", stdout):
                exit_code = main(
                    [
                        "providers",
                        "health",
                        "--config",
                        "configs/http-providers.example.json",
                        "--probe",
                        "local",
                        "--probe-timeout",
                        "0.75",
                    ]
                )

        self.assertEqual(exit_code, 0)
        provider_health.assert_called_once_with(
            Path("configs/http-providers.example.json"),
            probe_mode="local",
            probe_timeout_seconds=0.75,
        )

    def test_providers_health_prints_summary_and_exits_nonzero_when_failed(self):
        report = {
            "status": "failed",
            "summary": {
                "providers": 2,
                "failed_providers": 1,
                "bindings": 1,
                "failed_bindings": 1,
                "checks_failed": 2,
                "warnings": 0,
            },
            "providers": [
                {
                    "provider_id": "bad-provider",
                    "kind": "anthropic",
                    "status": "failed",
                    "checks": [{"id": "auth_ref", "status": "failed", "message": "Required auth_ref is not available."}],
                }
            ],
            "bindings": [
                {
                    "agent_id": "bad-agent",
                    "status": "failed",
                    "checks": [{"id": "provider_ready", "status": "failed", "message": "Provider bad-provider is not ready."}],
                }
            ],
        }
        stdout = StringIO()
        with patch("agentsassemble.cli.provider_health_report", return_value=report) as provider_health:
            with patch("sys.stdout", stdout):
                exit_code = main(["providers", "health", "--config", "configs/http-providers.example.json"])

        self.assertEqual(exit_code, 1)
        provider_health.assert_called_once_with(
            Path("configs/http-providers.example.json"),
            probe_mode="none",
            probe_timeout_seconds=2.0,
        )
        output = stdout.getvalue()
        self.assertIn("provider health: failed", output)
        self.assertIn("providers: 2 checked, 1 failed", output)
        self.assertIn("bad-provider: auth_ref: Required auth_ref is not available.", output)
        self.assertIn("bad-agent: provider_ready: Provider bad-provider is not ready.", output)

    def test_live_agent_doctor_posts_readiness_request_and_prints_summary(self):
        payload = {
            "status": "ready",
            "checks": [{"id": "health", "status": "ok"}, {"id": "smoke", "status": "ok"}],
            "health": {
                "status": "ok",
                "agents": {"attention": []},
                "processes": {
                    "attention": [],
                    "reasons": {
                        "restart-group": {
                            "event_type": "stale_watchdog",
                            "reason": "missing manifest agent agent-a",
                        }
                    },
                },
                "connections": {
                    "expected": 2,
                    "connected": 1,
                    "attention": ["resident-main:agent-b:missing"],
                },
                "sessions": {
                    "total": 2,
                    "ready": 0,
                    "attention": ["resident-m1:resident-main:meeting:duplicate_active_group"],
                },
            },
            "smoke": {"status": "ok", "group_id": "doctor-smoke", "replies": []},
            "probes": [{"status": "ok", "agent_id": "agent-a", "reply_event_id": "reply-a"}],
        }
        stdout = StringIO()
        with patch("agentsassemble.cli._request_json", return_value=payload) as request_json:
            with patch("sys.stdout", stdout):
                exit_code = main(
                    [
                        "live-agent",
                        "doctor",
                        "--server",
                        "http://room.local",
                        "--group-id",
                        "doctor-smoke",
                        "--timeout",
                        "8",
                        "--probe-agent",
                        "agent-a",
                    ]
                )

        self.assertEqual(exit_code, 0)
        request_json.assert_called_once_with(
            "http://room.local/api/live-agent-readiness",
            method="POST",
            payload={"group_id": "doctor-smoke", "timeout": 8.0, "probe_agent_ids": ["agent-a"]},
            timeout_seconds=22.0,
        )
        output = stdout.getvalue()
        self.assertIn("readiness: ready", output)
        self.assertIn("health: ok", output)
        self.assertIn("smoke: ok doctor-smoke", output)
        self.assertIn("process reasons: restart-group stale_watchdog missing manifest agent agent-a", output)
        self.assertIn("connection attention: resident-main:agent-b:missing", output)
        self.assertIn("session attention: resident-m1:resident-main:meeting:duplicate_active_group", output)
        self.assertIn("probes: agent-a ok", output)

    def test_live_agent_doctor_posts_probe_group_request_with_conservative_timeout(self):
        payload = {
            "status": "ready",
            "checks": [{"id": "health", "status": "ok"}, {"id": "smoke", "status": "ok"}],
            "health": {"status": "ok", "agents": {"attention": []}, "processes": {"attention": []}},
            "smoke": {"status": "ok", "group_id": "doctor-smoke", "replies": []},
            "probe_groups": [{"status": "ok", "group_id": "resident-main", "agent_ids": ["agent-a", "agent-b"]}],
            "probes": [
                {"status": "ok", "agent_id": "agent-a", "reply_event_id": "reply-a"},
                {"status": "ok", "agent_id": "agent-b", "reply_event_id": "reply-b"},
            ],
        }
        stdout = StringIO()
        with patch("agentsassemble.cli._request_json", return_value=payload) as request_json:
            with patch("sys.stdout", stdout):
                exit_code = main(
                    [
                        "live-agent",
                        "doctor",
                        "--server",
                        "http://room.local",
                        "--group-id",
                        "doctor-smoke",
                        "--timeout",
                        "8",
                        "--probe-group",
                        "resident-main",
                    ]
                )

        self.assertEqual(exit_code, 0)
        request_json.assert_called_once_with(
            "http://room.local/api/live-agent-readiness",
            method="POST",
            payload={"group_id": "doctor-smoke", "timeout": 8.0, "probe_group_ids": ["resident-main"]},
            timeout_seconds=94.0,
        )
        self.assertIn("probes: agent-a ok, agent-b ok", stdout.getvalue())

    def test_live_agent_doctor_can_request_official_round_smoke(self):
        payload = {
            "status": "ready",
            "checks": [
                {"id": "health", "status": "ok"},
                {"id": "smoke", "status": "ok"},
                {"id": "official_round_smoke", "status": "ok"},
            ],
            "health": {"status": "ok", "agents": {"attention": []}, "processes": {"attention": []}},
            "smoke": {"status": "ok", "group_id": "doctor-smoke"},
            "official_round_smoke": {
                "status": "ok",
                "group_id": "doctor-smoke",
                "answered_count": 3,
                "timeout_count": 0,
                "skipped_count": 0,
            },
        }
        stdout = StringIO()
        with patch("agentsassemble.cli._request_json", return_value=payload) as request_json:
            with patch("sys.stdout", stdout):
                exit_code = main(
                    [
                        "live-agent",
                        "doctor",
                        "--server",
                        "http://room.local",
                        "--group-id",
                        "doctor-smoke",
                        "--timeout",
                        "8",
                        "--official-round-smoke",
                    ]
                )

        self.assertEqual(exit_code, 0)
        request_json.assert_called_once_with(
            "http://room.local/api/live-agent-readiness",
            method="POST",
            payload={"group_id": "doctor-smoke", "timeout": 8.0, "official_round_smoke": True},
            timeout_seconds=46.0,
        )
        output = stdout.getvalue()
        self.assertIn("readiness: ready", output)
        self.assertIn("official round smoke: ok doctor-smoke (3 answered, 0 timed out, 0 skipped)", output)

    def test_live_agent_doctor_can_request_session_smoke(self):
        payload = {
            "status": "ready",
            "checks": [
                {"id": "health", "status": "ok"},
                {"id": "smoke", "status": "ok"},
                {"id": "session_smoke", "status": "ok"},
            ],
            "health": {"status": "ok", "agents": {"attention": []}, "processes": {"attention": []}},
            "smoke": {"status": "ok", "group_id": "doctor-smoke"},
            "session_smoke": {
                "status": "ok",
                "group_id": "session-smoke",
                "meeting_id": "session-smoke",
                "expected_reply_count": 3,
                "lobby_probe_count": 1,
                "reply_count": 3,
                "post_restart_reply_count": 3,
                "post_recover_reply_count": 3,
                "soak_cycle_count": 2,
                "soak_interval_seconds": 0.5,
                "soak_reply_count": 6,
                "recover_status": "ready",
                "post_stop_process_status": "stopped",
            },
        }
        stdout = StringIO()
        with patch("agentsassemble.cli._request_json", return_value=payload) as request_json:
            with patch("sys.stdout", stdout):
                exit_code = main(
                    [
                        "live-agent",
                        "doctor",
                        "--server",
                        "http://room.local",
                        "--group-id",
                        "doctor-smoke",
                        "--timeout",
                        "8",
                        "--session-smoke",
                        "--session-smoke-soak-cycles",
                        "2",
                        "--session-smoke-soak-interval",
                        "0.5",
                    ]
                )

        self.assertEqual(exit_code, 0)
        request_json.assert_called_once_with(
            "http://room.local/api/live-agent-readiness",
            method="POST",
            payload={
                "group_id": "doctor-smoke",
                "timeout": 8.0,
                "session_smoke": True,
                "session_smoke_soak_cycle_count": 2,
                "session_smoke_soak_interval_seconds": 0.5,
            },
            timeout_seconds=207.0,
        )
        output = stdout.getvalue()
        self.assertIn("readiness: ready", output)
        self.assertIn(
            "session smoke: ok session-smoke "
            "(3/3 replies, post-restart 3/3, post-recover 3/3, soak 6/6 over 2 cycles, post-stop stopped)",
            output,
        )

    def test_live_agent_doctor_can_request_official_round_and_session_smoke(self):
        payload = {
            "status": "ready",
            "checks": [
                {"id": "health", "status": "ok"},
                {"id": "smoke", "status": "ok"},
                {"id": "official_round_smoke", "status": "ok"},
                {"id": "session_smoke", "status": "ok"},
            ],
            "health": {"status": "ok", "agents": {"attention": []}, "processes": {"attention": []}},
            "smoke": {"status": "ok", "group_id": "doctor-smoke"},
            "official_round_smoke": {
                "status": "ok",
                "group_id": "doctor-smoke",
                "answered_count": 3,
                "timeout_count": 0,
                "skipped_count": 0,
            },
            "session_smoke": {
                "status": "ok",
                "group_id": "session-smoke",
                "meeting_id": "session-smoke",
                "expected_reply_count": 3,
                "lobby_probe_count": 1,
                "reply_count": 3,
                "post_restart_reply_count": 3,
                "post_recover_reply_count": 3,
                "recover_status": "ready",
                "post_stop_process_status": "stopped",
            },
        }
        stdout = StringIO()
        with patch("agentsassemble.cli._request_json", return_value=payload) as request_json:
            with patch("sys.stdout", stdout):
                exit_code = main(
                    [
                        "live-agent",
                        "doctor",
                        "--server",
                        "http://room.local",
                        "--group-id",
                        "doctor-smoke",
                        "--timeout",
                        "8",
                        "--official-round-smoke",
                        "--session-smoke",
                    ]
                )

        self.assertEqual(exit_code, 0)
        request_json.assert_called_once_with(
            "http://room.local/api/live-agent-readiness",
            method="POST",
            payload={
                "group_id": "doctor-smoke",
                "timeout": 8.0,
                "official_round_smoke": True,
                "session_smoke": True,
            },
            timeout_seconds=202.0,
        )
        output = stdout.getvalue()
        self.assertIn("official round smoke: ok doctor-smoke (3 answered, 0 timed out, 0 skipped)", output)
        self.assertIn(
            "session smoke: ok session-smoke (3/3 replies, post-restart 3/3, post-recover 3/3, post-stop stopped)",
            output,
        )

    def test_live_agent_doctor_prints_probe_group_refusal_reason(self):
        payload = {
            "status": "failed",
            "checks": [
                {"id": "health", "status": "degraded"},
                {"id": "smoke", "status": "ok"},
                {"id": "probe_group:stopped-group", "status": "failed"},
            ],
            "health": {"status": "degraded", "agents": {"attention": []}, "processes": {"attention": ["stopped-group"]}},
            "smoke": {"status": "ok", "group_id": "doctor-smoke"},
            "probe_groups": [{"status": "failed", "group_id": "stopped-group", "reason": "group is not running"}],
        }
        stdout = StringIO()
        with patch("agentsassemble.cli._request_json", return_value=payload):
            with patch("sys.stdout", stdout):
                exit_code = main(["live-agent", "doctor", "--server", "http://room.local"])

        self.assertEqual(exit_code, 1)
        self.assertIn("probe groups: stopped-group failed (group is not running)", stdout.getvalue())

    def test_live_agent_smoke_uses_http_timeout_longer_than_smoke_window(self):
        payload = {"status": "ok", "group_id": "operator-smoke", "replies": []}
        stdout = StringIO()
        with patch("agentsassemble.cli._request_json", return_value=payload) as request_json:
            with patch("sys.stdout", stdout):
                exit_code = main(
                    [
                        "live-agent",
                        "smoke",
                        "--server",
                        "http://room.local",
                        "--group-id",
                        "operator-smoke",
                        "--timeout",
                        "12",
                    ]
                )

        self.assertEqual(exit_code, 0)
        request_json.assert_called_once_with(
            "http://room.local/api/live-agent-smoke",
            method="POST",
            payload={"group_id": "operator-smoke", "timeout": 12.0},
            timeout_seconds=18.0,
        )

    def test_live_agent_official_round_smoke_posts_endpoint_and_prints_summary(self):
        payload = {
            "status": "ok",
            "group_id": "round-smoke",
            "meeting_id": "official-round-smoke-round-smoke",
            "round_id": "official_round_smoke",
            "turn_count": 3,
            "answered_count": 3,
            "timeout_count": 0,
            "skipped_count": 0,
        }
        stdout = StringIO()
        with patch("agentsassemble.cli._request_json", return_value=payload) as request_json:
            with patch("sys.stdout", stdout):
                exit_code = main(
                    [
                        "live-agent",
                        "official-round-smoke",
                        "--server",
                        "http://room.local",
                        "--group-id",
                        "round-smoke",
                        "--timeout",
                        "8",
                    ]
                )

        self.assertEqual(exit_code, 0)
        request_json.assert_called_once_with(
            "http://room.local/api/live-agent-official-round-smoke",
            method="POST",
            payload={"group_id": "round-smoke", "timeout": 8.0},
            timeout_seconds=38.0,
        )
        self.assertIn("official round smoke ok: round-smoke", stdout.getvalue())
        self.assertIn("3 answered, 0 timed out, 0 skipped", stdout.getvalue())

    def test_live_agent_session_smoke_posts_endpoint_and_prints_summary(self):
        payload = {
            "status": "ok",
            "meeting_id": "session-smoke-meeting",
            "group_id": "session-smoke",
            "rounds_status": "answered",
            "answered_round_count": 1,
            "expected_reply_count": 3,
            "lobby_probe_count": 2,
            "reply_count": 6,
            "post_restart_reply_count": 6,
            "post_recover_reply_count": 6,
            "soak_cycle_count": 2,
            "soak_interval_seconds": 0.5,
            "soak_reply_count": 6,
            "start_status": "ready",
            "check_status": "ready",
            "resume_status": "ready",
            "restart_status": "ready",
            "recover_status": "ready",
            "stop_status": "stopped",
            "post_stop_process_status": "stopped",
        }
        stdout = StringIO()
        with patch("agentsassemble.cli._request_json", return_value=payload) as request_json:
            with patch("sys.stdout", stdout):
                exit_code = main(
                    [
                        "live-agent",
                        "session-smoke",
                        "--server",
                        "http://room.local",
                        "--group-id",
                        "session-smoke",
                        "--meeting-id",
                        "session-smoke-meeting",
                        "--timeout",
                        "8",
                        "--lobby-probes",
                        "2",
                        "--soak-cycles",
                        "2",
                        "--soak-interval",
                        "0.5",
                    ]
                )

        self.assertEqual(exit_code, 0)
        request_json.assert_called_once_with(
            "http://room.local/api/live-agent-session-smoke",
            method="POST",
            payload={
                "group_id": "session-smoke",
                "meeting_id": "session-smoke-meeting",
                "timeout": 8.0,
                "lobby_probe_count": 2,
                "soak_cycle_count": 2,
                "soak_interval_seconds": 0.5,
            },
            timeout_seconds=217.0,
        )
        output = stdout.getvalue()
        self.assertIn("resident session smoke ok: session-smoke-meeting", output)
        self.assertIn("rounds answered (1 answered)", output)
        self.assertIn("2 lobby probes", output)
        self.assertIn("6/6 replies", output)
        self.assertIn("post-restart 6/6 replies", output)
        self.assertIn("post-recover 6/6 replies", output)
        self.assertIn("soak 6/6 replies over 2 cycles", output)
        self.assertIn("post-stop stopped", output)
        self.assertIn("start ready, check ready, resume ready, restart ready, recover ready, stop stopped", output)

    def test_live_agent_session_smoke_returns_failure_for_non_ok_status(self):
        payload = {
            "status": "failed",
            "meeting_id": "session-smoke-meeting",
            "group_id": "session-smoke",
            "rounds_status": "answered",
            "answered_round_count": 1,
            "expected_reply_count": 3,
            "reply_count": 1,
            "post_restart_reply_count": 0,
            "post_recover_reply_count": 0,
            "start_status": "ready",
            "check_status": "ready",
            "resume_status": "",
            "restart_status": "",
            "recover_status": "",
            "stop_status": "stopped",
        }
        stdout = StringIO()
        with patch("agentsassemble.cli._request_json", return_value=payload):
            with patch("sys.stdout", stdout):
                exit_code = main(["live-agent", "session-smoke", "--server", "http://room.local"])

        self.assertEqual(exit_code, 1)

    def test_live_agent_doctor_json_exits_one_when_not_ready(self):
        payload = {
            "status": "degraded",
            "checks": [{"id": "health", "status": "degraded"}, {"id": "smoke", "status": "ok"}],
            "health": {
                "status": "degraded",
                "agents": {"attention": ["offline-agent"]},
                "processes": {"attention": []},
            },
            "smoke": {"status": "ok", "group_id": "doctor-smoke", "replies": []},
        }
        stdout = StringIO()
        with patch("agentsassemble.cli._request_json", return_value=payload):
            with patch("sys.stdout", stdout):
                exit_code = main(["live-agent", "doctor", "--server", "http://room.local", "--json"])

        self.assertEqual(exit_code, 1)
        self.assertEqual(json.loads(stdout.getvalue())["status"], "degraded")

    def test_live_agent_probe_parses_operator_options(self):
        args = build_parser().parse_args(
            [
                "live-agent",
                "probe",
                "--server",
                "http://room.local",
                "--agent-id",
                "agent-a",
                "--timeout",
                "3",
                "--json",
            ]
        )

        self.assertEqual(args.live_agent_command, "probe")
        self.assertEqual(args.server, "http://room.local")
        self.assertEqual(args.agent_id, "agent-a")
        self.assertEqual(args.timeout, 3.0)
        self.assertTrue(args.as_json)

    def test_live_agent_probe_posts_request_and_prints_summary(self):
        payload = {
            "status": "ok",
            "agent_id": "agent-a",
            "source_event_id": "probe-source",
            "reply_event_id": "reply-event",
        }
        stdout = StringIO()
        with patch("agentsassemble.cli._request_json", return_value=payload) as request_json:
            with patch("sys.stdout", stdout):
                exit_code = main(["live-agent", "probe", "--server", "http://room.local", "--agent-id", "agent-a", "--timeout", "3"])

        self.assertEqual(exit_code, 0)
        request_json.assert_called_once_with(
            "http://room.local/api/live-agents/agent-a/probe",
            method="POST",
            payload={"timeout_seconds": 3.0},
            timeout_seconds=10.0,
        )
        output = stdout.getvalue()
        self.assertIn("probe: ok", output)
        self.assertIn("agent: agent-a", output)
        self.assertIn("reply: reply-event", output)

    def test_live_agent_probe_uses_http_timeout_beyond_probe_window(self):
        with patch("agentsassemble.cli._request_json", return_value={"status": "timeout"}) as request_json:
            exit_code = main(["live-agent", "probe", "--server", "http://room.local", "--agent-id", "agent-a"])

        self.assertEqual(exit_code, 1)
        self.assertEqual(request_json.call_args.kwargs["timeout_seconds"], 14.0)

    def test_live_agent_probe_json_exits_one_for_timeout(self):
        payload = {"status": "timeout", "agent_id": "agent-a", "source_event_id": "probe-source"}
        stdout = StringIO()
        with patch("agentsassemble.cli._request_json", return_value=payload):
            with patch("sys.stdout", stdout):
                exit_code = main(["live-agent", "probe", "--server", "http://room.local", "--agent-id", "agent-a", "--json"])

        self.assertEqual(exit_code, 1)
        self.assertEqual(json.loads(stdout.getvalue())["status"], "timeout")

    def test_live_agent_smoke_verifies_supervised_fake_local_cli_and_live_session(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "room"
            root.mkdir()
            old_event = append_lobby_event(root, {"name": "나", "side": "mine", "message": "smoke 이전 잡담"})
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                stdout = StringIO()
                with patch("sys.stdout", stdout):
                    exit_code = main(
                        [
                            "live-agent",
                            "smoke",
                            "--server",
                            f"http://127.0.0.1:{server.server_port}",
                            "--group-id",
                            "operator-smoke",
                            "--timeout",
                            "8",
                            "--json",
                        ]
                    )
            finally:
                server.shutdown()
                server.server_close()

            self.assertEqual(exit_code, 0)
            payload = json.loads(stdout.getvalue())
            self.assertEqual(payload["status"], "ok")
            self.assertEqual(payload["group_id"], "operator-smoke")
            self.assertEqual(
                {reply["message"] for reply in payload["replies"]},
                {"smoke local_cli ok", "smoke live_session ok", "smoke remote_bridge ok"},
            )
            self.assertNotEqual(payload["source_event_id"], old_event["id"])
            self.assertEqual({reply["source_event_id"] for reply in payload["replies"]}, {payload["source_event_id"]})
            events = read_lobby(root)
            self.assertEqual(
                {event["message"] for event in events if event.get("actor_id") in payload["agent_ids"]},
                {"smoke local_cli ok", "smoke live_session ok", "smoke remote_bridge ok"},
            )
            processes = json.loads((root / "live-agent-runs" / "processes.json").read_text(encoding="utf-8"))
            group = next(item for item in processes["groups"] if item["group_id"] == "operator-smoke")
            self.assertEqual(group["status"], "stopped")
            operations = json.loads((root / "live-agent-runs" / "operations.jsonl").read_text(encoding="utf-8").splitlines()[-1])
            self.assertEqual(operations["operation"], "smoke.run")
            self.assertEqual(operations["status"], "success")
            self.assertEqual(operations["target_id"], "operator-smoke")

    def test_live_agent_smoke_returns_one_for_reached_server_failure(self):
        stderr = StringIO()
        with patch("agentsassemble.cli._request_json", side_effect=LiveAgentSmokeFailed("missing replies")):
            with patch("sys.stderr", stderr):
                exit_code = main(["live-agent", "smoke", "--server", "http://room.local"])

        self.assertEqual(exit_code, 1)
        self.assertIn("missing replies", stderr.getvalue())

    def test_live_agent_health_reads_real_http_endpoint(self):
        class FakeSupervisor:
            def snapshot_groups(self):
                return [{"group_id": "crashed-group", "status": "error"}]

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "room"
            root.mkdir()
            (root / "live_agents.json").write_text(
                json.dumps({"agents": [{"agent_id": "error-agent", "status": "error"}]}),
                encoding="utf-8",
            )
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root, process_supervisor=FakeSupervisor()))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                stdout = StringIO()
                with patch("sys.stdout", stdout):
                    exit_code = main(
                        [
                            "live-agent",
                            "health",
                            "--server",
                            f"http://127.0.0.1:{server.server_port}",
                            "--fail-on-degraded",
                        ]
                    )
            finally:
                server.shutdown()
                server.server_close()

            self.assertEqual(exit_code, 1)
            output = stdout.getvalue()
            self.assertIn("status: degraded", output)
            self.assertIn("agent attention: error-agent", output)
            self.assertIn("process attention: crashed-group", output)

    def test_live_agent_processes_start_parses_supervisor_options(self):
        args = build_parser().parse_args(
            [
                "live-agent",
                "processes",
                "start",
                "--server",
                "http://room.local",
                "--config",
                "configs/live-agents.example.json",
                "--group-id",
                "crew",
                "--auto-restart",
                "--max-restarts",
                "2",
                "--restart-backoff-seconds",
                "1.5",
                "--stale-restart-after-seconds",
                "240",
                "--json",
            ]
        )

        self.assertEqual(args.live_agent_command, "processes")
        self.assertEqual(args.live_agent_process_command, "start")
        self.assertEqual(args.server, "http://room.local")
        self.assertEqual(args.config, "configs/live-agents.example.json")
        self.assertEqual(args.group_id, "crew")
        self.assertTrue(args.auto_restart)
        self.assertEqual(args.max_restarts, 2)
        self.assertEqual(args.restart_backoff_seconds, 1.5)
        self.assertEqual(args.stale_restart_after_seconds, 240.0)
        self.assertTrue(args.as_json)

    def test_live_agent_processes_recover_parser_accepts_group_id(self):
        args = build_parser().parse_args(
            [
                "live-agent",
                "processes",
                "recover",
                "crew",
                "--server",
                "http://room.local",
                "--json",
            ]
        )

        self.assertEqual(args.live_agent_command, "processes")
        self.assertEqual(args.live_agent_process_command, "recover")
        self.assertEqual(args.group_id, "crew")
        self.assertEqual(args.server, "http://room.local")
        self.assertTrue(args.as_json)

    def test_live_agent_processes_stop_running_parser_accepts_json(self):
        args = build_parser().parse_args(
            [
                "live-agent",
                "processes",
                "stop-running",
                "--server",
                "http://room.local",
                "--json",
            ]
        )

        self.assertEqual(args.live_agent_command, "processes")
        self.assertEqual(args.live_agent_process_command, "stop-running")
        self.assertEqual(args.server, "http://room.local")
        self.assertTrue(args.as_json)

    def test_live_agent_processes_list_prints_summary(self):
        payload = {
            "groups": [
                {
                    "group_id": "crew",
                    "status": "running",
                    "pid": 1234,
                    "auto_restart": True,
                    "restart_count": 1,
                    "max_restarts": 3,
                    "stale_restart_after_seconds": 240,
                    "next_restart_at": "2026-05-17T12:01:00+00:00",
                    "config_path": "configs/live-agents.example.json",
                    "agents": [
                        {
                            "agent_id": "local-a",
                            "display_name": "Local A",
                            "provider_kind": "local_cli",
                            "connection_kind": "local_cli",
                        },
                        {
                            "agent_id": "friend-b",
                            "display_name": "Friend B",
                            "provider_kind": "claude_code",
                            "connection_kind": "remote_bridge",
                        },
                    ],
                    "recent_events": [
                        {
                            "event_type": "stale_watchdog",
                            "timestamp": "2026-05-17T11:59:45+00:00",
                            "group_id": "crew",
                            "status": "running",
                            "restart_count": 0,
                            "reason": "missing manifest agent local-a",
                        },
                        {
                            "event_type": "restart_scheduled",
                            "timestamp": "2026-05-17T11:59:50+00:00",
                            "group_id": "crew",
                            "status": "restarting",
                            "restart_count": 1,
                            "offline": {
                                "expected": 2,
                                "offline": 1,
                                "skipped": 1,
                                "offline_agent_ids": ["local-a"],
                                "attention": [{"agent_id": "friend-b", "status": "wrong_meeting"}],
                            },
                        },
                        {
                            "event_type": "started",
                            "timestamp": "2026-05-17T12:00:00+00:00",
                            "group_id": "crew",
                            "status": "running",
                            "pid": 1234,
                            "restart_count": 1,
                        },
                    ],
                    "agent_connection": {
                        "expected": 2,
                        "connected": 1,
                        "attention": [{"agent_id": "friend-b", "status": "missing"}],
                    },
                },
                {
                    "group_id": "stopped-crew",
                    "status": "stopped",
                    "pid": None,
                    "config_path": "fake.json",
                    "next_restart_at": "",
                },
            ]
        }
        stdout = StringIO()
        with patch("agentsassemble.cli._request_json", return_value=payload) as request_json:
            with patch("sys.stdout", stdout):
                exit_code = main(["live-agent", "processes", "list", "--server", "http://room.local"])

        self.assertEqual(exit_code, 0)
        request_json.assert_called_once_with("http://room.local/api/live-agent-processes")
        output = stdout.getvalue()
        self.assertIn("crew: running", output)
        self.assertIn("pid 1234", output)
        self.assertIn("restarts 1/3", output)
        self.assertIn("stale watchdog 240s", output)
        self.assertIn("next restart 2026-05-17T12:01:00+00:00", output)
        self.assertIn("agents Local A/local_cli, Friend B/remote_bridge", output)
        self.assertIn("agents connected 1/2", output)
        self.assertIn("missing friend-b", output)
        self.assertIn("last event started", output)
        self.assertIn("last offline restart_scheduled", output)
        self.assertIn("last reason stale_watchdog missing manifest agent local-a", output)
        self.assertIn("offline 1/2", output)
        self.assertIn("wrong_meeting friend-b", output)
        self.assertNotIn("command", output)
        self.assertNotIn("auth", output)
        stopped_line = next(line for line in output.splitlines() if line.startswith("stopped-crew:"))
        self.assertIn("stopped-crew: stopped", stopped_line)
        self.assertNotIn("next restart", stopped_line)

    def test_live_agent_processes_list_fail_on_attention_exits_one_after_printing_summary(self):
        payload = {
            "groups": [
                {
                    "group_id": "crew",
                    "status": "running",
                    "pid": 1234,
                    "agent_connection": {
                        "expected": 2,
                        "connected": 1,
                        "attention": [{"agent_id": "agent-b", "status": "missing"}],
                    },
                },
                {
                    "group_id": "crashed-crew",
                    "status": "error",
                    "pid": None,
                },
                {
                    "group_id": "stopped-crew",
                    "status": "stopped",
                    "pid": None,
                },
            ]
        }
        stdout = StringIO()
        with patch("agentsassemble.cli._request_json", return_value=payload) as request_json:
            with patch("sys.stdout", stdout):
                exit_code = main(
                    [
                        "live-agent",
                        "processes",
                        "list",
                        "--server",
                        "http://room.local",
                        "--fail-on-attention",
                    ]
                )

        self.assertEqual(exit_code, 1)
        request_json.assert_called_once_with("http://room.local/api/live-agent-processes")
        output = stdout.getvalue()
        self.assertIn("crew: running", output)
        self.assertIn("agents connected 1/2", output)
        self.assertIn("missing agent-b", output)
        self.assertIn("crashed-crew: error", output)
        self.assertIn("stopped-crew: stopped", output)

    def test_live_agent_processes_wait_polls_until_group_is_ready(self):
        payloads = [
            {"groups": []},
            {
                "groups": [
                    {
                        "group_id": "crew",
                        "status": "running",
                        "pid": 1234,
                        "agent_connection": {
                            "expected": 2,
                            "connected": 1,
                            "attention": [{"agent_id": "agent-b", "status": "missing"}],
                        },
                    }
                ]
            },
            {
                "groups": [
                    {
                        "group_id": "crew",
                        "status": "running",
                        "pid": 1234,
                        "agent_connection": {"expected": 2, "connected": 2, "attention": []},
                    }
                ]
            },
        ]
        stdout = StringIO()
        with patch("agentsassemble.cli._request_json", side_effect=payloads) as request_json:
            with patch("agentsassemble.cli.time.sleep") as sleep:
                with patch("sys.stdout", stdout):
                    exit_code = main(
                        [
                            "live-agent",
                            "processes",
                            "wait",
                            "crew",
                            "--server",
                            "http://room.local",
                            "--timeout",
                            "2",
                            "--poll-interval",
                            "0.1",
                        ]
                    )

        self.assertEqual(exit_code, 0)
        self.assertEqual(request_json.call_count, 3)
        self.assertEqual(request_json.call_args_list[-1].args, ("http://room.local/api/live-agent-processes",))
        self.assertIn("timeout_seconds", request_json.call_args_list[-1].kwargs)
        self.assertEqual(sleep.call_count, 2)
        output = stdout.getvalue()
        self.assertIn("Process group crew ready", output)
        self.assertIn("agents connected 2/2", output)

    def test_live_agent_processes_wait_times_out_with_last_observed_status(self):
        payload = {
            "groups": [
                {
                    "group_id": "crew",
                    "status": "running",
                    "pid": 1234,
                    "agent_connection": {
                        "expected": 2,
                        "connected": 1,
                        "attention": [{"agent_id": "agent-b", "status": "missing"}],
                    },
                }
            ]
        }
        stdout = StringIO()
        with patch("agentsassemble.cli._request_json", return_value=payload) as request_json:
            with patch("agentsassemble.cli.time.monotonic", side_effect=[0.0, 0.0, 0.9, 1.1]):
                with patch("agentsassemble.cli.time.sleep") as sleep:
                    with patch("sys.stdout", stdout):
                        exit_code = main(
                            [
                                "live-agent",
                                "processes",
                                "wait",
                                "crew",
                                "--server",
                                "http://room.local",
                                "--timeout",
                                "1",
                                "--poll-interval",
                                "0.1",
                            ]
                        )

        self.assertEqual(exit_code, 1)
        self.assertEqual(request_json.call_count, 1)
        self.assertEqual(sleep.call_count, 1)
        self.assertAlmostEqual(sleep.call_args.args[0], 0.1)
        output = stdout.getvalue()
        self.assertIn("Process group crew not ready after 1.0s", output)
        self.assertIn("agents connected 1/2", output)
        self.assertIn("missing agent-b", output)

    def test_live_agent_processes_wait_bounds_each_poll_request_to_remaining_timeout(self):
        payload = {
            "groups": [
                {
                    "group_id": "crew",
                    "status": "running",
                    "pid": 1234,
                    "agent_connection": {"expected": 1, "connected": 1, "attention": []},
                }
            ]
        }
        stdout = StringIO()
        with patch("agentsassemble.cli._request_json", return_value=payload) as request_json:
            with patch("agentsassemble.cli.time.monotonic", side_effect=[10.0, 10.0]):
                with patch("sys.stdout", stdout):
                    exit_code = main(
                        [
                            "live-agent",
                            "processes",
                            "wait",
                            "crew",
                            "--server",
                            "http://room.local",
                            "--timeout",
                            "3",
                        ]
                    )

        self.assertEqual(exit_code, 0)
        request_json.assert_called_once_with("http://room.local/api/live-agent-processes", timeout_seconds=3.0)

    def test_live_agent_processes_wait_sleeps_only_remaining_time_after_slow_poll(self):
        payload = {
            "groups": [
                {
                    "group_id": "crew",
                    "status": "running",
                    "pid": 1234,
                    "agent_connection": {
                        "expected": 2,
                        "connected": 1,
                        "attention": [{"agent_id": "agent-b", "status": "missing"}],
                    },
                }
            ]
        }
        stdout = StringIO()
        with patch("agentsassemble.cli._request_json", return_value=payload):
            with patch("agentsassemble.cli.time.monotonic", side_effect=[0.0, 0.0, 0.9, 1.1]):
                with patch("agentsassemble.cli.time.sleep") as sleep:
                    with patch("sys.stdout", stdout):
                        exit_code = main(
                            [
                                "live-agent",
                                "processes",
                                "wait",
                                "crew",
                                "--server",
                                "http://room.local",
                                "--timeout",
                                "1",
                                "--poll-interval",
                                "1",
                            ]
                        )

        self.assertEqual(exit_code, 1)
        self.assertEqual(sleep.call_count, 1)
        self.assertAlmostEqual(sleep.call_args.args[0], 0.1)

    def test_live_agent_processes_wait_reports_poll_timeout_as_wait_timeout_json(self):
        stdout = StringIO()
        stderr = StringIO()
        with patch("agentsassemble.cli._request_json", side_effect=TimeoutError("timed out")):
            with patch("agentsassemble.cli.time.monotonic", side_effect=[10.0, 10.0]):
                with patch("sys.stdout", stdout):
                    with patch("sys.stderr", stderr):
                        exit_code = main(
                            [
                                "live-agent",
                                "processes",
                                "wait",
                                "crew",
                                "--server",
                                "http://room.local",
                                "--timeout",
                                "3",
                                "--json",
                            ]
                        )

        self.assertEqual(exit_code, 1)
        self.assertEqual(stderr.getvalue(), "")
        payload = json.loads(stdout.getvalue())
        self.assertEqual(payload["status"], "timeout")
        self.assertEqual(payload["group_id"], "crew")
        self.assertEqual(payload["attempts"], 1)
        self.assertIsNone(payload["group"])
        self.assertEqual(payload["error"], "timed out")

    def test_live_agent_processes_wait_reports_wrapped_url_timeout_as_wait_timeout_json(self):
        stdout = StringIO()
        stderr = StringIO()
        timeout_error = cli_module.urllib.error.URLError(TimeoutError("timed out"))
        with patch("agentsassemble.cli._request_json", side_effect=timeout_error):
            with patch("agentsassemble.cli.time.monotonic", side_effect=[10.0, 10.0]):
                with patch("sys.stdout", stdout):
                    with patch("sys.stderr", stderr):
                        exit_code = main(
                            [
                                "live-agent",
                                "processes",
                                "wait",
                                "crew",
                                "--server",
                                "http://room.local",
                                "--timeout",
                                "3",
                                "--json",
                            ]
                        )

        self.assertEqual(exit_code, 1)
        self.assertEqual(stderr.getvalue(), "")
        payload = json.loads(stdout.getvalue())
        self.assertEqual(payload["status"], "timeout")
        self.assertEqual(payload["group_id"], "crew")
        self.assertEqual(payload["attempts"], 1)
        self.assertIsNone(payload["group"])
        self.assertEqual(payload["error"], "<urlopen error timed out>")

    def test_live_agent_processes_start_posts_supervisor_payload(self):
        payload = {"group": {"group_id": "crew", "status": "running", "pid": 1234}, "groups": []}
        stdout = StringIO()
        with patch("agentsassemble.cli._request_json", return_value=payload) as request_json:
            with patch("sys.stdout", stdout):
                exit_code = main(
                    [
                        "live-agent",
                        "processes",
                        "start",
                        "--server",
                        "http://room.local",
                        "--config",
                        "configs/live-agents.example.json",
                        "--group-id",
                        "crew",
                        "--auto-restart",
                        "--max-restarts",
                        "2",
                        "--restart-backoff-seconds",
                        "1.5",
                        "--stale-restart-after-seconds",
                        "240",
                    ]
                )

        self.assertEqual(exit_code, 0)
        request_json.assert_called_once_with(
            "http://room.local/api/live-agent-processes/start",
            method="POST",
            payload={
                "config_path": "configs/live-agents.example.json",
                "server": "http://room.local",
                "group_id": "crew",
                "auto_restart": True,
                "max_restarts": 2,
                "restart_backoff_seconds": 1.5,
                "stale_restart_after_seconds": 240.0,
            },
        )
        self.assertIn("Started crew (pid 1234)", stdout.getvalue())

    def test_live_agent_processes_start_requires_positive_restart_limit_when_enabled(self):
        stderr = StringIO()
        with patch("agentsassemble.cli._request_json") as request_json:
            with patch("sys.stderr", stderr):
                exit_code = main(
                    [
                        "live-agent",
                        "processes",
                        "start",
                        "--server",
                        "http://room.local",
                        "--config",
                        "configs/live-agents.example.json",
                        "--auto-restart",
                    ]
                )

        self.assertEqual(exit_code, 2)
        request_json.assert_not_called()
        self.assertIn("--auto-restart requires --max-restarts greater than 0", stderr.getvalue())

    def test_live_agent_processes_start_requires_auto_restart_for_stale_watchdog(self):
        stderr = StringIO()
        with patch("agentsassemble.cli._request_json") as request_json:
            with patch("sys.stderr", stderr):
                exit_code = main(
                    [
                        "live-agent",
                        "processes",
                        "start",
                        "--server",
                        "http://room.local",
                        "--config",
                        "configs/live-agents.example.json",
                        "--stale-restart-after-seconds",
                        "240",
                    ]
                )

        self.assertEqual(exit_code, 2)
        request_json.assert_not_called()
        self.assertIn("--stale-restart-after-seconds requires --auto-restart", stderr.getvalue())

    def test_live_agent_processes_rejects_invalid_restart_numbers(self):
        invalid_commands = [
            [
                "live-agent",
                "processes",
                "start",
                "--config",
                "configs/live-agents.example.json",
                "--max-restarts",
                "-1",
            ],
            [
                "live-agent",
                "processes",
                "start",
                "--config",
                "configs/live-agents.example.json",
                "--restart-backoff-seconds",
                "-0.1",
            ],
            [
                "live-agent",
                "processes",
                "start",
                "--config",
                "configs/live-agents.example.json",
                "--restart-backoff-seconds",
                "inf",
            ],
            [
                "live-agent",
                "processes",
                "start",
                "--config",
                "configs/live-agents.example.json",
                "--restart-backoff-seconds",
                "nan",
            ],
        ]

        with patch("sys.stderr", StringIO()):
            for command in invalid_commands:
                with self.subTest(command=command):
                    with self.assertRaises(SystemExit) as raised:
                        build_parser().parse_args(command)
                    self.assertEqual(raised.exception.code, 2)

    def test_live_agent_processes_json_prints_raw_payload(self):
        payload = {"groups": [{"group_id": "crew", "status": "running"}]}
        stdout = StringIO()
        with patch("agentsassemble.cli._request_json", return_value=payload):
            with patch("sys.stdout", stdout):
                exit_code = main(["live-agent", "processes", "list", "--server", "http://room.local", "--json"])

        self.assertEqual(exit_code, 0)
        self.assertEqual(json.loads(stdout.getvalue()), payload)

    def test_live_agent_processes_events_fetches_filtered_history(self):
        payload = {
            "events": [
                {
                    "timestamp": "2026-05-17T12:00:00+00:00",
                    "group_id": "crew one",
                    "event_type": "started",
                    "status": "running",
                    "pid": 1234,
                    "restart_count": 0,
                    "max_restarts": 2,
                },
                {
                    "timestamp": "2026-05-17T12:01:00+00:00",
                    "group_id": "crew one",
                    "event_type": "stale_watchdog",
                    "status": "running",
                    "returncode": -98,
                    "restart_count": 1,
                    "max_restarts": 2,
                    "reason": "stale manifest agent agent-a",
                    "next_restart_at": "2026-05-17T12:01:10+00:00",
                    "offline": {
                        "expected": 2,
                        "offline": 1,
                        "skipped": 1,
                        "offline_agent_ids": ["agent-a"],
                        "attention": [{"agent_id": "agent-b", "status": "wrong_meeting"}],
                    },
                },
            ]
        }
        stdout = StringIO()
        with patch("agentsassemble.cli._request_json", return_value=payload) as request_json:
            with patch("sys.stdout", stdout):
                exit_code = main(
                    [
                        "live-agent",
                        "processes",
                        "events",
                        "--server",
                        "http://room.local",
                        "--group-id",
                        "crew one",
                        "--limit",
                        "2",
                    ]
                )

        self.assertEqual(exit_code, 0)
        request_json.assert_called_once_with("http://room.local/api/live-agent-process-events?limit=2&group_id=crew+one")
        output = stdout.getvalue()
        self.assertIn("2026-05-17T12:00:00+00:00 crew one started running pid 1234 restarts 0/2", output)
        self.assertIn("2026-05-17T12:01:00+00:00 crew one stale_watchdog running returncode -98 restarts 1/2", output)
        self.assertIn("reason stale manifest agent agent-a", output)
        self.assertIn("next restart 2026-05-17T12:01:10+00:00", output)
        self.assertIn("offline 1/2", output)
        self.assertIn("wrong_meeting agent-b", output)

    def test_live_agent_processes_events_json_prints_raw_payload(self):
        payload = {
            "events": [{"group_id": "crew", "event_type": "started"}],
            "limit": 3,
            "group_id": "",
            "scan_limit": 500,
            "scanned_event_count": 1,
            "truncated": False,
        }
        stdout = StringIO()
        with patch("agentsassemble.cli._request_json", return_value=payload) as request_json:
            with patch("sys.stdout", stdout):
                exit_code = main(
                    [
                        "live-agent",
                        "processes",
                        "events",
                        "--server",
                        "http://room.local",
                        "--limit",
                        "3",
                        "--json",
                    ]
                )

        self.assertEqual(exit_code, 0)
        request_json.assert_called_once_with("http://room.local/api/live-agent-process-events?limit=3")
        self.assertEqual(json.loads(stdout.getvalue()), payload)

    def test_live_agent_processes_events_warns_when_scan_is_truncated(self):
        payload = {
            "events": [],
            "limit": 2,
            "group_id": "missing",
            "scan_limit": 3,
            "scanned_event_count": 3,
            "truncated": True,
        }
        stdout = StringIO()
        with patch("agentsassemble.cli._request_json", return_value=payload) as request_json:
            with patch("sys.stdout", stdout):
                exit_code = main(
                    [
                        "live-agent",
                        "processes",
                        "events",
                        "--server",
                        "http://room.local",
                        "--group-id",
                        "missing",
                        "--limit",
                        "2",
                        "--scan-limit",
                        "3",
                    ]
                )

        self.assertEqual(exit_code, 0)
        request_json.assert_called_once_with(
            "http://room.local/api/live-agent-process-events?limit=2&scan_limit=3&group_id=missing"
        )
        output = stdout.getvalue()
        self.assertIn("no live-agent process events", output)
        self.assertIn("searched recent 3 lifecycle events; older matches may exist", output)

    def test_live_agent_processes_wait_event_parses_filters_and_wait_options(self):
        args = build_parser().parse_args(
            [
                "live-agent",
                "processes",
                "wait-event",
                "--server",
                "http://room.local",
                "--group-id",
                "crew one",
                "--event-type",
                "restart_scheduled",
                "--status",
                "restarting",
                "--after-timestamp",
                "2026-05-17T12:00:00+00:00",
                "--limit",
                "5",
                "--scan-limit",
                "20",
                "--timeout",
                "3",
                "--poll-interval",
                "0.5",
                "--json",
            ]
        )

        self.assertEqual(args.live_agent_command, "processes")
        self.assertEqual(args.live_agent_process_command, "wait-event")
        self.assertEqual(args.group_id, "crew one")
        self.assertEqual(args.event_type, "restart_scheduled")
        self.assertEqual(args.status, "restarting")
        self.assertEqual(args.after_timestamp, "2026-05-17T12:00:00+00:00")
        self.assertEqual(args.limit, 5)
        self.assertEqual(args.scan_limit, 20)
        self.assertEqual(args.timeout, 3.0)
        self.assertEqual(args.poll_interval, 0.5)
        self.assertTrue(args.as_json)

    def test_live_agent_wait_room_event_parses_cursor_and_wait_options(self):
        args = build_parser().parse_args(
            [
                "live-agent",
                "wait-room-event",
                "--server",
                "http://room.local",
                "--agent-id",
                "claude-terminal",
                "--after-event-id",
                "evt-old",
                "--max-chain-depth",
                "2",
                "--timeout",
                "3",
                "--poll-interval",
                "0.5",
                "--json",
            ]
        )

        self.assertEqual(args.live_agent_command, "wait-room-event")
        self.assertEqual(args.agent_id, "claude-terminal")
        self.assertEqual(args.after_event_id, "evt-old")
        self.assertEqual(args.max_chain_depth, 2)
        self.assertEqual(args.timeout, 3.0)
        self.assertEqual(args.poll_interval, 0.5)
        self.assertTrue(args.as_json)

    def test_live_agent_wait_room_event_returns_next_non_self_lobby_event(self):
        stdout = StringIO()
        room_payload = {
            "agent": {
                "agent_id": "claude-terminal",
                "display_name": "Claude Terminal",
                "last_observed_event_id": "evt-old",
            },
            "lobby_events": [
                {"id": "evt-old", "name": "나", "message": "old"},
                {"id": "evt-self", "actor_id": "claude-terminal", "name": "Claude Terminal", "message": "self"},
                {"id": "evt-next", "name": "나", "message": "새 이벤트", "auto_chain_depth": 1},
            ],
        }

        with patch("agentsassemble.cli._request_json", return_value=room_payload) as request_json:
            with patch("sys.stdout", stdout):
                exit_code = main(
                    [
                        "live-agent",
                        "wait-room-event",
                        "--server",
                        "http://room.local",
                        "--agent-id",
                        "claude-terminal",
                        "--timeout",
                        "0",
                        "--json",
                    ]
                )

        self.assertEqual(exit_code, 0)
        request_json.assert_called_once_with("http://room.local/api/live-agents/claude-terminal/room")
        payload = json.loads(stdout.getvalue())
        self.assertEqual(payload["status"], "event")
        self.assertEqual(payload["event"]["id"], "evt-next")
        self.assertEqual(payload["source_event_id"], "evt-next")
        self.assertEqual(payload["reply_command"][0:7], ["python3", "-m", "agentsassemble.cli", "live-agent", "say", "--server", "http://room.local"])
        self.assertEqual(payload["reply_command"][-2:], ["--", "<reply>"])

    def test_live_agent_wait_room_event_treats_actor_id_as_authoritative_for_self_check(self):
        stdout = StringIO()
        room_payload = {
            "agent": {
                "agent_id": "claude-terminal",
                "display_name": "Shared Name",
                "last_observed_event_id": "evt-old",
            },
            "lobby_events": [
                {"id": "evt-old", "name": "나", "message": "old"},
                {"id": "evt-other", "actor_id": "other-agent", "name": "Shared Name", "message": "not self"},
            ],
        }

        with patch("agentsassemble.cli._request_json", return_value=room_payload):
            with patch("sys.stdout", stdout):
                exit_code = main(
                    [
                        "live-agent",
                        "wait-room-event",
                        "--server",
                        "http://room.local",
                        "--agent-id",
                        "claude-terminal",
                        "--timeout",
                        "0",
                        "--json",
                    ]
                )

        self.assertEqual(exit_code, 0)
        payload = json.loads(stdout.getvalue())
        self.assertEqual(payload["event"]["id"], "evt-other")

    def test_live_agent_wait_room_event_polls_until_candidate_arrives(self):
        stdout = StringIO()
        first_room = {
            "agent": {"agent_id": "claude-terminal", "last_observed_event_id": "evt-old"},
            "lobby_events": [{"id": "evt-old", "name": "나", "message": "old"}],
        }
        second_room = {
            "agent": {"agent_id": "claude-terminal", "last_observed_event_id": "evt-old"},
            "lobby_events": [
                {"id": "evt-old", "name": "나", "message": "old"},
                {"id": "evt-next", "name": "나", "message": "new"},
            ],
        }

        with patch("agentsassemble.cli._request_json", side_effect=[first_room, second_room]) as request_json:
            with patch("agentsassemble.cli.time.sleep") as sleep:
                with patch("sys.stdout", stdout):
                    exit_code = main(
                        [
                            "live-agent",
                            "wait-room-event",
                            "--server",
                            "http://room.local",
                            "--agent-id",
                            "claude-terminal",
                            "--timeout",
                            "1",
                            "--poll-interval",
                            "0",
                            "--json",
                        ]
                    )

        self.assertEqual(exit_code, 0)
        self.assertEqual(request_json.call_count, 2)
        sleep.assert_called_once()
        payload = json.loads(stdout.getvalue())
        self.assertEqual(payload["event"]["id"], "evt-next")

    def test_live_agent_wait_room_event_times_out_without_new_event(self):
        stdout = StringIO()
        room_payload = {
            "agent": {"agent_id": "claude-terminal", "last_observed_event_id": "evt-old"},
            "lobby_events": [{"id": "evt-old", "name": "나", "message": "old"}],
        }

        with patch("agentsassemble.cli._request_json", return_value=room_payload):
            with patch("sys.stdout", stdout):
                exit_code = main(
                    [
                        "live-agent",
                        "wait-room-event",
                        "--server",
                        "http://room.local",
                        "--agent-id",
                        "claude-terminal",
                        "--timeout",
                        "0",
                        "--json",
                    ]
                )

        self.assertEqual(exit_code, 1)
        payload = json.loads(stdout.getvalue())
        self.assertEqual(payload["status"], "timeout")
        self.assertEqual(payload["last_observed_event_id"], "evt-old")

    def test_live_agent_wait_room_event_skips_over_chain_limit(self):
        stdout = StringIO()
        room_payload = {
            "agent": {"agent_id": "claude-terminal", "last_observed_event_id": "evt-old"},
            "lobby_events": [
                {"id": "evt-old", "name": "나", "message": "old"},
                {"id": "evt-chain", "name": "Gemini", "message": "chain", "auto_chain_depth": 2},
            ],
        }

        with patch("agentsassemble.cli._request_json", return_value=room_payload):
            with patch("sys.stdout", stdout):
                exit_code = main(
                    [
                        "live-agent",
                        "wait-room-event",
                        "--server",
                        "http://room.local",
                        "--agent-id",
                        "claude-terminal",
                        "--max-chain-depth",
                        "1",
                        "--timeout",
                        "0",
                        "--json",
                    ]
                )

        self.assertEqual(exit_code, 1)
        payload = json.loads(stdout.getvalue())
        self.assertEqual(payload["status"], "timeout")

    def test_live_agent_wait_official_turn_parses_cursor_and_wait_options(self):
        args = build_parser().parse_args(
            [
                "live-agent",
                "wait-official-turn",
                "--server",
                "http://room.local",
                "--agent-id",
                "claude-terminal",
                "--after-event-id",
                "live-old",
                "--timeout",
                "3",
                "--poll-interval",
                "0.5",
                "--json",
            ]
        )

        self.assertEqual(args.live_agent_command, "wait-official-turn")
        self.assertEqual(args.agent_id, "claude-terminal")
        self.assertEqual(args.after_event_id, "live-old")
        self.assertEqual(args.timeout, 3.0)
        self.assertEqual(args.poll_interval, 0.5)
        self.assertTrue(args.as_json)

    def test_live_agent_wait_turn_request_alias_parses_same_options(self):
        args = build_parser().parse_args(
            [
                "live-agent",
                "wait-turn-request",
                "--server",
                "http://room.local",
                "--agent-id",
                "claude-terminal",
                "--after-event-id",
                "live-old",
                "--timeout",
                "3",
                "--poll-interval",
                "0.5",
                "--json",
            ]
        )

        self.assertEqual(args.live_agent_command, "wait-turn-request")
        self.assertEqual(args.agent_id, "claude-terminal")
        self.assertEqual(args.after_event_id, "live-old")
        self.assertEqual(args.timeout, 3.0)
        self.assertEqual(args.poll_interval, 0.5)
        self.assertTrue(args.as_json)

    def test_live_agent_wait_official_turn_returns_targeted_unanswered_request(self):
        stdout = StringIO()
        room_payload = {
            "agent": {
                "agent_id": "claude-terminal",
                "meeting_id": "meeting-1",
                "last_observed_live_event_id": "live-old",
            },
            "live_events": [
                {"id": "live-old", "kind": "message", "channel": "official", "actor_id": "other-agent", "content": "old"},
                {
                    "id": "live-other",
                    "kind": "live_agent_turn_request",
                    "target_agent_id": "other-agent",
                    "content": "not yours",
                },
                {
                    "id": "live-answered",
                    "kind": "live_agent_turn_request",
                    "target_agent_id": "claude-terminal",
                    "content": "already answered",
                },
                {
                    "id": "reply-answered",
                    "kind": "message",
                    "channel": "official",
                    "official_record": True,
                    "actor_id": "claude-terminal",
                    "source_event_id": "live-answered",
                    "content": "done",
                },
                {
                    "id": "live-next",
                    "kind": "live_agent_turn_request",
                    "meeting_id": "meeting-1",
                    "target_agent_id": "claude-terminal",
                    "role_id": "architect",
                    "display_name": "Claude Terminal",
                    "content": "Give the official answer.",
                },
            ],
        }

        with patch("agentsassemble.cli._request_json", return_value=room_payload) as request_json:
            with patch("sys.stdout", stdout):
                exit_code = main(
                    [
                        "live-agent",
                        "wait-official-turn",
                        "--server",
                        "http://room.local",
                        "--agent-id",
                        "claude-terminal",
                        "--timeout",
                        "0",
                        "--json",
                    ]
                )

        self.assertEqual(exit_code, 0)
        request_json.assert_called_once_with("http://room.local/api/live-agents/claude-terminal/room")
        payload = json.loads(stdout.getvalue())
        self.assertEqual(payload["status"], "event")
        self.assertEqual(payload["event"]["id"], "live-next")
        self.assertEqual(payload["meeting_id"], "meeting-1")
        self.assertEqual(payload["source_event_id"], "live-next")
        self.assertEqual(payload["reply_command"][0:7], ["python3", "-m", "agentsassemble.cli", "live-agent", "official-reply", "--server", "http://room.local"])
        self.assertEqual(payload["reply_command"][-2:], ["--", "<reply>"])

    def test_live_agent_wait_official_turn_uses_visible_tail_when_cursor_is_missing(self):
        stdout = StringIO()
        room_payload = {
            "agent": {
                "agent_id": "claude-terminal",
                "meeting_id": "meeting-1",
                "last_observed_live_event_id": "evicted-live-cursor",
            },
            "live_events": [
                {
                    "id": "live-visible",
                    "kind": "live_agent_turn_request",
                    "meeting_id": "meeting-1",
                    "target_agent_id": "claude-terminal",
                    "content": "visible tail request",
                },
            ],
        }

        with patch("agentsassemble.cli._request_json", return_value=room_payload):
            with patch("sys.stdout", stdout):
                exit_code = main(
                    [
                        "live-agent",
                        "wait-official-turn",
                        "--server",
                        "http://room.local",
                        "--agent-id",
                        "claude-terminal",
                        "--timeout",
                        "0",
                        "--json",
                    ]
                )

        self.assertEqual(exit_code, 0)
        payload = json.loads(stdout.getvalue())
        self.assertEqual(payload["event"]["id"], "live-visible")

    def test_live_agent_wait_official_turn_times_out_without_targeted_request(self):
        stdout = StringIO()
        room_payload = {
            "agent": {"agent_id": "claude-terminal", "last_observed_live_event_id": "live-old"},
            "live_events": [{"id": "live-old", "kind": "message", "content": "old"}],
        }

        with patch("agentsassemble.cli._request_json", return_value=room_payload):
            with patch("sys.stdout", stdout):
                exit_code = main(
                    [
                        "live-agent",
                        "wait-official-turn",
                        "--server",
                        "http://room.local",
                        "--agent-id",
                        "claude-terminal",
                        "--timeout",
                        "0",
                        "--json",
                    ]
                )

        self.assertEqual(exit_code, 1)
        payload = json.loads(stdout.getvalue())
        self.assertEqual(payload["status"], "timeout")
        self.assertEqual(payload["last_observed_live_event_id"], "live-old")

    def test_live_agent_official_reply_posts_official_reply(self):
        stdout = StringIO()
        response = {
            "agent": {"agent_id": "claude-terminal", "last_observed_live_event_id": "live-next"},
            "event": {"id": "reply-next"},
        }

        with patch("agentsassemble.cli._request_json", return_value=response) as request_json:
            with patch("sys.stdout", stdout):
                exit_code = main(
                    [
                        "live-agent",
                        "official-reply",
                        "--server",
                        "http://room.local",
                        "--agent-id",
                        "claude-terminal",
                        "--meeting-id",
                        "meeting-1",
                        "--source-event-id",
                        "live-next",
                        "--json",
                        "Official answer.",
                    ]
                )

        self.assertEqual(exit_code, 0)
        request_json.assert_called_once_with(
            "http://room.local/api/live-agents/claude-terminal/official-turn",
            method="POST",
            payload={
                "meeting_id": "meeting-1",
                "source_event_id": "live-next",
                "content": "Official answer.",
            },
        )
        payload = json.loads(stdout.getvalue())
        self.assertEqual(payload["event"]["id"], "reply-next")

    def test_live_agent_answer_turn_alias_posts_official_reply(self):
        stdout = StringIO()
        response = {
            "agent": {"agent_id": "claude-terminal", "last_observed_live_event_id": "live-next"},
            "event": {"id": "reply-next"},
        }

        with patch("agentsassemble.cli._request_json", return_value=response) as request_json:
            with patch("sys.stdout", stdout):
                exit_code = main(
                    [
                        "live-agent",
                        "answer-turn",
                        "--server",
                        "http://room.local",
                        "--agent-id",
                        "claude-terminal",
                        "--meeting-id",
                        "meeting-1",
                        "--source-event-id",
                        "live-next",
                        "--json",
                        "Official answer.",
                    ]
                )

        self.assertEqual(exit_code, 0)
        request_json.assert_called_once_with(
            "http://room.local/api/live-agents/claude-terminal/official-turn",
            method="POST",
            payload={
                "meeting_id": "meeting-1",
                "source_event_id": "live-next",
                "content": "Official answer.",
            },
        )
        payload = json.loads(stdout.getvalue())
        self.assertEqual(payload["event"]["id"], "reply-next")

    def test_live_agent_wait_next_parses_lobby_and_official_cursor_options(self):
        args = build_parser().parse_args(
            [
                "live-agent",
                "wait-next",
                "--server",
                "http://room.local",
                "--agent-id",
                "claude-terminal",
                "--after-event-id",
                "evt-old",
                "--after-live-event-id",
                "live-old",
                "--max-chain-depth",
                "2",
                "--timeout",
                "3",
                "--poll-interval",
                "0.5",
                "--json",
            ]
        )

        self.assertEqual(args.live_agent_command, "wait-next")
        self.assertEqual(args.agent_id, "claude-terminal")
        self.assertEqual(args.after_event_id, "evt-old")
        self.assertEqual(args.after_live_event_id, "live-old")
        self.assertEqual(args.max_chain_depth, 2)
        self.assertEqual(args.timeout, 3.0)
        self.assertEqual(args.poll_interval, 0.5)
        self.assertTrue(args.as_json)

    def test_live_agent_wait_next_prefers_official_turn_over_lobby_event(self):
        stdout = StringIO()
        room_payload = {
            "agent": {
                "agent_id": "claude-terminal",
                "meeting_id": "meeting-1",
                "last_observed_event_id": "evt-old",
                "last_observed_live_event_id": "live-old",
            },
            "lobby_events": [
                {"id": "evt-old", "name": "나", "message": "old"},
                {"id": "evt-next", "name": "나", "message": "lobby question"},
            ],
            "live_events": [
                {"id": "live-old", "kind": "message", "content": "old"},
                {
                    "id": "live-next",
                    "kind": "live_agent_turn_request",
                    "meeting_id": "meeting-1",
                    "target_agent_id": "claude-terminal",
                    "content": "official question",
                },
            ],
        }

        with patch("agentsassemble.cli._request_json", return_value=room_payload):
            with patch("sys.stdout", stdout):
                exit_code = main(
                    [
                        "live-agent",
                        "wait-next",
                        "--server",
                        "http://room.local",
                        "--agent-id",
                        "claude-terminal",
                        "--timeout",
                        "0",
                        "--json",
                    ]
                )

        self.assertEqual(exit_code, 0)
        payload = json.loads(stdout.getvalue())
        self.assertEqual(payload["action"], "official_turn")
        self.assertEqual(payload["event"]["id"], "live-next")
        self.assertEqual(payload["reply_command"][4], "official-reply")

    def test_live_agent_wait_next_returns_lobby_event_when_no_official_turn_is_pending(self):
        stdout = StringIO()
        room_payload = {
            "agent": {
                "agent_id": "claude-terminal",
                "display_name": "Claude Terminal",
                "last_observed_event_id": "evt-old",
            },
            "lobby_events": [
                {"id": "evt-old", "name": "나", "message": "old"},
                {"id": "evt-next", "name": "나", "message": "lobby question"},
            ],
            "live_events": [],
        }

        with patch("agentsassemble.cli._request_json", return_value=room_payload):
            with patch("sys.stdout", stdout):
                exit_code = main(
                    [
                        "live-agent",
                        "wait-next",
                        "--server",
                        "http://room.local",
                        "--agent-id",
                        "claude-terminal",
                        "--timeout",
                        "0",
                        "--json",
                    ]
                )

        self.assertEqual(exit_code, 0)
        payload = json.loads(stdout.getvalue())
        self.assertEqual(payload["action"], "lobby")
        self.assertEqual(payload["event"]["id"], "evt-next")
        self.assertEqual(payload["reply_command"][4], "say")

    def test_live_agent_wait_next_does_not_use_lobby_cursor_for_official_turns(self):
        stdout = StringIO()
        room_payload = {
            "agent": {
                "agent_id": "claude-terminal",
                "meeting_id": "meeting-1",
                "last_observed_event_id": "evt-lobby",
                "last_observed_live_event_id": "live-newer",
            },
            "lobby_events": [{"id": "evt-lobby", "name": "나", "message": "old lobby"}],
            "live_events": [
                {
                    "id": "live-old-request",
                    "kind": "live_agent_turn_request",
                    "meeting_id": "meeting-1",
                    "target_agent_id": "claude-terminal",
                    "content": "old official request",
                },
                {"id": "live-newer", "kind": "message", "channel": "official", "content": "newer marker"},
            ],
        }

        with patch("agentsassemble.cli._request_json", return_value=room_payload):
            with patch("sys.stdout", stdout):
                exit_code = main(
                    [
                        "live-agent",
                        "wait-next",
                        "--server",
                        "http://room.local",
                        "--agent-id",
                        "claude-terminal",
                        "--after-event-id",
                        "evt-lobby",
                        "--timeout",
                        "0",
                        "--json",
                    ]
                )

        self.assertEqual(exit_code, 1)
        payload = json.loads(stdout.getvalue())
        self.assertEqual(payload["status"], "timeout")
        self.assertEqual(payload["last_observed_event_id"], "evt-lobby")
        self.assertEqual(payload["last_observed_live_event_id"], "live-newer")

    def test_live_agent_wait_next_times_out_with_both_cursors(self):
        stdout = StringIO()
        room_payload = {
            "agent": {
                "agent_id": "claude-terminal",
                "last_observed_event_id": "evt-old",
                "last_observed_live_event_id": "live-old",
            },
            "lobby_events": [{"id": "evt-old", "name": "나", "message": "old"}],
            "live_events": [{"id": "live-old", "kind": "message", "content": "old"}],
        }

        with patch("agentsassemble.cli._request_json", return_value=room_payload):
            with patch("sys.stdout", stdout):
                exit_code = main(
                    [
                        "live-agent",
                        "wait-next",
                        "--server",
                        "http://room.local",
                        "--agent-id",
                        "claude-terminal",
                        "--timeout",
                        "0",
                        "--json",
                    ]
                )

        self.assertEqual(exit_code, 1)
        payload = json.loads(stdout.getvalue())
        self.assertEqual(payload["status"], "timeout")
        self.assertEqual(payload["last_observed_event_id"], "evt-old")
        self.assertEqual(payload["last_observed_live_event_id"], "live-old")

    def test_live_agent_official_self_service_round_trip_against_gui_server(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "room"
            (root / "meetings" / "m1").mkdir(parents=True)
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
                            "claude-terminal",
                            "--display-name",
                            "Claude Terminal",
                            "--provider-kind",
                            "claude_code",
                            "--connection-kind",
                            "manual",
                            "--meeting-id",
                            "m1",
                            "--engagement-mode",
                            "moderator_called",
                        ]
                    )
                call_stdout = StringIO()
                with patch("sys.stdout", call_stdout):
                    call_exit = main(
                        [
                            "live-agent",
                            "call",
                            "--server",
                            server_url,
                            "--meeting-id",
                            "m1",
                            "--agent-id",
                            "claude-terminal",
                            "--role-id",
                            "architect",
                            "--json",
                            "Give the official answer.",
                        ]
                    )
                wait_stdout = StringIO()
                with patch("sys.stdout", wait_stdout):
                    wait_exit = main(
                        [
                            "live-agent",
                            "wait-official-turn",
                            "--server",
                            server_url,
                            "--agent-id",
                            "claude-terminal",
                            "--timeout",
                            "0",
                            "--json",
                        ]
                    )
                wait_payload = json.loads(wait_stdout.getvalue())
                reply_stdout = StringIO()
                with patch("sys.stdout", reply_stdout):
                    reply_exit = main(
                        [
                            "live-agent",
                            "official-reply",
                            "--server",
                            server_url,
                            "--agent-id",
                            "claude-terminal",
                            "--meeting-id",
                            "m1",
                            "--source-event-id",
                            wait_payload["source_event_id"],
                            "--json",
                            "Official self-service reply.",
                        ]
                    )
                operations = cli_module._request_json(f"{server_url}/api/live-agent-operations")
                persisted_agent = json.loads((root / "live_agents.json").read_text(encoding="utf-8"))["agents"][0]
            finally:
                server.shutdown()
                server.server_close()

        self.assertEqual((register_exit, call_exit, wait_exit, reply_exit), (0, 0, 0, 0))
        call_payload = json.loads(call_stdout.getvalue())
        reply_payload = json.loads(reply_stdout.getvalue())
        self.assertEqual(wait_payload["source_event_id"], call_payload["event"]["id"])
        self.assertEqual(wait_payload["meeting_id"], "m1")
        self.assertEqual(reply_payload["event"]["source_event_id"], wait_payload["source_event_id"])
        self.assertEqual(reply_payload["event"]["content"], "Official self-service reply.")
        self.assertEqual(persisted_agent["last_observed_live_event_id"], wait_payload["source_event_id"])
        self.assertIn("official_turn.reply", [item["operation"] for item in operations["operations"]])

    def test_live_agent_processes_wait_event_observes_matching_event_after_timestamp(self):
        payloads = [
            {
                "events": [
                    {
                        "timestamp": "2026-05-17T12:00:00+00:00",
                        "group_id": "crew-one",
                        "event_type": "restart_scheduled",
                        "status": "restarting",
                    }
                ],
                "truncated": False,
            },
            {
                "events": [
                    {
                        "timestamp": "2026-05-17T12:01:00+00:00",
                        "group_id": "crew-one",
                        "event_type": "restart_scheduled",
                        "status": "restarting",
                        "restart_count": 1,
                        "max_restarts": 2,
                    }
                ],
                "truncated": False,
            },
        ]
        stdout = StringIO()
        with patch("agentsassemble.cli._request_json", side_effect=payloads) as request_json:
            with patch("agentsassemble.cli.time.sleep") as sleep:
                with patch("sys.stdout", stdout):
                    exit_code = main(
                        [
                            "live-agent",
                            "processes",
                            "wait-event",
                            "--server",
                            "http://room.local",
                            "--group-id",
                            "crew one",
                            "--event-type",
                            "restart_scheduled",
                            "--status",
                            "restarting",
                            "--after-timestamp",
                            "2026-05-17T12:00:00+00:00",
                            "--limit",
                            "5",
                            "--scan-limit",
                            "20",
                            "--timeout",
                            "3",
                            "--poll-interval",
                            "0.1",
                            "--json",
                        ]
                    )

        self.assertEqual(exit_code, 0)
        self.assertEqual(request_json.call_count, 2)
        self.assertEqual(
            request_json.call_args_list[-1].args,
            ("http://room.local/api/live-agent-process-events?limit=5&scan_limit=20&group_id=crew+one",),
        )
        self.assertIn("timeout_seconds", request_json.call_args_list[-1].kwargs)
        self.assertEqual(sleep.call_count, 1)
        result = json.loads(stdout.getvalue())
        self.assertEqual(result["status"], "observed")
        self.assertEqual(result["event"]["timestamp"], "2026-05-17T12:01:00+00:00")

    def test_live_agent_processes_wait_event_times_out_with_last_event(self):
        payload = {
            "events": [
                {
                    "timestamp": "2026-05-17T12:00:00+00:00",
                    "group_id": "crew one",
                    "event_type": "started",
                    "status": "running",
                }
            ],
            "truncated": False,
        }
        stdout = StringIO()
        with patch("agentsassemble.cli._request_json", return_value=payload) as request_json:
            with patch("agentsassemble.cli.time.monotonic", side_effect=[0.0, 0.0, 1.1, 1.1]):
                with patch("agentsassemble.cli.time.sleep") as sleep:
                    with patch("sys.stdout", stdout):
                        exit_code = main(
                            [
                                "live-agent",
                                "processes",
                                "wait-event",
                                "--server",
                                "http://room.local",
                                "--group-id",
                                "crew one",
                                "--event-type",
                                "restart_scheduled",
                                "--timeout",
                                "1",
                                "--poll-interval",
                                "0.1",
                            ]
                        )

        self.assertEqual(exit_code, 1)
        self.assertEqual(request_json.call_count, 1)
        self.assertEqual(sleep.call_count, 0)
        output = stdout.getvalue()
        self.assertIn("Timed out waiting for live-agent process event restart_scheduled", output)
        self.assertIn("last event: 2026-05-17T12:00:00+00:00 crew one started running", output)

    def test_live_agent_processes_stop_restart_and_recover_quote_group_id(self):
        stop_payload = {
            "group": {
                "group_id": "crew one",
                "status": "stopped",
                "offline": {"expected": 2, "offline": 2, "skipped": 0, "offline_agent_ids": ["a", "b"], "attention": []},
            }
        }
        restart_payload = {"group": {"group_id": "crew one", "status": "running", "pid": 5678}}
        recover_payload = {"group": {"group_id": "crew one", "status": "running", "pid": 6789, "recovered_from_status": "unknown"}}
        stdout = StringIO()
        with patch("agentsassemble.cli._request_json", side_effect=[stop_payload, restart_payload, recover_payload]) as request_json:
            with patch("sys.stdout", stdout):
                stop_exit = main(["live-agent", "processes", "stop", "crew one", "--server", "http://room.local"])
                restart_exit = main(["live-agent", "processes", "restart", "crew one", "--server", "http://room.local"])
                recover_exit = main(["live-agent", "processes", "recover", "crew one", "--server", "http://room.local"])

        self.assertEqual(stop_exit, 0)
        self.assertEqual(restart_exit, 0)
        self.assertEqual(recover_exit, 0)
        self.assertEqual(
            request_json.call_args_list[0].args[0],
            "http://room.local/api/live-agent-processes/crew%20one/stop",
        )
        self.assertEqual(request_json.call_args_list[0].kwargs, {"method": "POST", "payload": {}})
        self.assertEqual(
            request_json.call_args_list[1].args[0],
            "http://room.local/api/live-agent-processes/crew%20one/restart",
        )
        self.assertEqual(request_json.call_args_list[1].kwargs, {"method": "POST", "payload": {}})
        self.assertEqual(
            request_json.call_args_list[2].args[0],
            "http://room.local/api/live-agent-processes/crew%20one/recover",
        )
        self.assertEqual(request_json.call_args_list[2].kwargs, {"method": "POST", "payload": {}})
        output = stdout.getvalue()
        self.assertIn("Stopped crew one (stopped, offline 2/2)", output)
        self.assertIn("Restarted crew one (pid 5678)", output)
        self.assertIn("Recovered crew one from unknown (pid 6789)", output)

    def test_live_agent_processes_stop_running_posts_bulk_endpoint(self):
        payload = {
            "result": {
                "stopped_count": 2,
                "failed_count": 0,
                "skipped_count": 1,
                "stopped": [
                    {
                        "group_id": "crew-a",
                        "status": "stopped",
                        "offline": {
                            "expected": 1,
                            "offline": 1,
                            "skipped": 0,
                            "offline_agent_ids": ["agent-a"],
                            "attention": [],
                        },
                    },
                    {
                        "group_id": "crew-b",
                        "status": "stopped",
                        "offline": {
                            "expected": 2,
                            "offline": 1,
                            "skipped": 1,
                            "offline_agent_ids": ["agent-b"],
                            "attention": [{"agent_id": "agent-c", "status": "wrong_meeting"}],
                        },
                    },
                ],
                "failed": [],
                "skipped": [{"group_id": "old-crew", "status": "unknown"}],
            },
            "groups": [],
        }
        stdout = StringIO()
        with patch("agentsassemble.cli._request_json", return_value=payload) as request_json:
            with patch("sys.stdout", stdout):
                exit_code = main(["live-agent", "processes", "stop-running", "--server", "http://room.local"])

        self.assertEqual(exit_code, 0)
        request_json.assert_called_once_with(
            "http://room.local/api/live-agent-processes/stop-running",
            method="POST",
            payload={},
        )
        self.assertIn("Stopped 2 live-agent process groups", stdout.getvalue())
        self.assertIn("skipped 1", stdout.getvalue())
        self.assertIn("offline 2/3", stdout.getvalue())

    def test_live_agent_processes_http_error_body_reaches_stderr(self):
        class BadRequestHandler:
            code = 400
            reason = "Bad Request"
            headers = {}

            def read(self):
                return b'{"error": "Live agent config missing.json was not found."}'

            def close(self):
                return None

        stderr = StringIO()
        with patch("urllib.request.urlopen", side_effect=urllib.error.HTTPError("url", 400, "Bad Request", {}, BadRequestHandler())):
            with patch("sys.stderr", stderr):
                exit_code = main(["live-agent", "processes", "list", "--server", "http://room.local"])

        self.assertEqual(exit_code, 2)
        self.assertIn("Live agent config missing.json was not found.", stderr.getvalue())

    def test_live_agent_processes_cli_controls_real_http_supervisor(self):
        class FakeSupervisor:
            def __init__(self):
                self.groups = []
                self.started = []
                self.stopped = []
                self.restarted = []

            def list_groups(self):
                return self.groups

            def start_group(
                self,
                *,
                config_path,
                server,
                group_id=None,
                auto_restart=False,
                max_restarts=0,
                restart_backoff_seconds=5.0,
            ):
                self.started.append(
                    {
                        "config_path": str(config_path),
                        "server": server,
                        "group_id": group_id,
                        "auto_restart": auto_restart,
                        "max_restarts": max_restarts,
                        "restart_backoff_seconds": restart_backoff_seconds,
                    }
                )
                record = {
                    "group_id": group_id or "live-agents",
                    "status": "running",
                    "pid": 1234,
                    "config_path": str(config_path),
                    "auto_restart": auto_restart,
                    "restart_count": 0,
                    "max_restarts": max_restarts,
                    "restart_backoff_seconds": restart_backoff_seconds,
                    "log_tail": "started",
                }
                self.groups = [record]
                return record

            def stop_group(self, group_id):
                self.stopped.append(group_id)
                record = dict(self.groups[0])
                record["status"] = "stopped"
                record["pid"] = None
                self.groups = [record]
                return record

            def restart_group(self, group_id):
                self.restarted.append(group_id)
                record = dict(self.groups[0])
                record["status"] = "running"
                record["pid"] = 5678
                self.groups = [record]
                return record

            def snapshot_groups(self):
                return self.groups

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "room"
            root.mkdir()
            config_path = Path(temp_dir) / "live-agents.json"
            config_path.write_text('{"agents": [{"agent_id": "a", "command": ["fake"]}]}', encoding="utf-8")
            supervisor = FakeSupervisor()
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root, process_supervisor=supervisor))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                server_url = f"http://127.0.0.1:{server.server_port}"
                with patch("sys.stdout", StringIO()) as stdout:
                    start_exit = main(
                        [
                            "live-agent",
                            "processes",
                            "start",
                            "--server",
                            server_url,
                            "--config",
                            str(config_path),
                            "--group-id",
                            "crew",
                            "--auto-restart",
                            "--max-restarts",
                            "2",
                        ]
                    )
                    list_exit = main(["live-agent", "processes", "list", "--server", server_url])
                    stop_exit = main(["live-agent", "processes", "stop", "crew", "--server", server_url])
                    restart_exit = main(["live-agent", "processes", "restart", "crew", "--server", server_url])
            finally:
                server.shutdown()
                server.server_close()

            self.assertEqual((start_exit, list_exit, stop_exit, restart_exit), (0, 0, 0, 0))
            self.assertEqual(supervisor.started[0]["group_id"], "crew")
            self.assertEqual(supervisor.started[0]["server"], server_url)
            self.assertEqual(supervisor.started[0]["auto_restart"], True)
            self.assertEqual(supervisor.started[0]["max_restarts"], 2)
            self.assertEqual(supervisor.stopped, ["crew"])
            self.assertEqual(supervisor.restarted, ["crew"])
            output = stdout.getvalue()
            self.assertIn("crew: running", output)
            self.assertIn("Started crew (pid 1234)", output)
            self.assertIn("Stopped crew (stopped)", output)
            self.assertIn("Restarted crew (pid 5678)", output)

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
                "http://friend.local:8777",
                "--auth-ref",
                "env:BRIDGE_TOKEN",
                "--max-ticks",
                "1",
            ]
        )

        self.assertEqual(args.live_agent_command, "run")
        self.assertEqual(args.connection_kind, "remote_bridge")
        self.assertEqual(args.endpoint, "http://friend.local:8777")
        self.assertEqual(args.auth_ref, "env:BRIDGE_TOKEN")
        self.assertEqual(args.resident_command, [])

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
        self.assertEqual(args.poll_interval, 2.0)
        self.assertEqual(args.heartbeat_interval, 30.0)
        self.assertEqual(args.cooldown, 5.0)
        self.assertEqual(args.max_chain_depth, 1)
        self.assertEqual(args.max_ticks, 0)
        self.assertEqual(args.resident_command, ["claude", "-p"])

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
        self.assertIn("--last-reply-at={last_reply_at}", heartbeat_template)
        self.assertIn("--last-observed-event-id={last_observed_event_id}", heartbeat_template)
        self.assertIn("--last-observed-live-event-id={last_observed_live_event_id}", heartbeat_template)
        self.assertFalse(any(call["url"].endswith("/room") for call in calls))

    def test_self_service_child_failure_keeps_error_presence(self):
        calls = []

        def request_json(url, *, method="GET", payload=None, **kwargs):
            del kwargs
            calls.append({"url": url, "method": method, "payload": payload})
            return {"agent": {"agent_id": "selfer", "status": (payload or {}).get("status", "online")}}

        supervisor = cli_module._SelfServiceResidentSupervisor(
            _self_service_resident_config(),
            request_json=request_json,
            sleep_fn=lambda seconds: None,
        )

        with patch("agentsassemble.cli.subprocess.Popen", return_value=_FailingSelfServiceProcess()):
            with self.assertRaises(subprocess.CalledProcessError):
                supervisor.run()

        heartbeat_payloads = [
            call["payload"]
            for call in calls
            if call["url"] == "http://room.local/api/live-agents/selfer/heartbeat"
        ]
        self.assertEqual(
            heartbeat_payloads,
            [
                {"status": "online"},
                {"status": "error", "last_error": "Self-service command exited with return code 7."},
            ],
        )

    def test_self_service_child_failure_falls_back_to_offline_when_error_heartbeat_fails(self):
        calls = []

        def request_json(url, *, method="GET", payload=None, **kwargs):
            del kwargs
            calls.append({"url": url, "method": method, "payload": payload})
            if payload and payload.get("status") == "error":
                raise RuntimeError("temporary heartbeat failure")
            return {"agent": {"agent_id": "selfer", "status": (payload or {}).get("status", "online")}}

        supervisor = cli_module._SelfServiceResidentSupervisor(
            _self_service_resident_config(),
            request_json=request_json,
            sleep_fn=lambda seconds: None,
        )

        with patch("agentsassemble.cli.subprocess.Popen", return_value=_FailingSelfServiceProcess()):
            with self.assertRaises(subprocess.CalledProcessError):
                supervisor.run()

        heartbeat_payloads = [
            call["payload"]
            for call in calls
            if call["url"] == "http://room.local/api/live-agents/selfer/heartbeat"
        ]
        self.assertEqual(
            heartbeat_payloads,
            [
                {"status": "online"},
                {"status": "error", "last_error": "Self-service command exited with return code 7."},
                {"status": "offline"},
            ],
        )

    def test_live_agent_run_group_keeps_self_service_child_in_group_process_session(self):
        config = ResidentAgentConfig(
            server="http://room.local",
            agent_id="selfer",
            display_name="Self Service",
            provider_kind="antigravity_cli",
            connection_kind="self_service",
            session_id="",
            endpoint="",
            auth_ref="",
            meeting_id="resident-m1",
            engagement_mode="always",
            command=[sys.executable, "-c", "pass"],
            timeout_seconds=120,
            poll_interval=0,
            heartbeat_interval=30,
            cooldown=5,
            max_chain_depth=1,
            max_ticks=1,
        )

        class FakeSelfServiceProcess:
            pid = 4321
            returncode = 0

            def poll(self):
                return self.returncode

            def wait(self, timeout=None):
                del timeout
                return self.returncode

            def terminate(self):
                self.returncode = -15

            def kill(self):
                self.returncode = -9

        popen_calls = []

        def fake_popen(command, **kwargs):
            popen_calls.append({"command": command, "kwargs": kwargs})
            return FakeSelfServiceProcess()

        def request_json(url, *, method="GET", payload=None, **kwargs):
            del url, method, kwargs
            return {"agent": {"agent_id": "selfer", "status": (payload or {}).get("status", "online")}}

        with (
            patch("agentsassemble.cli.load_group_configs", return_value=[config]),
            patch("agentsassemble.cli.resident_config_setup_error", return_value=""),
            patch("agentsassemble.cli._request_json", side_effect=request_json),
            patch("agentsassemble.cli._supports_process_groups", return_value=True),
            patch("agentsassemble.cli.subprocess.Popen", side_effect=fake_popen),
            patch("sys.stdout", StringIO()),
            patch("sys.stderr", StringIO()),
        ):
            exit_code = main(["live-agent", "run-group", "--config", "ignored.json"])

        self.assertEqual(exit_code, 0)
        self.assertEqual(len(popen_calls), 1)
        self.assertIs(popen_calls[0]["kwargs"]["start_new_session"], False)

    def test_self_service_parent_liveness_heartbeat_preserves_child_status(self):
        config = ResidentAgentConfig(
            server="http://room.local",
            agent_id="selfer",
            display_name="Self Service",
            provider_kind="antigravity_cli",
            connection_kind="self_service",
            session_id="",
            endpoint="",
            auth_ref="",
            meeting_id="resident-m1",
            engagement_mode="always",
            command=["agent"],
            timeout_seconds=120,
            poll_interval=0.5,
            heartbeat_interval=1,
            cooldown=5,
            max_chain_depth=1,
        )
        calls = []

        def request_json(url, *, method="GET", payload=None, **kwargs):
            del kwargs
            calls.append({"url": url, "method": method, "payload": payload})
            return {"agent": {"agent_id": "selfer", "status": "working"}}

        supervisor = cli_module._SelfServiceResidentSupervisor(
            config,
            request_json=request_json,
            sleep_fn=lambda seconds: None,
        )
        supervisor.last_heartbeat_at = 0

        supervisor._heartbeat_if_due()

        self.assertEqual(calls[-1]["url"], "http://room.local/api/live-agents/selfer/heartbeat")
        self.assertEqual(calls[-1]["method"], "POST")
        self.assertEqual(calls[-1]["payload"], {"status": "online", "preserve_status": True})

    def test_self_service_room_command_templates_round_trip_shell_escaping(self):
        config = ResidentAgentConfig(
            server="http://room.local/path with space?x=1&y=$two",
            agent_id="agent with spaces;$",
            display_name="Self Service",
            provider_kind="antigravity_cli",
            connection_kind="self_service",
            session_id="",
            endpoint="",
            auth_ref="",
            meeting_id="",
            engagement_mode="always",
            command=["agent"],
            timeout_seconds=120,
            poll_interval=0.5,
            heartbeat_interval=30,
            cooldown=5,
            max_chain_depth=2,
        )

        env = cli_module._self_service_process_env(config)
        wait_next = shlex.split(env["AGENTSASSEMBLE_WAIT_NEXT_COMMAND"])
        self.assertIn("http://room.local/path with space?x=1&y=$two", wait_next)
        self.assertIn("agent with spaces;$", wait_next)
        self.assertIn("2", wait_next)
        official_template = shlex.split(env["AGENTSASSEMBLE_OFFICIAL_REPLY_COMMAND_TEMPLATE"])
        self.assertIn("{meeting_id}", official_template)
        self.assertNotIn("", official_template)
        heartbeat_template = shlex.split(env["AGENTSASSEMBLE_HEARTBEAT_COMMAND_TEMPLATE"])
        self.assertIn("http://room.local/path with space?x=1&y=$two", heartbeat_template)
        self.assertIn("agent with spaces;$", heartbeat_template)
        self.assertIn("{status}", heartbeat_template)
        self.assertIn("--last-error={last_error}", heartbeat_template)
        self.assertIn("--last-reply-at={last_reply_at}", heartbeat_template)
        self.assertIn("--last-observed-event-id={last_observed_event_id}", heartbeat_template)
        self.assertIn("--last-observed-live-event-id={last_observed_live_event_id}", heartbeat_template)
        say_template = shlex.split(env["AGENTSASSEMBLE_SAY_COMMAND_TEMPLATE"])
        self.assertIn("{message}", say_template)
        self.assertLess(say_template.index("--"), say_template.index("{message}"))
        self.assertLess(official_template.index("--"), official_template.index("{message}"))
        say_argv = [
            "-h" if item == "{message}" else "evt-1" if item == "{source_event_id}" else "1" if item == "{auto_chain_depth}" else item
            for item in say_template
        ]
        official_argv = [
            "-h" if item == "{message}" else "meeting-1" if item == "{meeting_id}" else "live-1" if item == "{source_event_id}" else item
            for item in official_template
        ]
        heartbeat_argv = [
            item.replace("{last_error}", "--provider-failed")
            .replace("{status}", "error")
            .replace("{last_reply_at}", "2026-05-20T00:00:00+00:00")
            .replace("{last_observed_event_id}", "evt-1")
            .replace("{last_observed_live_event_id}", "live-1")
            for item in heartbeat_template
        ]
        say_args = build_parser().parse_args(say_argv[3:])
        official_args = build_parser().parse_args(official_argv[3:])
        heartbeat_args = build_parser().parse_args(heartbeat_argv[3:])
        self.assertEqual(say_args.message, ["-h"])
        self.assertEqual(official_args.message, ["-h"])
        self.assertEqual(heartbeat_args.status, "error")
        self.assertEqual(heartbeat_args.last_error, "--provider-failed")
        self.assertEqual(heartbeat_args.last_reply_at, "2026-05-20T00:00:00+00:00")
        self.assertEqual(heartbeat_args.last_observed_event_id, "evt-1")
        self.assertEqual(heartbeat_args.last_observed_live_event_id, "live-1")

    def test_heartbeat_payload_ignores_unreplaced_optional_template_placeholders(self):
        args = build_parser().parse_args(
            [
                "live-agent",
                "heartbeat",
                "--server",
                "http://room.local",
                "--agent-id",
                "selfer",
                "--status",
                "online",
                "--last-error",
                "{last_error}",
                "--last-reply-at",
                "{last_reply_at}",
                "--last-observed-event-id",
                "{last_observed_event_id}",
                "--last-observed-live-event-id",
                "{last_observed_live_event_id}",
            ]
        )

        payload = cli_module._heartbeat_payload(args)

        self.assertEqual(payload, {"status": "online"})

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

    def test_live_agent_run_group_accepts_config_path_and_tick_bound(self):
        args = build_parser().parse_args(
            ["live-agent", "run-group", "--config", "configs/live-agents.example.json", "--max-ticks", "2"]
        )

        self.assertEqual(args.live_agent_command, "run-group")
        self.assertEqual(args.config, "configs/live-agents.example.json")
        self.assertEqual(args.max_ticks, 2)

    def test_live_agent_run_group_rejects_negative_tick_bound(self):
        stderr = StringIO()
        with patch("sys.stderr", stderr):
            with self.assertRaises(SystemExit) as raised:
                build_parser().parse_args(
                    ["live-agent", "run-group", "--config", "configs/live-agents.example.json", "--max-ticks", "-1"]
                )

        self.assertEqual(raised.exception.code, 2)
        self.assertIn("value must be non-negative", stderr.getvalue())

    def test_live_agent_run_group_accepts_server_override(self):
        args = build_parser().parse_args(
            [
                "live-agent",
                "run-group",
                "--config",
                "configs/live-agents.example.json",
                "--server",
                "http://127.0.0.1:9999",
                "--max-ticks",
                "1",
            ]
        )

        self.assertEqual(args.server, "http://127.0.0.1:9999")

        with patch("agentsassemble.cli.load_group_configs", return_value=[]) as load_configs:
            stdout = StringIO()
            with patch("sys.stdout", stdout):
                exit_code = main(
                    [
                        "live-agent",
                        "run-group",
                        "--config",
                        "configs/live-agents.example.json",
                        "--server",
                        "http://127.0.0.1:9999",
                        "--max-ticks",
                        "1",
                    ]
                )

        self.assertEqual(exit_code, 0)
        load_configs.assert_called_once_with(
            Path("configs/live-agents.example.json"),
            max_ticks_override=1,
            server_override="http://127.0.0.1:9999",
        )

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

    def test_live_agent_run_group_posts_two_fake_cli_replies(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "room"
            root.mkdir()
            source_event = append_lobby_event(root, {"name": "나", "side": "mine", "message": "다들 살아있어?"})
            config_path = Path(temp_dir) / "live-agents.json"
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                config_path.write_text(
                    json.dumps(
                        {
                            "server": f"http://127.0.0.1:{server.server_port}",
                            "poll_interval": 0,
                            "cooldown": 0,
                            "max_chain_depth": 0,
                            "agents": [
                                {
                                    "agent_id": "agent-a",
                                    "display_name": "Agent A",
                                    "engagement_mode": "always",
                                    "command": [
                                        sys.executable,
                                        "-c",
                                        "import sys; sys.stdin.read(); print('Agent A reply')",
                                    ],
                                },
                                {
                                    "agent_id": "agent-b",
                                    "display_name": "Agent B",
                                    "engagement_mode": "always",
                                    "command": [
                                        sys.executable,
                                        "-c",
                                        "import sys; sys.stdin.read(); print('Agent B reply')",
                                    ],
                                },
                            ],
                        }
                    ),
                    encoding="utf-8",
                )
                stdout = StringIO()
                with patch("sys.stdout", stdout):
                    exit_code = main(["live-agent", "run-group", "--config", str(config_path), "--max-ticks", "1"])
            finally:
                server.shutdown()
                server.server_close()

            self.assertEqual(exit_code, 0)
            events = read_lobby(root)
            replies = [event for event in events if event["actor_id"] in {"agent-a", "agent-b"}]
            self.assertEqual({event["message"] for event in replies}, {"Agent A reply", "Agent B reply"})
            self.assertEqual({event["source_event_id"] for event in replies}, {source_event["id"]})
            self.assertEqual({event["auto_chain_depth"] for event in replies}, {1})
            output = stdout.getvalue()
            self.assertIn("Resident group stopped after posting 2 replies", output)
            self.assertIn("agent-a=1", output)
            self.assertIn("agent-b=1", output)

    def test_live_agent_run_group_reports_remote_bridge_setup_errors(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "live-agents.json"
            config_path.write_text(
                json.dumps(
                    {
                        "agents": [
                            {
                                "agent_id": "friend-bridge",
                                "connection_kind": "remote_bridge",
                                "endpoint": "http://friend.local:8777",
                                "auth_ref": "literal:<redacted>",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            stdout = StringIO()
            stderr = StringIO()

            with patch("sys.stdout", stdout), patch("sys.stderr", stderr):
                exit_code = main(["live-agent", "run-group", "--config", str(config_path), "--max-ticks", "1"])

            self.assertEqual(exit_code, 2)
            self.assertIn("friend-bridge", stderr.getvalue())
            self.assertIn("available auth_ref", stderr.getvalue())
            self.assertNotIn("Resident group stopped", stdout.getvalue())

    def test_live_agent_run_group_rejects_missing_local_and_live_session_commands_before_launch(self):
        configs = [
            ResidentAgentConfig(
                server="http://room.local",
                agent_id="missing-local",
                display_name="Missing Local",
                provider_kind="local_cli",
                connection_kind="local_cli",
                session_id="",
                endpoint="",
                auth_ref="",
                meeting_id="",
                engagement_mode="always",
                command=["definitely-missing-local-cli"],
                timeout_seconds=30,
                poll_interval=0,
                heartbeat_interval=30,
                cooldown=0,
                max_chain_depth=1,
                max_ticks=1,
            ),
            ResidentAgentConfig(
                server="http://room.local",
                agent_id="missing-live-session",
                display_name="Missing Live Session",
                provider_kind="local_cli",
                connection_kind="live_session",
                session_id="",
                endpoint="",
                auth_ref="",
                meeting_id="",
                engagement_mode="always",
                command=["definitely-missing-live-session-cli"],
                timeout_seconds=30,
                poll_interval=0,
                heartbeat_interval=30,
                cooldown=0,
                max_chain_depth=1,
                max_ticks=1,
            ),
        ]
        constructed = []

        class RecordingRunner:
            def __init__(self, *args, **kwargs):
                constructed.append((args, kwargs))

            def run(self):
                return 0

        stdout = StringIO()
        stderr = StringIO()
        with (
            patch("agentsassemble.cli.load_group_configs", return_value=configs),
            patch("agentsassemble.cli.LiveAgentRunner", RecordingRunner),
            patch("sys.stdout", stdout),
            patch("sys.stderr", stderr),
        ):
            exit_code = main(["live-agent", "run-group", "--config", "ignored.json"])

        self.assertEqual(exit_code, 2)
        self.assertEqual(constructed, [])
        self.assertIn("missing-local", stderr.getvalue())
        self.assertIn("missing-live-session", stderr.getvalue())
        self.assertIn("Command not found", stderr.getvalue())
        self.assertNotIn("Resident group stopped", stdout.getvalue())

    def test_live_agent_run_group_rejects_missing_self_service_python_script_before_registration(self):
        configs = [
            ResidentAgentConfig(
                server="http://room.local",
                agent_id="missing-self-service",
                display_name="Missing Self Service",
                provider_kind="local_cli",
                connection_kind="self_service",
                session_id="",
                endpoint="",
                auth_ref="",
                meeting_id="",
                engagement_mode="always",
                command=[sys.executable, "scripts/missing_self_service.py"],
                timeout_seconds=30,
                poll_interval=0,
                heartbeat_interval=30,
                cooldown=0,
                max_chain_depth=1,
                max_ticks=1,
            )
        ]
        constructed = []

        class RecordingSupervisor:
            def __init__(self, *args, **kwargs):
                constructed.append((args, kwargs))

            def run(self):
                return 0

        with tempfile.TemporaryDirectory() as temp_dir:
            previous_cwd = Path.cwd()
            os.chdir(temp_dir)
            try:
                stdout = StringIO()
                stderr = StringIO()
                with (
                    patch("agentsassemble.cli.load_group_configs", return_value=configs),
                    patch("agentsassemble.cli._SelfServiceResidentSupervisor", RecordingSupervisor),
                    patch(
                        "agentsassemble.live_agent_preflight.shutil.which",
                        side_effect=lambda command: command if command == sys.executable else None,
                    ),
                    patch("sys.stdout", stdout),
                    patch("sys.stderr", stderr),
                ):
                    exit_code = main(["live-agent", "run-group", "--config", "ignored.json"])
            finally:
                os.chdir(previous_cwd)

        self.assertEqual(exit_code, 2)
        self.assertEqual(constructed, [])
        self.assertIn("missing-self-service", stderr.getvalue())
        self.assertIn("Command script not found: scripts/missing_self_service.py", stderr.getvalue())
        self.assertNotIn("Self-service resident agent stopped", stdout.getvalue())

    def test_live_agent_run_group_rejects_codex_safety_probe_failure_before_launch(self):
        configs = [
            ResidentAgentConfig(
                server="http://room.local",
                agent_id="codex-live",
                display_name="Codex Live",
                provider_kind="codex_live_session",
                connection_kind="live_session",
                session_id="",
                endpoint="",
                auth_ref="",
                meeting_id="",
                engagement_mode="always",
                command=["codex"],
                timeout_seconds=30,
                poll_interval=0,
                heartbeat_interval=30,
                cooldown=0,
                max_chain_depth=1,
                max_ticks=1,
            )
        ]
        constructed = []

        class RecordingRunner:
            def __init__(self, *args, **kwargs):
                constructed.append((args, kwargs))

            def run(self):
                return 0

        stdout = StringIO()
        stderr = StringIO()
        with (
            patch("agentsassemble.cli.load_group_configs", return_value=configs),
            patch(
                "agentsassemble.cli.resident_config_setup_error",
                return_value="Codex command does not accept required live-session safety flags.",
            ),
            patch("agentsassemble.cli.LiveAgentRunner", RecordingRunner),
            patch("sys.stdout", stdout),
            patch("sys.stderr", stderr),
        ):
            exit_code = main(["live-agent", "run-group", "--config", "ignored.json"])

        self.assertEqual(exit_code, 2)
        self.assertEqual(constructed, [])
        self.assertIn("codex-live", stderr.getvalue())
        self.assertIn("required live-session safety flags", stderr.getvalue())
        self.assertNotIn("Resident group stopped", stdout.getvalue())

    def test_live_agent_run_group_rejects_duplicate_agent_ids_before_launch(self):
        configs = [
            ResidentAgentConfig(
                server="http://room.local",
                agent_id="duplicate-agent",
                display_name="Duplicate A",
                provider_kind="local_cli",
                connection_kind="local_cli",
                session_id="",
                endpoint="",
                auth_ref="",
                meeting_id="",
                engagement_mode="always",
                command=[sys.executable],
                timeout_seconds=30,
                poll_interval=0,
                heartbeat_interval=30,
                cooldown=0,
                max_chain_depth=1,
                max_ticks=1,
            ),
            ResidentAgentConfig(
                server="http://room.local",
                agent_id="duplicate-agent",
                display_name="Duplicate B",
                provider_kind="local_cli",
                connection_kind="local_cli",
                session_id="",
                endpoint="",
                auth_ref="",
                meeting_id="",
                engagement_mode="always",
                command=[sys.executable],
                timeout_seconds=30,
                poll_interval=0,
                heartbeat_interval=30,
                cooldown=0,
                max_chain_depth=1,
                max_ticks=1,
            ),
        ]
        constructed = []

        class RecordingRunner:
            def __init__(self, *args, **kwargs):
                constructed.append((args, kwargs))

            def run(self):
                return 0

        stderr = StringIO()
        with (
            patch("agentsassemble.cli.load_group_configs", return_value=configs),
            patch("agentsassemble.cli.LiveAgentRunner", RecordingRunner),
            patch("sys.stdout", StringIO()),
            patch("sys.stderr", stderr),
        ):
            exit_code = main(["live-agent", "run-group", "--config", "ignored.json"])

        self.assertEqual(exit_code, 2)
        self.assertEqual(constructed, [])
        self.assertIn("duplicate-agent", stderr.getvalue())
        self.assertIn("Duplicate agent id", stderr.getvalue())

    def test_live_agent_run_group_does_not_register_any_agent_when_setup_fails(self):
        configs = [
            ResidentAgentConfig(
                server="http://room.local",
                agent_id="would-register",
                display_name="Would Register",
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
                max_ticks=1,
            ),
            ResidentAgentConfig(
                server="http://room.local",
                agent_id="bad-bridge",
                display_name="Bad Bridge",
                provider_kind="claude_code",
                connection_kind="remote_bridge",
                session_id="",
                endpoint="http://friend.local:8777",
                auth_ref="literal:<redacted>",
                meeting_id="",
                engagement_mode="always",
                command=[],
                timeout_seconds=30,
                poll_interval=0,
                heartbeat_interval=30,
                cooldown=0,
                max_chain_depth=1,
                max_ticks=1,
            ),
        ]
        constructed = []

        class RecordingRunner:
            def __init__(self, *args, **kwargs):
                constructed.append(args)

            def run(self):
                return 0

        stderr = StringIO()
        with (
            patch("agentsassemble.cli.load_group_configs", return_value=configs),
            patch("agentsassemble.cli.LiveAgentRunner", RecordingRunner),
            patch("sys.stdout", StringIO()),
            patch("sys.stderr", stderr),
        ):
            exit_code = main(["live-agent", "run-group", "--config", "ignored.json"])

        self.assertEqual(exit_code, 2)
        self.assertEqual(constructed, [])
        self.assertIn("bad-bridge", stderr.getvalue())

    def test_local_cli_resident_command_runner_close_terminates_active_process(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            pid_path = Path(temp_dir) / "child.pid"
            runner = cli_module._LocalCliCommandRunner()
            errors = []

            def invoke_runner():
                try:
                    runner(
                        [
                            sys.executable,
                            "-u",
                            "-c",
                            (
                                "import os, pathlib, sys, time; "
                                "pathlib.Path(sys.argv[1]).write_text(str(os.getpid()), encoding='utf-8'); "
                                "time.sleep(30)"
                            ),
                            str(pid_path),
                        ],
                        "prompt",
                        timeout_seconds=30,
                    )
                except Exception as error:
                    errors.append(error)

            thread = threading.Thread(target=invoke_runner)
            thread.start()
            try:
                deadline = time.time() + 5
                while time.time() < deadline and not pid_path.exists():
                    time.sleep(0.01)
                self.assertTrue(pid_path.exists())

                runner.close()
                thread.join(timeout=3)
            finally:
                runner.close()
                thread.join(timeout=1)

            self.assertFalse(thread.is_alive())
            self.assertTrue(errors)

    def test_local_cli_terminate_falls_back_to_process_kill_without_sigkill(self):
        class FakeSignal:
            SIGTERM = 15

        class TimeoutProcess:
            def __init__(self):
                self.returncode = None
                self.terminated = False
                self.killed = False
                self.waits = 0

            def poll(self):
                return self.returncode

            def terminate(self):
                self.terminated = True

            def wait(self, timeout=None):
                del timeout
                self.waits += 1
                if self.waits == 1:
                    raise cli_module.subprocess.TimeoutExpired(["fake"], 1)
                self.returncode = -9
                return self.returncode

            def kill(self):
                self.killed = True
                self.returncode = -9

        process = TimeoutProcess()

        with (
            patch("agentsassemble.cli._supports_process_groups", return_value=False),
            patch("agentsassemble.cli.signal", FakeSignal),
        ):
            cli_module._terminate_process(process)

        self.assertTrue(process.terminated)
        self.assertTrue(process.killed)

    @unittest.skipUnless(cli_module._supports_process_groups(), "requires POSIX process-group support")
    def test_local_cli_resident_command_runner_close_terminates_child_processes(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            child_pid_path = Path(temp_dir) / "grandchild.pid"
            runner = cli_module._LocalCliCommandRunner()
            errors = []

            def invoke_runner():
                try:
                    runner(
                        [
                            sys.executable,
                            "-u",
                            "-c",
                            (
                                "import pathlib, subprocess, sys, time; "
                                "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)']); "
                                "pathlib.Path(sys.argv[1]).write_text(str(child.pid), encoding='utf-8'); "
                                "time.sleep(30)"
                            ),
                            str(child_pid_path),
                        ],
                        "prompt",
                        timeout_seconds=30,
                    )
                except Exception as error:
                    errors.append(error)

            thread = threading.Thread(target=invoke_runner)
            thread.start()
            child_pid = None
            child_alive_after_close = None
            try:
                deadline = time.time() + 5
                while time.time() < deadline and not child_pid_path.exists():
                    time.sleep(0.01)
                self.assertTrue(child_pid_path.exists())
                child_pid = int(child_pid_path.read_text(encoding="utf-8"))

                runner.close()
                thread.join(timeout=3)
                child_alive_after_close = not _wait_for_pid_exit(child_pid)
            finally:
                if child_pid is not None and _pid_exists(child_pid):
                    try:
                        _kill_pid(child_pid)
                    except ProcessLookupError:
                        pass
                runner.close()
                thread.join(timeout=1)

            self.assertFalse(thread.is_alive())
            self.assertFalse(child_alive_after_close)
            self.assertTrue(errors)

    @unittest.skipUnless(cli_module._supports_process_groups(), "requires POSIX process-group support")
    def test_local_cli_resident_command_runner_timeout_terminates_child_processes(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            child_pid_path = Path(temp_dir) / "timeout-grandchild.pid"
            runner = cli_module._LocalCliCommandRunner()
            errors = []

            def invoke_runner():
                try:
                    runner(
                        [
                            sys.executable,
                            "-u",
                            "-c",
                            (
                                "import pathlib, subprocess, sys, time; "
                                "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)']); "
                                "pathlib.Path(sys.argv[1]).write_text(str(child.pid), encoding='utf-8'); "
                                "time.sleep(30)"
                            ),
                            str(child_pid_path),
                        ],
                        "prompt",
                        timeout_seconds=0.2,
                    )
                except cli_module.subprocess.TimeoutExpired as error:
                    errors.append(error)

            thread = threading.Thread(target=invoke_runner)
            thread.start()
            child_pid = None
            child_alive_after_timeout = None
            try:
                deadline = time.time() + 5
                while time.time() < deadline and not child_pid_path.exists():
                    time.sleep(0.01)
                self.assertTrue(child_pid_path.exists())
                child_pid = int(child_pid_path.read_text(encoding="utf-8"))

                thread.join(timeout=5)
                child_alive_after_timeout = not _wait_for_pid_exit(child_pid)
            finally:
                if child_pid is not None and _pid_exists(child_pid):
                    try:
                        _kill_pid(child_pid)
                    except ProcessLookupError:
                        pass
                runner.close()
                thread.join(timeout=1)

            self.assertFalse(thread.is_alive())
            self.assertTrue(errors)
            self.assertFalse(child_alive_after_timeout)

    def test_local_cli_resident_command_runner_terminates_child_on_interruption(self):
        class InterruptingProcess:
            def __init__(self):
                self.returncode = None
                self.terminated = False
                self.killed = False

            def communicate(self, input=None, timeout=None):
                del input, timeout
                raise KeyboardInterrupt()

            def poll(self):
                return self.returncode

            def terminate(self):
                self.terminated = True
                self.returncode = -15

            def wait(self, timeout=None):
                del timeout
                return self.returncode

            def kill(self):
                self.killed = True
                self.returncode = -9

        process = InterruptingProcess()
        with patch("agentsassemble.cli.subprocess.Popen", return_value=process):
            runner = cli_module._LocalCliCommandRunner()
            with self.assertRaises(KeyboardInterrupt):
                runner(["fake-provider"], "prompt", timeout_seconds=30)

        self.assertTrue(process.terminated)
        self.assertFalse(process.killed)

    def test_resident_shutdown_signal_handler_closes_and_raises_keyboard_interrupt(self):
        sigterm = getattr(signal, "SIGTERM", None)
        if sigterm is None:
            self.skipTest("SIGTERM is unavailable on this platform")

        installed_handlers = {}
        previous_handlers = {}
        restored_handlers = []

        def fake_signal(signum, handler):
            previous = previous_handlers.setdefault(signum, object())
            if signum in installed_handlers:
                restored_handlers.append((signum, handler))
            else:
                installed_handlers[signum] = handler
            return previous

        closed = []
        with patch("agentsassemble.cli.signal.signal", side_effect=fake_signal):
            restore = cli_module._install_resident_shutdown_signal_handlers(lambda: closed.append(True))
            self.assertIn(sigterm, installed_handlers)
            with self.assertRaises(KeyboardInterrupt):
                installed_handlers[sigterm](sigterm, None)
            restore()

        self.assertEqual(closed, [True])
        self.assertIn((sigterm, previous_handlers[sigterm]), restored_handlers)

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

    def test_live_agent_run_group_shutdown_signal_closes_active_runners(self):
        configs = [
            ResidentAgentConfig(
                server="http://room.local",
                agent_id="signal-agent",
                display_name="Signal Agent",
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
            ),
            ResidentAgentConfig(
                server="http://room.local",
                agent_id="sibling-blocked",
                display_name="Sibling Blocked",
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
            ),
        ]
        sibling_started = threading.Event()
        sibling_closed_while_running = threading.Event()
        installed_shutdown = {}
        restored = threading.Event()

        class CloseRecordingRunner:
            def __init__(self, agent_id):
                self.agent_id = agent_id
                self.closed = False

            def close(self):
                self.closed = True

        class SignalInterruptingRunner:
            def __init__(self, config, *, command_runner, **kwargs):
                del kwargs
                self.config = config
                self.command_runner = command_runner

            def run(self):
                if self.config.agent_id == "signal-agent":
                    if not sibling_started.wait(timeout=1):
                        raise AssertionError("secondary runner did not start")
                    installed_shutdown["callback"]()
                    raise KeyboardInterrupt()
                sibling_started.set()
                deadline = time.time() + 0.5
                while time.time() < deadline:
                    if self.command_runner.closed:
                        sibling_closed_while_running.set()
                        return 0
                    time.sleep(0.01)
                return 0

        def command_runner_for_config(config):
            return CloseRecordingRunner(config.agent_id)

        def install_shutdown_handler(callback):
            installed_shutdown["callback"] = callback
            return lambda: restored.set()

        with (
            patch("agentsassemble.cli.load_group_configs", return_value=configs),
            patch("agentsassemble.cli.resident_config_setup_error", return_value=""),
            patch("agentsassemble.cli._command_runner_for_config", side_effect=command_runner_for_config),
            patch("agentsassemble.cli._install_resident_shutdown_signal_handlers", side_effect=install_shutdown_handler, create=True),
            patch("agentsassemble.cli.LiveAgentRunner", SignalInterruptingRunner),
            patch("sys.stdout", StringIO()),
            patch("sys.stderr", StringIO()),
        ):
            exit_code = main(["live-agent", "run-group", "--config", "ignored.json"])

        self.assertEqual(exit_code, 0)
        self.assertTrue(sibling_closed_while_running.is_set())
        self.assertTrue(restored.is_set())

    def test_live_agent_run_group_suppresses_secondary_errors_after_shutdown(self):
        configs = [
            ResidentAgentConfig(
                server="http://room.local",
                agent_id="primary-interrupt",
                display_name="Primary Interrupt",
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
            ),
            ResidentAgentConfig(
                server="http://room.local",
                agent_id="secondary-stop",
                display_name="Secondary Stop",
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
            ),
        ]

        class ShutdownAwareRunner:
            def __init__(self, config, *, stop_event, **kwargs):
                del kwargs
                self.config = config
                self.stop_event = stop_event

            def run(self):
                if self.config.agent_id == "primary-interrupt":
                    raise KeyboardInterrupt()
                while not self.stop_event.is_set():
                    time.sleep(0.01)
                raise RuntimeError("secondary closed during shutdown")

        stderr = StringIO()
        with (
            patch("agentsassemble.cli.load_group_configs", return_value=configs),
            patch("agentsassemble.cli.resident_config_setup_error", return_value=""),
            patch("agentsassemble.cli.LiveAgentRunner", ShutdownAwareRunner),
            patch("sys.stdout", StringIO()),
            patch("sys.stderr", stderr),
        ):
            exit_code = main(["live-agent", "run-group", "--config", "ignored.json"])

        self.assertEqual(exit_code, 0)
        self.assertNotIn("secondary closed during shutdown", stderr.getvalue())

    def test_live_agent_run_group_reports_worker_system_exit_as_error(self):
        configs = [
            ResidentAgentConfig(
                server="http://room.local",
                agent_id="exiting-agent",
                display_name="Exiting Agent",
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
        ]

        class ExitingRunner:
            def __init__(self, *args, **kwargs):
                del args, kwargs

            def run(self):
                raise SystemExit("runner exited unexpectedly")

        stderr = StringIO()
        with (
            patch("agentsassemble.cli.load_group_configs", return_value=configs),
            patch("agentsassemble.cli.resident_config_setup_error", return_value=""),
            patch("agentsassemble.cli.LiveAgentRunner", ExitingRunner),
            patch("sys.stdout", StringIO()),
            patch("sys.stderr", stderr),
        ):
            exit_code = main(["live-agent", "run-group", "--config", "ignored.json"])

        self.assertEqual(exit_code, 2)
        self.assertIn("exiting-agent: runner exited unexpectedly", stderr.getvalue())

    def test_live_agent_run_group_closes_sibling_runners_after_worker_keyboard_interrupt(self):
        configs = [
            ResidentAgentConfig(
                server="http://room.local",
                agent_id="interrupting-agent",
                display_name="Interrupting Agent",
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
            ),
            ResidentAgentConfig(
                server="http://room.local",
                agent_id="sibling-blocked",
                display_name="Sibling Blocked",
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
            ),
        ]
        runners = {}
        sibling_started = threading.Event()
        sibling_closed_while_running = threading.Event()

        class CloseRecordingRunner:
            def __init__(self, agent_id):
                self.agent_id = agent_id
                self.closed = False

            def close(self):
                self.closed = True

        class InterruptingRunner:
            def __init__(self, config, *, command_runner, **kwargs):
                del kwargs
                self.config = config
                self.command_runner = command_runner

            def run(self):
                if self.config.agent_id == "interrupting-agent":
                    if not sibling_started.wait(timeout=1):
                        raise AssertionError("secondary runner did not start")
                    raise KeyboardInterrupt()
                sibling_started.set()
                deadline = time.time() + 0.5
                while time.time() < deadline:
                    if self.command_runner.closed:
                        sibling_closed_while_running.set()
                        return 0
                    time.sleep(0.01)
                return 0

        def command_runner_for_config(config):
            runner = CloseRecordingRunner(config.agent_id)
            runners[config.agent_id] = runner
            return runner

        with (
            patch("agentsassemble.cli.load_group_configs", return_value=configs),
            patch("agentsassemble.cli.resident_config_setup_error", return_value=""),
            patch("agentsassemble.cli._command_runner_for_config", side_effect=command_runner_for_config),
            patch("agentsassemble.cli.LiveAgentRunner", InterruptingRunner),
            patch("sys.stdout", StringIO()),
            patch("sys.stderr", StringIO()),
        ):
            exit_code = main(["live-agent", "run-group", "--config", "ignored.json"])

        self.assertEqual(exit_code, 0)
        self.assertTrue(sibling_closed_while_running.is_set())

    def test_live_agent_run_group_keeps_sibling_runner_after_primary_failure(self):
        configs = [
            ResidentAgentConfig(
                server="http://room.local",
                agent_id="primary-error",
                display_name="Primary Error",
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
            ),
            ResidentAgentConfig(
                server="http://room.local",
                agent_id="secondary-blocked",
                display_name="Secondary Blocked",
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
            ),
        ]
        runners = {}
        sibling_started = threading.Event()
        sibling_closed_while_running = threading.Event()
        sibling_finished = threading.Event()

        class CloseRecordingRunner:
            def __init__(self, agent_id):
                self.agent_id = agent_id
                self.closed = False

            def close(self):
                self.closed = True

        class BlockingSiblingRunner:
            def __init__(self, config, *, command_runner, **kwargs):
                del kwargs
                self.config = config
                self.command_runner = command_runner

            def run(self):
                if self.config.agent_id == "primary-error":
                    if not sibling_started.wait(timeout=1):
                        raise AssertionError("secondary runner did not start")
                    raise RuntimeError("primary boom")
                sibling_started.set()
                deadline = time.time() + 0.5
                while time.time() < deadline:
                    if self.command_runner.closed:
                        sibling_closed_while_running.set()
                        return 0
                    time.sleep(0.01)
                sibling_finished.set()
                return 3

        def command_runner_for_config(config):
            runner = CloseRecordingRunner(config.agent_id)
            runners[config.agent_id] = runner
            return runner

        stderr = StringIO()
        with (
            patch("agentsassemble.cli.load_group_configs", return_value=configs),
            patch("agentsassemble.cli.resident_config_setup_error", return_value=""),
            patch("agentsassemble.cli._command_runner_for_config", side_effect=command_runner_for_config),
            patch("agentsassemble.cli.LiveAgentRunner", BlockingSiblingRunner),
            patch("sys.stdout", StringIO()),
            patch("sys.stderr", stderr),
        ):
            exit_code = main(["live-agent", "run-group", "--config", "ignored.json"])

        self.assertEqual(exit_code, 2)
        self.assertIn("primary-error: primary boom", stderr.getvalue())
        self.assertTrue(sibling_finished.is_set())
        self.assertFalse(sibling_closed_while_running.is_set())

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


if __name__ == "__main__":
    unittest.main()
