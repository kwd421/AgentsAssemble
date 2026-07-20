"""HTTP routes for room friends, direct messages, and the local profile."""
from __future__ import annotations

from collections.abc import Callable
from http import HTTPStatus

from agentsassemble.legacy.live_agent.state import read_live_agents
from agentsassemble.features.social.direct_messages import room_friend_dm_payload
from agentsassemble.features.social.friends import (
    delete_room_friend,
    room_friends_payload,
    upsert_room_friend,
)
from agentsassemble.features.social.profile import read_user_profile, update_user_profile
from agentsassemble.web.router import RequestContext, Router


def register_room_friend_profile_routes(
    router: Router,
    *,
    post_direct_dm: Callable[
        [RequestContext, dict[str, object]],
        dict[str, object],
    ],
) -> None:
    """Attach room-friend, direct-message, and local-profile routes."""

    @router.get("/api/room-friends/dm")
    def room_friend_dm(ctx: RequestContext) -> None:
        try:
            ctx.send_json(
                room_friend_dm_payload(
                    ctx.deps.output_root,
                    ctx.query_value("friend_id"),
                )
            )
        except ValueError as error:
            ctx.send_error(HTTPStatus.BAD_REQUEST, str(error))

    @router.get("/api/room-friends")
    def room_friends(ctx: RequestContext) -> None:
        ctx.send_json(
            room_friends_payload(
                ctx.deps.output_root,
                read_live_agents(ctx.deps.output_root),
            )
        )

    @router.get("/api/user-profile")
    def user_profile(ctx: RequestContext) -> None:
        ctx.send_json(read_user_profile(ctx.deps.output_root))

    @router.post("/api/room-friends/dm")
    def post_room_friend_dm(ctx: RequestContext) -> None:
        payload = ctx.read_json_body()
        if payload is None:
            return
        try:
            ctx.send_json(post_direct_dm(ctx, payload))
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
                    read_live_agents(ctx.deps.output_root),
                ),
            }
        )

    @router.post("/api/user-profile")
    def post_user_profile(ctx: RequestContext) -> None:
        payload = ctx.read_json_body()
        if payload is None:
            return
        ctx.send_json(update_user_profile(ctx.deps.output_root, payload))

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
                    read_live_agents(ctx.deps.output_root),
                ),
            }
        )


__all__ = ["register_room_friend_profile_routes"]
