from __future__ import annotations

import unittest

import agentsassemble.room_admission_workflow_maintenance as compatibility_models
import agentsassemble.room_admission_workflow_maintenance_command as compatibility_command
from agentsassemble.admission import maintenance as owned_models
from agentsassemble.admission import maintenance_command as owned_command


class AdmissionMaintenancePackageTests(unittest.TestCase):
    def test_root_modules_export_owned_maintenance_boundaries(self) -> None:
        self.assertIs(
            compatibility_models.AdmissionWorkflowSelection,
            owned_models.AdmissionWorkflowSelection,
        )
        self.assertIs(
            compatibility_models.PurgeReport,
            owned_models.PurgeReport,
        )
        self.assertIs(
            compatibility_command.purge_admission_workflows,
            owned_command.purge_admission_workflows,
        )


if __name__ == "__main__":
    unittest.main()
