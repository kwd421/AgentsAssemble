import unittest

from agentsassemble.providers.sandbox_launcher import (
    CodexReadonlyLauncher,
    CodexWorkspaceWriteLauncher,
    NoSandboxLauncher,
    sandbox_launcher_for,
)


class TestSandboxLauncherFor(unittest.TestCase):
    def test_codex_defaults_to_readonly_without_opt_in(self):
        launcher = sandbox_launcher_for("codex_live_session", "live_session")
        self.assertIsInstance(launcher, CodexReadonlyLauncher)
        self.assertEqual(
            launcher.command(["codex"]),
            [
                "codex",
                "exec",
                "--sandbox",
                "read-only",
                "--ignore-user-config",
                "--ignore-rules",
            ],
        )

    def test_codex_workspace_write_opt_in(self):
        launcher = sandbox_launcher_for("codex_live_session", "live_session", sandbox="workspace-write")
        self.assertIsInstance(launcher, CodexWorkspaceWriteLauncher)
        self.assertEqual(launcher.enforcement, "codex_workspace_write")
        cmd = launcher.command(["codex"])
        self.assertEqual(cmd[:2], ["codex", "exec"])
        self.assertIn("workspace-write", cmd)
        self.assertNotIn("read-only", cmd)
        self.assertNotIn("danger-full-access", cmd)

    def test_non_codex_ignores_sandbox_opt_in(self):
        # workspace-write only applies to codex; others stay advisory/no-sandbox.
        launcher = sandbox_launcher_for("cursor", "live_session", sandbox="workspace-write")
        self.assertIsInstance(launcher, NoSandboxLauncher)
        self.assertEqual(launcher.command(["cursor-agent"]), ["cursor-agent"])


if __name__ == "__main__":
    unittest.main()
