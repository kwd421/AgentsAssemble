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


if __name__ == "__main__":
    unittest.main()
