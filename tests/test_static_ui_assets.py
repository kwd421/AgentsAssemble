import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
STATIC_DIR = ROOT / "agentsassemble" / "static"
PYPROJECT = ROOT / "pyproject.toml"


class StaticUiAssetTests(unittest.TestCase):
    def test_hero_asset_is_packaged_and_within_budget(self):
        hero = STATIC_DIR / "council-hero.jpg"

        self.assertTrue(hero.exists())
        self.assertLessEqual(hero.stat().st_size, 350_000)
        self.assertIn('"static/*.jpg"', PYPROJECT.read_text())

    def test_responsive_layout_hooks_are_present(self):
        css = (STATIC_DIR / "styles.css").read_text()

        self.assertIn("@media (max-width: 860px)", css)
        self.assertIn("@media (max-width: 560px)", css)
        self.assertIn("overflow-x: auto;", css)
        self.assertIn("contain: layout paint;", css)
        self.assertIn(":focus-visible", css)

    def test_lobby_separates_stage_from_activity_feed(self):
        script = (STATIC_DIR / "app.js").read_text()
        css = (STATIC_DIR / "styles.css").read_text()

        self.assertIn('class="lobby-stage"', script)
        self.assertIn('class="lobby-activity"', script)
        self.assertIn(".lobby-stage", css)
        self.assertIn(".lobby-activity", css)
        self.assertIn("grid-template-rows: auto minmax(0, 1fr) auto;", css)

    def test_live_stage_leaves_room_for_transcript(self):
        css = (STATIC_DIR / "styles.css").read_text()

        self.assertIn("min-height: clamp(480px, calc(100vh - 210px), 720px);", css)
        self.assertIn("height: clamp(280px, 38vh, 470px);", css)
        self.assertIn("font-size: clamp(30px, 4.25vw, 58px);", css)
        self.assertIn("min-height: 430px;", css)
        self.assertIn("height: clamp(220px, 34vh, 300px);", css)

    def test_board_cards_are_dynamic_and_scrollable(self):
        script = (STATIC_DIR / "app.js").read_text()
        css = (STATIC_DIR / "styles.css").read_text()

        self.assertIn("(meeting.roles || []).map", script)
        self.assertIn('에이전트 ${(meeting.roles || []).length}', script)
        self.assertIn("grid-template-columns: repeat(auto-fit", css)
        self.assertIn("max-height: clamp(520px, 72vh, 780px);", css)

    def test_archive_surfaces_owner_and_document_type(self):
        script = (STATIC_DIR / "app.js").read_text()
        css = (STATIC_DIR / "styles.css").read_text()

        self.assertIn("archiveOwnerLabel(state.archiveKey, payload)", script)
        self.assertIn("function archiveOwnerLabel", script)
        self.assertIn("archiveKindLabel(key)", script)
        self.assertIn(".archive-list button strong", css)
        self.assertIn(".archive-list button span", css)

    def test_tabs_expose_semantic_state(self):
        html = (STATIC_DIR / "index.html").read_text()
        script = (STATIC_DIR / "app.js").read_text()

        self.assertIn('role="tablist"', html)
        self.assertEqual(html.count('role="tab"'), 4)
        self.assertEqual(html.count('role="tabpanel"'), 4)
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
