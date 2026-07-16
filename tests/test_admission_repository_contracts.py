from __future__ import annotations

import unittest

import agentsassemble.room_invite_repository as compatibility_repository
from agentsassemble.admission import repository as owned_repository
from agentsassemble.admission.workflow_record import (
    validate_admission_workflow_record,
)


class AdmissionRepositoryContractPackageTests(unittest.TestCase):
    def test_root_repository_exports_owned_contracts(self) -> None:
        self.assertIs(
            compatibility_repository.InviteSessionRepository,
            owned_repository.InviteSessionRepository,
        )
        self.assertIs(
            compatibility_repository.UnconfiguredInviteSessionRepository,
            owned_repository.UnconfiguredInviteSessionRepository,
        )
        self.assertIs(
            compatibility_repository.InviteRepositoryError,
            owned_repository.InviteRepositoryError,
        )

    def test_workflow_record_discards_unapproved_secret_fields(self) -> None:
        record = validate_admission_workflow_record(
            {
                "workflow_id": "workflow-1",
                "request_id": "request-1",
                "token_fingerprint": "token-fingerprint",
                "payload_hash": "payload-hash",
                "status": "started",
                "raw_invite_token": "must-not-persist",
                "session_token": "must-not-persist",
            },
            workflow_id="workflow-1",
        )

        self.assertNotIn("raw_invite_token", record)
        self.assertNotIn("session_token", record)


if __name__ == "__main__":
    unittest.main()
