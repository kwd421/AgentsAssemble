from __future__ import annotations

import io
import json
import tempfile
import threading
import time
import unittest
from urllib.error import HTTPError
from pathlib import Path
from unittest.mock import MagicMock, patch

from agentsassemble.providers.deepseek import DeepSeekApiRuntime
from agentsassemble.diagnostics.cleanup import CleanupReport
from agentsassemble.providers.codex_app_server_live import CodexAppServerLiveRuntime
from agentsassemble.providers.process_environment import (
    environment_contains_secret_names,
    sanitized_provider_environment,
)
from agentsassemble.providers.capabilities import (
    ProviderCapabilityCatalog,
    ProviderCatalogSelectionError,
)
from agentsassemble.providers.secrets import ProviderSecretStore
from agentsassemble.providers.runtime_config import ProviderRuntimeConfig
from agentsassemble.application.room_attendee import _leave_room, _orientation_text, parse_agent_invite_url
from agentsassemble.application.room_attendee import AgentAttendee
from agentsassemble.providers.windows_conpty import WindowsConPtyRuntime
from agentsassemble.providers.live_cli import _terminal_query_response
from agentsassemble.providers.live_cli_transcripts import _antigravity_user_request


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

    def __init__(self, *, initial_output: list[str] | None = None) -> None:
        self.closed = False
        self.writes: list[str] = []
        self.output: list[str] = list(initial_output or [])

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
        self.assertNotIn("resolved_executable", codex)

    def test_claude_catalog_distinguishes_exact_models_from_latest_aliases(self):
        catalog = ProviderCapabilityCatalog(
            runner=lambda _command, _timeout: (0, "Claude help", ""),
            resolver=lambda executable: f"/bin/{executable}",
        )

        claude = next(item for item in catalog.payload(refresh=True) if item["id"] == "claude")
        model = next(control for control in claude["controls"] if control["key"] == "model")
        options = {option["value"]: option["label"] for option in model["options"]}

        self.assertEqual(model["default_value"], "claude-haiku-4-5")
        self.assertEqual(options["claude-sonnet-4-6"], "Claude Sonnet 4.6")
        self.assertEqual(options["sonnet"], "Sonnet (latest alias)")

        revision = str(catalog.snapshot()["catalog_revision"])
        common = {
            "reasoning_effort": "low",
            "service_tier": "default",
            "permission_mode": "meeting_read_only",
        }
        exact = catalog.validate_selection(
            catalog_revision=revision,
            provider_id="claude",
            values={**common, "model": "claude-sonnet-4-6"},
        )
        alias = catalog.validate_selection(
            catalog_revision=revision,
            provider_id="claude",
            values={**common, "model": "sonnet"},
        )

        self.assertEqual(exact.model_selection_kind, "exact")
        self.assertEqual(alias.model_selection_kind, "alias")
        self.assertEqual(exact.catalog_revision, revision)

    def test_catalog_rejects_model_effort_and_service_tier_mismatches(self):
        def runner(command: list[str], _timeout: float):
            if command[1:3] == ["debug", "models"]:
                return 0, json.dumps(
                    {
                        "models": [
                            {
                                "slug": "model-low",
                                "supported_reasoning_levels": [{"effort": "low"}],
                                "service_tiers": [{"id": "priority"}],
                            },
                            {
                                "slug": "model-high",
                                "supported_reasoning_levels": [{"effort": "high"}],
                                "service_tiers": [],
                            },
                        ]
                    }
                ), ""
            if command[1:] == ["models", "--verbose"]:
                return 0, "opencode/provider-live\n", ""
            if command[0].endswith("claude"):
                return 0, "Claude help", ""
            return 1, "", "unsupported"

        catalog = ProviderCapabilityCatalog(
            runner=runner,
            resolver=lambda executable: f"/bin/{executable}",
        )
        revision = str(catalog.snapshot(refresh=True)["catalog_revision"])
        common = {
            "model": "model-high",
            "reasoning_effort": "high",
            "service_tier": "default",
            "permission_mode": "meeting_read_only",
        }

        catalog.validate_selection(
            catalog_revision=revision,
            provider_id="codex",
            values=common,
        )
        with self.assertRaises(ProviderCatalogSelectionError) as effort:
            catalog.validate_selection(
                catalog_revision=revision,
                provider_id="codex",
                values={**common, "reasoning_effort": "low"},
            )
        self.assertEqual(effort.exception.code, "unsupported_model_effort_combination")
        with self.assertRaises(ProviderCatalogSelectionError) as tier:
            catalog.validate_selection(
                catalog_revision=revision,
                provider_id="codex",
                values={**common, "service_tier": "priority"},
            )
        self.assertEqual(tier.exception.code, "unsupported_model_service_tier_combination")

    def test_catalog_rejects_missing_model_relation_scope(self):
        def runner(command: list[str], _timeout: float):
            if command[1:3] == ["debug", "models"]:
                return 0, json.dumps(
                    {
                        "models": [
                            {
                                "slug": "model-low",
                                "supported_reasoning_levels": [{"effort": "low"}],
                            }
                        ]
                    }
                ), ""
            if command[1:] == ["models", "--verbose"]:
                return 0, "opencode/provider-live\n", ""
            if command[0].endswith("claude"):
                return 0, "Claude help", ""
            return 1, "", "unsupported"

        catalog = ProviderCapabilityCatalog(
            runner=runner,
            resolver=lambda executable: f"/bin/{executable}",
        )
        revision = str(catalog.snapshot(refresh=True)["catalog_revision"])
        with catalog._lock:
            codex = next(provider for provider in catalog._cached if provider["id"] == "codex")
            model_control = next(control for control in codex["controls"] if control["key"] == "model")
            model_control["options"][0]["metadata"].pop("relation_scope")

        with self.assertRaises(ProviderCatalogSelectionError) as rejected:
            catalog.validate_selection(
                catalog_revision=revision,
                provider_id="codex",
                values={
                    "model": "model-low",
                    "reasoning_effort": "low",
                    "service_tier": "default",
                    "permission_mode": "meeting_read_only",
                },
            )

        self.assertEqual(rejected.exception.code, "catalog_invalid")

    def test_expired_catalog_is_visible_but_not_startable_during_refresh(self):
        block_refresh = [False]
        refresh_started = threading.Event()
        release = threading.Event()

        def runner(command: list[str], _timeout: float):
            if block_refresh[0]:
                refresh_started.set()
                release.wait(2)
            if command[1:3] == ["debug", "models"]:
                return 0, json.dumps({"models": [{"slug": "gpt-live"}]}), ""
            if command[1:] == ["models", "--verbose"]:
                return 0, "opencode/provider-live\n", ""
            if command[0].endswith("claude"):
                return 0, "Claude help", ""
            return 1, "", "unsupported"

        catalog = ProviderCapabilityCatalog(
            runner=runner,
            resolver=lambda executable: f"/bin/{executable}",
        )
        ready = catalog.snapshot(refresh=True)
        revision = str(ready["catalog_revision"])
        block_refresh[0] = True
        catalog._cached_at = time.monotonic() - catalog._ttl_seconds - 1.0
        stale = catalog.snapshot()
        try:
            self.assertTrue(refresh_started.wait(1))
            self.assertEqual(stale["status"], "loading")
            self.assertTrue(all(not provider["startable"] for provider in stale["providers"]))
            self.assertTrue(all(provider["catalog_source"] == "stale_cache" for provider in stale["providers"]))
            with self.assertRaises(ProviderCatalogSelectionError) as rejected:
                catalog.validate_selection(
                    catalog_revision=revision,
                    provider_id="codex",
                    values={
                        "model": "gpt-live",
                        "reasoning_effort": "",
                        "service_tier": "default",
                        "permission_mode": "meeting_read_only",
                    },
                )
            self.assertEqual(rejected.exception.code, "catalog_not_ready")
        finally:
            release.set()

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
        self.assertTrue(all("resolved_executable" not in provider for provider in initial["providers"]))

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

    def test_catalog_listener_failure_is_isolated_and_recorded(self):
        successful_calls: list[str] = []

        def runner(command: list[str], _timeout: float):
            if command[1:3] == ["debug", "models"]:
                return 0, json.dumps({"models": [{"slug": "gpt-live"}]}), ""
            if command[1:] == ["models", "--verbose"]:
                return 0, "opencode/provider-live\n", ""
            if command[0].endswith("claude"):
                return 0, "Claude help", ""
            return 1, "", "unsupported"

        def failing_listener(_snapshot):
            raise RuntimeError("listener failed")

        catalog = ProviderCapabilityCatalog(runner=runner, resolver=lambda executable: f"/bin/{executable}")
        remove_failing = catalog.subscribe(failing_listener)
        remove_successful = catalog.subscribe(lambda snapshot: successful_calls.append(str(snapshot["status"])))
        try:
            snapshot = catalog.snapshot(refresh=True)
        finally:
            remove_failing()
            remove_successful()

        self.assertEqual(successful_calls, ["ready"])
        self.assertNotIn("diagnostics", snapshot)
        diagnostics = catalog.diagnostics()
        self.assertEqual(diagnostics["catalog_listener_error_count"], 1)
        self.assertIn("failing_listener", diagnostics["listener_type"])
        self.assertEqual(diagnostics["exception_type"], "RuntimeError")
        self.assertEqual(diagnostics["category"], "refresh_ready")
        self.assertTrue(diagnostics["last_failure_at"])

    def test_deepseek_stream_emits_content_but_not_reasoning_or_key(self):
        captured = {}

        def opener(request, timeout: float):
            captured["headers"] = dict(request.header_items())
            captured["body"] = json.loads(request.data)
            return io.BytesIO(
                b'data: {"choices":[{"delta":{"reasoning_content":"private"}}]}\n\n'
                b'data: {"model":"deepseek-v4-flash","choices":[{"delta":{"content":"hello"}}]}\n\n'
                b'data: [DONE]\n\n'
            )

        runtime = DeepSeekApiRuntime("deepseek", api_key="sk-private", opener=opener)
        deltas: list[str] = []
        runtime.send("say hi")
        result = runtime.read_output(timeout_seconds=2, on_delta=deltas.append)

        self.assertEqual(result["content"], "hello")
        self.assertEqual(result["metadata"]["observed_model_id"], "deepseek-v4-flash")
        self.assertEqual(deltas, ["hello"])
        self.assertNotIn("private", json.dumps(result))
        self.assertNotIn("sk-private", json.dumps(runtime.health()))
        self.assertEqual(captured["body"]["reasoning_effort"], "high")

    def test_cleanup_report_redacts_secret_like_error_values(self):
        report = CleanupReport("test")
        report.record_failure(
            "runtime.stop",
            RuntimeError("token=aai1.secret-value api_key=sk-secretvalue"),
            handle_id="owned-handle",
        )

        payload = json.dumps(report.as_dict())
        self.assertNotIn("secret-value", payload)
        self.assertNotIn("sk-secretvalue", payload)
        self.assertIn("[redacted]", payload)

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

    def test_codex_app_server_live_runtime_reports_provider_observed_model(self):
        class FakeAppServer:
            def send_turn(self, handle, packet):
                del handle, packet
                return iter([{"type": "message_final", "content": "hello"}])

            def diagnose(self, handle):
                del handle
                return {"observed_model_id": "gpt-5.6-luna"}

        runtime = CodexAppServerLiveRuntime(
            "codex-guest",
            workspace="/tmp/room",
            model="gpt-5.6-luna",
            reasoning_effort="low",
            permission_mode="meeting_read_only",
        )
        runtime.runtime = FakeAppServer()
        runtime.pending = "hello"

        result = runtime.read_output(timeout_seconds=2)

        self.assertEqual(result["metadata"]["observed_model_id"], "gpt-5.6-luna")

    def test_non_codex_attendees_build_complete_provider_runtime_configs(self):
        captured: list[tuple[ProviderRuntimeConfig, str]] = []
        opencode_server = MagicMock()
        opencode_server.start.return_value = {"endpoint": "http://127.0.0.1:43210", "pid": 43210}

        def capture(config: ProviderRuntimeConfig, *, credential: str = ""):
            captured.append((config, credential))
            return config

        with (
            tempfile.TemporaryDirectory() as temp_dir,
            patch("agentsassemble.application.room_attendee.runtime_from_config", side_effect=capture),
            patch("agentsassemble.application.room_attendee.OpenCodeServerProcess", return_value=opencode_server),
            patch("agentsassemble.application.room_attendee.PROVIDER_SECRETS.get", return_value="deepseek-secret"),
        ):
            workspace = Path(temp_dir)
            for provider_id in ("claude", "grok", "antigravity", "opencode", "deepseek"):
                attendee = AgentAttendee(
                    invite_url="https://room.example/join?token=aai1.secret",
                    provider_id=provider_id,
                )
                runtime = attendee._build_runtime(f"{provider_id}-guest", workspace)
                self.assertIsInstance(runtime, ProviderRuntimeConfig)

        self.assertEqual([config.participant_id for config, _ in captured], [
            "claude-guest",
            "grok-guest",
            "antigravity-guest",
            "opencode-guest",
            "deepseek-guest",
        ])
        self.assertEqual(captured[1][0].transport, "acp_stdio")
        self.assertEqual(captured[3][0].provider_endpoint, "http://127.0.0.1:43210")
        self.assertEqual(captured[4][1], "deepseek-secret")
        self.assertNotIn("deepseek-secret", repr(captured[4][0]))

    def test_attendee_cleanup_continues_after_runtime_stop_failure(self):
        calls: list[str] = []

        class Runtime:
            def stop(self, *, timeout_seconds=2.0):
                del timeout_seconds
                calls.append("runtime")
                raise RuntimeError("provider refused to stop")

            def health(self):
                return {"running": True}

        class Process:
            def poll(self):
                return 0

        class OpenCodeServer:
            process = Process()

            def stop(self):
                calls.append("opencode")

        class Temporary:
            def cleanup(self):
                calls.append("temporary")

        attendee = AgentAttendee(
            invite_url="https://room.example/join?token=aai1.secret",
            provider_id="opencode",
        )
        attendee._runtime = Runtime()
        attendee._opencode_server = OpenCodeServer()
        with patch("agentsassemble.application.room_attendee._leave_room", side_effect=lambda *_: calls.append("leave")):
            report = attendee._cleanup(session_token="session-secret", temporary=Temporary())

        self.assertEqual(calls, ["runtime", "opencode", "leave", "temporary"])
        self.assertFalse(report.ok)
        self.assertEqual(report.attempted, 4)
        self.assertEqual(report.completed, 3)
        self.assertEqual(report.orphaned_handle_ids, ["provider-runtime"])

    def test_attendee_exits_cleanly_after_remote_stop(self):
        runtime = MagicMock()
        bridge = MagicMock()
        bridge.run.return_value = 0
        bridge.remote_stop_requested = True
        attendee = AgentAttendee(
            invite_url="https://room.example/join?token=aai1.secret",
            provider_id="claude",
        )
        attendee._build_runtime = MagicMock(return_value=runtime)

        with (
            patch(
                "agentsassemble.application.room_attendee.join_agent_room_session",
                return_value={
                    "session_token": "session-secret",
                    "agent_id": "claude-guest",
                    "meeting_id": "general",
                    "provider_kind": "claude_code",
                    "guide": {},
                },
            ),
            patch("agentsassemble.application.room_attendee.connect_room_ws", return_value=object()) as connect,
            patch("agentsassemble.application.room_attendee.RoomAgentBridge", return_value=bridge),
            patch("agentsassemble.application.room_attendee._leave_room"),
        ):
            result = attendee.run()

        self.assertEqual(result, 0)
        self.assertEqual(connect.call_count, 1)
        runtime.stop.assert_not_called()

    def test_attendee_rejects_an_agent_invite_without_an_explicit_provider(self):
        attendee = AgentAttendee(
            invite_url="https://room.example/join?token=aai1.secret",
            provider_id="claude",
        )
        attendee._build_runtime = MagicMock()

        with (
            patch(
                "agentsassemble.application.room_attendee.join_agent_room_session",
                return_value={
                    "session_token": "session-secret",
                    "agent_id": "claude-guest",
                    "meeting_id": "general",
                    "provider_kind": "manual",
                    "guide": {},
                },
            ),
            patch("agentsassemble.application.room_attendee.connect_room_ws") as connect,
            patch("agentsassemble.application.room_attendee._leave_room"),
        ):
            with self.assertRaisesRegex(ValueError, "must name the provider"):
                attendee.run()

        attendee._build_runtime.assert_not_called()
        connect.assert_not_called()

    def test_leave_treats_an_already_revoked_session_as_complete(self):
        revoked = HTTPError("https://room.example/api/room-invite/leave", 401, "Unauthorized", {}, None)

        with patch("agentsassemble.application.room_attendee.urlopen", side_effect=revoked):
            _leave_room("https://room.example", "session-secret")

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

    def test_windows_runtime_waits_for_configured_startup_readiness(self):
        fake = FakeConPtyProcess(initial_output=["\x1b[3;3Hplan\x1b[3;8Hmode\x1b[3;13Hon"])
        with tempfile.TemporaryDirectory() as temp_dir, patch("shutil.which", return_value="C:/bin/fake.exe"):
            runtime = WindowsConPtyRuntime(
                "fake",
                ["fake"],
                cwd=Path(temp_dir),
                startup_quiet_seconds=0.01,
                startup_timeout_seconds=1.0,
                startup_ready_contains="plan mode on",
                process_factory=lambda *args, **kwargs: fake,
            )
            runtime.send("first")
            runtime.stop()

        self.assertEqual(fake.writes, ["first\r"])


if __name__ == "__main__":
    unittest.main()
