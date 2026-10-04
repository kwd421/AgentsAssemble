"""Tests for room_invite module: create, join, session, and leave flow."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agentsassemble.admission.invite_service import InviteApplicationService
from agentsassemble.admission.repository import (
    InviteRepositoryNotConfigured,
    UnconfiguredInviteSessionRepository,
)
from agentsassemble.persistence.local.admission.repository import (
    MemoryInviteSessionRepository,
)
from agentsassemble.admission.invite import (
    active_sessions_summary,
    configure_room_invite_store,
    create_room_invite,
    join_room_with_invite,
    pending_invites_summary,
    reload_room_invite_store,
    reset_state,
    revoke_invite,
    revoke_session,
    set_runtime_host_token,
    set_runtime_public_url,
    verify_session_token,
)


class TestRoomInviteRepositoryConfiguration(unittest.TestCase):
    def test_facade_fails_closed_before_repository_configuration(self) -> None:
        with patch(
            "agentsassemble.admission.invite._compatibility_state.invite_application",
            InviteApplicationService(UnconfiguredInviteSessionRepository()),
        ):
            with self.assertRaises(InviteRepositoryNotConfigured):
                create_room_invite(
                    room_url="http://127.0.0.1:8765",
                    meeting_id="room-a",
                )

    def test_explicit_ephemeral_configuration_remains_available(self) -> None:
        configure_room_invite_store(None)
        self.addCleanup(reset_state)

        invite = create_room_invite(
            room_url="http://127.0.0.1:8765",
            meeting_id="room-a",
        )

        self.assertTrue(str(invite["invite_token"]).startswith("aai1."))

    def test_application_services_do_not_share_repository_state(self) -> None:
        first = InviteApplicationService(
            MemoryInviteSessionRepository(),
            public_url=lambda: "https://first.example",
        )
        second = InviteApplicationService(
            MemoryInviteSessionRepository(),
            public_url=lambda: "https://second.example",
        )

        invite = first.create(
            room_url="http://127.0.0.1:8765",
            meeting_id="room-a",
            display_name="First Guest",
        )

        self.assertEqual(first.inspect(str(invite["join_code"]))["status"], "valid")
        self.assertEqual(second.inspect(str(invite["join_code"]))["reason"], "invite_not_found")
        self.assertEqual(first.pending()[0]["display_name"], "First Guest")
        self.assertEqual(first.revoke(str(invite["invite_id"])), True)
        self.assertEqual(first.inspect(str(invite["join_code"]))["reason"], "invite_revoked")
        self.assertNotEqual(first.signing_secret(), second.signing_secret())

    def test_application_service_revokes_only_invites_for_one_room(self) -> None:
        service = InviteApplicationService(MemoryInviteSessionRepository())
        first = service.create(
            room_url="http://127.0.0.1:8765",
            meeting_id="room-a",
        )
        second = service.create(
            room_url="http://127.0.0.1:8765",
            meeting_id="room-b",
        )

        self.assertEqual(service.revoke_room("room-a"), 1)
        self.assertEqual(service.inspect(str(first["join_code"]))["reason"], "invite_revoked")
        self.assertEqual(service.inspect(str(second["join_code"]))["status"], "valid")



class TestRoomInviteCreateJoinFlow(unittest.TestCase):
    def setUp(self):
        reset_state()

    def tearDown(self):
        reset_state()

    def test_create_invite_returns_token(self):
        invite = create_room_invite(
            room_url="https://192.168.1.10:8765",
            meeting_id="test-meeting",
            agent_id="guest-1",
            display_name="Guest One",
        )
        self.assertIn("invite_token", invite)
        self.assertEqual(invite["meeting_id"], "test-meeting")
        self.assertEqual(invite["agent_id"], "guest-1")
        self.assertTrue(invite["invite_token"].startswith("aai1."))

    def test_agent_bridge_invite_normalizes_provider_id_to_canonical_kind(self):
        invite = create_room_invite(
            room_url="https://192.168.1.10:8765",
            meeting_id="test-meeting",
            agent_id="claude-guest",
            client_type="agent_bridge",
            provider_kind="claude",
            max_uses=1,
        )
        joined = join_room_with_invite(str(invite["invite_token"]))

        self.assertEqual(invite["provider_kind"], "claude_code")
        self.assertEqual(joined["provider_kind"], "claude_code")

    def test_agent_bridge_invite_rejects_unknown_provider_before_issuing_token(self):
        with self.assertRaisesRegex(ValueError, "supported provider"):
            create_room_invite(
                room_url="https://192.168.1.10:8765",
                meeting_id="test-meeting",
                agent_id="unknown-guest",
                client_type="agent_bridge",
                provider_kind="unknown-provider",
                max_uses=1,
            )

    def test_create_invite_returns_secure_join_url_when_public_url_configured(self):
        set_runtime_public_url("https://shared-room.example.com")

        invite = create_room_invite(
            room_url="http://127.0.0.1:8765",
            meeting_id="test-meeting",
            agent_id="guest-1",
            display_name="Guest One",
        )

        self.assertTrue(str(invite["join_url"]).startswith("https://shared-room.example.com/join?token="))
        self.assertIn("?token=aaj1_", invite["join_url"])
        self.assertNotIn(str(invite["invite_token"]), str(invite["join_url"]))
        self.assertEqual(invite["remote_client_packet"]["join_url"], invite["join_url"])
        self.assertNotIn("env", invite["remote_client_packet"])

        joined = join_room_with_invite(str(invite["join_code"]), display_name="Mobile Guest")
        self.assertEqual(joined["status"], "admitted")
        self.assertEqual(joined["meeting_id"], "test-meeting")

    def test_create_invite_returns_remote_client_entry_packet(self):
        invite = create_room_invite(
            room_url="https://192.168.1.10:8765",
            meeting_id="test-meeting",
            agent_id="guest-1",
            display_name="Guest One",
        )

        packet = invite["remote_client_packet"]
        self.assertEqual(packet["packet_kind"], "agent_attendee_entry_packet")
        self.assertEqual(packet["agent"]["agent_id"], "guest-1")
        self.assertEqual(packet["agent"]["display_name"], "Guest One")
        self.assertNotIn("env", packet)
        self.assertNotIn("http", packet)
        self.assertEqual(packet["attend"]["command"], "assemble room attend --provider <provider>")
        self.assertEqual(packet["attend"]["live_transport"], "websocket_push")
        self.assertEqual(packet["admission_contract"]["identity_proof"], "hmac_sha256_invite_token")
        self.assertEqual(packet["admission_contract"]["provider_execution"], "not_started_by_invite")
        self.assertFalse(packet["safety"]["contains_invite_token"])
        self.assertFalse(packet["safety"]["contains_session_token"])

    def test_join_with_valid_token(self):
        invite = create_room_invite(
            room_url="https://192.168.1.10:8765",
            meeting_id="test-meeting",
            agent_id="guest-1",
            display_name="Guest One",
            max_uses=1,
        )
        result = join_room_with_invite(invite["invite_token"], meeting_id="test-meeting")
        self.assertEqual(result["status"], "admitted")
        self.assertIn("session_token", result)
        self.assertEqual(result["agent_id"], "guest-1")
        self.assertEqual(result["display_name"], "Guest One")
        self.assertTrue(result["session_token"].startswith("aas1."))
        self.assertEqual(result["invite_scope"], "room")

    def test_join_token_single_use(self):
        invite = create_room_invite(
            room_url="https://192.168.1.10:8765",
            meeting_id="test-meeting",
            agent_id="guest-1",
            max_uses=1,
        )
        first = join_room_with_invite(invite["invite_token"], meeting_id="test-meeting")
        self.assertEqual(first["status"], "admitted")
        second = join_room_with_invite(invite["invite_token"], meeting_id="test-meeting")
        self.assertEqual(second["status"], "rejected")
        self.assertEqual(second["reason"], "token_already_used")

    def test_join_with_invalid_token(self):
        result = join_room_with_invite("aai1.bogus.token")
        self.assertEqual(result["status"], "rejected")

    def test_session_token_verification(self):
        invite = create_room_invite(
            room_url="https://192.168.1.10:8765",
            meeting_id="test-meeting",
            agent_id="guest-1",
            max_uses=1,
        )
        join_result = join_room_with_invite(invite["invite_token"])
        session = verify_session_token(join_result["session_token"])
        self.assertIsNotNone(session)
        self.assertEqual(session["agent_id"], "guest-1")

    def test_default_invite_is_unlimited_and_mints_unique_ids(self):
        # Discord-style default: one open link admits many, each with a unique id.
        invite = create_room_invite(
            room_url="https://192.168.1.10:8765",
            meeting_id="test-meeting",
            agent_id="guest",
        )
        self.assertEqual(invite["max_uses"], 0)
        self.assertEqual(invite["permission_mode"], "participant")
        first = join_room_with_invite(invite["invite_token"], meeting_id="test-meeting")
        second = join_room_with_invite(invite["invite_token"], meeting_id="test-meeting")
        self.assertEqual(first["status"], "admitted")
        self.assertEqual(second["status"], "admitted")
        self.assertNotEqual(first["agent_id"], second["agent_id"])
        self.assertTrue(first["agent_id"].startswith("guest-"))

    def test_read_only_invite_scope_survives_join_and_session_verification(self):
        invite = create_room_invite(
            room_url="https://192.168.1.10:8765",
            meeting_id="test-meeting",
            agent_id="guest-1",
            invite_scope="read_only",
        )

        self.assertEqual(invite["invite_scope"], "read_only")
        join_result = join_room_with_invite(invite["invite_token"])
        self.assertEqual(join_result["status"], "admitted")
        self.assertEqual(join_result["invite_scope"], "read_only")

        session = verify_session_token(join_result["session_token"])
        self.assertIsNotNone(session)
        self.assertEqual(session["invite_scope"], "read_only")

    def test_session_revoke(self):
        invite = create_room_invite(
            room_url="https://192.168.1.10:8765",
            meeting_id="test-meeting",
            agent_id="guest-1",
        )
        join_result = join_room_with_invite(invite["invite_token"])
        token = join_result["session_token"]
        self.assertTrue(revoke_session(token))
        self.assertIsNone(verify_session_token(token))

    def test_active_sessions_summary(self):
        invite = create_room_invite(
            room_url="https://192.168.1.10:8765",
            meeting_id="test-meeting",
            agent_id="guest-1",
            display_name="Guest One",
            max_uses=1,
        )
        join_room_with_invite(invite["invite_token"])
        sessions = active_sessions_summary()
        self.assertEqual(len(sessions), 1)
        self.assertEqual(sessions[0]["agent_id"], "guest-1")
        # Session token should NOT be in the summary
        self.assertNotIn("session_token", sessions[0])

    def test_create_invite_auto_generates_agent_id(self):
        invite = create_room_invite(
            room_url="https://192.168.1.10:8765",
            meeting_id="test-meeting",
        )
        self.assertTrue(invite["agent_id"].startswith("guest-"))

    def test_meeting_id_mismatch_rejected(self):
        invite = create_room_invite(
            room_url="https://192.168.1.10:8765",
            meeting_id="meeting-a",
            agent_id="guest-1",
        )
        result = join_room_with_invite(invite["invite_token"], meeting_id="meeting-b")
        self.assertEqual(result["status"], "rejected")
        self.assertEqual(result["reason"], "identity_mismatch")

    def test_persistent_store_survives_reload_without_raw_session_or_host_tokens(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store_path = Path(temp_dir) / "room-invite-state.json"
            configure_room_invite_store(store_path)
            set_runtime_host_token("host-secret-that-must-not-persist")
            invite = create_room_invite(
                room_url="https://192.168.1.10:8765",
                meeting_id="test-meeting",
                agent_id="guest-1",
                display_name="Guest One",
                max_uses=1,
            )
            join_result = join_room_with_invite(invite["invite_token"], meeting_id="test-meeting")
            self.assertEqual(join_result["status"], "admitted")
            session_token = str(join_result["session_token"])

            persisted_text = store_path.read_text(encoding="utf-8")
            self.assertNotIn(session_token, persisted_text)
            self.assertNotIn(str(invite["invite_token"]), persisted_text)
            self.assertNotIn("host-secret-that-must-not-persist", persisted_text)
            persisted = json.loads(persisted_text)
            self.assertIn("invite_secret", persisted)
            self.assertIn("sessions", persisted)
            self.assertIn("used_nonce_fingerprints", persisted)
            self.assertTrue(all(key.startswith("aas1.") is False for key in persisted["sessions"]))

            reload_room_invite_store()
            session = verify_session_token(session_token)
            self.assertIsNotNone(session)
            self.assertEqual(session["agent_id"], "guest-1")

            second_join = join_room_with_invite(invite["invite_token"], meeting_id="test-meeting")
            self.assertEqual(second_join["status"], "rejected")
            self.assertEqual(second_join["reason"], "token_already_used")

    def test_revoked_invite_remains_revoked_after_store_reload(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store_path = Path(temp_dir) / "room-invite-state.json"
            configure_room_invite_store(store_path)
            invite = create_room_invite(
                room_url="https://192.168.1.10:8765",
                meeting_id="test-meeting",
                agent_id="guest-1",
            )
            self.assertTrue(revoke_invite(str(invite["invite_id"])))
            reload_room_invite_store()

            invites = pending_invites_summary()
            self.assertEqual(invites[0]["invite_id"], invite["invite_id"])
            self.assertTrue(invites[0]["revoked"])
            result = join_room_with_invite(invite["invite_token"], meeting_id="test-meeting")
            self.assertEqual(result["status"], "rejected")
            self.assertEqual(result["reason"], "invite_revoked")

    def test_expired_persisted_session_is_rejected_and_cleaned_up(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store_path = Path(temp_dir) / "room-invite-state.json"
            configure_room_invite_store(store_path)
            invite = create_room_invite(
                room_url="https://192.168.1.10:8765",
                meeting_id="test-meeting",
                agent_id="guest-1",
            )
            with patch("agentsassemble.admission.invite.SESSION_TOKEN_TTL_SECONDS", -1):
                join_result = join_room_with_invite(invite["invite_token"], meeting_id="test-meeting")
            session_token = str(join_result["session_token"])
            self.assertIsNone(verify_session_token(session_token))
            self.assertEqual(active_sessions_summary(), [])

            persisted_text = store_path.read_text(encoding="utf-8")
            self.assertNotIn(session_token, persisted_text)
            persisted = json.loads(persisted_text)
            self.assertEqual(persisted["sessions"], {})


if __name__ == "__main__":
    unittest.main()
