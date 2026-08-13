from __future__ import annotations

import io
import json
import unittest
from urllib.error import HTTPError
from urllib.request import Request

from agentsassemble.providers.remote_openai import (
    RemoteOpenAICompatibleRuntime,
    discover_remote_openai_models,
    remote_openai_catalog_payload,
    remote_openai_discovery_failure_payload,
    remote_openai_profile,
)


class _Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()


class TokenRouterProviderTests(unittest.TestCase):
    def test_public_catalog_failure_keeps_the_static_free_model_startable(self):
        profile = remote_openai_profile("tokenrouter")
        self.assertIsNotNone(profile)

        class PublicCatalogRejected(RuntimeError):
            code = "provider_turn_failed"

        payload = remote_openai_discovery_failure_payload(
            profile,
            PublicCatalogRejected("authentication required"),
        )

        self.assertTrue(payload["startable"])
        self.assertEqual(payload["discovery_error_code"], "provider_turn_failed")
        self.assertEqual(
            [option["value"] for option in payload["controls"][0]["options"]],
            ["moonshotai/kimi-k3-free"],
        )

    def test_provider_catalog_uses_tokenrouter_models_instead_of_one_bundled_choice(self):
        profile = remote_openai_profile("tokenrouter")
        self.assertIsNotNone(profile)
        response = {
            "data": [
                {
                    "id": "moonshotai/kimi-k3-free",
                    "name": "Kimi K3 Free",
                    "supported_parameters": ["tools"],
                    "input_modalities": ["text"],
                    "free": True,
                },
                {
                    "id": "moonshotai/kimi-k2.6",
                    "name": "Kimi K2.6",
                    "supported_parameters": ["tools"],
                    "input_modalities": ["text"],
                    "pricing": {"prompt": "0.4", "completion": "2.0"},
                },
            ]
        }

        models = discover_remote_openai_models(
            profile,
            api_key="configured-key",
            opener=lambda _request, timeout: _Response(json.dumps(response).encode()),
        )
        payload = remote_openai_catalog_payload(profile, discovered_models=models)

        self.assertEqual(
            [option["value"] for option in payload["controls"][0]["options"]],
            ["moonshotai/kimi-k3-free", "moonshotai/kimi-k2.6"],
        )

    def test_exhausted_tokenrouter_key_is_reported_as_quota_not_credentials(self):
        profile = remote_openai_profile("tokenrouter")
        self.assertIsNotNone(profile)
        payload = {
            "error": {
                "message": "该令牌额度已用尽，RemainQuota = 0",
                "type": "api_error",
            }
        }

        def opener(request: Request, timeout: float):
            del timeout
            raise HTTPError(
                request.full_url,
                401,
                "Unauthorized",
                {},
                io.BytesIO(json.dumps(payload).encode()),
            )

        runtime = RemoteOpenAICompatibleRuntime(
            "tokenrouter-agent",
            profile=profile,
            api_key="configured-but-exhausted",
            model="moonshotai/kimi-k3-free",
            opener=opener,
        )
        runtime.send("짧게 인사해 줘.")

        with self.assertRaises(RuntimeError) as raised:
            runtime.read_output(timeout_seconds=2)

        self.assertEqual(raised.exception.code, "quota_exhausted")

    def test_kimi_free_selection_reaches_the_openai_compatible_boundary(self):
        profile = remote_openai_profile("tokenrouter")
        self.assertIsNotNone(profile)
        requests: list[tuple[str, dict[str, object]]] = []

        def opener(request: Request, timeout: float):
            del timeout
            requests.append((request.full_url, json.loads(request.data)))
            chunk = {
                "model": "moonshotai/kimi-k3-free",
                "choices": [{"delta": {"content": "테스트 응답"}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 2},
            }
            return _Response(
                f"data: {json.dumps(chunk)}\n\ndata: [DONE]\n\n".encode()
            )

        runtime = RemoteOpenAICompatibleRuntime(
            "tokenrouter-agent",
            profile=profile,
            api_key="secret-never-reported",
            model="moonshotai/kimi-k3-free",
            opener=opener,
        )
        runtime.send("짧게 인사해 줘.")
        result = runtime.read_output(timeout_seconds=2)

        self.assertEqual(
            requests,
            [
                (
                    "https://api.tokenrouter.com/v1/chat/completions",
                    {
                        "model": "moonshotai/kimi-k3-free",
                        "messages": [{"role": "user", "content": "짧게 인사해 줘."}],
                        "stream": True,
                        "stream_options": {"include_usage": True},
                        "max_tokens": 4096,
                    },
                )
            ],
        )
        self.assertEqual(result["content"], "테스트 응답")
        self.assertEqual(
            result["metadata"]["observed_model_id"],
            "moonshotai/kimi-k3-free",
        )
        self.assertNotIn("secret-never-reported", json.dumps(result))


if __name__ == "__main__":
    unittest.main()
