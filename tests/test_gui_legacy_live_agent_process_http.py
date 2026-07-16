import unittest

from agentsassemble.gui_legacy_live_agent_process_http import (
    LegacyProcessHttpDeps,
    register_legacy_process_mutation_routes,
)
from agentsassemble.web.router import Router


class GuiLegacyLiveAgentProcessHttpTests(unittest.TestCase):
    def test_process_registrar_owns_exact_and_dynamic_mutations(self) -> None:
        router = Router()
        register_legacy_process_mutation_routes(
            router,
            deps=LegacyProcessHttpDeps(
                service=object(),  # type: ignore[arg-type]
                read_operation_payload=lambda _ctx, _operation, _target: {},
                default_server_url=lambda _ctx: "http://127.0.0.1:8765",
            ),
        )

        self.assertEqual(
            set(router.routes()),
            {
                ("POST", "/api/live-agent-processes/start"),
                ("POST", "/api/live-agent-processes/stop-running"),
            },
        )
        self.assertEqual(
            set(router.dynamic_routes()),
            {
                ("POST", "/api/live-agent-processes/{group_id}/stop"),
                ("POST", "/api/live-agent-processes/{group_id}/restart"),
                ("POST", "/api/live-agent-processes/{group_id}/recover"),
            },
        )


if __name__ == "__main__":
    unittest.main()
