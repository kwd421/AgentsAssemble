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

    def test_korean_protocol_data_and_mixed_behavior_are_not_ui_copy_tests(self) -> None:
        rules = _rules(
            """
            import unittest

            class ExampleTests(unittest.TestCase):
                def test_round_trip(self):
                    stored = store_message("자동 응답")
                    self.assertEqual(stored["content"], "자동 응답")
                    self.assertEqual(read_message(stored["id"])["id"], stored["id"])

                def test_label_mapping_also_enforces_safe_state(self):
                    result = summarize_state("waiting")
                    self.assertEqual(result["label"], "응답 대기")
                    self.assertEqual(result["pending_count"], 2)
            """
        )

        self.assertEqual(rules, set())

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

    def test_source_path_alias_is_rejected(self) -> None:
        rules = _rules(
            """
            import unittest
            from pathlib import Path

            class ExampleTests(unittest.TestCase):
                def test_aliased_source_read(self):
                    target = Path("agentsassemble") / "service.py"
                    source = target.read_text()
                    self.assertIn("publish", source)
            """
        )

        self.assertEqual(rules, {"source_text"})

    def test_tautological_assertions_are_rejected(self) -> None:
        rules = _rules(
            """
            import unittest

            class ExampleTests(unittest.TestCase):
                def test_constant_truth(self):
                    self.assertTrue(True)

                def test_same_value(self):
                    result = execute_workflow()
                    self.assertEqual(result, result)
            """
        )

        self.assertEqual(rules, {"tautological"})

    def test_mock_only_oracle_is_rejected_through_aliases_and_helpers(self) -> None:
        rules = _rules(
            """
            import unittest

            class ExampleTests(unittest.TestCase):
                def _assert_published(self):
                    assertion = self.publisher.assert_called_once_with
                    assertion("room-a")

                def test_publish(self):
                    run()
                    self._assert_published()
            """
        )

        self.assertEqual(rules, {"mock_only"})

    def test_mock_observation_fields_are_not_behavioral_oracles(self) -> None:
        rules = _rules(
            """
            import unittest

            class ExampleTests(unittest.TestCase):
                def test_publish(self):
                    run()
                    self.assertEqual(self.publisher.call_args.args[0], "room-a")
            """
        )

        self.assertEqual(rules, {"mock_only"})

    def test_bare_mock_observation_is_not_a_behavioral_oracle(self) -> None:
        rules = _rules(
            """
            import unittest

            class ExampleTests(unittest.TestCase):
                def test_publish(self):
                    run()
                    assert self.publisher.called
            """
        )

        self.assertEqual(rules, {"mock_only"})

    def test_private_patch_is_rejected_through_import_and_target_aliases(self) -> None:
        rules = _rules(
            """
            import unittest
            from unittest.mock import patch as replace

            class ExampleTests(unittest.TestCase):
                def test_private_call(self):
                    target = "agentsassemble.service._publish"
                    with replace(target):
                        run()
                    self.assertEqual(read_state(), "unchanged")
            """
        )

        self.assertEqual(rules, {"private_patch"})

    def test_module_helper_private_patch_is_part_of_the_test_boundary(self) -> None:
        rules = _rules(
            """
            import unittest
            from unittest.mock import patch

            def exercise_private_path():
                with patch("agentsassemble.service._publish"):
                    run()

            class ExampleTests(unittest.TestCase):
                def test_publish(self):
                    exercise_private_path()
                    self.assertEqual(read_state(), "unchanged")
            """
        )

        self.assertEqual(rules, {"private_patch"})

    def test_class_setup_private_patch_is_part_of_each_test_boundary(self) -> None:
        rules = _rules(
            """
            import unittest
            from unittest.mock import patch

            class ExampleTests(unittest.TestCase):
                def setUp(self):
                    self.patcher = patch("agentsassemble.service._publish")
                    self.patcher.start()

                def test_publish(self):
                    self.assertEqual(read_state(), "unchanged")
            """
        )

        self.assertEqual(rules, {"private_patch"})

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

    def test_git_diff_audits_the_whole_test_file_when_a_helper_changes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "tests").mkdir()
            (root / "tests" / "test_sample.py").write_text(
                "def helper():\n    return 'before'\n\n"
                "def test_original():\n    assert result() == 'created'\n",
                encoding="utf-8",
            )
            self._git(root, "init")
            self._git(root, "config", "user.email", "tests@example.invalid")
            self._git(root, "config", "user.name", "Tests")
            self._git(root, "add", "tests/test_sample.py")
            self._git(root, "commit", "-m", "baseline")
            base = self._git(root, "rev-parse", "HEAD").strip()
            (root / "tests" / "test_sample.py").write_text(
                "def helper():\n    return 'after'\n\n"
                "def test_original():\n    assert result() == 'created'\n",
                encoding="utf-8",
            )

            changed = changed_python_test_lines(root, base=base)

        self.assertEqual(changed, {"tests/test_sample.py": None})

    def test_git_diff_selects_support_contracts_and_deletion_only_changes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "tests").mkdir()
            contract = root / "tests" / "repository_contract.py"
            removed = root / "tests" / "test_removed.py"
            contract.write_text(
                "class RepositoryContract:\n"
                "    def test_round_trip(self):\n"
                "        assert read(write('value')) == 'value'\n",
                encoding="utf-8",
            )
            removed.write_text(
                "def test_removed():\n    assert workflow().status == 'done'\n",
                encoding="utf-8",
            )
            self._git(root, "init")
            self._git(root, "config", "user.email", "tests@example.invalid")
            self._git(root, "config", "user.name", "Tests")
            self._git(root, "add", "tests")
            self._git(root, "commit", "-m", "baseline")
            base = self._git(root, "rev-parse", "HEAD").strip()
            contract.write_text(
                "class RepositoryContract:\n"
                "    def test_round_trip(self):\n"
                "        assert read(write('next')) == 'next'\n",
                encoding="utf-8",
            )
            removed.unlink()

            changed = changed_python_test_lines(root, base=base)

        self.assertEqual(
            changed,
            {
                "tests/repository_contract.py": None,
                "tests/test_removed.py": frozenset(),
            },
        )

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
