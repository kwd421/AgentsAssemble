from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from urllib.request import Request, urlopen

from agentsassemble.providers.claude_hooks import ClaudeHookRuntime


class _FakeTerminalRuntime:
    def __init__(self, agent_id: str, command: list[str], **kwargs: object) -> None:
        self.agent_id = agent_id
        self.command = list(command)
        self.environment = dict(kwargs.get("env") or {})
        self.running = False

    def start(self) -> dict[str, object]:
        self.running = True
        return self.health()

    def stop(self, *, timeout_seconds: float = 2.0) -> None:
        del timeout_seconds
        self.running = False

    def health(self) -> dict[str, object]:
        return {"running": self.running, "command_configured": list(self.command)}


def _post_hook(
    endpoint: str,
    token: str,
    payload: dict[str, object],
) -> dict[str, object]:
    request = Request(
        endpoint,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urlopen(request, timeout=5) as response:
        return json.loads(response.read().decode("utf-8"))


class ClaudeProviderHookTests(unittest.TestCase):
    def test_interactive_session_round_trips_permissions_and_questions_through_native_hooks(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            captured_runtime: list[_FakeTerminalRuntime] = []

            def terminal_runtime(agent_id: str, command: list[str], **kwargs: object):
                runtime = _FakeTerminalRuntime(agent_id, command, **kwargs)
                captured_runtime.append(runtime)
                return runtime

            runtime = ClaudeHookRuntime(
                "claude-agent",
                ["claude", "--model", "claude-sonnet-5"],
                cwd=root / "workspace",
                state_dir=root / "provider-state",
                terminal_runtime_factory=terminal_runtime,
            )
            requests: list[dict[str, object]] = []

            def resolve(request: dict[str, object], respond) -> None:
                requests.append(request)
                if request["response_kind"] == "answers":
                    respond({"answers": {"question-0": ["React", "TypeScript"]}})
                else:
                    respond({"option_id": "allow-once"})

            runtime.set_request_handler(resolve)
            runtime.start()
            settings_path = Path(runtime.health()["provider_request_settings_path"])
            terminal = captured_runtime[0]
            hook_token = terminal.environment["AGENTSASSEMBLE_CLAUDE_HOOK_TOKEN"]
            settings = json.loads(settings_path.read_text(encoding="utf-8"))
            hook_endpoint = settings["hooks"]["PreToolUse"][0]["hooks"][0]["url"]
            try:
                permission = _post_hook(
                    hook_endpoint,
                    hook_token,
                    {
                        "hook_event_name": "PermissionRequest",
                        "tool_name": "Bash",
                        "tool_input": {"command": "npm test"},
                    },
                )
                question = _post_hook(
                    hook_endpoint,
                    hook_token,
                    {
                        "hook_event_name": "PreToolUse",
                        "tool_name": "AskUserQuestion",
                        "tool_input": {
                            "questions": [
                                {
                                    "header": "Stack",
                                    "question": "Which stack?",
                                    "options": [
                                        {"label": "React", "description": "UI"},
                                        {"label": "Vue", "description": "UI"},
                                    ],
                                    "multiSelect": True,
                                }
                            ]
                        },
                    },
                )
                plan = _post_hook(
                    hook_endpoint,
                    hook_token,
                    {
                        "hook_event_name": "PreToolUse",
                        "tool_name": "ExitPlanMode",
                        "tool_input": {"allowedPrompts": []},
                    },
                )

                settings_index = terminal.command.index("--settings") + 1

                self.assertEqual(Path(terminal.command[settings_index]), settings_path)
                self.assertEqual(
                    settings["hooks"]["PreToolUse"][0]["matcher"],
                    "AskUserQuestion|ExitPlanMode",
                )
                self.assertEqual(
                    permission["hookSpecificOutput"]["decision"]["behavior"],
                    "allow",
                )
                self.assertEqual(
                    question["hookSpecificOutput"]["updatedInput"]["answers"],
                    {"Which stack?": "React, TypeScript"},
                )
                self.assertEqual(
                    plan["hookSpecificOutput"]["permissionDecision"],
                    "allow",
                )
                self.assertEqual(
                    [request["request_kind"] for request in requests],
                    ["permission", "user_input", "permission"],
                )
            finally:
                runtime.stop()

            self.assertFalse(settings_path.exists())

    def test_permission_hook_failure_returns_a_native_permission_denial(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            captured_runtime: list[_FakeTerminalRuntime] = []

            def terminal_runtime(agent_id: str, command: list[str], **kwargs: object):
                runtime = _FakeTerminalRuntime(agent_id, command, **kwargs)
                captured_runtime.append(runtime)
                return runtime

            runtime = ClaudeHookRuntime(
                "claude-agent",
                ["claude", "--model", "claude-sonnet-5"],
                cwd=root / "workspace",
                state_dir=root / "provider-state",
                terminal_runtime_factory=terminal_runtime,
            )

            def fail_request(_request: dict[str, object], _respond) -> None:
                raise RuntimeError("room request transport failed")

            runtime.set_request_handler(fail_request)
            runtime.start()
            settings_path = Path(runtime.health()["provider_request_settings_path"])
            terminal = captured_runtime[0]
            hook_token = terminal.environment["AGENTSASSEMBLE_CLAUDE_HOOK_TOKEN"]
            settings = json.loads(settings_path.read_text(encoding="utf-8"))
            hook_endpoint = settings["hooks"]["PermissionRequest"][0]["hooks"][0]["url"]
            try:
                permission = _post_hook(
                    hook_endpoint,
                    hook_token,
                    {
                        "hook_event_name": "PermissionRequest",
                        "tool_name": "Bash",
                        "tool_input": {"command": "npm test"},
                    },
                )
            finally:
                runtime.stop()

        decision = permission["hookSpecificOutput"]
        self.assertEqual(decision["hookEventName"], "PermissionRequest")
        self.assertEqual(decision["decision"]["behavior"], "deny")
        self.assertIn("room request transport failed", decision["decision"]["message"])


if __name__ == "__main__":
    unittest.main()
