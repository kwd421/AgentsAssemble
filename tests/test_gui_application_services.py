from __future__ import annotations

import tempfile
import threading
import time
import unittest
from pathlib import Path

from agentsassemble.admission.coordinator import RoomAdmissionCoordinator
from agentsassemble.admission.invite_service import InviteApplicationService
from agentsassemble.admission.session_service import RoomSessionService
from agentsassemble.attachments import FileAttachmentStore
from agentsassemble.application.gui import GuiApplicationServices
from agentsassemble.identity.pairing import OperatorPairingService
from agentsassemble.legacy.admission_projection import LiveAgentLegacyAdmissionProjection
from agentsassemble.persistence.local.admission.repository import (
    MemoryInviteSessionRepository,
)
from agentsassemble.public_invite_runtime import PublicInviteRuntime
from agentsassemble.room_admission import RoomAdmissionService


class _Repository:
    def __init__(self, events: list[str]) -> None:
        self.events = events

    def close(self) -> None:
        self.events.append("repository.close")


class _InviteRepository(MemoryInviteSessionRepository):
    def __init__(self, events: list[str]) -> None:
        super().__init__()
        self.events = events

    def close(self) -> None:
        self.events.append("invite.close")


class _IdentityRepository:
    def __init__(self, events: list[str]) -> None:
        self.events = events

    def close(self) -> None:
        self.events.append("identity.close")


class _ApplicationDatabase:
    def __init__(self, events: list[str]) -> None:
        self.events = events

    def close(self) -> None:
        self.events.append("database.close")


class _ProcessSupervisor:
    def __init__(
        self,
        events: list[str],
        *,
        fail_start: bool = False,
        fail_stop_monitor: bool = False,
    ) -> None:
        self.events = events
        self.fail_start = fail_start
        self.fail_stop_monitor = fail_stop_monitor

    def start_monitor(self) -> None:
        self.events.append("process.start")
        if self.fail_start:
            raise RuntimeError("process monitor failed")

    def stop_monitor(self) -> None:
        self.events.append("process.monitor.stop")
        if self.fail_stop_monitor:
            raise RuntimeError("sensitive monitor cleanup detail")

    def close(self) -> None:
        self.events.append("process.close")


class _BlockingProcessSupervisor(_ProcessSupervisor):
    def __init__(self, events: list[str]) -> None:
        super().__init__(events)
        self.entered = threading.Event()
        self.release = threading.Event()

    def start_monitor(self) -> None:
        self.events.append("process.start")
        self.entered.set()
        self.release.wait(timeout=2)


class _SessionMonitor:
    def __init__(
        self,
        events: list[str],
        *,
        fail_start: bool = False,
        fail_stop: bool = False,
    ) -> None:
        self.events = events
        self.fail_start = fail_start
        self.fail_stop = fail_stop
        self.default_server = ""

    def start(self) -> None:
        self.events.append("session.start")
        if self.fail_start:
            raise RuntimeError("session monitor failed")

    def stop(self) -> None:
        self.events.append("session.stop")
        if self.fail_stop:
            raise RuntimeError("session monitor stop failed")


class _Tunnel:
    def __init__(self, events: list[str], *, fail_start: bool = False) -> None:
        self.events = events
        self.fail_start = fail_start

    def set_local_url(self, server_url: str) -> None:
        self.events.append(f"tunnel.url:{server_url}")

    def start(self) -> None:
        self.events.append("tunnel.start")
        if self.fail_start:
            raise RuntimeError("tunnel failed")

    def stop(self) -> None:
        self.events.append("tunnel.stop")


class _RealtimeController:
    def __init__(self, events: list[str]) -> None:
        self.events = events

    def close(self) -> None:
        self.events.append("realtime.close")


class _FlowSupervisor:
    def status(self, *, meeting_id: str = "", quota_viewer=None):
        del meeting_id, quota_viewer
        return {"flow": {"status": "idle"}}


