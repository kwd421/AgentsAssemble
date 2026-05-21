import json
import os
import shlex
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class SelfServiceExampleTests(unittest.TestCase):
    def test_custom_self_service_example_agent_uses_room_command_templates(self):
        config = json.loads((PROJECT_ROOT / "configs" / "live-agents.self-service.example.json").read_text(encoding="utf-8"))
        custom_agent = next(agent for agent in config["agents"] if agent["agent_id"] == "custom-cli-live")
        command = list(custom_agent["command"])
        script_arg = next(part for part in command if str(part).endswith(".py"))
        script_path = PROJECT_ROOT / script_arg
        self.assertTrue(script_path.exists())

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            fake_cli = root / "fake_cli.py"
            calls_path = root / "calls.jsonl"
            state_path = root / "state.txt"
            fake_cli.write_text(
                "\n".join(
                    [
                        "import json, os, sys",
                        "calls_path = os.environ['SELF_SERVICE_CALLS_PATH']",
                        "state_path = os.environ['SELF_SERVICE_STATE_PATH']",
                        "args = sys.argv[1:]",
                        "with open(calls_path, 'a', encoding='utf-8') as handle:",
                        "    handle.write(json.dumps(args) + '\\n')",
                        "command = args[0] if args else ''",
                        "if command == 'room':",
                        "    print(json.dumps({'agent': {'engagement_mode': 'always'}}))",
                        "elif command == 'wait-next':",
                        "    if os.path.exists(state_path):",
                        "        print(json.dumps({'status': 'idle'}))",
                        "    else:",
                        "        open(state_path, 'w', encoding='utf-8').write('seen')",
                        "        print(json.dumps({'status': 'event', 'action': 'lobby', 'source_event_id': 'evt-1'}))",
                        "elif command in {'say', 'official-reply', 'heartbeat'}:",
                        "    print(json.dumps({'status': 'ok'}))",
                        "else:",
                        "    sys.exit(2)",
                    ]
                ),
                encoding="utf-8",
            )

            base = [sys.executable, str(fake_cli)]
            env = os.environ.copy()
            env.update(
                {
                    "SELF_SERVICE_CALLS_PATH": str(calls_path),
                    "SELF_SERVICE_STATE_PATH": str(state_path),
                    "AGENTSASSEMBLE_MEETING_ID": "meeting-1",
                    "AGENTSASSEMBLE_POLL_INTERVAL": "0.05",
                    "AGENTSASSEMBLE_ROOM_COMMAND": shlex.join([*base, "room"]),
                    "AGENTSASSEMBLE_WAIT_NEXT_COMMAND": shlex.join([*base, "wait-next", "--json"]),
                    "AGENTSASSEMBLE_SAY_COMMAND_TEMPLATE": shlex.join(
                        [
                            *base,
                            "say",
                            "--source-event-id",
                            "{source_event_id}",
                            "--auto-chain-depth",
                            "{auto_chain_depth}",
                            "--",
                            "{message}",
                        ]
                    ),
                    "AGENTSASSEMBLE_OFFICIAL_REPLY_COMMAND_TEMPLATE": shlex.join(
                        [
                            *base,
                            "official-reply",
                            "--meeting-id",
                            "{meeting_id}",
                            "--source-event-id",
                            "{source_event_id}",
                            "--",
                            "{message}",
                        ]
                    ),
                    "AGENTSASSEMBLE_HEARTBEAT_COMMAND_TEMPLATE": shlex.join(
                        [
                            *base,
                            "heartbeat",
                            "--status",
                            "{status}",
                            "--last-error={last_error}",
                            "--last-reply-at={last_reply_at}",
                            "--last-observed-event-id={last_observed_event_id}",
                            "--last-observed-live-event-id={last_observed_live_event_id}",
                            "--json",
                        ]
                    ),
                }
            )
            process_command = [
                sys.executable if part == "python3" else str(PROJECT_ROOT / part) if part == script_arg else str(part)
                for part in command
            ] + ["--message=-h", "--once"]
            process = subprocess.Popen(
                process_command,
                cwd=PROJECT_ROOT,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
            )
            try:
                exit_code = process.wait(timeout=3)
                stderr = process.stderr.read() if process.stderr is not None else ""
            finally:
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=1)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=1)
                if process.stderr is not None:
                    process.stderr.close()

            self.assertEqual(exit_code, 0, stderr)
            calls = [json.loads(line) for line in calls_path.read_text(encoding="utf-8").splitlines() if line.strip()]
            say_call = next(call for call in calls if call and call[0] == "say")
            self.assertIn("--json", say_call)
            self.assertIn("--", say_call)
            self.assertLess(say_call.index("--json"), say_call.index("--"))
            self.assertEqual(say_call[say_call.index("--") + 1], "-h")
            self.assertIn("--last-observed-event-id=evt-1", next(call for call in calls if call and call[0] == "heartbeat"))

    def test_custom_self_service_example_agent_posts_official_reply_from_wait_next(self):
        config = json.loads((PROJECT_ROOT / "configs" / "live-agents.self-service.example.json").read_text(encoding="utf-8"))
        custom_agent = next(agent for agent in config["agents"] if agent["agent_id"] == "custom-cli-live")
        command = list(custom_agent["command"])
        script_arg = next(part for part in command if str(part).endswith(".py"))

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            fake_cli = root / "fake_cli.py"
            calls_path = root / "calls.jsonl"
            state_path = root / "state.txt"
            fake_cli.write_text(
                "\n".join(
                    [
                        "import json, os, sys",
                        "calls_path = os.environ['SELF_SERVICE_CALLS_PATH']",
                        "state_path = os.environ['SELF_SERVICE_STATE_PATH']",
                        "args = sys.argv[1:]",
                        "with open(calls_path, 'a', encoding='utf-8') as handle:",
                        "    handle.write(json.dumps(args) + '\\n')",
                        "command = args[0] if args else ''",
                        "if command == 'wait-next':",
                        "    if os.path.exists(state_path):",
                        "        print(json.dumps({'status': 'idle'}))",
                        "    else:",
                        "        open(state_path, 'w', encoding='utf-8').write('seen')",
                        "        print(json.dumps({'status': 'event', 'action': 'official_turn', 'meeting_id': 'meeting-1', 'source_event_id': 'live-1'}))",
                        "elif command in {'official-reply', 'heartbeat'}:",
                        "    print(json.dumps({'status': 'ok'}))",
                        "elif command == 'say':",
                        "    sys.exit(9)",
                        "else:",
                        "    sys.exit(2)",
                    ]
                ),
                encoding="utf-8",
            )

            base = [sys.executable, str(fake_cli)]
            env = os.environ.copy()
            env.update(
                {
                    "SELF_SERVICE_CALLS_PATH": str(calls_path),
                    "SELF_SERVICE_STATE_PATH": str(state_path),
                    "AGENTSASSEMBLE_MEETING_ID": "meeting-1",
                    "AGENTSASSEMBLE_POLL_INTERVAL": "0.05",
                    "AGENTSASSEMBLE_ROOM_COMMAND": shlex.join([*base, "room"]),
                    "AGENTSASSEMBLE_WAIT_NEXT_COMMAND": shlex.join([*base, "wait-next", "--json"]),
                    "AGENTSASSEMBLE_SAY_COMMAND_TEMPLATE": shlex.join(
                        [
                            *base,
                            "say",
                            "--source-event-id",
                            "{source_event_id}",
                            "--auto-chain-depth",
                            "{auto_chain_depth}",
                            "--",
                            "{message}",
                        ]
                    ),
                    "AGENTSASSEMBLE_OFFICIAL_REPLY_COMMAND_TEMPLATE": shlex.join(
                        [
                            *base,
                            "official-reply",
                            "--meeting-id",
                            "{meeting_id}",
                            "--source-event-id",
                            "{source_event_id}",
                            "--",
                            "{message}",
                        ]
                    ),
                    "AGENTSASSEMBLE_HEARTBEAT_COMMAND_TEMPLATE": shlex.join(
                        [
                            *base,
                            "heartbeat",
                            "--status",
                            "{status}",
                            "--last-error={last_error}",
                            "--last-reply-at={last_reply_at}",
                            "--last-observed-event-id={last_observed_event_id}",
                            "--last-observed-live-event-id={last_observed_live_event_id}",
                            "--json",
                        ]
                    ),
                }
            )
            process_command = [
                sys.executable if part == "python3" else str(PROJECT_ROOT / part) if part == script_arg else str(part)
                for part in command
            ] + ["--message=-h", "--once"]
            process = subprocess.Popen(
                process_command,
                cwd=PROJECT_ROOT,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
            )
            try:
                exit_code = process.wait(timeout=3)
                stderr = process.stderr.read() if process.stderr is not None else ""
            finally:
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=1)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=1)
                if process.stderr is not None:
                    process.stderr.close()

            self.assertEqual(exit_code, 0, stderr)
            calls = [json.loads(line) for line in calls_path.read_text(encoding="utf-8").splitlines() if line.strip()]
            official_call = next(call for call in calls if call and call[0] == "official-reply")
            self.assertNotIn("say", {call[0] for call in calls if call})
            self.assertIn("--json", official_call)
            self.assertIn("--", official_call)
            self.assertLess(official_call.index("--json"), official_call.index("--"))
            self.assertEqual(official_call[official_call.index("--") + 1], "-h")
            self.assertIn("--last-observed-live-event-id=live-1", next(call for call in calls if call and call[0] == "heartbeat"))


if __name__ == "__main__":
    unittest.main()
