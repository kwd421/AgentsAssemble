"""Explicit canonical room creation for the browser room directory."""
from __future__ import annotations

from http import HTTPStatus
from uuid import uuid4

from agentsassemble.room.text import clean_room_text
from agentsassemble.web.router import RequestContext, Router


def register_room_creation_routes(router: Router) -> None:
    @router.post("/api/rooms")
    def create_room(ctx: RequestContext) -> None:
        if not ctx.is_local_operator() and not ctx.require_moderator():
            return
        payload = ctx.read_json_body()
        if payload is None:
            return
        room_id = clean_room_text(payload.get("room_id"), limit=128)
        label = clean_room_text(payload.get("label"), limit=128)
        if not room_id:
            ctx.send_error(HTTPStatus.BAD_REQUEST, "room_id is required")
            return
        try:
            result = create_canonical_room(
                ctx,
                room_id=room_id,
                label=label,
            )
        except ValueError as error:
            ctx.send_error(HTTPStatus.BAD_REQUEST, str(error))
            return
        ctx.send_json(result)


def create_canonical_room(
    ctx: RequestContext,
    *,
    room_id: str,
    label: str = "",
    owner_id: str = "",
) -> dict[str, object]:
    """Create one canonical room and update its identity-directory projection."""

    canonical_room = ctx.deps.rooms.room(room_id)
    previous_identity_room = ctx.deps.identities.get_room(room_id)
    room_uid = str(
        canonical_room.get("room_uid")
        or (previous_identity_room or {}).get("room_uid")
        or uuid4()
    )
    identity_room = ctx.deps.identities.upsert_room(
        room_id=room_id,
        room_uid=room_uid,
        owner_id=(
            owner_id
            or ctx.preference_user_id()
            or ctx.deps.identities.operator_user_id()
        ),
        label=label or room_id,
        origin="frontend_room",
    )
    try:
        room = ctx.deps.rooms.create_room(
            room_id,
            label=label or room_id,
            room_uid=room_uid,
        )
    except Exception:
        _restore_identity_room(
            ctx,
            room_id,
            previous_identity_room,
        )
        raise
    return {
        "status": "ready",
        "server_id": ctx.deps.identities.server_id(),
        "room": {
            "room_id": room_id,
            "room_uid": str(room.get("room_uid") or room_uid),
            "label": str(room.get("label") or room_id),
            "last_active_at": str(room.get("updated_at") or ""),
            "archived": False,
            "status": str(room.get("status") or "active"),
            "origin": str(identity_room.get("origin") or "frontend_room"),
        },
    }


def _restore_identity_room(
    ctx: RequestContext,
    room_id: str,
    previous: dict[str, object] | None,
) -> None:
    """Compensate the identity projection when canonical creation fails."""

    if previous is None:
        ctx.deps.identities.delete_room(room_id)
        return
    ctx.deps.identities.upsert_room(
        room_id=room_id,
        room_uid=str(previous.get("room_uid") or ""),
        owner_id=str(previous.get("owner_id") or ""),
        label=str(previous.get("label") or ""),
        origin=str(previous.get("origin") or ""),
    )
    ctx.deps.identities.set_room_archived(
        room_id,
        bool(previous.get("archived")),
    )


__all__ = ["create_canonical_room", "register_room_creation_routes"]
