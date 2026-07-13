import io
import json
import unittest
from contextlib import redirect_stdout

from agentsassemble.cli_legacy_live_agent_format import (
    _format_live_agent_readiness,
    _format_live_agent_real_session_smoke,
    _format_live_agent_session_smoke,
    _print_live_agent_process_payload,
)


class LegacyLiveAgentCliFormatTests(unittest.TestCase):
    def test_empty_process_payload_has_stable_human_line(self) -> None:
        output = io.StringIO()

        with redirect_stdout(output):
            _print_live_agent_process_payload({"groups": []}, as_json=False)

        self.assertEqual(output.getvalue(), "no live-agent process groups\n")

    def test_process_json_is_stable_pretty_json_with_terminal_newline(self) -> None:
        payload = {"groups": [{"group_id": "group-a", "config_path": "[redacted]"}]}
        output = io.StringIO()

        with redirect_stdout(output):
            _print_live_agent_process_payload(payload, as_json=True)

        self.assertEqual(json.loads(output.getvalue()), payload)
        self.assertTrue(output.getvalue().endswith("\n"))
        self.assertIn("[redacted]", output.getvalue())

    def test_smoke_formatters_preserve_failed_and_degraded_statuses(self) -> None:
        session = _format_live_agent_session_smoke(
            {"status": "failed", "meeting_id": "room-a", "group_id": "group-a"}
        )
        real = _format_live_agent_real_session_smoke(
            {"status": "degraded", "meeting_id": "room-b", "group_id": "group-b"}
        )

        self.assertIn("resident session smoke failed", session)
        self.assertIn("real resident session smoke degraded", real)

    def test_readiness_format_preserves_attention_and_probe_summary(self) -> None:
        rendered = _format_live_agent_readiness(
            {
                "status": "degraded",
                "health": {
                    "status": "degraded",
                    "agents": {"attention": ["agent-a:offline"]},
                    "processes": {"attention": []},
                    "connections": {"attention": []},
                    "sessions": {"attention": []},
                },
                "smoke": {"status": "failed", "group_id": "group-a"},
                "probes": [{"agent_id": "agent-a", "status": "timeout"}],
            }
        )

        self.assertIn("readiness: degraded", rendered)
        self.assertIn("agent attention: agent-a:offline", rendered)
        self.assertIn("probes: agent-a timeout", rendered)


if __name__ == "__main__":
    unittest.main()
