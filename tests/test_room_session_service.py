from __future__ import annotations

import unittest

from agentsassemble.persistence.local.admission.repository import (
    MemoryInviteSessionRepository,
)
from agentsassemble.room_session_service import RoomSessionService


class RoomSessionServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repository = MemoryInviteSessionRepository()
        self.service = RoomSessionService(
            self.repository,
            token_prefix="aas1",
            ttl_seconds=3600,
            token_key=lambda: "server-signing-secret",
        )
        self.record = {
            "agent_id": "guest-a",
            "display_name": "Guest A",
            "meeting_id": "room-a",
            "invite_scope": "room",
            "participant_type": "human",
            "client_type": "browser",
            "provider_kind": "manual",
            "owner_id": "owner-a",
            "connection_kind": "native_remote_room_client",
        }

    def test_same_request_reconstructs_the_same_session_bearer(self) -> None:
        first_token, first = self.service.ensure_for_request("workflow-1", self.record)
        second_token, second = self.service.ensure_for_request("workflow-1", self.record)

        self.assertEqual(second_token, first_token)
        self.assertEqual(second, first)
        self.assertEqual(len(self.repository.list_sessions()), 1)

    def test_different_requests_get_different_bearers(self) -> None:
        first = self.service.token_for_request("workflow-1")
        second = self.service.token_for_request("workflow-2")

        self.assertNotEqual(first, second)

    def test_idempotent_issue_requires_a_server_key(self) -> None:
        service = RoomSessionService(
            self.repository,
            token_prefix="aas1",
            ttl_seconds=3600,
        )

        with self.assertRaisesRegex(RuntimeError, "key is not configured"):
            service.ensure_for_request("workflow-1", self.record)


if __name__ == "__main__":
    unittest.main()
