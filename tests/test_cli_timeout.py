import unittest
import json
import os
import signal
import sys
import tempfile
import threading
import time
import urllib.error
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import StringIO
from unittest.mock import patch

from agentsassemble import cli as cli_module
from agentsassemble.cli import build_parser, main
from agentsassemble.gui import _make_handler, append_lobby_event, read_lobby
from agentsassemble.live_agent_runner import ResidentAgentConfig
from agentsassemble.live_agent_smoke import LiveAgentSmokeFailed


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
            },
        )
        self.assertIn("claude-code-live: error", stdout.getvalue())

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
        self.assertIn("post_recover_reply_count=3", output)
        self.assertIn("soak_cycle_count=2", output)
        self.assertIn("soak_reply_count=6", output)
        self.assertIn("soak_check_statuses=ready,ready", output)

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
            },
            "connections": {
                "expected": 2,
                "connected": 1,
                "attention": ["crew:friend-b:missing"],
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
        self.assertIn("process attention: crashed-group, orphan-group", output)
        self.assertIn("connections: 1 connected / 2 expected", output)
        self.assertIn("connection attention: crew:friend-b:missing", output)

    def test_live_agent_health_can_emit_json_and_fail_on_degraded(self):
        payload = {"status": "degraded", "agents": {"counts": {}, "attention": []}, "processes": {"counts": {}, "attention": []}}
        stdout = StringIO()
        with patch("agentsassemble.cli._request_json", return_value=payload):
            with patch("sys.stdout", stdout):
                exit_code = main(["live-agent", "health", "--server", "http://room.local", "--json", "--fail-on-degraded"])

        self.assertEqual(exit_code, 1)
        self.assertEqual(json.loads(stdout.getvalue())["status"], "degraded")

    def test_live_agent_health_fail_on_degraded_allows_ok_status(self):
        payload = {"status": "ok", "agents": {"counts": {}, "attention": []}, "processes": {"counts": {}, "attention": []}}
        stdout = StringIO()
        with patch("agentsassemble.cli._request_json", return_value=payload):
            with patch("sys.stdout", stdout):
                exit_code = main(["live-agent", "health", "--server", "http://room.local", "--fail-on-degraded"])

        self.assertEqual(exit_code, 0)
        self.assertIn("status: ok", stdout.getvalue())

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
        self.assertTrue(args.as_json)

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
                "--json",
            ]
        )

        self.assertEqual(args.live_agent_command, "restart-session")
        self.assertEqual(args.meeting_id, "resident-m1")
        self.assertEqual(args.group_id, "resident-main")
        self.assertEqual(args.connect_timeout, 7.0)
        self.assertTrue(args.as_json)

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
                "--json",
            ]
        )

        self.assertEqual(args.live_agent_command, "recover-session")
        self.assertEqual(args.meeting_id, "resident-m1")
        self.assertEqual(args.group_id, "resident-main")
        self.assertEqual(args.connect_timeout, 7.0)
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
            "health": {"status": "ok", "agents": {"attention": []}, "processes": {"attention": []}},
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
        self.assertIn("session smoke: ok session-smoke (3/3 replies, post-recover 3/3, soak 6/6 over 2 cycles)", output)

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
        self.assertIn("session smoke: ok session-smoke (3/3 replies, post-recover 3/3)", output)

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
                            "event_type": "started",
                            "timestamp": "2026-05-17T12:00:00+00:00",
                            "group_id": "crew",
                            "status": "running",
                            "pid": 1234,
                            "restart_count": 1,
                        }
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
        self.assertNotIn("command", output)
        self.assertNotIn("auth", output)
        stopped_line = next(line for line in output.splitlines() if line.startswith("stopped-crew:"))
        self.assertIn("stopped-crew: stopped", stopped_line)
        self.assertNotIn("next restart", stopped_line)

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

    def test_live_agent_processes_stop_restart_and_recover_quote_group_id(self):
        stop_payload = {"group": {"group_id": "crew one", "status": "stopped"}}
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
        self.assertIn("Stopped crew one (stopped)", output)
        self.assertIn("Restarted crew one (pid 5678)", output)
        self.assertIn("Recovered crew one from unknown (pid 6789)", output)

    def test_live_agent_processes_stop_running_posts_bulk_endpoint(self):
        payload = {
            "result": {
                "stopped_count": 2,
                "failed_count": 0,
                "skipped_count": 1,
                "stopped": [
                    {"group_id": "crew-a", "status": "stopped"},
                    {"group_id": "crew-b", "status": "stopped"},
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
        room_payload = {"lobby_events": [{"name": "나", "message": "방 상태 어때?"}]}
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
            {"message": "Claude Code Live 응답", "kind": "message"},
        )
        run_delegate.assert_called_once()
        self.assertEqual(run_delegate.call_args.args[0], ["claude", "-p"])
        self.assertIn("방 상태 어때?", run_delegate.call_args.args[1])
        self.assertIn("AgentsAssemble", run_delegate.call_args.args[1])
        self.assertNotIn("AgentCouncil", run_delegate.call_args.args[1])
        self.assertIn("Posted evt1", stdout.getvalue())

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

    def test_live_agent_run_group_suppresses_secondary_shutdown_errors(self):
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
                if self.config.agent_id == "primary-error":
                    raise RuntimeError("primary boom")
                while not self.stop_event.is_set():
                    time.sleep(0.01)
                raise RuntimeError("secondary closed during shutdown")

        stderr = StringIO()
        with (
            patch("agentsassemble.cli.load_group_configs", return_value=configs),
            patch("agentsassemble.cli.LiveAgentRunner", ShutdownAwareRunner),
            patch("sys.stdout", StringIO()),
            patch("sys.stderr", stderr),
        ):
            exit_code = main(["live-agent", "run-group", "--config", "ignored.json"])

        self.assertEqual(exit_code, 2)
        self.assertIn("primary-error: primary boom", stderr.getvalue())
        self.assertNotIn("secondary closed during shutdown", stderr.getvalue())

    def test_live_agent_run_group_closes_sibling_runners_after_primary_failure(self):
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
                return 0

        def command_runner_for_config(config):
            runner = CloseRecordingRunner(config.agent_id)
            runners[config.agent_id] = runner
            return runner

        stderr = StringIO()
        with (
            patch("agentsassemble.cli.load_group_configs", return_value=configs),
            patch("agentsassemble.cli._command_runner_for_config", side_effect=command_runner_for_config),
            patch("agentsassemble.cli.LiveAgentRunner", BlockingSiblingRunner),
            patch("sys.stdout", StringIO()),
            patch("sys.stderr", stderr),
        ):
            exit_code = main(["live-agent", "run-group", "--config", "ignored.json"])

        self.assertEqual(exit_code, 2)
        self.assertIn("primary-error: primary boom", stderr.getvalue())
        self.assertTrue(sibling_closed_while_running.is_set())

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
