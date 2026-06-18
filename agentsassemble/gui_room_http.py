"""Room domain HTTP routes: invites, guest sessions, roster, moderation.

First domain extracted from gui.py's do_GET/do_POST if-chains (R2). These are
the endpoints the identity/DB migration reworks, so they moved out of the
monolith first. Handlers receive a RequestContext (auth + body parsing) and
reach server-scoped helpers through ctx.deps.
"""
from __future__ import annotations

import threading
from http import HTTPStatus

from agentsassemble.gui_router import RequestContext, Router
from agentsassemble.live_agent_room_admin import expel_live_agent_from_room_payload
from agentsassemble.live_agents import connect_live_agent, read_live_agents
from agentsassemble.meeting_events import (
    append_lobby_event_to_file,
    clean_lobby_text,
    read_lobby_events,
    read_lobby_events_after,
)
from agentsassemble.room_channels import (
    ChannelError,
    add_channel,
    channel_stream_filename,
    find_channel,
    remove_channel,
    rename_channel,
    reorder_channels,
)
from agentsassemble.multi_host_invites import NATIVE_REMOTE_ROOM_CLIENT_KIND
from agentsassemble.room_invite import (
    active_sessions_summary,
    create_room_invite,
    get_public_url,
    join_room_with_invite,
    pending_invites_summary,
    revoke_invite,
    revoke_session,
    revoke_sessions_for_participant,
)
from agentsassemble.room_members import (
    is_room_member_muted,
    remove_room_member,
    room_members_payload,
    set_room_member_muted,
    upsert_room_member,
)
from agentsassemble.room_speech import (
    ActorIdentity,
    GovernedLobbySayRejected,
    ensure_lobby_say_allowed,
    governed_lobby_say,
)
from agentsassemble.room_settings import room_settings_payload, update_room_settings
from agentsassemble.room_users import (
    grant_operator_to_device,
    list_rooms,
    operator_user_id,
    set_room_archived,
    user_for_participant,
)
from agentsassemble.room_votes import vote_summary
from agentsassemble.stable_entry import stable_entry_url
from agentsassemble.voice_presence import (
    join_voice,
    leave_all_voice,
    leave_voice,
    voice_participants,
)

# Custom text channels each have their own channel_<id>.jsonl. A single lock
# serializes appends across them (low write rate; separate files don't race, but
# two writers to one channel would), mirroring gui's LIVE_AGENT_LOBBY_LOCK.
_CHANNEL_LOBBY_LOCK = threading.Lock()


# The local operator console (loopback) has no session; like /api/lobby it is
# trusted to name itself. A stable id keeps its voice presence + messages coherent.
_LOCAL_OPERATOR_PARTICIPANT_ID = "operator-local"
_LOCAL_OPERATOR_DISPLAY_DEFAULT = "호스트"


def _stamp_session_identity(payload: dict[str, object], session: dict[str, object]) -> None:
    """Overwrite client-supplied identity with the authenticated session's, so a
    poster can never spoof name/actor. Shared by the lobby and custom-channel say
    paths so they can't drift. Polls ride the same field: only "vote"/"vote_cast"
    survive as kinds; everything else is a plain message."""
    payload["name"] = session["display_name"]
    payload["actor_id"] = session["agent_id"]
    payload["actor_type"] = (
        "human" if str(session.get("participant_type") or "human") == "human" else "agent"
    )
    payload["side"] = "other"
    requested_kind = str(payload.get("kind") or "")
    payload["kind"] = requested_kind if requested_kind in {"vote", "vote_cast"} else "message"
    if session.get("meeting_id"):
        payload["flow_meeting_id"] = session["meeting_id"]


def _stamp_local_identity(payload: dict[str, object], meeting_id: str) -> None:
    """Stamp the local operator console's identity on a channel message. Like
    /api/lobby, the loopback caller is trusted to supply its display name; the
    actor id is fixed so the operator reads as one consistent participant."""
    payload["name"] = clean_lobby_text(payload.get("name"), limit=80) or _LOCAL_OPERATOR_DISPLAY_DEFAULT
    payload["actor_id"] = _LOCAL_OPERATOR_PARTICIPANT_ID
    payload["actor_type"] = "human"
    payload["side"] = "mine"
    requested_kind = str(payload.get("kind") or "")
    payload["kind"] = requested_kind if requested_kind in {"vote", "vote_cast"} else "message"
    if meeting_id:
        payload["flow_meeting_id"] = meeting_id


