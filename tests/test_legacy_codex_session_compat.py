import json
import tempfile
import unittest
from pathlib import Path

from agentsassemble.legacy_codex_session_compat import (
    LegacyCodexSessionCompatibilityService,
    LegacyCodexSessionError,
)
from agentsassemble.meeting_events import write_live_state


class EmptySupervisor:
    def snapshot_groups(self):
        return []

    def list_groups(self):
        return []


class LegacyCodexSessionCompatibilityServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name)
        self.operations: list[dict[str, object]] = []
        self.ensure_calls: list[tuple[dict[str, object], str]] = []
        self.restart_calls: list[dict[str, object]] = []
        self.service = LegacyCodexSessionCompatibilityService(
            output_root=self.root,
            processes=EmptySupervisor(),
            ensure_session=self._ensure_session,
            restart_session=self._restart_session,
            record_operation=lambda _root, **kwargs: self.operations.append(kwargs),
        )

    def _ensure_session(
        self,
        _root,
        _processes,
        payload: dict[str, object],
        *,
        default_server: str,
    ) -> dict[str, object]:
        self.ensure_calls.append((dict(payload), default_server))
        return {
            "status": "ready",
            "action": "resume",
            "meeting_id": payload["meeting_id"],
            "group_id": payload["group_id"],
        }

    def _restart_session(self, _root, _processes, payload: dict[str, object]) -> dict[str, object]:
        self.restart_calls.append(dict(payload))
        return {
            "status": "ready",
            "meeting_id": payload["meeting_id"],
            "group_id": payload["group_id"],
        }

    def _write_meeting(self, *, rounds=None) -> None:
        meeting_dir = self.root / "meetings" / "m1"
        meeting_dir.mkdir(parents=True)
        write_live_state(
            meeting_dir,
            {
                "meeting_id": "m1",
                "roles": [{"id": "critic", "display_name": "Critic"}],
                "debate_rounds": list(rounds or []),
                "live_status": "running",
            },
        )

    def test_invite_writes_config_and_records_only_safe_binding_details(self) -> None:
        self._write_meeting()

        result = self.service.invite(
            {"meeting_id": "m1", "role_id": "critic", "session_id": "secret-session"}
        )

        self.assertEqual(result["binding"]["role_id"], "critic")
        self.assertTrue((self.root / "codex-live-session.local.json").exists())
        self.assertEqual(self.operations[0]["operation"], "codex_session.invite")
        self.assertEqual(self.operations[0]["status"], "success")
        self.assertNotIn("secret-session", json.dumps(self.operations))
        self.assertNotIn("config_path", json.dumps(self.operations))

    def test_invite_failure_records_safe_error_without_session_id(self) -> None:
        self._write_meeting()

        with self.assertRaises(LegacyCodexSessionError) as raised:
            self.service.invite(
                {"meeting_id": "m1", "role_id": "missing", "session_id": "secret-session"}
            )

        self.assertEqual(str(raised.exception), "Codex live session invite failed.")
        self.assertEqual(raised.exception.details, {})
        self.assertEqual(self.operations[0]["status"], "failed")
        self.assertNotIn("secret-session", json.dumps(self.operations))

    def test_join_writes_configs_calls_ensure_and_records_safe_result(self) -> None:
        self._write_meeting()

        result = self.service.join(
            {
                "meeting_id": "m1",
                "role_id": "critic",
                "session_id": "secret-session",
                "connect_timeout_seconds": 0,
            },
            default_server="http://room.local:8765",
        )

        self.assertEqual(result["status"], "ready")
        self.assertEqual(len(self.ensure_calls), 1)
        self.assertEqual(self.ensure_calls[0][1], "http://room.local:8765")
        self.assertEqual(self.restart_calls, [])
        self.assertTrue((self.root / "live-agents.codex-session.local.json").exists())
        self.assertEqual(self.operations[0]["operation"], "codex_session.join")
        self.assertEqual(self.operations[0]["status"], "success")
        operation_blob = json.dumps(self.operations)
        self.assertNotIn("secret-session", operation_blob)
        self.assertNotIn("codex-live-session.local.json", operation_blob)

    def test_join_rejects_started_round_before_writing_or_ensuring(self) -> None:
        self._write_meeting(rounds=[{"id": "round-1", "status": "answered"}])

        with self.assertRaises(LegacyCodexSessionError) as raised:
            self.service.join(
                {"meeting_id": "m1", "role_id": "critic", "session_id": "secret-session"},
                default_server="http://room.local:8765",
            )

        self.assertEqual(str(raised.exception), "Codex live session join failed.")
        self.assertEqual(raised.exception.details, {"meeting_id": "m1", "role_id": "critic"})
        self.assertEqual(self.ensure_calls, [])
        self.assertFalse((self.root / "codex-live-session.local.json").exists())
        self.assertEqual(self.operations[0]["status"], "failed")
        self.assertNotIn("secret-session", json.dumps(self.operations))


if __name__ == "__main__":
    unittest.main()
