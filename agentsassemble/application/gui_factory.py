from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from agentsassemble.admission.coordinator import RoomAdmissionCoordinator
from agentsassemble.admission.invite_service import (
    InviteApplicationService,
    SESSION_TOKEN_PREFIX,
    SESSION_TOKEN_TTL_SECONDS,
)
from agentsassemble.admission.preflight import RoomAdmissionService
from agentsassemble.admission.projection import LegacyAdmissionProjection
from agentsassemble.admission.repository import InviteSessionRepository
from agentsassemble.admission.session_service import RoomSessionService
from agentsassemble.application.gui import (
    ApplicationDatabase,
    FlowSupervisor,
    GuiApplicationServices,
    ProcessSupervisor,
    SessionRunMonitor,
)
from agentsassemble.application.room_users import (
    configure_room_users_backend,
    release_room_users_backend,
)
from agentsassemble.attachments import FileAttachmentStore
from agentsassemble.identity.pairing import OperatorPairingService
from agentsassemble.identity.repository import IdentityBackend
from agentsassemble.persistence.local.admission.repository import (
    JsonInviteSessionRepository,
)
from agentsassemble.persistence.local.identity.registry import (
    identity_store_for_output_root,
    register_identity_store_for_output_root,
    unregister_identity_store_for_output_root,
)
from agentsassemble.persistence.local.room.repository import RoomStore
from agentsassemble.providers.bridge_process import NativeCliBridgeProcessManager
from agentsassemble.application.public_invite_runtime import PublicInviteRuntime
from agentsassemble.application.public_tunnel import PublicTunnelManager
from agentsassemble.room_invite import (
    configure_room_invite_repository,
    default_room_invite_store_path,
)
from agentsassemble.room.realtime import (
    RoomRealtimeController,
    default_native_cli_provider_specs,
)
from agentsassemble.room.repository import RoomRepository
from agentsassemble.web.room_session import WsTicketStore


@dataclass(frozen=True)
class GuiRuntimeConstructors:
    """Concrete runtime choices supplied by the stable GUI entrypoint.

    The selected legacy process and monitor implementations still live beside
    ``serve_gui``. Passing their constructors explicitly keeps that compatibility
    patch surface without leaving ownership and rollback rules in the entrypoint.
    """

    process_supervisor: Callable[[Path], ProcessSupervisor]
    session_run_controller: Callable[[Path], object]
    flow_supervisor: Callable[[Path], FlowSupervisor]
    public_invite_runtime: Callable[[], PublicInviteRuntime]
    public_tunnel_manager: Callable[..., PublicTunnelManager]
    session_run_monitor: Callable[..., SessionRunMonitor]
    legacy_admission_projection: Callable[[Path], LegacyAdmissionProjection]
    backfill_room_registry: Callable[[Path, IdentityBackend], None]


