from __future__ import annotations

import unittest
import tempfile
import threading
from pathlib import Path

from agentsassemble.providers.freebuff_runtime import (
    FreebuffRuntime,
    _extract_model_labels,
    _match_model_label,
)
from agentsassemble.providers.launch_specs import native_cli_provider_definition
from agentsassemble.providers.runtime_factory import runtime_from_config
from agentsassemble.providers.runtime_config import ProviderRuntimeConfig


class FreebuffRuntimeTests(unittest.TestCase):
    def test_successful_model_selection_returns_and_submits_the_visible_label(self) -> None:
        class Terminal:
            def __init__(self) -> None:
                self.keys: list[str] = []

            def health(self) -> dict[str, object]:
                return {"terminal_tail": "Select a model\nDeepSeek V4 Flash 07/31\n"}

            def send_keys(self, value: str) -> None:
                self.keys.append(value)

        with tempfile.TemporaryDirectory() as temp_dir:
            runtime = FreebuffRuntime(
                "freebuff-deadlock-regression",
                workspace=temp_dir,
                state_dir=Path(temp_dir) / "state",
            )
            terminal = Terminal()
            runtime._terminal = terminal
            runtime._version = "test-version"
            completed: dict[str, object] = {}

            def select() -> None:
                completed["label"] = runtime._select_model_by_label()

            worker = threading.Thread(target=select, daemon=True)
            worker.start()
            worker.join(timeout=6.0)
            self.assertFalse(worker.is_alive(), "successful label selection deadlocked")
            self.assertIn("DeepSeek", str(completed.get("label") or ""))
            self.assertEqual(terminal.keys[-1], "\r")

    def test_catalog_registers_freebuff_subscription_provider(self) -> None:
        definition = native_cli_provider_definition("freebuff")
        self.assertIsNotNone(definition)
        self.assertEqual(definition.provider_kind, "freebuff_live_session")
        self.assertEqual(definition.catalog_group, "subscription")
        self.assertEqual(definition.default_model, "DeepSeek V4 Flash")
        spec = definition.make_default_spec(cwd=".")
        self.assertEqual(spec.command[0], "freebuff")

    def test_model_label_match_uses_name_not_menu_ordinal(self) -> None:
        screen = """
        Select a model
        GPT-5.6 Luna
        MiniMax M3
        DeepSeek V4 Flash 07/31
        MiMo 2.5
        """
        labels = _extract_model_labels(screen)
        match = _match_model_label(labels, "DeepSeek V4 Flash")
        self.assertIsNotNone(match)
        index, label = match
        self.assertEqual(index, labels.index(label))
        self.assertIn("DeepSeek", label)
        self.assertIn("Flash", label)
        # Changing order must still find by name.
        reordered = list(reversed(labels))
        rematch = _match_model_label(reordered, "DeepSeek V4 Flash")
        self.assertIsNotNone(rematch)
        self.assertEqual(rematch[1], label)
        self.assertEqual(rematch[0], reordered.index(label))

    def test_missing_flash_label_fails_closed(self) -> None:
        labels = _extract_model_labels("GPT-5.6 Luna\nMiniMax M3\n")
        self.assertIsNone(_match_model_label(labels, "DeepSeek V4 Flash"))

    def test_runtime_factory_builds_freebuff_runtime(self) -> None:
        config = ProviderRuntimeConfig(
            participant_id="freebuff-1",
            provider_kind="freebuff_live_session",
            runtime_kind="live_cli",
            command=("freebuff",),
            cwd="/tmp",
            model="DeepSeek V4 Flash",
            reasoning_effort="",
            service_tier="",
            variant="",
            permission_mode="workspace_write",
            max_output_tokens=0,
            context_contract_bytes=0,
            transport="pty",
            quiet_seconds=0.5,
            input_mode="raw",
            submit_newline="\r",
            submit_delay_seconds=0.0,
            terminal_rows=40,
            terminal_columns=120,
            startup_quiet_seconds=1.0,
            startup_timeout_seconds=30.0,
            startup_accept_contains="",
            startup_accept_keys="\r",
            startup_ready_contains="",
            startup_input="",
            runtime_state_dir="/tmp/freebuff-state",
            provider_endpoint="",
            provider_server_pid=None,
        )
        runtime = runtime_from_config(config)
        self.assertEqual(type(runtime).__name__, "FreebuffRuntime")
        health = runtime.health()
        self.assertIn("structured_protocol", health["unsupported"])


if __name__ == "__main__":
    unittest.main()
