import tempfile
import unittest
from http import HTTPStatus
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

from agentsassemble import room_invite
from agentsassemble.application import room_users
from agentsassemble.admission.session_service import RoomSessionService
from agentsassemble.web.router import GuiDeps, RequestContext
from agentsassemble.persistence.local.admission.repository import (
    MemoryInviteSessionRepository,
)
from agentsassemble.persistence.local.identity.registry import (
    identity_store_at,
    reset_identity_store_registry,
)

DEVICE_TOKEN = "phone-device-token-1234"


class FakeHandler:
    def __init__(self, *, headers=None):
        self.headers = dict(headers or {})
        self.headers.setdefault("Host", "room.example.com")
        self.server = SimpleNamespace(server_address=("0.0.0.0", 8765))
        self.sent_json = None
        self.sent_error = None

    def _send_json(self, payload):
        self.sent_json = payload

    def _send_error(self, status, message):
        self.sent_error = (status, message)


def _context(handler, deps):
    parsed = urlparse("/api/test")
    return RequestContext(handler, deps, parsed, parse_qs(parsed.query))


class HostAccountTests(unittest.TestCase):
    """The operator account moderates from any entrance (DB-3)."""

    def setUp(self):
        room_invite.reset_state()
        room_users.reset_state()
        reset_identity_store_registry()
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.addCleanup(room_invite.reset_state)
        self.addCleanup(room_users.reset_state)
        self.addCleanup(reset_identity_store_registry)
        self.identities = identity_store_at(Path(self._tmp.name) / "identity.db")
        room_users.configure_room_users_backend(self.identities)
        self.invite_repository = MemoryInviteSessionRepository()
        room_invite.configure_room_invite_repository(self.invite_repository)
        room_invite.set_runtime_host_token("host-secret")
        self.deps = GuiDeps(
            output_root=Path(self._tmp.name),
            identity_backend=self.identities,
            room_sessions=RoomSessionService(
                self.invite_repository,
                token_prefix=room_invite.SESSION_TOKEN_PREFIX,
                ttl_seconds=room_invite.SESSION_TOKEN_TTL_SECONDS,
            ),
            public_invite_runtime=room_invite.compatibility_public_invite_runtime(),
        )

    def _join(self, device_token=""):
        invite = room_invite.create_room_invite(
            room_url="http://192.168.1.10:8765",
            meeting_id="ops-room",
            agent_id="guest",
            display_name="Guest",
        )
        return room_invite.join_room_with_invite(
            invite["invite_token"],
            meeting_id="ops-room",
            display_name="운영자폰",
            device_token=device_token,
        )

    def test_claimed_device_join_carries_operator_flag(self):
        room_users.grant_operator_to_device(DEVICE_TOKEN, display_name="SeiNel")
        result = self._join(device_token=DEVICE_TOKEN)
        self.assertEqual(result["status"], "admitted")
        self.assertEqual(result["agent_id"], "operator-local")
        self.assertTrue(result["operator"])
        self.assertTrue(result["stable_identity"])

    def test_two_claimed_devices_join_as_the_same_operator_participant(self):
        second_device = "tablet-device-token-5678"
        first_user = room_users.grant_operator_to_device(DEVICE_TOKEN, display_name="SeiNel")
        second_user = room_users.grant_operator_to_device(second_device)

        self.assertEqual(first_user["user_id"], second_user["user_id"])
        self.assertEqual(first_user["participant_id"], "operator-local")
        self.assertEqual(self._join(device_token=DEVICE_TOKEN)["agent_id"], "operator-local")
        self.assertEqual(self._join(device_token=second_device)["agent_id"], "operator-local")

    def test_unclaimed_device_join_is_not_operator(self):
        result = self._join(device_token="someone-elses-device-1")
        self.assertEqual(result["status"], "admitted")
        self.assertFalse(result["operator"])

    def test_operator_session_passes_require_moderator_without_host_token(self):
        room_users.grant_operator_to_device(DEVICE_TOKEN)
        result = self._join(device_token=DEVICE_TOKEN)
        handler = FakeHandler(headers={"Authorization": f"Bearer {result['session_token']}"})
        self.assertTrue(_context(handler, self.deps).require_moderator())
        self.assertIsNone(handler.sent_error)

    def test_regular_guest_session_fails_require_moderator(self):
        result = self._join(device_token="ordinary-guest-device")
        handler = FakeHandler(headers={"Authorization": f"Bearer {result['session_token']}"})
        self.assertFalse(_context(handler, self.deps).require_moderator())
        self.assertEqual(handler.sent_error[0], HTTPStatus.FORBIDDEN)

    def test_host_token_still_passes_require_moderator(self):
        handler = FakeHandler(headers={"X-Host-Token": "host-secret"})
        self.assertTrue(_context(handler, self.deps).require_moderator())

    def test_loopback_operator_does_not_depend_on_the_public_host_token(self):
        handler = FakeHandler(headers={"Host": "127.0.0.1:8765"})
        handler.server = SimpleNamespace(server_address=("127.0.0.1", 8765))
        self.deps.public_invite.set_host_token("different-host-secret")

        self.assertTrue(_context(handler, self.deps).require_moderator())
        self.assertIsNone(handler.sent_error)

    def test_grant_requires_meaningful_device_token(self):
        self.assertIsNone(room_users.grant_operator_to_device("short"))

    def test_operator_survives_session_expiry_and_rejoin(self):
        room_users.grant_operator_to_device(DEVICE_TOKEN)
        first = self._join(device_token=DEVICE_TOKEN)
        second = self._join(device_token=DEVICE_TOKEN)  # rejoin revokes the old session
        self.assertEqual(first["agent_id"], second["agent_id"])
        self.assertTrue(second["operator"])
        handler = FakeHandler(headers={"Authorization": f"Bearer {second['session_token']}"})
        self.assertTrue(_context(handler, self.deps).require_moderator())


if __name__ == "__main__":
    unittest.main()
