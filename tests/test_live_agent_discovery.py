import json
import tempfile
import unittest
from pathlib import Path
from io import StringIO
from http.server import ThreadingHTTPServer
from unittest.mock import patch
import threading
import subprocess
import urllib.request

from agentsassemble.cli import main
from agentsassemble.gui import _make_handler
from agentsassemble.live_agent_discovery import build_discovered_live_agent_config
from agentsassemble.live_agent_runner import load_group_configs


class LiveAgentDiscoveryTests(unittest.TestCase):
    def test_build_discovered_config_includes_supported_installed_clis(self):
        def resolver(command):
            return f"/opt/bin/{command}" if command in {"claude", "codex", "antigravity", "gemini"} else None

        report = build_discovered_live_agent_config(
            server="http://room.local",
            meeting_id="resident-m1",
            command_resolver=resolver,
        )

        self.assertEqual(report["status"], "ok")
        self.assertEqual(
            [agent["agent_id"] for agent in report["config"]["agents"]],
            ["claude-code-live", "codex-live", "antigravity-cli-live"],
        )
        self.assertEqual(report["config"]["agents"][0]["connection_kind"], "terminal_session")
        self.assertEqual(report["config"]["agents"][0]["engagement_mode"], "mentioned")
        self.assertEqual(report["config"]["agents"][1]["provider_kind"], "codex_live_session")
        self.assertNotIn("command", report["config"]["agents"][1])
        self.assertEqual(report["config"]["agents"][1]["timeout_seconds"], 240)
        self.assertEqual(report["config"]["agents"][2]["connection_kind"], "self_service")
        gemini = next(item for item in report["discoveries"] if item["command"] == "gemini")
        self.assertTrue(gemini["available"])
        self.assertFalse(gemini["included"])
        self.assertEqual(gemini["reason"], "legacy")

    def test_build_discovered_config_can_include_legacy_gemini_when_requested(self):
        def resolver(command):
            return f"/opt/bin/{command}" if command == "gemini" else None

        report = build_discovered_live_agent_config(
            server="http://room.local",
            meeting_id="",
            engagement_mode="always",
            include_legacy_gemini=True,
            command_resolver=resolver,
        )

        self.assertEqual(report["status"], "ok")
        self.assertEqual(report["config"]["agents"][0]["agent_id"], "gemini-cli-legacy-live")
        self.assertEqual(report["config"]["agents"][0]["provider_kind"], "gemini_cli_legacy")

    def test_live_agent_discover_writes_config_and_next_commands(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = Path(temp_dir) / "live-agents.discovered.json"

            def resolver(command):
                return f"/opt/bin/{command}" if command in {"claude", "codex"} else None

            with patch("agentsassemble.live_agent_discovery.shutil.which", side_effect=resolver):
                with patch("sys.stdout", StringIO()) as stdout:
                    exit_code = main(
                        [
                            "live-agent",
                            "discover",
                            "--server",
                            "http://room.local",
                            "--meeting-id",
                            "resident-m1",
                            "--engagement-mode",
                            "always",
                            "--output",
                            str(output_path),
                            "--json",
                        ]
                    )

            self.assertEqual(exit_code, 0)
            written = json.loads(output_path.read_text(encoding="utf-8"))
            self.assertEqual([agent["agent_id"] for agent in written["agents"]], ["claude-code-live", "codex-live"])
            self.assertEqual(written["agents"][0]["meeting_id"], "resident-m1")
            loaded = load_group_configs(output_path)
            self.assertEqual(loaded[1].provider_kind, "codex_live_session")
            self.assertEqual(loaded[1].command, ["codex"])
            payload = json.loads(stdout.getvalue())
            self.assertEqual(payload["next_commands"]["preflight"][-1], str(output_path))

    def test_live_agent_discover_returns_one_when_no_supported_cli_is_found(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = Path(temp_dir) / "live-agents.discovered.json"
            with patch("agentsassemble.live_agent_discovery.shutil.which", return_value=None):
                with patch("sys.stdout", StringIO()):
                    exit_code = main(["live-agent", "discover", "--output", str(output_path), "--json"])

            self.assertEqual(exit_code, 1)
            self.assertFalse(output_path.exists())

    def test_live_agent_discover_does_not_execute_or_contact_any_agent(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = Path(temp_dir) / "live-agents.discovered.json"

            def resolver(command):
                return f"/opt/bin/{command}" if command == "antigravity" else None

            with patch("agentsassemble.live_agent_discovery.shutil.which", side_effect=resolver):
                with patch("agentsassemble.cli.subprocess.Popen", side_effect=AssertionError("agent process started")):
                    with patch("agentsassemble.cli.subprocess.run", side_effect=AssertionError("command executed")):
                        with patch("agentsassemble.cli._request_json", side_effect=AssertionError("room contacted")):
                            with patch("sys.stdout", StringIO()):
                                exit_code = main(["live-agent", "discover", "--output", str(output_path), "--json"])

            self.assertEqual(exit_code, 0)
            written = json.loads(output_path.read_text(encoding="utf-8"))
            self.assertEqual(written["agents"][0]["connection_kind"], "self_service")

    def test_gui_discovery_api_writes_output_root_config_without_running_agents(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)

            def resolver(command):
                return f"/opt/bin/{command}" if command in {"claude", "antigravity"} else None

            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                url = f"http://127.0.0.1:{server.server_port}/api/live-agent-discovery"
                request = urllib.request.Request(
                    url,
                    method="POST",
                    data=json.dumps({"meeting_id": "resident-m1"}).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                )
                with patch("agentsassemble.live_agent_discovery.shutil.which", side_effect=resolver):
                    with patch.object(subprocess, "Popen", side_effect=AssertionError("agent process started")):
                        with patch.object(subprocess, "run", side_effect=AssertionError("command executed")):
                            with patch("agentsassemble.gui._request_json", side_effect=AssertionError("room contacted")):
                                with urllib.request.urlopen(request) as response:
                                    payload = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                thread.join(timeout=2)
                server.server_close()

            output_path = root / "live-agents.discovered.local.json"
            self.assertEqual(payload["status"], "ok")
            self.assertTrue(payload["written"])
            self.assertEqual(payload["output"], str(output_path))
            self.assertTrue(output_path.exists())
            written = json.loads(output_path.read_text(encoding="utf-8"))
            self.assertEqual([agent["agent_id"] for agent in written["agents"]], ["claude-code-live", "antigravity-cli-live"])
            self.assertEqual(written["agents"][0]["meeting_id"], "resident-m1")
            self.assertEqual(payload["next_commands"]["preflight"][-1], str(output_path))

    def test_gui_discovery_api_can_return_report_without_writing_config(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                url = f"http://127.0.0.1:{server.server_port}/api/live-agent-discovery"
                request = urllib.request.Request(
                    url,
                    method="POST",
                    data=json.dumps({"write_config": False}).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                )
                with patch("agentsassemble.live_agent_discovery.shutil.which", side_effect=lambda command: "/opt/bin/claude" if command == "claude" else None):
                    with urllib.request.urlopen(request) as response:
                        payload = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                thread.join(timeout=2)
                server.server_close()

            self.assertEqual(payload["status"], "ok")
            self.assertFalse(payload["written"])
            self.assertEqual(payload["output"], "")
            self.assertFalse((root / "live-agents.discovered.local.json").exists())
            self.assertEqual(payload["config"]["agents"][0]["agent_id"], "claude-code-live")

    def test_gui_discovery_api_does_not_write_when_no_supported_cli_is_found(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                url = f"http://127.0.0.1:{server.server_port}/api/live-agent-discovery"
                request = urllib.request.Request(
                    url,
                    method="POST",
                    data=json.dumps({}).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                )
                with patch("agentsassemble.live_agent_discovery.shutil.which", return_value=None):
                    with urllib.request.urlopen(request) as response:
                        payload = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                thread.join(timeout=2)
                server.server_close()

            self.assertEqual(payload["status"], "empty")
            self.assertFalse(payload["written"])
            self.assertEqual(payload["output"], "")
            self.assertFalse((root / "live-agents.discovered.local.json").exists())