class GuiApplicationServicesTests(unittest.TestCase):
    def _services(
        self,
        root: Path,
        events: list[str],
        *,
        fail_process_start: bool = False,
        fail_process_stop_monitor: bool = False,
        fail_session_start: bool = False,
        fail_session_stop: bool = False,
        fail_tunnel_start: bool = False,
        owns_resources: bool = True,
        owns_database: bool = False,
    ) -> GuiApplicationServices:
        room_repository = _Repository(events)
        invite_repository = _InviteRepository(events)
        identity_repository = _IdentityRepository(events)
        invites = InviteApplicationService(invite_repository)
        sessions = RoomSessionService(
            invite_repository,
            token_prefix="aas1",
            ttl_seconds=3600,
            token_key=invites.signing_secret,
        )
        admission_preflight = RoomAdmissionService(
            identities=identity_repository,  # type: ignore[arg-type]
            rooms=room_repository,  # type: ignore[arg-type]
            invite_inspector=invites.inspect,
        )
        admission = RoomAdmissionCoordinator(
            invites=invites,
            sessions=sessions,
            identities=identity_repository,  # type: ignore[arg-type]
            rooms=room_repository,  # type: ignore[arg-type]
        )
        pairing = OperatorPairingService(
            identities=identity_repository,  # type: ignore[arg-type]
            rooms=room_repository,  # type: ignore[arg-type]
            sessions=sessions,
        )
        return GuiApplicationServices(
            output_root=root,
            room_repository=room_repository,  # type: ignore[arg-type]
            invite_repository=invite_repository,
            invites=invites,
            sessions=sessions,
            admission_preflight=admission_preflight,
            admission=admission,
            pairing=pairing,
            public_invite=PublicInviteRuntime(environ={}),
            identity_backend=identity_repository,  # type: ignore[arg-type]
            invite_store_path=root / "room-invites.json",
            media_store=FileAttachmentStore(root),
            process_supervisor=_ProcessSupervisor(  # type: ignore[arg-type]
                events,
                fail_start=fail_process_start,
                fail_stop_monitor=fail_process_stop_monitor,
            ),
            session_run_controller=object(),  # type: ignore[arg-type]
            session_run_monitor=_SessionMonitor(
                events,
                fail_start=fail_session_start,
                fail_stop=fail_session_stop,
            ),
            flow_supervisor=_FlowSupervisor(),
            public_tunnel_manager=_Tunnel(  # type: ignore[arg-type]
                events,
                fail_start=fail_tunnel_start,
            ),
            ws_ticket_store=object(),  # type: ignore[arg-type]
            native_cli_bridge_manager=None,
            room_realtime_controller=_RealtimeController(events),  # type: ignore[arg-type]
            legacy_admission_projection=LiveAgentLegacyAdmissionProjection(root),
            application_database=_ApplicationDatabase(events) if owns_database else None,
            identity_registry_cleanup=lambda: events.append("identity.unregister"),
            owns_room_repository=owns_resources,
            owns_invite_repository=owns_resources,
            owns_identity_backend=owns_resources,
            owns_process_supervisor=owns_resources,
            owns_session_run_monitor=owns_resources,
            owns_public_tunnel_manager=owns_resources,
            owns_room_realtime_controller=owns_resources,
            owns_application_database=owns_database,
        )

    def test_start_preserves_post_bind_order_and_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            events: list[str] = []
            services = self._services(Path(temp_dir), events)

            services.start(
                "http://127.0.0.1:8765/",
                before_session_monitor=lambda server_url: events.append(f"autostart:{server_url}"),
                start_public_tunnel=True,
            )
            services.start("http://127.0.0.1:8765")

        self.assertEqual(
            events,
            [
                "process.start",
                "tunnel.url:http://127.0.0.1:8765",
                "autostart:http://127.0.0.1:8765",
                "session.start",
                "tunnel.start",
            ],
        )

    def test_shutdown_preserves_order_and_closes_once(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            events: list[str] = []
            services = self._services(Path(temp_dir), events)

            services.shutdown(transport_close=lambda: events.append("transport.close"))
            services.shutdown(transport_close=lambda: events.append("transport.close.again"))

        self.assertEqual(
            events,
            [
                "session.stop",
                "tunnel.stop",
                "process.close",
                "realtime.close",
                "transport.close",
                "identity.unregister",
                "invite.close",
                "identity.close",
                "repository.close",
            ],
        )

    def test_shutdown_does_not_close_borrowed_resources(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            events: list[str] = []
            services = self._services(Path(temp_dir), events, owns_resources=False)

            services.shutdown(transport_close=lambda: events.append("transport.close"))

        self.assertEqual(events, ["transport.close", "identity.unregister"])

    def test_shutdown_closes_shared_database_once_after_repository_adapters(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            events: list[str] = []
            services = self._services(
                Path(temp_dir),
                events,
                owns_database=True,
            )

            services.shutdown()
            services.shutdown()

        self.assertEqual(events[-4:], [
            "invite.close",
            "identity.close",
            "repository.close",
            "database.close",
        ])

    def test_shutdown_attempts_all_cleanup_after_one_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            events: list[str] = []
            services = self._services(Path(temp_dir), events, fail_session_stop=True)

            with self.assertRaisesRegex(RuntimeError, "session monitor stop failed"):
                services.shutdown(transport_close=lambda: events.append("transport.close"))

        self.assertEqual(
            events,
            [
                "session.stop",
                "tunnel.stop",
                "process.close",
                "realtime.close",
                "transport.close",
                "identity.unregister",
                "invite.close",
                "identity.close",
                "repository.close",
            ],
        )

    def test_failed_start_cannot_be_silently_retried_and_can_be_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            events: list[str] = []
            services = self._services(Path(temp_dir), events, fail_process_start=True)

            with self.assertRaisesRegex(RuntimeError, "process monitor failed"):
                services.start("http://127.0.0.1:8765")
            self.assertEqual(events, ["process.start", "process.monitor.stop"])
            with self.assertRaisesRegex(RuntimeError, "cannot start from state 'start_failed'"):
                services.start("http://127.0.0.1:8765")
            services.close()
            services.close()

        self.assertEqual(
            events,
            [
                "process.start",
                "process.monitor.stop",
                "session.stop",
                "tunnel.stop",
                "process.close",
                "realtime.close",
                "identity.unregister",
                "invite.close",
                "identity.close",
                "repository.close",
            ],
        )

    def test_pre_monitor_callback_failure_rolls_back_the_process_monitor(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            events: list[str] = []
            services = self._services(Path(temp_dir), events)

            def fail_callback(server_url: str) -> None:
                events.append(f"autostart:{server_url}")
                raise RuntimeError("autostart failed")

            with self.assertRaisesRegex(RuntimeError, "autostart failed"):
                services.start(
                    "http://127.0.0.1:8765",
                    before_session_monitor=fail_callback,
                )

        self.assertEqual(
            events,
            [
                "process.start",
                "tunnel.url:http://127.0.0.1:8765",
                "autostart:http://127.0.0.1:8765",
                "process.monitor.stop",
            ],
        )

    def test_session_start_failure_rolls_back_attempted_services_in_reverse_order(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            events: list[str] = []
            services = self._services(
                Path(temp_dir),
                events,
                fail_session_start=True,
            )

            with self.assertRaisesRegex(RuntimeError, "session monitor failed"):
                services.start("http://127.0.0.1:8765")

        self.assertEqual(
            events,
            [
                "process.start",
                "tunnel.url:http://127.0.0.1:8765",
                "session.start",
                "session.stop",
                "process.monitor.stop",
            ],
        )

    def test_tunnel_start_failure_rolls_back_all_started_services(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            events: list[str] = []
            services = self._services(
                Path(temp_dir),
                events,
                fail_tunnel_start=True,
            )

            with self.assertRaisesRegex(RuntimeError, "tunnel failed"):
                services.start(
                    "http://127.0.0.1:8765",
                    start_public_tunnel=True,
                )

        self.assertEqual(
            events,
            [
                "process.start",
                "tunnel.url:http://127.0.0.1:8765",
                "session.start",
                "tunnel.start",
                "tunnel.stop",
                "session.stop",
                "process.monitor.stop",
            ],
        )

    def test_startup_rollback_preserves_original_error_and_redacts_cleanup_detail(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            events: list[str] = []
            services = self._services(
                Path(temp_dir),
                events,
                fail_process_start=True,
                fail_process_stop_monitor=True,
            )

            with self.assertRaisesRegex(RuntimeError, "process monitor failed") as caught:
                services.start("http://127.0.0.1:8765")

        notes = list(getattr(caught.exception, "__notes__", []))
        self.assertEqual(
            notes,
            ["GUI startup rollback failed in process_monitor: RuntimeError."],
        )
        self.assertNotIn("sensitive monitor cleanup detail", str(notes))

    def test_shutdown_waits_for_in_progress_start_before_closing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            events: list[str] = []
            services = self._services(Path(temp_dir), events)
            process_supervisor = _BlockingProcessSupervisor(events)
            services.process_supervisor = process_supervisor  # type: ignore[assignment]

            starter = threading.Thread(target=lambda: services.start("http://127.0.0.1:8765"))
            stopper = threading.Thread(target=services.close)
            starter.start()
            self.assertTrue(process_supervisor.entered.wait(timeout=1))
            stopper.start()
            time.sleep(0.05)
            self.assertTrue(stopper.is_alive())
            process_supervisor.release.set()
            starter.join(timeout=1)
            stopper.join(timeout=1)

        self.assertFalse(starter.is_alive())
        self.assertFalse(stopper.is_alive())
        self.assertLess(events.index("session.start"), events.index("session.stop"))
        self.assertEqual(
            events[-4:],
            ["identity.unregister", "invite.close", "identity.close", "repository.close"],
        )


if __name__ == "__main__":
    unittest.main()
