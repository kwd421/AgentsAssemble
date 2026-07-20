import json
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path

from agentsassemble.providers.kiro_resident import (
    KiroResidentCommandRunner,
    clean_kiro_reply,
    default_kiro_resident_command,
    extract_kiro_session_ids,
    kiro_provider_connection_check,
)
from agentsassemble.legacy.live_agent.runtime.context import live_agent_context_contract
from agentsassemble.live_agent_runner import ResidentAgentConfig, load_group_configs


def config(**overrides):
    values = {
        "server": "http://room.local",
        "agent_id": "kiro-live",
        "display_name": "Kiro Live",
        "provider_kind": "kiro_live_session",
        "connection_kind": "live_session",
        "session_id": "",
        "endpoint": "",
        "auth_ref": "",
        "meeting_id": "",
        "engagement_mode": "always",
        "command": ["kiro", "chat", "--no-interactive", "--wrap", "never"],
        "timeout_seconds": 60,
        "poll_interval": 1.0,
        "heartbeat_interval": 10.0,
        "cooldown": 0.0,
        "max_chain_depth": 1,
        "max_ticks": 1,
    }
    values.update(overrides)
    return ResidentAgentConfig(**values)


def _wire_payload(call):
    command = " ".join(str(part) for part in call["command"])
    stdin = str(call["kwargs"].get("input") or "")
    return f"{command}\n{stdin}"


