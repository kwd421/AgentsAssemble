import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import UTC, datetime, timedelta
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from agentsassemble.cli import build_parser, main
from agentsassemble.live_agent_flow import (
    FlowOptions,
    LiveAgentFlowClient,
    active_flow_context,
    flow_should_yield_for_fairness,
    parse_flow_decision,
)
from agentsassemble.live_agent_flow_resources import FlowResourceRecorder, summarize_resource_samples


class FakeClock:
    def __init__(self):
        self.now = datetime(2026, 5, 24, 12, 0, tzinfo=UTC)

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += timedelta(seconds=seconds)


class LiveAgentFlowDecisionTests(unittest.TestCase):
    def test_active_flow_context_can_be_scoped_to_one_meeting(self):
        events = [
            {
                "id": "other-flow-start",
                "message": "다른 회의 시작",
                "flow_id": "flow-other",
                "flow_meeting_id": "m2",
                "flow_event_type": "started",
            },
            {
                "id": "target-flow-start",
                "message": "이 회의 시작",
                "flow_id": "flow-target",
                "flow_meeting_id": "m1",
                "flow_event_type": "started",
            },
            {
                "id": "other-flow-stop",
                "message": "다른 회의 종료",
                "flow_id": "flow-other",
                "flow_meeting_id": "m2",
                "flow_event_type": "stopped",
            },
        ]

        self.assertEqual(active_flow_context(events, meeting_id="m1")["flow_id"], "flow-target")
        self.assertIsNone(active_flow_context(events, meeting_id="missing"))

    def test_parse_flow_decision_accepts_markdown_wrapped_json(self):
        decision = parse_flow_decision(
            """```json
            {"action":"challenge","target_agent_id":"agent-b","reason":"근거 약함","message":"그건 전제가 약해요."}
            ```"""
        )

        self.assertEqual(decision.action, "challenge")
        self.assertEqual(decision.target_agent_id, "agent-b")
        self.assertEqual(decision.reason, "근거 약함")
        self.assertEqual(decision.message, "그건 전제가 약해요.")

    def test_parse_flow_decision_falls_back_to_speak_for_plain_text(self):
        decision = parse_flow_decision("그냥 보기엔 스쿠나 쪽이 더 유리해 보여.")

        self.assertEqual(decision.action, "speak")
        self.assertEqual(decision.message, "그냥 보기엔 스쿠나 쪽이 더 유리해 보여.")
        self.assertEqual(decision.reason, "")

    def test_parse_flow_decision_allows_wait_without_visible_message(self):
        decision = parse_flow_decision('{"action":"wait","reason":"이미 할 말이 나왔다","message":""}')

        self.assertEqual(decision.action, "wait")
        self.assertEqual(decision.message, "")
        self.assertEqual(decision.reason, "이미 할 말이 나왔다")


