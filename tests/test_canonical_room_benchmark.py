import tempfile
import unittest
from pathlib import Path

from agentsassemble.canonical_room_benchmark import (
    CanonicalRoomBenchmarkOptions,
    run_canonical_room_benchmark,
)
from agentsassemble.cli import build_parser


class CanonicalRoomBenchmarkTests(unittest.TestCase):
    def test_room_benchmark_cli_defaults_to_long_room_cardinality(self):
        args = build_parser().parse_args(["room", "benchmark"])

        self.assertEqual(args.events, 100_000)
        self.assertEqual(args.agent_count, 10)
        self.assertEqual(args.read_window, 200)
        self.assertEqual(args.samples, 50)
        self.assertFalse(args.keep_output)

    def test_benchmark_uses_indexed_bounded_canonical_reads(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            result = run_canonical_room_benchmark(
                CanonicalRoomBenchmarkOptions(
                    output_root=Path(temp_dir),
                    events=2_000,
                    agent_count=3,
                    read_window=50,
                    samples=5,
                    cleanup=True,
                )
            )

        self.assertEqual(result["benchmark"], "canonical_room_sqlite_v1")
        self.assertEqual(result["status"], "ok")
        self.assertTrue(result["cleanup_removed"])
        self.assertEqual(result["measured_event_count"], 2_006)
        self.assertTrue(all(result["acceptance"].values()))
        self.assertLessEqual(result["context_bounds"]["max_events"], 12)
        self.assertLessEqual(result["context_bounds"]["max_chars"], 4000)
        self.assertFalse(result["query_plan"]["full_scan_detected"])
        self.assertTrue(
            any("SEARCH room_events" in detail for detail in result["query_plan"]["details"])
        )
        for metric_name in (
            "append_ms",
            "latest_window_ms",
            "reconnect_after_seq_ms",
            "history_before_seq_ms",
            "agent_context_ms",
            "event_by_id_ms",
            "session_lookup_ms",
        ):
            self.assertEqual(result["metrics"][metric_name]["count"], 5)


if __name__ == "__main__":
    unittest.main()
