import base64
import subprocess
import tempfile
import unittest
from pathlib import Path

from agentsassemble.providers.antigravity_resident import (
    ANTIGRAVITY_BACKEND_ERROR,
    ANTIGRAVITY_EMPTY_REPLY,
    ANTIGRAVITY_MISSING_CONVERSATION_ID,
    ANTIGRAVITY_SUBPROCESS_NONZERO,
    AntigravityResidentCommandRunner,
    antigravity_auth_check,
    antigravity_command_check,
    antigravity_error_category,
    antigravity_provider_connection_check,
    clean_antigravity_conversation_id,
    default_antigravity_resident_command,
)
from agentsassemble.live_agent_runner import ResidentAgentConfig


REAL_AGY_PRINT_CAPTURE_B64 = (
    "7KeI66y47ZWY7IugICoqIuybkO2UvOyKpCDstZzqsJU/Iioq7J2AIOuLpOydjCDrkZAg6rCA7KeAIOunpeudveycvOuhnCDri7Xr"
    "s4Drk5zrprQg7IiYIOyeiOyKteuLiOuLpC4KCi0tLQoKIyMjIDEuIOybkO2UvOyKpCDshLjqs4TqtIAg64K0IOy1nOqwleyekCAo"
    "7JuQ7J6RIOyEpOyglSkK7ZiE7J6sIOybkO2UvOyKpCDshLjqs4TqtIDsl5DshJwg7LWc6rCV7Jy866GcIOq8ve2eiOuKlCDsnbjr"
    "rLzrk6TsnYAg64uk7J2M6rO8IOqwmeyKteuLiOuLpDoKKiAqKuyghOyEpOyggeyduCDsoJXsoJAqKjog6rOoIEQuIOuhnOyggCwg"
    "7JeQ65Oc7JuM65OcIOuJtOqyjOydtO2KuCAo7Z2w7IiY7Je8KSwg66Gd7IqkIEQuIOyngOuyoQoqICoq7ZiE7IS464yAIOy1nOqw"
    "leq4iSAo7IKs7ZmpKSoqOiDsg7ntgazsiqQsIOy5tOydtOuPhCjsg53sobQg7IucKSwg66eI7IOsIEQuIO2LsOy5mCAo6rKA7J2A"
    "IOyImOyXvCkKKiAqKuyjvOyduOqztSoqOiDrqr3tgqQgRC4g66Oo7ZS8ICjquLDslrQgNSAn7YOc7JaR7IugIOuLiOy5tCcg6rCB"
    "7ISxKQoqICoq66eJ7ZuE7J2YIOygiOuMgOyekCoqOiDsnoQgKEltdSksIOyhsOydtOuztOydtCAo7Jet7IKs7KCBIOyduOusvCkK"
    "Ci0tLQoKIyMjIDIuIGBBZ2VudHNBc3NlbWJsZWAg7ZSE66Gc7KCd7Yq4IOuCtCDrjbDrqqgg7Yag66GgCuuzuCDroIjtj6zsp4Dt"
    "hqDrpqwoYEFnZW50c0Fzc2VtYmxlYCnsl5DripQg7JuQ7ZS87IqkIO2GoOuhoOydhCDthYzrp4jroZwg7ZWcIOupgO2LsCDsl5Ds"
    "nbTsoITtirgg7ZqM7J2YIOuNsOuqqOqwgCDtj6ztlajrkJjslrQg7J6I7Iq164uI64ukLgoqICoq7KO87KCcKio6ICLsm5DtlLzs"
    "iqQgM+uMgOyepSDspJEg64iE6rCAIOygnOydvCDshLzqsIA/IiAoW2RlbW8tY291bmNpbC5qc29uXShmaWxlOi8vL1VzZXJzL3Nl"
    "aW5lbC9Qcm9qZWN0cy9BZ2VudHNBc3NlbWJsZS9jb25maWdzL2RlbW8tY291bmNpbC5qc29uKSDshKTsoJUg7LC46rOgKQoqICoq"
    "7JeQ7J207KCE7Yq4IOudvOyduOyXhSoqOgogICogKirshKTsoJXstqkqKiAoYGxvcmVfbGF3eWVyYCk6IOqzteyLnSDshKTsoJXq"
    "s7wg7JuQ7J6R7J2YIOydvOq0gOyEseydhCDsmrDshKDsi5ztlaguCiAgKiAqKuqzteyLneydtOutmOyVjOyVhCoqIChgc2hvd19t"
    "ZV90aGVfZmVhdHNgKTog7Iuk7KCcIOyekeykkeyXkOyEnCDrs7Tsl6zspIAg7KCE7YisIOusmOyCrOyZgCDsoITsoIHsnYQg7KSR"
    "7Iuc7ZWoLgogICogKirrp4zqsKTrn6wqKiAoYGZhbmJvYXJkX3NrZXB0aWNgKTog7Luk666k64uI7YuwKOuUlOyLnOyduOyCrOyd"
    "tOuTnCDrp4ztmZQg6rCk65+s66asIOuTsSkg67CI6rO8IOu5hO2MkOyggSDqtIDsoJDsnYQg7KCB7Jqp7ZWoLgoKIyMjIyDrjbDr"
    "qqgg7Iuk7ZaJIOuwqeuylQrroZzsu6wg7ZmY6rK97JeQ7IScIOyVhOuemCDrqoXroLnslrTrpbwg7Iuk7ZaJ7ZWY7JesIOydtOuT"
    "pOydtCDthqDroaDtlZjripQg642w66qo66W8IO2ZleyduO2VoCDsiJgg7J6I7Iq164uI64ukOgpgYGBiYXNoCnB5dGhvbjMgLW0g"
    "YWdlbnRzYXNzZW1ibGUuY2xpIGRlbW8gLS1hZGFwdGVyIG1vY2sKYGBgCgotLS0KCu2YueyLnCDsm5DtlLzsiqQg7IS46rOE6rSA"
    "7J2YIO2MjOybjCDrsLjrn7DsiqTsl5Ag64yA7ZW0IOuNlCDqtoHquIjtlZwg7KCQ7J20IOyeiOycvOyLnOqxsOuCmCwg67O4IO2U"
    "hOuhnOygne2KuOydmCDrjbDrqqgg7ISk7KCV7J2EIOyImOyglSDrmJDripQg7Iuk7ZaJ7ZW067O06rOgIOyLtuycvOyLoOqwgOya"
    "lD8g7Y647ZWY6rKMIOyVjOugpOyjvOyLnOuptCDrj4TsmYDrk5zrpqzqsqDsirXri4jri6QhCgojIyMg7J6R7JeFIOyalOyVvQoq"
    "IOyCrOyaqeyekCDsp4jrrLgg67aE7ISd7J2EIOychO2VtCDsoITssrQg7ZSE66Gc7KCd7Yq4IOuUlOugie2GoOumrOulvCDrpqzs"
    "iqTtjIXtlZjqs6AsIGDsm5DtlLzsiqRgIO2CpOybjOuTnOuhnCDshozsiqTsvZTrk5zrpbwg6rKA7IOJ7ZaI7Iq164uI64ukLgoq"
    "IOyEpOyglSDtjIzsnbwoW2RlbW8tY291bmNpbC5qc29uXShmaWxlOi8vL1VzZXJzL3NlaW5lbC9Qcm9qZWN0cy9BZ2VudHNBc3Nl"
    "bWJsZS9jb25maWdzL2RlbW8tY291bmNpbC5qc29uKSkg67CPIO2FnO2UjOumvyhbdGVtcGxhdGVzLnB5XShmaWxlOi8vL1VzZXJz"
    "L3NlaW5lbC9Qcm9qZWN0cy9BZ2VudHNBc3NlbWJsZS9hZ2VudHNhc3NlbWJsZS90ZW1wbGF0ZXMucHkpKSwg7YWM7Iqk7Yq4IOy9"
    "lOuTnChbdGVzdF9jbGF1ZGVfcmVzaWRlbnQucHldKGZpbGU6Ly8vVXNlcnMvc2VpbmVsL1Byb2plY3RzL0FnZW50c0Fzc2VtYmxl"
    "L3Rlc3RzL3Rlc3RfY2xhdWRlX3Jlc2lkZW50LnB5KSkg67aE7ISd7J2EIO2Gte2VtCDtlITroZzsoJ3tirgg64K0IOuNsOuqqCDq"
    "tazshLHsnYQg7YyM7JWF7ZWY6rOgIOydtOulvCDsm5DsnpEg7ISk7KCV6rO8IOunpO2Vke2VmOyXrCDslYjrgrTrk5zroLjsirXr"
    "i4jri6QuCg=="
)


