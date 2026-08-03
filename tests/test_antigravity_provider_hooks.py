from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from agentsassemble.providers.antigravity_hooks import AntigravityHookRuntime
from agentsassemble.providers.process_environment import sanitized_provider_environment


class _FakeTerminalRuntime:
    def __init__(self, agent_id: str, command: list[str], **_kwargs: object) -> None:
        self.agent_id = agent_id
        self.command = list(command)
        self.last_seen_event_id = ""
        self.running = False

    def start(self) -> dict[str, object]:
        self.running = True
        return self.health()

    def send(self, _text: str) -> None:
        pass

    def send_room_observation(self, _text: str, *, media_blocks=None) -> None:
        del media_blocks

    def read_output(self, **_kwargs: object) -> dict[str, object]:
        return {"kind": "agent_message", "content": "done"}

    def interrupt(self) -> None:
        pass

    def stop(self, *, timeout_seconds: float = 2.0) -> None:
        del timeout_seconds
        self.running = False

    def health(self) -> dict[str, object]:
        return {"running": self.running, "command_configured": list(self.command)}


class AntigravityProviderHookTests(unittest.TestCase):
    def test_provider_environment_only_accepts_explicit_hook_session_credentials(self) -> None:
        environment = sanitized_provider_environment(
            {
                "AGENTSASSEMBLE_ANTIGRAVITY_HOOK_ENDPOINT": "http://127.0.0.1:1234/hook",
                "AGENTSASSEMBLE_ANTIGRAVITY_HOOK_TOKEN": "ephemeral",
                "AGENTSASSEMBLE_HOST_TOKEN": "server-secret",
            },
            source={"HOME": "/home/test", "PATH": "/bin"},
        )

        self.assertEqual(
            environment,
            {
                "HOME": "/home/test",
                "PATH": "/bin",
                "AGENTSASSEMBLE_ANTIGRAVITY_HOOK_ENDPOINT": "http://127.0.0.1:1234/hook",
                "AGENTSASSEMBLE_ANTIGRAVITY_HOOK_TOKEN": "ephemeral",
            },
        )

    def test_hook_process_round_trips_permission_and_question_through_room_handler(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            runtime_environment: dict[str, str] = {}

            def terminal_runtime(agent_id, command, **kwargs):
                runtime_environment.update(kwargs.get("env") or {})
                return _FakeTerminalRuntime(agent_id, command, **kwargs)

            runtime = AntigravityHookRuntime(
                "agy-agent",
                ["agy", "--sandbox"],
                cwd=temp_dir,
                terminal_runtime_factory=terminal_runtime,
            )
            requests: list[dict[str, object]] = []

            def resolve(request: dict[str, object], respond) -> None:
                requests.append(request)
                if request["response_kind"] == "answers":
                    respond({"answers": {"question-0": ["둘째 안"]}})
                else:
                    respond({"option_id": "allow-once"})

            runtime.set_request_handler(resolve)
            runtime.start()
            try:
                hooks_path = Path(runtime.health()["provider_request_hooks_path"])
                hooks = json.loads(hooks_path.read_text(encoding="utf-8"))
                command = hooks["agentsassemble-room-requests"]["PreToolUse"][0]["hooks"][0][
                    "command"
                ]

                permission = subprocess.run(
                    command,
                    input=json.dumps(
                        {
                            "toolCall": {
                                "name": "run_command",
                                "args": {"CommandLine": "git status", "Cwd": temp_dir},
                            },
                            "conversationId": "agy-conversation",
                        }
                    ),
                    text=True,
                    shell=True,
                    capture_output=True,
                    env={**os.environ, **runtime_environment},
                    timeout=5,
                    check=True,
                )
                question = subprocess.run(
                    command,
                    input=json.dumps(
                        {
                            "toolCall": {
                                "name": "ask_question",
                                "args": {
                                    "questions": [
                                        {
                                            "question": "어느 안으로 갈까요?",
                                            "options": ["첫째 안", "둘째 안"],
                                            "is_multi_select": False,
                                        }
                                    ]
                                },
                            },
                            "conversationId": "agy-conversation",
                        }
                    ),
                    text=True,
                    shell=True,
                    capture_output=True,
                    env={**os.environ, **runtime_environment},
                    timeout=5,
                    check=True,
                )
            finally:
                runtime.stop()

            self.assertFalse(hooks_path.exists())

        self.assertEqual(json.loads(permission.stdout)["decision"], "allow")
        question_result = json.loads(question.stdout)
        self.assertEqual(question_result["decision"], "deny")
        self.assertIn("둘째 안", question_result["reason"])
        self.assertEqual([request["request_kind"] for request in requests], ["permission", "user_input"])
        self.assertEqual(requests[1]["questions"][0]["options"][1]["label"], "둘째 안")

    def test_room_portal_command_is_allowed_without_opening_a_room_request(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            runtime = AntigravityHookRuntime(
                "agy-agent",
                ["agy", "--sandbox"],
                cwd=temp_dir,
                terminal_runtime_factory=_FakeTerminalRuntime,
            )
            requests: list[dict[str, object]] = []
            runtime.set_request_handler(lambda request, _respond: requests.append(request))
            runtime.start()
            try:
                result = runtime.handle_hook(
                    {
                        "toolCall": {
                            "name": "run_command",
                            "args": {"CommandLine": "agentsassemble-room read", "Cwd": temp_dir},
                        }
                    }
                )
            finally:
                runtime.stop()

        self.assertEqual(result["decision"], "allow")
        self.assertEqual(requests, [])

    def test_runtime_restores_existing_workspace_hooks_after_stop(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            hooks_path = Path(temp_dir) / ".agents" / "hooks.json"
            hooks_path.parent.mkdir()
            original = {
                "user-linter": {
                    "PostToolUse": [
                        {
                            "matcher": "run_command",
                            "hooks": [{"type": "command", "command": "lint"}],
                        }
                    ]
                }
            }
            hooks_path.write_text(json.dumps(original), encoding="utf-8")
            runtime = AntigravityHookRuntime(
                "agy-agent",
                ["agy", "--sandbox"],
                cwd=temp_dir,
                terminal_runtime_factory=_FakeTerminalRuntime,
            )

            runtime.start()
            active = json.loads(hooks_path.read_text(encoding="utf-8"))
            runtime.stop()
            restored = json.loads(hooks_path.read_text(encoding="utf-8"))

        self.assertIn("agentsassemble-room-requests", active)
        self.assertEqual(restored, original)


if __name__ == "__main__":
    unittest.main()
