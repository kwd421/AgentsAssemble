import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from agentsassemble.providers.adapters.http_llm import (
    AnthropicMessagesAdapter,
    GeminiGenerateContentAdapter,
    GrokChatAdapter,
    LocalOpenAICompatibleAdapter,
)
from agentsassemble.providers.remote_http import RemoteEndpointBlocked
from agentsassemble.models import ProviderConfig, Role


class FakeRequester:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def __call__(self, url, headers, payload, timeout_seconds):
        self.calls.append(
            {
                "url": url,
                "headers": headers,
                "payload": payload,
                "timeout_seconds": timeout_seconds,
            }
        )
        return self.response


class HttpLlmAdapterTests(unittest.TestCase):
    def test_remote_anthropic_adapter_never_sends_credentials_to_loopback_http(self):
        received: list[dict[str, object]] = []

        class CaptureHandler(BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get("Content-Length") or 0)
                received.append(
                    {
                        "api_key": self.headers.get("x-api-key"),
                        "body": self.rfile.read(length),
                    }
                )
                body = json.dumps(
                    {"content": [{"type": "text", "text": "captured"}]}
                ).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, _format, *_args):
                return

        server = ThreadingHTTPServer(("127.0.0.1", 0), CaptureHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            adapter = AnthropicMessagesAdapter(
                ProviderConfig(
                    id="malicious-anthropic",
                    kind="anthropic",
                    display_name="Malicious Anthropic",
                    default_model="claude-test",
                    endpoint=f"http://127.0.0.1:{server.server_port}/messages",
                    auth_ref="literal:must-not-leave-process",
                )
            )
            role = Role("architect", "아키텍트", "Architecture", "design alternatives")

            with self.assertRaises(RemoteEndpointBlocked):
                adapter.run_research(
                    role,
                    {"role_id": role.id},
                    "질문",
                    _Depth(),
                    _Steering(),
                )
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

        self.assertEqual(received, [])

    def test_local_openai_compatible_round_uses_chat_completions(self):
        requester = FakeRequester(
            {
                "choices": [
                    {
                        "message": {
                            "content": '{"content":"로컬 의견","position":"찬성","stance_status":"held","change_conditions":["근거"],"confidence":"medium"}'
                        }
                    }
                ]
            }
        )
        adapter = LocalOpenAICompatibleAdapter(
            ProviderConfig(
                id="lmstudio-gemma",
                kind="local_openai_compatible",
                display_name="LM Studio Gemma",
                default_model="gemma-3-4b-it",
                endpoint="http://127.0.0.1:1234/v1",
            ),
            requester=requester,
        )
        role = Role("reviewer", "리뷰어", "Review", "local review")

        message = adapter.run_round(role, {"role_id": role.id}, "round_1", "말해줘", {"x": 1})

        self.assertEqual(requester.calls[0]["url"], "http://127.0.0.1:1234/v1/chat/completions")
        self.assertEqual(requester.calls[0]["payload"]["model"], "gemma-3-4b-it")
        self.assertEqual(requester.calls[0]["payload"]["messages"][0]["role"], "system")
        self.assertEqual(message["content"], "로컬 의견")
        self.assertEqual(message["position"], "찬성")
        self.assertEqual(message["provider"]["kind"], "local_openai_compatible")
        prompt = requester.calls[0]["payload"]["messages"][1]["content"]
        self.assertIn("Research is raw material, not your spoken message", prompt)
        self.assertIn("4-8 Korean sentences", prompt)
        self.assertIn("at most 2 short paragraphs", prompt)
        self.assertIn("held|qualified|reframed|revised|conceded", prompt)
        self.assertIn("emotion", prompt)
        self.assertIn("conflict_style", prompt)

    def test_anthropic_research_uses_messages_api(self):
        requester = FakeRequester(
            {
                "content": [
                    {
                        "type": "text",
                        "text": '{"queries":["q"],"sources":[],"summary":"클로드 조사","confidence":"medium","uncertainty":"","claim_evidence":[],"counterclaims":[],"rejected_claims":[]}',
                    }
                ]
            }
        )
        adapter = AnthropicMessagesAdapter(
            ProviderConfig(
                id="claude",
                kind="anthropic",
                display_name="Claude",
                default_model="claude-3-5-sonnet",
                auth_ref="literal:test-key",
            ),
            requester=requester,
        )
        role = Role("architect", "아키텍트", "Architecture", "design alternatives")

        research = adapter.run_research(role, {"role_id": role.id}, "질문", _Depth(), _Steering())

        self.assertEqual(requester.calls[0]["url"], "https://api.anthropic.com/v1/messages")
        self.assertEqual(requester.calls[0]["headers"]["x-api-key"], "test-key")
        self.assertEqual(requester.calls[0]["headers"]["anthropic-version"], "2023-06-01")
        self.assertEqual(requester.calls[0]["payload"]["model"], "claude-3-5-sonnet")
        self.assertEqual(research["summary"], "클로드 조사")
        self.assertEqual(research["provider"]["kind"], "anthropic")

    def test_gemini_synthesis_uses_generate_content(self):
        requester = FakeRequester(
            {
                "candidates": [
                    {
                        "content": {
                            "parts": [
                                {
                                    "text": '{"winner":"A","ranking":["A","B"],"confidence":"medium","caveats":[],"summary":"제미나이 종합","tasks":{}}'
                                }
                            ]
                        }
                    }
                ]
            }
        )
        adapter = GeminiGenerateContentAdapter(
            ProviderConfig(
                id="gemini",
                kind="gemini",
                display_name="Gemini",
                default_model="gemini-2.5-pro",
                auth_ref="literal:gemini-key",
            ),
            requester=requester,
        )

        synthesis = adapter.synthesize({"role_id": "moderator"}, "질문", {"rounds": []})

        self.assertIn("models/gemini-2.5-pro:generateContent", requester.calls[0]["url"])
        self.assertNotIn("gemini-key", requester.calls[0]["url"])
        self.assertEqual(requester.calls[0]["headers"]["x-goog-api-key"], "gemini-key")
        self.assertIn("contents", requester.calls[0]["payload"])
        self.assertEqual(synthesis["summary"], "제미나이 종합")
        self.assertEqual(synthesis["provider"]["kind"], "gemini")

    def test_grok_uses_xai_openai_compatible_endpoint(self):
        requester = FakeRequester(
            {
                "choices": [
                    {
                        "message": {
                            "content": '{"content":"그록 반박","position":"반대","stance_status":"held","change_conditions":[],"confidence":"medium"}'
                        }
                    }
                ]
            }
        )
        adapter = GrokChatAdapter(
            ProviderConfig(
                id="grok",
                kind="grok",
                display_name="Grok",
                default_model="grok-4",
                auth_ref="literal:xai-key",
            ),
            requester=requester,
        )
        role = Role("skeptic", "회의론자", "Skeptic", "challenge assumptions")

        message = adapter.run_round(role, {"role_id": role.id}, "round_2", "반박", {})

        self.assertEqual(requester.calls[0]["url"], "https://api.x.ai/v1/chat/completions")
        self.assertEqual(requester.calls[0]["headers"]["Authorization"], "Bearer xai-key")
        self.assertEqual(message["content"], "그록 반박")
        self.assertEqual(message["provider"]["kind"], "grok")


class _Depth:
    name = "smoke"
    label = "Smoke"
    min_sources = 1
    target_sources = 1
    min_queries = 1
    min_claims = 1
    min_counterclaims = 0
    notes_per_source = 1
    source_mix = "minimal"
    instructions = "minimal"


class _Steering:
    @property
    def is_open(self):
        return True

    def to_dict(self):
        return {"stance": "open", "prompt": None}


if __name__ == "__main__":
    unittest.main()
