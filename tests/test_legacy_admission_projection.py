from __future__ import annotations

import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

from agentsassemble.legacy.admission_projection import (
    LegacyAdmissionParticipant,
    LiveAgentLegacyAdmissionProjection,
)


class LegacyAdmissionProjectionTests(unittest.TestCase):
    def test_join_and_leave_use_the_retained_roster_shape(self) -> None:
        calls: list[tuple[Path, dict[str, object]]] = []

        def record(root: Path, payload: dict[str, object]) -> object:
            calls.append((root, payload))
            return payload

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            projection = LiveAgentLegacyAdmissionProjection(root, connect=record)
            joined = projection.participant_joined(
                LegacyAdmissionParticipant(
                    participant_id="agent-1",
                    display_name="Agent One",
                    provider_kind="codex",
                    connection_kind="native_cli_bridge",
                    room_id="room-1",
                    owner_display_name="Owner",
                )
            )
            left = projection.participant_left("agent-1")

        self.assertTrue(joined)
        self.assertTrue(left)
        self.assertEqual(calls[0][0], root)
        self.assertEqual(
            calls[0][1],
            {
                "agent_id": "agent-1",
                "display_name": "Agent One",
                "provider_kind": "codex",
                "connection_kind": "native_cli_bridge",
                "meeting_id": "room-1",
                "status": "online",
                "owner_display_name": "Owner",
            },
        )
        self.assertEqual(calls[1][1], {"agent_id": "agent-1", "status": "offline"})
        self.assertEqual(
            projection.diagnostics(),
            {
                "healthy": True,
                "failure_count": 0,
                "recent_failures": [],
                "tail_truncated": False,
            },
        )

    def test_failure_diagnostics_are_bounded_and_redacted(self) -> None:
        def fail(_root: Path, _payload: dict[str, object]) -> object:
            raise ValueError("token=secret https://private.example/path")

        projection = LiveAgentLegacyAdmissionProjection(
            Path("/not/exposed"),
            connect=fail,
            failure_tail_size=2,
            clock=lambda: datetime(2026, 7, 16, 1, 2, 3, tzinfo=UTC),
        )

        for participant_id in ("agent-1", "agent-2", "agent-3"):
            self.assertFalse(projection.participant_left(participant_id))

        diagnostics = projection.diagnostics()
        self.assertFalse(diagnostics["healthy"])
        self.assertEqual(diagnostics["failure_count"], 3)
        self.assertTrue(diagnostics["tail_truncated"])
        self.assertEqual(
            [failure["participant_id"] for failure in diagnostics["recent_failures"]],
            ["agent-2", "agent-3"],
        )
        self.assertEqual(
            {failure["error_type"] for failure in diagnostics["recent_failures"]},
            {"ValueError"},
        )
        self.assertNotIn("secret", str(diagnostics))
        self.assertNotIn("private.example", str(diagnostics))
        self.assertNotIn("/not/exposed", str(diagnostics))


if __name__ == "__main__":
    unittest.main()
