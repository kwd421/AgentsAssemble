import unittest
import json
import sys
import tempfile
import threading
from pathlib import Path
from http.server import ThreadingHTTPServer
from io import StringIO
from unittest.mock import patch

from agentsassemble.cli import build_parser, main
from agentsassemble.gui import _make_handler


class CliTimeoutSessionStartTests(unittest.TestCase):

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
                "--legacy-internal",
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
                "--legacy-internal",
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
                "--legacy-internal",
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
                        "--legacy-internal",
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
                            "--legacy-internal",
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
                                "--legacy-internal",
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
                        "--legacy-internal",
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
                        "--legacy-internal",
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
                        "--legacy-internal",
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
                        "--legacy-internal",
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
                        "--legacy-internal",
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
                        "--legacy-internal",
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
                        "--legacy-internal",
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
                        "--legacy-internal",
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
                        "--legacy-internal",
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
                "--legacy-internal",
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
                        "--legacy-internal",
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
                        "--legacy-internal",
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
                            "--legacy-internal",
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
