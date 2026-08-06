"""Canonical room attachment upload and download routes."""
from __future__ import annotations

from dataclasses import dataclass
from http import HTTPStatus
import secrets

from agentsassemble.room.attachments import (
    AttachmentError,
    INLINE_SAFE_IMAGE_TYPES,
    MAX_ATTACHMENT_BYTES,
    normalize_content_type,
    sanitize_attachment_filename,
)
from agentsassemble.room.text import clean_room_text
from agentsassemble.web.router import RequestContext, Router


_PROFILE_AVATAR_PURPOSE = "profile_avatar"
# Base64 expands the 10 MiB binary limit by 4/3. The extra 64 KiB leaves room
# for the JSON envelope while keeping unauthenticated public requests bounded.
_MAX_UPLOAD_REQUEST_BYTES = ((MAX_ATTACHMENT_BYTES + 2) // 3) * 4 + (64 * 1024)


@dataclass(frozen=True)
class _AuthorizedUpload:
    room_id: str
    profile_avatar: bool = False


def register_attachment_routes(router: Router) -> None:
    @router.get_dynamic("/api/attachments/{attachment_id}")
    def download_attachment(ctx: RequestContext, params: dict[str, str]) -> None:
        try:
            private_metadata = ctx.deps.media.read_metadata(params["attachment_id"])
            metadata, file_path = ctx.deps.media.read_file(params["attachment_id"])
        except AttachmentError as error:
            ctx.send_error(HTTPStatus.NOT_FOUND, str(error))
            return
        if not _authorize_download(ctx, private_metadata):
            return
        inline = metadata.get("is_image") is True and "view" in ctx.query and "download" not in ctx.query
        ctx.send_attachment_file(file_path, metadata, inline=inline)

    @router.post("/api/attachments")
    def upload_attachment(ctx: RequestContext) -> None:
        if not _require_bounded_upload_body(ctx):
            return
        payload = ctx.read_json_body()
        if payload is None:
            return
        authority = _authorize_upload(ctx, payload)
        if authority is None:
            return
        if authority.room_id and not _require_existing_room(ctx, authority.room_id):
            return
        if authority.profile_avatar and not _require_safe_profile_image(ctx, payload):
            return
        stored_payload = {
            **payload,
            "room_id": authority.room_id,
            "meeting_id": "",
        }
        try:
            attachment = ctx.deps.media.store(stored_payload)
        except AttachmentError as error:
            ctx.send_error(HTTPStatus.BAD_REQUEST, str(error))
            return
        response: dict[str, object] = {"attachment": attachment}
        if authority.room_id and not authority.profile_avatar:
            try:
                _metadata, file_path = ctx.deps.media.read_file(str(attachment.get("id") or ""))
                response["room_media"] = ctx.deps.rooms.attach_media(
                    authority.room_id,
                    filename=str(attachment.get("filename") or ""),
                    content_type=str(attachment.get("content_type") or ""),
                    data=file_path.read_bytes(),
                    supported=str(attachment.get("content_type") or "") in INLINE_SAFE_IMAGE_TYPES,
                )
            except (AttachmentError, OSError, ValueError) as error:
                try:
                    ctx.deps.media.delete(
                        str(attachment.get("id") or ""),
                    )
                except AttachmentError:
                    pass
                ctx.send_error(HTTPStatus.BAD_REQUEST, str(error))
                return
        ctx.send_json(response)


def _require_bounded_upload_body(ctx: RequestContext) -> bool:
    raw_content_length = str(ctx.headers.get("Content-Length") or "0").strip()
    try:
        content_length = int(raw_content_length)
    except ValueError:
        ctx.send_error(HTTPStatus.BAD_REQUEST, "Invalid Content-Length")
        return False
    if content_length < 0:
        ctx.send_error(HTTPStatus.BAD_REQUEST, "Invalid Content-Length")
        return False
    if content_length <= _MAX_UPLOAD_REQUEST_BYTES:
        return True
    ctx.send_error(
        HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
        "Attachment request is too large",
    )
    return False


def _authorize_upload(
    ctx: RequestContext,
    payload: dict[str, object],
) -> _AuthorizedUpload | None:
    requested_room_id = clean_room_text(
        payload.get("room_id") or payload.get("meeting_id"),
        limit=128,
    )
    purpose = clean_room_text(payload.get("purpose"), limit=32)
    profile_avatar = purpose == _PROFILE_AVATAR_PURPOSE

    if _has_operator_authority(ctx):
        return _AuthorizedUpload(
            room_id=requested_room_id,
            profile_avatar=profile_avatar,
        )

    session = ctx.session()
    if session is not None:
        if session.get("invite_scope") == "read_only":
            ctx.send_error(
                HTTPStatus.FORBIDDEN,
                "read-only invite session cannot upload attachments",
            )
            return None
        session_room_id = clean_room_text(session.get("meeting_id"), limit=128)
        if not session_room_id:
            ctx.send_error(
                HTTPStatus.FORBIDDEN,
                "room-bound posting session required",
            )
            return None
        if requested_room_id and requested_room_id != session_room_id:
            ctx.send_error(
                HTTPStatus.FORBIDDEN,
                "attachment room does not match the posting session",
            )
            return None
        return _AuthorizedUpload(
            room_id=session_room_id,
            profile_avatar=profile_avatar,
        )

    if not profile_avatar:
        ctx.send_error(
            HTTPStatus.UNAUTHORIZED,
            "operator credential or room posting session required",
        )
        return None

    invite_token = str(payload.get("invite_token") or "").strip()
    if not invite_token:
        ctx.send_error(
            HTTPStatus.UNAUTHORIZED,
            "invite token required for pre-join profile upload",
        )
        return None
    inspected = ctx.deps.invites.inspect(invite_token, meeting_id=requested_room_id)
    if inspected.get("status") != "valid":
        reason = clean_room_text(inspected.get("reason"), limit=64) or "invite_invalid"
        ctx.send_error(HTTPStatus.FORBIDDEN, reason, code=reason)
        return None
    if (
        clean_room_text(inspected.get("client_type"), limit=32) != "browser"
        or clean_room_text(inspected.get("participant_type"), limit=32) != "human"
    ):
        ctx.send_error(
            HTTPStatus.FORBIDDEN,
            "browser guest invite required for pre-join profile upload",
        )
        return None
    invite_room_id = clean_room_text(inspected.get("meeting_id"), limit=128)
    if not invite_room_id:
        ctx.send_error(HTTPStatus.FORBIDDEN, "invite is not bound to a room")
        return None
    return _AuthorizedUpload(
        room_id=invite_room_id,
        profile_avatar=True,
    )


def _has_operator_authority(ctx: RequestContext) -> bool:
    if ctx.is_local_operator() or ctx.is_operator_session():
        return True
    return bool(ctx.deps.public_invite.host_token() and ctx.is_host())


def _authorize_download(
    ctx: RequestContext,
    metadata: dict[str, object],
) -> bool:
    if _has_operator_authority(ctx):
        return True

    expected_access = str(metadata.get("access_token") or "").strip()
    provided_access = str((ctx.query.get("access") or [""])[0]).strip()
    if (
        expected_access
        and provided_access
        and secrets.compare_digest(expected_access, provided_access)
    ):
        return True

    session = ctx.session()
    if session is None:
        ctx.send_error(HTTPStatus.UNAUTHORIZED, "attachment access is required")
        return False
    attachment_room_id = clean_room_text(metadata.get("room_id"), limit=128)
    session_room_id = clean_room_text(session.get("meeting_id"), limit=128)
    if attachment_room_id and session_room_id == attachment_room_id:
        return True
    ctx.send_error(
        HTTPStatus.FORBIDDEN,
        "attachment is not part of this session room",
    )
    return False


def _require_existing_room(ctx: RequestContext, room_id: str) -> bool:
    try:
        room = ctx.deps.rooms.room(room_id)
    except ValueError as error:
        ctx.send_error(HTTPStatus.BAD_REQUEST, str(error))
        return False
    if room:
        return True
    ctx.send_error(HTTPStatus.NOT_FOUND, "room was not found")
    return False


def _require_safe_profile_image(
    ctx: RequestContext,
    payload: dict[str, object],
) -> bool:
    filename = sanitize_attachment_filename(payload.get("filename"))
    content_type = normalize_content_type(payload.get("content_type"), filename)
    if content_type in INLINE_SAFE_IMAGE_TYPES:
        return True
    ctx.send_error(
        HTTPStatus.BAD_REQUEST,
        "profile avatar must be a supported image",
    )
    return False
