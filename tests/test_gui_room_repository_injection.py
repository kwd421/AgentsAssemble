from __future__ import annotations

import os
import tempfile
import unittest
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from agentsassemble.agent_sessions import create_agent_session_payload
from agentsassemble.gui import _make_handler, serve_gui
from agentsassemble.room_realtime import RoomRealtimeController
from agentsassemble.room_repository_factory import (
    RoomRepositoryConfigurationError,
    RoomRepositorySettings,
)
from agentsassemble.room_store import RoomStore


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
        repository = object()
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

        with patch(
            "agentsassemble.gui.RoomRepositorySettings.from_environment",
            return_value=settings,
        ) as from_environment, patch(
            "agentsassemble.gui.build_room_repository",
            return_value=repository,
        ) as build_repository, patch(
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
        build_repository.assert_called_once_with(
            Path("/tmp/gui-room-repository-test"),
            settings,
        )
        self.assertIs(
            make_handler.call_args.kwargs["room_repository_override"],
            repository,
        )
        self.assertIn("Room repository: postgresql", stdout.getvalue())
        self.assertNotIn("secret-user", stdout.getvalue())
        self.assertNotIn("secret-pass", stdout.getvalue())

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
                self.assertFalse((server_root / "rooms" / "rooms.sqlite3").exists())
            finally:
                handler.room_realtime_controller.close()

    def test_handler_uses_override_controller_repository(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            repository = RoomStore(root / "repository")
            controller = RoomRealtimeController(root / "server", providers=[], repository=repository)
            try:
                handler = _make_handler(
                    root / "server",
                    room_realtime_controller_override=controller,
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
            controller = RoomRealtimeController(
                root / "server",
                providers=[],
                repository=controller_repository,
            )
            try:
                with self.assertRaisesRegex(ValueError, "share one room repository"):
                    _make_handler(
                        root / "server",
                        room_realtime_controller_override=controller,
                        room_repository_override=route_repository,
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
