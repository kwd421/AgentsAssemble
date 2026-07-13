import unittest
from pathlib import Path

from tests.frontend_api_source import api_barrel_source, api_module_source


ROOT = Path(__file__).resolve().parents[1]


def read_frontend(path: str) -> str:
    return (ROOT / "frontend" / "src" / path).read_text(encoding="utf-8")


class FrontendAgentCreateFlowTests(unittest.TestCase):
    def test_api_exposes_canonical_provider_profile_and_credential_contract(self):
        source = api_barrel_source()
        agent_session_source = api_module_source("agentSessions")

        self.assertIn("reasoningEffort", agent_session_source)
        self.assertIn("serviceTier", agent_session_source)
        self.assertIn("permissionMode", agent_session_source)
        self.assertIn("fetchDeepSeekCredentialStatus", source)
        self.assertIn('"/api/provider-credentials/deepseek"', source)
        self.assertNotIn("speed_options", source)

    def test_friends_view_has_agent_add_entry_between_search_and_friend_add(self):
        source = read_frontend("views/FriendsView.tsx")

        self.assertIn("onStartAddAgent", source)
        self.assertIn("에이전트 추가", source)
        self.assertLess(source.index('placeholder="검색하기"'), source.index("에이전트 추가"))
        self.assertLess(source.index("에이전트 추가"), source.index("<h2>친구 추가하기</h2>"))

    def test_room_connection_panel_removes_top_summary_and_keeps_member_list(self):
        source = read_frontend("views/components/RoomConnectionPanel.tsx")

        self.assertNotIn('aria-label="방 연결 정보"', source)
        self.assertNotIn("dc-room-connection-grid", source)
        self.assertIn("onStartAddAgent", source)
        self.assertIn("에이전트 추가", source)
        self.assertIn("<MemberList", source)

    def test_agent_create_modal_does_not_restore_legacy_login_or_poll_controls(self):
        source = read_frontend("views/components/AgentCreateModal.tsx")

        self.assertNotIn("authAction", source)
        self.assertNotIn("startFrontendLiveAgentLogin", source)
        self.assertNotIn("pollInterval", source)
        self.assertIn('type="password"', source)
        self.assertIn("setDeepSeekKey(\"\")", source)


if __name__ == "__main__":
    unittest.main()
