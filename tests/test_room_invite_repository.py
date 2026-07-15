from __future__ import annotations

import json
import tempfile
import threading
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from agentsassemble.room_invite_repository import (
    InviteRepositoryCorrupt,
    InviteRepositoryNotConfigured,
    InviteRepositoryUnavailable,
    InviteRepositoryWriteFailed,
    InviteSessionRepository,
    JsonInviteSessionRepository,
    MemoryInviteSessionRepository,
    ROOM_INVITE_STORE_SCHEMA,
    UnconfiguredInviteSessionRepository,
)


def _invite(*, invite_id: str = "invite-1", max_uses: int = 2) -> dict[str, object]:
    now = datetime.now(UTC)
    return {
        "invite_id": invite_id,
        "agent_id": "guest",
        "display_name": "Guest",
        "meeting_id": "room-a",
        "invite_scope": "room",
        "participant_type": "human",
        "client_type": "browser",
        "provider_kind": "manual",
        "created_by_user_id": "owner-a",
        "join_code_fingerprint": f"join-{invite_id}",
        "join_nonce": f"nonce-{invite_id}",
        "permission_mode": "participant",
        "max_uses": max_uses,
        "use_count": 0,
        "created_at": now.isoformat(),
        "expires_at": (now + timedelta(minutes=10)).isoformat(),
        "revoked": False,
    }


def _session() -> dict[str, object]:
    now = datetime.now(UTC)
    return {
        "agent_id": "guest-a",
        "display_name": "Guest A",
        "meeting_id": "room-a",
        "invite_scope": "room",
        "participant_type": "human",
        "client_type": "browser",
        "provider_kind": "manual",
        "owner_id": "owner-a",
        "connection_kind": "native_remote_room_client",
        "joined_at": now.isoformat(),
        "expires_at": (now + timedelta(hours=1)).isoformat(),
    }


class UnconfiguredInviteSessionRepositoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repository = UnconfiguredInviteSessionRepository()

    def test_implements_repository_contract(self) -> None:
        self.assertIsInstance(self.repository, InviteSessionRepository)

    def test_all_storage_operations_fail_with_configuration_error(self) -> None:
        operations = [
            self.repository.signing_secret,
            self.repository.existing_signing_secret,
            lambda: self.repository.save_invite(_invite()),
            lambda: self.repository.invite("invite-1"),
            lambda: self.repository.invite_for_join_code("join-invite-1"),
            lambda: self.repository.nonce_was_used("nonce-1"),
            lambda: self.repository.consume(
                invite_id="invite-1",
                nonce_fingerprint="nonce-1",
                reusable=False,
                max_uses=1,
            ),
            lambda: self.repository.revoke_invite("invite-1"),
            lambda: self.repository.revoke_room_invites("room-a"),
            self.repository.list_invites,
            lambda: self.repository.save_session("session-1", _session()),
            lambda: self.repository.session("session-1"),
            lambda: self.repository.revoke_session("session-1"),
            lambda: self.repository.revoke_participant_sessions("room-a", "guest-a"),
            lambda: self.repository.revoke_room_sessions("room-a"),
            self.repository.list_sessions,
            self.repository.reload,
            self.repository.clear,
        ]

        for operation in operations:
            with self.subTest(operation=operation):
                with self.assertRaisesRegex(
                    InviteRepositoryNotConfigured,
                    "repository is not configured",
                ):
                    operation()

    def test_close_is_safe_before_configuration(self) -> None:
        self.assertIsNone(self.repository.close())


