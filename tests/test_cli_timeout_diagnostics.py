import unittest
import json
import tempfile
import threading
from pathlib import Path
from http.server import ThreadingHTTPServer
from io import StringIO
from unittest.mock import patch

from agentsassemble import cli as cli_module
from agentsassemble.cli import build_parser, main
from agentsassemble.gui import _make_handler, append_lobby_event, read_lobby
from agentsassemble.legacy.live_agent.runtime.smoke import LiveAgentSmokeFailed


class CliTimeoutDiagnosticsTests(unittest.TestCase):

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
            "sandbox_enforcement": {
                "counts": {"advisory": 1, "codex_readonly": 1, "os_sandboxed": 0, "unknown": 0},
                "attention": [],
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
            "admission": {
                "total": 3,
                "host_approved": 1,
                "unapproved": 2,
                "counts": {
                    "bound_to_meeting": 1,
                    "binding_conflict": 1,
                    "meeting_lobby_only": 1,
                    "meeting_missing": 0,
                    "lobby_only": 0,
                    "unknown": 0,
                },
                "attention": [
                    "resident-m1:agent-b:binding_conflict",
                    "resident-m1:guest-agent:meeting_lobby_only",
                ],
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
        self.assertIn("sandbox: advisory 1, codex_readonly 1, os_sandboxed 0, unknown 0", output)
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
        self.assertIn("admission: 1 host-approved / 3 total", output)
        self.assertIn("binding conflict 1", output)
        self.assertIn("meeting lobby 1", output)
        self.assertIn("admission attention: resident-m1:agent-b:binding_conflict, resident-m1:guest-agent:meeting_lobby_only", output)

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

    def test_live_agent_real_session_smoke_parses_explicit_approval_options(self):
        args = build_parser().parse_args(
            [
                "live-agent",
                "real-session-smoke",
                "--server",
                "http://room.local",
                "--live-agent-config",
                "configs/live-agents.example.json",
                "--council-config",
                "configs/demo-council.json",
                "--agent-config",
                "configs/agents.example.json",
                "--group-id",
                "real-smoke",
                "--meeting-id",
                "real-smoke-meeting",
                "--timeout",
                "9",
                "--approve-real-providers",
                "--json",
            ]
        )

        self.assertEqual(args.live_agent_command, "real-session-smoke")
        self.assertEqual(args.server, "http://room.local")
        self.assertEqual(args.live_agent_config, "configs/live-agents.example.json")
        self.assertEqual(args.council_config, "configs/demo-council.json")
        self.assertEqual(args.agent_config, "configs/agents.example.json")
        self.assertEqual(args.group_id, "real-smoke")
        self.assertEqual(args.meeting_id, "real-smoke-meeting")
        self.assertEqual(args.timeout, 9.0)
        self.assertTrue(args.approve_real_providers)
        self.assertTrue(args.as_json)

    def test_live_agent_continuity_proof_parses_explicit_approval_options(self):
        args = build_parser().parse_args(
            [
                "live-agent",
                "continuity-proof",
                "--provider-kind",
                "kiro_live_session",
                "--connection-kind",
                "live_session",
                "--agent-id",
                "kiro-proof",
                "--timeout",
                "180",
                "--approve-real-providers",
                "--json",
                "--command",
                "kiro",
                "chat",
                "--no-interactive",
                "--wrap",
                "never",
            ]
        )

        self.assertEqual(args.live_agent_command, "continuity-proof")
        self.assertEqual(args.provider_kind, "kiro_live_session")
        self.assertEqual(args.connection_kind, "live_session")
        self.assertEqual(args.agent_id, "kiro-proof")
        self.assertEqual(args.timeout, 180)
        self.assertTrue(args.approve_real_providers)
        self.assertEqual(args.resident_command, ["kiro", "chat", "--no-interactive", "--wrap", "never"])
        self.assertTrue(args.as_json)

    def test_live_agent_continuity_proof_formatter_includes_limits(self):
        formatted = cli_module._format_live_agent_continuity_proof(
            {
                "status": "ok",
                "provider_kind": "kiro_live_session",
                "method": "provider_resume_suffix_recall",
                "session_id_captured": True,
                "expected_suffix_matched": True,
                "expected_suffix_recalled": True,
                "recall_match_mode": "exact",
                "reason": "ok",
            }
        )

        self.assertIn("two-turn provider-owned resume recall only", formatted)
        self.assertIn("does not prove room admission or tool safety", formatted)
        self.assertIn("suffix yes (exact)", formatted)

    def test_live_agent_continuity_proof_formatter_shows_tolerant_recall(self):
        formatted = cli_module._format_live_agent_continuity_proof(
            {
                "status": "ok",
                "provider_kind": "antigravity_live_session",
                "method": "provider_resume_suffix_recall",
                "session_id_captured": True,
                "expected_suffix_matched": False,
                "expected_suffix_recalled": True,
                "recall_match_mode": "mentioned",
                "reason": "ok",
            }
        )

        self.assertIn("suffix yes (mentioned)", formatted)

    def test_live_agent_continuity_proof_group_parses_config_and_approval(self):
        args = build_parser().parse_args(
            [
                "live-agent",
                "continuity-proof-group",
                "--config",
                "configs/live-agents.provider-staging.example.json",
                "--server",
                "http://127.0.0.1:8765",
                "--approve-real-providers",
                "--json",
            ]
        )

        self.assertEqual(args.live_agent_command, "continuity-proof-group")
        self.assertEqual(args.config, "configs/live-agents.provider-staging.example.json")
        self.assertEqual(args.server, "http://127.0.0.1:8765")
        self.assertTrue(args.approve_real_providers)
        self.assertTrue(args.as_json)

    def test_live_agent_continuity_proof_group_formatter_includes_counts(self):
        formatted = cli_module._format_live_agent_continuity_proof_group(
            {
                "status": "partial",
                "ok_count": 2,
                "failed_count": 0,
                "unsupported_count": 4,
                "approval_required_count": 0,
            }
        )

        self.assertIn("continuity proof group partial", formatted)
        self.assertIn("2 ok", formatted)
        self.assertIn("4 unsupported", formatted)
        self.assertIn("two-turn provider-owned resume recall only", formatted)

    def test_live_agent_continuity_proof_group_exit_code_allows_audit_only_unsupported(self):
        self.assertEqual(cli_module._live_agent_continuity_proof_group_exit_code({"status": "unsupported"}), 0)
        self.assertEqual(cli_module._live_agent_continuity_proof_group_exit_code({"status": "partial"}), 0)
        self.assertEqual(cli_module._live_agent_continuity_proof_group_exit_code({"status": "failed"}), 1)
        self.assertEqual(cli_module._live_agent_continuity_proof_group_exit_code({"status": "approval_required"}), 1)

    def test_live_agent_continuity_proof_group_provider_staging_requires_cursor_approval(self):
        stdout = StringIO()

        with patch("sys.stdout", stdout):
            exit_code = main(
                [
                    "live-agent",
                    "continuity-proof-group",
                    "--config",
                    "configs/live-agents.provider-staging.example.json",
                    "--json",
                ]
            )

        self.assertEqual(exit_code, 1)
        payload = json.loads(stdout.getvalue())
        self.assertEqual(payload["status"], "approval_required")
        self.assertEqual(payload["total_count"], 4)
        self.assertEqual(payload["unsupported_count"], 3)
        self.assertEqual(payload["approval_required_count"], 1)
        self.assertEqual(
            {item["agent_id"]: item["status"] for item in payload["results"]},
            {
                "claude-code-live": "unsupported",
                "cursor-agent-live-session": "approval_required",
                "grok-build-live": "unsupported",
                "openclaw-cli-live": "unsupported",
            },
        )

    def test_live_agent_real_session_smoke_requires_matching_config_paths(self):
        with self.assertRaises(SystemExit):
            build_parser().parse_args(
                [
                    "live-agent",
                    "real-session-smoke",
                    "--live-agent-config",
                    "configs/live-agents.example.json",
                    "--agent-config",
                    "configs/agents.example.json",
                ]
            )
        with self.assertRaises(SystemExit):
            build_parser().parse_args(
                [
                    "live-agent",
                    "real-session-smoke",
                    "--live-agent-config",
                    "configs/live-agents.example.json",
                    "--council-config",
                    "configs/demo-council.json",
                ]
            )

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
                "api",
                "--probe-timeout",
                "0.75",
                "--json",
            ]
        )

        self.assertEqual(args.command, "providers")
        self.assertEqual(args.providers_command, "health")
        self.assertEqual(args.config, "configs/http-providers.example.json")
        self.assertEqual(args.probe_mode, "api")
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

    def test_live_agent_real_session_smoke_refuses_missing_approval_without_network(self):
        stdout = StringIO()
        with patch("agentsassemble.cli._request_json") as request_json:
            with patch("sys.stdout", stdout):
                exit_code = main(
                    [
                        "live-agent",
                        "real-session-smoke",
                        "--server",
                        "http://room.local",
                        "--live-agent-config",
                        "/Users/me/private/live-agents.real.json",
                        "--council-config",
                        "/Users/me/private/council.json",
                        "--agent-config",
                        "/Users/me/private/agents.json",
                        "--json",
                    ]
                )

        self.assertEqual(exit_code, 1)
        request_json.assert_not_called()
        payload = json.loads(stdout.getvalue())
        self.assertEqual(payload["status"], "approval_required")
        self.assertNotIn("/Users/me", stdout.getvalue())
        self.assertNotIn("live-agents.real.json", stdout.getvalue())

    def test_live_agent_real_session_smoke_posts_endpoint_with_explicit_approval(self):
        payload = {
            "status": "ok",
            "meeting_id": "real-smoke-meeting",
            "group_id": "real-smoke",
            "start_status": "ready",
            "reply_probe_status": "ok",
            "reply_probe_ok_count": 2,
            "reply_probe_count": 2,
            "stop_status": "stopped",
            "post_stop_process_status": "stopped",
        }
        stdout = StringIO()
        with patch("agentsassemble.cli._request_json", return_value=payload) as request_json:
            with patch("sys.stdout", stdout):
                exit_code = main(
                    [
                        "live-agent",
                        "real-session-smoke",
                        "--server",
                        "http://room.local",
                        "--live-agent-config",
                        "configs/live-agents.example.json",
                        "--council-config",
                        "configs/demo-council.json",
                        "--agent-config",
                        "configs/agents.example.json",
                        "--group-id",
                        "real-smoke",
                        "--meeting-id",
                        "real-smoke-meeting",
                        "--timeout",
                        "9",
                        "--approve-real-providers",
                    ]
                )

        self.assertEqual(exit_code, 0)
        request_json.assert_called_once_with(
            "http://room.local/api/live-agent-real-session-smoke",
            method="POST",
            payload={
                "group_id": "real-smoke",
                "meeting_id": "real-smoke-meeting",
                "timeout": 9.0,
                "live_agent_config_path": "configs/live-agents.example.json",
                "council_config_path": "configs/demo-council.json",
                "agent_config_path": "configs/agents.example.json",
                "approve_real_providers": True,
            },
            timeout_seconds=253.0,
        )
        self.assertIn("real resident session smoke ok: real-smoke-meeting", stdout.getvalue())
        self.assertIn("probes ok: 2/2 ok", stdout.getvalue())
        self.assertIn("post-stop stopped", stdout.getvalue())

    def test_live_agent_real_session_smoke_posts_only_requested_deep_checks(self):
        payload = {
            "status": "failed",
            "meeting_id": "real-smoke-meeting",
            "group_id": "real-smoke",
            "start_status": "ready",
            "reply_probe_status": "ok",
            "reply_probe_ok_count": 1,
            "reply_probe_count": 1,
            "official_round_smoke": True,
            "official_rounds_status": "timeout",
            "official_answered_round_count": 0,
            "official_round_count": 1,
            "restart_smoke": True,
            "restart_status": "ready",
            "post_restart_reply_probe_status": "ok",
            "post_restart_reply_probe_ok_count": 1,
            "post_restart_reply_probe_count": 1,
            "stop_status": "stopped",
            "post_stop_process_status": "stopped",
        }
        stdout = StringIO()
        with patch("agentsassemble.cli._request_json", return_value=payload) as request_json:
            with patch("sys.stdout", stdout):
                exit_code = main(
                    [
                        "live-agent",
                        "real-session-smoke",
                        "--server",
                        "http://room.local",
                        "--live-agent-config",
                        "configs/live-agents.example.json",
                        "--council-config",
                        "configs/demo-council.json",
                        "--agent-config",
                        "configs/agents.example.json",
                        "--approve-real-providers",
                        "--official-round-smoke",
                        "--restart-smoke",
                    ]
                )

        self.assertEqual(exit_code, 1)
        request_json.assert_called_once()
        request_payload = request_json.call_args.kwargs["payload"]
        self.assertTrue(request_payload["official_round_smoke"])
        self.assertTrue(request_payload["restart_smoke"])
        self.assertIn("official timeout: 0/1 answered", stdout.getvalue())
        self.assertIn("restart ready", stdout.getvalue())
        self.assertIn("post-restart probes ok: 1/1 ok", stdout.getvalue())

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
