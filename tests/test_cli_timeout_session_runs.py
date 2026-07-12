import unittest
import json
import urllib.parse
from io import StringIO
from unittest.mock import patch

from agentsassemble.cli import build_parser, main


class CliTimeoutSessionRunsTests(unittest.TestCase):

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

    def test_live_agent_session_runs_retry_now_parses_current_real_provider_approval(self):
        args = build_parser().parse_args(
            [
                "live-agent",
                "session-runs",
                "retry-now",
                "--server",
                "http://room.local",
                "--run-id",
                "retry-later",
                "--approve-real-providers",
            ]
        )

        self.assertEqual(args.live_agent_command, "session-runs")
        self.assertEqual(args.live_agent_session_runs_command, "retry-now")
        self.assertTrue(args.approve_real_providers)

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

    def test_live_agent_session_runs_retry_now_posts_current_approval_only_when_requested(self):
        payload = {
            "status": "reconciled",
            "session_run": {
                "run_id": "retry-real",
                "action": "ensure",
                "status": "ready",
                "active": True,
                "meeting_id": "resident-m1",
                "group_id": "resident-main",
                "phase": "recover",
            },
            "results": [{"run_id": "retry-real", "status": "ready"}],
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
                        "retry-real",
                        "--approve-real-providers",
                    ]
                )

        self.assertEqual(exit_code, 0)
        request_json.assert_called_once_with(
            "http://room.local/api/live-agent-session-runs/retry-real/retry-now",
            method="POST",
            payload={"approve_real_providers": True},
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
