import tempfile
import unittest
import json
import os
import sys
import threading
import time
from io import StringIO
from pathlib import Path
from http.server import ThreadingHTTPServer
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from agentsassemble.gui import (
    _make_handler,
    _safe_static_path,
    _sse_event,
    _sse_stream_error_payload,
    _stream_snapshot_payload,
    append_lobby_event,
    build_meeting_payload,
    list_meetings,
    provider_catalog_payload,
    read_lobby,
    read_side_chat,
    serve_gui,
    codex_session_invite_payload,
    codex_sessions_payload,
    connect_live_agent_payload,
    live_agents_payload,
    send_lobby_message_to_remote_bridge,
    append_side_chat_event,
)
from agentsassemble.meeting_events import append_live_event, write_live_state
from agentsassemble.meeting_events import read_live_events_after, read_lobby_events_after, read_side_chat_events_after
from agentsassemble.meeting import run_demo_meeting
from agentsassemble.live_agent_processes import LiveAgentProcessSupervisor


def _read_sse_frame(response, timeout: float = 3.0) -> str:
    lines = []
    deadline = time.time() + timeout
    while time.time() < deadline:
        raw_line = response.readline()
        if raw_line == b"":
            break
        line = raw_line.decode("utf-8").strip()
        if not line:
            if lines:
                break
            continue
        lines.append(line)
    return "\n".join(lines)


