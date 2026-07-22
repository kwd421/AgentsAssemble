"""Application lifecycle for the local GUI server."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from agentsassemble.admission.repository import InviteSessionRepository
from agentsassemble.application.gui import ApplicationDatabase, GuiApplicationServices
from agentsassemble.identity.repository import IdentityBackend
from agentsassemble.room.repository import RoomRepository


@dataclass(frozen=True)
class GuiRuntimeDependencies:
    is_loopback_host: Callable[[str], bool]
    room_repository_settings: Callable[..., Any]
    build_postgres_application_database: Callable[..., ApplicationDatabase]
    build_room_repository: Callable[..., RoomRepository]
    build_invite_session_repository: Callable[..., InviteSessionRepository]
    build_identity_repository: Callable[..., IdentityBackend]
    build_application_services: Callable[..., GuiApplicationServices]
    make_handler: Callable[..., type]
    server_factory: Callable[..., Any]
    local_server_url: Callable[[object], str]
    autostart_live_agent_group: Callable[..., None]
    print_startup_banner: Callable[..., None]


def serve_gui_runtime(
    *,
    dependencies: GuiRuntimeDependencies,
    host: str = "127.0.0.1",
    port: int = 8765,
    output_root: Path | None = None,
    room_repository_backend: str = "sqlite",
    room_postgres_dsn_env: str,
    attention_shadow_mode: str = "off",
    public_url: str = "",
    host_token: str = "",
    unsafe_expose_control_plane: bool = False,
    start_public_tunnel: bool = False,
    live_agent_config: Path | None = None,
    live_agent_group_id: str = "",
    live_agent_auto_restart: bool = False,
    live_agent_max_restarts: int = 0,
    live_agent_restart_backoff_seconds: float = 5.0,
    live_agent_stale_restart_after_seconds: float = 0.0,
    frontend_dist_root: Path | None = None,
) -> None:
    if not dependencies.is_loopback_host(host) and not unsafe_expose_control_plane:
        raise ValueError(
            "Direct non-loopback GUI bind is disabled because it exposes the local control plane. "
            "Use a loopback bind with the public tunnel, or pass --unsafe-expose-control-plane "
            "only on an isolated trusted network."
        )
    root = output_root or Path(".agentsassemble")
    room_repository_settings = dependencies.room_repository_settings(
        backend=room_repository_backend,
        postgres_dsn_env=room_postgres_dsn_env,
    )
    application_database: ApplicationDatabase | None = None
    room_repository: RoomRepository | None = None
    invite_repository: InviteSessionRepository | None = None
    identity_backend: IdentityBackend | None = None
    owns_identity_backend = room_repository_settings.backend == "postgresql"
    repositories_transferred = False
    services: GuiApplicationServices | None = None
    server: Any | None = None
    try:
        if room_repository_settings.backend == "postgresql":
            application_database = dependencies.build_postgres_application_database(
                room_repository_settings
            )
            room_repository = dependencies.build_room_repository(
                root,
                room_repository_settings,
                postgres_database=application_database,
            )
            invite_repository = dependencies.build_invite_session_repository(
                root,
                room_repository_settings,
                postgres_database=application_database,
            )
            identity_backend = dependencies.build_identity_repository(
                root,
                room_repository_settings,
                postgres_database=application_database,
            )
        else:
            room_repository = dependencies.build_room_repository(
                root,
                room_repository_settings,
            )
            invite_repository = dependencies.build_invite_session_repository(
                root,
                room_repository_settings,
            )
            identity_backend = dependencies.build_identity_repository(
                root,
                room_repository_settings,
            )
        assert room_repository is not None
        assert invite_repository is not None
        assert identity_backend is not None
        repositories_transferred = True
        services = dependencies.build_application_services(
            root,
            room_repository_override=room_repository,
            owns_room_repository_override=True,
            invite_repository_override=invite_repository,
            owns_invite_repository_override=True,
            identity_backend_override=identity_backend,
            owns_identity_backend_override=owns_identity_backend,
            application_database_override=application_database,
            owns_application_database_override=application_database is not None,
            attention_shadow_mode=attention_shadow_mode,
        )
        handler = dependencies.make_handler(
            root,
            application_services=services,
            process_supervisor=services.process_supervisor,
            session_run_controller=services.session_run_controller,
            session_run_monitor=services.session_run_monitor,
            flow_supervisor=services.flow_supervisor,
            frontend_dist_root=frontend_dist_root,
            public_tunnel_manager=services.public_tunnel_manager,
            room_repository_override=room_repository,
            attention_shadow_mode=attention_shadow_mode,
        )
        server = dependencies.server_factory((host, port), handler)
    except BaseException as error:
        if services is not None:
            try:
                services.close()
            except BaseException as cleanup_error:
                error.add_note(f"GUI service cleanup after startup failure failed: {cleanup_error}")
        elif not repositories_transferred:
            if identity_backend is not None and owns_identity_backend:
                close_identity = getattr(identity_backend, "close", None)
                if callable(close_identity):
                    try:
                        close_identity()
                    except BaseException as cleanup_error:
                        error.add_note(
                            "Identity repository cleanup after startup failure failed: "
                            f"{cleanup_error}"
                        )
            if invite_repository is not None:
                try:
                    invite_repository.close()
                except BaseException as cleanup_error:
                    error.add_note(
                        f"Invite repository cleanup after startup failure failed: {cleanup_error}"
                    )
            if room_repository is not None:
                try:
                    room_repository.close()
                except BaseException as cleanup_error:
                    error.add_note(
                        f"Room repository cleanup after startup failure failed: {cleanup_error}"
                    )
            if application_database is not None:
                try:
                    application_database.close()
                except BaseException as cleanup_error:
                    error.add_note(
                        "PostgreSQL application database cleanup after startup failure failed: "
                        f"{cleanup_error}"
                    )
        raise
    if not dependencies.is_loopback_host(host):
        print(
            f"WARNING: AgentsAssemble GUI explicitly bound to non-loopback host {host!r}; the control "
            "plane is unauthenticated and can launch local processes. This unsafe mode is for isolated networks only."
        )
    try:
        assert services is not None
        public_invite_runtime = services.public_invite
        if host_token:
            public_invite_runtime.set_host_token(host_token)
        if public_url:
            public_invite_runtime.set_public_url(public_url)
        if (public_url or start_public_tunnel) and not public_invite_runtime.host_token():
            generated_token = public_invite_runtime.generate_host_token()
            print(f"AgentsAssemble host token: {generated_token}")
        assert server is not None
        server_url = dependencies.local_server_url(server.server_address)

        def autostart(server_url: str) -> None:
            if live_agent_config is None:
                return
            dependencies.autostart_live_agent_group(
                root,
                services.process_supervisor,
                config_path=live_agent_config,
                server_url=server_url,
                group_id=live_agent_group_id,
                auto_restart=live_agent_auto_restart,
                max_restarts=live_agent_max_restarts,
                restart_backoff_seconds=live_agent_restart_backoff_seconds,
                stale_restart_after_seconds=live_agent_stale_restart_after_seconds,
            )

        services.start(
            server_url,
            before_session_monitor=autostart,
            start_public_tunnel=start_public_tunnel,
        )
        dependencies.print_startup_banner(
            server_url,
            frontend_dist_root=frontend_dist_root,
            room_repository_backend=room_repository_settings.backend,
        )
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping AgentsAssemble GUI")
    finally:
        assert services is not None
        assert server is not None
        services.shutdown(transport_close=server.server_close)
