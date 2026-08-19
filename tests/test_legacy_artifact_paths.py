from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from agentsassemble.legacy.meeting.support.artifacts import (
    _safe_artifact_component,
    _safe_artifact_path,
    write_research,
)


class LegacyArtifactPathTests(unittest.TestCase):
    def test_write_research_accepts_safe_role_id(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            meeting_dir = Path(temp_dir) / "meetings" / "meeting-1"
            meeting_dir.mkdir(parents=True)
            write_research(
                meeting_dir,
                {
                    "role_id": "reviewer_1",
                    "display_name": "Reviewer",
                    "research_depth": {},
                    "queries": [],
                    "sources": [],
                    "summary": "No findings.",
                    "confidence": "medium",
                    "uncertainty": "None.",
                },
            )

            research_dir = meeting_dir / "private_research" / "reviewer_1"
            self.assertTrue((research_dir / "research.json").is_file())
            self.assertTrue((research_dir / "research.md").is_file())

    def test_unsafe_role_ids_are_rejected_before_writing(self) -> None:
        unsafe_values = (
            "",
            ".",
            "..",
            "../escape",
            "nested/path",
            "nested\\path",
            "/absolute",
            "C:drive",
            " padded",
            "trailing ",
            "control\x00value",
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            meeting_dir = Path(temp_dir) / "meetings" / "meeting-1"
            meeting_dir.mkdir(parents=True)

            for value in unsafe_values:
                with self.subTest(value=value):
                    with self.assertRaises(ValueError):
                        write_research(
                            meeting_dir,
                            {"role_id": value},
                        )

            self.assertFalse((Path(temp_dir) / "escape").exists())

    def test_component_validation_rejects_windows_reserved_names(self) -> None:
        for value in ("CON", "nul", "LPT1"):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    _safe_artifact_component(value, field="role_id")

    @unittest.skipIf(
        not hasattr(os, "symlink"),
        "symbolic links are unavailable",
    )
    def test_containment_rejects_symlink_escape(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            meeting_dir = root / "meeting"
            outside_dir = root / "outside"
            meeting_dir.mkdir()
            outside_dir.mkdir()
            link = meeting_dir / "private_research"
            try:
                link.symlink_to(outside_dir, target_is_directory=True)
            except OSError as error:
                self.skipTest(f"symbolic links are unavailable: {error}")

            with self.assertRaises(ValueError):
                _safe_artifact_path(
                    meeting_dir,
                    "private_research",
                    "reviewer",
                )


if __name__ == "__main__":
    unittest.main()
