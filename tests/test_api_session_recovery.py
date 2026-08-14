from __future__ import annotations

import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from urllib.request import Request

from agentsassemble.providers.remote_openai import (
    RemoteOpenAICompatibleRuntime,
    remote_openai_profile,
)


class _Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()


class ApiSessionRecoveryTests(unittest.TestCase):
    def test_successful_write_checkpoint_clears_the_recovery_barrier(self):
        profile = remote_openai_profile("tokenrouter")
        self.assertIsNotNone(profile)
        requests = 0

        def opener(_request: Request, timeout: float):
            nonlocal requests
            del timeout
            requests += 1
            if requests == 1:
                return _tool_call_response(
                    "call-write",
                    "write_workspace_file",
                    {"path": "generated.txt", "content": "completed\n"},
                )
            return _content_response("moonshotai/kimi-k3-free", "write completed")

        def approve(_request, respond):
            respond({"option_id": "allow_once"})

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            workspace = root / "workspace"
            state_dir = root / "state"
            _initialize_git_workspace(workspace, {"tracked.txt": "baseline\n"})
            runtime = RemoteOpenAICompatibleRuntime(
                "tokenrouter-completed-write",
                profile=profile,
                api_key="test-key",
                model="moonshotai/kimi-k3-free",
                opener=opener,
                workspace=str(workspace),
                permission_mode="workspace_write",
                state_dir=str(state_dir),
            )
            runtime.set_request_handler(approve)
            runtime.send("write generated.txt")
            runtime.read_output(timeout_seconds=2)

            resumed = RemoteOpenAICompatibleRuntime(
                "tokenrouter-completed-write",
                profile=profile,
                api_key="test-key",
                model="moonshotai/kimi-k3-free",
                workspace=str(workspace),
                permission_mode="workspace_write",
                state_dir=str(state_dir),
                resume_required=True,
            )

        self.assertTrue(resumed.health()["provider_session_reused"])

    def test_recovery_blocks_when_the_workspace_changed_after_checkpoint(self):
        profile = remote_openai_profile("tokenrouter")
        self.assertIsNotNone(profile)
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            workspace = root / "workspace"
            state_dir = root / "state"
            _initialize_git_workspace(workspace, {"tracked.txt": "checkpoint state\n"})
            runtime = RemoteOpenAICompatibleRuntime(
                "tokenrouter-workspace-drift",
                profile=profile,
                api_key="test-key",
                model="moonshotai/kimi-k3-free",
                opener=lambda _request, timeout: _content_response(
                    "moonshotai/kimi-k3-free", "checkpointed"
                ),
                workspace=str(workspace),
                permission_mode="workspace_write",
                state_dir=str(state_dir),
            )
            runtime.send("remember this workspace")
            runtime.read_output(timeout_seconds=2)
            (workspace / "tracked.txt").write_text(
                "changed outside the session\n",
                encoding="utf-8",
            )

            with self.assertRaises(RuntimeError) as caught:
                RemoteOpenAICompatibleRuntime(
                    "tokenrouter-workspace-drift",
                    profile=profile,
                    api_key="test-key",
                    model="moonshotai/kimi-k3-free",
                    workspace=str(workspace),
                    permission_mode="workspace_write",
                    state_dir=str(state_dir),
                    resume_required=True,
                )

        self.assertEqual(caught.exception.code, "api_context_workspace_drift")

    def test_recovery_blocks_after_a_write_outlives_its_provider_turn(self):
        profile = remote_openai_profile("tokenrouter")
        self.assertIsNotNone(profile)
        requests = 0

        def opener(_request: Request, timeout: float):
            nonlocal requests
            del timeout
            requests += 1
            if requests == 1:
                return _content_response("moonshotai/kimi-k3-free", "baseline")
            if requests == 2:
                return _tool_call_response(
                    "call-write",
                    "write_workspace_file",
                    {"path": "generated.txt", "content": "side effect\n"},
                )
            raise ConnectionError("provider disconnected after the write")

        def approve(_request, respond):
            respond({"option_id": "allow_once"})

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            workspace = root / "workspace"
            state_dir = root / "state"
            _initialize_git_workspace(workspace, {"tracked.txt": "baseline\n"})
            runtime = RemoteOpenAICompatibleRuntime(
                "tokenrouter-interrupted-write",
                profile=profile,
                api_key="test-key",
                model="moonshotai/kimi-k3-free",
                opener=opener,
                workspace=str(workspace),
                permission_mode="workspace_write",
                state_dir=str(state_dir),
            )
            runtime.set_request_handler(approve)
            runtime.send("create a baseline checkpoint")
            runtime.read_output(timeout_seconds=2)
            runtime.send("write generated.txt")
            with self.assertRaisesRegex(ConnectionError, "disconnected"):
                runtime.read_output(timeout_seconds=2)
            self.assertEqual(
                (workspace / "generated.txt").read_text(encoding="utf-8"),
                "side effect\n",
            )

            with self.assertRaises(RuntimeError) as caught:
                RemoteOpenAICompatibleRuntime(
                    "tokenrouter-interrupted-write",
                    profile=profile,
                    api_key="test-key",
                    model="moonshotai/kimi-k3-free",
                    workspace=str(workspace),
                    permission_mode="workspace_write",
                    state_dir=str(state_dir),
                    resume_required=True,
                )

        self.assertEqual(caught.exception.code, "api_context_recovery_blocked")


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
    return _Response(
        f"data: {json.dumps(chunk, ensure_ascii=False)}\n\ndata: [DONE]\n\n".encode()
    )


