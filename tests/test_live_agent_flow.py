import json
import unittest
from datetime import UTC, datetime, timedelta

from agentsassemble.cli import build_parser
from agentsassemble.live_agent_flow import (
    FlowOptions,
    LiveAgentFlowClient,
    active_flow_context,
    parse_flow_decision,
)


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


class LiveAgentFlowCliTests(unittest.TestCase):
    def test_flow_parser_has_timebox_defaults(self):
        args = build_parser().parse_args(["live-agent", "flow", "--meeting-id", "m1", "--topic", "고죠 vs 스쿠나"])

        self.assertEqual(args.live_agent_command, "flow")
        self.assertEqual(args.meeting_id, "m1")
        self.assertEqual(args.topic, "고죠 vs 스쿠나")
        self.assertEqual(args.duration_seconds, 180.0)
        self.assertEqual(args.tick_interval, 2.0)
        self.assertEqual(args.cooldown, 8.0)
        self.assertEqual(args.max_agent_turns, 12)
        self.assertEqual(args.max_total_turns, 30)
        self.assertEqual(args.max_silence_seconds, 20.0)


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
        )

        self.assertEqual(result["flow"]["status"], "finished")
        self.assertEqual(result["flow"]["total_turns"], 2)
        start_payloads = [payload for url, method, payload, _ in calls if url.endswith("/api/live-agent-flow/start")]
        self.assertEqual(start_payloads[0]["meeting_id"], "m1")
        self.assertEqual(start_payloads[0]["topic"], "고죠 vs 스쿠나")
        self.assertEqual(start_payloads[0]["duration_seconds"], 6)
        self.assertNotIn("command", json.dumps(calls, ensure_ascii=False))


if __name__ == "__main__":
    unittest.main()