def build_gui_application_services(
    output_root: Path,
    *,
    constructors: GuiRuntimeConstructors,
    process_supervisor: ProcessSupervisor | None = None,
    session_run_controller: object | None = None,
    session_run_monitor: SessionRunMonitor | None = None,
    flow_supervisor: FlowSupervisor | None = None,
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
) -> GuiApplicationServices:
    """Build the single server-scoped ownership graph used by the GUI."""

    if room_realtime_controller_override is not None:
        room_repository = room_repository_override or room_realtime_controller_override.store
        if room_repository is not room_realtime_controller_override.store:
            raise ValueError(
                "Room realtime controller and GUI routes must share one room repository instance."
            )
        owns_room_repository = bool(
            owns_room_repository_override and room_repository_override is not None
        )
    else:
        room_repository = room_repository_override or RoomStore(output_root)
        owns_room_repository = bool(
            room_repository_override is None or owns_room_repository_override
        )

    cleanup_actions: list[tuple[str, Callable[[], object]]] = []

    def remember_cleanup(name: str, callback: Callable[[], object]) -> None:
        cleanup_actions.append((name, callback))

    owns_application_database = bool(
        application_database_override is not None
        and owns_application_database_override
    )
    if owns_application_database:
        remember_cleanup(
            "application_database.close",
            application_database_override.close,
        )

    if owns_room_repository:
        remember_cleanup("room_repository.close", room_repository.close)

    invite_repository = invite_repository_override or JsonInviteSessionRepository(
        default_room_invite_store_path(output_root)
    )
    owns_invite_repository = bool(
        invite_repository_override is None or owns_invite_repository_override
    )
    identity_backend = identity_backend_override or identity_store_for_output_root(
        output_root
    )
    owns_identity_backend = bool(
        identity_backend_override is not None and owns_identity_backend_override
    )

    owns_process_supervisor = process_supervisor is None
    owns_session_run_monitor = session_run_monitor is None
    owns_public_tunnel_manager = public_tunnel_manager is None
    owns_room_realtime_controller = room_realtime_controller_override is None

    try:
        configure_room_invite_repository(invite_repository)
        if owns_invite_repository:
            remember_cleanup("invite_repository.close", invite_repository.close)
        if owns_identity_backend:
            close_identity = getattr(identity_backend, "close", None)
            if callable(close_identity):
                remember_cleanup("identity_backend.close", close_identity)

        register_identity_store_for_output_root(output_root, identity_backend)

        def release_identity_registration() -> None:
            release_room_users_backend(identity_backend)
            unregister_identity_store_for_output_root(output_root, identity_backend)

        remember_cleanup("identity_backend.unregister", release_identity_registration)
        configure_room_users_backend(identity_backend)
        constructors.backfill_room_registry(output_root, identity_backend)

        public_invite_runtime = (
            public_invite_runtime_override or constructors.public_invite_runtime()
        )
        invite_application = InviteApplicationService(
            invite_repository,
            public_url=public_invite_runtime.public_url,
        )
        room_session_service = RoomSessionService(
            invite_repository,
            token_prefix=SESSION_TOKEN_PREFIX,
            ttl_seconds=SESSION_TOKEN_TTL_SECONDS,
            token_key=invite_application.signing_secret,
        )
        admission_preflight = RoomAdmissionService(
            identities=identity_backend,
            rooms=room_repository,
            invite_inspector=invite_application.inspect,
        )
        admission_coordinator = RoomAdmissionCoordinator(
            invites=invite_application,
            sessions=room_session_service,
            identities=identity_backend,
            rooms=room_repository,
            transaction_boundary=application_database_override,
        )
        operator_pairing_service = OperatorPairingService(
            identities=identity_backend,
            rooms=room_repository,
            sessions=room_session_service,
            transaction_boundary=application_database_override,
        )
        legacy_admission_projection = constructors.legacy_admission_projection(
            output_root
        )

        live_agent_process_supervisor = (
            process_supervisor or constructors.process_supervisor(output_root)
        )
        if owns_process_supervisor:
            remember_cleanup(
                "process_supervisor.close",
                live_agent_process_supervisor.close,
            )

        live_agent_session_run_controller = (
            session_run_controller or constructors.session_run_controller(output_root)
        )
        live_agent_flow_supervisor = (
            flow_supervisor or constructors.flow_supervisor(output_root)
        )
        invite_tunnel_manager = (
            public_tunnel_manager
            or constructors.public_tunnel_manager(
                public_invite_runtime=public_invite_runtime,
            )
        )
        if owns_public_tunnel_manager:
            remember_cleanup(
                "public_tunnel_manager.stop",
                invite_tunnel_manager.stop,
            )

        live_agent_session_run_monitor = (
            session_run_monitor
            or constructors.session_run_monitor(
                output_root,
                live_agent_process_supervisor,
                live_agent_session_run_controller,
                default_server="",
            )
        )
        if owns_session_run_monitor:
            remember_cleanup(
                "session_run_monitor.stop",
                live_agent_session_run_monitor.stop,
            )

        ws_ticket_store = WsTicketStore()
        native_cli_bridge_manager: NativeCliBridgeProcessManager | None = None
        if room_realtime_controller_override is not None:
            room_realtime_controller = room_realtime_controller_override
        else:
            native_cli_bridge_manager = NativeCliBridgeProcessManager(output_root)
            built_controller: RoomRealtimeController | None = None
            try:
                built_controller = RoomRealtimeController(
                    output_root,
                    invite_application=invite_application,
                    room_sessions=room_session_service,
                    providers=default_native_cli_provider_specs(workspace=Path.cwd()),
                    bridge_manager=native_cli_bridge_manager,
                    repository=room_repository,
                    attention_shadow_mode=attention_shadow_mode,
                )
                native_cli_bridge_manager.set_exit_listener(
                    built_controller.bridge_process_exited
                )
            except BaseException as error:
                try:
                    if built_controller is not None:
                        built_controller.close()
                    else:
                        native_cli_bridge_manager.close()
                except BaseException as cleanup_error:
                    error.add_note(
                        "GUI realtime construction cleanup failed: "
                        f"{cleanup_error}"
                    )
                raise
            room_realtime_controller = built_controller
            remember_cleanup(
                "room_realtime_controller.close",
                room_realtime_controller.close,
            )

        services = GuiApplicationServices(
            output_root=output_root,
            room_repository=room_repository,
            invite_repository=invite_repository,
            invites=invite_application,
            sessions=room_session_service,
            admission_preflight=admission_preflight,
            admission=admission_coordinator,
            pairing=operator_pairing_service,
            public_invite=public_invite_runtime,
            identity_backend=identity_backend,
            invite_store_path=default_room_invite_store_path(output_root),
            media_store=FileAttachmentStore(output_root),
            process_supervisor=live_agent_process_supervisor,
            session_run_controller=live_agent_session_run_controller,
            session_run_monitor=live_agent_session_run_monitor,
            flow_supervisor=live_agent_flow_supervisor,
            public_tunnel_manager=invite_tunnel_manager,
            ws_ticket_store=ws_ticket_store,
            native_cli_bridge_manager=native_cli_bridge_manager,
            room_realtime_controller=room_realtime_controller,
            legacy_admission_projection=legacy_admission_projection,
            application_database=application_database_override,
            identity_registry_cleanup=release_identity_registration,
            owns_room_repository=owns_room_repository,
            owns_invite_repository=owns_invite_repository,
            owns_identity_backend=owns_identity_backend,
            owns_process_supervisor=owns_process_supervisor,
            owns_session_run_monitor=owns_session_run_monitor,
            owns_public_tunnel_manager=owns_public_tunnel_manager,
            owns_room_realtime_controller=owns_room_realtime_controller,
            owns_application_database=owns_application_database,
        )
    except BaseException as error:
        for name, callback in reversed(cleanup_actions):
            try:
                callback()
            except BaseException as cleanup_error:
                error.add_note(
                    f"GUI service construction cleanup failed in {name}: {cleanup_error}"
                )
        raise
    cleanup_actions.clear()
    return services
