from __future__ import annotations

import io
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from agentsassemble.deepseek_runtime import DeepSeekApiRuntime
from agentsassemble.process_environment import (
    environment_contains_secret_names,
    sanitized_provider_environment,
)
from agentsassemble.provider_capabilities import ProviderCapabilityCatalog
from agentsassemble.provider_secrets import ProviderSecretStore
from agentsassemble.room_attendee import _orientation_text, parse_agent_invite_url
from agentsassemble.room_attendee import AgentAttendee
from agentsassemble.windows_conpty import WindowsConPtyRuntime
from agentsassemble.live_cli_transcripts import _antigravity_user_request
from agentsassemble.live_cli import _terminal_query_response


class FakeKeyring:
    def __init__(self) -> None:
        self.values: dict[tuple[str, str], str] = {}

    def get_password(self, service: str, user: str):
        return self.values.get((service, user))

    def set_password(self, service: str, user: str, value: str) -> None:
        self.values[(service, user)] = value

    def delete_password(self, service: str, user: str) -> None:
        self.values.pop((service, user), None)


class FakeConPtyProcess:
    pid = 8123

    def __init__(self) -> None:
        self.closed = False
        self.writes: list[str] = []
        self.output: list[str] = []

    def isalive(self) -> bool:
        return not self.closed

    def write(self, value: str) -> None:
        self.writes.append(value)
        self.output.append("answer\r\n")

    def read(self, _size: int):
        if self.output:
            return self.output.pop(0)
        time.sleep(0.01)
        return ""

    def terminate(self) -> None:
        self.closed = True


