import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from agentsassemble.identity_store import IdentityStore
from agentsassemble.room_api_provider import (
    ApiProviderError,
    api_error_category,
    chat_completion,
    chat_completion_with_fallback,
    record_api_usage,
    run_api_call,
)

MSGS = [{"role": "user", "content": "hi"}]


def _ok_body(content="hello", *, usage=True):
    data = {"choices": [{"message": {"role": "assistant", "content": content}}]}
    if usage:
        data["usage"] = {"prompt_tokens": 12, "completion_tokens": 7}
    return json.dumps(data).encode("utf-8")


def _poster(status, body):
    captured = {}

    def post(url, body_bytes, headers, timeout):
        captured["url"] = url
        captured["headers"] = headers
        captured["payload"] = json.loads(body_bytes.decode("utf-8"))
        return status, body

    post.captured = captured
    return post


class ChatCompletionTests(unittest.TestCase):
    def test_happy_path_returns_text_and_provider_usage(self):
        post = _poster(200, _ok_body("the answer"))
        reply = chat_completion("nvidia", "minimaxai/minimax-m2", MSGS, api_key="k", http_post=post)
        self.assertEqual(reply.text, "the answer")
        self.assertEqual(reply.usage.input_tokens, 12)
        self.assertEqual(reply.usage.output_tokens, 7)
        self.assertFalse(reply.usage.estimated)
        self.assertEqual(reply.cost_owner, "free")  # nvidia default

    def test_url_and_auth_header_built_from_catalog(self):
        post = _poster(200, _ok_body())
        chat_completion("nvidia", "minimaxai/minimax-m2", MSGS, api_key="secret-key", http_post=post)
        self.assertEqual(post.captured["url"], "https://integrate.api.nvidia.com/v1/chat/completions")
        self.assertEqual(post.captured["headers"]["Authorization"], "Bearer secret-key")
        self.assertEqual(post.captured["payload"]["model"], "minimaxai/minimax-m2")

    def test_missing_usage_block_is_estimated_and_flagged(self):
        post = _poster(200, _ok_body("a longer answer here", usage=False))
        reply = chat_completion("nvidia", "minimaxai/minimax-m2", MSGS, api_key="k", http_post=post)
        self.assertTrue(reply.usage.estimated)
        self.assertGreater(reply.usage.output_tokens, 0)

    def test_key_source_overrides_cost_owner(self):
        post = _poster(200, _ok_body())
        reply = chat_completion(
            "nvidia", "minimaxai/minimax-m2", MSGS, api_key="k", key_source="byok", http_post=post
        )
        self.assertEqual(reply.cost_owner, "byok")

    def test_local_provider_needs_no_key(self):
        post = _poster(200, _ok_body())
        reply = chat_completion("lmstudio", "local-model", MSGS, http_post=post)
        self.assertEqual(reply.cost_owner, "local")
        self.assertNotIn("Authorization", post.captured["headers"])


class ErrorMappingTests(unittest.TestCase):
    def _expect(self, status, category):
        post = _poster(status, b'{"error":"x"}')
        with self.assertRaises(ApiProviderError) as ctx:
            chat_completion("nvidia", "minimaxai/minimax-m2", MSGS, api_key="k", http_post=post)
        self.assertEqual(ctx.exception.category, category)
        self.assertEqual(api_error_category(ctx.exception), category)

    def test_401_is_auth(self):
        self._expect(401, "auth")

    def test_429_is_rate_limit(self):
        self._expect(429, "rate_limit")

    def test_500_is_unavailable(self):
        self._expect(500, "unavailable")

    def test_400_is_bad_response(self):
        self._expect(400, "bad_response")

    def test_unknown_provider_is_config_error(self):
        with self.assertRaises(ApiProviderError) as ctx:
            chat_completion("nope", "x", MSGS, http_post=_poster(200, _ok_body()))
        self.assertEqual(ctx.exception.category, "config")

    def test_missing_key_is_auth_error(self):
        with self.assertRaises(ApiProviderError) as ctx:
            chat_completion("nvidia", "minimaxai/minimax-m2", MSGS, api_key="", http_post=_poster(200, _ok_body()))
        self.assertEqual(ctx.exception.category, "auth")

    def test_empty_content_is_bad_response(self):
        post = _poster(200, _ok_body("   "))
        with self.assertRaises(ApiProviderError) as ctx:
            chat_completion("nvidia", "minimaxai/minimax-m2", MSGS, api_key="k", http_post=post)
        self.assertEqual(ctx.exception.category, "bad_response")