def register_room_routes(router: Router) -> None:
    """Attach the room-domain routes to the server's route table."""

    def _members_payload(ctx: RequestContext, meeting_id: str) -> dict[str, object]:
        return room_members_payload(
            ctx.deps.output_root,
            read_live_agents(ctx.deps.output_root),
            meeting_id=meeting_id,
            sessions=active_sessions_summary(),
        )

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
        return {
            "room_id": str(room.get("room_id") or ""),
            "label": str(room.get("label") or ""),
            "last_active_at": str(room.get("last_active_at") or ""),
            "archived": bool(room.get("archived")),
            "origin": str(room.get("origin") or ""),
        }

    # -- lobby history ------------------------------------------------------

    @router.get("/api/lobby")
    def lobby_history(ctx: RequestContext) -> None:
        meeting_id = ctx.query_value("meeting_id")
        before_event_id = ctx.query_value("before").strip()
        if before_event_id:
            # Scroll-up history page: events older than the client's oldest
            # visible event.
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

    # -- guest session endpoints (invited browsers / remote clients) --------

    @router.get("/api/room/events")
    def room_events_stream(ctx: RequestContext) -> None:
        session = ctx.require_session()
        if session is None:
            return
        ctx.handler._send_sse_stream(
            "lobby",
            "lobby",
            meeting_id=str(session.get("meeting_id") or ""),
            last_event_id=ctx.handler._last_event_id(ctx.query),
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
            # Incremental polling: only events after the client's cursor.
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
        event = governed_lobby_say(
            ctx.deps.output_root,
            identity=identity,
            payload=payload,
            append_lobby_event=ctx.deps.append_lobby_event,
            public_lobby_allows_room_scope=ctx.deps.public_lobby_allows_room_scope,
            is_muted=is_room_member_muted,
            policy_already_checked=True,
        )
        ctx.send_json({"event": event})

    # -- polls (/vote) ---------------------------------------------------------

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

    # -- room registry -------------------------------------------------------

    @router.get("/api/rooms")
    def rooms_list(ctx: RequestContext) -> None:
        session = ctx.session()
        operator_view = (
            ctx.handler._request_uses_loopback_host()
            or ctx.is_host()
            or ctx.is_operator_session()
        )
        if not operator_view and session is None:
            ctx.send_error(HTTPStatus.UNAUTHORIZED, "session token required")
            return
        include_archived = ctx.query_value("include_archived").lower() in {"1", "true", "yes", "on"}
        owner_id = "" if operator_view else _owner_id_for_session(session)
        ctx.send_json(
            {
                "rooms": [
                    _room_payload(room)
                    for room in list_rooms(owner_id=owner_id, include_archived=include_archived)
                ]
            }
        )

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
        updated = set_room_archived(room_id, bool(payload.get("archived")))
        if not updated:
            ctx.send_error(HTTPStatus.NOT_FOUND, "room not found")
            return
        ctx.send_json({"status": "archived" if payload.get("archived") else "active", "room_id": room_id})

    # -- roster + host moderation -------------------------------------------

    @router.get("/api/events/roster")
    def roster_events_stream(ctx: RequestContext) -> None:
        # Local console push channel for the member panel (R6): one frame on
        # connect, then only when the roster changes.
        ctx.handler._send_sse_stream(
            "roster",
            "roster",
            meeting_id=ctx.query_value("meeting_id"),
            last_event_id=None,
        )

    @router.get("/api/room-members")
    def room_members(ctx: RequestContext) -> None:
        # Local operator console reads freely; through the public entrance the
        # roster needs at least a valid guest session (or moderator credential).
        if (
            not ctx.handler._request_uses_loopback_host()
            and ctx.session() is None
            and not ctx.is_host()
        ):
            ctx.send_error(HTTPStatus.UNAUTHORIZED, "session token required")
            return
        ctx.send_json(_members_payload(ctx, ctx.query_value("meeting_id")))

    @router.post("/api/room-members")
    def room_members_upsert(ctx: RequestContext) -> None:
        payload = ctx.read_json_body()
        if payload is None:
            return
        try:
            member = upsert_room_member(ctx.deps.output_root, payload)
        except ValueError as error:
            ctx.send_error(HTTPStatus.BAD_REQUEST, str(error))
            return
        ctx.send_json(
            {
                "member": member,
                **_members_payload(ctx, str(member.get("meeting_id") or "")),
            }
        )

    @router.post("/api/room-members/mute")
    def room_members_mute(ctx: RequestContext) -> None:
        # Moderation: host token or the operator's own session (any entrance).
        if not ctx.require_moderator():
            return
        payload = ctx.read_json_body()
        if payload is None:
            return
        try:
            member = set_room_member_muted(
                ctx.deps.output_root,
                meeting_id=str(payload.get("meeting_id") or ""),
                participant_id=str(payload.get("participant_id") or ""),
                muted=bool(payload.get("muted", True)),
            )
        except ValueError as error:
            ctx.send_error(HTTPStatus.BAD_REQUEST, str(error))
            return
        ctx.send_json(
            {
                "member": member,
                **_members_payload(ctx, str(member.get("meeting_id") or "")),
            }
        )

    @router.post("/api/room-members/kick")
    def room_members_kick(ctx: RequestContext) -> None:
        # Host privilege: remove a participant from the room now. Revokes
        # their session(s), drops the saved roster row, and expels a live
        # agent if one is bound. (Kick is not a ban — open invites still let
        # them rejoin.)
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
        revoked_sessions = revoke_sessions_for_participant(kick_meeting_id, kick_participant_id)
        removed_member = remove_room_member(ctx.deps.output_root, kick_meeting_id, kick_participant_id)
        leave_all_voice(kick_meeting_id, kick_participant_id)  # drop from any voice channel too
        expelled_agent = False
        if any(
            clean_lobby_text(agent.get("agent_id"), limit=128) == clean_lobby_text(kick_participant_id, limit=128)
            and (
                not kick_meeting_id.strip()
                or clean_lobby_text(agent.get("meeting_id"), limit=128) == clean_lobby_text(kick_meeting_id, limit=128)
            )
            for agent in read_live_agents(ctx.deps.output_root)
        ):
            try:
                expel_live_agent_from_room_payload(
                    ctx.deps.output_root,
                    ctx.deps.process_supervisor,
                    {"meeting_id": kick_meeting_id, "agent_id": kick_participant_id},
                )
                expelled_agent = True
            except (OSError, ValueError):
                expelled_agent = False  # best-effort; session + roster removal already applied
        ctx.send_json(
            {
                "status": "kicked",
                "participant_id": kick_participant_id,
                "revoked_sessions": revoked_sessions,
                "removed_member": removed_member,
                "expelled_agent": expelled_agent,
                **_members_payload(ctx, kick_meeting_id),
            }
        )

    # -- custom channels (Discord-style text/voice) -------------------------

    def _channels_for(output_root, meeting_id: str) -> list[dict[str, object]]:
        payload = room_settings_payload(output_root, room_id=meeting_id)
        settings = payload.get("settings") if isinstance(payload, dict) else {}
        channels = settings.get("channels") if isinstance(settings, dict) else None
        return list(channels) if isinstance(channels, list) else []

    def _channel_error(ctx: RequestContext, error: ChannelError) -> None:
        status = {
            "not_found": HTTPStatus.NOT_FOUND,
            "duplicate": HTTPStatus.CONFLICT,
            "limit": HTTPStatus.CONFLICT,
        }.get(error.category, HTTPStatus.BAD_REQUEST)
        ctx.send_error(status, str(error))

    @router.get("/api/room-channels")
    def room_channels_list(ctx: RequestContext) -> None:
        # Readable like the roster: local console freely, public entrance needs a
        # valid session or the host credential.
        if (
            not ctx.handler._request_uses_loopback_host()
            and ctx.session() is None
            and not ctx.is_host()
        ):
            ctx.send_error(HTTPStatus.UNAUTHORIZED, "session token required")
            return
        meeting_id = ctx.query_value("meeting_id") or ctx.query_value("room_id")
        ctx.send_json({"room_id": meeting_id, "channels": _channels_for(ctx.deps.output_root, meeting_id)})

    @router.post("/api/room-channels")
    def room_channels_mutate(ctx: RequestContext) -> None:
        # Channel create/rename/delete/reorder is a room-shape change: host token
        # or operator (director) session only — same gate as mute/kick.
        if not ctx.require_moderator():
            return
        payload = ctx.read_json_body()
        if payload is None:
            return
        meeting_id = str(payload.get("meeting_id") or payload.get("room_id") or "")
        if not meeting_id.strip():
            ctx.send_error(HTTPStatus.BAD_REQUEST, "meeting_id is required")
            return
        action = str(payload.get("action") or "").strip().lower()
        output_root = ctx.deps.output_root
        current = _channels_for(output_root, meeting_id)
        created: dict[str, object] | None = None
        try:
            if action == "create":
                current, created = add_channel(
                    current,
                    name=payload.get("name"),
                    channel_type=payload.get("type") or payload.get("channel_type") or "text",
                )
            elif action == "rename":
                current = rename_channel(current, str(payload.get("channel_id") or ""), payload.get("name"))
            elif action in {"delete", "remove"}:
                current = remove_channel(current, str(payload.get("channel_id") or ""))
            elif action == "reorder":
                ordered = payload.get("ordered_ids") or payload.get("orderedIds") or []
                current = reorder_channels(current, [str(item) for item in ordered] if isinstance(ordered, list) else [])
            else:
                ctx.send_error(HTTPStatus.BAD_REQUEST, "unknown channel action")
                return
        except ChannelError as error:
            _channel_error(ctx, error)
            return
        saved = update_room_settings(output_root, {"room_id": meeting_id, "channels": current})
        result_settings = saved.get("settings") if isinstance(saved, dict) else {}
        channels = result_settings.get("channels") if isinstance(result_settings, dict) else None
        response: dict[str, object] = {
            "room_id": meeting_id,
            "channels": list(channels) if isinstance(channels, list) else [],
        }
        if created is not None:
            response["channel"] = created
        ctx.send_json(response)

    # -- custom text/voice channels (dual-mode auth) ------------------------
    #
    # The local operator console (loopback, no session) and admitted guests
    # (session token) share these routes — mirroring the /api/lobby vs
    # /api/room/* split, collapsed into one family. A guest is stamped with its
    # admitted identity; a loopback/host caller (the operator's own machine) is
    # trusted to supply its display name, exactly as /api/lobby already is.

    def _channel_caller(ctx: RequestContext, payload_meeting_id: str = "", *, write: bool = False):
        """Resolve (meeting_id, session_or_None) for a channel request, or send
        the right error and return (None, None)."""
        session = ctx.session()
        if session is not None:
            if write and session.get("invite_scope") == "read_only":
                ctx.send_error(HTTPStatus.FORBIDDEN, "read-only invite session cannot post")
                return None, None
            return str(session.get("meeting_id") or ""), session
        if ctx.handler._request_uses_loopback_host() or ctx.is_host():
            return str(payload_meeting_id or ""), None
        ctx.send_error(HTTPStatus.UNAUTHORIZED, "session token required")
        return None, None

    def _resolve_channel(ctx: RequestContext, meeting_id: str, channel_id: str, *, want_type: str):
        channel = find_channel(_channels_for(ctx.deps.output_root, meeting_id), channel_id)
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
            clean_lobby_text(payload.get("name"), limit=80) or _LOCAL_OPERATOR_DISPLAY_DEFAULT,
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
        if session is not None and is_room_member_muted(
            ctx.deps.output_root, meeting_id, str(session.get("agent_id") or "")
        ):
            ctx.send_error(HTTPStatus.FORBIDDEN, "muted by room host")
            return
        channel_id = str(payload.get("channel_id") or "")
        if _resolve_channel(ctx, meeting_id, channel_id, want_type="text") is None:
            return
        filename = channel_stream_filename(channel_id)
        if not filename:
            ctx.send_error(HTTPStatus.BAD_REQUEST, "invalid channel id")
            return
        if session is not None:
            _stamp_session_identity(payload, session)
        else:
            _stamp_local_identity(payload, meeting_id)
        path = ctx.deps.output_root / filename
        with _CHANNEL_LOBBY_LOCK:
            event = append_lobby_event_to_file(path, payload, allow_flow_metadata=True)
        ctx.send_json({"event": event, "channel_id": channel_id})

    # -- voice channels (presence only; audio streaming deferred) -----------

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
        # A heartbeat too: re-posting join refreshes presence so a live client
        # stays in the voice roster and a dropped one falls out after the TTL.
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
            meeting_id, channel_id, participant_id,
            display_name=display_name, self_muted=bool(payload.get("muted", False)),
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

    # -- operator account ------------------------------------------------------

    @router.post("/api/host/claim")
    def host_claim(ctx: RequestContext) -> None:
        # Host-token gated: binds this browser/device's stable identity to the
        # operator account, so its sessions moderate from any entrance without
        # carrying the raw host token around.
        if not ctx.require_host():
            return
        payload = ctx.read_json_body()
        if payload is None:
            return
        user = grant_operator_to_device(
            str(payload.get("device_token") or ""),
            display_name=str(payload.get("display_name") or ""),
        )
        if user is None:
            ctx.send_error(HTTPStatus.BAD_REQUEST, "device_token is required (8+ chars)")
            return
        ctx.send_json(
            {
                "status": "claimed",
                "user_id": user["user_id"],
                "participant_id": user["participant_id"],
                "operator": bool(user.get("is_operator")),
            }
        )

    # -- invite lifecycle ----------------------------------------------------

    @router.get("/api/room-invite/sessions")
    def room_invite_sessions(ctx: RequestContext) -> None:
        if not ctx.require_moderator():
            return
        ctx.send_json({"sessions": active_sessions_summary()})

    @router.get("/api/room-invite/invites")
    def room_invite_invites(ctx: RequestContext) -> None:
        if not ctx.require_moderator():
            return
        ctx.send_json({"invites": pending_invites_summary()})

    @router.post("/api/room-invite/create")
    def room_invite_create(ctx: RequestContext) -> None:
        # Moderator gate: the host (token) or the operator's session.
        if not ctx.require_moderator():
            return
        payload = ctx.read_json_body()
        if payload is None:
            return
        allow_local_dev_invite = payload.get("local_dev_preview") is True or payload.get("allow_local_dev") is True
        if not get_public_url() and not allow_local_dev_invite:
            ctx.send_error(
                HTTPStatus.CONFLICT,
                "public URL is required before creating an external guest invite",
            )
            return
        try:
            invite = create_room_invite(
                room_url=ctx.handler._local_server_url(),
                meeting_id=str(payload.get("meeting_id") or ""),
                agent_id=str(payload.get("agent_id") or ""),
                display_name=str(payload.get("display_name") or ""),
                ttl_seconds=int(payload.get("ttl_seconds") or 600),
                invite_scope=str(payload.get("invite_scope") or "room"),
                permission_mode=str(payload.get("permission_mode") or ""),
                max_uses=int(payload.get("max_uses", 0)),
            )
        except (ValueError, TypeError) as error:
            ctx.send_error(HTTPStatus.BAD_REQUEST, str(error))
            return
        stable_url = stable_entry_url()
        if stable_url and invite.get("invite_token"):
            # Permanent alias that survives tunnel rotation — share this one.
            invite["stable_join_url"] = f"{stable_url}/join?token={invite['invite_token']}"
        ctx.send_json(invite)

    @router.post("/api/room-invite/join")
    def room_invite_join(ctx: RequestContext) -> None:
        payload = ctx.read_json_body()
        if payload is None:
            return
        token = str(payload.get("invite_token") or "").strip()
        if not token:
            ctx.send_error(HTTPStatus.BAD_REQUEST, "invite_token is required")
            return
        result = join_room_with_invite(
            token,
            meeting_id=str(payload.get("meeting_id") or ""),
            display_name=str(payload.get("display_name") or ""),
            device_token=str(payload.get("device_token") or ""),
            participant_type=str(payload.get("participant_type") or ""),
            owner_display_name=str(payload.get("owner_display_name") or ""),
        )
        if result.get("status") != "admitted":
            ctx.send_error(HTTPStatus.FORBIDDEN, str(result.get("reason", "rejected")))
            return
        participant_type = str(result.get("participant_type") or "human")
        if participant_type == "human":
            try:
                upsert_room_member(
                    ctx.deps.output_root,
                    {
                        "participant_id": result["agent_id"],
                        "display_name": result["display_name"],
                        "meeting_id": result["meeting_id"],
                        "role": "human",
                        "participant_type": "human",
                        "connection_kind": NATIVE_REMOTE_ROOM_CLIENT_KIND,
                        "status": "online",
                        "source": "room_invite",
                    },
                )
            except ValueError:
                pass  # non-fatal: room access is still governed by the session token
        else:
            try:
                connect_live_agent(
                    ctx.deps.output_root,
                    {
                        "agent_id": result["agent_id"],
                        "display_name": result["display_name"],
                        "provider_kind": "manual",
                        "connection_kind": NATIVE_REMOTE_ROOM_CLIENT_KIND,
                        "meeting_id": result["meeting_id"],
                        "status": "online",
                        "owner_display_name": str(result.get("owner_display_name") or ""),
                    },
                )
            except ValueError:
                pass  # non-fatal: roster update best-effort
        ctx.send_json(result)

    @router.post("/api/room-invite/companion")
    def room_invite_companion(ctx: RequestContext) -> None:
        session = ctx.require_posting_session("create companion invites")
        if session is None:
            return
        payload = ctx.read_json_body()
        if payload is None:
            return
        try:
            invite = create_room_invite(
                room_url=ctx.handler._local_server_url(),
                meeting_id=str(session.get("meeting_id") or ""),
                agent_id=str(payload.get("agent_id") or ""),
                display_name=str(payload.get("display_name") or ""),
                ttl_seconds=min(int(payload.get("ttl_seconds") or 600), 3600),
                invite_scope="room",
                participant_type="remote",
                max_uses=1,  # companion packets hand off one running AI; keep its identity stable
            )
        except (ValueError, TypeError) as error:
            ctx.send_error(HTTPStatus.BAD_REQUEST, str(error))
            return
        ctx.send_json(invite)

    @router.post("/api/room-invite/leave")
    def room_invite_leave(ctx: RequestContext) -> None:
        payload = ctx.read_json_body()
        if payload is None:
            return
        session = ctx.require_session()
        if session is None:
            return
        # Mark agent offline in roster
        try:
            connect_live_agent(
                ctx.deps.output_root,
                {"agent_id": session["agent_id"], "status": "offline"},
            )
        except ValueError:
            pass
        revoke_session(ctx.bearer_token())
        ctx.send_json({"status": "left", "agent_id": session["agent_id"]})

    @router.post("/api/room-invite/revoke")
    def room_invite_revoke(ctx: RequestContext) -> None:
        if not ctx.require_moderator():
            return
        payload = ctx.read_json_body()
        if payload is None:
            return
        invite_id = str(payload.get("invite_id") or "").strip()
        session_token_to_revoke = str(payload.get("session_token") or "").strip()
        if invite_id:
            if revoke_invite(invite_id):
                ctx.send_json({"status": "revoked", "invite_id": invite_id})
            else:
                ctx.send_error(HTTPStatus.NOT_FOUND, "invite not found")
        elif session_token_to_revoke:
            if revoke_session(session_token_to_revoke):
                ctx.send_json({"status": "revoked"})
            else:
                ctx.send_error(HTTPStatus.NOT_FOUND, "session not found")
        else:
            ctx.send_error(HTTPStatus.BAD_REQUEST, "invite_id or session_token required")
