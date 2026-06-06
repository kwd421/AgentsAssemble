import subprocess
import unittest

from agentsassemble.codex_resident import codex_auth_check


class CodexResidentAuthTests(unittest.TestCase):
    def test_auth_check_reports_login_required_and_accepts_login_status(self):
        def authenticated(command, **kwargs):
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="Logged in using ChatGPT")

        self.assertEqual(codex_auth_check(["codex"], command_runner=authenticated)["status"], "ok")

        def unauthenticated(command, **kwargs):
            return subprocess.CompletedProcess(command, 1, stdout="", stderr="not authenticated")

        check = codex_auth_check(["codex"], command_runner=unauthenticated)

        self.assertEqual(check["id"], "codex_auth")
        self.assertEqual(check["status"], "failed")
        self.assertIn("Codex 로그인이 필요합니다", check["message"])


if __name__ == "__main__":
    unittest.main()
