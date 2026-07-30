"""Canonical room roster, moderation, and channel/media-adjacent routes."""
from __future__ import annotations

import threading
from collections.abc import Callable
from http import HTTPStatus

from agentsassemble.web.router import RequestContext, Router
from agentsassemble.legacy.live_agent.runtime.room_admin import expel_live_agent_from_room_payload
from agentsassemble.legacy.live_agent.state import read_live_agents
from agentsassemble.legacy.meeting.core.events import (
    append_lobby_event_to_file,
    read_lobby_events,
    read_lobby_events_after,
)
from agentsassemble.room.channels import (
    channel_stream_filename,
    find_channel,
)
from agentsassemble.room.moderation import (
    is_room_member_muted,
    remove_room_member,
)
from agentsassemble.room.text import clean_room_text
from agentsassemble.room.speech import (
    ActorIdentity,
    GovernedLobbySayRejected,
    ensure_lobby_say_allowed,
    governed_channel_say,
)
from agentsassemble.room.voice_presence import (
    join_voice,
    leave_all_voice,
    leave_voice,
    voice_participants,
)
from agentsassemble.web.routes.room_members import (
    register_room_member_routes,
    room_members_response,
)


_CHANNEL_LOBBY_LOCK = threading.Lock()
_LOCAL_OPERATOR_PARTICIPANT_ID = "operator-local"
_LOCAL_OPERATOR_DISPLAY_DEFAULT = "호스트"


