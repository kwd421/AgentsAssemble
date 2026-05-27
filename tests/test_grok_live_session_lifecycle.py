import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from urllib.request import Request, urlopen

from agentsassemble.grok_resident import (
    GROK_JSON_PARSE_FAILURE,
    GROK_MISSING_SESSION_ID,
    GROK_SUBPROCESS_NONZERO,
    GROK_SUBPROCESS_TIMEOUT,
    GrokResidentCommandRunner,
    grok_error_category,
)
from agentsassemble.gui import _make_handler
from agentsassemble.live_agent_processes import LiveAgentProcessSupervisor
from agentsassemble.live_agent_runner import ResidentAgentConfig
from agentsassemble.live_agents import read_live_agents
from agentsassemble.meeting_events import read_live_events


def config(**overrides):
    values = {
        "server": "http://room.local",
        "agent_id": "grok-live",
        "display_name": "Grok Live",
        "provider_kind": "grok_live_session",
        "connection_kind": "live_session",
        "session_id": "",
        "endpoint": "",
        "auth_ref": "",
        "meeting_id": "",
        "engagement_mode": "moderator_called",
        "command": ["grok"],
        "timeout_seconds": 5,
        "poll_interval": 0.05,
        "heartbeat_interval": 0.0,
        "cooldown": 0.0,
        "max_chain_depth": 0,
        "max_ticks": 1,
    }
    values.update(overrides)
    return ResidentAgentConfig(**values)


