from __future__ import annotations

import io
import json
import os
import signal
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

from agentsassemble.providers.api_work_tools import ApiWorkHarness
from agentsassemble.providers.remote_openai import (
    RemoteOpenAICompatibleRuntime,
    remote_openai_profile,
)


class _Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()


class ApiWorkHarnessSecurityTests(unittest.TestCase):
    @unittest.skipUnless(sys.platform == "darwin", "macOS command containment required")
    def test_command_exceeding_output_budget_is_stopped_before_timeout(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            harness = ApiWorkHarness(
                workspace,
                permission_mode="workspace_write",
                request_handler=lambda _request, respond: respond(
                    {"option_id": "allow_once"}
                ),
            )
            started = time.monotonic()

            with self.assertRaisesRegex(RuntimeError, "output limit"):
                harness.execute(
                    "run_workspace_command",
                    {
                        "command": [
                            sys.executable,
                            "-c",
                            (
                                "import sys, time; "
                                "sys.stdout.write('x' * 2000000); "
                                "sys.stdout.flush(); "
                                "time.sleep(30)"
                            ),
                        ],
                        "timeout_seconds": 8,
                    },
                )

            self.assertLess(time.monotonic() - started, 4.0)

    def test_approved_write_cannot_follow_a_directory_replaced_by_a_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            workspace = root / "workspace"
            safe_directory = workspace / "safe"
            outside = root / "outside"
            safe_directory.mkdir(parents=True)
            outside.mkdir()

            def swap_path(_request, respond) -> None:
                safe_directory.rmdir()
                try:
                    safe_directory.symlink_to(outside, target_is_directory=True)
                except OSError as error:
                    self.skipTest(f"symlinks are unavailable: {error}")
                respond({"option_id": "allow_once"})

            harness = ApiWorkHarness(
                workspace,
                permission_mode="workspace_write",
                request_handler=swap_path,
            )

            with self.assertRaises((OSError, ValueError)):
                harness.execute(
                    "write_workspace_file",
                    {"path": "safe/owned.txt", "content": "must stay inside"},
                )

            self.assertFalse((outside / "owned.txt").exists())

    @unittest.skipUnless(sys.platform == "darwin", "macOS command containment required")
    def test_workspace_command_cannot_create_descendant_processes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            harness = ApiWorkHarness(
                workspace,
                permission_mode="workspace_write",
                request_handler=lambda _request, respond: respond(
                    {"option_id": "allow_once"}
                ),
            )
            script = (
                "import subprocess, sys; "
                "subprocess.run([sys.executable, '-c', 'print(123)'], check=True)"
            )
            result = harness.execute(
                "run_workspace_command",
                {
                    "command": [sys.executable, "-c", script],
                    "timeout_seconds": 3,
                },
            )

            self.assertNotEqual(result["exit_code"], 0)
            self.assertIn("Operation not permitted", result["stderr"])

    @unittest.skipUnless(sys.platform == "darwin", "macOS command containment required")
    def test_escape_attempt_cannot_leave_a_child_holding_output_pipes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            child_pid_file = workspace / "escaped.pid"
            harness = ApiWorkHarness(
                workspace,
                permission_mode="workspace_write",
                request_handler=lambda _request, respond: respond(
                    {"option_id": "allow_once"}
                ),
            )
            script = (
                "import pathlib, subprocess, sys; "
                "child=subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(6)'], "
                "start_new_session=True); "
                "pathlib.Path('escaped.pid').write_text(str(child.pid))"
            )
            started = time.monotonic()
            result = harness.execute(
                "run_workspace_command",
                {
                    "command": [sys.executable, "-c", script],
                    "timeout_seconds": 3,
                },
            )

            self.assertLess(time.monotonic() - started, 2.0)
            self.assertNotEqual(result["exit_code"], 0)
            self.assertFalse(child_pid_file.exists())

    @unittest.skipUnless(sys.platform == "darwin", "macOS command containment required")
    def test_interrupt_terminates_the_active_workspace_command(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            pid_file = workspace / "command.pid"
            harness = ApiWorkHarness(
                workspace,
                permission_mode="workspace_write",
                request_handler=lambda _request, respond: respond(
                    {"option_id": "allow_once"}
                ),
            )
            failures: list[BaseException] = []

            def execute() -> None:
                try:
                    harness.execute(
                        "run_workspace_command",
                        {
                            "command": [
                                sys.executable,
                                "-c",
                                (
                                    "import os, pathlib, time; "
                                    "pathlib.Path('command.pid').write_text(str(os.getpid())); "
                                    "time.sleep(60)"
                                ),
                            ],
                            "timeout_seconds": 120,
                        },
                    )
                except BaseException as error:
                    failures.append(error)

            worker = threading.Thread(target=execute, daemon=True)
            worker.start()
            deadline = time.monotonic() + 3.0
            while not pid_file.exists() and time.monotonic() < deadline:
                time.sleep(0.02)
            self.assertTrue(pid_file.exists())
            command_pid = int(pid_file.read_text(encoding="utf-8"))
            try:
                self.assertTrue(harness.interrupt())
                worker.join(timeout=3.0)
                self.assertFalse(worker.is_alive())
                self.assertFalse(_process_exists(command_pid))
                self.assertTrue(failures)
                self.assertIn("interrupted", str(failures[0]).casefold())
            finally:
                if _process_exists(command_pid):
                    os.kill(command_pid, signal.SIGKILL)
                worker.join(timeout=1.0)

    @unittest.skipUnless(sys.platform == "darwin", "macOS command containment required")
    def test_runtime_interrupt_reaches_an_active_api_workspace_command(self) -> None:
        profile = remote_openai_profile("tokenrouter")
        self.assertIsNotNone(profile)

        def opener(_request, timeout: float):
            del timeout
            return _tool_call_response(
                "call-run",
                "run_workspace_command",
                {
                    "command": [
                        sys.executable,
                        "-c",
                        (
                            "import os, pathlib, time; "
                            "pathlib.Path('command.pid').write_text(str(os.getpid())); "
                            "time.sleep(60)"
                        ),
                    ],
                    "timeout_seconds": 120,
                },
            )

        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            pid_file = workspace / "command.pid"
            runtime = RemoteOpenAICompatibleRuntime(
                "tokenrouter-interrupt",
                profile=profile,
                api_key="test-key",
                model="moonshotai/kimi-k3-free",
                opener=opener,
                workspace=str(workspace),
                permission_mode="workspace_write",
            )
            runtime.set_request_handler(
                lambda _request, respond: respond({"option_id": "allow_once"})
            )
            runtime.send("오래 실행되는 명령을 실행해 줘.")
            failures: list[BaseException] = []

            def read_output() -> None:
                try:
                    runtime.read_output(timeout_seconds=120)
                except BaseException as error:
                    failures.append(error)

            worker = threading.Thread(target=read_output, daemon=True)
            worker.start()
            deadline = time.monotonic() + 3.0
            while not pid_file.exists() and time.monotonic() < deadline:
                time.sleep(0.02)
            self.assertTrue(pid_file.exists())
            command_pid = int(pid_file.read_text(encoding="utf-8"))
            try:
                runtime.interrupt()
                worker.join(timeout=3.0)
                self.assertFalse(worker.is_alive())
                self.assertTrue(failures)
                self.assertIn("interrupted", str(failures[0]).casefold())
                self.assertFalse(_process_exists(command_pid))
            finally:
                if _process_exists(command_pid):
                    os.kill(command_pid, signal.SIGKILL)
                worker.join(timeout=1.0)

    @unittest.skipIf(sys.platform == "darwin", "macOS provides command containment")
    def test_command_execution_fails_closed_without_verified_containment(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            harness = ApiWorkHarness(
                Path(temp_dir),
                permission_mode="workspace_write",
                request_handler=lambda _request, respond: respond(
                    {"option_id": "allow_once"}
                ),
            )

            with self.assertRaisesRegex(RuntimeError, "verified child-process containment"):
                harness.execute(
                    "run_workspace_command",
                    {"command": [sys.executable, "-c", "print('unsafe')"]},
                )


def _process_exists(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def _tool_call_response(
    call_id: str,
    name: str,
    arguments: dict[str, object],
) -> _Response:
    chunk = {
        "choices": [
            {
                "delta": {
                    "tool_calls": [
                        {
                            "index": 0,
                            "id": call_id,
                            "type": "function",
                            "function": {
                                "name": name,
                                "arguments": json.dumps(
                                    arguments,
                                    ensure_ascii=False,
                                ),
                            },
                        }
                    ]
                }
            }
        ]
    }
    body = f"data: {json.dumps(chunk, ensure_ascii=False)}\n\ndata: [DONE]\n\n"
    return _Response(body.encode())


if __name__ == "__main__":
    unittest.main()
