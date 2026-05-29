import json
import math
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

from agentsassemble.cli import build_parser, main
from agentsassemble.room_event_benchmark import (
    RoomEventBenchmarkOptions,
    SCHEDULER_IMBALANCE_MARGIN,
    SCHEDULER_P99_LATENCY_CEILING_MS,
    flow_speaking_distribution,
    run_room_event_benchmark,
)


class RoomEventBenchmarkTests(unittest.TestCase):
    def test_benchmark_reports_finite_metrics_and_cleans_run_directory(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            result = run_room_event_benchmark(
                RoomEventBenchmarkOptions(
                    output_root=Path(temp_dir),
                    events=24,
                    read_window=6,
                    warmup_events=3,
                    agent_count=3,
                    cleanup=True,
                )
            )

        self.assertEqual(result["schema_version"], 1)
        self.assertEqual(result["benchmark"], "room_event_log_v1")
        self.assertTrue(result["cleanup_removed"])
        metrics = result["metrics"]
        for key in (
            "lobby_append_ms",
            "live_append_ms",
            "lobby_read_after_cursor_ms",
            "live_read_after_cursor_ms",
            "lobby_tail_read_ms",
            "live_tail_read_ms",
        ):
            self.assertIn(key, metrics)
            self.assert_metric_is_finite(metrics[key])
        fairness = metrics["flow_speaking_distribution"]
        self.assertEqual(fairness["definition"], "imbalance_ratio=max_agent_speaking_count/max(min_agent_speaking_count,1)")
        self.assertEqual(fairness["total_speaking_turns"], 24)
        self.assertEqual(fairness["agent_count"], 3)
        self.assertEqual(fairness["imbalance_ratio"], 1.0)
        scheduler = metrics["flow_scheduler_comparison"]
        self.assertGreaterEqual(
            scheduler["scheduler_off"]["normalized_imbalance"] - scheduler["scheduler_on"]["normalized_imbalance"],
            SCHEDULER_IMBALANCE_MARGIN,
        )
        self.assertLessEqual(
            scheduler["predicate_latency_ms"]["p99_ms"],
            SCHEDULER_P99_LATENCY_CEILING_MS,
        )
        self.assertNotIn("lobby_sse_append_to_frame_ms", metrics)
        self.assertTrue(any("Lobby SSE append-to-frame latency" in note for note in result["notes"]))
        self.assertTrue(any("queue wait time and backpressure counts remain out of scope" in note for note in result["notes"]))

    def test_benchmark_reports_finite_lobby_sse_delivery_metric_when_samples_are_enabled(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            result = run_room_event_benchmark(
                RoomEventBenchmarkOptions(
                    output_root=Path(temp_dir),
                    events=4,
                    read_window=3,
                    warmup_events=1,
                    agent_count=2,
                    sse_samples=2,
                    cleanup=True,
                )
            )

        metric = result["metrics"]["lobby_sse_append_to_frame_ms"]
        self.assertEqual(metric["count"], 2)
        self.assertEqual(metric["samples_requested"], 2)
        self.assertEqual(metric["polling_cadence_seconds"], 0.2)
        self.assertEqual(metric["keepalive_interval_seconds"], 1.0)
        self.assertTrue(metric["enabled"])
        self.assert_metric_is_finite(metric)
        self.assertLess(metric["max_ms"], 700.0)

    def test_default_benchmark_omits_lobby_sse_delivery_metric(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            result = run_room_event_benchmark(
                RoomEventBenchmarkOptions(
                    output_root=Path(temp_dir),
                    events=4,
                    read_window=2,
                    warmup_events=1,
                    agent_count=2,
                    cleanup=True,
                )
            )

        self.assertNotIn("lobby_sse_append_to_frame_ms", result["metrics"])

    def test_flow_speaking_distribution_defines_imbalance_ratio(self):
        events = [
            {"flow_id": "f1", "flow_action": "speak", "actor_id": "a"},
            {"flow_id": "f1", "flow_action": "challenge", "actor_id": "a"},
            {"flow_id": "f1", "flow_action": "wait", "actor_id": "b"},
            {"flow_id": "f1", "flow_action": "speak", "actor_id": "b"},
            {"flow_id": "other", "flow_action": "speak", "actor_id": "c"},
        ]

        distribution = flow_speaking_distribution(events, flow_id="f1")

        self.assertEqual(distribution["counts"], {"a": 2, "b": 1})
        self.assertEqual(distribution["spread"], 1)
        self.assertEqual(distribution["imbalance_ratio"], 2.0)

    def test_cli_parser_accepts_room_benchmark_options(self):
        args = build_parser().parse_args(
            [
                "live-agent",
                "room-benchmark",
                "--output-root",
                "bench-root",
                "--events",
                "12",
                "--read-window",
                "4",
                "--warmup-events",
                "2",
                "--agent-count",
                "3",
                "--sse-samples",
                "2",
                "--json",
            ]
        )

        self.assertEqual(args.live_agent_command, "room-benchmark")
        self.assertEqual(args.output_root, "bench-root")
        self.assertEqual(args.events, 12)
        self.assertEqual(args.read_window, 4)
        self.assertEqual(args.warmup_events, 2)
        self.assertEqual(args.agent_count, 3)
        self.assertEqual(args.sse_samples, 2)
        self.assertTrue(args.as_json)

    def test_cli_parser_defaults_and_rejects_negative_sse_samples(self):
        args = build_parser().parse_args(["live-agent", "room-benchmark"])

        self.assertEqual(args.sse_samples, 0)
        with self.assertRaises(SystemExit):
            build_parser().parse_args(["live-agent", "room-benchmark", "--sse-samples", "-1"])

    def test_cli_room_benchmark_outputs_parseable_json(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            stdout = StringIO()
            with redirect_stdout(stdout):
                exit_code = main(
                    [
                        "live-agent",
                        "room-benchmark",
                        "--output-root",
                        temp_dir,
                        "--events",
                        "16",
                        "--read-window",
                        "5",
                        "--warmup-events",
                        "2",
                        "--json",
                    ]
                )
            payload = json.loads(stdout.getvalue())

        self.assertEqual(exit_code, 0)
        self.assertEqual(payload["schema_version"], 1)
        self.assertTrue(payload["cleanup_removed"])
        self.assertIn("metrics", payload)
        self.assertIn("environment", payload)

    def assert_metric_is_finite(self, metric):
        self.assertGreater(metric["count"], 0)
        for key in ("avg_ms", "p50_ms", "p95_ms", "p99_ms", "max_ms"):
            value = metric[key]
            self.assertIsInstance(value, float)
            self.assertTrue(math.isfinite(value), key)
            self.assertGreaterEqual(value, 0.0)


if __name__ == "__main__":
    unittest.main()
