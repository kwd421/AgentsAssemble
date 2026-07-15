from __future__ import annotations

import hashlib
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from agentsassemble.identity_store import (
    IdentityStore,
    LOCAL_OPERATOR_PARTICIPANT_ID,
    LOCAL_OPERATOR_USER_ID,
)
from agentsassemble.operator_pairing import OperatorPairingService, normalize_pairing_origin
from agentsassemble.room_invite_repository import JsonInviteSessionRepository
from agentsassemble.room_session_service import RoomSessionService
from agentsassemble.room_store import RoomStore
from agentsassemble.room_users import device_auth_key


class _RecordingTransactionBoundary:
    def __init__(self) -> None:
        self.active = False
        self.events: list[str] = []

    @contextmanager
    def transaction(self):
        self.events.append("begin")
        self.active = True
        try:
            yield object()
        except BaseException:
            self.events.append("rollback")
            raise
        else:
            self.events.append("commit")
        finally:
            self.active = False


class OperatorPairingServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.identities = IdentityStore(self.root / "identity.db")
        self.rooms = RoomStore(self.root / "rooms")
        self.rooms.create_room("room-a", label="Room A")
        self.identities.claim_local_operator_credential(
            device_auth_key("local-operator-device"),
            display_name="SeiNel",
        )
        self.now = datetime(2026, 7, 15, 12, 0, tzinfo=UTC)
        self.token_number = 0
        self.session_repository = JsonInviteSessionRepository(self.root / "invite-state.json")
        self.sessions = RoomSessionService(
            self.session_repository,
            token_prefix="aas1",
            ttl_seconds=3600,
            now=lambda: self.now,
            token_key=lambda: "operator-pairing-test-key",
        )

        def next_token() -> str:
            self.token_number += 1
            return f"fixed-secret-token-{self.token_number}"

        self.service = OperatorPairingService(
            identities=self.identities,
            rooms=self.rooms,
            sessions=self.sessions,
            now=lambda: self.now,
            token_factory=next_token,
        )

    def _create(self) -> dict[str, object]:
        return self.service.create(
            room_id="room-a",
            public_url="https://Public.Example/path",
        )

    @staticmethod
    def _token(created: dict[str, object]) -> str:
        return parse_qs(urlsplit(str(created["pairing_url"])).query)["token"][0]

    def test_redeem_attaches_new_origin_to_canonical_operator_and_issues_session(self) -> None:
        created = self._create()
        token = self._token(created)

        result = self.service.redeem(
            pairing_token=token,
            device_token="public-origin-device",
            request_origin="https://public.example",
        )

        self.assertEqual(result["status"], "admitted")
        self.assertEqual(result["agent_id"], LOCAL_OPERATOR_PARTICIPANT_ID)
        self.assertTrue(result["operator"])
        self.assertEqual(
            self.identities.user_for_credential(device_auth_key("public-origin-device"))["user_id"],
            LOCAL_OPERATOR_USER_ID,
        )
        self.assertEqual(self.sessions.verify(str(result["session_token"]))["meeting_id"], "room-a")
        self.assertEqual(
            self.rooms.participant("room-a", LOCAL_OPERATOR_PARTICIPANT_ID)["display_name"],
            "SeiNel",
        )

    def test_raw_pairing_token_is_not_persisted(self) -> None:
        created = self._create()
        token = self._token(created)

        self.assertNotIn(token.encode("utf-8"), (self.root / "identity.db").read_bytes())

    def test_origin_normalization_handles_default_ports_and_ipv6(self) -> None:
        self.assertEqual(
            normalize_pairing_origin("https://Public.Example:443/path"),
            "https://public.example",
        )
        self.assertEqual(
            normalize_pairing_origin("http://[::1]:80/path"),
            "http://[::1]",
        )
        self.assertEqual(
            normalize_pairing_origin("https://[2001:db8::1]:8443/path"),
            "https://[2001:db8::1]:8443",
        )

    def test_wrong_origin_does_not_consume_pairing(self) -> None:
        created = self._create()
        token = self._token(created)

        wrong = self.service.redeem(
            pairing_token=token,
            device_token="public-origin-device",
            request_origin="https://other.example",
        )
        right = self.service.redeem(
            pairing_token=token,
            device_token="public-origin-device",
            request_origin="https://public.example",
        )

        self.assertEqual(wrong["reason"], "pairing_origin_mismatch")
        self.assertEqual(right["status"], "admitted")

    def test_pairing_is_single_use_even_under_concurrent_redeem(self) -> None:
        created = self._create()
        token = self._token(created)

        def redeem(device: str) -> dict[str, object]:
            return self.service.redeem(
                pairing_token=token,
                device_token=device,
                request_origin="https://public.example",
            )

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(redeem, ("public-device-a", "public-device-b")))

        self.assertEqual(sum(result.get("status") == "admitted" for result in results), 1)
        self.assertEqual(sum(result.get("status") == "rejected" for result in results), 1)

    def test_same_device_retry_returns_the_completed_session(self) -> None:
        created = self._create()
        token = self._token(created)

        first = self.service.redeem(
            pairing_token=token,
            device_token="public-origin-device",
            request_origin="https://public.example",
        )
        second = self.service.redeem(
            pairing_token=token,
            device_token="public-origin-device",
            request_origin="https://public.example",
        )

        self.assertEqual(first["status"], "admitted")
        self.assertEqual(second["status"], "admitted")
        self.assertEqual(second["session_token"], first["session_token"])
        self.assertEqual(len(self.session_repository.list_sessions()), 1)

    def test_same_device_resumes_after_failure_but_other_device_is_rejected(self) -> None:
        created = self._create()
        token = self._token(created)

        with patch.object(
            self.rooms,
            "upsert_participant",
            side_effect=RuntimeError("injected participant failure"),
        ):
            with self.assertRaisesRegex(RuntimeError, "injected participant failure"):
                self.service.redeem(
                    pairing_token=token,
                    device_token="public-origin-device",
                    request_origin="https://public.example",
                )

        rejected = self.service.redeem(
            pairing_token=token,
            device_token="different-public-device",
            request_origin="https://public.example",
        )
        resumed_service = OperatorPairingService(
            identities=IdentityStore(self.root / "identity.db"),
            rooms=RoomStore(self.root / "rooms"),
            sessions=self._restarted_sessions(),
            now=lambda: self.now,
        )
        resumed = resumed_service.redeem(
            pairing_token=token,
            device_token="public-origin-device",
            request_origin="https://public.example",
        )
        record = self.identities.operator_pairing_for_fingerprint(
            hashlib.sha256(token.encode("utf-8")).hexdigest()
        )

        self.assertEqual(rejected["reason"], "pairing_already_used")
        self.assertEqual(resumed["status"], "admitted")
        self.assertEqual(record["redemption_status"], "completed")
        self.assertNotIn(b"public-origin-device", (self.root / "identity.db").read_bytes())

    def test_retry_after_session_save_reuses_the_same_bearer(self) -> None:
        created = self._create()
        token = self._token(created)
        update = self.identities.update_operator_pairing_redemption

        def fail_completion_once(**kwargs: object) -> dict[str, object] | None:
            if kwargs.get("status") == "completed":
                raise RuntimeError("injected completion failure")
            return update(**kwargs)

        with patch.object(
            self.identities,
            "update_operator_pairing_redemption",
            side_effect=fail_completion_once,
        ):
            with self.assertRaisesRegex(RuntimeError, "injected completion failure"):
                self.service.redeem(
                    pairing_token=token,
                    device_token="public-origin-device",
                    request_origin="https://public.example",
                )

        stored_before_retry = self.session_repository.list_sessions()
        restarted_sessions = self._restarted_sessions()
        resumed_service = OperatorPairingService(
            identities=IdentityStore(self.root / "identity.db"),
            rooms=RoomStore(self.root / "rooms"),
            sessions=restarted_sessions,
            now=lambda: self.now,
        )
        resumed = resumed_service.redeem(
            pairing_token=token,
            device_token="public-origin-device",
            request_origin="https://public.example",
        )

        self.assertEqual(resumed["status"], "admitted")
        self.assertEqual(len(stored_before_retry), 1)
        self.assertEqual(
            resumed["session_token"],
            restarted_sessions.token_for_request(f"operator-pairing:{created['pairing_id']}"),
        )

    def test_hosted_boundary_starts_after_device_claim_and_commits_completion(self) -> None:
        created = self._create()
        token = self._token(created)
        boundary = _RecordingTransactionBoundary()
        service = OperatorPairingService(
            identities=self.identities,
            rooms=self.rooms,
            sessions=self.sessions,
            transaction_boundary=boundary,
            now=lambda: self.now,
        )
        consume = self.identities.consume_operator_pairing
        upsert_membership = self.identities.upsert_membership

        def consume_before_transaction(**kwargs):
            self.assertFalse(boundary.active)
            return consume(**kwargs)

        def membership_inside(record):
            self.assertTrue(boundary.active)
            return upsert_membership(record)

        with patch.object(
            self.identities,
            "consume_operator_pairing",
            side_effect=consume_before_transaction,
        ), patch.object(
            self.identities,
            "upsert_membership",
            side_effect=membership_inside,
        ):
            result = service.redeem(
                pairing_token=token,
                device_token="public-origin-device",
                request_origin="https://public.example",
            )

        self.assertEqual(result["status"], "admitted")
        self.assertEqual(boundary.events, ["begin", "commit"])

    def test_hosted_pairing_failure_status_is_written_after_transaction_rollback(self) -> None:
        created = self._create()
        token = self._token(created)
        boundary = _RecordingTransactionBoundary()
        service = OperatorPairingService(
            identities=self.identities,
            rooms=self.rooms,
            sessions=self.sessions,
            transaction_boundary=boundary,
            now=lambda: self.now,
        )
        update = self.identities.update_operator_pairing_redemption

        def track_failure_status(**kwargs):
            if kwargs.get("status") == "failed_retryable":
                self.assertFalse(boundary.active)
            return update(**kwargs)

        with patch.object(
            self.rooms,
            "upsert_participant",
            side_effect=RuntimeError("participant write failed"),
        ), patch.object(
            self.identities,
            "update_operator_pairing_redemption",
            side_effect=track_failure_status,
        ):
            with self.assertRaisesRegex(RuntimeError, "participant write failed"):
                service.redeem(
                    pairing_token=token,
                    device_token="public-origin-device",
                    request_origin="https://public.example",
                )

        self.assertEqual(boundary.events, ["begin", "rollback"])

    def _restarted_sessions(self) -> RoomSessionService:
        return RoomSessionService(
            JsonInviteSessionRepository(self.root / "invite-state.json"),
            token_prefix="aas1",
            ttl_seconds=3600,
            now=lambda: self.now,
            token_key=lambda: "operator-pairing-test-key",
        )

    def test_expired_and_revoked_pairings_are_rejected(self) -> None:
        expired = self._create()
        self.now += timedelta(seconds=121)
        expired_result = self.service.redeem(
            pairing_token=self._token(expired),
            device_token="public-origin-device",
            request_origin="https://public.example",
        )

        self.now += timedelta(seconds=1)
        revoked = self._create()
        self.assertTrue(self.service.revoke(str(revoked["pairing_id"])))
        revoked_result = self.service.redeem(
            pairing_token=self._token(revoked),
            device_token="another-public-device",
            request_origin="https://public.example",
        )

        self.assertEqual(expired_result["reason"], "pairing_expired")
        self.assertEqual(revoked_result["reason"], "pairing_revoked")


if __name__ == "__main__":
    unittest.main()
