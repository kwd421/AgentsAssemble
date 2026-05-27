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
from agentsassemble.live_agent_discovery import build_discovered_live_agent_config, build_discovered_session_bundle
from agentsassemble.live_agent_operations import append_live_agent_operation, read_live_agent_operations
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
        self.assertEqual(discoveries["claude"]["join_semantics"], "terminal_pty_prompt_bridge")
        self.assertEqual(discoveries["claude"]["context_durability"], "process_lifetime")
        self.assertEqual(discoveries["claude"]["sandbox_enforcement"], "advisory")
        self.assertEqual(discoveries["claude"]["evidence_basis"], "path_and_pty_preflight")
        self.assertEqual(discoveries["claude"]["operator_action"], "auto_join")
        self.assertTrue(discoveries["claude"]["requires_approval"])
        self.assertIn("preflight", discoveries["claude"]["safety_note"])

        self.assertEqual(discoveries["codex"]["entry_status"], "ready")
        self.assertEqual(discoveries["codex"]["entry_mode"], "codex_live_session")
        self.assertEqual(discoveries["codex"]["join_semantics"], "codex_exec_resume")
        self.assertEqual(discoveries["codex"]["context_durability"], "provider_managed_resume")
        self.assertEqual(discoveries["codex"]["sandbox_enforcement"], "codex_readonly")
        self.assertEqual(discoveries["codex"]["evidence_basis"], "path_and_codex_safety_preflight")
        self.assertEqual(discoveries["codex"]["operator_action"], "auto_join")
        self.assertTrue(discoveries["codex"]["requires_approval"])
        self.assertIn("Codex", discoveries["codex"]["safety_note"])

        self.assertEqual(discoveries["antigravity"]["entry_status"], "missing")
        self.assertEqual(discoveries["antigravity"]["entry_mode"], "self_service")
        self.assertEqual(discoveries["antigravity"]["join_semantics"], "self_service_room_loop")
        self.assertEqual(discoveries["antigravity"]["context_durability"], "provider_managed_room_loop")
        self.assertEqual(discoveries["antigravity"]["sandbox_enforcement"], "advisory")
        self.assertEqual(discoveries["antigravity"]["evidence_basis"], "path_and_self_service_preflight")
        self.assertEqual(discoveries["antigravity"]["operator_action"], "install_cli")
        self.assertFalse(discoveries["antigravity"]["requires_approval"])

        self.assertEqual(discoveries["gemini"]["entry_status"], "legacy")
        self.assertEqual(discoveries["gemini"]["entry_mode"], "terminal_session")
        self.assertEqual(discoveries["gemini"]["join_semantics"], "terminal_pty_prompt_bridge")
        self.assertEqual(discoveries["gemini"]["context_durability"], "process_lifetime")
        self.assertEqual(discoveries["gemini"]["evidence_basis"], "path_and_pty_preflight")
        self.assertEqual(discoveries["gemini"]["operator_action"], "include_legacy_gemini")
        self.assertFalse(discoveries["gemini"]["requires_approval"])
        self.assertIn("legacy", discoveries["gemini"]["safety_note"])

    def test_discovery_includes_external_cli_candidates_with_prompt_bridge_contract(self):
        external_commands = {"cursor-agent", "grok", "hermes", "openclaw"}

        def resolver(command):
            return f"/opt/bin/{command}" if command in external_commands else None

        report = build_discovered_live_agent_config(
            server="http://room.local",
            meeting_id="resident-m1",
            command_resolver=resolver,
            terminal_session_supported=lambda: True,
        )

        agents = report["config"]["agents"]
        self.assertEqual(
            [agent["agent_id"] for agent in agents],
            ["cursor-agent-live", "grok-live", "hermes-cli-live", "openclaw-cli-live"],
        )
        terminal_agents = [agents[0], agents[2], agents[3]]
        for agent in terminal_agents:
            self.assertEqual(agent["connection_kind"], "terminal_session")
            self.assertEqual(agent["join_semantics"], "terminal_pty_prompt_bridge")
            self.assertEqual(agent["context_durability"], "process_lifetime")
            self.assertEqual(agent["sandbox_enforcement"], "advisory")
            self.assertEqual(agent["evidence_basis"], "path_and_pty_preflight")
        grok_agent = agents[1]
        self.assertEqual(grok_agent["provider_kind"], "grok_live_session")
        self.assertEqual(grok_agent["connection_kind"], "live_session")
        self.assertEqual(grok_agent["join_semantics"], "grok_session_resume")
        self.assertEqual(grok_agent["context_durability"], "provider_managed_resume")
        self.assertEqual(grok_agent["sandbox_enforcement"], "advisory")
        self.assertEqual(grok_agent["evidence_basis"], "path_and_grok_resume_preflight")
        self.assertNotIn("command", grok_agent)
        self.assertEqual([agent.get("command") for agent in agents], [["cursor-agent"], None, ["hermes"], ["openclaw"]])

        discoveries = {item["command"]: item for item in report["discoveries"]}
        for command in {"cursor-agent", "hermes", "openclaw"}:
            self.assertTrue(discoveries[command]["available"])
            self.assertTrue(discoveries[command]["included"])
            self.assertTrue(discoveries[command]["requires_approval"])
            self.assertEqual(discoveries[command]["join_semantics"], "terminal_pty_prompt_bridge")
            self.assertEqual(discoveries[command]["context_durability"], "process_lifetime")
            self.assertEqual(discoveries[command]["sandbox_enforcement"], "advisory")
            self.assertEqual(discoveries[command]["evidence_basis"], "path_and_pty_preflight")
        self.assertTrue(discoveries["grok"]["available"])
        self.assertTrue(discoveries["grok"]["included"])
        self.assertTrue(discoveries["grok"]["requires_approval"])
        self.assertEqual(discoveries["grok"]["entry_mode"], "grok_live_session")
        self.assertEqual(discoveries["grok"]["join_semantics"], "grok_session_resume")
        self.assertEqual(discoveries["grok"]["context_durability"], "provider_managed_resume")
        self.assertEqual(discoveries["grok"]["evidence_basis"], "path_and_grok_resume_preflight")

    def test_discovered_session_bundle_labels_stateless_prompt_call_honestly(self):
        bundle = build_discovered_session_bundle(
            {
                "agents": [
                    {
                        "agent_id": "stateless-local",
                        "display_name": "Stateless Local",
                        "provider_kind": "local_cli",
                        "join_semantics": "stateless_prompt_call",
                        "context_durability": "stateless_prompt",
                        "sandbox_enforcement": "advisory",
                        "evidence_basis": "path_and_local_cli_delegate",
                    },
                    {
                        "agent_id": "durable-codex",
                        "display_name": "Durable Codex",
                        "provider_kind": "codex_live_session",
                        "join_semantics": "codex_exec_resume",
                        "context_durability": "provider_managed_resume",
                        "sandbox_enforcement": "codex_readonly",
                        "evidence_basis": "path_and_codex_safety_preflight",
                    },
                ]
            }
        )

        roles = {role["id"]: role for role in bundle["council_config"]["roles"]}
        stateless_role = roles["stateless_local"]
        durable_role = roles["durable_codex"]
        question = bundle["council_config"]["question"]
        instruction = bundle["council_config"]["meeting_template"]["rounds"][0]["instruction"]
        bindings = {binding["agent_id"]: binding for binding in bundle["agent_config"]["agent_bindings"]}

        self.assertNotIn("resident session", stateless_role["lens"])
        self.assertIn("stateless", stateless_role["lens"])
        self.assertNotIn("resident session", stateless_role["research_focus"])
        self.assertNotIn("Join the resident session", stateless_role["research_focus"])
        self.assertIn("stateless_prompt_call", stateless_role["research_focus"])
        self.assertEqual(bindings["stateless-local"]["join_semantics"], "stateless_prompt_call")
        self.assertEqual(bindings["stateless-local"]["context_durability"], "stateless_prompt")

        self.assertIn("resident session", durable_role["research_focus"])
        self.assertEqual(bindings["durable-codex"]["join_semantics"], "codex_exec_resume")
        self.assertEqual(bindings["durable-codex"]["context_durability"], "provider_managed_resume")

        self.assertNotIn("from your resident session", question)
        self.assertIn("declared join semantics", question)
        self.assertNotIn("from your resident session", instruction)
        self.assertIn("declared join semantics", instruction)

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
            self.assertIn(
                "entry claude ready terminal_session terminal_pty_prompt_bridge process_lifetime advisory path_and_pty_preflight auto_join approval required",
                output,
            )
            self.assertIn(
                "entry gemini legacy terminal_session terminal_pty_prompt_bridge process_lifetime advisory path_and_pty_preflight include_legacy_gemini",
                output,
            )
            self.assertIn(
                "entry codex missing codex_live_session codex_exec_resume provider_managed_resume codex_readonly path_and_codex_safety_preflight install_cli",
                output,
            )

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
            self.assertIn(
                "entry claude unsupported terminal_session terminal_pty_prompt_bridge process_lifetime advisory path_and_pty_preflight unsupported_terminal",
                output,
            )
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
            self.assertEqual(
                [role["join_semantics"] for role in council["roles"]],
                ["terminal_pty_prompt_bridge", "codex_exec_resume", "self_service_room_loop"],
            )
            self.assertEqual(
                [role["context_durability"] for role in council["roles"]],
                ["process_lifetime", "provider_managed_resume", "provider_managed_room_loop"],
            )
            self.assertEqual(
                [role["sandbox_enforcement"] for role in council["roles"]],
                ["advisory", "codex_readonly", "advisory"],
            )
            self.assertNotIn("discovered local CLI transport", council["roles"][0]["research_focus"])
            self.assertIn("terminal_pty_prompt_bridge", council["roles"][0]["research_focus"])
            self.assertIn("sandbox enforcement is advisory", council["roles"][0]["research_focus"])
            self.assertEqual(agent_config["providers"][0]["join_semantics"], "terminal_pty_prompt_bridge")
            self.assertEqual(agent_config["providers"][1]["context_durability"], "provider_managed_resume")
            self.assertEqual(agent_config["providers"][1]["sandbox_enforcement"], "codex_readonly")
            self.assertEqual(agent_config["agent_bindings"][2]["evidence_basis"], "path_and_self_service_preflight")
            self.assertEqual(agent_config["agent_bindings"][2]["sandbox_enforcement"], "advisory")
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

    def test_live_agent_auto_join_can_approve_only_one_discovered_agent(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = Path(temp_dir) / "live-agents.discovered.json"
            requests = []

            def resolver(command):
                return f"/opt/bin/{command}" if command in {"claude", "codex", "antigravity"} else None

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
                    return {
                        "status": "ready",
                        "meeting_id": "resident-m1",
                        "group_id": "live-agents.discovered",
                        "connection": {"expected": 1, "connected": 1, "attention": []},
                        "process": {"status": "running", "attention": []},
                    }
                if url == "http://room.local/api/live-agent-session-runs/ensure":
                    return {
                        "status": "ready",
                        "action": "start",
                        "meeting_id": "resident-m1",
                        "group_id": "live-agents.discovered",
                        "connection": {"expected": 1, "connected": 1, "attention": []},
                        "process": {"status": "running", "attention": []},
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
                                "--approve-real-providers",
                                "--approve-agent",
                                "codex-live",
                                "--json",
                            ]
                        )

            self.assertEqual(exit_code, 0)
            written = json.loads(output_path.read_text(encoding="utf-8"))
            self.assertEqual([agent["agent_id"] for agent in written["agents"]], ["codex-live"])
            council = json.loads((Path(temp_dir) / "council.discovered.json").read_text(encoding="utf-8"))
            agent_config = json.loads((Path(temp_dir) / "agents.discovered.json").read_text(encoding="utf-8"))
            self.assertEqual([role["id"] for role in council["roles"]], ["codex_live"])
            self.assertEqual([binding["agent_id"] for binding in agent_config["agent_bindings"]], ["codex-live"])
            ensure_request = next(request for request in requests if request["url"] == "http://room.local/api/live-agent-session-runs/ensure")
            self.assertEqual(ensure_request["payload"]["approve_real_providers"], True)
            self.assertEqual(ensure_request["payload"]["probe_bound_agents"], True)
            payload = json.loads(stdout.getvalue())
            discoveries = {item["command"]: item for item in payload["discovery"]["discoveries"]}
            self.assertEqual(discoveries["codex"]["approval_status"], "approved")
            self.assertTrue(discoveries["codex"]["included"])
            self.assertEqual(discoveries["claude"]["approval_status"], "not_approved")
            self.assertFalse(discoveries["claude"]["included"])
            self.assertEqual(discoveries["antigravity"]["approval_status"], "not_approved")
            self.assertFalse(discoveries["antigravity"]["included"])
            self.assertEqual(payload["discovery"]["approval_filter"]["approved_agents"], ["codex-live"])
            self.assertEqual(payload["session"]["connection"]["expected"], 1)

    def test_live_agent_auto_join_can_approve_by_discovered_command(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = Path(temp_dir) / "live-agents.discovered.json"

            def resolver(command):
                return f"/opt/bin/{command}" if command in {"claude", "codex"} else None

            def request_json(url, *, method="GET", payload=None, timeout_seconds=None):
                if url.startswith("http://room.local/api/live-agent-sessions/readiness?"):
                    return {
                        "status": "ready",
                        "meeting_id": "resident-m1",
                        "group_id": "live-agents.discovered",
                        "connection": {"expected": 1, "connected": 1, "attention": []},
                        "process": {"status": "running", "attention": []},
                    }
                if url == "http://room.local/api/live-agent-session-runs/ensure":
                    return {
                        "status": "ready",
                        "action": "start",
                        "meeting_id": "resident-m1",
                        "group_id": "live-agents.discovered",
                        "connection": {"expected": 1, "connected": 1, "attention": []},
                        "process": {"status": "running", "attention": []},
                    }
                raise AssertionError(f"unexpected request: {url}")

            stdout = StringIO()
            with (
                patch("agentsassemble.live_agent_discovery.shutil.which", side_effect=resolver),
                patch("agentsassemble.live_agent_discovery.terminal_sessions_supported", return_value=True),
                patch("agentsassemble.cli._request_json", side_effect=request_json),
                patch("sys.stdout", stdout),
            ):
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
                        "--approve-command",
                        "codex",
                        "--json",
                    ]
                )

            self.assertEqual(exit_code, 0)
            written = json.loads(output_path.read_text(encoding="utf-8"))
            self.assertEqual([agent["agent_id"] for agent in written["agents"]], ["codex-live"])
            payload = json.loads(stdout.getvalue())
            self.assertEqual(payload["discovery"]["approval_filter"]["approved_commands"], ["codex"])

    def test_live_agent_auto_join_exact_approval_with_no_match_does_not_write_or_ensure(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = Path(temp_dir) / "live-agents.discovered.json"
            stdout = StringIO()

            def resolver(command):
                return f"/opt/bin/{command}" if command == "codex" else None

            with (
                patch("agentsassemble.live_agent_discovery.shutil.which", side_effect=resolver),
                patch("agentsassemble.live_agent_discovery.terminal_sessions_supported", return_value=True),
                patch("agentsassemble.cli._request_json", side_effect=AssertionError("approval filter must stop before ensure")),
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
                        "--approve-agent",
                        "unknown-live",
                        "--json",
                    ]
                )

            self.assertEqual(exit_code, 1)
            self.assertFalse(output_path.exists())
            payload = json.loads(stdout.getvalue())
            self.assertEqual(payload["status"], "approval_required")
            self.assertEqual(payload["action"], "none")
            self.assertEqual(payload["discovery"]["approval_filter"]["approved_count"], 0)
            self.assertEqual(payload["discovery"]["approval_filter"]["approved_agents"], [])
            self.assertEqual(payload["discovery"]["approval_filter"]["unmatched_approval_count"], 1)
            self.assertNotIn("unknown-live", json.dumps(payload, ensure_ascii=False))
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
                    "approve_real_providers": True,
                    "probe_bound_agents": True,
                    "probe_timeout_seconds": 12.0,
                },
            )
            payload = json.loads(stdout.getvalue())
            self.assertEqual(payload["status"], "ready")
            self.assertEqual(payload["action"], "start")
            self.assertEqual(payload["discovery"]["session_bundle"]["group_id"], "live-agents.discovered")
            self.assertEqual(payload["session"]["connection"]["connected"], 2)
            self.assertEqual(payload["session"]["session_run"]["run_id"], "run-auto-1")

    def test_live_agent_auto_join_does_not_force_probe_for_approval_free_discovery(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = Path(temp_dir) / "live-agents.fake.json"
            requests = []
            report = {
                "status": "ok",
                "config": {"agents": [{"agent_id": "fake-local", "provider_kind": "local_cli"}]},
                "discoveries": [
                    {
                        "command": "fake",
                        "provider_kind": "local_cli",
                        "available": True,
                        "included": True,
                        "requires_approval": False,
                    }
                ],
                "session_bundle": {
                    "live_agent_config_path": str(output_path),
                    "council_config_path": str(Path(temp_dir) / "council.fake.json"),
                    "agent_config_path": str(Path(temp_dir) / "agents.fake.json"),
                    "group_id": "live-agents.fake",
                },
            }

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
                        "action": "start",
                        "meeting_id": "resident-fake",
                        "group_id": "live-agents.fake",
                        "connection": {"expected": 1, "connected": 1, "attention": []},
                        "process": {"status": "running", "attention": []},
                    }
                if url.startswith("http://room.local/api/live-agent-sessions/readiness?"):
                    return {
                        "status": "ready",
                        "meeting_id": "resident-fake",
                        "group_id": "live-agents.fake",
                        "connection": {"expected": 1, "connected": 1, "attention": []},
                        "process": {"status": "running", "attention": []},
                    }
                raise AssertionError(f"unexpected request: {url}")

            stdout = StringIO()
            with (
                patch("agentsassemble.cli._write_live_agent_discovery_outputs", return_value=(output_path, report)),
                patch("agentsassemble.cli._request_json", side_effect=request_json),
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

            self.assertEqual(exit_code, 0)
            ensure_request = next(request for request in requests if request["url"] == "http://room.local/api/live-agent-session-runs/ensure")
            self.assertNotIn("probe_bound_agents", ensure_request["payload"])
            self.assertNotIn("probe_timeout_seconds", ensure_request["payload"])
            payload = json.loads(stdout.getvalue())
            self.assertEqual(payload["status"], "ready")

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
            operation = json.loads((root / "live-agent-runs" / "operations.jsonl").read_text(encoding="utf-8").splitlines()[-1])
            self.assertEqual(operation["operation"], "discovery.run")
            self.assertEqual(operation["details"]["join_semantics"], ["self_service_room_loop", "terminal_pty_prompt_bridge"])
            self.assertEqual(operation["details"]["context_durability"], ["process_lifetime", "provider_managed_room_loop"])
            self.assertEqual(operation["details"]["sandbox_enforcement"], ["advisory"])
            self.assertEqual(operation["details"]["evidence_basis"], ["path_and_pty_preflight", "path_and_self_service_preflight"])
            self.assertEqual(operation["details"]["approval_required"], 2)
            self.assertNotIn("/opt/bin", json.dumps(operation, ensure_ascii=False))

    def test_discovery_operation_contract_values_are_whitelisted(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            append_live_agent_operation(
                root,
                operation="discovery.run",
                status="success",
                details={
                    "join_semantics": ["terminal_pty_prompt_bridge", "env:OPENAI_API_KEY"],
                    "context_durability": ["process_lifetime", "literal:secret"],
                    "sandbox_enforcement": ["advisory", "os_sandboxed", "literal:secret"],
                    "evidence_basis": ["path_and_pty_preflight", "sk-abcdef123456"],
                },
            )

            operation = read_live_agent_operations(root, limit=1)[0]

        self.assertEqual(operation["details"]["join_semantics"], ["terminal_pty_prompt_bridge"])
        self.assertEqual(operation["details"]["context_durability"], ["process_lifetime"])
        self.assertEqual(operation["details"]["sandbox_enforcement"], ["advisory", "os_sandboxed"])
        self.assertEqual(operation["details"]["evidence_basis"], ["path_and_pty_preflight"])
        operation_text = json.dumps(operation, ensure_ascii=False)
        self.assertNotIn("env:OPENAI_API_KEY", operation_text)
        self.assertNotIn("literal:secret", operation_text)
        self.assertNotIn("sk-abcdef123456", operation_text)

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