def real_agy_print_capture() -> str:
    return base64.b64decode(REAL_AGY_PRINT_CAPTURE_B64).decode("utf-8")


def config(**overrides):
    values = {
        "server": "http://room.local",
        "agent_id": "antigravity-a",
        "display_name": "Antigravity A",
        "provider_kind": "antigravity_live_session",
        "connection_kind": "live_session",
        "session_id": "",
        "endpoint": "",
        "auth_ref": "",
        "meeting_id": "",
        "engagement_mode": "always",
        "command": ["agy"],
        "timeout_seconds": 60,
        "poll_interval": 1.0,
        "heartbeat_interval": 10.0,
        "cooldown": 0.0,
        "max_chain_depth": 1,
        "max_ticks": 1,
    }
    values.update(overrides)
    return ResidentAgentConfig(**values)


class AntigravityResidentTests(unittest.TestCase):
    def test_runner_captures_created_conversation_then_resumes_it(self):
        calls = []
        conversation_id = "a" * 36

        def command_runner(command, **kwargs):
            calls.append({"command": command, "kwargs": kwargs})
            if "--log-file" in command:
                log_path = Path(command[command.index("--log-file") + 1])
                log_path.write_text(f"Created conversation {conversation_id}\n", encoding="utf-8")
            if "--conversation" in command:
                self.assertIn(conversation_id, command)
                self.assertNotIn("SECRET-CODE", " ".join(command))
                return subprocess.CompletedProcess(command, 0, stdout="The suffix is C123.", stderr="SECRET-CODE")
            self.assertIn("SECRET-CODE", " ".join(command))
            return subprocess.CompletedProcess(command, 0, stdout="READY\n", stderr="SECRET-CODE")

        with tempfile.TemporaryDirectory() as temp_dir:
            runner = AntigravityResidentCommandRunner(config(), command_runner=command_runner, cwd=Path(temp_dir))
            try:
                first = runner([], "store SECRET-CODE", timeout_seconds=45)
                second = runner([], "suffix only", timeout_seconds=45)
            finally:
                runner.close()

        self.assertEqual(first, "READY")
        self.assertEqual(second, "The suffix is C123.")
        self.assertEqual(runner.session_id, conversation_id)
        self.assertEqual(calls[0]["command"][0], "agy")
        self.assertIn("--print", calls[0]["command"])
        self.assertIn("--conversation", calls[1]["command"])
        self.assertEqual(calls[0]["kwargs"]["cwd"], str(Path(temp_dir)))

    def test_runner_reports_safe_failures(self):
        # A missing conversation id is no longer fatal: Antigravity answers even
        # when not fully logged in (no id in the log), so a valid reply still
        # reaches the room — just statelessly (no --conversation resume).
        def no_conversation(command, **kwargs):
            return subprocess.CompletedProcess(command, 0, stdout="READY", stderr="")

        runner = AntigravityResidentCommandRunner(config(), command_runner=no_conversation, cwd=Path.cwd())
        try:
            self.assertEqual(runner([], "prompt", timeout_seconds=45), "READY")
            self.assertEqual(runner.session_id, "")  # stayed stateless, did not raise
        finally:
            runner.close()

        def nonzero(command, **kwargs):
            return subprocess.CompletedProcess(command, 7, stdout="", stderr="SECRET")

        failed = AntigravityResidentCommandRunner(
            config(session_id="a" * 36),
            command_runner=nonzero,
            cwd=Path.cwd(),
        )
        try:
            with self.assertRaisesRegex(RuntimeError, "return code 7") as caught:
                failed([], "prompt", timeout_seconds=45)
        finally:
            failed.close()
        self.assertEqual(antigravity_error_category(caught.exception), ANTIGRAVITY_SUBPROCESS_NONZERO)

        def empty(command, **kwargs):
            return subprocess.CompletedProcess(command, 0, stdout="   ", stderr="")

        empty_runner = AntigravityResidentCommandRunner(
            config(session_id="a" * 36),
            command_runner=empty,
            cwd=Path.cwd(),
        )
        try:
            with self.assertRaisesRegex(ValueError, "empty reply") as empty_error:
                empty_runner([], "prompt", timeout_seconds=45)
        finally:
            empty_runner.close()
        self.assertEqual(antigravity_error_category(empty_error.exception), ANTIGRAVITY_EMPTY_REPLY)

    def test_runner_rejects_stale_stdout_when_backend_reports_quota_error(self):
        conversation_id = "a" * 36

        def quota_error(command, **kwargs):
            log_path = Path(command[command.index("--log-file") + 1])
            log_path.write_text(
                "Created conversation aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\n"
                "agent executor error: RESOURCE_EXHAUSTED (code 429): quota reached\n",
                encoding="utf-8",
            )
            return subprocess.CompletedProcess(command, 0, stdout="previous successful reply", stderr="")

        runner = AntigravityResidentCommandRunner(
            config(session_id=conversation_id),
            command_runner=quota_error,
            cwd=Path.cwd(),
        )
        try:
            with self.assertRaisesRegex(RuntimeError, "backend reported") as caught:
                runner([], "prompt", timeout_seconds=45)
        finally:
            runner.close()
        self.assertEqual(antigravity_error_category(caught.exception), ANTIGRAVITY_BACKEND_ERROR)

    def test_runner_keeps_latest_json_object_from_conversation_replay(self):
        conversation_id = "a" * 36

        def replayed_json(command, **kwargs):
            return subprocess.CompletedProcess(
                command,
                0,
                stdout=(
                    '{"action":"speak","message":"old reply","reason":"old","target_agent_id":""}\n'
                    '{"action":"speak","message":"latest reply","reason":"new","target_agent_id":""}\n'
                ),
                stderr="",
            )

        runner = AntigravityResidentCommandRunner(
            config(session_id=conversation_id),
            command_runner=replayed_json,
            cwd=Path.cwd(),
        )
        try:
            reply = runner([], "prompt", timeout_seconds=45)
        finally:
            runner.close()

        self.assertEqual(reply, '{"action":"speak","message":"latest reply","reason":"new","target_agent_id":""}')

    def test_runner_rejects_ready_banner_with_unterminated_action_fragment(self):
        conversation_id = "a" * 36

        def bad_fragment(command, **kwargs):
            return subprocess.CompletedProcess(
                command,
                0,
                stdout=(
                    "Antigravity (antigravity-live) is present and ready for AgentsAssemble. "
                    'Connection active at cursor abc123. {"action":"speak","message":"half'
                ),
                stderr="",
            )

        runner = AntigravityResidentCommandRunner(
            config(session_id=conversation_id),
            command_runner=bad_fragment,
            cwd=Path.cwd(),
        )
        try:
            with self.assertRaisesRegex(ValueError, "empty reply") as empty:
                runner([], "prompt", timeout_seconds=45)
        finally:
            runner.close()

        self.assertEqual(antigravity_error_category(empty.exception), ANTIGRAVITY_EMPTY_REPLY)

    def test_runner_rejects_status_only_stdout(self):
        conversation_id = "a" * 36

        def status_only(command, **kwargs):
            return subprocess.CompletedProcess(
                command,
                0,
                stdout="Antigravity (antigravity-live) is present and ready for AgentsAssemble. Connection active at cursor abc123.",
                stderr="",
            )

        runner = AntigravityResidentCommandRunner(
            config(session_id=conversation_id),
            command_runner=status_only,
            cwd=Path.cwd(),
        )
        try:
            with self.assertRaisesRegex(ValueError, "empty reply") as empty:
                runner([], "prompt", timeout_seconds=45)
        finally:
            runner.close()

        self.assertEqual(antigravity_error_category(empty.exception), ANTIGRAVITY_EMPTY_REPLY)

    def test_runner_rejects_old_valid_json_when_latest_action_is_fragmented(self):
        conversation_id = "a" * 36

        def old_valid_then_fragment(command, **kwargs):
            return subprocess.CompletedProcess(
                command,
                0,
                stdout=(
                    '{"action":"speak","message":"old reply","reason":"old","target_agent_id":""}\n'
                    '{"action":"speak","message":"half'
                ),
                stderr="",
            )

        runner = AntigravityResidentCommandRunner(
            config(session_id=conversation_id),
            command_runner=old_valid_then_fragment,
            cwd=Path.cwd(),
        )
        try:
            with self.assertRaisesRegex(ValueError, "empty reply") as empty:
                runner([], "prompt", timeout_seconds=45)
        finally:
            runner.close()

        self.assertEqual(antigravity_error_category(empty.exception), ANTIGRAVITY_EMPTY_REPLY)

    def test_runner_keeps_latest_pretty_json_object_from_conversation_replay(self):
        conversation_id = "a" * 36

        def replayed_pretty_json(command, **kwargs):
            return subprocess.CompletedProcess(
                command,
                0,
                stdout=(
                    '{"action":"speak","message":"old reply","reason":"old","target_agent_id":""}\n'
                    "{\n"
                    '  "action": "speak",\n'
                    '  "message": "pretty latest",\n'
                    '  "reason": "new",\n'
                    '  "target_agent_id": ""\n'
                    "}\n"
                ),
                stderr="",
            )

        runner = AntigravityResidentCommandRunner(
            config(session_id=conversation_id),
            command_runner=replayed_pretty_json,
            cwd=Path.cwd(),
        )
        try:
            reply = runner([], "prompt", timeout_seconds=45)
        finally:
            runner.close()

        self.assertEqual(reply, '{"action":"speak","message":"pretty latest","reason":"new","target_agent_id":""}')

    def test_runner_rejects_spaced_action_fragment(self):
        conversation_id = "a" * 36

        def spaced_fragment(command, **kwargs):
            return subprocess.CompletedProcess(
                command,
                0,
                stdout='{ "action": "speak", "message": "half',
                stderr="",
            )

        runner = AntigravityResidentCommandRunner(
            config(session_id=conversation_id),
            command_runner=spaced_fragment,
            cwd=Path.cwd(),
        )
        try:
            with self.assertRaisesRegex(ValueError, "empty reply") as empty:
                runner([], "prompt", timeout_seconds=45)
        finally:
            runner.close()

        self.assertEqual(antigravity_error_category(empty.exception), ANTIGRAVITY_EMPTY_REPLY)

    def test_runner_rejects_old_valid_json_when_latest_output_is_status(self):
        conversation_id = "a" * 36

        def old_valid_then_status(command, **kwargs):
            return subprocess.CompletedProcess(
                command,
                0,
                stdout=(
                    '{"action":"speak","message":"old reply","reason":"old","target_agent_id":""}\n'
                    "Connection active at cursor abc123."
                ),
                stderr="",
            )

        runner = AntigravityResidentCommandRunner(
            config(session_id=conversation_id),
            command_runner=old_valid_then_status,
            cwd=Path.cwd(),
        )
        try:
            with self.assertRaisesRegex(ValueError, "empty reply") as empty:
                runner([], "prompt", timeout_seconds=45)
        finally:
            runner.close()

        self.assertEqual(antigravity_error_category(empty.exception), ANTIGRAVITY_EMPTY_REPLY)

    def test_runner_extracts_leading_answer_from_real_agy_print_capture(self):
        conversation_id = "a" * 36

        def real_capture(command, **kwargs):
            return subprocess.CompletedProcess(command, 0, stdout=real_agy_print_capture(), stderr="")

        runner = AntigravityResidentCommandRunner(
            config(session_id=conversation_id),
            command_runner=real_capture,
            cwd=Path.cwd(),
        )
        try:
            reply = runner([], "prompt", timeout_seconds=45)
        finally:
            runner.close()

        self.assertTrue(reply.startswith('질문하신 **"원피스 최강?"**은'))
        self.assertIn("원피스 세계관 내 최강자", reply)
        self.assertNotIn("### 작업 요약", reply)
        self.assertNotIn("전체 프로젝트 디렉토리를 리스팅", reply)

    def test_runner_strips_trailing_antigravity_meta_summary_line(self):
        conversation_id = "a" * 36
        stdout = (
            "루피가 현재 주인공 보정과 기어 5 각성까지 포함하면 최강 후보입니다.\n\n"
            "데모 데이터 상의 최강 대장 결론 정리 및 원작의 대표적인 명대사 명단을 정리하여 답변 제공."
        )

        def reported_meta_tail(command, **kwargs):
            return subprocess.CompletedProcess(command, 0, stdout=stdout, stderr="")

        runner = AntigravityResidentCommandRunner(
            config(session_id=conversation_id),
            command_runner=reported_meta_tail,
            cwd=Path.cwd(),
        )
        try:
            reply = runner([], "prompt", timeout_seconds=45)
        finally:
            runner.close()

        self.assertEqual(reply, "루피가 현재 주인공 보정과 기어 5 각성까지 포함하면 최강 후보입니다.")

    def test_provider_checks_and_defaults_are_narrow(self):
        self.assertEqual(default_antigravity_resident_command("antigravity_live_session", "live_session", []), ["agy"])
        self.assertEqual(default_antigravity_resident_command("antigravity_cli", "live_session", []), [])
        self.assertEqual(
            antigravity_provider_connection_check("antigravity_live_session", "live_session"),
            {
                "id": "provider_connection_kind",
                "status": "ok",
                "message": "antigravity_live_session uses live_session.",
            },
        )
        self.assertEqual(antigravity_provider_connection_check("antigravity_cli", "self_service"), None)
        self.assertEqual(antigravity_command_check(["agy"])["status"], "ok")
        self.assertEqual(antigravity_command_check(["antigravity"])["status"], "ok")
        self.assertEqual(antigravity_command_check(["agy", "--continue"])["status"], "failed")
        self.assertEqual(antigravity_command_check(["hermes"])["status"], "failed")
        self.assertEqual(clean_antigravity_conversation_id("unsafe id"), "")

    def test_auth_check_reports_login_required_instead_of_generic_failure(self):
        calls = []

        def login_required(command, **kwargs):
            calls.append({"command": command, "kwargs": kwargs})
            log_path = Path(command[command.index("--log-file") + 1])
            log_path.write_text("UNAUTHENTICATED: please sign in before using Antigravity.\n", encoding="utf-8")
            return subprocess.CompletedProcess(command, 1, stdout="", stderr="not authenticated")

        check = antigravity_auth_check(["agy"], command_runner=login_required, timeout_seconds=3)

        self.assertEqual(check["id"], "antigravity_auth")
        self.assertEqual(check["status"], "failed")
        self.assertIn("Antigravity 로그인이 필요합니다", check["message"])
        self.assertEqual(calls[0]["command"][0], "agy")
        self.assertIn("--print", calls[0]["command"])

    def test_auth_check_accepts_silent_auth_success_after_initial_not_logged_in_log(self):
        def silent_auth_success(command, **kwargs):
            log_path = Path(command[command.index("--log-file") + 1])
            log_path.write_text(
                "Print mode: not authenticated, trying silent auth\n"
                "OAuth: authenticated successfully as user@example.test\n"
                "Print mode: silent auth succeeded\n",
                encoding="utf-8",
            )
            return subprocess.CompletedProcess(command, 0, stdout="READY\n", stderr="")

        check = antigravity_auth_check(["agy"], command_runner=silent_auth_success, timeout_seconds=3)

        self.assertEqual(check["id"], "antigravity_auth")
        self.assertEqual(check["status"], "ok")


if __name__ == "__main__":
    unittest.main()
