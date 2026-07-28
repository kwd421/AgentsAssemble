import json
import os
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from agentsassemble.legacy.live_agent.state import connect_live_agent, heartbeat_live_agent, read_live_agents
from agentsassemble.legacy.live_agent.runtime.processes import (
    LiveAgentProcessSupervisor as _LiveAgentProcessSupervisor,
    read_live_agent_process_event_history,
    read_live_agent_process_events,
)


def _ok_preflight(path, *, server_override=None):
    return {"status": "ok", "checks": [], "agents": []}


def _read_lifecycle_events(root: Path):
    path = root / "live-agent-runs" / "events.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _supports_process_groups():
    return hasattr(os, "killpg") and hasattr(os, "setsid") and hasattr(os, "getpgid")


def _pid_exists(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _wait_for_pid_exit(pid: int, *, timeout_seconds: float = 5.0) -> bool:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        if not _pid_exists(pid):
            return True
        time.sleep(0.01)
    return not _pid_exists(pid)


def LiveAgentProcessSupervisor(output_root, **kwargs):
    kwargs.setdefault("preflight_checker", _ok_preflight)
    return _LiveAgentProcessSupervisor(output_root, **kwargs)


class FakeProcess:
    def __init__(self, pid=4321, wait_timeouts=0):
        self.pid = pid
        self.returncode = None
        self.signals = []
        self.terminated = False
        self.killed = False
        self.wait_timeouts = wait_timeouts

    def poll(self):
        return self.returncode

    def send_signal(self, signum):
        self.signals.append(signum)

    def wait(self, timeout=None):
        if self.wait_timeouts:
            self.wait_timeouts -= 1
            raise subprocess.TimeoutExpired("fake", timeout)
        self.returncode = 0
        return 0

    def terminate(self):
        self.terminated = True
        self.returncode = -15

    def kill(self):
        self.killed = True
        self.returncode = -9


class LiveAgentProcessSupervisorTests(unittest.TestCase):
    def test_start_group_launches_run_group_with_server_override_and_log_file(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = root / "live-agents.json"
            config_path.write_text('{"agents": [{"agent_id": "a", "command": ["fake"]}]}', encoding="utf-8")
            launched = []

            def command_factory(command, **kwargs):
                launched.append((command, kwargs))
                return FakeProcess()

            supervisor = LiveAgentProcessSupervisor(
                root,
                command_factory=command_factory,
                now_fn=lambda: datetime(2026, 5, 17, 12, 0, tzinfo=UTC),
            )

            record = supervisor.start_group(
                config_path=config_path,
                server="http://127.0.0.1:8765",
                group_id="main-room",
            )

            self.assertEqual(record["group_id"], "main-room")
            self.assertEqual(record["status"], "running")
            self.assertEqual(record["pid"], 4321)
            self.assertEqual(record["config_path"], str(config_path))
            self.assertEqual(record["server"], "http://127.0.0.1:8765")
            self.assertTrue(record["log_path"].endswith("live-agent-runs/main-room.log"))
            command, kwargs = launched[0]
            self.assertEqual(command[:3], [supervisor.python_executable, "-m", "agentsassemble.cli"])
            self.assertIn("run-group", command)
            self.assertIn("--server", command)
            self.assertIn("http://127.0.0.1:8765", command)
            self.assertIn("--agent-manifest", command)
            manifest_path = Path(command[command.index("--agent-manifest") + 1])
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(manifest["meeting_id"], "")
            self.assertEqual(manifest["agent_ids"], ["a"])
            self.assertIs(kwargs["stderr"], kwargs["stdout"])

    def test_start_group_persists_diagnostic_flag(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = root / "live-agents.json"
            config_path.write_text('{"agents": [{"agent_id": "a", "command": ["fake"]}]}', encoding="utf-8")
            supervisor = LiveAgentProcessSupervisor(root, command_factory=lambda command, **kwargs: FakeProcess())

            record = supervisor.start_group(
                config_path=config_path,
                server="http://room.local",
                group_id="doctor-smoke",
                diagnostic=True,
            )

            self.assertTrue(record["diagnostic"])
            persisted = json.loads((root / "live-agent-runs" / "processes.json").read_text(encoding="utf-8"))
            self.assertTrue(persisted["groups"][0]["diagnostic"])

    def test_start_group_uses_unique_launch_manifest_per_run(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = root / "live-agents.json"
            launched = []

            def command_factory(command, **kwargs):
                launched.append(command)
                return FakeProcess(pid=4000 + len(launched))

            supervisor = LiveAgentProcessSupervisor(root, command_factory=command_factory)
            config_path.write_text('{"agents": [{"agent_id": "agent-a", "command": ["fake"]}]}', encoding="utf-8")
            supervisor.start_group(
                config_path=config_path,
                server="http://room.local",
                group_id="crew",
                meeting_id="meeting-1",
            )
            first_manifest_path = Path(launched[0][launched[0].index("--agent-manifest") + 1])
            first_manifest_before_restart = json.loads(first_manifest_path.read_text(encoding="utf-8"))

            supervisor.stop_group("crew")
            config_path.write_text('{"agents": [{"agent_id": "agent-b", "command": ["fake"]}]}', encoding="utf-8")
            supervisor.start_group(
                config_path=config_path,
                server="http://room.local",
                group_id="crew",
                meeting_id="meeting-2",
            )
            second_manifest_path = Path(launched[1][launched[1].index("--agent-manifest") + 1])
            second_manifest = json.loads(second_manifest_path.read_text(encoding="utf-8"))
            first_manifest_after_restart = json.loads(first_manifest_path.read_text(encoding="utf-8"))

            self.assertNotEqual(first_manifest_path, second_manifest_path)
            self.assertEqual(first_manifest_before_restart["meeting_id"], "meeting-1")
            self.assertEqual(first_manifest_before_restart["agent_ids"], ["agent-a"])
            self.assertEqual(first_manifest_after_restart, first_manifest_before_restart)
            self.assertEqual(second_manifest["meeting_id"], "meeting-2")
            self.assertEqual(second_manifest["agent_ids"], ["agent-b"])

    def test_start_group_persists_meeting_identity_and_restart_preserves_it(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = root / "live-agents.json"
            config_path.write_text('{"agents": [{"agent_id": "a", "command": ["fake"]}]}', encoding="utf-8")
            processes = []

            def command_factory(command, **kwargs):
                process = FakeProcess(pid=4400 + len(processes))
                processes.append(process)
                return process

            supervisor = LiveAgentProcessSupervisor(root, command_factory=command_factory)

            started = supervisor.start_group(
                config_path=config_path,
                server="http://room.local",
                group_id="crew",
                meeting_id="resident-m1",
            )
            supervisor.stop_group("crew")
            reloaded = LiveAgentProcessSupervisor(root, command_factory=command_factory)
            restarted = reloaded.restart_group("crew")
            persisted = json.loads((root / "live-agent-runs" / "processes.json").read_text(encoding="utf-8"))

        self.assertEqual(started["meeting_id"], "resident-m1")
        self.assertEqual(restarted["meeting_id"], "resident-m1")
        self.assertEqual(persisted["groups"][0]["meeting_id"], "resident-m1")

    def test_recover_group_preserves_meeting_identity_from_persisted_record(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = root / "live-agents.json"
            config_path.write_text('{"agents": [{"agent_id": "a", "command": ["fake"]}]}', encoding="utf-8")
            processes = []

            def command_factory(command, **kwargs):
                process = FakeProcess(pid=4450 + len(processes))
                processes.append(process)
                return process

            supervisor = LiveAgentProcessSupervisor(root, command_factory=command_factory)
            supervisor.start_group(
                config_path=config_path,
                server="http://room.local",
                group_id="crew",
                meeting_id="resident-m1",
            )
            reloaded = LiveAgentProcessSupervisor(root, command_factory=command_factory)

            recovered = reloaded.recover_group("crew")
            persisted = json.loads((root / "live-agent-runs" / "processes.json").read_text(encoding="utf-8"))

        self.assertEqual(recovered["status"], "running")
        self.assertEqual(recovered["meeting_id"], "resident-m1")
        self.assertEqual(persisted["groups"][0]["meeting_id"], "resident-m1")

    def test_delayed_auto_restart_preserves_meeting_identity(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = root / "live-agents.json"
            config_path.write_text('{"agents": [{"agent_id": "a", "command": ["fake"]}]}', encoding="utf-8")
            current_time = {"value": datetime(2026, 5, 17, 12, 0, tzinfo=UTC)}
            processes = []

            def command_factory(command, **kwargs):
                process = FakeProcess(pid=4460 + len(processes))
                processes.append(process)
                return process

            supervisor = LiveAgentProcessSupervisor(
                root,
                command_factory=command_factory,
                now_fn=lambda: current_time["value"],
            )
            supervisor.start_group(
                config_path=config_path,
                server="http://room.local",
                group_id="crew",
                meeting_id="resident-m1",
                auto_restart=True,
                max_restarts=1,
                restart_backoff_seconds=10,
            )
            processes[0].returncode = 2

            scheduled = supervisor.list_groups()[0]
            current_time["value"] += timedelta(seconds=11)
            restarted = supervisor.list_groups()[0]

        self.assertEqual(scheduled["status"], "restarting")
        self.assertEqual(scheduled["meeting_id"], "resident-m1")
        self.assertEqual(restarted["status"], "running")
        self.assertEqual(restarted["meeting_id"], "resident-m1")

    def test_start_group_persists_safe_agent_manifest_from_config(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = root / "live-agents.json"
            config_path.write_text(
                json.dumps(
                    {
                        "agents": [
                            {
                                "agent_id": "local-a",
                                "display_name": "Local A",
                                "provider_kind": "local_cli",
                                "connection_kind": "local_cli",
                                "command": ["fake"],
                            },
                            {
                                "agent_id": "friend-b",
                                "display_name": "Friend B",
                                "provider_kind": "claude_code",
                                "connection_kind": "remote_bridge",
                                "endpoint": "https://friend.local:8777",
                                "auth_ref": "env:SECRET_TOKEN",
                            },
                        ]
                    }
                ),
                encoding="utf-8",
            )

            def preflight_checker(path, *, server_override=None):
                return {
                    "status": "ok",
                    "checks": [],
                    "agents": [
                        {
                            "agent_id": "local-a",
                            "display_name": "Preflight Local A",
                            "provider_kind": "local_cli",
                            "connection_kind": "local_cli",
                            "command": ["fake", "--token", "secret-value"],
                            "command_path": "/usr/local/bin/fake",
                            "endpoint": "http://must-not-persist.local",
                            "auth_ref": "env:SECRET_TOKEN",
                        },
                        {
                            "agent_id": "friend-b",
                            "display_name": "Preflight Friend B",
                            "provider_kind": "preflight_provider",
                            "connection_kind": "remote_bridge",
                            "endpoint": "https://friend.local:8777",
                            "auth_ref": "env:SECRET_TOKEN",
                        },
                        {
                            "agent_id": "preflight-only",
                            "display_name": "Preflight Only",
                            "provider_kind": "local_cli",
                            "connection_kind": "local_cli",
                            "command": ["fake"],
                        },
                    ],
                }

            supervisor = LiveAgentProcessSupervisor(
                root,
                command_factory=lambda command, **kwargs: FakeProcess(),
                preflight_checker=preflight_checker,
            )

            record = supervisor.start_group(config_path=config_path, server="http://room.local", group_id="crew")
            persisted = json.loads((root / "live-agent-runs" / "processes.json").read_text(encoding="utf-8"))
            persisted_text = json.dumps(persisted, ensure_ascii=False)

        expected_agents = [
            {
                "agent_id": "local-a",
                "display_name": "Local A",
                "provider_kind": "local_cli",
                "connection_kind": "local_cli",
            },
            {
                "agent_id": "friend-b",
                "display_name": "Friend B",
                "provider_kind": "claude_code",
                "connection_kind": "remote_bridge",
            },
        ]
        self.assertEqual(record["agents"], expected_agents)
        self.assertEqual(persisted["groups"][0]["agents"], expected_agents)
        self.assertNotIn("secret-value", persisted_text)
        self.assertNotIn("SECRET_TOKEN", persisted_text)
        self.assertNotIn("friend.local", persisted_text)
        self.assertNotIn("command_path", persisted_text)
        self.assertNotIn("Preflight", persisted_text)
        self.assertNotIn("preflight-only", persisted_text)

    def test_start_group_preflights_config_before_launching_process(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = root / "live-agents.json"
            config_path.write_text('{"agents": [{"agent_id": "a", "command": ["fake"]}]}', encoding="utf-8")
            preflights = []
            launched = []

            def preflight_checker(path, *, server_override=None):
                preflights.append((path, server_override, len(launched)))
                return {"status": "ok", "checks": [], "agents": []}

            def command_factory(command, **kwargs):
                launched.append(command)
                return FakeProcess()

            supervisor = LiveAgentProcessSupervisor(
                root,
                command_factory=command_factory,
                preflight_checker=preflight_checker,
            )

            record = supervisor.start_group(config_path=config_path, server="http://room.local", group_id="crew")

        self.assertEqual(record["status"], "running")
        self.assertEqual(preflights, [(config_path, "http://room.local", 0)])
        self.assertEqual(len(launched), 1)

    def test_start_group_refuses_failed_preflight_without_launching_process_or_log_record(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = root / "live-agents.json"
            config_path.write_text('{"agents": [{"agent_id": "bad-agent", "command": ["missing"]}]}', encoding="utf-8")

            def preflight_checker(path, *, server_override=None):
                return {
                    "status": "failed",
                    "checks": [],
                    "agents": [
                        {
                            "agent_id": "bad-agent",
                            "checks": [
                                {
                                    "id": "command",
                                    "status": "failed",
                                    "message": "Command not found: missing",
                                }
                            ],
                        }
                    ],
                }

            def command_factory(command, **kwargs):
                raise AssertionError("preflight failure must not launch a process")

            supervisor = LiveAgentProcessSupervisor(
                root,
                command_factory=command_factory,
                preflight_checker=preflight_checker,
            )

            with self.assertRaisesRegex(ValueError, "Live agent preflight failed: bad-agent command: Command not found"):
                supervisor.start_group(config_path=config_path, server="http://room.local", group_id="crew")

            self.assertFalse((root / "live-agent-runs" / "crew.log").exists())
            self.assertFalse((root / "live-agent-runs" / "processes.json").exists())

    def test_restart_group_refuses_failed_preflight_without_launching_process(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            runs_dir = root / "live-agent-runs"
            runs_dir.mkdir()
            config_path = root / "live-agents.json"
            config_path.write_text('{"agents": [{"agent_id": "bad-agent", "command": ["missing"]}]}', encoding="utf-8")
            (runs_dir / "processes.json").write_text(
                json.dumps(
                    {
                        "groups": [
                            {
                                "group_id": "crew",
                                "status": "stopped",
                                "pid": None,
                                "config_path": str(config_path),
                                "server": "http://room.local",
                                "log_path": str(runs_dir / "crew.log"),
                                "started_at": "2026-05-17T12:00:00+00:00",
                                "stopped_at": "2026-05-17T12:01:00+00:00",
                                "returncode": 0,
                                "last_error": "",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            launched = []

            def preflight_checker(path, *, server_override=None):
                return {
                    "status": "failed",
                    "checks": [],
                    "agents": [
                        {
                            "agent_id": "bad-agent",
                            "checks": [
                                {
                                    "id": "command",
                                    "status": "failed",
                                    "message": "Command not found: missing",
                                }
                            ],
                        }
                    ],
                }

            def command_factory(command, **kwargs):
                launched.append(command)
                return FakeProcess()

            supervisor = LiveAgentProcessSupervisor(
                root,
                command_factory=command_factory,
                preflight_checker=preflight_checker,
            )

            with self.assertRaisesRegex(ValueError, "bad-agent command"):
                supervisor.restart_group("crew")

        self.assertEqual(launched, [])

    def test_auto_restart_failed_preflight_marks_error_without_relaunching_process(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = root / "live-agents.json"
            config_path.write_text('{"agents": [{"agent_id": "a", "command": ["fake"]}]}', encoding="utf-8")
            reports = {
                "current": {"status": "ok", "checks": [], "agents": []},
            }
            processes = []

            def preflight_checker(path, *, server_override=None):
                return reports["current"]

            def command_factory(command, **kwargs):
                process = FakeProcess(pid=6000 + len(processes))
                processes.append(process)
                return process

            supervisor = LiveAgentProcessSupervisor(
                root,
                command_factory=command_factory,
                preflight_checker=preflight_checker,
                now_fn=lambda: datetime(2026, 5, 17, 12, 0, tzinfo=UTC),
            )
            supervisor.start_group(
                config_path=config_path,
                server="http://room.local",
                group_id="crew",
                auto_restart=True,
                max_restarts=1,
                restart_backoff_seconds=0,
            )
            reports["current"] = {
                "status": "failed",
                "checks": [],
                "agents": [
                    {
                        "agent_id": "bad-agent",
                        "checks": [
                            {
                                "id": "command",
                                "status": "failed",
                                "message": "Command not found: missing",
                            }
                        ],
                    }
                ],
            }
            processes[0].returncode = 2

            groups = supervisor.list_groups()

        self.assertEqual(len(processes), 1)
        self.assertEqual(groups[0]["status"], "error")
        self.assertEqual(groups[0]["restart_count"], 1)
        self.assertIn("Live agent preflight failed", groups[0]["last_error"])
        self.assertIn("bad-agent command", groups[0]["last_error"])

    def test_auto_restart_failed_preflight_redacts_sensitive_last_error(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = root / "live-agents.json"
            config_path.write_text('{"agents": [{"agent_id": "a", "command": ["fake"]}]}', encoding="utf-8")
            reports = {"current": {"status": "ok", "checks": [], "agents": []}}
            processes = []

            def preflight_checker(path, *, server_override=None):
                return reports["current"]

            def command_factory(command, **kwargs):
                process = FakeProcess(pid=6200 + len(processes))
                processes.append(process)
                return process

            supervisor = LiveAgentProcessSupervisor(
                root,
                command_factory=command_factory,
                preflight_checker=preflight_checker,
                now_fn=lambda: datetime(2026, 5, 17, 12, 0, tzinfo=UTC),
            )
            supervisor.start_group(
                config_path=config_path,
                server="http://room.local",
                group_id="crew",
                auto_restart=True,
                max_restarts=1,
                restart_backoff_seconds=0,
            )
            reports["current"] = {
                "status": "failed",
                "checks": [
                    {
                        "id": "config",
                        "status": "failed",
                        "message": "failed /private/live-agents.json --token secret-token http://room.local",
                    }
                ],
                "agents": [],
            }
            processes[0].returncode = 2

            groups = supervisor.list_groups()
            persisted = json.loads((root / "live-agent-runs" / "processes.json").read_text(encoding="utf-8"))
            lifecycle_text = (root / "live-agent-runs" / "events.jsonl").read_text(encoding="utf-8")

        self.assertEqual(groups[0]["status"], "error")
        self.assertIn("Restart failed: process error details redacted.", groups[0]["last_error"])
        self.assertNotIn("secret-token", groups[0]["last_error"])
        self.assertNotIn("live-agents.json", groups[0]["last_error"])
        self.assertNotIn("secret-token", persisted["groups"][0]["last_error"])
        self.assertNotIn("live-agents.json", persisted["groups"][0]["last_error"])
        self.assertNotIn("secret-token", lifecycle_text)
        self.assertNotIn("live-agents.json", lifecycle_text)

    def test_delayed_auto_restart_failed_preflight_redacts_sensitive_last_error(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = root / "live-agents.json"
            config_path.write_text('{"agents": [{"agent_id": "a", "command": ["fake"]}]}', encoding="utf-8")
            current_time = {"value": datetime(2026, 5, 17, 12, 0, tzinfo=UTC)}
            reports = {"current": {"status": "ok", "checks": [], "agents": []}}
            processes = []

            def preflight_checker(path, *, server_override=None):
                return reports["current"]

            def command_factory(command, **kwargs):
                process = FakeProcess(pid=6300 + len(processes))
                processes.append(process)
                return process

            supervisor = LiveAgentProcessSupervisor(
                root,
                command_factory=command_factory,
                preflight_checker=preflight_checker,
                now_fn=lambda: current_time["value"],
            )
            supervisor.start_group(
                config_path=config_path,
                server="http://room.local",
                group_id="crew",
                auto_restart=True,
                max_restarts=1,
                restart_backoff_seconds=10,
            )
            processes[0].returncode = 2
            supervisor.list_groups()
            reports["current"] = {
                "status": "failed",
                "checks": [
                    {
                        "id": "config",
                        "status": "failed",
                        "message": "failed /private/live-agents.json --token secret-token http://room.local",
                    }
                ],
                "agents": [],
            }
            current_time["value"] += timedelta(seconds=11)

            groups = supervisor.list_groups()
            persisted = json.loads((root / "live-agent-runs" / "processes.json").read_text(encoding="utf-8"))

        self.assertEqual(groups[0]["status"], "error")
        self.assertIn("Restart failed: process error details redacted.", groups[0]["last_error"])
        self.assertNotIn("secret-token", groups[0]["last_error"])
        self.assertNotIn("live-agents.json", groups[0]["last_error"])
        self.assertNotIn("secret-token", persisted["groups"][0]["last_error"])
        self.assertNotIn("live-agents.json", persisted["groups"][0]["last_error"])

    def test_auto_restart_launch_exception_redacts_sensitive_last_error(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = root / "live-agents.json"
            config_path.write_text('{"agents": [{"agent_id": "a", "command": ["fake"]}]}', encoding="utf-8")
            launches = 0
            processes = []

            def command_factory(command, **kwargs):
                nonlocal launches
                launches += 1
                if launches == 1:
                    process = FakeProcess(pid=6400)
                    processes.append(process)
                    return process
                raise RuntimeError("launch failed /private/live-agents.json --token secret-token http://room.local")

            supervisor = LiveAgentProcessSupervisor(
                root,
                command_factory=command_factory,
                now_fn=lambda: datetime(2026, 5, 17, 12, 0, tzinfo=UTC),
            )
            supervisor.start_group(
                config_path=config_path,
                server="http://room.local",
                group_id="crew",
                auto_restart=True,
                max_restarts=1,
                restart_backoff_seconds=0,
            )
            processes[0].returncode = 2

            groups = supervisor.list_groups()
            persisted = json.loads((root / "live-agent-runs" / "processes.json").read_text(encoding="utf-8"))

        self.assertEqual(groups[0]["status"], "error")
        self.assertIn("Restart failed: process error details redacted.", groups[0]["last_error"])
        self.assertNotIn("secret-token", groups[0]["last_error"])
        self.assertNotIn("live-agents.json", groups[0]["last_error"])
        self.assertNotIn("secret-token", persisted["groups"][0]["last_error"])
        self.assertNotIn("live-agents.json", persisted["groups"][0]["last_error"])

    def test_list_groups_redacts_legacy_sensitive_last_error_without_persisting_output_field(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            state_dir = root / "live-agent-runs"
            state_dir.mkdir(parents=True)
            state_path = state_dir / "processes.json"
            state_path.write_text(
                json.dumps(
                    {
                        "groups": [
                            {
                                "group_id": "crew",
                                "status": "error",
                                "pid": None,
                                "meeting_id": "",
                                "config_path": "",
                                "server": "",
                                "log_path": "",
                                "started_at": "",
                                "stopped_at": "",
                                "returncode": 2,
                                "last_error": "failed /private/live-agents.json token=secret-token",
                                "auto_restart": False,
                                "restart_count": 0,
                                "max_restarts": 0,
                                "restart_backoff_seconds": 5,
                                "stale_restart_after_seconds": 0,
                                "next_restart_at": "",
                                "diagnostic": False,
                                "agents": [],
                                "recovered_from_status": "",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )

            groups = LiveAgentProcessSupervisor(root).list_groups()
            persisted = json.loads(state_path.read_text(encoding="utf-8"))

        self.assertEqual(groups[0]["last_error"], "process error details redacted.")
        self.assertNotIn("secret-token", groups[0]["last_error"])
        self.assertNotIn("live-agents.json", groups[0]["last_error"])
        self.assertIn("secret-token", persisted["groups"][0]["last_error"])

    def test_delayed_auto_restart_failed_preflight_marks_error_without_relaunching_process(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = root / "live-agents.json"
            config_path.write_text('{"agents": [{"agent_id": "a", "command": ["fake"]}]}', encoding="utf-8")
            current_time = {"value": datetime(2026, 5, 17, 12, 0, tzinfo=UTC)}
            reports = {
                "current": {"status": "ok", "checks": [], "agents": []},
            }
            processes = []

            def preflight_checker(path, *, server_override=None):
                return reports["current"]

            def command_factory(command, **kwargs):
                process = FakeProcess(pid=6100 + len(processes))
                processes.append(process)
                return process

            supervisor = LiveAgentProcessSupervisor(
                root,
                command_factory=command_factory,
                preflight_checker=preflight_checker,
                now_fn=lambda: current_time["value"],
            )
            supervisor.start_group(
                config_path=config_path,
                server="http://room.local",
                group_id="crew",
                auto_restart=True,
                max_restarts=1,
                restart_backoff_seconds=10,
            )
            connect_live_agent(root, {"agent_id": "a", "status": "online"}, now=current_time["value"])
            heartbeat_live_agent(
                root,
                "a",
                status="error",
                metadata={"last_error": "Self-service command exited with return code 2."},
                now=current_time["value"],
            )
            processes[0].returncode = 2
            waiting = supervisor.list_groups()
            reports["current"] = {
                "status": "failed",
                "checks": [],
                "agents": [
                    {
                        "agent_id": "bad-agent",
                        "checks": [
                            {
                                "id": "command",
                                "status": "failed",
                                "message": "Command not found: missing",
                            }
                        ],
                    }
                ],
            }
            current_time["value"] += timedelta(seconds=11)

            groups = supervisor.list_groups()
            agents = {str(agent["agent_id"]): agent for agent in read_live_agents(root, now=current_time["value"])}
            events = _read_lifecycle_events(root)

        self.assertEqual(len(processes), 1)
        self.assertEqual(waiting[0]["status"], "restarting")
        self.assertEqual(groups[0]["status"], "error")
        self.assertIsNone(groups[0]["pid"])
        self.assertEqual(groups[0]["restart_count"], 1)
        self.assertIn("Live agent preflight failed", groups[0]["last_error"])
        self.assertIn("bad-agent command", groups[0]["last_error"])
        self.assertEqual(agents["a"]["status"], "error")
        self.assertEqual(agents["a"]["last_error"], "Self-service command exited with return code 2.")
        self.assertEqual(events[-1]["event_type"], "restart_failed")
        self.assertEqual(events[-1]["offline"]["offline_agent_ids"], [])
        self.assertEqual(events[-1]["offline"]["attention"], [{"agent_id": "a", "status": "preserved_error"}])

    def test_stop_group_interrupts_owned_process_and_marks_stopped(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            process = FakeProcess(pid=9876)
            supervisor = LiveAgentProcessSupervisor(
                Path(temp_dir),
                command_factory=lambda command, **kwargs: process,
                now_fn=lambda: datetime(2026, 5, 17, 12, 0, tzinfo=UTC),
            )
            config_path = Path(temp_dir) / "live-agents.json"
            config_path.write_text('{"agents": [{"agent_id": "a", "command": ["fake"]}]}', encoding="utf-8")
            supervisor.start_group(config_path=config_path, server="http://room.local", group_id="crew")

            record = supervisor.stop_group("crew")

            self.assertEqual(process.signals, [signal.SIGINT])
            self.assertEqual(record["status"], "stopped")
            self.assertEqual(record["returncode"], 0)
            self.assertEqual(supervisor.list_groups()[0]["status"], "stopped")

    def test_stop_group_marks_matching_manifest_agents_offline(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            process = FakeProcess(pid=9876)
            now = datetime(2026, 5, 17, 12, 0, tzinfo=UTC)
            supervisor = LiveAgentProcessSupervisor(
                root,
                command_factory=lambda command, **kwargs: process,
                now_fn=lambda: now,
            )
            config_path = root / "live-agents.json"
            config_path.write_text(
                json.dumps(
                    {
                        "agents": [
                            {"agent_id": "agent-a", "display_name": "Agent A", "command": ["fake"]},
                            {"agent_id": "agent-b", "display_name": "Agent B", "command": ["fake"]},
                        ]
                    }
                ),
                encoding="utf-8",
            )
            supervisor.start_group(config_path=config_path, server="http://room.local", group_id="crew", meeting_id="meeting-1")
            connect_live_agent(root, {"agent_id": "agent-a", "meeting_id": "meeting-1", "status": "online"}, now=now)
            connect_live_agent(root, {"agent_id": "agent-b", "meeting_id": "meeting-1", "status": "working"}, now=now)

            stopped = supervisor.stop_group("crew")
            agents = {str(agent["agent_id"]): agent for agent in read_live_agents(root, now=now)}

        self.assertEqual(agents["agent-a"]["status"], "offline")
        self.assertEqual(agents["agent-b"]["status"], "offline")
        self.assertEqual(stopped["offline"]["expected"], 2)
        self.assertEqual(stopped["offline"]["offline"], 2)
        self.assertEqual(stopped["offline"]["skipped"], 0)
        self.assertEqual(stopped["offline"]["offline_agent_ids"], ["agent-a", "agent-b"])
        self.assertEqual(stopped["offline"]["attention"], [])

    def test_stop_group_kills_matching_orphan_run_group_when_record_is_already_stopped(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = root / "live-agents.json"
            config_path.write_text(
                json.dumps(
                    {
                        "agents": [
                            {
                                "agent_id": "agent-a",
                                "display_name": "Agent A",
                                "command": ["fake"],
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            state_path = root / "live-agent-runs" / "processes.json"
            state_path.parent.mkdir(parents=True)
            state_path.write_text(
                json.dumps(
                    {
                        "groups": [
                            {
                                "group_id": "crew",
                                "status": "stopped",
                                "pid": None,
                                "meeting_id": "meeting-1",
                                "config_path": str(config_path),
                                "server": "http://room.local",
                                "agents": [
                                    {
                                        "agent_id": "agent-a",
                                        "display_name": "Agent A",
                                        "provider_kind": "codex",
                                        "connection_kind": "live_session",
                                    }
                                ],
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            alive = {3333: True, 4444: True}
            sent_signals = []

            def process_lister():
                return [
                    {
                        "pid": 3333,
                        "command": (
                            f"{sys.executable} -m agentsassemble.cli live-agent run-group "
                            f"--config {config_path} --server http://room.local"
                        ),
                    },
                    {
                        "pid": 4444,
                        "command": (
                            f"{sys.executable} -m agentsassemble.cli live-agent run-group "
                            f"--config {root / 'other.json'} --server http://room.local"
                        ),
                    },
                ]

            def signal_sender(pid, signum):
                sent_signals.append((pid, signum))
                if signum == signal.SIGTERM:
                    alive[pid] = False

            supervisor = LiveAgentProcessSupervisor(
                root,
                now_fn=lambda: datetime(2026, 5, 17, 12, 0, tzinfo=UTC),
                orphan_process_lister=process_lister,
                orphan_signal_sender=signal_sender,
                orphan_pid_alive_checker=lambda pid: alive.get(pid, False),
                orphan_sleep=lambda _seconds: None,
            )
            connect_live_agent(
                root,
                {"agent_id": "agent-a", "meeting_id": "meeting-1", "status": "online"},
                now=datetime(2026, 5, 17, 12, 0, tzinfo=UTC),
            )

            stopped = supervisor.stop_group("crew")
            agents = {
                str(agent["agent_id"]): agent
                for agent in read_live_agents(root, now=datetime(2026, 5, 17, 12, 0, tzinfo=UTC))
            }

        self.assertEqual(stopped["status"], "stopped")
        self.assertEqual(stopped["offline"]["offline_agent_ids"], ["agent-a"])
        self.assertEqual(stopped["orphan_processes"]["matched"], 1)
        self.assertEqual(stopped["orphan_processes"]["terminated_pids"], [3333])
        self.assertEqual(stopped["orphan_processes"]["still_running_pids"], [])
        self.assertEqual(sent_signals, [(3333, signal.SIGTERM)])
        self.assertEqual(agents["agent-a"]["status"], "offline")

    def test_stop_group_kills_same_agent_orphan_when_record_config_was_rewritten(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            original_config_path = root / "live-agents.original.json"
            original_config_path.write_text(
                json.dumps(
                    {
                        "agents": [
                            {
                                "agent_id": "agent-a",
                                "display_name": "Agent A",
                                "meeting_id": "meeting-1",
                                "command": ["fake"],
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            rewritten_config_path = root / "live-agent-runs" / "per-agent-configs" / "meeting-1--crew--agent-a.json"
            rewritten_config_path.parent.mkdir(parents=True)
            rewritten_config_path.write_text(original_config_path.read_text(encoding="utf-8"), encoding="utf-8")
            launch_manifest_path = root / "live-agent-runs" / "manifests" / "crew.json"
            launch_manifest_path.parent.mkdir(parents=True, exist_ok=True)
            launch_manifest_path.write_text(
                json.dumps(
                    {
                        "schema": "agentsassemble.live_agent_run_group_manifest.v1",
                        "group_id": "crew",
                        "meeting_id": "meeting-1",
                        "agent_ids": ["agent-a"],
                    }
                ),
                encoding="utf-8",
            )
            state_path = root / "live-agent-runs" / "processes.json"
            state_path.write_text(
                json.dumps(
                    {
                        "groups": [
                            {
                                "group_id": "crew",
                                "status": "stopped",
                                "pid": None,
                                "meeting_id": "meeting-1",
                                "config_path": str(rewritten_config_path),
                                "server": "http://room.local",
                                "agents": [
                                    {
                                        "agent_id": "agent-a",
                                        "display_name": "Agent A",
                                        "provider_kind": "codex",
                                        "connection_kind": "live_session",
                                    }
                                ],
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            alive = {3333: True}
            sent_signals = []

            def process_lister():
                return [
                    {
                        "pid": 3333,
                        "command": (
                            f"{sys.executable} -m agentsassemble.cli live-agent run-group "
                            f"--config {original_config_path} --server http://room.local "
                            f"--agent-manifest {launch_manifest_path}"
                        ),
                    }
                ]

            def signal_sender(pid, signum):
                sent_signals.append((pid, signum))
                if signum == signal.SIGTERM:
                    alive[pid] = False

            supervisor = LiveAgentProcessSupervisor(
                root,
                now_fn=lambda: datetime(2026, 5, 17, 12, 0, tzinfo=UTC),
                orphan_process_lister=process_lister,
                orphan_signal_sender=signal_sender,
                orphan_pid_alive_checker=lambda pid: alive.get(pid, False),
                orphan_sleep=lambda _seconds: None,
            )
            connect_live_agent(
                root,
                {"agent_id": "agent-a", "meeting_id": "meeting-1", "status": "online"},
                now=datetime(2026, 5, 17, 12, 0, tzinfo=UTC),
            )

            stopped = supervisor.stop_group_if_owned("crew", meeting_id="meeting-1", agent_ids=["agent-a"])
            agents = {
                str(agent["agent_id"]): agent
                for agent in read_live_agents(root, now=datetime(2026, 5, 17, 12, 0, tzinfo=UTC))
            }

        self.assertEqual(stopped["status"], "stopped")
        self.assertEqual(stopped["offline"]["offline_agent_ids"], ["agent-a"])
        self.assertEqual(stopped["orphan_processes"]["matched"], 1)
        self.assertEqual(stopped["orphan_processes"]["terminated_pids"], [3333])
        self.assertEqual(stopped["orphan_processes"]["still_running_pids"], [])
        self.assertEqual(sent_signals, [(3333, signal.SIGTERM)])
        self.assertEqual(agents["agent-a"]["status"], "offline")

    def test_stop_group_does_not_trust_rewritten_orphan_config_without_launch_manifest(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            original_config_path = root / "live-agents.original.json"
            original_config_path.write_text(
                json.dumps(
                    {
                        "agents": [
                            {
                                "agent_id": "agent-a",
                                "display_name": "Agent A",
                                "meeting_id": "meeting-1",
                                "command": ["fake"],
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            rewritten_config_path = root / "live-agent-runs" / "per-agent-configs" / "meeting-1--crew--agent-a.json"
            rewritten_config_path.parent.mkdir(parents=True)
            rewritten_config_path.write_text(original_config_path.read_text(encoding="utf-8"), encoding="utf-8")
            state_path = root / "live-agent-runs" / "processes.json"
            state_path.write_text(
                json.dumps(
                    {
                        "groups": [
                            {
                                "group_id": "crew",
                                "status": "stopped",
                                "pid": None,
                                "meeting_id": "meeting-1",
                                "config_path": str(rewritten_config_path),
                                "server": "http://room.local",
                                "agents": [
                                    {
                                        "agent_id": "agent-a",
                                        "display_name": "Agent A",
                                        "provider_kind": "codex",
                                        "connection_kind": "live_session",
                                    }
                                ],
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            sent_signals = []

            def process_lister():
                return [
                    {
                        "pid": 3333,
                        "command": (
                            f"{sys.executable} -m agentsassemble.cli live-agent run-group "
                            f"--config {original_config_path} --server http://room.local"
                        ),
                    }
                ]

            supervisor = LiveAgentProcessSupervisor(
                root,
                now_fn=lambda: datetime(2026, 5, 17, 12, 0, tzinfo=UTC),
                orphan_process_lister=process_lister,
                orphan_signal_sender=lambda pid, signum: sent_signals.append((pid, signum)),
                orphan_pid_alive_checker=lambda pid: True,
                orphan_sleep=lambda _seconds: None,
            )
            connect_live_agent(
                root,
                {"agent_id": "agent-a", "meeting_id": "meeting-1", "status": "online"},
                now=datetime(2026, 5, 17, 12, 0, tzinfo=UTC),
            )

            stopped = supervisor.stop_group_if_owned("crew", meeting_id="meeting-1", agent_ids=["agent-a"])
            agents = {
                str(agent["agent_id"]): agent
                for agent in read_live_agents(root, now=datetime(2026, 5, 17, 12, 0, tzinfo=UTC))
            }

        self.assertEqual(stopped["status"], "stopped")
        self.assertNotIn("orphan_processes", stopped)
        self.assertEqual(sent_signals, [])
        self.assertEqual(agents["agent-a"]["status"], "online")

    def test_stop_group_does_not_match_orphan_from_previous_launch_manifest(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            old_config_path = root / "old-live-agents.json"
            old_config_path.write_text(
                json.dumps(
                    {
                        "agents": [
                            {
                                "agent_id": "agent-a",
                                "display_name": "Agent A",
                                "meeting_id": "meeting-1",
                                "command": ["fake"],
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            new_config_path = root / "new-live-agents.json"
            new_config_path.write_text(
                json.dumps(
                    {
                        "agents": [
                            {
                                "agent_id": "agent-b",
                                "display_name": "Agent B",
                                "meeting_id": "meeting-2",
                                "command": ["fake"],
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            stale_manifest_path = root / "live-agent-runs" / "manifests" / "crew--old-launch.json"
            stale_manifest_path.parent.mkdir(parents=True, exist_ok=True)
            stale_manifest_path.write_text(
                json.dumps(
                    {
                        "schema": "agentsassemble.live_agent_run_group_manifest.v1",
                        "group_id": "crew",
                        "meeting_id": "meeting-1",
                        "agent_ids": ["agent-a"],
                    }
                ),
                encoding="utf-8",
            )
            state_path = root / "live-agent-runs" / "processes.json"
            state_path.write_text(
                json.dumps(
                    {
                        "groups": [
                            {
                                "group_id": "crew",
                                "status": "stopped",
                                "pid": None,
                                "meeting_id": "meeting-2",
                                "config_path": str(new_config_path),
                                "server": "http://room.local",
                                "agents": [
                                    {
                                        "agent_id": "agent-b",
                                        "display_name": "Agent B",
                                        "provider_kind": "codex",
                                        "connection_kind": "live_session",
                                    }
                                ],
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            sent_signals = []

            def process_lister():
                return [
                    {
                        "pid": 3333,
                        "command": (
                            f"{sys.executable} -m agentsassemble.cli live-agent run-group "
                            f"--config {old_config_path} --server http://room.local "
                            f"--agent-manifest {stale_manifest_path}"
                        ),
                    }
                ]

            supervisor = LiveAgentProcessSupervisor(
                root,
                now_fn=lambda: datetime(2026, 5, 17, 12, 0, tzinfo=UTC),
                orphan_process_lister=process_lister,
                orphan_signal_sender=lambda pid, signum: sent_signals.append((pid, signum)),
                orphan_pid_alive_checker=lambda pid: True,
                orphan_sleep=lambda _seconds: None,
            )
            connect_live_agent(
                root,
                {"agent_id": "agent-b", "meeting_id": "meeting-2", "status": "online"},
                now=datetime(2026, 5, 17, 12, 0, tzinfo=UTC),
            )

            stopped = supervisor.stop_group_if_owned("crew", meeting_id="meeting-2", agent_ids=["agent-b"])
            agents = {
                str(agent["agent_id"]): agent
                for agent in read_live_agents(root, now=datetime(2026, 5, 17, 12, 0, tzinfo=UTC))
            }

        self.assertEqual(stopped["status"], "stopped")
        self.assertNotIn("orphan_processes", stopped)
        self.assertEqual(sent_signals, [])
        self.assertEqual(agents["agent-b"]["status"], "online")

    def test_process_error_preserves_matching_agent_error_presence(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            process = FakeProcess(pid=9876)
            now = datetime(2026, 5, 17, 12, 0, tzinfo=UTC)
            supervisor = LiveAgentProcessSupervisor(
                root,
                command_factory=lambda command, **kwargs: process,
                now_fn=lambda: now,
            )
            config_path = root / "live-agents.json"
            config_path.write_text(
                json.dumps(
                    {
                        "agents": [
                            {"agent_id": "agent-a", "display_name": "Agent A", "command": ["fake"]},
                            {"agent_id": "agent-b", "display_name": "Agent B", "command": ["fake"]},
                        ]
                    }
                ),
                encoding="utf-8",
            )
            supervisor.start_group(config_path=config_path, server="http://room.local", group_id="crew", meeting_id="meeting-1")
            connect_live_agent(root, {"agent_id": "agent-a", "meeting_id": "meeting-1", "status": "online"}, now=now)
            connect_live_agent(root, {"agent_id": "agent-b", "meeting_id": "meeting-1", "status": "online"}, now=now)
            heartbeat_live_agent(
                root,
                "agent-b",
                status="error",
                metadata={"last_error": "Self-service command exited with return code 7."},
                now=now,
            )

            process.returncode = 7
            stopped = supervisor.list_groups()[0]
            agents = {str(agent["agent_id"]): agent for agent in read_live_agents(root, now=now)}
            events = _read_lifecycle_events(root)

        self.assertEqual(stopped["status"], "error")
        self.assertEqual(agents["agent-a"]["status"], "offline")
        self.assertEqual(agents["agent-b"]["status"], "error")
        self.assertEqual(agents["agent-b"]["last_error"], "Self-service command exited with return code 7.")
        self.assertEqual(stopped["recent_events"][-1]["offline"]["expected"], 2)
        self.assertEqual(stopped["recent_events"][-1]["offline"]["offline"], 1)
        self.assertEqual(stopped["recent_events"][-1]["offline"]["skipped"], 1)
        self.assertEqual(stopped["recent_events"][-1]["offline"]["offline_agent_ids"], ["agent-a"])
        self.assertEqual(
            stopped["recent_events"][-1]["offline"]["attention"],
            [{"agent_id": "agent-b", "status": "preserved_error"}],
        )
        self.assertEqual(events[-1]["event_type"], "error")
        self.assertEqual(events[-1]["offline"]["offline_agent_ids"], ["agent-a"])
        self.assertEqual(events[-1]["offline"]["attention"], [{"agent_id": "agent-b", "status": "preserved_error"}])

    def test_stop_group_does_not_offline_manifest_agent_from_another_meeting(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            process = FakeProcess(pid=9876)
            now = datetime(2026, 5, 17, 12, 0, tzinfo=UTC)
            supervisor = LiveAgentProcessSupervisor(
                root,
                command_factory=lambda command, **kwargs: process,
                now_fn=lambda: now,
            )
            config_path = root / "live-agents.json"
            config_path.write_text('{"agents": [{"agent_id": "agent-a", "command": ["fake"]}]}', encoding="utf-8")
            supervisor.start_group(config_path=config_path, server="http://room.local", group_id="crew", meeting_id="meeting-1")
            connect_live_agent(root, {"agent_id": "agent-a", "meeting_id": "meeting-2", "status": "online"}, now=now)

            stopped = supervisor.stop_group("crew")
            agents = {str(agent["agent_id"]): agent for agent in read_live_agents(root, now=now)}

        self.assertEqual(agents["agent-a"]["status"], "online")
        self.assertEqual(agents["agent-a"]["meeting_id"], "meeting-2")
        self.assertEqual(stopped["offline"]["expected"], 1)
        self.assertEqual(stopped["offline"]["offline"], 0)
        self.assertEqual(stopped["offline"]["skipped"], 1)
        self.assertEqual(stopped["offline"]["attention"], [{"agent_id": "agent-a", "status": "wrong_meeting"}])

    def test_stop_group_does_not_offline_agent_still_owned_by_another_running_group(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            processes = []
            now = datetime(2026, 5, 17, 12, 0, tzinfo=UTC)

            def command_factory(command, **kwargs):
                process = FakeProcess(pid=9800 + len(processes))
                processes.append(process)
                return process

            supervisor = LiveAgentProcessSupervisor(
                root,
                command_factory=command_factory,
                now_fn=lambda: now,
            )
            config_path = root / "live-agents.json"
            config_path.write_text('{"agents": [{"agent_id": "agent-a", "command": ["fake"]}]}', encoding="utf-8")
            supervisor.start_group(config_path=config_path, server="http://room.local", group_id="crew-a")
            supervisor.start_group(config_path=config_path, server="http://room.local", group_id="crew-b")
            connect_live_agent(root, {"agent_id": "agent-a", "status": "online"}, now=now)

            stopped = supervisor.stop_group("crew-a")
            agents = {str(agent["agent_id"]): agent for agent in read_live_agents(root, now=now)}

        self.assertEqual(agents["agent-a"]["status"], "online")
        self.assertEqual(stopped["offline"]["expected"], 1)
        self.assertEqual(stopped["offline"]["offline"], 0)
        self.assertEqual(stopped["offline"]["skipped"], 1)
        self.assertEqual(stopped["offline"]["attention"], [{"agent_id": "agent-a", "status": "still_owned"}])
        self.assertEqual(supervisor.list_groups()[0]["status"], "stopped")
        self.assertEqual(supervisor.list_groups()[1]["status"], "running")

    def test_stop_running_groups_stops_owned_running_and_pending_restart_groups(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = root / "live-agents.json"
            config_path.write_text('{"agents": [{"agent_id": "a", "command": ["fake"]}]}', encoding="utf-8")
            processes = []

            def command_factory(command, **kwargs):
                process = FakeProcess(pid=9000 + len(processes))
                processes.append(process)
                return process

            supervisor = LiveAgentProcessSupervisor(
                root,
                command_factory=command_factory,
                now_fn=lambda: datetime(2026, 5, 17, 12, 0, tzinfo=UTC),
            )
            supervisor.start_group(config_path=config_path, server="http://room.local", group_id="running-a")
            supervisor.start_group(config_path=config_path, server="http://room.local", group_id="already-stopped")
            supervisor.stop_group("already-stopped")
            supervisor.start_group(
                config_path=config_path,
                server="http://room.local",
                group_id="pending-restart",
                auto_restart=True,
                max_restarts=1,
                restart_backoff_seconds=60,
            )
            processes[-1].returncode = 7
            supervisor.list_groups()
            connect_live_agent(root, {"agent_id": "a", "status": "online"}, now=datetime(2026, 5, 17, 12, 0, tzinfo=UTC))

            result = supervisor.stop_running_groups()
            groups = {group["group_id"]: group for group in supervisor.list_groups()}

        self.assertEqual(result["stopped_count"], 2)
        self.assertEqual(result["failed_count"], 0)
        self.assertEqual(result["skipped_count"], 1)
        self.assertEqual([record["group_id"] for record in result["stopped"]], ["running-a", "pending-restart"])
        self.assertEqual(groups["running-a"]["status"], "stopped")
        self.assertEqual(groups["pending-restart"]["status"], "stopped")
        self.assertEqual(groups["pending-restart"]["next_restart_at"], "")
        self.assertEqual(groups["already-stopped"]["status"], "stopped")
        self.assertEqual(processes[0].signals, [signal.SIGINT])
        self.assertEqual(result["stopped"][0]["offline"]["offline_agent_ids"], [])
        self.assertEqual(result["stopped"][0]["offline"]["attention"], [{"agent_id": "a", "status": "still_owned"}])
        self.assertEqual(result["stopped"][1]["offline"]["offline_agent_ids"], ["a"])

    def test_stop_running_groups_cancels_due_restart_without_launching_again(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = root / "live-agents.json"
            config_path.write_text('{"agents": [{"agent_id": "a", "command": ["fake"]}]}', encoding="utf-8")
            current_time = {"value": datetime(2026, 5, 17, 12, 0, tzinfo=UTC)}
            processes = []

            def command_factory(command, **kwargs):
                process = FakeProcess(pid=9100 + len(processes))
                processes.append(process)
                return process

            supervisor = LiveAgentProcessSupervisor(
                root,
                command_factory=command_factory,
                now_fn=lambda: current_time["value"],
            )
            supervisor.start_group(
                config_path=config_path,
                server="http://room.local",
                group_id="pending-restart",
                auto_restart=True,
                max_restarts=1,
                restart_backoff_seconds=1,
            )
            processes[0].returncode = 7
            supervisor.list_groups()
            current_time["value"] += timedelta(seconds=2)

            result = supervisor.stop_running_groups()
            groups = {group["group_id"]: group for group in supervisor.list_groups()}

        self.assertEqual(len(processes), 1)
        self.assertEqual(result["stopped_count"], 1)
        self.assertEqual(groups["pending-restart"]["status"], "stopped")
        self.assertEqual(groups["pending-restart"]["next_restart_at"], "")

    def test_start_group_refuses_duplicate_running_group(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = root / "live-agents.json"
            config_path.write_text('{"agents": [{"agent_id": "a", "command": ["fake"]}]}', encoding="utf-8")
            supervisor = LiveAgentProcessSupervisor(root, command_factory=lambda command, **kwargs: FakeProcess())

            supervisor.start_group(config_path=config_path, server="http://room.local", group_id="crew")

            with self.assertRaises(ValueError):
                supervisor.start_group(config_path=config_path, server="http://room.local", group_id="crew")

    def test_start_group_prevents_concurrent_duplicate_launches(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = root / "live-agents.json"
            config_path.write_text('{"agents": [{"agent_id": "a", "command": ["fake"]}]}', encoding="utf-8")
            launched = []

            def command_factory(command, **kwargs):
                launched.append(command)
                time.sleep(0.05)
                return FakeProcess(pid=1000 + len(launched))

            supervisor = LiveAgentProcessSupervisor(root, command_factory=command_factory)
            barrier = threading.Barrier(2)
            results = []
            errors = []

            def start():
                barrier.wait()
                try:
                    results.append(supervisor.start_group(config_path=config_path, server="http://room.local", group_id="crew"))
                except ValueError as error:
                    errors.append(str(error))

            threads = [threading.Thread(target=start), threading.Thread(target=start)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()

            self.assertEqual(len(launched), 1)
            self.assertEqual(len(results), 1)
            self.assertEqual(len(errors), 1)

    def test_close_interrupts_owned_running_groups(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            process = FakeProcess(pid=2468)
            supervisor = LiveAgentProcessSupervisor(
                Path(temp_dir),
                command_factory=lambda command, **kwargs: process,
                now_fn=lambda: datetime(2026, 5, 17, 12, 0, tzinfo=UTC),
            )
            config_path = Path(temp_dir) / "live-agents.json"
            config_path.write_text('{"agents": [{"agent_id": "a", "command": ["fake"]}]}', encoding="utf-8")
            supervisor.start_group(config_path=config_path, server="http://room.local", group_id="crew")

            supervisor.close()

            self.assertEqual(process.signals, [signal.SIGINT])
            self.assertEqual(supervisor.list_groups()[0]["status"], "stopped")

    def test_stop_group_preserves_already_crashed_returncode(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            process = FakeProcess(pid=1357)
            supervisor = LiveAgentProcessSupervisor(Path(temp_dir), command_factory=lambda command, **kwargs: process)
            config_path = Path(temp_dir) / "live-agents.json"
            config_path.write_text('{"agents": [{"agent_id": "a", "command": ["fake"]}]}', encoding="utf-8")
            supervisor.start_group(config_path=config_path, server="http://room.local", group_id="crew")
            process.returncode = 2

            record = supervisor.stop_group("crew")

            self.assertEqual(record["returncode"], 2)
            self.assertEqual(record["status"], "error")

    def test_stop_group_terminates_when_interrupt_times_out(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            process = FakeProcess(pid=8642, wait_timeouts=1)
            supervisor = LiveAgentProcessSupervisor(Path(temp_dir), command_factory=lambda command, **kwargs: process)
            config_path = Path(temp_dir) / "live-agents.json"
            config_path.write_text('{"agents": [{"agent_id": "a", "command": ["fake"]}]}', encoding="utf-8")
            supervisor.start_group(config_path=config_path, server="http://room.local", group_id="crew")

            record = supervisor.stop_group("crew")

            self.assertEqual(process.signals, [signal.SIGINT])
            self.assertTrue(process.terminated)
            self.assertEqual(record["returncode"], 0)

    @unittest.skipUnless(_supports_process_groups(), "requires POSIX process-group support")
    def test_stop_group_timeout_terminates_supervised_child_processes(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            child_pid_path = root / "supervised-child.pid"
            config_path = root / "live-agents.json"
            config_path.write_text('{"agents": [{"agent_id": "a", "command": ["fake"]}]}', encoding="utf-8")

            def command_factory(command, **kwargs):
                del command
                return subprocess.Popen(
                    [
                        sys.executable,
                        "-u",
                        "-c",
                        (
                            "import pathlib, signal, subprocess, sys, time; "
                            "signal.signal(signal.SIGINT, signal.SIG_IGN); "
                            "child = subprocess.Popen(["
                            "sys.executable, '-c', "
                            "'import signal, time; signal.signal(signal.SIGINT, signal.SIG_IGN); time.sleep(30)'"
                            "]); "
                            "pathlib.Path(sys.argv[1]).write_text(str(child.pid), encoding='utf-8'); "
                            "time.sleep(30)"
                        ),
                        str(child_pid_path),
                    ],
                    stdout=kwargs.get("stdout"),
                    stderr=kwargs.get("stderr"),
                    text=kwargs.get("text", True),
                    start_new_session=kwargs.get("start_new_session", False),
                )

            supervisor = LiveAgentProcessSupervisor(root, command_factory=command_factory)
            child_pid = None
            child_alive_after_stop = None
            try:
                supervisor.start_group(config_path=config_path, server="http://room.local", group_id="crew")
                deadline = time.time() + 5
                while time.time() < deadline and not child_pid_path.exists():
                    time.sleep(0.01)
                self.assertTrue(child_pid_path.exists())
                child_pid = int(child_pid_path.read_text(encoding="utf-8"))

                supervisor.stop_group("crew", timeout_seconds=0.1)
                child_alive_after_stop = not _wait_for_pid_exit(child_pid)
            finally:
                if child_pid is not None and _pid_exists(child_pid):
                    try:
                        os.kill(child_pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                supervisor.close()

            self.assertFalse(child_alive_after_stop)

    def test_start_and_stop_persist_process_records(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            process = FakeProcess(pid=1234)
            supervisor = LiveAgentProcessSupervisor(
                root,
                command_factory=lambda command, **kwargs: process,
                now_fn=lambda: datetime(2026, 5, 17, 12, 0, tzinfo=UTC),
            )
            config_path = root / "live-agents.json"
            config_path.write_text('{"agents": [{"agent_id": "a", "command": ["fake"]}]}', encoding="utf-8")

            started = supervisor.start_group(config_path=config_path, server="http://room.local", group_id="crew")
            state_path = root / "live-agent-runs" / "processes.json"
            persisted_start = json.loads(state_path.read_text(encoding="utf-8"))
            stopped = supervisor.stop_group("crew")
            persisted_stop = json.loads(state_path.read_text(encoding="utf-8"))

            self.assertEqual(persisted_start["groups"][0]["group_id"], started["group_id"])
            self.assertEqual(persisted_start["groups"][0]["status"], "running")
            self.assertEqual(persisted_stop["groups"][0]["status"], "stopped")
            self.assertEqual(persisted_stop["groups"][0]["returncode"], stopped["returncode"])

    def test_start_and_stop_write_safe_lifecycle_events(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            process = FakeProcess(pid=1234)
            current_time = {"value": datetime(2026, 5, 17, 12, 0, tzinfo=UTC)}
            supervisor = LiveAgentProcessSupervisor(
                root,
                command_factory=lambda command, **kwargs: process,
                now_fn=lambda: current_time["value"],
            )
            config_path = root / "live-agents.json"
            config_path.write_text(
                '{"agents": [{"agent_id": "a", "command": ["fake", "--token", "secret-value"]}]}',
                encoding="utf-8",
            )

            started = supervisor.start_group(config_path=config_path, server="http://room.local", group_id="crew")
            connect_live_agent(root, {"agent_id": "a", "status": "working"}, now=current_time["value"])
            current_time["value"] = datetime(2026, 5, 17, 12, 1, tzinfo=UTC)
            stopped = supervisor.stop_group("crew")
            events = _read_lifecycle_events(root)
            persisted = json.loads((root / "live-agent-runs" / "processes.json").read_text(encoding="utf-8"))
            events_text = json.dumps(events, ensure_ascii=False)

        self.assertEqual([event["event_type"] for event in events], ["started", "stopped"])
        self.assertEqual(events[0]["timestamp"], "2026-05-17T12:00:00+00:00")
        self.assertEqual(events[0]["group_id"], "crew")
        self.assertEqual(events[0]["status"], "running")
        self.assertEqual(events[0]["pid"], 1234)
        self.assertEqual(events[1]["timestamp"], "2026-05-17T12:01:00+00:00")
        self.assertEqual(events[1]["status"], "stopped")
        self.assertEqual(events[1]["returncode"], 0)
        self.assertEqual(events[1]["offline"]["expected"], 1)
        self.assertEqual(events[1]["offline"]["offline"], 1)
        self.assertEqual(events[1]["offline"]["offline_agent_ids"], ["a"])
        self.assertEqual(events[1]["offline"]["attention"], [])
        self.assertEqual(started["recent_events"], [events[0]])
        self.assertEqual(stopped["recent_events"], events)
        self.assertNotIn("secret-value", events_text)
        self.assertNotIn("http://room.local", events_text)
        self.assertNotIn("command", events_text)
        self.assertNotIn("endpoint", events_text)
        self.assertNotIn("auth_ref", events_text)
        self.assertNotIn("prompt", events_text)
        self.assertNotIn("log_tail", events_text)
        self.assertNotIn("command_path", events_text)
        self.assertNotIn("env:", events_text)
        self.assertNotIn("recent_events", persisted["groups"][0])
        self.assertNotIn("offline", persisted["groups"][0])

    def test_auto_restart_writes_lifecycle_events_for_crash_and_relaunch(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = root / "live-agents.json"
            config_path.write_text('{"agents": [{"agent_id": "a", "command": ["fake"]}]}', encoding="utf-8")
            current_time = {"value": datetime(2026, 5, 17, 12, 0, tzinfo=UTC)}
            processes = []

            def command_factory(command, **kwargs):
                process = FakeProcess(pid=7000 + len(processes))
                processes.append(process)
                return process

            supervisor = LiveAgentProcessSupervisor(
                root,
                command_factory=command_factory,
                now_fn=lambda: current_time["value"],
            )
            supervisor.start_group(
                config_path=config_path,
                server="http://room.local",
                group_id="crew",
                auto_restart=True,
                max_restarts=1,
                restart_backoff_seconds=10,
            )
            connect_live_agent(root, {"agent_id": "a", "status": "online"}, now=current_time["value"])
            processes[0].returncode = 2
            waiting = supervisor.list_groups()
            current_time["value"] += timedelta(seconds=11)
            restarted = supervisor.list_groups()
            events = _read_lifecycle_events(root)

        self.assertEqual([event["event_type"] for event in events], ["started", "restart_scheduled", "started"])
        self.assertEqual(events[1]["status"], "restarting")
        self.assertEqual(events[1]["returncode"], 2)
        self.assertEqual(events[1]["restart_count"], 1)
        self.assertEqual(events[1]["next_restart_at"], "2026-05-17T12:00:10+00:00")
        self.assertEqual(events[1]["offline"]["offline_agent_ids"], ["a"])
        self.assertEqual(events[2]["pid"], 7001)
        self.assertEqual(events[2]["restart_count"], 1)
        self.assertEqual(waiting[0]["recent_events"], events[:2])
        self.assertEqual(restarted[0]["recent_events"], events)

    def test_crash_lifecycle_error_event_includes_offline_attention(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            process = FakeProcess(pid=7100)
            current_time = {"value": datetime(2026, 5, 17, 12, 0, tzinfo=UTC)}
            supervisor = LiveAgentProcessSupervisor(
                root,
                command_factory=lambda command, **kwargs: process,
                now_fn=lambda: current_time["value"],
            )
            config_path = root / "live-agents.json"
            config_path.write_text('{"agents": [{"agent_id": "agent-a", "command": ["fake"]}]}', encoding="utf-8")
            supervisor.start_group(
                config_path=config_path,
                server="http://room.local",
                group_id="crew",
                meeting_id="meeting-1",
            )
            connect_live_agent(
                root,
                {"agent_id": "agent-a", "meeting_id": "meeting-2", "status": "online"},
                now=current_time["value"],
            )
            process.returncode = 2

            groups = supervisor.list_groups()
            events = _read_lifecycle_events(root)

        self.assertEqual([event["event_type"] for event in events], ["started", "error"])
        self.assertEqual(events[1]["offline"]["expected"], 1)
        self.assertEqual(events[1]["offline"]["offline"], 0)
        self.assertEqual(events[1]["offline"]["skipped"], 1)
        self.assertEqual(events[1]["offline"]["offline_agent_ids"], [])
        self.assertEqual(events[1]["offline"]["attention"], [{"agent_id": "agent-a", "status": "wrong_meeting"}])
        self.assertEqual(groups[0]["recent_events"], events)

    def test_recent_lifecycle_events_are_bounded(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = root / "live-agents.json"
            config_path.write_text('{"agents": [{"agent_id": "a", "command": ["fake"]}]}', encoding="utf-8")
            current_time = {"value": datetime(2026, 5, 17, 12, 0, tzinfo=UTC)}
            processes = []

            def command_factory(command, **kwargs):
                process = FakeProcess(pid=7200 + len(processes))
                processes.append(process)
                return process

            supervisor = LiveAgentProcessSupervisor(
                root,
                command_factory=command_factory,
                now_fn=lambda: current_time["value"],
            )
            for index in range(3):
                supervisor.start_group(config_path=config_path, server="http://room.local", group_id="crew")
                current_time["value"] += timedelta(minutes=1)
                supervisor.stop_group("crew")
                current_time["value"] += timedelta(minutes=1)

            record = supervisor.list_groups()[0]
            events = _read_lifecycle_events(root)

        self.assertEqual(len(events), 6)
        self.assertEqual(len(record["recent_events"]), 5)
        self.assertEqual(record["recent_events"], events[-5:])
        self.assertEqual([event["event_type"] for event in record["recent_events"]], ["stopped", "started", "stopped", "started", "stopped"])

    def test_recent_lifecycle_events_do_not_load_whole_history_file_at_once(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = root / "live-agents.json"
            config_path.write_text('{"agents": [{"agent_id": "a", "command": ["fake"]}]}', encoding="utf-8")
            processes = []

            def command_factory(command, **kwargs):
                process = FakeProcess(pid=7250 + len(processes))
                processes.append(process)
                return process

            supervisor = LiveAgentProcessSupervisor(root, command_factory=command_factory)
            for _ in range(3):
                supervisor.start_group(config_path=config_path, server="http://room.local", group_id="crew")
                supervisor.stop_group("crew")
            original_open = Path.open

            class BoundedReadFile:
                def __init__(self, wrapped):
                    self.wrapped = wrapped

                def __enter__(self):
                    self.wrapped.__enter__()
                    return self

                def __exit__(self, exc_type, exc, traceback):
                    return self.wrapped.__exit__(exc_type, exc, traceback)

                def __getattr__(self, name):
                    return getattr(self.wrapped, name)

                def __iter__(self):
                    return iter(self.wrapped)

                def read(self, size=-1):
                    if size is None or size < 0:
                        raise AssertionError("unbounded lifecycle read")
                    return self.wrapped.read(size)

            def open_guard(path, *args, **kwargs):
                mode = str(args[0] if args else kwargs.get("mode", "r"))
                opened = original_open(path, *args, **kwargs)
                if path.name == "events.jsonl" and "r" in mode:
                    return BoundedReadFile(opened)
                return opened

            with patch.object(Path, "open", open_guard):
                record = supervisor.list_groups()[0]

        self.assertEqual(len(record["recent_events"]), 5)

    def test_recent_lifecycle_events_are_read_from_tail_without_iterating_old_history(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            runs_dir = root / "live-agent-runs"
            runs_dir.mkdir(parents=True)
            (runs_dir / "processes.json").write_text(
                json.dumps({"groups": [{"group_id": "crew", "status": "stopped", "pid": None}]}),
                encoding="utf-8",
            )
            older_events = [
                {
                    "timestamp": f"2026-05-17T11:{index:02d}:00+00:00",
                    "group_id": "archive",
                    "event_type": "started",
                    "status": "stopped",
                }
                for index in range(30)
            ]
            recent_events = [
                {
                    "timestamp": f"2026-05-17T12:0{index}:00+00:00",
                    "group_id": "crew",
                    "event_type": "started" if index % 2 == 0 else "stopped",
                    "status": "stopped",
                    "pid": 9000 + index,
                    "returncode": None,
                    "restart_count": 0,
                    "max_restarts": 0,
                }
                for index in range(5)
            ]
            (runs_dir / "events.jsonl").write_text(
                "\n".join(json.dumps(event, ensure_ascii=False) for event in [*older_events, *recent_events]) + "\n",
                encoding="utf-8",
            )
            original_open = Path.open

            class NoTextIterationFile:
                def __init__(self, wrapped):
                    self.wrapped = wrapped

                def __enter__(self):
                    self.wrapped.__enter__()
                    return self

                def __exit__(self, exc_type, exc, traceback):
                    return self.wrapped.__exit__(exc_type, exc, traceback)

                def __getattr__(self, name):
                    return getattr(self.wrapped, name)

                def __iter__(self):
                    raise AssertionError("process rows must not iterate lifecycle history from the beginning")

            def open_guard(path, *args, **kwargs):
                mode = str(args[0] if args else kwargs.get("mode", "r"))
                opened = original_open(path, *args, **kwargs)
                if path.name == "events.jsonl" and "r" in mode and "b" not in mode:
                    return NoTextIterationFile(opened)
                return opened

            with patch.object(Path, "open", open_guard):
                groups = LiveAgentProcessSupervisor(root).list_groups()

        self.assertEqual(groups[0]["recent_events"], recent_events)

    def test_read_live_agent_process_events_returns_recent_safe_events(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            path = root / "live-agent-runs" / "events.jsonl"
            path.parent.mkdir(parents=True)
            records = [
                {
                    "timestamp": "2026-05-17T12:00:00+00:00",
                    "group_id": "crew",
                    "event_type": "started",
                    "status": "running",
                    "pid": 1234,
                    "server": "http://room.local",
                    "config_path": "/tmp/live-agents.json",
                    "command": ["fake", "--token", "secret-value"],
                },
                {
                    "timestamp": "2026-05-17T12:01:00+00:00",
                    "group_id": "other",
                    "event_type": "started",
                    "status": "running",
                    "pid": 2234,
                },
                {
                    "timestamp": "2026-05-17T12:02:00+00:00",
                    "group_id": "crew",
                    "event_type": "restart_scheduled",
                    "status": "restarting",
                    "returncode": 2,
                    "restart_count": 1,
                    "max_restarts": 3,
                    "next_restart_at": "2026-05-17T12:02:10+00:00",
                    "offline": {
                        "expected": 2,
                        "offline": 1,
                        "skipped": 1,
                        "offline_agent_ids": ["agent-a"],
                        "attention": [{"agent_id": "agent-b", "status": "wrong_meeting"}],
                    },
                    "log_tail": "provider output",
                    "prompt": "secret prompt",
                    "auth_ref": "env:SECRET_TOKEN",
                },
                {"group_id": "crew", "status": "ignored"},
            ]
            path.write_text(
                "\n".join(
                    [
                        json.dumps(records[0]),
                        "not-json",
                        json.dumps(records[1]),
                        json.dumps(records[2]),
                        json.dumps(records[3]),
                    ]
                ),
                encoding="utf-8",
            )

            events = read_live_agent_process_events(root, limit=2, group_id="crew")
            events_text = json.dumps(events, ensure_ascii=False)

        self.assertEqual([event["event_type"] for event in events], ["started", "restart_scheduled"])
        self.assertEqual(events[1]["offline"]["expected"], 2)
        self.assertEqual(events[1]["offline"]["offline"], 1)
        self.assertEqual(events[1]["offline"]["offline_agent_ids"], ["agent-a"])
        self.assertEqual(events[1]["offline"]["attention"], [{"agent_id": "agent-b", "status": "wrong_meeting"}])
        self.assertNotIn("other", json.dumps([event["group_id"] for event in events]))
        self.assertNotIn("secret-value", events_text)
        self.assertNotIn("http://room.local", events_text)
        self.assertNotIn("config_path", events_text)
        self.assertNotIn("command", events_text)
        self.assertNotIn("auth_ref", events_text)
        self.assertNotIn("prompt", events_text)
        self.assertNotIn("log_tail", events_text)

    def test_read_live_agent_process_events_sanitizes_whitelisted_string_fields(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            path = root / "live-agent-runs" / "events.jsonl"
            path.parent.mkdir(parents=True)
            path.write_text(
                json.dumps(
                    {
                        "timestamp": "literal:SECRET_TOKEN",
                        "group_id": "crew",
                        "event_type": "started http://secret.local",
                        "status": "/tmp/secret.json",
                        "pid": -1234,
                        "returncode": True,
                        "next_restart_at": "env:SECRET_TOKEN",
                        "previous_status": "prompt secret",
                        "reason": "secret raw provider output token",
                    }
                ),
                encoding="utf-8",
            )

            events = read_live_agent_process_events(root, limit=1)
            events_text = json.dumps(events, ensure_ascii=False)

        self.assertEqual(events[0]["event_type"], "updated")
        self.assertEqual(events[0]["status"], "unknown")
        self.assertIsNone(events[0]["pid"])
        self.assertIsNone(events[0]["returncode"])
        self.assertEqual(events[0]["timestamp"], "")
        self.assertNotIn("next_restart_at", events[0])
        self.assertNotIn("previous_status", events[0])
        self.assertNotIn("SECRET_TOKEN", events_text)
        self.assertNotIn("http://secret.local", events_text)
        self.assertNotIn("/tmp/secret.json", events_text)
        self.assertNotIn("prompt secret", events_text)
        self.assertNotIn("reason", events[0])
        self.assertNotIn("secret raw provider output token", events_text)

    def test_read_live_agent_process_events_preserves_only_known_watchdog_reasons(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            path = root / "live-agent-runs" / "events.jsonl"
            path.parent.mkdir(parents=True)
            rows = [
                {
                    "timestamp": "2026-05-17T12:00:00+00:00",
                    "group_id": "crew",
                    "event_type": "started",
                    "status": "running",
                    "reason": "missing manifest agent agent-a",
                },
                {
                    "timestamp": "2026-05-17T12:00:01+00:00",
                    "group_id": "crew",
                    "event_type": "stale_watchdog",
                    "status": "running",
                    "reason": "secret raw provider output token",
                },
                {
                    "timestamp": "2026-05-17T12:00:02+00:00",
                    "group_id": "crew",
                    "event_type": "stale_watchdog",
                    "status": "running",
                    "reason": "missing manifest agent agent-a",
                },
            ]
            path.write_text("\n".join(json.dumps(row) for row in rows), encoding="utf-8")

            events = read_live_agent_process_events(root, limit=3)
            events_text = json.dumps(events, ensure_ascii=False)

        self.assertNotIn("reason", events[0])
        self.assertNotIn("reason", events[1])
        self.assertEqual(events[2]["reason"], "missing manifest agent agent-a")
        self.assertNotIn("secret raw provider output token", events_text)

    def test_read_live_agent_process_events_does_not_load_whole_history_file_at_once(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            path = root / "live-agent-runs" / "events.jsonl"
            path.parent.mkdir(parents=True)
            path.write_text(
                "\n".join(
                    json.dumps(
                        {
                            "timestamp": f"2026-05-17T12:00:{index:02d}+00:00",
                            "group_id": "crew",
                            "event_type": "started",
                            "status": "running",
                            "pid": 8000 + index,
                        }
                    )
                    for index in range(5)
                ),
                encoding="utf-8",
            )
            original_open = Path.open

            class BoundedReadFile:
                def __init__(self, wrapped):
                    self.wrapped = wrapped

                def __enter__(self):
                    self.wrapped.__enter__()
                    return self

                def __exit__(self, exc_type, exc, traceback):
                    return self.wrapped.__exit__(exc_type, exc, traceback)

                def __getattr__(self, name):
                    return getattr(self.wrapped, name)

                def read(self, size=-1):
                    if size is None or size < 0:
                        raise AssertionError("unbounded process lifecycle event read")
                    return self.wrapped.read(size)

            def open_guard(path, *args, **kwargs):
                mode = str(args[0] if args else kwargs.get("mode", "r"))
                opened = original_open(path, *args, **kwargs)
                if path.name == "events.jsonl" and "r" in mode:
                    return BoundedReadFile(opened)
                return opened

            with patch.object(Path, "open", open_guard):
                events = read_live_agent_process_events(root, limit=2)

        self.assertEqual([event["pid"] for event in events], [8003, 8004])

    def test_read_live_agent_process_event_history_caps_sparse_group_scan(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            path = root / "live-agent-runs" / "events.jsonl"
            path.parent.mkdir(parents=True)
            path.write_text(
                "\n".join(
                    json.dumps(
                        {
                            "timestamp": f"2026-05-17T12:00:{index:02d}+00:00",
                            "group_id": "other",
                            "event_type": "started",
                            "status": "running",
                            "pid": 8100 + index,
                        }
                    )
                    for index in range(5)
                ),
                encoding="utf-8",
            )

            history = read_live_agent_process_event_history(root, limit=2, group_id="missing", scan_limit=3)

        self.assertEqual(history["events"], [])
        self.assertEqual(history["limit"], 2)
        self.assertEqual(history["group_id"], "missing")
        self.assertEqual(history["scan_limit"], 3)
        self.assertEqual(history["scanned_event_count"], 3)
        self.assertEqual(history["truncated"], True)

    def test_read_live_agent_process_event_history_is_not_truncated_when_result_window_fills(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            path = root / "live-agent-runs" / "events.jsonl"
            path.parent.mkdir(parents=True)
            path.write_text(
                "\n".join(
                    json.dumps(
                        {
                            "timestamp": f"2026-05-17T12:00:{index:02d}+00:00",
                            "group_id": "crew",
                            "event_type": "started",
                            "status": "running",
                            "pid": 8200 + index,
                        }
                    )
                    for index in range(5)
                ),
                encoding="utf-8",
            )

            history = read_live_agent_process_event_history(root, limit=2, group_id="crew", scan_limit=3)

        self.assertEqual([event["pid"] for event in history["events"]], [8203, 8204])
        self.assertEqual(history["scanned_event_count"], 2)
        self.assertEqual(history["truncated"], False)

    def test_read_live_agent_process_event_history_preserves_partial_matches_with_truncation(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            path = root / "live-agent-runs" / "events.jsonl"
            path.parent.mkdir(parents=True)
            records = [
                ("crew", 8300),
                ("crew", 8301),
                ("other", 8302),
                ("crew", 8303),
                ("other", 8304),
                ("crew", 8305),
            ]
            path.write_text(
                "\n".join(
                    json.dumps(
                        {
                            "timestamp": f"2026-05-17T12:00:{index:02d}+00:00",
                            "group_id": group_id,
                            "event_type": "started",
                            "status": "running",
                            "pid": pid,
                        }
                    )
                    for index, (group_id, pid) in enumerate(records)
                ),
                encoding="utf-8",
            )

            history = read_live_agent_process_event_history(root, limit=3, group_id="crew", scan_limit=3)

        self.assertEqual([event["pid"] for event in history["events"]], [8303, 8305])
        self.assertEqual(history["scanned_event_count"], 3)
        self.assertEqual(history["truncated"], True)

    def test_list_groups_reads_lifecycle_history_once_for_all_groups(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = root / "live-agents.json"
            config_path.write_text('{"agents": [{"agent_id": "a", "command": ["fake"]}]}', encoding="utf-8")
            processes = []

            def command_factory(command, **kwargs):
                process = FakeProcess(pid=7280 + len(processes))
                processes.append(process)
                return process

            supervisor = LiveAgentProcessSupervisor(root, command_factory=command_factory)
            for group_id in ("alpha", "beta", "gamma"):
                supervisor.start_group(config_path=config_path, server="http://room.local", group_id=group_id)
                supervisor.stop_group(group_id)
            original_open = Path.open
            event_open_count = 0

            def open_guard(path, *args, **kwargs):
                nonlocal event_open_count
                mode = str(args[0] if args else kwargs.get("mode", "r"))
                if path.name == "events.jsonl" and "r" in mode:
                    event_open_count += 1
                return original_open(path, *args, **kwargs)

            with patch.object(Path, "open", open_guard):
                groups = supervisor.list_groups()

        self.assertEqual(len(groups), 3)
        self.assertEqual(event_open_count, 1)

    def test_due_auto_restarts_do_not_rescan_lifecycle_history_per_group(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = root / "live-agents.json"
            config_path.write_text('{"agents": [{"agent_id": "a", "command": ["fake"]}]}', encoding="utf-8")
            current_time = {"value": datetime(2026, 5, 17, 12, 0, tzinfo=UTC)}
            processes = []

            def command_factory(command, **kwargs):
                process = FakeProcess(pid=7290 + len(processes))
                processes.append(process)
                return process

            supervisor = LiveAgentProcessSupervisor(
                root,
                command_factory=command_factory,
                now_fn=lambda: current_time["value"],
            )
            for group_id in ("alpha", "beta", "gamma"):
                supervisor.start_group(
                    config_path=config_path,
                    server="http://room.local",
                    group_id=group_id,
                    auto_restart=True,
                    max_restarts=1,
                    restart_backoff_seconds=1,
                )
            for process in processes:
                process.returncode = 2
            supervisor.list_groups()
            current_time["value"] += timedelta(seconds=2)
            original_open = Path.open
            event_open_count = 0

            def open_guard(path, *args, **kwargs):
                nonlocal event_open_count
                mode = str(args[0] if args else kwargs.get("mode", "r"))
                if path.name == "events.jsonl" and "r" in mode:
                    event_open_count += 1
                return original_open(path, *args, **kwargs)

            with patch.object(Path, "open", open_guard):
                groups = supervisor.list_groups()

        self.assertEqual(len(groups), 3)
        self.assertEqual(event_open_count, 1)

    def test_stale_watchdog_schedules_auto_restart_for_missing_manifest_agent(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = root / "live-agents.json"
            config_path.write_text('{"agents": [{"agent_id": "agent-a", "command": ["fake"]}]}', encoding="utf-8")
            current_time = {"value": datetime(2026, 5, 17, 12, 0, tzinfo=UTC)}
            processes = []

            def command_factory(command, **kwargs):
                process = FakeProcess(pid=7310 + len(processes))
                processes.append(process)
                return process

            supervisor = LiveAgentProcessSupervisor(
                root,
                command_factory=command_factory,
                now_fn=lambda: current_time["value"],
            )
            supervisor.start_group(
                config_path=config_path,
                server="http://room.local",
                group_id="crew",
                meeting_id="resident-m1",
                auto_restart=True,
                max_restarts=1,
                restart_backoff_seconds=10,
                stale_restart_after_seconds=60,
            )
            current_time["value"] += timedelta(seconds=61)
            groups = supervisor.list_groups()
            events = _read_lifecycle_events(root)

        self.assertEqual(groups[0]["status"], "restarting")
        self.assertIsNone(groups[0]["pid"])
        self.assertEqual(groups[0]["returncode"], -98)
        self.assertEqual(groups[0]["restart_count"], 1)
        self.assertEqual(groups[0]["meeting_id"], "resident-m1")
        self.assertEqual(groups[0]["stale_restart_after_seconds"], 60.0)
        self.assertIn("Stale watchdog", groups[0]["last_error"])
        self.assertTrue(processes[0].signals)
        self.assertEqual([event["event_type"] for event in events], ["started", "stale_watchdog", "restart_scheduled"])
        self.assertEqual(events[1]["reason"], "missing manifest agent agent-a")
        self.assertEqual(groups[0]["recent_events"][1]["reason"], "missing manifest agent agent-a")

    def test_stale_watchdog_waits_until_group_exceeds_threshold(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = root / "live-agents.json"
            config_path.write_text('{"agents": [{"agent_id": "agent-a", "command": ["fake"]}]}', encoding="utf-8")
            current_time = {"value": datetime(2026, 5, 17, 12, 0, tzinfo=UTC)}

            supervisor = LiveAgentProcessSupervisor(
                root,
                command_factory=lambda command, **kwargs: FakeProcess(pid=7320),
                now_fn=lambda: current_time["value"],
            )
            supervisor.start_group(
                config_path=config_path,
                server="http://room.local",
                group_id="crew",
                auto_restart=True,
                max_restarts=1,
                restart_backoff_seconds=10,
                stale_restart_after_seconds=60,
            )
            current_time["value"] += timedelta(seconds=59)
            groups = supervisor.list_groups()

        self.assertEqual(groups[0]["status"], "running")
        self.assertEqual(groups[0]["restart_count"], 0)

    def test_stale_watchdog_does_not_restart_at_exact_threshold(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = root / "live-agents.json"
            config_path.write_text('{"agents": [{"agent_id": "agent-a", "command": ["fake"]}]}', encoding="utf-8")
            current_time = {"value": datetime(2026, 5, 17, 12, 0, tzinfo=UTC)}

            supervisor = LiveAgentProcessSupervisor(
                root,
                command_factory=lambda command, **kwargs: FakeProcess(pid=7325),
                now_fn=lambda: current_time["value"],
            )
            supervisor.start_group(
                config_path=config_path,
                server="http://room.local",
                group_id="crew",
                auto_restart=True,
                max_restarts=1,
                restart_backoff_seconds=10,
                stale_restart_after_seconds=60,
            )
            current_time["value"] += timedelta(seconds=60)
            groups = supervisor.list_groups()

        self.assertEqual(groups[0]["status"], "running")
        self.assertEqual(groups[0]["restart_count"], 0)

    def test_stale_watchdog_schedules_auto_restart_for_stale_manifest_agent(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = root / "live-agents.json"
            config_path.write_text('{"agents": [{"agent_id": "agent-a", "command": ["fake"]}]}', encoding="utf-8")
            current_time = {"value": datetime(2026, 5, 17, 12, 0, tzinfo=UTC)}

            supervisor = LiveAgentProcessSupervisor(
                root,
                command_factory=lambda command, **kwargs: FakeProcess(pid=7326),
                now_fn=lambda: current_time["value"],
            )
            supervisor.start_group(
                config_path=config_path,
                server="http://room.local",
                group_id="crew",
                auto_restart=True,
                max_restarts=1,
                restart_backoff_seconds=10,
                stale_restart_after_seconds=60,
            )
            heartbeat_live_agent(root, "agent-a", status="online", now=current_time["value"])
            current_time["value"] += timedelta(seconds=61)
            groups = supervisor.list_groups()

        self.assertEqual(groups[0]["status"], "restarting")
        self.assertIn("stale manifest agent agent-a", groups[0]["last_error"])

    def test_stale_watchdog_schedules_auto_restart_for_offline_manifest_agent(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = root / "live-agents.json"
            config_path.write_text('{"agents": [{"agent_id": "agent-a", "command": ["fake"]}]}', encoding="utf-8")
            current_time = {"value": datetime(2026, 5, 17, 12, 0, tzinfo=UTC)}

            supervisor = LiveAgentProcessSupervisor(
                root,
                command_factory=lambda command, **kwargs: FakeProcess(pid=73261),
                now_fn=lambda: current_time["value"],
            )
            supervisor.start_group(
                config_path=config_path,
                server="http://room.local",
                group_id="crew",
                auto_restart=True,
                max_restarts=1,
                restart_backoff_seconds=10,
                stale_restart_after_seconds=60,
            )
            current_time["value"] += timedelta(seconds=61)
            heartbeat_live_agent(root, "agent-a", status="offline", now=current_time["value"])
            groups = supervisor.list_groups()

        self.assertEqual(groups[0]["status"], "restarting")
        self.assertIn("offline manifest agent agent-a", groups[0]["last_error"])

    def test_stale_watchdog_schedules_auto_restart_for_error_manifest_agent(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = root / "live-agents.json"
            config_path.write_text('{"agents": [{"agent_id": "agent-a", "command": ["fake"]}]}', encoding="utf-8")
            current_time = {"value": datetime(2026, 5, 17, 12, 0, tzinfo=UTC)}

            supervisor = LiveAgentProcessSupervisor(
                root,
                command_factory=lambda command, **kwargs: FakeProcess(pid=73262),
                now_fn=lambda: current_time["value"],
            )
            supervisor.start_group(
                config_path=config_path,
                server="http://room.local",
                group_id="crew",
                auto_restart=True,
                max_restarts=1,
                restart_backoff_seconds=10,
                stale_restart_after_seconds=60,
            )
            current_time["value"] += timedelta(seconds=61)
            heartbeat_live_agent(root, "agent-a", status="error", now=current_time["value"], metadata={"last_error": "secret raw provider output"})
            groups = supervisor.list_groups()
            events = _read_lifecycle_events(root)

        self.assertEqual(groups[0]["status"], "restarting")
        self.assertIn("error manifest agent agent-a", groups[0]["last_error"])
        self.assertNotIn("secret raw provider output", groups[0]["last_error"])
        self.assertEqual(events[1]["reason"], "error manifest agent agent-a")
        self.assertNotIn("secret raw provider output", json.dumps(events, ensure_ascii=False))

    def test_stale_watchdog_schedules_auto_restart_for_wrong_meeting_manifest_agent(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = root / "live-agents.json"
            config_path.write_text('{"agents": [{"agent_id": "agent-a", "command": ["fake"]}]}', encoding="utf-8")
            current_time = {"value": datetime(2026, 5, 17, 12, 0, tzinfo=UTC)}

            supervisor = LiveAgentProcessSupervisor(
                root,
                command_factory=lambda command, **kwargs: FakeProcess(pid=7327),
                now_fn=lambda: current_time["value"],
            )
            supervisor.start_group(
                config_path=config_path,
                server="http://room.local",
                group_id="crew",
                meeting_id="resident-m1",
                auto_restart=True,
                max_restarts=1,
                restart_backoff_seconds=10,
                stale_restart_after_seconds=60,
            )
            current_time["value"] += timedelta(seconds=61)
            connect_live_agent(
                root,
                {
                    "agent_id": "agent-a",
                    "display_name": "Agent A",
                    "status": "working",
                    "meeting_id": "resident-m2",
                },
                now=current_time["value"],
            )

            groups = supervisor.list_groups()
            events = _read_lifecycle_events(root)

        self.assertEqual(groups[0]["status"], "restarting")
        self.assertIsNone(groups[0]["pid"])
        self.assertEqual(groups[0]["returncode"], -98)
        self.assertEqual(groups[0]["restart_count"], 1)
        self.assertIn("wrong meeting manifest agent agent-a", groups[0]["last_error"])
        self.assertNotIn("resident-m2", groups[0]["last_error"])
        self.assertEqual([event["event_type"] for event in events], ["started", "stale_watchdog", "restart_scheduled"])

    def test_stale_watchdog_ignores_fresh_manifest_agent(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = root / "live-agents.json"
            config_path.write_text('{"agents": [{"agent_id": "agent-a", "command": ["fake"]}]}', encoding="utf-8")
            current_time = {"value": datetime(2026, 5, 17, 12, 0, tzinfo=UTC)}

            supervisor = LiveAgentProcessSupervisor(
                root,
                command_factory=lambda command, **kwargs: FakeProcess(pid=7330),
                now_fn=lambda: current_time["value"],
            )
            supervisor.start_group(
                config_path=config_path,
                server="http://room.local",
                group_id="crew",
                auto_restart=True,
                max_restarts=1,
                restart_backoff_seconds=10,
                stale_restart_after_seconds=60,
            )
            current_time["value"] += timedelta(seconds=70)
            heartbeat_live_agent(root, "agent-a", status="working", now=current_time["value"])
            groups = supervisor.list_groups()

        self.assertEqual(groups[0]["status"], "running")
        self.assertEqual(groups[0]["restart_count"], 0)

    def test_stale_watchdog_ignores_agent_meeting_id_when_group_has_no_meeting_owner(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = root / "live-agents.json"
            config_path.write_text('{"agents": [{"agent_id": "agent-a", "command": ["fake"]}]}', encoding="utf-8")
            current_time = {"value": datetime(2026, 5, 17, 12, 0, tzinfo=UTC)}

            supervisor = LiveAgentProcessSupervisor(
                root,
                command_factory=lambda command, **kwargs: FakeProcess(pid=7328),
                now_fn=lambda: current_time["value"],
            )
            supervisor.start_group(
                config_path=config_path,
                server="http://room.local",
                group_id="crew",
                auto_restart=True,
                max_restarts=1,
                restart_backoff_seconds=10,
                stale_restart_after_seconds=60,
            )
            current_time["value"] += timedelta(seconds=61)
            connect_live_agent(
                root,
                {
                    "agent_id": "agent-a",
                    "display_name": "Agent A",
                    "status": "working",
                    "meeting_id": "resident-m2",
                },
                now=current_time["value"],
            )

            groups = supervisor.list_groups()

        self.assertEqual(groups[0]["status"], "running")
        self.assertEqual(groups[0]["restart_count"], 0)

    def test_stale_watchdog_ignores_diagnostic_groups(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = root / "live-agents.json"
            config_path.write_text('{"agents": [{"agent_id": "agent-a", "command": ["fake"]}]}', encoding="utf-8")
            current_time = {"value": datetime(2026, 5, 17, 12, 0, tzinfo=UTC)}

            supervisor = LiveAgentProcessSupervisor(
                root,
                command_factory=lambda command, **kwargs: FakeProcess(pid=7332),
                now_fn=lambda: current_time["value"],
            )
            supervisor.start_group(
                config_path=config_path,
                server="http://room.local",
                group_id="crew",
                auto_restart=True,
                max_restarts=1,
                restart_backoff_seconds=10,
                stale_restart_after_seconds=60,
                diagnostic=True,
            )
            current_time["value"] += timedelta(seconds=61)
            groups = supervisor.list_groups()

        self.assertEqual(groups[0]["status"], "running")
        self.assertEqual(groups[0]["restart_count"], 0)

    def test_stale_watchdog_skips_corrupt_presence_file(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = root / "live-agents.json"
            config_path.write_text('{"agents": [{"agent_id": "agent-a", "command": ["fake"]}]}', encoding="utf-8")
            (root / "live_agents.json").write_text("{not valid json", encoding="utf-8")
            current_time = {"value": datetime(2026, 5, 17, 12, 0, tzinfo=UTC)}

            supervisor = LiveAgentProcessSupervisor(
                root,
                command_factory=lambda command, **kwargs: FakeProcess(pid=7335),
                now_fn=lambda: current_time["value"],
            )
            supervisor.start_group(
                config_path=config_path,
                server="http://room.local",
                group_id="crew",
                auto_restart=True,
                max_restarts=1,
                restart_backoff_seconds=10,
                stale_restart_after_seconds=60,
            )
            current_time["value"] += timedelta(seconds=61)
            groups = supervisor.list_groups()
            events = _read_lifecycle_events(root)

        self.assertEqual(groups[0]["status"], "running")
        self.assertEqual(groups[0]["restart_count"], 0)
        self.assertEqual([event["event_type"] for event in events], ["started"])

    def test_stale_watchdog_immediate_backoff_starts_one_fresh_replacement(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = root / "live-agents.json"
            config_path.write_text('{"agents": [{"agent_id": "agent-a", "command": ["fake"]}]}', encoding="utf-8")
            current_time = {"value": datetime(2026, 5, 17, 12, 0, tzinfo=UTC)}
            processes = []

            def command_factory(command, **kwargs):
                process = FakeProcess(pid=7345 + len(processes))
                processes.append(process)
                return process

            supervisor = LiveAgentProcessSupervisor(
                root,
                command_factory=command_factory,
                now_fn=lambda: current_time["value"],
            )
            supervisor.start_group(
                config_path=config_path,
                server="http://room.local",
                group_id="crew",
                auto_restart=True,
                max_restarts=2,
                restart_backoff_seconds=0,
                stale_restart_after_seconds=60,
            )
            current_time["value"] += timedelta(seconds=61)
            groups = supervisor.list_groups()
            events = _read_lifecycle_events(root)

        self.assertEqual(groups[0]["status"], "running")
        self.assertEqual(groups[0]["pid"], 7346)
        self.assertEqual(groups[0]["restart_count"], 1)
        self.assertEqual(len(processes), 2)
        self.assertEqual(
            [event["event_type"] for event in events],
            ["started", "stale_watchdog", "restart_scheduled", "started"],
        )

    def test_stale_watchdog_normalizes_fractional_threshold_to_whole_seconds(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = root / "live-agents.json"
            config_path.write_text('{"agents": [{"agent_id": "agent-a", "command": ["fake"]}]}', encoding="utf-8")
            supervisor = LiveAgentProcessSupervisor(root, command_factory=lambda command, **kwargs: FakeProcess())
            group = supervisor.start_group(
                config_path=config_path,
                server="http://room.local",
                group_id="crew",
                auto_restart=True,
                max_restarts=1,
                stale_restart_after_seconds=60.2,
            )

        self.assertEqual(group["stale_restart_after_seconds"], 61)

    def test_stale_watchdog_requires_auto_restart_budget(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = root / "live-agents.json"
            config_path.write_text('{"agents": [{"agent_id": "agent-a", "command": ["fake"]}]}', encoding="utf-8")
            supervisor = LiveAgentProcessSupervisor(root, command_factory=lambda command, **kwargs: FakeProcess())

            with self.assertRaisesRegex(ValueError, "stale watchdog requires auto_restart"):
                supervisor.start_group(
                    config_path=config_path,
                    server="http://room.local",
                    group_id="crew",
                    stale_restart_after_seconds=60,
                )

    def test_stale_watchdog_requires_positive_heartbeat_interval(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = root / "live-agents.json"
            config_path.write_text(
                '{"agents": [{"agent_id": "agent-a", "command": ["fake"], "heartbeat_interval": 0}]}',
                encoding="utf-8",
            )
            supervisor = LiveAgentProcessSupervisor(root, command_factory=lambda command, **kwargs: FakeProcess())

            with self.assertRaisesRegex(ValueError, "positive heartbeat_interval"):
                supervisor.start_group(
                    config_path=config_path,
                    server="http://room.local",
                    group_id="crew",
                    auto_restart=True,
                    max_restarts=1,
                    stale_restart_after_seconds=60,
                )

    def test_stale_watchdog_requires_threshold_above_runner_heartbeat_window(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = root / "live-agents.json"
            config_path.write_text(
                '{"agents": [{"agent_id": "agent-a", "command": ["fake"], "heartbeat_interval": 30, "poll_interval": 35}]}',
                encoding="utf-8",
            )
            supervisor = LiveAgentProcessSupervisor(root, command_factory=lambda command, **kwargs: FakeProcess())

            with self.assertRaisesRegex(ValueError, "greater than heartbeat_interval \\+ poll_interval"):
                supervisor.start_group(
                    config_path=config_path,
                    server="http://room.local",
                    group_id="crew",
                    auto_restart=True,
                    max_restarts=1,
                    stale_restart_after_seconds=60,
                )

    def test_stale_watchdog_stop_failure_does_not_repeat_watchdog_events(self):
        class StubbornProcess:
            pid = 7340
            returncode = None

            def __init__(self):
                self.signals = []
                self.terminated = False
                self.killed = False

            def poll(self):
                return None

            def send_signal(self, signum):
                self.signals.append(signum)

            def terminate(self):
                self.terminated = True

            def kill(self):
                self.killed = True

            def wait(self, timeout=None):
                raise subprocess.TimeoutExpired("fake", timeout)

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = root / "live-agents.json"
            config_path.write_text('{"agents": [{"agent_id": "agent-a", "command": ["fake"]}]}', encoding="utf-8")
            current_time = {"value": datetime(2026, 5, 17, 12, 0, tzinfo=UTC)}
            process = StubbornProcess()
            supervisor = LiveAgentProcessSupervisor(
                root,
                command_factory=lambda command, **kwargs: process,
                now_fn=lambda: current_time["value"],
            )
            supervisor.start_group(
                config_path=config_path,
                server="http://room.local",
                group_id="crew",
                auto_restart=True,
                max_restarts=1,
                restart_backoff_seconds=10,
                stale_restart_after_seconds=60,
            )
            current_time["value"] += timedelta(seconds=61)
            first_groups = supervisor.list_groups()
            second_groups = supervisor.list_groups()
            events = _read_lifecycle_events(root)

        self.assertEqual(first_groups[0]["status"], "error")
        self.assertEqual(second_groups[0]["status"], "error")
        self.assertIn("Stale watchdog failed to stop group", first_groups[0]["last_error"])
        self.assertEqual(
            [event["event_type"] for event in events],
            ["started", "stale_watchdog_stop_failed"],
        )
        self.assertEqual(events[1]["reason"], "missing manifest agent agent-a")

    def test_auto_restart_failure_writes_restart_failed_lifecycle_event(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = root / "live-agents.json"
            config_path.write_text('{"agents": [{"agent_id": "a", "command": ["fake"]}]}', encoding="utf-8")
            reports = {
                "current": {"status": "ok", "checks": [], "agents": []},
            }
            processes = []

            def preflight_checker(path, *, server_override=None):
                return reports["current"]

            def command_factory(command, **kwargs):
                process = FakeProcess(pid=7300 + len(processes))
                processes.append(process)
                return process

            supervisor = LiveAgentProcessSupervisor(
                root,
                command_factory=command_factory,
                preflight_checker=preflight_checker,
            )
            supervisor.start_group(
                config_path=config_path,
                server="http://room.local",
                group_id="crew",
                auto_restart=True,
                max_restarts=1,
                restart_backoff_seconds=0,
            )
            connect_live_agent(root, {"agent_id": "a", "status": "online"})
            reports["current"] = {
                "status": "failed",
                "checks": [],
                "agents": [
                    {
                        "agent_id": "bad-agent",
                        "checks": [
                            {
                                "id": "command",
                                "status": "failed",
                                "message": "Command not found: missing",
                            }
                        ],
                    }
                ],
            }
            processes[0].returncode = 2

            groups = supervisor.list_groups()
            events = _read_lifecycle_events(root)

        self.assertEqual([event["event_type"] for event in events], ["started", "restart_scheduled", "restart_failed"])
        self.assertEqual(events[1]["offline"]["offline_agent_ids"], ["a"])
        self.assertEqual(events[-1]["offline"]["offline_agent_ids"], ["a"])
        self.assertEqual(events[-1]["status"], "error")
        self.assertEqual(events[-1]["returncode"], 2)
        self.assertEqual(groups[0]["recent_events"], events)

    def test_process_exit_lifecycle_event_is_not_duplicated_by_repeated_polling(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = root / "live-agents.json"
            config_path.write_text('{"agents": [{"agent_id": "a", "command": ["fake"]}]}', encoding="utf-8")
            process = FakeProcess(pid=4321)
            supervisor = LiveAgentProcessSupervisor(root, command_factory=lambda command, **kwargs: process)
            supervisor.start_group(config_path=config_path, server="http://room.local", group_id="crew")
            process.returncode = 2

            first = supervisor.list_groups()
            second = supervisor.list_groups()
            events = _read_lifecycle_events(root)

        self.assertEqual([event["event_type"] for event in events], ["started", "error"])
        self.assertEqual(events[1]["returncode"], 2)
        self.assertEqual(first[0]["recent_events"], events)
        self.assertEqual(second[0]["recent_events"], events)

    def test_recovered_unknown_lifecycle_event_is_written_once(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            runs_dir = root / "live-agent-runs"
            runs_dir.mkdir()
            (runs_dir / "processes.json").write_text(
                json.dumps(
                    {
                        "groups": [
                            {
                                "group_id": "orphan-running",
                                "status": "running",
                                "pid": 1111,
                                "config_path": "configs/live-agents.example.json",
                                "server": "http://room.local",
                                "log_path": str(runs_dir / "orphan-running.log"),
                                "started_at": "2026-05-17T12:00:00+00:00",
                                "stopped_at": "",
                                "returncode": None,
                                "last_error": "",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )

            recovered = LiveAgentProcessSupervisor(
                root,
                now_fn=lambda: datetime(2026, 5, 17, 12, 2, tzinfo=UTC),
            ).list_groups()
            second_recovery = LiveAgentProcessSupervisor(
                root,
                now_fn=lambda: datetime(2026, 5, 17, 12, 3, tzinfo=UTC),
            ).list_groups()
            events = _read_lifecycle_events(root)

        self.assertEqual(recovered[0]["status"], "unknown")
        self.assertEqual([event["event_type"] for event in events], ["recovered_unknown"])
        self.assertEqual(events[0]["timestamp"], "2026-05-17T12:02:00+00:00")
        self.assertEqual(events[0]["status"], "unknown")
        self.assertIsNone(events[0]["pid"])
        self.assertEqual(second_recovery[0]["recent_events"], events)

    def test_new_supervisor_recovers_historical_records_and_marks_orphan_running_unknown(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            runs_dir = root / "live-agent-runs"
            runs_dir.mkdir()
            (runs_dir / "processes.json").write_text(
                json.dumps(
                    {
                        "groups": [
                            {
                                "group_id": "orphan-running",
                                "status": "running",
                                "pid": 1111,
                                "config_path": "configs/live-agents.example.json",
                                "server": "http://room.local",
                                "log_path": str(runs_dir / "orphan-running.log"),
                                "started_at": "2026-05-17T12:00:00+00:00",
                                "stopped_at": "",
                                "returncode": None,
                                "last_error": "",
                            },
                            {
                                "group_id": "finished",
                                "status": "stopped",
                                "pid": 2222,
                                "config_path": "configs/live-agents.example.json",
                                "server": "http://room.local",
                                "log_path": str(runs_dir / "finished.log"),
                                "started_at": "2026-05-17T12:00:00+00:00",
                                "stopped_at": "2026-05-17T12:01:00+00:00",
                                "returncode": 0,
                                "last_error": "",
                            },
                            {
                                "group_id": "crashed",
                                "status": "error",
                                "pid": 3333,
                                "config_path": "configs/live-agents.example.json",
                                "server": "http://room.local",
                                "log_path": str(runs_dir / "crashed.log"),
                                "started_at": "2026-05-17T12:00:00+00:00",
                                "stopped_at": "2026-05-17T12:01:00+00:00",
                                "returncode": 2,
                                "last_error": "",
                            },
                        ]
                    }
                ),
                encoding="utf-8",
            )

            recovered = {record["group_id"]: record for record in LiveAgentProcessSupervisor(root).list_groups()}
            persisted = json.loads((runs_dir / "processes.json").read_text(encoding="utf-8"))
            persisted_by_id = {record["group_id"]: record for record in persisted["groups"]}

        self.assertEqual(recovered["orphan-running"]["status"], "unknown")
        self.assertIsNone(recovered["orphan-running"]["pid"])
        self.assertEqual(recovered["orphan-running"]["agents"], [])
        self.assertEqual(recovered["orphan-running"]["recent_events"][0]["event_type"], "recovered_unknown")
        self.assertIsNone(persisted_by_id["orphan-running"]["pid"])
        self.assertEqual(recovered["finished"]["status"], "stopped")
        self.assertEqual(recovered["finished"]["pid"], 2222)
        self.assertEqual(recovered["finished"]["agents"], [])
        self.assertEqual(recovered["finished"]["recent_events"], [])
        self.assertEqual(persisted_by_id["finished"]["pid"], 2222)
        self.assertEqual(recovered["crashed"]["status"], "error")
        self.assertEqual(recovered["crashed"]["pid"], 3333)
        self.assertEqual(recovered["crashed"]["agents"], [])
        self.assertEqual(recovered["crashed"]["recent_events"], [])
        self.assertEqual(persisted_by_id["crashed"]["pid"], 3333)

    def test_persisted_invalid_process_status_is_reported_unknown(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            runs_dir = root / "live-agent-runs"
            runs_dir.mkdir()
            (runs_dir / "processes.json").write_text(
                json.dumps(
                    {
                        "groups": [
                            {
                                "group_id": "crew",
                                "status": "running/../../secret",
                                "pid": 4444,
                                "config_path": "configs/live-agents.example.json",
                                "server": "http://room.local",
                                "log_path": str(runs_dir / "crew.log"),
                                "started_at": "2026-05-17T12:00:00+00:00",
                                "stopped_at": "",
                                "returncode": None,
                                "last_error": "",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )

            group = LiveAgentProcessSupervisor(root).snapshot_groups()[0]

        self.assertEqual(group["status"], "unknown")

    def test_persisted_invalid_process_numbers_are_reported_unknown(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            runs_dir = root / "live-agent-runs"
            runs_dir.mkdir()
            (runs_dir / "processes.json").write_text(
                json.dumps(
                    {
                        "groups": [
                            {
                                "group_id": "crew",
                                "status": "error",
                                "pid": -4444,
                                "config_path": "configs/live-agents.example.json",
                                "server": "http://room.local",
                                "log_path": str(runs_dir / "crew.log"),
                                "started_at": "2026-05-17T12:00:00+00:00",
                                "stopped_at": "2026-05-17T12:01:00+00:00",
                                "returncode": True,
                                "last_error": "",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )

            group = LiveAgentProcessSupervisor(root).snapshot_groups()[0]

        self.assertIsNone(group["pid"])
        self.assertIsNone(group["returncode"])

    def test_list_groups_includes_bounded_log_tail_without_persisting_it(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            runs_dir = root / "live-agent-runs"
            runs_dir.mkdir()
            log_path = runs_dir / "crew.log"
            log_path.write_text("a" * 5000 + "final clue", encoding="utf-8")
            (runs_dir / "processes.json").write_text(
                json.dumps(
                    {
                        "groups": [
                            {
                                "group_id": "crew",
                                "status": "error",
                                "pid": 1111,
                                "config_path": "configs/live-agents.example.json",
                                "server": "http://room.local",
                                "log_path": str(log_path),
                                "started_at": "2026-05-17T12:00:00+00:00",
                                "stopped_at": "2026-05-17T12:01:00+00:00",
                                "returncode": 2,
                                "last_error": "",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )

            groups = LiveAgentProcessSupervisor(root, log_tail_bytes=128).list_groups()
            persisted = json.loads((runs_dir / "processes.json").read_text(encoding="utf-8"))

        self.assertIn("final clue", groups[0]["log_tail"])
        self.assertLessEqual(len(groups[0]["log_tail"]), 128)
        self.assertNotIn("log_tail", persisted["groups"][0])

    def test_list_groups_redacts_sensitive_log_tail_without_persisting_it(self):
        sensitive_tails = [
            "safe boot line\ntoken=secret-token http://friend.local/private/live-agents.json",
            "Authorization: Bearer bridge-token",
            "password=hunter2",
            "env:API_KEY",
            "$API_KEY",
            "${API_KEY}",
            "dotenv file .env",
            "loading configs/live-agents.toml",
            "C:\\Users\\friend\\live-agents.toml",
            "--api-key secret-token",
        ]
        for log_tail in sensitive_tails:
            with self.subTest(log_tail=log_tail):
                with tempfile.TemporaryDirectory() as temp_dir:
                    root = Path(temp_dir)
                    runs_dir = root / "live-agent-runs"
                    runs_dir.mkdir()
                    log_path = runs_dir / "crew.log"
                    log_path.write_text(log_tail, encoding="utf-8")
                    (runs_dir / "processes.json").write_text(
                        json.dumps(
                            {
                                "groups": [
                                    {
                                        "group_id": "crew",
                                        "status": "error",
                                        "pid": 1111,
                                        "config_path": "configs/live-agents.example.json",
                                        "server": "http://room.local",
                                        "log_path": str(log_path),
                                        "started_at": "2026-05-17T12:00:00+00:00",
                                        "stopped_at": "2026-05-17T12:01:00+00:00",
                                        "returncode": 2,
                                        "last_error": "",
                                    }
                                ]
                            }
                        ),
                        encoding="utf-8",
                    )

                    groups = LiveAgentProcessSupervisor(root, log_tail_bytes=512).list_groups()
                    persisted = json.loads((runs_dir / "processes.json").read_text(encoding="utf-8"))
                    raw_log = log_path.read_text(encoding="utf-8")

                self.assertEqual(groups[0]["log_tail"], "log tail redacted.")
                self.assertNotIn(log_tail, groups[0]["log_tail"])
                self.assertNotIn("safe boot line", groups[0]["log_tail"])
                self.assertEqual(raw_log, log_tail)
                self.assertNotIn("log_tail", persisted["groups"][0])

    def test_snapshot_groups_does_not_start_due_auto_restarts(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            runs_dir = root / "live-agent-runs"
            runs_dir.mkdir()
            config_path = root / "live-agents.json"
            config_path.write_text('{"agents": [{"agent_id": "a", "command": ["fake"]}]}', encoding="utf-8")
            (runs_dir / "processes.json").write_text(
                json.dumps(
                    {
                        "groups": [
                            {
                                "group_id": "crew",
                                "status": "restarting",
                                "pid": None,
                                "config_path": str(config_path),
                                "server": "http://room.local",
                                "log_path": str(runs_dir / "crew.log"),
                                "started_at": "2026-05-17T12:00:00+00:00",
                                "stopped_at": "2026-05-17T12:01:00+00:00",
                                "returncode": 2,
                                "last_error": "waiting to restart",
                                "auto_restart": True,
                                "restart_count": 1,
                                "max_restarts": 3,
                                "restart_backoff_seconds": 0,
                                "next_restart_at": "2026-05-17T12:01:00+00:00",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            launches = []
            supervisor = LiveAgentProcessSupervisor(
                root,
                command_factory=lambda command, **kwargs: launches.append(command) or FakeProcess(pid=2468),
                now_fn=lambda: datetime(2026, 5, 17, 12, 2, tzinfo=UTC),
            )

            snapshot = supervisor.snapshot_groups()

        self.assertEqual(snapshot[0]["status"], "restarting")
        self.assertEqual(snapshot[0]["pid"], None)
        self.assertEqual(launches, [])

    def test_close_does_not_rewrite_already_stopped_process_records(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            process = FakeProcess(pid=1234)
            current_time = {"value": datetime(2026, 5, 17, 12, 0, tzinfo=UTC)}
            supervisor = LiveAgentProcessSupervisor(
                root,
                command_factory=lambda command, **kwargs: process,
                now_fn=lambda: current_time["value"],
            )
            config_path = root / "live-agents.json"
            config_path.write_text('{"agents": [{"agent_id": "a", "command": ["fake"]}]}', encoding="utf-8")

            supervisor.start_group(config_path=config_path, server="http://room.local", group_id="crew")
            current_time["value"] = datetime(2026, 5, 17, 12, 1, tzinfo=UTC)
            supervisor.stop_group("crew")
            state_path = root / "live-agent-runs" / "processes.json"
            persisted_stop = json.loads(state_path.read_text(encoding="utf-8"))
            current_time["value"] = datetime(2026, 5, 17, 12, 2, tzinfo=UTC)
            supervisor.close()
            persisted_close = json.loads(state_path.read_text(encoding="utf-8"))

        self.assertEqual(persisted_close["groups"][0]["stopped_at"], persisted_stop["groups"][0]["stopped_at"])

    def test_restart_group_relaunches_from_persisted_record(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = root / "live-agents.json"
            config_path.write_text(
                json.dumps(
                    {
                        "agents": [
                            {
                                "agent_id": "a",
                                "display_name": "Agent A",
                                "provider_kind": "local_cli",
                                "connection_kind": "local_cli",
                                "command": ["fake"],
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            launched = []

            def command_factory(command, **kwargs):
                process = FakeProcess(pid=5000 + len(launched))
                launched.append((command, process))
                return process

            supervisor = LiveAgentProcessSupervisor(
                root,
                command_factory=command_factory,
                now_fn=lambda: datetime(2026, 5, 17, 12, 0, tzinfo=UTC),
            )
            supervisor.start_group(config_path=config_path, server="http://room.local", group_id="crew")
            supervisor.stop_group("crew")

            restarted = supervisor.restart_group("crew")
            persisted = json.loads((root / "live-agent-runs" / "processes.json").read_text(encoding="utf-8"))

        self.assertEqual(restarted["group_id"], "crew")
        self.assertEqual(restarted["status"], "running")
        self.assertEqual(restarted["pid"], 5001)
        self.assertEqual(restarted["config_path"], str(config_path))
        self.assertEqual(restarted["server"], "http://room.local")
        self.assertEqual(
            restarted["agents"],
            [
                {
                    "agent_id": "a",
                    "display_name": "Agent A",
                    "provider_kind": "local_cli",
                    "connection_kind": "local_cli",
                }
            ],
        )
        self.assertEqual(persisted["groups"][0]["agents"], restarted["agents"])
        self.assertEqual(len(launched), 2)

    def test_restart_group_resets_auto_restart_budget_for_new_manual_run(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = root / "live-agents.json"
            config_path.write_text('{"agents": [{"agent_id": "a", "command": ["fake"]}]}', encoding="utf-8")
            processes = []

            def command_factory(command, **kwargs):
                process = FakeProcess(pid=5100 + len(processes))
                processes.append(process)
                return process

            supervisor = LiveAgentProcessSupervisor(root, command_factory=command_factory)
            supervisor.start_group(
                config_path=config_path,
                server="http://room.local",
                group_id="crew",
                auto_restart=True,
                max_restarts=1,
                restart_backoff_seconds=0,
            )
            processes[0].returncode = 2
            supervisor.list_groups()
            processes[1].returncode = 2
            exhausted = supervisor.list_groups()

            manually_restarted = supervisor.restart_group("crew")
            processes[2].returncode = 2
            after_manual_crash = supervisor.list_groups()

        self.assertEqual(exhausted[0]["status"], "error")
        self.assertEqual(exhausted[0]["restart_count"], 1)
        self.assertEqual(manually_restarted["status"], "running")
        self.assertEqual(manually_restarted["auto_restart"], True)
        self.assertEqual(manually_restarted["max_restarts"], 1)
        self.assertEqual(manually_restarted["restart_backoff_seconds"], 0.0)
        self.assertEqual(manually_restarted["restart_count"], 0)
        self.assertEqual(after_manual_crash[0]["status"], "running")
        self.assertEqual(after_manual_crash[0]["pid"], 5103)
        self.assertEqual(after_manual_crash[0]["restart_count"], 1)
        self.assertEqual(len(processes), 4)

    def test_restart_group_refuses_owned_running_group(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = root / "live-agents.json"
            config_path.write_text('{"agents": [{"agent_id": "a", "command": ["fake"]}]}', encoding="utf-8")
            supervisor = LiveAgentProcessSupervisor(root, command_factory=lambda command, **kwargs: FakeProcess())
            supervisor.start_group(config_path=config_path, server="http://room.local", group_id="crew")

            with self.assertRaises(ValueError):
                supervisor.restart_group("crew")

    def test_restart_group_relaunches_unknown_historical_record(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            runs_dir = root / "live-agent-runs"
            runs_dir.mkdir()
            config_path = root / "live-agents.json"
            config_path.write_text(
                json.dumps(
                    {
                        "agents": [
                            {
                                "agent_id": "a",
                                "display_name": "Agent A",
                                "provider_kind": "local_cli",
                                "connection_kind": "local_cli",
                                "command": ["fake"],
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            (runs_dir / "processes.json").write_text(
                json.dumps(
                    {
                        "groups": [
                            {
                                "group_id": "crew",
                                "status": "running",
                                "pid": 1111,
                                "config_path": str(config_path),
                                "server": "http://room.local",
                                "log_path": str(runs_dir / "crew.log"),
                                "started_at": "2026-05-17T12:00:00+00:00",
                                "stopped_at": "",
                                "returncode": None,
                                "last_error": "",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            launched = []

            def command_factory(command, **kwargs):
                launched.append(command)
                return FakeProcess(pid=6789)

            supervisor = LiveAgentProcessSupervisor(root, command_factory=command_factory)

            restarted = supervisor.restart_group("crew")
            persisted = json.loads((runs_dir / "processes.json").read_text(encoding="utf-8"))

        self.assertEqual(restarted["status"], "running")
        self.assertEqual(restarted["pid"], 6789)
        self.assertEqual(restarted["config_path"], str(config_path))
        self.assertEqual(restarted["server"], "http://room.local")
        self.assertEqual(
            restarted["agents"],
            [
                {
                    "agent_id": "a",
                    "display_name": "Agent A",
                    "provider_kind": "local_cli",
                    "connection_kind": "local_cli",
                }
            ],
        )
        self.assertEqual(persisted["groups"][0]["agents"], restarted["agents"])
        self.assertEqual(len(launched), 1)

    def test_restart_group_refuses_blank_persisted_config_before_preflight(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            runs_dir = root / "live-agent-runs"
            runs_dir.mkdir()
            state_path = runs_dir / "processes.json"
            state_path.write_text(
                json.dumps(
                    {
                        "groups": [
                            {
                                "group_id": "crew",
                                "status": "stopped",
                                "pid": None,
                                "config_path": "",
                                "server": "http://room.local",
                                "log_path": str(runs_dir / "crew.log"),
                                "started_at": "2026-05-17T12:00:00+00:00",
                                "stopped_at": "2026-05-17T12:01:00+00:00",
                                "returncode": 0,
                                "last_error": "",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            original_state = state_path.read_text(encoding="utf-8")
            preflights = []
            launches = []

            def preflight_checker(path, *, server_override=None):
                preflights.append((path, server_override))
                return {"status": "ok", "checks": [], "agents": []}

            supervisor = LiveAgentProcessSupervisor(
                root,
                command_factory=lambda command, **kwargs: launches.append(command) or FakeProcess(pid=6789),
                preflight_checker=preflight_checker,
            )

            with self.assertRaisesRegex(ValueError, "has no config to restart"):
                supervisor.restart_group("crew")

            self.assertEqual(preflights, [])
            self.assertEqual(launches, [])
            self.assertEqual(state_path.read_text(encoding="utf-8"), original_state)
            self.assertFalse((runs_dir / "events.jsonl").exists())

    def test_restart_group_refuses_blank_persisted_server_before_preflight(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            runs_dir = root / "live-agent-runs"
            runs_dir.mkdir()
            config_path = root / "live-agents.json"
            config_path.write_text('{"agents": [{"agent_id": "a", "command": ["fake"]}]}', encoding="utf-8")
            state_path = runs_dir / "processes.json"
            state_path.write_text(
                json.dumps(
                    {
                        "groups": [
                            {
                                "group_id": "crew",
                                "status": "stopped",
                                "pid": None,
                                "config_path": str(config_path),
                                "server": "   ",
                                "log_path": str(runs_dir / "crew.log"),
                                "started_at": "2026-05-17T12:00:00+00:00",
                                "stopped_at": "2026-05-17T12:01:00+00:00",
                                "returncode": 0,
                                "last_error": "",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            original_state = state_path.read_text(encoding="utf-8")
            preflights = []
            launches = []

            def preflight_checker(path, *, server_override=None):
                preflights.append((path, server_override))
                return {"status": "ok", "checks": [], "agents": []}

            supervisor = LiveAgentProcessSupervisor(
                root,
                command_factory=lambda command, **kwargs: launches.append(command) or FakeProcess(pid=6789),
                preflight_checker=preflight_checker,
            )

            with self.assertRaisesRegex(ValueError, "has no server to restart"):
                supervisor.restart_group("crew")

            self.assertEqual(preflights, [])
            self.assertEqual(launches, [])
            self.assertEqual(state_path.read_text(encoding="utf-8"), original_state)
            self.assertFalse((runs_dir / "events.jsonl").exists())

    def test_recover_group_relaunches_unknown_historical_record_with_recovery_evidence(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            runs_dir = root / "live-agent-runs"
            runs_dir.mkdir()
            config_path = root / "live-agents.json"
            config_path.write_text(
                json.dumps(
                    {
                        "agents": [
                            {
                                "agent_id": "a",
                                "display_name": "Agent A",
                                "provider_kind": "local_cli",
                                "connection_kind": "local_cli",
                                "command": ["fake"],
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            (runs_dir / "processes.json").write_text(
                json.dumps(
                    {
                        "groups": [
                            {
                                "group_id": "crew",
                                "status": "running",
                                "pid": 1111,
                                "config_path": str(config_path),
                                "server": "http://room.local",
                                "log_path": str(runs_dir / "crew.log"),
                                "started_at": "2026-05-17T12:00:00+00:00",
                                "stopped_at": "",
                                "returncode": None,
                                "last_error": "",
                                "auto_restart": True,
                                "restart_count": 1,
                                "max_restarts": 2,
                                "restart_backoff_seconds": 0,
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            launched = []

            def command_factory(command, **kwargs):
                launched.append(command)
                return FakeProcess(pid=6789)

            supervisor = LiveAgentProcessSupervisor(
                root,
                command_factory=command_factory,
                now_fn=lambda: datetime(2026, 5, 17, 12, 2, tzinfo=UTC),
            )

            recovered = supervisor.recover_group("crew")
            persisted = json.loads((runs_dir / "processes.json").read_text(encoding="utf-8"))
            events = _read_lifecycle_events(root)

        self.assertEqual(recovered["status"], "running")
        self.assertEqual(recovered["pid"], 6789)
        self.assertEqual(recovered["recovered_from_status"], "unknown")
        self.assertEqual(recovered["restart_count"], 0)
        self.assertEqual(recovered["auto_restart"], True)
        self.assertEqual(recovered["max_restarts"], 2)
        self.assertEqual(persisted["groups"][0]["recovered_from_status"], "unknown")
        self.assertEqual([event["event_type"] for event in events], ["recovered_unknown", "recovered"])
        self.assertEqual(events[-1]["status"], "running")
        self.assertEqual(events[-1]["previous_status"], "unknown")
        self.assertEqual(len(launched), 1)

    def test_recover_group_refuses_blank_persisted_config_before_preflight(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            runs_dir = root / "live-agent-runs"
            runs_dir.mkdir()
            state_path = runs_dir / "processes.json"
            state_path.write_text(
                json.dumps(
                    {
                        "groups": [
                            {
                                "group_id": "crew",
                                "status": "error",
                                "pid": None,
                                "config_path": "   ",
                                "server": "http://room.local",
                                "log_path": str(runs_dir / "crew.log"),
                                "started_at": "2026-05-17T12:00:00+00:00",
                                "stopped_at": "2026-05-17T12:01:00+00:00",
                                "returncode": 1,
                                "last_error": "old crash",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            original_state = state_path.read_text(encoding="utf-8")
            preflights = []
            launches = []

            def preflight_checker(path, *, server_override=None):
                preflights.append((path, server_override))
                return {"status": "ok", "checks": [], "agents": []}

            supervisor = LiveAgentProcessSupervisor(
                root,
                command_factory=lambda command, **kwargs: launches.append(command) or FakeProcess(pid=6789),
                preflight_checker=preflight_checker,
            )

            with self.assertRaisesRegex(ValueError, "has no config to recover"):
                supervisor.recover_group("crew")

            self.assertEqual(preflights, [])
            self.assertEqual(launches, [])
            self.assertEqual(state_path.read_text(encoding="utf-8"), original_state)
            self.assertFalse((runs_dir / "events.jsonl").exists())

    def test_recover_group_refuses_blank_persisted_server_before_preflight(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            runs_dir = root / "live-agent-runs"
            runs_dir.mkdir()
            config_path = root / "live-agents.json"
            config_path.write_text('{"agents": [{"agent_id": "a", "command": ["fake"]}]}', encoding="utf-8")
            state_path = runs_dir / "processes.json"
            state_path.write_text(
                json.dumps(
                    {
                        "groups": [
                            {
                                "group_id": "crew",
                                "status": "error",
                                "pid": None,
                                "config_path": str(config_path),
                                "server": "   ",
                                "log_path": str(runs_dir / "crew.log"),
                                "started_at": "2026-05-17T12:00:00+00:00",
                                "stopped_at": "2026-05-17T12:01:00+00:00",
                                "returncode": 1,
                                "last_error": "old crash",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            original_state = state_path.read_text(encoding="utf-8")
            preflights = []
            launches = []

            def preflight_checker(path, *, server_override=None):
                preflights.append((path, server_override))
                return {"status": "ok", "checks": [], "agents": []}

            supervisor = LiveAgentProcessSupervisor(
                root,
                command_factory=lambda command, **kwargs: launches.append(command) or FakeProcess(pid=6789),
                preflight_checker=preflight_checker,
            )

            with self.assertRaisesRegex(ValueError, "has no server to recover"):
                supervisor.recover_group("crew")

            self.assertEqual(preflights, [])
            self.assertEqual(launches, [])
            self.assertEqual(state_path.read_text(encoding="utf-8"), original_state)
            self.assertFalse((runs_dir / "events.jsonl").exists())

    def test_recover_group_refuses_owned_running_group(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = root / "live-agents.json"
            config_path.write_text('{"agents": [{"agent_id": "a", "command": ["fake"]}]}', encoding="utf-8")
            supervisor = LiveAgentProcessSupervisor(root, command_factory=lambda command, **kwargs: FakeProcess())
            supervisor.start_group(config_path=config_path, server="http://room.local", group_id="crew")

            with self.assertRaises(ValueError):
                supervisor.recover_group("crew")

    def test_recover_group_refuses_intentionally_stopped_group(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            runs_dir = root / "live-agent-runs"
            runs_dir.mkdir()
            config_path = root / "live-agents.json"
            config_path.write_text('{"agents": [{"agent_id": "a", "command": ["fake"]}]}', encoding="utf-8")
            (runs_dir / "processes.json").write_text(
                json.dumps(
                    {
                        "groups": [
                            {
                                "group_id": "crew",
                                "status": "stopped",
                                "pid": None,
                                "config_path": str(config_path),
                                "server": "http://room.local",
                                "log_path": str(runs_dir / "crew.log"),
                                "started_at": "2026-05-17T12:00:00+00:00",
                                "stopped_at": "2026-05-17T12:01:00+00:00",
                                "returncode": 0,
                                "last_error": "",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            launched = []
            supervisor = LiveAgentProcessSupervisor(
                root,
                command_factory=lambda command, **kwargs: launched.append(command) or FakeProcess(pid=6789),
            )

            with self.assertRaisesRegex(ValueError, "is stopped; use restart"):
                supervisor.recover_group("crew")

        self.assertEqual(launched, [])

    def test_auto_restart_relaunches_crashed_group_within_limit(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = root / "live-agents.json"
            config_path.write_text('{"agents": [{"agent_id": "a", "command": ["fake"]}]}', encoding="utf-8")
            processes = []

            def command_factory(command, **kwargs):
                process = FakeProcess(pid=7000 + len(processes))
                processes.append(process)
                return process

            supervisor = LiveAgentProcessSupervisor(
                root,
                command_factory=command_factory,
                now_fn=lambda: datetime(2026, 5, 17, 12, 0, tzinfo=UTC),
            )
            supervisor.start_group(
                config_path=config_path,
                server="http://room.local",
                group_id="crew",
                auto_restart=True,
                max_restarts=2,
                restart_backoff_seconds=0,
            )
            processes[0].returncode = 2

            groups = supervisor.list_groups()
            persisted = json.loads((root / "live-agent-runs" / "processes.json").read_text(encoding="utf-8"))

        self.assertEqual(groups[0]["status"], "running")
        self.assertEqual(groups[0]["pid"], 7001)
        self.assertEqual(groups[0]["auto_restart"], True)
        self.assertEqual(groups[0]["restart_count"], 1)
        self.assertEqual(groups[0]["max_restarts"], 2)
        self.assertIn("return code 2", groups[0]["last_error"])
        self.assertEqual(persisted["groups"][0]["restart_count"], 1)
        self.assertEqual(len(processes), 2)

    def test_monitor_restarts_crashed_group_without_list_polling(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = root / "live-agents.json"
            config_path.write_text('{"agents": [{"agent_id": "a", "command": ["fake"]}]}', encoding="utf-8")
            processes = []

            def command_factory(command, **kwargs):
                process = FakeProcess(pid=7100 + len(processes))
                processes.append(process)
                return process

            supervisor = LiveAgentProcessSupervisor(root, command_factory=command_factory)
            try:
                supervisor.start_group(
                    config_path=config_path,
                    server="http://room.local",
                    group_id="crew",
                    auto_restart=True,
                    max_restarts=1,
                    restart_backoff_seconds=0,
                )
                processes[0].returncode = 2

                supervisor.start_monitor(interval_seconds=0.01)
                deadline = time.monotonic() + 1
                while len(processes) < 2 and time.monotonic() < deadline:
                    time.sleep(0.01)
                snapshot = supervisor.snapshot_groups()
            finally:
                supervisor.stop_monitor()
                supervisor.close()

        self.assertEqual(len(processes), 2)
        self.assertEqual(snapshot[0]["status"], "running")
        self.assertEqual(snapshot[0]["pid"], 7101)
        self.assertEqual(snapshot[0]["restart_count"], 1)

    def test_auto_restart_waits_for_backoff_before_relaunching(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = root / "live-agents.json"
            config_path.write_text('{"agents": [{"agent_id": "a", "command": ["fake"]}]}', encoding="utf-8")
            current_time = {"value": datetime(2026, 5, 17, 12, 0, tzinfo=UTC)}
            processes = []

            def command_factory(command, **kwargs):
                process = FakeProcess(pid=8000 + len(processes))
                processes.append(process)
                return process

            supervisor = LiveAgentProcessSupervisor(
                root,
                command_factory=command_factory,
                now_fn=lambda: current_time["value"],
            )
            supervisor.start_group(
                config_path=config_path,
                server="http://room.local",
                group_id="crew",
                auto_restart=True,
                max_restarts=1,
                restart_backoff_seconds=10,
            )
            processes[0].returncode = 2

            waiting = supervisor.list_groups()
            current_time["value"] += timedelta(seconds=11)
            restarted = supervisor.list_groups()

        self.assertEqual(waiting[0]["status"], "restarting")
        self.assertIsNone(waiting[0]["pid"])
        self.assertEqual(waiting[0]["restart_count"], 1)
        self.assertEqual(waiting[0]["next_restart_at"], "2026-05-17T12:00:10+00:00")
        self.assertEqual(len(processes), 2)
        self.assertEqual(restarted[0]["status"], "running")
        self.assertEqual(restarted[0]["pid"], 8001)

    def test_monitor_starts_due_backoff_restart_without_list_polling(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = root / "live-agents.json"
            config_path.write_text('{"agents": [{"agent_id": "a", "command": ["fake"]}]}', encoding="utf-8")
            current_time = {"value": datetime(2026, 5, 17, 12, 0, tzinfo=UTC)}
            processes = []

            def command_factory(command, **kwargs):
                process = FakeProcess(pid=8300 + len(processes))
                processes.append(process)
                return process

            supervisor = LiveAgentProcessSupervisor(root, command_factory=command_factory, now_fn=lambda: current_time["value"])
            try:
                supervisor.start_group(
                    config_path=config_path,
                    server="http://room.local",
                    group_id="crew",
                    auto_restart=True,
                    max_restarts=1,
                    restart_backoff_seconds=30,
                )
                processes[0].returncode = 2
                supervisor.start_monitor(interval_seconds=0.01)
                deadline = time.monotonic() + 1
                while supervisor.snapshot_groups()[0]["status"] != "restarting" and time.monotonic() < deadline:
                    time.sleep(0.01)
                waiting = supervisor.snapshot_groups()
                current_time["value"] += timedelta(seconds=31)
                deadline = time.monotonic() + 1
                while len(processes) < 2 and time.monotonic() < deadline:
                    time.sleep(0.01)
                restarted = supervisor.snapshot_groups()
            finally:
                supervisor.stop_monitor()
                supervisor.close()

        self.assertEqual(waiting[0]["status"], "restarting")
        self.assertEqual(waiting[0]["next_restart_at"], "2026-05-17T12:00:30+00:00")
        self.assertEqual(len(processes), 2)
        self.assertEqual(restarted[0]["status"], "running")
        self.assertEqual(restarted[0]["pid"], 8301)

    def test_monitor_snapshot_records_successful_tick_liveness(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            supervisor = LiveAgentProcessSupervisor(root)
            try:
                supervisor.start_monitor(interval_seconds=0.01)
                deadline = time.monotonic() + 1
                snapshot = {}
                while time.monotonic() < deadline:
                    snapshot = supervisor.monitor_snapshot()
                    if snapshot.get("last_status") == "ok":
                        break
                    time.sleep(0.01)
                running_snapshot = supervisor.monitor_snapshot()
            finally:
                supervisor.stop_monitor()
                stopped_snapshot = supervisor.monitor_snapshot()

        self.assertEqual(snapshot["last_status"], "ok")
        self.assertEqual(snapshot["last_group_count"], 0)
        self.assertRegex(str(snapshot["last_tick_at"]), r"^\d{4}-\d{2}-\d{2}T")
        self.assertEqual(snapshot["last_error_type"], "")
        self.assertEqual(running_snapshot["running"], True)
        self.assertEqual(stopped_snapshot["running"], False)

    def test_monitor_snapshot_records_safe_failure_and_keeps_loop_alive(self):
        class FailingSupervisor(_LiveAgentProcessSupervisor):
            def __init__(self, output_root):
                super().__init__(output_root, preflight_checker=_ok_preflight)
                self.calls = 0

            def _refresh_running_groups(self):
                self.calls += 1
                raise RuntimeError("failed reading /Users/me/private/live-agents.secret.json")

        with tempfile.TemporaryDirectory() as temp_dir:
            supervisor = FailingSupervisor(Path(temp_dir))
            try:
                supervisor.start_monitor(interval_seconds=0.01)
                deadline = time.monotonic() + 1
                snapshot = {}
                while time.monotonic() < deadline:
                    snapshot = supervisor.monitor_snapshot()
                    if snapshot.get("last_status") == "failed":
                        break
                    time.sleep(0.01)
                running_snapshot = supervisor.monitor_snapshot()
            finally:
                supervisor.stop_monitor()
                supervisor.close()

        self.assertGreaterEqual(supervisor.calls, 1)
        self.assertEqual(snapshot["last_status"], "failed")
        self.assertEqual(snapshot["last_error_type"], "RuntimeError")
        self.assertEqual(snapshot["last_group_count"], 0)
        self.assertNotIn("/Users/me/private", json.dumps(snapshot, ensure_ascii=False))
        self.assertEqual(running_snapshot["running"], True)

    def test_stop_monitor_prevents_blocked_refresh_after_stop_request(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = root / "live-agents.json"
            config_path.write_text('{"agents": [{"agent_id": "a", "command": ["fake"]}]}', encoding="utf-8")
            processes = []

            def command_factory(command, **kwargs):
                process = FakeProcess(pid=8400 + len(processes))
                processes.append(process)
                return process

            supervisor = LiveAgentProcessSupervisor(root, command_factory=command_factory)
            supervisor.start_group(
                config_path=config_path,
                server="http://room.local",
                group_id="crew",
                auto_restart=True,
                max_restarts=1,
                restart_backoff_seconds=0,
            )
            processes[0].returncode = 2
            with supervisor._lock:
                supervisor._monitor_stop = threading.Event()
                monitor = threading.Thread(
                    target=supervisor._monitor_loop,
                    args=(supervisor._monitor_stop, 0.01),
                    daemon=True,
                )
                supervisor._monitor_thread = monitor
                monitor.start()
                time.sleep(0.03)
                stopper = threading.Thread(target=supervisor.stop_monitor)
                stopper.start()
                deadline = time.monotonic() + 1
                while not supervisor._monitor_stop.is_set() and time.monotonic() < deadline:
                    time.sleep(0.01)
            stopper.join(timeout=1)
            snapshot = supervisor.snapshot_groups()
            supervisor.close()

        self.assertEqual(len(processes), 1)
        self.assertEqual(snapshot[0]["status"], "running")
        self.assertEqual(snapshot[0]["pid"], 8400)
        self.assertFalse(stopper.is_alive())

    def test_stop_group_cancels_pending_auto_restart(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = root / "live-agents.json"
            config_path.write_text('{"agents": [{"agent_id": "a", "command": ["fake"]}]}', encoding="utf-8")
            current_time = {"value": datetime(2026, 5, 17, 12, 0, tzinfo=UTC)}
            processes = []

            def command_factory(command, **kwargs):
                process = FakeProcess(pid=8100 + len(processes))
                processes.append(process)
                return process

            supervisor = LiveAgentProcessSupervisor(
                root,
                command_factory=command_factory,
                now_fn=lambda: current_time["value"],
            )
            supervisor.start_group(
                config_path=config_path,
                server="http://room.local",
                group_id="crew",
                meeting_id="meeting-1",
                auto_restart=True,
                max_restarts=1,
                restart_backoff_seconds=30,
            )
            connect_live_agent(root, {"agent_id": "a", "meeting_id": "meeting-1", "status": "online"}, now=current_time["value"])
            heartbeat_live_agent(
                root,
                "a",
                status="error",
                metadata={"last_error": "Self-service command exited with return code 2."},
                now=current_time["value"],
            )
            processes[0].returncode = 2
            supervisor.list_groups()

            stopped = supervisor.stop_group("crew")
            current_time["value"] += timedelta(seconds=31)
            after_due = supervisor.list_groups()
            agents = {str(agent["agent_id"]): agent for agent in read_live_agents(root, now=current_time["value"])}

        self.assertEqual(stopped["status"], "stopped")
        self.assertEqual(stopped["next_restart_at"], "")
        self.assertEqual(stopped["offline"]["offline_agent_ids"], ["a"])
        self.assertEqual(stopped["offline"]["attention"], [])
        self.assertEqual(agents["a"]["status"], "offline")
        self.assertEqual(after_due[0]["status"], "stopped")
        self.assertEqual(len(processes), 1)

    def test_auto_restart_launch_failure_marks_error_without_stale_running_state(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = root / "live-agents.json"
            config_path.write_text('{"agents": [{"agent_id": "a", "command": ["fake"]}]}', encoding="utf-8")
            processes = []

            def command_factory(command, **kwargs):
                if processes:
                    raise OSError("restart command unavailable")
                process = FakeProcess(pid=8200)
                processes.append(process)
                return process

            supervisor = LiveAgentProcessSupervisor(
                root,
                command_factory=command_factory,
                now_fn=lambda: datetime(2026, 5, 17, 12, 0, tzinfo=UTC),
            )
            supervisor.start_group(
                config_path=config_path,
                server="http://room.local",
                group_id="crew",
                auto_restart=True,
                max_restarts=1,
                restart_backoff_seconds=0,
            )
            processes[0].returncode = 2

            groups = supervisor.list_groups()
            persisted = json.loads((root / "live-agent-runs" / "processes.json").read_text(encoding="utf-8"))

        self.assertEqual(groups[0]["status"], "error")
        self.assertIsNone(groups[0]["pid"])
        self.assertEqual(groups[0]["returncode"], 2)
        self.assertEqual(groups[0]["restart_count"], 1)
        self.assertIn("restart command unavailable", groups[0]["last_error"])
        self.assertEqual(persisted["groups"][0]["status"], "error")

    def test_immediate_auto_restart_refuses_blank_persisted_config_before_preflight(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = root / "live-agents.json"
            config_path.write_text('{"agents": [{"agent_id": "a", "command": ["fake"]}]}', encoding="utf-8")
            preflights = []
            processes = []

            def preflight_checker(path, *, server_override=None):
                preflights.append((path, server_override))
                return {"status": "ok", "checks": [], "agents": []}

            def command_factory(command, **kwargs):
                process = FakeProcess(pid=8600 + len(processes))
                processes.append(process)
                return process

            supervisor = LiveAgentProcessSupervisor(
                root,
                command_factory=command_factory,
                preflight_checker=preflight_checker,
                now_fn=lambda: datetime(2026, 5, 17, 12, 0, tzinfo=UTC),
            )
            supervisor.start_group(
                config_path=config_path,
                server="http://room.local",
                group_id="crew",
                auto_restart=True,
                max_restarts=1,
                restart_backoff_seconds=0,
            )
            preflights.clear()
            supervisor._records["crew"]["config_path"] = ""
            processes[0].returncode = 2

            groups = supervisor.list_groups()
            persisted = json.loads((root / "live-agent-runs" / "processes.json").read_text(encoding="utf-8"))
            events = _read_lifecycle_events(root)
            events_text = json.dumps(events, ensure_ascii=False)

        self.assertEqual(len(processes), 1)
        self.assertEqual(preflights, [])
        self.assertEqual(groups[0]["status"], "error")
        self.assertIsNone(groups[0]["pid"])
        self.assertEqual(groups[0]["restart_count"], 1)
        self.assertIn("has no config to restart", groups[0]["last_error"])
        self.assertEqual(persisted["groups"][0]["status"], "error")
        self.assertEqual(persisted["groups"][0]["restart_count"], 1)
        self.assertEqual(persisted["groups"][0]["next_restart_at"], "")
        self.assertIn("has no config to restart", persisted["groups"][0]["last_error"])
        self.assertEqual([event["event_type"] for event in events], ["started", "restart_scheduled", "restart_failed"])
        self.assertEqual(events[-1]["status"], "error")
        self.assertEqual(events[-1]["restart_count"], 1)
        self.assertNotIn("http://room.local", events_text)
        self.assertNotIn(str(config_path), events_text)

    def test_delayed_auto_restart_refuses_blank_persisted_config_before_preflight(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = root / "live-agents.json"
            config_path.write_text('{"agents": [{"agent_id": "a", "command": ["fake"]}]}', encoding="utf-8")
            current_time = {"value": datetime(2026, 5, 17, 12, 0, tzinfo=UTC)}
            preflights = []
            processes = []

            def preflight_checker(path, *, server_override=None):
                preflights.append((path, server_override))
                return {"status": "ok", "checks": [], "agents": []}

            def command_factory(command, **kwargs):
                process = FakeProcess(pid=8700 + len(processes))
                processes.append(process)
                return process

            supervisor = LiveAgentProcessSupervisor(
                root,
                command_factory=command_factory,
                preflight_checker=preflight_checker,
                now_fn=lambda: current_time["value"],
            )
            supervisor.start_group(
                config_path=config_path,
                server="http://room.local",
                group_id="crew",
                auto_restart=True,
                max_restarts=1,
                restart_backoff_seconds=10,
            )
            preflights.clear()
            supervisor._records["crew"]["config_path"] = "   "
            processes[0].returncode = 2
            waiting = supervisor.list_groups()
            current_time["value"] += timedelta(seconds=11)

            groups = supervisor.list_groups()
            persisted = json.loads((root / "live-agent-runs" / "processes.json").read_text(encoding="utf-8"))
            events = _read_lifecycle_events(root)
            events_text = json.dumps(events, ensure_ascii=False)

        self.assertEqual(waiting[0]["status"], "restarting")
        self.assertEqual(len(processes), 1)
        self.assertEqual(preflights, [])
        self.assertEqual(groups[0]["status"], "error")
        self.assertIsNone(groups[0]["pid"])
        self.assertEqual(groups[0]["restart_count"], 1)
        self.assertEqual(groups[0]["next_restart_at"], "")
        self.assertIn("has no config to restart", groups[0]["last_error"])
        self.assertEqual(persisted["groups"][0]["status"], "error")
        self.assertEqual(persisted["groups"][0]["restart_count"], 1)
        self.assertEqual(persisted["groups"][0]["next_restart_at"], "")
        self.assertIn("has no config to restart", persisted["groups"][0]["last_error"])
        self.assertEqual([event["event_type"] for event in events], ["started", "restart_scheduled", "restart_failed"])
        self.assertEqual(events[-1]["status"], "error")
        self.assertEqual(events[-1]["restart_count"], 1)
        self.assertNotIn("http://room.local", events_text)
        self.assertNotIn(str(config_path), events_text)

    def test_immediate_auto_restart_refuses_blank_persisted_server_before_preflight(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = root / "live-agents.json"
            config_path.write_text('{"agents": [{"agent_id": "a", "command": ["fake"]}]}', encoding="utf-8")
            preflights = []
            processes = []

            def preflight_checker(path, *, server_override=None):
                preflights.append((path, server_override))
                return {"status": "ok", "checks": [], "agents": []}

            def command_factory(command, **kwargs):
                process = FakeProcess(pid=8800 + len(processes))
                processes.append(process)
                return process

            supervisor = LiveAgentProcessSupervisor(
                root,
                command_factory=command_factory,
                preflight_checker=preflight_checker,
                now_fn=lambda: datetime(2026, 5, 17, 12, 0, tzinfo=UTC),
            )
            supervisor.start_group(
                config_path=config_path,
                server="http://room.local",
                group_id="crew",
                auto_restart=True,
                max_restarts=1,
                restart_backoff_seconds=0,
            )
            preflights.clear()
            supervisor._records["crew"]["server"] = ""
            processes[0].returncode = 2

            groups = supervisor.list_groups()
            persisted = json.loads((root / "live-agent-runs" / "processes.json").read_text(encoding="utf-8"))
            events = _read_lifecycle_events(root)
            events_text = json.dumps(events, ensure_ascii=False)

        self.assertEqual(len(processes), 1)
        self.assertEqual(preflights, [])
        self.assertEqual(groups[0]["status"], "error")
        self.assertIsNone(groups[0]["pid"])
        self.assertEqual(groups[0]["restart_count"], 1)
        self.assertIn("has no server to restart", groups[0]["last_error"])
        self.assertEqual(persisted["groups"][0]["status"], "error")
        self.assertEqual(persisted["groups"][0]["restart_count"], 1)
        self.assertEqual(persisted["groups"][0]["next_restart_at"], "")
        self.assertIn("has no server to restart", persisted["groups"][0]["last_error"])
        self.assertEqual([event["event_type"] for event in events], ["started", "restart_scheduled", "restart_failed"])
        self.assertEqual(events[-1]["status"], "error")
        self.assertEqual(events[-1]["restart_count"], 1)
        self.assertNotIn("http://room.local", events_text)
        self.assertNotIn(str(config_path), events_text)

    def test_delayed_auto_restart_refuses_blank_persisted_server_before_preflight(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = root / "live-agents.json"
            config_path.write_text('{"agents": [{"agent_id": "a", "command": ["fake"]}]}', encoding="utf-8")
            current_time = {"value": datetime(2026, 5, 17, 12, 0, tzinfo=UTC)}
            preflights = []
            processes = []

            def preflight_checker(path, *, server_override=None):
                preflights.append((path, server_override))
                return {"status": "ok", "checks": [], "agents": []}

            def command_factory(command, **kwargs):
                process = FakeProcess(pid=8900 + len(processes))
                processes.append(process)
                return process

            supervisor = LiveAgentProcessSupervisor(
                root,
                command_factory=command_factory,
                preflight_checker=preflight_checker,
                now_fn=lambda: current_time["value"],
            )
            supervisor.start_group(
                config_path=config_path,
                server="http://room.local",
                group_id="crew",
                auto_restart=True,
                max_restarts=1,
                restart_backoff_seconds=10,
            )
            preflights.clear()
            supervisor._records["crew"]["server"] = "   "
            processes[0].returncode = 2
            waiting = supervisor.list_groups()
            current_time["value"] += timedelta(seconds=11)

            groups = supervisor.list_groups()
            persisted = json.loads((root / "live-agent-runs" / "processes.json").read_text(encoding="utf-8"))
            events = _read_lifecycle_events(root)
            events_text = json.dumps(events, ensure_ascii=False)

        self.assertEqual(waiting[0]["status"], "restarting")
        self.assertEqual(len(processes), 1)
        self.assertEqual(preflights, [])
        self.assertEqual(groups[0]["status"], "error")
        self.assertIsNone(groups[0]["pid"])
        self.assertEqual(groups[0]["restart_count"], 1)
        self.assertEqual(groups[0]["next_restart_at"], "")
        self.assertIn("has no server to restart", groups[0]["last_error"])
        self.assertEqual(persisted["groups"][0]["status"], "error")
        self.assertEqual(persisted["groups"][0]["restart_count"], 1)
        self.assertEqual(persisted["groups"][0]["next_restart_at"], "")
        self.assertIn("has no server to restart", persisted["groups"][0]["last_error"])
        self.assertEqual([event["event_type"] for event in events], ["started", "restart_scheduled", "restart_failed"])
        self.assertEqual(events[-1]["status"], "error")
        self.assertEqual(events[-1]["restart_count"], 1)
        self.assertNotIn("http://room.local", events_text)
        self.assertNotIn(str(config_path), events_text)

    def test_auto_restart_stops_at_max_restarts(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = root / "live-agents.json"
            config_path.write_text('{"agents": [{"agent_id": "a", "command": ["fake"]}]}', encoding="utf-8")
            processes = []

            def command_factory(command, **kwargs):
                process = FakeProcess(pid=9000 + len(processes))
                processes.append(process)
                return process

            supervisor = LiveAgentProcessSupervisor(root, command_factory=command_factory)
            supervisor.start_group(
                config_path=config_path,
                server="http://room.local",
                group_id="crew",
                auto_restart=True,
                max_restarts=0,
                restart_backoff_seconds=0,
            )
            processes[0].returncode = 2

            groups = supervisor.list_groups()

        self.assertEqual(groups[0]["status"], "error")
        self.assertEqual(groups[0]["returncode"], 2)
        self.assertEqual(groups[0]["restart_count"], 0)
        self.assertEqual(len(processes), 1)

    def test_non_finite_restart_backoff_falls_back_to_default(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = root / "live-agents.json"
            config_path.write_text('{"agents": [{"agent_id": "a", "command": ["fake"]}]}', encoding="utf-8")
            supervisor = LiveAgentProcessSupervisor(root, command_factory=lambda command, **kwargs: FakeProcess())

            started = supervisor.start_group(
                config_path=config_path,
                server="http://room.local",
                group_id="crew",
                auto_restart=True,
                max_restarts=1,
                restart_backoff_seconds=float("inf"),
            )

        self.assertEqual(started["restart_backoff_seconds"], 5.0)

    def test_persisted_non_finite_restart_backoff_falls_back_to_default(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            runs_dir = root / "live-agent-runs"
            runs_dir.mkdir()
            (runs_dir / "processes.json").write_text(
                json.dumps(
                    {
                        "groups": [
                            {
                                "group_id": "crew",
                                "status": "stopped",
                                "pid": None,
                                "config_path": "configs/live-agents.example.json",
                                "server": "http://room.local",
                                "log_path": str(runs_dir / "crew.log"),
                                "started_at": "2026-05-17T12:00:00+00:00",
                                "stopped_at": "2026-05-17T12:01:00+00:00",
                                "returncode": 0,
                                "last_error": "",
                                "restart_backoff_seconds": float("nan"),
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )

            groups = LiveAgentProcessSupervisor(root).list_groups()

        self.assertEqual(groups[0]["restart_backoff_seconds"], 5.0)

    def test_persisted_non_finite_restart_counts_fall_back_to_default(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            runs_dir = root / "live-agent-runs"
            runs_dir.mkdir()
            (runs_dir / "processes.json").write_text(
                json.dumps(
                    {
                        "groups": [
                            {
                                "group_id": "crew",
                                "status": "stopped",
                                "pid": None,
                                "config_path": "configs/live-agents.example.json",
                                "server": "http://room.local",
                                "log_path": str(runs_dir / "crew.log"),
                                "started_at": "2026-05-17T12:00:00+00:00",
                                "stopped_at": "2026-05-17T12:01:00+00:00",
                                "returncode": 0,
                                "last_error": "",
                                "restart_count": float("inf"),
                                "max_restarts": float("inf"),
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )

            groups = LiveAgentProcessSupervisor(root).list_groups()

        self.assertEqual(groups[0]["restart_count"], 0)
        self.assertEqual(groups[0]["max_restarts"], 0)


if __name__ == "__main__":
    unittest.main()
