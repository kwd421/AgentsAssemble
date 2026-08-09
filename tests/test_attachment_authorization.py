from __future__ import annotations

import base64
import json
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import Mock
from urllib.parse import parse_qs, urlparse

from agentsassemble.admission.invite_service import InviteApplicationService
from agentsassemble.admission.session_service import RoomSessionService
from agentsassemble.application.public_invite_runtime import PublicInviteRuntime
from agentsassemble.persistence.local.admission.repository import (
    MemoryInviteSessionRepository,
)
from agentsassemble.persistence.local.identity.repository import IdentityStore
from agentsassemble.persistence.local.room.repository import RoomStore
from agentsassemble.room.attachments import (
    FileAttachmentStore,
    read_attachment_file,
    read_attachment_metadata,
    store_uploaded_attachment,
)
from agentsassemble.web.router import GuiDeps, RequestContext, Router
from agentsassemble.web.routes.attachments import register_attachment_routes
from tests.gui_server_test_support import HTTPStatus, _RoomsRouteHandler


def _attachment_dependencies(
    root: Path,
    *,
    public_invite: PublicInviteRuntime | None = None,
) -> GuiDeps:
    repository = MemoryInviteSessionRepository()
    invites = InviteApplicationService(repository)
    return GuiDeps(
        output_root=root,
        room_repository=RoomStore(root),
        identity_backend=IdentityStore(root / "identity.db"),
        invite_application=invites,
        room_sessions=RoomSessionService(
            repository,
            token_prefix="aas1",
            ttl_seconds=3600,
            token_key=invites.signing_secret,
        ),
        public_invite_runtime=public_invite or PublicInviteRuntime(environ={}),
        attachment_store=FileAttachmentStore(root),
    )


def _dispatch_attachment_upload(
    deps: GuiDeps,
    payload: dict[str, object],
    *,
    headers: dict[str, str] | None = None,
    loopback: bool = False,
    content_length: int | None = None,
) -> _RoomsRouteHandler:
    path = "/api/attachments"
    handler = _RoomsRouteHandler(
        path=path,
        method="POST",
        payload=payload,
        headers=headers,
        loopback=loopback,
    )
    if content_length is not None:
        handler.headers["Content-Length"] = str(content_length)
    router = Router()
    register_attachment_routes(router)
    parsed = urlparse(path)
    context = RequestContext(handler, deps, parsed, parse_qs(parsed.query))
    if not router.dispatch("POST", context):
        raise AssertionError("attachment route was not handled")
    return handler


def _dispatch_attachment_download(
    deps: GuiDeps,
    path: str,
    *,
    headers: dict[str, str] | None = None,
    loopback: bool = False,
) -> _RoomsRouteHandler:
    handler = _RoomsRouteHandler(
        path=path,
        method="GET",
        headers=headers,
        loopback=loopback,
    )
    handler.sent_attachment = None

    def capture_attachment(file_path, metadata, *, inline):
        handler.sent_attachment = (file_path.read_bytes(), metadata, inline)

    handler._send_attachment_file = capture_attachment
    router = Router()
    register_attachment_routes(router)
    parsed = urlparse(path)
    context = RequestContext(handler, deps, parsed, parse_qs(parsed.query))
    if not router.dispatch("GET", context):
        raise AssertionError("attachment route was not handled")
    return handler


def _image_payload(**updates: object) -> dict[str, object]:
    return {
        "filename": "avatar.png",
        "content_type": "image/png",
        "data_base64": base64.b64encode(b"image-bytes").decode("ascii"),
        **updates,
    }


