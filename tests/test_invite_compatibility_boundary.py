from __future__ import annotations

import ast
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CURRENT_INVITE_APPLICATION_MODULES = (
    "agentsassemble/gui_application.py",
    "agentsassemble/room_admission.py",
    "agentsassemble/room_admission_coordinator.py",
    "agentsassemble/room_admission_saga.py",
    "agentsassemble/room_invite_application.py",
)


def _imported_modules(relative_path: str) -> set[str]:
    tree = ast.parse((ROOT / relative_path).read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    return imported


class InviteCompatibilityBoundaryTests(unittest.TestCase):
    def test_current_invite_application_does_not_depend_on_global_facade(self) -> None:
        offenders = {
            relative_path: "agentsassemble.room_invite"
            for relative_path in CURRENT_INVITE_APPLICATION_MODULES
            if "agentsassemble.room_invite" in _imported_modules(relative_path)
        }

        self.assertEqual(offenders, {})


if __name__ == "__main__":
    unittest.main()
