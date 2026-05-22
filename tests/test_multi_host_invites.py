import json
import unittest
from datetime import UTC, datetime, timedelta

from agentsassemble.multi_host_invites import (
    LAN_INVITE_MODE,
    NATIVE_REMOTE_ROOM_CLIENT_KIND,
    create_lan_invite_packet,
    sign_lan_invite_claims,
    verify_lan_invite_token,
)


class MultiHostInviteTests(unittest.TestCase):
    def test_lan_invite_packet_binds_native_room_client_identity_without_secret_leak(self):
        issued_at = datetime(2026, 5, 22, 1, 2, 3, tzinfo=UTC)

        packet = create_lan_invite_packet(
            room_url="http://192.168.1.50:8765",
            meeting_id="resident-m1",
            agent_id="friend-claude",
            display_name="Friend Claude",
            provider_kind="claude_code",
            secret="test-secret",
            ttl_seconds=600,
            issued_at=issued_at,
            nonce="fixed-nonce",
        )

        self.assertEqual(packet["mode"], LAN_INVITE_MODE)
        self.assertEqual(packet["client_kind"], NATIVE_REMOTE_ROOM_CLIENT_KIND)
        self.assertEqual(packet["room_url"], "http://192.168.1.50:8765")
        self.assertEqual(packet["agent"]["agent_id"], "friend-claude")
        self.assertEqual(packet["agent"]["provider_kind"], "claude_code")
        self.assertEqual(packet["admission"]["identity_proof"], "hmac_sha256_invite_token")
        self.assertEqual(packet["admission"]["provider_execution"], "not_started_by_invite")
        self.assertEqual(packet["admission"]["remote_transport"], NATIVE_REMOTE_ROOM_CLIENT_KIND)
        self.assertFalse(packet["admission"]["remote_http_bridge"])
        self.assertNotIn("test-secret", json.dumps(packet, ensure_ascii=False))

        verified = verify_lan_invite_token(
            packet["token"],
            secret="test-secret",
            expected_meeting_id="resident-m1",
            expected_agent_id="friend-claude",
            now=issued_at + timedelta(seconds=1),
        )

        self.assertEqual(verified["status"], "ok")
        self.assertEqual(verified["identity_status"], "verified")
        self.assertEqual(verified["claims"]["mode"], LAN_INVITE_MODE)
        self.assertEqual(verified["claims"]["client_kind"], NATIVE_REMOTE_ROOM_CLIENT_KIND)
        self.assertEqual(verified["claims"]["agent"]["agent_id"], "friend-claude")
        self.assertEqual(verified["admission"]["provider_execution"], "not_started_by_invite")

    def test_lan_invite_verify_rejects_missing_or_mismatched_identity_claims(self):
        issued_at = datetime(2026, 5, 22, 1, 2, 3, tzinfo=UTC)
        token = sign_lan_invite_claims(
            {
                "schema": "agentsassemble.lan_invite.v1",
                "mode": LAN_INVITE_MODE,
                "client_kind": NATIVE_REMOTE_ROOM_CLIENT_KIND,
                "room_url": "http://192.168.1.50:8765",
                "issued_at": issued_at.isoformat(),
                "expires_at": (issued_at + timedelta(seconds=60)).isoformat(),
                "nonce": "fixed-nonce",
            },
            secret="test-secret",
        )

        missing = verify_lan_invite_token(
            token,
            secret="test-secret",
            now=issued_at + timedelta(seconds=1),
        )
        self.assertEqual(missing["status"], "failed")
        self.assertEqual(missing["identity_status"], "missing_identity_claims")

        packet = create_lan_invite_packet(
            room_url="http://192.168.1.50:8765",
            meeting_id="resident-m1",
            agent_id="friend-claude",
            display_name="Friend Claude",
            provider_kind="claude_code",
            secret="test-secret",
            ttl_seconds=60,
            issued_at=issued_at,
            nonce="fixed-nonce",
        )
        mismatched = verify_lan_invite_token(
            packet["token"],
            secret="test-secret",
            expected_meeting_id="other-meeting",
            expected_agent_id="friend-claude",
            now=issued_at + timedelta(seconds=1),
        )
        self.assertEqual(mismatched["status"], "failed")
        self.assertEqual(mismatched["identity_status"], "identity_mismatch")

    def test_lan_invite_rejects_expired_or_tampered_tokens(self):
        issued_at = datetime(2026, 5, 22, 1, 2, 3, tzinfo=UTC)
        packet = create_lan_invite_packet(
            room_url="http://10.0.0.8:8765",
            meeting_id="resident-m1",
            agent_id="friend-cursor",
            display_name="Friend Cursor",
            provider_kind="cursor",
            secret="test-secret",
            ttl_seconds=5,
            issued_at=issued_at,
            nonce="fixed-nonce",
        )

        expired = verify_lan_invite_token(
            packet["token"],
            secret="test-secret",
            now=issued_at + timedelta(seconds=6),
        )
        self.assertEqual(expired["status"], "failed")
        self.assertEqual(expired["identity_status"], "expired")

        tampered = packet["token"][:-1] + ("A" if packet["token"][-1] != "A" else "B")
        invalid = verify_lan_invite_token(
            tampered,
            secret="test-secret",
            now=issued_at + timedelta(seconds=1),
        )
        self.assertEqual(invalid["status"], "failed")
        self.assertEqual(invalid["identity_status"], "invalid_signature")
        self.assertNotIn("claims", invalid)

        malformed = verify_lan_invite_token(
            "aai1.abc.é",
            secret="test-secret",
            now=issued_at + timedelta(seconds=1),
        )
        self.assertEqual(malformed["status"], "failed")
        self.assertEqual(malformed["identity_status"], "malformed_token")

    def test_lan_invite_rejects_bridge_or_unsafe_room_urls(self):
        with self.assertRaisesRegex(ValueError, "native remote room client"):
            create_lan_invite_packet(
                room_url="http://192.168.1.50:8765",
                meeting_id="resident-m1",
                agent_id="friend-bridge",
                display_name="Friend Bridge",
                provider_kind="remote_http_bridge",
                secret="test-secret",
            )

        with self.assertRaisesRegex(ValueError, "without userinfo, query, or fragment"):
            create_lan_invite_packet(
                room_url="http://token@192.168.1.50:8765/?secret=1",
                meeting_id="resident-m1",
                agent_id="friend-claude",
                display_name="Friend Claude",
                provider_kind="claude_code",
                secret="test-secret",
            )

        with self.assertRaisesRegex(ValueError, "LAN, loopback, or private host"):
            create_lan_invite_packet(
                room_url="https://example.com/room",
                meeting_id="resident-m1",
                agent_id="friend-claude",
                display_name="Friend Claude",
                provider_kind="claude_code",
                secret="test-secret",
            )

        for unusable_url in (
            "http://0.0.0.0:8765",
            "http://[::]:8765",
            "http://255.255.255.255:8765",
        ):
            with self.subTest(unusable_url=unusable_url):
                with self.assertRaisesRegex(ValueError, "connectable LAN, loopback, or private host"):
                    create_lan_invite_packet(
                        room_url=unusable_url,
                        meeting_id="resident-m1",
                        agent_id="friend-claude",
                        display_name="Friend Claude",
                        provider_kind="claude_code",
                        secret="test-secret",
                    )


if __name__ == "__main__":
    unittest.main()
