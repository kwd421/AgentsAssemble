"""Current custom text-channel and voice-presence HTTP routes."""
from __future__ import annotations

import threading
from collections.abc import Callable
from http import HTTPStatus

from agentsassemble.features.jsonl_chat import (
    append_chat_event,
    read_chat_events,
    read_chat_events_after,
)
from agentsassemble.room.channels import channel_stream_filename, find_channel
from agentsassemble.room.moderation import is_room_member_muted
from agentsassemble.room.speech import (
    ActorIdentity,
    GovernedLobbySayRejected,
    ensure_lobby_say_allowed,
    governed_channel_say,
)
from agentsassemble.room.text import clean_room_text
from agentsassemble.room.voice_presence import join_voice, leave_voice, voice_participants
from agentsassemble.web.router import RequestContext, Router


_CHANNEL_WRITE_LOCK = threading.Lock()
_LOCAL_OPERATOR_PARTICIPANT_ID = "operator-local"
_LOCAL_OPERATOR_DISPLAY_DEFAULT = "호스트"


def register_room_media_routes(
    router: Router,
    *,
    speech_rejection_status: Callable[[str], HTTPStatus],
) -> None:
    def channels_for(ctx: RequestContext, room_id: str) -> list[dict[str, object]]:
        channels = ctx.deps.rooms.room_settings(room_id).get("channels")
        return list(channels) if isinstance(channels, list) else []

    def require_room(ctx: RequestContext, room_id: str) -> bool:
        if not room_id:
            ctx.send_error(HTTPStatus.BAD_REQUEST, "room_id is required")
            return False
        if ctx.deps.rooms.room(room_id):
            return True
        ctx.send_error(HTTPStatus.NOT_FOUND, f"Room {room_id} was not found.")
        return False

    def channel_caller(
        ctx: RequestContext,
        requested_room_id: str = "",
        *,
        write: bool = False,
    ) -> tuple[str | None, dict[str, object] | None]:
        session = ctx.session()
        if session is not None:
            if write and session.get("invite_scope") == "read_only":
                ctx.send_error(HTTPStatus.FORBIDDEN, "read-only invite session cannot post")
                return None, None
            return str(session.get("meeting_id") or ""), session
        if ctx.is_local_operator() or ctx.is_host():
            return clean_room_text(requested_room_id, limit=128), None
        ctx.send_error(HTTPStatus.UNAUTHORIZED, "session token required")
        return None, None

    def resolve_channel(
        ctx: RequestContext,
        room_id: str,
        channel_id: str,
        *,
        want_type: str,
    ) -> dict[str, object] | None:
        channel = find_channel(channels_for(ctx, room_id), channel_id)
        if channel is None:
            ctx.send_error(HTTPStatus.NOT_FOUND, "unknown channel")
            return None
        if str(channel.get("type")) != want_type:
            ctx.send_error(HTTPStatus.BAD_REQUEST, f"channel is not a {want_type} channel")
            return None
        return channel

    def voice_identity(
        session: dict[str, object] | None,
        payload: dict[str, object],
    ) -> tuple[str, str]:
        if session is not None:
            return (
                str(session.get("agent_id") or ""),
                str(session.get("display_name") or session.get("agent_id") or ""),
            )
        return (
            _LOCAL_OPERATOR_PARTICIPANT_ID,
            clean_room_text(payload.get("name"), limit=80)
            or _LOCAL_OPERATOR_DISPLAY_DEFAULT,
        )

    @router.get("/api/room-channels")
    def room_channels(ctx: RequestContext) -> None:
        room_id = ctx.query_value("room_id") or ctx.query_value("meeting_id")
        if not ctx.require_room_access(room_id) or not require_room(ctx, room_id):
            return
        ctx.send_json({"room_id": room_id, "channels": channels_for(ctx, room_id)})

    @router.post("/api/room-channels")
    def reject_http_channel_mutation(ctx: RequestContext) -> None:
        if not ctx.require_moderator():
            return
        ctx.send_error(
            HTTPStatus.CONFLICT,
            "Room channels must use the canonical room WebSocket settings command.",
        )

    @router.get("/api/room/channel-lobby")
    def channel_history(ctx: RequestContext) -> None:
        room_id, _ = channel_caller(
            ctx,
            ctx.query_value("room_id") or ctx.query_value("meeting_id"),
        )
        if room_id is None:
            return
        channel_id = ctx.query_value("channel_id")
        if resolve_channel(ctx, room_id, channel_id, want_type="text") is None:
            return
        filename = channel_stream_filename(channel_id)
        if not filename:
            ctx.send_error(HTTPStatus.BAD_REQUEST, "invalid channel id")
            return
        path = ctx.deps.output_root / filename
        after = ctx.query_value("after").strip()
        events = (
            read_chat_events_after(path, after, limit=80)
            if after
            else read_chat_events(path, limit=80)
        )
        ctx.send_json({"events": events, "channel_id": channel_id})

    @router.post("/api/room/channel-say")
    def channel_say(ctx: RequestContext) -> None:
        payload = ctx.read_json_body()
        if payload is None:
            return
        room_id, session = channel_caller(
            ctx,
            str(payload.get("room_id") or payload.get("meeting_id") or ""),
            write=True,
        )
        if room_id is None:
            return
        if session is not None:
            identity = ActorIdentity.from_mapping(session)
            try:
                ensure_lobby_say_allowed(
                    ctx.deps.output_root,
                    identity,
                    is_muted=is_room_member_muted,
                )
            except GovernedLobbySayRejected as rejected:
                ctx.send_error(HTTPStatus.FORBIDDEN, str(rejected))
                return
        else:
            identity = ActorIdentity(
                agent_id=_LOCAL_OPERATOR_PARTICIPANT_ID,
                display_name=clean_room_text(payload.get("name"), limit=80)
                or _LOCAL_OPERATOR_DISPLAY_DEFAULT,
                participant_type="human",
                meeting_id=room_id,
            )
        channel_id = str(payload.get("channel_id") or "")
        if resolve_channel(ctx, room_id, channel_id, want_type="text") is None:
            return
        filename = channel_stream_filename(channel_id)
        if not filename:
            ctx.send_error(HTTPStatus.BAD_REQUEST, "invalid channel id")
            return
        try:
            with _CHANNEL_WRITE_LOCK:
                event = governed_channel_say(
                    ctx.deps.output_root,
                    channel_path=ctx.deps.output_root / filename,
                    identity=identity,
                    payload=payload,
                    append_channel_event=lambda path, event_payload, **_metadata: append_chat_event(
                        path,
                        event_payload,
                        channel=channel_id,
                    ),
                    is_muted=is_room_member_muted,
                    side="mine" if session is None else "other",
                    policy_already_checked=True,
                )
        except GovernedLobbySayRejected as rejected:
            ctx.send_error(speech_rejection_status(rejected.category), str(rejected))
            return
        ctx.send_json({"event": event, "channel_id": channel_id})

    def voice_response(ctx: RequestContext, room_id: str, channel_id: str) -> None:
        ctx.send_json(
            {
                "channel_id": channel_id,
                "participants": voice_participants(room_id, channel_id),
            }
        )

    @router.get("/api/room/voice")
    def voice(ctx: RequestContext) -> None:
        room_id, _ = channel_caller(
            ctx,
            ctx.query_value("room_id") or ctx.query_value("meeting_id"),
        )
        if room_id is None:
            return
        channel_id = ctx.query_value("channel_id")
        if resolve_channel(ctx, room_id, channel_id, want_type="voice") is None:
            return
        voice_response(ctx, room_id, channel_id)

    @router.post("/api/room/voice/join")
    def voice_join(ctx: RequestContext) -> None:
        payload = ctx.read_json_body()
        if payload is None:
            return
        room_id, session = channel_caller(
            ctx,
            str(payload.get("room_id") or payload.get("meeting_id") or ""),
            write=True,
        )
        if room_id is None:
            return
        channel_id = str(payload.get("channel_id") or "")
        if resolve_channel(ctx, room_id, channel_id, want_type="voice") is None:
            return
        participant_id, display_name = voice_identity(session, payload)
        join_voice(
            room_id,
            channel_id,
            participant_id,
            display_name=display_name,
            self_muted=bool(payload.get("muted", False)),
        )
        voice_response(ctx, room_id, channel_id)

    @router.post("/api/room/voice/leave")
    def voice_leave(ctx: RequestContext) -> None:
        payload = ctx.read_json_body()
        if payload is None:
            return
        room_id, session = channel_caller(
            ctx,
            str(payload.get("room_id") or payload.get("meeting_id") or ""),
        )
        if room_id is None:
            return
        channel_id = str(payload.get("channel_id") or "")
        participant_id, _ = voice_identity(session, payload)
        leave_voice(room_id, channel_id, participant_id)
        voice_response(ctx, room_id, channel_id)


__all__ = ["register_room_media_routes"]
