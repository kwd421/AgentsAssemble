"""Current local HTTP/WebSocket application entrypoint."""
from __future__ import annotations

import re
from http import HTTPStatus
from pathlib import Path

from agentsassemble.admission.repository import InviteSessionRepository
from agentsassemble.admission.repository_factory import build_invite_session_repository
from agentsassemble.application.agent_sessions.commands import room_sse_frames_after_cursor
from agentsassemble.application.gui import ApplicationDatabase, GuiApplicationServices
from agentsassemble.application.gui_factory import (
    GuiRuntimeConstructors,
    build_gui_application_services,
)
from agentsassemble.application.gui_runtime import GuiRuntimeDependencies, serve_gui_runtime
from agentsassemble.application.public_invite_runtime import PublicInviteRuntime
from agentsassemble.application.public_tunnel import PublicTunnelManager
from agentsassemble.application.room_repository_factory import (
    DEFAULT_POSTGRES_DSN_ENV,
    RoomRepositorySettings,
    build_postgres_application_database,
    build_room_repository,
)
from agentsassemble.identity.factory import build_identity_repository
from agentsassemble.identity.google import GoogleAccountLoginService
from agentsassemble.identity.repository import IdentityBackend
from agentsassemble.providers.login import ProviderLoginService
from agentsassemble.room.realtime import RoomRealtimeController
from agentsassemble.room.repository import RoomRepository
from agentsassemble.web.frontend_runtime import (
    REACT_APP_BUILD_COMMAND,
    default_frontend_dist_root,
    frontend_dist_status,
)
from agentsassemble.web.gui_server import make_gui_http_handler
from agentsassemble.web.http_server import AgentsAssembleHTTPServer as ThreadingHTTPServer
from agentsassemble.web.room_ws_composition import build_ws_room_deps_factory
from agentsassemble.web.router import GuiDeps, RequestContext, Router, local_server_url
from agentsassemble.web.routes.gui import (
    install_gui_route_authorization,
    register_current_gui_routes,
)
from agentsassemble.web.static import ReactStaticTransport


def _build_gui_application_services(
    output_root: Path,
    *,
    public_tunnel_manager: PublicTunnelManager | None = None,
    room_realtime_controller_override: RoomRealtimeController | None = None,
    room_repository_override: RoomRepository | None = None,
    owns_room_repository_override: bool = False,
    invite_repository_override: InviteSessionRepository | None = None,
    owns_invite_repository_override: bool = False,
    identity_backend_override: IdentityBackend | None = None,
    owns_identity_backend_override: bool = False,
    application_database_override: ApplicationDatabase | None = None,
    owns_application_database_override: bool = False,
    public_invite_runtime_override: PublicInviteRuntime | None = None,
    attention_shadow_mode: str = "off",
    reconcile_startup_sessions: bool = True,
) -> GuiApplicationServices:
    return build_gui_application_services(
        output_root,
        constructors=GuiRuntimeConstructors(
            public_invite_runtime=PublicInviteRuntime,
            public_tunnel_manager=PublicTunnelManager,
        ),
        public_tunnel_manager=public_tunnel_manager,
        room_realtime_controller_override=room_realtime_controller_override,
        room_repository_override=room_repository_override,
        owns_room_repository_override=owns_room_repository_override,
        invite_repository_override=invite_repository_override,
        owns_invite_repository_override=owns_invite_repository_override,
        identity_backend_override=identity_backend_override,
        owns_identity_backend_override=owns_identity_backend_override,
        application_database_override=application_database_override,
        owns_application_database_override=owns_application_database_override,
        public_invite_runtime_override=public_invite_runtime_override,
        attention_shadow_mode=attention_shadow_mode,
        reconcile_startup_sessions=reconcile_startup_sessions,
    )


def serve_gui(
    host: str = "127.0.0.1",
    port: int = 8765,
    output_root: Path | None = None,
    *,
    room_repository_backend: str = "sqlite",
    room_postgres_dsn_env: str = DEFAULT_POSTGRES_DSN_ENV,
    attention_shadow_mode: str = "off",
    public_url: str = "",
    host_token: str = "",
    unsafe_expose_control_plane: bool = False,
    start_public_tunnel: bool = False,
    frontend_dist_root: Path | None = None,
) -> None:
    serve_gui_runtime(
        dependencies=GuiRuntimeDependencies(
            is_loopback_host=_is_loopback_host,
            room_repository_settings=RoomRepositorySettings.from_environment,
            build_postgres_application_database=build_postgres_application_database,
            build_room_repository=build_room_repository,
            build_invite_session_repository=build_invite_session_repository,
            build_identity_repository=build_identity_repository,
            build_application_services=_build_gui_application_services,
            make_handler=_make_handler,
            server_factory=ThreadingHTTPServer,
            local_server_url=local_server_url,
            print_startup_banner=_print_gui_startup_banner,
        ),
        host=host,
        port=port,
        output_root=output_root,
        room_repository_backend=room_repository_backend,
        room_postgres_dsn_env=room_postgres_dsn_env,
        attention_shadow_mode=attention_shadow_mode,
        public_url=public_url,
        host_token=host_token,
        unsafe_expose_control_plane=unsafe_expose_control_plane,
        start_public_tunnel=start_public_tunnel,
        frontend_dist_root=frontend_dist_root,
    )


