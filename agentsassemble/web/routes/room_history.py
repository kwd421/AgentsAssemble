"""Canonical room history, registry, message, and vote HTTP routes."""
from __future__ import annotations

from http import HTTPStatus

from agentsassemble.application.agent_sessions import room_status_payload
from agentsassemble.room.votes import legacy_vote_summary
from agentsassemble.web.router import RequestContext, Router
from agentsassemble.room.global_settings import public_room_global_settings


def register_room_history_routes(router: Router) -> None:
    """Register lobby history, room messages, votes, and room registry routes."""

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
            "room_uid": str(room.get("room_uid") or ""),
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

    @router.get("/api/room-events/stream")
    def canonical_room_events_stream(ctx: RequestContext) -> None:
        room_id = ctx.query_value("room_id") or ctx.query_value("meeting_id")
        if not room_id:
            ctx.send_error(HTTPStatus.BAD_REQUEST, "room_id is required")
            return
        if not ctx.require_room_access(room_id):
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
            ctx.send_json(legacy_vote_summary(events, vote_id))
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
        session_room_id = (
            ""
            if operator_view or session is None
            else str(session.get("meeting_id") or "").strip()
        )
        rooms_by_id = {
            str(room.get("room_id") or ""): _room_payload(room)
            for room in ctx.deps.identities.list_rooms(
                owner_id=owner_id,
                include_archived=include_archived,
            )
            if not session_room_id
            or str(room.get("room_id") or "") == session_room_id
        }
        for room in ctx.deps.rooms.list_rooms(include_archived=include_archived):
            room_id = str(room.get("room_id") or "")
            if not room_id or (
                session_room_id and room_id != session_room_id
            ):
                continue
            room_settings = public_room_global_settings(
                ctx.deps.rooms.room_settings(room_id)
            )
            rooms_by_id[room_id] = {
                **rooms_by_id.get(room_id, {}),
                **_room_payload(
                    {
                        "room_id": room_id,
                        "room_uid": room.get("room_uid", ""),
                        "label": room_settings["label"],
                        "last_active_at": room.get("updated_at", ""),
                        "status": room.get("status", "active"),
                        "origin": "agent_session",
                    }
                ),
                "room_settings": {
                    "room_id": room_id,
                    **room_settings,
                },
            }
        ctx.send_json(
            {
                "server_id": ctx.deps.identities.server_id(),
                "rooms": list(rooms_by_id.values()),
            }
        )

    @router.get("/api/rooms/state")
    def room_state(ctx: RequestContext) -> None:
        room_id = ctx.query_value("room_id") or ctx.query_value("meeting_id")
        if not room_id:
            ctx.send_error(HTTPStatus.BAD_REQUEST, "room_id is required")
            return
        if not ctx.require_room_access(room_id):
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