class LiveAgentFlowFairnessTests(unittest.TestCase):
    def test_leading_agent_yields_to_less_active_participant(self):
        events = [
            {"flow_id": "flow-1", "flow_action": "speak", "actor_id": "agent-a"},
            {"flow_id": "flow-1", "flow_action": "challenge", "actor_id": "agent-a"},
            {"flow_id": "flow-1", "flow_action": "speak", "actor_id": "agent-b"},
        ]

        self.assertTrue(
            flow_should_yield_for_fairness(
                events,
                flow_id="flow-1",
                agent_id="agent-a",
                participant_agent_ids=["agent-a", "agent-b"],
            )
        )

    def test_even_or_lagging_agent_does_not_yield(self):
        events = [
            {"flow_id": "flow-1", "flow_action": "speak", "actor_id": "agent-a"},
            {"flow_id": "flow-1", "flow_action": "speak", "actor_id": "agent-b"},
        ]

        self.assertFalse(
            flow_should_yield_for_fairness(
                events,
                flow_id="flow-1",
                agent_id="agent-a",
                participant_agent_ids=["agent-a", "agent-b"],
            )
        )
        self.assertFalse(
            flow_should_yield_for_fairness(
                events + [{"flow_id": "flow-1", "flow_action": "speak", "actor_id": "agent-a"}],
                flow_id="flow-1",
                agent_id="agent-b",
                participant_agent_ids=["agent-a", "agent-b"],
            )
        )

    def test_zero_count_participant_sets_the_baseline(self):
        events = [{"flow_id": "flow-1", "flow_action": "speak", "actor_id": "agent-a"}]

        self.assertTrue(
            flow_should_yield_for_fairness(
                events,
                flow_id="flow-1",
                agent_id="agent-a",
                participant_agent_ids=["agent-a", "agent-b"],
            )
        )

    def test_solo_empty_or_missing_self_baseline_does_not_deadlock(self):
        events = [{"flow_id": "flow-1", "flow_action": "speak", "actor_id": "agent-a"}]

        self.assertFalse(
            flow_should_yield_for_fairness(
                events,
                flow_id="flow-1",
                agent_id="agent-a",
                participant_agent_ids=["agent-a"],
            )
        )
        self.assertFalse(
            flow_should_yield_for_fairness(
                events,
                flow_id="flow-1",
                agent_id="agent-a",
                participant_agent_ids=[],
            )
        )
        self.assertFalse(
            flow_should_yield_for_fairness(
                events,
                flow_id="flow-1",
                agent_id="agent-a",
                participant_agent_ids=["agent-b"],
            )
        )

    def test_max_lead_allows_a_small_gap(self):
        events = [
            {"flow_id": "flow-1", "flow_action": "speak", "actor_id": "agent-a"},
            {"flow_id": "flow-1", "flow_action": "speak", "actor_id": "agent-a"},
            {"flow_id": "flow-1", "flow_action": "speak", "actor_id": "agent-b"},
        ]

        self.assertFalse(
            flow_should_yield_for_fairness(
                events,
                flow_id="flow-1",
                agent_id="agent-a",
                participant_agent_ids=["agent-a", "agent-b"],
                max_lead=1,
            )
        )

    def test_recent_window_prevents_old_turns_from_permanently_punishing_agent(self):
        events = [
            {"flow_id": "flow-1", "flow_action": "speak", "actor_id": "agent-a"},
            {"flow_id": "flow-1", "flow_action": "speak", "actor_id": "agent-a"},
            {"flow_id": "flow-1", "flow_action": "speak", "actor_id": "agent-a"},
            {"flow_id": "flow-1", "flow_action": "speak", "actor_id": "agent-b"},
        ]

        self.assertFalse(
            flow_should_yield_for_fairness(
                events,
                flow_id="flow-1",
                agent_id="agent-a",
                participant_agent_ids=["agent-a", "agent-b"],
                recent_window=1,
            )
        )

    def test_min_gap_blocks_immediate_repeat_until_another_active_participant_speaks(self):
        events = [{"flow_id": "flow-1", "flow_action": "speak", "actor_id": "agent-a"}]

        self.assertTrue(
            flow_should_yield_for_fairness(
                events,
                flow_id="flow-1",
                agent_id="agent-a",
                participant_agent_ids=["agent-a", "agent-b"],
                min_gap=1,
                start_order=True,
            )
        )
        self.assertFalse(
            flow_should_yield_for_fairness(
                events,
                flow_id="flow-1",
                agent_id="agent-b",
                participant_agent_ids=["agent-a", "agent-b"],
                min_gap=1,
                start_order=True,
            )
        )

    def test_min_gap_blocks_underrepresented_agent_from_immediate_repeat(self):
        events = (
            [{"flow_id": "flow-1", "flow_action": "speak", "actor_id": "agent-b"} for _ in range(10)]
            + [{"flow_id": "flow-1", "flow_action": "speak", "actor_id": "agent-c"} for _ in range(10)]
            + [{"flow_id": "flow-1", "flow_action": "speak", "actor_id": "agent-a"}]
        )

        self.assertTrue(
            flow_should_yield_for_fairness(
                events,
                flow_id="flow-1",
                agent_id="agent-a",
                participant_agent_ids=["agent-a", "agent-b", "agent-c"],
                min_gap=1,
                start_order=True,
            )
        )
        self.assertFalse(
            flow_should_yield_for_fairness(
                events,
                flow_id="flow-1",
                agent_id="agent-b",
                participant_agent_ids=["agent-a", "agent-b", "agent-c"],
                min_gap=1,
                start_order=True,
            )
        )

    def test_start_order_makes_empty_history_deterministic(self):
        events = []

        decisions = [
            (
                flow_should_yield_for_fairness(
                    events,
                    flow_id="flow-1",
                    agent_id="agent-a",
                    participant_agent_ids=["agent-a", "agent-b", "agent-c"],
                    start_order=True,
                ),
                flow_should_yield_for_fairness(
                    events,
                    flow_id="flow-1",
                    agent_id="agent-b",
                    participant_agent_ids=["agent-a", "agent-b", "agent-c"],
                    start_order=True,
                ),
                flow_should_yield_for_fairness(
                    events,
                    flow_id="flow-1",
                    agent_id="agent-c",
                    participant_agent_ids=["agent-a", "agent-b", "agent-c"],
                    start_order=True,
                ),
            )
            for _ in range(100)
        ]

        self.assertEqual(set(decisions), {(False, True, True)})

    def test_start_order_ties_use_least_recent_speaker_after_history_exists(self):
        events = [
            {"flow_id": "flow-1", "flow_action": "speak", "actor_id": "agent-a"},
            {"flow_id": "flow-1", "flow_action": "speak", "actor_id": "agent-b"},
            {"flow_id": "flow-1", "flow_action": "speak", "actor_id": "agent-c"},
        ]
        participants = ["agent-c", "agent-a", "agent-b"]

        self.assertFalse(
            flow_should_yield_for_fairness(
                events,
                flow_id="flow-1",
                agent_id="agent-a",
                participant_agent_ids=participants,
                min_gap=0,
                start_order=True,
            )
        )
        self.assertTrue(
            flow_should_yield_for_fairness(
                events,
                flow_id="flow-1",
                agent_id="agent-c",
                participant_agent_ids=participants,
                min_gap=0,
                start_order=True,
            )
        )

    def test_start_order_ties_rotate_by_least_recent_eligible_speaker(self):
        events = [
            {"flow_id": "flow-1", "flow_action": "speak", "actor_id": "agent-a"},
            {"flow_id": "flow-1", "flow_action": "speak", "actor_id": "agent-b"},
            {"flow_id": "flow-1", "flow_action": "speak", "actor_id": "agent-c"},
            {"flow_id": "flow-1", "flow_action": "speak", "actor_id": "agent-a"},
        ]
        participants = ["agent-c", "agent-a", "agent-b"]

        self.assertFalse(
            flow_should_yield_for_fairness(
                events,
                flow_id="flow-1",
                agent_id="agent-b",
                participant_agent_ids=participants,
                min_gap=0,
                start_order=True,
            )
        )
        self.assertTrue(
            flow_should_yield_for_fairness(
                events,
                flow_id="flow-1",
                agent_id="agent-c",
                participant_agent_ids=participants,
                min_gap=0,
                start_order=True,
            )
        )


