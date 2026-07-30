from __future__ import annotations

import unittest

from agentsassemble.providers.model_verification import (
    model_observation_matches,
    model_verification_status,
)


class ProviderModelVerificationTests(unittest.TestCase):
    def test_missing_observation_respects_required_policy(self) -> None:
        self.assertEqual(
            model_verification_status(
                requested_model_id="gpt-5.6-luna",
                observed_model_id="",
                selection_kind="exact",
                observation_policy="required",
            ),
            "pending",
        )
        self.assertEqual(
            model_verification_status(
                requested_model_id="gpt-5.6-luna",
                observed_model_id="",
                selection_kind="exact",
                observation_policy="unavailable",
            ),
            "unavailable",
        )

    def test_alias_and_exact_selections_keep_distinct_statuses(self) -> None:
        self.assertEqual(
            model_verification_status(
                requested_model_id="sonnet",
                observed_model_id="claude-sonnet-4-6",
                selection_kind="alias",
                observation_policy="required",
                provider_kind="claude_code",
            ),
            "resolved_alias",
        )
        self.assertEqual(
            model_verification_status(
                requested_model_id="gpt-5.6-luna",
                observed_model_id="gpt-5.6-luna",
                selection_kind="exact",
                observation_policy="required",
            ),
            "verified",
        )

    def test_claude_release_accepts_its_dated_provider_revision(self) -> None:
        requested = "claude-haiku-4-5"
        observed = "claude-haiku-4-5-20251001"

        self.assertEqual(
            model_verification_status(
                requested_model_id=requested,
                observed_model_id=observed,
                selection_kind="exact",
                observation_policy="required",
                provider_kind="claude_code",
            ),
            "verified_provider_revision",
        )
        self.assertTrue(
            model_observation_matches(
                requested_model_id=requested,
                observed_model_id=observed,
                selection_kind="exact",
                provider_kind="claude_code",
            )
        )

    def test_provider_revision_exception_does_not_apply_to_other_providers(self) -> None:
        self.assertEqual(
            model_verification_status(
                requested_model_id="claude-haiku-4-5",
                observed_model_id="claude-haiku-4-5-20251001",
                selection_kind="exact",
                observation_policy="required",
                provider_kind="codex_live_session",
            ),
            "mismatch",
        )

    def test_antigravity_exact_model_accepts_its_display_label(self) -> None:
        self.assertEqual(
            model_verification_status(
                requested_model_id="gemini-3.6-flash-low",
                observed_model_id="Gemini 3.6 Flash (Low)",
                selection_kind="exact",
                observation_policy="required",
                provider_kind="antigravity_live_session",
            ),
            "verified_provider_display",
        )
        self.assertFalse(
            model_observation_matches(
                requested_model_id="gemini-3.6-flash-low",
                observed_model_id="Gemini 3.6 Flash (Medium)",
                selection_kind="exact",
                provider_kind="antigravity_live_session",
            )
        )

    def test_ollama_cloud_route_accepts_the_same_reported_model_without_route_suffix(self) -> None:
        self.assertTrue(
            model_observation_matches(
                requested_model_id="nemotron-3-super:cloud",
                observed_model_id="nemotron-3-super",
                selection_kind="exact",
                provider_kind="ollama_api",
            )
        )
        self.assertFalse(
            model_observation_matches(
                requested_model_id="nemotron-3-super:cloud",
                observed_model_id="nemotron-3-nano",
                selection_kind="exact",
                provider_kind="ollama_api",
            )
        )
        self.assertFalse(
            model_observation_matches(
                requested_model_id="nemotron-3-super:cloud",
                observed_model_id="nemotron-3-super",
                selection_kind="exact",
                provider_kind="deepseek_api",
            )
        )

    def test_mismatch_and_missing_ids_do_not_match(self) -> None:
        self.assertFalse(
            model_observation_matches(
                requested_model_id="gpt-5.6-luna",
                observed_model_id="gpt-5.5-codex",
                selection_kind="exact",
            )
        )
        self.assertFalse(
            model_observation_matches(
                requested_model_id="gpt-5.6-luna",
                observed_model_id="",
                selection_kind="exact",
            )
        )


if __name__ == "__main__":
    unittest.main()
