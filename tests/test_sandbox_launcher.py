import unittest

from agentsassemble.providers.sandbox_launcher import (
    CODEX_EXEC_SAFETY_FLAGS,
    CODEX_EXEC_WORKSPACE_WRITE_FLAGS,
    SANDBOX_ENFORCEMENT_LEVELS,
    CodexReadonlyLauncher,
    CodexWorkspaceWriteLauncher,
    NoSandboxLauncher,
    sandbox_launcher_for,
    safe_sandbox_enforcement,
)


class TestSandboxLauncherFor(unittest.TestCase):
    def test_codex_live_session_codex_resume(self):
        launcher = sandbox_launcher_for("codex_live_session", "codex_resume")
        self.assertIsInstance(launcher, CodexReadonlyLauncher)
        self.assertEqual(launcher.enforcement, "codex_readonly")

    def test_codex_defaults_to_readonly_without_opt_in(self):
        launcher = sandbox_launcher_for("codex_live_session", "live_session")
        self.assertIsInstance(launcher, CodexReadonlyLauncher)
        self.assertIn("read-only", launcher.command(["codex"]))

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

    def test_codex_live_session_live_session(self):
        launcher = sandbox_launcher_for("codex_live_session", "live_session")
        self.assertIsInstance(launcher, CodexReadonlyLauncher)

    def test_codex_codex_resume(self):
        launcher = sandbox_launcher_for("codex", "codex_resume")
        self.assertIsInstance(launcher, CodexReadonlyLauncher)

    def test_codex_non_resume_returns_advisory(self):
        launcher = sandbox_launcher_for("codex", "live_session")
        self.assertIsInstance(launcher, NoSandboxLauncher)
        self.assertEqual(launcher.enforcement, "advisory")

    def test_unknown_provider_returns_advisory(self):
        launcher = sandbox_launcher_for("cursor", "live_session")
        self.assertIsInstance(launcher, NoSandboxLauncher)

    def test_empty_strings_return_advisory(self):
        launcher = sandbox_launcher_for("", "")
        self.assertIsInstance(launcher, NoSandboxLauncher)

    def test_none_inputs_return_advisory(self):
        launcher = sandbox_launcher_for(None, None)
        self.assertIsInstance(launcher, NoSandboxLauncher)

    def test_numeric_inputs_return_advisory(self):
        launcher = sandbox_launcher_for(123, 456)
        self.assertIsInstance(launcher, NoSandboxLauncher)

    def test_whitespace_stripped(self):
        launcher = sandbox_launcher_for("  codex_live_session  ", "  codex_resume  ")
        self.assertIsInstance(launcher, CodexReadonlyLauncher)

    def test_newlines_replaced(self):
        launcher = sandbox_launcher_for("codex_live\n_session", "codex_resume")
        # newline breaks the match -> advisory
        self.assertIsInstance(launcher, NoSandboxLauncher)


class TestCodexReadonlyLauncherCommand(unittest.TestCase):
    def test_command_shape(self):
        launcher = CodexReadonlyLauncher()
        result = launcher.command(["codex"])
        self.assertEqual(result, ["codex", "exec", "--sandbox", "read-only", "--ignore-user-config", "--ignore-rules"])

    def test_command_preserves_base(self):
        launcher = CodexReadonlyLauncher()
        result = launcher.command(["custom-codex", "--flag"])
        self.assertEqual(result[:2], ["custom-codex", "--flag"])
        self.assertIn("exec", result)
        self.assertIn("--sandbox", result)
        self.assertIn("read-only", result)
        self.assertIn("--ignore-user-config", result)
        self.assertIn("--ignore-rules", result)


class TestNoSandboxLauncherCommand(unittest.TestCase):
    def test_passthrough(self):
        launcher = NoSandboxLauncher()
        self.assertEqual(launcher.command(["echo", "hi"]), ["echo", "hi"])

    def test_tuple_input_returns_list(self):
        launcher = NoSandboxLauncher()
        result = launcher.command(("a", "b"))
        self.assertIsInstance(result, list)
        self.assertEqual(result, ["a", "b"])


class TestSafeSandboxEnforcement(unittest.TestCase):
    def test_valid_levels_pass(self):
        for level in SANDBOX_ENFORCEMENT_LEVELS:
            self.assertEqual(safe_sandbox_enforcement(level), level)

    def test_junk_returns_empty(self):
        self.assertEqual(safe_sandbox_enforcement("bogus"), "")

    def test_none_returns_empty(self):
        self.assertEqual(safe_sandbox_enforcement(None), "")

    def test_numeric_returns_empty(self):
        self.assertEqual(safe_sandbox_enforcement(42), "")

    def test_whitespace_stripped(self):
        self.assertEqual(safe_sandbox_enforcement("  advisory  "), "advisory")

    def test_casing_not_normalized(self):
        # clean_lobby_text does not lowercase, so uppercase fails
        self.assertEqual(safe_sandbox_enforcement("ADVISORY"), "")


class TestConstants(unittest.TestCase):
    def test_enforcement_levels_is_set(self):
        self.assertIsInstance(SANDBOX_ENFORCEMENT_LEVELS, set)
        self.assertIn("advisory", SANDBOX_ENFORCEMENT_LEVELS)
        self.assertIn("codex_readonly", SANDBOX_ENFORCEMENT_LEVELS)
        self.assertIn("os_sandboxed", SANDBOX_ENFORCEMENT_LEVELS)

    def test_codex_exec_safety_flags_tuple(self):
        self.assertIsInstance(CODEX_EXEC_SAFETY_FLAGS, tuple)
        self.assertEqual(CODEX_EXEC_SAFETY_FLAGS, ("--sandbox", "read-only", "--ignore-user-config", "--ignore-rules"))


if __name__ == "__main__":
    unittest.main()
