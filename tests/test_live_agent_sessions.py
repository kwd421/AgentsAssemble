import json
import sys
import tempfile
import unittest
from pathlib import Path

from agentsassemble.live_agent_meetings import start_live_agent_meeting
from agentsassemble.live_agent_runner import load_group_configs
from agentsassemble.live_agent_sessions import (
    check_live_agent_session,
    resume_live_agent_session,
    restart_live_agent_session,
    start_live_agent_session,
    stop_live_agent_session,
)
from agentsassemble.live_agents import connect_live_agent, heartbeat_live_agent, read_live_agents


class FakeSessionSupervisor:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.started = []

    def start_group(self, **kwargs):
        self.started.append(kwargs)
        return {
            "group_id": kwargs.get("group_id") or "resident-group",
            "status": "running",
            "agents": [
                {"agent_id": "agent-a", "display_name": "Agent A", "provider_kind": "local_cli", "connection_kind": "local_cli"}
            ],
        }


class LiveAgentSessionStartTests(unittest.TestCase):
    def test_start_session_refuses_manifest_mismatch_before_writing_state(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            council_config = _write_council_config(root, ["architect", "critic"])
            agent_config = _write_agent_config(root, ["agent-a", "agent-b"])
            live_agent_config = _write_live_agent_config(root, ["agent-a"])
            supervisor = FakeSessionSupervisor(root)

            with self.assertRaises(ValueError) as raised:
                start_live_agent_session(
                    root,
                    supervisor,
                    server="http://127.0.0.1:8765",
                    council_config_path=council_config,
                    agent_config_path=agent_config,
                    live_agent_config_path=live_agent_config,
                    meeting_id="resident-m1",
                    group_id="resident-main",
                )

            self.assertIn("does not cover meeting agents", str(raised.exception))
            self.assertEqual(supervisor.started, [])
            self.assertFalse((root / "meetings").exists())
            self.assertFalse((root / "live-agent-runs").exists())

    def test_start_session_refuses_extra_manifest_agent_before_writing_state(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            council_config = _write_council_config(root, ["architect"])
            agent_config = _write_agent_config(root, ["agent-a"])
            live_agent_config = _write_live_agent_config(root, ["agent-a", "agent-extra"])
            supervisor = FakeSessionSupervisor(root)

            with self.assertRaises(ValueError) as raised:
                start_live_agent_session(
                    root,
                    supervisor,
                    server="http://127.0.0.1:8765",
                    council_config_path=council_config,
                    agent_config_path=agent_config,
                    live_agent_config_path=live_agent_config,
                    meeting_id="resident-m1",
                    group_id="resident-main",
                )

            self.assertIn("does not match meeting agents", str(raised.exception))
            self.assertEqual(supervisor.started, [])
            self.assertFalse((root / "meetings").exists())

    def test_start_session_refuses_provider_kind_mismatch_before_writing_state(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            council_config = _write_council_config(root, ["architect"])
            agent_config = _write_agent_config(root, ["agent-a"], provider_kind="claude_code")
            live_agent_config = _write_live_agent_config(root, ["agent-a"], provider_kind="local_cli")
            supervisor = FakeSessionSupervisor(root)

            with self.assertRaises(ValueError) as raised:
                start_live_agent_session(
                    root,
                    supervisor,
                    server="http://127.0.0.1:8765",
                    council_config_path=council_config,
                    agent_config_path=agent_config,
                    live_agent_config_path=live_agent_config,
                    meeting_id="resident-m1",
                    group_id="resident-main",
                )

            self.assertIn("provider_kind", str(raised.exception))
            self.assertEqual(supervisor.started, [])
            self.assertFalse((root / "meetings").exists())

    def test_start_session_refuses_remote_provider_with_local_connection_before_writing_state(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            council_config = _write_council_config(root, ["architect"])
            agent_config = _write_agent_config(root, ["agent-a"], provider_kind="remote_http_bridge")
            live_agent_config = _write_live_agent_config(root, ["agent-a"], provider_kind="remote_http_bridge", connection_kind="local_cli")
            supervisor = FakeSessionSupervisor(root)

            with self.assertRaises(ValueError) as raised:
                start_live_agent_session(
                    root,
                    supervisor,
                    server="http://127.0.0.1:8765",
                    council_config_path=council_config,
                    agent_config_path=agent_config,
                    live_agent_config_path=live_agent_config,
                    meeting_id="resident-m1",
                    group_id="resident-main",
                )

            self.assertIn("connection_kind", str(raised.exception))
            self.assertEqual(supervisor.started, [])
            self.assertFalse((root / "meetings").exists())

    def test_start_session_allows_remote_bridge_provider_label_behind_bridge_transport(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            council_config = _write_council_config(root, ["architect"])
            agent_config = _write_agent_config(root, ["agent-a"], provider_kind="remote_http_bridge")
            live_agent_config = _write_live_agent_config(
                root,
                ["agent-a"],
                provider_kind="claude_code",
                connection_kind="remote_bridge",
                endpoint="http://bridge.local:8777",
                auth_ref="literal:test-token",
            )

            session = start_live_agent_session(
                root,
                FakeSessionSupervisor(root),
                server="http://127.0.0.1:8765",
                council_config_path=council_config,
                agent_config_path=agent_config,
                live_agent_config_path=live_agent_config,
                meeting_id="resident-m1",
                group_id="resident-main",
                connect_timeout_seconds=0,
            )

            self.assertEqual(session["group"]["status"], "running")
            self.assertEqual(session["connection"]["expected"], 1)

    def test_start_session_example_configs_match_demo_council(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            from agentsassemble.live_agents import heartbeat_live_agent

            root = Path(temp_dir)

            class ExampleSupervisor:
                def start_group(self, **kwargs):
                    configs = load_group_configs(kwargs["config_path"], server_override=kwargs["server"])
                    for config in configs:
                        heartbeat_live_agent(root, config.agent_id, status="online")
                    return {
                        "group_id": kwargs.get("group_id") or "resident-main",
                        "status": "running",
                        "agents": [
                            {
                                "agent_id": config.agent_id,
                                "display_name": config.display_name,
                                "provider_kind": config.provider_kind,
                                "connection_kind": config.connection_kind,
                            }
                            for config in configs
                        ],
                    }

            session = start_live_agent_session(
                root,
                ExampleSupervisor(),
                server="http://127.0.0.1:8765",
                council_config_path=Path("configs/demo-council.json"),
                agent_config_path=Path("configs/agents.start-session.example.json"),
                live_agent_config_path=Path("configs/live-agents.start-session.example.json"),
                meeting_id="resident-example",
                group_id="resident-main",
                connect_timeout_seconds=0,
                preflight_checker=lambda *args, **kwargs: {"status": "ok"},
            )

            self.assertEqual(session["status"], "ready")
            self.assertEqual(session["connection"]["expected"], 3)
            self.assertEqual(session["connection"]["connected"], 3)
            self.assertEqual(session["process"]["matched"], 3)

    def test_start_session_diagnostic_marks_agents_and_process_group(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            council_config = _write_council_config(root, ["architect"])
            agent_config = _write_agent_config(root, ["agent-a"])
            live_agent_config = _write_live_agent_config(root, ["agent-a"])

            class DiagnosticSupervisor:
                def __init__(self) -> None:
                    self.started = []

                def start_group(self, **kwargs):
                    self.started.append(kwargs)
                    heartbeat_live_agent(root, "agent-a", status="online")
                    return {
                        "group_id": kwargs.get("group_id") or "resident-main",
                        "status": "running",
                        "diagnostic": kwargs.get("diagnostic"),
                        "agents": [
                            {
                                "agent_id": "agent-a",
                                "display_name": "Agent A",
                                "provider_kind": "local_cli",
                                "connection_kind": "local_cli",
                            }
                        ],
                    }

            supervisor = DiagnosticSupervisor()

            session = start_live_agent_session(
                root,
                supervisor,
                server="http://127.0.0.1:8765",
                council_config_path=council_config,
                agent_config_path=agent_config,
                live_agent_config_path=live_agent_config,
                meeting_id="resident-diagnostic",
                group_id="resident-diagnostic",
                connect_timeout_seconds=0,
                diagnostic=True,
            )

            self.assertEqual(session["status"], "ready")
            self.assertTrue(supervisor.started[0]["diagnostic"])
            agents = read_live_agents(root)
            self.assertEqual({agent["agent_id"]: agent["diagnostic"] for agent in agents}, {"agent-a": True})

    def test_start_session_preflight_failure_reports_agent_level_reason(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            council_config = _write_council_config(root, ["architect"])
            agent_config = _write_agent_config(root, ["agent-a"])
            live_agent_config = _write_live_agent_config(root, ["agent-a"])
            supervisor = FakeSessionSupervisor(root)

            def failed_preflight(path, *, server_override=None):
                return {
                    "status": "failed",
                    "checks": [{"id": "agent_ids", "status": "ok", "message": "Agent ids are unique."}],
                    "agents": [
                        {
                            "agent_id": "agent-a",
                            "checks": [
                                {"id": "command", "status": "failed", "message": "Command not found: missing-cli"}
                            ],
                        }
                    ],
                }

            with self.assertRaises(ValueError) as raised:
                start_live_agent_session(
                    root,
                    supervisor,
                    server="http://127.0.0.1:8765",
                    council_config_path=council_config,
                    agent_config_path=agent_config,
                    live_agent_config_path=live_agent_config,
                    meeting_id="resident-m1",
                    group_id="resident-main",
                    preflight_checker=failed_preflight,
                )

            self.assertIn("agent-a command: Command not found: missing-cli", str(raised.exception))
            self.assertEqual(supervisor.started, [])
            self.assertFalse((root / "meetings").exists())

    def test_start_session_preflight_failure_redacts_top_level_config_path(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            council_config = _write_council_config(root, ["architect"])
            agent_config = _write_agent_config(root, ["agent-a"])
            live_agent_config = _write_live_agent_config(root, ["agent-a"])
            private_config = root / "private-live-agents.json"
            supervisor = FakeSessionSupervisor(root)

            def failed_preflight(path, *, server_override=None):
                return {
                    "status": "failed",
                    "checks": [
                        {
                            "id": "config_load",
                            "status": "failed",
                            "message": f"No such file or directory: '{private_config}'",
                        }
                    ],
                    "agents": [],
                }

            with self.assertRaises(ValueError) as raised:
                start_live_agent_session(
                    root,
                    supervisor,
                    server="http://127.0.0.1:8765",
                    council_config_path=council_config,
                    agent_config_path=agent_config,
                    live_agent_config_path=live_agent_config,
                    meeting_id="resident-m1",
                    group_id="resident-main",
                    preflight_checker=failed_preflight,
                )

            message = str(raised.exception)
            self.assertIn("config_load", message)
            self.assertIn("details redacted", message)
            self.assertNotIn(str(private_config), message)
            self.assertNotIn("private-live-agents", message)
            self.assertEqual(supervisor.started, [])
            self.assertFalse((root / "meetings").exists())

    def test_start_session_preflight_failure_redacts_agent_command_path(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            council_config = _write_council_config(root, ["architect"])
            agent_config = _write_agent_config(root, ["agent-a"])
            live_agent_config = _write_live_agent_config(root, ["agent-a"])
            private_command = root / "secret-tool"
            supervisor = FakeSessionSupervisor(root)

            def failed_preflight(path, *, server_override=None):
                return {
                    "status": "failed",
                    "checks": [],
                    "agents": [
                        {
                            "agent_id": "agent-a",
                            "checks": [
                                {
                                    "id": "command",
                                    "status": "failed",
                                    "message": f"Command not found: {private_command}",
                                }
                            ],
                        }
                    ],
                }

            with self.assertRaises(ValueError) as raised:
                start_live_agent_session(
                    root,
                    supervisor,
                    server="http://127.0.0.1:8765",
                    council_config_path=council_config,
                    agent_config_path=agent_config,
                    live_agent_config_path=live_agent_config,
                    meeting_id="resident-m1",
                    group_id="resident-main",
                    preflight_checker=failed_preflight,
                )

            message = str(raised.exception)
            self.assertIn("agent-a command", message)
            self.assertIn("details redacted", message)
            self.assertNotIn(str(private_command), message)
            self.assertNotIn("secret-tool", message)
            self.assertEqual(supervisor.started, [])
            self.assertFalse((root / "meetings").exists())

    def test_start_session_returns_sanitized_payload_without_config_paths_or_log_tail(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            council_config = _write_council_config(root, ["architect"])
            agent_config = _write_agent_config(root, ["agent-a"])
            live_agent_config = _write_live_agent_config(root, ["agent-a"])

            class SensitiveSupervisor(FakeSessionSupervisor):
                def start_group(self, **kwargs):
                    self.started.append(kwargs)
                    return {
                        "group_id": "resident-main",
                        "status": "running",
                        "config_path": str(live_agent_config),
                        "server": "http://room.local",
                        "log_path": str(root / "live-agent-runs" / "resident-main.log"),
                        "log_tail": "provider secret output",
                        "agents": [
                            {
                                "agent_id": "agent-a",
                                "display_name": "Agent A",
                                "provider_kind": "local_cli",
                                "connection_kind": "local_cli",
                            }
                        ],
                    }

            session = start_live_agent_session(
                root,
                SensitiveSupervisor(root),
                server="http://127.0.0.1:8765",
                council_config_path=council_config,
                agent_config_path=agent_config,
                live_agent_config_path=live_agent_config,
                meeting_id="resident-m1",
                group_id="resident-main",
                connect_timeout_seconds=0,
            )

            serialized = json.dumps(session, ensure_ascii=False)
            self.assertNotIn(str(council_config), serialized)
            self.assertNotIn(str(agent_config), serialized)
            self.assertNotIn(str(live_agent_config), serialized)
            self.assertNotIn("provider secret output", serialized)
            self.assertEqual(session["group"], {"group_id": "resident-main", "status": "running"})
            self.assertEqual(
                session["meeting"],
                {
                    "meeting_id": "resident-m1",
                    "live_status": "running",
                    "role_count": 1,
                    "bound_agent_count": 1,
                },
            )

    def test_start_session_returns_starting_when_agents_are_not_connected_yet(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            council_config = _write_council_config(root, ["architect"])
            agent_config = _write_agent_config(root, ["agent-a"])
            live_agent_config = _write_live_agent_config(root, ["agent-a"])
            supervisor = FakeSessionSupervisor(root)

            session = start_live_agent_session(
                root,
                supervisor,
                server="http://127.0.0.1:8765",
                council_config_path=council_config,
                agent_config_path=agent_config,
                live_agent_config_path=live_agent_config,
                meeting_id="resident-m1",
                group_id="resident-main",
                connect_timeout_seconds=0,
            )

            self.assertEqual(session["status"], "starting")
            self.assertEqual(session["connection"]["expected"], 1)
            self.assertEqual(session["connection"]["connected"], 0)
            self.assertEqual(session["connection"]["attention"], ["agent-a:offline"])
            self.assertTrue((root / "meetings" / "resident-m1" / "live_state.json").exists())

    def test_start_session_returns_starting_when_group_is_not_running_even_if_agents_connected(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            from agentsassemble.live_agents import heartbeat_live_agent

            root = Path(temp_dir)
            council_config = _write_council_config(root, ["architect"])
            agent_config = _write_agent_config(root, ["agent-a"])
            live_agent_config = _write_live_agent_config(root, ["agent-a"])

            class StoppedSupervisor(FakeSessionSupervisor):
                def start_group(self, **kwargs):
                    self.started.append(kwargs)
                    heartbeat_live_agent(root, "agent-a", status="online")
                    return {"group_id": "resident-main", "status": "stopped", "agents": [{"agent_id": "agent-a"}]}

            session = start_live_agent_session(
                root,
                StoppedSupervisor(root),
                server="http://127.0.0.1:8765",
                council_config_path=council_config,
                agent_config_path=agent_config,
                live_agent_config_path=live_agent_config,
                meeting_id="resident-m1",
                group_id="resident-main",
                connect_timeout_seconds=0,
            )

            self.assertEqual(session["status"], "starting")
            self.assertEqual(session["connection"]["connected"], 1)
            self.assertEqual(session["process"]["status"], "stopped")
            self.assertIn("group:stopped", session["process"]["attention"])

    def test_start_session_start_group_failure_exposes_created_meeting_for_recovery(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            council_config = _write_council_config(root, ["architect"])
            agent_config = _write_agent_config(root, ["agent-a"])
            live_agent_config = _write_live_agent_config(root, ["agent-a"])

            class FailingSupervisor(FakeSessionSupervisor):
                def start_group(self, **kwargs):
                    self.started.append(kwargs)
                    raise RuntimeError("process launch refused")

            supervisor = FailingSupervisor(root)
            with self.assertRaises(Exception) as raised:
                start_live_agent_session(
                    root,
                    supervisor,
                    server="http://127.0.0.1:8765",
                    council_config_path=council_config,
                    agent_config_path=agent_config,
                    live_agent_config_path=live_agent_config,
                    group_id="resident-main",
                )

            meeting_id = getattr(raised.exception, "meeting_id", "")
            self.assertTrue(meeting_id)
            self.assertTrue((root / "meetings" / meeting_id / "live_state.json").exists())

    def test_start_session_refuses_resident_config_for_another_meeting_before_writing_state(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            council_config = _write_council_config(root, ["architect"])
            agent_config = _write_agent_config(root, ["agent-a"])
            live_agent_config = _write_live_agent_config(root, ["agent-a"], meeting_id="other-meeting")
            supervisor = FakeSessionSupervisor(root)

            with self.assertRaises(ValueError) as raised:
                start_live_agent_session(
                    root,
                    supervisor,
                    server="http://127.0.0.1:8765",
                    council_config_path=council_config,
                    agent_config_path=agent_config,
                    live_agent_config_path=live_agent_config,
                    meeting_id="resident-m1",
                    group_id="resident-main",
                )

            self.assertIn("meeting id does not match", str(raised.exception))
            self.assertEqual(supervisor.started, [])
            self.assertFalse((root / "meetings").exists())

    def test_resume_session_reuses_running_group_for_existing_meeting_without_recreating_it(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            council_config = _write_council_config(root, ["architect"])
            agent_config = _write_agent_config(root, ["agent-a"])
            live_agent_config = _write_live_agent_config(root, ["agent-a"])
            start_live_agent_meeting(
                root,
                council_config_path=council_config,
                agent_config_path=agent_config,
                meeting_id="resident-m1",
            )
            heartbeat_live_agent(root, "agent-a", status="online")

            class RunningSupervisor:
                def __init__(self) -> None:
                    self.started = []

                def list_groups(self):
                    return [
                        {
                            "group_id": "resident-main",
                            "status": "running",
                            "agents": [{"agent_id": "agent-a", "provider_kind": "local_cli", "connection_kind": "local_cli"}],
                        }
                    ]

                def start_group(self, **kwargs):
                    self.started.append(kwargs)
                    raise AssertionError("resume must not start a group that is already running")

            supervisor = RunningSupervisor()

            session = resume_live_agent_session(
                root,
                supervisor,
                server="http://127.0.0.1:8765",
                live_agent_config_path=live_agent_config,
                meeting_id="resident-m1",
                group_id="resident-main",
                connect_timeout_seconds=0,
                preflight_checker=lambda *args, **kwargs: {"status": "ok"},
            )

            self.assertEqual(session["status"], "ready")
            self.assertEqual(session["meeting_id"], "resident-m1")
            self.assertEqual(session["group_id"], "resident-main")
            self.assertEqual(session["connection"]["connected"], 1)
            self.assertEqual(supervisor.started, [])
            meeting = json.loads((root / "meetings" / "resident-m1" / "live_state.json").read_text(encoding="utf-8"))
            self.assertEqual(meeting["meeting_id"], "resident-m1")

    def test_resume_session_reuses_running_group_with_normalized_group_id(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            council_config = _write_council_config(root, ["architect"])
            agent_config = _write_agent_config(root, ["agent-a"])
            live_agent_config = _write_live_agent_config(root, ["agent-a"])
            start_live_agent_meeting(
                root,
                council_config_path=council_config,
                agent_config_path=agent_config,
                meeting_id="resident-m1",
            )
            heartbeat_live_agent(root, "agent-a", status="online")

            class NormalizingSupervisor:
                def __init__(self) -> None:
                    self.started = []

                def list_groups(self):
                    return [
                        {
                            "group_id": "resident-main",
                            "status": "running",
                            "agents": [{"agent_id": "agent-a", "provider_kind": "local_cli", "connection_kind": "local_cli"}],
                        }
                    ]

                def start_group(self, **kwargs):
                    self.started.append(kwargs)
                    raise AssertionError("resume must normalize group id before deciding whether to start")

            supervisor = NormalizingSupervisor()

            session = resume_live_agent_session(
                root,
                supervisor,
                server="http://127.0.0.1:8765",
                live_agent_config_path=live_agent_config,
                meeting_id="resident-m1",
                group_id="resident main",
                connect_timeout_seconds=0,
                preflight_checker=lambda *args, **kwargs: {"status": "ok"},
            )

            self.assertEqual(session["status"], "ready")
            self.assertEqual(session["group_id"], "resident-main")
            self.assertEqual(supervisor.started, [])

    def test_resume_session_starts_missing_group_without_marking_roster_online(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            council_config = _write_council_config(root, ["architect"])
            agent_config = _write_agent_config(root, ["agent-a"])
            live_agent_config = _write_live_agent_config(root, ["agent-a"])
            start_live_agent_meeting(
                root,
                council_config_path=council_config,
                agent_config_path=agent_config,
                meeting_id="resident-m1",
            )
            (root / "live_agents.json").unlink()

            class MissingSupervisor:
                def __init__(self) -> None:
                    self.started = []

                def list_groups(self):
                    return []

                def start_group(self, **kwargs):
                    self.started.append(kwargs)
                    return {
                        "group_id": kwargs.get("group_id") or "resident-main",
                        "status": "running",
                        "agents": [{"agent_id": "agent-a", "provider_kind": "local_cli", "connection_kind": "local_cli"}],
                    }

            supervisor = MissingSupervisor()

            session = resume_live_agent_session(
                root,
                supervisor,
                server="http://127.0.0.1:8765",
                live_agent_config_path=live_agent_config,
                meeting_id="resident-m1",
                group_id="resident-main",
                connect_timeout_seconds=0,
                preflight_checker=lambda *args, **kwargs: {"status": "ok"},
            )

            self.assertEqual(session["status"], "starting")
            self.assertEqual(session["connection"]["attention"], ["agent-a:offline"])
            self.assertEqual(supervisor.started[0]["config_path"], live_agent_config)
            self.assertEqual(supervisor.started[0]["group_id"], "resident-main")
            agents = read_live_agents(root)
            self.assertEqual(len(agents), 1)
            self.assertEqual(agents[0]["agent_id"], "agent-a")
            self.assertEqual(agents[0]["meeting_id"], "resident-m1")
            self.assertEqual(agents[0]["status"], "offline")

    def test_resume_session_restarts_unknown_group_from_supplied_config(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            council_config = _write_council_config(root, ["architect"])
            agent_config = _write_agent_config(root, ["agent-a"])
            live_agent_config = _write_live_agent_config(root, ["agent-a"])
            start_live_agent_meeting(
                root,
                council_config_path=council_config,
                agent_config_path=agent_config,
                meeting_id="resident-m1",
            )

            class UnknownSupervisor:
                def __init__(self) -> None:
                    self.started = []
                    self.recovered = []

                def list_groups(self):
                    return [
                        {
                            "group_id": "resident-main",
                            "status": "unknown",
                            "agents": [{"agent_id": "old-agent"}],
                        }
                    ]

                def recover_group(self, group_id):
                    self.recovered.append(group_id)
                    return {"group_id": group_id, "status": "running", "agents": [{"agent_id": "old-agent"}]}

                def start_group(self, **kwargs):
                    self.started.append(kwargs)
                    return {
                        "group_id": kwargs.get("group_id") or "resident-main",
                        "status": "running",
                        "agents": [{"agent_id": "agent-a", "provider_kind": "local_cli", "connection_kind": "local_cli"}],
                    }

            supervisor = UnknownSupervisor()

            session = resume_live_agent_session(
                root,
                supervisor,
                server="http://127.0.0.1:8765",
                live_agent_config_path=live_agent_config,
                meeting_id="resident-m1",
                group_id="resident-main",
                connect_timeout_seconds=0,
                preflight_checker=lambda *args, **kwargs: {"status": "ok"},
            )

            self.assertEqual(session["process"]["matched"], 1)
            self.assertEqual(supervisor.recovered, [])
            self.assertEqual(supervisor.started[0]["config_path"], live_agent_config)

    def test_resume_session_refuses_missing_meeting_before_starting_processes(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            live_agent_config = _write_live_agent_config(root, ["agent-a"])
            supervisor = FakeSessionSupervisor(root)

            with self.assertRaises(ValueError) as raised:
                resume_live_agent_session(
                    root,
                    supervisor,
                    server="http://127.0.0.1:8765",
                    live_agent_config_path=live_agent_config,
                    meeting_id="missing-meeting",
                    group_id="resident-main",
                )

            self.assertIn("Meeting missing-meeting was not found", str(raised.exception))
            self.assertEqual(supervisor.started, [])

    def test_resume_session_refuses_manifest_mismatch_against_existing_meeting(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            council_config = _write_council_config(root, ["architect", "critic"])
            agent_config = _write_agent_config(root, ["agent-a", "agent-b"])
            live_agent_config = _write_live_agent_config(root, ["agent-a"])
            start_live_agent_meeting(
                root,
                council_config_path=council_config,
                agent_config_path=agent_config,
                meeting_id="resident-m1",
            )
            supervisor = FakeSessionSupervisor(root)

            with self.assertRaises(ValueError) as raised:
                resume_live_agent_session(
                    root,
                    supervisor,
                    server="http://127.0.0.1:8765",
                    live_agent_config_path=live_agent_config,
                    meeting_id="resident-m1",
                    group_id="resident-main",
                    preflight_checker=lambda *args, **kwargs: {"status": "ok"},
                )

            self.assertIn("does not cover meeting agents", str(raised.exception))
            self.assertEqual(supervisor.started, [])

    def test_stop_session_stops_group_and_marks_bound_agents_offline(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            council_config = _write_council_config(root, ["architect", "critic"])
            agent_config = _write_agent_config(root, ["agent-a", "agent-b"])
            start_live_agent_meeting(
                root,
                council_config_path=council_config,
                agent_config_path=agent_config,
                meeting_id="resident-m1",
            )
            heartbeat_live_agent(root, "agent-a", status="online")
            heartbeat_live_agent(root, "agent-b", status="working")

            class StopSupervisor:
                def __init__(self) -> None:
                    self.stopped = []

                def stop_group(self, group_id):
                    self.stopped.append(group_id)
                    return {
                        "group_id": group_id,
                        "status": "stopped",
                        "config_path": str(root / "secret-live-agents.json"),
                        "log_tail": "secret provider output",
                        "agents": [{"agent_id": "agent-a"}, {"agent_id": "agent-b"}],
                    }

            supervisor = StopSupervisor()

            session = stop_live_agent_session(
                root,
                supervisor,
                meeting_id="resident-m1",
                group_id="resident main",
            )

            self.assertEqual(session["status"], "stopped")
            self.assertEqual(session["meeting_id"], "resident-m1")
            self.assertEqual(session["group_id"], "resident-main")
            self.assertEqual(supervisor.stopped, ["resident-main"])
            self.assertEqual(session["offline"]["expected"], 2)
            self.assertEqual(session["offline"]["offline"], 2)
            agents = {agent["agent_id"]: agent for agent in read_live_agents(root)}
            self.assertEqual(agents["agent-a"]["status"], "offline")
            self.assertEqual(agents["agent-b"]["status"], "offline")
            serialized = json.dumps(session, ensure_ascii=False)
            self.assertNotIn("secret-live-agents", serialized)
            self.assertNotIn("secret provider output", serialized)
            self.assertTrue((root / "meetings" / "resident-m1" / "live_state.json").exists())

    def test_stop_session_refuses_missing_meeting_before_stopping_group(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)

            class StopSupervisor:
                def __init__(self) -> None:
                    self.stopped = []

                def stop_group(self, group_id):
                    self.stopped.append(group_id)
                    return {"group_id": group_id, "status": "stopped", "agents": []}

            supervisor = StopSupervisor()

            with self.assertRaises(ValueError) as raised:
                stop_live_agent_session(root, supervisor, meeting_id="missing-meeting", group_id="resident-main")

            self.assertIn("Meeting missing-meeting was not found", str(raised.exception))
            self.assertEqual(supervisor.stopped, [])

    def test_stop_session_requires_explicit_group_id_before_stopping(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            council_config = _write_council_config(root, ["architect"])
            agent_config = _write_agent_config(root, ["agent-a"])
            start_live_agent_meeting(
                root,
                council_config_path=council_config,
                agent_config_path=agent_config,
                meeting_id="resident-m1",
            )

            class StopSupervisor:
                def __init__(self) -> None:
                    self.stopped = []

                def stop_group(self, group_id):
                    self.stopped.append(group_id)
                    return {"group_id": group_id, "status": "stopped", "agents": [{"agent_id": "agent-a"}]}

            supervisor = StopSupervisor()

            with self.assertRaises(ValueError) as raised:
                stop_live_agent_session(root, supervisor, meeting_id="resident-m1", group_id=" ")

            self.assertIn("Live agent group id is required", str(raised.exception))
            self.assertEqual(supervisor.stopped, [])

    def test_stop_session_refuses_group_manifest_mismatch_before_stop_and_offline(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            council_config = _write_council_config(root, ["architect", "critic"])
            agent_config = _write_agent_config(root, ["agent-a", "agent-b"])
            start_live_agent_meeting(
                root,
                council_config_path=council_config,
                agent_config_path=agent_config,
                meeting_id="resident-m1",
            )
            heartbeat_live_agent(root, "agent-a", status="online")

            class StopSupervisor:
                def __init__(self) -> None:
                    self.stopped = []

                def list_groups(self):
                    return [
                        {
                            "group_id": "resident-main",
                            "status": "running",
                            "agents": [{"agent_id": "agent-a"}, {"agent_id": "agent-x"}],
                        }
                    ]

                def stop_group(self, group_id):
                    self.stopped.append(group_id)
                    return {"group_id": group_id, "status": "stopped", "agents": [{"agent_id": "agent-a"}]}

            supervisor = StopSupervisor()

            with self.assertRaises(ValueError) as raised:
                stop_live_agent_session(root, supervisor, meeting_id="resident-m1", group_id="resident-main")

            self.assertIn("does not match meeting agents", str(raised.exception))
            self.assertEqual(supervisor.stopped, [])
            agents = {agent["agent_id"]: agent for agent in read_live_agents(root)}
            self.assertEqual(agents["agent-a"]["status"], "online")

    def test_stop_session_leaves_wrong_meeting_roster_row_untouched(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            council_config = _write_council_config(root, ["architect"])
            agent_config = _write_agent_config(root, ["agent-a"])
            start_live_agent_meeting(
                root,
                council_config_path=council_config,
                agent_config_path=agent_config,
                meeting_id="resident-m1",
            )
            connect_live_agent(
                root,
                {
                    "agent_id": "agent-a",
                    "display_name": "Agent A",
                    "provider_kind": "local_cli",
                    "connection_kind": "local_cli",
                    "meeting_id": "other-meeting",
                    "engagement_mode": "moderator_called",
                    "status": "online",
                    "capabilities": ["room_chat", "official_turn"],
                },
            )

            class StopSupervisor:
                def stop_group(self, group_id):
                    return {"group_id": group_id, "status": "stopped", "agents": [{"agent_id": "agent-a"}]}

                def list_groups(self):
                    return [{"group_id": "resident-main", "status": "running", "agents": [{"agent_id": "agent-a"}]}]

            session = stop_live_agent_session(
                root,
                StopSupervisor(),
                meeting_id="resident-m1",
                group_id="resident-main",
            )

            self.assertEqual(session["status"], "stopping")
            self.assertEqual(session["offline"]["expected"], 1)
            self.assertEqual(session["offline"]["offline"], 0)
            self.assertEqual(session["offline"]["attention"], ["agent-a:wrong_meeting"])
            agents = {agent["agent_id"]: agent for agent in read_live_agents(root)}
            self.assertEqual(agents["agent-a"]["meeting_id"], "other-meeting")
            self.assertEqual(agents["agent-a"]["status"], "online")

    def test_stop_session_reports_stopping_when_stop_returns_unknown_status(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            council_config = _write_council_config(root, ["architect"])
            agent_config = _write_agent_config(root, ["agent-a"])
            start_live_agent_meeting(
                root,
                council_config_path=council_config,
                agent_config_path=agent_config,
                meeting_id="resident-m1",
            )
            heartbeat_live_agent(root, "agent-a", status="online")

            class StopSupervisor:
                def stop_group(self, group_id):
                    return {"group_id": group_id, "agents": [{"agent_id": "agent-a"}]}

                def list_groups(self):
                    return [{"group_id": "resident-main", "status": "running", "agents": [{"agent_id": "agent-a"}]}]

            session = stop_live_agent_session(
                root,
                StopSupervisor(),
                meeting_id="resident-m1",
                group_id="resident-main",
            )

            self.assertEqual(session["status"], "stopping")
            self.assertIn("group:unknown", session["process"]["attention"])

    def test_stop_session_reports_process_attention_when_group_remains_running(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            council_config = _write_council_config(root, ["architect"])
            agent_config = _write_agent_config(root, ["agent-a"])
            start_live_agent_meeting(
                root,
                council_config_path=council_config,
                agent_config_path=agent_config,
                meeting_id="resident-m1",
            )
            heartbeat_live_agent(root, "agent-a", status="online")

            class StopSupervisor:
                def stop_group(self, group_id):
                    return {"group_id": group_id, "status": "running", "agents": [{"agent_id": "agent-a"}]}

                def list_groups(self):
                    return [{"group_id": "resident-main", "status": "running", "agents": [{"agent_id": "agent-a"}]}]

            session = stop_live_agent_session(
                root,
                StopSupervisor(),
                meeting_id="resident-m1",
                group_id="resident-main",
            )

            self.assertEqual(session["status"], "stopping")
            self.assertIn("group:running", session["process"]["attention"])

    def test_stop_session_does_not_mark_agents_offline_when_stop_group_fails(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            council_config = _write_council_config(root, ["architect"])
            agent_config = _write_agent_config(root, ["agent-a"])
            start_live_agent_meeting(
                root,
                council_config_path=council_config,
                agent_config_path=agent_config,
                meeting_id="resident-m1",
            )
            heartbeat_live_agent(root, "agent-a", status="online")

            class StopSupervisor:
                def __init__(self) -> None:
                    self.stopped = []

                def list_groups(self):
                    return [{"group_id": "resident-main", "status": "running", "agents": [{"agent_id": "agent-a"}]}]

                def stop_group(self, group_id):
                    self.stopped.append(group_id)
                    raise ValueError("stop failed: /private/live-agents.json")

            supervisor = StopSupervisor()

            with self.assertRaises(ValueError):
                stop_live_agent_session(root, supervisor, meeting_id="resident-m1", group_id="resident-main")

            self.assertEqual(supervisor.stopped, ["resident-main"])
            agents = {agent["agent_id"]: agent for agent in read_live_agents(root)}
            self.assertEqual(agents["agent-a"]["status"], "online")

    def test_restart_session_stops_running_group_and_waits_for_fresh_presence(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            council_config = _write_council_config(root, ["architect", "critic"])
            agent_config = _write_agent_config(root, ["agent-a", "agent-b"])
            start_live_agent_meeting(
                root,
                council_config_path=council_config,
                agent_config_path=agent_config,
                meeting_id="resident-m1",
            )
            heartbeat_live_agent(root, "agent-a", status="online")
            heartbeat_live_agent(root, "agent-b", status="working")

            class RestartSupervisor:
                def __init__(self) -> None:
                    self.stopped = []
                    self.restarted = []

                def snapshot_groups(self):
                    return [
                        {
                            "group_id": "resident-main",
                            "status": "running",
                            "agents": [{"agent_id": "agent-a"}, {"agent_id": "agent-b"}],
                        }
                    ]

                def stop_group(self, group_id):
                    self.stopped.append(group_id)
                    return {"group_id": group_id, "status": "stopped", "agents": [{"agent_id": "agent-a"}, {"agent_id": "agent-b"}]}

                def restart_group(self, group_id):
                    self.restarted.append(group_id)
                    agents = {agent["agent_id"]: agent for agent in read_live_agents(root)}
                    if agents["agent-a"]["status"] != "offline" or agents["agent-b"]["status"] != "offline":
                        raise AssertionError("restart must clear stale presence before starting the group again")
                    heartbeat_live_agent(root, "agent-a", status="online")
                    heartbeat_live_agent(root, "agent-b", status="working")
                    return {"group_id": group_id, "status": "running", "agents": [{"agent_id": "agent-a"}, {"agent_id": "agent-b"}]}

            supervisor = RestartSupervisor()

            session = restart_live_agent_session(
                root,
                supervisor,
                meeting_id="resident-m1",
                group_id="resident main",
                connect_timeout_seconds=0,
            )

            self.assertEqual(session["status"], "ready")
            self.assertEqual(session["meeting_id"], "resident-m1")
            self.assertEqual(session["group_id"], "resident-main")
            self.assertEqual(supervisor.stopped, ["resident-main"])
            self.assertEqual(supervisor.restarted, ["resident-main"])
            self.assertEqual(session["offline"]["offline"], 2)
            self.assertEqual(session["connection"]["connected"], 2)
            self.assertTrue((root / "meetings" / "resident-m1" / "live_state.json").exists())

    def test_restart_session_stops_restarting_group_before_clearing_presence(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            council_config = _write_council_config(root, ["architect"])
            agent_config = _write_agent_config(root, ["agent-a"])
            start_live_agent_meeting(
                root,
                council_config_path=council_config,
                agent_config_path=agent_config,
                meeting_id="resident-m1",
            )
            heartbeat_live_agent(root, "agent-a", status="online")

            class RestartSupervisor:
                def __init__(self) -> None:
                    self.stopped = []
                    self.restarted = []

                def snapshot_groups(self):
                    return [{"group_id": "resident-main", "status": "restarting", "agents": [{"agent_id": "agent-a"}]}]

                def stop_group(self, group_id):
                    self.stopped.append(group_id)
                    agents = {agent["agent_id"]: agent for agent in read_live_agents(root)}
                    if agents["agent-a"]["status"] != "online":
                        raise AssertionError("pending restart must be stopped before stale presence is cleared")
                    return {"group_id": group_id, "status": "stopped", "agents": [{"agent_id": "agent-a"}]}

                def restart_group(self, group_id):
                    self.restarted.append(group_id)
                    agents = {agent["agent_id"]: agent for agent in read_live_agents(root)}
                    if agents["agent-a"]["status"] != "offline":
                        raise AssertionError("presence should be cleared before starting the group again")
                    heartbeat_live_agent(root, "agent-a", status="online")
                    return {"group_id": group_id, "status": "running", "agents": [{"agent_id": "agent-a"}]}

            supervisor = RestartSupervisor()

            session = restart_live_agent_session(
                root,
                supervisor,
                meeting_id="resident-m1",
                group_id="resident-main",
                connect_timeout_seconds=0,
            )

            self.assertEqual(session["status"], "ready")
            self.assertEqual(supervisor.stopped, ["resident-main"])
            self.assertEqual(supervisor.restarted, ["resident-main"])

    def test_restart_session_revalidates_restarted_manifest_before_ready(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            council_config = _write_council_config(root, ["architect"])
            agent_config = _write_agent_config(root, ["agent-a"])
            start_live_agent_meeting(
                root,
                council_config_path=council_config,
                agent_config_path=agent_config,
                meeting_id="resident-m1",
            )
            heartbeat_live_agent(root, "agent-a", status="online")

            class RestartSupervisor:
                def snapshot_groups(self):
                    return [{"group_id": "resident-main", "status": "stopped", "agents": [{"agent_id": "agent-a"}]}]

                def restart_group(self, group_id):
                    heartbeat_live_agent(root, "agent-a", status="online")
                    return {
                        "group_id": group_id,
                        "status": "running",
                        "agents": [
                            {"agent_id": "agent-a"},
                            {"agent_id": "agent-a"},
                            {"agent_id": "agent-extra"},
                        ],
                    }

            session = restart_live_agent_session(
                root,
                RestartSupervisor(),
                meeting_id="resident-m1",
                group_id="resident-main",
                connect_timeout_seconds=0,
            )

            self.assertEqual(session["status"], "starting")
            self.assertFalse(session["process"]["ready"])
            self.assertIn("agent-a:duplicate_in_group", session["process"]["attention"])
            self.assertIn("agent-extra:extra_in_group", session["process"]["attention"])
            self.assertEqual(session["connection"]["connected"], 1)

    def test_restart_session_uses_read_only_process_snapshot_before_mutation(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            council_config = _write_council_config(root, ["architect"])
            agent_config = _write_agent_config(root, ["agent-a"])
            start_live_agent_meeting(
                root,
                council_config_path=council_config,
                agent_config_path=agent_config,
                meeting_id="resident-m1",
            )
            heartbeat_live_agent(root, "agent-a", status="online")

            class RestartSupervisor:
                def __init__(self) -> None:
                    self.snapshot_calls = 0

                def snapshot_groups(self):
                    self.snapshot_calls += 1
                    return [{"group_id": "resident-main", "status": "stopped", "agents": [{"agent_id": "agent-a"}]}]

                def list_groups(self):
                    raise AssertionError("restart prevalidation must not use mutating list_groups when snapshot_groups is available")

                def restart_group(self, group_id):
                    heartbeat_live_agent(root, "agent-a", status="online")
                    return {"group_id": group_id, "status": "running", "agents": [{"agent_id": "agent-a"}]}

            supervisor = RestartSupervisor()

            session = restart_live_agent_session(
                root,
                supervisor,
                meeting_id="resident-m1",
                group_id="resident-main",
                connect_timeout_seconds=0,
            )

            self.assertEqual(session["status"], "ready")
            self.assertEqual(supervisor.snapshot_calls, 1)

    def test_restart_session_reports_starting_without_fresh_presence(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            council_config = _write_council_config(root, ["architect"])
            agent_config = _write_agent_config(root, ["agent-a"])
            start_live_agent_meeting(
                root,
                council_config_path=council_config,
                agent_config_path=agent_config,
                meeting_id="resident-m1",
            )
            heartbeat_live_agent(root, "agent-a", status="online")

            class RestartSupervisor:
                def snapshot_groups(self):
                    return [{"group_id": "resident-main", "status": "stopped", "agents": [{"agent_id": "agent-a"}]}]

                def stop_group(self, group_id):
                    raise AssertionError("stopped group should not be stopped before restart")

                def restart_group(self, group_id):
                    return {"group_id": group_id, "status": "running", "agents": [{"agent_id": "agent-a"}]}

            session = restart_live_agent_session(
                root,
                RestartSupervisor(),
                meeting_id="resident-m1",
                group_id="resident-main",
                connect_timeout_seconds=0,
            )

            self.assertEqual(session["status"], "starting")
            self.assertEqual(session["offline"]["offline"], 1)
            self.assertEqual(session["connection"]["attention"], ["agent-a:offline"])
            agents = {agent["agent_id"]: agent for agent in read_live_agents(root)}
            self.assertEqual(agents["agent-a"]["status"], "offline")

    def test_restart_session_refuses_manifest_mismatch_before_restart(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            council_config = _write_council_config(root, ["architect", "critic"])
            agent_config = _write_agent_config(root, ["agent-a", "agent-b"])
            start_live_agent_meeting(
                root,
                council_config_path=council_config,
                agent_config_path=agent_config,
                meeting_id="resident-m1",
            )
            heartbeat_live_agent(root, "agent-a", status="online")

            class RestartSupervisor:
                def __init__(self) -> None:
                    self.stopped = []
                    self.restarted = []

                def snapshot_groups(self):
                    return [
                        {
                            "group_id": "resident-main",
                            "status": "running",
                            "agents": [{"agent_id": "agent-a"}, {"agent_id": "agent-x"}],
                        }
                    ]

                def stop_group(self, group_id):
                    self.stopped.append(group_id)
                    return {"group_id": group_id, "status": "stopped", "agents": [{"agent_id": "agent-a"}]}

                def restart_group(self, group_id):
                    self.restarted.append(group_id)
                    return {"group_id": group_id, "status": "running", "agents": [{"agent_id": "agent-a"}]}

            supervisor = RestartSupervisor()

            with self.assertRaises(ValueError) as raised:
                restart_live_agent_session(root, supervisor, meeting_id="resident-m1", group_id="resident-main")

            self.assertIn("does not match meeting agents", str(raised.exception))
            self.assertEqual(supervisor.stopped, [])
            self.assertEqual(supervisor.restarted, [])
            agents = {agent["agent_id"]: agent for agent in read_live_agents(root)}
            self.assertEqual(agents["agent-a"]["status"], "online")

    def test_restart_session_refuses_duplicate_manifest_agent_before_restart(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            council_config = _write_council_config(root, ["architect"])
            agent_config = _write_agent_config(root, ["agent-a"])
            start_live_agent_meeting(
                root,
                council_config_path=council_config,
                agent_config_path=agent_config,
                meeting_id="resident-m1",
            )
            heartbeat_live_agent(root, "agent-a", status="online")

            class RestartSupervisor:
                def __init__(self) -> None:
                    self.restarted = []

                def snapshot_groups(self):
                    return [
                        {
                            "group_id": "resident-main",
                            "status": "stopped",
                            "agents": [{"agent_id": "agent-a"}, {"agent_id": "agent-a"}],
                        }
                    ]

                def restart_group(self, group_id):
                    self.restarted.append(group_id)
                    return {"group_id": group_id, "status": "running", "agents": [{"agent_id": "agent-a"}]}

            supervisor = RestartSupervisor()

            with self.assertRaises(ValueError) as raised:
                restart_live_agent_session(root, supervisor, meeting_id="resident-m1", group_id="resident-main")

            self.assertIn("duplicate agent-a", str(raised.exception))
            self.assertEqual(supervisor.restarted, [])
            agents = {agent["agent_id"]: agent for agent in read_live_agents(root)}
            self.assertEqual(agents["agent-a"]["status"], "online")

    def test_restart_session_leaves_wrong_meeting_roster_row_untouched(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            council_config = _write_council_config(root, ["architect"])
            agent_config = _write_agent_config(root, ["agent-a"])
            start_live_agent_meeting(
                root,
                council_config_path=council_config,
                agent_config_path=agent_config,
                meeting_id="resident-m1",
            )
            connect_live_agent(
                root,
                {
                    "agent_id": "agent-a",
                    "display_name": "Agent A",
                    "provider_kind": "local_cli",
                    "connection_kind": "local_cli",
                    "meeting_id": "other-meeting",
                    "engagement_mode": "moderator_called",
                    "status": "online",
                    "capabilities": ["room_chat", "official_turn"],
                },
            )

            class RestartSupervisor:
                def snapshot_groups(self):
                    return [{"group_id": "resident-main", "status": "stopped", "agents": [{"agent_id": "agent-a"}]}]

                def restart_group(self, group_id):
                    return {"group_id": group_id, "status": "running", "agents": [{"agent_id": "agent-a"}]}

            session = restart_live_agent_session(
                root,
                RestartSupervisor(),
                meeting_id="resident-m1",
                group_id="resident-main",
                connect_timeout_seconds=0,
            )

            self.assertEqual(session["status"], "starting")
            self.assertEqual(session["offline"]["attention"], ["agent-a:wrong_meeting"])
            self.assertEqual(session["connection"]["attention"], ["agent-a:wrong_meeting"])
            agents = {agent["agent_id"]: agent for agent in read_live_agents(root)}
            self.assertEqual(agents["agent-a"]["meeting_id"], "other-meeting")
            self.assertEqual(agents["agent-a"]["status"], "online")

    def test_check_session_reports_ready_without_mutating_runtime_state(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            council_config = _write_council_config(root, ["architect", "critic"])
            agent_config = _write_agent_config(root, ["agent-a", "agent-b"])
            start_live_agent_meeting(
                root,
                council_config_path=council_config,
                agent_config_path=agent_config,
                meeting_id="resident-m1",
            )
            heartbeat_live_agent(root, "agent-a", status="online")
            heartbeat_live_agent(root, "agent-b", status="working")
            before_roster = (root / "live_agents.json").read_text(encoding="utf-8")

            class CheckSupervisor:
                def __init__(self) -> None:
                    self.started = []
                    self.stopped = []

                def snapshot_groups(self):
                    return [
                        {
                            "group_id": "resident-main",
                            "status": "running",
                            "config_path": str(root / "secret-live-agents.json"),
                            "log_tail": "secret provider output",
                            "agents": [{"agent_id": "agent-a"}, {"agent_id": "agent-b"}],
                        }
                    ]

                def start_group(self, **kwargs):
                    self.started.append(kwargs)
                    raise AssertionError("check must not start groups")

                def stop_group(self, group_id):
                    self.stopped.append(group_id)
                    raise AssertionError("check must not stop groups")

            supervisor = CheckSupervisor()

            session = check_live_agent_session(
                root,
                supervisor,
                meeting_id="resident-m1",
                group_id="resident main",
            )

            self.assertEqual(session["status"], "ready")
            self.assertEqual(session["meeting_id"], "resident-m1")
            self.assertEqual(session["group_id"], "resident-main")
            self.assertEqual(session["connection"]["connected"], 2)
            self.assertEqual(session["process"]["status"], "running")
            self.assertEqual(session["process"]["attention"], [])
            self.assertEqual(supervisor.started, [])
            self.assertEqual(supervisor.stopped, [])
            self.assertEqual((root / "live_agents.json").read_text(encoding="utf-8"), before_roster)
            serialized = json.dumps(session, ensure_ascii=False)
            self.assertNotIn("secret-live-agents", serialized)
            self.assertNotIn("secret provider output", serialized)

    def test_check_session_uses_read_only_process_snapshot_when_available(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            council_config = _write_council_config(root, ["architect"])
            agent_config = _write_agent_config(root, ["agent-a"])
            start_live_agent_meeting(
                root,
                council_config_path=council_config,
                agent_config_path=agent_config,
                meeting_id="resident-m1",
            )
            heartbeat_live_agent(root, "agent-a", status="online")

            class CheckSupervisor:
                def __init__(self) -> None:
                    self.snapshot_calls = 0

                def snapshot_groups(self):
                    self.snapshot_calls += 1
                    return [{"group_id": "resident-main", "status": "running", "agents": [{"agent_id": "agent-a"}]}]

                def list_groups(self):
                    raise AssertionError("check must not use mutating list_groups when snapshot_groups is available")

            supervisor = CheckSupervisor()

            session = check_live_agent_session(
                root,
                supervisor,
                meeting_id="resident-m1",
                group_id="resident-main",
            )

            self.assertEqual(session["status"], "ready")
            self.assertEqual(supervisor.snapshot_calls, 1)

    def test_check_session_reports_degraded_mismatch_without_reassigning_wrong_meeting_roster(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            council_config = _write_council_config(root, ["architect"])
            agent_config = _write_agent_config(root, ["agent-a"])
            start_live_agent_meeting(
                root,
                council_config_path=council_config,
                agent_config_path=agent_config,
                meeting_id="resident-m1",
            )
            connect_live_agent(
                root,
                {
                    "agent_id": "agent-a",
                    "display_name": "Agent A",
                    "provider_kind": "local_cli",
                    "connection_kind": "local_cli",
                    "meeting_id": "other-meeting",
                    "engagement_mode": "moderator_called",
                    "status": "online",
                    "capabilities": ["room_chat", "official_turn"],
                },
            )
            before_roster = (root / "live_agents.json").read_text(encoding="utf-8")

            class CheckSupervisor:
                def snapshot_groups(self):
                    return [
                        {
                            "group_id": "resident-main",
                            "status": "running",
                            "agents": [{"agent_id": "agent-a"}, {"agent_id": "agent-x"}],
                        }
                    ]

            session = check_live_agent_session(
                root,
                CheckSupervisor(),
                meeting_id="resident-m1",
                group_id="resident-main",
            )

            self.assertEqual(session["status"], "degraded")
            self.assertEqual(session["connection"]["attention"], ["agent-a:wrong_meeting"])
            self.assertIn("agent-x:extra_in_group", session["process"]["attention"])
            self.assertEqual((root / "live_agents.json").read_text(encoding="utf-8"), before_roster)
            agents = {agent["agent_id"]: agent for agent in read_live_agents(root)}
            self.assertEqual(agents["agent-a"]["meeting_id"], "other-meeting")
            self.assertEqual(agents["agent-a"]["status"], "online")

    def test_check_session_reports_degraded_duplicate_manifest_agent(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            council_config = _write_council_config(root, ["architect"])
            agent_config = _write_agent_config(root, ["agent-a"])
            start_live_agent_meeting(
                root,
                council_config_path=council_config,
                agent_config_path=agent_config,
                meeting_id="resident-m1",
            )
            heartbeat_live_agent(root, "agent-a", status="online")

            class CheckSupervisor:
                def snapshot_groups(self):
                    return [
                        {
                            "group_id": "resident-main",
                            "status": "running",
                            "agents": [{"agent_id": "agent-a"}, {"agent_id": "agent-a"}],
                        }
                    ]

            session = check_live_agent_session(
                root,
                CheckSupervisor(),
                meeting_id="resident-m1",
                group_id="resident-main",
            )

            self.assertEqual(session["status"], "degraded")
            self.assertIn("agent-a:duplicate_in_group", session["process"]["attention"])

    def test_check_session_requires_existing_meeting_and_explicit_group_id(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            council_config = _write_council_config(root, ["architect"])
            agent_config = _write_agent_config(root, ["agent-a"])
            start_live_agent_meeting(
                root,
                council_config_path=council_config,
                agent_config_path=agent_config,
                meeting_id="resident-m1",
            )

            class CheckSupervisor:
                def list_groups(self):
                    return []

            with self.assertRaises(ValueError) as raised:
                check_live_agent_session(root, CheckSupervisor(), meeting_id="resident-m1", group_id=" ")
            self.assertIn("Live agent group id is required", str(raised.exception))

            with self.assertRaises(ValueError) as raised:
                check_live_agent_session(root, CheckSupervisor(), meeting_id="missing-meeting", group_id="resident-main")
            self.assertIn("Meeting missing-meeting was not found", str(raised.exception))


def _write_council_config(root: Path, role_ids: list[str]) -> Path:
    path = root / "council.json"
    path.write_text(
        json.dumps(
            {
                "topic": "resident session",
                "question": "Can resident agents start as one session?",
                "roles": [
                    {
                        "id": role_id,
                        "display_name": role_id.title(),
                        "lens": f"{role_id} lens",
                        "research_focus": f"{role_id} focus",
                    }
                    for role_id in role_ids
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return path


def _write_agent_config(root: Path, agent_ids: list[str], *, provider_kind: str = "local_cli") -> Path:
    path = root / "agents.json"
    roles = ["architect", "critic", "tester", "operator"]
    path.write_text(
        json.dumps(
            {
                "providers": [
                    {
                        "id": "local-cli",
                        "kind": provider_kind,
                        "display_name": "Local CLI",
                        "endpoint": "http://bridge.local" if provider_kind == "remote_http_bridge" else None,
                        "command": [sys.executable, "-c", "print('ok')"],
                    }
                ],
                "permission_profiles": [
                    {
                        "id": "meeting_readonly",
                        "meeting_read": True,
                        "lobby_chat": True,
                        "official_turn": True,
                    }
                ],
                "agent_bindings": [
                    {
                        "agent_id": agent_id,
                        "role_id": role_id,
                        "provider_id": "local-cli",
                        "permission_profile_id": "meeting_readonly",
                        "engagement_mode": "always",
                    }
                    for agent_id, role_id in zip(agent_ids, roles, strict=False)
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return path


def _write_live_agent_config(
    root: Path,
    agent_ids: list[str],
    *,
    meeting_id: str = "",
    provider_kind: str = "local_cli",
    connection_kind: str = "local_cli",
    endpoint: str = "",
    auth_ref: str = "",
) -> Path:
    path = root / "live-agents.json"
    path.write_text(
        json.dumps(
            {
                "server": "http://127.0.0.1:8765",
                "agents": [
                    {
                        "agent_id": agent_id,
                        "display_name": agent_id,
                        "provider_kind": provider_kind,
                        "connection_kind": connection_kind,
                        "endpoint": endpoint,
                        "auth_ref": auth_ref,
                        "meeting_id": meeting_id,
                        "engagement_mode": "moderator_called",
                        "command": [sys.executable, "-c", "print('ok')"],
                    }
                    for agent_id in agent_ids
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return path
