import unittest

from agentsassemble.gui_legacy_live_agent_session_http import (
    register_legacy_session_mutation_routes as compatibility_register,
)
from agentsassemble.legacy.live_agent.http.session import (
    LegacySessionHttpDeps,
    register_legacy_session_mutation_routes,
)
from agentsassemble.web.router import Router


class GuiLegacyLiveAgentSessionHttpTests(unittest.TestCase):
    def test_root_module_exports_owned_registrar(self) -> None:
        self.assertIs(compatibility_register, register_legacy_session_mutation_routes)

    def test_session_registrar_owns_all_group_and_agent_mutation_routes(self) -> None:
        router = Router()
        register_legacy_session_mutation_routes(
            router,
            deps=LegacySessionHttpDeps(
                service=object(),  # type: ignore[arg-type]
                read_operation_payload=lambda _ctx, _operation: {},
                default_server_url=lambda _ctx: "http://127.0.0.1:8765",
            ),
        )

        self.assertEqual(
            set(router.routes()),
            {
                ("POST", "/api/live-agent-sessions/start"),
                ("POST", "/api/live-agent-sessions/ensure"),
                ("POST", "/api/live-agent-sessions/resume"),
                ("POST", "/api/live-agent-sessions/check"),
                ("POST", "/api/live-agent-sessions/restart"),
                ("POST", "/api/live-agent-sessions/recover"),
                ("POST", "/api/live-agent-sessions/stop"),
                ("POST", "/api/live-agent-sessions/resume-agent"),
                ("POST", "/api/live-agent-sessions/agent-timing"),
                ("POST", "/api/live-agent-sessions/agent-options"),
                ("POST", "/api/live-agent-sessions/stop-agent"),
            },
        )


if __name__ == "__main__":
    unittest.main()