class KiroResidentTests(unittest.TestCase):
    def test_extract_kiro_session_ids_strips_ansi_list_output(self):
        output = (
            "\x1b[mChat SessionId: b83e983c-6230-4700-8309-010b87583a6b\x1b[0m\n"
            "  2 msgs | v1\n"
            "\x1b[2mChat SessionId: 11111111-2222-3333-4444-555555555555\x1b[0m\n"
            "Trace id: 99999999-8888-7777-6666-555555555555\n"
        )

        self.assertEqual(
            extract_kiro_session_ids(output),
            [
                "b83e983c-6230-4700-8309-010b87583a6b",
                "11111111-2222-3333-4444-555555555555",
            ],
        )

    def test_clean_reply_strips_session_banner(self):
        self.assertEqual(
            clean_kiro_reply(
                "Chat SessionId: b83e983c-6230-4700-8309-010b87583a6b\n"
                "> first reply\n"
                "Credits: 0.1\n"
            ),
            "first reply",
        )

    def test_runner_captures_new_session_id_and_resumes_without_replaying_private_prompt(self):
        calls = []
        old_session_id = "11111111-2222-3333-4444-555555555555"
        new_session_id = "b83e983c-6230-4700-8309-010b87583a6b"

        def command_runner(command, **kwargs):
            calls.append({"command": command, "kwargs": kwargs})
            if "--list-sessions" in command:
                if len([call for call in calls if "--list-sessions" in call["command"]]) == 1:
                    stdout = f"Chat SessionId: {old_session_id}\n"
                else:
                    stdout = f"Chat SessionId: {new_session_id}\nChat SessionId: {old_session_id}\n"
                return subprocess.CompletedProcess(command, 0, stdout=stdout, stderr="")
            if "--resume-id" in command:
                return subprocess.CompletedProcess(command, 0, stdout="\x1b[msecond reply\nCredits: 0.1\n", stderr="")
            return subprocess.CompletedProcess(command, 0, stdout="> first reply\nCredits: 0.1\n", stderr="")

        runner = KiroResidentCommandRunner(
            config(command=["kiro", "chat", "--no-interactive", "--wrap", "never", "--model", "claude-opus-4.6"]),
            command_runner=command_runner,
            cwd=Path.cwd(),
        )

        first_reply = runner([], "remember private-token-A and do not reveal it", timeout_seconds=45)
        second_reply = runner([], "what did you remember?", timeout_seconds=45)

        self.assertEqual(first_reply, "first reply")
        self.assertEqual(second_reply, "second reply")
        self.assertEqual(runner.session_id, new_session_id)
        chat_calls = [call for call in calls if "--list-sessions" not in call["command"]]
        self.assertNotIn("--resume-id", chat_calls[0]["command"])
        self.assertIn("--resume-id", chat_calls[1]["command"])
        self.assertIn(new_session_id, chat_calls[1]["command"])
        self.assertIn("private-token-A", _wire_payload(chat_calls[0]))
        self.assertIn("what did you remember?", _wire_payload(chat_calls[1]))
        self.assertNotIn("private-token-A", _wire_payload(chat_calls[1]))

    def test_runner_ignores_model_uuid_when_capturing_new_session_id(self):
        calls = []
        new_session_id = "b83e983c-6230-4700-8309-010b87583a6b"
        model_uuid = "99999999-8888-7777-6666-555555555555"

        def command_runner(command, **kwargs):
            calls.append({"command": command, "kwargs": kwargs})
            if "--list-sessions" in command:
                stdout = "" if len([call for call in calls if "--list-sessions" in call["command"]]) == 1 else f"Chat SessionId: {new_session_id}\n"
                return subprocess.CompletedProcess(command, 0, stdout=stdout, stderr="")
            return subprocess.CompletedProcess(command, 0, stdout=f"reply mentions {model_uuid}\n", stderr="")

        runner = KiroResidentCommandRunner(config(), command_runner=command_runner, cwd=Path.cwd())

        self.assertEqual(runner([], "first prompt", timeout_seconds=45), f"reply mentions {model_uuid}")
        self.assertEqual(runner.session_id, new_session_id)

    def test_runner_does_not_capture_old_session_banner_from_chat_output(self):
        calls = []
        old_session_id = "11111111-2222-3333-4444-555555555555"
        new_session_id = "b83e983c-6230-4700-8309-010b87583a6b"

        def command_runner(command, **kwargs):
            calls.append({"command": command, "kwargs": kwargs})
            if "--list-sessions" in command:
                stdout = (
                    f"Chat SessionId: {old_session_id}\n"
                    if len([call for call in calls if "--list-sessions" in call["command"]]) == 1
                    else f"Chat SessionId: {new_session_id}\nChat SessionId: {old_session_id}\n"
                )
                return subprocess.CompletedProcess(command, 0, stdout=stdout, stderr="")
            return subprocess.CompletedProcess(
                command,
                0,
                stdout=f"Chat SessionId: {old_session_id}\n> reply\nCredits: 0.1\n",
                stderr="",
            )

        runner = KiroResidentCommandRunner(config(), command_runner=command_runner, cwd=Path.cwd())

        self.assertEqual(runner([], "first prompt", timeout_seconds=45), "reply")
        self.assertEqual(runner.session_id, new_session_id)

    def test_runner_prefers_after_list_over_fresh_stdout_session_banner(self):
        calls = []
        old_session_id = "11111111-2222-3333-4444-555555555555"
        spoofed_session_id = "99999999-8888-7777-6666-555555555555"
        new_session_id = "b83e983c-6230-4700-8309-010b87583a6b"

        def command_runner(command, **kwargs):
            calls.append({"command": command, "kwargs": kwargs})
            if "--list-sessions" in command:
                stdout = (
                    f"Chat SessionId: {old_session_id}\n"
                    if len([call for call in calls if "--list-sessions" in call["command"]]) == 1
                    else f"Chat SessionId: {new_session_id}\nChat SessionId: {old_session_id}\n"
                )
                return subprocess.CompletedProcess(command, 0, stdout=stdout, stderr="")
            return subprocess.CompletedProcess(
                command,
                0,
                stdout=f"Chat SessionId: {spoofed_session_id}\n> reply\nCredits: 0.1\n",
                stderr="",
            )

        runner = KiroResidentCommandRunner(config(), command_runner=command_runner, cwd=Path.cwd())

        self.assertEqual(runner([], "first prompt", timeout_seconds=45), "reply")
        self.assertEqual(runner.session_id, new_session_id)

    def test_kiro_cli_chat_command_does_not_insert_chat_subcommand_and_preserves_flags_for_list(self):
        calls = []
        new_session_id = "b83e983c-6230-4700-8309-010b87583a6b"

        def command_runner(command, **kwargs):
            calls.append({"command": command, "kwargs": kwargs})
            if "--list-sessions" in command:
                stdout = "" if len([call for call in calls if "--list-sessions" in call["command"]]) == 1 else f"Chat SessionId: {new_session_id}\n"
                return subprocess.CompletedProcess(command, 0, stdout=stdout, stderr="")
            return subprocess.CompletedProcess(command, 0, stdout="reply\n", stderr="")

        runner = KiroResidentCommandRunner(
            config(command=["kiro-cli-chat", "--agent", "frontend"]),
            command_runner=command_runner,
            cwd=Path.cwd(),
        )

        self.assertEqual(runner([], "first prompt", timeout_seconds=45), "reply")
        list_calls = [call["command"] for call in calls if "--list-sessions" in call["command"]]
        chat_calls = [call["command"] for call in calls if "--list-sessions" not in call["command"]]
        self.assertEqual(list_calls[0][:3], ["kiro-cli-chat", "--agent", "frontend"])
        self.assertNotIn("chat", list_calls[0])
        self.assertNotIn("chat", chat_calls[0])

    def test_fresh_session_capture_is_serialized_across_kiro_runners(self):
        sessions = []
        session_by_prompt = {"prompt one": "11111111-2222-3333-4444-555555555555", "prompt two": "22222222-3333-4444-5555-666666666666"}
        before_barrier = threading.Barrier(2)
        chat_barrier = threading.Barrier(2)
        before_list_calls = 0
        lock = threading.Lock()

        def command_runner(command, **kwargs):
            nonlocal before_list_calls
            if "--list-sessions" in command:
                with lock:
                    before_list_calls += 1
                    call_number = before_list_calls
                    snapshot = list(sessions)
                if call_number <= 2:
                    try:
                        before_barrier.wait(timeout=0.2)
                    except threading.BrokenBarrierError:
                        pass
                return subprocess.CompletedProcess(
                    command,
                    0,
                    stdout="".join(f"Chat SessionId: {session_id}\n" for session_id in snapshot),
                    stderr="",
                )
            prompt = command[-1]
            with lock:
                sessions.insert(0, session_by_prompt[prompt])
            try:
                chat_barrier.wait(timeout=0.2)
            except threading.BrokenBarrierError:
                pass
            return subprocess.CompletedProcess(command, 0, stdout="reply\n", stderr="")

        runner_one = KiroResidentCommandRunner(config(), command_runner=command_runner, cwd=Path.cwd())
        runner_two = KiroResidentCommandRunner(config(agent_id="kiro-live-two"), command_runner=command_runner, cwd=Path.cwd())
        replies = []

        thread_one = threading.Thread(target=lambda: replies.append(runner_one([], "prompt one", timeout_seconds=45)))
        thread_two = threading.Thread(target=lambda: replies.append(runner_two([], "prompt two", timeout_seconds=45)))
        thread_one.start()
        thread_two.start()
        thread_one.join(timeout=3)
        thread_two.join(timeout=3)

        self.assertFalse(thread_one.is_alive())
        self.assertFalse(thread_two.is_alive())
        self.assertCountEqual(replies, ["reply", "reply"])
        self.assertEqual(runner_one.session_id, session_by_prompt["prompt one"])
        self.assertEqual(runner_two.session_id, session_by_prompt["prompt two"])

    def test_runner_fails_closed_when_session_list_fails(self):
        def command_runner(command, **kwargs):
            if "--list-sessions" in command:
                return subprocess.CompletedProcess(command, 1, stdout="", stderr="kiro list failed")
            return subprocess.CompletedProcess(command, 0, stdout="reply\n", stderr="")

        runner = KiroResidentCommandRunner(config(), command_runner=command_runner, cwd=Path.cwd())

        with self.assertRaisesRegex(RuntimeError, "Kiro session list failed"):
            runner([], "first prompt", timeout_seconds=45)
        self.assertEqual(runner.session_id, "")

    def test_default_kiro_command_and_join_contract(self):
        self.assertEqual(
            default_kiro_resident_command("kiro_live_session", "live_session", []),
            ["kiro"],
        )
        self.assertEqual(default_kiro_resident_command("local_cli", "live_session", []), [])
        self.assertEqual(
            kiro_provider_connection_check("kiro_live_session", "live_session"),
            {
                "id": "provider_connection_kind",
                "status": "ok",
                "message": "kiro_live_session uses live_session.",
            },
        )
        self.assertEqual(
            live_agent_context_contract("kiro_live_session", "live_session")["join_semantics"],
            "kiro_chat_resume",
        )
        self.assertEqual(
            live_agent_context_contract("kiro_live_session", "live_session")["context_durability"],
            "provider_managed_resume",
        )

    def test_group_config_defaults_kiro_live_session_command(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "live-agents.json"
            path.write_text(
                json.dumps(
                    {
                        "agents": [
                            {
                                "agent_id": "kiro-live",
                                "provider_kind": "kiro_live_session",
                                "connection_kind": "live_session",
                                "session_id": "b83e983c-6230-4700-8309-010b87583a6b",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            loaded = load_group_configs(path)

        self.assertEqual(loaded[0].provider_kind, "kiro_live_session")
        self.assertEqual(loaded[0].connection_kind, "live_session")
        self.assertEqual(loaded[0].command, ["kiro"])


if __name__ == "__main__":
    unittest.main()
