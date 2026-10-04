from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from agentsassemble.gui import _make_handler
from agentsassemble.room.realtime import RoomRealtimeController
from tests.room_realtime_test_support import memory_room_access_services
from agentsassemble.room_store import RoomStore


class GuiRoomRepositoryInjectionTests(unittest.TestCase):
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

if __name__ == "__main__":
    unittest.main()
