"""Invite creation, admission, operator claim, and session lifecycle routes."""
from __future__ import annotations

from http import HTTPStatus
from uuid import UUID

from agentsassemble.web.router import RequestContext, Router
from agentsassemble.identity.repository import device_auth_key
from agentsassemble.admission.projection import LegacyAdmissionParticipant
from agentsassemble.admission.lan_invite import NATIVE_REMOTE_ROOM_CLIENT_KIND
from agentsassemble.identity.pairing import normalize_pairing_origin
from agentsassemble.admission.coordinator import AdmissionIdempotencyConflict
from agentsassemble.application.stable_entry import stable_entry_url


def register_invite_admission_routes(router: Router) -> None:
    """Register operator claim and room invite/admission routes."""

    @router.post("/api/host/claim")
    def host_claim(ctx: RequestContext) -> None:
        if not ctx.require_host():
            return
        payload = ctx.read_json_body()
        if payload is None:
            return
        auth_key = device_auth_key(str(payload.get("device_token") or ""))
        user = ctx.deps.identities.claim_local_operator_credential(
            auth_key,
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
        ctx.send_json({"sessions": ctx.deps.sessions.active_summary()})

    @router.get("/api/room-invite/invites")
    def room_invite_invites(ctx: RequestContext) -> None:
        if not ctx.require_moderator():
            return
        ctx.send_json({"invites": ctx.deps.invites.pending()})

    @router.post("/api/room-invite/create")
    def room_invite_create(ctx: RequestContext) -> None:
        if not ctx.require_moderator():
            return
        payload = ctx.read_json_body()
        if payload is None:
            return
        allow_local_dev_invite = payload.get("local_dev_preview") is True or payload.get("allow_local_dev") is True
        if not ctx.deps.invites.public_url() and not allow_local_dev_invite:
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
            creator_user = (
                ctx.deps.identities.user_for_participant(creator_participant_id)
                if creator_participant_id
                else None
            )
            created_by_user_id = str(
                (creator_user or {}).get("user_id")
                or ctx.deps.identities.operator_user_id()
            )
            invite = ctx.deps.invites.create(
                room_url=ctx.local_server_url(),
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
        raw_request_id = str(payload.get("request_id") or "").strip()
        if not raw_request_id:
            ctx.send_error(
                HTTPStatus.BAD_REQUEST,
                "request_id is required",
                code="request_id_required",
            )
            return
        try:
            parsed_request_id = UUID(raw_request_id)
            request_id = str(parsed_request_id)
        except (TypeError, ValueError, AttributeError):
            ctx.send_error(
                HTTPStatus.BAD_REQUEST,
                "request_id must be a UUID",
                code="request_id_invalid",
            )
            return
        if raw_request_id != request_id or parsed_request_id.int == 0:
            ctx.send_error(
                HTTPStatus.BAD_REQUEST,
                "request_id must be a canonical non-zero UUID",
                code="request_id_invalid",
            )
            return
        try:
            result = ctx.deps.admission.admit(
                invite_token=token,
                request_id=request_id,
                meeting_id=str(payload.get("meeting_id") or ""),
                display_name=str(payload.get("display_name") or ""),
                device_token=str(payload.get("device_token") or ""),
                participant_type=str(payload.get("participant_type") or ""),
                owner_display_name=str(payload.get("owner_display_name") or ""),
            )
        except AdmissionIdempotencyConflict as error:
            ctx.send_error(
                HTTPStatus.CONFLICT,
                str(error),
                code="idempotency_conflict",
            )
            return
        if result.get("status") != "admitted":
            reason = str(result.get("reason", "rejected"))
            if reason == "room_unavailable":
                ctx.send_error(HTTPStatus.GONE, "room was deleted or does not exist")
                return
            ctx.send_error(HTTPStatus.FORBIDDEN, reason)
            return
        participant_type = str(result.get("participant_type") or "human")
        if participant_type != "human":
            ctx.deps.admission_projection.participant_joined(
                LegacyAdmissionParticipant(
                    participant_id=str(result["agent_id"]),
                    display_name=str(result["display_name"]),
                    provider_kind=str(result.get("provider_kind") or "manual"),
                    connection_kind=str(
                        result.get("connection_kind") or NATIVE_REMOTE_ROOM_CLIENT_KIND
                    ),
                    room_id=str(result["meeting_id"]),
                    owner_display_name=str(result.get("owner_display_name") or ""),
                )
            )
        ctx.send_json(result)

    @router.post("/api/room-invite/admission")
    def room_invite_admission(ctx: RequestContext) -> None:
        payload = ctx.read_json_body()
        if payload is None:
            return
        token = str(payload.get("invite_token") or "").strip()
        if not token:
            ctx.send_error(HTTPStatus.BAD_REQUEST, "invite_token is required")
            return
        decision = ctx.deps.admission_preflight.resolve(
            invite_token=token,
            device_token=str(ctx.headers.get("X-Device-Token") or ""),
            session=ctx.session(),
        )
        ctx.send_json(decision)

    @router.post("/api/operator-pairing/create")
    def operator_pairing_create(ctx: RequestContext) -> None:
        if not ctx.require_moderator():
            return
        payload = ctx.read_json_body()
        if payload is None:
            return
        public_url = ctx.deps.invites.public_url()
        if not public_url:
            ctx.send_error(HTTPStatus.CONFLICT, "public URL is required before pairing")
            return
        try:
            result = ctx.deps.pairing.create(
                room_id=str(payload.get("meeting_id") or ""),
                public_url=public_url,
                ttl_seconds=int(payload.get("ttl_seconds") or 120),
            )
        except (TypeError, ValueError) as error:
            ctx.send_error(HTTPStatus.BAD_REQUEST, str(error))
            return
        ctx.send_json(result)

    @router.post("/api/operator-pairing/redeem")
    def operator_pairing_redeem(ctx: RequestContext) -> None:
        payload = ctx.read_json_body()
        if payload is None:
            return
        header_origin = str(ctx.headers.get("Origin") or "").strip()
        if not header_origin:
            ctx.send_error(HTTPStatus.FORBIDDEN, "pairing_origin_required")
            return
        try:
            request_origin = normalize_pairing_origin(header_origin)
        except ValueError:
            ctx.send_error(HTTPStatus.FORBIDDEN, "pairing_origin_invalid")
            return
        result = ctx.deps.pairing.redeem(
            pairing_token=str(payload.get("pairing_token") or ""),
            device_token=str(ctx.headers.get("X-Device-Token") or ""),
            request_origin=request_origin,
        )
        if result.get("status") != "admitted":
            ctx.send_error(HTTPStatus.FORBIDDEN, str(result.get("reason") or "pairing_rejected"))
            return
        ctx.send_json(result)

    @router.post("/api/operator-pairing/revoke")
    def operator_pairing_revoke(ctx: RequestContext) -> None:
        if not ctx.require_moderator():
            return
        payload = ctx.read_json_body()
        if payload is None:
            return
        pairing_id = str(payload.get("pairing_id") or "").strip()
        if not pairing_id:
            ctx.send_error(HTTPStatus.BAD_REQUEST, "pairing_id is required")
            return
        revoked = ctx.deps.pairing.revoke(pairing_id)
        if not revoked:
            ctx.send_error(HTTPStatus.NOT_FOUND, "active pairing was not found")
            return
        ctx.send_json({"status": "revoked", "pairing_id": pairing_id})

    @router.post("/api/room-invite/companion")
    def room_invite_companion(ctx: RequestContext) -> None:
        session = ctx.require_posting_session("create companion invites")
        if session is None:
            return
        payload = ctx.read_json_body()
        if payload is None:
            return
        try:
            invite = ctx.deps.invites.create(
                room_url=ctx.local_server_url(),
                meeting_id=str(session.get("meeting_id") or ""),
                agent_id=str(payload.get("agent_id") or ""),
                display_name=str(payload.get("display_name") or ""),
                ttl_seconds=min(int(payload.get("ttl_seconds") or 600), 3600),
                invite_scope="room",
                participant_type="remote",
                max_uses=1,
                created_by_user_id=str(
                    (
                        ctx.deps.identities.user_for_participant(
                            str(session.get("agent_id") or "")
                        )
                        or {}
                    ).get("user_id")
                    or ""
                ),
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
        if str(session.get("participant_type") or "human") != "human":
            ctx.deps.admission_projection.participant_left(str(session["agent_id"]))
        ctx.deps.sessions.revoke(ctx.bearer_token())
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
            if ctx.deps.invites.revoke(invite_id):
                ctx.send_json({"status": "revoked", "invite_id": invite_id})
            else:
                ctx.send_error(HTTPStatus.NOT_FOUND, "invite not found")
        elif session_token_to_revoke:
            if ctx.deps.sessions.revoke(session_token_to_revoke):
                ctx.send_json({"status": "revoked"})
            else:
                ctx.send_error(HTTPStatus.NOT_FOUND, "session not found")
        else:
            ctx.send_error(HTTPStatus.BAD_REQUEST, "invite_id or session_token required")
