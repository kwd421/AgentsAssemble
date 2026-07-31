from __future__ import annotations

import io
import json
import tempfile
import unittest
from pathlib import Path
from urllib.request import Request

from agentsassemble.providers.capabilities import ProviderCapabilityCatalog
from agentsassemble.providers.remote_openai import (
    RemoteOpenAICompatibleRuntime,
    discover_remote_openai_models,
    remote_openai_catalog_payload,
    remote_openai_profile,
    remote_openai_profiles,
)
from agentsassemble.providers.room_portal import RoomPortal


class _Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()


class RemoteOpenAIProviderTests(unittest.TestCase):
    def test_gateway_discovery_admits_only_text_models_with_room_tools(self):
        profile = remote_openai_profile("openrouter")
        self.assertIsNotNone(profile)
        response = {
            "data": [
                {
                    "id": "vendor/tool-model:free",
                    "name": "Tool Model",
                    "architecture": {"input_modalities": ["text"]},
                    "supported_parameters": ["tools", "tool_choice"],
                    "context_length": 131072,
                    "pricing": {"prompt": "0", "completion": "0"},
                },
                {
                    "id": "vendor/plain-model",
                    "name": "Plain Model",
                    "architecture": {"input_modalities": ["text"]},
                    "supported_parameters": ["temperature"],
                },
            ]
        }

        models = discover_remote_openai_models(
            profile,
            opener=lambda _request, timeout: _Response(json.dumps(response).encode()),
        )
        payload = remote_openai_catalog_payload(
            profile,
            discovered_models=models,
        )

        self.assertTrue(payload["startable"])
        options = payload["controls"][0]["options"]
        self.assertEqual([option["value"] for option in options], ["vendor/tool-model:free"])
        self.assertEqual(options[0]["metadata"]["pricing"], "free")
        self.assertEqual(options[0]["metadata"]["family"], "Vendor")

    def test_static_model_profiles_declare_the_effort_relation_scope(self):
        # A profile that offers a reasoning-effort control must say how that
        # effort relates to its models, or ProviderCapabilityCatalog rejects
        # every selection as catalog_invalid -- including the profile's own
        # default effort, which leaves the provider impossible to create.
        checked = []
        for profile in remote_openai_profiles():
            if profile.discovery_path or not profile.reasoning_efforts:
                continue
            payload = remote_openai_catalog_payload(profile)
            controls = {control["key"]: control for control in payload["controls"]}
            self.assertIn("reasoning_effort", controls, profile.provider_id)
            for option in controls["model"]["options"]:
                metadata = dict(option.get("metadata") or {})
                scope = metadata.get("relation_scope")
                self.assertIn(
                    scope,
                    {"global", "per_model"},
                    f"{profile.provider_id} model {option['value']} has no relation scope",
                )
                if scope == "per_model":
                    self.assertIn("reasoning_efforts", metadata, option["value"])
            checked.append(profile.provider_id)
        self.assertTrue(checked, "expected at least one static effort profile")

    def test_static_model_profiles_accept_their_default_effort(self):
        # The end of the same contract, through the real validator: creating an
        # agent with the values the modal defaults to must succeed.
        for profile in remote_openai_profiles():
            if profile.discovery_path or not profile.reasoning_efforts:
                continue
            payload = remote_openai_catalog_payload(profile)
            controls = {control["key"]: control for control in payload["controls"]}
            metadata = dict(controls["model"]["options"][0].get("metadata") or {})
            with self.subTest(provider=profile.provider_id):
                ProviderCapabilityCatalog._validate_model_relation(
                    provider_id=profile.provider_id,
                    metadata=metadata,
                    metadata_key="reasoning_efforts",
                    selected_value=profile.default_reasoning_effort,
                    error_code="unsupported_model_effort_combination",
                )

    def test_openrouter_runtime_reads_and_publishes_through_room_tools(self):
        profile = remote_openai_profile("openrouter")
        self.assertIsNotNone(profile)
        requests: list[dict[str, object]] = []

        def opener(request: Request, timeout: float):
            del timeout
            body = json.loads(request.data)
            requests.append(body)
            if len(requests) == 1:
                return _tool_call_response("call-read", "read_discussion", {})
            if len(requests) == 2:
                return _tool_call_response(
                    "call-publish",
                    "publish_message",
                    {"content": "공용 어댑터 발언"},
                )
            return _content_response("openai/gpt-oss-20b:free", "published")

        with tempfile.TemporaryDirectory() as temp_dir:
            portal = RoomPortal(Path(temp_dir), participant_id="openrouter-agent")
            portal.prepare()
            portal.ingest_frame(
                {
                    "events": [
                        {
                            "id": "evt-1",
                            "seq": 1,
                            "type": "message_final",
                            "actor_id": "host",
                            "content": "공용 어댑터를 확인해 줘.",
                        }
                    ]
                }
            )
            portal.begin_observation("turn-1", input_up_to_seq=1)
            runtime = RemoteOpenAICompatibleRuntime(
                "openrouter-agent",
                profile=profile,
                api_key="secret-never-reported",
                model="openai/gpt-oss-20b:free",
                max_output_tokens=8192,
                opener=opener,
                room_portal=portal,
            )

            runtime.send_room_observation("room.wake turn-1")
            result = runtime.read_output(timeout_seconds=2)
            publication = portal.consume_publication("turn-1")

        self.assertEqual(publication, "공용 어댑터 발언")
        self.assertEqual(result["metadata"]["room_tool_rounds"], 2)
        self.assertEqual(result["metadata"]["observed_model_id"], "openai/gpt-oss-20b:free")
        self.assertEqual(len(requests), 3)
        self.assertTrue(all(request["max_tokens"] == 8192 for request in requests))
        self.assertNotIn("secret-never-reported", json.dumps(result))
        self.assertNotIn("secret-never-reported", json.dumps(runtime.health()))


def _tool_call_response(
    call_id: str,
    name: str,
    arguments: dict[str, object],
) -> _Response:
    chunk = {
        "choices": [
            {
                "delta": {
                    "tool_calls": [
                        {
                            "index": 0,
                            "id": call_id,
                            "type": "function",
                            "function": {
                                "name": name,
                                "arguments": json.dumps(arguments, ensure_ascii=False),
                            },
                        }
                    ]
                }
            }
        ]
    }
    return _Response(f"data: {json.dumps(chunk, ensure_ascii=False)}\n\ndata: [DONE]\n\n".encode())


def _content_response(model: str, content: str) -> _Response:
    chunk = {
        "model": model,
        "choices": [{"delta": {"content": content}}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 2},
    }
    return _Response(f"data: {json.dumps(chunk)}\n\ndata: [DONE]\n\n".encode())


if __name__ == "__main__":
    unittest.main()