class InviteSessionRepositoryContract:
    repository: MemoryInviteSessionRepository

    def test_invite_lookup_returns_copies(self) -> None:
        self.repository.save_invite(_invite())

        by_id = self.repository.invite("invite-1")
        by_code = self.repository.invite_for_join_code("join-invite-1")
        self.assertIsNotNone(by_id)
        self.assertEqual(by_code, by_id)
        by_id["display_name"] = "mutated"
        self.assertEqual(self.repository.invite("invite-1")["display_name"], "Guest")

    def test_capped_invite_consumption_is_atomic(self) -> None:
        self.repository.save_invite(_invite(max_uses=2))
        barrier = threading.Barrier(8)
        results: list[str] = []
        result_lock = threading.Lock()

        def consume() -> None:
            barrier.wait()
            result = self.repository.consume(
                invite_id="invite-1",
                nonce_fingerprint="unused-for-reusable",
                reusable=True,
                max_uses=2,
            )
            with result_lock:
                results.append(result)

        threads = [threading.Thread(target=consume) for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=2)

        self.assertEqual(results.count(""), 2)
        self.assertEqual(results.count("invite_use_limit_reached"), 6)
        self.assertEqual(self.repository.invite("invite-1")["use_count"], 2)

    def test_single_use_nonce_rejects_replay(self) -> None:
        first = self.repository.consume(
            invite_id="missing-record",
            nonce_fingerprint="nonce-fingerprint",
            reusable=False,
            max_uses=1,
        )
        second = self.repository.consume(
            invite_id="missing-record",
            nonce_fingerprint="nonce-fingerprint",
            reusable=False,
            max_uses=1,
        )

        self.assertEqual(first, "")
        self.assertEqual(second, "token_already_used")
        self.assertTrue(self.repository.nonce_was_used("nonce-fingerprint"))

    def test_session_revocation_is_scoped(self) -> None:
        first = _session()
        second = {**_session(), "agent_id": "guest-b"}
        third = {**_session(), "meeting_id": "room-b"}
        self.repository.save_session("session-a", first)
        self.repository.save_session("session-b", second)
        self.repository.save_session("session-c", third)

        self.assertEqual(
            self.repository.revoke_participant_sessions("room-a", "guest-a"),
            1,
        )
        self.assertIsNone(self.repository.session("session-a"))
        self.assertIsNotNone(self.repository.session("session-b"))
        self.assertIsNotNone(self.repository.session("session-c"))
        self.assertEqual(self.repository.revoke_room_sessions("room-a"), 1)
        self.assertIsNotNone(self.repository.session("session-c"))

    def test_room_invite_revocation_is_scoped(self) -> None:
        self.repository.save_invite(_invite(invite_id="invite-a"))
        self.repository.save_invite(
            {**_invite(invite_id="invite-b"), "meeting_id": "room-b"}
        )

        self.assertEqual(self.repository.revoke_room_invites("room-a"), 1)
        self.assertTrue(self.repository.invite("invite-a")["revoked"])
        self.assertFalse(self.repository.invite("invite-b")["revoked"])


class MemoryInviteSessionRepositoryTests(
    InviteSessionRepositoryContract,
    unittest.TestCase,
):
    def setUp(self) -> None:
        self.repository = MemoryInviteSessionRepository()


