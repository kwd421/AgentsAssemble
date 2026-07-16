from __future__ import annotations

import unittest

from agentsassemble.providers.auth import (
    provider_auth_error_message,
    provider_login_required_message,
)


class ProviderAuthTests(unittest.TestCase):
    def test_login_message_names_provider_and_native_command(self) -> None:
        self.assertEqual(
            provider_login_required_message("Codex", "codex login"),
            (
                "Codex 로그인이 필요합니다. 터미널에서 codex login을 실행해 "
                "로그인한 뒤 다시 연결 확인을 누르세요."
            ),
        )

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
