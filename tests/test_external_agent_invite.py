"""An agent already running on the joiner's machine may be invited as itself."""
from __future__ import annotations

import unittest

from agentsassemble.admission.invite_service import InviteApplicationService
from agentsassemble.persistence.local.admission.repository import (
    MemoryInviteSessionRepository,
)
from agentsassemble.providers.launch_specs import EXTERNAL_AGENT_PROVIDER_KIND


class ExternalAgentInviteTests(unittest.TestCase):
    def _service(self) -> InviteApplicationService:
        return InviteApplicationService(
            MemoryInviteSessionRepository(),
            public_url=lambda: "http://127.0.0.1:8765",
        )

    def _create(self, service: InviteApplicationService, provider_kind: str) -> dict:
        return service.create(
            room_url="http://127.0.0.1:8765",
            meeting_id="room-a",
            agent_id="outside-agent",
            display_name="Outside Agent",
            client_type="agent_bridge",
            provider_kind=provider_kind,
        )

    def test_an_external_agent_may_be_invited_without_a_local_definition(self) -> None:
        # This server never launches such an agent, so requiring one of its own
        # CLI definitions locked out every agent that was already running.
        invite = self._create(self._service(), EXTERNAL_AGENT_PROVIDER_KIND)

        self.assertEqual(invite["provider_kind"], EXTERNAL_AGENT_PROVIDER_KIND)
        self.assertEqual(invite["client_type"], "agent_bridge")

    def test_a_known_provider_still_resolves_through_its_definition(self) -> None:
        invite = self._create(self._service(), "codex_live_session")

        self.assertEqual(invite["provider_kind"], "codex_live_session")

    def test_an_unknown_provider_is_still_refused(self) -> None:
        # Opening the gate for external agents must not open it for typos or
        # for a kind this server has no idea how to treat.
        with self.assertRaises(ValueError):
            self._create(self._service(), "totally-made-up")


if __name__ == "__main__":
    unittest.main()
