from __future__ import annotations

import io
import json
import unittest
from urllib.request import Request

from agentsassemble.providers.remote_openai import (
    RemoteOpenAICompatibleRuntime,
    remote_openai_catalog_payload,
    remote_openai_profile,
)


class _Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()


class TokenRouterProviderTests(unittest.TestCase):
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

        payload = remote_openai_catalog_payload(profile)
        model_options = payload["controls"][0]["options"]
        runtime = RemoteOpenAICompatibleRuntime(
            "tokenrouter-agent",
            profile=profile,
            api_key="secret-never-reported",
            model="moonshotai/kimi-k3-free",
            opener=opener,
        )
        runtime.send("짧게 인사해 줘.")
        result = runtime.read_output(timeout_seconds=2)

        self.assertTrue(payload["startable"])
        self.assertEqual(model_options[0]["metadata"]["pricing"], "free")
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
