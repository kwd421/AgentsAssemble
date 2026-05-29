import json
import subprocess
import unittest
from datetime import UTC, datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class Completed:
    def __init__(self, returncode: int = 0, stdout: str = "", stderr: str = ""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def benchmark_payload(*, predicate_p99_ms: float = 12.5) -> dict[str, object]:
    return {
        "schema_version": 1,
        "benchmark": "room_event_log_v1",
        "environment": {
            "platform": "private-platform",
            "python": "3.12.0",
        },
        "params": {
            "events": 120,
            "read_window": 20,
            "warmup_events": 10,
            "agent_count": 3,
            "sse_samples": 2,
            "cleanup": True,
        },
        "paths": {
            "run_root": str(ROOT / "private-benchmark-run"),
            "lobby_log": str(ROOT / "private-benchmark-run" / "lobby.jsonl"),
            "live_log": str(ROOT / "private-benchmark-run" / "meetings" / "m1" / "live_events.jsonl"),
            "temporary_root": "/tmp/agentsassemble-room-benchmark-private",
        },
        "metrics": {
            "lobby_append_ms": {"p99_ms": 0.11, "p95_ms": 0.1, "count": 120},
            "live_append_ms": {"p99_ms": 0.22, "p95_ms": 0.2, "count": 120},
            "lobby_read_after_cursor_ms": {"p99_ms": 0.33, "p95_ms": 0.3, "count": 20},
            "live_read_after_cursor_ms": {"p99_ms": 0.44, "p95_ms": 0.4, "count": 20},
            "lobby_tail_read_ms": {"p99_ms": 0.55, "avg_ms": 0.55},
            "live_tail_read_ms": {"p99_ms": 0.66, "avg_ms": 0.66},
            "lobby_sse_append_to_frame_ms": {"p99_ms": 8.8, "count": 2},
            "flow_scheduler_comparison": {
                "normalized_improvement": 1.0,
                "predicate_latency_ms": {"p99_ms": predicate_p99_ms, "count": 60},
                "scheduler_on": {"normalized_imbalance": 0.0},
                "scheduler_off": {"normalized_imbalance": 1.0},
            },
        },
        "notes": ["private implementation detail"],
        "unexpected_future_field": {"do_not_echo": True},
    }


class ReleaseHealthTests(unittest.TestCase):
    def test_catalog_matches_v0_1_release_check_order_without_command_details(self):
        from agentsassemble.release_health import RELEASE_HEALTH_CHECK_IDS, release_health_catalog_payload

        payload = release_health_catalog_payload(now=datetime(2026, 5, 29, 0, 0, tzinfo=UTC))

        self.assertEqual(
            RELEASE_HEALTH_CHECK_IDS,
            [
                "node_check_static",
                "unittest_static_ui_assets",
                "unittest_docs_architecture",
                "unittest_mcp_server",
                "unittest_gui_and_live_agent_smoke",
                "compileall_package",
                "git_diff_check",
                "room_event_benchmark",
            ],
        )
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["schema_version"], 1)
        self.assertEqual(payload["generated_at"], "2026-05-29T00:00:00+00:00")
        self.assertEqual([check["id"] for check in payload["checks"]], RELEASE_HEALTH_CHECK_IDS)
        benchmark = payload["checks"][-1]
        self.assertEqual(benchmark["id"], "room_event_benchmark")
        self.assertEqual(benchmark["category"], "live_room")
        self.assertEqual(benchmark["kind"], "benchmark")
        self.assertTrue(benchmark["optional"])
        self.assertNotIn("argv", json.dumps(payload, ensure_ascii=False))
        self.assertNotIn("command", json.dumps(payload, ensure_ascii=False))
        self.assertNotIn(str(ROOT), json.dumps(payload, ensure_ascii=False))

    def test_catalog_exposes_order_default_run_and_safety_class_without_command_details(self):
        from agentsassemble.release_health import RELEASE_HEALTH_SAFETY_CLASSES, release_health_catalog_payload

        payload = release_health_catalog_payload(now=datetime(2026, 5, 29, 0, 0, tzinfo=UTC))
        checks = payload["checks"]
        default_checks = [check for check in checks if check["default_run"]]
        opt_in_checks = [check for check in checks if not check["default_run"]]

        self.assertEqual([check["order"] for check in default_checks], list(range(1, 8)))
        self.assertEqual([check["id"] for check in opt_in_checks], ["room_event_benchmark"])
        self.assertIsNone(opt_in_checks[0]["order"])
        self.assertEqual(opt_in_checks[0]["safety_class"], "local_room_benchmark")
        for check in checks:
            self.assertEqual(check["default_run"], not check["optional"])
            self.assertIn(check["safety_class"], RELEASE_HEALTH_SAFETY_CLASSES)

        serialized = json.dumps(payload, ensure_ascii=False)
        for forbidden in (
            "argv",
            "command",
            "commands",
            "cwd",
            "env",
            "path",
            "--warmup-events",
            "--read-window",
            "agentsassemble.cli",
            str(ROOT),
        ):
            self.assertNotIn(forbidden, serialized)

    def test_safety_class_values_are_closed_vocabulary(self):
        from agentsassemble.release_health import RELEASE_HEALTH_CHECKS, RELEASE_HEALTH_SAFETY_CLASSES

        self.assertEqual(
            RELEASE_HEALTH_SAFETY_CLASSES,
            {
                "frontend_static_syntax",
                "python_unit",
                "python_integration",
                "python_compile",
                "git_format",
                "local_room_benchmark",
            },
        )
        for check in RELEASE_HEALTH_CHECKS:
            self.assertIn(check.safety_class, RELEASE_HEALTH_SAFETY_CLASSES)

    def test_default_release_health_selection_excludes_optional_room_event_benchmark(self):
        from agentsassemble.release_health import validate_release_health_check_selection

        selected = validate_release_health_check_selection()

        self.assertEqual(
            [check.id for check in selected],
            [
                "node_check_static",
                "unittest_static_ui_assets",
                "unittest_docs_architecture",
                "unittest_mcp_server",
                "unittest_gui_and_live_agent_smoke",
                "compileall_package",
                "git_diff_check",
            ],
        )
        self.assertFalse(any(check.id == "room_event_benchmark" for check in selected))

    def test_explicit_check_room_event_benchmark_selects_only_benchmark(self):
        from agentsassemble.release_health import validate_release_health_check_selection

        selected = validate_release_health_check_selection(check_ids=["room_event_benchmark"])

        self.assertEqual([check.id for check in selected], ["room_event_benchmark"])
        self.assertTrue(selected[0].optional)

    def test_room_event_benchmark_check_uses_cli_invocation_and_does_not_start_providers(self):
        from agentsassemble.release_health import run_release_health_checks

        calls = []

        def fake_runner(argv, **kwargs):
            calls.append(list(argv))
            return Completed(stdout='{"benchmark":"room_event_log_v1"}')

        payload = run_release_health_checks(
            check_ids=["room_event_benchmark"],
            runner=fake_runner,
            now_fn=lambda: datetime(2026, 5, 29, 0, 0, tzinfo=UTC),
        )

        self.assertEqual(len(calls), 1)
        command = calls[0]
        self.assertEqual(
            command[:5],
            ["python3", "-m", "agentsassemble.cli", "live-agent", "room-benchmark"],
        )
        for forbidden in (
            "start-session",
            "--agent-config",
            "--live-agent-config",
            "codex",
            "claude",
            "cursor",
            "grok",
            "gui",
        ):
            self.assertNotIn(forbidden, command)
        self.assertEqual(payload["summary"], {"total": 1, "passed": 1, "failed": 0, "skipped": 0, "ok": True})

    def test_cli_parser_accepts_list_and_bounded_run_options(self):
        from agentsassemble.cli import build_parser

        list_args = build_parser().parse_args(["release-health", "--json"])
        run_args = build_parser().parse_args(
            [
                "release-health",
                "run",
                "--check",
                "git_diff_check",
                "--skip",
                "compileall_package",
                "--timeout",
                "3",
                "--as-json",
            ]
        )

        self.assertEqual(list_args.command, "release-health")
        self.assertIsNone(list_args.release_health_command)
        self.assertTrue(list_args.as_json)
        self.assertEqual(run_args.release_health_command, "run")
        self.assertEqual(run_args.check, ["git_diff_check"])
        self.assertEqual(run_args.skip, ["compileall_package"])
        self.assertEqual(run_args.timeout, 3.0)
        self.assertTrue(run_args.as_json)

    def test_runner_uses_fixed_commands_and_reports_summary(self):
        from agentsassemble.release_health import run_release_health_checks

        calls = []

        def fake_runner(argv, **kwargs):
            calls.append((list(argv), kwargs))
            return Completed(stdout=f"ran {argv[0]}")

        payload = run_release_health_checks(
            check_ids=["compileall_package", "git_diff_check"],
            runner=fake_runner,
            now_fn=lambda: datetime(2026, 5, 29, 0, 0, tzinfo=UTC),
        )

        self.assertEqual([call[0] for call in calls], [["python3", "-m", "compileall", "-q", "agentsassemble"], ["git", "diff", "--check"]])
        self.assertTrue(all(call[1]["cwd"] == ROOT for call in calls))
        self.assertTrue(all(call[1]["shell"] is False for call in calls))
        self.assertEqual(payload["summary"], {"total": 2, "passed": 2, "failed": 0, "skipped": 0, "ok": True})
        self.assertEqual([result["status"] for result in payload["results"]], ["passed", "passed"])
        serialized = json.dumps(payload, ensure_ascii=False)
        self.assertNotIn("argv", serialized)
        self.assertNotIn(str(ROOT), serialized)

    def test_runner_rejects_unknown_check_ids_before_running(self):
        from agentsassemble.release_health import ReleaseHealthSelectionError, run_release_health_checks

        def fake_runner(argv, **kwargs):
            raise AssertionError("unknown checks must fail before any process is started")

        with self.assertRaises(ReleaseHealthSelectionError):
            run_release_health_checks(check_ids=["not_a_check"], runner=fake_runner)

    def test_sanitizer_redacts_paths_env_assignments_and_truncates_tail(self):
        from agentsassemble.release_health import sanitize_release_health_output

        home = Path.home()
        raw = "\n".join(
            [
                f"{ROOT}/agentsassemble/cli.py",
                f"{home}/.tokens/provider.env",
                "DEEPSEEK_API_KEY=secret",
                "ordinary line",
                "x" * 200,
            ]
        )

        sanitized = sanitize_release_health_output(raw, repo_root=ROOT, limit=120)

        self.assertLessEqual(len(sanitized), 120)
        self.assertNotIn(str(ROOT), sanitized)
        self.assertNotIn(str(home), sanitized)
        self.assertNotIn("DEEPSEEK_API_KEY", sanitized)
        self.assertIn("ordinary line", sanitize_release_health_output(raw, repo_root=ROOT, limit=500))

    def test_timeout_is_failed_without_traceback_or_command_echo(self):
        from agentsassemble.release_health import run_release_health_checks

        def fake_runner(argv, **kwargs):
            raise subprocess.TimeoutExpired(cmd=argv, timeout=1.0, output="partial stdout", stderr=f"{ROOT}/private.log")

        payload = run_release_health_checks(
            check_ids=["git_diff_check"],
            timeout_seconds=1.0,
            runner=fake_runner,
            now_fn=lambda: datetime(2026, 5, 29, 0, 0, tzinfo=UTC),
        )

        result = payload["results"][0]
        self.assertEqual(result["status"], "failed")
        self.assertIn("timed out", result["stderr_tail"])
        self.assertNotIn("Traceback", json.dumps(result, ensure_ascii=False))
        self.assertNotIn("git diff --check", json.dumps(result, ensure_ascii=False))
        self.assertNotIn(str(ROOT), json.dumps(result, ensure_ascii=False))

    def test_missing_tool_is_skipped_without_crashing(self):
        from agentsassemble.release_health import run_release_health_checks

        def fake_runner(argv, **kwargs):
            raise FileNotFoundError(argv[0])

        payload = run_release_health_checks(
            check_ids=["git_diff_check"],
            runner=fake_runner,
            now_fn=lambda: datetime(2026, 5, 29, 0, 0, tzinfo=UTC),
        )

        result = payload["results"][0]
        self.assertEqual(result["status"], "skipped")
        self.assertEqual(result["skipped_reason"], "missing_tool:git")
        self.assertEqual(payload["summary"], {"total": 1, "passed": 0, "failed": 0, "skipped": 1, "ok": True})

    def test_room_event_benchmark_result_includes_structured_summary(self):
        from agentsassemble.release_health import run_release_health_checks

        def fake_runner(argv, **kwargs):
            return Completed(stdout=json.dumps(benchmark_payload()))

        payload = run_release_health_checks(
            check_ids=["room_event_benchmark"],
            runner=fake_runner,
            now_fn=lambda: datetime(2026, 5, 29, 0, 0, tzinfo=UTC),
        )

        result = payload["results"][0]
        self.assertEqual(result["status"], "passed")
        summary = result["benchmark_summary"]
        self.assertEqual(summary["status"], "ok")
        self.assertEqual(summary["schema_version"], 1)
        self.assertEqual(
            summary["params"],
            {
                "events": 120,
                "read_window": 20,
                "warmup_events": 10,
                "agent_count": 3,
                "sse_samples": 2,
            },
        )
        self.assertEqual(
            summary["metrics_summary"],
            {
                "lobby_append_p99_ms": 0.11,
                "live_append_p99_ms": 0.22,
                "lobby_read_after_cursor_p99_ms": 0.33,
                "live_read_after_cursor_p99_ms": 0.44,
                "lobby_tail_read_ms": 0.55,
                "live_tail_read_ms": 0.66,
                "lobby_sse_append_to_frame_p99_ms": 8.8,
                "flow_normalized_improvement": 1.0,
                "flow_scheduler_predicate_p99_ms": 12.5,
            },
        )
        self.assertEqual(
            summary["regression_signals"],
            [
                {
                    "name": "flow_scheduler_predicate_p99_ms",
                    "value_ms": 12.5,
                    "ceiling_ms": 75.0,
                    "ok": True,
                }
            ],
        )
        self.assertEqual(summary["ceilings"], {"flow_scheduler_predicate_p99_ms": 75.0})

    def test_room_event_benchmark_summary_marks_unparsed_when_stdout_is_not_json(self):
        from agentsassemble.release_health import run_release_health_checks

        def fake_runner(argv, **kwargs):
            return Completed(stdout="not json")

        payload = run_release_health_checks(
            check_ids=["room_event_benchmark"],
            runner=fake_runner,
            now_fn=lambda: datetime(2026, 5, 29, 0, 0, tzinfo=UTC),
        )

        result = payload["results"][0]
        self.assertEqual(result["status"], "passed")
        self.assertEqual(result["benchmark_summary"], {"status": "unparsed"})

    def test_room_event_benchmark_summary_omits_paths_commands_and_unknown_future_fields(self):
        from agentsassemble.release_health import run_release_health_checks

        def fake_runner(argv, **kwargs):
            return Completed(stdout=json.dumps(benchmark_payload()))

        payload = run_release_health_checks(
            check_ids=["room_event_benchmark"],
            runner=fake_runner,
            now_fn=lambda: datetime(2026, 5, 29, 0, 0, tzinfo=UTC),
        )

        result = payload["results"][0]
        self.assertEqual(result["stdout_tail"], "room benchmark stdout omitted; use benchmark_summary")
        serialized = json.dumps(result, ensure_ascii=False)
        for forbidden in (
            "paths",
            "notes",
            "environment",
            '"metrics"',
            "unexpected_future_field",
            "private-benchmark-run",
            "/tmp/",
            str(ROOT),
            "argv",
            "cwd",
            "command",
            "--warmup-events",
            "agentsassemble.cli",
        ):
            self.assertNotIn(forbidden, serialized)

    def test_room_event_benchmark_regression_signal_counts_ceiling_breach_without_failing_check(self):
        from agentsassemble.release_health import run_release_health_checks

        def fake_runner(argv, **kwargs):
            return Completed(stdout=json.dumps(benchmark_payload(predicate_p99_ms=100.0)))

        payload = run_release_health_checks(
            check_ids=["room_event_benchmark"],
            runner=fake_runner,
            now_fn=lambda: datetime(2026, 5, 29, 0, 0, tzinfo=UTC),
        )

        result = payload["results"][0]
        self.assertEqual(result["status"], "passed")
        self.assertEqual(payload["status"], "ok")
        self.assertTrue(payload["summary"]["ok"])
        self.assertGreaterEqual(payload["summary"]["regression_signals_failed"], 1)
        self.assertEqual(
            result["benchmark_summary"]["regression_signals"],
            [
                {
                    "name": "flow_scheduler_predicate_p99_ms",
                    "value_ms": 100.0,
                    "ceiling_ms": 75.0,
                    "ok": False,
                }
            ],
        )


if __name__ == "__main__":
    unittest.main()
