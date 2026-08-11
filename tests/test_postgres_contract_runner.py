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
    POSTGRES_TEST_DSN_ENV,
    run_postgres_contract_suite,
)


def _suite_with(test_method: Callable[[unittest.TestCase], None]) -> unittest.TestSuite:
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

    def test_preflight_fails_closed_without_requirements_and_redacts_dsn(self) -> None:
        project_root = Path(__file__).resolve().parents[1]
        missing_dsn_environment = dict(os.environ)
        missing_dsn_environment.pop(POSTGRES_TEST_DSN_ENV, None)
        missing_dsn = subprocess.run(
            [sys.executable, "-S", "-m", "tests.run_postgres_contracts"],
            cwd=project_root,
            env=missing_dsn_environment,
            capture_output=True,
            text=True,
        )

        self.assertEqual(missing_dsn.returncode, 2)
        self.assertIn(POSTGRES_TEST_DSN_ENV, missing_dsn.stderr)

        secret_dsn = "postgresql://secret-user:secret-password@example.invalid/rooms"
        missing_drivers_environment = {
            **missing_dsn_environment,
            POSTGRES_TEST_DSN_ENV: secret_dsn,
        }
        missing_drivers = subprocess.run(
            [sys.executable, "-S", "-m", "tests.run_postgres_contracts"],
            cwd=project_root,
            env=missing_drivers_environment,
            capture_output=True,
            text=True,
        )

        self.assertEqual(missing_drivers.returncode, 2)
        for module_name in ("alembic", "psycopg", "psycopg_pool", "sqlalchemy"):
            self.assertIn(module_name, missing_drivers.stderr)
        self.assertNotIn(secret_dsn, missing_drivers.stderr)

    def test_strict_runner_rejects_skipped_empty_and_failing_suites(self) -> None:
        def passing(_self) -> None:
            return None

        def skipped(self) -> None:
            self.skipTest("database unavailable")

        def failing(self) -> None:
            self.fail("contract failed")

        cases = (
            ("passing", _suite_with(passing), 0, "OK"),
            ("skipped", _suite_with(skipped), 1, "database unavailable"),
            ("empty", unittest.TestSuite(), 1, "did not discover any tests"),
            ("failing", _suite_with(failing), 1, "contract failed"),
        )
        for label, suite, expected_exit, expected_evidence in cases:
            with self.subTest(label=label):
                output = StringIO()

                exit_code = run_postgres_contract_suite(suite, stream=output)

                self.assertEqual(exit_code, expected_exit)
                self.assertIn(expected_evidence, output.getvalue())


if __name__ == "__main__":
    unittest.main()
