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
from agentsassemble.providers.claude_usage import ClaudeUsageService
from agentsassemble.providers.codex_usage import CodexUsageService
from agentsassemble.providers.deepseek_usage import DeepSeekUsageService
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
from agentsassemble.providers.launch_specs import native_cli_provider_spec_from_payload


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
    def test_deepseek_usage_returns_sanitized_balance_without_exposing_credential(self):
        service = DeepSeekUsageService(
            credential_reader=lambda: "private-deepseek-key",
            fetcher=lambda key: {
                "is_available": True,
                "balance_infos": [
                    {
                        "currency": "USD",
                        "total_balance": "12.340000",
                        "granted_balance": "private-grant-detail",
                    }
                ],
                "credential_echo": key,
            },
        )

        usage = service.read()

        self.assertEqual(
            usage["account_balances"],
            [{"currency": "USD", "amount": "12.34"}],
        )
        self.assertNotIn("private", json.dumps(usage))

    def test_deepseek_usage_marks_provider_reported_unavailable_balance_as_exhausted(self):
        service = DeepSeekUsageService(
            credential_reader=lambda: "private-deepseek-key",
            fetcher=lambda _key: {
                "is_available": False,
                "balance_infos": [
                    {
                        "currency": "USD",
                        "total_balance": "0",
                    }
                ],
            },
        )

        usage = service.read()

        self.assertFalse(usage["account_available"])
        self.assertEqual(usage["quota_state"], "exhausted")

    def test_claude_usage_returns_only_public_windows_and_reuses_short_cache(self):
        fetch_count = 0

        def fetcher() -> dict[str, object]:
            nonlocal fetch_count
            fetch_count += 1
            return {
                "five_hour": {"utilization": 2, "resets_at": "2026-07-23T15:00:00Z"},
                "seven_day": {"utilization": 40, "resets_at": "2026-07-23T23:00:00Z"},
            }

        service = ClaudeUsageService(
            fetcher=fetcher,
        )

        first = service.read()
        second = service.read()

        self.assertEqual((first["quota_5h"], first["quota_1w"]), ("2%", "40%"))
        self.assertEqual(fetch_count, 1)
        self.assertEqual(first["source"], "claude_native_usage")

    def test_antigravity_plan_prefix_is_not_part_of_the_delivered_turn(self):
        self.assertEqual(
            _antigravity_user_request("<USER_REQUEST>\n/plan room input\n</USER_REQUEST>"),
            "room input",
        )

    def test_pty_answers_terminal_queries_needed_by_interactive_tuis(self):
        response = _terminal_query_response(
            b"\x1b[6n\x1b[c\x1b[?u\x1b[?2026$p\x1b[?2027$p"
            b"\x1b]10;?\x1b\\\x1b]11;?\x1b\\"
        )

        self.assertIn(b"\x1b[1;1R", response)
        self.assertIn(b"\x1b[?1;2c", response)
        self.assertIn(b"\x1b[?0u", response)
        self.assertIn(b"\x1b[?2026;2$y", response)
        self.assertIn(b"\x1b[?2027;2$y", response)
        self.assertIn(b"rgb:ffff", response)
        self.assertIn(b"rgb:0000", response)
        self.assertEqual(
            _antigravity_user_request("<USER_REQUEST>room input/plan</USER_REQUEST>"),
            "room input",
        )

    def test_codex_usage_selects_the_native_model_limit_without_exposing_limit_ids(self):
        service = CodexUsageService(
            fetcher=lambda: {
                "rateLimits": {
                    "primary": {
                        "usedPercent": 91,
                        "windowDurationMins": 10080,
                        "resetsAt": 1785327678,
                    },
                    "secondary": None,
                },
                "rateLimitsByLimitId": {
                    "private-default-id": {
                        "limitName": None,
                        "primary": {
                            "usedPercent": 91,
                            "windowDurationMins": 10080,
                            "resetsAt": 1785327678,
                        },
                    },
                    "private-spark-id": {
                        "limitName": "GPT-5.3-Codex-Spark",
                        "primary": {
                            "usedPercent": 7,
                            "windowDurationMins": 10080,
                            "resetsAt": 1785345000,
                        },
                    },
                },
            }
        )

        standard = service.read(model="gpt-5.6-sol")
        spark = service.read(model="gpt-5.3-codex-spark")

        self.assertEqual(standard["quota_windows"][0]["percent"], 91)
        self.assertEqual(spark["quota_windows"][0]["percent"], 7)
        self.assertNotIn("private-", json.dumps([standard, spark]))

    def test_provider_child_environment_drops_server_and_api_secrets(self):
        environment = sanitized_provider_environment(
            source={
                "HOME": "/home/test",
                "PATH": "/bin",
                "CEREBRAS_API_KEY": "secret",
                "DEEPSEEK_API_KEY": "secret",
                "AGENTSASSEMBLE_INVITE_TOKEN": "invite",
            }
        )

        self.assertEqual(environment, {"HOME": "/home/test", "PATH": "/bin"})
        self.assertFalse(environment_contains_secret_names(environment))

    def test_api_provider_secret_status_never_returns_value_or_secret_metadata(self):
        backend = FakeKeyring()
        store = ProviderSecretStore(
            backend=backend,
            environment={
                "CEREBRAS_API_KEY": "cerebras-fallback",
                "DEEPSEEK_API_KEY": "deepseek-fallback",
            },
        )

        for provider_id in ("cerebras", "deepseek"):
            with self.subTest(provider_id=provider_id):
                secret = f"{provider_id}-private-value"
                status = store.set(provider_id, secret)
                self.assertEqual(
                    status,
                    {"configured": True, "source": "keyring"},
                )
                self.assertNotIn(secret, json.dumps(status))
                self.assertEqual(store.get(provider_id), secret)

    def test_custom_api_accepts_a_full_completion_endpoint_and_rejects_link_wrappers(self):
        catalog = ProviderCapabilityCatalog(
            runner=lambda _command, _timeout: (1, "", "not installed"),
            resolver=lambda _executable: None,
            remote_model_discovery=lambda _profile, _api_key: [],
        )
        snapshot = catalog.snapshot(refresh=True)
        revision = str(snapshot["catalog_revision"])

        selected = catalog.validate_selection(
            catalog_revision=revision,
            provider_id="custom_api",
            values={
                "provider_endpoint": "https://api.example.com/v1/chat/completions",
                "model": "vendor-model",
                "permission_mode": "meeting_read_only",
                "max_output_tokens": "4096",
            },
        )

        self.assertEqual(selected.provider_endpoint, "https://api.example.com/v1")
        self.assertEqual(selected.model, "vendor-model")
        with self.assertRaises(ProviderCatalogSelectionError) as rejected:
            catalog.validate_selection(
                catalog_revision=revision,
                provider_id="custom_api",
                values={
                    "provider_endpoint": (
                        "https://unsafelink.example/https://api.example.com/v1/chat/completions"
                    ),
                    "model": "vendor-model",
                    "permission_mode": "meeting_read_only",
                    "max_output_tokens": "4096",
                },
            )
        self.assertEqual(rejected.exception.code, "invalid_provider_endpoint")

    def test_capability_probe_returns_native_controls_without_commands(self):
        def runner(command: list[str], _timeout: float):
            if command[1:3] == ["debug", "models"]:
                return 0, json.dumps({"models": [{"slug": "gpt-5.6-luna", "display_name": "Luna", "supported_reasoning_levels": [{"effort": "low"}, {"effort": "ultra", "description": "Maximum reasoning with automatic task delegation"}], "service_tiers": [{"id": "priority"}]}]}), ""
            if command[0].endswith("ollama") and command[1:] == ["list"]:
                return 0, (
                    "NAME                       ID              SIZE\n"
                    "gemma4:12b                 4eb23ef187e2    7.6 GB\n"
                    "nemotron-3-super:cloud     c6398e09afd4    -\n"
                ), ""
            if command[0].endswith("ollama") and command[1:3] == ["show", "gemma4:12b"]:
                return 0, "Capabilities\n  completion\n  thinking\n  tools\n", ""
            if command[0].endswith("ollama") and command[1:3] == ["show", "nemotron-3-super:cloud"]:
                return 0, "Capabilities\n  completion\n  thinking\n  tools\n", ""
            if command[0].endswith("lms") and command[1:] == ["status"]:
                return 0, "Server: ON\n", ""
            if command[0].endswith("lms") and command[1:] == ["ps", "--json"]:
                return 0, json.dumps(
                    [
                        {
                            "type": "llm",
                            "identifier": "gemma-4-e4b-it",
                            "trainedForToolUse": True,
                        },
                        {
                            "type": "llm",
                            "identifier": "plain-text-model",
                            "trainedForToolUse": False,
                        },
                    ]
                ), ""
            if command[0].endswith("agy"):
                return 0, (
                    "gemini-3.6-flash-high\n"
                    "gemini-3.6-flash-medium\n"
                    "gemini-3.6-flash-low\n"
                    "claude-sonnet-4-6\n"
                ), ""
            if command[0].endswith("cursor-agent"):
                return 0, (
                    "auto - Auto (current, default)\n"
                    "gpt-5.4-low - GPT-5.4 Low\n"
                    "gpt-5.4-high - GPT-5.4 High\n"
                    "gpt-5.4-high-fast - GPT-5.4 High Fast\n"
                    "gpt-5.5-extra-high - GPT-5.5 Extra High\n"
                ), ""
            if command[1:] == ["models", "--verbose"]:
                return 0, (
                    "opencode/deepseek-v4-flash-free\n"
                    + json.dumps(
                        {
                            "id": "deepseek-v4-flash-free",
                            "providerID": "opencode",
                            "name": "DeepSeek V4 Flash Free",
                            "family": "deepseek-flash-free",
                            "cost": {
                                "input": 0,
                                "output": 0,
                                "cache": {"read": 0, "write": 0},
                            },
                        }
                    )
                    + "\n"
                    "opencode-go/glm-5.2\n"
                    + json.dumps(
                        {
                            "id": "glm-5.2",
                            "providerID": "opencode-go",
                            "name": "GLM 5.2",
                            "family": "glm",
                            "cost": {
                                "input": 0.2,
                                "output": 0.4,
                                "cache": {"read": 0, "write": 0},
                            },
                        }
                    )
                    + "\n"
                ), ""
            return 0, "", ""

        catalog = ProviderCapabilityCatalog(runner=runner, resolver=lambda executable: f"/bin/{executable}")
        payload = catalog.payload(refresh=True)
        codex = next(item for item in payload if item["id"] == "codex")

        self.assertEqual(codex["discovery_status"], "ready")
        self.assertEqual(codex["catalog_source"], "discovered")
        self.assertEqual(codex["controls"][0]["default_value"], "gpt-5.6-luna")
        codex_effort = next(
            control for control in codex["controls"] if control["key"] == "reasoning_effort"
        )
        ultra = next(
            option for option in codex_effort["options"] if option["value"] == "ultra"
        )
        self.assertEqual(ultra["metadata"]["effect"], "ultra")
        self.assertIn("automatic task delegation", ultra["metadata"]["description"])
        self.assertNotIn("command", codex)
        self.assertNotIn("resolved_executable", codex)
        antigravity = next(item for item in payload if item["id"] == "antigravity")
        antigravity_model = next(
            control for control in antigravity["controls"] if control["key"] == "model"
        )
        antigravity_effort = next(
            control
            for control in antigravity["controls"]
            if control["key"] == "reasoning_effort"
        )
        self.assertEqual(
            [(option["value"], option["label"]) for option in antigravity_model["options"]],
            [
                ("gemini-3.6-flash", "Gemini 3.6 Flash"),
                ("claude-sonnet-4-6", "Claude Sonnet 4.6"),
            ],
        )
        self.assertEqual(
            [option["value"] for option in antigravity_effort["options"]],
            ["", "high", "medium", "low"],
        )
        cursor = next(item for item in payload if item["id"] == "cursor")
        model = next(control for control in cursor["controls"] if control["key"] == "model")
        self.assertEqual(model["default_value"], "auto")
        self.assertEqual(
            [(option["value"], option["label"]) for option in model["options"]],
            [
                ("auto", "Auto (current, default)"),
                ("gpt-5.4", "GPT 5.4"),
                ("gpt-5.5", "GPT 5.5"),
            ],
        )
        cursor_effort = next(
            control for control in cursor["controls"] if control["key"] == "reasoning_effort"
        )
        self.assertEqual(
            [option["value"] for option in cursor_effort["options"]],
            ["default", "low", "high", "extra-high"],
        )
        # Cursor's catalog is preserved as exact model/effort/speed tuples.
        auto_option = next(option for option in model["options"] if option["value"] == "auto")
        gpt_54_option = next(option for option in model["options"] if option["value"] == "gpt-5.4")
        gpt_55_option = next(option for option in model["options"] if option["value"] == "gpt-5.5")
        self.assertEqual(auto_option["metadata"]["reasoning_efforts"], ["default"])
        self.assertEqual(gpt_55_option["metadata"]["reasoning_efforts"], ["extra-high"])
        self.assertEqual(
            gpt_54_option["metadata"]["runtime_variants"],
            [
                {"reasoning_effort": "low", "service_tier": "default"},
                {"reasoning_effort": "high", "service_tier": "default"},
                {"reasoning_effort": "high", "service_tier": "fast"},
            ],
        )
        opencode = next(item for item in payload if item["id"] == "opencode")
        opencode_models = next(
            control for control in opencode["controls"] if control["key"] == "model"
        )
        self.assertEqual(
            [option["value"] for option in opencode_models["options"]],
            [
                "opencode/deepseek-v4-flash-free",
                "opencode-go/glm-5.2",
            ],
        )
        self.assertEqual(
            [option["label"] for option in opencode_models["options"]],
            ["DeepSeek V4 Flash", "GLM 5.2"],
        )
        self.assertEqual(
            [
                (
                    option["metadata"]["group"],
                    option["metadata"].get("pricing"),
                )
                for option in opencode_models["options"]
            ],
            [("Zen", "free"), ("Go", None)],
        )
        revision = str(catalog.snapshot()["catalog_revision"])
        catalog.validate_selection(
            catalog_revision=revision,
            provider_id="cursor",
            values={
                "model": "gpt-5.4",
                "reasoning_effort": "high",
                "service_tier": "fast",
                "permission_mode": "meeting_read_only",
            },
        )
        with self.assertRaises(ProviderCatalogSelectionError) as invalid_variant:
            catalog.validate_selection(
                catalog_revision=revision,
                provider_id="cursor",
                values={
                    "model": "gpt-5.4",
                    "reasoning_effort": "low",
                    "service_tier": "fast",
                    "permission_mode": "meeting_read_only",
                },
            )
        self.assertEqual(
            invalid_variant.exception.code,
            "unsupported_model_runtime_combination",
        )
        ollama = next(item for item in payload if item["id"] == "ollama")
        self.assertTrue(ollama["startable"])
        self.assertEqual(ollama["catalog_group"], "subscription")
        self.assertFalse(ollama["workspace_required"])
        self.assertEqual(
            [
                (
                    option["value"],
                    option["label"],
                    option["metadata"]["catalog_group"],
                    option["metadata"]["execution_location"],
                    option["metadata"].get("pricing"),
                )
                for option in ollama["controls"][0]["options"]
            ],
            [
                ("gemma4:12b", "Gemma 4 12B", "local", "local", None),
                (
                    "nemotron-3-super:cloud",
                    "Nemotron 3 Super",
                    "subscription",
                    "cloud",
                    "free_tier",
                ),
            ],
        )
        lmstudio = next(item for item in payload if item["id"] == "lmstudio")
        self.assertTrue(lmstudio["startable"])
        self.assertEqual(lmstudio["catalog_group"], "local")
        self.assertFalse(lmstudio["workspace_required"])
        self.assertEqual(
            [
                (option["value"], option["label"])
                for option in lmstudio["controls"][0]["options"]
            ],
            [("gemma-4-e4b-it", "Gemma 4 E4b It")],
        )
        local_spec = native_cli_provider_spec_from_payload(
            {
                "provider_id": "lmstudio",
                "model": "gemma-4-e4b-it",
                "permission_mode": "meeting_read_only",
                "catalog_revision": revision,
            }
        )
        self.assertEqual(local_spec.provider_kind, "lmstudio_api")
        self.assertTrue(Path(local_spec.cwd).is_absolute())

    def test_antigravity_blank_effort_only_matches_exact_models_without_variants(self):
        def runner(command: list[str], _timeout: float):
            if command[0].endswith("agy"):
                return 0, (
                    "gemini-3.6-flash-high\n"
                    "gemini-3.6-flash-low\n"
                    "claude-sonnet-4-6\n"
                    "gpt-oss-120b-medium\n"
                ), ""
            return 1, "", "unsupported"

        catalog = ProviderCapabilityCatalog(
            runner=runner,
            resolver=lambda executable: "/bin/agy" if executable == "agy" else None,
        )
        revision = str(catalog.snapshot(refresh=True)["catalog_revision"])
        common = {
            "permission_mode": "meeting_read_only",
            "service_tier": "",
            "variant": "",
        }

        exact = catalog.validate_selection(
            catalog_revision=revision,
            provider_id="antigravity",
            values={
                **common,
                "model": "claude-sonnet-4-6",
                "reasoning_effort": "",
            },
        )
        self.assertEqual(exact.reasoning_effort, "")
        spec = native_cli_provider_spec_from_payload(
            {
                "provider_id": exact.provider_id,
                "display_name": "Agy Claude",
                "workspace": ".",
                "model": exact.model,
                "model_selection_kind": exact.model_selection_kind,
                "catalog_revision": exact.catalog_revision,
                "reasoning_effort": exact.reasoning_effort,
                "permission_mode": exact.permission_mode,
            }
        )
        self.assertEqual(
            spec.command,
            ("agy", "--model", "claude-sonnet-4-6", "--sandbox"),
        )

        for model, effort in (
            ("gemini-3.6-flash", "low"),
            ("gpt-oss-120b", "medium"),
        ):
            with self.subTest(model=model, effort=effort):
                selected = catalog.validate_selection(
                    catalog_revision=revision,
                    provider_id="antigravity",
                    values={
                        **common,
                        "model": model,
                        "reasoning_effort": effort,
                    },
                )
                self.assertEqual(selected.reasoning_effort, effort)

        for model in ("gemini-3.6-flash", "gpt-oss-120b"):
            with self.subTest(model=model):
                with self.assertRaises(ProviderCatalogSelectionError) as rejected:
                    catalog.validate_selection(
                        catalog_revision=revision,
                        provider_id="antigravity",
                        values={
                            **common,
                            "model": model,
                            "reasoning_effort": "",
                        },
                    )
                self.assertEqual(
                    rejected.exception.code,
                    "unsupported_model_effort_combination",
                )

    def test_grok_catalog_applies_the_selected_workspace_permission(self):
        catalog = ProviderCapabilityCatalog(
            runner=lambda _command, _timeout: (
                0,
                "* grok-4.5\nDefault model: grok-4.5\n",
                "",
            ),
            resolver=lambda executable: "/bin/grok" if executable == "grok" else None,
        )

        snapshot = catalog.snapshot(refresh=True)
        grok = next(
            provider
            for provider in snapshot["providers"]
            if provider["id"] == "grok"
        )
        permission = next(
            control
            for control in grok["controls"]
            if control["key"] == "permission_mode"
        )
        self.assertEqual(
            [option["value"] for option in permission["options"]],
            ["meeting_read_only", "workspace_write"],
        )

        selected = catalog.validate_selection(
            catalog_revision=str(snapshot["catalog_revision"]),
            provider_id="grok",
            values={
                "model": "grok-4.5",
                "reasoning_effort": "medium",
                "permission_mode": "workspace_write",
            },
        )
        self.assertEqual(selected.permission_mode, "workspace_write")

    def test_claude_catalog_exposes_only_exact_models(self):
        def runner(command: list[str], _timeout: float):
            if "definitely-not-supported" in command:
                return 0, "", "Warning: Unknown --effort value"
            return 0, "Claude help", ""

        catalog = ProviderCapabilityCatalog(
            runner=runner,
            resolver=lambda executable: f"/bin/{executable}",
            claude_model_discovery=lambda _executable: [
                "claude-haiku-4-5",
                "claude-sonnet-5",
                "claude-sonnet-4-6",
                "claude-opus-4-8",
            ],
            claude_xhigh_model_discovery=lambda _executable: [
                "claude-sonnet-5",
                "claude-opus-4-8",
            ],
        )

        claude = next(item for item in catalog.payload(refresh=True) if item["id"] == "claude")
        model = next(control for control in claude["controls"] if control["key"] == "model")
        options = {option["value"]: option["label"] for option in model["options"]}

        self.assertEqual(model["default_value"], "claude-haiku-4-5")
        self.assertEqual(claude["catalog_source"], "discovered")
        self.assertEqual(options["claude-sonnet-4-6"], "Claude Sonnet 4.6")
        self.assertEqual(options["claude-opus-4-8"], "Claude Opus 4.8")
        self.assertNotIn("sonnet", options)
        self.assertNotIn("opus", options)
        self.assertNotIn("haiku", options)
        effort = next(
            control
            for control in claude["controls"]
            if control["key"] == "reasoning_effort"
        )
        ultracode = next(
            option
            for option in effort["options"]
            if option["value"] == "ultracode"
        )
        self.assertEqual(ultracode["metadata"]["effect"], "ultra")
        self.assertIn(
            "ultracode",
            next(
                option
                for option in model["options"]
                if option["value"] == "claude-opus-4-8"
            )["metadata"]["reasoning_efforts"],
        )
        self.assertNotIn(
            "ultracode",
            next(
                option
                for option in model["options"]
                if option["value"] == "claude-sonnet-4-6"
            )["metadata"]["reasoning_efforts"],
        )

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
        self.assertEqual(exact.model_selection_kind, "exact")
        self.assertEqual(exact.catalog_revision, revision)
        ultracode_selection = catalog.validate_selection(
            catalog_revision=revision,
            provider_id="claude",
            values={
                **common,
                "model": "claude-opus-4-8",
                "reasoning_effort": "ultracode",
            },
        )
        self.assertEqual(ultracode_selection.reasoning_effort, "ultracode")
        with self.assertRaises(ProviderCatalogSelectionError):
            catalog.validate_selection(
                catalog_revision=revision,
                provider_id="claude",
                values={**common, "model": "sonnet"},
            )

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

    def test_expired_catalog_remains_startable_during_background_refresh(self):
        block_refresh = [False]
        refresh_started = threading.Event()
        release = threading.Event()

        def runner(command: list[str], _timeout: float):
            if block_refresh[0]:
                refresh_started.set()
                release.wait(2)
            if command[1:3] == ["debug", "models"]:
                return 0, json.dumps(
                    {
                        "models": [
                            {
                                "slug": "gpt-live",
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
        ready = catalog.snapshot(refresh=True)
        revision = str(ready["catalog_revision"])
        block_refresh[0] = True
        catalog._cached_at = time.monotonic() - catalog._ttl_seconds - 1.0
        stale = catalog.snapshot()
        try:
            self.assertTrue(refresh_started.wait(1))
            self.assertEqual(stale["status"], "ready")
            codex = next(provider for provider in stale["providers"] if provider["id"] == "codex")
            self.assertTrue(codex["startable"])
            self.assertEqual(codex["discovery_status"], "ready")
            self.assertEqual(codex["catalog_source"], "discovered")
            selected = catalog.validate_selection(
                catalog_revision=revision,
                provider_id="codex",
                values={
                    "model": "gpt-live",
                    "reasoning_effort": "low",
                    "service_tier": "default",
                    "permission_mode": "meeting_read_only",
                },
            )
            self.assertEqual(selected.model, "gpt-live")
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
        self.assertTrue(
            all(
                not provider["startable"]
                for provider in initial["providers"]
                if provider["runtime_kind"] != "api"
            )
        )
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

    def test_deepseek_stream_reports_reasoning_activity_without_leaking_it_into_final_or_health(self):
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
        activities: list[dict[str, object]] = []
        runtime.send("say hi")
        result = runtime.read_output(
            timeout_seconds=2,
            on_delta=deltas.append,
            on_activity=activities.append,
        )

        self.assertEqual(result["content"], "hello")
        self.assertEqual(result["metadata"]["observed_model_id"], "deepseek-v4-flash")
        self.assertEqual(deltas, ["hello"])
        self.assertEqual(
            [activity["status"] for activity in activities],
            ["running", "completed"],
        )
        self.assertTrue(all(activity["category"] == "reasoning" for activity in activities))
        self.assertEqual(activities[-1]["activity_detail"], "private")
        self.assertNotIn("private", json.dumps(result))
        self.assertNotIn("sk-private", json.dumps(runtime.health()))
        self.assertEqual(captured["body"]["reasoning_effort"], "high")

    def test_openai_compatible_runtime_distinguishes_exhausted_quota_from_rate_limit(self):
        def runtime_for(payload: dict[str, object]) -> DeepSeekApiRuntime:
            def opener(request, timeout):
                del timeout
                raise HTTPError(
                    request.full_url,
                    429,
                    "Too Many Requests",
                    {},
                    io.BytesIO(json.dumps(payload).encode()),
                )

            return DeepSeekApiRuntime(
                "deepseek",
                api_key="sk-private",
                opener=opener,
            )

        exhausted = runtime_for(
            {
                "error": {
                    "code": "insufficient_quota",
                    "message": "Insufficient quota for this account.",
                }
            }
        )
        exhausted.send("hello")
        with self.assertRaises(RuntimeError) as exhausted_error:
            exhausted.read_output(timeout_seconds=1)
        self.assertEqual(exhausted_error.exception.code, "quota_exhausted")

        rate_limited = runtime_for(
            {"error": {"message": "Too many requests. Try again later."}}
        )
        rate_limited.send("hello")
        with self.assertRaises(RuntimeError) as rate_limit_error:
            rate_limited.read_output(timeout_seconds=1)
        self.assertEqual(rate_limit_error.exception.code, "provider_rate_limited")

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
        self.assertIn("Welcome", orientation)
        self.assertNotIn("Speak naturally", orientation)

    def test_codex_attendee_uses_structured_persistent_cli_transport(self):
        attendee = AgentAttendee(
            invite_url="https://room.example/join?token=aai1.secret",
            provider_id="codex",
            service_tier="priority",
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            runtime = attendee._build_runtime("codex-guest", Path(temp_dir))
        self.assertIn("app-server", runtime.runtime.command)
        self.assertNotIn("exec", runtime.runtime.command)
        self.assertEqual(runtime.profile["sandbox"], "read-only")
        self.assertEqual(runtime.profile["service_tier"], "priority")
        self.assertIn('service_tier="priority"', runtime.runtime.command)

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
                return iter(
                    [
                        {
                            "type": "thinking_delta",
                            "category": "command",
                            "status": "running",
                            "activity_id": "command-1",
                            "activity_title": "명령",
                            "activity_detail": "pwd",
                            "content": "Using tool: pwd",
                        },
                        {"type": "message_final", "content": "hello"},
                    ]
                )

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
        activities = []

        result = runtime.read_output(timeout_seconds=2, on_activity=activities.append)

        self.assertEqual(result["metadata"]["observed_model_id"], "gpt-5.6-luna")
        self.assertEqual(
            activities,
            [
                {
                    "category": "command",
                    "status": "running",
                    "activity_id": "command-1",
                    "activity_title": "명령",
                    "activity_detail": "pwd",
                    "content": "Using tool: pwd",
                }
            ],
        )

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
            patch(
                "agentsassemble.application.room_attendee.PROVIDER_SECRETS.get",
                side_effect=lambda provider_id: f"{provider_id}-secret",
            ),
        ):
            workspace = Path(temp_dir)
            for provider_id in (
                "claude",
                "grok",
                "antigravity",
                "cursor",
                "opencode",
                "deepseek",
                "cerebras",
            ):
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
            "cursor-guest",
            "opencode-guest",
            "deepseek-guest",
            "cerebras-guest",
        ])
        self.assertEqual(captured[1][0].transport, "acp_stdio")
        self.assertEqual(captured[4][0].provider_endpoint, "http://127.0.0.1:43210")
        self.assertEqual(captured[5][1], "deepseek-secret")
        self.assertNotIn("deepseek-secret", repr(captured[5][0]))
        self.assertEqual(captured[6][1], "cerebras-secret")
        self.assertNotIn("cerebras-secret", repr(captured[6][0]))

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
        leave_response = MagicMock()
        leave_response.__enter__.return_value = io.BytesIO(b"")

        def leave_request(*_args, **_kwargs):
            calls.append("leave")
            return leave_response

        with patch(
            "agentsassemble.application.room_attendee.urlopen",
            side_effect=leave_request,
        ):
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
        leave_response = MagicMock()
        leave_response.__enter__.return_value = io.BytesIO(b"")

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
            patch(
                "agentsassemble.application.room_attendee.urlopen",
                return_value=leave_response,
            ),
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
        ):
            with self.assertRaisesRegex(ValueError, "must name the provider"):
                attendee.run()

        attendee._build_runtime.assert_not_called()
        connect.assert_not_called()

    def test_leave_treats_an_already_revoked_session_as_complete(self):
        revoked = HTTPError("https://room.example/api/room-invite/leave", 401, "Unauthorized", {}, None)

        with patch(
            "agentsassemble.application.room_attendee.urlopen",
            side_effect=revoked,
        ) as open_request:
            _leave_room("https://room.example", "session-secret")

        open_request.assert_called_once()
        request = open_request.call_args.args[0]
        self.assertEqual(request.full_url, "https://room.example/api/room-invite/leave")
        self.assertEqual(request.get_method(), "POST")
        self.assertEqual(request.get_header("Authorization"), "Bearer session-secret")
        self.assertEqual(open_request.call_args.kwargs, {"timeout": 5.0})

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

    def test_windows_timeout_cannot_promote_late_output_into_the_next_turn(self):
        processes = []

        class TimeoutThenReplyProcess(FakeConPtyProcess):
            def __init__(self, generation):
                super().__init__()
                self.generation = generation
                self.turn_writes = 0

            def write(self, value: str) -> None:
                self.writes.append(value)
                if value == "\x03":
                    return
                self.turn_writes += 1
                if self.generation == 1 and self.turn_writes == 1:
                    return
                reply = "previous reply" if self.generation == 1 else "current reply"
                self.output.append(reply + "\r\n")

        def process_factory(*args, **kwargs):
            process = TimeoutThenReplyProcess(len(processes) + 1)
            processes.append(process)
            return process

        with tempfile.TemporaryDirectory() as temp_dir, patch("shutil.which", return_value="C:/bin/fake.exe"):
            runtime = WindowsConPtyRuntime(
                "fake",
                ["fake"],
                cwd=Path(temp_dir),
                idle_quiet_seconds=0.01,
                process_factory=process_factory,
            )
            try:
                runtime.send("first")
                with self.assertRaises(TimeoutError):
                    runtime.read_output(timeout_seconds=0.05)
                runtime.send("second")
                second = runtime.read_output(timeout_seconds=1)
            finally:
                runtime.stop()

        self.assertIn("current reply", second["content"])
        self.assertNotIn("previous reply", second["content"])


if __name__ == "__main__":
    unittest.main()
