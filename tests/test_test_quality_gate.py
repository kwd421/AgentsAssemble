from __future__ import annotations

import tempfile
import textwrap
import unittest
from pathlib import Path

from scripts.check_test_quality import (
    analyze_python_test_source,
    changed_python_test_lines,
    load_exceptions,
)


def _rules(source: str) -> set[str]:
    return {
        violation.rule
        for violation in analyze_python_test_source(
            "tests/test_example.py",
            textwrap.dedent(source),
        )
    }


class TestQualityGateTests(unittest.TestCase):
    def test_behavioral_state_and_failure_oracles_are_accepted(self) -> None:
        rules = _rules(
            """
            import unittest

            class ExampleTests(unittest.TestCase):
                def test_persists_then_rejects_duplicate(self):
                    store = FakeStore()
                    created = store.create("room-a")
                    self.assertEqual(created["status"], "created")
                    self.assertEqual(store.read("room-a")["status"], "created")
                    with self.assertRaises(ValueError):
                        store.create("room-a")
            """
        )

        self.assertEqual(rules, set())

    def test_source_text_private_patch_mock_only_and_missing_oracle_are_rejected(self) -> None:
        rules = _rules(
            """
            import unittest
            from pathlib import Path
            from unittest.mock import patch

            class ExampleTests(unittest.TestCase):
                def test_source_contains_function(self):
                    source = Path("agentsassemble/service.py").read_text()
                    self.assertIn("def publish", source)

                def test_private_call(self):
                    with patch("agentsassemble.service._publish") as publish:
                        run()
                    publish.assert_called_once()

                def test_no_oracle(self):
                    run()
            """
        )

        self.assertEqual(
            rules,
            {"mock_only", "no_oracle", "private_patch", "source_text"},
        )

    def test_helper_assertion_and_checked_subprocess_are_real_oracles(self) -> None:
        rules = _rules(
            """
            import subprocess
            import unittest

            class ExampleTests(unittest.TestCase):
                def _expect_rejected(self):
                    with self.assertRaises(ValueError):
                        run()

                def test_delegated_failure(self):
                    self._expect_rejected()

                def test_external_runtime(self):
                    subprocess.run(["provider", "--smoke"], check=True)
            """
        )

        self.assertEqual(rules, set())

    def test_exact_korean_copy_and_symbol_identity_are_rejected(self) -> None:
        rules = _rules(
            """
            import unittest

            class ExampleTests(unittest.TestCase):
                def test_label(self):
                    self.assertEqual("대기 중", label_for("idle"))

                def test_export(self):
                    self.assertIs(compatibility.publish, current.publish)
            """
        )

        self.assertEqual(rules, {"exact_ui_copy", "symbol_only"})

    def test_runtime_state_boolean_is_not_mistaken_for_symbol_existence(self) -> None:
        rules = _rules(
            """
            import unittest

            class ExampleTests(unittest.TestCase):
                def test_state(self):
                    response = execute_workflow()
                    self.assertTrue(response.accepted)
            """
        )

        self.assertEqual(rules, set())

    def test_direct_and_split_path_source_reads_are_rejected(self) -> None:
        rules = _rules(
            """
            import unittest
            from pathlib import Path

            class ExampleTests(unittest.TestCase):
                def test_direct_source_read(self):
                    self.assertIn(
                        "publish",
                        (Path("frontend") / "src" / "room.ts").read_text(),
                    )
            """
        )

        self.assertEqual(rules, {"source_text"})

    def test_exception_requires_a_specific_reason_and_suppresses_only_named_rule(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "exceptions.toml"
            path.write_text(
                textwrap.dedent(
                    """
                    [[allow]]
                    test_id = "tests.test_example.ExampleTests.test_export"
                    rules = ["symbol_only"]
                    reason = "Protects the published compatibility import used by plugins."
                    """
                ),
                encoding="utf-8",
            )

            exceptions = load_exceptions(path)
            violations = analyze_python_test_source(
                "tests/test_example.py",
                textwrap.dedent(
                    """
                    import unittest

                    class ExampleTests(unittest.TestCase):
                        def test_export(self):
                            self.assertIs(compatibility.publish, current.publish)
                    """
                ),
                exceptions=exceptions,
            )

        self.assertEqual(violations, ())

    def test_exception_rejects_unknown_rules(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "exceptions.toml"
            path.write_text(
                textwrap.dedent(
                    """
                    [[allow]]
                    test_id = "tests.test_example.ExampleTests.test_export"
                    rules = ["anything_goes"]
                    reason = "This reason is deliberately long enough to reach validation."
                    """
                ),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "Unknown test-quality"):
                load_exceptions(path)

    def test_git_diff_selects_only_changed_test_function_lines(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "tests").mkdir()
            (root / "tests" / "test_sample.py").write_text(
                "def test_original():\n    assert True\n",
                encoding="utf-8",
            )
            self._git(root, "init")
            self._git(root, "config", "user.email", "tests@example.invalid")
            self._git(root, "config", "user.name", "Tests")
            self._git(root, "add", "tests/test_sample.py")
            self._git(root, "commit", "-m", "baseline")
            base = self._git(root, "rev-parse", "HEAD").strip()
            (root / "tests" / "test_sample.py").write_text(
                "def test_original():\n    assert True\n\n"
                "def test_added():\n    assert result() == 'created'\n",
                encoding="utf-8",
            )

            changed = changed_python_test_lines(root, base=base)

        self.assertEqual(changed, {"tests/test_sample.py": frozenset({3, 4, 5})})

    def _git(self, root: Path, *arguments: str) -> str:
        import subprocess

        return subprocess.run(
            ["git", *arguments],
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
        ).stdout


if __name__ == "__main__":
    unittest.main()
