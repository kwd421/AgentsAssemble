"""Tests for room_invite module: create, join, session, and leave flow."""

from __future__ import annotations

import unittest

from agentsassemble.room_invite import (
    create_room_invite,
    join_room_with_invite,
    reset_state,
    revoke_session,
    verify_session_token,
    active_sessions_summary,
)


class TestRoomInviteCreateJoinFlow(unittest.TestCase):
    def setUp(self):
        reset_state()

    def tearDown(self):
        reset_state()

    def test_create_invite_returns_token(self):
        invite = create_room_invite(
            room_url="http://192.168.1.10:8765",
            meeting_id="test-meeting",
            agent_id="guest-1",
            display_name="Guest One",
        )
        self.assertIn("invite_token", invite)
        self.assertEqual(invite["meeting_id"], "test-meeting")
        self.assertEqual(invite["agent_id"], "guest-1")
        self.assertTrue(invite["invite_token"].startswith("aai1."))

    def test_join_with_valid_token(self):
        invite = create_room_invite(
            room_url="http://192.168.1.10:8765",
            meeting_id="test-meeting",
            agent_id="guest-1",
            display_name="Guest One",
        )
        result = join_room_with_invite(invite["invite_token"], meeting_id="test-meeting")
        self.assertEqual(result["status"], "admitted")
        self.assertIn("session_token", result)
        self.assertEqual(result["agent_id"], "guest-1")
        self.assertEqual(result["display_name"], "Guest One")
        self.assertTrue(result["session_token"].startswith("aas1."))

    def test_join_token_single_use(self):
        invite = create_room_invite(
            room_url="http://192.168.1.10:8765",
            meeting_id="test-meeting",
            agent_id="guest-1",
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
            room_url="http://192.168.1.10:8765",
            meeting_id="test-meeting",
            agent_id="guest-1",
        )
        join_result = join_room_with_invite(invite["invite_token"])
        session = verify_session_token(join_result["session_token"])
        self.assertIsNotNone(session)
        self.assertEqual(session["agent_id"], "guest-1")

    def test_session_revoke(self):
        invite = create_room_invite(
            room_url="http://192.168.1.10:8765",
            meeting_id="test-meeting",
            agent_id="guest-1",
        )
        join_result = join_room_with_invite(invite["invite_token"])
        token = join_result["session_token"]
        self.assertTrue(revoke_session(token))
        self.assertIsNone(verify_session_token(token))

    def test_active_sessions_summary(self):
        invite = create_room_invite(
            room_url="http://192.168.1.10:8765",
            meeting_id="test-meeting",
            agent_id="guest-1",
            display_name="Guest One",
        )
        join_room_with_invite(invite["invite_token"])
        sessions = active_sessions_summary()
        self.assertEqual(len(sessions), 1)
        self.assertEqual(sessions[0]["agent_id"], "guest-1")
        # Session token should NOT be in the summary
        self.assertNotIn("session_token", sessions[0])

    def test_create_invite_auto_generates_agent_id(self):
        invite = create_room_invite(
            room_url="http://192.168.1.10:8765",
            meeting_id="test-meeting",
        )
        self.assertTrue(invite["agent_id"].startswith("guest-"))

    def test_meeting_id_mismatch_rejected(self):
        invite = create_room_invite(
            room_url="http://192.168.1.10:8765",
            meeting_id="meeting-a",
            agent_id="guest-1",
        )
        result = join_room_with_invite(invite["invite_token"], meeting_id="meeting-b")
        self.assertEqual(result["status"], "rejected")
        self.assertEqual(result["reason"], "identity_mismatch")


if __name__ == "__main__":
    unittest.main()
