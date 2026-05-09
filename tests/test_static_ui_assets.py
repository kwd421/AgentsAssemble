import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
STATIC_DIR = ROOT / "agentsassemble" / "static"
PYPROJECT = ROOT / "pyproject.toml"


def static_js() -> str:
    return "\n".join(path.read_text() for path in sorted(STATIC_DIR.glob("*.js")))


def static_css() -> str:
    return "\n".join(path.read_text() for path in sorted(STATIC_DIR.glob("*.css")))


class StaticUiAssetTests(unittest.TestCase):
    def test_responsive_layout_hooks_are_present(self):
        css = static_css()

        self.assertIn("@media (max-width: 860px)", css)
        self.assertIn("@media (max-width: 560px)", css)
        self.assertIn("overflow-x: auto;", css)
        self.assertIn("contain: layout paint;", css)
        self.assertIn(":focus-visible", css)
        self.assertIn("width: min(28vw, 130px);", css)
        self.assertIn("#run-demo {\n    grid-column: 1 / -1;", css)

    def test_app_status_region_reports_demo_state(self):
        html = (STATIC_DIR / "index.html").read_text()
        script = static_js()
        css = static_css()

        self.assertIn('id="app-status"', html)
        self.assertIn('role="status"', html)
        self.assertIn('type="module"', html)
        self.assertIn('/static/base.css', html)
        self.assertIn('/static/responsive.css', html)
        self.assertIn("function showAppStatus", script)
        self.assertIn('showAppStatus("Mock Demo 실행 중"', script)
        self.assertIn('showAppStatus("Mock Demo 생성 완료"', script)
        self.assertIn(".app-status", css)

    def test_lobby_separates_stage_from_activity_feed(self):
        script = static_js()
        css = static_css()

        self.assertIn('class="lobby-summary"', script)
        self.assertIn('class="lobby-activity"', script)
        self.assertIn(".lobby-summary", css)
        self.assertIn(".lobby-activity", css)
        self.assertIn("grid-template-rows: auto minmax(0, 1fr) auto;", css)
        self.assertIn("function renderApprovedBindings", script)
        self.assertIn('aria-label="승인된 본회의 에이전트"', script)
        self.assertIn(".approved-bindings", css)

    def test_live_view_prioritizes_official_chat(self):
        script = static_js()
        css = static_css()

        self.assertIn('class="live-chat-header"', script)
        self.assertIn('class="message-list live-transcript live-chat-feed"', script)
        self.assertIn("function renderOfficialRoster", script)
        self.assertIn(".live-chat-room", css)
        self.assertIn(".live-chat-feed", css)
        self.assertIn("width: fit-content;", css)
        self.assertIn("border-radius: 16px 16px 16px 5px;", css)
        self.assertIn("grid-template-columns: minmax(0, 1fr) minmax(260px, 340px);", css)
        self.assertIn("min-height: clamp(620px, calc(100vh - 230px), 860px);", css)
        self.assertIn("이 영역의 발언은 transcript.md와 decision.md의 근거가 됩니다.", script)
        self.assertIn("isLiveTranscriptNearBottom(live)", script)
        self.assertIn("scrollLiveTranscriptToLatest(live)", script)
        self.assertIn('aria-label="공식 토론 기록"', script)
        self.assertIn('aria-live="polite"', script)
        self.assertIn(".record-badge", css)
        self.assertIn("function providerLabel", script)
        self.assertIn("function agentLabel", script)
        self.assertIn("meeting read-only", script)

    def test_board_cards_are_dynamic_and_scrollable(self):
        script = static_js()
        css = static_css()

        self.assertIn("(meeting.roles || []).map", script)
        self.assertIn('에이전트 ${(meeting.roles || []).length}', script)
        self.assertIn("grid-template-columns: repeat(auto-fit", css)
        self.assertIn("max-height: clamp(520px, 72vh, 780px);", css)

    def test_archive_surfaces_owner_and_document_type(self):
        script = static_js()
        css = static_css()

        self.assertIn("archiveOwnerLabel(state.archiveKey, payload)", script)
        self.assertIn("function archiveOwnerLabel", script)
        self.assertIn("archiveKindLabel(key)", script)
        self.assertIn("function copyTextWithTextarea", script)
        self.assertIn("return copyTextWithTextarea(content)", script)
        self.assertIn("function buildArchiveManifest", script)
        self.assertIn('class="archive-vault"', script)
        self.assertIn(".archive-vault", css)
        self.assertIn(".archive-stat", css)
        self.assertIn(".archive-list button strong", css)
        self.assertIn(".archive-list button span", css)

    def test_tabs_expose_semantic_state(self):
        html = (STATIC_DIR / "index.html").read_text()
        script = static_js()
        spec = (ROOT / "docs" / "gui-v0-spec.md").read_text()

        self.assertIn('role="tablist"', html)
        self.assertEqual(html.count('role="tab"'), 4)
        self.assertEqual(html.count('role="tabpanel"'), 4)
        self.assertIn("all four tabs", spec)
        self.assertIn("[로비] [실황] [작전판] [아카이브]", spec)
        self.assertIn('aria-selected="true"', html)
        self.assertEqual(html.count('tabindex="-1"'), 3)
        self.assertIn('tabindex="0"', html)
        self.assertIn('aria-controls="lobby"', html)
        self.assertIn('aria-labelledby="tab-lobby"', html)
        self.assertIn('tab.setAttribute("aria-selected"', script)
        self.assertIn("tab.tabIndex = isActive ? 0 : -1", script)
        self.assertIn("panel.hidden = !isActive", script)


if __name__ == "__main__":
    unittest.main()
