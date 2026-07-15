"""Canonical room history, registry, and lifecycle HTTP routes."""
from __future__ import annotations

from collections.abc import Callable
from http import HTTPStatus

from agentsassemble.agent_sessions import (
    enqueue_agent_session_auto_turn_for_lobby_event,
    room_action_payload,
    room_lifecycle_payload,
    room_status_payload,
)
from agentsassemble.gui_router import RequestContext, Router
from agentsassemble.live_agent_frontend_create import ensure_frontend_meeting
from agentsassemble.meeting_events import clean_lobby_text
from agentsassemble.room_speech import (
    ActorIdentity,
    GovernedLobbySayRejected,
    ensure_lobby_say_allowed,
    governed_lobby_say,
)
from agentsassemble.room_users import (
    list_rooms,
    operator_user_id,
    set_room_archived,
    user_for_participant,
)
from agentsassemble.room_votes import vote_summary
from agentsassemble.room_members import is_room_member_muted


def register_room_history_routes(
    router: Router,
    *,
    agent_session_control_allowed: Callable[[RequestContext], bool],
    agent_turn_adapter: Callable[..., object],
    speech_rejection_status: Callable[[str], HTTPStatus],
) -> None:
    """Register lobby history, room messages, votes, and room registry routes."""

    def _session_summary(session: dict[str, object]) -> dict[str, object]:
        return {
            "agent_id": session["agent_id"],
            "display_name": session["display_name"],
            "invite_scope": session.get("invite_scope", "room"),
        }

    def _owner_id_for_session(session: dict[str, object] | None) -> str:
        if session is None:
            return operator_user_id()
        participant_id = str(session.get("agent_id") or "")
        user = user_for_participant(participant_id)
        return str((user or {}).get("user_id") or participant_id)

    def _room_payload(room: dict[str, object]) -> dict[str, object]:
        status = str(room.get("status") or ("archived" if room.get("archived") else "active"))
        return {
            "room_id": str(room.get("room_id") or ""),
            "label": str(room.get("label") or ""),
            "last_active_at": str(room.get("last_active_at") or ""),
            "archived": bool(room.get("archived")) or status == "archived",
            "status": status,
            "origin": str(room.get("origin") or ""),
        }

    @router.get("/api/lobby")
    def lobby_history(ctx: RequestContext) -> None:
        meeting_id = ctx.query_value("meeting_id")
        before_event_id = ctx.query_value("before").strip()
        if before_event_id:
            ctx.send_json(
                ctx.deps.read_lobby_before(
                    ctx.deps.output_root,
                    before_event_id=before_event_id,
                    limit=ctx.deps.history_page_limit(ctx.query),
                    meeting_id=meeting_id,
                )
            )
            return
        ctx.send_json({"events": ctx.deps.read_lobby(ctx.deps.output_root, meeting_id=meeting_id)})

    @router.get("/api/room/events")
    def room_events_stream(ctx: RequestContext) -> None:
        session = ctx.require_session()
        if session is None:
            return
        ctx.send_sse_stream(
            "lobby",
            "lobby",
            meeting_id=str(session.get("meeting_id") or ""),
            last_event_id=ctx.last_event_id(),
        )

    @router.get("/api/room-events/stream")
    def canonical_room_events_stream(ctx: RequestContext) -> None:
        room_id = ctx.query_value("room_id") or ctx.query_value("meeting_id")
        if not room_id:
            ctx.send_error(HTTPStatus.BAD_REQUEST, "room_id is required")
            return
        if ctx.send_room_events_sse_stream(
            room_id=room_id,
            cursor=ctx.query_value("cursor") or ctx.last_event_id(),
        ):
            return
        ctx.send_json(
            room_status_payload(
                ctx.deps.output_root,
                room_id,
                repository=ctx.deps.rooms,
            )
        )

    @router.get("/api/room/lobby")
    def room_lobby(ctx: RequestContext) -> None:
        session = ctx.require_session()
        if session is None:
            return
        before_event_id = ctx.query_value("before").strip()
        if before_event_id:
            page = ctx.deps.read_lobby_before(
                ctx.deps.output_root,
                before_event_id=before_event_id,
                limit=ctx.deps.history_page_limit(ctx.query),
                meeting_id=str(session.get("meeting_id") or ""),
            )
            page["session"] = _session_summary(session)
            ctx.send_json(page)
            return
        room_events = ctx.deps.read_lobby(
            ctx.deps.output_root, meeting_id=str(session.get("meeting_id") or "")
        )
        after_event_id = ctx.query_value("after").strip()
        if after_event_id:
            for index, event in enumerate(room_events):
                if str(event.get("id") or "") == after_event_id:
                    room_events = room_events[index + 1 :]
                    break
        ctx.send_json({"events": room_events, "session": _session_summary(session)})

    @router.post("/api/room/say")
    def room_say(ctx: RequestContext) -> None:
        session = ctx.require_posting_session()
        if session is None:
            return
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
        payload = ctx.read_json_body()
        if payload is None:
            return
        try:
            event = governed_lobby_say(
                ctx.deps.output_root,
                identity=identity,
                payload=payload,
                append_lobby_event=ctx.deps.append_lobby_event,
                public_lobby_allows_room_scope=ctx.deps.public_lobby_allows_room_scope,
                is_muted=is_room_member_muted,
                policy_already_checked=True,
            )
        except GovernedLobbySayRejected as rejected:
            ctx.send_error(speech_rejection_status(rejected.category), str(rejected))
            return
        if agent_session_control_allowed(ctx):
            enqueue_agent_session_auto_turn_for_lobby_event(
                ctx.deps.output_root,
                event,
                turn_adapter=agent_turn_adapter,
                repository=ctx.deps.rooms,
            )
        ctx.send_json({"event": event})

    def _vote_summary_response(ctx: RequestContext, meeting_id: str, vote_id: str) -> None:
        events = ctx.deps.read_lobby(ctx.deps.output_root, None, meeting_id=meeting_id)
        try:
            ctx.send_json(vote_summary(events, vote_id))
        except ValueError as error:
            ctx.send_error(HTTPStatus.NOT_FOUND, str(error))

    @router.get("/api/lobby/vote")
    def lobby_vote_summary(ctx: RequestContext) -> None:
        _vote_summary_response(ctx, ctx.query_value("meeting_id"), ctx.query_value("vote_id"))

    @router.get("/api/room/vote")
    def room_vote_summary(ctx: RequestContext) -> None:
        session = ctx.require_session()
        if session is None:
            return
        _vote_summary_response(
            ctx, str(session.get("meeting_id") or ""), ctx.query_value("vote_id")
        )

    @router.get("/api/rooms")
    def rooms_list(ctx: RequestContext) -> None:
        session = ctx.session()
        operator_view = (
            ctx.uses_loopback_host()
            or ctx.is_host()
            or ctx.is_operator_session()
        )
        if not operator_view and session is None:
            ctx.send_error(HTTPStatus.UNAUTHORIZED, "session token required")
            return
        include_archived = ctx.query_value("include_archived").lower() in {"1", "true", "yes", "on"}
        owner_id = "" if operator_view else _owner_id_for_session(session)
        rooms_by_id = {
            str(room.get("room_id") or ""): _room_payload(room)
            for room in list_rooms(owner_id=owner_id, include_archived=include_archived)
        }
        for room in ctx.deps.rooms.list_rooms(include_archived=include_archived):
            room_id = str(room.get("room_id") or "")
            if not room_id:
                continue
            rooms_by_id[room_id] = {
                **rooms_by_id.get(room_id, {}),
                **_room_payload(
                    {
                        "room_id": room_id,
                        "label": room.get("label", ""),
                        "last_active_at": room.get("updated_at", ""),
                        "status": room.get("status", "active"),
                        "origin": "agent_session",
                    }
                ),
            }
        ctx.send_json({"rooms": list(rooms_by_id.values())})

    @router.get("/api/rooms/state")
    def room_state(ctx: RequestContext) -> None:
        room_id = ctx.query_value("room_id") or ctx.query_value("meeting_id")
        if not room_id:
            ctx.send_error(HTTPStatus.BAD_REQUEST, "room_id is required")
            return
        try:
            ctx.send_json(
                room_status_payload(
                    ctx.deps.output_root,
                    room_id,
                    repository=ctx.deps.rooms,
                )
            )
        except ValueError as error:
            ctx.send_error(HTTPStatus.BAD_REQUEST, str(error))


