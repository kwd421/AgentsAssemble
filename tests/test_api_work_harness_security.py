from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path

from agentsassemble.providers.api_work_tool_schemas import work_tool_schemas
from agentsassemble.providers.api_work_tools import ApiWorkHarness


class ApiWorkHarnessSecurityTests(unittest.TestCase):
    def test_search_skips_oversized_files_and_reports_only_bounded_results(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir) / "workspace"
            workspace.mkdir()
            oversized = workspace / "oversized.txt"
            with oversized.open("wb") as stream:
                stream.truncate(2_000_000)
            (workspace / "small.txt").write_text(
                "bounded-search-target\n",
                encoding="utf-8",
            )
            harness = ApiWorkHarness(
                workspace,
                permission_mode="workspace_write",
            )

            started = time.monotonic()
            result = harness.execute(
                "search_workspace_text",
                {"path": ".", "query": "bounded-search-target"},
            )

            self.assertLess(time.monotonic() - started, 1.0)
            self.assertEqual(
                result["matches"],
                [
                    {
                        "path": "small.txt",
                        "line": 1,
                        "text": "bounded-search-target",
                    }
                ],
            )

    def test_workspace_discovery_stops_when_the_action_is_interrupted(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir) / "workspace"
            workspace.mkdir()
            (workspace / "never-read.txt").write_text("content", encoding="utf-8")
            harness = ApiWorkHarness(
                workspace,
                permission_mode="workspace_write",
                interrupt_requested=lambda: True,
            )

            with self.assertRaisesRegex(RuntimeError, "interrupted"):
                harness.execute("list_workspace_files", {"path": "."})

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

    def test_workspace_command_is_neither_advertised_nor_executable(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            workspace = root / "workspace"
            workspace.mkdir()
            outside_marker = root / "outside-marker"
            approvals: list[dict[str, object]] = []
            harness = ApiWorkHarness(
                workspace,
                permission_mode="workspace_write",
                request_handler=lambda request, _respond: approvals.append(request),
            )

            advertised = {
                str(schema.get("function", {}).get("name") or "")
                for schema in work_tool_schemas("workspace_write")
                if isinstance(schema.get("function"), dict)
            }
            with self.assertRaisesRegex(RuntimeError, "disabled"):
                harness.execute(
                    "run_workspace_command",
                    {
                        "command": [
                            "python3",
                            "-c",
                            f"from pathlib import Path; Path({str(outside_marker)!r}).write_text('escaped')",
                        ]
                    },
                )

            self.assertNotIn("run_workspace_command", advertised)
            self.assertEqual(approvals, [])
            self.assertFalse(outside_marker.exists())


if __name__ == "__main__":
    unittest.main()