def _make_handler(
    output_root: Path,
    *,
    application_services: GuiApplicationServices | None = None,
    frontend_dist_root: Path | None = None,
    public_tunnel_manager: PublicTunnelManager | None = None,
    google_account_service_override: GoogleAccountLoginService | None = None,
    room_realtime_controller_override: RoomRealtimeController | None = None,
    room_repository_override: RoomRepository | None = None,
    invite_repository_override: InviteSessionRepository | None = None,
    public_invite_runtime_override: PublicInviteRuntime | None = None,
    attention_shadow_mode: str = "off",
) -> type:
    services = application_services or _build_gui_application_services(
        output_root,
        public_tunnel_manager=public_tunnel_manager,
        room_realtime_controller_override=room_realtime_controller_override,
        room_repository_override=room_repository_override,
        invite_repository_override=invite_repository_override,
        public_invite_runtime_override=public_invite_runtime_override,
        attention_shadow_mode=attention_shadow_mode,
    )
    if output_root.resolve() != services.output_root.resolve():
        raise ValueError("GUI application services were built for a different output root.")

    room_controller = services.room_realtime_controller

    def execute_room_runtime_command(
        identity: dict[str, object],
        command: dict[str, object],
        server_url: str,
    ) -> dict[str, object]:
        return room_controller.handle_command(
            identity,
            command,
            server_url=server_url,
            ticket_issuer=services.issue_bridge_connection,
        )

    route_deps = GuiDeps(
        output_root=output_root,
        room_repository=services.room_repository,
        identity_backend=services.identity_backend,
        invite_application=services.invites,
        room_sessions=services.sessions,
        admission_preflight_service=services.admission_preflight,
        admission_coordinator=services.admission,
        operator_pairing_service=services.pairing,
        public_invite_runtime=services.public_invite,
        attachment_store=services.media_store,
        room_command_handler=room_controller.handle_command,
        room_runtime_command_handler=execute_room_runtime_command,
    )
    route_table = Router()

    def read_operation_payload(
        ctx: RequestContext,
        _operation_name: str,
        _target_id: str = "",
    ) -> dict[str, object] | None:
        return ctx.read_json_body()

    register_current_gui_routes(
        route_table,
        services=services,
        provider_login_service=ProviderLoginService(
            catalog_refresher=lambda: room_controller.provider_catalog.snapshot(
                refresh=True
            ),
        ),
        google_account_service=(
            google_account_service_override
            or GoogleAccountLoginService.from_environment()
        ),
        read_operation_payload=read_operation_payload,
    )
    install_gui_route_authorization(route_table)

    ws_room_deps_factory = build_ws_room_deps_factory(
        output_root=output_root,
        services=services,
        room_repository=services.room_repository,
        local_server_url=local_server_url,
        execute_room_command=execute_room_runtime_command,
    )
    static_transport = ReactStaticTransport(
        frontend_root=(frontend_dist_root or default_frontend_dist_root()).resolve(),
        pre_join_guide_payload=lambda server_url: {
            "server_url": server_url,
            "public_url": services.public_invite.public_url(),
        },
        api_catalog_payload=lambda server_url: {
            "server_url": server_url,
            "public_url": services.public_invite.public_url(),
        },
    )
    return make_gui_http_handler(
        output_root=output_root,
        services=services,
        route_table=route_table,
        route_deps=route_deps,
        static_transport=static_transport,
        ws_ticket_store=services.ws_ticket_store,
        room_realtime_controller=room_controller,
        ws_room_deps_factory=ws_room_deps_factory,
        room_repository=services.room_repository,
        room_sse_frames_after_cursor=room_sse_frames_after_cursor,
        sse_stream_error_payload=_sse_stream_error_payload,
        sse_frame_id=_sse_frame_id,
    )


def _sse_frame_id(frame: str) -> str:
    match = re.search(r"^id:\s*(.+)$", frame, flags=re.MULTILINE)
    return match.group(1).strip() if match else ""


def _sse_stream_error_payload(
    stream: str,
    error: Exception,
    *,
    meeting_id: str | None = None,
) -> dict[str, object]:
    return {
        "stream": stream,
        "room_id": str(meeting_id or ""),
        "error": str(error)[:500],
    }


def _is_loopback_host(host: str) -> bool:
    return str(host or "").strip().lower() in {"127.0.0.1", "localhost", "::1"}


def _print_gui_startup_banner(
    server_url: str,
    *,
    frontend_dist_root: Path | None = None,
    room_repository_backend: str = "sqlite",
) -> None:
    status = frontend_dist_status(frontend_dist_root)
    print(f"AgentsAssemble GUI: {server_url.rstrip('/')}")
    print(f"- Room repository: {room_repository_backend}")
    if status.static_available:
        print(f"- Room client: {server_url.rstrip('/')}/")
    else:
        print(f"- Build the room client: {REACT_APP_BUILD_COMMAND}")


__all__ = ["_build_gui_application_services", "_make_handler", "serve_gui"]
