from __future__ import annotations

import io
import json
import tempfile
import unittest
from pathlib import Path
from urllib.request import Request

from agentsassemble.providers.remote_openai import (
    RemoteOpenAICompatibleRuntime,
    remote_openai_profile,
)
from agentsassemble.providers.room_portal import RoomPortal


class _Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()


class OpenAICompatibleRoomActionTests(unittest.TestCase):
    def test_runtime_exposes_active_plugin_tools_and_stages_provider_actions(self):
        from plugins.rimworld.server.sim import ColonySimulation

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
                return _tool_call_response("call-observe", "rimworld_observe", {})
            if len(requests) == 3:
                return _tool_calls_response(
                    [
                        (
                            "call-act",
                            "rimworld_act",
                            {"action": "eat", "action_args": {}},
                        ),
                        (
                            "call-speak",
                            "rimworld_speak",
                            {"text": "식량을 확인합니다."},
                        ),
                    ]
                )
            return _content_response("openai/test", "colony action staged")

        with tempfile.TemporaryDirectory() as temp_dir:
            portal = RoomPortal(Path(temp_dir), participant_id="agent-a")
            portal.prepare()
            portal.ingest_frame(
                {
                    "room_settings": {"activity_plugin": "rimworld"},
                    "participants": [
                        {"participant_id": "agent-a", "participant_type": "agent"},
                        {"participant_id": "agent-b", "participant_type": "agent"},
                        {"participant_id": "agent-c", "participant_type": "agent"},
                    ],
                }
            )
            portal.ingest_frame(
                {
                    "stream": "plugin",
                    "events": [
                        {
                            "type": "plugin.snapshot",
                            "plugin_id": "rimworld",
                            "payload": ColonySimulation(seed=3).snapshot(),
                        }
                    ],
                }
            )
            portal.begin_observation("turn-plugin", input_up_to_seq=0)
            runtime = RemoteOpenAICompatibleRuntime(
                "agent-a",
                profile=profile,
                api_key="test-key",
                model="openai/test",
                opener=opener,
                room_portal=portal,
            )

            runtime.send_room_observation("room.wake turn-plugin")
            result = runtime.read_output(timeout_seconds=2)
            batch = portal.activity_plugin_command_batch("turn-plugin")

        offered_names = {
            tool["function"]["name"]
            for tool in requests[0]["tools"]
        }
        self.assertTrue(
            {"rimworld_observe", "rimworld_inspect", "rimworld_act", "rimworld_speak"}
            .issubset(offered_names)
        )
        self.assertTrue(
            all(
                message.get("name", "").replace("_", "").isalnum()
                for request in requests[1:]
                for message in request["messages"]
                if message.get("role") == "tool"
            )
        )
        self.assertEqual(result["content"], "colony action staged")
        self.assertEqual(batch["args"]["colonist_id"], "c1")
        self.assertEqual(batch["args"]["act"]["action"], "eat")
        self.assertEqual(batch["args"]["speak"], "식량을 확인합니다.")

    def test_runtime_reads_and_publishes_through_room_tools(self):
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
            raise AssertionError(
                "The provider was called again after its public room action."
            )

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
        self.assertEqual(len(requests), 2)
        self.assertEqual(
            requests[0]["tool_choice"],
            {"type": "function", "function": {"name": "read_discussion"}},
        )
        self.assertEqual(requests[1]["tool_choice"], "auto")
        self.assertTrue(all(request["max_tokens"] == 8192 for request in requests))
        self.assertNotIn("secret-never-reported", json.dumps(result))
        self.assertNotIn("secret-never-reported", json.dumps(runtime.health()))

    def test_first_terminal_action_completes_when_provider_batches_extra_actions(self):
        profile = remote_openai_profile("openrouter")
        self.assertIsNotNone(profile)

        calls = 0

        def opener(_request: Request, timeout: float):
            nonlocal calls
            del timeout
            calls += 1
            if calls == 1:
                return _tool_calls_response(
                    [
                        ("call-read", "read_discussion", {}),
                        (
                            "call-publish-before-read-completes",
                            "publish_message",
                            {"content": "읽기와 함께 실행되면 안 되는 발언"},
                        ),
                    ]
                )
            return _tool_calls_response(
                [
                    (
                        "call-publish-first",
                        "publish_message",
                        {"content": "첫 공개 발언"},
                    ),
                    (
                        "call-publish-extra",
                        "publish_message",
                        {"content": "실행되면 안 되는 추가 발언"},
                    ),
                ]
            )

        with tempfile.TemporaryDirectory() as temp_dir:
            portal = RoomPortal(Path(temp_dir), participant_id="openrouter-agent")
            portal.prepare()
            portal.begin_observation("turn-1", input_up_to_seq=0)
            runtime = RemoteOpenAICompatibleRuntime(
                "openrouter-agent",
                profile=profile,
                api_key="test-key",
                model="openai/gpt-oss-20b:free",
                opener=opener,
                room_portal=portal,
            )

            runtime.send_room_observation("room.wake turn-1")
            result = runtime.read_output(timeout_seconds=2)
            publication = portal.consume_publication("turn-1")

        self.assertEqual(publication, "첫 공개 발언")
        self.assertEqual(result["metadata"]["room_tool_rounds"], 2)
        self.assertEqual(
            result["metadata"]["discarded_after_terminal_tool_calls"],
            2,
        )


def _tool_call_response(
    call_id: str,
    name: str,
    arguments: dict[str, object],
) -> _Response:
    return _tool_calls_response([(call_id, name, arguments)])


def _tool_calls_response(
    calls: list[tuple[str, str, dict[str, object]]],
) -> _Response:
    chunk = {
        "choices": [
            {
                "delta": {
                    "tool_calls": [
                        {
                            "index": index,
                            "id": call_id,
                            "type": "function",
                            "function": {
                                "name": name,
                                "arguments": json.dumps(arguments, ensure_ascii=False),
                            },
                        }
                        for index, (call_id, name, arguments) in enumerate(calls)
                    ]
                }
            }
        ]
    }
    body = f"data: {json.dumps(chunk, ensure_ascii=False)}\n\ndata: [DONE]\n\n"
    return _Response(body.encode())


def _content_response(model: str, content: str) -> _Response:
    chunk = {
        "model": model,
        "choices": [{"delta": {"content": content}}],
    }
    body = f"data: {json.dumps(chunk, ensure_ascii=False)}\n\ndata: [DONE]\n\n"
    return _Response(body.encode())
