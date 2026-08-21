"""HTTP routes for room friends, direct messages, and the local profile."""
from __future__ import annotations

from http import HTTPStatus

from agentsassemble.features.social.friends import (
    delete_room_friend,
    room_friends_payload,
    upsert_room_friend,
)
from agentsassemble.features.social.profile import read_user_profile, update_user_profile
from agentsassemble.web.router import RequestContext, Router


def register_room_friend_profile_routes(
    router: Router,
) -> None:
    """Attach room-friend, direct-message, and local-profile routes."""

    @router.get("/api/room-friends")
    def room_friends(ctx: RequestContext) -> None:
        ctx.send_json(
            room_friends_payload(
                ctx.deps.output_root,
                [],
            )
        )

    @router.get("/api/user-profile")
    def user_profile(ctx: RequestContext) -> None:
        user = ctx.authenticated_user()
        if user is None:
            ctx.send_error(
                HTTPStatus.UNAUTHORIZED,
                "authenticated user profile required",
            )
            return
        try:
            ctx.send_json(
                read_user_profile(
                    ctx.deps.output_root,
                    identities=ctx.deps.identities,
                    user_id=str(user.get("user_id") or ""),
                )
            )
        except ValueError as error:
            ctx.send_error(HTTPStatus.BAD_REQUEST, str(error))

    @router.post("/api/room-friends")
    def post_room_friend(ctx: RequestContext) -> None:
        payload = ctx.read_json_body()
        if payload is None:
            return
        friend = upsert_room_friend(ctx.deps.output_root, payload)
        ctx.send_json(
            {
                "friend": friend,
                **room_friends_payload(
                    ctx.deps.output_root,
                    [],
                ),
            }
        )

    @router.post("/api/user-profile")
    def post_user_profile(ctx: RequestContext) -> None:
        payload = ctx.read_json_body()
        if payload is None:
            return
        user = ctx.authenticated_user()
        if user is None:
            ctx.send_error(
                HTTPStatus.UNAUTHORIZED,
                "authenticated user profile required",
            )
            return
        try:
            ctx.send_json(
                update_user_profile(
                    ctx.deps.output_root,
                    payload,
                    identities=ctx.deps.identities,
                    rooms=ctx.deps.rooms,
                    user_id=str(user.get("user_id") or ""),
                )
            )
        except ValueError as error:
            ctx.send_error(HTTPStatus.BAD_REQUEST, str(error))

    @router.delete("/api/room-friends")
    def delete_room_friend_route(ctx: RequestContext) -> None:
        try:
            deleted = delete_room_friend(
                ctx.deps.output_root,
                ctx.query_value("friend_id"),
            )
        except ValueError as error:
            ctx.send_error(HTTPStatus.BAD_REQUEST, str(error))
            return
        ctx.send_json(
            {
                "deleted": deleted,
                **room_friends_payload(
                    ctx.deps.output_root,
                    [],
                ),
            }
        )


__all__ = ["register_room_friend_profile_routes"]