class AttachmentAuthorizationTests(unittest.TestCase):
    def test_expired_prejoin_avatar_is_rejected_and_reclaimed_on_read(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            deps = _attachment_dependencies(root)
            pending = deps.media.store(
                {
                    **_image_payload(purpose="profile_avatar"),
                    "upload_subject": "prejoin:expired-read",
                    "prejoin_pending": True,
                }
            )
            attachment_id = str(pending["id"])
            attachment_dir = root / "attachments" / attachment_id
            metadata_path = attachment_dir / "metadata.json"
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            metadata["pending_until"] = (
                datetime.now(UTC) - timedelta(seconds=1)
            ).isoformat()
            metadata_path.write_text(json.dumps(metadata), encoding="utf-8")

            response = _dispatch_attachment_download(
                deps,
                f"/api/attachments/{attachment_id}?view=1",
            )

            self.assertEqual(
                response.sent_error,
                (HTTPStatus.NOT_FOUND, "Attachment not found"),
            )
            self.assertIsNone(response.sent_attachment)
            self.assertFalse(attachment_dir.exists())

    def test_prejoin_avatar_requires_a_stable_device_and_replaces_its_prior_upload(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            deps = _attachment_dependencies(root)
            deps.rooms.create_room("room-a")
            invite = deps.invites.create(
                room_url="http://127.0.0.1:8765",
                meeting_id="room-a",
                display_name="Guest",
            )
            without_device = _dispatch_attachment_upload(
                deps,
                _image_payload(
                    purpose="profile_avatar",
                    invite_token=invite["join_code"],
                ),
            )
            first = _dispatch_attachment_upload(
                deps,
                _image_payload(
                    purpose="profile_avatar",
                    invite_token=invite["join_code"],
                    device_token="prejoin-avatar-device",
                ),
            )
            second = _dispatch_attachment_upload(
                deps,
                _image_payload(
                    purpose="profile_avatar",
                    invite_token=invite["join_code"],
                    device_token="prejoin-avatar-device",
                    data_base64=base64.b64encode(b"replacement-avatar").decode("ascii"),
                ),
            )

            self.assertEqual(
                without_device.sent_error,
                (
                    HTTPStatus.UNAUTHORIZED,
                    "device token required for pre-join profile upload",
                ),
            )
            first_id = str(first.sent_json["attachment"]["id"])
            second_id = str(second.sent_json["attachment"]["id"])
            self.assertFalse((root / "attachments" / first_id).exists())
            self.assertTrue((root / "attachments" / second_id).is_dir())
            self.assertEqual(
                read_attachment_metadata(root, second_id)["prejoin_pending"],
                True,
            )

    def test_private_room_attachment_download_requires_current_room_authority(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            deps = _attachment_dependencies(root)
            deps.rooms.create_room("room-a")
            deps.rooms.create_room("room-b")
            attachment = store_uploaded_attachment(
                root,
                _image_payload(room_id="room-a"),
            )
            other_room_token, _session = deps.sessions.issue(
                {
                    "agent_id": "guest-b",
                    "display_name": "Guest B",
                    "meeting_id": "room-b",
                    "invite_scope": "room",
                    "participant_type": "human",
                    "client_type": "browser",
                }
            )
            same_room_token, _session = deps.sessions.issue(
                {
                    "agent_id": "guest-a",
                    "display_name": "Guest A",
                    "meeting_id": "room-a",
                    "invite_scope": "room",
                    "participant_type": "human",
                    "client_type": "browser",
                }
            )

            anonymous = _dispatch_attachment_download(
                deps,
                f"/api/attachments/{attachment['id']}?view=1",
            )
            wrong_room = _dispatch_attachment_download(
                deps,
                f"/api/attachments/{attachment['id']}?view=1",
                headers={"Authorization": f"Bearer {other_room_token}"},
            )
            same_room = _dispatch_attachment_download(
                deps,
                f"/api/attachments/{attachment['id']}?view=1",
                headers={"Authorization": f"Bearer {same_room_token}"},
            )
            stale_public_url = _dispatch_attachment_download(
                deps,
                str(attachment["url"]),
            )

            self.assertEqual(
                anonymous.sent_error,
                (HTTPStatus.UNAUTHORIZED, "attachment access is required"),
            )
            self.assertEqual(
                wrong_room.sent_error,
                (HTTPStatus.FORBIDDEN, "attachment is not part of this session room"),
            )
            self.assertEqual(same_room.sent_attachment[0], b"image-bytes")
            self.assertEqual(
                stale_public_url.sent_error,
                (HTTPStatus.UNAUTHORIZED, "attachment access is required"),
            )

    def test_oversized_public_request_is_rejected_before_body_read(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            deps = _attachment_dependencies(root)

            response = _dispatch_attachment_upload(
                deps,
                _image_payload(room_id="room-a"),
                content_length=100 * 1024 * 1024,
            )

            self.assertEqual(
                response.sent_error,
                (
                    HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                    "Attachment request is too large",
                ),
            )
            self.assertEqual(response.rfile.tell(), 0)
            self.assertFalse((root / "attachments").exists())

    def test_unauthenticated_remote_upload_is_rejected_before_file_write(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            deps = _attachment_dependencies(root)
            deps.rooms.create_room("room-a")

            response = _dispatch_attachment_upload(
                deps,
                _image_payload(room_id="room-a"),
            )

            self.assertEqual(
                response.sent_error,
                (
                    HTTPStatus.UNAUTHORIZED,
                    "operator credential or room posting session required",
                ),
            )
            self.assertFalse((root / "attachments").exists())

    def test_posting_session_upload_is_bound_to_session_room(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            deps = _attachment_dependencies(root)
            deps.rooms.create_room("room-a")
            deps.rooms.create_room("room-b")
            session_token, _session = deps.sessions.issue(
                {
                    "agent_id": "guest-a",
                    "display_name": "Guest A",
                    "meeting_id": "room-a",
                    "invite_scope": "room",
                    "participant_type": "human",
                    "client_type": "browser",
                }
            )
            headers = {"Authorization": f"Bearer {session_token}"}

            mismatch = _dispatch_attachment_upload(
                deps,
                _image_payload(room_id="room-b"),
                headers=headers,
            )
            accepted = _dispatch_attachment_upload(
                deps,
                _image_payload(),
                headers=headers,
            )

            self.assertEqual(
                mismatch.sent_error,
                (
                    HTTPStatus.FORBIDDEN,
                    "attachment room does not match the posting session",
                ),
            )
            attachment = accepted.sent_json["attachment"]
            metadata = read_attachment_metadata(root, str(attachment["id"]))
            _public_metadata, attachment_path = read_attachment_file(
                root,
                str(attachment["id"]),
            )
            self.assertEqual(metadata["room_id"], "room-a")
            self.assertIn("room_media", accepted.sent_json)
            self.assertTrue(attachment_path.is_file())
            media_event = deps.rooms.read_events("room-a")[-1]
            self.assertEqual(media_event["type"], "media_attached")
            self.assertEqual(accepted.sent_json["room_media"], media_event["media"])
            self.assertNotIn("path", accepted.sent_json["room_media"])
            self.assertEqual(len(list((root / "attachments").iterdir())), 1)

    def test_posting_session_cannot_publish_an_anonymous_room_appearance(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            deps = _attachment_dependencies(root)
            deps.rooms.create_room("room-a")
            session_token, _session = deps.sessions.issue(
                {
                    "agent_id": "guest-a",
                    "display_name": "Guest A",
                    "meeting_id": "room-a",
                    "invite_scope": "room",
                    "participant_type": "human",
                    "client_type": "browser",
                }
            )

            response = _dispatch_attachment_upload(
                deps,
                _image_payload(purpose="room_appearance"),
                headers={"Authorization": f"Bearer {session_token}"},
            )

            attachment = response.sent_json["attachment"]
            attachment_id = str(attachment["id"])
            self.assertEqual(
                read_attachment_metadata(root, attachment_id)["purpose"],
                "room_attachment",
            )
            anonymous = _dispatch_attachment_download(
                deps,
                f"/api/attachments/{attachment_id}?view=1",
            )
            self.assertEqual(
                anonymous.sent_error,
                (HTTPStatus.UNAUTHORIZED, "attachment access is required"),
            )

    def test_failed_canonical_media_write_removes_the_staged_upload(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            deps = _attachment_dependencies(root)
            deps.rooms.create_room("room-a")
            failing_rooms = Mock(wraps=deps.rooms)
            failing_rooms.attach_media.side_effect = ValueError(
                "canonical media write failed",
            )
            deps.room_repository = failing_rooms
            session_token, _session = deps.sessions.issue(
                {
                    "agent_id": "guest-a",
                    "display_name": "Guest A",
                    "meeting_id": "room-a",
                    "invite_scope": "room",
                    "participant_type": "human",
                    "client_type": "browser",
                }
            )

            response = _dispatch_attachment_upload(
                deps,
                _image_payload(),
                headers={"Authorization": f"Bearer {session_token}"},
            )

            self.assertEqual(
                response.sent_error,
                (HTTPStatus.BAD_REQUEST, "canonical media write failed"),
            )
            self.assertEqual(list((root / "attachments").iterdir()), [])
            self.assertNotIn(
                "media_attached",
                [event["type"] for event in deps.rooms.read_events("room-a")],
            )

    def test_read_only_session_cannot_upload(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            deps = _attachment_dependencies(root)
            deps.rooms.create_room("room-a")
            session_token, _session = deps.sessions.issue(
                {
                    "agent_id": "guest-read-only",
                    "display_name": "Guest",
                    "meeting_id": "room-a",
                    "invite_scope": "read_only",
                    "participant_type": "human",
                    "client_type": "browser",
                }
            )

            response = _dispatch_attachment_upload(
                deps,
                _image_payload(room_id="room-a"),
                headers={"Authorization": f"Bearer {session_token}"},
            )

            self.assertEqual(
                response.sent_error,
                (
                    HTTPStatus.FORBIDDEN,
                    "read-only invite session cannot upload attachments",
                ),
            )
            self.assertFalse((root / "attachments").exists())

    def test_browser_invite_authorizes_only_safe_prejoin_profile_image(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            deps = _attachment_dependencies(root)
            deps.rooms.create_room("room-a")
            deps.rooms.create_room("room-b")
            invite = deps.invites.create(
                room_url="http://127.0.0.1:8765",
                meeting_id="room-a",
                display_name="Guest",
            )

            mismatch = _dispatch_attachment_upload(
                deps,
                _image_payload(
                    room_id="room-b",
                    purpose="profile_avatar",
                    invite_token=invite["join_code"],
                    device_token="profile-upload-device",
                ),
            )
            accepted = _dispatch_attachment_upload(
                deps,
                _image_payload(
                    purpose="profile_avatar",
                    invite_token=invite["join_code"],
                    device_token="profile-upload-device",
                ),
            )
            rejected = _dispatch_attachment_upload(
                deps,
                {
                    "purpose": "profile_avatar",
                    "invite_token": invite["join_code"],
                    "device_token": "profile-upload-device",
                    "filename": "active.svg",
                    "content_type": "image/svg+xml",
                    "data_base64": base64.b64encode(b"<svg></svg>").decode("ascii"),
                },
            )

            self.assertEqual(
                mismatch.sent_error,
                (HTTPStatus.FORBIDDEN, "meeting_mismatch"),
            )
            attachment = accepted.sent_json["attachment"]
            metadata = read_attachment_metadata(root, str(attachment["id"]))
            self.assertEqual(metadata["room_id"], "room-a")
            public_avatar = _dispatch_attachment_download(
                deps,
                str(attachment["url"]),
            )
            self.assertNotIn("room_media", accepted.sent_json)
            self.assertNotIn(
                "media_attached",
                [event["type"] for event in deps.rooms.read_events("room-a")],
            )
            self.assertEqual(
                rejected.sent_error,
                (
                    HTTPStatus.BAD_REQUEST,
                    "profile avatar must be a supported image",
                ),
            )
            self.assertEqual(len(list((root / "attachments").iterdir())), 1)
            self.assertEqual(public_avatar.sent_attachment[0], b"image-bytes")

    def test_agent_bridge_invite_cannot_authorize_prejoin_profile_upload(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            deps = _attachment_dependencies(root)
            deps.rooms.create_room("room-a")
            invite = deps.invites.create(
                room_url="http://127.0.0.1:8765",
                meeting_id="room-a",
                display_name="Codex",
                client_type="agent_bridge",
                participant_type="agent",
                provider_kind="codex",
                max_uses=1,
            )

            response = _dispatch_attachment_upload(
                deps,
                _image_payload(
                    purpose="profile_avatar",
                    invite_token=invite["join_code"],
                    device_token="agent-bridge-upload-device",
                ),
            )

            self.assertEqual(
                response.sent_error,
                (
                    HTTPStatus.FORBIDDEN,
                    "browser guest invite required for pre-join profile upload",
                ),
            )
            self.assertFalse((root / "attachments").exists())

    def test_explicit_host_credential_can_upload_to_selected_room(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            public_invite = PublicInviteRuntime(environ={})
            public_invite.set_host_token("host-secret")
            deps = _attachment_dependencies(root, public_invite=public_invite)
            deps.rooms.create_room("room-a")

            response = _dispatch_attachment_upload(
                deps,
                _image_payload(room_id="room-a"),
                headers={"X-Host-Token": "host-secret"},
            )

            self.assertIsNone(response.sent_error)
            self.assertIn("room_media", response.sent_json)


if __name__ == "__main__":
    unittest.main()
