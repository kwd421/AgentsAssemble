from __future__ import annotations

import io
import json
import unittest
from urllib.request import Request

from agentsassemble.providers.remote_openai import (
    discover_remote_openai_models,
    remote_openai_catalog_payload,
    remote_openai_profile,
)


class _Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()


class CerebrasModelCatalogTests(unittest.TestCase):
    def test_public_tool_catalog_reaches_the_provider_creation_options(self):
        profile = remote_openai_profile("cerebras")
        self.assertIsNotNone(profile)
        requested_urls: list[str] = []
        response = {
            "data": [
                {
                    "id": "zai-glm-4.7",
                    "name": "Z.ai GLM 4.7",
                    "input_modalities": ["text"],
                    "supported_features": ["tools", "reasoning"],
                    "context_length": 131072,
                },
                {
                    "id": "gemma-4-31b",
                    "name": "Gemma 4 31B",
                    "input_modalities": ["text", "image"],
                    "supported_features": ["tools", "reasoning"],
                    "context_length": 131072,
                },
                {
                    "id": "gpt-oss-120b",
                    "name": "OpenAI GPT OSS",
                    "input_modalities": ["text"],
                    "supported_features": ["tools", "reasoning"],
                    "context_length": 131072,
                },
                {
                    "id": "text-only-without-tools",
                    "name": "No Room Tools",
                    "input_modalities": ["text"],
                    "supported_features": ["reasoning"],
                },
            ]
        }

        def opener(request: Request, timeout: float):
            self.assertGreater(timeout, 0)
            requested_urls.append(request.full_url)
            return _Response(json.dumps(response).encode())

        models = discover_remote_openai_models(profile, opener=opener)
        payload = remote_openai_catalog_payload(profile, discovered_models=models)

        self.assertEqual(
            requested_urls,
            ["https://api.cerebras.ai/public/v1/models?format=openrouter"],
        )
        self.assertTrue(payload["startable"])
        self.assertEqual(
            [option["value"] for option in payload["controls"][0]["options"]],
            ["zai-glm-4.7", "gemma-4-31b", "gpt-oss-120b"],
        )


if __name__ == "__main__":
    unittest.main()
