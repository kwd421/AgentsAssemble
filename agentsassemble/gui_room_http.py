"""Room domain HTTP routes: invites, guest sessions, roster, moderation.

First domain extracted from gui.py's do_GET/do_POST if-chains (R2). These are
the endpoints the identity/DB migration reworks, so they moved out of the
monolith first. Handlers receive a RequestContext (auth + body parsing) and
reach server-scoped helpers through ctx.deps.
"""
from __future__ import annotations

from http import HTTPStatus

from agentsassemble.gui_router import RequestContext, Router
from agentsassemble.live_agent_room_admin import expel_live_agent_from_room_payload
from agentsassemble.live_agents import connect_live_agent, read_live_agents
from agentsassemble.meeting_events import clean_lobby_text
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
from agentsassemble.room_users import grant_operator_to_device
from agentsassemble.room_votes import vote_summary
from agentsassemble.stable_entry import stable_entry_url


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
        if is_room_member_muted(
            ctx.deps.output_root,
            str(session.get("meeting_id") or ""),
            str(session.get("agent_id") or ""),
        ):
            ctx.send_error(HTTPStatus.FORBIDDEN, "muted by room host")
            return
        payload = ctx.read_json_body()
        if payload is None:
            return
        # Inject authenticated identity; never trust client-supplied identity
        payload["name"] = session["display_name"]
        payload["actor_id"] = session["agent_id"]
        payload["actor_type"] = (
            "human" if str(session.get("participant_type") or "human") == "human" else "agent"
        )
        payload["side"] = "other"
        # Polls ride the same channel: "vote" opens one, "vote_cast" is a ballot.
        requested_kind = str(payload.get("kind") or "")
        payload["kind"] = requested_kind if requested_kind in {"vote", "vote_cast"} else "message"
        if session.get("meeting_id"):
            payload["flow_meeting_id"] = session["meeting_id"]
        event = ctx.deps.append_lobby_event(
            ctx.deps.output_root,
            payload,
            allow_flow_metadata=ctx.deps.public_lobby_allows_room_scope(payload),
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
