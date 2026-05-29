import json
import unittest
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from agentsassemble.local_resources import LocalResourceMonitor, collect_local_resource_snapshot


def _ps_result(output: str, returncode: int = 0):
    return SimpleNamespace(stdout=output, stderr="", returncode=returncode)


class LocalResourceTests(unittest.TestCase):
    def test_snapshot_sanitizes_command_names_without_leaking_args_or_paths(self):
        ps_output = "\n".join(
            [
                " 101     1  12.5   2048 /Users/seinel/.local/bin/kiro --config /Users/seinel/.codex/auth.json",
                " 102     1   3.0   1024 codex --api-key secret-value",
                " 103     1   2.0    512 /Applications/WindowServer",
            ]
        )

        snapshot = collect_local_resource_snapshot(
            ps_runner=lambda *args, **kwargs: _ps_result(ps_output),
            now_fn=lambda: datetime(2026, 5, 29, tzinfo=UTC),
            load_average_fn=lambda: (1.0, 0.5, 0.25),
            cpu_count_fn=lambda: 8,
            current_pid=9999,
        )

        process_text = json.dumps(snapshot["processes"], ensure_ascii=False)
        self.assertIn("kiro", process_text)
        self.assertIn("codex", process_text)
        self.assertNotIn("/Users/seinel", process_text)
        self.assertNotIn("--config", process_text)
        self.assertNotIn("secret-value", process_text)
        self.assertNotIn("WindowServer", process_text)

    def test_snapshot_includes_supervised_resident_pid_outside_allowlist(self):
        ps_output = " 202     1   1.5   4096 custom-private-resident"

        snapshot = collect_local_resource_snapshot(
            ps_runner=lambda *args, **kwargs: _ps_result(ps_output),
            supervised_pids={202},
            now_fn=lambda: datetime(2026, 5, 29, tzinfo=UTC),
            load_average_fn=lambda: (0.1, 0.1, 0.1),
            cpu_count_fn=lambda: 8,
            current_pid=9999,
        )

        self.assertEqual(snapshot["processes"][0]["pid"], 202)
        self.assertEqual(snapshot["processes"][0]["role"], "supervised_resident")

    def test_snapshot_redacts_sensitive_supervised_process_basenames(self):
        ps_output = "\n".join(
            [
                " 211     1   1.5   4096 codex-session-019e3038-39cc-76a2-a746-5ba8c0f3b408",
                " 212     1   1.5   4096 auth.json",
                " 213     1   1.5   4096 provider-log-tail",
                " 214     1   1.5   4096 019e3038-39cc-76a2-a746-5ba8c0f3b408",
            ]
        )

        snapshot = collect_local_resource_snapshot(
            ps_runner=lambda *args, **kwargs: _ps_result(ps_output),
            supervised_pids={211, 212, 213, 214},
            now_fn=lambda: datetime(2026, 5, 29, tzinfo=UTC),
            load_average_fn=lambda: (0.1, 0.1, 0.1),
            cpu_count_fn=lambda: 8,
            current_pid=9999,
        )

        process_text = json.dumps(snapshot["processes"], ensure_ascii=False)
        self.assertEqual({process["comm"] for process in snapshot["processes"]}, {"resident-process"})
        self.assertNotIn("019e3038", process_text)
        self.assertNotIn("auth.json", process_text)
        self.assertNotIn("provider-log-tail", process_text)

    def test_snapshot_marks_degraded_for_high_load_or_high_process_cpu(self):
        ps_output = " 301     1  96.0 100000 python3"

        snapshot = collect_local_resource_snapshot(
            ps_runner=lambda *args, **kwargs: _ps_result(ps_output),
            now_fn=lambda: datetime(2026, 5, 29, tzinfo=UTC),
            load_average_fn=lambda: (12.0, 10.0, 8.0),
            cpu_count_fn=lambda: 4,
            current_pid=9999,
        )

        self.assertEqual(snapshot["status"], "degraded")
        self.assertIn("load_average_high", snapshot["summary"]["attention"])
        self.assertIn("process_cpu_high", snapshot["summary"]["attention"])

    def test_summary_role_breakdown_partitions_visible_processes(self):
        ps_output = "\n".join(
            [
                " 501   999   4.0   2048 python3",
                " 502     1   5.5   4096 custom-resident",
                " 503     1   6.5   8192 node",
            ]
        )

        snapshot = collect_local_resource_snapshot(
            ps_runner=lambda *args, **kwargs: _ps_result(ps_output),
            supervised_pids={502},
            now_fn=lambda: datetime(2026, 5, 29, tzinfo=UTC),
            load_average_fn=lambda: (0.1, 0.1, 0.1),
            cpu_count_fn=lambda: 8,
            current_pid=999,
        )

        breakdown = snapshot["summary"]["role_breakdown"]
        self.assertEqual(breakdown["agentsassemble"], {"count": 1, "cpu_pct": 4.0, "rss_kb": 2048})
        self.assertEqual(breakdown["supervised_resident"], {"count": 1, "cpu_pct": 5.5, "rss_kb": 4096})
        self.assertEqual(breakdown["other"], {"count": 1, "cpu_pct": 6.5, "rss_kb": 8192})
        self.assertAlmostEqual(
            sum(role["cpu_pct"] for role in breakdown.values()),
            snapshot["summary"]["total_cpu_pct"],
            places=1,
        )
        self.assertEqual(
            sum(role["rss_kb"] for role in breakdown.values()),
            snapshot["summary"]["total_rss_kb"],
        )

    def test_summary_role_breakdown_includes_zero_rows_for_absent_roles(self):
        snapshot = collect_local_resource_snapshot(
            ps_runner=lambda *args, **kwargs: _ps_result(" 601     1   3.5   2048 python3"),
            now_fn=lambda: datetime(2026, 5, 29, tzinfo=UTC),
            load_average_fn=lambda: (0.1, 0.1, 0.1),
            cpu_count_fn=lambda: 8,
            current_pid=9999,
        )

        breakdown = snapshot["summary"]["role_breakdown"]
        self.assertEqual(breakdown["supervised_resident"], {"count": 0, "cpu_pct": 0.0, "rss_kb": 0})
        self.assertEqual(breakdown["agentsassemble"], {"count": 0, "cpu_pct": 0.0, "rss_kb": 0})
        self.assertEqual(breakdown["other"], {"count": 1, "cpu_pct": 3.5, "rss_kb": 2048})

    def test_summary_role_breakdown_does_not_leak_sensitive_process_names(self):
        ps_output = "\n".join(
            [
                " 701     1   1.5   4096 auth.json",
                " 702     1   2.0   8192 codex-session-019e3038-39cc-76a2-a746-5ba8c0f3b408",
            ]
        )

        snapshot = collect_local_resource_snapshot(
            ps_runner=lambda *args, **kwargs: _ps_result(ps_output),
            supervised_pids={701, 702},
            now_fn=lambda: datetime(2026, 5, 29, tzinfo=UTC),
            load_average_fn=lambda: (0.1, 0.1, 0.1),
            cpu_count_fn=lambda: 8,
            current_pid=9999,
        )

        serialized = json.dumps(snapshot, ensure_ascii=False)
        self.assertEqual(snapshot["summary"]["role_breakdown"]["supervised_resident"]["count"], 2)
        self.assertNotIn("auth.json", serialized)
        self.assertNotIn("codex-session", serialized)
        self.assertNotIn("019e3038", serialized)

    def test_summary_role_breakdown_matches_truncated_visible_processes(self):
        ps_output = "\n".join(
            f" {800 + index}     1  {float(index):.1f}   1024 node"
            for index in range(1, 41)
        )

        snapshot = collect_local_resource_snapshot(
            ps_runner=lambda *args, **kwargs: _ps_result(ps_output),
            now_fn=lambda: datetime(2026, 5, 29, tzinfo=UTC),
            load_average_fn=lambda: (0.1, 0.1, 0.1),
            cpu_count_fn=lambda: 8,
            current_pid=9999,
            max_processes=10,
        )

        breakdown = snapshot["summary"]["role_breakdown"]
        self.assertEqual(snapshot["summary"]["process_count"], 10)
        self.assertEqual(sum(role["count"] for role in breakdown.values()), 10)
        self.assertAlmostEqual(
            sum(role["cpu_pct"] for role in breakdown.values()),
            snapshot["summary"]["total_cpu_pct"],
            places=1,
        )
        self.assertEqual(
            sum(role["rss_kb"] for role in breakdown.values()),
            snapshot["summary"]["total_rss_kb"],
        )

    def test_monitor_reuses_recent_snapshot_cache(self):
        calls = []
        now = datetime(2026, 5, 29, tzinfo=UTC)

        def ps_runner(*args, **kwargs):
            calls.append(args)
            return _ps_result(" 401     1   1.0   2048 python3")

        clock = {"now": now}
        monitor = LocalResourceMonitor(
            ps_runner=ps_runner,
            now_fn=lambda: clock["now"],
            load_average_fn=lambda: (0.1, 0.1, 0.1),
            cpu_count_fn=lambda: 8,
            current_pid=9999,
            cache_seconds=2.0,
        )

        first = monitor.snapshot()
        second = monitor.snapshot()
        clock["now"] = now + timedelta(seconds=3)
        third = monitor.snapshot()

        self.assertEqual(len(calls), 2)
        self.assertEqual(first["generated_at"], second["generated_at"])
        self.assertNotEqual(second["generated_at"], third["generated_at"])


if __name__ == "__main__":
    unittest.main()
