import tempfile
import unittest
from pathlib import Path

from agentsassemble.legacy.live_agent.probe import (
    LegacyLiveAgentProbeService,
    probe_timeout_seconds,
)
from agentsassemble.legacy.live_agent.runtime.operations import read_live_agent_operations


class LegacyLiveAgentProbeServiceTests(unittest.TestCase):
    def test_success_records_ids_without_reply_content(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            calls: list[tuple[Path, str, float]] = []

            def run_probe(
                output_root: Path,
                agent_id: str,
                *,
                timeout_seconds: float,
            ) -> dict[str, object]:
                calls.append((output_root, agent_id, timeout_seconds))
                return {
                    "status": "ok",
                    "source_event_id": "source-a",
                    "reply_event_id": "reply-a",
                    "reply": {"message": "private probe reply"},
                }

            result = LegacyLiveAgentProbeService(root, probe_runner=run_probe).run(
                "agent-a",
                {"timeout_seconds": 3},
            )
            operation = read_live_agent_operations(root, operation="probe.run")[0]

        self.assertEqual(calls, [(root, "agent-a", 3.0)])
        self.assertEqual(result["status"], "ok")
        self.assertEqual(operation["status"], "success")
        self.assertEqual(operation["details"]["source_event_id"], "source-a")
        self.assertEqual(operation["details"]["reply_event_id"], "reply-a")
        self.assertNotIn("private probe reply", str(operation))

    def test_non_ok_result_records_failed_operation_and_capped_timeout(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            observed_timeout: list[float] = []

            def run_probe(*args: object, timeout_seconds: float) -> dict[str, object]:
                observed_timeout.append(timeout_seconds)
                return {"status": "timeout", "source_event_id": "source-a"}

            LegacyLiveAgentProbeService(root, probe_runner=run_probe).run(
                "agent-a",
                {"timeout_seconds": 300},
            )
            operation = read_live_agent_operations(root, operation="probe.run")[0]

        self.assertEqual(observed_timeout, [240.0])
        self.assertEqual(operation["status"], "failed")
        self.assertEqual(operation["details"]["result_status"], "timeout")
        self.assertEqual(operation["details"]["timeout_seconds"], 240.0)

    def test_domain_failure_records_timeout_and_reraises(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)

            def run_probe(*args: object, **kwargs: object) -> dict[str, object]:
                raise ValueError("Live agent missing was not found.")

            with self.assertRaisesRegex(ValueError, "was not found"):
                LegacyLiveAgentProbeService(root, probe_runner=run_probe).run(
                    "missing",
                    {"timeout": 5},
                )
            operation = read_live_agent_operations(root, operation="probe.run")[0]

        self.assertEqual(operation["status"], "failed")
        self.assertEqual(operation["target_id"], "missing")
        self.assertEqual(operation["details"], {"result_status": "failed", "timeout_seconds": 5.0})

    def test_timeout_normalization_preserves_existing_defaults_and_bounds(self) -> None:
        self.assertEqual(probe_timeout_seconds({}), 12.0)
        self.assertEqual(probe_timeout_seconds({"timeout": "bad"}), 12.0)
        self.assertEqual(probe_timeout_seconds({"timeout": float("nan")}), 12.0)
        self.assertEqual(probe_timeout_seconds({"timeout": -1}), 0.0)
        self.assertEqual(probe_timeout_seconds({"timeout": 999}), 240.0)


if __name__ == "__main__":
    unittest.main()
