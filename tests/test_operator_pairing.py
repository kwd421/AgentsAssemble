from __future__ import annotations

import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from agentsassemble.identity_store import (
    IdentityStore,
    LOCAL_OPERATOR_PARTICIPANT_ID,
    LOCAL_OPERATOR_USER_ID,
)
from agentsassemble.operator_pairing import OperatorPairingService, normalize_pairing_origin
from agentsassemble.room_invite import reset_state, verify_session_token
from agentsassemble.room_store import RoomStore
from agentsassemble.room_users import device_auth_key


class OperatorPairingServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_state()
        self.addCleanup(reset_state)
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

        def next_token() -> str:
            self.token_number += 1
            return f"fixed-secret-token-{self.token_number}"

        self.service = OperatorPairingService(
            identities=self.identities,
            rooms=self.rooms,
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
        self.assertEqual(verify_session_token(str(result["session_token"]))["meeting_id"], "room-a")
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
