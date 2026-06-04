import json
import os
import shlex
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.parse
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

from agentsassemble.gui import _make_handler
from agentsassemble.live_agent_meetings import start_live_agent_meeting


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class SelfServiceExampleTests(unittest.TestCase):
    def test_director_led_fake_self_service_agents_read_and_reply_through_room_tools(self):
        config = json.loads(
            (PROJECT_ROOT / "configs" / "live-agents.director-led-team.self-service.example.json").read_text(
                encoding="utf-8"
            )
        )
        agents = config["agents"]

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            calls_path = root / "self_service_calls.jsonl"
            fake_cli = root / "fake_room_cli.py"
            fake_cli.write_text(
                "\n".join(
                    [
                        "import json, os, subprocess, sys",
                        "calls_path = os.environ['SELF_SERVICE_CALLS_PATH']",
                        "args = sys.argv[1:]",
                        "with open(calls_path, 'a', encoding='utf-8') as handle:",
                        "    handle.write(json.dumps(args, ensure_ascii=False) + '\\n')",
                        "sys.exit(subprocess.run([sys.executable, '-m', 'agentsassemble.cli', *args]).returncode)",
                    ]
                ),
                encoding="utf-8",
            )
            start_live_agent_meeting(
                root,
                council_config_path=PROJECT_ROOT / "configs" / "director-led-team.example.json",
                agent_config_path=PROJECT_ROOT / "configs" / "agents.director-led-team.example.json",
                meeting_id="director-led-room",
            )
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                server_url = f"http://127.0.0.1:{server.server_port}"
                for agent in agents:
                    _post_json(
                        f"{server_url}/api/live-agents",
                        {
                            "agent_id": agent["agent_id"],
                            "display_name": agent["display_name"],
                            "provider_kind": agent["provider_kind"],
                            "connection_kind": agent["connection_kind"],
                            "meeting_id": "director-led-room",
                            "engagement_mode": "always",
                            "capabilities": ["room_chat", "self_service"],
                        },
                    )
                    _post_json(
                        f"{server_url}/api/live-agents/{urllib.parse.quote(agent['agent_id'], safe='')}/engagement",
                        {"engagement_mode": "always"},
                    )
                baseline_lobby = _get_json(f"{server_url}/api/lobby")
                baseline_event_id = _latest_event_id(baseline_lobby.get("events"))
                for agent in agents:
                    _post_json(
                        f"{server_url}/api/live-agents/{urllib.parse.quote(agent['agent_id'], safe='')}/heartbeat",
                        {"status": "online", "last_observed_event_id": baseline_event_id},
                    )
                human_event = _post_json(
                    f"{server_url}/api/lobby",
                    {"name": "human", "side": "mine", "kind": "message", "message": "작업 지시"},
                )
                source_event_id = str(human_event["event"]["id"])

                for agent in agents:
                    env = os.environ.copy()
                    env.update(_self_service_env(server_url, agent["agent_id"], fake_cli, calls_path))
                    process = subprocess.Popen(
                        [
                            sys.executable,
                            str(PROJECT_ROOT / "scripts" / "my_self_service_agent.py"),
                            "--message",
                            f"{agent['display_name']} reviewed the room diff.",
                            "--once",
                            "--wait-timeout",
                            "1",
                        ],
                        cwd=PROJECT_ROOT,
                        env=env,
                        stdin=subprocess.DEVNULL,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.PIPE,
                        text=True,
                    )
                    try:
                        exit_code = process.wait(timeout=5)
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
                    self.assertEqual(exit_code, 0, f"{agent['agent_id']}: {stderr}")

                lobby = _get_json(f"{server_url}/api/lobby")
            finally:
                server.shutdown()
                server.server_close()

            events = lobby["events"]
            replies = [
                event
                for event in events
                if event.get("live_agent_endpoint") is True and event.get("source_event_id") == source_event_id
            ]
            self.assertEqual(sorted(event["actor_id"] for event in replies), sorted(agent["agent_id"] for agent in agents))
            self.assertTrue(all(event.get("auto_chain_depth") == 1 for event in replies))

            calls = [json.loads(line) for line in calls_path.read_text(encoding="utf-8").splitlines() if line.strip()]
            live_agent_verbs = [call[1] for call in calls if len(call) >= 2 and call[0] == "live-agent"]
            calls_by_verb = _live_agent_calls_by_verb(calls)
            expected_agent_ids = sorted(agent["agent_id"] for agent in agents)
            expected_heartbeat_agent_ids = sorted(agent_id for agent_id in expected_agent_ids for _ in range(2))
            self.assertEqual(set(live_agent_verbs), {"wait-next", "say", "heartbeat"})
            self.assertEqual(live_agent_verbs.count("wait-next"), len(agents))
            self.assertEqual(live_agent_verbs.count("say"), len(agents))
            self.assertEqual(live_agent_verbs.count("heartbeat"), len(agents) * 2)
            self.assertEqual(_agent_ids_for_calls(calls_by_verb["wait-next"]), expected_agent_ids)
            self.assertEqual(_agent_ids_for_calls(calls_by_verb["say"]), expected_agent_ids)
            self.assertEqual(_agent_ids_for_calls(calls_by_verb["heartbeat"]), expected_heartbeat_agent_ids)
            self.assertEqual(_heartbeat_statuses_by_agent(calls_by_verb["heartbeat"]), {agent_id: {"working", "online"} for agent_id in expected_agent_ids})
            self.assertNotIn("run", live_agent_verbs)
            self.assertNotIn("flow", live_agent_verbs)

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

    def test_custom_self_service_example_agent_posts_dm_reply_from_wait_next(self):
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
                        "    print(json.dumps({'status': 'event', 'action': 'dm', 'source_event_id': 'dm-1'}))",
                        "elif command in {'dm-reply', 'heartbeat'}:",
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
                    "AGENTSASSEMBLE_MEETING_ID": "meeting-1",
                    "AGENTSASSEMBLE_POLL_INTERVAL": "0.05",
                    "AGENTSASSEMBLE_ROOM_COMMAND": shlex.join([*base, "room"]),
                    "AGENTSASSEMBLE_WAIT_NEXT_COMMAND": shlex.join([*base, "wait-next", "--json"]),
                    "AGENTSASSEMBLE_SAY_COMMAND_TEMPLATE": shlex.join([*base, "say", "--source-event-id", "{source_event_id}", "--auto-chain-depth", "{auto_chain_depth}", "--", "{message}"]),
                    "AGENTSASSEMBLE_OFFICIAL_REPLY_COMMAND_TEMPLATE": shlex.join([*base, "official-reply", "--meeting-id", "{meeting_id}", "--source-event-id", "{source_event_id}", "--", "{message}"]),
                    "AGENTSASSEMBLE_DM_REPLY_COMMAND_TEMPLATE": shlex.join([*base, "dm-reply", "--source-event-id", "{source_event_id}", "--", "{message}"]),
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
                            "--last-observed-dm-event-id={last_observed_dm_event_id}",
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
            dm_call = next(call for call in calls if call and call[0] == "dm-reply")
            self.assertNotIn("say", {call[0] for call in calls if call})
            self.assertNotIn("official-reply", {call[0] for call in calls if call})
            self.assertIn("--json", dm_call)
            self.assertIn("--", dm_call)
            self.assertEqual(dm_call[dm_call.index("--") + 1], "-h")
            self.assertIn("--last-observed-dm-event-id=dm-1", next(call for call in calls if call and call[0] == "heartbeat"))

    def test_custom_self_service_example_agent_reports_error_when_official_reply_launch_fails(self):
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
                        "    print(json.dumps({'status': 'event', 'action': 'official_turn', 'meeting_id': 'meeting-1', 'source_event_id': 'live-1'}))",
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
                            "/definitely/missing/agentsassemble-official-reply",
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

            self.assertEqual(exit_code, 1, stderr)
            calls = [json.loads(line) for line in calls_path.read_text(encoding="utf-8").splitlines() if line.strip()]
            self.assertNotIn("say", {call[0] for call in calls if call})
            self.assertNotIn("official-reply", {call[0] for call in calls if call})
            heartbeat_calls = [call for call in calls if call and call[0] == "heartbeat"]
            self.assertEqual(heartbeat_calls[0][heartbeat_calls[0].index("--status") + 1], "working")
            error_heartbeat = heartbeat_calls[-1]
            self.assertEqual(error_heartbeat[error_heartbeat.index("--status") + 1], "error")
            self.assertIn("--last-error=official reply failed", error_heartbeat)
            self.assertIn("--last-observed-live-event-id=live-1", error_heartbeat)

    def test_custom_self_service_example_agent_reports_error_when_lobby_reply_launch_fails(self):
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
                        "    print(json.dumps({'status': 'event', 'action': 'lobby', 'source_event_id': 'evt-1'}))",
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
                    "AGENTSASSEMBLE_MEETING_ID": "meeting-1",
                    "AGENTSASSEMBLE_POLL_INTERVAL": "0.05",
                    "AGENTSASSEMBLE_ROOM_COMMAND": shlex.join([*base, "room"]),
                    "AGENTSASSEMBLE_WAIT_NEXT_COMMAND": shlex.join([*base, "wait-next", "--json"]),
                    "AGENTSASSEMBLE_SAY_COMMAND_TEMPLATE": shlex.join(
                        [
                            "/definitely/missing/agentsassemble-say",
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

            self.assertEqual(exit_code, 1, stderr)
            calls = [json.loads(line) for line in calls_path.read_text(encoding="utf-8").splitlines() if line.strip()]
            self.assertNotIn("say", {call[0] for call in calls if call})
            self.assertNotIn("official-reply", {call[0] for call in calls if call})
            heartbeat_calls = [call for call in calls if call and call[0] == "heartbeat"]
            self.assertEqual(heartbeat_calls[0][heartbeat_calls[0].index("--status") + 1], "working")
            error_heartbeat = heartbeat_calls[-1]
            self.assertEqual(error_heartbeat[error_heartbeat.index("--status") + 1], "error")
            self.assertIn("--last-error=lobby reply failed", error_heartbeat)
            self.assertIn("--last-observed-event-id=evt-1", error_heartbeat)

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

    def test_custom_self_service_example_agent_reports_error_when_return_packet_ack_launch_fails(self):
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
                        "    read = [sys.executable, __file__, 'return-packet', '--source-event-id', 'packet-1', '--json']",
                        "    ack = ['/definitely/missing/agentsassemble-return-packet-ack']",
                        "    print(json.dumps({'status': 'event', 'action': 'return_packet', 'source_event_id': 'packet-1', 'read_command': read, 'ack_command': ack}))",
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
            read_call = next(call for call in calls if call and call[0] == "return-packet")
            heartbeat_calls = [call for call in calls if call and call[0] == "heartbeat"]
            self.assertEqual(len(heartbeat_calls), 1)
            error_heartbeat = heartbeat_calls[0]
            self.assertLess(calls.index(read_call), calls.index(error_heartbeat))
            self.assertEqual(error_heartbeat[error_heartbeat.index("--status") + 1], "error")
            self.assertIn("--last-error=return packet ack failed", error_heartbeat)
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

    def test_custom_self_service_example_agent_acks_persona_blocked_official_turn_without_replying(self):
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
                        "    ack = [sys.executable, __file__, 'heartbeat', '--status', 'online', '--last-observed-live-event-id=live-next', '--last-attention=persona_context_blocked_official_turn', '--json']",
                        "    print(json.dumps({'status': 'event', 'action': 'persona_blocks_official_turn', 'source_event_id': 'live-next', 'reason': 'persona_context_blocked_official_turn', 'attention': ['persona_context_blocked_official_turn'], 'ack_command': ack}))",
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
                    "AGENTSASSEMBLE_MEETING_ID": "meeting-1",
                    "AGENTSASSEMBLE_POLL_INTERVAL": "0.05",
                    "AGENTSASSEMBLE_ROOM_COMMAND": shlex.join([*base, "room"]),
                    "AGENTSASSEMBLE_WAIT_NEXT_COMMAND": shlex.join([*base, "wait-next", "--json"]),
                    "AGENTSASSEMBLE_SAY_COMMAND_TEMPLATE": shlex.join([*base, "say", "--", "{message}"]),
                    "AGENTSASSEMBLE_OFFICIAL_REPLY_COMMAND_TEMPLATE": shlex.join([*base, "official-reply", "--", "{message}"]),
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
            self.assertIn("--last-observed-live-event-id=live-next", heartbeat_call)
            self.assertIn("--last-attention=persona_context_blocked_official_turn", heartbeat_call)

    def test_custom_self_service_example_agent_reports_error_when_observe_lobby_ack_launch_fails(self):
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
                        "    ack = ['/definitely/missing/agentsassemble-observe-lobby-ack']",
                        "    print(json.dumps({'status': 'event', 'action': 'observe_lobby', 'source_event_id': 'evt-observe', 'engagement_mode': 'mentioned', 'ack_command': ack}))",
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
            self.assertIn("--last-error=lobby observation ack failed", error_heartbeat)
            self.assertIn("--last-observed-event-id=evt-observe", error_heartbeat)


def _self_service_env(server_url: str, agent_id: str, fake_cli: Path, calls_path: Path) -> dict[str, str]:
    base = [sys.executable, str(fake_cli), "live-agent"]
    identity = ["--server", server_url, "--agent-id", agent_id]
    return {
        "SELF_SERVICE_CALLS_PATH": str(calls_path),
        "AGENTSASSEMBLE_MEETING_ID": "director-led-room",
        "AGENTSASSEMBLE_POLL_INTERVAL": "0.05",
        "AGENTSASSEMBLE_ROOM_COMMAND": shlex.join([*base, "room", *identity]),
        "AGENTSASSEMBLE_WAIT_NEXT_COMMAND": shlex.join(
            [
                *base,
                "wait-next",
                *identity,
                "--max-chain-depth",
                "1",
                "--poll-interval",
                "0.05",
                "--json",
            ]
        ),
        "AGENTSASSEMBLE_SAY_COMMAND_TEMPLATE": shlex.join(
            [
                *base,
                "say",
                *identity,
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
                *identity,
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
                *identity,
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


def _post_json(url: str, payload: dict[str, object]) -> dict[str, object]:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=5) as response:
        loaded = json.loads(response.read().decode("utf-8"))
    return loaded if isinstance(loaded, dict) else {}


def _get_json(url: str) -> dict[str, object]:
    with urllib.request.urlopen(url, timeout=5) as response:
        loaded = json.loads(response.read().decode("utf-8"))
    return loaded if isinstance(loaded, dict) else {}


def _latest_event_id(events: object) -> str:
    if not isinstance(events, list):
        return ""
    for event in reversed(events):
        if isinstance(event, dict) and str(event.get("id") or "").strip():
            return str(event.get("id") or "").strip()
    return ""


def _live_agent_calls_by_verb(calls: list[object]) -> dict[str, list[list[str]]]:
    grouped: dict[str, list[list[str]]] = {}
    for call in calls:
        if not isinstance(call, list) or len(call) < 2 or call[0] != "live-agent":
            continue
        verb = str(call[1])
        grouped.setdefault(verb, []).append([str(part) for part in call])
    return grouped


def _agent_ids_for_calls(calls: list[list[str]]) -> list[str]:
    agent_ids = []
    for call in calls:
        if "--agent-id" in call:
            index = call.index("--agent-id")
            if index + 1 < len(call):
                agent_ids.append(call[index + 1])
    return sorted(agent_ids)


def _heartbeat_statuses_by_agent(calls: list[list[str]]) -> dict[str, set[str]]:
    statuses_by_agent: dict[str, set[str]] = {}
    for call in calls:
        agent_id = _argument_after(call, "--agent-id")
        status = _argument_after(call, "--status")
        if agent_id and status:
            statuses_by_agent.setdefault(agent_id, set()).add(status)
    return statuses_by_agent


def _argument_after(call: list[str], flag: str) -> str:
    if flag not in call:
        return ""
    index = call.index(flag)
    if index + 1 >= len(call):
        return ""
    return call[index + 1]


if __name__ == "__main__":
    unittest.main()
