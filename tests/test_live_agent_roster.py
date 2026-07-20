import unittest

from agentsassemble.legacy.live_agent.runtime.quota import quota_viewer_for_host, quota_viewer_for_session
from agentsassemble.legacy.live_agent.runtime.roster import safe_live_agent_roster_payload


class LiveAgentRosterPayloadTests(unittest.TestCase):
    def test_safe_roster_preserves_runtime_comparison_join_semantics_override(self):
        safe = safe_live_agent_roster_payload(
            {
                "agents": [
                    {
                        "agent_id": "codex-runtime",
                        "display_name": "Codex Runtime",
                        "provider_kind": "codex",
                        "connection_kind": "live_session",
                        "join_semantics": "runtime_managed_room_turn",
                    },
                    {
                        "agent_id": "codex-tool-loop",
                        "display_name": "Codex Tool Loop",
                        "provider_kind": "codex",
                        "connection_kind": "live_session",
                        "join_semantics": "mcp_tool_loop",
                    },
                    {
                        "agent_id": "codex-unverified-loop",
                        "display_name": "Codex Unverified Loop",
                        "provider_kind": "codex",
                        "connection_kind": "live_session",
                        "join_semantics": "provider_tool_loop",
                    },
                ]
            }
        )

        agents = {agent["agent_id"]: agent for agent in safe["agents"]}
        self.assertEqual(agents["codex-runtime"]["join_semantics"], "runtime_managed_room_turn")
        self.assertEqual(agents["codex-runtime"]["execution_mode"], "runtime_managed_room_turn")
        self.assertEqual(agents["codex-runtime"]["runner_residency"], "resident_room_runtime")
        self.assertEqual(agents["codex-runtime"]["provider_residency"], "per_turn_exec_resume")
        self.assertFalse(agents["codex-runtime"]["provider_persistent"])
        self.assertEqual(agents["codex-tool-loop"]["join_semantics"], "mcp_tool_loop")
        self.assertEqual(agents["codex-tool-loop"]["execution_mode"], "provider_tool_loop")
        self.assertEqual(agents["codex-tool-loop"]["runner_residency"], "provider_owned_tool_loop")
        self.assertTrue(agents["codex-tool-loop"]["provider_persistent"])
        self.assertEqual(agents["codex-unverified-loop"]["execution_mode"], "tool_loop_unverified")
        self.assertFalse(agents["codex-unverified-loop"]["provider_persistent"])
        self.assertIn("not been verified", agents["codex-unverified-loop"]["tool_loop_unverified_reason"])

    def test_safe_roster_payload_limits_guest_quota_to_session_owner_and_companion(self):
        payload = {
            "agents": [
                {
                    "agent_id": "host-codex",
                    "display_name": "Host Codex",
                    "provider_kind": "codex",
                    "connection_kind": "live_session",
                    "quota_5h": "host-5h",
                    "quota_1w": "host-1w",
                    "quota_state": "low",
                    "quota_windows": [{"label": "5-hour", "percent": 80}],
                },
                {
                    "agent_id": "guest-a",
                    "display_name": "Guest A",
                    "provider_kind": "manual",
                    "connection_kind": "native_remote_room_client",
                    "quota_5h": "guest-5h",
                    "quota_1w": "guest-1w",
                    "quota_state": "ok",
                    "quota_windows": [{"label": "5-hour", "percent": 20}],
                },
                {
                    "agent_id": "guest-a-ai",
                    "display_name": "Guest A AI",
                    "provider_kind": "claude_code",
                    "connection_kind": "native_remote_room_client",
                    "quota_5h": "guest-ai-5h",
                    "quota_1w": "guest-ai-1w",
                    "quota_state": "low",
                    "quota_windows": [{"label": "5-hour", "percent": 35}],
                },
            ],
        }

        safe = safe_live_agent_roster_payload(
            payload,
            quota_viewer=quota_viewer_for_session({"agent_id": "guest-a"}),
        )

        agents = {agent["agent_id"]: agent for agent in safe["agents"]}
        self.assertNotIn("quota_5h", agents["host-codex"])
        self.assertNotIn("quota_1w", agents["host-codex"])
        self.assertNotIn("quota_state", agents["host-codex"])
        self.assertNotIn("quota_windows", agents["host-codex"])
        self.assertEqual(agents["guest-a"]["quota_5h"], "guest-5h")
        self.assertEqual(agents["guest-a"]["quota_1w"], "guest-1w")
        self.assertEqual(agents["guest-a"]["quota_state"], "ok")
        self.assertEqual(agents["guest-a"]["quota_windows"], [{"label": "5-hour", "percent": 20}])
        self.assertEqual(agents["guest-a-ai"]["quota_5h"], "guest-ai-5h")
        self.assertEqual(agents["guest-a-ai"]["quota_1w"], "guest-ai-1w")
        self.assertEqual(agents["guest-a-ai"]["quota_state"], "low")
        self.assertEqual(agents["guest-a-ai"]["quota_windows"], [{"label": "5-hour", "percent": 35}])

    def test_safe_roster_payload_hides_remote_owner_quota_from_host(self):
        payload = {
            "agents": [
                {
                    "agent_id": "host-codex",
                    "display_name": "Host Codex",
                    "provider_kind": "codex",
                    "connection_kind": "local_cli",
                    "quota_5h": "host-5h",
                    "quota_1w": "host-1w",
                    "quota_state": "ok",
                    "quota_windows": [{"label": "5-hour", "percent": 10}],
                },
                {
                    "agent_id": "friend-native",
                    "display_name": "Friend Native",
                    "provider_kind": "claude_code",
                    "connection_kind": "native_remote_room_client",
                    "quota_5h": "friend-5h",
                    "quota_1w": "friend-1w",
                    "quota_state": "low",
                    "quota_windows": [{"label": "5-hour", "percent": 90}],
                },
                {
                    "agent_id": "friend-bridge",
                    "display_name": "Friend Bridge",
                    "provider_kind": "remote_http_bridge",
                    "connection_kind": "remote_bridge",
                    "quota_5h": "bridge-5h",
                    "quota_1w": "bridge-1w",
                    "quota_state": "exhausted",
                    "quota_windows": [{"label": "5-hour", "percent": 99}],
                },
            ],
        }

        safe = safe_live_agent_roster_payload(payload, quota_viewer=quota_viewer_for_host())

        agents = {agent["agent_id"]: agent for agent in safe["agents"]}
        self.assertEqual(agents["host-codex"]["quota_5h"], "host-5h")
        self.assertEqual(agents["host-codex"]["quota_1w"], "host-1w")
        self.assertEqual(agents["host-codex"]["quota_state"], "ok")
        self.assertEqual(agents["host-codex"]["quota_windows"], [{"label": "5-hour", "percent": 10}])
        for agent_id in ("friend-native", "friend-bridge"):
            self.assertNotIn("quota_5h", agents[agent_id])
            self.assertNotIn("quota_1w", agents[agent_id])
            self.assertNotIn("quota_state", agents[agent_id])
            self.assertNotIn("quota_windows", agents[agent_id])


if __name__ == "__main__":
    unittest.main()
