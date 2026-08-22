"""Authorized HTTP search and bounded message-context routes."""
from __future__ import annotations

from http import HTTPStatus

from agentsassemble.features.jsonl_chat import read_chat_event_context
from agentsassemble.room.channels import channel_stream_filename, clean_channels, find_channel
from agentsassemble.room.text import clean_room_text
from agentsassemble.web.router import RequestContext, Router

from .service import MessageSearchService


LOBBY_CHANNEL_ID = "lobby"
CONTEXT_RADIUS = 15


def register_message_search_routes(router: Router) -> None:
    @router.get("/api/room-search")
    def search_messages(ctx: RequestContext) -> None:
        room_id = _room_id(ctx)
        if not ctx.require_room_access(room_id) or not _room_exists(ctx, room_id):
            return
        query = clean_room_text(ctx.query_value("q"), limit=200)
        if not query:
            ctx.send_error(HTTPStatus.BAD_REQUEST, "q is required")
            return
        requested_channel = clean_room_text(ctx.query_value("channel_id"), limit=128) or LOBBY_CHANNEL_ID
        channel_ids = _searchable_channel_ids(ctx, room_id, requested_channel)
        if channel_ids is None:
            return

        service = MessageSearchService(ctx.deps.output_root)
        if LOBBY_CHANNEL_ID in channel_ids:
            service.sync_lobby(ctx.deps.rooms, room_id)
        for channel_id in channel_ids:
            if channel_id == LOBBY_CHANNEL_ID:
                continue
            service.sync_custom_channel(
                room_id,
                channel_id,
                ctx.deps.output_root / channel_stream_filename(channel_id),
            )
        ctx.send_json(
            service.search(
                room_id,
                query=query,
                channel_ids=channel_ids,
                cursor=clean_room_text(ctx.query_value("cursor"), limit=2048),
            )
        )

    @router.get("/api/room-search/context")
    def message_context(ctx: RequestContext) -> None:
        room_id = _room_id(ctx)
        if not ctx.require_room_access(room_id) or not _room_exists(ctx, room_id):
            return
        channel_id = clean_room_text(ctx.query_value("channel_id"), limit=128) or LOBBY_CHANNEL_ID
        event_id = clean_room_text(ctx.query_value("event_id"), limit=128)
        if not event_id:
            ctx.send_error(HTTPStatus.BAD_REQUEST, "event_id is required")
            return
        if channel_id == "all":
            ctx.send_error(HTTPStatus.BAD_REQUEST, "a concrete channel_id is required")
            return
        if _searchable_channel_ids(ctx, room_id, channel_id) is None:
            return
        if channel_id == LOBBY_CHANNEL_ID:
            events = _lobby_context(ctx, room_id, event_id)
        else:
            events = _custom_channel_context(ctx, channel_id, event_id)
        if events is None:
            return
        ctx.send_json({"channel_id": channel_id, "event_id": event_id, "events": events})


def _room_id(ctx: RequestContext) -> str:
    return clean_room_text(
        ctx.query_value("room_id") or ctx.query_value("meeting_id"),
        limit=128,
    )


def _room_exists(ctx: RequestContext, room_id: str) -> bool:
    if room_id and ctx.deps.rooms.room(room_id):
        return True
    ctx.send_error(HTTPStatus.NOT_FOUND, "room was not found")
    return False


def _text_channels(ctx: RequestContext, room_id: str) -> list[dict[str, object]]:
    channels = ctx.deps.rooms.room_settings(room_id).get("channels")
    return [channel for channel in clean_channels(channels) if channel.get("type") == "text"]


def _searchable_channel_ids(
    ctx: RequestContext,
    room_id: str,
    requested_channel: str,
) -> list[str] | None:
    channels = _text_channels(ctx, room_id)
    if requested_channel == "all":
        return [LOBBY_CHANNEL_ID, *(str(channel["id"]) for channel in channels)]
    if requested_channel == LOBBY_CHANNEL_ID:
        return [LOBBY_CHANNEL_ID]
    channel = find_channel(channels, requested_channel)
    if channel is None:
        ctx.send_error(HTTPStatus.NOT_FOUND, "text channel was not found")
        return None
    return [requested_channel]


def _lobby_context(
    ctx: RequestContext,
    room_id: str,
    event_id: str,
) -> list[dict[str, object]] | None:
    target = ctx.deps.rooms.event_by_id(room_id, event_id)
    if not target or str(target.get("type") or "") != "message_final":
        ctx.send_error(HTTPStatus.NOT_FOUND, "message was not found")
        return None
    seq = max(0, int(target.get("seq") or 0))
    before = ctx.deps.rooms.read_events(
        room_id,
        before_seq=seq,
        limit=CONTEXT_RADIUS,
        newest=True,
        event_types=["message_final"],
    )
    after = ctx.deps.rooms.read_events(
        room_id,
        after_seq=seq,
        limit=CONTEXT_RADIUS,
        event_types=["message_final"],
    )
    return [*before, target, *after]


def _custom_channel_context(
    ctx: RequestContext,
    channel_id: str,
    event_id: str,
) -> list[dict[str, object]] | None:
    events = read_chat_event_context(
        ctx.deps.output_root / channel_stream_filename(channel_id),
        event_id,
        radius=CONTEXT_RADIUS,
    )
    if not events:
        ctx.send_error(HTTPStatus.NOT_FOUND, "message was not found")
        return None
    return events


__all__ = ["register_message_search_routes"]
