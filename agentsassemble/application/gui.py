from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from agentsassemble.admission.coordinator import RoomAdmissionCoordinator
from agentsassemble.admission.invite_service import InviteApplicationService
from agentsassemble.admission.preflight import RoomAdmissionService
from agentsassemble.admission.repository import InviteSessionRepository
from agentsassemble.admission.session_service import RoomSessionService
from agentsassemble.application.transaction import ApplicationTransactionBoundary
from agentsassemble.room.attachments import FileAttachmentStore
from agentsassemble.identity.pairing import OperatorPairingService
from agentsassemble.identity.repository import IdentityBackend
from agentsassemble.admission.projection import LegacyAdmissionProjection
from agentsassemble.providers.bridge_process import NativeCliBridgeProcessManager
from agentsassemble.application.public_tunnel import PublicTunnelManager
from agentsassemble.application.public_invite_runtime import PublicInviteRuntime
from agentsassemble.room.realtime import RoomRealtimeController
from agentsassemble.room.repository import RoomRepository
from agentsassemble.web.room_session import WsTicketStore


class SessionRunMonitor(Protocol):
    default_server: str

    def start(self) -> None: ...

    def stop(self) -> None: ...


class FlowSupervisor(Protocol):
    def status(
        self,
        *,
        meeting_id: str = "",
        quota_viewer: dict[str, object] | None = None,
    ) -> dict[str, object]: ...


class ProcessSupervisor(Protocol):
    def start_monitor(self, *, interval_seconds: float = 2.0) -> None: ...

    def stop_monitor(self, *, timeout_seconds: float = 5.0) -> None: ...

    def close(self) -> None: ...


class ApplicationDatabase(ApplicationTransactionBoundary, Protocol):
    def close(self) -> None: ...


@dataclass
class GuiApplicationServices:
    """Server-scoped services and their explicit lifecycle.

    ``ThreadingHTTPServer`` remains owned by ``serve_gui``. Its close callback
    is passed to :meth:`shutdown` so request transport closes after background
    runtimes and before the room repository, preserving the established order.
    """

    output_root: Path
    room_repository: RoomRepository
    invite_repository: InviteSessionRepository
    invites: InviteApplicationService
    sessions: RoomSessionService
    admission_preflight: RoomAdmissionService
    admission: RoomAdmissionCoordinator
    pairing: OperatorPairingService
    public_invite: PublicInviteRuntime
    identity_backend: IdentityBackend
    invite_store_path: Path
    media_store: FileAttachmentStore
    process_supervisor: ProcessSupervisor
    session_run_controller: object
    session_run_monitor: SessionRunMonitor
    flow_supervisor: FlowSupervisor
    public_tunnel_manager: PublicTunnelManager
    ws_ticket_store: WsTicketStore
    native_cli_bridge_manager: NativeCliBridgeProcessManager | None
    room_realtime_controller: RoomRealtimeController
    legacy_admission_projection: LegacyAdmissionProjection
    provider_usage_service: object | None = None
    application_database: ApplicationDatabase | None = None
    identity_registry_cleanup: Callable[[], object] | None = None
    owns_room_repository: bool = True
    owns_invite_repository: bool = True
    owns_identity_backend: bool = False
    owns_process_supervisor: bool = True
    owns_session_run_monitor: bool = True
    owns_public_tunnel_manager: bool = True
    owns_room_realtime_controller: bool = True
    owns_application_database: bool = False
    _state: str = field(default="new", init=False, repr=False)
    _state_lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)

    def start(
        self,
        server_url: str,
        *,
        before_session_monitor: Callable[[str], None] | None = None,
        start_public_tunnel: bool = False,
    ) -> None:
        """Start background services once, after the HTTP server is bound."""

        clean_server_url = str(server_url or "").rstrip("/")
        if not clean_server_url:
            raise ValueError("GUI server URL is required before services start.")
        with self._state_lock:
            if self._state == "started":
                return
            if self._state == "closed":
                raise RuntimeError("GUI application services are already closed.")
            if self._state != "new":
                raise RuntimeError(f"GUI application services cannot start from state {self._state!r}.")
            self._state = "starting"
            rollback_actions: list[tuple[str, Callable[[], object]]] = []
            try:
                rollback_actions.append(
                    ("process_monitor", lambda: self.process_supervisor.stop_monitor())
                )
                self.process_supervisor.start_monitor()
                self.public_tunnel_manager.set_local_url(clean_server_url)
                self.session_run_monitor.default_server = clean_server_url
                if before_session_monitor is not None:
                    before_session_monitor(clean_server_url)
                rollback_actions.append(
                    ("session_run_monitor", self.session_run_monitor.stop)
                )
                self.session_run_monitor.start()
                if start_public_tunnel:
                    rollback_actions.append(
                        ("public_tunnel", self.public_tunnel_manager.stop)
                    )
                    self.public_tunnel_manager.start()
            except BaseException as error:
                self._state = "start_failed"
                for name, callback in reversed(rollback_actions):
                    try:
                        callback()
                    except BaseException as cleanup_error:
                        error.add_note(
                            "GUI startup rollback failed in "
                            f"{name}: {type(cleanup_error).__name__}."
                        )
                raise
            self._state = "started"

    def shutdown(
        self,
        *,
        transport_close: Callable[[], object] | None = None,
        preserve_provider_runtimes: bool = False,
    ) -> None:
        """Close every owned resource once, without abandoning later cleanup."""

        with self._state_lock:
            if self._state == "closed":
                return
            self._state = "closed"

        errors: list[BaseException] = []

        def attempt(callback: Callable[[], object]) -> None:
            try:
                callback()
            except BaseException as error:
                errors.append(error)

        if self.owns_session_run_monitor:
            attempt(self.session_run_monitor.stop)
        if self.owns_public_tunnel_manager:
            attempt(self.public_tunnel_manager.stop)
        if self.owns_process_supervisor and not preserve_provider_runtimes:
            attempt(self.process_supervisor.close)
        if self.owns_room_realtime_controller:
            attempt(
                lambda: self.room_realtime_controller.close(
                    preserve_provider_runtimes=preserve_provider_runtimes,
                )
            )
        if transport_close is not None:
            attempt(transport_close)
        if self.identity_registry_cleanup is not None:
            attempt(self.identity_registry_cleanup)
        if self.owns_invite_repository:
            attempt(self.invite_repository.close)
        if self.owns_identity_backend:
            close_identity = getattr(self.identity_backend, "close", None)
            if callable(close_identity):
                attempt(close_identity)
        if self.owns_room_repository:
            attempt(self.room_repository.close)
        if self.owns_application_database and self.application_database is not None:
            attempt(self.application_database.close)

        if errors:
            first = errors[0]
            if len(errors) > 1:
                first.add_note(
                    f"{len(errors) - 1} additional GUI shutdown error(s) "
                    "were suppressed after cleanup."
                )
            raise first

    def close(self) -> None:
        self.shutdown()
