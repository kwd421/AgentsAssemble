from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta

from agentsassemble.room_invite_repository import (
    InviteRepositoryWriteFailed,
    MemoryInviteSessionRepository,
)
from agentsassemble.room_session_issuer import RoomSessionIssuer, session_token_fingerprint


class RoomSessionIssuerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repository = MemoryInviteSessionRepository()
        self.now = datetime(2026, 7, 15, tzinfo=UTC)
        self.issuer = RoomSessionIssuer(
            self.repository,
            token_prefix="aas1",
            ttl_seconds=60,
            now=lambda: self.now,
            token_factory=lambda: "raw-secret-token",
        )

    def test_issue_persists_only_fingerprint_and_verify_returns_session(self) -> None:
        token, session = self.issuer.issue(
            {"agent_id": "guest-a", "meeting_id": "room-a"}
        )

        self.assertEqual(token, "aas1.raw-secret-token")
        self.assertIsNone(self.repository.session(token))
        persisted = self.repository.session(session_token_fingerprint(token))
        self.assertEqual(persisted, session)
        self.assertEqual(self.issuer.verify(token), session)

    def test_expired_or_malformed_session_is_revoked(self) -> None:
        token, _session = self.issuer.issue(
            {"agent_id": "guest-a", "meeting_id": "room-a"}
        )
        malformed_token = "aas1.malformed"
        self.repository.save_session(
            session_token_fingerprint(malformed_token),
            {
                "agent_id": "guest-b",
                "meeting_id": "room-a",
                "expires_at": "not-a-date",
            },
        )
        self.now += timedelta(seconds=61)

        self.assertIsNone(self.issuer.verify(token))
        self.assertIsNone(self.issuer.verify(malformed_token))
        self.assertEqual(self.repository.list_sessions(), [])

    def test_issuing_replacement_invalidates_prior_participant_token(self) -> None:
        first_token, _ = self.issuer.issue(
            {"agent_id": "guest-a", "meeting_id": "room-a"}
        )
        replacement = RoomSessionIssuer(
            self.repository,
            token_prefix="aas1",
            ttl_seconds=60,
            now=lambda: self.now,
            token_factory=lambda: "replacement-token",
        )

        second_token, second_session = replacement.issue(
            {"agent_id": "guest-a", "meeting_id": "room-a"}
        )

        self.assertIsNone(self.issuer.verify(first_token))
        self.assertEqual(replacement.verify(second_token), second_session)
        self.assertEqual(len(self.repository.list_sessions()), 1)

    def test_failed_replacement_preserves_prior_participant_token(self) -> None:
        class FailingRepository(MemoryInviteSessionRepository):
            fail_writes = False

            def _persist_locked(self) -> None:
                if self.fail_writes:
                    raise InviteRepositoryWriteFailed("injected write failure")

        repository = FailingRepository()
        first = RoomSessionIssuer(
            repository,
            token_prefix="aas1",
            ttl_seconds=60,
            now=lambda: self.now,
            token_factory=lambda: "first-token",
        )
        first_token, first_session = first.issue(
            {"agent_id": "guest-a", "meeting_id": "room-a"}
        )
        repository.fail_writes = True
        replacement = RoomSessionIssuer(
            repository,
            token_prefix="aas1",
            ttl_seconds=60,
            now=lambda: self.now,
            token_factory=lambda: "replacement-token",
        )

        with self.assertRaises(InviteRepositoryWriteFailed):
            replacement.issue({"agent_id": "guest-a", "meeting_id": "room-a"})

        self.assertEqual(first.verify(first_token), first_session)
        self.assertEqual(len(repository.list_sessions()), 1)

    def test_participant_and_room_revocation_are_delegated(self) -> None:
        first = RoomSessionIssuer(
            self.repository,
            token_prefix="aas1",
            ttl_seconds=60,
            now=lambda: self.now,
            token_factory=lambda: "first",
        )
        second = RoomSessionIssuer(
            self.repository,
            token_prefix="aas1",
            ttl_seconds=60,
            now=lambda: self.now,
            token_factory=lambda: "second",
        )
        first.issue({"agent_id": "guest-a", "meeting_id": "room-a"})
        second.issue({"agent_id": "guest-b", "meeting_id": "room-a"})

        self.assertEqual(self.issuer.revoke_participant("room-a", "guest-a"), 1)
        self.assertEqual(self.issuer.revoke_room("room-a"), 1)
        self.assertEqual(self.issuer.active(), [])


if __name__ == "__main__":
    unittest.main()
