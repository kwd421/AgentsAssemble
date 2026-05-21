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

    def test_custom_self_service_example_agent_trusts_wait_next_replyable_lobby_action(self):
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
                        "if command == 'room':",
                        "    print(json.dumps({'agent': {'engagement_mode': 'mentioned'}}))",
                        "elif command == 'wait-next':",
                        "    if os.path.exists(state_path):",
                        "        print(json.dumps({'status': 'idle'}))",
                        "    else:",
                        "        open(state_path, 'w', encoding='utf-8').write('seen')",
                        "        print(json.dumps({'status': 'event', 'action': 'lobby', 'source_event_id': 'evt-mentioned', 'auto_chain_depth': 1}))",
                        "elif command in {'say', 'heartbeat'}:",
                        "    print(json.dumps({'status': 'ok'}))",
                        "elif command == 'official-reply':",
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
            ] + ["--once"]
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
            self.assertIn("--source-event-id", say_call)
            self.assertIn("evt-mentioned", say_call)
            self.assertIn("--last-observed-event-id=evt-mentioned", next(call for call in calls if call and call[0] == "heartbeat"))

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

    def test_custom_self_service_example_agent_reads_return_packet_before_ack_without_replying(self):
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
                        "        read = [sys.executable, __file__, 'return-packet', '--source-event-id', 'packet-1', '--json']",
                        "        ack = [sys.executable, __file__, 'heartbeat', '--status', 'online', '--last-observed-live-event-id=packet-1', '--json']",
                        "        print(json.dumps({'status': 'event', 'action': 'return_packet', 'source_event_id': 'packet-1', 'artifact_path': 'return_packets/architect.md', 'read_command': read, 'ack_command': ack}))",
                        "elif command == 'return-packet':",
                        "    print(json.dumps({'status': 'ok', 'markdown': 'Architect private return packet.'}))",
                        "elif command == 'heartbeat':",
                        "    print(json.dumps({'status': 'ok'}))",
                        "elif command in {'say', 'official-reply'}:",
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
            ] + ["--once"]
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
            self.assertNotIn("say", {call[0] for call in calls if call})
            self.assertNotIn("official-reply", {call[0] for call in calls if call})
            read_call = next(call for call in calls if call and call[0] == "return-packet")
            heartbeat_call = next(call for call in calls if call and call[0] == "heartbeat")
            self.assertLess(calls.index(read_call), calls.index(heartbeat_call))
            self.assertIn("--source-event-id", read_call)
            self.assertIn("packet-1", read_call)
            self.assertIn("--last-observed-live-event-id=packet-1", heartbeat_call)

    def test_custom_self_service_example_agent_does_not_ack_return_packet_without_read_command(self):
        config = json.loads((PROJECT_ROOT / "configs" / "live-agents.self-service.example.json").read_text(encoding="utf-8"))
        custom_agent = next(agent for agent in config["agents"] if agent["agent_id"] == "custom-cli-live")
        command = list(custom_agent["command"])
        script_arg = next(part for part in command if str(part).endswith(".py"))

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            fake_cli = root / "fake_cli.py"
            calls_path = root / "calls.jsonl"
            fake_cli.write_text(
                "\n".join(
                    [
                        "import json, os, sys",
                        "calls_path = os.environ['SELF_SERVICE_CALLS_PATH']",
                        "args = sys.argv[1:]",
                        "with open(calls_path, 'a', encoding='utf-8') as handle:",
                        "    handle.write(json.dumps(args) + '\\n')",
                        "command = args[0] if args else ''",
                        "if command == 'wait-next':",
                        "    ack = [sys.executable, __file__, 'heartbeat', '--status', 'online', '--last-observed-live-event-id=packet-1', '--json']",
                        "    print(json.dumps({'status': 'event', 'action': 'return_packet', 'source_event_id': 'packet-1', 'ack_command': ack}))",
                        "elif command == 'heartbeat':",
                        "    print(json.dumps({'status': 'ok'}))",
                        "elif command in {'say', 'official-reply', 'return-packet'}:",
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
                    "AGENTSASSEMBLE_MEETING_ID": "meeting-1",
                    "AGENTSASSEMBLE_POLL_INTERVAL": "0.05",
                    "AGENTSASSEMBLE_ROOM_COMMAND": shlex.join([*base, "room"]),
                    "AGENTSASSEMBLE_WAIT_NEXT_COMMAND": shlex.join([*base, "wait-next", "--json"]),
                    "AGENTSASSEMBLE_SAY_COMMAND_TEMPLATE": shlex.join(
                        [*base, "say", "--source-event-id", "{source_event_id}", "--auto-chain-depth", "{auto_chain_depth}", "--", "{message}"]
                    ),
                    "AGENTSASSEMBLE_OFFICIAL_REPLY_COMMAND_TEMPLATE": shlex.join(
                        [*base, "official-reply", "--meeting-id", "{meeting_id}", "--source-event-id", "{source_event_id}", "--", "{message}"]
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
            ] + ["--once"]
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

            self.assertEqual(exit_code, 1, stderr)
            calls = [json.loads(line) for line in calls_path.read_text(encoding="utf-8").splitlines() if line.strip()]
            self.assertNotIn("say", {call[0] for call in calls if call})
            self.assertNotIn("official-reply", {call[0] for call in calls if call})
            heartbeat_calls = [call for call in calls if call and call[0] == "heartbeat"]
            self.assertEqual(len(heartbeat_calls), 1)
            error_heartbeat = heartbeat_calls[0]
            self.assertEqual(error_heartbeat[error_heartbeat.index("--status") + 1], "error")
            self.assertIn("--last-error=return packet read command missing", error_heartbeat)
            self.assertIn("--last-observed-live-event-id=packet-1", error_heartbeat)

    def test_custom_self_service_example_agent_reports_error_when_return_packet_read_launch_fails(self):
        config = json.loads((PROJECT_ROOT / "configs" / "live-agents.self-service.example.json").read_text(encoding="utf-8"))
        custom_agent = next(agent for agent in config["agents"] if agent["agent_id"] == "custom-cli-live")
        command = list(custom_agent["command"])
        script_arg = next(part for part in command if str(part).endswith(".py"))

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            fake_cli = root / "fake_cli.py"
            calls_path = root / "calls.jsonl"
            fake_cli.write_text(
                "\n".join(
                    [
                        "import json, os, sys",
                        "calls_path = os.environ['SELF_SERVICE_CALLS_PATH']",
                        "args = sys.argv[1:]",
                        "with open(calls_path, 'a', encoding='utf-8') as handle:",
                        "    handle.write(json.dumps(args) + '\\n')",
                        "command = args[0] if args else ''",
                        "if command == 'wait-next':",
                        "    read = ['/definitely/missing/agentsassemble-return-packet-reader']",
                        "    ack = [sys.executable, __file__, 'heartbeat', '--status', 'online', '--last-observed-live-event-id=packet-1', '--json']",
                        "    print(json.dumps({'status': 'event', 'action': 'return_packet', 'source_event_id': 'packet-1', 'read_command': read, 'ack_command': ack}))",
                        "elif command == 'heartbeat':",
                        "    print(json.dumps({'status': 'ok'}))",
                        "elif command in {'say', 'official-reply', 'return-packet'}:",
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
                    "AGENTSASSEMBLE_MEETING_ID": "meeting-1",
                    "AGENTSASSEMBLE_POLL_INTERVAL": "0.05",
                    "AGENTSASSEMBLE_ROOM_COMMAND": shlex.join([*base, "room"]),
                    "AGENTSASSEMBLE_WAIT_NEXT_COMMAND": shlex.join([*base, "wait-next", "--json"]),
                    "AGENTSASSEMBLE_SAY_COMMAND_TEMPLATE": shlex.join(
                        [*base, "say", "--source-event-id", "{source_event_id}", "--auto-chain-depth", "{auto_chain_depth}", "--", "{message}"]
                    ),
                    "AGENTSASSEMBLE_OFFICIAL_REPLY_COMMAND_TEMPLATE": shlex.join(
                        [*base, "official-reply", "--meeting-id", "{meeting_id}", "--source-event-id", "{source_event_id}", "--", "{message}"]
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
            ] + ["--once"]
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

            self.assertEqual(exit_code, 1, stderr)
            calls = [json.loads(line) for line in calls_path.read_text(encoding="utf-8").splitlines() if line.strip()]
            self.assertNotIn("say", {call[0] for call in calls if call})
            self.assertNotIn("official-reply", {call[0] for call in calls if call})
            heartbeat_calls = [call for call in calls if call and call[0] == "heartbeat"]
            self.assertEqual(len(heartbeat_calls), 1)
            error_heartbeat = heartbeat_calls[0]
            self.assertEqual(error_heartbeat[error_heartbeat.index("--status") + 1], "error")
            self.assertIn("--last-error=return packet read failed", error_heartbeat)
            self.assertIn("--last-observed-live-event-id=packet-1", error_heartbeat)

    def test_custom_self_service_example_agent_heartbeats_cursor_only_timeout_observation(self):
        config = json.loads((PROJECT_ROOT / "configs" / "live-agents.self-service.example.json").read_text(encoding="utf-8"))
        custom_agent = next(agent for agent in config["agents"] if agent["agent_id"] == "custom-cli-live")
        command = list(custom_agent["command"])
        script_arg = next(part for part in command if str(part).endswith(".py"))

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            fake_cli = root / "fake_cli.py"
            calls_path = root / "calls.jsonl"
            fake_cli.write_text(
                "\n".join(
                    [
                        "import json, os, sys",
                        "calls_path = os.environ['SELF_SERVICE_CALLS_PATH']",
                        "args = sys.argv[1:]",
                        "with open(calls_path, 'a', encoding='utf-8') as handle:",
                        "    handle.write(json.dumps(args) + '\\n')",
                        "command = args[0] if args else ''",
                        "if command == 'wait-next':",
                        "    print(json.dumps({'status': 'timeout', 'last_observed_event_id': 'evt-chain', 'last_observed_live_event_id': 'live-info'}))",
                        "    sys.exit(1)",
                        "elif command == 'heartbeat':",
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
            ] + ["--once"]
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
            heartbeat_call = next(call for call in calls if call and call[0] == "heartbeat")
            self.assertIn("--status", heartbeat_call)
            self.assertIn("online", heartbeat_call)
            self.assertIn("--last-observed-event-id=evt-chain", heartbeat_call)
            self.assertIn("--last-observed-live-event-id=live-info", heartbeat_call)

    def test_custom_self_service_example_agent_acks_observe_lobby_without_replying(self):
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
                        "        ack = [sys.executable, __file__, 'heartbeat', '--status', 'online', '--last-observed-event-id=evt-observe', '--json']",
                        "        print(json.dumps({'status': 'event', 'action': 'observe_lobby', 'source_event_id': 'evt-observe', 'engagement_mode': 'mentioned', 'ack_command': ack}))",
                        "elif command == 'heartbeat':",
                        "    print(json.dumps({'status': 'ok'}))",
                        "elif command in {'say', 'official-reply'}:",
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
            ] + ["--once"]
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
            self.assertNotIn("say", {call[0] for call in calls if call})
            self.assertNotIn("official-reply", {call[0] for call in calls if call})
            heartbeat_call = next(call for call in calls if call and call[0] == "heartbeat")
            self.assertIn("--last-observed-event-id=evt-observe", heartbeat_call)


if __name__ == "__main__":
    unittest.main()
