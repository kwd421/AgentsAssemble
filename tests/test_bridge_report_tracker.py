from __future__ import annotations

import unittest

from agentsassemble.providers.bridge_protocol import BridgeReportRejected, BridgeReportTimeout
from agentsassemble.providers.bridge_report_tracker import BridgeReportTracker


class BridgeReportTrackerTests(unittest.TestCase):
    def test_request_correlates_ack_received_by_transport_pump(self):
        tracker = BridgeReportTracker(timeout_seconds=0.1)
        queued: list[dict[str, object]] = []

        def send(request_id: str) -> None:
            queued.append({"op": "ack", "request_id": request_id, "accepted": True})

        def pump() -> bool:
            if not queued:
                return False
            tracker.resolve_message(queued.pop(0))
            return True

        result = tracker.request(
            "bridge.ready",
            send=send,
            pump=pump,
            is_closed=lambda: False,
            wait_interval_seconds=0.001,
        )

        self.assertTrue(result["accepted"])

    def test_request_surfaces_correlated_nack_code(self):
        tracker = BridgeReportTracker(timeout_seconds=0.1)
        queued: list[dict[str, object]] = []

        def send(request_id: str) -> None:
            queued.append(
                {
                    "op": "nack",
                    "request_id": request_id,
                    "error": {"code": "provider_model_mismatch", "message": "wrong model"},
                }
            )

        def pump() -> bool:
            tracker.resolve_message(queued.pop(0))
            return True

        with self.assertRaises(BridgeReportRejected) as rejected:
            tracker.request(
                "message.final",
                send=send,
                pump=pump,
                is_closed=lambda: False,
                wait_interval_seconds=0.001,
            )

        self.assertEqual(rejected.exception.code, "provider_model_mismatch")
        self.assertEqual(str(rejected.exception), "wrong model")

    def test_request_times_out_without_a_response(self):
        tracker = BridgeReportTracker(timeout_seconds=0.01)

        with self.assertRaises(BridgeReportTimeout) as timeout:
            tracker.request(
                "bridge.ready",
                send=lambda _request_id: None,
                pump=lambda: False,
                is_closed=lambda: False,
                wait_interval_seconds=0.001,
            )

        self.assertEqual(timeout.exception.code, "bridge_report_timeout")

    def test_unknown_ack_is_consumed_without_resolving_another_request(self):
        tracker = BridgeReportTracker(timeout_seconds=0.1)

        self.assertTrue(
            tracker.resolve_message({"op": "ack", "request_id": "unknown", "accepted": True})
        )
        self.assertFalse(tracker.resolve_message({"op": "turn.assign"}))


if __name__ == "__main__":
    unittest.main()
