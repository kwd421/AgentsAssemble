import unittest
from io import StringIO
from unittest.mock import patch

from agentsassemble.cli import build_parser, main


class CliTimeoutSessionControlsTests(unittest.TestCase):

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

    def test_live_agent_finalize_meeting_parser_accepts_close_pending(self):
        args = build_parser().parse_args(
            [
                "live-agent",
                "finalize-meeting",
                "--server",
                "http://room.local",
                "--meeting-id",
                "resident-m1",
                "--close-pending",
            ]
        )

        self.assertEqual(args.live_agent_command, "finalize-meeting")
        self.assertEqual(args.meeting_id, "resident-m1")
        self.assertTrue(args.close_pending)

    def test_live_agent_finalize_meeting_posts_request_and_prints_summary(self):
        response = {
            "status": "finalized",
            "meeting_id": "resident-m1",
            "official_event_count": 2,
            "cancelled_pending_count": 1,
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
            payload={"force": False, "close_pending": False},
            timeout_seconds=20.0,
        )
        self.assertIn("Finalized resident-m1: 2 official events, 1 pending turn cancelled", stdout.getvalue())

    def test_live_agent_finalize_meeting_posts_close_pending_request(self):
        response = {
            "status": "finalized",
            "meeting_id": "resident-m1",
            "official_event_count": 2,
            "cancelled_pending_count": 2,
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
                        "--close-pending",
                    ]
                )

        self.assertEqual(exit_code, 0)
        request_json.assert_called_once_with(
            "http://room.local/api/meetings/resident-m1/finalize",
            method="POST",
            payload={"force": False, "close_pending": True},
            timeout_seconds=20.0,
        )
        self.assertIn("2 pending turns cancelled", stdout.getvalue())

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
                "--legacy-internal",
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
                        "--legacy-internal",
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
                        "--legacy-internal",
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
                        "--legacy-internal",
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
                        "--legacy-internal",
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
                "--legacy-internal",
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
                        "--legacy-internal",
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
                        "--legacy-internal",
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
