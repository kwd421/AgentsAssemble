from __future__ import annotations

import io
import json
import unittest
from unittest.mock import patch

from agentsassemble.providers.opencode_usage import (
    OpenCodeUsageService,
    build_opencode_go_credential,
    fetch_opencode_go_usage,
)
from agentsassemble.providers.usage_contract import ProviderUsageUnavailable


class OpenCodeUsageTests(unittest.TestCase):
    def test_go_dashboard_windows_are_reported_without_exposing_cookie(self):
        class Response(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                self.close()

        response = Response(
            b'$R[28]={rollingUsage:{usagePercent:17,resetInSec:120},'
            b'weeklyUsage:{usagePercent:63,resetInSec:3600},'
            b'monthlyUsage:{usagePercent:81,resetInSec:7200}}'
        )
        secret_cookie = "Fe26.2**private-opencode-session"
        credential = build_opencode_go_credential("wk_TEST1234", secret_cookie)

        with patch(
            "agentsassemble.providers.opencode_usage.safe_remote_urlopen",
            return_value=response,
        ):
            usage = fetch_opencode_go_usage(credential)

        self.assertEqual(
            [(item["label"], item["percent"]) for item in usage["quota_windows"]],
            [("5h", 17), ("1w", 63), ("30d", 81)],
        )
        self.assertEqual(usage["quota_state"], "low")
        self.assertNotIn(secret_cookie, json.dumps(usage))

    def test_go_only_usage_reuses_the_cached_dashboard_result(self):
        fetch_count = 0

        def fetcher(_cookie: str) -> dict[str, object]:
            nonlocal fetch_count
            fetch_count += 1
            return {
                "provider_id": "opencode",
                "status": "ready",
                "source": "opencode_go_dashboard",
                "quota_5h": "3%",
                "quota_1w": "9%",
                "quota_state": "ok",
                "quota_windows": [],
            }

        service = OpenCodeUsageService(
            credential_reader=lambda: "private-opencode-cookie",
            fetcher=fetcher,
        )

        first = service.read(model="opencode-go/glm-5.2")
        second = service.read(model="opencode-go/glm-5.2")
        with self.assertRaises(ProviderUsageUnavailable):
            service.read(model="opencode/deepseek-v4-flash-free")

        self.assertEqual(first, second)
        self.assertEqual(fetch_count, 1)


if __name__ == "__main__":
    unittest.main()
