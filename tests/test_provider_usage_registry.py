from __future__ import annotations

import unittest

from agentsassemble.providers.provider_usage import ProviderUsageRegistry
from agentsassemble.providers.usage_contract import ProviderUsageUnavailable


class _UnavailableUsageReader:
    def __init__(self) -> None:
        self.read_count = 0

    def read(
        self,
        *,
        model: str = "",
        refresh: bool = False,
    ) -> dict[str, object]:
        del model, refresh
        self.read_count += 1
        raise ProviderUsageUnavailable("provider_usage_temporarily_unavailable")


class ProviderUsageRegistryTests(unittest.TestCase):
    def test_repeated_failed_reads_do_not_restart_the_provider_during_cooldown(self) -> None:
        reader = _UnavailableUsageReader()
        registry = ProviderUsageRegistry(
            {"example": reader},
            failure_cooldown_seconds=30,
        )

        for _ in range(2):
            with self.assertRaisesRegex(
                ProviderUsageUnavailable,
                "provider_usage_temporarily_unavailable",
            ):
                registry.read("example")

        self.assertEqual(reader.read_count, 1)


if __name__ == "__main__":
    unittest.main()
