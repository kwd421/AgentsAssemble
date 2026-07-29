"""Route registration for the composed GUI application."""
from __future__ import annotations

from collections.abc import Callable
from http import HTTPStatus

from agentsassemble.application.gui import GuiApplicationServices
from agentsassemble.features.mafia.routes import register_mafia_routes
from agentsassemble.features.side_chat.routes import register_side_chat_routes
from agentsassemble.features.social.routes import register_room_friend_profile_routes
from agentsassemble.web.router import RequestContext, Router
from agentsassemble.web.routes.attachments import register_attachment_routes
from agentsassemble.web.routes.observability import register_observability_routes
from agentsassemble.web.routes.providers import register_provider_routes
from agentsassemble.web.routes.public_invite import register_public_invite_admin_routes
from agentsassemble.web.routes.retired import register_retired_legacy_routes
from agentsassemble.web.routes.room_creation import register_room_creation_routes
from agentsassemble.web.routes.room_settings import register_room_settings_routes
from agentsassemble.web.websocket import register_ws_ticket_route


def register_current_gui_routes(
    route_table: Router,
    *,
    services: GuiApplicationServices,
    provider_login_service: object,
    post_direct_dm: Callable[[RequestContext, dict[str, object]], dict[str, object]],
    read_operation_payload: Callable[..., dict[str, object] | None],
    record_operation: Callable[..., object],
) -> None:
    register_ws_ticket_route(
        route_table,
        ws_ticket_store=services.ws_ticket_store,
        is_local_operator=lambda ctx: ctx.is_local_operator(),
    )
    register_attachment_routes(route_table)
    register_retired_legacy_routes(route_table)
    register_room_creation_routes(route_table)
    register_room_settings_routes(route_table)
    register_side_chat_routes(route_table)
    register_room_friend_profile_routes(route_table, post_direct_dm=post_direct_dm)

    def provider_credentials_allowed(ctx: RequestContext) -> bool:
        if not ctx.is_local_operator() and not ctx.require_moderator():
            return False
        if ctx.uses_loopback_host():
            return True
        forwarded = str(ctx.headers.get("X-Forwarded-Proto") or "").lower()
        if forwarded != "https" or not ctx.peer_is_loopback():
            ctx.send_error(
                HTTPStatus.FORBIDDEN,
                "HTTPS is required for remote credential management",
            )
            return False
        return True

    register_provider_routes(
        route_table,
        credentials_allowed=provider_credentials_allowed,
        is_local_operator=lambda ctx: ctx.is_local_operator(),
        login_service=provider_login_service,
        usage_service=services.provider_usage_service,
    )
    register_public_invite_admin_routes(
        route_table,
        tunnel=services.public_tunnel_manager,
        is_local_operator=lambda ctx: ctx.is_local_operator(),
        local_server_url=lambda ctx: ctx.local_server_url(),
    )
    register_observability_routes(
        route_table,
        processes=services.process_supervisor,
        admission_projection=services.legacy_admission_projection,
    )
    register_mafia_routes(route_table, read_operation_payload=read_operation_payload)
