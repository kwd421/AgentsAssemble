from __future__ import annotations

import unittest

import agentsassemble.admission as compatibility_admission
import agentsassemble.room_admission as compatibility_preflight
from agentsassemble.admission import preflight as owned_preflight
from agentsassemble.admission import projection as owned_projection
from agentsassemble.legacy import admission_projection
from agentsassemble.legacy import meeting_admission


class AdmissionPackageTests(unittest.TestCase):
    def test_former_meeting_admission_exports_remain_lazy_compatible(self) -> None:
        self.assertIs(
            compatibility_admission.build_admission_decisions,
            meeting_admission.build_admission_decisions,
        )
        self.assertIs(
            compatibility_admission.MEETING_UNSAFE_PERMISSIONS,
            meeting_admission.MEETING_UNSAFE_PERMISSIONS,
        )

    def test_room_admission_root_module_exports_the_owned_preflight(self) -> None:
        self.assertIs(
            compatibility_preflight.RoomAdmissionService,
            owned_preflight.RoomAdmissionService,
        )

    def test_legacy_projection_module_exports_the_owned_contract(self) -> None:
        self.assertIs(
            admission_projection.LegacyAdmissionParticipant,
            owned_projection.LegacyAdmissionParticipant,
        )
        self.assertIs(
            admission_projection.LegacyAdmissionProjection,
            owned_projection.LegacyAdmissionProjection,
        )


if __name__ == "__main__":
    unittest.main()
