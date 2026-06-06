import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read_frontend(path: str) -> str:
    return (ROOT / "frontend" / "src" / path).read_text(encoding="utf-8")


class FrontendAgentCreateFlowTests(unittest.TestCase):
    def test_api_exposes_frontend_agent_create_endpoints(self):
        source = read_frontend("api.ts")

        self.assertIn("LiveAgentCreateProvider", source)
        self.assertIn('"antigravity"', source)
        self.assertIn("model_options", source)
        self.assertIn("effort_options", source)
        self.assertIn("speed_options", source)
        self.assertIn("model_id", source)
        self.assertIn("effort", source)
        self.assertIn("speed", source)
        self.assertIn("fetchLiveAgentCreateOptions", source)
        self.assertIn('"/api/live-agent-create/options"', source)
        self.assertIn("checkFrontendLiveAgent", source)
        self.assertIn('"/api/live-agent-create/check"', source)
        self.assertIn("createFrontendLiveAgent", source)
        self.assertIn('"/api/live-agent-create"', source)
        self.assertIn("startFrontendLiveAgentLogin", source)
        self.assertIn('"/api/live-agent-create/login"', source)
        self.assertIn("workspace_path", source)

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

    def test_agent_create_modal_has_model_effort_and_speed_controls(self):
        source = read_frontend("views/components/AgentCreateModal.tsx")

        self.assertIn("modelId", source)
        self.assertIn("effort", source)
        self.assertIn("speed", source)
        self.assertIn("model_options", source)
        self.assertIn("effort_options", source)
        self.assertIn("speed_options", source)
        self.assertIn("<select", source)
        self.assertIn("모델", source)
        self.assertIn("추론 강도", source)
        self.assertIn("응답 속도", source)

    def test_agent_create_modal_can_start_provider_login_from_auth_action(self):
        source = read_frontend("views/components/AgentCreateModal.tsx")

        self.assertIn("authAction", source)
        self.assertIn("handleLogin", source)
        self.assertIn("startFrontendLiveAgentLogin", source)
        self.assertIn("로그인 창을 열었습니다", source)


if __name__ == "__main__":
    unittest.main()
