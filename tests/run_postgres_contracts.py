from __future__ import annotations

import importlib.util
import os
import sys
import unittest
from collections.abc import Callable, Mapping
from typing import TextIO


POSTGRES_TEST_DSN_ENV = "AGENTSASSEMBLE_TEST_POSTGRES_DSN"
POSTGRES_CONTRACT_MODULES = (
    "tests.test_postgres_connection_pool",
    "tests.test_postgres_room_schema",
    "tests.test_postgres_room_repository",
    "tests.test_room_repository_migration",
)
POSTGRES_REQUIRED_MODULES = (
    "alembic",
    "psycopg",
    "psycopg_pool",
    "sqlalchemy",
)


def missing_postgres_contract_requirements(
    environment: Mapping[str, str],
    *,
    find_module: Callable[[str], object | None] = importlib.util.find_spec,
) -> tuple[str, ...]:
    missing: list[str] = []
    if not environment.get(POSTGRES_TEST_DSN_ENV, "").strip():
        missing.append(POSTGRES_TEST_DSN_ENV)
    missing.extend(name for name in POSTGRES_REQUIRED_MODULES if find_module(name) is None)
    return tuple(missing)


def load_postgres_contract_suite(
    loader: unittest.TestLoader | None = None,
) -> unittest.TestSuite:
    return (loader or unittest.defaultTestLoader).loadTestsFromNames(
        POSTGRES_CONTRACT_MODULES
    )


def run_postgres_contract_suite(
    suite: unittest.TestSuite,
    *,
    stream: TextIO | None = None,
) -> int:
    output = stream or sys.stderr
    result = unittest.TextTestRunner(stream=output, verbosity=2).run(suite)
    if result.testsRun == 0:
        output.write("PostgreSQL contract suite did not discover any tests.\n")
        return 1
    if result.skipped:
        output.write(
            f"PostgreSQL contract suite skipped {len(result.skipped)} test(s); "
            "this job requires every selected contract to run.\n"
        )
        for test, reason in result.skipped:
            output.write(f"- {test.id()}: {reason}\n")
        return 1
    return 0 if result.wasSuccessful() else 1


def main() -> int:
    missing = missing_postgres_contract_requirements(os.environ)
    if missing:
        print(
            "PostgreSQL contract prerequisites are missing: " + ", ".join(missing),
            file=sys.stderr,
        )
        return 2
    return run_postgres_contract_suite(load_postgres_contract_suite())


if __name__ == "__main__":
    raise SystemExit(main())
