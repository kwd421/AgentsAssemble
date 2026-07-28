import unittest
import json
import os
import sys
import tempfile
import threading
import time
from pathlib import Path
from http.server import ThreadingHTTPServer
from io import StringIO
from unittest.mock import patch

from agentsassemble.cli import build_parser, main
from agentsassemble.gui import _make_handler, append_lobby_event, read_lobby
from agentsassemble.live_agent_runner import ResidentAgentConfig


from tests.test_cli_timeout_runtime_helpers import (
    _FailingSelfServiceProcess,
    _self_service_resident_config,
)


class CliTimeoutRunGroupTests(unittest.TestCase):

    def test_live_agent_run_group_keeps_self_service_child_in_group_process_session(self):
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
            command=[sys.executable, "-c", "pass"],
            timeout_seconds=120,
            poll_interval=0,
            heartbeat_interval=30,
            cooldown=5,
            max_chain_depth=1,
            max_ticks=1,
        )

        class FakeSelfServiceProcess:
            pid = 4321
            returncode = 0

            def poll(self):
                return self.returncode

            def wait(self, timeout=None):
                del timeout
                return self.returncode

            def terminate(self):
                self.returncode = -15

            def kill(self):
                self.returncode = -9

        popen_calls = []

        def fake_popen(command, **kwargs):
            popen_calls.append({"command": command, "kwargs": kwargs})
            return FakeSelfServiceProcess()

        def request_json(url, *, method="GET", payload=None, **kwargs):
            del url, method, kwargs
            return {"agent": {"agent_id": "selfer", "status": (payload or {}).get("status", "online")}}

        with (
            patch("agentsassemble.cli.load_group_configs", return_value=[config]),
            patch("agentsassemble.cli.resident_config_setup_error", return_value=""),
            patch("agentsassemble.cli._request_json", side_effect=request_json),
            patch("agentsassemble.cli._supports_process_groups", return_value=True),
            patch("agentsassemble.cli.subprocess.Popen", side_effect=fake_popen),
            patch("sys.stdout", StringIO()),
            patch("sys.stderr", StringIO()),
        ):
            exit_code = main(["live-agent", "--legacy-internal", "run-group", "--config", "ignored.json"])

        self.assertEqual(exit_code, 0)
        self.assertEqual(len(popen_calls), 1)
        self.assertIs(popen_calls[0]["kwargs"]["start_new_session"], False)

    def test_live_agent_run_group_self_service_child_failure_keeps_single_safe_error_heartbeat(self):
        config = _self_service_resident_config(command=["/private/fake-self-service"], max_ticks=1)
        calls = []

        def request_json(url, *, method="GET", payload=None, **kwargs):
            del method, kwargs
            calls.append({"url": url, "payload": payload})
            return {"agent": {"agent_id": "selfer", "status": (payload or {}).get("status", "online")}}

        with (
            patch("agentsassemble.cli.load_group_configs", return_value=[config]),
            patch("agentsassemble.cli.resident_config_setup_error", return_value=""),
            patch("agentsassemble.cli._request_json", side_effect=request_json),
            patch("agentsassemble.cli.subprocess.Popen", return_value=_FailingSelfServiceProcess()),
            patch("sys.stdout", StringIO()),
            patch("sys.stderr", StringIO()),
        ):
            exit_code = main(["live-agent", "--legacy-internal", "run-group", "--config", "ignored.json"])

        self.assertEqual(exit_code, 2)
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
        heartbeat_blob = json.dumps(heartbeat_payloads)
        self.assertNotIn("/private/fake-self-service", heartbeat_blob)
        self.assertNotIn("returned non-zero exit status", heartbeat_blob)

    def test_live_agent_run_group_accepts_config_path_and_tick_bound(self):
        args = build_parser().parse_args(
            ["live-agent", "--legacy-internal", "run-group", "--config", "configs/live-agents.example.json", "--max-ticks", "2"]
        )

        self.assertEqual(args.live_agent_command, "run-group")
        self.assertEqual(args.config, "configs/live-agents.example.json")
        self.assertEqual(args.max_ticks, 2)

    def test_live_agent_run_group_rejects_negative_tick_bound(self):
        stderr = StringIO()
        with patch("sys.stderr", stderr):
            with self.assertRaises(SystemExit) as raised:
                build_parser().parse_args(
                    ["live-agent", "--legacy-internal", "run-group", "--config", "configs/live-agents.example.json", "--max-ticks", "-1"]
                )

        self.assertEqual(raised.exception.code, 2)
        self.assertIn("value must be non-negative", stderr.getvalue())

    def test_live_agent_run_group_accepts_server_override(self):
        args = build_parser().parse_args(
            [
                "live-agent",
                "--legacy-internal",
                "run-group",
                "--config",
                "configs/live-agents.example.json",
                "--server",
                "http://127.0.0.1:9999",
                "--max-ticks",
                "1",
            ]
        )

        self.assertEqual(args.server, "http://127.0.0.1:9999")

        with patch("agentsassemble.cli.load_group_configs", return_value=[]) as load_configs:
            stdout = StringIO()
            with patch("sys.stdout", stdout):
                exit_code = main(
                    [
                        "live-agent",
                        "--legacy-internal",
                        "run-group",
                        "--config",
                        "configs/live-agents.example.json",
                        "--server",
                        "http://127.0.0.1:9999",
                        "--max-ticks",
                        "1",
                    ]
                )

        self.assertEqual(exit_code, 0)
        load_configs.assert_called_once_with(
            Path("configs/live-agents.example.json"),
            max_ticks_override=1,
            server_override="http://127.0.0.1:9999",
        )

    def test_live_agent_run_group_posts_two_fake_cli_replies(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "room"
            root.mkdir()
            source_event = append_lobby_event(root, {"name": "나", "side": "mine", "message": "다들 살아있어?"})
            config_path = Path(temp_dir) / "live-agents.json"
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                config_path.write_text(
                    json.dumps(
                        {
                            "server": f"http://127.0.0.1:{server.server_port}",
                            "poll_interval": 0,
                            "cooldown": 0,
                            "max_chain_depth": 0,
                            "agents": [
                                {
                                    "agent_id": "agent-a",
                                    "display_name": "Agent A",
                                    "engagement_mode": "always",
                                    "command": [
                                        sys.executable,
                                        "-c",
                                        "import sys; sys.stdin.read(); print('Agent A reply')",
                                    ],
                                },
                                {
                                    "agent_id": "agent-b",
                                    "display_name": "Agent B",
                                    "engagement_mode": "always",
                                    "command": [
                                        sys.executable,
                                        "-c",
                                        "import sys; sys.stdin.read(); print('Agent B reply')",
                                    ],
                                },
                            ],
                        }
                    ),
                    encoding="utf-8",
                )
                stdout = StringIO()
                with patch("sys.stdout", stdout):
                    exit_code = main(["live-agent", "--legacy-internal", "run-group", "--config", str(config_path), "--max-ticks", "1"])
            finally:
                server.shutdown()
                server.server_close()

            self.assertEqual(exit_code, 0)
            events = read_lobby(root)
            replies = [event for event in events if event["actor_id"] in {"agent-a", "agent-b"}]
            self.assertEqual({event["message"] for event in replies}, {"Agent A reply", "Agent B reply"})
            self.assertEqual({event["source_event_id"] for event in replies}, {source_event["id"]})
            self.assertEqual({event["auto_chain_depth"] for event in replies}, {1})
            output = stdout.getvalue()
            self.assertIn("Resident group stopped after posting 2 replies", output)
            self.assertIn("agent-a=1", output)
            self.assertIn("agent-b=1", output)

    def test_live_agent_run_group_reports_remote_bridge_setup_errors(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "live-agents.json"
            config_path.write_text(
                json.dumps(
                    {
                        "agents": [
                            {
                                "agent_id": "friend-bridge",
                                "connection_kind": "remote_bridge",
                                "endpoint": "https://friend.local:8777",
                                "auth_ref": "literal:<redacted>",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            stdout = StringIO()
            stderr = StringIO()

            with patch("sys.stdout", stdout), patch("sys.stderr", stderr):
                exit_code = main(["live-agent", "--legacy-internal", "run-group", "--config", str(config_path), "--max-ticks", "1"])

            self.assertEqual(exit_code, 2)
            self.assertIn("friend-bridge", stderr.getvalue())
            self.assertIn("available auth_ref", stderr.getvalue())
            self.assertNotIn("Resident group stopped", stdout.getvalue())

    def test_live_agent_run_group_rejects_missing_local_and_live_session_commands_before_launch(self):
        configs = [
            ResidentAgentConfig(
                server="http://room.local",
                agent_id="missing-local",
                display_name="Missing Local",
                provider_kind="local_cli",
                connection_kind="local_cli",
                session_id="",
                endpoint="",
                auth_ref="",
                meeting_id="",
                engagement_mode="always",
                command=["definitely-missing-local-cli"],
                timeout_seconds=30,
                poll_interval=0,
                heartbeat_interval=30,
                cooldown=0,
                max_chain_depth=1,
                max_ticks=1,
            ),
            ResidentAgentConfig(
                server="http://room.local",
                agent_id="missing-live-session",
                display_name="Missing Live Session",
                provider_kind="local_cli",
                connection_kind="live_session",
                session_id="",
                endpoint="",
                auth_ref="",
                meeting_id="",
                engagement_mode="always",
                command=["definitely-missing-live-session-cli"],
                timeout_seconds=30,
                poll_interval=0,
                heartbeat_interval=30,
                cooldown=0,
                max_chain_depth=1,
                max_ticks=1,
            ),
        ]
        constructed = []

        class RecordingRunner:
            def __init__(self, *args, **kwargs):
                constructed.append((args, kwargs))

            def run(self):
                return 0

        stdout = StringIO()
        stderr = StringIO()
        with (
            patch("agentsassemble.cli.load_group_configs", return_value=configs),
            patch("agentsassemble.cli.LiveAgentRunner", RecordingRunner),
            patch("sys.stdout", stdout),
            patch("sys.stderr", stderr),
        ):
            exit_code = main(["live-agent", "--legacy-internal", "run-group", "--config", "ignored.json"])

        self.assertEqual(exit_code, 2)
        self.assertEqual(constructed, [])
        self.assertIn("missing-local", stderr.getvalue())
        self.assertIn("missing-live-session", stderr.getvalue())
        self.assertIn("Command not found", stderr.getvalue())
        self.assertNotIn("Resident group stopped", stdout.getvalue())

    def test_live_agent_run_group_rejects_missing_self_service_python_script_before_registration(self):
        configs = [
            ResidentAgentConfig(
                server="http://room.local",
                agent_id="missing-self-service",
                display_name="Missing Self Service",
                provider_kind="local_cli",
                connection_kind="self_service",
                session_id="",
                endpoint="",
                auth_ref="",
                meeting_id="",
                engagement_mode="always",
                command=[sys.executable, "scripts/missing_self_service.py"],
                timeout_seconds=30,
                poll_interval=0,
                heartbeat_interval=30,
                cooldown=0,
                max_chain_depth=1,
                max_ticks=1,
            )
        ]
        constructed = []

        class RecordingSupervisor:
            def __init__(self, *args, **kwargs):
                constructed.append((args, kwargs))

            def run(self):
                return 0

        with tempfile.TemporaryDirectory() as temp_dir:
            previous_cwd = Path.cwd()
            os.chdir(temp_dir)
            try:
                stdout = StringIO()
                stderr = StringIO()
                with (
                    patch("agentsassemble.cli.load_group_configs", return_value=configs),
                    patch("agentsassemble.cli._SelfServiceResidentSupervisor", RecordingSupervisor),
                    patch(
                        "agentsassemble.legacy.live_agent.runtime.preflight.shutil.which",
                        side_effect=lambda command: command if command == sys.executable else None,
                    ),
                    patch("sys.stdout", stdout),
                    patch("sys.stderr", stderr),
                ):
                    exit_code = main(["live-agent", "--legacy-internal", "run-group", "--config", "ignored.json"])
            finally:
                os.chdir(previous_cwd)

        self.assertEqual(exit_code, 2)
        self.assertEqual(constructed, [])
        self.assertIn("missing-self-service", stderr.getvalue())
        self.assertIn("Command script not found: scripts/missing_self_service.py", stderr.getvalue())
        self.assertNotIn("Self-service resident agent stopped", stdout.getvalue())

    def test_live_agent_run_group_rejects_codex_safety_probe_failure_before_launch(self):
        configs = [
            ResidentAgentConfig(
                server="http://room.local",
                agent_id="codex-live",
                display_name="Codex Live",
                provider_kind="codex_live_session",
                connection_kind="live_session",
                session_id="",
                endpoint="",
                auth_ref="",
                meeting_id="",
                engagement_mode="always",
                command=["codex"],
                timeout_seconds=30,
                poll_interval=0,
                heartbeat_interval=30,
                cooldown=0,
                max_chain_depth=1,
                max_ticks=1,
            )
        ]
        constructed = []

        class RecordingRunner:
            def __init__(self, *args, **kwargs):
                constructed.append((args, kwargs))

            def run(self):
                return 0

        stdout = StringIO()
        stderr = StringIO()
        with (
            patch("agentsassemble.cli.load_group_configs", return_value=configs),
            patch(
                "agentsassemble.cli.resident_config_setup_error",
                return_value="Codex command does not accept required live-session safety flags.",
            ),
            patch("agentsassemble.cli.LiveAgentRunner", RecordingRunner),
            patch("sys.stdout", stdout),
            patch("sys.stderr", stderr),
        ):
            exit_code = main(["live-agent", "--legacy-internal", "run-group", "--config", "ignored.json"])

        self.assertEqual(exit_code, 2)
        self.assertEqual(constructed, [])
        self.assertIn("codex-live", stderr.getvalue())
        self.assertIn("required live-session safety flags", stderr.getvalue())
        self.assertNotIn("Resident group stopped", stdout.getvalue())

    def test_live_agent_run_group_rejects_claude_print_mode_before_launch(self):
        configs = [
            ResidentAgentConfig(
                server="http://room.local",
                agent_id="claude-haiku",
                display_name="Claude Haiku",
                provider_kind="claude_code",
                connection_kind="local_cli",
                session_id="",
                endpoint="",
                auth_ref="",
                meeting_id="",
                engagement_mode="always",
                command=["claude", "-p", "--model", "haiku"],
                timeout_seconds=30,
                poll_interval=0,
                heartbeat_interval=30,
                cooldown=0,
                max_chain_depth=1,
                max_ticks=1,
            )
        ]
        constructed = []

        class RecordingRunner:
            def __init__(self, *args, **kwargs):
                constructed.append((args, kwargs))

            def run(self):
                return 0

        stdout = StringIO()
        stderr = StringIO()
        with (
            patch("agentsassemble.cli.load_group_configs", return_value=configs),
            patch("agentsassemble.cli.LiveAgentRunner", RecordingRunner),
            patch("sys.stdout", stdout),
            patch("sys.stderr", stderr),
        ):
            exit_code = main(["live-agent", "--legacy-internal", "run-group", "--config", "ignored.json"])

        self.assertEqual(exit_code, 2)
        self.assertEqual(constructed, [])
        self.assertIn("claude-haiku", stderr.getvalue())
        self.assertIn("must not use Claude Code print/non-interactive mode", stderr.getvalue())
        self.assertNotIn("Resident group stopped", stdout.getvalue())

    def test_live_agent_run_group_rejects_duplicate_agent_ids_before_launch(self):
        configs = [
            ResidentAgentConfig(
                server="http://room.local",
                agent_id="duplicate-agent",
                display_name="Duplicate A",
                provider_kind="local_cli",
                connection_kind="local_cli",
                session_id="",
                endpoint="",
                auth_ref="",
                meeting_id="",
                engagement_mode="always",
                command=[sys.executable],
                timeout_seconds=30,
                poll_interval=0,
                heartbeat_interval=30,
                cooldown=0,
                max_chain_depth=1,
                max_ticks=1,
            ),
            ResidentAgentConfig(
                server="http://room.local",
                agent_id="duplicate-agent",
                display_name="Duplicate B",
                provider_kind="local_cli",
                connection_kind="local_cli",
                session_id="",
                endpoint="",
                auth_ref="",
                meeting_id="",
                engagement_mode="always",
                command=[sys.executable],
                timeout_seconds=30,
                poll_interval=0,
                heartbeat_interval=30,
                cooldown=0,
                max_chain_depth=1,
                max_ticks=1,
            ),
        ]
        constructed = []

        class RecordingRunner:
            def __init__(self, *args, **kwargs):
                constructed.append((args, kwargs))

            def run(self):
                return 0

        stderr = StringIO()
        with (
            patch("agentsassemble.cli.load_group_configs", return_value=configs),
            patch("agentsassemble.cli.LiveAgentRunner", RecordingRunner),
            patch("sys.stdout", StringIO()),
            patch("sys.stderr", stderr),
        ):
            exit_code = main(["live-agent", "--legacy-internal", "run-group", "--config", "ignored.json"])

        self.assertEqual(exit_code, 2)
        self.assertEqual(constructed, [])
        self.assertIn("duplicate-agent", stderr.getvalue())
        self.assertIn("Duplicate agent id", stderr.getvalue())

    def test_live_agent_run_group_does_not_register_any_agent_when_setup_fails(self):
        configs = [
            ResidentAgentConfig(
                server="http://room.local",
                agent_id="would-register",
                display_name="Would Register",
                provider_kind="local_cli",
                connection_kind="local_cli",
                session_id="",
                endpoint="",
                auth_ref="",
                meeting_id="",
                engagement_mode="always",
                command=["fake"],
                timeout_seconds=30,
                poll_interval=0,
                heartbeat_interval=30,
                cooldown=0,
                max_chain_depth=1,
                max_ticks=1,
            ),
            ResidentAgentConfig(
                server="http://room.local",
                agent_id="bad-bridge",
                display_name="Bad Bridge",
                provider_kind="claude_code",
                connection_kind="remote_bridge",
                session_id="",
                endpoint="https://friend.local:8777",
                auth_ref="literal:<redacted>",
                meeting_id="",
                engagement_mode="always",
                command=[],
                timeout_seconds=30,
                poll_interval=0,
                heartbeat_interval=30,
                cooldown=0,
                max_chain_depth=1,
                max_ticks=1,
            ),
        ]
        constructed = []

        class RecordingRunner:
            def __init__(self, *args, **kwargs):
                constructed.append(args)

            def run(self):
                return 0

        stderr = StringIO()
        with (
            patch("agentsassemble.cli.load_group_configs", return_value=configs),
            patch("agentsassemble.cli.LiveAgentRunner", RecordingRunner),
            patch("sys.stdout", StringIO()),
            patch("sys.stderr", stderr),
        ):
            exit_code = main(["live-agent", "--legacy-internal", "run-group", "--config", "ignored.json"])

        self.assertEqual(exit_code, 2)
        self.assertEqual(constructed, [])
        self.assertIn("bad-bridge", stderr.getvalue())

    def test_live_agent_run_group_shutdown_signal_closes_active_runners(self):
        configs = [
            ResidentAgentConfig(
                server="http://room.local",
                agent_id="signal-agent",
                display_name="Signal Agent",
                provider_kind="local_cli",
                connection_kind="local_cli",
                session_id="",
                endpoint="",
                auth_ref="",
                meeting_id="",
                engagement_mode="always",
                command=["fake"],
                timeout_seconds=30,
                poll_interval=0,
                heartbeat_interval=30,
                cooldown=0,
                max_chain_depth=1,
                max_ticks=0,
            ),
            ResidentAgentConfig(
                server="http://room.local",
                agent_id="sibling-blocked",
                display_name="Sibling Blocked",
                provider_kind="local_cli",
                connection_kind="local_cli",
                session_id="",
                endpoint="",
                auth_ref="",
                meeting_id="",
                engagement_mode="always",
                command=["fake"],
                timeout_seconds=30,
                poll_interval=0,
                heartbeat_interval=30,
                cooldown=0,
                max_chain_depth=1,
                max_ticks=0,
            ),
        ]
        sibling_started = threading.Event()
        sibling_closed_while_running = threading.Event()
        installed_shutdown = {}
        restored = threading.Event()

        class CloseRecordingRunner:
            def __init__(self, agent_id):
                self.agent_id = agent_id
                self.closed = False

            def close(self):
                self.closed = True

        class SignalInterruptingRunner:
            def __init__(self, config, *, command_runner, **kwargs):
                del kwargs
                self.config = config
                self.command_runner = command_runner

            def run(self):
                if self.config.agent_id == "signal-agent":
                    if not sibling_started.wait(timeout=1):
                        raise AssertionError("secondary runner did not start")
                    installed_shutdown["callback"]()
                    raise KeyboardInterrupt()
                sibling_started.set()
                deadline = time.time() + 0.5
                while time.time() < deadline:
                    if self.command_runner.closed:
                        sibling_closed_while_running.set()
                        return 0
                    time.sleep(0.01)
                return 0

        def command_runner_for_config(config):
            return CloseRecordingRunner(config.agent_id)

        def install_shutdown_handler(callback):
            installed_shutdown["callback"] = callback
            return lambda: restored.set()

        with (
            patch("agentsassemble.cli.load_group_configs", return_value=configs),
            patch("agentsassemble.cli.resident_config_setup_error", return_value=""),
            patch("agentsassemble.cli._command_runner_for_config", side_effect=command_runner_for_config),
            patch("agentsassemble.cli._install_resident_shutdown_signal_handlers", side_effect=install_shutdown_handler, create=True),
            patch("agentsassemble.cli.LiveAgentRunner", SignalInterruptingRunner),
            patch("sys.stdout", StringIO()),
            patch("sys.stderr", StringIO()),
        ):
            # stagger 0: this test asserts concurrent-runtime sibling/shutdown
            # semantics, not staggered startup ordering.
            exit_code = main(["live-agent", "--legacy-internal", "run-group", "--config", "ignored.json", "--launch-stagger-seconds", "0"])

        self.assertEqual(exit_code, 0)
        self.assertTrue(sibling_closed_while_running.is_set())
        self.assertTrue(restored.is_set())

    def test_live_agent_run_group_suppresses_secondary_errors_after_shutdown(self):
        configs = [
            ResidentAgentConfig(
                server="http://room.local",
                agent_id="primary-interrupt",
                display_name="Primary Interrupt",
                provider_kind="local_cli",
                connection_kind="local_cli",
                session_id="",
                endpoint="",
                auth_ref="",
                meeting_id="",
                engagement_mode="always",
                command=["fake"],
                timeout_seconds=30,
                poll_interval=0,
                heartbeat_interval=30,
                cooldown=0,
                max_chain_depth=1,
                max_ticks=0,
            ),
            ResidentAgentConfig(
                server="http://room.local",
                agent_id="secondary-stop",
                display_name="Secondary Stop",
                provider_kind="local_cli",
                connection_kind="local_cli",
                session_id="",
                endpoint="",
                auth_ref="",
                meeting_id="",
                engagement_mode="always",
                command=["fake"],
                timeout_seconds=30,
                poll_interval=0,
                heartbeat_interval=30,
                cooldown=0,
                max_chain_depth=1,
                max_ticks=0,
            ),
        ]

        class ShutdownAwareRunner:
            def __init__(self, config, *, stop_event, **kwargs):
                del kwargs
                self.config = config
                self.stop_event = stop_event

            def run(self):
                if self.config.agent_id == "primary-interrupt":
                    raise KeyboardInterrupt()
                while not self.stop_event.is_set():
                    time.sleep(0.01)
                raise RuntimeError("secondary closed during shutdown")

        stderr = StringIO()
        with (
            patch("agentsassemble.cli.load_group_configs", return_value=configs),
            patch("agentsassemble.cli.resident_config_setup_error", return_value=""),
            patch("agentsassemble.cli.LiveAgentRunner", ShutdownAwareRunner),
            patch("sys.stdout", StringIO()),
            patch("sys.stderr", stderr),
        ):
            exit_code = main(["live-agent", "--legacy-internal", "run-group", "--config", "ignored.json"])

        self.assertEqual(exit_code, 0)
        self.assertNotIn("secondary closed during shutdown", stderr.getvalue())

    def test_live_agent_run_group_reports_worker_system_exit_as_error(self):
        configs = [
            ResidentAgentConfig(
                server="http://room.local",
                agent_id="exiting-agent",
                display_name="Exiting Agent",
                provider_kind="local_cli",
                connection_kind="local_cli",
                session_id="",
                endpoint="",
                auth_ref="",
                meeting_id="",
                engagement_mode="always",
                command=["fake"],
                timeout_seconds=30,
                poll_interval=0,
                heartbeat_interval=30,
                cooldown=0,
                max_chain_depth=1,
                max_ticks=0,
            )
        ]

        class ExitingRunner:
            def __init__(self, *args, **kwargs):
                del args, kwargs

            def run(self):
                raise SystemExit("runner exited unexpectedly")

        stderr = StringIO()
        with (
            patch("agentsassemble.cli.load_group_configs", return_value=configs),
            patch("agentsassemble.cli.resident_config_setup_error", return_value=""),
            patch("agentsassemble.cli.LiveAgentRunner", ExitingRunner),
            patch("agentsassemble.cli._request_json", return_value={}),
            patch("sys.stdout", StringIO()),
            patch("sys.stderr", stderr),
        ):
            exit_code = main(["live-agent", "--legacy-internal", "run-group", "--config", "ignored.json"])

        self.assertEqual(exit_code, 2)
        self.assertIn("exiting-agent: runner exited unexpectedly", stderr.getvalue())

    def test_live_agent_run_group_worker_failure_reports_safe_presence_error(self):
        configs = [
            ResidentAgentConfig(
                server="http://room.local",
                agent_id="crashing-agent",
                display_name="Crashing Agent",
                provider_kind="local_cli",
                connection_kind="local_cli",
                session_id="",
                endpoint="",
                auth_ref="",
                meeting_id="",
                engagement_mode="always",
                command=["fake"],
                timeout_seconds=30,
                poll_interval=0,
                heartbeat_interval=30,
                cooldown=0,
                max_chain_depth=1,
                max_ticks=0,
            )
        ]
        requests = []

        class CrashingRunner:
            def __init__(self, *args, **kwargs):
                del args, kwargs

            def run(self):
                raise RuntimeError("worker failed with token=secret-token in /Users/me/private/live-agents.json")

        def request_json(url, **kwargs):
            requests.append((url, kwargs))
            return {"agent": {"agent_id": "crashing-agent"}}

        stderr = StringIO()
        with (
            patch("agentsassemble.cli.load_group_configs", return_value=configs),
            patch("agentsassemble.cli.resident_config_setup_error", return_value=""),
            patch("agentsassemble.cli.LiveAgentRunner", CrashingRunner),
            patch("agentsassemble.cli._request_json", side_effect=request_json),
            patch("sys.stdout", StringIO()),
            patch("sys.stderr", stderr),
        ):
            exit_code = main(["live-agent", "--legacy-internal", "run-group", "--config", "ignored.json"])

        self.assertEqual(exit_code, 2)
        self.assertIn("crashing-agent", stderr.getvalue())
        heartbeat_requests = [
            kwargs["payload"]
            for url, kwargs in requests
            if url == "http://room.local/api/live-agents/crashing-agent/heartbeat"
        ]
        self.assertEqual(
            heartbeat_requests,
            [{"status": "error", "last_error": "Resident worker error details redacted."}],
        )
        self.assertNotIn("secret-token", json.dumps(heartbeat_requests))
        self.assertNotIn("/Users/me/private/live-agents.json", json.dumps(heartbeat_requests))

    def test_live_agent_run_group_worker_failure_keeps_plain_exception_text_out_of_presence(self):
        configs = [
            ResidentAgentConfig(
                server="http://room.local",
                agent_id="plain-crash",
                display_name="Plain Crash",
                provider_kind="local_cli",
                connection_kind="local_cli",
                session_id="",
                endpoint="",
                auth_ref="",
                meeting_id="",
                engagement_mode="always",
                command=["fake"],
                timeout_seconds=30,
                poll_interval=0,
                heartbeat_interval=30,
                cooldown=0,
                max_chain_depth=1,
                max_ticks=0,
            )
        ]
        requests = []

        class CrashingRunner:
            def __init__(self, *args, **kwargs):
                del args, kwargs

            def run(self):
                raise RuntimeError("provider output: model returned confidential draft")

        def request_json(url, **kwargs):
            requests.append((url, kwargs))
            return {"agent": {"agent_id": "plain-crash"}}

        with (
            patch("agentsassemble.cli.load_group_configs", return_value=configs),
            patch("agentsassemble.cli.resident_config_setup_error", return_value=""),
            patch("agentsassemble.cli.LiveAgentRunner", CrashingRunner),
            patch("agentsassemble.cli._request_json", side_effect=request_json),
            patch("sys.stdout", StringIO()),
            patch("sys.stderr", StringIO()),
        ):
            exit_code = main(["live-agent", "--legacy-internal", "run-group", "--config", "ignored.json"])

        self.assertEqual(exit_code, 2)
        heartbeat_requests = [
            kwargs["payload"]
            for url, kwargs in requests
            if url == "http://room.local/api/live-agents/plain-crash/heartbeat"
        ]
        self.assertEqual(
            heartbeat_requests,
            [{"status": "error", "last_error": "Resident worker failed with RuntimeError."}],
        )
        self.assertNotIn("provider output", json.dumps(heartbeat_requests))
        self.assertNotIn("confidential draft", json.dumps(heartbeat_requests))

    def test_live_agent_run_group_closes_sibling_runners_after_worker_keyboard_interrupt(self):
        configs = [
            ResidentAgentConfig(
                server="http://room.local",
                agent_id="interrupting-agent",
                display_name="Interrupting Agent",
                provider_kind="local_cli",
                connection_kind="local_cli",
                session_id="",
                endpoint="",
                auth_ref="",
                meeting_id="",
                engagement_mode="always",
                command=["fake"],
                timeout_seconds=30,
                poll_interval=0,
                heartbeat_interval=30,
                cooldown=0,
                max_chain_depth=1,
                max_ticks=0,
            ),
            ResidentAgentConfig(
                server="http://room.local",
                agent_id="sibling-blocked",
                display_name="Sibling Blocked",
                provider_kind="local_cli",
                connection_kind="local_cli",
                session_id="",
                endpoint="",
                auth_ref="",
                meeting_id="",
                engagement_mode="always",
                command=["fake"],
                timeout_seconds=30,
                poll_interval=0,
                heartbeat_interval=30,
                cooldown=0,
                max_chain_depth=1,
                max_ticks=0,
            ),
        ]
        runners = {}
        sibling_started = threading.Event()
        sibling_closed_while_running = threading.Event()

        class CloseRecordingRunner:
            def __init__(self, agent_id):
                self.agent_id = agent_id
                self.closed = False

            def close(self):
                self.closed = True

        class InterruptingRunner:
            def __init__(self, config, *, command_runner, **kwargs):
                del kwargs
                self.config = config
                self.command_runner = command_runner

            def run(self):
                if self.config.agent_id == "interrupting-agent":
                    if not sibling_started.wait(timeout=1):
                        raise AssertionError("secondary runner did not start")
                    raise KeyboardInterrupt()
                sibling_started.set()
                deadline = time.time() + 0.5
                while time.time() < deadline:
                    if self.command_runner.closed:
                        sibling_closed_while_running.set()
                        return 0
                    time.sleep(0.01)
                return 0

        def command_runner_for_config(config):
            runner = CloseRecordingRunner(config.agent_id)
            runners[config.agent_id] = runner
            return runner

        with (
            patch("agentsassemble.cli.load_group_configs", return_value=configs),
            patch("agentsassemble.cli.resident_config_setup_error", return_value=""),
            patch("agentsassemble.cli._command_runner_for_config", side_effect=command_runner_for_config),
            patch("agentsassemble.cli.LiveAgentRunner", InterruptingRunner),
            patch("sys.stdout", StringIO()),
            patch("sys.stderr", StringIO()),
        ):
            # stagger 0: this test asserts concurrent-runtime sibling/shutdown
            # semantics, not staggered startup ordering.
            exit_code = main(["live-agent", "--legacy-internal", "run-group", "--config", "ignored.json", "--launch-stagger-seconds", "0"])

        self.assertEqual(exit_code, 0)
        self.assertTrue(sibling_closed_while_running.is_set())

    def test_live_agent_run_group_stagger_skips_remaining_starts_after_shutdown(self):
        # With a launch stagger, residents start one at a time. If a shutdown
        # arrives during the stagger wait, the not-yet-started residents must
        # never launch (serialized startup, bail-on-shutdown).
        def _cfg(agent_id):
            return ResidentAgentConfig(
                server="http://room.local",
                agent_id=agent_id,
                display_name=agent_id,
                provider_kind="local_cli",
                connection_kind="local_cli",
                session_id="",
                endpoint="",
                auth_ref="",
                meeting_id="",
                engagement_mode="always",
                command=["fake"],
                timeout_seconds=30,
                poll_interval=0,
                heartbeat_interval=30,
                cooldown=0,
                max_chain_depth=1,
                max_ticks=0,
            )

        configs = [_cfg("first-agent"), _cfg("second-agent")]
        created_runners: list[str] = []
        first_started = threading.Event()

        class _Runner:
            def close(self):
                pass

        class ShutdownTriggeringRunner:
            def __init__(self, config, *, command_runner, **kwargs):
                del kwargs, command_runner
                self.config = config

            def run(self):
                # The first resident requests shutdown immediately; the second
                # must never be reached because of the (large) stagger wait.
                if self.config.agent_id == "first-agent":
                    first_started.set()
                    raise KeyboardInterrupt()
                return 0

        def command_runner_for_config(config):
            created_runners.append(config.agent_id)
            return _Runner()

        with (
            patch("agentsassemble.cli.load_group_configs", return_value=configs),
            patch("agentsassemble.cli.resident_config_setup_error", return_value=""),
            patch("agentsassemble.cli._command_runner_for_config", side_effect=command_runner_for_config),
            patch("agentsassemble.cli.LiveAgentRunner", ShutdownTriggeringRunner),
            patch("sys.stdout", StringIO()),
            patch("sys.stderr", StringIO()),
        ):
            # A large stagger would block for 30s if the shutdown were ignored;
            # the test returns promptly because stop_event interrupts the wait.
            exit_code = main(["live-agent", "--legacy-internal", "run-group", "--config", "ignored.json", "--launch-stagger-seconds", "30"])

        self.assertTrue(first_started.is_set())
        self.assertEqual(exit_code, 0)
        self.assertEqual(created_runners, ["first-agent"])

    def test_live_agent_run_group_keeps_sibling_runner_after_primary_failure(self):
        configs = [
            ResidentAgentConfig(
                server="http://room.local",
                agent_id="primary-error",
                display_name="Primary Error",
                provider_kind="local_cli",
                connection_kind="local_cli",
                session_id="",
                endpoint="",
                auth_ref="",
                meeting_id="",
                engagement_mode="always",
                command=["fake"],
                timeout_seconds=30,
                poll_interval=0,
                heartbeat_interval=30,
                cooldown=0,
                max_chain_depth=1,
                max_ticks=0,
            ),
            ResidentAgentConfig(
                server="http://room.local",
                agent_id="secondary-blocked",
                display_name="Secondary Blocked",
                provider_kind="local_cli",
                connection_kind="local_cli",
                session_id="",
                endpoint="",
                auth_ref="",
                meeting_id="",
                engagement_mode="always",
                command=["fake"],
                timeout_seconds=30,
                poll_interval=0,
                heartbeat_interval=30,
                cooldown=0,
                max_chain_depth=1,
                max_ticks=0,
            ),
        ]
        runners = {}
        sibling_started = threading.Event()
        sibling_closed_while_running = threading.Event()
        sibling_finished = threading.Event()

        class CloseRecordingRunner:
            def __init__(self, agent_id):
                self.agent_id = agent_id
                self.closed = False

            def close(self):
                self.closed = True

        class BlockingSiblingRunner:
            def __init__(self, config, *, command_runner, **kwargs):
                del kwargs
                self.config = config
                self.command_runner = command_runner

            def run(self):
                if self.config.agent_id == "primary-error":
                    if not sibling_started.wait(timeout=1):
                        raise AssertionError("secondary runner did not start")
                    raise RuntimeError("primary boom")
                sibling_started.set()
                deadline = time.time() + 0.5
                while time.time() < deadline:
                    if self.command_runner.closed:
                        sibling_closed_while_running.set()
                        return 0
                    time.sleep(0.01)
                sibling_finished.set()
                return 3

        def command_runner_for_config(config):
            runner = CloseRecordingRunner(config.agent_id)
            runners[config.agent_id] = runner
            return runner

        stderr = StringIO()
        with (
            patch("agentsassemble.cli.load_group_configs", return_value=configs),
            patch("agentsassemble.cli.resident_config_setup_error", return_value=""),
            patch("agentsassemble.cli._command_runner_for_config", side_effect=command_runner_for_config),
            patch("agentsassemble.cli.LiveAgentRunner", BlockingSiblingRunner),
            patch("agentsassemble.cli._request_json", return_value={}),
            patch("sys.stdout", StringIO()),
            patch("sys.stderr", stderr),
        ):
            # stagger 0: this test asserts concurrent-runtime sibling/shutdown
            # semantics, not staggered startup ordering.
            exit_code = main(["live-agent", "--legacy-internal", "run-group", "--config", "ignored.json", "--launch-stagger-seconds", "0"])

        self.assertEqual(exit_code, 2)
        self.assertIn("primary-error: primary boom", stderr.getvalue())
        self.assertTrue(sibling_finished.is_set())
        self.assertFalse(sibling_closed_while_running.is_set())
