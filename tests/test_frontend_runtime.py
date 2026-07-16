from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from agentsassemble.web.frontend_runtime import (
    default_frontend_dist_root,
    frontend_dist_status,
)


class FrontendRuntimeTests(unittest.TestCase):
    def test_default_root_still_points_to_repository_frontend_dist(self) -> None:
        expected = Path(__file__).resolve().parents[1] / "frontend" / "dist"

        self.assertEqual(default_frontend_dist_root(), expected)

    def test_missing_build_reports_missing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            status = frontend_dist_status(Path(temp_dir) / "dist")

        self.assertEqual(status.build_status, "missing")
        self.assertFalse(status.static_available)

    def test_complete_build_requires_every_referenced_asset(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            assets = root / "assets"
            assets.mkdir()
            (assets / "app.js").write_text("console.log('ok')", encoding="utf-8")
            (assets / "app.css").write_text("body {}", encoding="utf-8")
            (root / "index.html").write_text(
                (
                    '<link href="/assets/app.css?version=1" rel="stylesheet">'
                    '<script src="/assets/app.js#entry"></script>'
                ),
                encoding="utf-8",
            )

            status = frontend_dist_status(root)

        self.assertEqual(status.build_status, "available")
        self.assertTrue(status.static_available)

    def test_missing_or_unsafe_asset_reference_reports_incomplete(self) -> None:
        for reference in ("missing.js", "%2E%2E/secret.js"):
            with self.subTest(reference=reference):
                with tempfile.TemporaryDirectory() as temp_dir:
                    root = Path(temp_dir)
                    (root / "assets").mkdir()
                    (root / "index.html").write_text(
                        f'<script src="/assets/{reference}"></script>',
                        encoding="utf-8",
                    )

                    status = frontend_dist_status(root)

                self.assertEqual(status.build_status, "incomplete")
                self.assertFalse(status.static_available)


if __name__ == "__main__":
    unittest.main()
