from __future__ import annotations

import io
import json
import tempfile
import unittest
from http import HTTPStatus
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

from agentsassemble.admission.session_service import RoomSessionService
from agentsassemble.features.social.routes import register_room_friend_profile_routes
from agentsassemble.persistence.local.admission.repository import MemoryInviteSessionRepository
from agentsassemble.persistence.local.identity.repository import IdentityStore
from agentsassemble.persistence.local.room.repository import RoomStore
from agentsassemble.web.router import GuiDeps, RequestContext, Router


class _ProfileHandler:
    def __init__(self, path: str, *, method: str, token: str = "", payload: object = None) -> None:
        body = b"" if payload is None else json.dumps(payload).encode("utf-8")
        self.path = path
        self.command = method
        self.headers = {
            "Host": "room.example.test",
            "Content-Length": str(len(body)),
        }
        if token:
            self.headers["Authorization"] = f"Bearer {token}"
        self.rfile = io.BytesIO(body)
        self.sent_json: dict[str, object] | None = None
        self.sent_error: tuple[HTTPStatus, str] | None = None
        self.server = SimpleNamespace(server_address=("0.0.0.0", 8765))
        self.client_address = ("203.0.113.12", 40000)

    def _send_json(self, payload: dict[str, object]) -> None:
        self.sent_json = payload

    def _send_error(
        self,
        status: HTTPStatus,
        message: str,
        *,
        code: str = "",
        details: dict[str, object] | None = None,
    ) -> None:
        del code, details
        self.sent_error = (status, message)


class UserProfileRoomSyncTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self._temporary_directory.name)
        self.identities = IdentityStore(self.root / "identity.db")
        self.rooms = RoomStore(self.root)
        self.sessions = RoomSessionService(
            MemoryInviteSessionRepository(),
            token_prefix="aas1",
            ttl_seconds=3600,
        )
        self.router = Router()
        register_room_friend_profile_routes(
            self.router,
            post_direct_dm=lambda _ctx, _payload: {},
        )
        self.deps = GuiDeps(
            output_root=self.root,
            room_repository=self.rooms,
            identity_backend=self.identities,
            room_sessions=self.sessions,
        )

    def tearDown(self) -> None:
        self._temporary_directory.cleanup()

    def _guest(self, suffix: str, display_name: str) -> tuple[dict[str, object], str]:
        user = self.identities.resolve_credential_user(
            f"device:guest-{suffix}",
            user_id=f"user-{suffix}",
            participant_id=f"guest-{suffix}",
            display_name=display_name,
            participant_type="human",
        )
        token, _session = self.sessions.issue(
            {
                "agent_id": user["participant_id"],
                "display_name": display_name,
                "meeting_id": "room-profile",
                "participant_type": "human",
                "client_type": "browser",
                "invite_scope": "read_write",
            }
        )
        return user, token

    def _dispatch(
        self,
        method: str,
        *,
        token: str = "",
        payload: object = None,
    ) -> _ProfileHandler:
        path = "/api/user-profile"
        handler = _ProfileHandler(path, method=method, token=token, payload=payload)
        parsed = urlparse(path)
        context = RequestContext(handler, self.deps, parsed, parse_qs(parsed.query))
        self.assertTrue(self.router.dispatch(method, context))
        return handler

    def test_guest_profile_update_reaches_identity_room_and_realtime_event(self) -> None:
        guest, token = self._guest("one", "Before")
        other, other_token = self._guest("two", "Other")
        self.rooms.ensure_room("room-profile")
        for user in (guest, other):
            participant_id = str(user["participant_id"])
            self.rooms.upsert_participant(
                "room-profile",
                {
                    "participant_id": participant_id,
                    "display_name": user["display_name"],
                    "participant_type": "human",
                    "status": "joined",
                },
            )
            self.identities.upsert_membership(
                {
                    "meeting_id": "room-profile",
                    "participant_id": participant_id,
                    "display_name": user["display_name"],
                    "participant_type": "human",
                }
            )

        saved = self._dispatch(
            "POST",
            token=token,
            payload={
                "display_name": "After",
                "avatar_label": "AF",
                "avatar_image_url": "/api/attachments/profile_123?view=1",
                "custom_status": "동기화됨",
            },
        )

        self.assertIsNone(saved.sent_error)
        self.assertEqual(saved.sent_json["profile"]["display_name"], "After")
        self.assertEqual(self.identities.get_user("user-one")["display_name"], "After")
        self.assertEqual(
            self.identities.get_user("user-one")["avatar_image_url"],
            "/api/attachments/profile_123?view=1",
        )
        participant = self.rooms.participant("room-profile", "guest-one")
        self.assertEqual(participant["display_name"], "After")
        self.assertEqual(
            participant["avatar_image_url"],
            "/api/attachments/profile_123?view=1",
        )
        event = self.rooms.read_events("room-profile", newest=True, limit=1)[0]
        self.assertEqual(event["type"], "participant_updated")
        self.assertEqual(event["participant_id"], "guest-one")

        loaded = self._dispatch("GET", token=token)
        other_loaded = self._dispatch("GET", token=other_token)
        self.assertEqual(loaded.sent_json["profile"]["custom_status"], "동기화됨")
        self.assertEqual(other_loaded.sent_json["profile"]["display_name"], "Other")

    def test_remote_anonymous_profile_access_is_rejected(self) -> None:
        response = self._dispatch("GET")

        self.assertEqual(
            response.sent_error,
            (HTTPStatus.UNAUTHORIZED, "authenticated user profile required"),
        )


if __name__ == "__main__":
    unittest.main()
