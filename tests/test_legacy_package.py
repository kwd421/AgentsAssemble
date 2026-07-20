from __future__ import annotations

import unittest

import agentsassemble.legacy_live_agent_engagement as compatibility_engagement
import agentsassemble.legacy_live_agent_preflight as compatibility_preflight
from agentsassemble.legacy.live_agent import engagement as owned_engagement
from agentsassemble.legacy.live_agent import preflight as owned_preflight


class LegacyPackageTests(unittest.TestCase):
    def test_live_agent_engagement_root_module_exports_owned_service(self) -> None:
        self.assertIs(
            compatibility_engagement.LegacyLiveAgentEngagementService,
            owned_engagement.LegacyLiveAgentEngagementService,
        )
        self.assertIs(
            compatibility_engagement.update_live_agent_engagement_payload,
            owned_engagement.update_live_agent_engagement_payload,
        )

    def test_live_agent_preflight_root_module_exports_owned_service(self) -> None:
        self.assertIs(
            compatibility_preflight.LegacyLiveAgentPreflightService,
            owned_preflight.LegacyLiveAgentPreflightService,
        )
        self.assertIs(
            compatibility_preflight.live_agent_preflight_payload,
            owned_preflight.live_agent_preflight_payload,
        )


if __name__ == "__main__":
    unittest.main()
