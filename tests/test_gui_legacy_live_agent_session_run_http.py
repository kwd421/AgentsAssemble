import unittest

from agentsassemble.gui_legacy_live_agent_session_run_http import (
    register_legacy_session_run_basic_routes as compatibility_register,
)
from agentsassemble.legacy.live_agent.http.session_run import (
    LegacySessionRunHttpDeps,
    register_legacy_session_run_basic_routes,
)
from agentsassemble.web.router import Router


class GuiLegacyLiveAgentSessionRunHttpTests(unittest.TestCase):
    def test_root_module_exports_owned_registrar(self) -> None:
        self.assertIs(compatibility_register, register_legacy_session_run_basic_routes)

    def test_registrar_owns_basic_and_retry_now_routes(self) -> None:
        router = Router()
        register_legacy_session_run_basic_routes(
            router,
            deps=LegacySessionRunHttpDeps(
                service=object(),  # type: ignore[arg-type]
                read_operation_payload=lambda _ctx, _operation, _target: {},
                default_server_url=lambda _ctx: "http://127.0.0.1:8765",
            ),
        )

        self.assertEqual(
            set(router.routes()),
            {
                ("POST", "/api/live-agent-session-runs/pause"),
                ("POST", "/api/live-agent-session-runs/resume"),
                ("POST", "/api/live-agent-session-runs/stop"),
                ("POST", "/api/live-agent-session-runs/retry-now"),
                ("POST", "/api/live-agent-session-runs/ensure"),
            },
        )
        self.assertEqual(
            set(router.dynamic_routes()),
            {
                ("POST", "/api/live-agent-session-runs/{run_id}/pause"),
                ("POST", "/api/live-agent-session-runs/{run_id}/resume"),
                ("POST", "/api/live-agent-session-runs/{run_id}/stop"),
                ("POST", "/api/live-agent-session-runs/{run_id}/retry-now"),
            },
        )


if __name__ == "__main__":
    unittest.main()
