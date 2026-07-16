from __future__ import annotations

import threading
import unittest

from agentsassemble.room.bridge_stop_confirmation import (
    BridgeStopConfirmationError,
    ExternalBridgeStopCoordinator,
)


class ExternalBridgeStopCoordinatorTests(unittest.TestCase):
    def test_only_the_requested_generation_can_confirm_stop(self):
        coordinator = ExternalBridgeStopCoordinator(timeout_seconds=1.0)
        delivered: list[dict[str, object]] = []
        control_delivered = threading.Event()
        results: list[dict[str, object]] = []

        def send(message: dict[str, object]) -> bool:
            delivered.append(message)
            control_delivered.set()
            return True

        thread = threading.Thread(
            target=lambda: results.append(
                coordinator.request(
                    "general",
                    "codex",
                    generation=4,
                    send=send,
                )
            ),
            daemon=True,
        )
        thread.start()
        self.assertTrue(control_delivered.wait(timeout=1.0))
        control_id = str(delivered[0]["control_id"])

        with self.assertRaises(BridgeStopConfirmationError) as stale:
            coordinator.confirm(
                "general",
                "codex",
                generation=3,
                payload={"control_id": control_id, "stopped": True},
            )
        self.assertEqual(stale.exception.code, "stale_bridge_generation")

        coordinator.confirm(
            "general",
            "codex",
            generation=4,
            payload={"control_id": control_id, "stopped": True},
        )
        thread.join(timeout=1)

        self.assertFalse(thread.is_alive())
        self.assertTrue(results[0]["stopped"])

    def test_confirmation_persists_the_effect_before_releasing_stop(self):
        coordinator = ExternalBridgeStopCoordinator(timeout_seconds=1.0)
        delivered: list[dict[str, object]] = []
        control_delivered = threading.Event()
        results: list[dict[str, object]] = []
        persisted: list[tuple[str, bool]] = []

        def send(message: dict[str, object]) -> bool:
            delivered.append(message)
            control_delivered.set()
            return True

        thread = threading.Thread(
            target=lambda: results.append(
                coordinator.request(
                    "general",
                    "codex",
                    generation=4,
                    operation_id="operation-123",
                    send=send,
                )
            ),
            daemon=True,
        )
        thread.start()
        self.assertTrue(control_delivered.wait(timeout=1.0))

        coordinator.confirm(
            "general",
            "codex",
            generation=4,
            payload={"control_id": delivered[0]["control_id"], "stopped": True},
            before_release=lambda operation_id, result: persisted.append(
                (operation_id, bool(result["stopped"]))
            ),
        )
        thread.join(timeout=1.0)

        self.assertEqual(delivered[0]["control_id"], "stop-operation-123")
        self.assertEqual(persisted, [("operation-123", True)])
        self.assertEqual(results[0]["stopped"], True)

    def test_missing_confirmation_is_an_explicit_timeout(self):
        coordinator = ExternalBridgeStopCoordinator(timeout_seconds=0.01)

        with self.assertRaises(BridgeStopConfirmationError) as timeout:
            coordinator.request(
                "general",
                "codex",
                generation=1,
                send=lambda _message: True,
            )

        self.assertEqual(timeout.exception.code, "external_stop_unconfirmed")

    def test_delivery_exception_is_an_explicit_control_failure(self):
        coordinator = ExternalBridgeStopCoordinator(timeout_seconds=0.01)

        def fail_delivery(_message: dict[str, object]) -> bool:
            raise OSError("socket closed")

        with self.assertRaises(BridgeStopConfirmationError) as failed:
            coordinator.request(
                "general",
                "codex",
                generation=1,
                send=fail_delivery,
            )

        self.assertEqual(failed.exception.code, "bridge_stop_delivery_failed")


if __name__ == "__main__":
    unittest.main()
