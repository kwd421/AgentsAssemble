import json
import sys
import tempfile
import unittest
from pathlib import Path

from agentsassemble.live_cli_smoke import _marker_recalled, run_live_cli_smoke
from agentsassemble.providers.live_cli import live_cli_supported


def _fake_cli_script(path: Path) -> None:
    path.write_text(
        "\n".join(
            [
                "import re, sys",
                "marker = ''",
                "for line in sys.stdin:",
                "    text = line.strip()",
                "    if not text:",
                "        continue",
                "    found = re.search(r'AGENTSASSEMBLE_SESSION_MARKER=([A-Za-z0-9_.-]+)', text)",
                "    if found:",
                "        marker = found.group(1)",
                "        print('remembered ' + marker, flush=True)",
                "    else:",
                "        print('marker is ' + marker, flush=True)",
            ]
        ),
        encoding="utf-8",
    )


def _forgetful_cli_script(path: Path) -> None:
    path.write_text(
        "\n".join(
            [
                "import sys",
                "for line in sys.stdin:",
                "    text = line.strip()",
                "    if not text:",
                "        continue",
                "    print('no marker here', flush=True)",
            ]
        ),
        encoding="utf-8",
    )


class LiveCliSmokeTests(unittest.TestCase):
    def test_marker_recall_tolerates_terminal_layout_splitting(self):
        self.assertTrue(_marker_recalled("grok-001", "Responding... grok-        001"))
        self.assertTrue(_marker_recalled("antigravity-001", "antigravity Generating... 001"))
        self.assertTrue(_marker_recalled("grok-001", "grok status text grok- 001"))
        self.assertTrue(_marker_recalled("codex-001", "codex-001"))
        self.assertFalse(_marker_recalled("grok-001", "grok-002"))

    def test_missing_command_is_unavailable_and_writes_result(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config = root / "providers.json"
            config.write_text(
                json.dumps(
                    {
                        "providers": [
                            {
                                "id": "missing",
                                "kind": "live_cli",
                                "display_name": "Missing CLI",
                                "command": ["definitely-not-agentsassemble-cli"],
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )

            result = run_live_cli_smoke(
                config_path=config,
                output_root=root,
                providers=["missing"],
                approve_real_provider=True,
                timeout_seconds=1,
            )
            result_exists = Path(result["result_path"]).exists()

        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["providers"][0]["status"], "unavailable")
        self.assertIn("configured command missing", result["providers"][0]["last_error"])
        self.assertTrue(result_exists)

    @unittest.skipUnless(live_cli_supported(), "requires POSIX PTY support")
    def test_fake_cli_keeps_same_pid_and_recalls_marker(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            fake = root / "fake_cli.py"
            _fake_cli_script(fake)
            config = root / "providers.json"
            config.write_text(
                json.dumps(
                    {
                        "providers": [
                            {
                                "id": "fake",
                                "kind": "live_cli",
                                "display_name": "Fake CLI",
                                "command": [sys.executable, "-u", str(fake)],
                                "cwd": str(root),
                                "input_mode": "bracketed_paste",
                                "submit_newline": "\r",
                                "session_probe_prompt": (
                                    "AGENTSASSEMBLE_SESSION_MARKER=fake-001 를 기억해줘."
                                ),
                                "memory_check_prompt": "아까 marker 값 뭐였지?",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )

            result = run_live_cli_smoke(
                config_path=config,
                output_root=root,
                providers=["fake"],
                approve_real_provider=True,
                timeout_seconds=3,
            )

        provider = result["providers"][0]
        self.assertEqual(result["status"], "ok")
        self.assertEqual(provider["status"], "ok")
        self.assertTrue(provider["same_pid_over_turns"])
        self.assertTrue(provider["memory_marker_recalled"])
        self.assertEqual(provider["pid_first_turn"], provider["pid_second_turn"])
        self.assertGreaterEqual(provider["ttfo_ms"][0], 0)
        self.assertGreaterEqual(provider["total_turn_ms"][1], 0)

    @unittest.skipUnless(live_cli_supported(), "requires POSIX PTY support")
    def test_marker_recall_failure_is_not_reported_as_ok(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            fake = root / "forgetful_cli.py"
            _forgetful_cli_script(fake)
            config = root / "providers.json"
            config.write_text(
                json.dumps(
                    {
                        "providers": [
                            {
                                "id": "forgetful",
                                "kind": "live_cli",
                                "display_name": "Forgetful CLI",
                                "command": [sys.executable, "-u", str(fake)],
                                "cwd": str(root),
                                "session_probe_prompt": "AGENTSASSEMBLE_SESSION_MARKER=forgetful-001 를 기억해줘.",
                                "memory_check_prompt": "아까 marker 값 뭐였지?",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )

            result = run_live_cli_smoke(
                config_path=config,
                output_root=root,
                providers=["forgetful"],
                approve_real_provider=True,
                timeout_seconds=3,
            )

        provider = result["providers"][0]
        self.assertEqual(result["status"], "error")
        self.assertEqual(provider["status"], "error")
        self.assertFalse(provider["memory_marker_recalled"])
        self.assertIn("memory marker", provider["last_error"])
