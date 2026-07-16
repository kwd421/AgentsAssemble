import json
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest.mock import patch

from agentsassemble.providers.cursor_resident import (
    CURSOR_SUBPROCESS_NONZERO,
    CursorResidentCommandRunner,
    cursor_error_category,
)
from agentsassemble.live_agent_runner import ResidentAgentConfig


def config(**overrides):
    values = {
        "server": "http://room.local",
        "agent_id": "cursor-live",
        "display_name": "Cursor Live",
        "provider_kind": "cursor_live_session",
        "connection_kind": "live_session",
        "session_id": "",
        "endpoint": "",
        "auth_ref": "",
        "meeting_id": "",
        "engagement_mode": "moderator_called",
        "command": ["cursor-agent"],
        "timeout_seconds": 5,
        "poll_interval": 0.05,
        "heartbeat_interval": 0.0,
        "cooldown": 0.0,
        "max_chain_depth": 0,
        "max_ticks": 1,
    }
    values.update(overrides)
    return ResidentAgentConfig(**values)


class CursorLiveSessionLifecycleTests(unittest.TestCase):
    def test_fake_cursor_resume_preserves_workspace_across_turns(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            fake_cursor = temp_path / "cursor-agent"
            fake_log = temp_path / "fake-cursor.jsonl"
            state_file = temp_path / "workspace-state.txt"
            _write_fake_cursor_executable(fake_cursor)
            runner = CursorResidentCommandRunner(config(command=[str(fake_cursor)]), cwd=temp_path)
            try:
                with patch.dict(
                    os.environ,
                    {
                        "AGENTSASSEMBLE_FAKE_CURSOR_LOG": str(fake_log),
                        "AGENTSASSEMBLE_FAKE_CURSOR_STATE": str(state_file),
                    },
                    clear=False,
                ):
                    self.assertEqual(runner([], "store FIRST", timeout_seconds=5), "READY")
                    self.assertEqual(runner([], "recall", timeout_seconds=5), "C123")
                    workspace = runner.workspace_dir
                    self.assertTrue(workspace.exists())
            finally:
                runner.close()

            invocations = [
                json.loads(line)
                for line in fake_log.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            self.assertEqual([entry["mode"] for entry in invocations], ["create", "resume", "resume"])
            workspaces = [entry["workspace"] for entry in invocations if entry["mode"] == "resume"]
            self.assertEqual(len(set(workspaces)), 1)
            self.assertEqual(invocations[1]["resume_id"], "cursor-fake-chat-001")
            self.assertNotIn("prompt", invocations[1])
            self.assertFalse(workspace.exists())

    def test_fake_cursor_workspace_mismatch_surfaces_safe_nonzero(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            fake_cursor = temp_path / "cursor-agent"
            fake_log = temp_path / "fake-cursor.jsonl"
            state_file = temp_path / "workspace-state.txt"
            _write_fake_cursor_executable(fake_cursor)
            runner = CursorResidentCommandRunner(config(command=[str(fake_cursor)]), cwd=temp_path)
            try:
                with patch.dict(
                    os.environ,
                    {
                        "AGENTSASSEMBLE_FAKE_CURSOR_LOG": str(fake_log),
                        "AGENTSASSEMBLE_FAKE_CURSOR_STATE": str(state_file),
                    },
                    clear=False,
                ):
                    self.assertEqual(runner([], "store FIRST", timeout_seconds=5), "READY")
                    runner._workspace_dir.cleanup()
                    runner._workspace_dir = tempfile.TemporaryDirectory(prefix="agentsassemble-cursor-resident-workspace-")
                    with self.assertRaisesRegex(RuntimeError, "return code 2") as caught:
                        runner([], "recall", timeout_seconds=5)
            finally:
                runner.close()

            self.assertEqual(cursor_error_category(caught.exception), CURSOR_SUBPROCESS_NONZERO)


def _write_fake_cursor_executable(path: Path) -> None:
    script = textwrap.dedent(
        """\
        #!/usr/bin/env python3
        import json
        import os
        import sys
        from pathlib import Path

        log_path = Path(os.environ["AGENTSASSEMBLE_FAKE_CURSOR_LOG"])
        state_path = Path(os.environ["AGENTSASSEMBLE_FAKE_CURSOR_STATE"])

        def log(payload):
            with log_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(payload, sort_keys=True) + "\\n")

        args = sys.argv[1:]
        if args == ["create-chat"]:
            log({"mode": "create"})
            print("cursor-fake-chat-001")
            raise SystemExit(0)

        resume_id = args[args.index("--resume") + 1]
        workspace = args[args.index("--workspace") + 1]
        log({"mode": "resume", "resume_id": resume_id, "workspace": workspace, "stdin_length": len(sys.stdin.read())})
        if state_path.exists():
            expected_workspace = state_path.read_text(encoding="utf-8").strip()
            if workspace != expected_workspace:
                print("workspace mismatch with private prompt", file=sys.stderr)
                raise SystemExit(2)
            print("C123")
        else:
            state_path.write_text(workspace, encoding="utf-8")
            print("READY")
        """
    )
    path.write_text(script, encoding="utf-8")
    path.chmod(0o755)


if __name__ == "__main__":
    unittest.main()