def _content_response(model: str, content: str) -> _Response:
    chunk = {
        "model": model,
        "choices": [{"delta": {"content": content}}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 2},
    }
    return _Response(f"data: {json.dumps(chunk)}\n\ndata: [DONE]\n\n".encode())


def _initialize_git_workspace(workspace: Path, files: dict[str, str]) -> None:
    workspace.mkdir(parents=True)
    for relative, content in files.items():
        path = workspace / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    environment = {
        **os.environ,
        "GIT_AUTHOR_NAME": "AgentsAssemble Test",
        "GIT_AUTHOR_EMAIL": "tests@agentsassemble.invalid",
        "GIT_COMMITTER_NAME": "AgentsAssemble Test",
        "GIT_COMMITTER_EMAIL": "tests@agentsassemble.invalid",
    }
    for command in (
        ["git", "init", "--quiet"],
        ["git", "add", "."],
        ["git", "commit", "--quiet", "-m", "test fixture"],
    ):
        subprocess.run(
            command,
            cwd=workspace,
            env=environment,
            check=True,
            capture_output=True,
        )


class ApiConversationCheckpointTests(unittest.TestCase):
    def test_persist_rebuilds_missing_tool_result_backing_files(self) -> None:
        from agentsassemble.providers.api_session import ApiConversationStore

        with tempfile.TemporaryDirectory() as temp_dir:
            store = ApiConversationStore(
                temp_dir,
                agent_id="deepseek-session",
                provider_name="DeepSeek",
                model="deepseek-v4-flash",
                workspace=temp_dir,
                permission_mode="meeting_read_only",
            )
            messages = [
                {"role": "user", "content": "read the file"},
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {
                            "id": "call-1",
                            "type": "function",
                            "function": {"name": "read_discussion", "arguments": "{}"},
                        }
                    ],
                },
                {
                    "role": "tool",
                    "tool_call_id": "call-1",
                    "name": "read_discussion",
                    "content": '{"ok":true}',
                },
            ]
            persisted = store.persist(messages, {"call-1"}, {})
            tool = next(item for item in persisted if item.get("role") == "tool")
            self.assertIn("aa-tool-result://sha256/", tool["content"])
            loaded_messages, delivered, references = store.load()
            self.assertIn("call-1", delivered)
            self.assertIn("call-1", references)


if __name__ == "__main__":
    unittest.main()
