import unittest
import json
import tempfile
import threading
import urllib.error
from pathlib import Path
from http.server import ThreadingHTTPServer
from io import StringIO
from unittest.mock import patch

from agentsassemble import cli as cli_module
from agentsassemble.cli import build_parser, main
from agentsassemble.gui import _make_handler


class CliTimeoutProcessesTests(unittest.TestCase):

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
