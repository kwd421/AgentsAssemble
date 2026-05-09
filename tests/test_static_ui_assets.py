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