def register_room_lifecycle_routes(router: Router) -> None:
    """Register participant leave/kick/export and room close/archive routes."""

    def _room_owner_id(ctx: RequestContext) -> str:
        session = ctx.session()
        if session is not None:
            participant_id = str(session.get("agent_id") or "")
            user = user_for_participant(participant_id)
            return str((user or {}).get("user_id") or participant_id)
        if ctx.uses_loopback_host() or ctx.is_host():
            return operator_user_id()
        return ""

    def _loopback_or_moderator(ctx: RequestContext) -> bool:
        if ctx.uses_loopback_host():
            return True
        return ctx.require_moderator()

    @router.post("/api/room/ensure")
    def room_ensure(ctx: RequestContext) -> None:
        payload = ctx.read_json_body()
        if payload is None:
            return
        try:
            meeting_dir = ensure_frontend_meeting(
                ctx.deps.output_root,
                clean_lobby_text(payload.get("meeting_id"), limit=128),
                label=clean_lobby_text(payload.get("label"), limit=128),
                owner_id=_room_owner_id(ctx),
            )
        except (OSError, ValueError) as error:
            ctx.send_error(HTTPStatus.BAD_REQUEST, str(error))
            return
        ctx.send_json({"status": "ready", "meeting_id": meeting_dir.name})

    def _leave_allowed(ctx: RequestContext, payload: dict[str, object]) -> bool:
        if ctx.uses_loopback_host() or ctx.is_host() or ctx.is_operator_session():
            return True
        session = ctx.session()
        if not session:
            return False
        requested = clean_lobby_text(payload.get("participant_id") or payload.get("agent_id"), limit=128)
        return requested and requested == clean_lobby_text(session.get("agent_id"), limit=128)

    def _participant_action(ctx: RequestContext, action: str) -> None:
        payload = ctx.read_json_body()
        if payload is None:
            return
        if action in {"kick", "export"} and not _loopback_or_moderator(ctx):
            return
        if action == "leave" and not _leave_allowed(ctx, payload):
            ctx.send_error(HTTPStatus.FORBIDDEN, "participant session token required")
            return
        try:
            ctx.send_json(
                room_action_payload(
                    ctx.deps.output_root,
                    payload,
                    action,
                    repository=ctx.deps.rooms,
                )
            )
        except ValueError as error:
            ctx.send_error(HTTPStatus.BAD_REQUEST, str(error))

    @router.post("/api/room-participants/leave")
    def room_participants_leave(ctx: RequestContext) -> None:
        _participant_action(ctx, "leave")

    @router.post("/api/room-participants/kick")
    def room_participants_kick(ctx: RequestContext) -> None:
        _participant_action(ctx, "kick")

    @router.post("/api/room-participants/export")
    def room_participants_export(ctx: RequestContext) -> None:
        _participant_action(ctx, "export")

    def _room_lifecycle_action(ctx: RequestContext, action: str) -> None:
        if not _loopback_or_moderator(ctx):
            return
        payload = ctx.read_json_body()
        if payload is None:
            return
        try:
            ctx.send_json(
                room_lifecycle_payload(
                    ctx.deps.output_root,
                    payload,
                    action,
                    repository=ctx.deps.rooms,
                )
            )
        except ValueError as error:
            ctx.send_error(HTTPStatus.BAD_REQUEST, str(error))

    @router.post("/api/rooms/close")
    def rooms_close(ctx: RequestContext) -> None:
        _room_lifecycle_action(ctx, "close")

    @router.post("/api/rooms/archive")
    def rooms_archive(ctx: RequestContext) -> None:
        if not ctx.require_moderator():
            return
        payload = ctx.read_json_body()
        if payload is None:
            return
        room_id = clean_lobby_text(payload.get("room_id"), limit=128)
        if not room_id:
            ctx.send_error(HTTPStatus.BAD_REQUEST, "room_id is required")
            return
        archived = bool(payload.get("archived"))
        updated = set_room_archived(room_id, archived)
        store_updated = False
        try:
            if ctx.deps.rooms.room(room_id):
                ctx.deps.rooms.set_room_status(room_id, "archived" if archived else "active")
                store_updated = True
        except ValueError:
            store_updated = False
        if not updated and not store_updated:
            ctx.send_error(HTTPStatus.NOT_FOUND, "room not found")
            return
        ctx.send_json({"status": "archived" if archived else "active", "room_id": room_id})
