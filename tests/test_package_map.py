from __future__ import annotations

import unittest
from pathlib import Path

from scripts.generate_package_map import build_package_map, load_package_graph


ROOT = Path(__file__).resolve().parents[1]


class PackageMapTests(unittest.TestCase):
    def test_committed_package_map_matches_ast_inventory(self) -> None:
        expected = build_package_map(ROOT)
        actual = (ROOT / "docs" / "product" / "PACKAGE_MAP.md").read_text(
            encoding="utf-8"
        )

        self.assertEqual(actual, expected)

    def test_existing_admission_module_collision_is_visible_before_package_move(self) -> None:
        package_map = build_package_map(ROOT)

        self.assertIn("`agentsassemble.admission`", package_map)
        self.assertIn("`agentsassemble/admission.py`", package_map)
        self.assertIn("legacy", package_map)

    def test_nested_persistence_modules_keep_persistence_ownership(self) -> None:
        graph = load_package_graph(ROOT)
        package_map = build_package_map(ROOT)

        self.assertEqual(
            graph.domains[
                "agentsassemble.persistence.postgres.application_database"
            ],
            "persistence",
        )
        self.assertEqual(
            graph.domains["agentsassemble.persistence.postgres.connection_pool"],
            "persistence",
        )
        self.assertIn(
            "`agentsassemble.persistence.postgres.application_database`",
            package_map,
        )
        self.assertIn("`persistence/postgres/` | in-target-package", package_map)


if __name__ == "__main__":
    unittest.main()
