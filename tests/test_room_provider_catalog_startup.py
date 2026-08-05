from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path

from agentsassemble.providers.capabilities import ProviderCapabilityCatalog
from agentsassemble.room.realtime import RoomRealtimeController
from tests.room_realtime_test_support import memory_room_access_services


HOST = {
    "agent_id": "operator-local",
    "display_name": "Host",
    "participant_type": "human",
    "client_type": "browser",
    "invite_scope": "read_write",
    "meeting_id": "general",
    "operator": True,
}


class RoomProviderCatalogStartupTests(unittest.TestCase):
    def test_room_server_makes_provider_catalog_ready_without_manual_refresh(self) -> None:
        catalog = ProviderCapabilityCatalog(
            runner=lambda _command, _timeout: (1, "", "unsupported"),
            resolver=lambda _executable: None,
            remote_model_discovery=lambda _profile, _api_key: [],
        )
        room_access = memory_room_access_services()
        with tempfile.TemporaryDirectory() as temp_dir:
            controller = RoomRealtimeController(
                Path(temp_dir),
                **room_access.controller_kwargs(),
                provider_catalog=catalog,
            )
            try:
                deadline = time.monotonic() + 2.0
                status = ""
                while time.monotonic() < deadline:
                    status = str(controller.snapshot(HOST)["provider_catalog"]["status"])
                    if status == "ready":
                        break
                    time.sleep(0.01)

                self.assertEqual(status, "ready")
            finally:
                controller.close()


if __name__ == "__main__":
    unittest.main()
