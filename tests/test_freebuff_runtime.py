from __future__ import annotations

import unittest
import tempfile
import sys
import threading
from pathlib import Path

from agentsassemble.providers.freebuff_runtime import (
    FreebuffRuntime,
    _extract_model_labels,
    _match_model_label,
)


class FreebuffRuntimeTests(unittest.TestCase):
    def test_runtime_start_reaches_model_selection_with_a_supported_input_transport(self) -> None:
        terminals = []

        class Terminal:
            def __init__(self, _agent_id, _command, **kwargs) -> None:
                if kwargs.get("input_mode") not in {"line", "bracketed_paste"}:
                    raise ValueError("unsupported input transport")
                self.keys: list[str] = []
                terminals.append(self)

            def start(self) -> dict[str, object]:
                return self.health()

            def stop(self, *, timeout_seconds: float = 2.0) -> None:
                del timeout_seconds

            def health(self) -> dict[str, object]:
                return {
                    "running": True,
                    "terminal_tail": "Select a model\nDeepSeek V4 Flash 07/31\n",
                }

            def send_keys(self, value: str) -> None:
                self.keys.append(value)

        with tempfile.TemporaryDirectory() as temp_dir:
            runtime = FreebuffRuntime(
                "freebuff-start",
                workspace=temp_dir,
                state_dir=Path(temp_dir) / "state",
                executable=sys.executable,
                terminal_runtime_factory=Terminal,
            )
            health = runtime.start()

        self.assertTrue(health["running"])
        self.assertIn("DeepSeek", health["selected_model"])
        self.assertEqual(terminals[0].keys[-1], "\r")

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

    def test_model_labels_are_discovered_from_a_flattened_tui_repaint(self) -> None:
        screen = (
            "│› DeepSeek V4 Flash 07/31  Smartest & Fastest · Reasoning: high│"
            "│  MiMo 2.5                 Balanced · Images│"
        )

        labels = _extract_model_labels(screen)

        self.assertEqual(labels, ["DeepSeek V4 Flash 07/31", "MiMo 2.5"])

    def test_missing_flash_label_fails_closed(self) -> None:
        labels = _extract_model_labels("GPT-5.6 Luna\nMiniMax M3\n")
        self.assertIsNone(_match_model_label(labels, "DeepSeek V4 Flash"))

    def test_session_service_error_is_reported_instead_of_missing_model(self) -> None:
        class Terminal:
            def health(self) -> dict[str, object]:
                return {
                    "terminal_tail": (
                        'freebuff session GET failed: 503 '
                        '{"error":"service_overloaded","retryAfterMs":10000}'
                    )
                }

        with tempfile.TemporaryDirectory() as temp_dir:
            runtime = FreebuffRuntime(
                "freebuff-overloaded",
                workspace=temp_dir,
                state_dir=Path(temp_dir) / "state",
            )
            runtime._terminal = Terminal()

            with self.assertRaisesRegex(Exception, "service_overloaded"):
                runtime._select_model_by_label()


if __name__ == "__main__":
    unittest.main()