class ProviderRuntimeControlTests(unittest.TestCase):
    def test_antigravity_plan_prefix_is_not_part_of_the_delivered_turn(self):
        self.assertEqual(
            _antigravity_user_request("<USER_REQUEST>\n/plan room input\n</USER_REQUEST>"),
            "room input",
        )

    def test_pty_answers_terminal_queries_needed_by_interactive_tuis(self):
        response = _terminal_query_response(b"\x1b[6n\x1b[c\x1b[?u\x1b]10;?\x1b\\\x1b]11;?\x1b\\")

        self.assertIn(b"\x1b[1;1R", response)
        self.assertIn(b"\x1b[?1;2c", response)
        self.assertIn(b"\x1b[?0u", response)
        self.assertIn(b"rgb:ffff", response)
        self.assertIn(b"rgb:0000", response)
        self.assertEqual(
            _antigravity_user_request("<USER_REQUEST>room input/plan</USER_REQUEST>"),
            "room input",
        )

    def test_provider_child_environment_drops_server_and_api_secrets(self):
        environment = sanitized_provider_environment(
            source={
                "HOME": "/home/test",
                "PATH": "/bin",
                "DEEPSEEK_API_KEY": "secret",
                "AGENTSASSEMBLE_INVITE_TOKEN": "invite",
            }
        )

        self.assertEqual(environment, {"HOME": "/home/test", "PATH": "/bin"})
        self.assertFalse(environment_contains_secret_names(environment))

    def test_secret_status_never_returns_value_or_secret_metadata(self):
        backend = FakeKeyring()
        store = ProviderSecretStore(backend=backend, environment={"DEEPSEEK_API_KEY": "fallback"})

        status = store.set("deepseek", "sk-private-value")

        self.assertEqual(status, {"configured": True, "source": "keyring"})
        self.assertNotIn("sk-private-value", json.dumps(status))
        self.assertEqual(store.get("deepseek"), "sk-private-value")

    def test_capability_probe_returns_native_controls_without_commands(self):
        def runner(command: list[str], _timeout: float):
            if command[1:3] == ["debug", "models"]:
                return 0, json.dumps({"models": [{"slug": "gpt-5.6-luna", "display_name": "Luna", "supported_reasoning_levels": [{"effort": "low"}], "service_tiers": [{"id": "priority"}]}]}), ""
            if command[1:] == ["models", "--verbose"]:
                return 0, "opencode-go/glm-5.2\n", ""
            return 0, "", ""

        catalog = ProviderCapabilityCatalog(runner=runner, resolver=lambda executable: f"/bin/{executable}")
        payload = catalog.payload(refresh=True)
        codex = next(item for item in payload if item["id"] == "codex")

        self.assertEqual(codex["discovery_status"], "ready")
        self.assertEqual(codex["catalog_source"], "discovered")
        self.assertEqual(codex["controls"][0]["default_value"], "gpt-5.6-luna")
        self.assertNotIn("command", codex)

    def test_cold_capability_catalog_is_loading_until_discovery_finishes(self):
        release = threading.Event()

        def runner(command: list[str], _timeout: float):
            release.wait(1)
            if command[1:3] == ["debug", "models"]:
                return 0, json.dumps({"models": [{"slug": "gpt-live"}]}), ""
            if command[1:] == ["models", "--verbose"]:
                return 0, "opencode/provider-live\n", ""
            if command[0].endswith("claude"):
                return 0, "Claude help", ""
            return 1, "", "unsupported"

        catalog = ProviderCapabilityCatalog(runner=runner, resolver=lambda executable: f"/bin/{executable}")
        initial = catalog.snapshot()

        self.assertEqual(initial["status"], "loading")
        self.assertEqual(initial["catalog_revision"], "")
        self.assertTrue(all(not provider["startable"] for provider in initial["providers"] if provider["id"] != "deepseek"))
        self.assertTrue(all(not provider["controls"] for provider in initial["providers"] if provider["catalog_source"] == "discovered"))

        release.set()
        final = catalog.snapshot(refresh=True)
        self.assertEqual(final["status"], "ready")
        self.assertTrue(str(final["catalog_revision"]).startswith("cat-"))
        codex = next(provider for provider in final["providers"] if provider["id"] == "codex")
        self.assertEqual(codex["controls"][0]["options"][0]["value"], "gpt-live")

    def test_capability_catalog_notifies_after_background_refresh(self):
        notified = threading.Event()
        snapshots: list[dict[str, object]] = []

        def runner(command: list[str], _timeout: float):
            if command[1:3] == ["debug", "models"]:
                return 0, json.dumps({"models": [{"slug": "gpt-live"}]}), ""
            if command[1:] == ["models", "--verbose"]:
                return 0, "opencode/provider-live\n", ""
            if command[0].endswith("claude"):
                return 0, "Claude help", ""
            return 1, "", "unsupported"

        catalog = ProviderCapabilityCatalog(runner=runner, resolver=lambda executable: f"/bin/{executable}")
        remove = catalog.subscribe(lambda snapshot: (snapshots.append(snapshot), notified.set()))
        try:
            self.assertEqual(catalog.snapshot()["status"], "loading")
            self.assertTrue(notified.wait(2))
        finally:
            remove()

        self.assertEqual(snapshots[-1]["status"], "ready")

    def test_deepseek_stream_emits_content_but_not_reasoning_or_key(self):
        captured = {}

        def opener(request, timeout: float):
            captured["headers"] = dict(request.header_items())
            captured["body"] = json.loads(request.data)
            return io.BytesIO(
                b'data: {"choices":[{"delta":{"reasoning_content":"private"}}]}\n\n'
                b'data: {"choices":[{"delta":{"content":"hello"}}]}\n\n'
                b'data: [DONE]\n\n'
            )

        runtime = DeepSeekApiRuntime("deepseek", api_key="sk-private", opener=opener)
        deltas: list[str] = []
        runtime.send("say hi")
        result = runtime.read_output(timeout_seconds=2, on_delta=deltas.append)

        self.assertEqual(result["content"], "hello")
        self.assertEqual(deltas, ["hello"])
        self.assertNotIn("private", json.dumps(result))
        self.assertNotIn("sk-private", json.dumps(runtime.health()))
        self.assertEqual(captured["body"]["reasoning_effort"], "high")

    def test_agent_invite_parser_and_orientation_hide_backend_details(self):
        server, token = parse_agent_invite_url("https://room.example/join?token=aai1.secret")
        orientation = _orientation_text({"welcome": "Welcome", "how_to": ["Speak naturally"]})

        self.assertEqual(server, "https://room.example")
        self.assertEqual(token, "aai1.secret")
        self.assertNotIn(token, orientation)
        self.assertNotIn("https://", orientation)

    def test_codex_attendee_uses_structured_persistent_cli_transport(self):
        attendee = AgentAttendee(
            invite_url="https://room.example/join?token=aai1.secret",
            provider_id="codex",
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            runtime = attendee._build_runtime("codex-guest", Path(temp_dir))
        self.assertIn("app-server", runtime.runtime.command)
        self.assertNotIn("exec", runtime.runtime.command)
        self.assertEqual(runtime.profile["sandbox"], "read-only")

        explicit = AgentAttendee(
            invite_url="https://room.example/join?token=aai1.secret",
            provider_id="codex",
            workspace="/tmp/user-workspace",
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            explicit_runtime = explicit._build_runtime("codex-guest", Path(temp_dir))
        self.assertIn("app-server", explicit_runtime.runtime.command)

    def test_windows_runtime_keeps_one_process_and_stops_it(self):
        fake = FakeConPtyProcess()
        with tempfile.TemporaryDirectory() as temp_dir, patch("shutil.which", return_value="C:/bin/fake.exe"):
            runtime = WindowsConPtyRuntime(
                "fake",
                ["fake"],
                cwd=Path(temp_dir),
                idle_quiet_seconds=0.02,
                process_factory=lambda *args, **kwargs: fake,
            )
            first_pid = runtime.start()["pid"]
            runtime.send("first")
            runtime.send("second")
            runtime.stop()

        self.assertEqual(first_pid, 8123)
        self.assertEqual(fake.writes, ["first\r", "second\r"])
        self.assertTrue(fake.closed)
        self.assertFalse(runtime.health()["running"])


if __name__ == "__main__":
    unittest.main()
