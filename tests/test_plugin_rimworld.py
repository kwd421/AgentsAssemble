from __future__ import annotations

import json
import tempfile
import time
import unittest
from http import HTTPStatus
from pathlib import Path

from agentsassemble.plugin.manifest import load_first_party_manifests, load_manifest
from agentsassemble.plugin.activity_wakes import ActivityPluginWakeRouter
from agentsassemble.plugin.host_service import handle_ws_plugin_message, plugin_registry
from agentsassemble.plugin.process_host import PluginProcessHost
from agentsassemble.plugin.registry import PluginRegistry
from agentsassemble.plugin.settings import clean_activity_plugin
from agentsassemble.plugin.storage import PluginStorage
from agentsassemble.providers.room_portal import RoomPortal
from agentsassemble.web.static import ReactStaticTransport


class PluginRimworldTests(unittest.TestCase):
    def test_story_event_wakes_only_the_colonists_assigned_provider(self) -> None:
        requested: list[tuple[str, str]] = []
        router = ActivityPluginWakeRouter(
            active_plugin=lambda _room_id: "rimworld",
            provider_ids=lambda _room_id: ("agent-c", "agent-a", "agent-b"),
            request_observation=lambda room_id, agent_id: requested.append(
                (room_id, agent_id)
            ),
        )

        router.handle(
            "room-colony",
            {
                "type": "plugin.delta",
                "plugin_id": "rimworld",
                "payload": {
                    "colonists": [
                        {"id": "c1"},
                        {"id": "c2"},
                        {"id": "c3"},
                    ]
                },
                "agent_wakes": [
                    {"colonist_id": "c2", "reason": "job_completed"},
                    {"colonist_id": "c2", "reason": "social_event"},
                ],
            },
        )

        self.assertEqual(requested, [("room-colony", "agent-b")])

    def test_room_portal_exposes_and_batches_active_plugin_agent_tools(self) -> None:
        from plugins.rimworld.server.sim import ColonySimulation

        with tempfile.TemporaryDirectory() as temp_dir:
            portal = RoomPortal(Path(temp_dir) / "portal", participant_id="agent-b")
            portal.prepare()
            portal.ingest_frame(
                {
                    "room_settings": {"activity_plugin": "rimworld"},
                    "participants": [
                        {
                            "participant_id": "agent-a",
                            "participant_type": "agent",
                            "display_name": "A",
                        },
                        {
                            "participant_id": "agent-b",
                            "participant_type": "agent",
                            "display_name": "B",
                        },
                        {
                            "participant_id": "agent-c",
                            "participant_type": "agent",
                            "display_name": "C",
                        },
                    ],
                }
            )
            snapshot = ColonySimulation(seed=11).snapshot()
            portal.ingest_frame(
                {
                    "op": "event",
                    "stream": "plugin",
                    "events": [
                        {
                            "type": "plugin.snapshot",
                            "plugin_id": "rimworld",
                            "payload": snapshot,
                        }
                    ],
                }
            )
            portal.begin_observation("wake-plugin", input_up_to_seq=0)

            self.assertTrue(
                {
                    "rimworld.observe",
                    "rimworld.inspect",
                    "rimworld.act",
                    "rimworld.speak",
                }.issubset(portal.active_tool_names())
            )
            observed = portal.activity_plugin_observe()
            self.assertEqual(observed["colonist_id"], "c2")
            portal.activity_plugin_act(
                "build",
                {"kind": "bed", "x": 14, "y": 9},
            )
            portal.activity_plugin_speak("침대를 먼저 짓겠습니다.")
            batch = portal.activity_plugin_command_batch("wake-plugin")

        self.assertEqual(batch["plugin_id"], "rimworld")
        self.assertEqual(batch["revision"], str(snapshot["revision"]))
        self.assertEqual(batch["args"]["colonist_id"], "c2")
        self.assertEqual(batch["args"]["act"]["action"], "build")
        self.assertEqual(batch["args"]["speak"], "침대를 먼저 짓겠습니다.")

    def test_public_plugin_assets_do_not_expose_server_source(self) -> None:
        class Handler:
            headers: dict[str, str] = {}

            def __init__(self) -> None:
                self.file: Path | None = None
                self.status: HTTPStatus | None = None

            def _send_file(self, path: Path, *_args, **_kwargs) -> None:
                self.file = path

            def _send_error(self, status: HTTPStatus, _message: str) -> None:
                self.status = status

        transport = ReactStaticTransport(
            frontend_root=Path("missing"),
            pre_join_guide_payload=lambda _url: {},
            api_catalog_payload=lambda _url: {},
        )
        handler = Handler()

        handled = transport.dispatch_get(
            handler,
            path="/plugins/rimworld/server/main.py",
            query={},
        )

        self.assertTrue(handled)
        self.assertEqual(handler.status, HTTPStatus.NOT_FOUND)
        self.assertIsNone(handler.file)

        public_handler = Handler()
        handled = transport.dispatch_get(
            public_handler,
            path="/plugins/rimworld/web/index.html",
            query={},
        )
        self.assertTrue(handled)
        self.assertIsNone(public_handler.status)
        self.assertEqual(public_handler.file.name, "index.html")

    def test_read_only_room_identity_cannot_activate_plugin_process(self) -> None:
        try:
            with self.assertRaisesRegex(Exception, "permission"):
                handle_ws_plugin_message(
                    room_id="room-read-only",
                    identity={
                        "client_type": "browser",
                        "invite_scope": "read_only",
                        "operator": False,
                    },
                    message={"plugin_id": "rimworld", "action": "activate"},
                )
        finally:
            plugin_registry().deactivate("room-read-only", "rimworld")

    def test_first_party_manifest_loads_with_restricted_permissions(self) -> None:
        manifests = load_first_party_manifests()
        rimworld = next(item for item in manifests if item.id == "rimworld")
        self.assertEqual(rimworld.api, "agentsassemble.plugin/v1")
        self.assertEqual(
            rimworld.permissions,
            frozenset(
                {"room.read", "room.activity.write", "agent.tools", "plugin.storage"}
            ),
        )

    def test_denied_permissions_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "agentsassemble.plugin.json").write_text(
                json.dumps(
                    {
                        "api": "agentsassemble.plugin/v1",
                        "id": "evil",
                        "version": "0.0.1",
                        "display_name": "Evil",
                        "entrypoints": {"server": "s.py", "web": "w.html"},
                        "permissions": ["network", "room.read"],
                    }
                ),
                encoding="utf-8",
            )
            (root / "s.py").write_text("print('x')\n", encoding="utf-8")
            (root / "w.html").write_text("<html></html>", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "denied permissions"):
                load_manifest(root / "agentsassemble.plugin.json")

    def test_plugin_process_cannot_read_files_outside_its_package_or_storage(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            root = base / "plugin"
            root.mkdir()
            secret = base / "host-secret.txt"
            secret.write_text("must-not-leak", encoding="utf-8")
            (root / "web.html").write_text("<html></html>", encoding="utf-8")
            (root / "server.py").write_text(
                "\n".join(
                    [
                        "import json, sys",
                        "for line in sys.stdin:",
                        "    message = json.loads(line)",
                        "    if message.get('type') == 'plugin.start':",
                        "        try:",
                        f"            open({str(secret)!r}, encoding='utf-8').read()",
                        "        except PermissionError:",
                        "            print(json.dumps({'type':'plugin.error','code':'filesystem_blocked'}), flush=True)",
                        "        else:",
                        "            print(json.dumps({'type':'plugin.delta','payload':{'stolen':True}}), flush=True)",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            (root / "agentsassemble.plugin.json").write_text(
                json.dumps(
                    {
                        "api": "agentsassemble.plugin/v1",
                        "id": "isolation-probe",
                        "version": "0.0.1",
                        "entrypoints": {"server": "server.py", "web": "web.html"},
                        "permissions": ["room.read", "plugin.storage"],
                    }
                ),
                encoding="utf-8",
            )
            events: list[dict[str, object]] = []
            host = PluginProcessHost(
                manifest=load_manifest(root / "agentsassemble.plugin.json"),
                room_id="room-isolation",
                storage_dir=base / "storage",
                on_event=events.append,
            )
            try:
                host.start()
                deadline = time.monotonic() + 3
                while not events and time.monotonic() < deadline:
                    time.sleep(0.02)
                self.assertFalse(any((event.get("payload") or {}).get("stolen") for event in events))
                self.assertTrue(any(event.get("code") == "filesystem_blocked" for event in events))
            finally:
                host.stop()

    def test_activity_plugin_setting_accepts_only_known_first_party_ids(self) -> None:
        self.assertEqual(clean_activity_plugin("rimworld"), "rimworld")
        self.assertEqual(clean_activity_plugin("none"), "")
        with self.assertRaisesRegex(ValueError, "Unknown first-party"):
            clean_activity_plugin("marketplace-mod")

    def test_plugin_storage_batches_are_room_scoped_and_not_transcript(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            storage = PluginStorage(Path(temp_dir))
            storage.write_json("room-1", "rimworld", "latest", {"tick": 3})
            self.assertEqual(
                storage.read_json("room-1", "rimworld", "latest"),
                {"tick": 3},
            )
            with self.assertRaises(ValueError):
                storage.write_json("room-1", "rimworld", "../escape", {"x": 1})

    def test_plugin_process_snapshot_and_error_surface(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            events: list[dict[str, object]] = []
            registry = PluginRegistry(
                storage_root=Path(temp_dir),
                broadcast=lambda _room, envelope: events.append(envelope),
            )
            health = registry.activate("room-sim", "rimworld")
            self.assertTrue(health["running"])
            registry.request_snapshot("room-sim", "rimworld")
            deadline = time.monotonic() + 5.0
            while time.monotonic() < deadline and not any(
                item.get("type") == "plugin.snapshot" for item in events
            ):
                time.sleep(0.05)
            self.assertTrue(
                any(item.get("type") == "plugin.snapshot" for item in events),
                msg=f"events={events!r}",
            )
            # Unauthorized/unknown command should produce an explicit plugin.error.
            registry.handle_command(
                "room-sim",
                {"plugin_id": "rimworld", "command": "drop_database", "args": {}},
            )
            deadline = time.monotonic() + 5.0
            while time.monotonic() < deadline and not any(
                item.get("type") == "plugin.error" for item in events
            ):
                time.sleep(0.05)
            self.assertTrue(any(item.get("type") == "plugin.error" for item in events))
            registry.deactivate("room-sim", "rimworld")

    def test_plugin_command_requires_current_revision(self) -> None:
        from plugins.rimworld.server.main import PluginServer

        server = PluginServer()
        events: list[dict[str, object]] = []
        server._emit = lambda event: events.append(event)
        server.handle(
            {
                "type": "plugin.command",
                "id": "missing-revision",
                "command": "set_speed",
                "args": {"speed": 1},
            }
        )
        self.assertEqual(server.sim.speed, 0)
        self.assertEqual(events[-1].get("type"), "plugin.error")
        self.assertEqual(events[-1].get("code"), "revision_required")

    def test_plugin_delta_emits_need_wake_once_until_the_need_recovers(self) -> None:
        from plugins.rimworld.server.main import PluginServer

        server = PluginServer()
        events: list[dict[str, object]] = []
        server._emit = lambda event: events.append(event)
        server.sim.set_speed(1)
        server.sim.colonists[0].hunger = 0.301

        server.handle(
            {
                "type": "plugin.command",
                "id": "cross-hunger-threshold",
                "command": "step",
                "revision": str(server.sim.revision),
                "args": {"steps": 1},
            }
        )
        first_wakes = events[-1].get("agent_wakes")
        self.assertEqual(
            first_wakes,
            [{"colonist_id": "c1", "reason": "need_hunger"}],
        )

        server.handle(
            {
                "type": "plugin.command",
                "id": "remain-hungry",
                "command": "step",
                "revision": str(server.sim.revision),
                "args": {"steps": 1},
            }
        )
        self.assertNotIn("agent_wakes", events[-1])

    def test_plugin_restart_restores_the_last_accepted_state(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            first_events: list[dict[str, object]] = []
            first = PluginRegistry(
                storage_root=Path(temp_dir),
                broadcast=lambda _room_id, event: first_events.append(event),
            )
            first.activate("room-restore", "rimworld")
            deadline = time.monotonic() + 3
            while not first_events and time.monotonic() < deadline:
                time.sleep(0.02)
            first.handle_command(
                "room-restore",
                {
                    "plugin_id": "rimworld",
                    "command": "set_speed",
                    "revision": "0",
                    "args": {"speed": 1},
                },
            )
            deadline = time.monotonic() + 3
            while (
                not any((event.get("payload") or {}).get("speed") == 1 for event in first_events)
                and time.monotonic() < deadline
            ):
                time.sleep(0.02)
            first.deactivate("room-restore", "rimworld")

            restored_events: list[dict[str, object]] = []
            restored = PluginRegistry(
                storage_root=Path(temp_dir),
                broadcast=lambda _room_id, event: restored_events.append(event),
            )
            try:
                restored.activate("room-restore", "rimworld")
                deadline = time.monotonic() + 3
                while not restored_events and time.monotonic() < deadline:
                    time.sleep(0.02)
                self.assertEqual(restored_events[0]["payload"]["speed"], 1)
                self.assertEqual(restored_events[0]["payload"]["revision"], 1)
            finally:
                restored.deactivate("room-restore", "rimworld")

    def test_simulation_mental_break_and_model_error_wait(self) -> None:
        from plugins.rimworld.server.sim import (
            BREAK_LOW_MOOD_TICKS,
            ColonySimulation,
            EXTREME_BREAK,
        )

        sim = ColonySimulation(seed=7)
        colonist = sim.colonists[0]
        colonist.mood = EXTREME_BREAK - 0.01
        colonist.hunger = 0.0
        colonist.rest = 0.0
        for _ in range(BREAK_LOW_MOOD_TICKS - 1):
            sim._check_mental_breaks()
        self.assertFalse(colonist.mental_break)
        sim._check_mental_breaks()
        self.assertTrue(colonist.mental_break)
        sim.mark_model_error(colonist.id, "provider failed")
        self.assertTrue(colonist.waiting)
        with self.assertRaisesRegex(RuntimeError, "waiting"):
            sim.apply_act(colonist.id, "eat", {})


if __name__ == "__main__":
    unittest.main()
