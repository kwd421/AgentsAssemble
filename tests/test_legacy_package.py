from __future__ import annotations

import unittest

import agentsassemble.legacy_live_agent_engagement as compatibility_engagement
import agentsassemble.legacy_live_agent_preflight as compatibility_preflight
import agentsassemble.legacy_live_agent_probe as compatibility_probe
from agentsassemble.legacy.live_agent import engagement as owned_engagement
from agentsassemble.legacy.live_agent import preflight as owned_preflight
from agentsassemble.legacy.live_agent import probe as owned_probe


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

    def test_live_agent_probe_root_module_exports_owned_service(self) -> None:
        for name in (
            "LegacyLiveAgentProbeService",
            "live_agent_probe_payload",
            "probe_timeout_seconds",
        ):
            with self.subTest(name=name):
                self.assertIs(
                    getattr(compatibility_probe, name),
                    getattr(owned_probe, name),
                )


if __name__ == "__main__":
    unittest.main()
