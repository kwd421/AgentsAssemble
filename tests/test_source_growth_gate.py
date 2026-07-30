from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from scripts.check_source_growth import (
    SourceGrowthPolicy,
    collect_source_line_counts,
    source_growth_violations,
)


class SourceGrowthGateTests(unittest.TestCase):
    def test_growth_and_new_large_files_fail_at_their_public_policy_boundary(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            package = root / "agentsassemble"
            package.mkdir()
            (package / "owned.py").write_text("x\n" * 6, encoding="utf-8")
            (package / "new_large.py").write_text("x\n" * 5, encoding="utf-8")
            (package / "small.py").write_text("x\n" * 4, encoding="utf-8")
            policy = SourceGrowthPolicy(
                new_file_line_limit=4,
                file_limits={"agentsassemble/owned.py": 5},
            )

            violations = source_growth_violations(
                collect_source_line_counts(root),
                policy,
            )

        self.assertEqual(
            violations,
            (
                "agentsassemble/new_large.py: 5 lines exceeds the unowned-file "
                "limit of 4",
                "agentsassemble/owned.py: 6 lines exceeds its recorded ceiling of 5",
            ),
        )

    def test_deleted_recorded_file_must_be_removed_from_the_policy(self) -> None:
        policy = SourceGrowthPolicy(
            new_file_line_limit=4,
            file_limits={"agentsassemble/removed.py": 5},
        )

        violations = source_growth_violations({}, policy)

        self.assertEqual(
            violations,
            ("agentsassemble/removed.py: recorded source file is missing",),
        )


if __name__ == "__main__":
    unittest.main()
