from __future__ import annotations

import os
import tempfile
import unittest
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from agentsassemble.agent_sessions import create_agent_session_payload
from agentsassemble.gui import _build_gui_application_services, _make_handler, serve_gui
from agentsassemble.room.realtime import RoomRealtimeController
from tests.room_realtime_test_support import memory_room_access_services
from agentsassemble.application.room_repository_factory import (
    RoomRepositoryConfigurationError,
    RoomRepositorySettings,
)
from agentsassemble.room_store import RoomStore
from agentsassemble.persistence.local.admission.repository import (
    MemoryInviteSessionRepository,
)


class GuiRoomRepositoryInjectionTests(unittest.TestCase):
    def test_unconfigured_postgres_fails_before_process_or_sqlite_startup(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            with patch.dict(os.environ, {}, clear=True), patch(
                "agentsassemble.gui.LiveAgentProcessSupervisor"
            ) as process_supervisor:
                with self.assertRaises(RoomRepositoryConfigurationError):
                    serve_gui(
                        output_root=root,
                        room_repository_backend="postgresql",
                    )

            process_supervisor.assert_not_called()
            self.assertFalse((root / "rooms" / "rooms.sqlite3").exists())

    def test_gui_startup_injects_selected_repository_without_exposing_dsn(self) -> None:
        repository = MagicMock()
        settings = RoomRepositorySettings(
            backend="postgresql",
            postgres_dsn="postgresql://secret-user:secret-pass@example.invalid/rooms",
            postgres_dsn_env="ROOM_DATABASE_SECRET",
        )
        handler = SimpleNamespace(room_realtime_controller=MagicMock())
        server = MagicMock()
        server.server_address = ("127.0.0.1", 48765)
        server.serve_forever.side_effect = KeyboardInterrupt
        stdout = StringIO()
        invite_repository = MemoryInviteSessionRepository()
        identity_repository = MagicMock()
        application_database = MagicMock()
        identity_repository.list_rooms.return_value = []
        identity_repository.operator_user_id.return_value = ""

        with patch(
            "agentsassemble.gui.RoomRepositorySettings.from_environment",
            return_value=settings,
        ) as from_environment, patch(
            "agentsassemble.gui.build_postgres_application_database",
            return_value=application_database,
        ) as build_database, patch(
            "agentsassemble.gui.build_room_repository",
            return_value=repository,
        ) as build_repository, patch(
            "agentsassemble.gui.build_invite_session_repository",
            return_value=invite_repository,
        ) as build_invites, patch(
            "agentsassemble.gui.build_identity_repository",
            return_value=identity_repository,
        ) as build_identities, patch(
            "agentsassemble.gui._make_handler",
            return_value=handler,
        ) as make_handler, patch(
            "agentsassemble.gui.LiveAgentProcessSupervisor",
            return_value=MagicMock(),
        ), patch(
            "agentsassemble.gui.LiveAgentSessionRunController",
            return_value=MagicMock(),
        ), patch(
            "agentsassemble.gui.LiveAgentFlowSupervisor",
            return_value=MagicMock(),
        ), patch(
            "agentsassemble.gui.PublicTunnelManager",
            return_value=MagicMock(),
        ), patch(
            "agentsassemble.gui.LiveAgentSessionRunMonitor",
            return_value=MagicMock(),
        ), patch(
            "agentsassemble.gui.ThreadingHTTPServer",
            return_value=server,
        ), patch("sys.stdout", stdout):
            serve_gui(
                output_root=Path("/tmp/gui-room-repository-test"),
                room_repository_backend="postgresql",
                room_postgres_dsn_env="ROOM_DATABASE_SECRET",
            )

        from_environment.assert_called_once_with(
            backend="postgresql",
            postgres_dsn_env="ROOM_DATABASE_SECRET",
        )
        build_database.assert_called_once_with(settings)
        build_repository.assert_called_once_with(
            Path("/tmp/gui-room-repository-test"),
            settings,
            postgres_database=application_database,
        )
        build_invites.assert_called_once_with(
            Path("/tmp/gui-room-repository-test"),
            settings,
            postgres_database=application_database,
        )
        build_identities.assert_called_once_with(
            Path("/tmp/gui-room-repository-test"),
            settings,
            postgres_database=application_database,
        )
        self.assertIs(
            make_handler.call_args.kwargs["room_repository_override"],
            repository,
        )
        self.assertIn("Room repository: postgresql", stdout.getvalue())
        self.assertNotIn("secret-user", stdout.getvalue())
        self.assertNotIn("secret-pass", stdout.getvalue())
        repository.close.assert_called_once_with()
        identity_repository.close.assert_called_once_with()
        application_database.close.assert_called_once_with()

    def test_gui_closes_repository_when_later_startup_fails(self) -> None:
        repository = MagicMock()
        invite_repository = MemoryInviteSessionRepository()
        invite_repository.close = MagicMock()  # type: ignore[method-assign]
        identity_repository = MagicMock()
        identity_repository.list_rooms.return_value = []
        settings = RoomRepositorySettings(backend="sqlite")

        with patch(
            "agentsassemble.gui.RoomRepositorySettings.from_environment",
            return_value=settings,
        ), patch(
            "agentsassemble.gui.build_room_repository",
            return_value=repository,
        ), patch(
            "agentsassemble.gui.build_invite_session_repository",
            return_value=invite_repository,
        ), patch(
            "agentsassemble.gui.build_identity_repository",
            return_value=identity_repository,
        ), patch(
            "agentsassemble.gui.LiveAgentProcessSupervisor",
            side_effect=RuntimeError("startup failed"),
        ):
            with self.assertRaisesRegex(RuntimeError, "startup failed"):
                serve_gui(output_root=Path("/tmp/gui-room-repository-startup-failure"))

        repository.close.assert_called_once_with()
        invite_repository.close.assert_called_once_with()
        identity_repository.close.assert_not_called()

    def test_service_build_failure_does_not_close_borrowed_resources(self) -> None:
        repository = MagicMock()
        process_supervisor = MagicMock()

        with tempfile.TemporaryDirectory() as temp_dir, patch(
            "agentsassemble.gui.LiveAgentSessionRunController",
            side_effect=RuntimeError("session controller failed"),
        ):
            with self.assertRaisesRegex(RuntimeError, "session controller failed"):
                _build_gui_application_services(
                    Path(temp_dir),
                    room_repository_override=repository,
                    process_supervisor=process_supervisor,
                )

        repository.close.assert_not_called()
        process_supervisor.close.assert_not_called()

    def test_handler_shares_one_explicit_repository_with_controller_and_routes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            server_root = root / "server"
            repository_root = root / "repository"
            repository = RoomStore(repository_root)

            handler = _make_handler(
                server_root,
                room_repository_override=repository,
            )
            try:
                self.assertIs(handler.room_repository, repository)
                self.assertIs(handler.gui_deps.rooms, repository)
                self.assertIs(handler.room_realtime_controller.store, repository)
                self.assertIs(handler.application_services.room_repository, repository)
                self.assertIs(
                    handler.gui_deps.identities,
                    handler.application_services.identity_backend,
                )
                self.assertIs(
                    handler.gui_deps.media,
                    handler.application_services.media_store,
                )
                self.assertIs(
                    handler.gui_deps.pairing,
                    handler.application_services.pairing,
                )
                self.assertIs(
                    handler.gui_deps.sessions,
                    handler.application_services.sessions,
                )
                self.assertFalse((server_root / "rooms" / "rooms.sqlite3").exists())
            finally:
                handler.room_realtime_controller.close()

    def test_handler_uses_override_controller_repository(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            repository = RoomStore(root / "repository")
            access = memory_room_access_services()
            controller = RoomRealtimeController(
                root / "server",
                **access.controller_kwargs(),
                providers=[],
                repository=repository,
            )
            try:
                handler = _make_handler(
                    root / "server",
                    room_realtime_controller_override=controller,
                    invite_repository_override=access.repository,
                    public_invite_runtime_override=access.public_invite,
                )

                self.assertIs(handler.room_repository, repository)
                self.assertIs(handler.gui_deps.rooms, repository)
            finally:
                controller.close()

    def test_handler_rejects_mixed_controller_and_route_repositories(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            controller_repository = RoomStore(root / "controller")
            route_repository = RoomStore(root / "routes")
            access = memory_room_access_services()
            controller = RoomRealtimeController(
                root / "server",
                **access.controller_kwargs(),
                providers=[],
                repository=controller_repository,
            )
            try:
                with self.assertRaisesRegex(ValueError, "share one room repository"):
                    _make_handler(
                        root / "server",
                        room_realtime_controller_override=controller,
                        room_repository_override=route_repository,
                        invite_repository_override=access.repository,
                        public_invite_runtime_override=access.public_invite,
                    )
            finally:
                controller.close()

    def test_agent_session_payload_writes_only_to_injected_repository(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            output_root = root / "unused-default"
            repository = RoomStore(root / "repository")

            result = create_agent_session_payload(
                output_root,
                {
                    "room_id": "repository-room",
                    "agent_id": "agent-one",
                    "display_name": "Agent One",
                    "provider_kind": "codex",
                },
                repository=repository,
            )

            self.assertEqual(result["session"]["participant_id"], "agent-one")
            self.assertTrue(repository.session("repository-room", "agent-one"))
            self.assertFalse((output_root / "rooms" / "rooms.sqlite3").exists())


if __name__ == "__main__":
    unittest.main()
