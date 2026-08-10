from __future__ import annotations

import json
import tempfile
import time
import unittest
from pathlib import Path

from agentsassemble.plugin.manifest import load_first_party_manifests, load_manifest
from agentsassemble.plugin.registry import PluginRegistry
from agentsassemble.plugin.settings import clean_activity_plugin
from agentsassemble.plugin.storage import PluginStorage


class PluginRimworldTests(unittest.TestCase):
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

    def test_simulation_mental_break_and_model_error_wait(self) -> None:
        from plugins.rimworld.server.sim import ColonySimulation, EXTREME_BREAK

        sim = ColonySimulation(seed=7)
        colonist = sim.colonists[0]
        colonist.mood = EXTREME_BREAK - 0.01
        colonist.hunger = 0.0
        colonist.rest = 0.0
        sim._check_mental_breaks()
        self.assertTrue(colonist.mental_break)
        sim.mark_model_error(colonist.id, "provider failed")
        self.assertTrue(colonist.waiting)
        with self.assertRaisesRegex(RuntimeError, "waiting"):
            sim.apply_act(colonist.id, "eat", {})


if __name__ == "__main__":
    unittest.main()
