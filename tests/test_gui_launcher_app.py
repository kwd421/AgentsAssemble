import unittest
from pathlib import Path


class GuiLauncherAppTests(unittest.TestCase):
    def test_terminal_free_launcher_starts_public_invite_ready_gui(self):
        root = Path(__file__).resolve().parents[1]
        launcher = root / "Open AgentsAssemble Room.app" / "Contents" / "MacOS" / "open-agentsassemble-room"

        script = launcher.read_text(encoding="utf-8")

        self.assertIn("HOST_TOKEN_FILE", script)
        self.assertIn("--host-token", script)
        self.assertIn("--start-public-tunnel", script)
        self.assertIn("server_public_ready", script)
        self.assertIn("start_existing_public_tunnel", script)
        self.assertIn("start_new_session=True", script)
        self.assertIn("AGENTSASSEMBLE_PUBLIC_TUNNEL:-1", script)


if __name__ == "__main__":
    unittest.main()
