import json
import signal
import subprocess
import tempfile
import threading
import time
import unittest
from datetime import UTC, datetime, timedelta
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
            persisted = json.loads((runs_dir / "processes.json").read_text(encoding="utf-8"))
            persisted_by_id = {record["group_id"]: record for record in persisted["groups"]}

        self.assertEqual(recovered["orphan-running"]["status"], "unknown")
        self.assertIsNone(recovered["orphan-running"]["pid"])
        self.assertIsNone(persisted_by_id["orphan-running"]["pid"])
        self.assertEqual(recovered["finished"]["status"], "stopped")
        self.assertEqual(recovered["finished"]["pid"], 2222)
        self.assertEqual(persisted_by_id["finished"]["pid"], 2222)
        self.assertEqual(recovered["crashed"]["status"], "error")
        self.assertEqual(recovered["crashed"]["pid"], 3333)
        self.assertEqual(persisted_by_id["crashed"]["pid"], 3333)

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
            config_path.write_text('{"agents": [{"agent_id": "a", "command": ["fake"]}]}', encoding="utf-8")
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

        self.assertEqual(restarted["group_id"], "crew")
        self.assertEqual(restarted["status"], "running")
        self.assertEqual(restarted["pid"], 5001)
        self.assertEqual(restarted["config_path"], str(config_path))
        self.assertEqual(restarted["server"], "http://room.local")
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
            config_path.write_text('{"agents": [{"agent_id": "a", "command": ["fake"]}]}', encoding="utf-8")
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

        self.assertEqual(restarted["status"], "running")
        self.assertEqual(restarted["pid"], 6789)
        self.assertEqual(restarted["config_path"], str(config_path))
        self.assertEqual(restarted["server"], "http://room.local")
        self.assertEqual(len(launched), 1)

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
                auto_restart=True,
                max_restarts=1,
                restart_backoff_seconds=30,
            )
            processes[0].returncode = 2
            supervisor.list_groups()

            stopped = supervisor.stop_group("crew")
            current_time["value"] += timedelta(seconds=31)
            after_due = supervisor.list_groups()

        self.assertEqual(stopped["status"], "stopped")
        self.assertEqual(stopped["next_restart_at"], "")
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
