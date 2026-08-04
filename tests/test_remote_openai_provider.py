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
        catalog = ProviderCapabilityCatalog(
            runner=lambda _command, _timeout: (1, "", "not installed"),
            resolver=lambda _executable: None,
            claude_model_discovery=lambda _executable: [],
            claude_xhigh_model_discovery=lambda _executable: [],
            remote_model_discovery=lambda _profile, _api_key: [],
        )
        snapshot = catalog.snapshot(refresh=True)
        revision = str(snapshot["catalog_revision"])
        for profile in remote_openai_profiles():
            if profile.discovery_path or not profile.reasoning_efforts:
                continue
            payload = remote_openai_catalog_payload(profile)
            defaults = {
                control["key"]: str(control.get("default_value") or "")
                for control in payload["controls"]
            }
            with self.subTest(provider=profile.provider_id):
                selection = catalog.validate_selection(
                    catalog_revision=revision,
                    provider_id=profile.provider_id,
                    values=defaults,
                )
                self.assertEqual(selection.model, profile.default_model)
                self.assertEqual(selection.reasoning_effort, profile.default_reasoning_effort)

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

    def test_api_work_harness_changes_only_the_selected_workspace_after_approval(self):
        profile = remote_openai_profile("tokenrouter")
        self.assertIsNotNone(profile)
        requests: list[dict[str, object]] = []
        approval_requests: list[dict[str, object]] = []

        def opener(request: Request, timeout: float):
            del timeout
            requests.append(json.loads(request.data))
            if len(requests) == 1:
                return _tool_call_response(
                    "call-read",
                    "read_workspace_file",
                    {"path": "note.txt"},
                )
            if len(requests) == 2:
                return _tool_call_response(
                    "call-edit",
                    "replace_workspace_text",
                    {
                        "path": "note.txt",
                        "old_text": "before",
                        "new_text": "after",
                    },
                )
            return _content_response("moonshotai/kimi-k3-free", "수정했습니다.")

        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir) / "workspace"
            workspace.mkdir()
            note = workspace / "note.txt"
            note.write_text("before\n", encoding="utf-8")
            runtime = RemoteOpenAICompatibleRuntime(
                "tokenrouter-worker",
                profile=profile,
                api_key="test-key",
                model="moonshotai/kimi-k3-free",
                max_output_tokens=4096,
                opener=opener,
                workspace=str(workspace),
                permission_mode="workspace_write",
            )

            def approve(request, respond):
                approval_requests.append(request)
                respond({"option_id": "allow_once"})

            runtime.set_request_handler(approve)
            runtime.send("note.txt를 읽고 before를 after로 바꿔 줘.")
            result = runtime.read_output(timeout_seconds=2)

            self.assertEqual(note.read_text(encoding="utf-8"), "after\n")

        self.assertEqual(result["content"], "수정했습니다.")
        self.assertEqual(len(approval_requests), 1)
        self.assertEqual(approval_requests[0]["request_kind"], "permission")
        first_tools = {
            tool["function"]["name"]
            for tool in requests[0]["tools"]
        }
        self.assertIn("read_workspace_file", first_tools)
        self.assertIn("replace_workspace_text", first_tools)

    def test_api_work_harness_does_not_expose_workspace_tools_in_read_only_mode(self):
        profile = remote_openai_profile("tokenrouter")
        self.assertIsNotNone(profile)
        requests: list[dict[str, object]] = []

        def opener(request: Request, timeout: float):
            del timeout
            requests.append(json.loads(request.data))
            return _content_response("moonshotai/kimi-k3-free", "대화만 합니다.")

        with tempfile.TemporaryDirectory() as temp_dir:
            runtime = RemoteOpenAICompatibleRuntime(
                "tokenrouter-chat",
                profile=profile,
                api_key="test-key",
                model="moonshotai/kimi-k3-free",
                max_output_tokens=4096,
                opener=opener,
                workspace=temp_dir,
                permission_mode="meeting_read_only",
            )
            runtime.send("인사해 줘.")
            runtime.read_output(timeout_seconds=2)

        self.assertNotIn("tools", requests[0])

    def test_api_work_harness_search_does_not_follow_symlinks_outside_workspace(self):
        profile = remote_openai_profile("tokenrouter")
        self.assertIsNotNone(profile)
        requests: list[dict[str, object]] = []

        def opener(request: Request, timeout: float):
            del timeout
            requests.append(json.loads(request.data))
            if len(requests) == 1:
                return _tool_call_response(
                    "call-search",
                    "search_workspace_text",
                    {"query": "outside-secret"},
                )
            return _content_response("moonshotai/kimi-k3-free", "검색했습니다.")

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            workspace = root / "workspace"
            workspace.mkdir()
            outside = root / "outside.txt"
            outside.write_text("outside-secret\n", encoding="utf-8")
            try:
                (workspace / "linked.txt").symlink_to(outside)
            except OSError as error:
                self.skipTest(f"symlinks are unavailable: {error}")
            runtime = RemoteOpenAICompatibleRuntime(
                "tokenrouter-worker",
                profile=profile,
                api_key="test-key",
                model="moonshotai/kimi-k3-free",
                max_output_tokens=4096,
                opener=opener,
                workspace=str(workspace),
                permission_mode="workspace_write",
            )
            runtime.send("선택한 작업 폴더에서 outside-secret을 찾아 줘.")
            runtime.read_output(timeout_seconds=2)

        tool_result = next(
            message for message in requests[1]["messages"] if message.get("role") == "tool"
        )
        self.assertEqual(json.loads(tool_result["content"])["matches"], [])


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
