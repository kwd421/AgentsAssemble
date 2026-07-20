"""Canonical room history, registry, message, and vote HTTP routes."""
from __future__ import annotations

from collections.abc import Callable
from http import HTTPStatus

from agentsassemble.application.agent_sessions import (
    enqueue_agent_session_auto_turn_for_lobby_event,
    room_status_payload,
)
from agentsassemble.room.moderation import is_room_member_muted
from agentsassemble.room.speech import (
    ActorIdentity,
    GovernedLobbySayRejected,
    ensure_lobby_say_allowed,
    governed_lobby_say,
)
from agentsassemble.room.votes import vote_summary
from agentsassemble.web.router import RequestContext, Router


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

    def _owner_id_for_session(
        ctx: RequestContext,
        session: dict[str, object] | None,
    ) -> str:
        if session is None:
            return ctx.deps.identities.operator_user_id()
        participant_id = str(session.get("agent_id") or "")
        user = ctx.deps.identities.user_for_participant(participant_id)
        return str((user or {}).get("user_id") or participant_id)

    def _room_payload(room: dict[str, object]) -> dict[str, object]:
        status = str(
            room.get("status") or ("archived" if room.get("archived") else "active")
        )
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
        ctx.send_json(
            {
                "events": ctx.deps.read_lobby(
                    ctx.deps.output_root,
                    meeting_id=meeting_id,
                )
            }
        )

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
            ctx.deps.output_root,
            meeting_id=str(session.get("meeting_id") or ""),
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

    def _vote_summary_response(
        ctx: RequestContext,
        meeting_id: str,
        vote_id: str,
    ) -> None:
        events = ctx.deps.read_lobby(
            ctx.deps.output_root,
            None,
            meeting_id=meeting_id,
        )
        try:
            ctx.send_json(vote_summary(events, vote_id))
        except ValueError as error:
            ctx.send_error(HTTPStatus.NOT_FOUND, str(error))

    @router.get("/api/lobby/vote")
    def lobby_vote_summary(ctx: RequestContext) -> None:
        _vote_summary_response(
            ctx,
            ctx.query_value("meeting_id"),
            ctx.query_value("vote_id"),
        )

    @router.get("/api/room/vote")
    def room_vote_summary(ctx: RequestContext) -> None:
        session = ctx.require_session()
        if session is None:
            return
        _vote_summary_response(
            ctx,
            str(session.get("meeting_id") or ""),
            ctx.query_value("vote_id"),
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
        include_archived = ctx.query_value("include_archived").lower() in {
            "1",
            "true",
            "yes",
            "on",
        }
        owner_id = "" if operator_view else _owner_id_for_session(ctx, session)
        rooms_by_id = {
            str(room.get("room_id") or ""): _room_payload(room)
            for room in ctx.deps.identities.list_rooms(
                owner_id=owner_id,
                include_archived=include_archived,
            )
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
