from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from agentsassemble.room_admission_workflow_maintenance import AdmissionWorkflowSelection
from agentsassemble.cli import build_parser, main
from agentsassemble.room_invite_repository import JsonInviteSessionRepository


def _terminal_workflow(workflow_id: str) -> dict[str, object]:
    return {
        "workflow_id": workflow_id,
        "request_id": f"request-{workflow_id}",
        "token_fingerprint": f"token-{workflow_id}",
        "payload_hash": f"payload-{workflow_id}",
        "status": "completed",
        "resume_phase": "completed",
        "room_id": "room-a",
        "created_at": "2026-07-01T00:00:00+00:00",
        "updated_at": "2026-07-01T00:00:00+00:00",
    }


class AdmissionWorkflowMaintenanceCommandTests(unittest.TestCase):
    def test_parser_defaults_to_non_destructive_dry_run(self) -> None:
        args = build_parser().parse_args(
            [
                "room",
                "purge-admission-workflows",
                "--before",
                "2026-07-16T00:00:00+00:00",
            ]
        )

        self.assertFalse(args.apply)
        self.assertEqual(args.room_repository_backend, "sqlite")

    def test_cli_dry_run_then_explicit_apply(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            path = root / ".agentsassemble" / "room-invite-state.json"
            repository = JsonInviteSessionRepository(path)
            repository.create_admission_workflow(
                "terminal-workflow",
                _terminal_workflow("terminal-workflow"),
            )

            dry_output = io.StringIO()
            with redirect_stdout(dry_output):
                dry_status = main(
                    [
                        "room",
                        "purge-admission-workflows",
                        "--output-root",
                        str(root),
                        "--before",
                        "2026-07-16T00:00:00+00:00",
                        "--json",
                    ]
                )
            dry_result = json.loads(dry_output.getvalue())

            self.assertEqual(dry_status, 0)
            self.assertEqual(dry_result["mode"], "dry_run")
            self.assertEqual(dry_result["selected_count"], 1)
            self.assertEqual(dry_result["purged_count"], 0)
            self.assertIsNotNone(
                JsonInviteSessionRepository(path).admission_workflow(
                    "terminal-workflow"
                )
            )

            apply_output = io.StringIO()
            with redirect_stdout(apply_output):
                apply_status = main(
                    [
                        "room",
                        "purge-admission-workflows",
                        "--output-root",
                        str(root),
                        "--before",
                        "2026-07-16T00:00:00+00:00",
                        "--apply",
                        "--json",
                    ]
                )
            apply_result = json.loads(apply_output.getvalue())

            self.assertEqual(apply_status, 0)
            self.assertEqual(apply_result["mode"], "apply")
            self.assertEqual(apply_result["purged_count"], 1)
            self.assertIsNone(
                JsonInviteSessionRepository(path).admission_workflow(
                    "terminal-workflow"
                )
            )

    def test_report_workflow_ids_are_bounded(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = JsonInviteSessionRepository(
                Path(temp_dir) / "room-invite-state.json"
            )
            for index in range(101):
                workflow_id = f"workflow-{index:03d}"
                repository.create_admission_workflow(
                    workflow_id,
                    _terminal_workflow(workflow_id),
                )

            report = repository.purge_admission_workflows(
                AdmissionWorkflowSelection(room_id="room-a"),
                apply=False,
            )

            self.assertEqual(report.selected_count, 101)
            self.assertEqual(len(report.workflow_ids), 100)
            self.assertTrue(report.workflow_ids_truncated)


if __name__ == "__main__":
    unittest.main()
