import unittest

from agentsassemble.gui_legacy_live_agent_session_http import (
    LegacySessionHttpDeps,
    register_legacy_session_mutation_routes,
)
from agentsassemble.gui_router import Router


class GuiLegacyLiveAgentSessionHttpTests(unittest.TestCase):
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
