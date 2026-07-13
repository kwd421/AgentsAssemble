from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from agentsassemble.agent_sessions import create_agent_session_payload
from agentsassemble.gui import _make_handler
from agentsassemble.room_realtime import RoomRealtimeController
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
