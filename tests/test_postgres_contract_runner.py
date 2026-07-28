from __future__ import annotations

import os
import subprocess
import sys
import textwrap
import unittest
from collections.abc import Callable
from io import StringIO
from pathlib import Path

from tests.run_postgres_contracts import (
    POSTGRES_REQUIRED_MODULES,
    POSTGRES_TEST_DSN_ENV,
    missing_postgres_contract_requirements,
    run_postgres_contract_suite,
)


def _suite_with(
    test_method: Callable[[unittest.TestCase], None],
) -> unittest.TestSuite:
    class ContractCase(unittest.TestCase):
        def runTest(self) -> None:
            test_method(self)

    return unittest.TestSuite((ContractCase(),))


class PostgresContractRunnerTests(unittest.TestCase):
    def test_mandatory_suite_contains_every_postgres_gated_test(self) -> None:
        environment = dict(os.environ)
        environment.pop(POSTGRES_TEST_DSN_ENV, None)
        probe = subprocess.run(
            [
                sys.executable,
                "-c",
                textwrap.dedent(
                    """
                    import unittest
                    from tests.run_postgres_contracts import load_postgres_contract_suite

                    def flatten(suite):
                        for item in suite:
                            if isinstance(item, unittest.TestSuite):
                                yield from flatten(item)
                            else:
                                yield item

                    def postgres_gated(test):
                        method = getattr(test, test._testMethodName)
                        reasons = (
                            getattr(test.__class__, "__unittest_skip_why__", ""),
                            getattr(method, "__unittest_skip_why__", ""),
                        )
                        return any("postgres" in str(reason).lower() for reason in reasons)

                    discovered = unittest.defaultTestLoader.discover(
                        "tests",
                        top_level_dir=".",
                    )
                    gated_ids = {
                        test.id() for test in flatten(discovered) if postgres_gated(test)
                    }
                    mandatory_ids = {
                        test.id() for test in flatten(load_postgres_contract_suite())
                    }
                    if not gated_ids:
                        raise SystemExit("No PostgreSQL-gated tests were discovered.")
                    missing = sorted(gated_ids - mandatory_ids)
                    if missing:
                        raise SystemExit(
                            "Mandatory PostgreSQL suite omitted: " + ", ".join(missing)
                        )
                    """
                ),
            ],
            cwd=Path(__file__).resolve().parents[1],
            env=environment,
            capture_output=True,
            text=True,
        )

        self.assertEqual(probe.returncode, 0, probe.stdout + probe.stderr)

    def test_preflight_requires_dsn_and_every_postgres_module(self) -> None:
        missing = missing_postgres_contract_requirements(
            {},
            find_module=lambda _name: None,
        )

        self.assertEqual(
            missing,
            (POSTGRES_TEST_DSN_ENV, *POSTGRES_REQUIRED_MODULES),
        )

    def test_preflight_never_returns_the_dsn_value(self) -> None:
        secret_dsn = "postgresql://secret-user:secret-password@example.invalid/rooms"

        missing = missing_postgres_contract_requirements(
            {POSTGRES_TEST_DSN_ENV: secret_dsn},
            find_module=lambda _name: object(),
        )

        self.assertEqual(missing, ())
        self.assertNotIn(secret_dsn, repr(missing))

    def test_strict_runner_accepts_a_successful_unskipped_suite(self) -> None:
        def passing(_self) -> None:
            return None

        output = StringIO()

        exit_code = run_postgres_contract_suite(_suite_with(passing), stream=output)

        self.assertEqual(exit_code, 0)
        self.assertIn("OK", output.getvalue())

    def test_strict_runner_rejects_any_skipped_contract(self) -> None:
        def skipped(self) -> None:
            self.skipTest("database unavailable")

        output = StringIO()

        exit_code = run_postgres_contract_suite(_suite_with(skipped), stream=output)

        self.assertEqual(exit_code, 1)
        self.assertIn("requires every selected contract to run", output.getvalue())
        self.assertIn("database unavailable", output.getvalue())

    def test_strict_runner_rejects_an_empty_suite(self) -> None:
        output = StringIO()

        exit_code = run_postgres_contract_suite(unittest.TestSuite(), stream=output)

        self.assertEqual(exit_code, 1)
        self.assertIn("did not discover any tests", output.getvalue())

    def test_strict_runner_preserves_test_failures(self) -> None:
        def failing(self) -> None:
            self.fail("contract failed")

        output = StringIO()

        exit_code = run_postgres_contract_suite(_suite_with(failing), stream=output)

        self.assertEqual(exit_code, 1)
        self.assertIn("contract failed", output.getvalue())

if __name__ == "__main__":
    unittest.main()
