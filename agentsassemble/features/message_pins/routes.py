"""HTTP projection and human-only mutation routes for message pins."""
from __future__ import annotations

from http import HTTPStatus

from agentsassemble.features.jsonl_chat import read_chat_events
from agentsassemble.room.channels import channel_stream_filename, find_channel
from agentsassemble.room.text import clean_room_text
from agentsassemble.web.router import RequestContext, Router


LOBBY_CHANNEL_ID = "lobby"


def register_message_pin_routes(router: Router) -> None:
    @router.get("/api/room-pins")
    def list_pins(ctx: RequestContext) -> None:
        room_id = clean_room_text(
            ctx.query_value("room_id") or ctx.query_value("meeting_id"), limit=128
        )
        channel_id = clean_room_text(ctx.query_value("channel_id"), limit=128)
        if not ctx.require_room_access(room_id):
            return
        if not _channel_exists(ctx, room_id, channel_id):
            return
        ctx.send_json({"pins": _resolved_pins(ctx, room_id, channel_id)})

    @router.post("/api/room-pins")
    def set_pin(ctx: RequestContext) -> None:
        payload = ctx.read_json_body()
        if payload is None:
            return
        room_id = clean_room_text(
            payload.get("room_id") or payload.get("meeting_id"), limit=128
        )
        channel_id = clean_room_text(payload.get("channel_id"), limit=128)
        event_id = clean_room_text(payload.get("event_id"), limit=128)
        identity = ctx.room_command_identity(room_id)
        if identity is None:
            return
        if str(identity.get("meeting_id") or "") != room_id:
            ctx.send_error(HTTPStatus.FORBIDDEN, "session is not authorized for this room")
            return
        if str(identity.get("participant_type") or "") != "human":
            ctx.send_error(HTTPStatus.FORBIDDEN, "only people can change pinned messages")
            return
        if str(identity.get("invite_scope") or "room") == "read_only":
            ctx.send_error(HTTPStatus.FORBIDDEN, "read-only members cannot change pinned messages")
            return
        if not event_id:
            ctx.send_error(HTTPStatus.BAD_REQUEST, "event_id is required")
            return
        if not _channel_exists(ctx, room_id, channel_id):
            return
        event = _channel_event(ctx, room_id, channel_id, event_id)
        if not event:
            ctx.send_error(HTTPStatus.NOT_FOUND, "message was not found")
            return
        pinned = payload.get("pinned") is not False
        if pinned:
            ctx.deps.rooms.pin_message(
                room_id,
                channel_id,
                event_id,
                pinned_by=clean_room_text(identity.get("agent_id"), limit=128),
            )
        else:
            ctx.deps.rooms.unpin_message(room_id, channel_id, event_id)
        ctx.send_json(
            {
                "pinned": pinned,
                "pins": _resolved_pins(ctx, room_id, channel_id),
            }
        )


def _channel_exists(ctx: RequestContext, room_id: str, channel_id: str) -> bool:
    if not room_id or not ctx.deps.rooms.room(room_id):
        ctx.send_error(HTTPStatus.NOT_FOUND, "room was not found")
        return False
    if channel_id == LOBBY_CHANNEL_ID:
        return True
    channels = ctx.deps.rooms.room_settings(room_id).get("channels")
    channel = find_channel(list(channels) if isinstance(channels, list) else [], channel_id)
    if channel is None or str(channel.get("type") or "") != "text":
        ctx.send_error(HTTPStatus.NOT_FOUND, "text channel was not found")
        return False
    return True


def _resolved_pins(
    ctx: RequestContext,
    room_id: str,
    channel_id: str,
) -> list[dict[str, object]]:
    resolved: list[dict[str, object]] = []
    for pin in ctx.deps.rooms.pinned_messages(room_id, channel_id):
        event_id = str(pin.get("event_id") or "")
        event = _channel_event(ctx, room_id, channel_id, event_id)
        if not event:
            ctx.deps.rooms.unpin_message(room_id, channel_id, event_id)
            continue
        resolved.append(_pin_projection(pin, event, channel_id))
    return resolved


def _channel_event(
    ctx: RequestContext,
    room_id: str,
    channel_id: str,
    event_id: str,
) -> dict[str, object]:
    if channel_id == LOBBY_CHANNEL_ID:
        event = ctx.deps.rooms.event_by_id(room_id, event_id)
        if (
            str(event.get("type") or "") != "message_final"
            or event.get("message_deleted") is True
        ):
            return {}
        return event
    filename = channel_stream_filename(channel_id)
    if not filename:
        return {}
    events = read_chat_events(ctx.deps.output_root / filename, limit=10_000)
    return next((event for event in events if str(event.get("id") or "") == event_id), {})


def _pin_projection(
    pin: dict[str, object],
    event: dict[str, object],
    channel_id: str,
) -> dict[str, object]:
    actor = event.get("actor") if isinstance(event.get("actor"), dict) else {}
    attachments = event.get("attachments") if isinstance(event.get("attachments"), list) else []
    return {
        "event_id": str(pin.get("event_id") or ""),
        "channel_id": channel_id,
        "pinned_at": str(pin.get("pinned_at") or ""),
        "seq": int(event.get("seq") or 0),
        "author": clean_room_text(
            event.get("display_name")
            or event.get("name")
            or actor.get("participant_id"),
            limit=128,
        )
        or "Room",
        "content": clean_room_text(event.get("content") or event.get("message"), limit=12_000),
        "created_at": clean_room_text(event.get("created_at"), limit=128),
        "attachment_filenames": [
            clean_room_text(item.get("filename"), limit=256)
            for item in attachments
            if isinstance(item, dict) and clean_room_text(item.get("filename"), limit=256)
        ],
    }


__all__ = ["register_message_pin_routes"]
