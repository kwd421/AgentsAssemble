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
from agentsassemble.config import load_agent_runtime_config, load_council_config
from agentsassemble.gui import _make_handler
from agentsassemble.live_agent_discovery import build_discovered_live_agent_config
from agentsassemble.live_agent_runner import load_group_configs
from agentsassemble.live_agent_sessions import start_live_agent_session


class LiveAgentDiscoveryTests(unittest.TestCase):
    def test_build_discovered_config_includes_supported_installed_clis(self):
        def resolver(command):
            return f"/opt/bin/{command}" if command in {"claude", "codex", "antigravity", "gemini"} else None

        report = build_discovered_live_agent_config(
            server="http://room.local",
            meeting_id="resident-m1",
            command_resolver=resolver,
            terminal_session_supported=lambda: True,
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
        self.assertTrue(all("path" not in discovery for discovery in report["discoveries"]))

    def test_discovery_explains_each_entry_mode_and_operator_action(self):
        def resolver(command):
            return f"/opt/bin/{command}" if command in {"claude", "codex", "gemini"} else None

        report = build_discovered_live_agent_config(
            server="http://room.local",
            meeting_id="resident-m1",
            command_resolver=resolver,
            terminal_session_supported=lambda: True,
        )

        discoveries = {item["command"]: item for item in report["discoveries"]}
        self.assertEqual(discoveries["claude"]["entry_status"], "ready")
        self.assertEqual(discoveries["claude"]["entry_mode"], "terminal_session")
        self.assertEqual(discoveries["claude"]["operator_action"], "auto_join")
        self.assertTrue(discoveries["claude"]["requires_approval"])
        self.assertIn("preflight", discoveries["claude"]["safety_note"])

        self.assertEqual(discoveries["codex"]["entry_status"], "ready")
        self.assertEqual(discoveries["codex"]["entry_mode"], "codex_live_session")
        self.assertEqual(discoveries["codex"]["operator_action"], "auto_join")
        self.assertTrue(discoveries["codex"]["requires_approval"])
        self.assertIn("Codex", discoveries["codex"]["safety_note"])

        self.assertEqual(discoveries["antigravity"]["entry_status"], "missing")
        self.assertEqual(discoveries["antigravity"]["entry_mode"], "self_service")
        self.assertEqual(discoveries["antigravity"]["operator_action"], "install_cli")
        self.assertFalse(discoveries["antigravity"]["requires_approval"])

        self.assertEqual(discoveries["gemini"]["entry_status"], "legacy")
        self.assertEqual(discoveries["gemini"]["entry_mode"], "terminal_session")
        self.assertEqual(discoveries["gemini"]["operator_action"], "include_legacy_gemini")
        self.assertFalse(discoveries["gemini"]["requires_approval"])
        self.assertIn("legacy", discoveries["gemini"]["safety_note"])

    def test_build_discovered_config_can_include_legacy_gemini_when_requested(self):
        def resolver(command):
            return f"/opt/bin/{command}" if command == "gemini" else None

        report = build_discovered_live_agent_config(
            server="http://room.local",
            meeting_id="",
            engagement_mode="always",
            include_legacy_gemini=True,
            command_resolver=resolver,
            terminal_session_supported=lambda: True,
        )

        self.assertEqual(report["status"], "ok")
        self.assertEqual(report["config"]["agents"][0]["agent_id"], "gemini-cli-legacy-live")
        self.assertEqual(report["config"]["agents"][0]["provider_kind"], "gemini_cli_legacy")

    def test_discovery_skips_terminal_session_candidates_when_pty_is_unavailable(self):
        def resolver(command):
            return f"/opt/bin/{command}" if command in {"claude", "gemini"} else None

        report = build_discovered_live_agent_config(
            server="http://room.local",
            meeting_id="resident-m1",
            include_legacy_gemini=True,
            command_resolver=resolver,
            terminal_session_supported=lambda: False,
        )

        self.assertEqual(report["status"], "empty")
        self.assertEqual(report["config"]["agents"], [])
        discoveries = {item["command"]: item for item in report["discoveries"]}
        self.assertEqual(discoveries["claude"]["entry_status"], "unsupported")
        self.assertFalse(discoveries["claude"]["included"])
        self.assertEqual(discoveries["claude"]["reason"], "terminal_unsupported")
        self.assertEqual(discoveries["claude"]["operator_action"], "unsupported_terminal")
        self.assertFalse(discoveries["claude"]["requires_approval"])
        self.assertIn("PTY", discoveries["claude"]["safety_note"])
        self.assertEqual(discoveries["gemini"]["entry_status"], "unsupported")
        self.assertEqual(discoveries["gemini"]["reason"], "terminal_unsupported")

    def test_live_agent_discover_writes_config_and_next_commands(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = Path(temp_dir) / "live-agents.discovered.json"

            def resolver(command):
                return f"/opt/bin/{command}" if command in {"claude", "codex"} else None

            with (
                patch("agentsassemble.live_agent_discovery.shutil.which", side_effect=resolver),
                patch("agentsassemble.live_agent_discovery.terminal_sessions_supported", return_value=True),
                patch("sys.stdout", StringIO()) as stdout,
            ):
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

    def test_live_agent_discover_compact_output_shows_entry_decisions(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = Path(temp_dir) / "live-agents.discovered.json"

            def resolver(command):
                return f"/opt/bin/{command}" if command in {"claude", "gemini"} else None

            with (
                patch("agentsassemble.live_agent_discovery.shutil.which", side_effect=resolver),
                patch("agentsassemble.live_agent_discovery.terminal_sessions_supported", return_value=True),
                patch("sys.stdout", StringIO()) as stdout,
            ):
                exit_code = main(
                    [
                        "live-agent",
                        "discover",
                        "--server",
                        "http://room.local",
                        "--meeting-id",
                        "resident-m1",
                        "--output",
                        str(output_path),
                    ]
                )

            self.assertEqual(exit_code, 0)
            output = stdout.getvalue()
            self.assertIn("entry claude ready terminal_session auto_join approval required", output)
            self.assertIn("entry gemini legacy terminal_session include_legacy_gemini", output)
            self.assertIn("entry codex missing codex_live_session install_cli", output)

    def test_live_agent_discover_compact_output_shows_terminal_unsupported(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = Path(temp_dir) / "live-agents.discovered.json"

            def resolver(command):
                return f"/opt/bin/{command}" if command == "claude" else None

            with (
                patch("agentsassemble.live_agent_discovery.shutil.which", side_effect=resolver),
                patch("agentsassemble.live_agent_discovery.terminal_sessions_supported", return_value=False),
                patch("sys.stdout", StringIO()) as stdout,
            ):
                exit_code = main(
                    [
                        "live-agent",
                        "discover",
                        "--server",
                        "http://room.local",
                        "--meeting-id",
                        "resident-m1",
                        "--output",
                        str(output_path),
                    ]
                )

            self.assertEqual(exit_code, 1)
            self.assertFalse(output_path.exists())
            output = stdout.getvalue()
            self.assertIn("entry claude unsupported terminal_session unsupported_terminal", output)
            self.assertIn("No supported local agent CLIs found.", output)

    def test_live_agent_discover_can_write_session_bundle_and_ensure_command(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = Path(temp_dir) / "live-agents.discovered.json"

            def resolver(command):
                return f"/opt/bin/{command}" if command in {"claude", "codex", "antigravity"} else None

            with (
                patch("agentsassemble.live_agent_discovery.shutil.which", side_effect=resolver),
                patch("agentsassemble.live_agent_discovery.terminal_sessions_supported", return_value=True),
            ):
                with patch("agentsassemble.cli.subprocess.Popen", side_effect=AssertionError("agent process started")):
                    with patch("agentsassemble.cli.subprocess.run", side_effect=AssertionError("command executed")):
                        with patch("agentsassemble.cli._request_json", side_effect=AssertionError("room contacted")):
                            with patch("sys.stdout", StringIO()) as stdout:
                                exit_code = main(
                                    [
                                        "live-agent",
                                        "discover",
                                        "--server",
                                        "http://room.local",
                                        "--meeting-id",
                                        "resident-m1",
                                        "--output",
                                        str(output_path),
                                        "--session-bundle",
                                        "--json",
                                    ]
                                )

            self.assertEqual(exit_code, 0)
            payload = json.loads(stdout.getvalue())
            session_bundle = payload["session_bundle"]
            self.assertEqual(session_bundle["group_id"], "live-agents.discovered")
            council_path = Path(session_bundle["council_config_path"])
            agent_path = Path(session_bundle["agent_config_path"])
            self.assertTrue(council_path.exists())
            self.assertTrue(agent_path.exists())
            council = json.loads(council_path.read_text(encoding="utf-8"))
            agent_config = json.loads(agent_path.read_text(encoding="utf-8"))
            self.assertEqual(
                [role["id"] for role in council["roles"]],
                ["claude_code_live", "codex_live", "antigravity_cli_live"],
            )
            self.assertEqual(
                [binding["agent_id"] for binding in agent_config["agent_bindings"]],
                ["claude-code-live", "codex-live", "antigravity-cli-live"],
            )
            self.assertEqual(
                [provider["kind"] for provider in agent_config["providers"]],
                ["claude_code", "codex_live_session", "antigravity_cli"],
            )
            ensure = payload["next_commands"]["ensure_session"]
            self.assertIn("--council-config", ensure)
            self.assertIn(str(council_path), ensure)
            self.assertIn("--agent-config", ensure)
            self.assertIn(str(agent_path), ensure)
            self.assertIn("--live-agent-config", ensure)
            self.assertIn(str(output_path), ensure)
            self.assertIn("--meeting-id", ensure)
            self.assertIn("resident-m1", ensure)

    def test_live_agent_auto_join_requires_approval_before_durable_session_run(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = Path(temp_dir) / "live-agents.discovered.json"
            stdout = StringIO()

            def resolver(command):
                return f"/opt/bin/{command}" if command == "codex" else None

            with (
                patch("agentsassemble.live_agent_discovery.shutil.which", side_effect=resolver),
                patch("agentsassemble.live_agent_discovery.terminal_sessions_supported", return_value=True),
                patch("agentsassemble.cli._request_json", side_effect=AssertionError("approval gate must stop before ensure")),
                patch("sys.stdout", stdout),
            ):
                exit_code = main(
                    [
                        "live-agent",
                        "auto-join",
                        "--server",
                        "http://room.local",
                        "--output",
                        str(output_path),
                        "--json",
                    ]
                )

            self.assertEqual(exit_code, 1)
            self.assertTrue(output_path.exists())
            payload = json.loads(stdout.getvalue())
            self.assertEqual(payload["status"], "approval_required")
            self.assertEqual(payload["action"], "none")
            self.assertEqual(payload["approval_required"]["commands"], ["codex"])
            self.assertEqual(payload["session"], {})

    def test_live_agent_auto_join_writes_session_bundle_and_records_durable_session_run(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = Path(temp_dir) / "live-agents.discovered.json"
            requests = []
            readiness_seen = {"count": 0}

            def resolver(command):
                return f"/opt/bin/{command}" if command in {"claude", "codex"} else None

            def request_json(url, *, method="GET", payload=None, timeout_seconds=None):
                requests.append(
                    {
                        "url": url,
                        "method": method,
                        "payload": payload,
                        "timeout_seconds": timeout_seconds,
                    }
                )
                if url.startswith("http://room.local/api/live-agent-sessions/readiness?"):
                    readiness_seen["count"] += 1
                    return {
                        "status": "ready",
                        "meeting_id": "resident-m1",
                        "group_id": "live-agents.discovered",
                        "connection": {"expected": 2, "connected": 2, "attention": []},
                        "process": {"status": "running", "attention": []},
                    }
                if url == "http://room.local/api/live-agent-session-runs/ensure":
                    return {
                        "status": "ready",
                        "action": "start",
                        "meeting_id": "resident-m1",
                        "group_id": "live-agents.discovered",
                        "connection": {"expected": 2, "connected": 2, "attention": []},
                        "process": {"status": "running", "attention": []},
                        "session_run": {
                            "run_id": "run-auto-1",
                            "status": "ready",
                            "action": "ensure",
                            "active": True,
                            "meeting_id": "resident-m1",
                            "group_id": "live-agents.discovered",
                        },
                    }
                raise AssertionError(f"unexpected request: {url}")

            stdout = StringIO()
            with (
                patch("agentsassemble.live_agent_discovery.shutil.which", side_effect=resolver),
                patch("agentsassemble.live_agent_discovery.terminal_sessions_supported", return_value=True),
            ):
                with patch("agentsassemble.cli._request_json", side_effect=request_json):
                    with patch("sys.stdout", stdout):
                        exit_code = main(
                            [
                                "live-agent",
                                "auto-join",
                                "--server",
                                "http://room.local",
                                "--meeting-id",
                                "resident-m1",
                                "--output",
                                str(output_path),
                                "--connect-timeout",
                                "7",
                                "--wait-timeout",
                                "3",
                                "--wait-poll-interval",
                                "0.1",
                                "--approve-real-providers",
                                "--json",
                            ]
                        )

            self.assertEqual(exit_code, 0)
            self.assertTrue(output_path.exists())
            self.assertTrue((Path(temp_dir) / "council.discovered.json").exists())
            self.assertTrue((Path(temp_dir) / "agents.discovered.json").exists())
            start_request = next(request for request in requests if request["url"] == "http://room.local/api/live-agent-session-runs/ensure")
            self.assertEqual(
                start_request["payload"],
                {
                    "meeting_id": "resident-m1",
                    "group_id": "live-agents.discovered",
                    "council_config_path": str(Path(temp_dir) / "council.discovered.json"),
                    "agent_config_path": str(Path(temp_dir) / "agents.discovered.json"),
                    "live_agent_config_path": str(output_path),
                    "connect_timeout_seconds": 7.0,
                    "auto_restart": False,
                    "max_restarts": 0,
                    "restart_backoff_seconds": 5.0,
                    "stale_restart_after_seconds": 0.0,
                },
            )
            payload = json.loads(stdout.getvalue())
            self.assertEqual(payload["status"], "ready")
            self.assertEqual(payload["action"], "start")
            self.assertEqual(payload["discovery"]["session_bundle"]["group_id"], "live-agents.discovered")
            self.assertEqual(payload["session"]["connection"]["connected"], 2)
            self.assertEqual(payload["session"]["session_run"]["run_id"], "run-auto-1")

    def test_live_agent_auto_join_can_finalize_after_remaining_rounds(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = Path(temp_dir) / "live-agents.discovered.json"
            requests = []
            readiness_seen = {"count": 0}

            def resolver(command):
                return f"/opt/bin/{command}" if command in {"claude", "codex"} else None

            def request_json(url, *, method="GET", payload=None, timeout_seconds=None):
                requests.append(
                    {
                        "url": url,
                        "method": method,
                        "payload": payload,
                        "timeout_seconds": timeout_seconds,
                    }
                )
                if url.startswith("http://room.local/api/live-agent-sessions/readiness?"):
                    readiness_seen["count"] += 1
                    return {
                        "status": "ready",
                        "meeting_id": "resident-m1",
                        "group_id": "live-agents.discovered",
                        "connection": {"expected": 2, "connected": 2, "attention": []},
                        "process": {"status": "running", "attention": []},
                    }
                if url == "http://room.local/api/live-agent-session-runs/ensure":
                    return {
                        "status": "ready",
                        "action": "start",
                        "meeting_id": "resident-m1",
                        "group_id": "live-agents.discovered",
                        "connection": {"expected": 2, "connected": 2, "attention": []},
                        "process": {"status": "running", "attention": []},
                        "auto_rounds": {
                            "status": "answered",
                            "round_count": 2,
                            "answered_round_count": 2,
                            "completed_round_count": 0,
                            "timeout_round_count": 0,
                            "skipped_round_count": 0,
                        },
                        "finalization": {
                            "status": "finalized",
                            "meeting_id": "resident-m1",
                            "official_event_count": 4,
                        },
                    }
                raise AssertionError(f"unexpected request: {url}")

            stdout = StringIO()
            with (
                patch("agentsassemble.live_agent_discovery.shutil.which", side_effect=resolver),
                patch("agentsassemble.live_agent_discovery.terminal_sessions_supported", return_value=True),
            ):
                with patch("agentsassemble.cli._request_json", side_effect=request_json):
                    with patch("sys.stdout", stdout):
                        exit_code = main(
                            [
                                "live-agent",
                                "auto-join",
                                "--server",
                                "http://room.local",
                                "--meeting-id",
                                "resident-m1",
                                "--output",
                                str(output_path),
                                "--run-remaining-rounds",
                                "--finalize-after-rounds",
                                "--round-timeout",
                                "11",
                                "--max-rounds",
                                "3",
                                "--stop-on-timeout",
                                "--approve-real-providers",
                                "--json",
                            ]
                        )

            self.assertEqual(exit_code, 0)
            start_request = next(request for request in requests if request["url"] == "http://room.local/api/live-agent-session-runs/ensure")
            self.assertEqual(start_request["payload"]["run_remaining_rounds"], True)
            self.assertEqual(start_request["payload"]["finalize_after_rounds"], True)
            self.assertEqual(start_request["payload"]["round_timeout_seconds"], 11.0)
            self.assertEqual(start_request["payload"]["round_max_rounds"], 3)
            self.assertEqual(start_request["payload"]["round_stop_on_timeout"], True)
            payload = json.loads(stdout.getvalue())
            self.assertEqual(payload["status"], "ready")
            self.assertEqual(payload["session"]["finalization"]["status"], "finalized")
            self.assertEqual(payload["session"]["finalization"]["official_event_count"], 4)

    def test_live_agent_auto_join_without_meeting_id_uses_server_ensure_for_existing_group(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = Path(temp_dir) / "live-agents.discovered.json"
            requests = []

            def resolver(command):
                return f"/opt/bin/{command}" if command in {"claude", "codex"} else None

            def request_json(url, *, method="GET", payload=None, timeout_seconds=None):
                requests.append(
                    {
                        "url": url,
                        "method": method,
                        "payload": payload,
                        "timeout_seconds": timeout_seconds,
                    }
                )
                if url == "http://room.local/api/live-agent-session-runs/ensure":
                    return {
                        "status": "ready",
                        "action": "none",
                        "meeting_id": "resident-existing",
                        "group_id": "live-agents.discovered",
                        "connection": {"expected": 2, "connected": 2, "attention": []},
                        "process": {"status": "running", "attention": []},
                        "auto_rounds": {
                            "status": "answered",
                            "round_count": 1,
                            "answered_round_count": 1,
                            "completed_round_count": 0,
                            "timeout_round_count": 0,
                            "skipped_round_count": 0,
                        },
                        "finalization": {
                            "status": "finalized",
                            "meeting_id": "resident-existing",
                            "official_event_count": 2,
                        },
                    }
                if url == "http://room.local/api/live-agent-sessions/start":
                    raise AssertionError("auto-join without a meeting id should let durable server ensure adopt owned groups")
                if url == "http://room.local/api/live-agent-sessions/ensure":
                    raise AssertionError("auto-join should record durable session-run intent")
                raise AssertionError(f"unexpected request: {url}")

            stdout = StringIO()
            with (
                patch("agentsassemble.live_agent_discovery.shutil.which", side_effect=resolver),
                patch("agentsassemble.live_agent_discovery.terminal_sessions_supported", return_value=True),
            ):
                with patch("agentsassemble.cli._request_json", side_effect=request_json):
                    with patch("sys.stdout", stdout):
                        exit_code = main(
                            [
                                "live-agent",
                                "auto-join",
                                "--server",
                                "http://room.local",
                                "--output",
                                str(output_path),
                                "--run-remaining-rounds",
                                "--finalize-after-rounds",
                                "--round-timeout",
                                "11",
                                "--max-rounds",
                                "3",
                                "--approve-real-providers",
                                "--json",
                            ]
                        )

            self.assertEqual(exit_code, 0)
            ensure_request = next(request for request in requests if request["url"] == "http://room.local/api/live-agent-session-runs/ensure")
            self.assertEqual(ensure_request["payload"]["meeting_id"], "")
            self.assertEqual(ensure_request["payload"]["group_id"], "live-agents.discovered")
            self.assertEqual(ensure_request["payload"]["live_agent_config_path"], str(output_path))
            self.assertEqual(ensure_request["payload"]["run_remaining_rounds"], True)
            self.assertEqual(ensure_request["payload"]["finalize_after_rounds"], True)
            self.assertFalse(any(request["url"] == "http://room.local/api/live-agent-sessions/start" for request in requests))
            payload = json.loads(stdout.getvalue())
            self.assertEqual(payload["status"], "ready")
            self.assertEqual(payload["action"], "none")
            self.assertEqual(payload["session"]["meeting_id"], "resident-existing")

    def test_live_agent_auto_join_reports_finalization_failure_as_session_failure(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = Path(temp_dir) / "live-agents.discovered.json"
            readiness_seen = {"count": 0}

            def resolver(command):
                return f"/opt/bin/{command}" if command in {"claude", "codex"} else None

            def request_json(url, *, method="GET", payload=None, timeout_seconds=None):
                if url.startswith("http://room.local/api/live-agent-sessions/readiness?"):
                    readiness_seen["count"] += 1
                    return {
                        "status": "ready",
                        "meeting_id": "resident-m1",
                        "group_id": "live-agents.discovered",
                        "connection": {"expected": 2, "connected": 2, "attention": []},
                        "process": {"status": "running", "attention": []},
                    }
                if url == "http://room.local/api/live-agent-session-runs/ensure":
                    return {
                        "status": "ready",
                        "action": "start",
                        "meeting_id": "resident-m1",
                        "group_id": "live-agents.discovered",
                        "connection": {"expected": 2, "connected": 2, "attention": []},
                        "process": {"status": "running", "attention": []},
                        "auto_rounds": {
                            "status": "answered",
                            "round_count": 1,
                            "answered_round_count": 1,
                            "completed_round_count": 0,
                            "timeout_round_count": 0,
                            "skipped_round_count": 0,
                        },
                        "finalization": {
                            "status": "failed",
                            "meeting_id": "resident-m1",
                            "reason": "pending_turn_request",
                            "official_event_count": 2,
                        },
                    }
                raise AssertionError(f"unexpected request: {url}")

            stdout = StringIO()
            with (
                patch("agentsassemble.live_agent_discovery.shutil.which", side_effect=resolver),
                patch("agentsassemble.live_agent_discovery.terminal_sessions_supported", return_value=True),
            ):
                with patch("agentsassemble.cli._request_json", side_effect=request_json):
                    with patch("sys.stdout", stdout):
                        exit_code = main(
                            [
                                "live-agent",
                                "auto-join",
                                "--server",
                                "http://room.local",
                                "--meeting-id",
                                "resident-m1",
                                "--output",
                                str(output_path),
                                "--run-remaining-rounds",
                                "--finalize-after-rounds",
                                "--approve-real-providers",
                            ]
                        )

            self.assertEqual(exit_code, 1)
            self.assertIn("Auto-joined via start", stdout.getvalue())
            self.assertIn("finalization failed: 2 official events", stdout.getvalue())

    def test_live_agent_auto_join_returns_one_when_no_supported_cli_is_found(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = Path(temp_dir) / "live-agents.discovered.json"
            stdout = StringIO()
            with patch("agentsassemble.live_agent_discovery.shutil.which", return_value=None):
                with patch("agentsassemble.cli._request_json", side_effect=AssertionError("session should not start")):
                    with patch("sys.stdout", stdout):
                        exit_code = main(["live-agent", "auto-join", "--output", str(output_path), "--json"])

            self.assertEqual(exit_code, 1)
            self.assertFalse(output_path.exists())
            payload = json.loads(stdout.getvalue())
            self.assertEqual(payload["status"], "empty")
            self.assertEqual(payload["action"], "none")
            self.assertEqual(payload["session"], {})

    def test_live_agent_discover_refuses_session_bundle_path_collisions_before_writing(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = Path(temp_dir) / "live-agents.discovered.json"

            def resolver(command):
                return f"/opt/bin/{command}" if command == "claude" else None

            with (
                patch("agentsassemble.live_agent_discovery.shutil.which", side_effect=resolver),
                patch("agentsassemble.live_agent_discovery.terminal_sessions_supported", return_value=True),
            ):
                with patch("sys.stderr", StringIO()) as stderr:
                    exit_code = main(
                        [
                            "live-agent",
                            "discover",
                            "--output",
                            str(output_path),
                            "--session-bundle",
                            "--session-agent-output",
                            str(output_path),
                            "--json",
                        ]
                    )

            self.assertEqual(exit_code, 2)
            self.assertIn("session bundle output paths must be distinct", stderr.getvalue())
            self.assertFalse(output_path.exists())
            self.assertFalse((Path(temp_dir) / "council.discovered.json").exists())

    def test_discovered_session_bundle_can_start_visible_session_with_manifest_match(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            output_path = root / "live-agents.discovered.json"

            def resolver(command):
                return f"/opt/bin/{command}" if command in {"claude", "codex", "antigravity"} else None

            with (
                patch("agentsassemble.live_agent_discovery.shutil.which", side_effect=resolver),
                patch("agentsassemble.live_agent_discovery.terminal_sessions_supported", return_value=True),
                patch("sys.stdout", StringIO()) as stdout,
            ):
                exit_code = main(
                    [
                        "live-agent",
                        "discover",
                        "--server",
                        "http://room.local",
                        "--meeting-id",
                        "resident-m1",
                        "--output",
                        str(output_path),
                        "--session-bundle",
                        "--json",
                    ]
                )

            self.assertEqual(exit_code, 0)
            payload = json.loads(stdout.getvalue())
            council_path = Path(payload["session_bundle"]["council_config_path"])
            agent_path = Path(payload["session_bundle"]["agent_config_path"])
            council = load_council_config(council_path)
            runtime = load_agent_runtime_config(agent_path)
            resident_configs = load_group_configs(output_path)
            supervisor = _DiscoverySessionSupervisor(resident_configs)

            session = start_live_agent_session(
                root,
                supervisor,
                server="http://room.local",
                council_config_path=council_path,
                agent_config_path=agent_path,
                live_agent_config_path=output_path,
                meeting_id="resident-m1",
                group_id="resident-main",
                connect_timeout_seconds=0,
                preflight_checker=lambda *args, **kwargs: {"status": "ok"},
            )

            self.assertEqual([role.id for role in council.roles], ["claude_code_live", "codex_live", "antigravity_cli_live"])
            self.assertEqual([provider["kind"] for provider in runtime["providers"]], ["claude_code", "codex_live_session", "antigravity_cli"])
            self.assertEqual(session["status"], "starting")
            self.assertEqual(session["process"]["expected"], 3)
            self.assertEqual(session["connection"]["expected"], 3)
            self.assertEqual(supervisor.started[0]["meeting_id"], "resident-m1")

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
                with (
                    patch("agentsassemble.live_agent_discovery.shutil.which", side_effect=resolver),
                    patch("agentsassemble.live_agent_discovery.terminal_sessions_supported", return_value=True),
                ):
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

    def test_gui_discovery_api_can_write_session_bundle_without_running_agents(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)

            def resolver(command):
                return f"/opt/bin/{command}" if command in {"claude", "codex"} else None

            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                url = f"http://127.0.0.1:{server.server_port}/api/live-agent-discovery"
                request = urllib.request.Request(
                    url,
                    method="POST",
                    data=json.dumps({"meeting_id": "resident-m1", "session_bundle": True}).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                )
                with (
                    patch("agentsassemble.live_agent_discovery.shutil.which", side_effect=resolver),
                    patch("agentsassemble.live_agent_discovery.terminal_sessions_supported", return_value=True),
                ):
                    with patch.object(subprocess, "Popen", side_effect=AssertionError("agent process started")):
                        with patch.object(subprocess, "run", side_effect=AssertionError("command executed")):
                            with patch("agentsassemble.gui._request_json", side_effect=AssertionError("room contacted")):
                                with urllib.request.urlopen(request) as response:
                                    payload = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                thread.join(timeout=2)
                server.server_close()

            self.assertEqual(payload["status"], "ok")
            self.assertTrue((root / "live-agents.discovered.local.json").exists())
            self.assertTrue((root / "council.discovered.local.json").exists())
            self.assertTrue((root / "agents.discovered.local.json").exists())
            self.assertEqual(payload["session_bundle"]["group_id"], "live-agents.discovered.local")
            self.assertEqual(payload["session_bundle"]["agent_config_path"], str(root / "agents.discovered.local.json"))
            self.assertIn("ensure_session", payload["next_commands"])

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
                with (
                    patch("agentsassemble.live_agent_discovery.shutil.which", side_effect=lambda command: "/opt/bin/claude" if command == "claude" else None),
                    patch("agentsassemble.live_agent_discovery.terminal_sessions_supported", return_value=True),
                ):
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


class _DiscoverySessionSupervisor:
    def __init__(self, resident_configs):
        self.resident_configs = resident_configs
        self.started = []

    def list_groups(self):
        return {"groups": []}

    def start_group(self, **kwargs):
        self.started.append(kwargs)
        return {
            "group_id": kwargs.get("group_id") or "resident-main",
            "status": "running",
            "started_at": "2026-05-20T00:00:00+00:00",
            "agents": [
                {
                    "agent_id": config.agent_id,
                    "display_name": config.display_name,
                    "provider_kind": config.provider_kind,
                    "connection_kind": config.connection_kind,
                }
                for config in self.resident_configs
            ],
        }
