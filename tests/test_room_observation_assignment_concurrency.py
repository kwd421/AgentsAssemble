from __future__ import annotations

import tempfile
import threading
import unittest
from pathlib import Path

from agentsassemble.persistence.local.room.repository import RoomStore
from agentsassemble.providers.launch_specs import NativeCliProviderSpec
from agentsassemble.room.realtime import RoomRealtimeController
from tests.room_realtime_test_support import FakeBridgeManager, memory_room_access_services


HOST = {
    "agent_id": "operator-local",
    "display_name": "Host",
    "participant_type": "human",
    "client_type": "browser",
    "invite_scope": "read_write",
    "meeting_id": "general",
    "operator": True,
}


class StaticProviderCatalog:
    def subscribe(self, _callback):
        return lambda: None

    def snapshot(self, *args, **kwargs):
        del args, kwargs
        return {"status": "ready", "catalog_revision": "test", "providers": []}


class ConcurrentTurnAssignmentStore(RoomStore):
    """Hold the first busy write so a competing assignment can reach it."""

    def __init__(self, output_root):
        super().__init__(output_root)
        self.first_assignment_entered = threading.Event()
        self.second_assignment_entered = threading.Event()
        self.release_first_assignment = threading.Event()
        self._assignment_write_count = 0
        self._assignment_write_lock = threading.Lock()

    def update_session_fields(self, room_id, session_id, **updates):
        if updates.get("runtime_status") == "busy" and updates.get("active_turn_id"):
            with self._assignment_write_lock:
                self._assignment_write_count += 1
                write_count = self._assignment_write_count
            if write_count == 1:
                self.first_assignment_entered.set()
                self.release_first_assignment.wait(timeout=1.0)
            elif write_count == 2:
                self.second_assignment_entered.set()
        return super().update_session_fields(room_id, session_id, **updates)


class RoomObservationAssignmentConcurrencyTests(unittest.TestCase):
    def test_competing_ambient_and_room_check_assign_one_durable_turn(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            store = ConcurrentTurnAssignmentStore(root)
            controller = RoomRealtimeController(
                root,
                **memory_room_access_services().controller_kwargs(),
                repository=store,
                providers=[
                    NativeCliProviderSpec(
                        agent_id="codex",
                        display_name="Codex",
                        command=("codex",),
                        cwd=".",
                    )
                ],
                bridge_manager=FakeBridgeManager(),
                provider_catalog=StaticProviderCatalog(),
                external_stop_timeout_seconds=0.2,
            )
            errors: list[BaseException] = []
            try:
                controller.connect(HOST)
                store.update_session_fields(
                    "general", "codex", bridge_handle_id="handle-codex"
                )
                identity = {
                    "agent_id": "codex",
                    "display_name": "Codex",
                    "participant_type": "agent",
                    "client_type": "agent_bridge",
                    "invite_scope": "read_write",
                    "meeting_id": "general",
                    "session_id": "codex",
                    "provider_kind": "codex_live_session",
                    "operator": False,
                }
                channel = controller.connect(identity)
                channel.subscribe({"room_events"})
                controller.handle_command(
                    identity,
                    {
                        "op": "command",
                        "request_id": "concurrent-ready",
                        "action": "bridge.ready",
                        "payload": {
                            "bridge_launch_id": "handle-codex",
                            "pid": 808,
                            "running": True,
                            "transport": "pty",
                            "provider_session_active": True,
                            "started_at": None,
                            "is_one_shot": False,
                        },
                    },
                )
                store.update_room_settings("general", {"conversation_mode": "ambient"})
                channel.drain()

                def run(command):
                    try:
                        controller.handle_command(*command)
                    except BaseException as error:
                        errors.append(error)

                ambient = threading.Thread(
                    target=run,
                    args=((HOST, {
                        "op": "command",
                        "request_id": "concurrent-ambient",
                        "action": "message.send",
                        "payload": {"content": "raid warning"},
                    }),),
                )
                room_check = threading.Thread(
                    target=run,
                    args=((identity, {
                        "op": "command",
                        "request_id": "concurrent-room-check",
                        "action": "room.check",
                        "payload": {},
                    }),),
                )
                ambient.start()
                self.assertTrue(store.first_assignment_entered.wait(timeout=1.0))
                room_check.start()
                store.second_assignment_entered.wait(timeout=0.2)
                store.release_first_assignment.set()
                ambient.join(timeout=1.0)
                room_check.join(timeout=1.0)

                started = [
                    event
                    for event in store.read_events("general")
                    if event.get("type") == "turn_started"
                    and event.get("participant_id") == "codex"
                ]
                wakes = [
                    message
                    for message in channel.drain()
                    if message.get("op") == "room.wake"
                ]
                session = store.session("general", "codex")

                self.assertFalse(ambient.is_alive())
                self.assertFalse(room_check.is_alive())
                self.assertEqual(errors, [])
                self.assertEqual(len(started), 1)
                self.assertEqual(len(wakes), 1)
                self.assertEqual(session["active_turn_id"], started[0]["turn_id"])
                self.assertEqual(wakes[0]["turn_id"], started[0]["turn_id"])
            finally:
                store.release_first_assignment.set()
                controller.close()


if __name__ == "__main__":
    unittest.main()
