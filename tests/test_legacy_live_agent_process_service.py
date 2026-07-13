import tempfile
import unittest
from pathlib import Path

from agentsassemble.legacy_live_agent_process_service import (
    LegacyLiveAgentProcessMutationService,
    LegacyProcessMutationActions,
    LegacyProcessMutationError,
)


class LegacyLiveAgentProcessMutationServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.operations: list[dict[str, object]] = []

    def _service(self, *, start) -> LegacyLiveAgentProcessMutationService:
        return LegacyLiveAgentProcessMutationService(
            Path(self.temp.name),
            processes=object(),
            actions=LegacyProcessMutationActions(
                start=start,
                stop_running=lambda *_args, **_kwargs: {"result": {}},
                stop=lambda *_args, **_kwargs: {"group": {}},
                restart=lambda *_args, **_kwargs: {"group": {}},
                recover=lambda *_args, **_kwargs: {"group": {}},
            ),
            record_operation=lambda _root, **fields: self.operations.append(fields),
        )

    def test_spawn_os_error_is_typed_and_audited(self) -> None:
        service = self._service(
            start=lambda *_args, **_kwargs: (_ for _ in ()).throw(PermissionError("denied"))
        )

        with self.assertRaises(LegacyProcessMutationError):
            service.start({"group_id": "group-a"}, default_server="http://room.local")

        self.assertEqual(self.operations[-1]["status"], "failed")
        self.assertEqual(self.operations[-1]["operation"], "process.start")

    def test_unexpected_error_is_audited_and_propagated(self) -> None:
        service = self._service(
            start=lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("programmer failure"))
        )

        with self.assertRaisesRegex(RuntimeError, "programmer failure"):
            service.start({"group_id": "group-a"}, default_server="http://room.local")

        self.assertEqual(self.operations[-1]["details"]["exception_type"], "RuntimeError")
        self.assertEqual(self.operations[-1]["details"]["failure_phase"], "start")


if __name__ == "__main__":
    unittest.main()
