"""Tests for public internet invite v1: host gate, join_url, revocation, identity."""

from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from agentsassemble.room_invite import (
    create_room_invite,
    get_host_token,
    get_public_url,
    host_gate_required,
    join_room_with_invite,
    pending_invites_summary,
    reset_state,
    revoke_invite,
    revoke_session,
    verify_host_token,
    verify_session_token,
)


class TestHostTokenGate(unittest.TestCase):
    def setUp(self):
        reset_state()

    def tearDown(self):
        reset_state()

    def test_no_host_token_configured_allows_all(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("AGENTSASSEMBLE_HOST_TOKEN", None)
            os.environ.pop("AGENTSASSEMBLE_PUBLIC_URL", None)
            self.assertTrue(verify_host_token(""))
            self.assertTrue(verify_host_token("anything"))

    def test_public_url_set_no_host_token_rejects(self):
        with patch.dict(os.environ, {"AGENTSASSEMBLE_PUBLIC_URL": "https://tunnel.example.com"}, clear=False):
            os.environ.pop("AGENTSASSEMBLE_HOST_TOKEN", None)
            self.assertFalse(verify_host_token(""))
            self.assertFalse(verify_host_token("anything"))

    def test_host_gate_required_when_public_url_set(self):
        with patch.dict(os.environ, {"AGENTSASSEMBLE_PUBLIC_URL": "https://tunnel.example.com"}, clear=False):
            self.assertTrue(host_gate_required())

    def test_host_gate_not_required_when_no_public_url(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("AGENTSASSEMBLE_PUBLIC_URL", None)
            self.assertFalse(host_gate_required())

    def test_public_url_with_host_token_accepts_correct(self):
        with patch.dict(os.environ, {
            "AGENTSASSEMBLE_PUBLIC_URL": "https://tunnel.example.com",
            "AGENTSASSEMBLE_HOST_TOKEN": "secret123",
        }):
            self.assertTrue(verify_host_token("secret123"))
            self.assertFalse(verify_host_token("wrong"))

    def test_host_token_configured_rejects_empty(self):
        with patch.dict(os.environ, {"AGENTSASSEMBLE_HOST_TOKEN": "secret123"}):
            self.assertFalse(verify_host_token(""))

    def test_host_token_configured_rejects_wrong(self):
        with patch.dict(os.environ, {"AGENTSASSEMBLE_HOST_TOKEN": "secret123"}):
            self.assertFalse(verify_host_token("wrong"))

    def test_host_token_configured_accepts_correct(self):
        with patch.dict(os.environ, {"AGENTSASSEMBLE_HOST_TOKEN": "secret123"}):
            self.assertTrue(verify_host_token("secret123"))

    def test_get_host_token_returns_env(self):
        with patch.dict(os.environ, {"AGENTSASSEMBLE_HOST_TOKEN": "mytoken"}):
            self.assertEqual(get_host_token(), "mytoken")

    def test_get_host_token_empty_when_unset(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("AGENTSASSEMBLE_HOST_TOKEN", None)
            self.assertEqual(get_host_token(), "")


class TestJoinUrl(unittest.TestCase):
    def setUp(self):
        reset_state()

    def tearDown(self):
        reset_state()

    def test_no_public_url_no_join_url(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("AGENTSASSEMBLE_PUBLIC_URL", None)
            invite = create_room_invite(
                room_url="http://127.0.0.1:8765",
                meeting_id="test",
            )
            self.assertNotIn("join_url", invite)

    def test_public_url_generates_join_url(self):
        with patch.dict(os.environ, {"AGENTSASSEMBLE_PUBLIC_URL": "https://my-tunnel.example.com"}):
            invite = create_room_invite(
                room_url="http://127.0.0.1:8765",
                meeting_id="test",
            )
            self.assertIn("join_url", invite)
            join_url = str(invite["join_url"])
            self.assertTrue(join_url.startswith("https://my-tunnel.example.com/join?token="))
            # Token should be in the URL
            self.assertIn(str(invite["invite_token"]), join_url)

    def test_public_url_trailing_slash_stripped(self):
        with patch.dict(os.environ, {"AGENTSASSEMBLE_PUBLIC_URL": "https://example.com/"}):
            self.assertEqual(get_public_url(), "https://example.com")


class TestInviteRevocation(unittest.TestCase):
    def setUp(self):
        reset_state()

    def tearDown(self):
        reset_state()

    def test_create_invite_returns_invite_id(self):
        invite = create_room_invite(
            room_url="http://127.0.0.1:8765",
            meeting_id="test",
            agent_id="guest-1",
        )
        self.assertIn("invite_id", invite)
        self.assertTrue(len(str(invite["invite_id"])) > 0)

    def test_pending_invites_summary_lists_created(self):
        invite = create_room_invite(
            room_url="http://127.0.0.1:8765",
            meeting_id="test",
            agent_id="guest-1",
            display_name="Guest One",
        )
        pending = pending_invites_summary()
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0]["agent_id"], "guest-1")
        self.assertEqual(pending[0]["invite_id"], invite["invite_id"])
        self.assertFalse(pending[0]["revoked"])

    def test_revoke_invite_marks_revoked(self):
        invite = create_room_invite(
            room_url="http://127.0.0.1:8765",
            meeting_id="test",
            agent_id="guest-1",
        )
        invite_id = str(invite["invite_id"])
        self.assertTrue(revoke_invite(invite_id))
        pending = pending_invites_summary()
        # Revoked invites are still in the list but marked
        self.assertEqual(len(pending), 1)
        self.assertTrue(pending[0]["revoked"])

    def test_revoke_nonexistent_returns_false(self):
        self.assertFalse(revoke_invite("nonexistent"))

    def test_revoked_invite_cannot_be_used(self):
        invite = create_room_invite(
            room_url="http://127.0.0.1:8765",
            meeting_id="test",
            agent_id="guest-1",
        )
        invite_id = str(invite["invite_id"])
        revoke_invite(invite_id)
        result = join_room_with_invite(str(invite["invite_token"]))
        self.assertEqual(result["status"], "rejected")
        self.assertEqual(result["reason"], "invite_revoked")


class TestGuestIdentityEnforcement(unittest.TestCase):
    """Verify that guests cannot spoof identity after joining."""

    def setUp(self):
        reset_state()

    def tearDown(self):
        reset_state()

    def test_session_identity_matches_invite(self):
        invite = create_room_invite(
            room_url="http://127.0.0.1:8765",
            meeting_id="test",
            agent_id="guest-1",
            display_name="Guest One",
        )
        result = join_room_with_invite(str(invite["invite_token"]))
        self.assertEqual(result["status"], "admitted")
        session = verify_session_token(str(result["session_token"]))
        self.assertIsNotNone(session)
        self.assertEqual(session["agent_id"], "guest-1")
        self.assertEqual(session["display_name"], "Guest One")

    def test_session_revoke_invalidates(self):
        invite = create_room_invite(
            room_url="http://127.0.0.1:8765",
            meeting_id="test",
            agent_id="guest-1",
        )
        result = join_room_with_invite(str(invite["invite_token"]))
        token = str(result["session_token"])
        self.assertTrue(revoke_session(token))
        self.assertIsNone(verify_session_token(token))

    def test_invalid_session_token_rejected(self):
        self.assertIsNone(verify_session_token(""))
        self.assertIsNone(verify_session_token("bogus"))
        self.assertIsNone(verify_session_token("aas1.fake"))


if __name__ == "__main__":
    unittest.main()
