import unittest
from agentsassemble import cli as cli_module
from io import StringIO
from types import SimpleNamespace
from unittest.mock import patch

from agentsassemble.cli import build_parser, main
from agentsassemble.web.cli_errors import CliHttpError
from agentsassemble.cli_legacy_live_agent_sessions import (
    LegacySessionCliRuntime,
    wait_for_session_after_control,
)


class CliTimeoutSessionEnsureTests(unittest.TestCase):

    def test_control_wait_requires_identity_and_marks_timeout(self):
        args = SimpleNamespace(
            meeting_id="",
            group_id="",
            server="http://room.local",
            wait_timeout=1.0,
            wait_poll_interval=0.1,
        )
        runtime = LegacySessionCliRuntime(
            request_json=lambda *_args, **_kwargs: (_ for _ in ()).throw(TimeoutError()),
            server_url=lambda server, path: f"{server}{path}",
            operation_http_timeout=lambda *_args, **_kwargs: 1.0,
            monotonic=lambda: 0.0,
            sleep=lambda _seconds: None,
            is_wait_timeout=lambda error: isinstance(error, TimeoutError),
            session_ensure_action=lambda _payload: "none",
        )
        with self.assertRaisesRegex(ValueError, "requires meeting_id and group_id"):
            wait_for_session_after_control(args, {"status": "starting"}, runtime=runtime)

        response = wait_for_session_after_control(
            args,
            {"status": "starting", "meeting_id": "room-a", "group_id": "group-a"},
            runtime=runtime,
        )
        self.assertEqual(response["wait_status"], "timeout")

    def test_live_agent_ensure_session_parser_accepts_session_configs_and_wait_options(self):
        args = build_parser().parse_args(
            [
                "live-agent",
                "--legacy-internal",
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
                        "--legacy-internal",
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
                        "--legacy-internal",
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
                        "--legacy-internal",
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
            side_effect=[
                CliHttpError("Meeting resident-m1 was not found.", status_code=404),
                start_response,
                ready_snapshot,
            ],
        ) as request_json:
            with patch("sys.stdout", stdout):
                exit_code = main(
                    [
                        "live-agent",
                        "--legacy-internal",
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

    def test_live_agent_ensure_session_does_not_parse_error_text_as_not_found(self):
        stderr = StringIO()
        with patch(
            "agentsassemble.cli._request_json",
            side_effect=CliHttpError("storage failure: meeting was not found", status_code=500),
        ) as request_json:
            with patch("sys.stderr", stderr):
                exit_code = main(
                    [
                        "live-agent",
                        "--legacy-internal",
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

        self.assertEqual(exit_code, 2)
        self.assertEqual(request_json.call_count, 1)
        self.assertIn("storage failure", stderr.getvalue())

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
                        "--legacy-internal",
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
                        "--legacy-internal",
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
                        "--legacy-internal",
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
                        "--legacy-internal",
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
                        "--legacy-internal",
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
                                "--legacy-internal",
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
                            "--legacy-internal",
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
                        "--legacy-internal",
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