class LiveAgentFlowCliTests(unittest.TestCase):
    def test_flow_parser_has_timebox_defaults(self):
        args = build_parser().parse_args(["live-agent", "flow", "--meeting-id", "m1", "--topic", "고죠 vs 스쿠나"])

        self.assertEqual(args.live_agent_command, "flow")
        self.assertEqual(args.meeting_id, "m1")
        self.assertEqual(args.topic, "고죠 vs 스쿠나")
        self.assertEqual(args.duration_seconds, 180.0)
        self.assertEqual(args.tick_interval, 2.0)
        self.assertEqual(args.cooldown, 8.0)
        self.assertEqual(args.max_agent_turns, 0)
        self.assertEqual(args.max_total_turns, 0)
        self.assertEqual(args.max_silence_seconds, 20.0)

    def test_flow_options_default_to_timeboxed_unlimited_turns(self):
        options = FlowOptions()

        self.assertEqual(options.duration_seconds, 180.0)
        self.assertEqual(options.max_agent_turns, 0)
        self.assertEqual(options.max_total_turns, 0)

    def test_flow_cli_is_disabled_and_does_not_write_resource_report(self):
        calls = []

        def request_json(url, *, method="GET", payload=None, timeout_seconds=None):
            del timeout_seconds
            calls.append((url, method, payload))
            if url.endswith("/api/live-agent-flow/start"):
                return {"flow": {"flow_id": "flow-cli", "status": "finished", "total_turns": 1, "agent_count": 2}}
            if url.endswith("/api/local-resources"):
                return {
                    "status": "ok",
                    "generated_at": "2026-06-08T00:00:00+00:00",
                    "cpu_count": 8,
                    "load_average": {"one": 0.1, "five": 0.1, "fifteen": 0.1},
                    "summary": {
                        "process_count": 3,
                        "total_cpu_pct": 12.5,
                        "total_rss_kb": 16384,
                        "role_breakdown": {
                            "supervised_resident": {"count": 2, "cpu_pct": 9.5, "rss_kb": 12288}
                        },
                    },
                    "processes": [],
                }
            raise AssertionError(f"unexpected request {method} {url}")

        with tempfile.TemporaryDirectory() as temp_dir:
            report_path = Path(temp_dir) / "flow-resources.json"
            stdout = StringIO()
            stderr = StringIO()
            with patch("agentsassemble.cli._request_json", side_effect=request_json), redirect_stdout(stdout), redirect_stderr(stderr):
                exit_code = main(
                    [
                        "live-agent",
                        "flow",
                        "--server",
                        "http://room.local",
                        "--meeting-id",
                        "m1",
                        "--topic",
                        "비올때 뭘 해야하는가",
                        "--duration-seconds",
                        "1",
                        "--resource-report",
                        str(report_path),
                        "--resource-sample-interval",
                        "0",
                        "--runtime-mode",
                        "runtime_managed_room_turn",
                    ]
                )

            self.assertFalse(report_path.exists())

        self.assertEqual(exit_code, 2)
        self.assertEqual(calls, [])
        self.assertEqual(stdout.getvalue(), "")
        self.assertIn("Play/free flow is disabled", stderr.getvalue())


