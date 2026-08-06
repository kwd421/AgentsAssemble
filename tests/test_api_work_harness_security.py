from __future__ import annotations

import os
import signal
import sys
import tempfile
import time
import unittest
from pathlib import Path

from agentsassemble.providers.api_work_tools import ApiWorkHarness


class ApiWorkHarnessSecurityTests(unittest.TestCase):
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

    @unittest.skipUnless(hasattr(os, "killpg") and hasattr(os, "setsid"), "POSIX process groups required")
    def test_timed_out_command_terminates_its_descendant_processes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            child_pid_file = workspace / "child.pid"
            harness = ApiWorkHarness(
                workspace,
                permission_mode="workspace_write",
                request_handler=lambda _request, respond: respond(
                    {"option_id": "allow_once"}
                ),
            )
            script = (
                "import pathlib, subprocess, sys, time; "
                "child=subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)']); "
                "pathlib.Path('child.pid').write_text(str(child.pid)); "
                "time.sleep(60)"
            )
            child_pid = 0
            try:
                with self.assertRaises(TimeoutError):
                    harness.execute(
                        "run_workspace_command",
                        {
                            "command": [sys.executable, "-c", script],
                            "timeout_seconds": 1,
                        },
                    )
                deadline = time.monotonic() + 3.0
                while not child_pid_file.exists() and time.monotonic() < deadline:
                    time.sleep(0.02)
                child_pid = int(child_pid_file.read_text(encoding="utf-8"))
                while _process_exists(child_pid) and time.monotonic() < deadline:
                    time.sleep(0.02)
                self.assertFalse(_process_exists(child_pid))
            finally:
                if child_pid and _process_exists(child_pid):
                    os.kill(child_pid, signal.SIGKILL)


def _process_exists(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


if __name__ == "__main__":
    unittest.main()
