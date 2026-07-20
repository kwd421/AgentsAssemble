import unittest
import json
import tempfile
import threading
from pathlib import Path
from http.server import ThreadingHTTPServer
from io import StringIO
from unittest.mock import patch

from agentsassemble.cli import build_parser, main
from agentsassemble.gui import _make_handler
from agentsassemble.legacy.live_agent.runtime.operations import append_live_agent_operation


class CliTimeoutOperationsTests(unittest.TestCase):

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

    def test_live_agent_operations_list_prioritizes_readiness_long_session_health_causes(self):
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
                        "health_process_reasons": [
                            "orphan-group recovered_unknown orphan running record marked unknown"
                        ],
                        "health_process_attention": ["orphan-group"],
                        "session_smoke_reply_count": 3,
                        "health_observation_attention": ["resident-m1:resident-main:agent-a:lobby_cursor_behind"],
                        "health_observation_lobby_behind_count": 1,
                        "health_observation_live_behind_count": 1,
                        "health_observation_error_count": 1,
                        "health_shared_memory_attention": ["resident-m1:resident-main:memory_unavailable"],
                        "health_session_run_attention": ["resident-m1:resident-main:run-a:ready:no_current_readiness"],
                        "health_session_run_retrying": 1,
                        "health_session_run_monitor_attention": ["failed:RuntimeError"],
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
        self.assertIn("health_observation_attention=resident-m1:resident-main:agent-a:lobby_cursor_behind", output)
        self.assertIn("health_session_run_attention=resident-m1:resident-main:run-a:ready:no_current_readiness", output)
        self.assertIn("health_session_run_monitor_attention=failed:RuntimeError", output)
        self.assertLess(
            output.index("health_observation_attention="),
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

    def test_live_agent_operations_list_prioritizes_real_session_smoke_evidence(self):
        payload = {
            "operations": [
                {
                    "timestamp": "2026-05-18T01:02:03+00:00",
                    "operation": "session.real_smoke",
                    "status": "success",
                    "target_id": "real-smoke",
                    "summary": "ran approved real resident session smoke",
                    "details": {
                        "group_id": "real-smoke",
                        "meeting_id": "real-smoke-meeting",
                        "approval_required": True,
                        "approved": True,
                        "diagnostic": True,
                        "result_status": "ok",
                        "start_status": "ready",
                        "expected_agent_count": 2,
                        "connected_agent_count": 2,
                        "reply_probe_status": "ok",
                        "reply_probe_count": 2,
                        "reply_probe_ok_count": 2,
                        "stop_status": "stopped",
                        "post_stop_process_status": "stopped",
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
        self.assertIn("session.real_smoke", output)
        self.assertIn("result_status=ok", output)
        self.assertIn("reply_probe_ok_count=2", output)
        self.assertIn("post_stop_process_status=stopped", output)
        self.assertLess(output.index("reply_probe_ok_count=2"), output.index("stop_status=stopped"))

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

    def test_live_agent_operations_list_prioritizes_discovery_exact_approval_result(self):
        payload = {
            "operations": [
                {
                    "timestamp": "2026-05-18T01:02:03+00:00",
                    "operation": "discovery.run",
                    "status": "success",
                    "target_id": "live-agent-discovery",
                    "summary": "",
                    "details": {
                        "agents": 1,
                        "discovered": 3,
                        "join_semantics": ["terminal_pty_prompt_bridge", "codex_exec_resume"],
                        "context_durability": ["process_lifetime", "provider_managed_resume"],
                        "sandbox_enforcement": ["advisory", "codex_readonly"],
                        "evidence_basis": ["path_and_pty_preflight", "path_and_codex_safety_preflight"],
                        "approval_required": 1,
                        "result_status": "ok",
                        "approved_count": 1,
                        "approved_agent_ids": ["codex-live"],
                        "excluded_agent_count": 2,
                        "unmatched_approval_count": 1,
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
        self.assertIn("discovery.run", output)
        self.assertIn("result_status=ok", output)
        self.assertIn("approved_count=1", output)
        self.assertIn("approved_agent_ids=codex-live", output)
        self.assertIn("excluded_agent_count=2", output)
        self.assertIn("unmatched_approval_count=1", output)
        self.assertLess(output.index("approved_count=1"), output.index("agents=1"))

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