class LiveAgentFlowClientTests(unittest.TestCase):
    def test_client_starts_waits_and_finishes_flow_without_starting_providers(self):
        clock = FakeClock()
        calls = []
        statuses = [
            {"flow": {"flow_id": "flow-1", "status": "running", "total_turns": 0}},
            {"flow": {"flow_id": "flow-1", "status": "running", "total_turns": 2}},
            {"flow": {"flow_id": "flow-1", "status": "finished", "total_turns": 2}},
        ]

        def request_json(url, *, method="GET", payload=None, timeout_seconds=None):
            calls.append((url, method, payload, timeout_seconds))
            if url.endswith("/api/live-agent-flow/start"):
                return {"flow": {"flow_id": "flow-1", "status": "running", "total_turns": 0}}
            if "/api/live-agent-flow?" in url:
                return statuses.pop(0)
            raise AssertionError(f"unexpected request {method} {url}")

        client = LiveAgentFlowClient(
            server="http://room.local",
            request_json=request_json,
            sleep_fn=clock.sleep,
            now_fn=clock,
        )

        result = client.run(
            meeting_id="m1",
            topic="고죠 vs 스쿠나",
            options=FlowOptions(duration_seconds=6, tick_interval=2, max_total_turns=3),
            sample_fn=lambda payload: calls.append(("sample", "CALLBACK", payload, None)),
        )

        self.assertEqual(result["flow"]["status"], "finished")
        self.assertEqual(result["flow"]["total_turns"], 2)
        start_payloads = [payload for url, method, payload, _ in calls if url.endswith("/api/live-agent-flow/start")]
        self.assertEqual(start_payloads[0]["meeting_id"], "m1")
        self.assertEqual(start_payloads[0]["topic"], "고죠 vs 스쿠나")
        self.assertEqual(start_payloads[0]["duration_seconds"], 6)
        self.assertNotIn("command", json.dumps(calls, ensure_ascii=False))
        self.assertTrue(any(url == "sample" for url, *_ in calls))


class LiveAgentFlowResourceReportTests(unittest.TestCase):
    def test_resource_recorder_samples_and_writes_peak_supervised_usage(self):
        clock = FakeClock()
        calls = []

        def request_json(url, **kwargs):
            calls.append((url, kwargs))
            return {
                "status": "ok",
                "generated_at": clock().isoformat(),
                "cpu_count": 8,
                "load_average": {"one": 0.1, "five": 0.1, "fifteen": 0.1},
                "summary": {
                    "process_count": 2,
                    "total_cpu_pct": 9.0,
                    "total_rss_kb": 12000,
                    "role_breakdown": {
                        "supervised_resident": {"count": 1, "cpu_pct": 7.5, "rss_kb": 8192},
                        "agentsassemble": {"count": 1, "cpu_pct": 1.5, "rss_kb": 3808},
                        "other": {"count": 0, "cpu_pct": 0.0, "rss_kb": 0},
                    },
                    "attention": [],
                },
                "processes": [],
            }

        recorder = FlowResourceRecorder(
            server="http://room.local/",
            request_json=request_json,
            sample_interval_seconds=5,
            now_fn=clock,
        )
        recorder.sample({"flow": {"flow_id": "flow-1", "status": "running", "total_turns": 1}})
        recorder.sample({"flow": {"flow_id": "flow-1", "status": "running", "total_turns": 2}})
        clock.sleep(5)
        recorder.sample({"flow": {"flow_id": "flow-1", "status": "finished", "total_turns": 2}})

        with tempfile.TemporaryDirectory() as temp_dir:
            report_path = Path(temp_dir) / "resources.json"
            report = recorder.write_report(
                report_path,
                meeting_id="m1",
                topic="비올때 뭘 해야하는가",
                flow_result={"flow": {"flow_id": "flow-1", "status": "finished"}},
                runtime_mode="runtime_managed_room_turn",
            )

        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0][0], "http://room.local/api/local-resources")
        self.assertEqual(report["sample_count"], 2)
        self.assertEqual(report["summary"]["peak_supervised_rss_kb"], 8192)
        self.assertEqual(report["summary"]["peak_supervised_cpu_pct"], 7.5)
        self.assertEqual(report["runtime_mode"], "runtime_managed_room_turn")

    def test_resource_summary_keeps_absent_supervised_usage_as_zero(self):
        summary = summarize_resource_samples(
            [
                {
                    "resources": {
                        "status": "ok",
                        "summary": {
                            "process_count": 1,
                            "total_cpu_pct": 2.0,
                            "total_rss_kb": 2048,
                            "role_breakdown": {},
                        },
                    }
                }
            ]
        )

        self.assertEqual(summary["sample_count"], 1)
        self.assertEqual(summary["peak_supervised_rss_kb"], 0)
        self.assertEqual(summary["peak_supervised_cpu_pct"], 0.0)


if __name__ == "__main__":
    unittest.main()
