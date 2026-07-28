from __future__ import annotations

import unittest

from agentsassemble.providers.auth import (
    provider_auth_error_message,
    provider_login_required_message,
)


class ProviderAuthTests(unittest.TestCase):
    def test_auth_markers_are_matched_case_insensitively(self) -> None:
        for text in (
            "AUTHENTICATION REQUIRED",
            "Provider is not logged in",
            "Please Sign In to continue",
        ):
            with self.subTest(text=text):
                self.assertEqual(
                    provider_auth_error_message(
                        text,
                        provider_label="Grok",
                        login_command="grok login",
                    ),
                    provider_login_required_message("Grok", "grok login"),
                )

    def test_unrelated_provider_error_is_not_reclassified_as_auth(self) -> None:
        self.assertEqual(
            provider_auth_error_message(
                "request timed out",
                provider_label="Antigravity",
                login_command="agy",
            ),
            "",
        )


if __name__ == "__main__":
    unittest.main()