class JsonInviteSessionRepositoryTests(
    InviteSessionRepositoryContract,
    unittest.TestCase,
):
    def setUp(self) -> None:
        self._temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp_dir.cleanup)
        self.path = Path(self._temp_dir.name) / "room-invite-state.json"
        self.repository = JsonInviteSessionRepository(self.path)

    def test_reload_preserves_reusable_policy_sessions_and_replay_state(self) -> None:
        self.repository.save_invite(_invite(max_uses=3))
        self.repository.consume(
            invite_id="invite-1",
            nonce_fingerprint="unused",
            reusable=True,
            max_uses=3,
        )
        self.repository.consume(
            invite_id="single",
            nonce_fingerprint="used-nonce",
            reusable=False,
            max_uses=1,
        )
        self.repository.save_session("session-fingerprint", _session())

        reloaded = JsonInviteSessionRepository(self.path)

        invite = reloaded.invite("invite-1")
        self.assertEqual(invite["max_uses"], 3)
        self.assertEqual(invite["use_count"], 1)
        self.assertTrue(reloaded.nonce_was_used("used-nonce"))
        self.assertEqual(reloaded.session("session-fingerprint")["agent_id"], "guest-a")

    def test_persistence_contains_only_fingerprints_not_raw_tokens(self) -> None:
        self.repository.save_invite(_invite())
        self.repository.save_session("sha256-session-fingerprint", _session())

        persisted = self.path.read_text(encoding="utf-8")
        payload = json.loads(persisted)
        self.assertIn("sha256-session-fingerprint", payload["sessions"])
        self.assertNotIn("aas1.raw-session-token", persisted)
        self.assertNotIn("aai1.raw-invite-token", persisted)

    def test_reading_an_uninitialized_secret_has_no_persistence_side_effect(self) -> None:
        self.assertEqual(self.repository.existing_signing_secret(), "")
        self.assertFalse(self.path.exists())

    def test_existing_invalid_json_fails_closed(self) -> None:
        self.path.write_text("{not-json", encoding="utf-8")

        with self.assertRaises(InviteRepositoryCorrupt):
            JsonInviteSessionRepository(self.path)

    def test_existing_non_object_state_fails_closed(self) -> None:
        self.path.write_text("[]", encoding="utf-8")

        with self.assertRaises(InviteRepositoryCorrupt):
            JsonInviteSessionRepository(self.path)

    def test_existing_unknown_schema_fails_closed(self) -> None:
        self.path.write_text(
            json.dumps(
                {
                    "schema": "agentsassemble.room_invite_state.v999",
                    "invite_secret": "",
                    "sessions": {},
                    "pending_invites": {},
                    "used_nonce_fingerprints": [],
                }
            ),
            encoding="utf-8",
        )

        with self.assertRaises(InviteRepositoryCorrupt):
            JsonInviteSessionRepository(self.path)

    def test_existing_invalid_field_types_fail_closed(self) -> None:
        self.path.write_text(
            json.dumps(
                {
                    "schema": ROOM_INVITE_STORE_SCHEMA,
                    "invite_secret": "",
                    "sessions": [],
                    "pending_invites": {},
                    "used_nonce_fingerprints": [],
                }
            ),
            encoding="utf-8",
        )

        with self.assertRaises(InviteRepositoryCorrupt):
            JsonInviteSessionRepository(self.path)

    def test_existing_invalid_nested_values_fail_closed(self) -> None:
        payload = {
            "schema": ROOM_INVITE_STORE_SCHEMA,
            "invite_secret": "",
            "sessions": {},
            "pending_invites": {
                "invite-1": {**_invite(), "max_uses": "not-a-number"},
            },
            "used_nonce_fingerprints": [],
        }
        self.path.write_text(json.dumps(payload), encoding="utf-8")

        with self.assertRaises(InviteRepositoryCorrupt):
            JsonInviteSessionRepository(self.path)

    def test_existing_invalid_expiry_fails_closed(self) -> None:
        payload = {
            "schema": ROOM_INVITE_STORE_SCHEMA,
            "invite_secret": "",
            "sessions": {
                "session-fingerprint": {**_session(), "expires_at": "not-a-date"},
            },
            "pending_invites": {},
            "used_nonce_fingerprints": [],
        }
        self.path.write_text(json.dumps(payload), encoding="utf-8")

        with self.assertRaises(InviteRepositoryCorrupt):
            JsonInviteSessionRepository(self.path)

    def test_existing_non_string_nonce_fails_closed(self) -> None:
        payload = {
            "schema": ROOM_INVITE_STORE_SCHEMA,
            "invite_secret": "",
            "sessions": {},
            "pending_invites": {},
            "used_nonce_fingerprints": [123],
        }
        self.path.write_text(json.dumps(payload), encoding="utf-8")

        with self.assertRaises(InviteRepositoryCorrupt):
            JsonInviteSessionRepository(self.path)

    def test_existing_unreadable_state_fails_closed(self) -> None:
        self.path.write_text("{}", encoding="utf-8")

        with patch.object(Path, "read_text", side_effect=PermissionError("denied")):
            with self.assertRaises(InviteRepositoryUnavailable):
                JsonInviteSessionRepository(self.path)

    def test_write_failure_rolls_back_invite_and_signing_secret(self) -> None:
        with patch.object(Path, "write_text", side_effect=OSError("disk full")):
            with self.assertRaises(InviteRepositoryWriteFailed):
                self.repository.signing_secret()
            with self.assertRaises(InviteRepositoryWriteFailed):
                self.repository.save_invite(_invite())

        self.assertEqual(self.repository.existing_signing_secret(), "")
        self.assertIsNone(self.repository.invite("invite-1"))
        self.assertFalse(self.path.exists())

    def test_replace_failure_rolls_back_session_mutation(self) -> None:
        self.repository.save_invite(_invite())
        with patch.object(Path, "replace", side_effect=OSError("replace failed")):
            with self.assertRaises(InviteRepositoryWriteFailed):
                self.repository.save_session("session-fingerprint", _session())

        self.assertIsNone(self.repository.session("session-fingerprint"))
        reloaded = JsonInviteSessionRepository(self.path)
        self.assertIsNone(reloaded.session("session-fingerprint"))

    def test_permission_hardening_failure_does_not_publish_new_state(self) -> None:
        with patch.object(Path, "chmod", side_effect=OSError("chmod failed")):
            with self.assertRaises(InviteRepositoryWriteFailed):
                self.repository.save_invite(_invite())

        self.assertIsNone(self.repository.invite("invite-1"))
        self.assertFalse(self.path.exists())

    def test_failed_nonce_persistence_does_not_consume_token(self) -> None:
        with patch.object(Path, "replace", side_effect=OSError("replace failed")):
            with self.assertRaises(InviteRepositoryWriteFailed):
                self.repository.consume(
                    invite_id="single",
                    nonce_fingerprint="nonce-fingerprint",
                    reusable=False,
                    max_uses=1,
                )

        self.assertFalse(self.repository.nonce_was_used("nonce-fingerprint"))

    def test_failed_revoke_persistence_restores_invite(self) -> None:
        self.repository.save_invite(_invite())
        with patch.object(Path, "replace", side_effect=OSError("replace failed")):
            with self.assertRaises(InviteRepositoryWriteFailed):
                self.repository.revoke_invite("invite-1")

        self.assertFalse(self.repository.invite("invite-1")["revoked"])

    def test_failed_reusable_consume_restores_use_count(self) -> None:
        self.repository.save_invite(_invite(max_uses=2))
        with patch.object(Path, "replace", side_effect=OSError("replace failed")):
            with self.assertRaises(InviteRepositoryWriteFailed):
                self.repository.consume(
                    invite_id="invite-1",
                    nonce_fingerprint="unused",
                    reusable=True,
                    max_uses=2,
                )

        self.assertEqual(self.repository.invite("invite-1")["use_count"], 0)


if __name__ == "__main__":
    unittest.main()