class GrokLiveSessionLifecycleTests(unittest.TestCase):
    def test_fake_grok_lifecycle_preserves_session_through_start_restart_stop(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            root = temp_path / "room"
            root.mkdir()
            bin_dir = temp_path / "bin"
            bin_dir.mkdir()
            fake_grok_log = temp_path / "fake-grok.jsonl"
            _write_fake_grok_executable(bin_dir / "grok")
            council_config = temp_path / "council.json"
            agent_config = temp_path / "agents.json"
            live_agent_config = temp_path / "live-agents.json"
            _write_single_grok_session_configs(council_config, agent_config, live_agent_config)

            supervisor = LiveAgentProcessSupervisor(root)
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root, process_supervisor=supervisor))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            meeting_id = "grok-live-fake-complete"
            group_id = "grok-live-fake-complete"
            stop_payload = {}
            env = {
                "PATH": f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}",
                "AGENTSASSEMBLE_FAKE_GROK_LOG": str(fake_grok_log),
            }
            try:
                with patch.dict(os.environ, env, clear=False):
                    start_payload = _post_json(
                        server.server_port,
                        "/api/live-agent-sessions/start",
                        {
                            "meeting_id": meeting_id,
                            "group_id": group_id,
                            "council_config_path": str(council_config),
                            "agent_config_path": str(agent_config),
                            "live_agent_config_path": str(live_agent_config),
                            "connect_timeout_seconds": 8,
                            "run_remaining_rounds": True,
                            "round_timeout_seconds": 8,
                            "round_max_rounds": 1,
                            "round_stop_on_timeout": True,
                        },
                        timeout=60,
                    )
                    restart_payload = _post_json(
                        server.server_port,
                        "/api/live-agent-sessions/restart",
                        {
                            "meeting_id": meeting_id,
                            "group_id": group_id,
                            "connect_timeout_seconds": 8,
                            "run_remaining_rounds": True,
                            "round_timeout_seconds": 8,
                            "round_max_rounds": 1,
                            "round_stop_on_timeout": True,
                            "finalize_after_rounds": True,
                        },
                        timeout=60,
                    )
                    stop_payload = _post_json(
                        server.server_port,
                        "/api/live-agent-sessions/stop",
                        {"meeting_id": meeting_id, "group_id": group_id},
                        timeout=12,
                    )
            finally:
                if not stop_payload:
                    try:
                        _post_json(
                            server.server_port,
                            "/api/live-agent-sessions/stop",
                            {"meeting_id": meeting_id, "group_id": group_id},
                            timeout=12,
                        )
                    except Exception:
                        pass
                supervisor.close()
                server.shutdown()
                server.server_close()

            self.assertEqual(start_payload["status"], "ready")
            self.assertEqual(start_payload["connection"]["expected"], 1)
            self.assertEqual(start_payload["connection"]["connected"], 1)
            self.assertEqual(start_payload["auto_rounds"]["status"], "answered")
            self.assertEqual(start_payload["auto_rounds"]["answered_round_count"], 1)
            self.assertEqual(restart_payload["status"], "ready")
            self.assertEqual(restart_payload["auto_rounds"]["status"], "answered")
            self.assertEqual(restart_payload["auto_rounds"]["answered_round_count"], 1)
            self.assertEqual(restart_payload["finalization"]["status"], "finalized")
            self.assertEqual(restart_payload["finalization"]["official_event_count"], 2)
            self.assertEqual(stop_payload["status"], "stopped")

            invocations = [
                json.loads(line)
                for line in fake_grok_log.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            self.assertEqual([entry["mode"] for entry in invocations], ["fresh", "resume"])
            self.assertEqual(invocations[1]["resume_id"], "grok-fake-session-001")
            self.assertTrue(all(entry["flags"] == ["--disable-web-search", "--no-subagents", "--verbatim"] for entry in invocations))
            self.assertTrue(all("prompt" not in entry for entry in invocations))

            meeting_dir = root / "meetings" / meeting_id
            official_events = [
                event
                for event in read_live_events(meeting_dir, limit=None)
                if event.get("kind") == "message" and event.get("official_record") is True
            ]
            self.assertEqual(len(official_events), 2)
            self.assertEqual({event.get("target_agent_id") for event in official_events}, {"grok-live"})
            agents = {agent["agent_id"]: agent for agent in read_live_agents(root) if agent.get("meeting_id") == meeting_id}
            self.assertEqual(agents["grok-live"]["session_id"], "grok-fake-session-001")
            self.assertEqual(agents["grok-live"]["status"], "offline")

    def test_fake_grok_failure_modes_keep_safe_categories(self):
        cases = [
            ("nonzero", GROK_SUBPROCESS_NONZERO, RuntimeError),
            ("timeout", GROK_SUBPROCESS_TIMEOUT, RuntimeError),
            ("malformed", GROK_JSON_PARSE_FAILURE, ValueError),
            ("missing_session", GROK_MISSING_SESSION_ID, ValueError),
        ]
        for mode, category, error_type in cases:
            with self.subTest(mode=mode):
                with tempfile.TemporaryDirectory() as temp_dir:
                    fake_grok = Path(temp_dir) / "grok"
                    _write_fake_grok_executable(fake_grok)
                    env = {"AGENTSASSEMBLE_FAKE_GROK_MODE": mode}
                    runner = GrokResidentCommandRunner(config(command=[str(fake_grok)]), cwd=Path(temp_dir))
                    try:
                        with patch.dict(os.environ, env, clear=False):
                            with self.assertRaises(error_type) as caught:
                                runner([], "prompt", timeout_seconds=1)
                    finally:
                        runner.close()
                self.assertEqual(grok_error_category(caught.exception), category)


def _post_json(port: int, path: str, payload: dict[str, object], *, timeout: float) -> dict[str, object]:
    request = Request(
        f"http://127.0.0.1:{port}{path}",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _write_single_grok_session_configs(council_config: Path, agent_config: Path, live_agent_config: Path) -> None:
    council_config.write_text(
        json.dumps(
            {
                "topic": "Fake Grok Lifecycle",
                "question": "Can the fake Grok control plane preserve session ids across restart?",
                "roles": [
                    {
                        "id": "grok_reviewer",
                        "display_name": "Grok Reviewer",
                        "lens": "Verify the Grok live_session control plane.",
                        "research_focus": "Answer official lifecycle turns through the fake Grok runner.",
                    }
                ],
                "meeting_template": {
                    "id": "grok_fake_lifecycle",
                    "display_name": "Fake Grok Lifecycle",
                    "rounds": [
                        {
                            "id": "start_round",
                            "title": "Start Round",
                            "instruction": "Confirm the first fake Grok resident turn.",
                            "turn_control": {"selection": "all_roles"},
                        },
                        {
                            "id": "restart_round",
                            "title": "Restart Round",
                            "instruction": "Confirm fake Grok resumed from the captured session id.",
                            "turn_control": {"selection": "all_roles"},
                        },
                    ],
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    agent_config.write_text(
        json.dumps(
            {
                "providers": [
                    {
                        "id": "grok-live-provider",
                        "kind": "grok_live_session",
                        "display_name": "Fake Grok Live Provider",
                        "timeout_seconds": 8,
                    }
                ],
                "permission_profiles": [
                    {
                        "id": "grok_live_meeting_readonly",
                        "meeting_read": True,
                        "lobby_chat": True,
                        "official_turn": True,
                        "web_search": False,
                        "tool_use": False,
                        "filesystem_read": False,
                        "filesystem_write": False,
                        "git_write": False,
                        "push": False,
                        "secrets": False,
                        "implementation": False,
                    }
                ],
                "agent_bindings": [
                    {
                        "agent_id": "grok-live",
                        "role_id": "grok_reviewer",
                        "owner_id": "host",
                        "provider_id": "grok-live-provider",
                        "model_id": "fake-grok",
                        "permission_profile_id": "grok_live_meeting_readonly",
                        "join_mode": "fresh",
                        "engagement_mode": "moderator_called",
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    live_agent_config.write_text(
        json.dumps(
            {
                "poll_interval": 0.05,
                "heartbeat_interval": 0,
                "cooldown": 0,
                "max_chain_depth": 0,
                "agents": [
                    {
                        "agent_id": "grok-live",
                        "display_name": "Grok Live",
                        "provider_kind": "grok_live_session",
                        "connection_kind": "live_session",
                        "engagement_mode": "moderator_called",
                        "command": ["grok"],
                        "timeout_seconds": 5,
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def _write_fake_grok_executable(path: Path) -> None:
    path.write_text(
        """#!/usr/bin/env python3
import json
import os
import sys
import time
from pathlib import Path


SESSION_ID = "grok-fake-session-001"


def record(payload):
    log_path = os.environ.get("AGENTSASSEMBLE_FAKE_GROK_LOG")
    if not log_path:
        return
    with open(log_path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, sort_keys=True) + "\\n")


args = sys.argv[1:]
mode = os.environ.get("AGENTSASSEMBLE_FAKE_GROK_MODE", "ok")
if mode == "nonzero":
    print("private provider error", file=sys.stderr)
    raise SystemExit(7)
if mode == "timeout":
    time.sleep(5)
if mode == "malformed":
    print("not json private prompt echo")
    raise SystemExit(0)

try:
    prompt_path = Path(args[args.index("--prompt-file") + 1])
except (ValueError, IndexError):
    print("missing --prompt-file", file=sys.stderr)
    raise SystemExit(2)

prompt = prompt_path.read_text(encoding="utf-8")
resume_id = ""
if "--resume" in args:
    try:
        resume_id = args[args.index("--resume") + 1]
    except IndexError:
        resume_id = ""
mode_name = "resume" if resume_id else "fresh"
flags = [flag for flag in ("--disable-web-search", "--no-subagents", "--verbatim") if flag in args]
record(
    {
        "mode": mode_name,
        "resume_id": resume_id,
        "prompt_length": len(prompt),
        "flags": flags,
    }
)
text = f"fake Grok {mode_name} reply"
if mode == "missing_session":
    print(json.dumps({"text": text}))
    raise SystemExit(0)
print(json.dumps({"sessionId": resume_id or SESSION_ID, "text": text}))
""",
        encoding="utf-8",
    )
    path.chmod(0o755)


if __name__ == "__main__":
    unittest.main()
