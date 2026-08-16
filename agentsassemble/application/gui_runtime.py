"""Application lifecycle for the local GUI server."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

from agentsassemble.admission.repository import InviteSessionRepository
from agentsassemble.application.engine_instance_lock import EngineInstanceLock
from agentsassemble.application.gui import ApplicationDatabase, GuiApplicationServices
from agentsassemble.application.desktop_parent_watchdog import (
    DESKTOP_PARENT_PID_ENV,
    install_desktop_shutdown_signal_handler,
    start_desktop_parent_watchdog,
)
from agentsassemble.application.rolling_restart import (
    RollingChildBootstrap,
    RollingRestartCoordinator,
)
from agentsassemble.application.stable_entry import (
    activate_stable_entry_publisher,
    configure_stable_entry_publisher,
    reset_stable_entry_publisher,
)
from agentsassemble.web.frontend_runtime import (
    frontend_build_version,
    materialize_frontend_release,
)
from agentsassemble.identity.repository import IdentityBackend
from agentsassemble.room.repository import RoomRepository


DESKTOP_RUNTIME_URL_PREFIX = "AgentsAssemble desktop runtime: "


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
    # Install user-local CLI dirs before catalog discovery or provider spawn.
    from agentsassemble.providers.process_environment import ensure_provider_cli_search_path

    ensure_provider_cli_search_path()
    rolling_bootstrap = RollingChildBootstrap.from_environment()
    runtime_instance_id = f"gui-{uuid4().hex[:16]}"
    if not dependencies.is_loopback_host(host) and not unsafe_expose_control_plane:
        raise ValueError(
            "Direct non-loopback GUI bind is disabled because it exposes the local control plane. "
            "Use a loopback bind with the public tunnel, or pass --unsafe-expose-control-plane "
            "only on an isolated trusted network."
        )
    from agentsassemble.application.user_data_root import resolve_output_root

    root = resolve_output_root(output_root)
    engine_instance_lock = EngineInstanceLock.acquire(
        root,
        inherited_fd=(
            rolling_bootstrap.engine_lock_fd if rolling_bootstrap is not None else None
        ),
    )
    advertised_server_url = ""

    try:
        served_frontend_root = materialize_frontend_release(
            frontend_dist_root,
            release_root=root / "runtime" / "frontend-releases",
        )
        room_repository_settings = dependencies.room_repository_settings(
            backend=room_repository_backend,
            postgres_dsn_env=room_postgres_dsn_env,
        )
    except BaseException:
        engine_instance_lock.close()
        raise
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
        service_options = {
            "room_repository_override": room_repository,
            "owns_room_repository_override": True,
            "invite_repository_override": invite_repository,
            "owns_invite_repository_override": True,
            "identity_backend_override": identity_backend,
            "owns_identity_backend_override": owns_identity_backend,
            "application_database_override": application_database,
            "owns_application_database_override": application_database is not None,
            "attention_shadow_mode": attention_shadow_mode,
        }
        if rolling_bootstrap is not None:
            service_options["reconcile_startup_sessions"] = False
        services = dependencies.build_application_services(
            root,
            **service_options,
        )
        if rolling_bootstrap is not None and services.native_cli_bridge_manager is not None:
            for room in room_repository.list_rooms(include_archived=True):
                room_id = str(room.get("room_id") or "")
                if not room_id:
                    continue
                for session in room_repository.sessions(room_id):
                    try:
                        services.native_cli_bridge_manager.adopt_preserved_shared_runtime(
                            room_id,
                            session,
                        )
                    except Exception as error:
                        # One unusable session record must not abort the
                        # replacement's boot: that turned a stale bridge into a
                        # permanent veto on every rolling restart. The session
                        # goes through normal recovery instead, and the reason
                        # is reported rather than swallowed.
                        print(
                            "AgentsAssemble rolling handoff: skipped preserved session "
                            f"{session.get('session_id') or session.get('participant_id')} "
                            f"in {room_id}: {error}",
                            flush=True,
                        )
        handler = dependencies.make_handler(
            root,
            application_services=services,
            process_supervisor=services.process_supervisor,
            session_run_controller=services.session_run_controller,
            session_run_monitor=services.session_run_monitor,
            flow_supervisor=services.flow_supervisor,
            frontend_dist_root=served_frontend_root,
            public_tunnel_manager=services.public_tunnel_manager,
            room_repository_override=room_repository,
            attention_shadow_mode=attention_shadow_mode,
        )
        if rolling_bootstrap is None:
            server = dependencies.server_factory((host, port), handler)
        else:
            server = dependencies.server_factory(
                (host, port),
                handler,
                inherited_fd=rolling_bootstrap.listener_fd,
            )
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
        engine_instance_lock.close()
        raise
    if not dependencies.is_loopback_host(host):
        print(
            f"WARNING: AgentsAssemble GUI explicitly bound to non-loopback host {host!r}; the control "
            "plane is unauthenticated and can launch local processes. This unsafe mode is for isolated networks only."
        )
    stable_entry_configured = False
    try:
        assert services is not None
        configure_stable_entry_publisher(
            root,
            owner_id=runtime_instance_id,
            predecessor_owner_id=(
                rolling_bootstrap.parent_instance_id if rolling_bootstrap else ""
            ),
            active=rolling_bootstrap is None,
        )
        stable_entry_configured = True
        public_invite_runtime = services.public_invite
        if host_token:
            public_invite_runtime.set_host_token(host_token)
        if public_url:
            services.public_tunnel_manager.set_manual_public_url(public_url)
        if (public_url or start_public_tunnel) and not public_invite_runtime.host_token():
            generated_token = public_invite_runtime.generate_host_token()
            print(f"AgentsAssemble host token: {generated_token}")
        assert server is not None
        server_url = dependencies.local_server_url(server.server_address)
        if os.environ.get("AGENTSASSEMBLE_DESKTOP_RUNTIME") == "1":
            # The desktop parent owns the child's stdout pipe. Reporting the
            # address only after the server has bound lets the kernel choose a
            # collision-free port without a find-free-port/rebind race.
            print(f"{DESKTOP_RUNTIME_URL_PREFIX}{server_url}", flush=True)
        rolling_restart = RollingRestartCoordinator(
            server,
            output_root=root,
            engine_lock_fd=engine_instance_lock.fileno(),
            generation=rolling_bootstrap.generation if rolling_bootstrap else 0,
            instance_id=runtime_instance_id,
            frontend_version=frontend_build_version(served_frontend_root),
        )
        server.rolling_restart = rolling_restart

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
        if rolling_bootstrap is not None:
            rolling_bootstrap.report_ready_and_wait()
            activate_stable_entry_publisher()
            rolling_restart.activate_from_handoff(
                rolling_bootstrap.operation_id,
            )
            services.room_realtime_controller.session_recovery.restart_preserved_server_sessions(
                server_url=server_url,
                ticket_issuer=services.issue_bridge_connection,
            )
        dependencies.print_startup_banner(
            server_url,
            frontend_dist_root=served_frontend_root,
            room_repository_backend=room_repository_settings.backend,
        )
        from agentsassemble.application.local_engine_registry import (
            write_local_engine_registry,
        )

        write_local_engine_registry(
            root,
            server_url=server_url,
            pid=os.getpid(),
            instance_id=runtime_instance_id,
        )
        advertised_server_url = server_url
        install_desktop_shutdown_signal_handler(lambda: server.shutdown())
        start_desktop_parent_watchdog(lambda: server.shutdown())
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping AgentsAssemble GUI")
    finally:
        try:
            assert services is not None
            assert server is not None
            rolling_restart = getattr(server, "rolling_restart", None)
            if rolling_restart is not None and rolling_restart.handoff_ready():
                try:
                    services.shutdown(
                        transport_close=server.server_close,
                        preserve_provider_runtimes=True,
                    )
                except BaseException as error:
                    rolling_restart.abandon_replacement(str(error))
                    raise
                rolling_restart.release_replacement()
            else:
                services.shutdown(transport_close=server.server_close)
        finally:
            try:
                from agentsassemble.application.local_engine_registry import (
                    clear_local_engine_registry,
                )

                if advertised_server_url:
                    clear_local_engine_registry(
                        root,
                        expected_pid=(
                            None
                            if os.environ.get(DESKTOP_PARENT_PID_ENV)
                            else os.getpid()
                        ),
                        expected_url=advertised_server_url,
                    )
            except Exception:
                pass
            if stable_entry_configured:
                reset_stable_entry_publisher()
            engine_instance_lock.close()
