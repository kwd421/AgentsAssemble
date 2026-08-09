from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from agentsassemble.persistence.local.room.repository import RoomStore
from agentsassemble.room.bridge_reports import RoomBridgeReportService
from agentsassemble.room.command_uow import RoomCommandUnitOfWork
from agentsassemble.room.errors import RoomCommandRejected
from agentsassemble.room.event_broker import RoomEventBroker


class RoomBridgeReportServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.store = RoomStore(self.root)
        self.store.create_room("general")
        self.store.upsert_participant(
            "general",
            {
                "participant_id": "codex",
                "display_name": "Codex",
                "participant_type": "agent",
                "status": "detached",
            },
        )
        self.store.upsert_session(
            "general",
            {
                "session_id": "codex",
                "participant_id": "codex",
                "display_name": "Codex",
                "process_ownership": "server",
                "enabled": True,
                "runtime_status": "starting",
                "transport": "http",
            },
        )
        self.broker = RoomEventBroker()
        self.assignments: list[tuple[str, str]] = []
        self.published_sessions: list[tuple[str, dict[str, object]]] = []
        self.service = RoomBridgeReportService(
            store=self.store,
            broker=self.broker,
            bridge_session=self._bridge_session,
            assign_pending=self._assign_pending,
            publish_session_state=lambda room_id, session: self.published_sessions.append(
                (room_id, dict(session))
            ),
        )
        self.identity = {
            "meeting_id": "general",
            "agent_id": "codex",
            "session_id": "codex",
            "client_type": "agent_bridge",
        }
        self.channel = self.broker.connect(self.identity)
        self.request_number = 0

    def tearDown(self) -> None:
        self.broker.close()
        self.store.close()
        self.temporary_directory.cleanup()

    def _bridge_session(
        self,
        _identity: dict[str, object],
        room_id: str,
        *,
        allow_unleased: bool = False,
    ) -> tuple[str, dict[str, object]]:
        del allow_unleased
        return "codex", self.store.session(room_id, "codex")

    def _assign_pending(self, room_id: str, agent_id: str) -> bool:
        self.assignments.append((room_id, agent_id))
        return True

    def _start_failed(self, payload: dict[str, object]) -> dict[str, object]:
        self.request_number += 1
        with RoomCommandUnitOfWork(
            self.store,
            room_id="general",
            principal_id="agent-session:codex",
            request_id=f"start-failed-{self.request_number}",
            action="bridge.start_failed",
            payload=payload,
        ) as unit:
            result = self.service.start_failed_in_unit(
                self.identity,
                "general",
                payload,
                unit=unit,
            )
            unit.build_ack(result)
            unit.record_ack()
        self.service.publish_start_failure("general", "codex")
        return result

    def test_ready_activates_one_generation_and_publishes_attached_state(self) -> None:
        self.store.update_session_fields(
            "general",
            "codex",
            last_error="earlier startup failure",
            last_error_code="api_context_checkpoint_missing",
            recovery_required=True,
            recovery_attempt_count=1,
        )
        result = self.service.ready(
            self.identity,
            "general",
            {
                "pid": 42,
                "running": True,
                "pty": False,
                "transport": "http_sse",
                "provider_session_active": True,
                "started_at": None,
            },
        )

        session = self.store.session("general", "codex")
        self.assertEqual(result["agent_session"]["runtime_status"], "idle")
        self.assertEqual(session["bridge_generation"], 1)
        self.assertEqual(session["reported_provider_pid"], 42)
        self.assertEqual(session["transport"], "http")
        self.assertEqual(session["reported_transport"], "http_sse")
        self.assertEqual(
            self.store.participant("general", "codex")["status"],
            "joined",
        )
        self.assertEqual(self.assignments, [("general", "codex")])
        self.assertEqual(self.published_sessions[-1][1]["session_id"], "codex")
        self.assertEqual(session["last_error_code"], "")
        self.assertFalse(session["recovery_required"])
        self.assertEqual(session["recovery_attempt_count"], 1)

    def test_ready_rejects_incomplete_health_before_activating_the_bridge(self) -> None:
        with self.assertRaises(RoomCommandRejected) as raised:
            self.service.ready(
                self.identity,
                "general",
                {
                    "running": True,
                    "provider_session_active": True,
                    "started_at": None,
                },
            )

        self.assertEqual(raised.exception.code, "adapter_health_invalid")
        self.assertFalse(self.broker.has_bridge("general", "codex"))
        self.assertEqual(
            self.store.participant("general", "codex")["status"],
            "detached",
        )

    def test_start_failure_is_persisted_before_the_bridge_becomes_ready(self) -> None:
        result = self._start_failed(
            {
                "error_code": "api_context_checkpoint_missing",
                "message": "This API Agent Session has prior turns but no recoverable checkpoint.",
            },
        )

        session = self.store.session("general", "codex")
        self.assertEqual(result["agent_session"]["runtime_status"], "error")
        self.assertEqual(session["last_error_code"], "api_context_checkpoint_missing")
        self.assertIn("no recoverable checkpoint", session["last_error"])
        self.assertFalse(self.broker.has_bridge("general", "codex"))

        with self.assertRaises(RoomCommandRejected) as repeated:
            self._start_failed(
                {"error_code": "provider_turn_failed", "message": "duplicate"},
            )
        self.assertEqual(repeated.exception.code, "bridge_start_failure_recorded")

    def test_ready_bridge_cannot_replace_its_state_with_a_start_failure(self) -> None:
        self.broker.activate_bridge(self.channel)

        with self.assertRaises(RoomCommandRejected) as raised:
            self._start_failed(
                {"error_code": "provider_turn_failed", "message": "late failure"},
            )

        self.assertEqual(raised.exception.code, "bridge_already_ready")
        self.assertEqual(self.store.session("general", "codex")["runtime_status"], "starting")

    def test_health_updates_public_runtime_diagnostics(self) -> None:
        self.broker.activate_bridge(self.channel)
        result = self.service.health(
            self.identity,
            "general",
            {
                "pid": "84",
                "running": True,
                "pty": True,
                "transport": "http_sse",
                "provider_session_active": True,
                "started_at": "2026-07-17T00:00:00Z",
                "resolved_executable": "/usr/local/bin/codex",
            },
        )

        session = self.store.session("general", "codex")
        self.assertNotIn("reported_provider_pid", result["agent_session"])
        self.assertEqual(session["reported_provider_pid"], 84)
        self.assertEqual(session["resolved_executable"], "/usr/local/bin/codex")
        self.assertFalse(session["pty"])
        self.assertEqual(session["transport"], "http")
        self.assertEqual(session["reported_transport"], "http_sse")


if __name__ == "__main__":
    unittest.main()