def register_legacy_moderation_media_routes(
    router: Router,
    *,
    speech_rejection_status: Callable[[str], HTTPStatus],
) -> None:
    """Register legacy resident kick, custom channel, and voice routes."""

    @router.post("/api/room-members/kick")
    def room_members_kick(ctx: RequestContext) -> None:
        if not ctx.require_moderator():
            return
        payload = ctx.read_json_body()
        if payload is None:
            return
        kick_meeting_id = str(payload.get("meeting_id") or "")
        kick_participant_id = str(payload.get("participant_id") or "")
        if not kick_participant_id.strip():
            ctx.send_error(HTTPStatus.BAD_REQUEST, "participant_id is required")
            return
        removed_member = remove_room_member(ctx.deps.output_root, kick_meeting_id, kick_participant_id)
        leave_all_voice(kick_meeting_id, kick_participant_id)
        expelled_agent = False
        revoked_sessions = 0

        def revoke_participant_sessions(room_id: str, participant_id: str) -> int:
            nonlocal revoked_sessions
            revoked_sessions = ctx.deps.sessions.revoke_participant(room_id, participant_id)
            return revoked_sessions

        is_live_agent = any(
            clean_room_text(agent.get("agent_id"), limit=128)
            == clean_room_text(kick_participant_id, limit=128)
            and (
                not kick_meeting_id.strip()
                or clean_room_text(agent.get("meeting_id"), limit=128)
                == clean_room_text(kick_meeting_id, limit=128)
            )
            for agent in read_live_agents(ctx.deps.output_root)
        )
        if is_live_agent:
            try:
                expel_result = expel_live_agent_from_room_payload(
                    ctx.deps.output_root,
                    ctx.deps.process_supervisor,
                    {"meeting_id": kick_meeting_id, "agent_id": kick_participant_id},
                    revoke_participant_sessions=revoke_participant_sessions,
                )
                revoked_sessions = int(expel_result.get("revoked_sessions") or revoked_sessions)
                expelled_agent = True
            except (OSError, ValueError):
                expelled_agent = False
        else:
            revoked_sessions = revoke_participant_sessions(
                kick_meeting_id,
                kick_participant_id,
            )
        ctx.send_json(
            {
                "status": "kicked",
                "participant_id": kick_participant_id,
                "revoked_sessions": revoked_sessions,
                "removed_member": removed_member,
                "expelled_agent": expelled_agent,
                **room_members_response(ctx, kick_meeting_id),
            }
        )

    def _channels_for(repository, meeting_id: str) -> list[dict[str, object]]:
        settings = repository.room_settings(meeting_id)
        channels = settings.get("channels")
        return list(channels) if isinstance(channels, list) else []

    def _require_room(ctx: RequestContext, meeting_id: str) -> bool:
        if not meeting_id.strip():
            ctx.send_error(HTTPStatus.BAD_REQUEST, "meeting_id is required")
            return False
        try:
            room = ctx.deps.rooms.room(meeting_id)
        except ValueError as error:
            ctx.send_error(HTTPStatus.BAD_REQUEST, str(error))
            return False
        if room:
            return True
        ctx.send_error(HTTPStatus.NOT_FOUND, f"Room {meeting_id} was not found.")
        return False

    @router.get("/api/room-channels")
    def room_channels_list(ctx: RequestContext) -> None:
        meeting_id = ctx.query_value("meeting_id") or ctx.query_value("room_id")
        if not ctx.require_room_access(meeting_id):
            return
        if not _require_room(ctx, meeting_id):
            return
        ctx.send_json({"room_id": meeting_id, "channels": _channels_for(ctx.deps.rooms, meeting_id)})

    @router.post("/api/room-channels")
    def room_channels_mutate(ctx: RequestContext) -> None:
        if not ctx.require_moderator():
            return
        ctx.send_error(
            HTTPStatus.CONFLICT,
            "Room channels must use the canonical room WebSocket settings command.",
        )

    def _channel_caller(ctx: RequestContext, payload_meeting_id: str = "", *, write: bool = False):
        session = ctx.session()
        if session is not None:
            if write and session.get("invite_scope") == "read_only":
                ctx.send_error(HTTPStatus.FORBIDDEN, "read-only invite session cannot post")
                return None, None
            return str(session.get("meeting_id") or ""), session
        if ctx.uses_loopback_host() or ctx.is_host():
            return str(payload_meeting_id or ""), None
        ctx.send_error(HTTPStatus.UNAUTHORIZED, "session token required")
        return None, None

    def _resolve_channel(ctx: RequestContext, meeting_id: str, channel_id: str, *, want_type: str):
        channel = find_channel(_channels_for(ctx.deps.rooms, meeting_id), channel_id)
        if channel is None:
            ctx.send_error(HTTPStatus.NOT_FOUND, "unknown channel")
            return None
        if str(channel.get("type")) != want_type:
            ctx.send_error(HTTPStatus.BAD_REQUEST, f"channel is not a {want_type} channel")
            return None
        return channel

    def _voice_caller_identity(session: dict[str, object] | None, payload: dict[str, object]) -> tuple[str, str]:
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

    @router.get("/api/room/channel-lobby")
    def room_channel_lobby(ctx: RequestContext) -> None:
        meeting_id, _session = _channel_caller(ctx, ctx.query_value("room_id") or ctx.query_value("meeting_id"))
        if meeting_id is None:
            return
        channel_id = str(ctx.query_value("channel_id") or "")
        if _resolve_channel(ctx, meeting_id, channel_id, want_type="text") is None:
            return
        filename = channel_stream_filename(channel_id)
        if not filename:
            ctx.send_error(HTTPStatus.BAD_REQUEST, "invalid channel id")
            return
        path = ctx.deps.output_root / filename
        after_event_id = ctx.query_value("after").strip()
        events = read_lobby_events_after(path, after_event_id) if after_event_id else read_lobby_events(path, limit=80)
        ctx.send_json({"events": events, "channel_id": channel_id})

    @router.post("/api/room/channel-say")
    def room_channel_say(ctx: RequestContext) -> None:
        payload = ctx.read_json_body()
        if payload is None:
            return
        meeting_id, session = _channel_caller(
            ctx, str(payload.get("meeting_id") or payload.get("room_id") or ""), write=True
        )
        if meeting_id is None:
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
                message = (
                    "read-only invite session cannot post"
                    if rejected.category == "read_only"
                    else "muted by room host"
                )
                ctx.send_error(HTTPStatus.FORBIDDEN, message)
                return
        else:
            identity = ActorIdentity(
                agent_id=_LOCAL_OPERATOR_PARTICIPANT_ID,
                display_name=clean_room_text(payload.get("name"), limit=80)
                or _LOCAL_OPERATOR_DISPLAY_DEFAULT,
                participant_type="human",
                meeting_id=meeting_id,
            )
        channel_id = str(payload.get("channel_id") or "")
        if _resolve_channel(ctx, meeting_id, channel_id, want_type="text") is None:
            return
        filename = channel_stream_filename(channel_id)
        if not filename:
            ctx.send_error(HTTPStatus.BAD_REQUEST, "invalid channel id")
            return
        path = ctx.deps.output_root / filename
        try:
            with _CHANNEL_LOBBY_LOCK:
                event = governed_channel_say(
                    ctx.deps.output_root,
                    channel_path=path,
                    identity=identity,
                    payload=payload,
                    append_channel_event=append_lobby_event_to_file,
                    is_muted=is_room_member_muted,
                    side="mine" if session is None else "other",
                    policy_already_checked=True,
                )
        except GovernedLobbySayRejected as rejected:
            ctx.send_error(speech_rejection_status(rejected.category), str(rejected))
            return
        ctx.send_json({"event": event, "channel_id": channel_id})

    def _voice_presence_response(ctx: RequestContext, meeting_id: str, channel_id: str) -> None:
        ctx.send_json(
            {"channel_id": channel_id, "participants": voice_participants(meeting_id, channel_id)}
        )

    @router.get("/api/room/voice")
    def room_voice_presence(ctx: RequestContext) -> None:
        meeting_id, _session = _channel_caller(ctx, ctx.query_value("room_id") or ctx.query_value("meeting_id"))
        if meeting_id is None:
            return
        channel_id = str(ctx.query_value("channel_id") or "")
        if _resolve_channel(ctx, meeting_id, channel_id, want_type="voice") is None:
            return
        _voice_presence_response(ctx, meeting_id, channel_id)

    @router.post("/api/room/voice/join")
    def room_voice_join(ctx: RequestContext) -> None:
        payload = ctx.read_json_body()
        if payload is None:
            return
        meeting_id, session = _channel_caller(
            ctx, str(payload.get("meeting_id") or payload.get("room_id") or ""), write=True
        )
        if meeting_id is None:
            return
        channel_id = str(payload.get("channel_id") or "")
        if _resolve_channel(ctx, meeting_id, channel_id, want_type="voice") is None:
            return
        participant_id, display_name = _voice_caller_identity(session, payload)
        join_voice(
            meeting_id,
            channel_id,
            participant_id,
            display_name=display_name,
            self_muted=bool(payload.get("muted", False)),
        )
        _voice_presence_response(ctx, meeting_id, channel_id)

    @router.post("/api/room/voice/leave")
    def room_voice_leave(ctx: RequestContext) -> None:
        payload = ctx.read_json_body()
        if payload is None:
            return
        meeting_id, session = _channel_caller(
            ctx, str(payload.get("meeting_id") or payload.get("room_id") or "")
        )
        if meeting_id is None:
            return
        channel_id = str(payload.get("channel_id") or "")
        participant_id, _ = _voice_caller_identity(session, payload)
        leave_voice(meeting_id, channel_id, participant_id)
        ctx.send_json(
            {"channel_id": channel_id, "participants": voice_participants(meeting_id, channel_id)}
        )


def register_moderation_media_routes(
    router: Router,
    *,
    speech_rejection_status: Callable[[str], HTTPStatus],
) -> None:
    """Preserve the historical combined moderation/media registrar."""
    register_room_member_routes(router)
    register_legacy_moderation_media_routes(
        router,
        speech_rejection_status=speech_rejection_status,
    )


__all__ = [
    "register_legacy_moderation_media_routes",
    "register_moderation_media_routes",
    "register_room_member_routes",
    "room_members_response",
]
