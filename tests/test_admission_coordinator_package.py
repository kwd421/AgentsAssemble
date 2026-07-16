from __future__ import annotations

import unittest

import agentsassemble.room_admission_coordinator as compatibility_coordinator
import agentsassemble.room_admission_saga as compatibility_saga
from agentsassemble.admission import coordinator as owned_coordinator
from agentsassemble.admission import saga as owned_saga


class AdmissionCoordinatorPackageTests(unittest.TestCase):
    def test_root_modules_export_owned_admission_workflow(self) -> None:
        self.assertIs(
            compatibility_coordinator.RoomAdmissionCoordinator,
            owned_coordinator.RoomAdmissionCoordinator,
        )
        self.assertIs(
            compatibility_coordinator.AdmissionIdempotencyConflict,
            owned_coordinator.AdmissionIdempotencyConflict,
        )
        self.assertIs(
            compatibility_saga.RoomAdmissionSaga,
            owned_saga.RoomAdmissionSaga,
        )
        self.assertIs(
            compatibility_saga.RoomAdmissionCompensationFailed,
            owned_saga.RoomAdmissionCompensationFailed,
        )


if __name__ == "__main__":
    unittest.main()
