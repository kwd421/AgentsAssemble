"""Canonical room attachment upload and download routes."""
from __future__ import annotations

from http import HTTPStatus

from agentsassemble.attachments import (
    AttachmentError,
    INLINE_SAFE_IMAGE_TYPES,
)
from agentsassemble.web.router import RequestContext, Router
from agentsassemble.meeting_events import clean_lobby_text


def register_attachment_routes(router: Router) -> None:
    @router.get_dynamic("/api/attachments/{attachment_id}")
    def download_attachment(ctx: RequestContext, params: dict[str, str]) -> None:
        try:
            metadata, file_path = ctx.deps.media.read_file(params["attachment_id"])
        except AttachmentError as error:
            ctx.send_error(HTTPStatus.NOT_FOUND, str(error))
            return
        inline = metadata.get("is_image") is True and "view" in ctx.query and "download" not in ctx.query
        ctx.send_attachment_file(file_path, metadata, inline=inline)

    @router.post("/api/attachments")
    def upload_attachment(ctx: RequestContext) -> None:
        payload = ctx.read_json_body()
        if payload is None:
            return
        try:
            attachment = ctx.deps.media.store(payload)
        except AttachmentError as error:
            ctx.send_error(HTTPStatus.BAD_REQUEST, str(error))
            return
        response: dict[str, object] = {"attachment": attachment}
        room_id = clean_lobby_text(payload.get("room_id") or payload.get("meeting_id"), limit=128)
        if room_id:
            try:
                _metadata, file_path = ctx.deps.media.read_file(str(attachment.get("id") or ""))
                response["room_media"] = ctx.deps.rooms.attach_media(
                    room_id,
                    filename=str(attachment.get("filename") or ""),
                    content_type=str(attachment.get("content_type") or ""),
                    data=file_path.read_bytes(),
                    supported=str(attachment.get("content_type") or "") in INLINE_SAFE_IMAGE_TYPES,
                )
            except (AttachmentError, OSError, ValueError) as error:
                ctx.send_error(HTTPStatus.BAD_REQUEST, str(error))
                return
        ctx.send_json(response)
