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
from agentsassemble.application.central_directory_host import CentralDirectoryHost
from agentsassemble.application.transaction import ApplicationTransactionBoundary
from agentsassemble.room.attachments import FileAttachmentStore
from agentsassemble.identity.pairing import OperatorPairingService
from agentsassemble.identity.repository import IdentityBackend
from agentsassemble.providers.bridge_process import NativeCliBridgeProcessManager
from agentsassemble.application.public_tunnel import PublicTunnelManager
from agentsassemble.application.public_invite_runtime import PublicInviteRuntime
from agentsassemble.room.realtime import RoomRealtimeController
from agentsassemble.room.repository import RoomRepository
from agentsassemble.web.room_session import WsTicketStore


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
    public_tunnel_manager: PublicTunnelManager
    ws_ticket_store: WsTicketStore
    native_cli_bridge_manager: NativeCliBridgeProcessManager | None
    room_realtime_controller: RoomRealtimeController
    provider_usage_service: object | None = None
    application_database: ApplicationDatabase | None = None
    identity_registry_cleanup: Callable[[], object] | None = None
    central_directory_host: CentralDirectoryHost | None = None
    owns_room_repository: bool = True
    owns_invite_repository: bool = True
    owns_identity_backend: bool = False
    owns_public_tunnel_manager: bool = True
    owns_central_directory_host: bool = True
    owns_room_realtime_controller: bool = True
    owns_application_database: bool = False
    _state: str = field(default="new", init=False, repr=False)
    _state_lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)

    def start(
        self,
        server_url: str,
        *,
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
                self.public_tunnel_manager.set_local_url(clean_server_url)
                if self.central_directory_host is None:
                    try:
                        server_id = self.identity_backend.server_id()
                        self.central_directory_host = CentralDirectoryHost.from_environment(
                            output_root=self.output_root,
                            server_id=server_id,
                            public_url_runtime=self.public_invite,
                        )
                    except Exception:
                        self.central_directory_host = None
                if self.central_directory_host is not None:
                    rollback_actions.append(
                        ("central_directory_host", self.central_directory_host.close)
                    )
                    self.central_directory_host.start()
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

    def issue_bridge_connection(
        self,
        bridge_identity: dict[str, object],
    ) -> dict[str, str]:
        room_id = str(bridge_identity.get("meeting_id") or "")
        session_id = str(
            bridge_identity.get("session_id")
            or bridge_identity.get("agent_id")
            or ""
        )
        session_token, bridge_session = self.sessions.ensure_server_bridge(
            f"{room_id}:{session_id}",
            bridge_identity,
        )
        return {
            "ticket": self.ws_ticket_store.issue(
                bridge_session,
                session_token=session_token,
            ),
            "session_token": session_token,
        }

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

        if self.owns_public_tunnel_manager:
            attempt(self.public_tunnel_manager.stop)
        if self.owns_central_directory_host and self.central_directory_host is not None:
            attempt(self.central_directory_host.close)
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
