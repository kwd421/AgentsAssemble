from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from agentsassemble.web.frontend_runtime import (
    frontend_build_version,
    frontend_dist_status,
    materialize_frontend_release,
)


class FrontendRuntimeTests(unittest.TestCase):
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

    def test_build_identity_changes_when_served_entrypoint_changes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            assets = root / "assets"
            assets.mkdir()
            (assets / "first.js").write_text("export const build = 1", encoding="utf-8")
            (assets / "second.js").write_text("export const build = 2", encoding="utf-8")
            index = root / "index.html"
            index.write_text(
                '<script type="module" src="/assets/first.js"></script>',
                encoding="utf-8",
            )
            first_version = frontend_build_version(root)

            index.write_text(
                '<script type="module" src="/assets/second.js"></script>',
                encoding="utf-8",
            )
            second_version = frontend_build_version(root)

        self.assertNotEqual(first_version, second_version)
        self.assertNotEqual(first_version, "unavailable")
        self.assertNotEqual(second_version, "unavailable")

    def test_running_release_keeps_its_assets_when_source_build_changes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "dist"
            assets = source / "assets"
            assets.mkdir(parents=True)
            (assets / "first.js").write_text("export const build = 1", encoding="utf-8")
            index = source / "index.html"
            index.write_text(
                '<script type="module" src="/assets/first.js"></script>',
                encoding="utf-8",
            )
            releases = root / "releases"
            first_release = materialize_frontend_release(
                source,
                release_root=releases,
            )

            (assets / "second.js").write_text("export const build = 2", encoding="utf-8")
            index.write_text(
                '<script type="module" src="/assets/second.js"></script>',
                encoding="utf-8",
            )
            second_release = materialize_frontend_release(
                source,
                release_root=releases,
            )

            self.assertNotEqual(first_release, second_release)
            self.assertTrue((first_release / "assets" / "first.js").is_file())
            self.assertFalse((first_release / "assets" / "second.js").exists())
            self.assertTrue((second_release / "assets" / "second.js").is_file())


if __name__ == "__main__":
    unittest.main()
