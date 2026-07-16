from __future__ import annotations

import unittest
from pathlib import Path

from scripts.check_package_architecture import (
    ROOT_COMPATIBILITY_SHIMS,
    current_top_level_modules,
    load_root_baseline,
    unexpected_top_level_modules,
    validate_compatibility_shims,
)


ROOT = Path(__file__).resolve().parents[1]


class PackageArchitectureGateTests(unittest.TestCase):
    def test_current_root_has_no_unowned_product_modules(self) -> None:
        unexpected = unexpected_top_level_modules(
            current_top_level_modules(ROOT),
            load_root_baseline(ROOT),
        )

        self.assertEqual(unexpected, ())

    def test_gate_rejects_a_synthetic_new_flat_module(self) -> None:
        unexpected = unexpected_top_level_modules(
            {"__init__.py", "cli.py", "gui.py", "new_product_module.py"},
            set(),
        )

        self.assertEqual(unexpected, ("new_product_module.py",))

    def test_root_compatibility_shims_have_replacement_and_removal_metadata(self) -> None:
        validate_compatibility_shims()
        baseline = load_root_baseline(ROOT)

        self.assertFalse(set(ROOT_COMPATIBILITY_SHIMS) & set(baseline))

    def test_package_map_tracks_every_current_top_level_module(self) -> None:
        package_map = (ROOT / "docs" / "product" / "PACKAGE_MAP.md").read_text(
            encoding="utf-8"
        )

        for filename in current_top_level_modules(ROOT):
            with self.subTest(filename=filename):
                self.assertIn(f"`agentsassemble/{filename}`", package_map)


if __name__ == "__main__":
    unittest.main()
