import json
import sys
import tempfile
import unittest
from pathlib import Path

from agentsassemble.live_agent_sessions import start_live_agent_session


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


def _write_agent_config(root: Path, agent_ids: list[str]) -> Path:
    path = root / "agents.json"
    roles = ["architect", "critic", "tester", "operator"]
    path.write_text(
        json.dumps(
            {
                "providers": [
                    {
                        "id": "local-cli",
                        "kind": "local_cli",
                        "display_name": "Local CLI",
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


def _write_live_agent_config(root: Path, agent_ids: list[str], *, meeting_id: str = "") -> Path:
    path = root / "live-agents.json"
    path.write_text(
        json.dumps(
            {
                "server": "http://127.0.0.1:8765",
                "agents": [
                    {
                        "agent_id": agent_id,
                        "display_name": agent_id,
                        "provider_kind": "local_cli",
                        "connection_kind": "local_cli",
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
