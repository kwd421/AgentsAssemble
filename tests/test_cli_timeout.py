import unittest
import json
import sys
import tempfile
import threading
import time
import urllib.error
from pathlib import Path
from http.server import ThreadingHTTPServer
from io import StringIO
from unittest.mock import patch

from agentsassemble.cli import build_parser, main
from agentsassemble.gui import _make_handler, append_lobby_event, read_lobby
from agentsassemble.live_agent_smoke import LiveAgentSmokeFailed


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
                "--json",
            ]
        )

        self.assertEqual(args.live_agent_command, "doctor")
        self.assertEqual(args.server, "http://room.local")
        self.assertEqual(args.group_id, "doctor-smoke")
        self.assertEqual(args.timeout, 8.0)
        self.assertTrue(args.as_json)

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
                "local",
                "--probe-timeout",
                "0.75",
                "--json",
            ]
        )

        self.assertEqual(args.command, "providers")
        self.assertEqual(args.providers_command, "health")
        self.assertEqual(args.config, "configs/http-providers.example.json")
        self.assertEqual(args.probe_mode, "local")
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
                    ]
                )

        self.assertEqual(exit_code, 0)
        request_json.assert_called_once_with(
            "http://room.local/api/live-agent-readiness",
            method="POST",
            payload={"group_id": "doctor-smoke", "timeout": 8.0},
        )
        output = stdout.getvalue()
        self.assertIn("readiness: ready", output)
        self.assertIn("health: ok", output)
        self.assertIn("smoke: ok doctor-smoke", output)

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
                {"smoke local_cli ok", "smoke live_session ok"},
            )
            self.assertNotEqual(payload["source_event_id"], old_event["id"])
            self.assertEqual({reply["source_event_id"] for reply in payload["replies"]}, {payload["source_event_id"]})
            events = read_lobby(root)
            self.assertEqual(
                {event["message"] for event in events if event.get("actor_id") in payload["agent_ids"]},
                {"smoke local_cli ok", "smoke live_session ok"},
            )
            processes = json.loads((root / "live-agent-runs" / "processes.json").read_text(encoding="utf-8"))
            group = next(item for item in processes["groups"] if item["group_id"] == "operator-smoke")
            self.assertEqual(group["status"], "stopped")

    def test_live_agent_smoke_returns_one_for_reached_server_failure(self):
        stderr = StringIO()
        with patch("agentsassemble.cli.run_live_agent_smoke", side_effect=LiveAgentSmokeFailed("missing replies")):
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
                    "config_path": "configs/live-agents.example.json",
                },
                {"group_id": "stopped-crew", "status": "stopped", "pid": None, "config_path": "fake.json"},
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
        self.assertIn("stopped-crew: stopped", output)

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

    def test_live_agent_processes_stop_and_restart_quote_group_id(self):
        stop_payload = {"group": {"group_id": "crew one", "status": "stopped"}}
        restart_payload = {"group": {"group_id": "crew one", "status": "running", "pid": 5678}}
        stdout = StringIO()
        with patch("agentsassemble.cli._request_json", side_effect=[stop_payload, restart_payload]) as request_json:
            with patch("sys.stdout", stdout):
                stop_exit = main(["live-agent", "processes", "stop", "crew one", "--server", "http://room.local"])
                restart_exit = main(["live-agent", "processes", "restart", "crew one", "--server", "http://room.local"])

        self.assertEqual(stop_exit, 0)
        self.assertEqual(restart_exit, 0)
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
        output = stdout.getvalue()
        self.assertIn("Stopped crew one (stopped)", output)
        self.assertIn("Restarted crew one (pid 5678)", output)

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
