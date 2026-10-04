from __future__ import annotations

import ast
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CURRENT_INVITE_APPLICATION_MODULES = (
    "agentsassemble/application/gui.py",
    "agentsassemble/application/gui_factory.py",
    "agentsassemble/web/routes/public_invite.py",
    "agentsassemble/web/security.py",
    "agentsassemble/web/routes/room_members.py",
    "agentsassemble/web/router.py",
    "agentsassemble/application/public_tunnel.py",
    "agentsassemble/room_admission.py",
    "agentsassemble/admission/coordinator.py",
    "agentsassemble/admission/saga.py",
    "agentsassemble/admission/invite_service.py",
    "agentsassemble/room/realtime.py",
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
            relative_path: "agentsassemble.admission.invite"
            for relative_path in CURRENT_INVITE_APPLICATION_MODULES
            if "agentsassemble.admission.invite" in _imported_modules(relative_path)
        }

        self.assertEqual(offenders, {})

if __name__ == "__main__":
    unittest.main()
