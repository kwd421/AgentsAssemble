from __future__ import annotations

import unittest
from unittest.mock import patch

from agentsassemble.legacy.gui_hooks import register_legacy_gui_routes
from agentsassemble.legacy.runtime_policy import (
    UNSAFE_LEGACY_MUTATIONS_ENV,
    quarantined_legacy_router,
    unsafe_legacy_mutations_enabled,
)
from agentsassemble.web.router import Router


def _handler(*_args: object, **_kwargs: object) -> None:
    return None


class LegacyRoutePolicyTests(unittest.TestCase):
    def test_default_policy_registers_reads_and_drops_mutations(self) -> None:
        router = Router()
        legacy = quarantined_legacy_router(router, environ={})

        legacy.get("/legacy/read")(_handler)
        legacy.post("/legacy/write")(_handler)
        legacy.delete("/legacy/delete")(_handler)
        legacy.get_dynamic("/legacy/items/{item_id}")(_handler)
        legacy.post_dynamic("/legacy/items/{item_id}")(_handler)
        legacy.add("PATCH", "/legacy/patch", _handler)

        self.assertEqual(router.routes(), [("GET", "/legacy/read")])
        self.assertEqual(
            router.dynamic_routes(),
            [("GET", "/legacy/items/{item_id}")],
        )
        self.assertEqual(
            legacy.blocked_routes(),
            [
                ("POST", "/legacy/write"),
                ("DELETE", "/legacy/delete"),
                ("POST", "/legacy/items/{item_id}"),
                ("PATCH", "/legacy/patch"),
            ],
        )

    def test_emergency_opt_in_restores_legacy_mutation_registration(self) -> None:
        router = Router()
        legacy = quarantined_legacy_router(
            router,
            environ={UNSAFE_LEGACY_MUTATIONS_ENV: "1"},
        )

        legacy.get("/legacy/read")(_handler)
        legacy.post("/legacy/write")(_handler)
        legacy.delete("/legacy/delete")(_handler)
        legacy.post_dynamic("/legacy/items/{item_id}")(_handler)

        self.assertEqual(
            router.routes(),
            [
                ("DELETE", "/legacy/delete"),
                ("GET", "/legacy/read"),
                ("POST", "/legacy/write"),
            ],
        )
        self.assertEqual(
            router.dynamic_routes(),
            [("POST", "/legacy/items/{item_id}")],
        )
        self.assertEqual(legacy.blocked_routes(), [])

    def test_only_exact_one_enables_unsafe_legacy_mutations(self) -> None:
        self.assertFalse(unsafe_legacy_mutations_enabled({}))
        self.assertFalse(
            unsafe_legacy_mutations_enabled(
                {UNSAFE_LEGACY_MUTATIONS_ENV: "true"}
            )
        )
        self.assertTrue(
            unsafe_legacy_mutations_enabled(
                {UNSAFE_LEGACY_MUTATIONS_ENV: "1"}
            )
        )

    def test_gui_composition_preserves_canonical_room_mutations_only(self) -> None:
        router = Router()

        class LegacyApplication:
            def register_meeting_routes(self, legacy: Router) -> None:
                legacy.get("/legacy/meetings")(_handler)
                legacy.post("/legacy/meetings")(_handler)

            def register_live_agent_routes(self, legacy: Router) -> None:
                legacy.get("/legacy/agents")(_handler)
                legacy.post("/legacy/agents/start")(_handler)

        def register_room_routes(current: Router) -> None:
            current.get("/api/rooms")(_handler)
            current.post("/api/rooms")(_handler)
            current.delete("/api/rooms/{room_id}")(_handler)

        def register_flow_routes(
            legacy: Router,
            **_kwargs: object,
        ) -> None:
            legacy.get("/legacy/flow")(_handler)
            legacy.post("/legacy/flow/start")(_handler)

        with (
            patch(
                "agentsassemble.legacy.gui_hooks.register_room_routes",
                register_room_routes,
            ),
            patch(
                "agentsassemble.legacy.gui_hooks.register_live_agent_flow_routes",
                register_flow_routes,
            ),
        ):
            register_legacy_gui_routes(
                router,
                legacy_application=LegacyApplication(),
                flow=object(),
                read_operation_payload=lambda *_args, **_kwargs: None,
                record_operation=lambda *_args, **_kwargs: None,
            )

        self.assertEqual(
            set(router.routes()),
            {
                ("DELETE", "/api/rooms/{room_id}"),
                ("GET", "/api/rooms"),
                ("GET", "/legacy/agents"),
                ("GET", "/legacy/flow"),
                ("GET", "/legacy/meetings"),
                ("POST", "/api/rooms"),
            },
        )
        self.assertNotIn(("POST", "/legacy/meetings"), router.routes())
        self.assertNotIn(("POST", "/legacy/agents/start"), router.routes())
        self.assertNotIn(("POST", "/legacy/flow/start"), router.routes())


if __name__ == "__main__":
    unittest.main()
