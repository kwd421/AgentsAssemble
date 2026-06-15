import os
import unittest
from unittest import mock

from agentsassemble import provider_catalog as pc


class CatalogLookupTests(unittest.TestCase):
    def test_known_providers_present(self):
        providers = pc.list_providers()
        self.assertIn("nvidia", providers)
        self.assertIn("openrouter", providers)
        self.assertIn("lmstudio", providers)

    def test_get_model_returns_entry(self):
        model = pc.get_model("nvidia", "minimaxai/minimax-m2")
        self.assertIsNotNone(model)
        self.assertEqual(model["name"], "MiniMax M2 (NVIDIA free)")

    def test_unknown_provider_or_model_is_none(self):
        self.assertIsNone(pc.get_provider("nope"))
        self.assertIsNone(pc.get_model("nvidia", "nope"))
        self.assertIsNone(pc.get_model("nope", "x"))

    def test_split_ref_handles_model_id_with_slash(self):
        self.assertEqual(
            pc.split_ref("nvidia/meta/llama-3.3-70b-instruct"),
            ("nvidia", "meta/llama-3.3-70b-instruct"),
        )


class CapabilityTests(unittest.TestCase):
    def test_capability_from_catalog(self):
        cap = pc.model_capability("nvidia", "minimaxai/minimax-m2")
        self.assertTrue(cap["text"])
        self.assertTrue(cap["tool_call"])
        self.assertFalse(cap["vision"])

    def test_unknown_model_falls_back_to_default_capability(self):
        cap = pc.model_capability("nope", "nope")
        self.assertEqual(cap, pc.DEFAULT_CAPABILITY)
        self.assertIsNot(cap, pc.DEFAULT_CAPABILITY)  # a copy, not the shared dict


class CostOwnerTests(unittest.TestCase):
    def test_model_level_overrides_provider_default(self):
        # openrouter default is byok, but the :free model says free
        self.assertEqual(
            pc.model_cost_owner("openrouter", "meta-llama/llama-3.3-70b-instruct:free"),
            "free",
        )

    def test_provider_default_when_model_has_none(self):
        self.assertEqual(pc.model_cost_owner("nvidia", "minimaxai/minimax-m2"), "free")

    def test_runtime_key_source_wins(self):
        # if the key came from the user (byok) at call time, that wins over catalog
        self.assertEqual(
            pc.model_cost_owner("nvidia", "minimaxai/minimax-m2", key_source="byok"),
            "byok",
        )


class KeyResolutionTests(unittest.TestCase):
    def test_env_key_resolved(self):
        with mock.patch.dict(os.environ, {"NVIDIA_API_KEY": "  nv-secret  "}):
            self.assertEqual(pc.resolve_api_key("nvidia"), "nv-secret")

    def test_no_env_means_empty(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(pc.resolve_api_key("openrouter"), "")

    def test_local_provider_has_no_env(self):
        self.assertEqual(pc.resolve_api_key("lmstudio"), "")


class FallbackChainTests(unittest.TestCase):
    def test_fallback_models_are_resolvable(self):
        pairs = pc.fallback_models()
        self.assertGreater(len(pairs), 0)
        for provider, model in pairs:
            self.assertIsNotNone(
                pc.get_model(provider, model),
                f"fallback ref {provider}/{model} not in catalog",
            )


class PayloadSafetyTests(unittest.TestCase):
    def test_payload_never_leaks_key_values(self):
        with mock.patch.dict(os.environ, {"NVIDIA_API_KEY": "nv-supersecret"}):
            payload = pc.catalog_payload()
        blob = repr(payload)
        self.assertNotIn("nv-supersecret", blob)
        # but it should report presence as a boolean
        self.assertTrue(payload["providers"]["nvidia"]["key_present"])

    def test_payload_shape(self):
        payload = pc.catalog_payload()
        self.assertIn("providers", payload)
        self.assertIn("fallback_chain", payload)
        nvidia = payload["providers"]["nvidia"]
        self.assertEqual(nvidia["base_url"], "https://integrate.api.nvidia.com/v1")
        model = nvidia["models"]["minimaxai/minimax-m2"]
        self.assertIn("capability", model)
        self.assertEqual(model["cost_owner"], "free")


if __name__ == "__main__":
    unittest.main()
