from __future__ import annotations

import unittest
from collections.abc import Callable
from io import StringIO

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
