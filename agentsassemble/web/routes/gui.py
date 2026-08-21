"""Route registration for the composed GUI application."""
from __future__ import annotations

from collections.abc import Callable
from http import HTTPStatus

from agentsassemble.application.gui import GuiApplicationServices
from agentsassemble.features.mafia.routes import register_mafia_routes
from agentsassemble.features.side_chat.routes import register_side_chat_routes
from agentsassemble.features.social.routes import register_room_friend_profile_routes
from agentsassemble.identity.google import GoogleAccountLoginService
from agentsassemble.web.router import RequestContext, Router
from agentsassemble.web.routes.attachments import register_attachment_routes
from agentsassemble.web.routes.central_login_callback import register_central_login_callback_routes
from agentsassemble.web.routes.accounts import register_account_routes
from agentsassemble.web.routes.identity_recovery import register_identity_recovery_routes
from agentsassemble.web.routes.observability import register_observability_routes
from agentsassemble.web.routes.personas import register_persona_routes
from agentsassemble.web.routes.providers import register_provider_routes
from agentsassemble.web.routes.public_invite import register_public_invite_admin_routes
from agentsassemble.web.routes.room_composition import register_room_routes
from agentsassemble.web.routes.room_creation import register_room_creation_routes
from agentsassemble.web.routes.room_settings import register_room_settings_routes
from agentsassemble.web.routes.runtime import register_runtime_routes
from agentsassemble.web.websocket import register_ws_ticket_route


# These mutation handlers prove a stronger credential or room/session
# capability themselves. Every other POST/DELETE route in the composed GUI is
# a local compatibility/control-plane operation and fails closed to the real
# local operator. Keeping the exception inventory here makes a newly
# registered mutation secure by default.
_HANDLER_AUTHORIZED_MUTATIONS = frozenset(
    {
        "/api/account/google",
        "/api/account/google/challenge",
        "/api/agent-sessions",
        "/api/agent-sessions/resume",
        "/api/attachments",
        "/api/host/claim",
        "/api/identity/recovery-code",
        "/api/identity/recovery-code/redeem",
        "/api/operator-pairing/create",
        "/api/operator-pairing/redeem",
        "/api/operator-pairing/revoke",
        "/api/provider-credentials/cerebras",
        "/api/provider-credentials/custom_api",
        "/api/provider-credentials/deepseek",
        "/api/provider-credentials/llmgateway",
        "/api/provider-credentials/openrouter",
        "/api/provider-credentials/opencode",
        "/api/provider-credentials/tokenrouter",
        "/api/provider-credentials/vercel",
        "/api/public-invite/host-token",
        "/api/public-invite/public-url",
        "/api/public-invite/tunnel/start",
        "/api/public-invite/tunnel/stop",
        "/api/room/channel-say",
        "/api/room/voice/join",
        "/api/room/voice/leave",
        "/api/room-channels",
        "/api/room-invite/admission",
        "/api/room-invite/agent-join",
        "/api/room-invite/companion",
        "/api/room-invite/create",
        "/api/room-invite/join",
        "/api/room-invite/leave",
        "/api/room-invite/revoke",
        "/api/room-members/mute",
        "/api/room-members/role",
        "/api/room-participants/export",
        "/api/room-participants/kick",
        "/api/room-participants/leave",
        "/api/room-settings",
        "/api/rooms",
        "/api/rooms/archive",
        "/api/rooms/close",
        "/api/runtime/rolling-restart",
        "/api/user-profile",
        "/api/ws-ticket",
    }
)


def install_gui_route_authorization(route_table: Router) -> None:
    """Protect every composed GUI mutation with an explicit route policy."""

    def authorize(method: str, registered_path: str, ctx: RequestContext) -> bool:
        if method not in {"POST", "DELETE"}:
            return True
        if registered_path in _HANDLER_AUTHORIZED_MUTATIONS:
            return True
        if ctx.is_local_operator():
            return True
        ctx.send_error(
            HTTPStatus.FORBIDDEN,
            "This compatibility operation is limited to the local operator.",
            code="local_operator_required",
        )
        return False

    route_table.set_route_authorizer(authorize)


def register_current_gui_routes(
    route_table: Router,
    *,
    services: GuiApplicationServices,
    provider_login_service: object,
    google_account_service: GoogleAccountLoginService,
    read_operation_payload: Callable[..., dict[str, object] | None],
) -> None:
    register_ws_ticket_route(
        route_table,
        ws_ticket_store=services.ws_ticket_store,
        is_local_operator=lambda ctx: ctx.is_local_operator(),
    )
    register_attachment_routes(route_table)
    register_central_login_callback_routes(route_table)
    register_account_routes(route_table, google=google_account_service)
    register_identity_recovery_routes(route_table)
    register_persona_routes(
        route_table,
        is_local_operator=lambda ctx: ctx.is_local_operator(),
    )
    register_room_creation_routes(route_table)
    register_room_routes(route_table)
    register_room_settings_routes(route_table)
    register_runtime_routes(
        route_table,
        room_repository=services.room_repository,
    )
    register_side_chat_routes(route_table)
    register_room_friend_profile_routes(route_table)

    def provider_credentials_allowed(ctx: RequestContext) -> bool:
        if ctx.is_local_operator():
            return True
        if not ctx.require_host():
            return False
        if not ctx.trusted_public_https_ingress_kind():
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
    register_observability_routes(route_table)
    register_mafia_routes(route_table, read_operation_payload=read_operation_payload)


__all__ = ["install_gui_route_authorization", "register_current_gui_routes"]