class FallbackChainTests(unittest.TestCase):
    # the fallback chain spans nvidia + openrouter; give both keys so the
    # auth gate passes and we exercise the actual rate-limit fall-through
    ENV = {"NVIDIA_API_KEY": "nv-key", "OPENROUTER_API_KEY": "or-key"}

    def test_rate_limit_falls_through_to_next(self):
        calls = []

        def post(url, body, headers, timeout):
            calls.append(url)
            if len(calls) == 1:
                return 429, b'{"error":"slow down"}'
            return 200, _ok_body("from fallback")

        with mock.patch.dict(os.environ, self.ENV):
            reply = chat_completion_with_fallback(MSGS, http_post=post)
        self.assertEqual(reply.text, "from fallback")
        self.assertGreaterEqual(len(calls), 2)

    def test_auth_error_does_not_fall_through(self):
        calls = []

        def post(url, body, headers, timeout):
            calls.append(url)
            return 401, b'{"error":"bad key"}'

        with mock.patch.dict(os.environ, self.ENV):
            with self.assertRaises(ApiProviderError) as ctx:
                chat_completion_with_fallback(MSGS, http_post=post)
        self.assertEqual(ctx.exception.category, "auth")
        self.assertEqual(len(calls), 1)  # fail fast, no pointless retry


class UsageRecordingTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.store = IdentityStore(Path(self._tmp.name) / "identity.db")
        env = mock.patch.dict(os.environ, {"NVIDIA_API_KEY": "nv-key"})
        env.start()
        self.addCleanup(env.stop)

    def test_run_api_call_returns_text_and_records_usage(self):
        post = _poster(200, _ok_body("the reply"))
        text = run_api_call(
            "nvidia",
            "minimaxai/minimax-m2",
            "what's up",
            store=self.store,
            user_id="u-1",
            participant_id="guest-1",
            meeting_id="room-x",
            http_post=post,
        )
        self.assertEqual(text, "the reply")
        summary = self.store.usage_summary(meeting_id="room-x")
        self.assertEqual(summary["events"], 1)
        self.assertEqual(summary["input_tokens"], 12)
        self.assertEqual(summary["output_tokens"], 7)
        self.assertEqual(summary["estimated_events"], 0)

    def test_estimated_usage_flag_persisted(self):
        post = _poster(200, _ok_body("estimated answer", usage=False))
        run_api_call(
            "nvidia", "minimaxai/minimax-m2", "hi", store=self.store, http_post=post
        )
        self.assertEqual(self.store.usage_summary()["estimated_events"], 1)

    def test_system_prompt_prepended(self):
        post = _poster(200, _ok_body())
        run_api_call(
            "nvidia", "minimaxai/minimax-m2", "user text",
            system="you are terse", store=self.store, http_post=post,
        )
        msgs = post.captured["payload"]["messages"]
        self.assertEqual(msgs[0], {"role": "system", "content": "you are terse"})
        self.assertEqual(msgs[1]["content"], "user text")

    def test_record_api_usage_tolerates_no_store(self):
        # the seam must be a no-op when no store is wired (e.g. pure-local dry run)
        post = _poster(200, _ok_body())
        text = run_api_call("nvidia", "minimaxai/minimax-m2", "hi", store=None, http_post=post)
        self.assertTrue(text)


if __name__ == "__main__":
    unittest.main()
