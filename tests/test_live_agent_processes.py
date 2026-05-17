import json
import signal
import subprocess
import tempfile
import threading
import time
import unittest
from datetime import UTC, datetime
from pathlib import Path

from agentsassemble.live_agent_processes import LiveAgentProcessSupervisor


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
            self.assertIs(kwargs["stderr"], kwargs["stdout"])

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

        self.assertEqual(recovered["orphan-running"]["status"], "unknown")
        self.assertEqual(recovered["finished"]["status"], "stopped")
        self.assertEqual(recovered["crashed"]["status"], "error")

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


if __name__ == "__main__":
    unittest.main()
