import unittest
import json
import shlex
import signal
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from unittest.mock import patch

from agentsassemble import cli as cli_module
from agentsassemble.cli import build_parser
from agentsassemble.live_agent_runner import ResidentAgentConfig


from tests.test_cli_timeout_runtime_helpers import (
    _FailingSelfServiceProcess,
    _kill_pid,
    _pid_exists,
    _self_service_resident_config,
    _wait_for_pid_exit,
)


class CliTimeoutRuntimeProcessTests(unittest.TestCase):

    def test_self_service_child_failure_keeps_error_presence(self):
        calls = []

        def request_json(url, *, method="GET", payload=None, **kwargs):
            del kwargs
            calls.append({"url": url, "method": method, "payload": payload})
            return {"agent": {"agent_id": "selfer", "status": (payload or {}).get("status", "online")}}

        supervisor = cli_module._SelfServiceResidentSupervisor(
            _self_service_resident_config(),
            request_json=request_json,
            sleep_fn=lambda seconds: None,
        )

        with patch("agentsassemble.cli.subprocess.Popen", return_value=_FailingSelfServiceProcess()):
            with self.assertRaises(subprocess.CalledProcessError):
                supervisor.run()

        heartbeat_payloads = [
            call["payload"]
            for call in calls
            if call["url"] == "http://room.local/api/live-agents/selfer/heartbeat"
        ]
        self.assertEqual(
            heartbeat_payloads,
            [
                {"status": "online"},
                {"status": "error", "last_error": "Self-service command exited with return code 7."},
            ],
        )

    def test_self_service_child_failure_falls_back_to_offline_when_error_heartbeat_fails(self):
        calls = []

        def request_json(url, *, method="GET", payload=None, **kwargs):
            del kwargs
            calls.append({"url": url, "method": method, "payload": payload})
            if payload and payload.get("status") == "error":
                raise RuntimeError("temporary heartbeat failure")
            return {"agent": {"agent_id": "selfer", "status": (payload or {}).get("status", "online")}}

        supervisor = cli_module._SelfServiceResidentSupervisor(
            _self_service_resident_config(),
            request_json=request_json,
            sleep_fn=lambda seconds: None,
        )

        with patch("agentsassemble.cli.subprocess.Popen", return_value=_FailingSelfServiceProcess()):
            with self.assertRaises(subprocess.CalledProcessError):
                supervisor.run()

        heartbeat_payloads = [
            call["payload"]
            for call in calls
            if call["url"] == "http://room.local/api/live-agents/selfer/heartbeat"
        ]
        self.assertEqual(
            heartbeat_payloads,
            [
                {"status": "online"},
                {"status": "error", "last_error": "Self-service command exited with return code 7."},
                {"status": "offline"},
            ],
        )

    def test_self_service_parent_liveness_heartbeat_preserves_child_status(self):
        config = ResidentAgentConfig(
            server="http://room.local",
            agent_id="selfer",
            display_name="Self Service",
            provider_kind="antigravity_cli",
            connection_kind="self_service",
            session_id="",
            endpoint="",
            auth_ref="",
            meeting_id="resident-m1",
            engagement_mode="always",
            command=["agent"],
            timeout_seconds=120,
            poll_interval=0.5,
            heartbeat_interval=1,
            cooldown=5,
            max_chain_depth=1,
        )
        calls = []

        def request_json(url, *, method="GET", payload=None, **kwargs):
            del kwargs
            calls.append({"url": url, "method": method, "payload": payload})
            return {"agent": {"agent_id": "selfer", "status": "working"}}

        supervisor = cli_module._SelfServiceResidentSupervisor(
            config,
            request_json=request_json,
            sleep_fn=lambda seconds: None,
        )
        supervisor.last_heartbeat_at = 0

        supervisor._heartbeat_if_due()

        self.assertEqual(calls[-1]["url"], "http://room.local/api/live-agents/selfer/heartbeat")
        self.assertEqual(calls[-1]["method"], "POST")
        self.assertEqual(calls[-1]["payload"], {"status": "online", "preserve_status": True})

    def test_self_service_room_command_templates_round_trip_shell_escaping(self):
        config = ResidentAgentConfig(
            server="http://room.local/path with space?x=1&y=$two",
            agent_id="agent with spaces;$",
            display_name="Self Service",
            provider_kind="antigravity_cli",
            connection_kind="self_service",
            session_id="",
            endpoint="",
            auth_ref="",
            meeting_id="",
            engagement_mode="always",
            command=["agent"],
            timeout_seconds=120,
            poll_interval=0.5,
            heartbeat_interval=30,
            cooldown=5,
            max_chain_depth=2,
        )

        env = cli_module._self_service_process_env(config)
        wait_next = shlex.split(env["AGENTSASSEMBLE_WAIT_NEXT_COMMAND"])
        self.assertIn("http://room.local/path with space?x=1&y=$two", wait_next)
        self.assertIn("agent with spaces;$", wait_next)
        self.assertIn("2", wait_next)
        official_template = shlex.split(env["AGENTSASSEMBLE_OFFICIAL_REPLY_COMMAND_TEMPLATE"])
        self.assertIn("{meeting_id}", official_template)
        self.assertNotIn("", official_template)
        heartbeat_template = shlex.split(env["AGENTSASSEMBLE_HEARTBEAT_COMMAND_TEMPLATE"])
        self.assertIn("http://room.local/path with space?x=1&y=$two", heartbeat_template)
        self.assertIn("agent with spaces;$", heartbeat_template)
        self.assertIn("{status}", heartbeat_template)
        self.assertIn("--last-error={last_error}", heartbeat_template)
        self.assertIn("--last-reply-at={last_reply_at}", heartbeat_template)
        self.assertIn("--last-observed-event-id={last_observed_event_id}", heartbeat_template)
        self.assertIn("--last-observed-live-event-id={last_observed_live_event_id}", heartbeat_template)
        leave_command = shlex.split(env["AGENTSASSEMBLE_LEAVE_COMMAND"])
        self.assertIn("http://room.local/path with space?x=1&y=$two", leave_command)
        self.assertIn("agent with spaces;$", leave_command)
        self.assertIn("leave", leave_command)
        say_template = shlex.split(env["AGENTSASSEMBLE_SAY_COMMAND_TEMPLATE"])
        self.assertIn("{message}", say_template)
        self.assertLess(say_template.index("--"), say_template.index("{message}"))
        self.assertLess(official_template.index("--"), official_template.index("{message}"))
        say_argv = [
            "-h" if item == "{message}" else "evt-1" if item == "{source_event_id}" else "1" if item == "{auto_chain_depth}" else item
            for item in say_template
        ]
        official_argv = [
            "-h" if item == "{message}" else "meeting-1" if item == "{meeting_id}" else "live-1" if item == "{source_event_id}" else item
            for item in official_template
        ]
        heartbeat_argv = [
            item.replace("{last_error}", "--provider-failed")
            .replace("{last_attention}", "persona_context_blocked_official_turn")
            .replace("{status}", "error")
            .replace("{last_reply_at}", "2026-05-20T00:00:00+00:00")
            .replace("{last_observed_event_id}", "evt-1")
            .replace("{last_observed_live_event_id}", "live-1")
            for item in heartbeat_template
        ]
        say_args = build_parser().parse_args(say_argv[3:])
        official_args = build_parser().parse_args(official_argv[3:])
        heartbeat_args = build_parser().parse_args(heartbeat_argv[3:])
        leave_args = build_parser().parse_args(leave_command[3:])
        self.assertEqual(say_args.message, ["-h"])
        self.assertEqual(official_args.message, ["-h"])
        self.assertEqual(heartbeat_args.status, "error")
        self.assertEqual(heartbeat_args.last_error, "--provider-failed")
        self.assertEqual(heartbeat_args.last_attention, "persona_context_blocked_official_turn")
        self.assertEqual(heartbeat_args.last_reply_at, "2026-05-20T00:00:00+00:00")
        self.assertEqual(heartbeat_args.last_observed_event_id, "evt-1")
        self.assertEqual(heartbeat_args.last_observed_live_event_id, "live-1")
        self.assertEqual(leave_args.live_agent_command, "leave")
        self.assertEqual(leave_args.agent_id, "agent with spaces;$")

    def test_heartbeat_payload_ignores_unreplaced_optional_template_placeholders(self):
        args = build_parser().parse_args(
            [
                "live-agent",
                "heartbeat",
                "--server",
                "http://room.local",
                "--agent-id",
                "selfer",
                "--status",
                "online",
                "--last-error",
                "{last_error}",
                "--last-attention",
                "{last_attention}",
                "--last-reply-at",
                "{last_reply_at}",
                "--last-observed-event-id",
                "{last_observed_event_id}",
                "--last-observed-live-event-id",
                "{last_observed_live_event_id}",
            ]
        )

        payload = cli_module._heartbeat_payload(args)

        self.assertEqual(payload, {"status": "online"})

    def test_heartbeat_payload_redacts_unknown_attention_text(self):
        args = build_parser().parse_args(
            [
                "live-agent",
                "heartbeat",
                "--server",
                "http://room.local",
                "--agent-id",
                "selfer",
                "--status",
                "online",
                "--last-attention",
                "raw card lore that should not appear in roster",
            ]
        )

        payload = cli_module._heartbeat_payload(args)

        self.assertEqual(payload["last_attention"], "presence_attention_redacted")
        self.assertNotIn("raw card lore", json.dumps(payload))

    def test_local_cli_resident_command_runner_close_terminates_active_process(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            pid_path = Path(temp_dir) / "child.pid"
            runner = cli_module._LocalCliCommandRunner()
            errors = []

            def invoke_runner():
                try:
                    runner(
                        [
                            sys.executable,
                            "-u",
                            "-c",
                            (
                                "import os, pathlib, sys, time; "
                                "pathlib.Path(sys.argv[1]).write_text(str(os.getpid()), encoding='utf-8'); "
                                "time.sleep(30)"
                            ),
                            str(pid_path),
                        ],
                        "prompt",
                        timeout_seconds=30,
                    )
                except Exception as error:
                    errors.append(error)

            thread = threading.Thread(target=invoke_runner)
            thread.start()
            try:
                deadline = time.time() + 5
                while time.time() < deadline and not pid_path.exists():
                    time.sleep(0.01)
                self.assertTrue(pid_path.exists())

                runner.close()
                thread.join(timeout=3)
            finally:
                runner.close()
                thread.join(timeout=1)

            self.assertFalse(thread.is_alive())
            self.assertTrue(errors)

    def test_local_cli_terminate_falls_back_to_process_kill_without_sigkill(self):
        class FakeSignal:
            SIGTERM = 15

        class TimeoutProcess:
            def __init__(self):
                self.returncode = None
                self.terminated = False
                self.killed = False
                self.waits = 0

            def poll(self):
                return self.returncode

            def terminate(self):
                self.terminated = True

            def wait(self, timeout=None):
                del timeout
                self.waits += 1
                if self.waits == 1:
                    raise cli_module.subprocess.TimeoutExpired(["fake"], 1)
                self.returncode = -9
                return self.returncode

            def kill(self):
                self.killed = True
                self.returncode = -9

        process = TimeoutProcess()

        with (
            patch("agentsassemble.cli._supports_process_groups", return_value=False),
            patch("agentsassemble.cli.signal", FakeSignal),
        ):
            cli_module._terminate_process(process)

        self.assertTrue(process.terminated)
        self.assertTrue(process.killed)

    @unittest.skipUnless(cli_module._supports_process_groups(), "requires POSIX process-group support")
    def test_local_cli_resident_command_runner_close_terminates_child_processes(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            child_pid_path = Path(temp_dir) / "grandchild.pid"
            runner = cli_module._LocalCliCommandRunner()
            errors = []

            def invoke_runner():
                try:
                    runner(
                        [
                            sys.executable,
                            "-u",
                            "-c",
                            (
                                "import pathlib, subprocess, sys, time; "
                                "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)']); "
                                "pathlib.Path(sys.argv[1]).write_text(str(child.pid), encoding='utf-8'); "
                                "time.sleep(30)"
                            ),
                            str(child_pid_path),
                        ],
                        "prompt",
                        timeout_seconds=30,
                    )
                except Exception as error:
                    errors.append(error)

            thread = threading.Thread(target=invoke_runner)
            thread.start()
            child_pid = None
            child_alive_after_close = None
            try:
                deadline = time.time() + 5
                while time.time() < deadline and not child_pid_path.exists():
                    time.sleep(0.01)
                self.assertTrue(child_pid_path.exists())
                child_pid = int(child_pid_path.read_text(encoding="utf-8"))

                runner.close()
                thread.join(timeout=3)
                child_alive_after_close = not _wait_for_pid_exit(child_pid)
            finally:
                if child_pid is not None and _pid_exists(child_pid):
                    try:
                        _kill_pid(child_pid)
                    except ProcessLookupError:
                        pass
                runner.close()
                thread.join(timeout=1)

            self.assertFalse(thread.is_alive())
            self.assertFalse(child_alive_after_close)
            self.assertTrue(errors)

    @unittest.skipUnless(cli_module._supports_process_groups(), "requires POSIX process-group support")
    def test_local_cli_resident_command_runner_timeout_terminates_child_processes(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            child_pid_path = Path(temp_dir) / "timeout-grandchild.pid"
            runner = cli_module._LocalCliCommandRunner()
            errors = []

            def invoke_runner():
                try:
                    runner(
                        [
                            sys.executable,
                            "-u",
                            "-c",
                            (
                                "import pathlib, subprocess, sys, time; "
                                "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)']); "
                                "pathlib.Path(sys.argv[1]).write_text(str(child.pid), encoding='utf-8'); "
                                "time.sleep(30)"
                            ),
                            str(child_pid_path),
                        ],
                        "prompt",
                        timeout_seconds=0.2,
                    )
                except cli_module.subprocess.TimeoutExpired as error:
                    errors.append(error)

            thread = threading.Thread(target=invoke_runner)
            thread.start()
            child_pid = None
            child_alive_after_timeout = None
            try:
                deadline = time.time() + 5
                while time.time() < deadline and not child_pid_path.exists():
                    time.sleep(0.01)
                self.assertTrue(child_pid_path.exists())
                child_pid = int(child_pid_path.read_text(encoding="utf-8"))

                thread.join(timeout=5)
                child_alive_after_timeout = not _wait_for_pid_exit(child_pid)
            finally:
                if child_pid is not None and _pid_exists(child_pid):
                    try:
                        _kill_pid(child_pid)
                    except ProcessLookupError:
                        pass
                runner.close()
                thread.join(timeout=1)

            self.assertFalse(thread.is_alive())
            self.assertTrue(errors)
            self.assertFalse(child_alive_after_timeout)

    def test_local_cli_resident_command_runner_terminates_child_on_interruption(self):
        class InterruptingProcess:
            def __init__(self):
                self.returncode = None
                self.terminated = False
                self.killed = False

            def communicate(self, input=None, timeout=None):
                del input, timeout
                raise KeyboardInterrupt()

            def poll(self):
                return self.returncode

            def terminate(self):
                self.terminated = True
                self.returncode = -15

            def wait(self, timeout=None):
                del timeout
                return self.returncode

            def kill(self):
                self.killed = True
                self.returncode = -9

        process = InterruptingProcess()
        with patch("agentsassemble.cli.subprocess.Popen", return_value=process):
            runner = cli_module._LocalCliCommandRunner()
            with self.assertRaises(KeyboardInterrupt):
                runner(["fake-provider"], "prompt", timeout_seconds=30)

        self.assertTrue(process.terminated)
        self.assertFalse(process.killed)

    def test_resident_shutdown_signal_handler_closes_and_raises_keyboard_interrupt(self):
        sigterm = getattr(signal, "SIGTERM", None)
        if sigterm is None:
            self.skipTest("SIGTERM is unavailable on this platform")

        installed_handlers = {}
        previous_handlers = {}
        restored_handlers = []

        def fake_signal(signum, handler):
            previous = previous_handlers.setdefault(signum, object())
            if signum in installed_handlers:
                restored_handlers.append((signum, handler))
            else:
                installed_handlers[signum] = handler
            return previous

        closed = []
        with patch("agentsassemble.cli.signal.signal", side_effect=fake_signal):
            restore = cli_module._install_resident_shutdown_signal_handlers(lambda: closed.append(True))
            self.assertIn(sigterm, installed_handlers)
            with self.assertRaises(KeyboardInterrupt):
                installed_handlers[sigterm](sigterm, None)
            restore()

        self.assertEqual(closed, [True])
        self.assertIn((sigterm, previous_handlers[sigterm]), restored_handlers)
