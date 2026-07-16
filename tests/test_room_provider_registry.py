from __future__ import annotations

import threading
import unittest

from agentsassemble.providers.launch_specs import NativeCliProviderSpec
from agentsassemble.room.errors import RoomCommandRejected
from agentsassemble.room.provider_registry import RoomProviderRegistry


def _spec(agent_id: str, *, display_name: str | None = None) -> NativeCliProviderSpec:
    return NativeCliProviderSpec(
        agent_id=agent_id,
        display_name=display_name or agent_id,
        command=("provider",),
        provider_kind="test_provider",
    )


class RoomProviderRegistryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.registry = RoomProviderRegistry(
            lock=threading.RLock(),
            default_room_id="general",
        )

    def test_provider_specs_are_isolated_by_room(self) -> None:
        self.registry.register("general", _spec("shared", display_name="General"))
        self.registry.register("other", _spec("shared", display_name="Other"))

        self.assertEqual(self.registry.provider("general", "shared").display_name, "General")
        self.assertEqual(self.registry.provider("other", "shared").display_name, "Other")

    def test_providers_returns_a_snapshot(self) -> None:
        self.registry.register("general", _spec("codex"))

        snapshot = self.registry.providers("general")
        snapshot.clear()

        self.assertTrue(self.registry.contains("general", "codex"))

    def test_display_name_update_preserves_the_rest_of_the_spec(self) -> None:
        original = _spec("codex", display_name="Codex")
        self.registry.register("general", original)

        self.assertTrue(self.registry.update_display_name("general", "codex", "Luna"))
        updated = self.registry.provider("general", "codex")

        self.assertEqual(updated.display_name, "Luna")
        self.assertEqual(updated.command, original.command)
        self.assertEqual(updated.provider_kind, original.provider_kind)

    def test_remove_and_remove_room_drop_only_the_requested_scope(self) -> None:
        self.registry.register("general", _spec("codex"))
        self.registry.register("general", _spec("grok"))
        self.registry.register("other", _spec("codex"))

        self.registry.remove("general", "codex")
        self.assertFalse(self.registry.contains("general", "codex"))
        self.assertTrue(self.registry.contains("general", "grok"))
        self.assertTrue(self.registry.contains("other", "codex"))

        self.registry.remove_room("general")
        self.assertEqual(self.registry.providers("general"), {})
        self.assertTrue(self.registry.contains("other", "codex"))

    def test_unknown_provider_uses_the_canonical_not_found_error(self) -> None:
        with self.assertRaises(RoomCommandRejected) as raised:
            self.registry.provider("general", "missing")

        self.assertEqual(raised.exception.code, "not_found")

    def test_empty_lookup_preserves_the_controller_compatibility_behavior(self) -> None:
        self.assertEqual(self.registry.providers(""), {})
        self.assertFalse(self.registry.contains("", "codex"))
        with self.assertRaises(RoomCommandRejected) as raised:
            self.registry.provider("", "")

        self.assertEqual(raised.exception.code, "not_found")

    def test_provider_agents_returns_a_stable_snapshot(self) -> None:
        self.registry.register("general", _spec("codex"))
        self.registry.register("other", _spec("grok"))

        self.assertCountEqual(
            self.registry.provider_agents(),
            (("general", "codex"), ("other", "grok")),
        )


if __name__ == "__main__":
    unittest.main()
