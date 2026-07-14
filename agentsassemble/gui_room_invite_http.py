"""Invite creation, admission, operator claim, and session lifecycle routes."""
from __future__ import annotations

from http import HTTPStatus

from agentsassemble.gui_router import RequestContext, Router
from agentsassemble.live_agents import connect_live_agent
from agentsassemble.multi_host_invites import NATIVE_REMOTE_ROOM_CLIENT_KIND
from agentsassemble.room_invite import (
    active_sessions_summary,
    create_room_invite,
    get_public_url,
    join_room_with_invite,
    pending_invites_summary,
    revoke_invite,
    revoke_session,
)
from agentsassemble.room_members import upsert_room_member
from agentsassemble.room_users import (
    grant_operator_to_device,
    operator_user_id,
    user_for_participant,
)
from agentsassemble.stable_entry import stable_entry_url


def register_invite_admission_routes(router: Router) -> None:
    """Register operator claim and room invite/admission routes."""

    @router.post("/api/host/claim")
    def host_claim(ctx: RequestContext) -> None:
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
        room_id = str(payload.get("meeting_id") or "").strip()
        try:
            room = ctx.deps.rooms.room(room_id)
        except ValueError as error:
            ctx.send_error(HTTPStatus.BAD_REQUEST, str(error))
            return
        if not room:
            ctx.send_error(HTTPStatus.NOT_FOUND, "room was not found")
            return
        try:
            client_type = str(payload.get("client_type") or "browser")
            request_session = ctx.session()
            creator_participant_id = str((request_session or {}).get("agent_id") or "")
            creator_user = user_for_participant(creator_participant_id) if creator_participant_id else None
            created_by_user_id = str((creator_user or {}).get("user_id") or operator_user_id())
            invite = create_room_invite(
                room_url=ctx.handler._local_server_url(),
                meeting_id=room_id,
                agent_id=str(payload.get("agent_id") or ""),
                display_name=str(payload.get("display_name") or ""),
                ttl_seconds=int(payload.get("ttl_seconds") or 600),
                invite_scope=str(payload.get("invite_scope") or "room"),
                permission_mode=str(payload.get("permission_mode") or ""),
                max_uses=1 if client_type == "agent_bridge" else int(payload.get("max_uses", 0)),
                participant_type="agent" if client_type == "agent_bridge" else str(payload.get("participant_type") or "human"),
                client_type=client_type,
                provider_kind=str(payload.get("provider_kind") or "manual"),
                created_by_user_id=created_by_user_id,
            )
        except (ValueError, TypeError) as error:
            ctx.send_error(HTTPStatus.BAD_REQUEST, str(error))
            return
        stable_url = stable_entry_url()
        if stable_url and invite.get("join_code"):
            invite["stable_join_url"] = f"{stable_url}/join?token={invite['join_code']}"
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
        room_id = str(result.get("meeting_id") or "")
        room = ctx.deps.rooms.room(room_id)
        if not room:
            revoke_session(str(result.get("session_token") or ""))
            ctx.send_error(HTTPStatus.GONE, "room was deleted or does not exist")
            return
        try:
            settings = ctx.deps.rooms.room_settings(room_id)
        except ValueError:
            revoke_session(str(result.get("session_token") or ""))
            ctx.send_error(HTTPStatus.INTERNAL_SERVER_ERROR, "room settings are unavailable")
            return
        result["room_label"] = str(settings.get("label") or room.get("label") or room_id)
        result["room_topic"] = str(settings.get("topic") or room.get("topic") or "")
        result["room_created_at"] = str(room.get("created_at") or "")
        participant_type = str(result.get("participant_type") or "human")
        if participant_type == "human":
            ctx.deps.rooms.upsert_participant(
                str(result["meeting_id"]),
                {
                    "participant_id": result["agent_id"],
                    "display_name": result["display_name"],
                    "participant_type": "human",
                    "role": "human",
                    "connection_kind": NATIVE_REMOTE_ROOM_CLIENT_KIND,
                    "status": "joined",
                },
            )
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
                pass
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
                pass
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
                max_uses=1,
                created_by_user_id=str((user_for_participant(str(session.get("agent_id") or "")) or {}).get("user_id") or ""),
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
