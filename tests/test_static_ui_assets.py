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


if __name__ == "__main__":
    unittest.main()