class GuiServerTests(unittest.TestCase):
    def test_build_meeting_payload_contains_tabs_and_artifacts(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            result = run_demo_meeting(adapter_name="mock", output_root=Path(temp_dir))

            payload = build_meeting_payload(result.meeting_dir)

            self.assertEqual(payload["meeting"]["meeting_id"], result.meeting_id)
            self.assertIn("agenda.md", payload["artifacts"])
            self.assertIn("transcript.md", payload["artifacts"])
            self.assertIn("decision.md", payload["artifacts"])
            self.assertIn("meeting.json", payload["artifacts"])
            self.assertIn("lore_lawyer/research.md", payload["research"])
            self.assertIn("lore_lawyer", payload["research_json"])
            self.assertIn("evidence_gate", payload["research_json"]["lore_lawyer"])
            self.assertIn("lore_lawyer.md", payload["return_packets"])
            self.assertEqual(payload["tabs"], ["lobby", "live", "board", "archive"])
            self.assertEqual(payload["tab_labels"]["lobby"], "로비")
            self.assertEqual(payload["tab_labels"]["live"], "실황")
            self.assertEqual(payload["tab_labels"]["board"], "작전판")
            self.assertEqual(payload["tab_labels"]["archive"], "아카이브")

    def test_build_meeting_payload_includes_room_log_for_free_chat(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            result = run_demo_meeting(adapter_name="mock", output_root=Path(temp_dir), meeting_mode="free_chat")

            payload = build_meeting_payload(result.meeting_dir)

            self.assertEqual(payload["meeting"]["meeting_mode"], "free_chat")
            self.assertIn("room-log.md", payload["artifacts"])
            self.assertIn("informal free-chat record", payload["artifacts"]["room-log.md"])
            self.assertEqual(payload["artifacts"].get("decision.md"), "")
            self.assertEqual(payload["artifacts"].get("transcript.md"), "")

    def test_build_meeting_payload_preserves_codex_live_session_binding_metadata(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            meeting_dir = Path(temp_dir) / "meetings" / "m1"
            meeting_dir.mkdir(parents=True)
            (meeting_dir / "meeting.json").write_text(
                json.dumps(
                    {
                        "meeting_id": "m1",
                        "topic": "테스트",
                        "question": "기존 세션을 이어받나?",
                        "roles": [
                            {
                                "id": "lore_lawyer",
                                "display_name": "설정충",
                                "lens": "Canon Analyst",
                                "research_focus": "canon",
                            }
                        ],
                        "provider_configs": {
                            "codex-live": {
                                "id": "codex-live",
                                "kind": "codex_live_session",
                                "display_name": "Codex CLI Live Session",
                            }
                        },
                        "permission_profiles": {
                            "codex_live_meeting_readonly": {"id": "codex_live_meeting_readonly"}
                        },
                        "agent_bindings": [
                            {
                                "agent_id": "codex-live-lore-lawyer",
                                "role_id": "lore_lawyer",
                                "owner_id": "host",
                                "provider_id": "codex-live",
                                "model_id": "local-codex-session",
                                "permission_profile_id": "codex_live_meeting_readonly",
                                "join_mode": "current_session",
                                "session_id": "019e3038-39cc-76a2-a746-5ba8c0f3b408",
                            }
                        ],
                        "debate_rounds": [],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            payload = build_meeting_payload(meeting_dir)

            binding = payload["meeting"]["agent_bindings"][0]
            self.assertEqual(binding["join_mode"], "current_session")
            self.assertEqual(binding["session_id"], "019e3038-39cc-76a2-a746-5ba8c0f3b408")
            self.assertEqual(payload["meeting"]["provider_configs"]["codex-live"]["kind"], "codex_live_session")

    def test_codex_sessions_payload_reads_recent_codex_sessions(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            codex_home = Path(temp_dir) / "codex-home"
            codex_home.mkdir()
            (codex_home / "session_index.jsonl").write_text(
                "\n".join(
                    [
                        json.dumps(
                            {
                                "id": "old",
                                "thread_name": "Old thread",
                                "updated_at": "2026-05-15T00:00:00Z",
                            }
                        ),
                        json.dumps(
                            {
                                "id": "new",
                                "thread_name": "New thread",
                                "updated_at": "2026-05-17T00:00:00Z",
                            }
                        ),
                    ]
                ),
                encoding="utf-8",
            )

            with patch.dict(os.environ, {"CODEX_HOME": str(codex_home)}):
                payload = codex_sessions_payload(limit=1)

            self.assertEqual(payload["sessions"], [{"id": "new", "thread_name": "New thread", "updated_at": "2026-05-17T00:00:00Z"}])

    def test_codex_session_invite_payload_writes_config_for_meeting_roles(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            meeting_dir = root / "meetings" / "m1"
            meeting_dir.mkdir(parents=True)
            (meeting_dir / "meeting.json").write_text(
                json.dumps(
                    {
                        "meeting_id": "m1",
                        "roles": [
                            {"id": "lore_lawyer", "display_name": "설정충"},
                            {"id": "fanboard_skeptic", "display_name": "회의론자"},
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            payload = codex_session_invite_payload(
                root,
                session_id="019e02af-c287-7cd1-aab7-c1e059c5ed44",
                role_id="lore_lawyer",
                meeting_id="m1",
            )

            config_path = root / "codex-live-session.local.json"
            config = json.loads(config_path.read_text(encoding="utf-8"))
            bindings = {binding["role_id"]: binding for binding in config["agent_bindings"]}
            self.assertEqual(payload["config_path"], str(config_path))
            self.assertEqual(payload["binding"]["role_id"], "lore_lawyer")
            self.assertEqual(payload["binding"]["join_mode"], "current_session")
            self.assertEqual(payload["binding"]["session_id"], "019e02af-c287-7cd1-aab7-c1e059c5ed44")
            self.assertEqual(bindings["lore_lawyer"]["session_id"], "019e02af-c287-7cd1-aab7-c1e059c5ed44")
            self.assertEqual(bindings["fanboard_skeptic"]["join_mode"], "fresh")

    def test_codex_session_invite_http_endpoint_writes_config(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            meeting_dir = root / "meetings" / "m1"
            meeting_dir.mkdir(parents=True)
            (meeting_dir / "meeting.json").write_text(
                json.dumps(
                    {
                        "meeting_id": "m1",
                        "roles": [{"id": "lore_lawyer", "display_name": "설정충"}],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/codex-sessions/invite",
                    data=json.dumps(
                        {
                            "meeting_id": "m1",
                            "role_id": "lore_lawyer",
                            "session_id": "019e02af-c287-7cd1-aab7-c1e059c5ed44",
                        }
                    ).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urlopen(request, timeout=4) as response:
                    payload = json.loads(response.read().decode("utf-8"))
                with urlopen(
                    f"http://127.0.0.1:{server.server_port}/api/live-agent-operations",
                    timeout=4,
                ) as response:
                    operations = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

            self.assertEqual(payload["binding"]["role_id"], "lore_lawyer")
            self.assertTrue((root / "codex-live-session.local.json").exists())

    def test_live_agent_process_endpoints_start_list_and_stop_group(self):
        class FakeSupervisor:
            def __init__(self):
                self.groups = []
                self.started = []
                self.stopped = []
                self.restarted = []

            def list_groups(self):
                return list(self.groups)

            def start_group(
                self,
                *,
                config_path,
                server,
                group_id=None,
                auto_restart=False,
                max_restarts=0,
                restart_backoff_seconds=5.0,
            ):
                self.started.append(
                    {
                        "config_path": config_path,
                        "server": server,
                        "group_id": group_id,
                        "auto_restart": auto_restart,
                        "max_restarts": max_restarts,
                        "restart_backoff_seconds": restart_backoff_seconds,
                    }
                )
                record = {
                    "group_id": group_id or "default",
                    "status": "running",
                    "pid": 4321,
                    "config_path": str(config_path),
                    "server": server,
                    "log_path": "live-agent-runs/default.log",
                    "started_at": "2026-05-17T12:00:00+00:00",
                    "stopped_at": "",
                    "returncode": None,
                    "last_error": "",
                    "log_tail": "resident booted",
                    "auto_restart": auto_restart,
                    "restart_count": 0,
                    "max_restarts": max_restarts,
                    "restart_backoff_seconds": restart_backoff_seconds,
                    "next_restart_at": "",
                    "agents": [
                        {
                            "agent_id": "local-a",
                            "display_name": "Local A",
                            "provider_kind": "local_cli",
                            "connection_kind": "local_cli",
                        }
                    ],
                    "recent_events": [
                        {
                            "event_type": "started",
                            "timestamp": "2026-05-17T12:00:00+00:00",
                            "group_id": group_id or "default",
                            "status": "running",
                            "pid": 4321,
                        }
                    ],
                }
                self.groups = [record]
                return record

            def stop_group(self, group_id):
                self.stopped.append(group_id)
                record = dict(self.groups[0])
                record["status"] = "stopped"
                record["returncode"] = 0
                self.groups = [record]
                return record

            def restart_group(self, group_id):
                self.restarted.append(group_id)
                record = dict(self.groups[0])
                record["status"] = "running"
                record["pid"] = 9876
                record["returncode"] = None
                self.groups = [record]
                return record

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = root / "live-agents.json"
            config_path.write_text('{"agents": [{"agent_id": "a", "command": ["fake"]}]}', encoding="utf-8")
            supervisor = FakeSupervisor()
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root, process_supervisor=supervisor))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                start_request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/live-agent-processes/start",
                    data=json.dumps(
                        {
                            "config_path": str(config_path),
                            "group_id": "crew",
                            "auto_restart": True,
                            "max_restarts": 2,
                            "restart_backoff_seconds": 1.5,
                        }
                    ).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urlopen(start_request, timeout=4) as response:
                    started = json.loads(response.read().decode("utf-8"))
                with urlopen(f"http://127.0.0.1:{server.server_port}/api/live-agent-processes", timeout=4) as response:
                    listed = json.loads(response.read().decode("utf-8"))
                stop_request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/live-agent-processes/crew/stop",
                    data=b"{}",
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urlopen(stop_request, timeout=4) as response:
                    stopped = json.loads(response.read().decode("utf-8"))
                restart_request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/live-agent-processes/crew/restart",
                    data=b"{}",
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urlopen(restart_request, timeout=4) as response:
                    restarted = json.loads(response.read().decode("utf-8"))
                with urlopen(
                    f"http://127.0.0.1:{server.server_port}/api/live-agent-operations?limit=10",
                    timeout=4,
                ) as response:
                    operations = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

            self.assertEqual(started["group"]["status"], "running")
            self.assertEqual(started["group"]["agents"][0]["agent_id"], "local-a")
            self.assertEqual(started["group"]["recent_events"][0]["event_type"], "started")
            self.assertEqual(listed["groups"][0]["group_id"], "crew")
            self.assertEqual(listed["groups"][0]["agents"][0]["connection_kind"], "local_cli")
            self.assertEqual(listed["groups"][0]["recent_events"][0]["status"], "running")
            self.assertEqual(listed["groups"][0]["log_tail"], "resident booted")
            self.assertEqual(stopped["group"]["status"], "stopped")
            self.assertEqual(restarted["group"]["status"], "running")
            self.assertEqual(restarted["group"]["pid"], 9876)
            self.assertEqual(restarted["group"]["agents"][0]["display_name"], "Local A")
            self.assertEqual(supervisor.started[0]["server"], f"http://127.0.0.1:{server.server_port}")
            self.assertEqual(supervisor.started[0]["auto_restart"], True)
            self.assertEqual(supervisor.started[0]["max_restarts"], 2)
            self.assertEqual(supervisor.started[0]["restart_backoff_seconds"], 1.5)
            self.assertEqual(supervisor.stopped, ["crew"])
            self.assertEqual(supervisor.restarted, ["crew"])
            self.assertEqual(
                [(operation["operation"], operation["status"], operation["target_id"]) for operation in operations["operations"]],
                [
                    ("process.start", "success", "crew"),
                    ("process.stop", "success", "crew"),
                    ("process.restart", "success", "crew"),
                ],
            )
            operation_text = json.dumps(operations, ensure_ascii=False)
            self.assertNotIn(str(config_path), operation_text)
            self.assertNotIn(f"http://127.0.0.1:{server.server_port}", operation_text)

    def test_live_agent_process_start_sanitizes_non_finite_backoff(self):
        class FakeSupervisor:
            def __init__(self):
                self.started = []

            def start_group(
                self,
                *,
                config_path,
                server,
                group_id=None,
                auto_restart=False,
                max_restarts=0,
                restart_backoff_seconds=5.0,
            ):
                self.started.append(restart_backoff_seconds)
                return {
                    "group_id": group_id or "default",
                    "status": "running",
                    "pid": 1234,
                    "config_path": str(config_path),
                    "server": server,
                    "restart_backoff_seconds": restart_backoff_seconds,
                }

            def list_groups(self):
                return []

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = root / "live-agents.json"
            config_path.write_text('{"agents": [{"agent_id": "a", "command": ["fake"]}]}', encoding="utf-8")
            supervisor = FakeSupervisor()
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root, process_supervisor=supervisor))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/live-agent-processes/start",
                    data=json.dumps(
                        {
                            "config_path": str(config_path),
                            "group_id": "crew",
                            "auto_restart": True,
                            "max_restarts": 1,
                            "restart_backoff_seconds": float("inf"),
                        }
                    ).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urlopen(request, timeout=4) as response:
                    payload = json.loads(response.read().decode("utf-8"))
                with urlopen(
                    f"http://127.0.0.1:{server.server_port}/api/live-agent-operations",
                    timeout=4,
                ) as response:
                    operations = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

            self.assertEqual(payload["group"]["restart_backoff_seconds"], 5.0)
            self.assertEqual(supervisor.started, [5.0])

    def test_live_agent_process_start_sanitizes_non_finite_restart_count(self):
        class FakeSupervisor:
            def __init__(self):
                self.started = []

            def start_group(
                self,
                *,
                config_path,
                server,
                group_id=None,
                auto_restart=False,
                max_restarts=0,
                restart_backoff_seconds=5.0,
            ):
                self.started.append(max_restarts)
                return {
                    "group_id": group_id or "default",
                    "status": "running",
                    "pid": 1234,
                    "config_path": str(config_path),
                    "server": server,
                    "max_restarts": max_restarts,
                }

            def list_groups(self):
                return []

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = root / "live-agents.json"
            config_path.write_text('{"agents": [{"agent_id": "a", "command": ["fake"]}]}', encoding="utf-8")
            supervisor = FakeSupervisor()
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root, process_supervisor=supervisor))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/live-agent-processes/start",
                    data=json.dumps(
                        {
                            "config_path": str(config_path),
                            "group_id": "crew",
                            "auto_restart": True,
                            "max_restarts": float("inf"),
                        }
                    ).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urlopen(request, timeout=4) as response:
                    payload = json.loads(response.read().decode("utf-8"))
                operations_url = f"http://127.0.0.1:{server.server_port}/api/live-agent-operations"
                with urlopen(operations_url, timeout=4) as response:
                    operations = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

            self.assertEqual(payload["group"]["max_restarts"], 0)
            self.assertEqual(supervisor.started, [0])

    def test_live_agent_process_start_returns_400_when_preflight_fails_without_launch(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = root / "live-agents.json"
            config_path.write_text(
                '{"agents": [{"agent_id": "bad-agent", "command": ["definitely-missing-agentsassemble-cli"]}]}',
                encoding="utf-8",
            )

            def command_factory(command, **kwargs):
                raise AssertionError("preflight failure must not launch a process")

            supervisor = LiveAgentProcessSupervisor(root, command_factory=command_factory)
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root, process_supervisor=supervisor))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/live-agent-processes/start",
                    data=json.dumps({"config_path": str(config_path), "group_id": "crew"}).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                try:
                    urlopen(request, timeout=4)
                except HTTPError as error:
                    error_code = error.code
                    body = error.read().decode("utf-8")
                    error.close()
                else:
                    self.fail("preflight failure should return HTTP 400")
                with urlopen(
                    f"http://127.0.0.1:{server.server_port}/api/live-agent-operations",
                    timeout=4,
                ) as response:
                    operations = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()
                supervisor.close()

            self.assertEqual(error_code, 400)
            self.assertIn("Live agent preflight failed", body)
            self.assertIn("bad-agent command", body)
            self.assertFalse((root / "live-agent-runs" / "crew.log").exists())
            self.assertFalse((root / "live-agent-runs" / "processes.json").exists())
            self.assertEqual(operations["operations"][0]["operation"], "process.start")
            self.assertEqual(operations["operations"][0]["status"], "failed")
            self.assertEqual(operations["operations"][0]["target_id"], "crew")
            self.assertIn("Live agent preflight failed", operations["operations"][0]["error"])
            self.assertNotIn("bad-agent command", operations["operations"][0]["error"])
            self.assertNotIn("definitely-missing-agentsassemble-cli", json.dumps(operations, ensure_ascii=False))

    def test_live_agent_process_start_records_invalid_json_operation(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/live-agent-processes/start",
                    data=b"{not json",
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                try:
                    urlopen(request, timeout=4)
                except HTTPError as error:
                    error_code = error.code
                    error.read()
                    error.close()
                else:
                    self.fail("invalid JSON should return HTTP 400")
                with urlopen(
                    f"http://127.0.0.1:{server.server_port}/api/live-agent-operations",
                    timeout=4,
                ) as response:
                    operations = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

            self.assertEqual(error_code, 400)
            self.assertEqual(operations["operations"][0]["operation"], "process.start")
            self.assertEqual(operations["operations"][0]["status"], "failed")
            self.assertEqual(operations["operations"][0]["error"], "Invalid JSON")

    def test_live_agent_process_start_records_invalid_utf8_operation(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/live-agent-processes/start",
                    data=b"\xff",
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                try:
                    urlopen(request, timeout=4)
                except HTTPError as error:
                    error_code = error.code
                    error.read()
                    error.close()
                else:
                    self.fail("invalid UTF-8 should return HTTP 400")
                with urlopen(
                    f"http://127.0.0.1:{server.server_port}/api/live-agent-operations",
                    timeout=4,
                ) as response:
                    operations = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

            self.assertEqual(error_code, 400)
            self.assertEqual(operations["operations"][0]["operation"], "process.start")
            self.assertEqual(operations["operations"][0]["status"], "failed")
            self.assertEqual(operations["operations"][0]["error"], "Invalid JSON")

    def test_live_agent_process_restart_returns_400_when_preflight_fails_without_launch(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            runs_dir = root / "live-agent-runs"
            runs_dir.mkdir()
            config_path = root / "live-agents.json"
            config_path.write_text(
                '{"agents": [{"agent_id": "bad-agent", "command": ["definitely-missing-agentsassemble-cli"]}]}',
                encoding="utf-8",
            )
            (runs_dir / "processes.json").write_text(
                json.dumps(
                    {
                        "groups": [
                            {
                                "group_id": "crew",
                                "status": "stopped",
                                "pid": None,
                                "config_path": str(config_path),
                                "server": "http://room.local",
                                "log_path": str(runs_dir / "crew.log"),
                                "started_at": "2026-05-17T12:00:00+00:00",
                                "stopped_at": "2026-05-17T12:01:00+00:00",
                                "returncode": 0,
                                "last_error": "",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )

            def command_factory(command, **kwargs):
                raise AssertionError("preflight failure must not launch a process")

            supervisor = LiveAgentProcessSupervisor(root, command_factory=command_factory)
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root, process_supervisor=supervisor))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/live-agent-processes/crew/restart",
                    data=b"{}",
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                try:
                    urlopen(request, timeout=4)
                except HTTPError as error:
                    error_code = error.code
                    body = error.read().decode("utf-8")
                    error.close()
                else:
                    self.fail("preflight failure should return HTTP 400")
                with urlopen(
                    f"http://127.0.0.1:{server.server_port}/api/live-agent-operations",
                    timeout=4,
                ) as response:
                    operations = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()
                supervisor.close()

            persisted = json.loads((runs_dir / "processes.json").read_text(encoding="utf-8"))

            self.assertEqual(error_code, 400)
            self.assertIn("Live agent preflight failed", body)
            self.assertIn("bad-agent command", body)
            self.assertFalse((runs_dir / "crew.log").exists())
            self.assertEqual(persisted["groups"][0]["status"], "stopped")
            self.assertEqual(persisted["groups"][0]["pid"], None)
            self.assertEqual(operations["operations"][0]["operation"], "process.restart")
            self.assertEqual(operations["operations"][0]["status"], "failed")
            self.assertEqual(operations["operations"][0]["target_id"], "crew")
            self.assertIn("Live agent preflight failed", operations["operations"][0]["error"])

    def test_live_agent_smoke_endpoint_runs_credential_free_group(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "room"
            root.mkdir()
            old_event = append_lobby_event(root, {"name": "나", "side": "mine", "message": "old chatter"})
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/live-agent-smoke",
                    data=json.dumps({"group_id": "gui-smoke", "timeout": 8}).encode("utf-8"),
                    headers={"Content-Type": "application/json", "Host": "127.0.0.1:1"},
                    method="POST",
                )
                with urlopen(request, timeout=12) as response:
                    payload = json.loads(response.read().decode("utf-8"))
                with urlopen(
                    f"http://127.0.0.1:{server.server_port}/api/live-agent-operations?limit=10",
                    timeout=4,
                ) as response:
                    operations = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

            self.assertEqual(payload["status"], "ok")
            self.assertEqual(payload["group_id"], "gui-smoke")
            self.assertNotEqual(payload["source_event_id"], old_event["id"])
            self.assertEqual({reply["source_event_id"] for reply in payload["replies"]}, {payload["source_event_id"]})
            self.assertEqual(
                {reply["message"] for reply in payload["replies"]},
                {"smoke local_cli ok", "smoke live_session ok"},
            )
            processes = json.loads((root / "live-agent-runs" / "processes.json").read_text(encoding="utf-8"))
            group = next(item for item in processes["groups"] if item["group_id"] == "gui-smoke")
            self.assertEqual(group["status"], "stopped")
            self.assertIn(("smoke.run", "success", "gui-smoke"), [
                (operation["operation"], operation["status"], operation["target_id"])
                for operation in operations["operations"]
            ])

    def test_live_agent_readiness_endpoint_uses_pre_smoke_health_and_runs_smoke(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "room"
            root.mkdir()
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                payloads = []
                for _ in range(2):
                    request = Request(
                        f"http://127.0.0.1:{server.server_port}/api/live-agent-readiness",
                        data=json.dumps({"group_id": "doctor-smoke", "timeout": 8}).encode("utf-8"),
                        headers={"Content-Type": "application/json", "Host": "127.0.0.1:1"},
                        method="POST",
                    )
                    with urlopen(request, timeout=12) as response:
                        payloads.append(json.loads(response.read().decode("utf-8")))
                with urlopen(f"http://127.0.0.1:{server.server_port}/api/live-agent-health", timeout=4) as response:
                    health_after_smoke = json.loads(response.read().decode("utf-8"))
                with urlopen(
                    f"http://127.0.0.1:{server.server_port}/api/live-agent-operations?limit=20",
                    timeout=4,
                ) as response:
                    operations = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

            for payload in payloads:
                self.assertEqual(payload["status"], "ready")
                self.assertEqual(payload["health"]["status"], "ok")
                self.assertEqual(payload["smoke"]["status"], "ok")
                self.assertEqual(payload["smoke"]["group_id"], "doctor-smoke")
                self.assertEqual(
                    {check["id"]: check["status"] for check in payload["checks"]},
                    {"health": "ok", "smoke": "ok"},
                )
            self.assertEqual(health_after_smoke["status"], "ok")
            processes = json.loads((root / "live-agent-runs" / "processes.json").read_text(encoding="utf-8"))
            group = next(item for item in processes["groups"] if item["group_id"] == "doctor-smoke")
            self.assertEqual(group["status"], "stopped")
            self.assertTrue(group["diagnostic"])
            agents = json.loads((root / "live_agents.json").read_text(encoding="utf-8"))["agents"]
            self.assertEqual({agent["diagnostic"] for agent in agents if agent["agent_id"].startswith("doctor-smoke-")}, {True})
            readiness_operations = [
                operation for operation in operations["operations"] if operation["operation"] == "readiness.check"
            ]
            self.assertEqual([operation["status"] for operation in readiness_operations], ["success", "success"])
            self.assertEqual({operation["target_id"] for operation in readiness_operations}, {"doctor-smoke"})

    def test_live_agent_readiness_endpoint_records_degraded_operation(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "room"
            root.mkdir()
            (root / "live_agents.json").write_text(
                json.dumps({"agents": [{"agent_id": "offline-agent", "status": "offline"}]}),
                encoding="utf-8",
            )
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/live-agent-readiness",
                    data=json.dumps({"group_id": "doctor-smoke", "timeout": 8}).encode("utf-8"),
                    headers={"Content-Type": "application/json", "Host": "127.0.0.1:1"},
                    method="POST",
                )
                with urlopen(request, timeout=12) as response:
                    payload = json.loads(response.read().decode("utf-8"))
                with urlopen(
                    f"http://127.0.0.1:{server.server_port}/api/live-agent-operations?limit=20",
                    timeout=4,
                ) as response:
                    operations = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

            self.assertEqual(payload["status"], "degraded")
            readiness_operations = [
                operation for operation in operations["operations"] if operation["operation"] == "readiness.check"
            ]
            self.assertEqual(readiness_operations[-1]["status"], "degraded")
            self.assertEqual(readiness_operations[-1]["details"]["result_status"], "degraded")

    def test_live_agent_preflight_endpoint_checks_config_without_starting_processes(self):
        class FakeSupervisor:
            def __init__(self):
                self.started = False

            def start_group(self, **kwargs):
                self.started = True
                raise AssertionError("preflight must not start process groups")

            def snapshot_groups(self):
                return []

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "room"
            root.mkdir()
            config_path = root / "live-agents.json"
            config_path.write_text(
                json.dumps(
                    {
                        "agents": [
                            {
                                "agent_id": "preflight-agent",
                                "provider_kind": "local_cli",
                                "connection_kind": "local_cli",
                                "command": [sys.executable, "-c", "print('not executed')"],
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            supervisor = FakeSupervisor()
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root, process_supervisor=supervisor))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/live-agent-preflight",
                    data=json.dumps({"config_path": str(config_path)}).encode("utf-8"),
                    headers={"Content-Type": "application/json", "Host": "127.0.0.1:1"},
                    method="POST",
                )
                with urlopen(request, timeout=4) as response:
                    payload = json.loads(response.read().decode("utf-8"))
                with urlopen(
                    f"http://127.0.0.1:{server.server_port}/api/live-agent-operations",
                    timeout=4,
                ) as response:
                    operations = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

            self.assertFalse(supervisor.started)
            self.assertEqual(payload["status"], "ok")
            self.assertEqual(payload["server"], "http://127.0.0.1:1")
            self.assertEqual(payload["summary"], {"agents": 1, "failed_agents": 0, "checks_failed": 0})
            self.assertEqual(payload["agents"][0]["agent_id"], "preflight-agent")
            self.assertEqual(operations["operations"][0]["operation"], "preflight.check")
            self.assertEqual(operations["operations"][0]["status"], "success")
            self.assertEqual(operations["operations"][0]["details"]["result_status"], "ok")

    def test_live_agent_health_endpoint_summarizes_agents_and_processes(self):
        class FakeSupervisor:
            def __init__(self):
                self.list_called = False

            def list_groups(self):
                self.list_called = True
                raise AssertionError("health endpoint must not refresh process groups")

            def snapshot_groups(self):
                return [
                    {"group_id": "running-group", "status": "running"},
                    {"group_id": "restart-group", "status": "restarting"},
                    {"group_id": "crashed-group", "status": "error"},
                    {"group_id": "orphan-group", "status": "unknown"},
                    {"group_id": "stopped-group", "status": "stopped"},
                    {"group_id": "diagnostic-stopped", "status": "stopped", "diagnostic": True},
                    {"group_id": "diagnostic-error", "status": "error", "diagnostic": True},
                    {
                        "group_id": "legacy-smoke",
                        "status": "stopped",
                        "returncode": 0,
                        "config_path": "/dev/null/agentsassemble-missing-smoke/live-agents.json",
                    },
                    {"status": "error"},
                    {"group_id": "odd-group", "status": "mystery"},
                ]

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "live_agents.json").write_text(
                json.dumps(
                    {
                        "agents": [
                            {"agent_id": "online-agent", "status": "online"},
                            {"agent_id": "working-agent", "status": "working"},
                            {"agent_id": "error-agent", "status": "error"},
                            {"agent_id": "offline-agent", "status": "offline"},
                            {"agent_id": "diagnostic-offline", "status": "offline", "diagnostic": True},
                            {"agent_id": "diagnostic-error", "status": "error", "diagnostic": True},
                            {
                                "agent_id": "legacy-smoke-local-cli",
                                "display_name": "Smoke Local CLI",
                                "provider_kind": "local_cli",
                                "connection_kind": "local_cli",
                                "status": "offline",
                            },
                            {
                                "agent_id": "legacy-smoke-live-session",
                                "display_name": "Smoke Live Session",
                                "provider_kind": "local_cli",
                                "connection_kind": "live_session",
                                "status": "error",
                            },
                            {"display_name": "Display Only", "status": "error"},
                            {"agent_id": "odd-agent", "status": "mystery"},
                        ]
                    }
                ),
                encoding="utf-8",
            )
            supervisor = FakeSupervisor()
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root, process_supervisor=supervisor))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with urlopen(f"http://127.0.0.1:{server.server_port}/api/live-agent-health", timeout=4) as response:
                    payload = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

            self.assertEqual(payload["status"], "degraded")
            self.assertEqual(payload["agents"]["counts"]["online"], 1)
            self.assertEqual(payload["agents"]["counts"]["working"], 1)
            self.assertEqual(payload["agents"]["counts"]["error"], 2)
            self.assertEqual(payload["agents"]["counts"]["offline"], 2)
            self.assertEqual(payload["agents"]["live"], 2)
            self.assertEqual(payload["agents"]["total"], 6)
            self.assertEqual(
                payload["agents"]["attention"],
                ["error-agent", "offline-agent", "missing-agent-id-5", "odd-agent"],
            )
            self.assertEqual(payload["processes"]["counts"]["running"], 1)
            self.assertEqual(payload["processes"]["counts"]["restarting"], 1)
            self.assertEqual(payload["processes"]["counts"]["error"], 2)
            self.assertEqual(payload["processes"]["counts"]["unknown"], 2)
            self.assertEqual(payload["processes"]["counts"]["stopped"], 1)
            self.assertEqual(payload["processes"]["total"], 7)
            self.assertEqual(
                payload["processes"]["attention"],
                [
                    "restart-group",
                    "crashed-group",
                    "orphan-group",
                    "stopped-group",
                    "missing-process-group-id-6",
                    "odd-group",
                ],
            )
            self.assertFalse(supervisor.list_called)

    def test_live_agent_health_keeps_reused_legacy_smoke_group_visible(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            active_config = root / "active-live-agents.json"
            active_config.write_text('{"agents": []}', encoding="utf-8")
            (root / "live_agents.json").write_text(
                json.dumps(
                    {
                        "agents": [
                            {
                                "agent_id": "legacy-smoke-local-cli",
                                "display_name": "Smoke Local CLI",
                                "provider_kind": "local_cli",
                                "connection_kind": "local_cli",
                                "status": "offline",
                            },
                            {
                                "agent_id": "legacy-smoke-live-session",
                                "display_name": "Smoke Live Session",
                                "provider_kind": "local_cli",
                                "connection_kind": "live_session",
                                "status": "offline",
                            },
                            {
                                "agent_id": "partial-smoke-local-cli",
                                "display_name": "Smoke Local CLI",
                                "provider_kind": "local_cli",
                                "connection_kind": "local_cli",
                                "status": "offline",
                            },
                        ]
                    }
                ),
                encoding="utf-8",
            )

            class FakeSupervisor:
                def snapshot_groups(self):
                    return [
                        {
                            "group_id": "legacy-smoke",
                            "status": "error",
                            "returncode": 1,
                            "config_path": str(active_config),
                        },
                        {
                            "group_id": "partial-smoke",
                            "status": "stopped",
                            "returncode": 0,
                            "config_path": "/dev/null/partial-smoke/live-agents.json",
                        }
                    ]

            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root, process_supervisor=FakeSupervisor()))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with urlopen(f"http://127.0.0.1:{server.server_port}/api/live-agent-health", timeout=4) as response:
                    payload = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

            self.assertEqual(payload["agents"]["total"], 0)
            self.assertEqual(payload["processes"]["total"], 2)
            self.assertEqual(payload["processes"]["counts"]["error"], 1)
            self.assertEqual(payload["processes"]["counts"]["stopped"], 1)
            self.assertEqual(payload["processes"]["attention"], ["legacy-smoke", "partial-smoke"])

    def test_serve_gui_closes_live_agent_process_supervisor(self):
        class FakeServer:
            def __init__(self, address, handler):
                self.address = address
                self.handler = handler
                self.server_address = (address[0], 43210 if address[1] == 0 else address[1])
                self.closed = False

            def serve_forever(self):
                raise KeyboardInterrupt

            def server_close(self):
                self.closed = True

        class FakeSupervisor:
            instances = []

            def __init__(self, output_root):
                self.output_root = output_root
                self.closed = False
                self.monitor_started = False
                self.instances.append(self)

            def start_monitor(self):
                self.monitor_started = True

            def close(self):
                self.closed = True

        with tempfile.TemporaryDirectory() as temp_dir:
            servers = []

            def server_factory(address, handler):
                server = FakeServer(address, handler)
                servers.append(server)
                return server

            with patch("agentsassemble.gui.LiveAgentProcessSupervisor", FakeSupervisor):
                with patch("agentsassemble.gui.ThreadingHTTPServer", server_factory):
                    with patch("sys.stdout", StringIO()):
                        serve_gui(host="127.0.0.1", port=0, output_root=Path(temp_dir))

        self.assertTrue(servers[0].closed)
        self.assertTrue(FakeSupervisor.instances[0].monitor_started)
        self.assertTrue(FakeSupervisor.instances[0].closed)

    def test_serve_gui_does_not_autostart_without_explicit_config(self):
        class FakeServer:
            def __init__(self, address, handler):
                self.server_address = (address[0], 43210 if address[1] == 0 else address[1])
                self.closed = False

            def serve_forever(self):
                raise KeyboardInterrupt

            def server_close(self):
                self.closed = True

        class FakeSupervisor:
            instances = []

            def __init__(self, output_root):
                self.started = []
                self.closed = False
                self.instances.append(self)

            def start_monitor(self):
                return None

            def start_group(self, **kwargs):
                self.started.append(kwargs)
                return {"group_id": kwargs.get("group_id") or "group", "status": "running"}

            def close(self):
                self.closed = True

        with tempfile.TemporaryDirectory() as temp_dir:
            servers = []

            def server_factory(address, handler):
                server = FakeServer(address, handler)
                servers.append(server)
                return server

            with patch("agentsassemble.gui.LiveAgentProcessSupervisor", FakeSupervisor):
                with patch("agentsassemble.gui.ThreadingHTTPServer", server_factory):
                    with patch("sys.stdout", StringIO()):
                        serve_gui(host="127.0.0.1", port=0, output_root=Path(temp_dir))

        self.assertEqual(FakeSupervisor.instances[0].started, [])
        self.assertTrue(servers[0].closed)
        self.assertTrue(FakeSupervisor.instances[0].closed)

    def test_serve_gui_autostarts_explicit_live_agent_config_after_server_bind(self):
        class FakeServer:
            def __init__(self, address, handler):
                self.server_address = (address[0], 45678 if address[1] == 0 else address[1])
                self.closed = False

            def serve_forever(self):
                raise KeyboardInterrupt

            def server_close(self):
                self.closed = True

        class FakeSupervisor:
            instances = []

            def __init__(self, output_root):
                self.output_root = output_root
                self.started = []
                self.closed = False
                self.instances.append(self)

            def start_monitor(self):
                return None

            def start_group(self, **kwargs):
                self.started.append(kwargs)
                return {"group_id": kwargs.get("group_id") or "group", "status": "running"}

            def close(self):
                self.closed = True

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = root / "live-agents.json"
            config_path.write_text('{"agents": []}', encoding="utf-8")
            servers = []

            def server_factory(address, handler):
                server = FakeServer(address, handler)
                servers.append(server)
                return server

            with patch("agentsassemble.gui.LiveAgentProcessSupervisor", FakeSupervisor):
                with patch("agentsassemble.gui.ThreadingHTTPServer", server_factory):
                    with patch("sys.stdout", StringIO()):
                        serve_gui(
                            host="127.0.0.1",
                            port=0,
                            output_root=root,
                            live_agent_config=config_path,
                            live_agent_group_id="boot",
                            live_agent_auto_restart=True,
                            live_agent_max_restarts=3,
                            live_agent_restart_backoff_seconds=1.5,
                        )

            operations = json.loads((root / "live-agent-runs" / "operations.jsonl").read_text(encoding="utf-8").splitlines()[-1])

        self.assertEqual(len(FakeSupervisor.instances[0].started), 1)
        started = FakeSupervisor.instances[0].started[0]
        self.assertEqual(started["config_path"], config_path)
        self.assertEqual(started["server"], "http://127.0.0.1:45678")
        self.assertEqual(started["group_id"], "boot")
        self.assertTrue(started["auto_restart"])
        self.assertEqual(started["max_restarts"], 3)
        self.assertEqual(started["restart_backoff_seconds"], 1.5)
        self.assertEqual(operations["operation"], "process.autostart")
        self.assertEqual(operations["status"], "success")
        self.assertEqual(operations["target_id"], "boot")
        self.assertTrue(servers[0].closed)
        self.assertTrue(FakeSupervisor.instances[0].closed)

    def test_serve_gui_records_failed_autostart_and_still_serves(self):
        class FakeServer:
            def __init__(self, address, handler):
                self.server_address = (address[0], 45679 if address[1] == 0 else address[1])
                self.served = False
                self.closed = False

            def serve_forever(self):
                self.served = True
                raise KeyboardInterrupt

            def server_close(self):
                self.closed = True

        class FakeSupervisor:
            instances = []

            def __init__(self, output_root):
                self.closed = False
                self.instances.append(self)

            def start_monitor(self):
                return None

            def start_group(self, **kwargs):
                raise ValueError("Live agent config /Users/me/private/live-agents.json was not found.")

            def close(self):
                self.closed = True

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = root / "missing-live-agents.json"
            servers = []

            def server_factory(address, handler):
                server = FakeServer(address, handler)
                servers.append(server)
                return server

            with patch("agentsassemble.gui.LiveAgentProcessSupervisor", FakeSupervisor):
                with patch("agentsassemble.gui.ThreadingHTTPServer", server_factory):
                    with patch("sys.stdout", StringIO()):
                        serve_gui(
                            host="127.0.0.1",
                            port=0,
                            output_root=root,
                            live_agent_config=config_path,
                            live_agent_group_id="boot",
                        )

            operations = json.loads((root / "live-agent-runs" / "operations.jsonl").read_text(encoding="utf-8").splitlines()[-1])
            persisted = json.dumps(operations, ensure_ascii=False)

        self.assertTrue(servers[0].served)
        self.assertTrue(servers[0].closed)
        self.assertTrue(FakeSupervisor.instances[0].closed)
        self.assertEqual(operations["operation"], "process.autostart")
        self.assertEqual(operations["status"], "failed")
        self.assertEqual(operations["target_id"], "boot")
        self.assertIn("Live agent config", operations["error"])
        self.assertNotIn("/Users/me/private", persisted)

    def test_serve_gui_cleans_up_when_monitor_start_fails(self):
        class FakeServer:
            def __init__(self, address, handler):
                self.closed = False

            def serve_forever(self):
                raise AssertionError("serve_forever should not run after monitor startup failure")

            def server_close(self):
                self.closed = True

        class FakeSupervisor:
            instances = []

            def __init__(self, output_root):
                self.closed = False
                self.instances.append(self)

            def start_monitor(self):
                raise RuntimeError("monitor failed")

            def close(self):
                self.closed = True

        with tempfile.TemporaryDirectory() as temp_dir:
            servers = []

            def server_factory(address, handler):
                server = FakeServer(address, handler)
                servers.append(server)
                return server

            with patch("agentsassemble.gui.LiveAgentProcessSupervisor", FakeSupervisor):
                with patch("agentsassemble.gui.ThreadingHTTPServer", server_factory):
                    with patch("sys.stdout", StringIO()):
                        with self.assertRaises(RuntimeError):
                            serve_gui(host="127.0.0.1", port=0, output_root=Path(temp_dir))

        self.assertTrue(servers[0].closed)
        self.assertTrue(FakeSupervisor.instances[0].closed)

    def test_live_agent_payload_registers_non_codex_presence(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)

            payload = connect_live_agent_payload(
                root,
                {
                    "agent_id": "claude-code-live",
                    "display_name": "Claude Code Live",
                    "provider_kind": "claude_code",
                    "connection_kind": "local_cli",
                    "engagement_mode": "mentioned",
                    "meeting_id": "m1",
                },
            )

            self.assertEqual(payload["agent"]["agent_id"], "claude-code-live")
            self.assertEqual(payload["agent"]["provider_kind"], "claude_code")
            self.assertEqual(payload["agent"]["connection_kind"], "local_cli")
            self.assertEqual(live_agents_payload(root)["agents"][0]["display_name"], "Claude Code Live")

    def test_live_agent_http_endpoint_registers_and_lists_presence(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/live-agents",
                    data=json.dumps(
                        {
                            "agent_id": "gemini-cli",
                            "display_name": "Gemini CLI",
                            "provider_kind": "gemini",
                            "connection_kind": "local_cli",
                            "session_id": "gemini-session",
                        }
                    ).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urlopen(request, timeout=4) as response:
                    payload = json.loads(response.read().decode("utf-8"))
                with urlopen(f"http://127.0.0.1:{server.server_port}/api/live-agents", timeout=4) as response:
                    listed = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

            self.assertEqual(payload["agent"]["agent_id"], "gemini-cli")
            self.assertEqual(listed["agents"][0]["session_id"], "gemini-session")
            self.assertIsInstance(listed["agents"][0]["heartbeat_age_seconds"], int)
            self.assertGreaterEqual(listed["agents"][0]["heartbeat_age_seconds"], 0)
            self.assertEqual(listed["agents"][0]["stale_after_seconds"], 180)

    def test_live_agent_engagement_endpoint_updates_policy_without_refreshing_heartbeat(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            registered = connect_live_agent_payload(
                root,
                {
                    "agent_id": "agent-a",
                    "display_name": "Agent A",
                    "provider_kind": "local_cli",
                    "connection_kind": "local_cli",
                    "engagement_mode": "always",
                },
            )
            original_last_seen = registered["agent"]["last_seen_at"]
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/live-agents/agent-a/engagement",
                    data=json.dumps({"engagement_mode": "watch"}).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urlopen(request, timeout=4) as response:
                    payload = json.loads(response.read().decode("utf-8"))
                with urlopen(f"http://127.0.0.1:{server.server_port}/api/live-agents/agent-a/room", timeout=4) as response:
                    room = json.loads(response.read().decode("utf-8"))
                with urlopen(
                    f"http://127.0.0.1:{server.server_port}/api/live-agent-operations",
                    timeout=4,
                ) as response:
                    operations = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

        self.assertEqual(payload["agent"]["engagement_mode"], "watch")
        self.assertEqual(payload["agent"]["last_seen_at"], original_last_seen)
        self.assertEqual(room["agent"]["engagement_mode"], "watch")
        self.assertEqual(room["agent"]["last_seen_at"], original_last_seen)
        self.assertEqual(operations["operations"][0]["operation"], "engagement.update")
        self.assertEqual(operations["operations"][0]["status"], "success")
        self.assertEqual(operations["operations"][0]["target_id"], "agent-a")
        self.assertEqual(operations["operations"][0]["details"]["previous_engagement_mode"], "always")
        self.assertEqual(operations["operations"][0]["details"]["engagement_mode"], "watch")

    def test_live_agent_engagement_endpoint_rejects_invalid_policy(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            connect_live_agent_payload(root, {"agent_id": "agent-a", "engagement_mode": "always"})
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/live-agents/agent-a/engagement",
                    data=json.dumps({"engagement_mode": "shout_forever"}).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with self.assertRaises(HTTPError) as context:
                    urlopen(request, timeout=4)
                context.exception.close()
                with urlopen(
                    f"http://127.0.0.1:{server.server_port}/api/live-agent-operations",
                    timeout=4,
                ) as response:
                    operations = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

        self.assertEqual(context.exception.code, 400)
        self.assertEqual(operations["operations"][0]["operation"], "engagement.update")
        self.assertEqual(operations["operations"][0]["status"], "failed")
        self.assertEqual(operations["operations"][0]["target_id"], "agent-a")
        self.assertIn("Unknown engagement mode", operations["operations"][0]["error"])

    def test_live_agent_room_endpoint_returns_lobby_context(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            connect_live_agent_payload(root, {"agent_id": "claude-code-live", "display_name": "Claude Code Live"})
            append_lobby_event(root, {"name": "나", "side": "mine", "message": "방 상태 보여?"})
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with urlopen(
                    f"http://127.0.0.1:{server.server_port}/api/live-agents/claude-code-live/room",
                    timeout=4,
                ) as response:
                    payload = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

            self.assertEqual(payload["agent"]["display_name"], "Claude Code Live")
            self.assertEqual(payload["lobby_events"][0]["message"], "방 상태 보여?")

    def test_live_agent_heartbeat_and_lobby_message_endpoints_allow_participation(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            connect_live_agent_payload(root, {"agent_id": "gemini-cli", "display_name": "Gemini CLI"})
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                heartbeat_request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/live-agents/gemini-cli/heartbeat",
                    data=json.dumps({"status": "working"}).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urlopen(heartbeat_request, timeout=4) as response:
                    heartbeat = json.loads(response.read().decode("utf-8"))
                message_request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/live-agents/gemini-cli/lobby",
                    data=json.dumps({"message": "Gemini CLI 접속 확인", "kind": "message"}).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urlopen(message_request, timeout=4) as response:
                    message = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

            self.assertEqual(heartbeat["agent"]["status"], "working")
            self.assertEqual(message["event"]["name"], "Gemini CLI")
            self.assertEqual(message["event"]["side"], "other-agent")
            self.assertEqual(message["event"]["message"], "Gemini CLI 접속 확인")

    def test_live_agent_heartbeat_persists_error_and_cursor_metadata(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            connect_live_agent_payload(root, {"agent_id": "claude-code-live", "display_name": "Claude Code Live"})
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/live-agents/claude-code-live/heartbeat",
                    data=json.dumps(
                        {
                            "status": "error",
                            "last_error": "command failed",
                            "last_observed_event_id": "evt1",
                            "last_reply_at": "2026-05-17T12:00:00+00:00",
                        }
                    ).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urlopen(request, timeout=4) as response:
                    payload = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

            agent = payload["agent"]
            self.assertEqual(agent["status"], "error")
            self.assertEqual(agent["last_error"], "command failed")
            self.assertEqual(agent["last_observed_event_id"], "evt1")
            self.assertEqual(agent["last_reply_at"], "2026-05-17T12:00:00+00:00")

    def test_live_agent_lobby_message_records_actor_source_and_chain_depth(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            connect_live_agent_payload(root, {"agent_id": "gemini-cli", "display_name": "Gemini CLI"})
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/live-agents/gemini-cli/lobby",
                    data=json.dumps(
                        {
                            "message": "자동 반응",
                            "source_event_id": "evt1",
                            "auto_chain_depth": 1,
                        }
                    ).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urlopen(request, timeout=4) as response:
                    payload = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

            event = payload["event"]
            self.assertEqual(event["actor_id"], "gemini-cli")
            self.assertEqual(event["source_event_id"], "evt1")
            self.assertEqual(event["auto_chain_depth"], 1)

    def test_lobby_events_are_appended_and_sanitized(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)

            append_lobby_event(root, {"name": "seinel\nbad", "kind": "ready", "message": ""})
            append_lobby_event(root, {"name": "bad", "side": "???", "kind": "???", "message": "x"})
            append_lobby_event(
                root,
                {"name": "friend", "side": "other-agent", "kind": "message", "message": "만갤러 준비됐냐?"},
            )

            events = read_lobby(root)

            self.assertEqual(len(events), 3)
            self.assertEqual(events[0]["kind"], "ready")
            self.assertEqual(events[0]["side"], "other")
            self.assertEqual(events[0]["message"], "준비됐습니다.")
            self.assertEqual(events[0]["name"], "seinel bad")
            self.assertEqual(events[1]["kind"], "message")
            self.assertEqual(events[1]["side"], "other")
            self.assertEqual(events[2]["side"], "other-agent")
            self.assertEqual(events[2]["message"], "만갤러 준비됐냐?")

    def test_side_chat_is_stored_separately_from_lobby_and_live_events(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)

            append_side_chat_event(root, {"name": "seinel", "side": "mine", "message": "실황 보면서 한마디"})
            append_lobby_event(root, {"name": "lobby", "side": "other", "message": "로비 메시지"})

            side_events = read_side_chat(root)

            self.assertEqual(len(side_events), 1)
            self.assertEqual(side_events[0]["message"], "실황 보면서 한마디")
            self.assertEqual(read_lobby(root)[0]["message"], "로비 메시지")
            self.assertTrue((root / "side_chat.jsonl").exists())
            self.assertFalse((root / "meetings" / "side_chat.jsonl").exists())

    def test_legacy_side_chat_lines_are_read_as_side_chat_channel(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            path = root / "side_chat.jsonl"
            path.write_text(
                '{"id":"legacy","created_at":"2026-05-12T00:00:00+00:00","name":"나","side":"mine","kind":"message","message":"old"}\n',
                encoding="utf-8",
            )

            side_events = read_side_chat(root)

            self.assertEqual(side_events[0]["channel"], "side_chat")
            self.assertFalse(side_events[0]["official_record"])

    def test_room_events_record_channel_audience_and_official_record_boundary(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            meeting_dir = root / "meetings" / "m1"
            meeting_dir.mkdir(parents=True)

            lobby_event = append_lobby_event(root, {"name": "나", "side": "mine", "message": "로비 잡담"})
            side_event = append_side_chat_event(root, {"name": "나", "side": "mine", "message": "실황 옆 잡담"})
            status_event = append_live_event(meeting_dir, {"kind": "status", "content": "회의 시작"})
            message_event = append_live_event(
                meeting_dir,
                {
                    "kind": "message",
                    "display_name": "설정충",
                    "content": "공식 발언",
                    "turn_id": "round_1:0:lore_lawyer",
                    "turn_index": 0,
                    "engagement_mode": "official_turn",
                },
            )
            synthesis_event = append_live_event(
                meeting_dir,
                {"kind": "synthesis", "display_name": "Moderator", "content": "종합"},
            )

            self.assertEqual(lobby_event["channel"], "lobby")
            self.assertEqual(side_event["channel"], "side_chat")
            self.assertEqual(lobby_event["audience"], "room")
            self.assertFalse(lobby_event["official_record"])
            self.assertFalse(side_event["official_record"])
            self.assertEqual(status_event["channel"], "system")
            self.assertFalse(status_event["official_record"])
            self.assertEqual(message_event["channel"], "official")
            self.assertTrue(message_event["official_record"])
            self.assertEqual(message_event["turn_id"], "round_1:0:lore_lawyer")
            self.assertEqual(message_event["turn_index"], 0)
            self.assertEqual(message_event["engagement_mode"], "official_turn")
            self.assertTrue(synthesis_event["official_record"])

    def test_room_event_readers_can_resume_after_event_id(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            meeting_dir = root / "meetings" / "m1"
            meeting_dir.mkdir(parents=True)

            first_lobby = append_lobby_event(root, {"name": "나", "side": "mine", "message": "첫 로비"})
            second_lobby = append_lobby_event(root, {"name": "친구", "side": "other", "message": "둘째 로비"})
            first_side = append_side_chat_event(root, {"name": "나", "side": "mine", "message": "첫 비공식"})
            second_side = append_side_chat_event(root, {"name": "친구", "side": "other", "message": "둘째 비공식"})
            first_live = append_live_event(meeting_dir, {"kind": "message", "content": "첫 공식"})
            second_live = append_live_event(meeting_dir, {"kind": "message", "content": "둘째 공식"})

            self.assertEqual(
                [event["id"] for event in read_lobby_events_after(root / "lobby.jsonl", first_lobby["id"])],
                [second_lobby["id"]],
            )
            self.assertEqual(
                [event["id"] for event in read_side_chat_events_after(root / "side_chat.jsonl", first_side["id"])],
                [second_side["id"]],
            )
            self.assertEqual(
                [event["id"] for event in read_live_events_after(meeting_dir, first_live["id"])],
                [second_live["id"]],
            )

    def test_sse_event_formats_id_event_name_and_json_data(self):
        body = _sse_event("lobby", {"id": "abc", "message": "안녕"}, event_id="abc").decode("utf-8")

        self.assertIn("id: abc\n", body)
        self.assertIn("event: lobby\n", body)
        self.assertIn('data: {"id": "abc", "message": "안녕"}\n\n', body)

    def test_sse_stream_error_payload_bounds_error_text(self):
        payload = _sse_stream_error_payload(
            "meeting",
            ValueError("line one\n" + ("x" * 700)),
            meeting_id="m1",
        )

        self.assertEqual(payload["stream"], "meeting")
        self.assertEqual(payload["meeting_id"], "m1")
        self.assertNotIn("\n", payload["error"])
        self.assertLessEqual(len(payload["error"]), 500)

    def test_lobby_sse_keeps_connection_open_with_heartbeat(self):
        stderr = StringIO()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            append_lobby_event(root, {"name": "나", "side": "mine", "message": "첫 로비"})
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            with patch("sys.stderr", stderr):
                thread.start()
                try:
                    with urlopen(f"http://127.0.0.1:{server.server_port}/api/events/lobby", timeout=4) as response:
                        lines = []
                        deadline = time.time() + 3
                        while time.time() < deadline and ": keep-alive" not in "\n".join(lines):
                            lines.append(response.readline().decode("utf-8").strip())
                    time.sleep(0.2)
                finally:
                    server.shutdown()
                    server.server_close()

            body = "\n".join(lines)
            self.assertIn("event: lobby", body)
            self.assertIn('"stream": "lobby"', body)
            self.assertIn(": keep-alive", body)
            self.assertNotIn("ConnectionResetError", stderr.getvalue())

    def test_sse_client_disconnect_does_not_log_connection_reset_traceback(self):
        stderr = StringIO()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            append_lobby_event(root, {"name": "나", "side": "mine", "message": "첫 로비"})
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            with patch("sys.stderr", stderr):
                thread.start()
                try:
                    response = urlopen(f"http://127.0.0.1:{server.server_port}/api/events/lobby", timeout=4)
                    try:
                        self.assertIn("event: lobby", _read_sse_frame(response))
                    finally:
                        response.close()
                    time.sleep(0.2)
                finally:
                    server.shutdown()
                    server.server_close()

        self.assertNotIn("ConnectionResetError", stderr.getvalue())

    def test_meeting_sse_sends_final_payload_after_event_cursor_is_current(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            meeting_dir = root / "meetings" / "m1"
            meeting_dir.mkdir(parents=True)
            event = append_live_event(meeting_dir, {"kind": "message", "content": "공식"})
            (meeting_dir / "meeting.json").write_text(
                json.dumps(
                    {
                        "meeting_id": "m1",
                        "topic": "테스트",
                        "question": "완료됐나?",
                        "roles": [],
                        "debate_rounds": [],
                        "live_status": "complete",
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/meetings/m1/events",
                    headers={"Last-Event-ID": event["id"]},
                )
                with urlopen(request, timeout=4) as response:
                    lines = []
                    deadline = time.time() + 3
                    while time.time() < deadline and "meeting_payload" not in "\n".join(lines):
                        lines.append(response.readline().decode("utf-8").strip())
            finally:
                server.shutdown()
                server.server_close()

            body = "\n".join(lines)
            self.assertIn("event: meeting", body)
            self.assertIn('"meeting_payload"', body)
            self.assertIn('"live_status": "complete"', body)

    def test_meeting_sse_reports_error_if_meeting_disappears_during_stream(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            meeting_dir = root / "meetings" / "m1"
            meeting_dir.mkdir(parents=True)
            append_live_event(meeting_dir, {"kind": "message", "content": "공식"})
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with urlopen(f"http://127.0.0.1:{server.server_port}/api/meetings/m1/events", timeout=4) as response:
                    meeting_frame = _read_sse_frame(response)
                    for path in sorted(meeting_dir.glob("*")):
                        path.unlink()
                    meeting_dir.rmdir()
                    error_frame = _read_sse_frame(response)
                    trailing = response.readline()
            finally:
                server.shutdown()
                server.server_close()

            self.assertIn("event: meeting", meeting_frame)
            self.assertIn("event: error", error_frame)
            self.assertEqual(error_frame.count("event: error"), 1)
            self.assertIn('"stream": "meeting"', error_frame)
            self.assertIn('"meeting_id": "m1"', error_frame)
            self.assertIn("Meeting m1 was not found.", error_frame)
            self.assertEqual(trailing, b"")

    def test_meeting_sse_reports_error_if_payload_file_vanishes_after_headers(self):
        stderr = StringIO()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            meeting_dir = root / "meetings" / "m1"
            meeting_dir.mkdir(parents=True)
            append_live_event(meeting_dir, {"kind": "message", "content": "공식"})
            (meeting_dir / "meeting.json").write_text("{}", encoding="utf-8")
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            with patch("agentsassemble.gui.build_meeting_payload", side_effect=FileNotFoundError("lost")):
                with patch("sys.stderr", stderr):
                    thread.start()
                    try:
                        with urlopen(
                            f"http://127.0.0.1:{server.server_port}/api/meetings/m1/events",
                            timeout=4,
                        ) as response:
                            error_frame = _read_sse_frame(response)
                            trailing = response.readline()
                    finally:
                        server.shutdown()
                        server.server_close()

            self.assertIn("event: error", error_frame)
            self.assertEqual(error_frame.count("event: error"), 1)
            self.assertIn('"stream": "meeting"', error_frame)
            self.assertIn('"meeting_id": "m1"', error_frame)
            self.assertIn("Meeting m1 was not found.", error_frame)
            self.assertEqual(trailing, b"")
            self.assertNotIn("FileNotFoundError", stderr.getvalue())

    def test_missing_meeting_sse_returns_json_404_before_stream_opens(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with self.assertRaises(HTTPError) as context:
                    urlopen(f"http://127.0.0.1:{server.server_port}/api/meetings/missing/events", timeout=4)
                content_type = context.exception.headers.get("Content-Type")
                body = json.loads(context.exception.read().decode("utf-8"))
                context.exception.close()
            finally:
                server.shutdown()
                server.server_close()

            self.assertEqual(context.exception.code, 404)
            self.assertEqual(content_type, "application/json; charset=utf-8")
            self.assertEqual(body, {"error": "Meeting not found"})

    def test_stream_snapshot_payload_keeps_lobby_side_chat_and_meeting_distinct(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            meeting_dir = root / "meetings" / "m1"
            meeting_dir.mkdir(parents=True)
            first_lobby = append_lobby_event(root, {"name": "나", "side": "mine", "message": "로비"})
            append_lobby_event(root, {"name": "상대", "side": "other", "message": "로비 둘"})
            append_side_chat_event(root, {"name": "나", "side": "mine", "message": "비공식"})
            append_live_event(meeting_dir, {"kind": "message", "content": "공식"})

            lobby_payload = _stream_snapshot_payload(root, "lobby", last_event_id=first_lobby["id"])
            side_payload = _stream_snapshot_payload(root, "side_chat")
            meeting_payload = _stream_snapshot_payload(root, "meeting", meeting_id="m1")

            self.assertEqual(lobby_payload["stream"], "lobby")
            self.assertEqual([event["message"] for event in lobby_payload["events"]], ["로비 둘"])
            self.assertEqual(side_payload["stream"], "side_chat")
            self.assertEqual(side_payload["events"][0]["message"], "비공식")
            self.assertEqual(meeting_payload["stream"], "meeting")
            self.assertEqual(meeting_payload["meeting_id"], "m1")
            self.assertEqual(meeting_payload["events"][0]["content"], "공식")

    def test_meeting_stream_includes_full_payload_after_final_record(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            result = run_demo_meeting(adapter_name="mock", output_root=root)

            payload = _stream_snapshot_payload(root, "meeting", meeting_id=result.meeting_id)

            self.assertEqual(payload["stream"], "meeting")
            self.assertEqual(payload["meeting_payload"]["meeting"]["live_status"], "complete")
            self.assertIn("decision_gate", payload["meeting_payload"]["meeting"])
            self.assertIn("decision.md", payload["meeting_payload"]["artifacts"])

    def test_meeting_stream_survives_partial_final_record(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            meeting_dir = root / "meetings" / "m1"
            meeting_dir.mkdir(parents=True)
            event = append_live_event(meeting_dir, {"kind": "message", "content": "공식"})
            (meeting_dir / "meeting.json").write_text("{", encoding="utf-8")

            payload = _stream_snapshot_payload(root, "meeting", meeting_id="m1")

            self.assertEqual(payload["stream"], "meeting")
            self.assertTrue(payload["meeting_payload_pending"])
            self.assertNotIn("meeting_payload", payload)

            (meeting_dir / "meeting.json").write_text(
                json.dumps(
                    {
                        "meeting_id": "m1",
                        "topic": "테스트",
                        "question": "완료됐나?",
                        "roles": [],
                        "debate_rounds": [],
                        "live_status": "complete",
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            recovered = _stream_snapshot_payload(root, "meeting", meeting_id="m1", last_event_id=event["id"])

            self.assertEqual(recovered["events"], [])
            self.assertEqual(recovered["meeting_payload"]["meeting"]["live_status"], "complete")

    def test_lobby_remote_bridge_reply_is_recorded_as_other_agent(self):
        import agentsassemble.gui as gui

        class FakeRequester:
            def __init__(self):
                self.calls = []

            def __call__(self, url, headers, payload, timeout_seconds):
                self.calls.append({"url": url, "headers": headers, "payload": payload})
                return {
                    "text": '{"message":"친구 Claude Code 준비됐습니다.","kind":"message"}',
                    "metadata": {"bridge": "friend-mac"},
                }

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            agent_config = root / "agents.json"
            agent_config.write_text(
                json.dumps(
                    {
                        "providers": [
                            {
                                "id": "friend-claude-code",
                                "kind": "remote_http_bridge",
                                "display_name": "Friend Claude Code",
                                "endpoint": "http://friend.local:8777",
                                "auth_ref": "literal:bridge-token",
                            }
                        ]
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            meeting_dir = root / "meetings" / "m1"
            meeting_dir.mkdir(parents=True)
            (meeting_dir / "meeting.json").write_text(
                json.dumps(
                    {
                        "meeting_id": "m1",
                        "topic": "테스트",
                        "question": "준비됐나?",
                        "roles": [
                            {
                                "id": "show_me_the_feats",
                                "display_name": "공식이뭘알아",
                                "lens": "전적/퍼포먼스",
                                "research_focus": "전투 결과",
                            }
                        ],
                        "provider_configs": {
                            "friend-claude-code": {
                                "id": "friend-claude-code",
                                "kind": "remote_http_bridge",
                                "display_name": "Friend Claude Code",
                                "endpoint": "http://friend.local:8777",
                                "auth_ref": "literal:<redacted>",
                            }
                        },
                        "agent_config_source": str(agent_config),
                        "agent_bindings": [
                            {
                                "agent_id": "friend-agent",
                                "role_id": "show_me_the_feats",
                                "owner_id": "friend",
                                "provider_id": "friend-claude-code",
                                "model_id": None,
                                "permission_profile_id": "meeting_read_only",
                                "join_mode": "current_session",
                            }
                        ],
                        "debate_rounds": [],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            requester = FakeRequester()
            previous_requester = gui.REMOTE_LOBBY_REQUESTER
            gui.REMOTE_LOBBY_REQUESTER = requester
            try:
                event = send_lobby_message_to_remote_bridge(root, "친구야 준비됐어?", meeting_id="m1", speaker_name="나")
            finally:
                gui.REMOTE_LOBBY_REQUESTER = previous_requester

            self.assertEqual(event["side"], "other-agent")
            self.assertEqual(event["name"], "공식이뭘알아")
            self.assertEqual(event["message"], "친구 Claude Code 준비됐습니다.")
            self.assertEqual(read_lobby(root)[0]["message"], "친구 Claude Code 준비됐습니다.")
            self.assertEqual(requester.calls[0]["headers"]["Authorization"], "Bearer bridge-token")
            self.assertEqual(requester.calls[0]["payload"]["step"], "lobby")

    def test_lobby_remote_bridge_rejects_redacted_public_literal_without_runtime_config(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            meeting_dir = root / "meetings" / "m1"
            meeting_dir.mkdir(parents=True)
            (meeting_dir / "meeting.json").write_text(
                json.dumps(
                    {
                        "meeting_id": "m1",
                        "roles": [
                            {
                                "id": "show_me_the_feats",
                                "display_name": "공식이뭘알아",
                                "lens": "전적/퍼포먼스",
                                "research_focus": "전투 결과",
                            }
                        ],
                        "provider_configs": {
                            "friend-claude-code": {
                                "id": "friend-claude-code",
                                "kind": "remote_http_bridge",
                                "display_name": "Friend Claude Code",
                                "endpoint": "http://friend.local:8777",
                                "auth_ref": "literal:<redacted>",
                            }
                        },
                        "agent_config_source": "default",
                        "agent_bindings": [
                            {
                                "agent_id": "friend-agent",
                                "role_id": "show_me_the_feats",
                                "owner_id": "friend",
                                "provider_id": "friend-claude-code",
                                "permission_profile_id": "meeting_read_only",
                            }
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "credential is not available"):
                send_lobby_message_to_remote_bridge(root, "친구야 준비됐어?", meeting_id="m1", speaker_name="나")

    def test_list_meetings_orders_latest_first(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            first = run_demo_meeting(adapter_name="mock", output_root=root)
            second = run_demo_meeting(adapter_name="mock", output_root=root)

            meetings = list_meetings(root)

            self.assertEqual(meetings[0]["meeting_id"], second.meeting_id)
            self.assertEqual(meetings[1]["meeting_id"], first.meeting_id)

    def test_live_meeting_payload_can_load_before_meeting_json_exists(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            meeting_dir = root / "meetings" / "live-1"
            meeting_dir.mkdir(parents=True)
            write_live_state(
                meeting_dir,
                {
                    "meeting_id": "live-1",
                    "topic": "Live topic",
                    "question": "Live question?",
                    "roles": [],
                    "debate_rounds": [],
                    "moderator_synthesis": {},
                    "live_status": "running",
                },
            )
            append_live_event(meeting_dir, {"kind": "status", "content": "회의 시작"})

            meetings = list_meetings(root)
            payload = build_meeting_payload(meeting_dir)

            self.assertEqual(meetings[0]["meeting_id"], "live-1")
            self.assertEqual(payload["meeting"]["live_status"], "running")
            self.assertEqual(payload["live_events"][0]["content"], "회의 시작")

    def test_live_meeting_payload_uses_live_state_while_final_record_is_partial(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            meeting_dir = root / "meetings" / "live-partial"
            meeting_dir.mkdir(parents=True)
            write_live_state(
                meeting_dir,
                {
                    "meeting_id": "live-partial",
                    "topic": "Partial topic",
                    "question": "Can the room recover?",
                    "roles": [],
                    "debate_rounds": [],
                    "moderator_synthesis": {},
                    "live_status": "running",
                },
            )
            append_live_event(meeting_dir, {"kind": "status", "content": "회의 진행 중"})
            (meeting_dir / "meeting.json").write_text("{", encoding="utf-8")

            meetings = list_meetings(root)
            payload = build_meeting_payload(meeting_dir)

            self.assertEqual(meetings[0]["meeting_id"], "live-partial")
            self.assertEqual(payload["meeting"]["live_status"], "running")
            self.assertEqual(payload["live_events"][0]["content"], "회의 진행 중")

    def test_stale_live_meeting_is_marked_stalled(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            meeting_dir = root / "meetings" / "stale-live"
            meeting_dir.mkdir(parents=True)
            write_live_state(
                meeting_dir,
                {
                    "meeting_id": "stale-live",
                    "topic": "Stalled topic",
                    "question": "Did the meeting stop?",
                    "roles": [],
                    "debate_rounds": [],
                    "moderator_synthesis": {},
                    "live_status": "running",
                },
            )
            append_live_event(meeting_dir, {"kind": "status", "content": "독립 리서치 시작"})
            stale_mtime = time.time() - 3600
            os.utime(meeting_dir / "live_state.json", (stale_mtime, stale_mtime))
            os.utime(meeting_dir / "live_events.jsonl", (stale_mtime, stale_mtime))

            meetings = list_meetings(root, now=time.time())
            payload = build_meeting_payload(meeting_dir, now=time.time())

            self.assertEqual(meetings[0]["live_status"], "stalled")
            self.assertEqual(payload["meeting"]["live_status"], "stalled")
            self.assertIn("stalled_reason", payload["meeting"])

    def test_static_paths_cannot_escape_static_root(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            static_root = root / "static"
            static_root.mkdir()
            (static_root / "app.js").write_text("", encoding="utf-8")

            self.assertEqual(_safe_static_path(static_root, "app.js"), (static_root / "app.js").resolve())
            self.assertIsNone(_safe_static_path(static_root, "../secret.txt"))

    def test_provider_catalog_payload_lists_planned_integrations(self):
        payload = provider_catalog_payload()
        providers = {provider["kind"]: provider for provider in payload["providers"]}

        self.assertEqual(providers["codex"]["status"], "available")
        self.assertEqual(providers["anthropic"]["status"], "available")
        self.assertEqual(providers["gemini"]["status"], "available")
        self.assertEqual(providers["grok"]["status"], "available")
        self.assertEqual(providers["local_openai_compatible"]["status"], "available")
        self.assertIn("capabilities", providers["claude_code"])
        self.assertTrue(providers["cursor"]["capabilities"]["supports_filesystem"])

    def test_provider_health_endpoint_checks_runtime_config_without_starting_meeting(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = root / "agents.json"
            config_path.write_text(
                json.dumps(
                    {
                        "providers": [
                            {"id": "mock-provider", "kind": "mock", "display_name": "Mock"}
                        ],
                        "permission_profiles": [{"id": "meeting"}],
                        "agent_bindings": [
                            {
                                "agent_id": "mock-agent",
                                "role_id": "lore_lawyer",
                                "provider_id": "mock-provider",
                                "permission_profile_id": "meeting",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/provider-health",
                    data=json.dumps(
                        {
                            "config_path": str(config_path),
                            "probe_mode": "local",
                            "probe_timeout_seconds": 0.75,
                        }
                    ).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urlopen(request, timeout=4) as response:
                    payload = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

            self.assertEqual(payload["status"], "ok")
            self.assertEqual(payload["probe_mode"], "local")
            self.assertEqual(payload["summary"]["providers"], 1)
            self.assertEqual(payload["providers"][0]["provider_id"], "mock-provider")

    def test_provider_health_endpoint_forwards_bridge_probe_options(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = root / "agents.json"
            config_path.write_text("{}", encoding="utf-8")
            report = {
                "status": "ok",
                "probe_mode": "bridge",
                "summary": {"providers": 0, "failed_providers": 0, "bindings": 0, "failed_bindings": 0, "checks_failed": 0, "warnings": 0},
                "providers": [],
                "bindings": [],
            }
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/provider-health",
                    data=json.dumps(
                        {
                            "config_path": str(config_path),
                            "probe_mode": "bridge",
                            "probe_timeout_seconds": 0.75,
                        }
                    ).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with patch("agentsassemble.gui.provider_health_report", return_value=report) as provider_health:
                    with urlopen(request, timeout=4) as response:
                        payload = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

            self.assertEqual(payload["probe_mode"], "bridge")
            provider_health.assert_called_once_with(
                config_path,
                probe_mode="bridge",
                probe_timeout_seconds=0.75,
            )


if __name__ == "__main__":
    unittest.main()
