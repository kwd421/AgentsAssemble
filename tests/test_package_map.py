from __future__ import annotations

import unittest
from pathlib import Path

from scripts.generate_package_map import build_package_map


ROOT = Path(__file__).resolve().parents[1]


class PackageMapTests(unittest.TestCase):
    def test_committed_package_map_matches_ast_inventory(self) -> None:
        expected = build_package_map(ROOT)
        actual = (ROOT / "docs" / "product" / "PACKAGE_MAP.md").read_text(
            encoding="utf-8"
        )

        self.assertEqual(actual, expected)


if __name__ == "__main__":
    unittest.main()
