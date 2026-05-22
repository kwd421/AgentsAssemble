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
from unittest.mock import ANY, patch
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
    live_agent_official_turn_payload,
    live_agent_turn_sequence_payload,
    live_agent_turn_request_payload,
    live_agent_turn_round_payload,
    live_agent_turn_rounds_payload,
    _live_agent_turn_rounds_payload_locked,
    _run_session_bound_agent_probe,
    _redact_real_session_smoke_lobby_events,
    _readiness_health_operation_details,
    _session_start_operation_details,
    live_agent_lobby_message_payload,
    LIVE_AGENT_ROOM_LOBBY_EVENT_LIMIT,
    live_agents_payload,
    live_agent_health_payload,
    live_agent_discovery_payload,
    live_agent_session_ensure_payload,
    _attach_session_auto_rounds_if_requested,
    send_lobby_message_to_remote_bridge,
    append_side_chat_event,
)
from agentsassemble.meeting_events import append_live_event, read_live_events, write_live_state
from agentsassemble.meeting_events import read_live_events_after, read_lobby_events_after, read_side_chat_events_after
from agentsassemble.meeting import run_demo_meeting
from agentsassemble.live_agents import connect_live_agent, heartbeat_live_agent, read_live_agents
from agentsassemble.live_agent_operations import append_live_agent_operation
from agentsassemble.live_agent_meetings import start_live_agent_meeting
from agentsassemble.live_agent_processes import LiveAgentProcessSupervisor
from agentsassemble.live_agent_session_runs import LiveAgentSessionRunController
from agentsassemble.live_agent_smoke import LiveAgentSmokeFailed
from agentsassemble.live_session_transport import terminal_sessions_supported


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
    def test_live_agent_discovery_payload_filters_exact_approved_agents_before_writing_bundle(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)

            def resolver(command):
                return f"/opt/bin/{command}" if command in {"claude", "codex", "antigravity"} else None

            with (
                patch("agentsassemble.live_agent_discovery.shutil.which", side_effect=resolver),
                patch("agentsassemble.live_agent_discovery.terminal_sessions_supported", return_value=True),
            ):
                report = live_agent_discovery_payload(
                    root,
                    {
                        "server": "http://room.local",
                        "meeting_id": "resident-gui",
                        "write_config": True,
                        "session_bundle": True,
                        "approved_agents": ["codex-live"],
                    },
                    default_server="http://default.local",
                )

            self.assertEqual(report["status"], "ok")
            output_path = Path(report["output"])
            self.assertTrue(output_path.exists())
            written = json.loads(output_path.read_text(encoding="utf-8"))
            self.assertEqual([agent["agent_id"] for agent in written["agents"]], ["codex-live"])
            council = json.loads(Path(report["session_bundle"]["council_config_path"]).read_text(encoding="utf-8"))
            agent_config = json.loads(Path(report["session_bundle"]["agent_config_path"]).read_text(encoding="utf-8"))
            self.assertEqual([role["id"] for role in council["roles"]], ["codex_live"])
            self.assertEqual([binding["agent_id"] for binding in agent_config["agent_bindings"]], ["codex-live"])
            discoveries = {item["command"]: item for item in report["discoveries"]}
            self.assertEqual(discoveries["codex"]["approval_status"], "approved")
            self.assertEqual(discoveries["claude"]["approval_status"], "not_approved")
            self.assertEqual(discoveries["antigravity"]["approval_status"], "not_approved")
            self.assertEqual(report["approval_filter"]["approved_agents"], ["codex-live"])

    def test_live_agent_discovery_endpoint_records_exact_approval_operation_evidence(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)

            def resolver(command):
                return f"/opt/bin/{command}" if command in {"claude", "codex", "antigravity"} else None

            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                server_url = f"http://127.0.0.1:{server.server_port}"
                with (
                    patch("agentsassemble.live_agent_discovery.shutil.which", side_effect=resolver),
                    patch("agentsassemble.live_agent_discovery.terminal_sessions_supported", return_value=True),
                ):
                    request = Request(
                        f"{server_url}/api/live-agent-discovery",
                        data=json.dumps(
                            {
                                "meeting_id": "resident-gui",
                                "write_config": True,
                                "session_bundle": True,
                                "approved_agents": ["codex-live", "missing-agent"],
                            }
                        ).encode("utf-8"),
                        headers={"Content-Type": "application/json"},
                        method="POST",
                    )
                    with urlopen(request, timeout=4) as response:
                        report = json.loads(response.read().decode("utf-8"))
                with urlopen(f"{server_url}/api/live-agent-operations", timeout=4) as response:
                    operations = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

            discovery_operations = [
                operation for operation in operations["operations"] if operation["operation"] == "discovery.run"
            ]
            self.assertEqual(report["approval_filter"]["approved_agents"], ["codex-live"])
            self.assertEqual(len(discovery_operations), 1)
            details = discovery_operations[0]["details"]
            self.assertEqual(details["result_status"], "ok")
            self.assertEqual(details["agents"], 1)
            self.assertEqual(details["approved_count"], 1)
            self.assertEqual(details["approved_agent_ids"], ["codex-live"])
            self.assertEqual(details["excluded_agent_count"], 2)
            self.assertEqual(details["unmatched_approval_count"], 1)
            serialized = json.dumps(discovery_operations[0], ensure_ascii=False)
            self.assertNotIn("/opt/bin", serialized)
            self.assertNotIn("approved_commands", serialized)
            self.assertNotIn("excluded_commands", serialized)

    def test_live_agent_discovery_endpoint_records_cli_approval_counts_without_command_names(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)

            def resolver(command):
                return f"/opt/bin/{command}" if command in {"claude", "codex", "antigravity"} else None

            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                server_url = f"http://127.0.0.1:{server.server_port}"
                with (
                    patch("agentsassemble.live_agent_discovery.shutil.which", side_effect=resolver),
                    patch("agentsassemble.live_agent_discovery.terminal_sessions_supported", return_value=True),
                ):
                    request = Request(
                        f"{server_url}/api/live-agent-discovery",
                        data=json.dumps(
                            {
                                "meeting_id": "resident-gui",
                                "write_config": True,
                                "session_bundle": True,
                                "approved_commands": ["codex", "missing-cli"],
                            }
                        ).encode("utf-8"),
                        headers={"Content-Type": "application/json"},
                        method="POST",
                    )
                    with urlopen(request, timeout=4) as response:
                        report = json.loads(response.read().decode("utf-8"))
                with urlopen(f"{server_url}/api/live-agent-operations", timeout=4) as response:
                    operations = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

            discovery_operations = [
                operation for operation in operations["operations"] if operation["operation"] == "discovery.run"
            ]
            self.assertEqual(report["approval_filter"]["approved_commands"], ["codex"])
            self.assertEqual(len(discovery_operations), 1)
            details = discovery_operations[0]["details"]
            self.assertEqual(details["result_status"], "ok")
            self.assertEqual(details["agents"], 1)
            self.assertEqual(details["approved_count"], 1)
            self.assertNotIn("approved_agent_ids", details)
            self.assertEqual(details["approved_cli_count"], 1)
            self.assertEqual(details["excluded_cli_count"], 2)
            self.assertEqual(details["excluded_agent_count"], 2)
            self.assertEqual(details["unmatched_approval_count"], 1)
            serialized_details = json.dumps(details, ensure_ascii=False)
            self.assertNotIn("/opt/bin", serialized_details)
            self.assertNotIn("missing-cli", serialized_details)
            self.assertNotIn("approved_commands", serialized_details)
            self.assertNotIn("excluded_commands", serialized_details)
            approval_details = {
                key: value
                for key, value in details.items()
                if key
                in {
                    "approved_count",
                    "approved_cli_count",
                    "excluded_agent_count",
                    "excluded_cli_count",
                    "unmatched_approval_count",
                }
            }
            serialized_approval_details = json.dumps(approval_details, ensure_ascii=False)
            self.assertNotIn("codex", serialized_approval_details)
            self.assertNotIn("claude", serialized_approval_details)
            self.assertNotIn("antigravity", serialized_approval_details)
            self.assertNotIn("missing-cli", serialized_approval_details)

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

    def test_live_agent_operations_endpoint_filters_operation_target_and_status(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            append_live_agent_operation(
                root,
                operation="session.start",
                status="success",
                target_id="resident-a",
                summary="matching operation",
            )
            append_live_agent_operation(
                root,
                operation="session.start",
                status="failed",
                target_id="resident-a",
                summary="wrong status",
            )
            append_live_agent_operation(
                root,
                operation="session.resume",
                status="success",
                target_id="resident-a",
                summary="wrong operation",
            )
            append_live_agent_operation(
                root,
                operation="session.start",
                status="success",
                target_id="resident-b",
                summary="wrong target",
            )
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with urlopen(
                    (
                        f"http://127.0.0.1:{server.server_port}/api/live-agent-operations"
                        "?limit=1&operation=session.start&target_id=resident-a&status=success&scan_limit=10"
                    ),
                    timeout=4,
                ) as response:
                    payload = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

        self.assertEqual([operation["summary"] for operation in payload["operations"]], ["matching operation"])
        self.assertEqual(payload["operation"], "session.start")
        self.assertEqual(payload["target_id"], "resident-a")
        self.assertEqual(payload["status"], "success")
        self.assertEqual(payload["scan_limit"], 10)
        self.assertFalse(payload["truncated"])

    def test_build_meeting_payload_includes_room_log_for_free_chat(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            result = run_demo_meeting(adapter_name="mock", output_root=Path(temp_dir), meeting_mode="free_chat")

            payload = build_meeting_payload(result.meeting_dir)

            self.assertEqual(payload["meeting"]["meeting_mode"], "free_chat")
            self.assertIn("room-log.md", payload["artifacts"])
            self.assertIn("informal free-chat record", payload["artifacts"]["room-log.md"])
            self.assertEqual(payload["artifacts"].get("decision.md"), "")
            self.assertEqual(payload["artifacts"].get("transcript.md"), "")

    def test_build_meeting_payload_projects_live_transcript_without_writing_file(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            meeting_dir = Path(temp_dir) / "meetings" / "m1"
            meeting_dir.mkdir(parents=True)
            write_live_state(meeting_dir, {"meeting_id": "m1", "topic": "runtime", "live_status": "running"})
            append_live_event(
                meeting_dir,
                {
                    "kind": "live_agent_turn_request",
                    "meeting_id": "m1",
                    "target_agent_id": "agent-a",
                    "content": "private request text",
                },
            )
            append_live_event(
                meeting_dir,
                {
                    "kind": "message",
                    "meeting_id": "m1",
                    "actor_id": "agent-a",
                    "display_name": "Agent A",
                    "source_event_id": "request-1",
                    "content": "official live reply",
                },
            )

            payload = build_meeting_payload(meeting_dir)

            self.assertIn("official live reply", payload["artifacts"]["transcript.md"])
            self.assertNotIn("private request text", payload["artifacts"]["transcript.md"])
            self.assertFalse((meeting_dir / "transcript.md").exists())

    def test_build_meeting_payload_projects_shared_memory_without_writing_file(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            meeting_dir = Path(temp_dir) / "meetings" / "m1"
            meeting_dir.mkdir(parents=True)
            write_live_state(meeting_dir, {"meeting_id": "m1", "topic": "runtime", "live_status": "running"})
            append_live_event(
                meeting_dir,
                {
                    "kind": "live_agent_turn_request",
                    "meeting_id": "m1",
                    "target_agent_id": "agent-a",
                    "content": "private request text",
                },
            )
            append_live_event(
                meeting_dir,
                {
                    "kind": "message",
                    "meeting_id": "m1",
                    "actor_id": "agent-a",
                    "display_name": "Agent A",
                    "source_event_id": "request-1",
                    "content": "official live reply\nAction: Preserve a shared memory artifact.",
                },
            )

            payload = build_meeting_payload(meeting_dir)

            self.assertIn("official live reply", payload["artifacts"]["shared_memory/rolling-summary.md"])
            self.assertIn("Preserve a shared memory artifact.", payload["artifacts"]["shared_memory/action-items.md"])
            self.assertNotIn("private request text", payload["artifacts"]["shared_memory/rolling-summary.md"])
            self.assertFalse((meeting_dir / "shared_memory" / "rolling-summary.md").exists())
            self.assertFalse((meeting_dir / "shared_memory" / "index.json").exists())

    def test_build_meeting_payload_preserves_existing_transcript(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            meeting_dir = Path(temp_dir) / "meetings" / "m1"
            meeting_dir.mkdir(parents=True)
            (meeting_dir / "meeting.json").write_text('{"meeting_id":"m1"}\n', encoding="utf-8")
            (meeting_dir / "transcript.md").write_text("# Canonical Transcript\n\nKeep me.\n", encoding="utf-8")
            append_live_event(
                meeting_dir,
                {
                    "kind": "message",
                    "meeting_id": "m1",
                    "actor_id": "agent-a",
                    "display_name": "Agent A",
                    "content": "late official event",
                },
            )

            payload = build_meeting_payload(meeting_dir)

            self.assertEqual(payload["artifacts"]["transcript.md"], "# Canonical Transcript\n\nKeep me.\n")

    def test_build_meeting_payload_preserves_existing_empty_transcript(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            meeting_dir = Path(temp_dir) / "meetings" / "m1"
            meeting_dir.mkdir(parents=True)
            write_live_state(meeting_dir, {"meeting_id": "m1", "topic": "runtime", "live_status": "running"})
            (meeting_dir / "transcript.md").write_text("", encoding="utf-8")
            append_live_event(
                meeting_dir,
                {
                    "kind": "message",
                    "meeting_id": "m1",
                    "actor_id": "agent-a",
                    "display_name": "Agent A",
                    "content": "official live reply",
                },
            )

            payload = build_meeting_payload(meeting_dir)

            self.assertEqual(payload["artifacts"]["transcript.md"], "")

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
            persisted_operations = (root / "live-agent-runs" / "operations.jsonl").read_text(encoding="utf-8")
            invite_operations = [
                operation
                for operation in operations["operations"]
                if operation["operation"] == "codex_session.invite"
            ]
            self.assertEqual(len(invite_operations), 1)
            self.assertEqual(invite_operations[0]["status"], "success")
            self.assertEqual(invite_operations[0]["target_id"], "lore_lawyer")
            self.assertEqual(
                invite_operations[0]["details"],
                {
                    "role_id": "lore_lawyer",
                    "agent_id": "codex-live-lore-lawyer",
                    "join_mode": "current_session",
                    "provider_id": "codex-live",
                },
            )
            operation_blob = json.dumps(invite_operations, ensure_ascii=False)
            self.assertNotIn("019e02af-c287-7cd1-aab7-c1e059c5ed44", operation_blob)
            self.assertNotIn("codex-live-session.local.json", operation_blob)
            self.assertNotIn("019e02af-c287-7cd1-aab7-c1e059c5ed44", persisted_operations)
            self.assertNotIn("codex-live-session.local.json", persisted_operations)

    def test_codex_session_invite_http_failure_records_safe_operation(self):
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
                            {"id": "show_me_the_feats", "display_name": "근거충"},
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            (root / "codex-live-session.local.json").write_text(
                json.dumps(
                    {
                        "agent_bindings": [
                            {
                                "agent_id": "codex-live-feats",
                                "role_id": "show_me_the_feats",
                                "owner_id": "host",
                                "provider_id": "codex-live",
                                "permission_profile_id": "codex_live_meeting_readonly",
                                "join_mode": "current_session",
                                "session_id": "019e02af-c287-7cd1-aab7-c1e059c5ed44",
                            }
                        ]
                    }
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
                with self.assertRaises(HTTPError) as caught:
                    urlopen(request, timeout=4)
                error_payload = json.loads(caught.exception.read().decode("utf-8"))
                caught.exception.close()
                with urlopen(
                    f"http://127.0.0.1:{server.server_port}/api/live-agent-operations",
                    timeout=4,
                ) as response:
                    operations = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

            self.assertEqual(caught.exception.code, 400)
            self.assertEqual(error_payload["error"], "Codex live session invite failed.")
            self.assertEqual(error_payload["details"], {"role_id": "lore_lawyer"})
            persisted_operations = (root / "live-agent-runs" / "operations.jsonl").read_text(encoding="utf-8")
            invite_operations = [
                operation
                for operation in operations["operations"]
                if operation["operation"] == "codex_session.invite"
            ]
            self.assertEqual(len(invite_operations), 1)
            self.assertEqual(invite_operations[0]["status"], "failed")
            self.assertEqual(invite_operations[0]["target_id"], "lore_lawyer")
            self.assertEqual(invite_operations[0]["details"], {"role_id": "lore_lawyer"})
            response_blob = json.dumps(error_payload, ensure_ascii=False)
            operation_blob = json.dumps(invite_operations, ensure_ascii=False)
            for blob in (response_blob, operation_blob, persisted_operations):
                self.assertNotIn("019e02af-c287-7cd1-aab7-c1e059c5ed44", blob)
                self.assertNotIn("codex-live-session.local.json", blob)

    def test_codex_session_invite_http_failure_omits_unknown_role_detail(self):
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
                            "role_id": "codex-live-session.local.json",
                            "session_id": "019e02af-c287-7cd1-aab7-c1e059c5ed44",
                        }
                    ).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with self.assertRaises(HTTPError) as caught:
                    urlopen(request, timeout=4)
                error_payload = json.loads(caught.exception.read().decode("utf-8"))
                caught.exception.close()
                with urlopen(
                    f"http://127.0.0.1:{server.server_port}/api/live-agent-operations",
                    timeout=4,
                ) as response:
                    operations = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

            self.assertEqual(caught.exception.code, 400)
            self.assertEqual(error_payload["error"], "Codex live session invite failed.")
            self.assertNotIn("details", error_payload)
            persisted_operations = (root / "live-agent-runs" / "operations.jsonl").read_text(encoding="utf-8")
            invite_operations = [
                operation
                for operation in operations["operations"]
                if operation["operation"] == "codex_session.invite"
            ]
            self.assertEqual(len(invite_operations), 1)
            self.assertEqual(invite_operations[0]["status"], "failed")
            self.assertEqual(invite_operations[0]["target_id"], "")
            self.assertEqual(invite_operations[0]["details"], {})
            response_blob = json.dumps(error_payload, ensure_ascii=False)
            operation_blob = json.dumps(invite_operations, ensure_ascii=False)
            for blob in (response_blob, operation_blob, persisted_operations):
                self.assertNotIn("019e02af-c287-7cd1-aab7-c1e059c5ed44", blob)
                self.assertNotIn("codex-live-session.local.json", blob)

    def test_codex_session_join_http_endpoint_binds_pre_round_meeting_and_ensures_session(self):
        class JoinSupervisor:
            def __init__(self, output_root: Path) -> None:
                self.output_root = output_root
                self.started = []
                self.groups = []

            def snapshot_groups(self):
                return list(self.groups)

            def list_groups(self):
                return list(self.groups)

            def start_group(self, **kwargs):
                self.started.append(kwargs)
                config = json.loads(Path(kwargs["config_path"]).read_text(encoding="utf-8"))
                agents = [{"agent_id": agent["agent_id"]} for agent in config["agents"]]
                for agent in config["agents"]:
                    connect_live_agent_payload(
                        self.output_root,
                        {
                            "agent_id": agent["agent_id"],
                            "display_name": agent.get("display_name", agent["agent_id"]),
                            "provider_kind": agent.get("provider_kind", "codex_live_session"),
                            "connection_kind": agent.get("connection_kind", "live_session"),
                            "meeting_id": kwargs["meeting_id"],
                            "session_id": agent.get("session_id", ""),
                        },
                    )
                    heartbeat_live_agent(
                        self.output_root,
                        agent["agent_id"],
                        status="online",
                        metadata={"session_id": agent.get("session_id", "")},
                    )
                group = {
                    "group_id": kwargs["group_id"],
                    "status": "running",
                    "meeting_id": kwargs["meeting_id"],
                    "agents": agents,
                }
                self.groups = [group]
                return group

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            meeting_dir = root / "meetings" / "m1"
            meeting_dir.mkdir(parents=True)
            write_live_state(
                meeting_dir,
                {
                    "meeting_id": "m1",
                    "topic": "runtime",
                    "question": "Can Codex join now?",
                    "roles": [
                        {"id": "lore_lawyer", "display_name": "설정충"},
                        {"id": "show_me_the_feats", "display_name": "근거충"},
                    ],
                    "provider_configs": {
                        "mock": {"id": "mock", "kind": "mock", "display_name": "Mock Demo"}
                    },
                    "permission_profiles": {
                        "meeting_read_only": {"id": "meeting_read_only", "meeting_read": True}
                    },
                    "agent_bindings": [
                        {
                            "agent_id": "lore-agent",
                            "role_id": "lore_lawyer",
                            "provider_id": "mock",
                            "permission_profile_id": "meeting_read_only",
                        },
                        {
                            "agent_id": "feats-agent",
                            "role_id": "show_me_the_feats",
                            "provider_id": "mock",
                            "permission_profile_id": "meeting_read_only",
                        },
                    ],
                    "debate_rounds": [],
                    "live_status": "running",
                },
            )
            supervisor = JoinSupervisor(root)
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root, process_supervisor=supervisor))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/codex-sessions/join",
                    data=json.dumps(
                        {
                            "meeting_id": "m1",
                            "role_id": "lore_lawyer",
                            "session_id": "019e02af-c287-7cd1-aab7-c1e059c5ed44",
                            "connect_timeout_seconds": 0,
                        }
                    ).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with patch(
                    "agentsassemble.live_agent_sessions.preflight_live_agent_config",
                    return_value={"status": "ok", "summary": {"agents": 2}},
                ):
                    with urlopen(request, timeout=4) as response:
                        payload = json.loads(response.read().decode("utf-8"))
                with urlopen(
                    f"http://127.0.0.1:{server.server_port}/api/live-agent-operations?limit=20",
                    timeout=4,
                ) as response:
                    operations = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

            self.assertEqual(payload["status"], "ready")
            self.assertEqual(payload["action"], "resume")
            self.assertEqual(payload["meeting_id"], "m1")
            self.assertEqual(payload["group_id"], "live-agents.codex-session.local")
            self.assertTrue((root / "codex-live-session.local.json").exists())
            self.assertTrue((root / "live-agents.codex-session.local.json").exists())
            self.assertEqual(supervisor.started[0]["meeting_id"], "m1")
            self.assertEqual(supervisor.started[0]["group_id"], "live-agents.codex-session.local")

            live_state = json.loads((meeting_dir / "live_state.json").read_text(encoding="utf-8"))
            bindings = {binding["role_id"]: binding for binding in live_state["agent_bindings"]}
            self.assertEqual(bindings["lore_lawyer"]["join_mode"], "current_session")
            self.assertEqual(bindings["lore_lawyer"]["session_id"], "019e02af-c287-7cd1-aab7-c1e059c5ed44")
            self.assertEqual(bindings["show_me_the_feats"]["join_mode"], "fresh")
            self.assertEqual(live_state["provider_configs"]["codex-live"]["kind"], "codex_live_session")
            self.assertEqual(live_state["permission_profiles"]["codex_live_meeting_readonly"]["official_turn"], True)

            resident_config = json.loads((root / "live-agents.codex-session.local.json").read_text(encoding="utf-8"))
            self.assertEqual([agent["agent_id"] for agent in resident_config["agents"]], ["codex-live-lore-lawyer", "codex-live-show-me-the-feats"])
            self.assertEqual(resident_config["agents"][0]["session_id"], "019e02af-c287-7cd1-aab7-c1e059c5ed44")
            self.assertEqual(resident_config["agents"][0]["meeting_id"], "m1")
            self.assertEqual({agent["engagement_mode"] for agent in resident_config["agents"]}, {"moderator_called"})

            join_operations = [operation for operation in operations["operations"] if operation["operation"] == "codex_session.join"]
            self.assertEqual(len(join_operations), 1)
            self.assertEqual(join_operations[0]["status"], "success")
            self.assertEqual(join_operations[0]["target_id"], "lore_lawyer")
            persisted_operations = (root / "live-agent-runs" / "operations.jsonl").read_text(encoding="utf-8")
            operation_blob = json.dumps(operations, ensure_ascii=False)
            for blob in (operation_blob, persisted_operations):
                self.assertNotIn("019e02af-c287-7cd1-aab7-c1e059c5ed44", blob)
                self.assertNotIn("codex-live-session.local.json", blob)
                self.assertNotIn("live-agents.codex-session.local.json", blob)

    def test_codex_session_join_refuses_meeting_with_official_progress_before_writing(self):
        class JoinSupervisor:
            def __init__(self) -> None:
                self.started = []

            def snapshot_groups(self):
                return []

            def list_groups(self):
                return []

            def start_group(self, **kwargs):
                self.started.append(kwargs)
                raise AssertionError("join should refuse before starting a group")

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            meeting_dir = root / "meetings" / "m1"
            meeting_dir.mkdir(parents=True)
            write_live_state(
                meeting_dir,
                {
                    "meeting_id": "m1",
                    "topic": "runtime",
                    "roles": [{"id": "lore_lawyer", "display_name": "설정충"}],
                    "debate_rounds": [{"id": "round_1", "status": "answered"}],
                    "live_status": "running",
                },
            )
            supervisor = JoinSupervisor()
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root, process_supervisor=supervisor))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/codex-sessions/join",
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
                with self.assertRaises(HTTPError) as caught:
                    urlopen(request, timeout=4)
                error_payload = json.loads(caught.exception.read().decode("utf-8"))
                caught.exception.close()
                with urlopen(
                    f"http://127.0.0.1:{server.server_port}/api/live-agent-operations?limit=20",
                    timeout=4,
                ) as response:
                    operations = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

            self.assertEqual(caught.exception.code, 400)
            self.assertEqual(error_payload["error"], "Codex live session join failed.")
            self.assertEqual(error_payload["details"], {"meeting_id": "m1", "role_id": "lore_lawyer"})
            self.assertEqual(supervisor.started, [])
            self.assertFalse((root / "codex-live-session.local.json").exists())
            self.assertFalse((root / "live-agents.codex-session.local.json").exists())
            operation_blob = json.dumps(operations, ensure_ascii=False)
            self.assertNotIn("019e02af-c287-7cd1-aab7-c1e059c5ed44", operation_blob)
            self.assertNotIn("codex-live-session.local.json", operation_blob)

    def test_codex_session_join_restarts_ready_group_when_selected_session_changes(self):
        class ReadyGroupSupervisor:
            def __init__(self, output_root: Path, config_path: Path) -> None:
                self.output_root = output_root
                self.stopped = []
                self.restarted = []
                self.groups = [
                    {
                        "group_id": "live-agents.codex-session.local",
                        "status": "running",
                        "meeting_id": "m1",
                        "config_path": str(config_path),
                        "server": "http://127.0.0.1:8765",
                        "agents": [{"agent_id": "codex-live-lore-lawyer"}],
                    }
                ]

            def snapshot_groups(self):
                return list(self.groups)

            def list_groups(self):
                return list(self.groups)

            def stop_group(self, group_id):
                self.stopped.append(group_id)
                self.groups[0]["status"] = "stopped"

            def restart_group(self, group_id):
                self.restarted.append(group_id)
                config = json.loads(Path(self.groups[0]["config_path"]).read_text(encoding="utf-8"))
                agent = config["agents"][0]
                connect_live_agent_payload(
                    self.output_root,
                    {
                        "agent_id": agent["agent_id"],
                        "display_name": agent.get("display_name", agent["agent_id"]),
                        "provider_kind": agent.get("provider_kind", "codex_live_session"),
                        "connection_kind": agent.get("connection_kind", "live_session"),
                        "meeting_id": "m1",
                        "session_id": agent.get("session_id", ""),
                    },
                )
                heartbeat_live_agent(self.output_root, agent["agent_id"], status="online", metadata={"session_id": agent.get("session_id", "")})
                self.groups[0]["status"] = "running"
                return dict(self.groups[0])

            def start_group(self, **kwargs):
                raise AssertionError("ready session drift should restart, not start")

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            meeting_dir = root / "meetings" / "m1"
            meeting_dir.mkdir(parents=True)
            write_live_state(
                meeting_dir,
                {
                    "meeting_id": "m1",
                    "topic": "runtime",
                    "roles": [{"id": "lore_lawyer", "display_name": "설정충"}],
                    "provider_configs": {"codex-live": {"id": "codex-live", "kind": "codex_live_session"}},
                    "permission_profiles": {"codex_live_meeting_readonly": {"id": "codex_live_meeting_readonly", "meeting_read": True}},
                    "agent_bindings": [
                        {
                            "agent_id": "codex-live-lore-lawyer",
                            "role_id": "lore_lawyer",
                            "provider_id": "codex-live",
                            "permission_profile_id": "codex_live_meeting_readonly",
                            "join_mode": "current_session",
                            "session_id": "old-session",
                        }
                    ],
                    "debate_rounds": [],
                    "live_status": "running",
                },
            )
            live_agent_config = root / "live-agents.codex-session.local.json"
            live_agent_config.write_text(
                json.dumps(
                    {
                        "agents": [
                            {
                                "agent_id": "codex-live-lore-lawyer",
                                "provider_kind": "codex_live_session",
                                "connection_kind": "live_session",
                                "meeting_id": "m1",
                                "session_id": "old-session",
                            }
                        ]
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            connect_live_agent_payload(
                root,
                {
                    "agent_id": "codex-live-lore-lawyer",
                    "display_name": "Codex Lore",
                    "provider_kind": "codex_live_session",
                    "connection_kind": "live_session",
                    "meeting_id": "m1",
                    "session_id": "old-session",
                },
            )
            heartbeat_live_agent(root, "codex-live-lore-lawyer", status="online", metadata={"session_id": "old-session"})
            supervisor = ReadyGroupSupervisor(root, live_agent_config)
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root, process_supervisor=supervisor))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/codex-sessions/join",
                    data=json.dumps(
                        {
                            "meeting_id": "m1",
                            "role_id": "lore_lawyer",
                            "session_id": "new-session",
                            "connect_timeout_seconds": 0,
                        }
                    ).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with patch(
                    "agentsassemble.live_agent_sessions.preflight_live_agent_config",
                    return_value={"status": "ok", "summary": {"agents": 1}},
                ):
                    with urlopen(request, timeout=4) as response:
                        payload = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

            self.assertEqual(payload["status"], "ready")
            self.assertEqual(payload["action"], "restart")
            self.assertEqual(supervisor.stopped, ["live-agents.codex-session.local"])
            self.assertEqual(supervisor.restarted, ["live-agents.codex-session.local"])
            live_agent = next(agent for agent in read_live_agents(root) if agent["agent_id"] == "codex-live-lore-lawyer")
            self.assertEqual(live_agent["session_id"], "new-session")

    def test_codex_session_join_refuses_existing_official_event_before_writing(self):
        class JoinSupervisor:
            def start_group(self, **kwargs):
                raise AssertionError("join should refuse before starting a group")

            def snapshot_groups(self):
                return []

            def list_groups(self):
                return []

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            meeting_dir = root / "meetings" / "m1"
            meeting_dir.mkdir(parents=True)
            original_state = {
                "meeting_id": "m1",
                "topic": "runtime",
                "roles": [{"id": "lore_lawyer", "display_name": "설정충"}],
                "debate_rounds": [],
                "live_status": "running",
            }
            write_live_state(meeting_dir, original_state)
            append_live_event(
                meeting_dir,
                {
                    "kind": "live_agent_turn_request",
                    "meeting_id": "m1",
                    "target_agent_id": "codex-live-lore-lawyer",
                    "content": "official turn already started",
                },
            )
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root, process_supervisor=JoinSupervisor()))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/codex-sessions/join",
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
                with self.assertRaises(HTTPError) as caught:
                    urlopen(request, timeout=4)
                error_payload = json.loads(caught.exception.read().decode("utf-8"))
                caught.exception.close()
            finally:
                server.shutdown()
                server.server_close()

            self.assertEqual(caught.exception.code, 400)
            self.assertEqual(error_payload["error"], "Codex live session join failed.")
            self.assertEqual(json.loads((meeting_dir / "live_state.json").read_text(encoding="utf-8")), original_state)
            self.assertFalse((root / "codex-live-session.local.json").exists())
            self.assertFalse((root / "live-agents.codex-session.local.json").exists())

    def test_codex_session_join_failure_records_safe_operation_without_session_or_config_paths(self):
        class FailingJoinSupervisor:
            def snapshot_groups(self):
                return []

            def list_groups(self):
                return []

            def start_group(self, **kwargs):
                raise ValueError(
                    "failed for 019e02af-c287-7cd1-aab7-c1e059c5ed44 "
                    "codex-live-session.local.json live-agents.codex-session.local.json"
                )

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            meeting_dir = root / "meetings" / "m1"
            meeting_dir.mkdir(parents=True)
            write_live_state(
                meeting_dir,
                {
                    "meeting_id": "m1",
                    "topic": "runtime",
                    "roles": [{"id": "lore_lawyer", "display_name": "설정충"}],
                    "debate_rounds": [],
                    "live_status": "running",
                },
            )
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root, process_supervisor=FailingJoinSupervisor()))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/codex-sessions/join",
                    data=json.dumps(
                        {
                            "meeting_id": "m1",
                            "role_id": "lore_lawyer",
                            "session_id": "019e02af-c287-7cd1-aab7-c1e059c5ed44",
                            "connect_timeout_seconds": 0,
                        }
                    ).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with patch(
                    "agentsassemble.live_agent_sessions.preflight_live_agent_config",
                    return_value={"status": "ok", "summary": {"agents": 1}},
                ):
                    with self.assertRaises(HTTPError) as caught:
                        urlopen(request, timeout=4)
                error_payload = json.loads(caught.exception.read().decode("utf-8"))
                caught.exception.close()
                persisted_operations = (root / "live-agent-runs" / "operations.jsonl").read_text(encoding="utf-8")
            finally:
                server.shutdown()
                server.server_close()

            self.assertEqual(caught.exception.code, 400)
            self.assertEqual(error_payload["error"], "Codex live session join failed.")
            self.assertEqual(error_payload["details"], {"meeting_id": "m1", "role_id": "lore_lawyer"})
            response_blob = json.dumps(error_payload, ensure_ascii=False)
            for blob in (response_blob, persisted_operations):
                self.assertNotIn("019e02af-c287-7cd1-aab7-c1e059c5ed44", blob)
                self.assertNotIn("codex-live-session.local.json", blob)
                self.assertNotIn("live-agents.codex-session.local.json", blob)

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
                stale_restart_after_seconds=0.0,
            ):
                self.started.append(
                    {
                        "config_path": config_path,
                        "server": server,
                        "group_id": group_id,
                        "auto_restart": auto_restart,
                        "max_restarts": max_restarts,
                        "restart_backoff_seconds": restart_backoff_seconds,
                        "stale_restart_after_seconds": stale_restart_after_seconds,
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
                    "stale_restart_after_seconds": stale_restart_after_seconds,
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
                record["offline"] = {
                    "expected": 1,
                    "offline": 1,
                    "skipped": 0,
                    "offline_agent_ids": ["local-a"],
                    "attention": [],
                }
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
                            "stale_restart_after_seconds": 240,
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
            self.assertEqual(stopped["group"]["offline"]["offline_agent_ids"], ["local-a"])
            self.assertEqual(restarted["group"]["status"], "running")
            self.assertEqual(restarted["group"]["pid"], 9876)
            self.assertEqual(restarted["group"]["agents"][0]["display_name"], "Local A")
            self.assertEqual(supervisor.started[0]["server"], f"http://127.0.0.1:{server.server_port}")
            self.assertEqual(supervisor.started[0]["auto_restart"], True)
            self.assertEqual(supervisor.started[0]["max_restarts"], 2)
            self.assertEqual(supervisor.started[0]["restart_backoff_seconds"], 1.5)
            self.assertEqual(supervisor.started[0]["stale_restart_after_seconds"], 240.0)
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
            self.assertIn('"offline_agent_count": 1', operation_text)
            self.assertIn('"offline_expected_agent_count": 1', operation_text)
            self.assertIn('"offline_agent_ids": ["local-a"]', operation_text)
            self.assertNotIn(str(config_path), operation_text)
            self.assertNotIn(f"http://127.0.0.1:{server.server_port}", operation_text)

    def test_live_agent_process_stop_running_endpoint_records_sanitized_operation(self):
        class FakeSupervisor:
            def __init__(self):
                self.groups = [
                    {"group_id": "crew-a", "status": "running", "pid": 1111, "config_path": "/tmp/secret-a.json"},
                    {"group_id": "crew-b", "status": "restarting", "pid": None, "config_path": "/tmp/secret-b.json"},
                    {"group_id": "old-crew", "status": "unknown", "pid": None, "config_path": "/tmp/secret-c.json"},
                ]
                self.stopped_running = False

            def list_groups(self):
                return list(self.groups)

            def stop_running_groups(self):
                self.stopped_running = True
                self.groups = [
                    {"group_id": "crew-a", "status": "stopped", "pid": None, "config_path": "/tmp/secret-a.json"},
                    {"group_id": "crew-b", "status": "stopped", "pid": None, "config_path": "/tmp/secret-b.json"},
                    {"group_id": "old-crew", "status": "unknown", "pid": None, "config_path": "/tmp/secret-c.json"},
                ]
                return {
                    "stopped_count": 2,
                    "failed_count": 0,
                    "skipped_count": 1,
                    "stopped": [
                        {
                            **self.groups[0],
                            "offline": {
                                "expected": 1,
                                "offline": 1,
                                "skipped": 0,
                                "offline_agent_ids": ["agent-a"],
                                "attention": [],
                            },
                        },
                        {
                            **self.groups[1],
                            "offline": {
                                "expected": 1,
                                "offline": 0,
                                "skipped": 1,
                                "offline_agent_ids": [],
                                "attention": [{"agent_id": "agent-b", "status": "wrong_meeting"}],
                            },
                        },
                    ],
                    "failed": [],
                    "skipped": self.groups[2:],
                }

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            supervisor = FakeSupervisor()
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root, process_supervisor=supervisor))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/live-agent-processes/stop-running",
                    data=b"{}",
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urlopen(request, timeout=4) as response:
                    payload = json.loads(response.read().decode("utf-8"))
                with urlopen(
                    f"http://127.0.0.1:{server.server_port}/api/live-agent-operations?limit=10",
                    timeout=4,
                ) as response:
                    operations = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

            self.assertTrue(supervisor.stopped_running)
            self.assertEqual(payload["result"]["stopped_count"], 2)
            self.assertEqual([group["status"] for group in payload["groups"]], ["stopped", "stopped", "unknown"])
            self.assertEqual(
                [(operation["operation"], operation["status"], operation["target_id"]) for operation in operations["operations"]],
                [("process.stop_running", "success", "running-groups")],
            )
            details = operations["operations"][0]["details"]
            self.assertEqual(details["stopped_count"], 2)
            self.assertEqual(details["failed_count"], 0)
            self.assertEqual(details["skipped_count"], 1)
            self.assertEqual(details["stopped_group_ids"], ["crew-a", "crew-b"])
            self.assertEqual(details["offline_agent_count"], 1)
            self.assertEqual(details["offline_expected_agent_count"], 2)
            self.assertEqual(details["offline_agent_ids"], ["agent-a"])
            self.assertEqual(details["offline_attention"], ["agent-b:wrong_meeting"])
            operation_text = json.dumps(operations, ensure_ascii=False)
            self.assertNotIn("/tmp/secret-a.json", operation_text)
            self.assertNotIn("/tmp/secret-b.json", operation_text)

    def test_live_agent_process_events_endpoint_returns_sanitized_tail_without_operation_record(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            path = root / "live-agent-runs" / "events.jsonl"
            path.parent.mkdir(parents=True)
            path.write_text(
                "\n".join(
                    [
                        json.dumps(
                            {
                                "timestamp": "2026-05-17T12:00:00+00:00",
                                "group_id": "crew",
                                "event_type": "started",
                                "status": "running",
                                "pid": 1234,
                                "server": "http://room.local",
                                "config_path": "/tmp/live-agents.json",
                            }
                        ),
                        json.dumps(
                            {
                                "timestamp": "2026-05-17T12:01:00+00:00",
                                "group_id": "other",
                                "event_type": "started",
                                "status": "running",
                                "pid": 2234,
                            }
                        ),
                        json.dumps(
                            {
                                "timestamp": "2026-05-17T12:02:00+00:00",
                                "group_id": "crew",
                                "event_type": "restart_scheduled",
                                "status": "restarting",
                                "returncode": 2,
                                "offline": {
                                    "expected": 2,
                                    "offline": 1,
                                    "skipped": 1,
                                    "offline_agent_ids": ["agent-a"],
                                    "attention": [{"agent_id": "agent-b", "status": "wrong_meeting"}],
                                },
                                "prompt": "secret prompt",
                                "log_tail": "provider output",
                            }
                        ),
                    ]
                ),
                encoding="utf-8",
            )
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with urlopen(
                    f"http://127.0.0.1:{server.server_port}/api/live-agent-process-events?group_id=crew&limit=2&scan_limit=4",
                    timeout=4,
                ) as response:
                    payload = json.loads(response.read().decode("utf-8"))
                with urlopen(
                    f"http://127.0.0.1:{server.server_port}/api/live-agent-operations?limit=10",
                    timeout=4,
                ) as response:
                    operations = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

        self.assertEqual([event["event_type"] for event in payload["events"]], ["started", "restart_scheduled"])
        self.assertEqual(payload["events"][1]["offline"]["offline_agent_ids"], ["agent-a"])
        self.assertEqual(payload["limit"], 2)
        self.assertEqual(payload["group_id"], "crew")
        self.assertEqual(payload["scan_limit"], 4)
        self.assertEqual(payload["scanned_event_count"], 3)
        self.assertEqual(payload["truncated"], False)
        payload_text = json.dumps(payload, ensure_ascii=False)
        self.assertNotIn("other", json.dumps([event["group_id"] for event in payload["events"]]))
        self.assertNotIn("http://room.local", payload_text)
        self.assertNotIn("config_path", payload_text)
        self.assertNotIn("prompt", payload_text)
        self.assertNotIn("log_tail", payload_text)
        self.assertEqual(operations["operations"], [])

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

    def test_live_agent_process_start_redacts_sensitive_error_body(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            missing_config = root / "private" / "live-agents.secret.json"
            supervisor = LiveAgentProcessSupervisor(root)
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root, process_supervisor=supervisor))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/live-agent-processes/start",
                    data=json.dumps({"config_path": str(missing_config), "group_id": "crew"}).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                try:
                    urlopen(request, timeout=4)
                except HTTPError as error:
                    error_code = error.code
                    body = json.loads(error.read().decode("utf-8"))
                    error.close()
                else:
                    self.fail("missing config should return HTTP 400")
                with urlopen(
                    f"http://127.0.0.1:{server.server_port}/api/live-agent-operations",
                    timeout=4,
                ) as response:
                    operations = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()
                supervisor.close()

            response_text = json.dumps(body, ensure_ascii=False)
            operation_text = json.dumps(operations, ensure_ascii=False)
            self.assertEqual(error_code, 400)
            self.assertEqual(body["error"], "Resident process group failed to start: details redacted.")
            self.assertNotIn("live-agents.secret.json", response_text)
            self.assertNotIn(str(missing_config), response_text)
            self.assertNotIn("live-agents.secret.json", operation_text)
            self.assertNotIn(str(missing_config), operation_text)

    def test_live_agent_process_restart_redacts_sensitive_error_body(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            missing_config = root / "private" / "live-agents.secret.json"
            state_dir = root / "live-agent-runs"
            state_dir.mkdir(parents=True)
            (state_dir / "processes.json").write_text(
                json.dumps(
                    {
                        "groups": [
                            {
                                "group_id": "crew",
                                "status": "error",
                                "pid": None,
                                "meeting_id": "",
                                "config_path": str(missing_config),
                                "server": "http://room.local",
                                "log_path": "",
                                "started_at": "",
                                "stopped_at": "",
                                "returncode": 2,
                                "last_error": "",
                                "auto_restart": False,
                                "restart_count": 0,
                                "max_restarts": 0,
                                "restart_backoff_seconds": 5,
                                "stale_restart_after_seconds": 0,
                                "next_restart_at": "",
                                "diagnostic": False,
                                "agents": [],
                                "recovered_from_status": "",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            supervisor = LiveAgentProcessSupervisor(root)
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
                    body = json.loads(error.read().decode("utf-8"))
                    error.close()
                else:
                    self.fail("missing persisted config should return HTTP 400")
                with urlopen(
                    f"http://127.0.0.1:{server.server_port}/api/live-agent-operations",
                    timeout=4,
                ) as response:
                    operations = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()
                supervisor.close()

            response_text = json.dumps(body, ensure_ascii=False)
            operation_text = json.dumps(operations, ensure_ascii=False)
            self.assertEqual(error_code, 400)
            self.assertEqual(body["error"], "Resident process group failed to restart: details redacted.")
            self.assertEqual(body["group_id"], "crew")
            self.assertNotIn("live-agents.secret.json", response_text)
            self.assertNotIn(str(missing_config), response_text)
            self.assertNotIn("live-agents.secret.json", operation_text)
            self.assertNotIn(str(missing_config), operation_text)

    def test_live_agent_process_recover_redacts_sensitive_error_body(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            missing_config = root / "private" / "live-agents.secret.json"
            state_dir = root / "live-agent-runs"
            state_dir.mkdir(parents=True)
            (state_dir / "processes.json").write_text(
                json.dumps(
                    {
                        "groups": [
                            {
                                "group_id": "crew",
                                "status": "error",
                                "pid": None,
                                "meeting_id": "",
                                "config_path": str(missing_config),
                                "server": "http://room.local",
                                "log_path": "",
                                "started_at": "",
                                "stopped_at": "",
                                "returncode": 2,
                                "last_error": "",
                                "auto_restart": False,
                                "restart_count": 0,
                                "max_restarts": 0,
                                "restart_backoff_seconds": 5,
                                "stale_restart_after_seconds": 0,
                                "next_restart_at": "",
                                "diagnostic": False,
                                "agents": [],
                                "recovered_from_status": "",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            supervisor = LiveAgentProcessSupervisor(root)
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root, process_supervisor=supervisor))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/live-agent-processes/crew/recover",
                    data=b"{}",
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                try:
                    urlopen(request, timeout=4)
                except HTTPError as error:
                    error_code = error.code
                    body = json.loads(error.read().decode("utf-8"))
                    error.close()
                else:
                    self.fail("missing persisted config should return HTTP 400")
            finally:
                server.shutdown()
                server.server_close()
                supervisor.close()

            response_text = json.dumps(body, ensure_ascii=False)
            self.assertEqual(error_code, 400)
            self.assertEqual(body["error"], "Resident process group failed to recover: details redacted.")
            self.assertEqual(body["group_id"], "crew")
            self.assertNotIn("live-agents.secret.json", response_text)
            self.assertNotIn(str(missing_config), response_text)

    def test_live_agent_process_stop_keeps_safe_not_found_error_body(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            supervisor = LiveAgentProcessSupervisor(root)
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root, process_supervisor=supervisor))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/live-agent-processes/missing-group/stop",
                    data=b"{}",
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                try:
                    urlopen(request, timeout=4)
                except HTTPError as error:
                    error_code = error.code
                    body = json.loads(error.read().decode("utf-8"))
                    error.close()
                else:
                    self.fail("missing group should return HTTP 400")
            finally:
                server.shutdown()
                server.server_close()
                supervisor.close()

            self.assertEqual(error_code, 400)
            self.assertEqual(body["error"], "Live agent group missing-group was not found.")
            self.assertEqual(body["group_id"], "missing-group")

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

    def test_live_agent_process_recover_records_safe_operation(self):
        class FakeRecoverySupervisor:
            def __init__(self):
                self.recovered = []
                self.group = {
                    "group_id": "crew",
                    "status": "unknown",
                    "pid": None,
                    "config_path": "/private/live-agents.json",
                    "server": "http://secret-room.local",
                    "auto_restart": True,
                    "restart_count": 1,
                    "max_restarts": 3,
                    "recovered_from_status": "unknown",
                    "agents": [{"agent_id": "local-a", "display_name": "Local A", "connection_kind": "local_cli"}],
                }

            def recover_group(self, group_id):
                self.recovered.append(group_id)
                recovered = dict(self.group)
                recovered["status"] = "running"
                recovered["pid"] = 6789
                recovered["recent_events"] = [
                    {"event_type": "recovered", "status": "running", "previous_status": "unknown"}
                ]
                self.group = recovered
                return recovered

            def list_groups(self):
                return [self.group]

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            supervisor = FakeRecoverySupervisor()
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root, process_supervisor=supervisor))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/live-agent-processes/crew/recover",
                    data=b"{}",
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urlopen(request, timeout=4) as response:
                    recovered = json.loads(response.read().decode("utf-8"))
                with urlopen(
                    f"http://127.0.0.1:{server.server_port}/api/live-agent-operations?limit=10",
                    timeout=4,
                ) as response:
                    operations = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

            self.assertEqual(supervisor.recovered, ["crew"])
            self.assertEqual(recovered["group"]["status"], "running")
            self.assertEqual(recovered["group"]["recovered_from_status"], "unknown")
            self.assertEqual(operations["operations"][0]["operation"], "process.recover")
            self.assertEqual(operations["operations"][0]["status"], "success")
            self.assertEqual(operations["operations"][0]["details"]["previous_status"], "unknown")
            operation_text = json.dumps(operations, ensure_ascii=False)
            self.assertNotIn("/private/live-agents.json", operation_text)
            self.assertNotIn("secret-room.local", operation_text)

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
                {"smoke local_cli ok", "smoke live_session ok", "smoke remote_bridge ok"},
            )
            processes = json.loads((root / "live-agent-runs" / "processes.json").read_text(encoding="utf-8"))
            group = next(item for item in processes["groups"] if item["group_id"] == "gui-smoke")
            self.assertEqual(group["status"], "stopped")
            self.assertIn(("smoke.run", "success", "gui-smoke"), [
                (operation["operation"], operation["status"], operation["target_id"])
                for operation in operations["operations"]
            ])
            persisted_text = json.dumps({"processes": processes, "operations": operations}, ensure_ascii=False)
            self.assertNotIn("agentsassemble-smoke-token", persisted_text)
            self.assertNotIn("auth_ref", persisted_text)

    def test_live_agent_session_smoke_endpoint_records_safe_operation(self):
        smoke_result = {
            "status": "ok",
            "meeting_id": "session-smoke-meeting",
            "group_id": "session-smoke",
            "agent_ids": [
                "session-smoke-local-cli",
                "session-smoke-live-session",
                "session-smoke-remote-bridge",
                "session-smoke-self-service",
            ],
            "source_event_id": "probe-secret",
            "rounds_status": "answered",
            "round_count": 1,
            "answered_round_count": 1,
            "completed_round_count": 0,
            "timeout_round_count": 0,
            "skipped_round_count": 0,
            "expected_reply_count": 4,
            "self_service_official_reply_count": 1,
            "self_service_lobby_reply_count": 2,
            "self_service_post_restart_reply_count": 2,
            "self_service_post_recover_reply_count": 2,
            "self_service_soak_reply_count": 2,
            "lobby_probe_count": 2,
            "source_event_ids": ["probe-secret", "probe-secret-2"],
            "reply_count": 8,
            "post_restart_source_event_id": "post-restart-secret",
            "post_restart_source_event_ids": ["post-restart-secret", "post-restart-secret-2"],
            "post_restart_reply_count": 8,
            "post_recover_source_event_id": "post-recover-secret",
            "post_recover_source_event_ids": ["post-recover-secret", "post-recover-secret-2"],
            "post_recover_reply_count": 8,
            "soak_cycle_count": 2,
            "soak_interval_seconds": 0.5,
            "soak_check_statuses": ["ready", "ready"],
            "soak_source_event_ids": ["soak-secret", "soak-secret-2"],
            "soak_reply_count": 8,
            "soak_replies": [
                {"id": "reply-soak-local", "actor_id": "session-smoke-local-cli", "source_event_id": "soak-secret"},
                {"id": "reply-soak-session", "actor_id": "session-smoke-live-session", "source_event_id": "soak-secret"},
                {"id": "reply-soak-bridge", "actor_id": "session-smoke-remote-bridge", "source_event_id": "soak-secret"},
                {"id": "reply-soak-self-service", "actor_id": "session-smoke-self-service", "source_event_id": "soak-secret"},
            ],
            "replies": [
                {"id": "reply-local", "actor_id": "session-smoke-local-cli", "source_event_id": "probe-secret"},
                {"id": "reply-session", "actor_id": "session-smoke-live-session", "source_event_id": "probe-secret"},
                {"id": "reply-bridge", "actor_id": "session-smoke-remote-bridge", "source_event_id": "probe-secret"},
                {"id": "reply-self-service", "actor_id": "session-smoke-self-service", "source_event_id": "probe-secret"},
            ],
            "post_restart_replies": [
                {
                    "id": "reply-post-local",
                    "actor_id": "session-smoke-local-cli",
                    "source_event_id": "post-restart-secret",
                },
                {
                    "id": "reply-post-session",
                    "actor_id": "session-smoke-live-session",
                    "source_event_id": "post-restart-secret",
                },
                {
                    "id": "reply-post-bridge",
                    "actor_id": "session-smoke-remote-bridge",
                    "source_event_id": "post-restart-secret",
                },
                {
                    "id": "reply-post-self-service",
                    "actor_id": "session-smoke-self-service",
                    "source_event_id": "post-restart-secret",
                },
            ],
            "post_recover_replies": [
                {
                    "id": "reply-recover-local",
                    "actor_id": "session-smoke-local-cli",
                    "source_event_id": "post-recover-secret",
                },
                {
                    "id": "reply-recover-session",
                    "actor_id": "session-smoke-live-session",
                    "source_event_id": "post-recover-secret",
                },
                {
                    "id": "reply-recover-bridge",
                    "actor_id": "session-smoke-remote-bridge",
                    "source_event_id": "post-recover-secret",
                },
                {
                    "id": "reply-recover-self-service",
                    "actor_id": "session-smoke-self-service",
                    "source_event_id": "post-recover-secret",
                },
            ],
            "start_status": "ready",
            "check_status": "ready",
            "resume_status": "ready",
            "restart_status": "ready",
            "recover_status": "ready",
            "stop_status": "stopped",
            "post_stop_process_status": "stopped",
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "room"
            root.mkdir()
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with patch("agentsassemble.gui.run_live_agent_session_smoke", return_value=smoke_result) as session_smoke:
                    request = Request(
                        f"http://127.0.0.1:{server.server_port}/api/live-agent-session-smoke",
                        data=json.dumps(
                            {
                                "group_id": "session-smoke",
                                "meeting_id": "session-smoke-meeting",
                                "timeout": 8,
                                "lobby_probe_count": 2,
                                "soak_cycle_count": 2,
                                "soak_interval_seconds": 0.5,
                            }
                        ).encode("utf-8"),
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

        self.assertEqual(payload["status"], "ok")
        session_smoke.assert_called_once_with(
            server=f"http://127.0.0.1:{server.server_port}",
            group_id="session-smoke",
            meeting_id="session-smoke-meeting",
            timeout_seconds=8.0,
            lobby_probe_count=2,
            soak_cycle_count=2,
            soak_interval_seconds=0.5,
            request_json=ANY,
            output_root=root,
        )
        session_operations = [operation for operation in operations["operations"] if operation["operation"] == "session.smoke"]
        self.assertEqual(session_operations[-1]["status"], "success")
        self.assertEqual(session_operations[-1]["target_id"], "session-smoke")
        self.assertEqual(session_operations[-1]["details"]["meeting_id"], "session-smoke-meeting")
        self.assertEqual(session_operations[-1]["details"]["rounds_status"], "answered")
        self.assertEqual(session_operations[-1]["details"]["answered_round_count"], 1)
        self.assertEqual(session_operations[-1]["details"]["lobby_probe_count"], 2)
        self.assertEqual(session_operations[-1]["details"]["self_service_official_reply_count"], 1)
        self.assertEqual(session_operations[-1]["details"]["self_service_lobby_reply_count"], 2)
        self.assertEqual(session_operations[-1]["details"]["self_service_post_restart_reply_count"], 2)
        self.assertEqual(session_operations[-1]["details"]["self_service_post_recover_reply_count"], 2)
        self.assertEqual(session_operations[-1]["details"]["self_service_soak_reply_count"], 2)
        self.assertEqual(session_operations[-1]["details"]["reply_count"], 8)
        self.assertEqual(session_operations[-1]["details"]["post_restart_reply_count"], 8)
        self.assertEqual(session_operations[-1]["details"]["post_recover_reply_count"], 8)
        self.assertEqual(session_operations[-1]["details"]["soak_cycle_count"], 2)
        self.assertEqual(session_operations[-1]["details"]["soak_reply_count"], 8)
        self.assertEqual(session_operations[-1]["details"]["soak_check_statuses"], ["ready", "ready"])
        self.assertEqual(session_operations[-1]["details"]["resume_status"], "ready")
        self.assertEqual(session_operations[-1]["details"]["recover_status"], "ready")
        self.assertEqual(session_operations[-1]["details"]["post_stop_process_status"], "stopped")
        operation_blob = json.dumps(session_operations, ensure_ascii=False)
        self.assertNotIn("probe-secret", operation_blob)
        self.assertNotIn("probe-secret-2", operation_blob)
        self.assertNotIn("post-restart-secret", operation_blob)
        self.assertNotIn("post-restart-secret-2", operation_blob)
        self.assertNotIn("post-recover-secret", operation_blob)
        self.assertNotIn("post-recover-secret-2", operation_blob)
        self.assertNotIn("soak-secret", operation_blob)
        self.assertNotIn("soak-secret-2", operation_blob)
        self.assertNotIn("reply-local", operation_blob)
        self.assertNotIn("reply-session", operation_blob)
        self.assertNotIn("reply-bridge", operation_blob)
        self.assertNotIn("reply-self-service", operation_blob)
        self.assertNotIn("reply-post-local", operation_blob)
        self.assertNotIn("reply-post-session", operation_blob)
        self.assertNotIn("reply-post-bridge", operation_blob)
        self.assertNotIn("reply-post-self-service", operation_blob)
        self.assertNotIn("reply-recover-local", operation_blob)
        self.assertNotIn("reply-recover-session", operation_blob)
        self.assertNotIn("reply-recover-bridge", operation_blob)
        self.assertNotIn("reply-recover-self-service", operation_blob)
        self.assertNotIn("reply-soak-local", operation_blob)
        self.assertNotIn("reply-soak-session", operation_blob)
        self.assertNotIn("reply-soak-bridge", operation_blob)
        self.assertNotIn("reply-soak-self-service", operation_blob)

    def test_live_agent_session_smoke_endpoint_records_safe_failure(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "room"
            root.mkdir()
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with patch(
                    "agentsassemble.gui.run_live_agent_session_smoke",
                    side_effect=LiveAgentSmokeFailed("secret /private/live-agents.json env:SECRET_TOKEN"),
                ):
                    request = Request(
                        f"http://127.0.0.1:{server.server_port}/api/live-agent-session-smoke",
                        data=json.dumps({"group_id": "session-smoke", "meeting_id": "session-smoke-meeting"}).encode("utf-8"),
                        headers={"Content-Type": "application/json"},
                        method="POST",
                    )
                    with self.assertRaises(HTTPError) as raised:
                        urlopen(request, timeout=12)
                    error_payload = json.loads(raised.exception.read().decode("utf-8"))
                    raised.exception.close()
                with urlopen(
                    f"http://127.0.0.1:{server.server_port}/api/live-agent-operations?limit=20",
                    timeout=4,
                ) as response:
                    operations = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

        self.assertEqual(error_payload["error"], "Session smoke could not be run.")
        session_operations = [operation for operation in operations["operations"] if operation["operation"] == "session.smoke"]
        self.assertEqual(session_operations[-1]["status"], "failed")
        operation_blob = json.dumps({"error": error_payload, "operations": session_operations}, ensure_ascii=False)
        self.assertNotIn("SECRET_TOKEN", operation_blob)
        self.assertNotIn("/private/live-agents.json", operation_blob)

    def test_live_agent_session_smoke_endpoint_rejects_negative_soak_payload_before_run(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "room"
            root.mkdir()
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with patch("agentsassemble.gui.run_live_agent_session_smoke") as session_smoke:
                    request = Request(
                        f"http://127.0.0.1:{server.server_port}/api/live-agent-session-smoke",
                        data=json.dumps({"soak_cycle_count": -1, "soak_interval_seconds": -0.5}).encode("utf-8"),
                        headers={"Content-Type": "application/json"},
                        method="POST",
                    )
                    with self.assertRaises(HTTPError) as raised:
                        urlopen(request, timeout=12)
                    error_payload = json.loads(raised.exception.read().decode("utf-8"))
                    raised.exception.close()
                with urlopen(
                    f"http://127.0.0.1:{server.server_port}/api/live-agent-operations?limit=20",
                    timeout=4,
                ) as response:
                    operations = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

        session_smoke.assert_not_called()
        self.assertEqual(error_payload["error"], "Session smoke could not be run.")
        session_operations = [operation for operation in operations["operations"] if operation["operation"] == "session.smoke"]
        self.assertEqual(session_operations[-1]["status"], "failed")

    def test_live_agent_session_smoke_endpoint_runs_credential_free_session(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "room"
            root.mkdir()
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/live-agent-session-smoke",
                    data=json.dumps({"group_id": "session-smoke-api", "meeting_id": "session-smoke-api", "timeout": 8}).encode("utf-8"),
                    headers={"Content-Type": "application/json", "Host": "127.0.0.1:1"},
                    method="POST",
                )
                with urlopen(request, timeout=45) as response:
                    payload = json.loads(response.read().decode("utf-8"))
                with urlopen(f"http://127.0.0.1:{server.server_port}/api/live-agent-health", timeout=4) as response:
                    health = json.loads(response.read().decode("utf-8"))
                with urlopen(
                    f"http://127.0.0.1:{server.server_port}/api/live-agent-operations?limit=40",
                    timeout=4,
                ) as response:
                    operations = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

            self.assertEqual(payload["status"], "ok")
            self.assertEqual(payload["rounds_status"], "answered")
            self.assertEqual(payload["round_count"], 1)
            self.assertEqual(payload["answered_round_count"], 1)
            expected_agent_count = len(payload["agent_ids"])
            self.assertEqual(payload["finalization_status"], "finalized")
            self.assertEqual(payload["finalization_official_event_count"], expected_agent_count)
            self.assertEqual(payload["return_packet_event_count"], expected_agent_count)
            self.assertEqual(payload["artifact_status"], "present")
            self.assertEqual(
                payload["artifact_paths"],
                ["agenda.md", "transcript.md", "decision.md", "return_packets/"],
            )
            self.assertEqual(payload["terminal_session_supported"], terminal_sessions_supported())
            self.assertEqual(payload["terminal_session_included"], payload["terminal_session_supported"])
            self.assertEqual(payload["terminal_session_status"], "covered" if payload["terminal_session_included"] else "skipped")
            if payload["terminal_session_included"]:
                self.assertIn("session-smoke-api-terminal-session", payload["agent_ids"])
            else:
                self.assertEqual(payload["terminal_session_reason"], "pty_unavailable")
            self.assertEqual(payload["expected_reply_count"], expected_agent_count)
            self.assertEqual(payload["self_service_official_reply_count"], 1)
            self.assertEqual(payload["self_service_lobby_reply_count"], 1)
            self.assertEqual(payload["self_service_post_restart_reply_count"], 1)
            self.assertEqual(payload["self_service_post_recover_reply_count"], 1)
            self.assertEqual(payload["self_service_soak_reply_count"], 0)
            self.assertEqual(payload["reply_count"], expected_agent_count)
            self.assertEqual(payload["post_restart_reply_count"], expected_agent_count)
            self.assertEqual(payload["post_recover_reply_count"], expected_agent_count)
            self.assertEqual(payload["recover_status"], "ready")
            self.assertNotEqual(payload["post_restart_source_event_id"], payload["source_event_id"])
            self.assertNotEqual(payload["post_recover_source_event_id"], payload["post_restart_source_event_id"])
            self.assertEqual({reply["actor_id"] for reply in payload["replies"]}, set(payload["agent_ids"]))
            self.assertEqual({reply["actor_id"] for reply in payload["post_restart_replies"]}, set(payload["agent_ids"]))
            self.assertEqual({reply["actor_id"] for reply in payload["post_recover_replies"]}, set(payload["agent_ids"]))
            self.assertEqual({reply["source_event_id"] for reply in payload["post_restart_replies"]}, {payload["post_restart_source_event_id"]})
            self.assertEqual({reply["source_event_id"] for reply in payload["post_recover_replies"]}, {payload["post_recover_source_event_id"]})
            self.assertFalse(any("message" in reply for reply in payload["replies"]))
            self.assertFalse(any("message" in reply for reply in payload["post_restart_replies"]))
            self.assertFalse(any("message" in reply for reply in payload["post_recover_replies"]))
            meeting_dir = root / "meetings" / payload["meeting_id"]
            meeting = json.loads((meeting_dir / "live_state.json").read_text(encoding="utf-8"))
            self.assertTrue(meeting["diagnostic"])
            self.assertEqual(meeting["diagnostic_kind"], "session_smoke")
            live_events = read_live_events(meeting_dir, limit=None)
            official_replies = [event for event in live_events if event.get("kind") == "message" and event.get("official_record") is True]
            self.assertEqual(len(official_replies), expected_agent_count)
            lobby_replies = [event for event in read_lobby(root) if event.get("actor_id") in payload["agent_ids"]]
            self.assertEqual(len(lobby_replies), expected_agent_count * 3)
            self.assertEqual(
                len([event for event in lobby_replies if event.get("source_event_id") == payload["source_event_id"]]),
                expected_agent_count,
            )
            self.assertEqual(
                len([event for event in lobby_replies if event.get("source_event_id") == payload["post_restart_source_event_id"]]),
                expected_agent_count,
            )
            self.assertEqual(
                len([event for event in lobby_replies if event.get("source_event_id") == payload["post_recover_source_event_id"]]),
                expected_agent_count,
            )
            agents = read_live_agents(root)
            self.assertEqual({agent["diagnostic"] for agent in agents if agent["agent_id"] in payload["agent_ids"]}, {True})
            processes = json.loads((root / "live-agent-runs" / "processes.json").read_text(encoding="utf-8"))
            group = next(item for item in processes["groups"] if item["group_id"] == "session-smoke-api")
            self.assertTrue(group["diagnostic"])
            self.assertEqual(group["status"], "stopped")
            self.assertEqual(health["status"], "ok")
            self.assertEqual(health["agents"]["total"], 0)
            self.assertEqual(health["processes"]["total"], 0)
            session_operations = [operation for operation in operations["operations"] if operation["operation"] == "session.smoke"]
            self.assertEqual(session_operations[-1]["details"]["finalization_status"], "finalized")
            self.assertEqual(session_operations[-1]["details"]["finalization_official_event_count"], expected_agent_count)
            self.assertEqual(session_operations[-1]["details"]["return_packet_event_count"], expected_agent_count)
            self.assertEqual(session_operations[-1]["details"]["artifact_status"], "present")
            operation_blob = json.dumps(operations["operations"], ensure_ascii=False)
            self.assertNotIn("session smoke local_cli ok", operation_blob)
            self.assertNotIn("session smoke live_session ok", operation_blob)
            self.assertNotIn("session smoke terminal_session ok", operation_blob)
            self.assertNotIn("session smoke remote_bridge ok", operation_blob)
            self.assertNotIn("session smoke self_service ok", operation_blob)
            self.assertNotIn("agentsassemble-smoke-token", operation_blob)
            self.assertNotIn("auth_ref", operation_blob)
            self.assertNotIn("config_path", operation_blob)
            self.assertNotIn("endpoint", operation_blob)
            self.assertNotIn("log_path", operation_blob)
            self.assertNotIn("command", operation_blob)

    def test_live_agent_official_round_smoke_endpoint_runs_credential_free_round(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "room"
            root.mkdir()
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/live-agent-official-round-smoke",
                    data=json.dumps({"group_id": "round-smoke", "timeout": 8}).encode("utf-8"),
                    headers={"Content-Type": "application/json", "Host": "127.0.0.1:1"},
                    method="POST",
                )
                with urlopen(request, timeout=45) as response:
                    payload = json.loads(response.read().decode("utf-8"))
                with urlopen(
                    f"http://127.0.0.1:{server.server_port}/api/live-agent-operations?limit=20",
                    timeout=4,
                ) as response:
                    operations = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

            self.assertEqual(payload["status"], "ok")
            self.assertEqual(payload["group_id"], "round-smoke")
            self.assertEqual(payload["round_id"], "official_round_smoke")
            self.assertEqual(payload["turn_count"], 3)
            self.assertEqual(payload["answered_count"], 3)
            self.assertEqual(payload["timeout_count"], 0)
            self.assertEqual(payload["skipped_count"], 0)
            self.assertEqual(len(payload["request_event_ids"]), 3)
            self.assertEqual(len(payload["reply_event_ids"]), 3)
            meeting_dir = root / "meetings" / payload["meeting_id"]
            live_events = read_live_events(meeting_dir, limit=None)
            requests = [event for event in live_events if event.get("kind") == "live_agent_turn_request"]
            replies = [event for event in live_events if event.get("kind") == "message" and event.get("official_record") is True]
            self.assertEqual(len(requests), 3)
            self.assertEqual(len(replies), 3)
            self.assertEqual({reply["source_event_id"] for reply in replies}, {request["id"] for request in requests})
            self.assertEqual(read_lobby(root), [])
            processes = json.loads((root / "live-agent-runs" / "processes.json").read_text(encoding="utf-8"))
            group = next(item for item in processes["groups"] if item["group_id"] == "round-smoke")
            self.assertEqual(group["status"], "stopped")
            self.assertIn(("smoke.official_round", "success", "round-smoke"), [
                (operation["operation"], operation["status"], operation["target_id"])
                for operation in operations["operations"]
            ])
            operation_blob = json.dumps(operations["operations"], ensure_ascii=False)
            self.assertNotIn("smoke local_cli ok", operation_blob)
            self.assertNotIn("smoke live_session ok", operation_blob)
            self.assertNotIn("smoke remote_bridge ok", operation_blob)
            self.assertNotIn("agentsassemble-smoke-token", operation_blob)
            self.assertNotIn("auth_ref", operation_blob)
            self.assertNotIn("config_path", operation_blob)
            self.assertNotIn("endpoint", operation_blob)
            self.assertNotIn("log_path", operation_blob)
            self.assertNotIn("command", operation_blob)

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

    def test_live_agent_readiness_operation_records_safe_health_attention(self):
        class FakeSupervisor:
            def snapshot_groups(self):
                return [
                    {
                        "group_id": "orphan-group",
                        "status": "unknown",
                        "recent_events": [{"event_type": "recovered_unknown", "status": "unknown"}],
                    }
                ]

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "room"
            root.mkdir()
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root, process_supervisor=FakeSupervisor()))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with patch(
                    "agentsassemble.gui.run_live_agent_smoke",
                    return_value={"status": "ok", "group_id": "doctor-smoke", "replies": []},
                ):
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
        details = readiness_operations[-1]["details"]
        self.assertEqual(details["health_status"], "degraded")
        self.assertEqual(details["health_process_attention"], ["orphan-group"])
        self.assertEqual(
            details["health_process_reasons"],
            ["orphan-group recovered_unknown orphan running record marked unknown"],
        )
        details_blob = json.dumps(details, ensure_ascii=False)
        self.assertNotIn("recent_events", details_blob)
        self.assertNotIn("live-agents.json", details_blob)

    def test_live_agent_readiness_operation_omits_suspicious_health_attention(self):
        class FakeSupervisor:
            def snapshot_groups(self):
                return [
                    {
                        "group_id": "/private/live-agents.json",
                        "status": "unknown",
                        "recent_events": [{"event_type": "recovered_unknown", "status": "unknown"}],
                    }
                ]

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "room"
            root.mkdir()
            (root / "live_agents.json").write_text(
                json.dumps({"agents": [{"agent_id": "env:SECRET_TOKEN", "status": "offline"}]}),
                encoding="utf-8",
            )
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root, process_supervisor=FakeSupervisor()))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with patch(
                    "agentsassemble.gui.run_live_agent_smoke",
                    return_value={"status": "ok", "group_id": "doctor-smoke", "replies": []},
                ):
                    request = Request(
                        f"http://127.0.0.1:{server.server_port}/api/live-agent-readiness",
                        data=json.dumps({"group_id": "doctor-smoke", "timeout": 8}).encode("utf-8"),
                        headers={"Content-Type": "application/json", "Host": "127.0.0.1:1"},
                        method="POST",
                    )
                    with urlopen(request, timeout=12) as response:
                        json.loads(response.read().decode("utf-8"))
                with urlopen(
                    f"http://127.0.0.1:{server.server_port}/api/live-agent-operations?limit=20",
                    timeout=4,
                ) as response:
                    operations = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

        readiness_operations = [
            operation for operation in operations["operations"] if operation["operation"] == "readiness.check"
        ]
        details_blob = json.dumps(readiness_operations[-1]["details"], ensure_ascii=False)
        self.assertNotIn("live-agents.json", details_blob)
        self.assertNotIn("SECRET_TOKEN", details_blob)
        self.assertNotIn("/private", details_blob)
        self.assertNotIn("env:", details_blob)

    def test_readiness_health_operation_details_filters_sensitive_values(self):
        details = _readiness_health_operation_details(
            {
                "status": "degraded",
                "processes": {
                    "attention": ["orphan-group", "/private/live-agents.json", "env:SECRET_TOKEN"],
                    "reasons": {
                        "orphan-group": {
                            "event_type": "recovered_unknown",
                            "reason": "orphan running record marked unknown",
                        },
                        "/private/live-agents.json": {
                            "event_type": "recovered_unknown",
                            "reason": "env:SECRET_TOKEN",
                        },
                    },
                },
            }
        )

        self.assertEqual(details["health_status"], "degraded")
        self.assertEqual(details["health_process_attention"], ["orphan-group"])
        self.assertEqual(
            details["health_process_reasons"],
            ["orphan-group recovered_unknown orphan running record marked unknown"],
        )
        details_blob = json.dumps(details, ensure_ascii=False)
        self.assertNotIn("live-agents.json", details_blob)
        self.assertNotIn("SECRET_TOKEN", details_blob)
        self.assertNotIn("/private", details_blob)
        self.assertNotIn("env:", details_blob)

    def test_readiness_health_operation_details_preserves_long_session_causes(self):
        details = _readiness_health_operation_details(
            {
                "status": "degraded",
                "observations": {
                    "lobby_behind_count": 1,
                    "live_behind_count": 2,
                    "error_count": 3,
                    "attention": [
                        "resident-m1:resident-main:agent-a:lobby_cursor_behind",
                        "env:SECRET_TOKEN",
                    ],
                },
                "shared_memory": {
                    "ready_sessions": 2,
                    "with_memory": 1,
                    "attention": [
                        "resident-m1:resident-main:memory_unavailable",
                        "/private/shared-memory.json",
                    ],
                },
                "session_runs": {
                    "active": 1,
                    "retrying": 1,
                    "attention": [
                        "resident-m1:resident-main:run-a:ready:no_current_readiness",
                        "resident-m1:/private/live-agents.json:run-b:degraded:retrying",
                    ],
                },
                "session_run_monitor": {
                    "last_result_count": 1,
                    "attention": ["failed:RuntimeError", "failed:/private/monitor.json"],
                },
            }
        )

        self.assertEqual(details["health_status"], "degraded")
        self.assertEqual(
            details["health_observation_attention"],
            ["resident-m1:resident-main:agent-a:lobby_cursor_behind"],
        )
        self.assertEqual(details["health_observation_lobby_behind_count"], 1)
        self.assertEqual(details["health_observation_live_behind_count"], 2)
        self.assertEqual(details["health_observation_error_count"], 3)
        self.assertEqual(details["health_shared_memory_attention"], ["resident-m1:resident-main:memory_unavailable"])
        self.assertEqual(details["health_shared_memory_ready_sessions"], 2)
        self.assertEqual(details["health_shared_memory_with_memory"], 1)
        self.assertEqual(
            details["health_session_run_attention"],
            ["resident-m1:resident-main:run-a:ready:no_current_readiness"],
        )
        self.assertEqual(details["health_session_run_active"], 1)
        self.assertEqual(details["health_session_run_retrying"], 1)
        self.assertEqual(details["health_session_run_monitor_attention"], ["failed:RuntimeError"])
        self.assertEqual(details["health_session_run_monitor_last_result_count"], 1)
        details_blob = json.dumps(details, ensure_ascii=False)
        self.assertNotIn("SECRET_TOKEN", details_blob)
        self.assertNotIn("/private", details_blob)
        self.assertNotIn("shared-memory.json", details_blob)
        self.assertNotIn("env:", details_blob)

    def test_live_agent_readiness_operation_records_observation_health_cause(self):
        class FakeSupervisor:
            def snapshot_groups(self):
                return [
                    {
                        "group_id": "resident-main",
                        "status": "running",
                        "meeting_id": "resident-m1",
                        "agents": [{"agent_id": "agent-a"}],
                    }
                ]

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "room"
            root.mkdir()
            _write_health_resident_meeting(root, agent_ids=["agent-a"])
            _write_lobby_jsonl_event(
                root,
                event_id="latest-lobby-event",
                actor_id="human",
                created_at="2026-05-21T10:00:00+00:00",
            )
            connect_live_agent(
                root,
                {
                    "agent_id": "agent-a",
                    "display_name": "Agent A",
                    "provider_kind": "local_cli",
                    "connection_kind": "local_cli",
                    "meeting_id": "resident-m1",
                    "last_observed_event_id": "older-lobby-event",
                },
            )
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root, process_supervisor=FakeSupervisor()))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with patch(
                    "agentsassemble.gui.run_live_agent_smoke",
                    return_value={"status": "ok", "group_id": "doctor-smoke", "replies": []},
                ):
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
        details = readiness_operations[-1]["details"]
        self.assertEqual(details["health_status"], "degraded")
        self.assertEqual(
            details["health_observation_attention"],
            ["resident-m1:resident-main:agent-a:lobby_cursor_behind"],
        )
        self.assertEqual(details["health_observation_lobby_behind_count"], 1)
        details_blob = json.dumps(details, ensure_ascii=False)
        self.assertNotIn("stale event text", details_blob)
        self.assertNotIn("latest-lobby-event", details_blob)

    def test_session_start_operation_details_drops_unrecognized_ensure_reason(self):
        details = _session_start_operation_details(
            {
                "status": "ready",
                "meeting_id": "resident-m1",
                "group_id": "resident-main",
                "action": "restart",
                "ensure_reason": "session drift from old-session to /private/new-session env:SECRET_TOKEN",
                "connection": {"expected": 1, "connected": 1},
                "process": {"status": "running"},
            }
        )

        self.assertEqual(details["ensure_action"], "restart")
        self.assertNotIn("ensure_reason", details)
        details_blob = json.dumps(details, ensure_ascii=False)
        self.assertNotIn("old-session", details_blob)
        self.assertNotIn("new-session", details_blob)
        self.assertNotIn("SECRET_TOKEN", details_blob)
        self.assertNotIn("/private", details_blob)
        self.assertNotIn("env:", details_blob)

    def test_live_agent_readiness_endpoint_runs_opt_in_targeted_probes(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "room"
            root.mkdir()
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            smoke_result = {"status": "ok", "group_id": "doctor-smoke", "source_event_id": "base-secret", "replies": []}
            probe_results = [
                {
                    "status": "ok",
                    "agent_id": "agent-a",
                    "source_event_id": "probe-source-a",
                    "reply_event_id": "reply-a",
                    "reply": {"message": "secret readiness reply"},
                },
                {
                    "status": "timeout",
                    "agent_id": "agent-b",
                    "source_event_id": "probe-source-b",
                },
                ValueError("Live agent agent-missing was not found at /Users/me/private.json env:SECRET_TOKEN."),
            ]
            try:
                with (
                    patch("agentsassemble.gui.run_live_agent_smoke", return_value=smoke_result),
                    patch("agentsassemble.gui.run_live_agent_probe", side_effect=probe_results) as probe,
                ):
                    request = Request(
                        f"http://127.0.0.1:{server.server_port}/api/live-agent-readiness",
                        data=json.dumps(
                            {
                                "group_id": "doctor-smoke",
                                "timeout": 8,
                                "probe_agent_ids": ["agent-a", "agent-b", "agent-missing"],
                            }
                        ).encode("utf-8"),
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

        self.assertEqual(payload["status"], "failed")
        self.assertEqual(
            {check["id"]: check["status"] for check in payload["checks"]},
            {
                "health": "ok",
                "smoke": "ok",
                "probe:agent-a": "ok",
                "probe:agent-b": "timeout",
                "probe:agent-missing": "failed",
            },
        )
        self.assertEqual(payload["probes"][0], {"status": "ok", "agent_id": "agent-a", "source_event_id": "probe-source-a", "reply_event_id": "reply-a"})
        self.assertEqual(payload["probes"][1], {"status": "timeout", "agent_id": "agent-b", "source_event_id": "probe-source-b"})
        self.assertEqual(
            payload["probes"][2],
            {"status": "failed", "agent_id": "agent-missing", "reason": "probe could not be run"},
        )
        serialized_payload = json.dumps(payload, ensure_ascii=False)
        self.assertNotIn("secret readiness reply", serialized_payload)
        self.assertNotIn("SECRET_TOKEN", serialized_payload)
        self.assertNotIn("/Users/me/private.json", serialized_payload)
        self.assertEqual([call.args[:2] for call in probe.call_args_list], [(root, "agent-a"), (root, "agent-b"), (root, "agent-missing")])
        self.assertEqual({call.kwargs["timeout_seconds"] for call in probe.call_args_list}, {8.0})
        readiness_operations = [
            operation for operation in operations["operations"] if operation["operation"] == "readiness.check"
        ]
        self.assertEqual(readiness_operations[-1]["status"], "failed")
        self.assertEqual(readiness_operations[-1]["details"]["result_status"], "failed")
        self.assertEqual(readiness_operations[-1]["details"]["probe_agent_ids"], ["agent-a", "agent-b", "agent-missing"])
        self.assertEqual(readiness_operations[-1]["details"]["probe_statuses"], ["agent-a:ok", "agent-b:timeout", "agent-missing:failed"])
        self.assertNotIn("secret readiness reply", json.dumps(readiness_operations, ensure_ascii=False))

    def test_live_agent_readiness_endpoint_runs_opt_in_official_round_smoke(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "room"
            root.mkdir()
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            smoke_result = {"status": "ok", "group_id": "doctor-smoke", "source_event_id": "base-secret", "replies": []}
            official_result = {
                "status": "ok",
                "group_id": "doctor-smoke",
                "meeting_id": "official-round-smoke-doctor-smoke",
                "round_id": "official_round_smoke",
                "agent_ids": ["doctor-smoke-local-cli"],
                "role_ids": ["smoke_local_cli"],
                "turn_count": 1,
                "answered_count": 1,
                "timeout_count": 0,
                "skipped_count": 0,
                "stopped": True,
                "timeout_seconds": 8.0,
                "statuses": ["answered"],
                "request_event_ids": ["request-secret"],
                "reply_event_ids": ["reply-secret"],
                "started_group": {"config_path": "/Users/me/private-live-agents.json"},
                "reply": {"message": "secret official reply"},
            }
            try:
                with (
                    patch("agentsassemble.gui.run_live_agent_smoke", return_value=smoke_result),
                    patch("agentsassemble.gui.run_live_agent_official_round_smoke", return_value=official_result) as official_smoke,
                ):
                    request = Request(
                        f"http://127.0.0.1:{server.server_port}/api/live-agent-readiness",
                        data=json.dumps(
                            {
                                "group_id": "doctor-smoke",
                                "timeout": 8,
                                "official_round_smoke": True,
                            }
                        ).encode("utf-8"),
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

        self.assertEqual(payload["status"], "ready")
        self.assertEqual(
            {check["id"]: check["status"] for check in payload["checks"]},
            {"health": "ok", "smoke": "ok", "official_round_smoke": "ok"},
        )
        self.assertEqual(
            payload["official_round_smoke"],
            {
                "status": "ok",
                "group_id": "doctor-smoke",
                "meeting_id": "official-round-smoke-doctor-smoke",
                "round_id": "official_round_smoke",
                "agent_ids": ["doctor-smoke-local-cli"],
                "role_ids": ["smoke_local_cli"],
                "turn_count": 1,
                "answered_count": 1,
                "timeout_count": 0,
                "skipped_count": 0,
                "stopped": True,
                "timeout_seconds": 8.0,
                "statuses": ["answered"],
            },
        )
        official_smoke.assert_called_once()
        self.assertEqual(official_smoke.call_args.kwargs["output_root"], root)
        self.assertEqual(official_smoke.call_args.kwargs["group_id"], "doctor-smoke")
        self.assertEqual(official_smoke.call_args.kwargs["timeout_seconds"], 8.0)
        serialized_payload = json.dumps(payload, ensure_ascii=False)
        self.assertNotIn("private-live-agents", serialized_payload)
        self.assertNotIn("secret official reply", serialized_payload)
        self.assertNotIn("base-secret", serialized_payload)
        self.assertNotIn("request-secret", serialized_payload)
        self.assertNotIn("reply-secret", serialized_payload)
        readiness_operations = [
            operation for operation in operations["operations"] if operation["operation"] == "readiness.check"
        ]
        self.assertEqual(readiness_operations[-1]["status"], "success")
        self.assertEqual(readiness_operations[-1]["details"]["official_round_smoke"], "ok")
        self.assertEqual(readiness_operations[-1]["details"]["official_round_answered_count"], 1)
        operation_blob = json.dumps(readiness_operations, ensure_ascii=False)
        self.assertNotIn("secret official reply", operation_blob)
        self.assertNotIn("base-secret", operation_blob)
        self.assertNotIn("request-secret", operation_blob)
        self.assertNotIn("reply-secret", operation_blob)

    def test_live_agent_readiness_endpoint_runs_opt_in_session_smoke(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "room"
            root.mkdir()
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            smoke_result = {"status": "ok", "group_id": "doctor-smoke", "replies": []}
            session_result = {
                "status": "ok",
                "meeting_id": "session-smoke-meeting",
                "group_id": "session-smoke",
                "agent_ids": ["session-smoke-local-cli"],
                "terminal_session_supported": False,
                "terminal_session_included": False,
                "terminal_session_status": "skipped",
                "terminal_session_reason": "pty_unavailable",
                "lobby_probe_count": 1,
                "expected_reply_count": 3,
                "self_service_official_reply_count": 1,
                "self_service_lobby_reply_count": 1,
                "self_service_post_restart_reply_count": 1,
                "self_service_post_recover_reply_count": 1,
                "self_service_soak_reply_count": 2,
                "reply_count": 3,
                "post_restart_reply_count": 3,
                "post_recover_reply_count": 3,
                "soak_cycle_count": 2,
                "soak_reply_count": 6,
                "soak_check_statuses": ["ready", "ready"],
                "rounds_status": "answered",
                "answered_round_count": 1,
                "finalization_status": "finalized",
                "finalization_official_event_count": 4,
                "return_packet_event_count": 4,
                "artifact_status": "present",
                "artifact_paths": ["agenda.md", "transcript.md", "decision.md", "return_packets/"],
                "start_status": "ready",
                "check_status": "ready",
                "resume_status": "ready",
                "restart_status": "ready",
                "recover_status": "ready",
                "stop_status": "stopped",
                "post_stop_process_status": "stopped",
                "source_event_id": "secret-source",
                "post_recover_source_event_id": "secret-recover-source",
                "soak_source_event_ids": ["secret-soak-source"],
                "replies": [{"id": "secret-reply", "message": "secret session reply"}],
                "soak_replies": [{"id": "secret-soak-reply", "message": "secret soak reply"}],
                "started_group": {"config_path": "/Users/me/private-live-agents.json"},
            }
            try:
                with (
                    patch("agentsassemble.gui.run_live_agent_smoke", return_value=smoke_result),
                    patch("agentsassemble.gui.run_live_agent_session_smoke", return_value=session_result) as session_smoke,
                ):
                    request = Request(
                        f"http://127.0.0.1:{server.server_port}/api/live-agent-readiness",
                        data=json.dumps(
                            {
                                "group_id": "doctor-smoke",
                                "timeout": 8,
                                "session_smoke": True,
                                "session_smoke_soak_cycle_count": 2,
                                "session_smoke_soak_interval_seconds": 0.5,
                            }
                        ).encode("utf-8"),
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

        self.assertEqual(payload["status"], "ready")
        self.assertEqual(
            {check["id"]: check["status"] for check in payload["checks"]},
            {"health": "ok", "smoke": "ok", "session_smoke": "ok"},
        )
        self.assertEqual(
            payload["session_smoke"],
            {
                "status": "ok",
                "meeting_id": "session-smoke-meeting",
                "group_id": "session-smoke",
                "agent_ids": ["session-smoke-local-cli"],
                "terminal_session_supported": False,
                "terminal_session_included": False,
                "terminal_session_status": "skipped",
                "terminal_session_reason": "pty_unavailable",
                "rounds_status": "answered",
                "answered_round_count": 1,
                "finalization_status": "finalized",
                "finalization_official_event_count": 4,
                "return_packet_event_count": 4,
                "artifact_status": "present",
                "artifact_paths": ["agenda.md", "transcript.md", "decision.md", "return_packets/"],
                "lobby_probe_count": 1,
                "expected_reply_count": 3,
                "self_service_official_reply_count": 1,
                "self_service_lobby_reply_count": 1,
                "self_service_post_restart_reply_count": 1,
                "self_service_post_recover_reply_count": 1,
                "self_service_soak_reply_count": 2,
                "reply_count": 3,
                "post_restart_reply_count": 3,
                "post_recover_reply_count": 3,
                "soak_cycle_count": 2,
                "soak_reply_count": 6,
                "soak_check_statuses": ["ready", "ready"],
                "start_status": "ready",
                "check_status": "ready",
                "resume_status": "ready",
                "restart_status": "ready",
                "recover_status": "ready",
                "stop_status": "stopped",
                "post_stop_process_status": "stopped",
            },
        )
        session_smoke.assert_called_once()
        self.assertEqual(session_smoke.call_args.kwargs["output_root"], root)
        self.assertEqual(session_smoke.call_args.kwargs["group_id"], "")
        self.assertEqual(session_smoke.call_args.kwargs["meeting_id"], "")
        self.assertEqual(session_smoke.call_args.kwargs["timeout_seconds"], 8.0)
        self.assertEqual(session_smoke.call_args.kwargs["soak_cycle_count"], 2)
        self.assertEqual(session_smoke.call_args.kwargs["soak_interval_seconds"], 0.5)
        serialized_payload = json.dumps(payload, ensure_ascii=False)
        self.assertNotIn("secret-source", serialized_payload)
        self.assertNotIn("secret-recover-source", serialized_payload)
        self.assertNotIn("secret-soak-source", serialized_payload)
        self.assertNotIn("secret session reply", serialized_payload)
        self.assertNotIn("secret soak reply", serialized_payload)
        self.assertNotIn("private-live-agents", serialized_payload)
        readiness_operations = [
            operation for operation in operations["operations"] if operation["operation"] == "readiness.check"
        ]
        self.assertEqual(readiness_operations[-1]["status"], "success")
        self.assertEqual(readiness_operations[-1]["details"]["session_smoke"], "ok")
        self.assertEqual(readiness_operations[-1]["details"]["session_smoke_terminal_session_status"], "skipped")
        self.assertFalse(readiness_operations[-1]["details"]["session_smoke_terminal_session_included"])
        self.assertEqual(readiness_operations[-1]["details"]["session_smoke_finalization_status"], "finalized")
        self.assertEqual(readiness_operations[-1]["details"]["session_smoke_finalization_official_event_count"], 4)
        self.assertEqual(readiness_operations[-1]["details"]["session_smoke_return_packet_event_count"], 4)
        self.assertEqual(readiness_operations[-1]["details"]["session_smoke_artifact_status"], "present")
        self.assertEqual(readiness_operations[-1]["details"]["session_smoke_self_service_official_reply_count"], 1)
        self.assertEqual(readiness_operations[-1]["details"]["session_smoke_self_service_lobby_reply_count"], 1)
        self.assertEqual(readiness_operations[-1]["details"]["session_smoke_self_service_post_recover_reply_count"], 1)
        self.assertEqual(readiness_operations[-1]["details"]["session_smoke_self_service_soak_reply_count"], 2)
        self.assertEqual(readiness_operations[-1]["details"]["session_smoke_reply_count"], 3)
        self.assertEqual(readiness_operations[-1]["details"]["session_smoke_post_recover_reply_count"], 3)
        self.assertEqual(readiness_operations[-1]["details"]["session_smoke_soak_cycle_count"], 2)
        self.assertEqual(readiness_operations[-1]["details"]["session_smoke_soak_reply_count"], 6)
        self.assertEqual(readiness_operations[-1]["details"]["session_smoke_soak_check_statuses"], ["ready", "ready"])
        self.assertEqual(readiness_operations[-1]["details"]["session_smoke_post_stop_process_status"], "stopped")
        readiness_blob = json.dumps(readiness_operations, ensure_ascii=False)
        self.assertNotIn("secret session reply", readiness_blob)
        self.assertNotIn("secret soak reply", readiness_blob)
        self.assertNotIn("secret-soak-source", readiness_blob)

    def test_live_agent_readiness_endpoint_sanitizes_session_smoke_failure(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "room"
            root.mkdir()
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with (
                    patch("agentsassemble.gui.run_live_agent_smoke", return_value={"status": "ok", "group_id": "doctor-smoke", "replies": []}),
                    patch(
                        "agentsassemble.gui.run_live_agent_session_smoke",
                        side_effect=ValueError("config_path=/Users/me/private-live-agents.json token=SECRET"),
                    ),
                ):
                    request = Request(
                        f"http://127.0.0.1:{server.server_port}/api/live-agent-readiness",
                        data=json.dumps({"group_id": "doctor-smoke", "timeout": 8, "session_smoke": True}).encode("utf-8"),
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

        self.assertEqual(payload["status"], "failed")
        self.assertEqual(payload["session_smoke"]["status"], "failed")
        self.assertEqual(payload["session_smoke"]["error"], "session smoke could not be run")
        serialized_payload = json.dumps(payload, ensure_ascii=False)
        serialized_operations = json.dumps(operations, ensure_ascii=False)
        for secret in ("SECRET", "private-live-agents", "/Users/me", "config_path", "token="):
            self.assertNotIn(secret, serialized_payload)
            self.assertNotIn(secret, serialized_operations)

    def test_live_agent_real_session_smoke_endpoint_rejects_missing_approval_without_launching(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "room"
            root.mkdir()
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/live-agent-real-session-smoke",
                    data=json.dumps(
                        {
                            "group_id": "real-smoke",
                            "meeting_id": "real-smoke-meeting",
                            "live_agent_config_path": "/Users/me/private/live-agents.real.json",
                        }
                    ).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with patch("agentsassemble.gui.run_live_agent_real_session_smoke") as real_smoke:
                    real_smoke.side_effect = AssertionError("missing approval must not start a real smoke")
                    with self.assertRaises(HTTPError) as raised:
                        urlopen(request, timeout=4)
                body = raised.exception.read().decode("utf-8")
                error_payload = json.loads(body)
                raised.exception.close()
                with urlopen(
                    f"http://127.0.0.1:{server.server_port}/api/live-agent-operations?limit=20",
                    timeout=4,
                ) as response:
                    operations = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

        self.assertEqual(raised.exception.code, 400)
        self.assertIn("requires current operator approval", error_payload["error"])
        serialized = json.dumps({"error": error_payload, "operations": operations}, ensure_ascii=False)
        self.assertNotIn("/Users/me", serialized)
        self.assertNotIn("live-agents.real.json", serialized)

    def test_live_agent_real_session_smoke_endpoint_rejects_string_false_approval(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "room"
            root.mkdir()
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/live-agent-real-session-smoke",
                    data=json.dumps(
                        {
                            "group_id": "real-smoke",
                            "meeting_id": "real-smoke-meeting",
                            "live_agent_config_path": "/Users/me/private/live-agents.real.json",
                            "approve_real_providers": "false",
                        }
                    ).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with patch("agentsassemble.gui.run_live_agent_real_session_smoke") as real_smoke:
                    real_smoke.side_effect = AssertionError("string false approval must not start a real smoke")
                    with self.assertRaises(HTTPError) as raised:
                        urlopen(request, timeout=4)
                raised.exception.close()
            finally:
                server.shutdown()
                server.server_close()

        self.assertEqual(raised.exception.code, 400)

    def test_live_agent_real_session_smoke_endpoint_requires_matching_configs(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "room"
            root.mkdir()
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/live-agent-real-session-smoke",
                    data=json.dumps(
                        {
                            "group_id": "real-smoke",
                            "meeting_id": "real-smoke-meeting",
                            "live_agent_config_path": "/Users/me/private/live-agents.real.json",
                            "approve_real_providers": True,
                        }
                    ).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with patch("agentsassemble.gui.run_live_agent_real_session_smoke") as real_smoke:
                    real_smoke.side_effect = AssertionError("missing matching configs must not start a real smoke")
                    with self.assertRaises(HTTPError) as raised:
                        urlopen(request, timeout=4)
                body = raised.exception.read().decode("utf-8")
                error_payload = json.loads(body)
                raised.exception.close()
                with urlopen(
                    f"http://127.0.0.1:{server.server_port}/api/live-agent-operations?limit=20",
                    timeout=4,
                ) as response:
                    operations = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

        self.assertEqual(raised.exception.code, 400)
        self.assertIn("requires explicit", error_payload["error"])
        serialized = json.dumps({"error": error_payload, "operations": operations}, ensure_ascii=False)
        self.assertNotIn("/Users/me", serialized)
        self.assertNotIn("live-agents.real.json", serialized)

    def test_live_agent_real_session_smoke_endpoint_returns_safe_approved_result(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "room"
            root.mkdir()
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            smoke_result = {
                "status": "ok",
                "meeting_id": "real-smoke-meeting",
                "group_id": "real-smoke",
                "approval_required": True,
                "approved": True,
                "diagnostic": True,
                "start_status": "ready",
                "expected_agent_count": 2,
                "connected_agent_count": 2,
                "reply_probe_status": "ok",
                "reply_probe_count": 2,
                "reply_probe_ok_count": 2,
                "stop_status": "stopped",
                "post_stop_process_status": "stopped",
                "live_agent_config_path": "/Users/me/private/live-agents.real.json",
                "reply_probe": {"replies": [{"message": "secret provider output"}]},
                "process": {"config_path": "/Users/me/private/live-agents.real.json"},
            }
            try:
                with patch("agentsassemble.gui.run_live_agent_real_session_smoke", return_value=smoke_result) as real_smoke:
                    request = Request(
                        f"http://127.0.0.1:{server.server_port}/api/live-agent-real-session-smoke",
                        data=json.dumps(
                            {
                                "group_id": "real-smoke",
                                "meeting_id": "real-smoke-meeting",
                                "live_agent_config_path": "/Users/me/private/live-agents.real.json",
                                "council_config_path": "/Users/me/private/council.json",
                                "agent_config_path": "/Users/me/private/agents.json",
                                "timeout": 9,
                                "approve_real_providers": True,
                            }
                        ).encode("utf-8"),
                        headers={"Content-Type": "application/json"},
                        method="POST",
                    )
                    with urlopen(request, timeout=4) as response:
                        payload = json.loads(response.read().decode("utf-8"))
                with urlopen(
                    f"http://127.0.0.1:{server.server_port}/api/live-agent-operations?limit=20",
                    timeout=4,
                ) as response:
                    operations = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

        real_smoke.assert_called_once()
        kwargs = real_smoke.call_args.kwargs
        self.assertEqual(kwargs["server"], f"http://127.0.0.1:{server.server_port}")
        self.assertEqual(kwargs["group_id"], "real-smoke")
        self.assertEqual(kwargs["meeting_id"], "real-smoke-meeting")
        self.assertEqual(kwargs["timeout_seconds"], 9.0)
        self.assertTrue(kwargs["approve_real_providers"])
        self.assertEqual(kwargs["output_root"], root)
        self.assertEqual(
            payload,
            {
                "status": "ok",
                "meeting_id": "real-smoke-meeting",
                "group_id": "real-smoke",
                "approval_required": True,
                "approved": True,
                "diagnostic": True,
                "start_status": "ready",
                "expected_agent_count": 2,
                "connected_agent_count": 2,
                "reply_probe_status": "ok",
                "reply_probe_count": 2,
                "reply_probe_ok_count": 2,
                "stop_status": "stopped",
                "post_stop_process_status": "stopped",
            },
        )
        operations_for_smoke = [
            operation for operation in operations["operations"] if operation["operation"] == "session.real_smoke"
        ]
        self.assertEqual(operations_for_smoke[-1]["status"], "success")
        self.assertEqual(operations_for_smoke[-1]["details"]["reply_probe_ok_count"], 2)
        serialized = json.dumps({"payload": payload, "operations": operations}, ensure_ascii=False)
        self.assertNotIn("/Users/me", serialized)
        self.assertNotIn("private", serialized)
        self.assertNotIn("secret provider output", serialized)
        self.assertNotIn("config_path", serialized)

    def test_real_session_smoke_lobby_redaction_removes_probe_and_reply_text(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            probe = append_lobby_event(
                root,
                {
                    "name": "Smoke",
                    "side": "mine",
                    "message": "probe contains private prompt text",
                    "actor_id": "human",
                },
            )
            reply = append_lobby_event(
                root,
                {
                    "name": "Claude",
                    "side": "other-agent",
                    "message": "provider reply contains private output",
                    "actor_id": "claude-live",
                    "source_event_id": probe["id"],
                },
                live_agent_endpoint=True,
            )
            unrelated = append_lobby_event(
                root,
                {
                    "name": "User",
                    "side": "mine",
                    "message": "keep this visible",
                    "actor_id": "human",
                },
            )

            result = _redact_real_session_smoke_lobby_events(root, [str(probe["id"])])
            events = read_lobby(root, limit=None)

        self.assertEqual(result["probe_event_count"], 1)
        self.assertEqual(result["reply_event_count"], 1)
        by_id = {str(event["id"]): event for event in events}
        self.assertEqual(by_id[str(probe["id"])]["message"], "[redacted real session smoke probe]")
        self.assertEqual(by_id[str(reply["id"])]["message"], "[redacted real session smoke reply]")
        self.assertEqual(by_id[str(reply["id"])]["source_event_id"], probe["id"])
        self.assertTrue(by_id[str(reply["id"])]["live_agent_endpoint"])
        self.assertEqual(by_id[str(unrelated["id"])]["message"], "keep this visible")

    def test_real_session_smoke_late_reply_is_redacted_at_append_time(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            connect_live_agent(
                root,
                {
                    "agent_id": "claude-live",
                    "display_name": "Claude Live",
                    "provider_kind": "claude_code",
                    "connection_kind": "live_session",
                    "status": "online",
                },
            )
            probe = append_lobby_event(
                root,
                {
                    "name": "Smoke",
                    "side": "mine",
                    "message": "probe contains private prompt text",
                    "actor_id": "human",
                },
            )
            _redact_real_session_smoke_lobby_events(root, [str(probe["id"])])

            result = live_agent_lobby_message_payload(
                root,
                "claude-live",
                {
                    "message": "late provider reply contains private output",
                    "source_event_id": probe["id"],
                },
            )
            events = read_lobby(root, limit=None)

        self.assertEqual(result["event"]["message"], "[redacted real session smoke reply]")
        serialized = json.dumps({"result": result, "events": events}, ensure_ascii=False)
        self.assertNotIn("late provider reply contains private output", serialized)

    def test_live_agent_readiness_endpoint_rejects_negative_session_smoke_soak_payload(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "room"
            root.mkdir()
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with (
                    patch("agentsassemble.gui.run_live_agent_smoke", return_value={"status": "ok", "group_id": "doctor-smoke"}),
                    patch("agentsassemble.gui.run_live_agent_session_smoke") as session_smoke,
                ):
                    request = Request(
                        f"http://127.0.0.1:{server.server_port}/api/live-agent-readiness",
                        data=json.dumps(
                            {
                                "group_id": "doctor-smoke",
                                "session_smoke": True,
                                "session_smoke_soak_cycle_count": -1,
                                "session_smoke_soak_interval_seconds": -0.5,
                            }
                        ).encode("utf-8"),
                        headers={"Content-Type": "application/json"},
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

        session_smoke.assert_not_called()
        self.assertEqual(payload["status"], "failed")
        self.assertEqual(payload["session_smoke"]["status"], "failed")
        self.assertEqual(payload["session_smoke"]["error"], "session smoke could not be run")
        readiness_operations = [operation for operation in operations["operations"] if operation["operation"] == "readiness.check"]
        self.assertEqual(readiness_operations[-1]["status"], "failed")

    def test_live_agent_readiness_endpoint_skips_session_smoke_when_base_smoke_fails(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "room"
            root.mkdir()
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with (
                    patch("agentsassemble.gui.run_live_agent_smoke", side_effect=LiveAgentSmokeFailed("Timed out")),
                    patch("agentsassemble.gui.run_live_agent_session_smoke") as session_smoke,
                ):
                    request = Request(
                        f"http://127.0.0.1:{server.server_port}/api/live-agent-readiness",
                        data=json.dumps({"group_id": "doctor-smoke", "timeout": 8, "session_smoke": True}).encode("utf-8"),
                        headers={"Content-Type": "application/json", "Host": "127.0.0.1:1"},
                        method="POST",
                    )
                    with urlopen(request, timeout=12) as response:
                        payload = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

        self.assertEqual(payload["status"], "failed")
        self.assertEqual(payload["session_smoke"]["status"], "skipped")
        self.assertEqual(payload["session_smoke"]["reason"], "smoke did not pass")
        session_smoke.assert_not_called()

    def test_live_agent_readiness_endpoint_sanitizes_official_round_smoke_failure(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "room"
            root.mkdir()
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with (
                    patch("agentsassemble.gui.run_live_agent_smoke", return_value={"status": "ok", "group_id": "doctor-smoke", "replies": []}),
                    patch(
                        "agentsassemble.gui.run_live_agent_official_round_smoke",
                        side_effect=ValueError("config_path=/Users/me/private-live-agents.json token=SECRET"),
                    ),
                ):
                    request = Request(
                        f"http://127.0.0.1:{server.server_port}/api/live-agent-readiness",
                        data=json.dumps(
                            {
                                "group_id": "doctor-smoke",
                                "timeout": 8,
                                "official_round_smoke": True,
                            }
                        ).encode("utf-8"),
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

        self.assertEqual(payload["status"], "failed")
        self.assertEqual(payload["official_round_smoke"]["status"], "failed")
        self.assertEqual(payload["official_round_smoke"]["error"], "official round smoke could not be run")
        serialized_payload = json.dumps(payload, ensure_ascii=False)
        serialized_operations = json.dumps(operations, ensure_ascii=False)
        for secret in ("SECRET", "private-live-agents", "/Users/me", "config_path", "token="):
            self.assertNotIn(secret, serialized_payload)
            self.assertNotIn(secret, serialized_operations)

    def test_live_agent_readiness_endpoint_sanitizes_official_round_smoke_error_result(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "room"
            root.mkdir()
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with (
                    patch("agentsassemble.gui.run_live_agent_smoke", return_value={"status": "ok", "group_id": "doctor-smoke", "replies": []}),
                    patch(
                        "agentsassemble.gui.run_live_agent_official_round_smoke",
                        return_value={
                            "status": "failed",
                            "group_id": "doctor-smoke",
                            "error": "config_path=/Users/me/private-live-agents.json token=SECRET",
                        },
                    ),
                ):
                    request = Request(
                        f"http://127.0.0.1:{server.server_port}/api/live-agent-readiness",
                        data=json.dumps(
                            {
                                "group_id": "doctor-smoke",
                                "timeout": 8,
                                "official_round_smoke": True,
                            }
                        ).encode("utf-8"),
                        headers={"Content-Type": "application/json", "Host": "127.0.0.1:1"},
                        method="POST",
                    )
                    with urlopen(request, timeout=12) as response:
                        payload = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

        self.assertEqual(payload["status"], "failed")
        self.assertEqual(payload["official_round_smoke"]["error"], "official round smoke could not be run")
        serialized_payload = json.dumps(payload, ensure_ascii=False)
        for secret in ("SECRET", "private-live-agents", "/Users/me", "config_path", "token="):
            self.assertNotIn(secret, serialized_payload)

    def test_live_agent_readiness_endpoint_skips_official_round_smoke_when_base_smoke_fails(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "room"
            root.mkdir()
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with (
                    patch("agentsassemble.gui.run_live_agent_smoke", side_effect=LiveAgentSmokeFailed("Timed out")),
                    patch("agentsassemble.gui.run_live_agent_official_round_smoke") as official_smoke,
                ):
                    request = Request(
                        f"http://127.0.0.1:{server.server_port}/api/live-agent-readiness",
                        data=json.dumps(
                            {
                                "group_id": "doctor-smoke",
                                "timeout": 8,
                                "official_round_smoke": True,
                            }
                        ).encode("utf-8"),
                        headers={"Content-Type": "application/json", "Host": "127.0.0.1:1"},
                        method="POST",
                    )
                    with urlopen(request, timeout=12) as response:
                        payload = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

        self.assertEqual(payload["status"], "failed")
        self.assertEqual(payload["official_round_smoke"]["status"], "skipped")
        self.assertEqual(payload["official_round_smoke"]["reason"], "smoke did not pass")
        official_smoke.assert_not_called()

    def test_live_agent_readiness_endpoint_refuses_too_many_targeted_probes(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "room"
            root.mkdir()
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            agent_ids = [f"agent-{index}" for index in range(11)]
            try:
                with (
                    patch("agentsassemble.gui.run_live_agent_smoke", return_value={"status": "ok", "group_id": "doctor-smoke", "replies": []}),
                    patch("agentsassemble.gui.run_live_agent_probe") as probe,
                ):
                    request = Request(
                        f"http://127.0.0.1:{server.server_port}/api/live-agent-readiness",
                        data=json.dumps(
                            {
                                "group_id": "doctor-smoke",
                                "timeout": 8,
                                "probe_agent_ids": agent_ids,
                            }
                        ).encode("utf-8"),
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

        self.assertEqual(payload["status"], "failed")
        self.assertEqual(payload["probe_error"], "Too many probe agents requested; maximum is 10.")
        self.assertEqual(
            {check["id"]: check["status"] for check in payload["checks"]},
            {"health": "ok", "smoke": "ok", "probe_request_limit": "failed"},
        )
        probe.assert_not_called()
        readiness_operations = [
            operation for operation in operations["operations"] if operation["operation"] == "readiness.check"
        ]
        self.assertEqual(readiness_operations[-1]["status"], "failed")
        self.assertEqual(readiness_operations[-1]["details"]["probe_agent_ids"], agent_ids)
        self.assertEqual(readiness_operations[-1]["details"]["probe_error"], "Too many probe agents requested; maximum is 10.")

    def test_live_agent_readiness_endpoint_sanitizes_smoke_payload(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "room"
            root.mkdir()
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            smoke_result = {
                "status": "ok",
                "group_id": "doctor-smoke",
                "agent_ids": ["smoke-local"],
                "source_event_id": "smoke-source",
                "started_group": {
                    "config_path": "/Users/me/private-live-agents.json",
                    "server": "http://127.0.0.1:8765",
                    "log_path": "/Users/me/.agentsassemble/live-agent-runs/doctor-smoke.log",
                    "log_tail": "secret log tail",
                },
                "stopped_group": {
                    "config_path": "/Users/me/private-live-agents.json",
                    "server": "http://127.0.0.1:8765",
                    "log_tail": "secret stopped log tail",
                },
                "replies": [{"actor_id": "smoke-local", "message": "secret smoke reply"}],
            }
            try:
                with patch("agentsassemble.gui.run_live_agent_smoke", return_value=smoke_result):
                    request = Request(
                        f"http://127.0.0.1:{server.server_port}/api/live-agent-readiness",
                        data=json.dumps({"group_id": "doctor-smoke", "timeout": 8}).encode("utf-8"),
                        headers={"Content-Type": "application/json", "Host": "127.0.0.1:1"},
                        method="POST",
                    )
                    with urlopen(request, timeout=12) as response:
                        payload = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

        self.assertEqual(
            payload["smoke"],
            {
                "status": "ok",
                "group_id": "doctor-smoke",
                "agent_ids": ["smoke-local"],
                "reply_count": 1,
            },
        )
        serialized_payload = json.dumps(payload, ensure_ascii=False)
        self.assertNotIn("smoke-source", serialized_payload)
        self.assertNotIn("private-live-agents", serialized_payload)
        self.assertNotIn("127.0.0.1:8765", serialized_payload)
        self.assertNotIn("log_tail", serialized_payload)
        self.assertNotIn("secret smoke reply", serialized_payload)

    def test_live_agent_readiness_endpoint_expands_probe_group_manifest(self):
        class FakeSupervisor:
            def snapshot_groups(self):
                return [
                    {
                        "group_id": "resident-main",
                        "status": "running",
                        "agents": [
                            {"agent_id": "agent-a", "display_name": "Agent A"},
                            {"agent_id": "agent-b", "display_name": "Agent B"},
                        ],
                    }
                ]

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "room"
            root.mkdir()
            (root / "live_agents.json").write_text(
                json.dumps(
                    {
                        "agents": [
                            {"agent_id": "agent-a", "display_name": "Agent A", "status": "online"},
                            {"agent_id": "agent-b", "display_name": "Agent B", "status": "online"},
                        ]
                    }
                ),
                encoding="utf-8",
            )
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root, process_supervisor=FakeSupervisor()))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            probe_results = [
                {"status": "ok", "agent_id": "agent-a", "source_event_id": "probe-source-a", "reply_event_id": "reply-a"},
                {"status": "ok", "agent_id": "agent-b", "source_event_id": "probe-source-b", "reply_event_id": "reply-b"},
            ]
            try:
                with (
                    patch("agentsassemble.gui.run_live_agent_smoke", return_value={"status": "ok", "group_id": "doctor-smoke", "replies": []}),
                    patch("agentsassemble.gui.run_live_agent_probe", side_effect=probe_results) as probe,
                ):
                    request = Request(
                        f"http://127.0.0.1:{server.server_port}/api/live-agent-readiness",
                        data=json.dumps(
                            {
                                "group_id": "doctor-smoke",
                                "timeout": 8,
                                "probe_agent_ids": ["agent-a"],
                                "probe_group_ids": ["resident-main"],
                            }
                        ).encode("utf-8"),
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

        self.assertEqual(payload["status"], "ready")
        self.assertEqual(
            {check["id"]: check["status"] for check in payload["checks"]},
            {
                "health": "ok",
                "smoke": "ok",
                "probe_group:resident-main": "ok",
                "probe:agent-a": "ok",
                "probe:agent-b": "ok",
            },
        )
        self.assertEqual(
            payload["probe_groups"],
            [{"status": "ok", "group_id": "resident-main", "agent_ids": ["agent-a", "agent-b"]}],
        )
        self.assertEqual([call.args[:2] for call in probe.call_args_list], [(root, "agent-a"), (root, "agent-b")])
        readiness_operations = [
            operation for operation in operations["operations"] if operation["operation"] == "readiness.check"
        ]
        self.assertEqual(readiness_operations[-1]["details"]["probe_group_ids"], ["resident-main"])
        self.assertEqual(readiness_operations[-1]["details"]["effective_probe_agent_ids"], ["agent-a", "agent-b"])

    def test_live_agent_readiness_endpoint_refuses_invalid_probe_groups_without_probe_side_effects(self):
        class FakeSupervisor:
            def snapshot_groups(self):
                return [
                    {"group_id": "stopped-group", "status": "stopped", "agents": [{"agent_id": "agent-a"}]},
                    {"group_id": "empty-group", "status": "running", "agents": []},
                ]

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "room"
            root.mkdir()
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root, process_supervisor=FakeSupervisor()))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with (
                    patch("agentsassemble.gui.run_live_agent_smoke", return_value={"status": "ok", "group_id": "doctor-smoke", "replies": []}),
                    patch("agentsassemble.gui.run_live_agent_probe") as probe,
                ):
                    request = Request(
                        f"http://127.0.0.1:{server.server_port}/api/live-agent-readiness",
                        data=json.dumps(
                            {
                                "group_id": "doctor-smoke",
                                "timeout": 8,
                                "probe_group_ids": ["stopped-group", "missing-group", "empty-group"],
                            }
                        ).encode("utf-8"),
                        headers={"Content-Type": "application/json", "Host": "127.0.0.1:1"},
                        method="POST",
                    )
                    with urlopen(request, timeout=12) as response:
                        payload = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

        self.assertEqual(payload["status"], "failed")
        self.assertEqual(
            payload["probe_groups"],
            [
                {"status": "failed", "group_id": "stopped-group", "reason": "group is not running"},
                {"status": "failed", "group_id": "missing-group", "reason": "group was not found"},
                {"status": "failed", "group_id": "empty-group", "reason": "group has no manifest agents"},
            ],
        )
        self.assertEqual(
            {check["id"]: check["status"] for check in payload["checks"]},
            {
                "health": "degraded",
                "smoke": "ok",
                "probe_group:stopped-group": "failed",
                "probe_group:missing-group": "failed",
                "probe_group:empty-group": "failed",
            },
        )
        probe.assert_not_called()

    def test_live_agent_readiness_endpoint_refuses_probe_groups_over_agent_cap_without_probe_side_effects(self):
        class FakeSupervisor:
            def snapshot_groups(self):
                return [
                    {
                        "group_id": "large-group",
                        "status": "running",
                        "agents": [{"agent_id": f"agent-{index}"} for index in range(11)],
                    }
                ]

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "room"
            root.mkdir()
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root, process_supervisor=FakeSupervisor()))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with (
                    patch("agentsassemble.gui.run_live_agent_smoke", return_value={"status": "ok", "group_id": "doctor-smoke", "replies": []}),
                    patch("agentsassemble.gui.run_live_agent_probe") as probe,
                ):
                    request = Request(
                        f"http://127.0.0.1:{server.server_port}/api/live-agent-readiness",
                        data=json.dumps(
                            {
                                "group_id": "doctor-smoke",
                                "timeout": 8,
                                "probe_group_ids": ["large-group"],
                            }
                        ).encode("utf-8"),
                        headers={"Content-Type": "application/json", "Host": "127.0.0.1:1"},
                        method="POST",
                    )
                    with urlopen(request, timeout=12) as response:
                        payload = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

        self.assertEqual(payload["status"], "failed")
        self.assertEqual(payload["probe_error"], "Too many probe agents requested; maximum is 10.")
        self.assertEqual(payload["probe_groups"][0]["status"], "ok")
        self.assertEqual(payload["probe_groups"][0]["agent_count"], 11)
        self.assertNotIn("agent_ids", payload["probe_groups"][0])
        self.assertNotIn("effective_probe_agent_ids", payload)
        probe.assert_not_called()

    def test_live_agent_readiness_endpoint_refuses_malformed_probe_ids_without_echoing_payload(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "room"
            root.mkdir()
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with (
                    patch("agentsassemble.gui.run_live_agent_smoke", return_value={"status": "ok", "group_id": "doctor-smoke", "replies": []}),
                    patch("agentsassemble.gui.run_live_agent_probe") as probe,
                ):
                    request = Request(
                        f"http://127.0.0.1:{server.server_port}/api/live-agent-readiness",
                        data=json.dumps(
                            {
                                "group_id": "doctor-smoke",
                                "timeout": 8,
                                "probe_agent_ids": [{"config_path": "/Users/me/private.json"}],
                                "probe_group_ids": [{"endpoint": "http://secret.local", "auth_ref": "env:SECRET_TOKEN"}],
                            }
                        ).encode("utf-8"),
                        headers={"Content-Type": "application/json", "Host": "127.0.0.1:1"},
                        method="POST",
                    )
                    with urlopen(request, timeout=12) as response:
                        payload = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

        self.assertEqual(payload["status"], "failed")
        self.assertEqual(payload["probe_error"], "Invalid probe id payload; expected a list of strings.")
        self.assertEqual(
            {check["id"]: check["status"] for check in payload["checks"]},
            {"health": "ok", "smoke": "ok", "probe_request_payload": "failed"},
        )
        probe.assert_not_called()
        serialized_payload = json.dumps(payload, ensure_ascii=False)
        self.assertNotIn("private.json", serialized_payload)
        self.assertNotIn("secret.local", serialized_payload)
        self.assertNotIn("SECRET_TOKEN", serialized_payload)

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

    def test_live_agent_preflight_endpoint_redacts_sensitive_config_failure_payload(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "room"
            root.mkdir()
            missing_config = root / "private" / "live-agents.secret.json"
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/live-agent-preflight",
                    data=json.dumps({"config_path": str(missing_config)}).encode("utf-8"),
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

            self.assertEqual(payload["status"], "failed")
            self.assertEqual(payload["config_path"], "[redacted]")
            self.assertEqual(payload["checks"][0]["message"], "Config load failed: details redacted.")
            serialized_payload = json.dumps(payload, ensure_ascii=False)
            serialized_operations = json.dumps(operations, ensure_ascii=False)
            self.assertNotIn(str(missing_config), serialized_payload)
            self.assertNotIn("live-agents.secret.json", serialized_payload)
            self.assertNotIn(str(missing_config), serialized_operations)
            self.assertNotIn("live-agents.secret.json", serialized_operations)

    def test_live_agent_preflight_endpoint_redacts_malformed_config_failure_payload(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "room"
            root.mkdir()
            config_path = root / "live-agents.json"
            config_path.write_text("{", encoding="utf-8")
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
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
            finally:
                server.shutdown()
                server.server_close()

            self.assertEqual(payload["status"], "failed")
            self.assertEqual(payload["config_path"], "[redacted]")
            self.assertEqual(payload["checks"][0]["message"], "Config load failed: details redacted.")
            serialized_payload = json.dumps(payload, ensure_ascii=False)
            self.assertNotIn(str(config_path), serialized_payload)
            self.assertNotIn("Expecting", serialized_payload)
            self.assertNotIn("line 1", serialized_payload)
            self.assertNotIn("char 0", serialized_payload)

    def test_live_agent_health_endpoint_summarizes_agents_and_processes(self):
        class FakeSupervisor:
            def __init__(self):
                self.list_called = False

            def list_groups(self):
                self.list_called = True
                raise AssertionError("health endpoint must not refresh process groups")

            def snapshot_groups(self):
                return [
                    {"group_id": "running-group", "status": "running", "meeting_id": "resident-m1"},
                    {"group_id": "unsafe-owner", "status": "running", "meeting_id": "../secret"},
                    {
                        "group_id": "restart-group",
                        "status": "restarting",
                        "meeting_id": "resident-m2",
                        "recent_events": [
                            {
                                "event_type": "stale_watchdog",
                                "reason": "missing manifest agent agent-a",
                            },
                            {
                                "event_type": "stale_watchdog",
                                "reason": "missing manifest agent live-agents.json",
                            },
                            {
                                "event_type": "restart_scheduled",
                                "reason": "env:SECRET_TOKEN",
                            },
                        ],
                    },
                    {
                        "group_id": "missing-config-group",
                        "status": "error",
                        "last_error": (
                            "Exited with return code 2; auto restart 1/1. "
                            "Restart failed: Live agent group missing-config-group has no config to restart."
                        ),
                        "recent_events": [{"event_type": "restart_failed"}],
                    },
                    {
                        "group_id": "missing-server-group",
                        "status": "error",
                        "last_error": (
                            "Exited with return code 2; auto restart 1/1. "
                            "Restart failed: Live agent group missing-server-group has no server to restart."
                        ),
                        "recent_events": [{"event_type": "restart_failed"}],
                    },
                    {
                        "group_id": "suspicious-restart-group",
                        "status": "error",
                        "last_error": (
                            "Exited with return code 2; auto restart 1/1. "
                            "Restart failed: Live agent group suspicious-restart-group has no config to restart. "
                            "/private/token/live-agents.json"
                        ),
                        "recent_events": [{"event_type": "restart_failed"}],
                    },
                    {"group_id": "crashed-group", "status": "error"},
                    {
                        "group_id": "orphan-group",
                        "status": "unknown",
                        "recent_events": [{"event_type": "recovered_unknown", "status": "unknown"}],
                    },
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
            self.assertEqual(payload["processes"]["counts"]["running"], 2)
            self.assertEqual(payload["processes"]["counts"]["restarting"], 1)
            self.assertEqual(payload["processes"]["counts"]["error"], 5)
            self.assertEqual(payload["processes"]["counts"]["unknown"], 2)
            self.assertEqual(payload["processes"]["counts"]["stopped"], 1)
            self.assertEqual(payload["processes"]["total"], 11)
            self.assertEqual(
                payload["processes"]["meeting_ids"],
                {"running-group": "resident-m1", "restart-group": "resident-m2"},
            )
            self.assertEqual(
                payload["processes"]["attention"],
                [
                    "restart-group",
                    "missing-config-group",
                    "missing-server-group",
                    "suspicious-restart-group",
                    "crashed-group",
                    "orphan-group",
                    "stopped-group",
                    "missing-process-group-id-10",
                    "odd-group",
                ],
            )
            self.assertEqual(
                payload["processes"]["reasons"],
                {
                    "restart-group": {"event_type": "stale_watchdog", "reason": "missing manifest agent agent-a"},
                    "missing-config-group": {"event_type": "restart_failed", "reason": "missing launch config"},
                    "missing-server-group": {"event_type": "restart_failed", "reason": "missing launch server"},
                    "orphan-group": {
                        "event_type": "recovered_unknown",
                        "reason": "orphan running record marked unknown",
                    },
                },
            )
            self.assertNotIn("SECRET_TOKEN", json.dumps(payload, ensure_ascii=False))
            self.assertNotIn("live-agents.json", json.dumps(payload, ensure_ascii=False))
            self.assertNotIn("private/token", json.dumps(payload, ensure_ascii=False))
            self.assertFalse(supervisor.list_called)

    def test_live_agent_health_redacts_sensitive_agent_attention_ids(self):
        sensitive_token = "ghp_abcdefghijklmnopqrstuvwxyz1234567890"

        class FakeSupervisor:
            def snapshot_groups(self):
                return []

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "live_agents.json").write_text(
                json.dumps(
                    {
                        "agents": [
                            {"agent_id": "env:SECRET_AGENT", "status": "error"},
                            {"agent_id": "literal:SECRET_AGENT", "status": "offline"},
                            {"agent_id": sensitive_token, "status": "stale"},
                            {"agent_id": "/Users/me/private/config.json", "status": "error"},
                            {"agent_id": "safe-agent", "status": "offline"},
                        ]
                    }
                ),
                encoding="utf-8",
            )

            payload = live_agent_health_payload(root, FakeSupervisor())

        serialized = json.dumps(payload, ensure_ascii=False)
        self.assertNotIn("SECRET_AGENT", serialized)
        self.assertNotIn(sensitive_token, serialized)
        self.assertNotIn("Users/me/private", serialized)
        self.assertNotIn("config.json", serialized)
        self.assertEqual(
            payload["agents"]["attention"],
            [
                "missing-agent-id-1",
                "missing-agent-id-2",
                "missing-agent-id-3",
                "missing-agent-id-4",
                "safe-agent",
            ],
        )

    def test_live_agent_health_reports_sandbox_enforcement_levels(self):
        class FakeSupervisor:
            def snapshot_groups(self):
                return []

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            connect_live_agent(
                root,
                {
                    "agent_id": "codex-live",
                    "provider_kind": "codex_live_session",
                    "connection_kind": "live_session",
                },
            )
            connect_live_agent(
                root,
                {
                    "agent_id": "claude-live",
                    "provider_kind": "claude_code",
                    "connection_kind": "terminal_session",
                    "sandbox_enforcement": "os_sandboxed",
                },
            )
            (root / "live_agents.json").write_text(
                json.dumps(
                    {
                        "agents": [
                            *json.loads((root / "live_agents.json").read_text(encoding="utf-8"))["agents"],
                            {
                                "agent_id": "unknown-live",
                                "display_name": "Unknown Live",
                                "provider_kind": "mystery_provider",
                                "connection_kind": "mystery_connection",
                                "status": "online",
                            },
                        ]
                    }
                ),
                encoding="utf-8",
            )

            payload = live_agent_health_payload(root, FakeSupervisor())

        self.assertEqual(payload["status"], "degraded")
        self.assertEqual(
            payload["sandbox_enforcement"],
            {
                "counts": {"advisory": 1, "codex_readonly": 1, "os_sandboxed": 0, "unknown": 1},
                "attention": ["unknown-live"],
            },
        )

    def test_live_agent_health_endpoint_summarizes_durable_session_run_retry(self):
        class FakeSupervisor:
            def list_groups(self):
                raise AssertionError("health endpoint must not refresh process groups")

            def snapshot_groups(self):
                return []

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            controller = LiveAgentSessionRunController(root)
            run = controller.begin_run(
                action="ensure",
                payload={
                    "meeting_id": "resident-m1",
                    "group_id": "resident-main",
                    "live_agent_config_path": "configs/private.json",
                    "server": "https://secret.example",
                },
            )
            controller.finish_run(
                run["run_id"],
                session={
                    "status": "degraded",
                    "meeting_id": "resident-m1",
                    "group_id": "resident-main",
                    "action": "recover",
                },
            )

            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root, process_supervisor=FakeSupervisor()))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with urlopen(f"http://127.0.0.1:{server.server_port}/api/live-agent-health", timeout=4) as response:
                    payload = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

        self.assertEqual(payload["status"], "degraded")
        self.assertEqual(payload["session_runs"]["total"], 1)
        self.assertEqual(payload["session_runs"]["active"], 1)
        self.assertEqual(payload["session_runs"]["ready"], 0)
        self.assertEqual(payload["session_runs"]["retrying"], 1)
        self.assertEqual(
            payload["session_runs"]["attention"],
            [f"resident-m1:resident-main:{run['run_id']}:degraded:retrying"],
        )
        self.assertEqual(payload["session_runs"]["items"][0]["run_id"], run["run_id"])
        self.assertEqual(payload["session_runs"]["items"][0]["reconcile_failure_count"], 1)
        self.assertEqual(payload["session_runs"]["items"][0]["reconcile_backoff_seconds"], 60)
        serialized = json.dumps(payload, ensure_ascii=False)
        self.assertNotIn("configs/private.json", serialized)
        self.assertNotIn("secret.example", serialized)

    def test_live_agent_health_degrades_ready_session_run_without_current_readiness(self):
        class FakeSupervisor:
            def list_groups(self):
                raise AssertionError("health endpoint must not refresh process groups")

            def snapshot_groups(self):
                return []

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _write_health_resident_meeting(root, agent_ids=["agent-a"])
            controller = LiveAgentSessionRunController(root)
            run = controller.begin_run(
                action="ensure",
                payload={
                    "meeting_id": "resident-m1",
                    "group_id": "resident-main",
                    "live_agent_config_path": "configs/private.json",
                    "server": "https://secret.example",
                },
            )
            controller.finish_run(
                run["run_id"],
                session={
                    "status": "ready",
                    "meeting_id": "resident-m1",
                    "group_id": "resident-main",
                    "action": "none",
                },
            )

            payload = live_agent_health_payload(root, FakeSupervisor())
            operations_path_exists = (root / "live-agent-runs" / "operations.jsonl").exists()

        self.assertEqual(payload["status"], "degraded")
        self.assertFalse(operations_path_exists)
        self.assertEqual(
            payload["session_runs"]["attention"],
            [f"resident-m1:resident-main:{run['run_id']}:ready:no_current_readiness"],
        )
        self.assertEqual(payload["session_runs"]["items"][0]["status"], "ready")
        self.assertEqual(payload["session_runs"]["items"][0]["readiness"]["status"], "degraded")
        self.assertEqual(
            payload["session_runs"]["items"][0]["readiness"]["attention"],
            ["session_run:no_current_readiness"],
        )
        serialized = json.dumps(payload, ensure_ascii=False)
        self.assertNotIn("configs/private.json", serialized)
        self.assertNotIn("secret.example", serialized)

    def test_live_agent_health_keeps_old_active_ready_session_runs_outside_recent_tail(self):
        class FakeSupervisor:
            def list_groups(self):
                raise AssertionError("health endpoint must not refresh process groups")

            def snapshot_groups(self):
                return []

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _write_health_resident_meeting(root, agent_ids=["agent-a"])
            controller = LiveAgentSessionRunController(root)
            active_ready_run = controller.begin_run(
                action="ensure",
                payload={
                    "meeting_id": "resident-m1",
                    "group_id": "resident-main",
                    "live_agent_config_path": "configs/private.json",
                },
            )
            controller.finish_run(
                active_ready_run["run_id"],
                session={
                    "status": "ready",
                    "meeting_id": "resident-m1",
                    "group_id": "resident-main",
                    "action": "none",
                },
            )
            for index in range(60):
                run = controller.begin_run(
                    action="ensure",
                    payload={"meeting_id": f"resident-done-{index}", "group_id": "resident-main"},
                )
                controller.finish_run(
                    run["run_id"],
                    session={
                        "status": "ready",
                        "meeting_id": f"resident-done-{index}",
                        "group_id": "resident-main",
                        "action": "none",
                    },
                )
                controller.stop_run(run["run_id"])

            payload = live_agent_health_payload(root, FakeSupervisor())

        run_ids = [item["run_id"] for item in payload["session_runs"]["items"]]
        self.assertIn(active_ready_run["run_id"], run_ids)
        self.assertIn(
            f"resident-m1:resident-main:{active_ready_run['run_id']}:ready:no_current_readiness",
            payload["session_runs"]["attention"],
        )

    def test_live_agent_health_accepts_ready_session_run_with_current_ready_overlay(self):
        class FakeSupervisor:
            def list_groups(self):
                raise AssertionError("health endpoint must not refresh process groups")

            def snapshot_groups(self):
                return [
                    {
                        "group_id": "resident-main",
                        "status": "running",
                        "meeting_id": "resident-m1",
                        "agents": [{"agent_id": "agent-a"}],
                    }
                ]

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _write_health_resident_meeting(root, agent_ids=["agent-a"])
            connect_live_agent(
                root,
                {
                    "agent_id": "agent-a",
                    "display_name": "Agent A",
                    "provider_kind": "local_cli",
                    "connection_kind": "local_cli",
                    "meeting_id": "resident-m1",
                },
            )
            controller = LiveAgentSessionRunController(root)
            run = controller.begin_run(
                action="ensure",
                payload={"meeting_id": "resident-m1", "group_id": "resident-main"},
            )
            controller.finish_run(
                run["run_id"],
                session={
                    "status": "ready",
                    "meeting_id": "resident-m1",
                    "group_id": "resident-main",
                    "action": "none",
                },
            )

            payload = live_agent_health_payload(root, FakeSupervisor())

        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["session_runs"]["attention"], [])
        self.assertEqual(payload["session_runs"]["items"][0]["readiness"]["status"], "ready")

    def test_live_agent_health_degrades_ready_session_run_with_duplicate_current_owner(self):
        class FakeSupervisor:
            def list_groups(self):
                raise AssertionError("health endpoint must not refresh process groups")

            def snapshot_groups(self):
                return [
                    {
                        "group_id": "resident-main",
                        "status": "running",
                        "meeting_id": "resident-m1",
                        "agents": [{"agent_id": "agent-a"}],
                    },
                    {
                        "group_id": "resident-shadow",
                        "status": "running",
                        "meeting_id": "resident-m1",
                        "agents": [{"agent_id": "agent-a"}],
                    },
                ]

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _write_health_resident_meeting(root, agent_ids=["agent-a"])
            connect_live_agent(
                root,
                {
                    "agent_id": "agent-a",
                    "display_name": "Agent A",
                    "provider_kind": "local_cli",
                    "connection_kind": "local_cli",
                    "meeting_id": "resident-m1",
                },
            )
            controller = LiveAgentSessionRunController(root)
            run = controller.begin_run(
                action="ensure",
                payload={"meeting_id": "resident-m1", "group_id": "resident-main"},
            )
            controller.finish_run(
                run["run_id"],
                session={
                    "status": "ready",
                    "meeting_id": "resident-m1",
                    "group_id": "resident-main",
                    "action": "none",
                },
            )

            payload = live_agent_health_payload(root, FakeSupervisor())

        self.assertEqual(payload["status"], "degraded")
        self.assertIn(
            f"resident-m1:resident-main:{run['run_id']}:ready:current_readiness_degraded",
            payload["session_runs"]["attention"],
        )
        self.assertIn("meeting:duplicate_active_group", payload["session_runs"]["items"][0]["readiness"]["attention"])

    def test_live_agent_health_endpoint_includes_process_monitor_liveness(self):
        class MonitorSupervisor:
            def list_groups(self):
                raise AssertionError("health endpoint must not refresh process groups")

            def snapshot_groups(self):
                return []

            def monitor_snapshot(self):
                return {
                    "running": True,
                    "interval_seconds": 2.5,
                    "last_tick_at": "2026-05-21T10:09:00+00:00",
                    "last_status": "ok",
                    "last_group_count": 1,
                    "last_error_type": "",
                }

        with tempfile.TemporaryDirectory() as temp_dir:
            payload = live_agent_health_payload(Path(temp_dir), MonitorSupervisor())

        self.assertEqual(payload["status"], "ok")
        self.assertEqual(
            payload["process_monitor"],
            {
                "running": True,
                "interval_seconds": 2.5,
                "last_tick_at": "2026-05-21T10:09:00+00:00",
                "last_status": "ok",
                "last_group_count": 1,
                "last_error_type": "",
                "attention": [],
            },
        )

    def test_live_agent_health_endpoint_degrades_on_safe_process_monitor_failure(self):
        class MonitorSupervisor:
            def list_groups(self):
                raise AssertionError("health endpoint must not refresh process groups")

            def snapshot_groups(self):
                return []

            def monitor_snapshot(self):
                return {
                    "running": True,
                    "interval_seconds": 2,
                    "last_tick_at": "2026-05-21T10:09:00+00:00",
                    "last_status": "failed",
                    "last_group_count": 0,
                    "last_error_type": "RuntimeError /Users/me/private/live-agents.secret.json",
                }

        with tempfile.TemporaryDirectory() as temp_dir:
            payload = live_agent_health_payload(Path(temp_dir), MonitorSupervisor())

        self.assertEqual(payload["status"], "degraded")
        self.assertEqual(payload["process_monitor"]["last_error_type"], "")
        self.assertEqual(payload["process_monitor"]["attention"], ["failed:Exception"])
        self.assertNotIn("/Users/me/private", json.dumps(payload, ensure_ascii=False))

    def test_live_agent_health_endpoint_includes_session_run_monitor_liveness(self):
        from agentsassemble.gui import LiveAgentSessionRunMonitor

        class FakeSupervisor:
            def list_groups(self):
                raise AssertionError("health endpoint must not refresh process groups")

            def snapshot_groups(self):
                return []

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            monitor = LiveAgentSessionRunMonitor(
                root,
                FakeSupervisor(),
                LiveAgentSessionRunController(root),
                default_server="http://room.local",
                interval_seconds=2.5,
            )
            monitor.run_once()
            server = ThreadingHTTPServer(
                ("127.0.0.1", 0),
                _make_handler(root, process_supervisor=FakeSupervisor(), session_run_monitor=monitor),
            )
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with urlopen(f"http://127.0.0.1:{server.server_port}/api/live-agent-health", timeout=4) as response:
                    payload = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

        monitor_payload = payload["session_run_monitor"]
        self.assertEqual(monitor_payload["running"], False)
        self.assertEqual(monitor_payload["interval_seconds"], 2.5)
        self.assertEqual(monitor_payload["last_status"], "ok")
        self.assertEqual(monitor_payload["last_result_count"], 0)
        self.assertRegex(monitor_payload["last_tick_at"], r"^\d{4}-\d{2}-\d{2}T")
        self.assertEqual(monitor_payload["last_error_type"], "")

    def test_live_agent_health_degrades_ready_session_when_lobby_cursor_lags_latest_event(self):
        class FakeSupervisor:
            def list_groups(self):
                raise AssertionError("health endpoint must not refresh process groups")

            def snapshot_groups(self):
                return [
                    {
                        "group_id": "resident-main",
                        "status": "running",
                        "meeting_id": "resident-m1",
                        "agents": [{"agent_id": "agent-a"}],
                    }
                ]

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _write_health_resident_meeting(root, agent_ids=["agent-a"])
            latest = append_lobby_event(root, {"name": "human", "message": "latest event text must stay out"})
            connect_live_agent(
                root,
                {
                    "agent_id": "agent-a",
                    "display_name": "Agent A",
                    "provider_kind": "local_cli",
                    "connection_kind": "local_cli",
                    "meeting_id": "resident-m1",
                    "last_observed_event_id": "older-event",
                },
            )

            payload = live_agent_health_payload(root, FakeSupervisor())

        self.assertEqual(payload["status"], "degraded")
        self.assertEqual(payload["observations"]["latest_lobby_event_id"], latest["id"])
        self.assertEqual(payload["observations"]["ready_agent_count"], 1)
        self.assertEqual(payload["observations"]["lobby_behind_count"], 1)
        self.assertEqual(
            payload["observations"]["attention"],
            ["resident-m1:resident-main:agent-a:lobby_cursor_behind"],
        )
        serialized = json.dumps(payload, ensure_ascii=False)
        self.assertNotIn("latest event text", serialized)

    def test_live_agent_health_keeps_ready_session_ok_when_lobby_cursor_is_current(self):
        class FakeSupervisor:
            def list_groups(self):
                raise AssertionError("health endpoint must not refresh process groups")

            def snapshot_groups(self):
                return [
                    {
                        "group_id": "resident-main",
                        "status": "running",
                        "meeting_id": "resident-m1",
                        "agents": [{"agent_id": "agent-a"}],
                    }
                ]

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _write_health_resident_meeting(root, agent_ids=["agent-a"])
            latest = append_lobby_event(root, {"name": "human", "message": "current event text must stay out"})
            connect_live_agent(
                root,
                {
                    "agent_id": "agent-a",
                    "display_name": "Agent A",
                    "provider_kind": "local_cli",
                    "connection_kind": "local_cli",
                    "meeting_id": "resident-m1",
                    "last_observed_event_id": latest["id"],
                    "last_reply_at": "2026-05-21T10:12:00+00:00",
                },
            )

            payload = live_agent_health_payload(root, FakeSupervisor())

        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["observations"]["attention"], [])
        self.assertEqual(payload["observations"]["lobby_behind_count"], 0)
        self.assertEqual(payload["observations"]["items"][0]["last_reply_at"], "2026-05-21T10:12:00+00:00")
        self.assertNotIn("current event text", json.dumps(payload, ensure_ascii=False))

    def test_live_agent_health_does_not_expose_or_degrade_on_preserved_last_error(self):
        class FakeSupervisor:
            def list_groups(self):
                raise AssertionError("health endpoint must not refresh process groups")

            def snapshot_groups(self):
                return [
                    {
                        "group_id": "resident-main",
                        "status": "running",
                        "meeting_id": "resident-m1",
                        "agents": [{"agent_id": "agent-a"}],
                    }
                ]

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _write_health_resident_meeting(root, agent_ids=["agent-a"])
            latest = append_lobby_event(root, {"name": "human", "message": "current event text must stay out"})
            connect_live_agent(
                root,
                {
                    "agent_id": "agent-a",
                    "display_name": "Agent A",
                    "provider_kind": "local_cli",
                    "connection_kind": "local_cli",
                    "meeting_id": "resident-m1",
                    "last_observed_event_id": latest["id"],
                    "last_error": "provider output raw model reply should never leak",
                },
            )

            payload = live_agent_health_payload(root, FakeSupervisor())

        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["observations"]["error_count"], 0)
        self.assertEqual(payload["observations"]["attention"], [])
        self.assertNotIn("last_error", payload["observations"]["items"][0])
        serialized = json.dumps(payload, ensure_ascii=False)
        self.assertNotIn("provider output", serialized)
        self.assertNotIn("raw model reply", serialized)

    def test_live_agent_health_includes_safe_admission_summary_without_degrading(self):
        class FakeSupervisor:
            def list_groups(self):
                raise AssertionError("health endpoint must not refresh process groups")

            def snapshot_groups(self):
                return []

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _write_health_resident_meeting(root, agent_ids=["agent-a", "agent-b"])
            connect_live_agent(
                root,
                {
                    "agent_id": "agent-a",
                    "display_name": "Agent A",
                    "provider_kind": "local_cli",
                    "connection_kind": "local_cli",
                    "meeting_id": "resident-m1",
                    "session_id": "private-session-a",
                },
            )
            connect_live_agent(
                root,
                {
                    "agent_id": "agent-b",
                    "display_name": "Agent B",
                    "provider_kind": "manual",
                    "connection_kind": "manual",
                    "meeting_id": "resident-m1",
                    "session_id": "private-session-b",
                },
            )
            connect_live_agent(
                root,
                {
                    "agent_id": "guest-agent",
                    "display_name": "Guest Agent",
                    "provider_kind": "manual",
                    "connection_kind": "manual",
                    "meeting_id": "resident-m1",
                    "session_id": "private-session-c",
                },
            )
            connect_live_agent(
                root,
                {
                    "agent_id": "lobby-agent",
                    "display_name": "Lobby Agent",
                    "provider_kind": "manual",
                    "connection_kind": "manual",
                    "session_id": "private-session-d",
                    "last_error": "provider output raw model reply should stay out",
                },
            )
            connect_live_agent(
                root,
                {
                    "agent_id": "diagnostic-agent",
                    "display_name": "Diagnostic Agent",
                    "provider_kind": "local_cli",
                    "connection_kind": "local_cli",
                    "meeting_id": "resident-m1",
                    "session_id": "private-session-diagnostic",
                    "diagnostic": True,
                },
            )
            state_path = root / "live_agents.json"
            state = json.loads(state_path.read_text(encoding="utf-8"))
            state["agents"][2]["admission_status"] = "bound_to_meeting"
            state["agents"][2]["host_approved_binding"] = True
            state["agents"][2]["binding_role_id"] = "spoofed"
            state_path.write_text(json.dumps(state), encoding="utf-8")

            payload = live_agent_health_payload(root, FakeSupervisor())

        self.assertEqual(payload["status"], "ok")
        admission = payload["admission"]
        self.assertEqual(admission["total"], 4)
        self.assertEqual(admission["host_approved"], 1)
        self.assertEqual(admission["unapproved"], 3)
        self.assertEqual(admission["counts"]["bound_to_meeting"], 1)
        self.assertEqual(admission["counts"]["binding_conflict"], 1)
        self.assertEqual(admission["counts"]["meeting_lobby_only"], 1)
        self.assertEqual(admission["counts"]["lobby_only"], 1)
        self.assertEqual(
            admission["attention"],
            [
                "resident-m1:agent-b:binding_conflict",
                "resident-m1:guest-agent:meeting_lobby_only",
                "lobby:lobby-agent:lobby_only",
            ],
        )
        serialized = json.dumps(payload, ensure_ascii=False)
        self.assertNotIn("private-session", serialized)
        self.assertNotIn("spoofed", serialized)
        self.assertNotIn("provider output", serialized)
        self.assertNotIn("raw model reply", serialized)

    def test_live_agent_health_degrades_ready_session_when_official_turn_cursor_lags_request(self):
        class FakeSupervisor:
            def list_groups(self):
                raise AssertionError("health endpoint must not refresh process groups")

            def snapshot_groups(self):
                return [
                    {
                        "group_id": "resident-main",
                        "status": "running",
                        "meeting_id": "resident-m1",
                        "agents": [{"agent_id": "agent-a"}],
                    }
                ]

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            meeting_dir = _write_health_resident_meeting(root, agent_ids=["agent-a"])
            request = append_live_event(
                meeting_dir,
                {
                    "kind": "live_agent_turn_request",
                    "meeting_id": "resident-m1",
                    "target_agent_id": "agent-a",
                    "content": "official request text must stay out",
                },
            )
            connect_live_agent(
                root,
                {
                    "agent_id": "agent-a",
                    "display_name": "Agent A",
                    "provider_kind": "local_cli",
                    "connection_kind": "local_cli",
                    "meeting_id": "resident-m1",
                    "last_observed_live_event_id": "older-live-event",
                },
            )

            payload = live_agent_health_payload(root, FakeSupervisor())

        self.assertEqual(payload["status"], "degraded")
        self.assertEqual(payload["observations"]["latest_live_request_count"], 1)
        self.assertEqual(payload["observations"]["items"][0]["latest_live_event_id"], request["id"])
        self.assertEqual(
            payload["observations"]["attention"],
            ["resident-m1:resident-main:agent-a:live_cursor_behind"],
        )
        self.assertNotIn("official request text", json.dumps(payload, ensure_ascii=False))

    def test_live_agent_health_treats_official_turn_reply_as_observed(self):
        class FakeSupervisor:
            def list_groups(self):
                raise AssertionError("health endpoint must not refresh process groups")

            def snapshot_groups(self):
                return [
                    {
                        "group_id": "resident-main",
                        "status": "running",
                        "meeting_id": "resident-m1",
                        "agents": [{"agent_id": "agent-a"}],
                    }
                ]

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            meeting_dir = _write_health_resident_meeting(root, agent_ids=["agent-a"])
            request = append_live_event(
                meeting_dir,
                {
                    "kind": "live_agent_turn_request",
                    "meeting_id": "resident-m1",
                    "target_agent_id": "agent-a",
                    "content": "official request text must stay out",
                },
            )
            append_live_event(
                meeting_dir,
                {
                    "kind": "message",
                    "meeting_id": "resident-m1",
                    "official_record": True,
                    "actor_id": "agent-a",
                    "source_event_id": request["id"],
                    "content": "official reply text must stay out",
                },
            )
            connect_live_agent(
                root,
                {
                    "agent_id": "agent-a",
                    "display_name": "Agent A",
                    "provider_kind": "local_cli",
                    "connection_kind": "local_cli",
                    "meeting_id": "resident-m1",
                    "last_observed_live_event_id": "older-live-event",
                },
            )

            payload = live_agent_health_payload(root, FakeSupervisor())

        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["observations"]["live_behind_count"], 0)
        self.assertEqual(payload["observations"]["attention"], [])
        self.assertEqual(payload["observations"]["items"][0]["live_status"], "answered")
        serialized = json.dumps(payload, ensure_ascii=False)
        self.assertNotIn("official request text", serialized)
        self.assertNotIn("official reply text", serialized)

    def test_live_agent_health_includes_shared_memory_summary_without_content(self):
        class FakeSupervisor:
            def list_groups(self):
                raise AssertionError("health endpoint must not refresh process groups")

            def snapshot_groups(self):
                return [
                    {
                        "group_id": "resident-main",
                        "status": "running",
                        "meeting_id": "resident-m1",
                        "agents": [{"agent_id": "agent-a"}],
                    }
                ]

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            meeting_dir = _write_health_resident_meeting(root, agent_ids=["agent-a"])
            append_live_event(
                meeting_dir,
                {
                    "kind": "message",
                    "meeting_id": "resident-m1",
                    "official_record": True,
                    "actor_id": "agent-a",
                    "role_id": "agent-a",
                    "content": "Action: Preserve resident memory health evidence.",
                },
            )
            last_event = append_live_event(
                meeting_dir,
                {
                    "kind": "message",
                    "meeting_id": "resident-m1",
                    "official_record": True,
                    "actor_id": "agent-a",
                    "role_id": "agent-a",
                    "content": "Question: Is shared memory still current?",
                },
            )
            connect_live_agent(
                root,
                {
                    "agent_id": "agent-a",
                    "display_name": "Agent A",
                    "provider_kind": "local_cli",
                    "connection_kind": "local_cli",
                    "meeting_id": "resident-m1",
                },
            )

            payload = live_agent_health_payload(root, FakeSupervisor())

        self.assertEqual(payload["status"], "ok")
        shared_memory = payload["shared_memory"]
        self.assertEqual(shared_memory["ready_sessions"], 1)
        self.assertEqual(shared_memory["with_memory"], 1)
        self.assertEqual(shared_memory["official_event_count"], 2)
        self.assertEqual(shared_memory["open_question_count"], 1)
        self.assertEqual(shared_memory["action_item_count"], 1)
        self.assertEqual(shared_memory["last_official_event_id"], last_event["id"])
        self.assertEqual(shared_memory["items"][0]["meeting_id"], "resident-m1")
        self.assertEqual(shared_memory["items"][0]["group_id"], "resident-main")
        self.assertEqual(shared_memory["items"][0]["official_event_count"], 2)
        serialized = json.dumps(payload, ensure_ascii=False)
        self.assertNotIn("Preserve resident memory health evidence", serialized)
        self.assertNotIn("Is shared memory still current", serialized)

    def test_live_agent_health_shared_memory_uses_full_counts_and_drops_source_text(self):
        class FakeSupervisor:
            def list_groups(self):
                raise AssertionError("health endpoint must not refresh process groups")

            def snapshot_groups(self):
                return [
                    {
                        "group_id": "resident-main",
                        "status": "running",
                        "meeting_id": "resident-m1",
                        "agents": [{"agent_id": "agent-a"}],
                    }
                ]

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            meeting_dir = _write_health_resident_meeting(root, agent_ids=["agent-a"])
            shared_dir = meeting_dir / "shared_memory"
            shared_dir.mkdir()
            (shared_dir / "index.json").write_text(
                json.dumps(
                    {
                        "source": "PROMPT BODY SHOULD NOT LEAK",
                        "official_event_count": 1,
                        "action_items": [{"text": "embedded fallback action should not leak"}],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            last_event = None
            for index in range(55):
                last_event = append_live_event(
                    meeting_dir,
                    {
                        "kind": "message",
                        "meeting_id": "resident-m1",
                        "official_record": True,
                        "actor_id": "agent-a",
                        "role_id": "agent-a",
                        "content": f"Action: Full memory count item {index}.",
                    },
                )
            connect_live_agent(
                root,
                {
                    "agent_id": "agent-a",
                    "display_name": "Agent A",
                    "provider_kind": "local_cli",
                    "connection_kind": "local_cli",
                    "meeting_id": "resident-m1",
                },
            )

            payload = live_agent_health_payload(root, FakeSupervisor())

        shared_memory = payload["shared_memory"]
        self.assertEqual(shared_memory["official_event_count"], 55)
        self.assertEqual(shared_memory["action_item_count"], 55)
        self.assertEqual(shared_memory["last_official_event_id"], last_event["id"])
        self.assertNotIn("source", shared_memory["items"][0])
        serialized = json.dumps(payload, ensure_ascii=False)
        self.assertNotIn("PROMPT BODY SHOULD NOT LEAK", serialized)
        self.assertNotIn("embedded fallback action should not leak", serialized)
        self.assertNotIn("Full memory count item", serialized)

    def test_live_agent_health_keeps_old_active_session_runs_outside_recent_tail(self):
        class FakeSupervisor:
            def list_groups(self):
                raise AssertionError("health endpoint must not refresh process groups")

            def snapshot_groups(self):
                return []

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            controller = LiveAgentSessionRunController(root)
            active_run = controller.begin_run(
                action="ensure",
                payload={
                    "meeting_id": "resident-old",
                    "group_id": "resident-main",
                    "live_agent_config_path": "configs/private.json",
                    "server": "https://secret.example",
                },
            )
            controller.finish_run(
                active_run["run_id"],
                session={
                    "status": "degraded",
                    "meeting_id": "resident-old",
                    "group_id": "resident-main",
                    "action": "recover",
                },
            )
            for index in range(60):
                run = controller.begin_run(
                    action="ensure",
                    payload={
                        "meeting_id": f"resident-done-{index}",
                        "group_id": "resident-main",
                        "live_agent_config_path": "configs/private.json",
                    },
                )
                controller.finish_run(
                    run["run_id"],
                    session={
                        "status": "ready",
                        "meeting_id": f"resident-done-{index}",
                        "group_id": "resident-main",
                        "action": "none",
                    },
                )

            payload = live_agent_health_payload(root, FakeSupervisor())

        self.assertEqual(payload["status"], "degraded")
        self.assertIn(
            f"resident-old:resident-main:{active_run['run_id']}:degraded:retrying",
            payload["session_runs"]["attention"],
        )
        self.assertIn(active_run["run_id"], [item["run_id"] for item in payload["session_runs"]["items"]])

    def test_live_agent_health_redacts_sensitive_session_run_ids(self):
        class FakeSupervisor:
            def list_groups(self):
                raise AssertionError("health endpoint must not refresh process groups")

            def snapshot_groups(self):
                return []

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            runs_dir = root / "live-agent-runs"
            runs_dir.mkdir(parents=True)
            (runs_dir / "session-runs.json").write_text(
                json.dumps(
                    {
                        "runs": [
                            {
                                "run_id": "ghp_abcdefghijklmnopqrstuvwxyz1234567890",
                                "action": "ensure",
                                "status": "degraded",
                                "active": True,
                                "phase": "/Users/me/private/live-agents.secret.json token sk-test-secret",
                                "meeting_id": "env:SECRET_MEETING",
                                "group_id": "literal:SECRET_GROUP",
                                "request": {},
                                "result": {"status": "degraded"},
                                "next_reconcile_at": "2026-05-21T10:07:00+00:00",
                                "reconcile_failure_count": 1,
                                "reconcile_backoff_seconds": 60,
                            }
                        ]
                    },
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )

            payload = live_agent_health_payload(root, FakeSupervisor())

        serialized = json.dumps(payload, ensure_ascii=False)
        self.assertNotIn("SECRET_MEETING", serialized)
        self.assertNotIn("SECRET_GROUP", serialized)
        self.assertNotIn("ghp_abcdefghijklmnopqrstuvwxyz1234567890", serialized)
        self.assertNotIn("Users/me/private", serialized)
        self.assertNotIn("live-agents.secret.json", serialized)
        self.assertNotIn("sk-test-secret", serialized)
        self.assertEqual(payload["session_runs"]["items"][0]["phase"], "")
        self.assertEqual(payload["session_runs"]["attention"], ["-:-:-:degraded:retrying"])

    def test_live_agent_health_ignores_diagnostic_session_runs(self):
        class FakeSupervisor:
            def list_groups(self):
                raise AssertionError("health endpoint must not refresh process groups")

            def snapshot_groups(self):
                return []

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            controller = LiveAgentSessionRunController(root)
            run = controller.begin_run(
                action="ensure",
                payload={
                    "meeting_id": "diagnostic-m1",
                    "group_id": "diagnostic-main",
                    "diagnostic": True,
                    "live_agent_config_path": "configs/private.json",
                },
            )
            controller.finish_run(
                run["run_id"],
                session={
                    "status": "degraded",
                    "meeting_id": "diagnostic-m1",
                    "group_id": "diagnostic-main",
                    "action": "recover",
                },
            )

            payload = live_agent_health_payload(root, FakeSupervisor())

        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["session_runs"]["total"], 0)
        self.assertEqual(payload["session_runs"]["active"], 0)
        self.assertEqual(payload["session_runs"]["ready"], 0)
        self.assertEqual(payload["session_runs"]["retrying"], 0)
        self.assertEqual(payload["session_runs"]["attention"], [])
        self.assertEqual(payload["session_runs"]["items"], [])

    def test_live_agent_health_ignores_legacy_string_diagnostic_session_runs(self):
        class FakeSupervisor:
            def list_groups(self):
                raise AssertionError("health endpoint must not refresh process groups")

            def snapshot_groups(self):
                return []

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            runs_dir = root / "live-agent-runs"
            runs_dir.mkdir(parents=True)
            (runs_dir / "session-runs.json").write_text(
                json.dumps(
                    {
                        "runs": [
                            {
                                "run_id": "legacy-diagnostic-run",
                                "action": "ensure",
                                "status": "degraded",
                                "active": True,
                                "phase": "reconcile_failed",
                                "meeting_id": "diagnostic-m1",
                                "group_id": "diagnostic-main",
                                "request": {"diagnostic": "true"},
                                "result": {"status": "degraded"},
                                "next_reconcile_at": "2026-05-21T10:07:00+00:00",
                                "reconcile_failure_count": 1,
                                "reconcile_backoff_seconds": 60,
                            }
                        ]
                    },
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )

            payload = live_agent_health_payload(root, FakeSupervisor())

        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["session_runs"]["total"], 0)
        self.assertEqual(payload["session_runs"]["items"], [])

    def test_live_agent_health_endpoint_reports_only_current_recovered_unknown_reason(self):
        class FakeSupervisor:
            def list_groups(self):
                raise AssertionError("health endpoint must not refresh process groups")

            def snapshot_groups(self):
                return [
                    {
                        "group_id": "current-orphan",
                        "status": "unknown",
                        "recent_events": [{"event_type": "recovered_unknown", "status": "unknown"}],
                    },
                    {
                        "group_id": "resolved-orphan",
                        "status": "unknown",
                        "recent_events": [
                            {"event_type": "recovered_unknown", "status": "unknown"},
                            {"event_type": "stopped", "status": "stopped"},
                        ],
                    },
                    {
                        "group_id": "running-after-recovery",
                        "status": "running",
                        "recent_events": [{"event_type": "recovered_unknown", "status": "unknown"}],
                    },
                ]

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root, process_supervisor=FakeSupervisor()))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with urlopen(f"http://127.0.0.1:{server.server_port}/api/live-agent-health", timeout=4) as response:
                    payload = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

        self.assertEqual(
            payload["processes"]["reasons"],
            {
                "current-orphan": {
                    "event_type": "recovered_unknown",
                    "reason": "orphan running record marked unknown",
                },
            },
        )

    def test_live_agent_health_endpoint_reports_only_current_restart_failed_launch_reason(self):
        class FakeSupervisor:
            def list_groups(self):
                raise AssertionError("health endpoint must not refresh process groups")

            def snapshot_groups(self):
                return [
                    {
                        "group_id": "current-failure",
                        "status": "error",
                        "last_error": (
                            "Exited with return code 2; auto restart 1/1. "
                            "Restart failed: Live agent group current-failure has no server to restart."
                        ),
                        "recent_events": [
                            {"event_type": "stale_watchdog", "reason": "missing manifest agent agent-a"},
                            {"event_type": "restart_failed"},
                        ],
                    },
                    {
                        "group_id": "resolved-failure",
                        "status": "error",
                        "last_error": (
                            "Exited with return code 2; auto restart 1/1. "
                            "Restart failed: Live agent group resolved-failure has no config to restart."
                        ),
                        "recent_events": [
                            {"event_type": "restart_failed"},
                            {"event_type": "started"},
                        ],
                    },
                    {
                        "group_id": "wrong-id-failure",
                        "status": "error",
                        "last_error": (
                            "Exited with return code 2; auto restart 1/1. "
                            "Restart failed: Live agent group other-group has no config to restart."
                        ),
                        "recent_events": [{"event_type": "restart_failed"}],
                    },
                    {
                        "group_id": "running-failure",
                        "status": "running",
                        "last_error": (
                            "Exited with return code 2; auto restart 1/1. "
                            "Restart failed: Live agent group running-failure has no config to restart."
                        ),
                        "recent_events": [{"event_type": "restart_failed"}],
                    },
                ]

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "live_agents.json").write_text(json.dumps({"agents": []}), encoding="utf-8")
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root, process_supervisor=FakeSupervisor()))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with urlopen(f"http://127.0.0.1:{server.server_port}/api/live-agent-health", timeout=4) as response:
                    payload = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

            self.assertEqual(
                payload["processes"]["reasons"],
                {"current-failure": {"event_type": "restart_failed", "reason": "missing launch server"}},
            )

    def test_live_agent_processes_payload_includes_output_only_agent_connection_evidence(self):
        class FakeSupervisor:
            def __init__(self):
                self.groups = [
                    {
                        "group_id": "crew",
                        "status": "running",
                        "agents": [
                            {"agent_id": "agent-a", "display_name": "Agent A"},
                            {"agent_id": "agent-b", "display_name": "Agent B"},
                        ],
                    }
                ]

            def list_groups(self):
                return self.groups

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "live_agents.json").write_text(
                json.dumps({"agents": [{"agent_id": "agent-a", "display_name": "Agent A", "status": "online"}]}),
                encoding="utf-8",
            )
            supervisor = FakeSupervisor()
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root, process_supervisor=supervisor))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with urlopen(f"http://127.0.0.1:{server.server_port}/api/live-agent-processes", timeout=4) as response:
                    payload = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

        connection = payload["groups"][0]["agent_connection"]
        self.assertEqual(connection["expected"], 2)
        self.assertEqual(connection["connected"], 1)
        self.assertEqual(connection["attention"], [{"agent_id": "agent-b", "status": "missing"}])
        self.assertNotIn("agent_connection", supervisor.groups[0])

    def test_live_agent_process_connection_evidence_reports_wrong_meeting(self):
        class FakeSupervisor:
            def list_groups(self):
                return [
                    {
                        "group_id": "crew",
                        "status": "running",
                        "meeting_id": "resident-m1",
                        "agents": [{"agent_id": "agent-a", "display_name": "Agent A"}],
                    }
                ]

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "live_agents.json").write_text(
                json.dumps(
                    {
                        "agents": [
                            {
                                "agent_id": "agent-a",
                                "display_name": "Agent A",
                                "status": "online",
                                "meeting_id": "resident-m2",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root, process_supervisor=FakeSupervisor()))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with urlopen(f"http://127.0.0.1:{server.server_port}/api/live-agent-processes", timeout=4) as response:
                    payload = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

        connection = payload["groups"][0]["agent_connection"]
        self.assertEqual(connection["expected"], 1)
        self.assertEqual(connection["connected"], 0)
        self.assertEqual(connection["attention"], [{"agent_id": "agent-a", "status": "wrong_meeting"}])

    def test_live_agent_process_connection_evidence_reports_provider_kind_mismatch(self):
        class FakeSupervisor:
            def list_groups(self):
                return [
                    {
                        "group_id": "crew",
                        "status": "running",
                        "meeting_id": "resident-m1",
                        "agents": [
                            {
                                "agent_id": "agent-a",
                                "display_name": "Agent A",
                                "provider_kind": "local_cli",
                                "connection_kind": "local_cli",
                            }
                        ],
                    }
                ]

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "live_agents.json").write_text(
                json.dumps(
                    {
                        "agents": [
                            {
                                "agent_id": "agent-a",
                                "display_name": "Manual Agent A",
                                "status": "online",
                                "meeting_id": "resident-m1",
                                "provider_kind": "manual",
                                "connection_kind": "manual",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root, process_supervisor=FakeSupervisor()))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with urlopen(f"http://127.0.0.1:{server.server_port}/api/live-agent-processes", timeout=4) as response:
                    payload = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

        connection = payload["groups"][0]["agent_connection"]
        self.assertEqual(connection["expected"], 1)
        self.assertEqual(connection["connected"], 0)
        self.assertEqual(connection["attention"], [{"agent_id": "agent-a", "status": "provider_kind_mismatch"}])

    def test_live_agent_process_connection_evidence_requires_presence_after_group_start(self):
        class FakeSupervisor:
            def list_groups(self):
                return [
                    {
                        "group_id": "crew",
                        "status": "running",
                        "started_at": "2999-01-01T00:01:00+00:00",
                        "agents": [{"agent_id": "agent-a", "display_name": "Agent A"}],
                    }
                ]

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "live_agents.json").write_text(
                json.dumps(
                    {
                        "agents": [
                            {
                                "agent_id": "agent-a",
                                "display_name": "Agent A",
                                "status": "online",
                                "last_seen_at": "2999-01-01T00:00:00+00:00",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root, process_supervisor=FakeSupervisor()))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with urlopen(f"http://127.0.0.1:{server.server_port}/api/live-agent-processes", timeout=4) as response:
                    payload = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

        connection = payload["groups"][0]["agent_connection"]
        self.assertEqual(connection["expected"], 1)
        self.assertEqual(connection["connected"], 0)
        self.assertEqual(connection["attention"], [{"agent_id": "agent-a", "status": "not_reconnected"}])

    def test_live_agent_process_connection_evidence_is_not_persisted_by_real_supervisor(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            process_path = root / "live-agent-runs" / "processes.json"
            process_path.parent.mkdir(parents=True)
            process_path.write_text(
                json.dumps(
                    {
                        "groups": [
                            {
                                "group_id": "crew",
                                "status": "stopped",
                                "agents": [
                                    {"agent_id": "agent-a", "display_name": "Agent A"},
                                    {"agent_id": "agent-b", "display_name": "Agent B"},
                                ],
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            (root / "live_agents.json").write_text(
                json.dumps({"agents": [{"agent_id": "agent-a", "display_name": "Agent A", "status": "online"}]}),
                encoding="utf-8",
            )
            supervisor = LiveAgentProcessSupervisor(root)
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root, process_supervisor=supervisor))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with urlopen(f"http://127.0.0.1:{server.server_port}/api/live-agent-processes", timeout=4) as response:
                    payload = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()
                supervisor.close()
            persisted = json.loads(process_path.read_text(encoding="utf-8"))

        self.assertEqual(payload["groups"][0]["agent_connection"]["connected"], 1)
        self.assertNotIn("agent_connection", persisted["groups"][0])

    def test_live_agent_health_degrades_when_running_manifest_agent_has_connection_attention(self):
        class FakeSupervisor:
            def __init__(self):
                self.list_called = False

            def list_groups(self):
                self.list_called = True
                raise AssertionError("health endpoint must not refresh process groups")

            def snapshot_groups(self):
                return [
                    {
                        "group_id": "crew",
                        "status": "running",
                        "agents": [
                            {"agent_id": "agent-a", "display_name": "Agent A"},
                            {"agent_id": "agent-b", "display_name": "Agent B"},
                            {"agent_id": "agent-c", "display_name": "Agent C"},
                            {"agent_id": "agent-d", "display_name": "Agent D"},
                            {"agent_id": "agent-e", "display_name": "Agent E"},
                        ],
                    }
                ]

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "live_agents.json").write_text(
                json.dumps(
                    {
                        "agents": [
                            {"agent_id": "agent-a", "display_name": "Agent A", "status": "working"},
                            {
                                "agent_id": "agent-c",
                                "display_name": "Agent C",
                                "status": "online",
                                "last_seen_at": "2020-01-01T00:00:00+00:00",
                            },
                            {"agent_id": "agent-d", "display_name": "Agent D", "status": "offline"},
                            {"agent_id": "agent-e", "display_name": "Agent E", "status": "error"},
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
        self.assertEqual(payload["connections"]["expected"], 5)
        self.assertEqual(payload["connections"]["connected"], 1)
        self.assertEqual(
            payload["connections"]["attention"],
            [
                "crew:agent-b:missing",
                "crew:agent-c:stale",
                "crew:agent-d:offline",
                "crew:agent-e:error",
            ],
        )
        self.assertFalse(supervisor.list_called)

    def test_live_agent_health_degrades_when_manifest_agent_is_attached_to_wrong_meeting(self):
        class FakeSupervisor:
            def snapshot_groups(self):
                return [
                    {
                        "group_id": "crew",
                        "status": "running",
                        "meeting_id": "resident-m1",
                        "agents": [{"agent_id": "agent-a", "display_name": "Agent A"}],
                    }
                ]

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "live_agents.json").write_text(
                json.dumps(
                    {
                        "agents": [
                            {
                                "agent_id": "agent-a",
                                "display_name": "Agent A",
                                "status": "working",
                                "meeting_id": "resident-m2",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root, process_supervisor=FakeSupervisor()))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with urlopen(f"http://127.0.0.1:{server.server_port}/api/live-agent-health", timeout=4) as response:
                    payload = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

        self.assertEqual(payload["status"], "degraded")
        self.assertEqual(payload["connections"]["expected"], 1)
        self.assertEqual(payload["connections"]["connected"], 0)
        self.assertEqual(payload["connections"]["attention"], ["crew:agent-a:wrong_meeting"])

    def test_live_agent_health_degrades_when_manifest_agent_provider_mismatches_presence(self):
        class FakeSupervisor:
            def snapshot_groups(self):
                return [
                    {
                        "group_id": "crew",
                        "status": "running",
                        "meeting_id": "resident-m1",
                        "agents": [
                            {
                                "agent_id": "agent-a",
                                "display_name": "Agent A",
                                "provider_kind": "local_cli",
                                "connection_kind": "local_cli",
                            }
                        ],
                    }
                ]

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "live_agents.json").write_text(
                json.dumps(
                    {
                        "agents": [
                            {
                                "agent_id": "agent-a",
                                "display_name": "Manual Agent A",
                                "status": "online",
                                "meeting_id": "resident-m1",
                                "provider_kind": "manual",
                                "connection_kind": "manual",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root, process_supervisor=FakeSupervisor()))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with urlopen(f"http://127.0.0.1:{server.server_port}/api/live-agent-health", timeout=4) as response:
                    payload = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

        self.assertEqual(payload["status"], "degraded")
        self.assertEqual(payload["connections"]["expected"], 1)
        self.assertEqual(payload["connections"]["connected"], 0)
        self.assertEqual(payload["connections"]["attention"], ["crew:agent-a:provider_kind_mismatch"])

    def test_live_agent_health_sanitizes_connection_attention_id_labels(self):
        class FakeSupervisor:
            def snapshot_groups(self):
                return [
                    {
                        "group_id": "/tmp/secret-group.json",
                        "status": "running",
                        "agents": [{"agent_id": "/tmp/secret-agent.json", "display_name": "Agent A"}],
                    }
                ]

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "live_agents.json").write_text(json.dumps({"agents": []}), encoding="utf-8")
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root, process_supervisor=FakeSupervisor()))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with urlopen(f"http://127.0.0.1:{server.server_port}/api/live-agent-health", timeout=4) as response:
                    payload = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

        self.assertEqual(payload["connections"]["attention"], ["unknown:unknown:missing"])

    def test_live_agent_health_redacts_sensitive_process_and_session_owner_ids(self):
        sensitive_group_token = "ghp_abcdefghijklmnopqrstuvwxyz1234567890"

        class FakeSupervisor:
            def snapshot_groups(self):
                return [
                    {
                        "group_id": "literal:SECRET_GROUP",
                        "status": "running",
                        "meeting_id": "env:SECRET_TOKEN",
                        "agents": [{"agent_id": "agent-a", "display_name": "Agent A"}],
                    },
                    {
                        "group_id": sensitive_group_token,
                        "status": "error",
                        "meeting_id": "env:SECRET_TOKEN",
                        "recent_events": [
                            {
                                "event_type": "stale_watchdog",
                                "reason": "missing manifest agent agent-b",
                            }
                        ],
                        "agents": [{"agent_id": "agent-b", "display_name": "Agent B"}],
                    },
                ]

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "live_agents.json").write_text(json.dumps({"agents": []}), encoding="utf-8")

            payload = live_agent_health_payload(root, FakeSupervisor())

        serialized = json.dumps(payload, ensure_ascii=False)
        self.assertNotIn("SECRET_TOKEN", serialized)
        self.assertNotIn("SECRET_GROUP", serialized)
        self.assertNotIn("literal:", serialized)
        self.assertNotIn("env:", serialized)
        self.assertNotIn(sensitive_group_token, serialized)
        self.assertEqual(payload["processes"]["meeting_ids"], {})
        self.assertEqual(payload["processes"]["attention"], ["missing-process-group-id-2"])
        self.assertEqual(
            payload["processes"]["reasons"],
            {
                "missing-process-group-id-2": {
                    "event_type": "stale_watchdog",
                    "reason": "missing manifest agent agent-b",
                }
            },
        )
        self.assertEqual(payload["connections"]["attention"], ["unknown:agent-a:missing"])
        self.assertEqual(payload["sessions"]["items"], [])

    def test_live_agent_health_redacts_token_like_process_reasons(self):
        sensitive_agent_token = "ghp_abcdefghijklmnopqrstuvwxyz1234567890"

        class FakeSupervisor:
            def snapshot_groups(self):
                return [
                    {
                        "group_id": "resident-main",
                        "status": "error",
                        "meeting_id": "resident-m1",
                        "recent_events": [
                            {
                                "event_type": "stale_watchdog",
                                "reason": f"missing manifest agent {sensitive_agent_token}",
                            }
                        ],
                        "agents": [{"agent_id": "agent-a", "display_name": "Agent A"}],
                    }
                ]

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _write_health_resident_meeting(root, agent_ids=["agent-a"])
            controller = LiveAgentSessionRunController(root)
            run = controller.begin_run(
                action="ensure",
                payload={"meeting_id": "resident-m1", "group_id": "resident-main"},
            )
            controller.finish_run(
                run["run_id"],
                session={
                    "status": "ready",
                    "meeting_id": "resident-m1",
                    "group_id": "resident-main",
                    "action": "none",
                },
            )

            payload = live_agent_health_payload(root, FakeSupervisor())

        serialized = json.dumps(payload, ensure_ascii=False)
        self.assertNotIn(sensitive_agent_token, serialized)
        self.assertEqual(payload["processes"]["reasons"], {})
        self.assertNotIn("process_reason", payload["sessions"]["items"][0])
        self.assertNotIn("process_reason", payload["session_runs"]["items"][0]["readiness"])

    def test_live_agent_health_degrades_when_manifest_agent_has_not_reconnected_after_group_start(self):
        class FakeSupervisor:
            def snapshot_groups(self):
                return [
                    {
                        "group_id": "crew",
                        "status": "running",
                        "started_at": "2999-01-01T00:01:00+00:00",
                        "agents": [{"agent_id": "agent-a", "display_name": "Agent A"}],
                    }
                ]

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "live_agents.json").write_text(
                json.dumps(
                    {
                        "agents": [
                            {
                                "agent_id": "agent-a",
                                "display_name": "Agent A",
                                "status": "working",
                                "last_seen_at": "2999-01-01T00:00:00+00:00",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root, process_supervisor=FakeSupervisor()))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with urlopen(f"http://127.0.0.1:{server.server_port}/api/live-agent-health", timeout=4) as response:
                    payload = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

        self.assertEqual(payload["status"], "degraded")
        self.assertEqual(payload["connections"]["expected"], 1)
        self.assertEqual(payload["connections"]["connected"], 0)
        self.assertEqual(payload["connections"]["attention"], ["crew:agent-a:not_reconnected"])

    def test_live_agent_health_ignores_diagnostic_connection_gaps(self):
        class FakeSupervisor:
            def snapshot_groups(self):
                return [
                    {
                        "group_id": "doctor-smoke",
                        "status": "running",
                        "diagnostic": True,
                        "agents": [{"agent_id": "diagnostic-missing", "display_name": "Diagnostic Missing"}],
                    }
                ]

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "live_agents.json").write_text(json.dumps({"agents": []}), encoding="utf-8")
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root, process_supervisor=FakeSupervisor()))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with urlopen(f"http://127.0.0.1:{server.server_port}/api/live-agent-health", timeout=4) as response:
                    payload = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["connections"], {"expected": 0, "connected": 0, "attention": []})

    def test_live_agent_health_reports_meeting_owned_session_readiness(self):
        class FakeSupervisor:
            def snapshot_groups(self):
                return [
                    {
                        "group_id": "resident-ready",
                        "status": "running",
                        "meeting_id": "meeting-ready",
                        "started_at": "2999-01-01T00:00:00+00:00",
                        "agents": [{"agent_id": "agent-a", "display_name": "Agent A"}],
                    },
                    {
                        "group_id": "resident-missing",
                        "status": "running",
                        "meeting_id": "meeting-missing",
                        "agents": [{"agent_id": "agent-b", "display_name": "Agent B"}],
                    },
                    {
                        "group_id": "resident-error",
                        "status": "error",
                        "meeting_id": "meeting-error",
                        "agents": [{"agent_id": "agent-c", "display_name": "Agent C"}],
                        "recent_events": [
                            {
                                "event_type": "stale_watchdog",
                                "reason": "missing manifest agent agent-c",
                            }
                        ],
                    },
                    {
                        "group_id": "resident-diagnostic",
                        "status": "error",
                        "meeting_id": "meeting-diagnostic",
                        "diagnostic": True,
                        "agents": [{"agent_id": "agent-d", "display_name": "Agent D"}],
                    },
                    {
                        "group_id": "manual-no-meeting",
                        "status": "running",
                        "agents": [{"agent_id": "agent-e", "display_name": "Agent E"}],
                    },
                ]

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            for meeting_id, agent_id in (
                ("meeting-ready", "agent-a"),
                ("meeting-missing", "agent-b"),
                ("meeting-error", "agent-c"),
            ):
                meeting_dir = root / "meetings" / meeting_id
                meeting_dir.mkdir(parents=True)
                write_live_state(
                    meeting_dir,
                    {
                        "meeting_id": meeting_id,
                        "agent_bindings": [
                            {"role_id": "resident", "agent_id": agent_id, "provider_id": "local-provider"}
                        ],
                        "provider_configs": {"local-provider": {"kind": "local_cli"}},
                    },
                )
            (root / "live_agents.json").write_text(
                json.dumps(
                    {
                        "agents": [
                            {
                                "agent_id": "agent-a",
                                "display_name": "Agent A",
                                "status": "online",
                                "meeting_id": "meeting-ready",
                                "provider_kind": "local_cli",
                                "connection_kind": "local_cli",
                                "last_seen_at": "2999-01-01T00:00:01+00:00",
                            },
                            {
                                "agent_id": "agent-c",
                                "display_name": "Agent C",
                                "status": "online",
                                "meeting_id": "meeting-error",
                                "provider_kind": "local_cli",
                                "connection_kind": "local_cli",
                            },
                        ]
                    }
                ),
                encoding="utf-8",
            )
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root, process_supervisor=FakeSupervisor()))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with urlopen(f"http://127.0.0.1:{server.server_port}/api/live-agent-health", timeout=4) as response:
                    payload = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

        self.assertEqual(payload["status"], "degraded")
        self.assertEqual(payload["sessions"]["total"], 3)
        self.assertEqual(payload["sessions"]["ready"], 1)
        self.assertEqual(payload["sessions"]["degraded"], 2)
        self.assertEqual(
            payload["sessions"]["attention"],
            [
                "meeting-missing:resident-missing:agent-b:missing",
                "meeting-error:resident-error:group:error",
            ],
        )
        self.assertEqual(
            payload["sessions"]["items"],
            [
                {
                    "meeting_id": "meeting-ready",
                    "group_id": "resident-ready",
                    "status": "ready",
                    "process_status": "running",
                    "expected": 1,
                    "connected": 1,
                    "ownership_attention": [],
                    "process_attention": [],
                    "connection_attention": [],
                    "attention": [],
                },
                {
                    "meeting_id": "meeting-missing",
                    "group_id": "resident-missing",
                    "status": "degraded",
                    "process_status": "running",
                    "expected": 1,
                    "connected": 0,
                    "ownership_attention": [],
                    "process_attention": [],
                    "connection_attention": ["agent-b:missing"],
                    "attention": ["agent-b:missing"],
                },
                {
                    "meeting_id": "meeting-error",
                    "group_id": "resident-error",
                    "status": "degraded",
                    "process_status": "error",
                    "expected": 1,
                    "connected": 1,
                    "ownership_attention": [],
                    "process_attention": ["group:error"],
                    "connection_attention": [],
                    "attention": ["group:error"],
                    "process_reason": {
                        "event_type": "stale_watchdog",
                        "reason": "missing manifest agent agent-c",
                    },
                },
            ],
        )
        session_blob = json.dumps(payload["sessions"], ensure_ascii=False)
        self.assertNotIn("meeting-diagnostic", session_blob)
        self.assertNotIn("manual-no-meeting", session_blob)

    def test_live_agent_health_session_readiness_degrades_missing_binding_provider_config(self):
        class FakeSupervisor:
            def snapshot_groups(self):
                return [
                    {
                        "group_id": "resident-main",
                        "status": "running",
                        "meeting_id": "resident-m1",
                        "agents": [
                            {
                                "agent_id": "agent-a",
                                "provider_kind": "local_cli",
                                "connection_kind": "local_cli",
                            }
                        ],
                    }
                ]

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            meeting_dir = root / "meetings" / "resident-m1"
            meeting_dir.mkdir(parents=True)
            write_live_state(
                meeting_dir,
                {
                    "meeting_id": "resident-m1",
                    "agent_bindings": [
                        {
                            "role_id": "architect",
                            "agent_id": "agent-a",
                            "provider_id": "missing-provider",
                        }
                    ],
                    "provider_configs": {},
                },
            )
            connect_live_agent(
                root,
                {
                    "agent_id": "agent-a",
                    "display_name": "Agent A",
                    "provider_kind": "local_cli",
                    "connection_kind": "local_cli",
                    "meeting_id": "resident-m1",
                    "status": "online",
                },
            )

            payload = live_agent_health_payload(root, FakeSupervisor())

        self.assertEqual(payload["status"], "degraded")
        self.assertEqual(payload["sessions"]["ready"], 0)
        self.assertEqual(payload["sessions"]["degraded"], 1)
        self.assertEqual(payload["sessions"]["attention"], ["resident-m1:resident-main:agent-a:binding_provider_missing"])
        self.assertEqual(payload["sessions"]["items"][0]["connected"], 0)
        self.assertEqual(payload["sessions"]["items"][0]["connection_attention"], ["agent-a:binding_provider_missing"])
        self.assertEqual(payload["connections"]["connected"], 1)
        self.assertEqual(payload["connections"]["attention"], [])
        self.assertEqual(payload["admission"]["attention"], ["resident-m1:agent-a:meeting_missing"])
        payload_blob = json.dumps(payload, ensure_ascii=False)
        self.assertNotIn("missing-provider", payload_blob)

    def test_live_agent_health_marks_owned_group_with_missing_meeting_degraded(self):
        class FakeSupervisor:
            def __init__(self):
                self.list_called = False

            def list_groups(self):
                self.list_called = True
                raise AssertionError("health endpoint must not refresh process groups")

            def snapshot_groups(self):
                return [
                    {
                        "group_id": "resident-missing-meeting",
                        "status": "running",
                        "meeting_id": "missing-meeting",
                        "config_path": "/tmp/secret-live-agents.json",
                        "log_tail": "secret provider output",
                        "agents": [{"agent_id": "agent-a", "display_name": "Agent A"}],
                    }
                ]

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "live_agents.json").write_text(json.dumps({"agents": []}), encoding="utf-8")
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root, process_supervisor=FakeSupervisor()))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with urlopen(f"http://127.0.0.1:{server.server_port}/api/live-agent-health", timeout=4) as response:
                    payload = json.loads(response.read().decode("utf-8"))
                with urlopen(f"http://127.0.0.1:{server.server_port}/api/live-agent-operations", timeout=4) as response:
                    operations = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

        self.assertEqual(payload["status"], "degraded")
        self.assertEqual(payload["sessions"]["total"], 1)
        self.assertEqual(payload["sessions"]["ready"], 0)
        self.assertEqual(payload["sessions"]["degraded"], 1)
        self.assertEqual(payload["sessions"]["attention"], ["missing-meeting:resident-missing-meeting:meeting:missing"])
        self.assertEqual(
            payload["sessions"]["items"][0],
            {
                "meeting_id": "missing-meeting",
                "group_id": "resident-missing-meeting",
                "status": "degraded",
                "process_status": "running",
                "expected": 1,
                "connected": 0,
                "ownership_attention": [],
                "process_attention": [],
                "connection_attention": [],
                "attention": ["meeting:missing"],
            },
        )
        session_blob = json.dumps(payload["sessions"], ensure_ascii=False)
        self.assertNotIn("/tmp/secret-live-agents.json", session_blob)
        self.assertNotIn("secret provider output", session_blob)
        self.assertEqual(operations["operations"], [])

    def test_live_agent_health_sanitizes_session_status_attention(self):
        class FakeSupervisor:
            def snapshot_groups(self):
                return [
                    {
                        "group_id": "resident-main",
                        "status": "error live-agents.json /tmp/secret",
                        "meeting_id": "resident-m1",
                        "agents": [{"agent_id": "agent-a", "display_name": "Agent A"}],
                    }
                ]

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            meeting_dir = root / "meetings" / "resident-m1"
            meeting_dir.mkdir(parents=True)
            write_live_state(
                meeting_dir,
                {
                    "meeting_id": "resident-m1",
                    "agent_bindings": [{"role_id": "resident", "agent_id": "agent-a", "provider_id": "local-provider"}],
                    "provider_configs": {"local-provider": {"kind": "local_cli"}},
                },
            )
            (root / "live_agents.json").write_text(
                json.dumps(
                    {
                        "agents": [
                            {
                                "agent_id": "agent-a",
                                "display_name": "Agent A",
                                "status": "offline /tmp/secret log_tail",
                                "meeting_id": "resident-m1",
                                "provider_kind": "local_cli",
                                "connection_kind": "local_cli",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root, process_supervisor=FakeSupervisor()))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with urlopen(f"http://127.0.0.1:{server.server_port}/api/live-agent-health", timeout=4) as response:
                    payload = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

        self.assertEqual(payload["sessions"]["items"][0]["process_status"], "unknown")
        self.assertEqual(payload["sessions"]["items"][0]["process_attention"], ["group:unknown"])
        self.assertEqual(payload["sessions"]["items"][0]["connection_attention"], ["agent-a:offline"])
        session_blob = json.dumps(payload["sessions"], ensure_ascii=False)
        self.assertNotIn("/tmp/secret", session_blob)
        self.assertNotIn("live-agents.json", session_blob)
        self.assertNotIn("log_tail", session_blob)

    def test_live_agent_health_degrades_duplicate_active_meeting_session_groups(self):
        class FakeSupervisor:
            def snapshot_groups(self):
                return [
                    {
                        "group_id": "resident-main",
                        "status": "running",
                        "meeting_id": "resident-m1",
                        "agents": [{"agent_id": "agent-a", "display_name": "Agent A"}],
                    },
                    {
                        "group_id": "resident-shadow",
                        "status": "running",
                        "meeting_id": "resident-m1",
                        "agents": [{"agent_id": "agent-a", "display_name": "Agent A"}],
                    },
                    {
                        "group_id": "resident-stopped",
                        "status": "stopped",
                        "meeting_id": "resident-m1",
                        "agents": [{"agent_id": "agent-a", "display_name": "Agent A"}],
                    },
                    {
                        "group_id": "resident-diagnostic",
                        "status": "running",
                        "meeting_id": "resident-m1",
                        "diagnostic": True,
                        "agents": [{"agent_id": "agent-a", "display_name": "Agent A"}],
                    },
                ]

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            meeting_dir = root / "meetings" / "resident-m1"
            meeting_dir.mkdir(parents=True)
            write_live_state(
                meeting_dir,
                {
                    "meeting_id": "resident-m1",
                    "agent_bindings": [{"role_id": "resident", "agent_id": "agent-a", "provider_id": "local-provider"}],
                    "provider_configs": {"local-provider": {"kind": "local_cli"}},
                },
            )
            (root / "live_agents.json").write_text(
                json.dumps(
                    {
                        "agents": [
                            {
                                "agent_id": "agent-a",
                                "display_name": "Agent A",
                                "status": "online",
                                "meeting_id": "resident-m1",
                                "provider_kind": "local_cli",
                                "connection_kind": "local_cli",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root, process_supervisor=FakeSupervisor()))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with urlopen(f"http://127.0.0.1:{server.server_port}/api/live-agent-health", timeout=4) as response:
                    payload = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

        self.assertEqual(payload["status"], "degraded")
        self.assertEqual(payload["sessions"]["total"], 3)
        self.assertEqual(payload["sessions"]["ready"], 0)
        self.assertEqual(payload["sessions"]["degraded"], 3)
        self.assertEqual(
            payload["sessions"]["attention"],
            [
                "resident-m1:resident-main:meeting:duplicate_active_group",
                "resident-m1:resident-shadow:meeting:duplicate_active_group",
                "resident-m1:resident-stopped:group:stopped",
            ],
        )
        items_by_group = {item["group_id"]: item for item in payload["sessions"]["items"]}
        self.assertEqual(items_by_group["resident-main"]["ownership_attention"], ["meeting:duplicate_active_group"])
        self.assertEqual(items_by_group["resident-shadow"]["ownership_attention"], ["meeting:duplicate_active_group"])
        self.assertEqual(items_by_group["resident-stopped"]["ownership_attention"], [])
        self.assertNotIn("resident-diagnostic", json.dumps(payload["sessions"], ensure_ascii=False))

    def test_live_agent_probe_endpoint_records_success_without_message_text(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            result = {
                "status": "ok",
                "agent_id": "agent-a",
                "source_event_id": "probe-source",
                "reply_event_id": "reply-event",
                "reply": {"id": "reply-event", "actor_id": "agent-a", "message": "secret probe reply"},
            }
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root, process_supervisor=LiveAgentProcessSupervisor(root)))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with patch("agentsassemble.gui.run_live_agent_probe", return_value=result) as probe:
                    request = Request(
                        f"http://127.0.0.1:{server.server_port}/api/live-agents/agent-a/probe",
                        data=json.dumps({"timeout_seconds": 3}).encode("utf-8"),
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

        probe.assert_called_once()
        self.assertEqual(probe.call_args.args[:2], (root, "agent-a"))
        self.assertEqual(probe.call_args.kwargs["timeout_seconds"], 3.0)
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["reply_event_id"], "reply-event")
        self.assertEqual(operations["operations"][0]["operation"], "probe.run")
        self.assertEqual(operations["operations"][0]["status"], "success")
        self.assertEqual(operations["operations"][0]["target_id"], "agent-a")
        self.assertEqual(operations["operations"][0]["details"]["result_status"], "ok")
        self.assertEqual(operations["operations"][0]["details"]["timeout_seconds"], 3.0)
        operation_text = json.dumps(operations, ensure_ascii=False)
        self.assertNotIn("secret probe reply", operation_text)

    def test_live_agent_probe_endpoint_records_effective_timeout_cap(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            result = {"status": "timeout", "agent_id": "agent-a", "source_event_id": "probe-source"}
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root, process_supervisor=LiveAgentProcessSupervisor(root)))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with patch("agentsassemble.gui.run_live_agent_probe", return_value=result) as probe:
                    request = Request(
                        f"http://127.0.0.1:{server.server_port}/api/live-agents/agent-a/probe",
                        data=json.dumps({"timeout_seconds": 300}).encode("utf-8"),
                        headers={"Content-Type": "application/json"},
                        method="POST",
                    )
                    with urlopen(request, timeout=4) as response:
                        json.loads(response.read().decode("utf-8"))
                with urlopen(
                    f"http://127.0.0.1:{server.server_port}/api/live-agent-operations",
                    timeout=4,
                ) as response:
                    operations = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

        self.assertEqual(probe.call_args.kwargs["timeout_seconds"], 60.0)
        self.assertEqual(operations["operations"][0]["details"]["timeout_seconds"], 60.0)

    def test_live_agent_probe_endpoint_records_timeout_and_unknown_agent(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root, process_supervisor=LiveAgentProcessSupervisor(root)))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                timeout_result = {"status": "timeout", "agent_id": "agent-a", "source_event_id": "probe-source"}
                with patch("agentsassemble.gui.run_live_agent_probe", return_value=timeout_result):
                    request = Request(
                        f"http://127.0.0.1:{server.server_port}/api/live-agents/agent-a/probe",
                        data=b"{}",
                        headers={"Content-Type": "application/json"},
                        method="POST",
                    )
                    with urlopen(request, timeout=4) as response:
                        payload = json.loads(response.read().decode("utf-8"))
                with patch("agentsassemble.gui.run_live_agent_probe", side_effect=ValueError("Live agent missing was not found.")):
                    missing_request = Request(
                        f"http://127.0.0.1:{server.server_port}/api/live-agents/missing/probe",
                        data=json.dumps({"timeout_seconds": 300}).encode("utf-8"),
                        headers={"Content-Type": "application/json"},
                        method="POST",
                    )
                    with self.assertRaises(HTTPError) as error:
                        urlopen(missing_request, timeout=4)
                    error.exception.read()
                    error.exception.close()
                with urlopen(
                    f"http://127.0.0.1:{server.server_port}/api/live-agent-operations",
                    timeout=4,
                ) as response:
                    operations = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

        self.assertEqual(payload["status"], "timeout")
        self.assertEqual(error.exception.code, 404)
        self.assertEqual(
            [(operation["operation"], operation["status"], operation["target_id"]) for operation in operations["operations"]],
            [("probe.run", "failed", "agent-a"), ("probe.run", "failed", "missing")],
        )
        self.assertEqual(operations["operations"][1]["details"]["timeout_seconds"], 60.0)

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
                            live_agent_stale_restart_after_seconds=120,
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
        self.assertEqual(started["stale_restart_after_seconds"], 120)
        self.assertEqual(operations["operation"], "process.autostart")
        self.assertEqual(operations["status"], "success")
        self.assertEqual(operations["target_id"], "boot")
        self.assertEqual(operations["details"]["stale_restart_after_seconds"], 120)
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
            self.assertEqual(payload["agent"]["join_semantics"], "stateless_prompt_call")
            self.assertEqual(payload["agent"]["context_durability"], "stateless_prompt")
            self.assertEqual(payload["agent"]["sandbox_enforcement"], "advisory")
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
                            "join_semantics": "env:SECRET_TOKEN",
                            "context_durability": "/private/provider-context",
                            "sandbox_enforcement": "os_sandboxed",
                        }
                    ).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urlopen(request, timeout=4) as response:
                    payload = json.loads(response.read().decode("utf-8"))
                with urlopen(f"http://127.0.0.1:{server.server_port}/api/live-agents", timeout=4) as response:
                    listed = json.loads(response.read().decode("utf-8"))
                with urlopen(f"http://127.0.0.1:{server.server_port}/api/live-agents?safe=0", timeout=4) as response:
                    explicit_raw = json.loads(response.read().decode("utf-8"))
                with urlopen(f"http://127.0.0.1:{server.server_port}/api/live-agent-operations", timeout=4) as response:
                    operations = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

            self.assertEqual(payload["agent"]["agent_id"], "gemini-cli")
            self.assertEqual(listed["agents"][0]["session_id"], "gemini-session")
            self.assertEqual(explicit_raw["agents"][0]["session_id"], "gemini-session")
            self.assertIsInstance(listed["agents"][0]["heartbeat_age_seconds"], int)
            self.assertGreaterEqual(listed["agents"][0]["heartbeat_age_seconds"], 0)
            self.assertEqual(listed["agents"][0]["stale_after_seconds"], 180)
            register_operations = [item for item in operations["operations"] if item["operation"] == "live_agent.register"]
            self.assertEqual(len(register_operations), 1)
            self.assertEqual(register_operations[0]["status"], "success")
            self.assertEqual(register_operations[0]["target_id"], "gemini-cli")
            self.assertEqual(register_operations[0]["details"]["agent_id"], "gemini-cli")
            self.assertEqual(register_operations[0]["details"]["provider_kind"], "gemini")
            self.assertEqual(register_operations[0]["details"]["connection_kind"], "local_cli")
            self.assertEqual(register_operations[0]["details"]["join_semantics"], "stateless_prompt_call")
            self.assertEqual(register_operations[0]["details"]["context_durability"], "stateless_prompt")
            self.assertEqual(register_operations[0]["details"]["sandbox_enforcement"], "advisory")
            self.assertEqual(register_operations[0]["details"]["registered_status"], "online")
            self.assertEqual(register_operations[0]["details"]["admission_status"], "lobby_only")
            self.assertFalse(register_operations[0]["details"]["host_approved_binding"])
            self.assertNotIn("session_id", register_operations[0]["details"])
            self.assertNotIn("gemini-session", json.dumps(operations, ensure_ascii=False))
            self.assertNotIn("SECRET_TOKEN", json.dumps(operations, ensure_ascii=False))
            self.assertNotIn("/private", json.dumps(operations, ensure_ascii=False))

    def test_live_agent_register_operation_records_bound_meeting_admission(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            meeting_dir = root / "meetings" / "resident-m1"
            meeting_dir.mkdir(parents=True)
            (meeting_dir / "meeting.json").write_text(
                json.dumps(
                    {
                        "meeting_id": "resident-m1",
                        "agent_bindings": [
                            {
                                "agent_id": "agent-a",
                                "role_id": "architect",
                                "provider_id": "local-cli",
                                "permission_profile_id": "meeting_readonly",
                                "join_mode": "fresh",
                            }
                        ],
                        "provider_configs": {
                            "local-cli": {
                                "id": "local-cli",
                                "kind": "local_cli",
                                "display_name": "Local CLI",
                            }
                        },
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
                    f"http://127.0.0.1:{server.server_port}/api/live-agents",
                    data=json.dumps(
                        {
                            "agent_id": "agent-a",
                            "display_name": "Agent A",
                            "provider_kind": "local_cli",
                            "connection_kind": "local_cli",
                            "meeting_id": "resident-m1",
                            "session_id": "private-session-id",
                        }
                    ).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urlopen(request, timeout=4) as response:
                    response.read()
                with urlopen(f"http://127.0.0.1:{server.server_port}/api/live-agent-operations", timeout=4) as response:
                    operations = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

            register_operation = next(item for item in operations["operations"] if item["operation"] == "live_agent.register")
            details = register_operation["details"]
            self.assertEqual(details["admission_status"], "bound_to_meeting")
            self.assertTrue(details["host_approved_binding"])
            self.assertEqual(details["binding_role_id"], "architect")
            self.assertEqual(details["binding_provider_id"], "local-cli")
            self.assertEqual(details["binding_provider_kind"], "local_cli")
            self.assertEqual(details["binding_permission_profile_id"], "meeting_readonly")
            self.assertEqual(details["binding_join_mode"], "fresh")
            self.assertNotIn("session_id", details)
            self.assertNotIn("private-session-id", json.dumps(operations, ensure_ascii=False))

    def test_live_agent_register_operation_records_unbound_and_conflicting_meeting_admission(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            meeting_dir = root / "meetings" / "resident-m1"
            meeting_dir.mkdir(parents=True)
            (meeting_dir / "meeting.json").write_text(
                json.dumps(
                    {
                        "meeting_id": "resident-m1",
                        "agent_bindings": [
                            {
                                "agent_id": "agent-a",
                                "role_id": "architect",
                                "provider_id": "local-cli",
                                "permission_profile_id": "meeting_readonly",
                            }
                        ],
                        "provider_configs": {"local-cli": {"id": "local-cli", "kind": "local_cli"}},
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                for payload in (
                    {
                        "agent_id": "guest-agent",
                        "display_name": "Guest Agent",
                        "provider_kind": "manual",
                        "connection_kind": "manual",
                        "meeting_id": "resident-m1",
                    },
                    {
                        "agent_id": "agent-a",
                        "display_name": "Agent A",
                        "provider_kind": "manual",
                        "connection_kind": "manual",
                        "meeting_id": "resident-m1",
                    },
                ):
                    request = Request(
                        f"http://127.0.0.1:{server.server_port}/api/live-agents",
                        data=json.dumps(payload).encode("utf-8"),
                        headers={"Content-Type": "application/json"},
                        method="POST",
                    )
                    with urlopen(request, timeout=4) as response:
                        response.read()
                with urlopen(f"http://127.0.0.1:{server.server_port}/api/live-agent-operations", timeout=4) as response:
                    operations = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

            by_target = {
                item["target_id"]: item["details"]
                for item in operations["operations"]
                if item["operation"] == "live_agent.register"
            }
            self.assertEqual(by_target["guest-agent"]["admission_status"], "meeting_lobby_only")
            self.assertFalse(by_target["guest-agent"]["host_approved_binding"])
            self.assertEqual(by_target["agent-a"]["admission_status"], "binding_conflict")
            self.assertFalse(by_target["agent-a"]["host_approved_binding"])
            self.assertEqual(by_target["agent-a"]["binding_role_id"], "architect")
            self.assertEqual(by_target["agent-a"]["binding_conflicts"], ["provider_kind_mismatch"])

    def test_live_agent_register_operation_records_missing_meeting_and_missing_provider_admission(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            meeting_dir = root / "meetings" / "resident-m1"
            meeting_dir.mkdir(parents=True)
            (meeting_dir / "meeting.json").write_text(
                json.dumps(
                    {
                        "meeting_id": "resident-m1",
                        "agent_bindings": [
                            {
                                "agent_id": "agent-b",
                                "role_id": "critic",
                                "provider_id": "missing-provider",
                                "permission_profile_id": "meeting_readonly",
                            }
                        ],
                        "provider_configs": {},
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                for payload in (
                    {
                        "agent_id": "lost-agent",
                        "display_name": "Lost Agent",
                        "provider_kind": "manual",
                        "connection_kind": "manual",
                        "meeting_id": "missing-meeting",
                        "session_id": "lost-secret-session",
                        "endpoint": "https://secret.example/leak",
                        "auth_ref": "literal:top-secret",
                        "config_path": "/Users/me/private-config.json",
                        "prompt": "hidden prompt phrase",
                        "provider_output": "raw-provider-output",
                    },
                    {
                        "agent_id": "agent-b",
                        "display_name": "Agent B",
                        "provider_kind": "local_cli",
                        "connection_kind": "local_cli",
                        "meeting_id": "resident-m1",
                    },
                ):
                    request = Request(
                        f"http://127.0.0.1:{server.server_port}/api/live-agents",
                        data=json.dumps(payload).encode("utf-8"),
                        headers={"Content-Type": "application/json"},
                        method="POST",
                    )
                    with urlopen(request, timeout=4) as response:
                        response.read()
                with urlopen(f"http://127.0.0.1:{server.server_port}/api/live-agent-operations", timeout=4) as response:
                    operations = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

            by_target = {
                item["target_id"]: item["details"]
                for item in operations["operations"]
                if item["operation"] == "live_agent.register"
            }
            self.assertEqual(by_target["lost-agent"]["admission_status"], "meeting_missing")
            self.assertFalse(by_target["lost-agent"]["host_approved_binding"])
            self.assertEqual(by_target["agent-b"]["admission_status"], "binding_conflict")
            self.assertFalse(by_target["agent-b"]["host_approved_binding"])
            self.assertEqual(by_target["agent-b"]["binding_provider_id"], "missing-provider")
            self.assertEqual(by_target["agent-b"]["binding_conflicts"], ["binding_provider_missing"])
            persisted = json.dumps(operations, ensure_ascii=False)
            self.assertNotIn("lost-secret-session", persisted)
            self.assertNotIn("https://secret.example/leak", persisted)
            self.assertNotIn("literal:top-secret", persisted)
            self.assertNotIn("/Users/me/private-config.json", persisted)
            self.assertNotIn("hidden prompt phrase", persisted)
            self.assertNotIn("raw-provider-output", persisted)

    def test_live_agent_http_endpoint_records_invalid_registration_json_operation(self):
        invalid_payloads = [
            ("malformed", b"{not json"),
            ("non_object", json.dumps(["not", "an", "object"]).encode("utf-8")),
            ("invalid_utf8", b"\xff"),
        ]
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                for _, body in invalid_payloads:
                    request = Request(
                        f"http://127.0.0.1:{server.server_port}/api/live-agents",
                        data=body,
                        headers={"Content-Type": "application/json"},
                        method="POST",
                    )
                    try:
                        urlopen(request, timeout=4)
                    except HTTPError as error:
                        self.assertEqual(error.code, 400)
                        error.read()
                        error.close()
                    else:
                        self.fail("invalid registration JSON should return HTTP 400")
                with urlopen(f"http://127.0.0.1:{server.server_port}/api/live-agent-operations", timeout=4) as response:
                    operations = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

            register_operations = [
                item for item in operations["operations"] if item["operation"] == "live_agent.register"
            ]
            self.assertEqual(len(register_operations), len(invalid_payloads))
            for operation in register_operations:
                self.assertEqual(operation["status"], "failed")
                self.assertEqual(operation["error"], "Invalid JSON")
                self.assertEqual(operation["target_id"], "")
                self.assertNotIn("session_id", operation.get("details", {}))
            operation_text = json.dumps(operations, ensure_ascii=False)
            self.assertNotIn("not json", operation_text)
            self.assertNotIn("not\", \"an\", \"object", operation_text)

    def test_live_agent_join_brief_http_endpoint_returns_safe_packet_without_registering(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                server_url = f"http://127.0.0.1:{server.server_port}"
                before_paths = sorted(path.relative_to(root).as_posix() for path in root.rglob("*"))
                request = Request(
                    f"{server_url}/api/live-agent-join-brief",
                    data=json.dumps(
                        {
                            "agent_id": "external-reviewer",
                            "display_name": "External Reviewer",
                            "provider_kind": "manual",
                            "connection_kind": "manual",
                            "meeting_id": "resident-m1",
                            "engagement_mode": "watch",
                            "timeout": 9,
                            "poll_interval": 0.5,
                            "max_chain_depth": 2,
                        }
                    ).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urlopen(request, timeout=4) as response:
                    payload = json.loads(response.read().decode("utf-8"))
                with urlopen(f"{server_url}/api/live-agents", timeout=4) as response:
                    roster = json.loads(response.read().decode("utf-8"))
                after_paths = sorted(path.relative_to(root).as_posix() for path in root.rglob("*"))
            finally:
                server.shutdown()
                server.server_close()

            self.assertEqual(payload["status"], "generated")
            self.assertEqual(payload["agent"]["agent_id"], "external-reviewer")
            self.assertEqual(payload["agent"]["meeting_id"], "resident-m1")
            self.assertEqual(payload["commands"]["register"][5:7], ["--server", server_url])
            self.assertIn("wait-next", payload["commands"]["wait_next"])
            self.assertIn("--max-chain-depth", payload["commands"]["wait_next"])
            self.assertIn("2", payload["commands"]["wait_next"])
            self.assertEqual(payload["commands"]["leave"][5:7], ["--server", server_url])
            self.assertIn("leave", payload["commands"]["leave"])
            self.assertIn("For observe_lobby actions, run the returned ack_command and do not post a reply.", payload["instructions"])
            self.assertIn("Run commands.leave before intentionally exiting the room.", payload["instructions"])
            self.assertEqual(payload["templates"]["say"][-2:], ["--", "{message}"])
            self.assertEqual(payload["safety"]["room_contacted"], False)
            self.assertEqual(payload["safety"]["provider_executed"], False)
            self.assertEqual(roster["agents"], [])
            self.assertEqual(after_paths, before_paths)
            self.assertFalse((root / "live-agent-runs" / "operations.jsonl").exists())
            serialized = json.dumps(payload)
            self.assertNotIn("endpoint", serialized)
            self.assertNotIn("auth", serialized)
            self.assertNotIn("session_id", serialized)
            self.assertNotIn("config_path", serialized)
            self.assertNotIn("log_path", serialized)

    def test_live_agent_join_brief_http_endpoint_rejects_nested_values_before_echo(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/live-agent-join-brief",
                    data=json.dumps(
                        {
                            "agent_id": "external-reviewer",
                            "display_name": {"session_id": "secret-session", "prompt": "private prompt"},
                            "provider_kind": ["auth_ref=TOKEN", "provider output"],
                            "server": {"endpoint": "https://example.invalid/private", "config_path": "/tmp/private.json"},
                            "meeting_id": {"reply": "private reply"},
                        }
                    ).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with self.assertRaises(HTTPError) as raised:
                    urlopen(request, timeout=4)
                with urlopen(f"http://127.0.0.1:{server.server_port}/api/live-agents", timeout=4) as response:
                    roster = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

            self.assertEqual(raised.exception.code, 400)
            body = raised.exception.read().decode("utf-8")
            raised.exception.close()
            self.assertNotIn("secret-session", body)
            self.assertNotIn("TOKEN", body)
            self.assertNotIn("private prompt", body)
            self.assertNotIn("private reply", body)
            self.assertEqual(roster["agents"], [])
            self.assertFalse((root / "live-agent-runs" / "operations.jsonl").exists())

    def test_live_agent_http_endpoint_filters_roster_by_meeting_agent_and_status(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            connect_live_agent(
                root,
                {
                    "agent_id": "agent-a",
                    "display_name": "Agent A",
                    "provider_kind": "local_cli",
                    "connection_kind": "local_cli",
                    "meeting_id": "resident-m1",
                },
            )
            connect_live_agent(
                root,
                {
                    "agent_id": "agent-b",
                    "display_name": "Agent B",
                    "provider_kind": "local_cli",
                    "connection_kind": "local_cli",
                    "meeting_id": "resident-m1",
                },
            )
            connect_live_agent(
                root,
                {
                    "agent_id": "agent-c",
                    "display_name": "Agent C",
                    "provider_kind": "local_cli",
                    "connection_kind": "local_cli",
                    "meeting_id": "resident-m2",
                },
            )
            heartbeat_live_agent(root, "agent-a", status="working")
            heartbeat_live_agent(root, "agent-c", status="working")
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with urlopen(
                    (
                        f"http://127.0.0.1:{server.server_port}/api/live-agents"
                        "?meeting_id=resident-m1&agent_id=agent-a&status=working"
                    ),
                    timeout=4,
                ) as response:
                    listed = json.loads(response.read().decode("utf-8"))
                with urlopen(
                    f"http://127.0.0.1:{server.server_port}/api/live-agents?meeting_id=resident-m1&status=working",
                    timeout=4,
                ) as response:
                    meeting_working = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

            self.assertEqual([agent["agent_id"] for agent in listed["agents"]], ["agent-a"])
            self.assertEqual([agent["agent_id"] for agent in meeting_working["agents"]], ["agent-a"])

    def test_live_agent_http_endpoint_safe_projection_redacts_roster_fields(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            connect_live_agent(
                root,
                {
                    "agent_id": "agent-a",
                    "display_name": "Agent A",
                    "provider_kind": "remote_bridge",
                    "connection_kind": "remote_bridge",
                    "meeting_id": "resident-m1",
                    "session_id": "private-session",
                    "endpoint": "http://secret.local:8777/bridge",
                    "last_error": "token=secret-token config /Users/seinel/private/live-agents.json",
                },
            )
            state_path = root / "live_agents.json"
            state = json.loads(state_path.read_text(encoding="utf-8"))
            state["agents"][0]["auth_ref"] = "env:SECRET_TOKEN"
            state["agents"][0]["config_path"] = "/Users/seinel/private/live-agents.json"
            state["agents"][0]["join_semantics"] = "codex_exec_resume"
            state["agents"][0]["context_durability"] = "provider_managed_resume"
            state_path.write_text(json.dumps(state), encoding="utf-8")
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with urlopen(f"http://127.0.0.1:{server.server_port}/api/live-agents?safe=1", timeout=4) as response:
                    listed = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

            agent = listed["agents"][0]
            self.assertEqual(agent["agent_id"], "agent-a")
            self.assertEqual(agent["meeting_id"], "resident-m1")
            self.assertEqual(agent["join_semantics"], "remote_bridge_room_loop")
            self.assertEqual(agent["context_durability"], "remote_owner_managed")
            self.assertEqual(agent["last_error"], "Live-agent presence error details redacted.")
            self.assertNotIn("session_id", agent)
            self.assertNotIn("endpoint", agent)
            self.assertNotIn("auth_ref", agent)
            self.assertNotIn("config_path", agent)
            encoded = json.dumps(listed)
            self.assertNotIn("secret.local", encoded)
            self.assertNotIn("secret-token", encoded)
            self.assertNotIn("private-session", encoded)
            self.assertNotIn("live-agents.json", encoded)

    def test_live_agent_http_endpoint_safe_projection_derives_admission_evidence(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            meeting_dir = root / "meetings" / "resident-m1"
            meeting_dir.mkdir(parents=True)
            (meeting_dir / "meeting.json").write_text(
                json.dumps(
                    {
                        "meeting_id": "resident-m1",
                        "agent_bindings": [
                            {
                                "agent_id": "agent-a",
                                "role_id": "architect",
                                "provider_id": "local-cli",
                                "permission_profile_id": "meeting_readonly",
                                "join_mode": "resident",
                            },
                            {
                                "agent_id": "agent-b",
                                "role_id": "critic",
                                "provider_id": "local-cli",
                                "permission_profile_id": "meeting_readonly",
                            },
                        ],
                        "provider_configs": {
                            "local-cli": {
                                "id": "local-cli",
                                "kind": "local_cli",
                                "display_name": "Local CLI",
                            }
                        },
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            connect_live_agent(
                root,
                {
                    "agent_id": "agent-a",
                    "display_name": "Agent A",
                    "provider_kind": "local_cli",
                    "connection_kind": "local_cli",
                    "meeting_id": "resident-m1",
                    "session_id": "private-session-a",
                },
            )
            connect_live_agent(
                root,
                {
                    "agent_id": "agent-b",
                    "display_name": "Agent B",
                    "provider_kind": "manual",
                    "connection_kind": "manual",
                    "meeting_id": "resident-m1",
                    "session_id": "private-session-b",
                },
            )
            connect_live_agent(
                root,
                {
                    "agent_id": "guest-agent",
                    "display_name": "Guest Agent",
                    "provider_kind": "manual",
                    "connection_kind": "manual",
                    "meeting_id": "resident-m1",
                    "session_id": "private-session-c",
                },
            )
            state_path = root / "live_agents.json"
            state = json.loads(state_path.read_text(encoding="utf-8"))
            state["agents"][2]["admission_status"] = "bound_to_meeting"
            state["agents"][2]["host_approved_binding"] = True
            state["agents"][2]["admission_evidence_source"] = "meeting_record"
            state["agents"][2]["binding_role_id"] = "spoofed"
            state["agents"][0]["binding_conflicts"] = ["provider_kind_mismatch"]
            state_path.write_text(json.dumps(state), encoding="utf-8")
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with urlopen(
                    f"http://127.0.0.1:{server.server_port}/api/live-agents?safe=1&meeting_id=resident-m1",
                    timeout=4,
                ) as response:
                    listed = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

            by_agent = {agent["agent_id"]: agent for agent in listed["agents"]}
            self.assertEqual(by_agent["agent-a"]["admission_status"], "bound_to_meeting")
            self.assertEqual(by_agent["agent-a"]["admission_evidence_source"], "meeting_record")
            self.assertTrue(by_agent["agent-a"]["host_approved_binding"])
            self.assertEqual(by_agent["agent-a"]["binding_role_id"], "architect")
            self.assertEqual(by_agent["agent-a"]["binding_provider_id"], "local-cli")
            self.assertEqual(by_agent["agent-a"]["binding_provider_kind"], "local_cli")
            self.assertEqual(by_agent["agent-a"]["binding_permission_profile_id"], "meeting_readonly")
            self.assertEqual(by_agent["agent-a"]["binding_join_mode"], "resident")
            self.assertNotIn("binding_conflicts", by_agent["agent-a"])
            self.assertEqual(by_agent["agent-b"]["admission_status"], "binding_conflict")
            self.assertFalse(by_agent["agent-b"]["host_approved_binding"])
            self.assertEqual(by_agent["agent-b"]["binding_conflicts"], ["provider_kind_mismatch"])
            self.assertEqual(by_agent["guest-agent"]["admission_status"], "meeting_lobby_only")
            self.assertEqual(by_agent["guest-agent"]["admission_evidence_source"], "meeting_record")
            self.assertFalse(by_agent["guest-agent"]["host_approved_binding"])
            self.assertNotIn("binding_role_id", by_agent["guest-agent"])
            encoded = json.dumps(listed, ensure_ascii=False)
            self.assertNotIn("private-session", encoded)
            self.assertNotIn("spoofed", encoded)

    def test_live_agent_http_endpoint_safe_projection_refuses_live_state_only_admission(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            meeting_dir = root / "meetings" / "resident-m1"
            meeting_dir.mkdir(parents=True)
            (meeting_dir / "live_state.json").write_text(
                json.dumps(
                    {
                        "meeting_id": "resident-m1",
                        "agent_bindings": [
                            {
                                "agent_id": "agent-a",
                                "role_id": "architect",
                                "provider_id": "local-cli",
                                "permission_profile_id": "meeting_readonly",
                            }
                        ],
                        "provider_configs": {
                            "local-cli": {
                                "id": "local-cli",
                                "kind": "local_cli",
                            }
                        },
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            connect_live_agent(
                root,
                {
                    "agent_id": "agent-a",
                    "display_name": "Agent A",
                    "provider_kind": "local_cli",
                    "connection_kind": "local_cli",
                    "meeting_id": "resident-m1",
                    "session_id": "private-session-a",
                },
            )
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with urlopen(f"http://127.0.0.1:{server.server_port}/api/live-agents?safe=1", timeout=4) as response:
                    listed = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

            agent = listed["agents"][0]
            self.assertEqual(agent["admission_status"], "meeting_missing")
            self.assertEqual(agent["admission_evidence_source"], "meeting_record")
            self.assertFalse(agent["host_approved_binding"])
            self.assertNotIn("binding_role_id", agent)
            self.assertNotIn("binding_provider_id", agent)
            self.assertNotIn("private-session", json.dumps(listed, ensure_ascii=False))

    def test_live_agent_http_endpoint_safe_projection_combines_with_filters(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            connect_live_agent(
                root,
                {
                    "agent_id": "agent-a",
                    "display_name": "Agent A",
                    "provider_kind": "local_cli",
                    "connection_kind": "local_cli",
                    "meeting_id": "resident-m1",
                    "session_id": "private-a",
                },
            )
            connect_live_agent(
                root,
                {
                    "agent_id": "agent-b",
                    "display_name": "Agent B",
                    "provider_kind": "local_cli",
                    "connection_kind": "local_cli",
                    "meeting_id": "resident-m2",
                    "session_id": "private-b",
                },
            )
            connect_live_agent(
                root,
                {
                    "agent_id": "agent-c",
                    "display_name": "Agent C",
                    "provider_kind": "local_cli",
                    "connection_kind": "local_cli",
                    "meeting_id": "resident-m1",
                    "session_id": "private-c",
                },
            )
            heartbeat_live_agent(root, "agent-a", status="working")
            heartbeat_live_agent(root, "agent-b", status="working")
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with urlopen(
                    (
                        f"http://127.0.0.1:{server.server_port}/api/live-agents"
                        "?safe=1&meeting_id=resident-m1"
                        "&agent_id=agent-a&agent_id=agent-c"
                        "&status=online&status=working"
                    ),
                    timeout=4,
                ) as response:
                    listed = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

            self.assertEqual([agent["agent_id"] for agent in listed["agents"]], ["agent-a", "agent-c"])
            self.assertEqual([agent["status"] for agent in listed["agents"]], ["working", "online"])
            self.assertNotIn("session_id", listed["agents"][0])
            self.assertNotIn("session_id", listed["agents"][1])

    def test_live_agent_http_endpoint_filters_inferred_stale_status(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            connect_live_agent(
                root,
                {
                    "agent_id": "stale-agent",
                    "display_name": "Stale Agent",
                    "provider_kind": "local_cli",
                    "connection_kind": "local_cli",
                    "meeting_id": "resident-m1",
                },
            )
            state_path = root / "live_agents.json"
            state = json.loads(state_path.read_text(encoding="utf-8"))
            state["agents"][0]["last_seen_at"] = "2000-01-01T00:00:00+00:00"
            state_path.write_text(json.dumps(state), encoding="utf-8")
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with urlopen(
                    f"http://127.0.0.1:{server.server_port}/api/live-agents?status=stale",
                    timeout=4,
                ) as response:
                    listed = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

            self.assertEqual([agent["agent_id"] for agent in listed["agents"]], ["stale-agent"])
            self.assertEqual(listed["agents"][0]["status"], "stale")

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
                context.exception.read()
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

    def test_live_agent_room_endpoint_keeps_probe_sized_lobby_tail(self):
        expected_room_tail_limit = LIVE_AGENT_ROOM_LOBBY_EVENT_LIMIT
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            connect_live_agent_payload(root, {"agent_id": "agent-a", "display_name": "Agent A"})
            buried_event = append_lobby_event(root, {"name": "나", "side": "mine", "message": "probe buried by busy room"})
            for index in range(expected_room_tail_limit - 1):
                append_lobby_event(
                    root,
                    {
                        "name": "Busy Agent",
                        "side": "other-agent",
                        "message": f"busy chatter {index}",
                        "actor_id": f"busy-{index}",
                    },
                )
            self.assertNotIn(buried_event["id"], {event["id"] for event in read_lobby(root)})

            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with urlopen(f"http://127.0.0.1:{server.server_port}/api/live-agents/agent-a/room", timeout=4) as response:
                    payload = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

            self.assertEqual(len(payload["lobby_events"]), expected_room_tail_limit)
            self.assertEqual(payload["lobby_events"][0]["id"], buried_event["id"])

    def test_live_agent_room_endpoint_returns_meeting_live_events_for_agent_meeting(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            meeting_dir = root / "meetings" / "m1"
            meeting_dir.mkdir(parents=True)
            write_live_state(meeting_dir, {"meeting_id": "m1", "topic": "runtime", "live_status": "running"})
            request_event = append_live_event(
                meeting_dir,
                {
                    "kind": "live_agent_turn_request",
                    "meeting_id": "m1",
                    "target_agent_id": "agent-a",
                    "role_id": "architect",
                    "content": "공식 의견",
                },
            )
            connect_live_agent_payload(root, {"agent_id": "agent-a", "display_name": "Agent A", "meeting_id": "m1"})
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with urlopen(f"http://127.0.0.1:{server.server_port}/api/live-agents/agent-a/room", timeout=4) as response:
                    payload = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

            self.assertEqual(payload["meeting_id"], "m1")
            self.assertEqual(payload["live_events"][0]["id"], request_event["id"])
            self.assertEqual(payload["live_events"][0]["target_agent_id"], "agent-a")
            self.assertEqual(payload["live_events"][0]["official_record"], False)

    def test_live_agent_room_endpoint_projects_return_packet_after_event_tail_ages_out(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            meeting_dir = root / "meetings" / "m1"
            meeting_dir.mkdir(parents=True)
            (meeting_dir / "meeting.json").write_text(
                json.dumps(
                    {
                        "meeting_id": "m1",
                        "topic": "runtime",
                        "live_status": "complete",
                        "roles": [
                            {"id": "architect", "display_name": "Architect"},
                            {"id": "critic", "display_name": "Critic"},
                        ],
                        "agent_bindings": [
                            {"role_id": "architect", "agent_id": "agent-a"},
                            {"role_id": "critic", "agent_id": "agent-b"},
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            packet_dir = meeting_dir / "return_packets"
            packet_dir.mkdir()
            (packet_dir / "architect.md").write_text("Architect packet body must stay out of the room event.", encoding="utf-8")
            (packet_dir / "architect.json").write_text(json.dumps({"role_id": "architect"}), encoding="utf-8")
            (packet_dir / "critic.md").write_text("Critic packet body must stay private to agent-b.", encoding="utf-8")
            (packet_dir / "critic.json").write_text(json.dumps({"role_id": "critic"}), encoding="utf-8")
            original_packet_event = append_live_event(
                meeting_dir,
                {
                    "kind": "artifact",
                    "artifact_kind": "return_packet",
                    "meeting_id": "m1",
                    "target_agent_id": "agent-a",
                    "audience": "agent:agent-a",
                    "role_id": "architect",
                    "display_name": "Architect",
                    "artifact_path": "return_packets/architect.md",
                    "artifact_json_path": "return_packets/architect.json",
                    "content": "Return packet ready: return_packets/architect.md",
                },
            )
            for index in range(205):
                tail_event = append_live_event(
                    meeting_dir,
                    {
                        "kind": "status",
                        "meeting_id": "m1",
                        "content": f"tail filler {index}",
                    },
                )
            connect_live_agent_payload(root, {"agent_id": "agent-a", "display_name": "Agent A", "meeting_id": "m1"})
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with urlopen(f"http://127.0.0.1:{server.server_port}/api/live-agents/agent-a/room", timeout=4) as response:
                    payload = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

            return_packet_events = [
                event
                for event in payload["live_events"]
                if event.get("kind") == "artifact" and event.get("artifact_kind") == "return_packet"
            ]
            self.assertEqual(len(return_packet_events), 1)
            packet_event = return_packet_events[0]
            self.assertEqual(packet_event["id"], original_packet_event["id"])
            self.assertEqual(packet_event["target_agent_id"], "agent-a")
            self.assertEqual(packet_event["audience"], "agent:agent-a")
            self.assertEqual(packet_event["role_id"], "architect")
            self.assertEqual(packet_event["artifact_path"], "return_packets/architect.md")
            self.assertEqual(packet_event["artifact_json_path"], "return_packets/architect.json")
            self.assertEqual(packet_event["official_record"], False)
            payload_text = json.dumps(payload, ensure_ascii=False)
            self.assertNotIn("Architect packet body must stay out", payload_text)
            self.assertNotIn("Critic packet body", payload_text)
            self.assertNotIn("return_packets/critic", payload_text)

            heartbeat_live_agent(root, "agent-a", metadata={"last_observed_live_event_id": original_packet_event["id"]})
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with urlopen(f"http://127.0.0.1:{server.server_port}/api/live-agents/agent-a/room", timeout=4) as response:
                    acknowledged_payload = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()
            self.assertFalse(
                [
                    event
                    for event in acknowledged_payload["live_events"]
                    if event.get("kind") == "artifact" and event.get("artifact_kind") == "return_packet"
                ]
            )

            heartbeat_live_agent(root, "agent-a", metadata={"last_observed_live_event_id": tail_event["id"]})
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with urlopen(f"http://127.0.0.1:{server.server_port}/api/live-agents/agent-a/room", timeout=4) as response:
                    later_cursor_payload = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()
            self.assertFalse(
                [
                    event
                    for event in later_cursor_payload["live_events"]
                    if event.get("kind") == "artifact" and event.get("artifact_kind") == "return_packet"
                ]
            )

    def test_live_agent_room_endpoint_stops_projected_return_packet_after_projected_ack(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            meeting_dir = root / "meetings" / "m1"
            meeting_dir.mkdir(parents=True)
            (meeting_dir / "meeting.json").write_text(
                json.dumps(
                    {
                        "meeting_id": "m1",
                        "topic": "runtime",
                        "live_status": "complete",
                        "roles": [{"id": "architect", "display_name": "Architect"}],
                        "agent_bindings": [{"role_id": "architect", "agent_id": "agent-a"}],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            packet_dir = meeting_dir / "return_packets"
            packet_dir.mkdir()
            (packet_dir / "architect.md").write_text("Architect packet body must stay out of the room event.", encoding="utf-8")
            (packet_dir / "architect.json").write_text(json.dumps({"role_id": "architect"}), encoding="utf-8")
            connect_live_agent_payload(root, {"agent_id": "agent-a", "display_name": "Agent A", "meeting_id": "m1"})
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with urlopen(f"http://127.0.0.1:{server.server_port}/api/live-agents/agent-a/room", timeout=4) as response:
                    payload = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()
            return_packet_events = [
                event
                for event in payload["live_events"]
                if event.get("kind") == "artifact" and event.get("artifact_kind") == "return_packet"
            ]
            self.assertEqual(len(return_packet_events), 1)
            projected_id = return_packet_events[0]["id"]

            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with urlopen(
                    (
                        f"http://127.0.0.1:{server.server_port}"
                        f"/api/live-agents/agent-a/return-packet?meeting_id=m1&source_event_id={projected_id}"
                    ),
                    timeout=4,
                ) as response:
                    projected_packet_payload = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()
            self.assertEqual(projected_packet_payload["source_event_id"], projected_id)
            self.assertEqual(projected_packet_payload["artifact_path"], "return_packets/architect.md")
            self.assertEqual(projected_packet_payload["markdown"], "Architect packet body must stay out of the room event.")

            heartbeat_live_agent(root, "agent-a", metadata={"last_observed_live_event_id": projected_id})
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with urlopen(f"http://127.0.0.1:{server.server_port}/api/live-agents/agent-a/room", timeout=4) as response:
                    acknowledged_payload = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()
            self.assertFalse(
                [
                    event
                    for event in acknowledged_payload["live_events"]
                    if event.get("kind") == "artifact" and event.get("artifact_kind") == "return_packet"
                ]
            )

            append_live_event(
                meeting_dir,
                {
                    "kind": "artifact",
                    "artifact_kind": "return_packet",
                    "meeting_id": "m1",
                    "target_agent_id": "agent-a",
                    "audience": "agent:agent-a",
                    "role_id": "architect",
                    "display_name": "Architect",
                    "artifact_path": "return_packets/architect.md",
                    "artifact_json_path": "return_packets/architect.json",
                    "content": "Return packet ready: return_packets/architect.md",
                },
            )
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with urlopen(f"http://127.0.0.1:{server.server_port}/api/live-agents/agent-a/room", timeout=4) as response:
                    late_original_payload = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()
            self.assertFalse(
                [
                    event
                    for event in late_original_payload["live_events"]
                    if event.get("kind") == "artifact" and event.get("artifact_kind") == "return_packet"
                ]
            )

    def test_live_agent_return_packet_endpoint_reads_only_targeted_agent_packet(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            meeting_dir = root / "meetings" / "m1"
            meeting_dir.mkdir(parents=True)
            (meeting_dir / "meeting.json").write_text(
                json.dumps(
                    {
                        "meeting_id": "m1",
                        "topic": "runtime",
                        "live_status": "complete",
                        "roles": [
                            {"id": "architect", "display_name": "Architect"},
                            {"id": "critic", "display_name": "Critic"},
                        ],
                        "agent_bindings": [
                            {"role_id": "architect", "agent_id": "agent-a"},
                            {"role_id": "critic", "agent_id": "agent-b"},
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            packet_dir = meeting_dir / "return_packets"
            packet_dir.mkdir()
            (packet_dir / "architect.md").write_text("Architect private return packet.", encoding="utf-8")
            (packet_dir / "architect.json").write_text(json.dumps({"role_id": "architect"}), encoding="utf-8")
            (packet_dir / "critic.md").write_text("Critic packet must stay private.", encoding="utf-8")
            (packet_dir / "critic.json").write_text(json.dumps({"role_id": "critic"}), encoding="utf-8")
            packet_event = append_live_event(
                meeting_dir,
                {
                    "kind": "artifact",
                    "artifact_kind": "return_packet",
                    "meeting_id": "m1",
                    "target_agent_id": "agent-a",
                    "audience": "agent:agent-a",
                    "role_id": "architect",
                    "display_name": "Architect",
                    "artifact_path": "return_packets/architect.md",
                    "artifact_json_path": "return_packets/architect.json",
                    "content": "Return packet ready: return_packets/architect.md",
                },
            )
            connect_live_agent_payload(
                root,
                {
                    "agent_id": "agent-a",
                    "display_name": "Agent A",
                    "provider_kind": "local_cli",
                    "connection_kind": "local_cli",
                    "meeting_id": "m1",
                },
            )
            connect_live_agent_payload(
                root,
                {
                    "agent_id": "agent-b",
                    "display_name": "Agent B",
                    "provider_kind": "local_cli",
                    "connection_kind": "local_cli",
                    "meeting_id": "m1",
                },
            )

            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with urlopen(
                    (
                        f"http://127.0.0.1:{server.server_port}"
                        f"/api/live-agents/agent-a/return-packet?meeting_id=m1&source_event_id={packet_event['id']}"
                    ),
                    timeout=4,
                ) as response:
                    payload = json.loads(response.read().decode("utf-8"))
                with self.assertRaises(HTTPError) as error_context:
                    urlopen(
                        (
                            f"http://127.0.0.1:{server.server_port}"
                            f"/api/live-agents/agent-b/return-packet?meeting_id=m1&source_event_id={packet_event['id']}"
                        ),
                        timeout=4,
                    )
                error_body = error_context.exception.read().decode("utf-8")
                error_context.exception.close()
            finally:
                server.shutdown()
                server.server_close()

            self.assertEqual(payload["status"], "ok")
            self.assertEqual(payload["agent_id"], "agent-a")
            self.assertEqual(payload["meeting_id"], "m1")
            self.assertEqual(payload["source_event_id"], packet_event["id"])
            self.assertEqual(payload["artifact_path"], "return_packets/architect.md")
            self.assertEqual(payload["artifact_json_path"], "return_packets/architect.json")
            self.assertEqual(payload["markdown"], "Architect private return packet.")
            self.assertEqual(payload["json"]["role_id"], "architect")
            payload_text = json.dumps(payload, ensure_ascii=False)
            self.assertNotIn("Critic packet", payload_text)
            self.assertNotIn("return_packets/critic", payload_text)
            self.assertEqual(error_context.exception.code, 404)
            self.assertNotIn("Architect private return packet", error_body)
            self.assertNotIn("Critic packet", error_body)

    def test_live_agent_return_packet_endpoint_rejects_cross_meeting_agent_id_reuse(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            meeting_dir = root / "meetings" / "m1"
            meeting_dir.mkdir(parents=True)
            (meeting_dir / "meeting.json").write_text(
                json.dumps(
                    {
                        "meeting_id": "m1",
                        "topic": "runtime",
                        "roles": [{"id": "architect", "display_name": "Architect"}],
                        "agent_bindings": [{"role_id": "architect", "agent_id": "agent-a"}],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            packet_dir = meeting_dir / "return_packets"
            packet_dir.mkdir()
            (packet_dir / "architect.md").write_text("Old meeting packet must not cross sessions.", encoding="utf-8")
            (packet_dir / "architect.json").write_text(json.dumps({"role_id": "architect"}), encoding="utf-8")
            packet_event = append_live_event(
                meeting_dir,
                {
                    "kind": "artifact",
                    "artifact_kind": "return_packet",
                    "meeting_id": "m1",
                    "target_agent_id": "agent-a",
                    "audience": "agent:agent-a",
                    "role_id": "architect",
                    "artifact_path": "return_packets/architect.md",
                    "artifact_json_path": "return_packets/architect.json",
                },
            )
            connect_live_agent_payload(root, {"agent_id": "agent-a", "display_name": "Agent A", "meeting_id": "m2"})

            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with self.assertRaises(HTTPError) as error_context:
                    urlopen(
                        (
                            f"http://127.0.0.1:{server.server_port}"
                            f"/api/live-agents/agent-a/return-packet?meeting_id=m1&source_event_id={packet_event['id']}"
                        ),
                        timeout=4,
                    )
                error_body = error_context.exception.read().decode("utf-8")
                error_context.exception.close()
            finally:
                server.shutdown()
                server.server_close()

            self.assertEqual(error_context.exception.code, 404)
            self.assertNotIn("Old meeting packet", error_body)

    def test_live_agent_room_endpoint_does_not_project_legacy_packet_after_known_live_cursor(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            meeting_dir = root / "meetings" / "m1"
            meeting_dir.mkdir(parents=True)
            (meeting_dir / "meeting.json").write_text(
                json.dumps(
                    {
                        "meeting_id": "m1",
                        "topic": "runtime",
                        "live_status": "complete",
                        "roles": [{"id": "architect", "display_name": "Architect"}],
                        "agent_bindings": [{"role_id": "architect", "agent_id": "agent-a"}],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            packet_dir = meeting_dir / "return_packets"
            packet_dir.mkdir()
            (packet_dir / "architect.md").write_text("Architect packet body must stay out of the room event.", encoding="utf-8")
            (packet_dir / "architect.json").write_text(json.dumps({"role_id": "architect"}), encoding="utf-8")
            later_event = append_live_event(
                meeting_dir,
                {
                    "kind": "status",
                    "meeting_id": "m1",
                    "content": "agent has observed this later event",
                },
            )
            connect_live_agent_payload(
                root,
                {
                    "agent_id": "agent-a",
                    "display_name": "Agent A",
                    "meeting_id": "m1",
                    "last_observed_live_event_id": later_event["id"],
                },
            )
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with urlopen(f"http://127.0.0.1:{server.server_port}/api/live-agents/agent-a/room", timeout=4) as response:
                    payload = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

            self.assertFalse(
                [
                    event
                    for event in payload["live_events"]
                    if event.get("kind") == "artifact" and event.get("artifact_kind") == "return_packet"
                ]
            )

    def test_live_agent_room_endpoint_returns_compact_shared_memory_for_agent_meeting(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            meeting_dir = root / "meetings" / "m1"
            meeting_dir.mkdir(parents=True)
            write_live_state(
                meeting_dir,
                {
                    "meeting_id": "m1",
                    "topic": "runtime",
                    "live_status": "running",
                    "shared_memory": {
                        "official_event_count": 1,
                        "last_official_event_id": "stale-reply",
                        "rolling_summary": [
                            {
                                "event_id": "stale-reply",
                                "speaker": "Architect",
                                "summary": "Stale embedded resident memory.",
                            }
                        ],
                    },
                },
            )
            shared_dir = meeting_dir / "shared_memory"
            shared_dir.mkdir(parents=True)
            (shared_dir / "index.json").write_text(
                json.dumps(
                    {
                        "official_event_count": 2,
                        "last_official_event_id": "reply-2",
                        "rolling_summary": [
                            {
                                "event_id": "reply-2",
                                "speaker": "Architect",
                                "summary": "Fresh index resident memory.",
                            }
                        ],
                        "action_items": [
                            {
                                "event_id": "reply-2",
                                "speaker": "Architect",
                                "text": "Use shared memory in prompts.",
                            }
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            append_live_event(
                meeting_dir,
                {
                    "kind": "live_agent_turn_request",
                    "meeting_id": "m1",
                    "target_agent_id": "agent-b",
                    "content": "private target-B instruction",
                },
            )
            connect_live_agent_payload(root, {"agent_id": "agent-a", "display_name": "Agent A", "meeting_id": "m1"})
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with urlopen(f"http://127.0.0.1:{server.server_port}/api/live-agents/agent-a/room", timeout=4) as response:
                    payload = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

            self.assertEqual(payload["shared_memory"]["official_event_count"], 2)
            self.assertEqual(payload["shared_memory"]["last_official_event_id"], "reply-2")
            self.assertEqual(payload["shared_memory"]["rolling_summary"][0]["summary"], "Fresh index resident memory.")
            self.assertEqual(payload["shared_memory"]["action_items"][0]["text"], "Use shared memory in prompts.")
            payload_text = json.dumps(payload["shared_memory"], ensure_ascii=False)
            self.assertNotIn("Stale embedded resident memory.", payload_text)
            self.assertNotIn("private target-B instruction", payload_text)

    def test_live_agent_room_endpoint_projects_fresh_shared_memory_when_index_is_stale(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            meeting_dir = root / "meetings" / "m1"
            meeting_dir.mkdir(parents=True)
            write_live_state(meeting_dir, {"meeting_id": "m1", "topic": "runtime", "live_status": "running"})
            shared_dir = meeting_dir / "shared_memory"
            shared_dir.mkdir(parents=True)
            (shared_dir / "index.json").write_text(
                json.dumps(
                    {
                        "official_event_count": 1,
                        "last_official_event_id": "stale-reply",
                        "rolling_summary": [
                            {
                                "event_id": "stale-reply",
                                "speaker": "Architect",
                                "summary": "Stale shared memory file.",
                            }
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            original_index = (shared_dir / "index.json").read_text(encoding="utf-8")
            append_live_event(
                meeting_dir,
                {
                    "kind": "message",
                    "meeting_id": "m1",
                    "channel": "official",
                    "official_record": True,
                    "actor_id": "agent-a",
                    "display_name": "Architect",
                    "content": "Fresh room memory.\nOpen question: Is the resident context current?",
                },
            )
            append_live_event(
                meeting_dir,
                {
                    "kind": "live_agent_turn_request",
                    "meeting_id": "m1",
                    "target_agent_id": "agent-b",
                    "content": "private target-B instruction",
                },
            )
            connect_live_agent_payload(root, {"agent_id": "agent-a", "display_name": "Agent A", "meeting_id": "m1"})
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with urlopen(f"http://127.0.0.1:{server.server_port}/api/live-agents/agent-a/room", timeout=4) as response:
                    payload = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

            payload_text = json.dumps(payload["shared_memory"], ensure_ascii=False)
            self.assertEqual(payload["shared_memory"]["rolling_summary"][0]["summary"], "Fresh room memory. Open question: Is the resident context current?")
            self.assertEqual(payload["shared_memory"]["open_questions"][0]["text"], "Is the resident context current?")
            self.assertNotIn("Stale shared memory file.", payload_text)
            self.assertNotIn("private target-B instruction", payload_text)
            self.assertEqual((shared_dir / "index.json").read_text(encoding="utf-8"), original_index)

    def test_live_agent_room_endpoint_uses_official_log_when_matching_index_contains_untrusted_text(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            meeting_dir = root / "meetings" / "m1"
            meeting_dir.mkdir(parents=True)
            write_live_state(meeting_dir, {"meeting_id": "m1", "topic": "runtime", "live_status": "running"})
            reply = append_live_event(
                meeting_dir,
                {
                    "kind": "message",
                    "meeting_id": "m1",
                    "channel": "official",
                    "official_record": True,
                    "actor_id": "agent-a",
                    "display_name": "Architect",
                    "content": "Endpoint official memory.\nAction: Keep room endpoint authoritative.",
                },
            )
            shared_dir = meeting_dir / "shared_memory"
            shared_dir.mkdir(parents=True)
            (shared_dir / "index.json").write_text(
                json.dumps(
                    {
                        "official_event_count": 1,
                        "last_official_event_id": reply["id"],
                        "rolling_summary": [
                            {
                                "event_id": reply["id"],
                                "speaker": "Architect",
                                "summary": "private provider output leak",
                            }
                        ],
                        "action_items": [
                            {
                                "event_id": reply["id"],
                                "speaker": "Architect",
                                "text": "private prompt leak",
                            }
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            original_index = (shared_dir / "index.json").read_text(encoding="utf-8")
            connect_live_agent_payload(root, {"agent_id": "agent-a", "display_name": "Agent A", "meeting_id": "m1"})
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with urlopen(f"http://127.0.0.1:{server.server_port}/api/live-agents/agent-a/room", timeout=4) as response:
                    payload = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

            payload_text = json.dumps(payload["shared_memory"], ensure_ascii=False)
            self.assertEqual(payload["shared_memory"]["last_official_event_id"], reply["id"])
            self.assertIn("Endpoint official memory.", payload_text)
            self.assertIn("Keep room endpoint authoritative.", payload_text)
            self.assertNotIn("private provider output leak", payload_text)
            self.assertNotIn("private prompt leak", payload_text)
            self.assertEqual((shared_dir / "index.json").read_text(encoding="utf-8"), original_index)

    def test_live_agent_room_endpoint_hides_other_agents_targeted_turn_requests(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            meeting_dir = root / "meetings" / "m1"
            meeting_dir.mkdir(parents=True)
            write_live_state(meeting_dir, {"meeting_id": "m1", "topic": "runtime", "live_status": "running"})
            append_live_event(
                meeting_dir,
                {
                    "kind": "live_agent_turn_request",
                    "meeting_id": "m1",
                    "target_agent_id": "agent-b",
                    "audience": "agent:agent-b",
                    "content": "private target-B instruction",
                },
            )
            append_live_event(
                meeting_dir,
                {
                    "kind": "message",
                    "meeting_id": "m1",
                    "actor_id": "agent-b",
                    "display_name": "Agent B",
                    "content": "public official statement",
                },
            )
            connect_live_agent_payload(root, {"agent_id": "agent-a", "display_name": "Agent A", "meeting_id": "m1"})
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with urlopen(f"http://127.0.0.1:{server.server_port}/api/live-agents/agent-a/room", timeout=4) as response:
                    payload = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

            contents = [event.get("content") for event in payload["live_events"]]
            self.assertNotIn("private target-B instruction", contents)
            self.assertIn("public official statement", contents)

    def test_live_agent_official_turn_request_and_reply_stay_out_of_lobby(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            meeting_dir = root / "meetings" / "m1"
            meeting_dir.mkdir(parents=True)
            write_live_state(meeting_dir, {"meeting_id": "m1", "topic": "runtime", "live_status": "running"})
            append_live_event(
                meeting_dir,
                {
                    "kind": "live_agent_turn_request",
                    "meeting_id": "m1",
                    "target_agent_id": "agent-b",
                    "audience": "agent:agent-b",
                    "content": "private target-B instruction",
                },
            )
            connect_live_agent_payload(
                root,
                {
                    "agent_id": "agent-a",
                    "display_name": "Agent A",
                    "provider_kind": "local_cli",
                    "connection_kind": "local_cli",
                    "meeting_id": "m1",
                    "engagement_mode": "moderator_called",
                    "last_error": "previous official turn failed",
                },
            )
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/meetings/m1/live-agent-turns/request",
                    data=json.dumps(
                        {
                            "agent_id": "agent-a",
                            "role_id": "architect",
                            "display_name": "Agent A",
                            "content": "공식 발언 차례",
                            "turn_id": "round_1:0:architect",
                            "turn_index": 0,
                        }
                    ).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urlopen(request, timeout=4) as response:
                    requested = json.loads(response.read().decode("utf-8"))
                reply_request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/live-agents/agent-a/official-turn",
                    data=json.dumps(
                        {
                            "meeting_id": "m1",
                            "source_event_id": requested["event"]["id"],
                            "content": "공식 답변\nAction item: Preserve resident shared memory.",
                            "role_id": "payload-override",
                            "display_name": "Payload Override",
                            "turn_id": "round_1:0:architect",
                            "turn_index": 0,
                        }
                    ).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urlopen(reply_request, timeout=4) as response:
                    replied = json.loads(response.read().decode("utf-8"))
                with urlopen(f"http://127.0.0.1:{server.server_port}/api/lobby", timeout=4) as response:
                    lobby = json.loads(response.read().decode("utf-8"))
                with urlopen(f"http://127.0.0.1:{server.server_port}/api/live-agent-operations", timeout=4) as response:
                    operations = json.loads(response.read().decode("utf-8"))
                persisted_agent = json.loads((root / "live_agents.json").read_text(encoding="utf-8"))["agents"][0]
            finally:
                server.shutdown()
                server.server_close()

            request_event = requested["event"]
            reply_event = replied["event"]
            self.assertEqual(request_event["kind"], "live_agent_turn_request")
            self.assertEqual(request_event["channel"], "system")
            self.assertFalse(request_event["official_record"])
            self.assertEqual(request_event["target_agent_id"], "agent-a")
            self.assertEqual(reply_event["kind"], "message")
            self.assertEqual(reply_event["channel"], "official")
            self.assertTrue(reply_event["official_record"])
            self.assertEqual(reply_event["source_event_id"], request_event["id"])
            self.assertEqual(reply_event["actor_id"], "agent-a")
            self.assertEqual(reply_event["role_id"], "architect")
            self.assertEqual(reply_event["display_name"], "Agent A")
            self.assertEqual(reply_event["turn_id"], "round_1:0:architect")
            self.assertEqual(reply_event["engagement_mode"], "moderator_called")
            self.assertEqual(replied["agent"]["last_error"], "")
            self.assertEqual(replied["agent"]["last_observed_live_event_id"], request_event["id"])
            self.assertEqual(persisted_agent["last_error"], "")
            self.assertNotIn("private target-B instruction", [event.get("content") for event in replied["live_events"]])
            self.assertEqual(lobby["events"], [])
            self.assertEqual([item["operation"] for item in operations["operations"]], ["official_turn.request", "official_turn.reply"])
            reply_operation = operations["operations"][1]
            self.assertEqual(reply_operation["details"]["shared_memory_official_event_count"], 1)
            self.assertEqual(reply_operation["details"]["shared_memory_action_item_count"], 1)
            transcript = build_meeting_payload(meeting_dir)["artifacts"]["transcript.md"]
            self.assertIn("공식 답변", transcript)
            self.assertIn(f"- Source event id: {request_event['id']}", transcript)
            self.assertNotIn("공식 발언 차례", transcript)
            self.assertNotIn("private target-B instruction", transcript)
            self.assertFalse((meeting_dir / "transcript.md").exists())
            self.assertTrue((meeting_dir / "shared_memory" / "rolling-summary.md").exists())
            self.assertTrue((meeting_dir / "shared_memory" / "action-items.md").exists())
            shared_memory_text = (meeting_dir / "shared_memory" / "action-items.md").read_text(encoding="utf-8")
            self.assertIn("Preserve resident shared memory.", shared_memory_text)
            self.assertNotIn("공식 발언 차례", shared_memory_text)
            operations_text = json.dumps(operations["operations"], ensure_ascii=False)
            self.assertNotIn("Preserve resident shared memory.", operations_text)

    def test_live_agent_official_turn_call_waits_for_verified_reply(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            meeting_dir = root / "meetings" / "m1"
            meeting_dir.mkdir(parents=True)
            write_live_state(meeting_dir, {"meeting_id": "m1", "topic": "runtime", "live_status": "running"})
            connect_live_agent_payload(
                root,
                {
                    "agent_id": "agent-a",
                    "display_name": "Agent A",
                    "provider_kind": "local_cli",
                    "connection_kind": "local_cli",
                    "meeting_id": "m1",
                    "engagement_mode": "moderator_called",
                },
            )
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            errors = []

            def answer_when_called():
                try:
                    deadline = time.time() + 2
                    request_event = None
                    while time.time() < deadline:
                        request_event = next(
                            (
                                event
                                for event in read_live_events(meeting_dir, limit=None)
                                if event.get("kind") == "live_agent_turn_request"
                                and event.get("target_agent_id") == "agent-a"
                            ),
                            None,
                        )
                        if request_event is not None:
                            break
                        time.sleep(0.01)
                    self.assertIsNotNone(request_event)
                    reply_request = Request(
                        f"http://127.0.0.1:{server.server_port}/api/live-agents/agent-a/official-turn",
                        data=json.dumps(
                            {
                                "meeting_id": "m1",
                                "source_event_id": request_event["id"],
                                "content": "검증된 공식 답변",
                            }
                        ).encode("utf-8"),
                        headers={"Content-Type": "application/json"},
                        method="POST",
                    )
                    with urlopen(reply_request, timeout=4) as response:
                        response.read()
                except Exception as error:  # pragma: no cover - failure is asserted below
                    errors.append(error)

            responder = threading.Thread(target=answer_when_called, daemon=True)
            responder.start()
            try:
                call_request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/meetings/m1/live-agent-turns/call",
                    data=json.dumps(
                        {
                            "agent_id": "agent-a",
                            "role_id": "architect",
                            "display_name": "Agent A",
                            "content": "공식 발언 차례",
                            "timeout_seconds": 2,
                        }
                    ).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urlopen(call_request, timeout=5) as response:
                    called = json.loads(response.read().decode("utf-8"))
                responder.join(timeout=2)
                with urlopen(f"http://127.0.0.1:{server.server_port}/api/lobby", timeout=4) as response:
                    lobby = json.loads(response.read().decode("utf-8"))
                with urlopen(f"http://127.0.0.1:{server.server_port}/api/live-agent-operations", timeout=4) as response:
                    operations = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

            if errors:
                raise errors[0]
            self.assertEqual(called["status"], "answered")
            self.assertEqual(called["request_event"]["kind"], "live_agent_turn_request")
            self.assertEqual(called["reply_event"]["kind"], "message")
            self.assertEqual(called["reply_event"]["actor_id"], "agent-a")
            self.assertEqual(called["reply_event"]["source_event_id"], called["request_event"]["id"])
            self.assertTrue(called["reply_event"]["official_record"])
            self.assertEqual(called["reply_event"]["content"], "검증된 공식 답변")
            self.assertEqual(lobby["events"], [])
            call_operations = [item for item in operations["operations"] if item["operation"] == "official_turn.call"]
            self.assertEqual(len(call_operations), 1)
            self.assertEqual(call_operations[0]["status"], "success")
            self.assertNotIn("공식 발언 차례", json.dumps(call_operations, ensure_ascii=False))
            self.assertNotIn("검증된 공식 답변", json.dumps(call_operations, ensure_ascii=False))
            transcript = build_meeting_payload(meeting_dir)["artifacts"]["transcript.md"]
            self.assertIn("검증된 공식 답변", transcript)
            self.assertIn(f"- Source event id: {called['request_event']['id']}", transcript)
            self.assertNotIn("공식 발언 차례", transcript)
            self.assertFalse((meeting_dir / "transcript.md").exists())

    def test_live_agent_official_turn_call_timeout_does_not_create_reply(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            meeting_dir = root / "meetings" / "m1"
            meeting_dir.mkdir(parents=True)
            write_live_state(meeting_dir, {"meeting_id": "m1", "topic": "runtime", "live_status": "running"})
            connect_live_agent_payload(root, {"agent_id": "agent-a", "display_name": "Agent A", "meeting_id": "m1"})
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                call_request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/meetings/m1/live-agent-turns/call",
                    data=json.dumps(
                        {
                            "agent_id": "agent-a",
                            "content": "공식 발언 차례",
                            "timeout_seconds": 0,
                        }
                    ).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urlopen(call_request, timeout=4) as response:
                    called = json.loads(response.read().decode("utf-8"))
                with urlopen(f"http://127.0.0.1:{server.server_port}/api/live-agent-operations", timeout=4) as response:
                    operations = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

            self.assertEqual(called["status"], "timeout")
            self.assertIsNone(called["reply_event"])
            self.assertEqual(called["request_event"]["kind"], "live_agent_turn_request")
            official_replies = [
                event
                for event in read_live_events(meeting_dir, limit=None)
                if event.get("kind") == "message" and event.get("source_event_id") == called["request_event"]["id"]
            ]
            self.assertEqual(official_replies, [])
            call_operations = [item for item in operations["operations"] if item["operation"] == "official_turn.call"]
            self.assertEqual(call_operations[0]["status"], "degraded")

    def test_live_agent_official_turn_sequence_calls_agents_in_order(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            meeting_dir = root / "meetings" / "m1"
            meeting_dir.mkdir(parents=True)
            write_live_state(meeting_dir, {"meeting_id": "m1", "topic": "runtime", "live_status": "running"})
            for agent_id, display_name in (("agent-a", "Agent A"), ("agent-b", "Agent B")):
                connect_live_agent_payload(
                    root,
                    {
                        "agent_id": agent_id,
                        "display_name": display_name,
                        "provider_kind": "local_cli",
                        "connection_kind": "local_cli",
                        "meeting_id": "m1",
                        "engagement_mode": "moderator_called",
                    },
                )
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            errors = []
            answered_request_ids = set()

            def answer_sequence_requests():
                try:
                    deadline = time.time() + 4
                    while time.time() < deadline and len(answered_request_ids) < 2:
                        for event in read_live_events(meeting_dir, limit=None):
                            if event.get("kind") != "live_agent_turn_request":
                                continue
                            if event["id"] in answered_request_ids:
                                continue
                            agent_id = event["target_agent_id"]
                            reply_request = Request(
                                f"http://127.0.0.1:{server.server_port}/api/live-agents/{agent_id}/official-turn",
                                data=json.dumps(
                                    {
                                        "meeting_id": "m1",
                                        "source_event_id": event["id"],
                                        "content": f"{agent_id} official reply",
                                    }
                                ).encode("utf-8"),
                                headers={"Content-Type": "application/json"},
                                method="POST",
                            )
                            with urlopen(reply_request, timeout=4) as response:
                                response.read()
                            answered_request_ids.add(event["id"])
                        time.sleep(0.01)
                except Exception as error:  # pragma: no cover - failure is asserted below
                    errors.append(error)

            responder = threading.Thread(target=answer_sequence_requests, daemon=True)
            responder.start()
            try:
                sequence_request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/meetings/m1/live-agent-turns/sequence",
                    data=json.dumps(
                        {
                            "timeout_seconds": 2,
                            "turns": [
                                {
                                    "agent_id": "agent-a",
                                    "role_id": "architect",
                                    "display_name": "Agent A",
                                    "turn_id": "round_1:0:architect",
                                    "turn_index": 0,
                                    "content": "private prompt for A",
                                },
                                {
                                    "agent_id": "agent-b",
                                    "role_id": "critic",
                                    "display_name": "Agent B",
                                    "turn_id": "round_1:1:critic",
                                    "turn_index": 1,
                                    "content": "private prompt for B",
                                },
                            ],
                        }
                    ).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urlopen(sequence_request, timeout=6) as response:
                    sequence = json.loads(response.read().decode("utf-8"))
                responder.join(timeout=2)
                with urlopen(f"http://127.0.0.1:{server.server_port}/api/live-agent-operations", timeout=4) as response:
                    operations = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

            if errors:
                raise errors[0]
            self.assertEqual(sequence["status"], "answered")
            self.assertEqual(sequence["answered_count"], 2)
            self.assertEqual(sequence["timeout_count"], 0)
            self.assertEqual(sequence["skipped_count"], 0)
            self.assertEqual([result["agent_id"] for result in sequence["results"]], ["agent-a", "agent-b"])
            self.assertEqual([result["status"] for result in sequence["results"]], ["answered", "answered"])
            self.assertEqual(sequence["results"][0]["reply_event"]["actor_id"], "agent-a")
            self.assertEqual(sequence["results"][1]["reply_event"]["actor_id"], "agent-b")
            self.assertEqual(sequence["results"][1]["request_event"]["turn_index"], 1)
            transcript = build_meeting_payload(meeting_dir)["artifacts"]["transcript.md"]
            self.assertIn("agent-a official reply", transcript)
            self.assertIn("agent-b official reply", transcript)
            self.assertNotIn("private prompt for A", transcript)
            self.assertNotIn("private prompt for B", transcript)
            sequence_operations = [item for item in operations["operations"] if item["operation"] == "official_turn.sequence"]
            self.assertEqual(len(sequence_operations), 1)
            self.assertEqual(sequence_operations[0]["status"], "success")
            operation_names = [item["operation"] for item in operations["operations"]]
            self.assertEqual(operation_names.count("official_turn.reply"), 2)
            self.assertEqual(operation_names.count("official_turn.sequence"), 1)
            operation_blob = json.dumps(operations["operations"], ensure_ascii=False)
            self.assertNotIn("private prompt for A", operation_blob)
            self.assertNotIn("agent-a official reply", operation_blob)

    def test_live_agent_official_turn_sequence_can_continue_after_timeout(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            meeting_dir = root / "meetings" / "m1"
            meeting_dir.mkdir(parents=True)
            write_live_state(meeting_dir, {"meeting_id": "m1", "topic": "runtime", "live_status": "running"})
            for agent_id in ("agent-a", "agent-b"):
                connect_live_agent_payload(root, {"agent_id": agent_id, "display_name": agent_id, "meeting_id": "m1"})
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                sequence_request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/meetings/m1/live-agent-turns/sequence",
                    data=json.dumps(
                        {
                            "timeout_seconds": 0,
                            "turns": [
                                {"agent_id": "agent-a", "content": "first prompt"},
                                {"agent_id": "agent-b", "content": "second prompt"},
                            ],
                        }
                    ).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urlopen(sequence_request, timeout=4) as response:
                    sequence = json.loads(response.read().decode("utf-8"))
                with urlopen(f"http://127.0.0.1:{server.server_port}/api/live-agent-operations", timeout=4) as response:
                    operations = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

            self.assertEqual(sequence["status"], "timeout")
            self.assertEqual(sequence["answered_count"], 0)
            self.assertEqual(sequence["timeout_count"], 2)
            self.assertEqual(sequence["skipped_count"], 0)
            self.assertFalse(sequence["stopped"])
            self.assertEqual([result["agent_id"] for result in sequence["results"]], ["agent-a", "agent-b"])
            request_events = [
                event for event in read_live_events(meeting_dir, limit=None) if event.get("kind") == "live_agent_turn_request"
            ]
            self.assertEqual([event["target_agent_id"] for event in request_events], ["agent-a", "agent-b"])
            sequence_operations = [item for item in operations["operations"] if item["operation"] == "official_turn.sequence"]
            self.assertEqual(sequence_operations[0]["status"], "degraded")

    def test_live_agent_official_turn_sequence_can_stop_after_timeout(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            meeting_dir = root / "meetings" / "m1"
            meeting_dir.mkdir(parents=True)
            write_live_state(meeting_dir, {"meeting_id": "m1", "topic": "runtime", "live_status": "running"})
            for agent_id in ("agent-a", "agent-b"):
                connect_live_agent_payload(root, {"agent_id": agent_id, "display_name": agent_id, "meeting_id": "m1"})
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                sequence_request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/meetings/m1/live-agent-turns/sequence",
                    data=json.dumps(
                        {
                            "timeout_seconds": 0,
                            "stop_on_timeout": True,
                            "turns": [
                                {"agent_id": "agent-a", "content": "first prompt"},
                                {"agent_id": "agent-b", "content": "second prompt"},
                            ],
                        }
                    ).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urlopen(sequence_request, timeout=4) as response:
                    sequence = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

            self.assertEqual(sequence["status"], "stopped")
            self.assertEqual(sequence["timeout_count"], 1)
            self.assertEqual(sequence["skipped_count"], 1)
            self.assertTrue(sequence["stopped"])
            self.assertEqual([result["agent_id"] for result in sequence["results"]], ["agent-a", "agent-b"])
            self.assertEqual([result["status"] for result in sequence["results"]], ["timeout", "skipped"])
            request_events = [
                event for event in read_live_events(meeting_dir, limit=None) if event.get("kind") == "live_agent_turn_request"
            ]
            self.assertEqual([event["target_agent_id"] for event in request_events], ["agent-a"])

    def test_live_agent_official_turn_sequence_validates_all_turns_before_append(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            meeting_dir = root / "meetings" / "m1"
            meeting_dir.mkdir(parents=True)
            write_live_state(meeting_dir, {"meeting_id": "m1", "topic": "runtime", "live_status": "running"})
            connect_live_agent_payload(root, {"agent_id": "agent-a", "display_name": "Agent A", "meeting_id": "m1"})

            with self.assertRaises(ValueError):
                live_agent_turn_sequence_payload(
                    root,
                    "m1",
                    {
                        "timeout_seconds": 0,
                        "turns": [
                            {"agent_id": "agent-a", "content": "valid first prompt"},
                            {"agent_id": "agent-b", "content": "invalid second prompt"},
                        ],
                    },
                )

            self.assertEqual(read_live_events(meeting_dir, limit=None), [])

    def test_live_agent_official_turn_round_builds_sequence_from_bindings(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            meeting_dir = root / "meetings" / "m1"
            meeting_dir.mkdir(parents=True)
            write_live_state(
                meeting_dir,
                {
                    "meeting_id": "m1",
                    "topic": "runtime",
                    "live_status": "running",
                    "roles": [
                        {"id": "architect", "display_name": "Architect"},
                        {"id": "critic", "display_name": "Critic"},
                    ],
                    "meeting_template": {
                        "rounds": [
                            {
                                "id": "round_1",
                                "instruction": "Template instruction",
                                "turn_control": {"selection": "all_roles"},
                            }
                        ]
                    },
                    "agent_bindings": [
                        {"role_id": "architect", "agent_id": "agent-a"},
                        {"role_id": "critic", "agent_id": "agent-b"},
                    ],
                    "debate_rounds": [],
                },
            )
            for agent_id, display_name in (("agent-a", "Agent A"), ("agent-b", "Agent B")):
                connect_live_agent_payload(
                    root,
                    {
                        "agent_id": agent_id,
                        "display_name": display_name,
                        "provider_kind": "local_cli",
                        "connection_kind": "local_cli",
                        "meeting_id": "m1",
                        "engagement_mode": "moderator_called",
                    },
                )
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            errors = []
            answered_request_ids = set()

            def answer_round_requests():
                try:
                    deadline = time.time() + 4
                    while time.time() < deadline and len(answered_request_ids) < 2:
                        for event in read_live_events(meeting_dir, limit=None):
                            if event.get("kind") != "live_agent_turn_request":
                                continue
                            if event["id"] in answered_request_ids:
                                continue
                            agent_id = event["target_agent_id"]
                            reply_request = Request(
                                f"http://127.0.0.1:{server.server_port}/api/live-agents/{agent_id}/official-turn",
                                data=json.dumps(
                                    {
                                        "meeting_id": "m1",
                                        "source_event_id": event["id"],
                                        "content": f"{agent_id} round reply",
                                    }
                                ).encode("utf-8"),
                                headers={"Content-Type": "application/json"},
                                method="POST",
                            )
                            with urlopen(reply_request, timeout=4) as response:
                                response.read()
                            answered_request_ids.add(event["id"])
                        time.sleep(0.01)
                except Exception as error:  # pragma: no cover - failure is asserted below
                    errors.append(error)

            responder = threading.Thread(target=answer_round_requests, daemon=True)
            responder.start()
            try:
                round_request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/meetings/m1/live-agent-turns/round",
                    data=json.dumps(
                        {
                            "round_id": "round_1",
                            "content": "private round instruction",
                            "timeout_seconds": 2,
                        }
                    ).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urlopen(round_request, timeout=6) as response:
                    round_result = json.loads(response.read().decode("utf-8"))
                responder.join(timeout=2)
                with urlopen(f"http://127.0.0.1:{server.server_port}/api/live-agent-operations", timeout=4) as response:
                    operations = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

            if errors:
                raise errors[0]
            self.assertEqual(round_result["status"], "answered")
            self.assertEqual(round_result["round_id"], "round_1")
            self.assertEqual(round_result["role_ids"], ["architect", "critic"])
            self.assertEqual([result["agent_id"] for result in round_result["results"]], ["agent-a", "agent-b"])
            self.assertEqual(round_result["results"][0]["request_event"]["display_name"], "Architect")
            self.assertEqual(round_result["results"][1]["request_event"]["turn_id"], "round_1:1:critic")
            live_state = json.loads((meeting_dir / "live_state.json").read_text(encoding="utf-8"))
            self.assertEqual(
                live_state["debate_rounds"],
                [
                    {
                        "id": "round_1",
                        "status": "answered",
                        "role_ids": ["architect", "critic"],
                        "turn_count": 2,
                        "answered_count": 2,
                        "timeout_count": 0,
                        "skipped_count": 0,
                    }
                ],
            )
            transcript = build_meeting_payload(meeting_dir)["artifacts"]["transcript.md"]
            self.assertIn("agent-a round reply", transcript)
            self.assertIn("agent-b round reply", transcript)
            self.assertNotIn("private round instruction", transcript)
            round_operations = [item for item in operations["operations"] if item["operation"] == "official_turn.round"]
            self.assertEqual(len(round_operations), 1)
            self.assertEqual(round_operations[0]["status"], "success")
            operation_blob = json.dumps(operations["operations"], ensure_ascii=False)
            self.assertNotIn("private round instruction", operation_blob)
            self.assertNotIn("agent-a round reply", operation_blob)

    def test_live_agent_official_turn_round_preserves_existing_debate_round_fields(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            meeting_dir = root / "meetings" / "m1"
            meeting_dir.mkdir(parents=True)
            write_live_state(
                meeting_dir,
                {
                    "meeting_id": "m1",
                    "topic": "runtime",
                    "live_status": "running",
                    "roles": [{"id": "architect", "display_name": "Architect"}],
                    "meeting_template": {
                        "rounds": [
                            {
                                "id": "round_1",
                                "instruction": "Template instruction",
                                "turn_control": {"selection": "all_roles"},
                            }
                        ]
                    },
                    "agent_bindings": [{"role_id": "architect", "agent_id": "agent-a"}],
                    "debate_rounds": [
                        {
                            "id": "round_1",
                            "title": "Keep title",
                            "messages": [{"content": "existing transcript detail"}],
                            "status": "draft",
                        }
                    ],
                },
            )
            connect_live_agent_payload(root, {"agent_id": "agent-a", "display_name": "Agent A", "meeting_id": "m1"})
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            errors = []

            def answer_request():
                try:
                    deadline = time.time() + 4
                    answered = False
                    while time.time() < deadline and not answered:
                        for event in read_live_events(meeting_dir, limit=None):
                            if event.get("kind") != "live_agent_turn_request":
                                continue
                            reply_request = Request(
                                f"http://127.0.0.1:{server.server_port}/api/live-agents/{event['target_agent_id']}/official-turn",
                                data=json.dumps(
                                    {
                                        "meeting_id": "m1",
                                        "source_event_id": event["id"],
                                        "content": "preserve fields reply",
                                    }
                                ).encode("utf-8"),
                                headers={"Content-Type": "application/json"},
                                method="POST",
                            )
                            with urlopen(reply_request, timeout=4) as response:
                                response.read()
                            answered = True
                            break
                        time.sleep(0.01)
                except Exception as error:  # pragma: no cover - failure is asserted below
                    errors.append(error)

            responder = threading.Thread(target=answer_request, daemon=True)
            responder.start()
            try:
                round_request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/meetings/m1/live-agent-turns/round",
                    data=json.dumps({"round_id": "round_1", "timeout_seconds": 2}).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urlopen(round_request, timeout=6) as response:
                    round_result = json.loads(response.read().decode("utf-8"))
                responder.join(timeout=2)
            finally:
                server.shutdown()
                server.server_close()

            if errors:
                raise errors[0]
            self.assertEqual(round_result["status"], "answered")
            live_state = json.loads((meeting_dir / "live_state.json").read_text(encoding="utf-8"))
            self.assertEqual(len(live_state["debate_rounds"]), 1)
            round_record = live_state["debate_rounds"][0]
            self.assertEqual(round_record["id"], "round_1")
            self.assertEqual(round_record["status"], "answered")
            self.assertEqual(round_record["role_ids"], ["architect"])
            self.assertEqual(round_record["turn_count"], 1)
            self.assertEqual(round_record["title"], "Keep title")
            self.assertEqual(round_record["messages"], [{"content": "existing transcript detail"}])

    def test_live_agent_official_turn_rounds_runs_remaining_template_rounds(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            meeting_dir = root / "meetings" / "m1"
            meeting_dir.mkdir(parents=True)
            write_live_state(
                meeting_dir,
                {
                    "meeting_id": "m1",
                    "topic": "runtime",
                    "live_status": "running",
                    "roles": [
                        {"id": "architect", "display_name": "Architect"},
                        {"id": "critic", "display_name": "Critic"},
                    ],
                    "meeting_template": {
                        "rounds": [
                            {"id": "round_1", "instruction": "Done", "turn_control": {"selection": "all_roles"}},
                            {"id": "round_2", "instruction": "Next", "turn_control": {"selection": "selected_roles", "speaker_role_ids": ["critic"]}},
                            {"id": "round_3", "instruction": "Later", "turn_control": {"selection": "all_roles"}},
                        ]
                    },
                    "agent_bindings": [
                        {"role_id": "architect", "agent_id": "agent-a"},
                        {"role_id": "critic", "agent_id": "agent-b"},
                    ],
                    "debate_rounds": [{"id": "round_1", "status": "answered"}],
                },
            )
            for agent_id, display_name in (("agent-a", "Agent A"), ("agent-b", "Agent B")):
                connect_live_agent_payload(root, {"agent_id": agent_id, "display_name": display_name, "meeting_id": "m1"})
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            errors = []
            answered_request_ids = set()

            def answer_remaining_round_requests():
                try:
                    deadline = time.time() + 4
                    while time.time() < deadline and len(answered_request_ids) < 1:
                        for event in read_live_events(meeting_dir, limit=None):
                            if event.get("kind") != "live_agent_turn_request" or event["id"] in answered_request_ids:
                                continue
                            agent_id = event["target_agent_id"]
                            reply_request = Request(
                                f"http://127.0.0.1:{server.server_port}/api/live-agents/{agent_id}/official-turn",
                                data=json.dumps(
                                    {
                                        "meeting_id": "m1",
                                        "source_event_id": event["id"],
                                        "content": f"{agent_id} remaining reply",
                                    }
                                ).encode("utf-8"),
                                headers={"Content-Type": "application/json"},
                                method="POST",
                            )
                            with urlopen(reply_request, timeout=4) as response:
                                response.read()
                            answered_request_ids.add(event["id"])
                        time.sleep(0.01)
                except Exception as error:  # pragma: no cover - failure is asserted below
                    errors.append(error)

            responder = threading.Thread(target=answer_remaining_round_requests, daemon=True)
            responder.start()
            try:
                rounds_request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/meetings/m1/live-agent-turns/rounds",
                    data=json.dumps({"timeout_seconds": 2, "max_rounds": 1}).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urlopen(rounds_request, timeout=6) as response:
                    rounds_result = json.loads(response.read().decode("utf-8"))
                responder.join(timeout=2)
                with urlopen(f"http://127.0.0.1:{server.server_port}/api/live-agent-operations?limit=20", timeout=4) as response:
                    operations = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

            if errors:
                raise errors[0]
            self.assertEqual(rounds_result["status"], "answered")
            self.assertEqual(rounds_result["round_count"], 1)
            self.assertEqual(rounds_result["answered_round_count"], 1)
            self.assertEqual(rounds_result["results"][0]["round_id"], "round_2")
            self.assertEqual(rounds_result["results"][0]["role_ids"], ["critic"])
            live_state = json.loads((meeting_dir / "live_state.json").read_text(encoding="utf-8"))
            self.assertEqual([round_item["id"] for round_item in live_state["debate_rounds"]], ["round_1", "round_2"])
            operations_text = json.dumps(operations["operations"], ensure_ascii=False)
            self.assertIn('"official_turn.rounds"', operations_text)
            self.assertNotIn("remaining reply", operations_text)
            rounds_operations = [item for item in operations["operations"] if item["operation"] == "official_turn.rounds"]
            self.assertEqual(rounds_operations[0]["status"], "success")
            self.assertEqual(rounds_operations[0]["details"]["round_ids"], ["round_2"])
            self.assertEqual(rounds_operations[0]["details"]["statuses"], ["answered"])

    def test_live_agent_official_turn_rounds_can_finalize_after_success(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            meeting_dir = root / "meetings" / "m1"
            meeting_dir.mkdir(parents=True)
            write_live_state(
                meeting_dir,
                {
                    "meeting_id": "m1",
                    "topic": "runtime",
                    "live_status": "running",
                    "roles": [{"id": "architect", "display_name": "Architect"}],
                    "meeting_template": {
                        "rounds": [
                            {"id": "round_1", "instruction": "Next", "turn_control": {"selection": "all_roles"}},
                        ]
                    },
                    "agent_bindings": [{"role_id": "architect", "agent_id": "agent-a"}],
                    "debate_rounds": [],
                },
            )
            connect_live_agent_payload(root, {"agent_id": "agent-a", "display_name": "Agent A", "meeting_id": "m1"})
            finalized = {
                "status": "finalized",
                "meeting_id": "m1",
                "official_event_count": 1,
                "artifact_event_id": "artifact-1",
                "shared_memory": {
                    "official_event_count": 1,
                    "last_official_event_id": "reply-1",
                    "decision_count": 0,
                    "open_question_count": 0,
                    "action_item_count": 1,
                },
            }
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            errors = []
            answered_request_ids = set()

            def answer_remaining_round_requests():
                try:
                    deadline = time.time() + 4
                    while time.time() < deadline and len(answered_request_ids) < 1:
                        for event in read_live_events(meeting_dir, limit=None):
                            if event.get("kind") != "live_agent_turn_request" or event["id"] in answered_request_ids:
                                continue
                            reply_request = Request(
                                f"http://127.0.0.1:{server.server_port}/api/live-agents/agent-a/official-turn",
                                data=json.dumps(
                                    {
                                        "meeting_id": "m1",
                                        "source_event_id": event["id"],
                                        "content": "agent-a remaining reply",
                                    }
                                ).encode("utf-8"),
                                headers={"Content-Type": "application/json"},
                                method="POST",
                            )
                            with urlopen(reply_request, timeout=4) as response:
                                response.read()
                            answered_request_ids.add(event["id"])
                        time.sleep(0.01)
                except Exception as error:  # pragma: no cover - failure is asserted below
                    errors.append(error)

            responder = threading.Thread(target=answer_remaining_round_requests, daemon=True)
            responder.start()
            try:
                with patch("agentsassemble.gui.finalize_live_agent_meeting", return_value=finalized) as finalize_meeting:
                    rounds_request = Request(
                        f"http://127.0.0.1:{server.server_port}/api/meetings/m1/live-agent-turns/rounds",
                        data=json.dumps({"timeout_seconds": 2, "max_rounds": 1, "finalize_after_rounds": True}).encode("utf-8"),
                        headers={"Content-Type": "application/json"},
                        method="POST",
                    )
                    with urlopen(rounds_request, timeout=6) as response:
                        rounds_result = json.loads(response.read().decode("utf-8"))
                    responder.join(timeout=2)
                with urlopen(f"http://127.0.0.1:{server.server_port}/api/live-agent-operations?limit=20", timeout=4) as response:
                    operations = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

            if errors:
                raise errors[0]
            finalize_meeting.assert_called_once_with((root / "meetings" / "m1").resolve())
            self.assertEqual(rounds_result["status"], "answered")
            self.assertEqual(rounds_result["finalization"]["status"], "finalized")
            self.assertEqual(rounds_result["finalization"]["official_event_count"], 1)
            rounds_operations = [item for item in operations["operations"] if item["operation"] == "official_turn.rounds"]
            self.assertEqual(rounds_operations[0]["status"], "success")
            self.assertEqual(rounds_operations[0]["details"]["finalization_status"], "finalized")
            self.assertEqual(rounds_operations[0]["details"]["finalization_official_event_count"], 1)
            self.assertEqual(rounds_operations[0]["details"]["shared_memory_official_event_count"], 1)
            self.assertEqual(rounds_operations[0]["details"]["shared_memory_last_event_id"], "reply-1")
            self.assertEqual(rounds_operations[0]["details"]["shared_memory_action_item_count"], 1)
            operations_text = json.dumps(operations["operations"], ensure_ascii=False)
            self.assertNotIn("remaining reply", operations_text)

    def test_live_agent_official_turn_rounds_skip_finalization_when_template_rounds_remain(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            meeting_dir = root / "meetings" / "m1"
            meeting_dir.mkdir(parents=True)
            write_live_state(
                meeting_dir,
                {
                    "meeting_id": "m1",
                    "topic": "runtime",
                    "live_status": "running",
                    "roles": [{"id": "architect", "display_name": "Architect"}],
                    "meeting_template": {
                        "rounds": [
                            {"id": "round_1", "instruction": "First", "turn_control": {"selection": "all_roles"}},
                            {"id": "round_2", "instruction": "Second", "turn_control": {"selection": "all_roles"}},
                        ]
                    },
                    "agent_bindings": [{"role_id": "architect", "agent_id": "agent-a"}],
                    "debate_rounds": [],
                },
            )
            connect_live_agent_payload(root, {"agent_id": "agent-a", "display_name": "Agent A", "meeting_id": "m1"})
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            errors = []
            answered_request_ids = set()

            def answer_first_request():
                try:
                    deadline = time.time() + 4
                    while time.time() < deadline and not answered_request_ids:
                        for event in read_live_events(meeting_dir, limit=None):
                            if event.get("kind") != "live_agent_turn_request":
                                continue
                            reply_request = Request(
                                f"http://127.0.0.1:{server.server_port}/api/live-agents/agent-a/official-turn",
                                data=json.dumps(
                                    {
                                        "meeting_id": "m1",
                                        "source_event_id": event["id"],
                                        "content": "round one reply",
                                    }
                                ).encode("utf-8"),
                                headers={"Content-Type": "application/json"},
                                method="POST",
                            )
                            with urlopen(reply_request, timeout=4) as response:
                                response.read()
                            answered_request_ids.add(event["id"])
                            break
                        time.sleep(0.01)
                except Exception as error:  # pragma: no cover - failure is asserted below
                    errors.append(error)

            responder = threading.Thread(target=answer_first_request, daemon=True)
            responder.start()
            try:
                with patch(
                    "agentsassemble.gui.finalize_live_agent_meeting",
                    return_value={"status": "finalized", "meeting_id": "m1", "official_event_count": 1},
                ) as finalize_meeting:
                    rounds_request = Request(
                        f"http://127.0.0.1:{server.server_port}/api/meetings/m1/live-agent-turns/rounds",
                        data=json.dumps({"timeout_seconds": 2, "max_rounds": 1, "finalize_after_rounds": True}).encode("utf-8"),
                        headers={"Content-Type": "application/json"},
                        method="POST",
                    )
                    with urlopen(rounds_request, timeout=6) as response:
                        rounds_result = json.loads(response.read().decode("utf-8"))
                    responder.join(timeout=2)
                with urlopen(f"http://127.0.0.1:{server.server_port}/api/live-agent-operations?limit=20", timeout=4) as response:
                    operations = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

            if errors:
                raise errors[0]
            finalize_meeting.assert_not_called()
            self.assertEqual(rounds_result["status"], "answered")
            self.assertEqual(rounds_result["finalization"]["status"], "skipped")
            self.assertEqual(rounds_result["finalization"]["reason"], "rounds_still_remaining")
            self.assertFalse((meeting_dir / "meeting.json").exists())
            rounds_operations = [item for item in operations["operations"] if item["operation"] == "official_turn.rounds"]
            self.assertEqual(rounds_operations[0]["status"], "degraded")
            self.assertEqual(rounds_operations[0]["details"]["finalization_status"], "skipped")
            self.assertEqual(rounds_operations[0]["details"]["finalization_reason"], "rounds_still_remaining")

    def test_live_agent_official_turn_rounds_does_not_duplicate_concurrent_remaining_round(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            meeting_dir = root / "meetings" / "m1"
            meeting_dir.mkdir(parents=True)
            write_live_state(
                meeting_dir,
                {
                    "meeting_id": "m1",
                    "topic": "runtime",
                    "live_status": "running",
                    "roles": [{"id": "architect", "display_name": "Architect"}],
                    "meeting_template": {
                        "rounds": [
                            {"id": "round_1", "instruction": "First", "turn_control": {"selection": "all_roles"}},
                        ]
                    },
                    "agent_bindings": [{"role_id": "architect", "agent_id": "agent-a"}],
                    "debate_rounds": [],
                },
            )
            connect_live_agent_payload(root, {"agent_id": "agent-a", "display_name": "Agent A", "meeting_id": "m1"})
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            start_gate = threading.Barrier(3)
            results = []
            errors = []
            answered_request_ids = set()

            def call_remaining_rounds():
                try:
                    start_gate.wait(timeout=2)
                    request = Request(
                        f"http://127.0.0.1:{server.server_port}/api/meetings/m1/live-agent-turns/rounds",
                        data=json.dumps({"timeout_seconds": 1, "max_rounds": 1}).encode("utf-8"),
                        headers={"Content-Type": "application/json"},
                        method="POST",
                    )
                    with urlopen(request, timeout=6) as response:
                        results.append(json.loads(response.read().decode("utf-8")))
                except Exception as error:  # pragma: no cover - failure is asserted below
                    errors.append(error)

            def answer_one_request():
                try:
                    first_seen_at = None
                    deadline = time.time() + 5
                    while time.time() < deadline and not answered_request_ids:
                        request_events = [
                            event
                            for event in read_live_events(meeting_dir, limit=None)
                            if event.get("kind") == "live_agent_turn_request"
                        ]
                        if request_events and first_seen_at is None:
                            first_seen_at = time.time()
                        should_answer = request_events and (
                            len(request_events) >= 2 or (first_seen_at is not None and time.time() - first_seen_at >= 0.25)
                        )
                        if should_answer:
                            event = request_events[0]
                            reply_request = Request(
                                f"http://127.0.0.1:{server.server_port}/api/live-agents/{event['target_agent_id']}/official-turn",
                                data=json.dumps(
                                    {
                                        "meeting_id": "m1",
                                        "source_event_id": event["id"],
                                        "content": "concurrent remaining reply",
                                    }
                                ).encode("utf-8"),
                                headers={"Content-Type": "application/json"},
                                method="POST",
                            )
                            with urlopen(reply_request, timeout=4) as response:
                                response.read()
                            answered_request_ids.add(event["id"])
                            break
                        time.sleep(0.01)
                except Exception as error:  # pragma: no cover - failure is asserted below
                    errors.append(error)

            caller_a = threading.Thread(target=call_remaining_rounds, daemon=True)
            caller_b = threading.Thread(target=call_remaining_rounds, daemon=True)
            responder = threading.Thread(target=answer_one_request, daemon=True)
            caller_a.start()
            caller_b.start()
            responder.start()
            try:
                start_gate.wait(timeout=2)
                caller_a.join(timeout=7)
                caller_b.join(timeout=7)
                responder.join(timeout=2)
            finally:
                server.shutdown()
                server.server_close()

            if errors:
                raise errors[0]
            self.assertEqual(len(results), 2)
            request_events = [
                event for event in read_live_events(meeting_dir, limit=None) if event.get("kind") == "live_agent_turn_request"
            ]
            self.assertEqual([event["turn_id"] for event in request_events], ["round_1:0:architect"])
            self.assertEqual(sorted(result["status"] for result in results), ["answered", "complete"])

    def test_live_agent_official_turn_round_skips_already_answered_round_after_remaining_batch(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            meeting_dir = root / "meetings" / "m1"
            meeting_dir.mkdir(parents=True)
            write_live_state(
                meeting_dir,
                {
                    "meeting_id": "m1",
                    "topic": "runtime",
                    "live_status": "running",
                    "roles": [{"id": "architect", "display_name": "Architect"}],
                    "meeting_template": {
                        "rounds": [
                            {"id": "round_1", "instruction": "First", "turn_control": {"selection": "all_roles"}},
                        ]
                    },
                    "agent_bindings": [{"role_id": "architect", "agent_id": "agent-a"}],
                    "debate_rounds": [],
                },
            )
            connect_live_agent_payload(root, {"agent_id": "agent-a", "display_name": "Agent A", "meeting_id": "m1"})
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            errors = []
            answered_request_ids = set()

            def answer_first_request():
                try:
                    deadline = time.time() + 4
                    while time.time() < deadline and not answered_request_ids:
                        for event in read_live_events(meeting_dir, limit=None):
                            if event.get("kind") != "live_agent_turn_request":
                                continue
                            reply_request = Request(
                                f"http://127.0.0.1:{server.server_port}/api/live-agents/{event['target_agent_id']}/official-turn",
                                data=json.dumps(
                                    {
                                        "meeting_id": "m1",
                                        "source_event_id": event["id"],
                                        "content": "first batch reply",
                                    }
                                ).encode("utf-8"),
                                headers={"Content-Type": "application/json"},
                                method="POST",
                            )
                            with urlopen(reply_request, timeout=4) as response:
                                response.read()
                            answered_request_ids.add(event["id"])
                            break
                        time.sleep(0.01)
                except Exception as error:  # pragma: no cover - failure is asserted below
                    errors.append(error)

            responder = threading.Thread(target=answer_first_request, daemon=True)
            responder.start()
            try:
                remaining_request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/meetings/m1/live-agent-turns/rounds",
                    data=json.dumps({"timeout_seconds": 2, "max_rounds": 1}).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urlopen(remaining_request, timeout=6) as response:
                    remaining_result = json.loads(response.read().decode("utf-8"))
                responder.join(timeout=2)
                duplicate_request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/meetings/m1/live-agent-turns/round",
                    data=json.dumps({"round_id": "round_1", "timeout_seconds": 0}).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urlopen(duplicate_request, timeout=4) as response:
                    duplicate_result = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

            if errors:
                raise errors[0]
            self.assertEqual(remaining_result["status"], "answered")
            self.assertEqual(duplicate_result["status"], "complete")
            self.assertEqual(duplicate_result["round_id"], "round_1")
            self.assertEqual(duplicate_result["turn_count"], 0)
            request_events = [
                event for event in read_live_events(meeting_dir, limit=None) if event.get("kind") == "live_agent_turn_request"
            ]
            self.assertEqual([event["turn_id"] for event in request_events], ["round_1:0:architect"])

    def test_live_agent_official_turn_rounds_respect_live_progress_when_meeting_json_exists(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            meeting_dir = root / "meetings" / "m1"
            meeting_dir.mkdir(parents=True)
            base_meeting = {
                "meeting_id": "m1",
                "topic": "runtime",
                "live_status": "running",
                "roles": [{"id": "architect", "display_name": "Architect"}],
                "meeting_template": {
                    "rounds": [
                        {"id": "round_1", "instruction": "First", "turn_control": {"selection": "all_roles"}},
                    ]
                },
                "agent_bindings": [{"role_id": "architect", "agent_id": "agent-a"}],
                "debate_rounds": [],
            }
            (meeting_dir / "meeting.json").write_text(json.dumps(base_meeting), encoding="utf-8")
            write_live_state(
                meeting_dir,
                {
                    **base_meeting,
                    "debate_rounds": [
                        {
                            "id": "round_1",
                            "status": "answered",
                            "role_ids": ["architect"],
                            "turn_count": 1,
                            "answered_count": 1,
                        }
                    ],
                },
            )
            connect_live_agent_payload(root, {"agent_id": "agent-a", "display_name": "Agent A", "meeting_id": "m1"})

            rounds_result = live_agent_turn_rounds_payload(root, "m1", {"timeout_seconds": 0, "max_rounds": 1})
            round_result = live_agent_turn_round_payload(root, "m1", {"round_id": "round_1", "timeout_seconds": 0})

            self.assertEqual(rounds_result["status"], "complete")
            self.assertEqual(rounds_result["round_count"], 0)
            self.assertEqual(round_result["status"], "complete")
            self.assertEqual(read_live_events(meeting_dir, limit=None), [])

    def test_live_agent_official_turn_rounds_treat_inner_complete_as_success(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            meeting_dir = root / "meetings" / "m1"
            meeting_dir.mkdir(parents=True)
            write_live_state(
                meeting_dir,
                {
                    "meeting_id": "m1",
                    "topic": "runtime",
                    "live_status": "running",
                    "roles": [{"id": "architect", "display_name": "Architect"}],
                    "meeting_template": {
                        "rounds": [
                            {"id": "round_1", "instruction": "First", "turn_control": {"selection": "all_roles"}},
                        ]
                    },
                    "agent_bindings": [{"role_id": "architect", "agent_id": "agent-a"}],
                    "debate_rounds": [{"id": "round_1", "status": "answered"}],
                },
            )
            connect_live_agent_payload(root, {"agent_id": "agent-a", "display_name": "Agent A", "meeting_id": "m1"})

            result = _live_agent_turn_rounds_payload_locked(
                root,
                "m1",
                ["round_1"],
                timeout_seconds=0,
                stop_on_timeout=False,
                max_rounds=1,
            )

            self.assertEqual(result["status"], "complete")
            self.assertEqual(result["round_count"], 1)
            self.assertEqual(result["answered_round_count"], 0)
            self.assertEqual(result["completed_round_count"], 1)
            self.assertEqual(result["results"][0]["status"], "complete")

    def test_meeting_payload_merges_live_round_progress_when_meeting_json_exists(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            meeting_dir = root / "meetings" / "m1"
            meeting_dir.mkdir(parents=True)
            base_meeting = {
                "meeting_id": "m1",
                "topic": "runtime",
                "live_status": "complete",
                "roles": [{"id": "architect", "display_name": "Architect"}],
                "meeting_template": {
                    "rounds": [
                        {"id": "round_1", "instruction": "First", "turn_control": {"selection": "all_roles"}},
                    ]
                },
                "agent_bindings": [{"role_id": "architect", "agent_id": "agent-a"}],
                "debate_rounds": [{"id": "round_1", "title": "Keep title", "status": "draft"}],
            }
            (meeting_dir / "meeting.json").write_text(json.dumps(base_meeting), encoding="utf-8")
            write_live_state(
                meeting_dir,
                {
                    **base_meeting,
                    "live_status": "running",
                    "debate_rounds": [
                        {
                            "id": "round_1",
                            "status": "answered",
                            "role_ids": ["architect"],
                            "turn_count": 1,
                            "answered_count": 1,
                        }
                    ],
                },
            )

            payload = build_meeting_payload(meeting_dir)

            self.assertEqual(payload["meeting"]["roles"], [{"id": "architect", "display_name": "Architect"}])
            self.assertEqual(payload["meeting"]["meeting_template"], base_meeting["meeting_template"])
            self.assertEqual(payload["meeting"]["live_status"], "complete")
            self.assertEqual(
                payload["meeting"]["debate_rounds"],
                [
                    {
                        "id": "round_1",
                        "title": "Keep title",
                        "status": "answered",
                        "role_ids": ["architect"],
                        "turn_count": 1,
                        "answered_count": 1,
                    }
                ],
            )

    def test_meeting_payload_ignores_invalid_live_progress_when_meeting_json_exists(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            meeting_dir = Path(temp_dir) / "meetings" / "m1"
            meeting_dir.mkdir(parents=True)
            (meeting_dir / "meeting.json").write_text(
                json.dumps(
                    {
                        "meeting_id": "m1",
                        "topic": "final",
                        "roles": [],
                        "meeting_template": {"rounds": []},
                        "debate_rounds": [],
                        "live_status": "complete",
                    }
                ),
                encoding="utf-8",
            )
            (meeting_dir / "live_state.json").write_text("{", encoding="utf-8")

            payload = build_meeting_payload(meeting_dir)

            self.assertEqual(payload["meeting"]["meeting_id"], "m1")
            self.assertEqual(payload["meeting"]["live_status"], "complete")

    def test_live_agent_official_turn_rounds_marks_inner_stopped_round_as_degraded(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            meeting_dir = root / "meetings" / "m1"
            meeting_dir.mkdir(parents=True)
            write_live_state(
                meeting_dir,
                {
                    "meeting_id": "m1",
                    "topic": "runtime",
                    "live_status": "running",
                    "roles": [
                        {"id": "architect", "display_name": "Architect"},
                        {"id": "critic", "display_name": "Critic"},
                    ],
                    "meeting_template": {
                        "rounds": [
                            {"id": "round_1", "instruction": "First", "turn_control": {"selection": "all_roles"}},
                        ]
                    },
                    "agent_bindings": [
                        {"role_id": "architect", "agent_id": "agent-a"},
                        {"role_id": "critic", "agent_id": "agent-b"},
                    ],
                    "debate_rounds": [],
                },
            )
            for agent_id in ("agent-a", "agent-b"):
                connect_live_agent_payload(root, {"agent_id": agent_id, "display_name": agent_id, "meeting_id": "m1"})
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                rounds_request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/meetings/m1/live-agent-turns/rounds",
                    data=json.dumps({"timeout_seconds": 0, "stop_on_timeout": True, "max_rounds": 1}).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urlopen(rounds_request, timeout=4) as response:
                    rounds_result = json.loads(response.read().decode("utf-8"))
                with urlopen(f"http://127.0.0.1:{server.server_port}/api/live-agent-operations?limit=20", timeout=4) as response:
                    operations = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

            self.assertEqual(rounds_result["status"], "stopped")
            self.assertEqual(rounds_result["round_count"], 1)
            self.assertEqual(rounds_result["answered_round_count"], 0)
            self.assertEqual(rounds_result["results"][0]["status"], "stopped")
            self.assertEqual(rounds_result["results"][0]["timeout_count"], 1)
            self.assertEqual(rounds_result["results"][0]["skipped_count"], 1)
            request_events = [event for event in read_live_events(meeting_dir, limit=None) if event.get("kind") == "live_agent_turn_request"]
            self.assertEqual([event["turn_id"] for event in request_events], ["round_1:0:architect"])
            rounds_operations = [item for item in operations["operations"] if item["operation"] == "official_turn.rounds"]
            self.assertEqual(rounds_operations[0]["status"], "degraded")

    def test_live_agent_official_turn_rounds_stops_remaining_after_timeout(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            meeting_dir = root / "meetings" / "m1"
            meeting_dir.mkdir(parents=True)
            write_live_state(
                meeting_dir,
                {
                    "meeting_id": "m1",
                    "topic": "runtime",
                    "live_status": "running",
                    "roles": [{"id": "architect", "display_name": "Architect"}],
                    "meeting_template": {
                        "rounds": [
                            {"id": "round_1", "instruction": "First", "turn_control": {"selection": "all_roles"}},
                            {"id": "round_2", "instruction": "Second", "turn_control": {"selection": "all_roles"}},
                        ]
                    },
                    "agent_bindings": [{"role_id": "architect", "agent_id": "agent-a"}],
                    "debate_rounds": [],
                },
            )
            connect_live_agent_payload(root, {"agent_id": "agent-a", "display_name": "Agent A", "meeting_id": "m1"})
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                rounds_request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/meetings/m1/live-agent-turns/rounds",
                    data=json.dumps({"timeout_seconds": 0, "stop_on_timeout": True, "max_rounds": 2}).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urlopen(rounds_request, timeout=4) as response:
                    rounds_result = json.loads(response.read().decode("utf-8"))
                with urlopen(f"http://127.0.0.1:{server.server_port}/api/live-agent-operations?limit=20", timeout=4) as response:
                    operations = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

            self.assertEqual(rounds_result["status"], "stopped")
            self.assertEqual(rounds_result["timeout_round_count"], 1)
            self.assertEqual(rounds_result["skipped_round_count"], 1)
            self.assertEqual([result["round_id"] for result in rounds_result["results"]], ["round_1", "round_2"])
            self.assertEqual([result["status"] for result in rounds_result["results"]], ["timeout", "skipped"])
            request_events = [event for event in read_live_events(meeting_dir, limit=None) if event.get("kind") == "live_agent_turn_request"]
            self.assertEqual([event["turn_id"] for event in request_events], ["round_1:0:architect"])
            rounds_operations = [item for item in operations["operations"] if item["operation"] == "official_turn.rounds"]
            self.assertEqual(rounds_operations[0]["status"], "degraded")

    def test_live_agent_meeting_start_creates_visible_bound_meeting_and_round_control(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            council_config = root / "council.json"
            agent_config = root / "agents.json"
            council_config.write_text(
                json.dumps(
                    {
                        "topic": "resident runtime",
                        "question": "Can resident agents run a real official round?",
                        "roles": [
                            {
                                "id": "architect",
                                "display_name": "Architect",
                                "lens": "Architecture",
                                "research_focus": "system shape",
                            },
                            {
                                "id": "critic",
                                "display_name": "Critic",
                                "lens": "Critique",
                                "research_focus": "risk",
                            },
                        ],
                        "meeting_template": {
                            "id": "resident-template",
                            "display_name": "Resident Template",
                            "rounds": [
                                {
                                    "id": "round_1",
                                    "title": "Resident round",
                                    "instruction": "Template instruction",
                                    "turn_control": {"selection": "all_roles"},
                                }
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
                                "id": "local-cli",
                                "kind": "local_cli",
                                "display_name": "Local CLI",
                                "endpoint": "https://api.example/run?api_key=SECRET",
                                "command": ["fake-agent"],
                            }
                        ],
                        "permission_profiles": [
                            {
                                "id": "meeting_readonly",
                                "meeting_read": True,
                                "lobby_chat": True,
                                "official_turn": True,
                            }
                        ],
                        "agent_bindings": [
                            {
                                "agent_id": "agent-a",
                                "role_id": "architect",
                                "provider_id": "local-cli",
                                "permission_profile_id": "meeting_readonly",
                                "engagement_mode": "always",
                            },
                            {
                                "agent_id": "agent-b",
                                "role_id": "critic",
                                "provider_id": "local-cli",
                                "permission_profile_id": "meeting_readonly",
                                "engagement_mode": "watch",
                            },
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            meeting_dir = root / "meetings" / "resident-m1"
            errors = []
            answered_request_ids = set()

            def answer_round_requests():
                try:
                    deadline = time.time() + 4
                    while time.time() < deadline and len(answered_request_ids) < 2:
                        if not meeting_dir.exists():
                            time.sleep(0.01)
                            continue
                        for event in read_live_events(meeting_dir, limit=None):
                            if event.get("kind") != "live_agent_turn_request":
                                continue
                            if event["id"] in answered_request_ids:
                                continue
                            agent_id = event["target_agent_id"]
                            reply_request = Request(
                                f"http://127.0.0.1:{server.server_port}/api/live-agents/{agent_id}/official-turn",
                                data=json.dumps(
                                    {
                                        "meeting_id": "resident-m1",
                                        "source_event_id": event["id"],
                                        "content": f"{agent_id} resident reply",
                                    }
                                ).encode("utf-8"),
                                headers={"Content-Type": "application/json"},
                                method="POST",
                            )
                            with urlopen(reply_request, timeout=4) as response:
                                response.read()
                            answered_request_ids.add(event["id"])
                        time.sleep(0.01)
                except Exception as error:  # pragma: no cover - failure is asserted below
                    errors.append(error)

            responder = threading.Thread(target=answer_round_requests, daemon=True)
            responder.start()
            try:
                start_request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/live-agent-meetings/start",
                    data=json.dumps(
                        {
                            "meeting_id": "resident-m1",
                            "council_config_path": str(council_config),
                            "agent_config_path": str(agent_config),
                        }
                    ).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urlopen(start_request, timeout=4) as response:
                    start_payload = json.loads(response.read().decode("utf-8"))
                with urlopen(f"http://127.0.0.1:{server.server_port}/api/meetings", timeout=4) as response:
                    meetings_payload = json.loads(response.read().decode("utf-8"))
                with urlopen(f"http://127.0.0.1:{server.server_port}/api/live-agents", timeout=4) as response:
                    agents_payload = json.loads(response.read().decode("utf-8"))
                round_request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/meetings/resident-m1/live-agent-turns/round",
                    data=json.dumps({"round_id": "round_1", "timeout_seconds": 2}).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urlopen(round_request, timeout=6) as response:
                    round_result = json.loads(response.read().decode("utf-8"))
                responder.join(timeout=2)
                with urlopen(f"http://127.0.0.1:{server.server_port}/api/live-agent-operations?limit=20", timeout=4) as response:
                    operations = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

            if errors:
                raise errors[0]
            self.assertEqual(start_payload["meeting_id"], "resident-m1")
            self.assertEqual(start_payload["meeting"]["live_status"], "running")
            self.assertFalse(start_payload["meeting"].get("diagnostic", False))
            self.assertIn("resident-m1", {meeting["meeting_id"] for meeting in meetings_payload["meetings"]})
            agents = {agent["agent_id"]: agent for agent in agents_payload["agents"]}
            binding_modes = {binding["agent_id"]: binding["engagement_mode"] for binding in start_payload["meeting"]["agent_bindings"]}
            self.assertEqual(binding_modes["agent-a"], "moderator_called")
            self.assertEqual(binding_modes["agent-b"], "moderator_called")
            self.assertEqual(agents["agent-a"]["meeting_id"], "resident-m1")
            self.assertEqual(agents["agent-b"]["meeting_id"], "resident-m1")
            self.assertEqual(agents["agent-a"]["engagement_mode"], "moderator_called")
            self.assertEqual(agents["agent-b"]["engagement_mode"], "moderator_called")
            self.assertEqual(agents["agent-a"]["endpoint"], "")
            self.assertEqual(agents["agent-b"]["endpoint"], "")
            self.assertEqual(round_result["status"], "answered")
            self.assertEqual([result["agent_id"] for result in round_result["results"]], ["agent-a", "agent-b"])
            transcript = build_meeting_payload(meeting_dir)["artifacts"]["transcript.md"]
            self.assertIn("agent-a resident reply", transcript)
            self.assertIn("agent-b resident reply", transcript)
            operation_pairs = [(item["operation"], item["status"], item["target_id"]) for item in operations["operations"]]
            self.assertIn(("meeting.start", "success", "resident-m1"), operation_pairs)
            self.assertIn(("official_turn.round", "success", "resident-m1"), operation_pairs)
            operation_blob = json.dumps(operations["operations"], ensure_ascii=False)
            self.assertNotIn(str(council_config), operation_blob)
            self.assertNotIn(str(agent_config), operation_blob)
            self.assertNotIn("resident reply", operation_blob)

    def test_live_agent_meeting_start_rejects_path_traversal_meeting_id(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/live-agent-meetings/start",
                    data=json.dumps({"meeting_id": "../outside"}).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with self.assertRaises(HTTPError) as raised:
                    urlopen(request, timeout=4)
                self.assertEqual(raised.exception.code, 400)
                raised.exception.close()
            finally:
                server.shutdown()
                server.server_close()

            self.assertFalse((root / "outside").exists())

    def test_live_agent_session_start_creates_meeting_starts_group_and_records_safe_operation(self):
        class FakeSessionSupervisor:
            def __init__(self, output_root: Path) -> None:
                self.output_root = output_root
                self.started = []
                self.group = {}

            def start_group(self, **kwargs):
                self.started.append(kwargs)
                heartbeat_live_agent(self.output_root, "agent-a", status="online")
                heartbeat_live_agent(self.output_root, "agent-b", status="working")
                self.group = {
                    "group_id": kwargs.get("group_id") or "resident-main",
                    "status": "running",
                    "agents": [
                        {"agent_id": "agent-a", "display_name": "Agent A", "provider_kind": "local_cli", "connection_kind": "local_cli"},
                        {"agent_id": "agent-b", "display_name": "Agent B", "provider_kind": "local_cli", "connection_kind": "local_cli"},
                    ],
                }
                return self.group

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            council_config = root / "council.json"
            agent_config = root / "agents.json"
            live_agent_config = root / "live-agents.json"
            council_config.write_text(
                json.dumps(
                    {
                        "topic": "resident session",
                        "question": "Can a resident session start in one operation?",
                        "roles": [
                            {"id": "architect", "display_name": "Architect", "lens": "Architecture", "research_focus": "system"},
                            {"id": "critic", "display_name": "Critic", "lens": "Critique", "research_focus": "risk"},
                        ],
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
                                "id": "local-cli",
                                "kind": "local_cli",
                                "display_name": "Local CLI",
                                "command": [sys.executable, "-c", "print('ok')"],
                            }
                        ],
                        "permission_profiles": [
                            {"id": "meeting_readonly", "meeting_read": True, "lobby_chat": True, "official_turn": True}
                        ],
                        "agent_bindings": [
                            {
                                "agent_id": "agent-a",
                                "role_id": "architect",
                                "provider_id": "local-cli",
                                "permission_profile_id": "meeting_readonly",
                                "engagement_mode": "always",
                            },
                            {
                                "agent_id": "agent-b",
                                "role_id": "critic",
                                "provider_id": "local-cli",
                                "permission_profile_id": "meeting_readonly",
                                "engagement_mode": "watch",
                            },
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            live_agent_config.write_text(
                json.dumps(
                    {
                        "agents": [
                            {
                                "agent_id": "agent-a",
                                "display_name": "Agent A",
                                "provider_kind": "local_cli",
                                "connection_kind": "local_cli",
                                "command": [sys.executable, "-c", "print('ok')"],
                            },
                            {
                                "agent_id": "agent-b",
                                "display_name": "Agent B",
                                "provider_kind": "local_cli",
                                "connection_kind": "local_cli",
                                "command": [sys.executable, "-c", "print('ok')"],
                            },
                        ]
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            supervisor = FakeSessionSupervisor(root)
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root, process_supervisor=supervisor))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/live-agent-sessions/start",
                    data=json.dumps(
                        {
                            "meeting_id": "resident-m1",
                            "group_id": "resident-main",
                            "council_config_path": str(council_config),
                            "agent_config_path": str(agent_config),
                            "live_agent_config_path": str(live_agent_config),
                            "connect_timeout_seconds": 0,
                            "auto_restart": True,
                            "max_restarts": 2,
                            "restart_backoff_seconds": 1,
                        }
                    ).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urlopen(request, timeout=4) as response:
                    session_payload = json.loads(response.read().decode("utf-8"))
                with urlopen(f"http://127.0.0.1:{server.server_port}/api/live-agent-operations?limit=20", timeout=4) as response:
                    operations = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

            self.assertEqual(session_payload["status"], "ready")
            self.assertEqual(session_payload["meeting_id"], "resident-m1")
            self.assertEqual(session_payload["group_id"], "resident-main")
            self.assertEqual(session_payload["meeting"]["role_count"], 2)
            self.assertEqual(session_payload["meeting"]["bound_agent_count"], 2)
            self.assertEqual(session_payload["group"], {"group_id": "resident-main", "status": "running"})
            self.assertEqual(session_payload["connection"]["connected"], 2)
            self.assertEqual(session_payload["connection"]["expected"], 2)
            self.assertEqual(supervisor.started[0]["config_path"], live_agent_config)
            self.assertEqual(supervisor.started[0]["group_id"], "resident-main")
            self.assertTrue(supervisor.started[0]["auto_restart"])
            self.assertEqual(supervisor.started[0]["max_restarts"], 2)
            meeting = json.loads((root / "meetings" / "resident-m1" / "live_state.json").read_text(encoding="utf-8"))
            self.assertEqual({binding["engagement_mode"] for binding in meeting["agent_bindings"]}, {"moderator_called"})
            session_operations = [operation for operation in operations["operations"] if operation["operation"] == "session.start"]
            self.assertEqual(session_operations[-1]["status"], "success")
            self.assertEqual(session_operations[-1]["target_id"], "resident-m1")
            self.assertEqual(session_operations[-1]["details"]["result_status"], "ready")
            operation_blob = json.dumps(session_operations, ensure_ascii=False)
            self.assertNotIn(str(council_config), operation_blob)
            self.assertNotIn(str(agent_config), operation_blob)
            self.assertNotIn(str(live_agent_config), operation_blob)

    def test_live_agent_session_start_auto_runs_remaining_rounds_when_ready(self):
        class FakeSessionSupervisor:
            def __init__(self, output_root: Path) -> None:
                self.output_root = output_root
                self.started = []

            def start_group(self, **kwargs):
                self.started.append(kwargs)
                heartbeat_live_agent(self.output_root, "agent-a", status="online")
                return {
                    "group_id": kwargs.get("group_id") or "resident-main",
                    "status": "running",
                    "agents": [
                        {"agent_id": "agent-a", "display_name": "Agent A", "provider_kind": "local_cli", "connection_kind": "local_cli"},
                    ],
                }

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            council_config = root / "council.json"
            agent_config = root / "agents.json"
            live_agent_config = root / "live-agents.json"
            council_config.write_text(
                json.dumps(
                    {
                        "topic": "resident session",
                        "question": "Can a resident session run itself?",
                        "roles": [
                            {"id": "architect", "display_name": "Architect", "lens": "Architecture", "research_focus": "system"},
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            agent_config.write_text(
                json.dumps(
                    {
                        "providers": [{"id": "local-cli", "kind": "local_cli", "display_name": "Local CLI"}],
                        "permission_profiles": [{"id": "meeting_readonly", "meeting_read": True, "official_turn": True}],
                        "agent_bindings": [
                            {
                                "agent_id": "agent-a",
                                "role_id": "architect",
                                "provider_id": "local-cli",
                                "permission_profile_id": "meeting_readonly",
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
                        "agents": [
                            {
                                "agent_id": "agent-a",
                                "display_name": "Agent A",
                                "provider_kind": "local_cli",
                                "connection_kind": "local_cli",
                                "command": [sys.executable, "-c", "print('ok')"],
                            }
                        ]
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            supervisor = FakeSessionSupervisor(root)
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root, process_supervisor=supervisor))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            errors = []
            answered_request_ids = set()

            def answer_auto_round_request():
                try:
                    meeting_dir = root / "meetings" / "resident-m1"
                    deadline = time.time() + 4
                    while time.time() < deadline and not answered_request_ids:
                        if not meeting_dir.exists():
                            time.sleep(0.01)
                            continue
                        for event in read_live_events(meeting_dir, limit=None):
                            if event.get("kind") != "live_agent_turn_request":
                                continue
                            reply_request = Request(
                                f"http://127.0.0.1:{server.server_port}/api/live-agents/{event['target_agent_id']}/official-turn",
                                data=json.dumps(
                                    {
                                        "meeting_id": "resident-m1",
                                        "source_event_id": event["id"],
                                        "content": "auto round reply",
                                    }
                                ).encode("utf-8"),
                                headers={"Content-Type": "application/json"},
                                method="POST",
                            )
                            with urlopen(reply_request, timeout=4) as response:
                                response.read()
                            answered_request_ids.add(event["id"])
                            break
                        time.sleep(0.01)
                except Exception as error:  # pragma: no cover - failure is asserted below
                    errors.append(error)

            responder = threading.Thread(target=answer_auto_round_request, daemon=True)
            responder.start()
            try:
                request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/live-agent-sessions/start",
                    data=json.dumps(
                        {
                            "meeting_id": "resident-m1",
                            "group_id": "resident-main",
                            "council_config_path": str(council_config),
                            "agent_config_path": str(agent_config),
                            "live_agent_config_path": str(live_agent_config),
                            "connect_timeout_seconds": 0,
                            "run_remaining_rounds": True,
                            "round_timeout_seconds": 2,
                            "round_max_rounds": 1,
                            "round_stop_on_timeout": True,
                        }
                    ).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urlopen(request, timeout=7) as response:
                    session_payload = json.loads(response.read().decode("utf-8"))
                responder.join(timeout=2)
                with urlopen(f"http://127.0.0.1:{server.server_port}/api/live-agent-operations?limit=20", timeout=4) as response:
                    operations = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

            if errors:
                raise errors[0]
            self.assertEqual(session_payload["status"], "ready")
            self.assertEqual(session_payload["auto_rounds"]["status"], "answered")
            self.assertEqual(session_payload["auto_rounds"]["round_count"], 1)
            self.assertEqual(session_payload["auto_rounds"]["answered_round_count"], 1)
            self.assertEqual(session_payload["auto_rounds"]["results"][0]["round_id"], "round_1")
            meeting_dir = root / "meetings" / "resident-m1"
            live_state = json.loads((meeting_dir / "live_state.json").read_text(encoding="utf-8"))
            self.assertEqual(live_state["debate_rounds"][0]["status"], "answered")
            session_operations = [operation for operation in operations["operations"] if operation["operation"] == "session.start"]
            self.assertEqual(session_operations[-1]["status"], "success")
            self.assertEqual(session_operations[-1]["details"]["auto_rounds_status"], "answered")
            self.assertEqual(session_operations[-1]["details"]["auto_rounds_round_count"], 1)
            operation_blob = json.dumps(session_operations, ensure_ascii=False)
            self.assertNotIn(str(council_config), operation_blob)
            self.assertNotIn(str(agent_config), operation_blob)
            self.assertNotIn(str(live_agent_config), operation_blob)
            self.assertNotIn("auto round reply", operation_blob)

    def test_start_session_fake_three_agents_runs_remaining_rounds_finalizes_and_stop_marks_offline(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "room"
            root.mkdir()
            config_dir = Path(temp_dir) / "configs"
            config_dir.mkdir()
            council_config = config_dir / "council.json"
            agent_config = config_dir / "agents.json"
            live_agent_config = config_dir / "live-agents.json"
            _write_three_agent_fake_session_configs(council_config, agent_config, live_agent_config)
            supervisor = LiveAgentProcessSupervisor(root)
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root, process_supervisor=supervisor))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            meeting_id = "resident-fake-complete"
            group_id = "resident-fake-complete"
            stop_payload = {}
            try:
                start_request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/live-agent-sessions/start",
                    data=json.dumps(
                        {
                            "meeting_id": meeting_id,
                            "group_id": group_id,
                            "council_config_path": str(council_config),
                            "agent_config_path": str(agent_config),
                            "live_agent_config_path": str(live_agent_config),
                            "connect_timeout_seconds": 8,
                            "run_remaining_rounds": True,
                            "round_timeout_seconds": 6,
                            "round_max_rounds": 2,
                            "round_stop_on_timeout": True,
                            "finalize_after_rounds": True,
                        }
                    ).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urlopen(start_request, timeout=60) as response:
                    session_payload = json.loads(response.read().decode("utf-8"))
                stop_request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/live-agent-sessions/stop",
                    data=json.dumps({"meeting_id": meeting_id, "group_id": group_id}).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urlopen(stop_request, timeout=12) as response:
                    stop_payload = json.loads(response.read().decode("utf-8"))
            finally:
                if not stop_payload:
                    try:
                        cleanup_request = Request(
                            f"http://127.0.0.1:{server.server_port}/api/live-agent-sessions/stop",
                            data=json.dumps({"meeting_id": meeting_id, "group_id": group_id}).encode("utf-8"),
                            headers={"Content-Type": "application/json"},
                            method="POST",
                        )
                        with urlopen(cleanup_request, timeout=12) as response:
                            response.read()
                    except Exception:
                        pass
                supervisor.close()
                server.shutdown()
                server.server_close()

            self.assertEqual(session_payload["status"], "ready")
            self.assertEqual(session_payload["connection"]["expected"], 3)
            self.assertEqual(session_payload["connection"]["connected"], 3)
            self.assertEqual(session_payload["auto_rounds"]["status"], "answered")
            self.assertEqual(session_payload["auto_rounds"]["round_count"], 2)
            self.assertEqual(session_payload["auto_rounds"]["answered_round_count"], 2)
            self.assertEqual(session_payload["finalization"]["status"], "finalized")
            self.assertEqual(session_payload["finalization"]["official_event_count"], 6)
            self.assertEqual(session_payload["finalization"]["return_packet_event_count"], 3)

            meeting_dir = root / "meetings" / meeting_id
            for relative_path in ("agenda.md", "transcript.md", "decision.md", "meeting.json"):
                self.assertTrue((meeting_dir / relative_path).exists(), relative_path)
            shared_memory = json.loads((meeting_dir / "shared_memory" / "index.json").read_text(encoding="utf-8"))
            self.assertEqual(shared_memory["official_event_count"], 6)
            for role_id in ("architect", "critic", "operator"):
                self.assertTrue((meeting_dir / "return_packets" / f"{role_id}.md").exists())
                self.assertTrue((meeting_dir / "return_packets" / f"{role_id}.json").exists())
            return_packet_events = [
                event
                for event in read_live_events(meeting_dir, limit=None)
                if event.get("kind") == "artifact" and event.get("artifact_kind") == "return_packet"
            ]
            self.assertEqual({event.get("target_agent_id") for event in return_packet_events}, {"agent-a", "agent-b", "agent-c"})
            self.assertEqual({event.get("official_record") for event in return_packet_events}, {False})

            self.assertEqual(stop_payload["status"], "stopped")
            self.assertEqual(stop_payload["offline"]["expected"], 3)
            self.assertEqual(stop_payload["offline"]["offline"], 3)
            agents = {agent["agent_id"]: agent for agent in read_live_agents(root) if agent.get("meeting_id") == meeting_id}
            self.assertEqual({agents[agent_id]["status"] for agent_id in ("agent-a", "agent-b", "agent-c")}, {"offline"})

    def test_start_session_codex_live_fake_cli_preserves_sessions_through_remaining_rounds(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "room"
            root.mkdir()
            bin_dir = Path(temp_dir) / "bin"
            bin_dir.mkdir()
            fake_codex_log = Path(temp_dir) / "fake-codex.jsonl"
            _write_fake_codex_executable(bin_dir / "codex")
            supervisor = LiveAgentProcessSupervisor(root)
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root, process_supervisor=supervisor))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            meeting_id = "codex-live-fake-complete"
            group_id = "codex-live-fake-complete"
            restart_payload = {}
            stop_payload = {}
            env = {
                "PATH": f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}",
                "AGENTSASSEMBLE_FAKE_CODEX_LOG": str(fake_codex_log),
            }
            try:
                with patch.dict(os.environ, env, clear=False):
                    start_request = Request(
                        f"http://127.0.0.1:{server.server_port}/api/live-agent-sessions/start",
                        data=json.dumps(
                            {
                                "meeting_id": meeting_id,
                                "group_id": group_id,
                                "council_config_path": "configs/demo-council.json",
                                "agent_config_path": "configs/codex-live-session.example.json",
                                "live_agent_config_path": "configs/live-agents.codex-session.example.json",
                                "connect_timeout_seconds": 8,
                                "run_remaining_rounds": True,
                                "round_timeout_seconds": 12,
                                "round_max_rounds": 1,
                                "round_stop_on_timeout": True,
                            }
                        ).encode("utf-8"),
                        headers={"Content-Type": "application/json"},
                        method="POST",
                    )
                    with urlopen(start_request, timeout=80) as response:
                        session_payload = json.loads(response.read().decode("utf-8"))
                    restart_request = Request(
                        f"http://127.0.0.1:{server.server_port}/api/live-agent-sessions/restart",
                        data=json.dumps(
                            {
                                "meeting_id": meeting_id,
                                "group_id": group_id,
                                "connect_timeout_seconds": 8,
                                "run_remaining_rounds": True,
                                "round_timeout_seconds": 12,
                                "round_max_rounds": 2,
                                "round_stop_on_timeout": True,
                                "finalize_after_rounds": True,
                            }
                        ).encode("utf-8"),
                        headers={"Content-Type": "application/json"},
                        method="POST",
                    )
                    with urlopen(restart_request, timeout=80) as response:
                        restart_payload = json.loads(response.read().decode("utf-8"))
                    stop_request = Request(
                        f"http://127.0.0.1:{server.server_port}/api/live-agent-sessions/stop",
                        data=json.dumps({"meeting_id": meeting_id, "group_id": group_id}).encode("utf-8"),
                        headers={"Content-Type": "application/json"},
                        method="POST",
                    )
                    with urlopen(stop_request, timeout=12) as response:
                        stop_payload = json.loads(response.read().decode("utf-8"))
            finally:
                if not stop_payload:
                    try:
                        cleanup_request = Request(
                            f"http://127.0.0.1:{server.server_port}/api/live-agent-sessions/stop",
                            data=json.dumps({"meeting_id": meeting_id, "group_id": group_id}).encode("utf-8"),
                            headers={"Content-Type": "application/json"},
                            method="POST",
                        )
                        with urlopen(cleanup_request, timeout=12) as response:
                            response.read()
                    except Exception:
                        pass
                supervisor.close()
                server.shutdown()
                server.server_close()

            self.assertEqual(session_payload["status"], "ready")
            self.assertEqual(session_payload["connection"]["expected"], 3)
            self.assertEqual(session_payload["connection"]["connected"], 3)
            self.assertEqual(session_payload["auto_rounds"]["status"], "answered")
            self.assertEqual(session_payload["auto_rounds"]["round_count"], 1)
            self.assertEqual(session_payload["auto_rounds"]["answered_round_count"], 1)
            self.assertEqual(restart_payload["status"], "ready")
            self.assertEqual(restart_payload["auto_rounds"]["status"], "answered")
            self.assertEqual(restart_payload["auto_rounds"]["round_count"], 1)
            self.assertEqual(restart_payload["auto_rounds"]["answered_round_count"], 1)
            self.assertEqual(restart_payload["finalization"]["status"], "finalized")
            self.assertEqual(restart_payload["finalization"]["official_event_count"], 6)

            invocations = [
                json.loads(line)
                for line in fake_codex_log.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            run_invocations = [entry for entry in invocations if entry["mode"] in {"fresh", "resume"}]
            self.assertEqual(len(run_invocations), 6)
            self.assertEqual([entry["mode"] for entry in run_invocations].count("fresh"), 3)
            self.assertEqual([entry["mode"] for entry in run_invocations].count("resume"), 3)
            self.assertEqual(
                {entry["session_id"] for entry in run_invocations if entry["mode"] == "resume"},
                {
                    "019e0000-0000-7000-a000-000000000001",
                    "019e0000-0000-7000-a000-000000000002",
                    "019e0000-0000-7000-a000-000000000003",
                },
            )
            self.assertTrue(all(entry["sandbox_flags"] == ["--sandbox", "read-only", "--ignore-rules"] for entry in run_invocations))

            agents = {agent["agent_id"]: agent for agent in read_live_agents(root) if agent.get("meeting_id") == meeting_id}
            self.assertEqual(
                {agents[agent_id]["session_id"] for agent_id in ("codex-live-lore", "codex-live-feats", "codex-live-skeptic")},
                {
                    "019e0000-0000-7000-a000-000000000001",
                    "019e0000-0000-7000-a000-000000000002",
                    "019e0000-0000-7000-a000-000000000003",
                },
            )
            self.assertEqual({agents[agent_id]["status"] for agent_id in agents}, {"offline"})
            self.assertEqual(stop_payload["status"], "stopped")
            self.assertEqual(stop_payload["offline"]["offline"], 3)

    def test_live_agent_session_start_probe_failure_skips_auto_rounds_and_records_safe_operation(self):
        class FakeSessionSupervisor:
            def __init__(self, output_root: Path) -> None:
                self.output_root = output_root

            def start_group(self, **kwargs):
                heartbeat_live_agent(self.output_root, "agent-a", status="online")
                return {
                    "group_id": kwargs.get("group_id") or "resident-main",
                    "status": "running",
                    "agents": [
                        {"agent_id": "agent-a", "display_name": "Agent A", "provider_kind": "local_cli", "connection_kind": "local_cli"},
                    ],
                }

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            council_config = root / "council.json"
            agent_config = root / "agents.json"
            live_agent_config = root / "live-agents.json"
            _write_single_agent_session_configs(council_config, agent_config, live_agent_config)
            supervisor = FakeSessionSupervisor(root)
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root, process_supervisor=supervisor))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                probe_result = {
                    "status": "timeout",
                    "agent_id": "agent-a",
                    "reason": "agent did not reply",
                    "source_event_id": "probe-event-1",
                    "reply": {"message": "probe reply should not be recorded"},
                }
                with (
                    patch("agentsassemble.gui.run_live_agent_probe", return_value=probe_result) as run_probe,
                    patch("agentsassemble.gui.live_agent_turn_rounds_payload") as rounds_payload,
                ):
                    request = Request(
                        f"http://127.0.0.1:{server.server_port}/api/live-agent-sessions/start",
                        data=json.dumps(
                            {
                                "meeting_id": "resident-m1",
                                "group_id": "resident-main",
                                "council_config_path": str(council_config),
                                "agent_config_path": str(agent_config),
                                "live_agent_config_path": str(live_agent_config),
                                "connect_timeout_seconds": 0,
                                "probe_bound_agents": True,
                                "probe_timeout_seconds": 0.5,
                                "run_remaining_rounds": True,
                                "round_timeout_seconds": 2,
                                "round_max_rounds": 1,
                                "round_stop_on_timeout": True,
                            }
                        ).encode("utf-8"),
                        headers={"Content-Type": "application/json"},
                        method="POST",
                    )
                    with urlopen(request, timeout=4) as response:
                        session_payload = json.loads(response.read().decode("utf-8"))
                with urlopen(f"http://127.0.0.1:{server.server_port}/api/live-agent-operations?limit=20", timeout=4) as response:
                    operations = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

        run_probe.assert_called_once_with(root, "agent-a", timeout_seconds=0.5)
        rounds_payload.assert_not_called()
        self.assertEqual(session_payload["status"], "ready")
        self.assertEqual(session_payload["reply_probe"]["status"], "failed")
        self.assertEqual(session_payload["reply_probe"]["ok_count"], 0)
        self.assertEqual(session_payload["reply_probe"]["timeout_count"], 1)
        self.assertEqual(session_payload["reply_probe"]["probes"][0]["status"], "timeout")
        self.assertEqual(session_payload["auto_rounds"]["status"], "skipped")
        self.assertEqual(session_payload["auto_rounds"]["reason"], "probe_not_ready")
        session_operations = [operation for operation in operations["operations"] if operation["operation"] == "session.start"]
        self.assertEqual(session_operations[-1]["status"], "degraded")
        self.assertEqual(session_operations[-1]["details"]["reply_probe_status"], "failed")
        self.assertEqual(session_operations[-1]["details"]["reply_probe_statuses"], ["agent-a:timeout"])
        self.assertEqual(session_operations[-1]["details"]["auto_rounds_reason"], "probe_not_ready")
        operation_blob = json.dumps(session_operations, ensure_ascii=False)
        self.assertNotIn(str(council_config), operation_blob)
        self.assertNotIn(str(agent_config), operation_blob)
        self.assertNotIn(str(live_agent_config), operation_blob)
        self.assertNotIn("probe reply should not be recorded", operation_blob)

    def test_session_bound_agent_probe_temporarily_opens_moderator_called_agent_and_restores_mode(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            connect_live_agent_payload(
                root,
                {
                    "agent_id": "agent-a",
                    "display_name": "Agent A",
                    "provider_kind": "local_cli",
                    "connection_kind": "local_cli",
                    "engagement_mode": "moderator_called",
                },
            )
            observed_modes = []

            def fake_probe(output_root: Path, agent_id: str, *, timeout_seconds: float):
                agent = next(item for item in read_live_agents(output_root) if item.get("agent_id") == agent_id)
                observed_modes.append(agent.get("engagement_mode"))
                return {"status": "ok", "agent_id": agent_id, "source_event_id": "probe-event-1", "reply_event_id": "reply-1"}

            with patch("agentsassemble.gui.run_live_agent_probe", side_effect=fake_probe) as run_probe:
                result = _run_session_bound_agent_probe(root, "agent-a", timeout_seconds=0.5)

            agent = next(item for item in read_live_agents(root) if item.get("agent_id") == "agent-a")
            persisted_agent = json.loads((root / "live_agents.json").read_text(encoding="utf-8"))["agents"][0]
            self.assertEqual(result["status"], "ok")
            self.assertEqual(observed_modes, ["human_only"])
            self.assertEqual(agent["engagement_mode"], "moderator_called")
            self.assertNotIn("engagement_mode_updated_at", persisted_agent)
            run_probe.assert_called_once_with(root, "agent-a", timeout_seconds=0.5)

    def test_live_agent_session_start_skips_auto_rounds_until_session_ready(self):
        class SlowSessionSupervisor:
            def start_group(self, **kwargs):
                return {
                    "group_id": kwargs.get("group_id") or "resident-main",
                    "status": "running",
                    "agents": [
                        {"agent_id": "agent-a", "display_name": "Agent A", "provider_kind": "local_cli", "connection_kind": "local_cli"},
                    ],
                }

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            council_config = root / "council.json"
            agent_config = root / "agents.json"
            live_agent_config = root / "live-agents.json"
            council_config.write_text(
                json.dumps(
                    {
                        "topic": "resident session",
                        "question": "Should slow sessions skip auto rounds?",
                        "roles": [
                            {"id": "architect", "display_name": "Architect", "lens": "Architecture", "research_focus": "system"},
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            agent_config.write_text(
                json.dumps(
                    {
                        "providers": [{"id": "local-cli", "kind": "local_cli", "display_name": "Local CLI"}],
                        "permission_profiles": [{"id": "meeting_readonly", "meeting_read": True, "official_turn": True}],
                        "agent_bindings": [
                            {
                                "agent_id": "agent-a",
                                "role_id": "architect",
                                "provider_id": "local-cli",
                                "permission_profile_id": "meeting_readonly",
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
                        "agents": [
                            {
                                "agent_id": "agent-a",
                                "display_name": "Agent A",
                                "provider_kind": "local_cli",
                                "connection_kind": "local_cli",
                                "command": [sys.executable, "-c", "print('ok')"],
                            }
                        ]
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root, process_supervisor=SlowSessionSupervisor()))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/live-agent-sessions/start",
                    data=json.dumps(
                        {
                            "meeting_id": "resident-m1",
                            "group_id": "resident-main",
                            "council_config_path": str(council_config),
                            "agent_config_path": str(agent_config),
                            "live_agent_config_path": str(live_agent_config),
                            "connect_timeout_seconds": 0,
                            "run_remaining_rounds": True,
                            "round_timeout_seconds": 0,
                            "round_max_rounds": 1,
                        }
                    ).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urlopen(request, timeout=4) as response:
                    session_payload = json.loads(response.read().decode("utf-8"))
                with urlopen(f"http://127.0.0.1:{server.server_port}/api/live-agent-operations?limit=20", timeout=4) as response:
                    operations = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

            meeting_dir = root / "meetings" / "resident-m1"
            self.assertEqual(session_payload["status"], "starting")
            self.assertEqual(session_payload["auto_rounds"]["status"], "skipped")
            self.assertEqual(session_payload["auto_rounds"]["reason"], "session_not_ready")
            request_events = [event for event in read_live_events(meeting_dir, limit=None) if event.get("kind") == "live_agent_turn_request"]
            self.assertEqual(request_events, [])
            session_operations = [operation for operation in operations["operations"] if operation["operation"] == "session.start"]
            self.assertEqual(session_operations[-1]["status"], "degraded")
            self.assertEqual(session_operations[-1]["details"]["auto_rounds_status"], "skipped")

    def test_live_agent_session_start_redacts_config_load_paths_from_error_response(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            live_agent_config = root / "live-agents.json"
            private_council_config = root / "private-council.json"
            live_agent_config.write_text(
                json.dumps(
                    {
                        "agents": [
                            {
                                "agent_id": "agent-a",
                                "display_name": "Agent A",
                                "provider_kind": "local_cli",
                                "connection_kind": "local_cli",
                                "command": [sys.executable, "-c", "print('ok')"],
                            }
                        ]
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            supervisor = LiveAgentProcessSupervisor(root, command_factory=lambda *args, **kwargs: None)
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root, process_supervisor=supervisor))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/live-agent-sessions/start",
                    data=json.dumps(
                        {
                            "meeting_id": "resident-m1",
                            "council_config_path": str(private_council_config),
                            "live_agent_config_path": str(live_agent_config),
                        }
                    ).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with self.assertRaises(HTTPError) as raised:
                    urlopen(request, timeout=4)
                body = raised.exception.read().decode("utf-8")
                error_payload = json.loads(body)
                raised.exception.close()
            finally:
                server.shutdown()
                server.server_close()

            self.assertNotIn(str(private_council_config), body)
            self.assertNotIn("private-council", body)
            self.assertIn("details redacted", body)
            self.assertNotIn("meeting_id", error_payload)
            self.assertEqual(error_payload["details"]["requested_meeting_id"], "resident-m1")

    def test_live_agent_session_start_group_failure_returns_created_meeting_for_recovery(self):
        class FailingSessionSupervisor:
            def start_group(self, **kwargs):
                raise RuntimeError("process launch refused")

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            council_config = root / "council.json"
            agent_config = root / "agents.json"
            live_agent_config = root / "live-agents.json"
            council_config.write_text(
                json.dumps(
                    {
                        "topic": "resident session",
                        "question": "Can a failed process start leave recoverable meeting evidence?",
                        "roles": [
                            {"id": "architect", "display_name": "Architect", "lens": "Architecture", "research_focus": "system"}
                        ],
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
                                "id": "local-cli",
                                "kind": "local_cli",
                                "display_name": "Local CLI",
                                "command": [sys.executable, "-c", "print('ok')"],
                            }
                        ],
                        "permission_profiles": [
                            {"id": "meeting_readonly", "meeting_read": True, "lobby_chat": True, "official_turn": True}
                        ],
                        "agent_bindings": [
                            {
                                "agent_id": "agent-a",
                                "role_id": "architect",
                                "provider_id": "local-cli",
                                "permission_profile_id": "meeting_readonly",
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
                        "agents": [
                            {
                                "agent_id": "agent-a",
                                "display_name": "Agent A",
                                "provider_kind": "local_cli",
                                "connection_kind": "local_cli",
                                "command": [sys.executable, "-c", "print('ok')"],
                            }
                        ]
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root, process_supervisor=FailingSessionSupervisor()))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/live-agent-sessions/start",
                    data=json.dumps(
                        {
                            "group_id": "resident-main",
                            "council_config_path": str(council_config),
                            "agent_config_path": str(agent_config),
                            "live_agent_config_path": str(live_agent_config),
                            "connect_timeout_seconds": 0,
                        }
                    ).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with self.assertRaises(HTTPError) as raised:
                    urlopen(request, timeout=4)
                error_payload = json.loads(raised.exception.read().decode("utf-8"))
                raised.exception.close()
                with urlopen(f"http://127.0.0.1:{server.server_port}/api/live-agent-operations?limit=20", timeout=4) as response:
                    operations = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

            meeting_id = error_payload["meeting_id"]
            self.assertEqual(raised.exception.code, 400)
            self.assertTrue(meeting_id)
            self.assertEqual(error_payload["details"]["meeting_id"], meeting_id)
            self.assertEqual(error_payload["details"]["recoverable_meeting_id"], meeting_id)
            self.assertTrue((root / "meetings" / meeting_id / "live_state.json").exists())
            session_operations = [operation for operation in operations["operations"] if operation["operation"] == "session.start"]
            self.assertEqual(session_operations[-1]["status"], "failed")
            self.assertEqual(session_operations[-1]["target_id"], meeting_id)
            self.assertEqual(session_operations[-1]["details"]["meeting_id"], meeting_id)

    def test_live_agent_session_resume_existing_meeting_records_safe_operation(self):
        class RunningSessionSupervisor:
            def __init__(self) -> None:
                self.started = []

            def list_groups(self):
                return [
                    {
                        "group_id": "resident-main",
                        "status": "running",
                        "agents": [{"agent_id": "agent-a", "provider_kind": "local_cli", "connection_kind": "local_cli"}],
                    }
                ]

            def start_group(self, **kwargs):
                self.started.append(kwargs)
                raise AssertionError("resume should reuse the already running group")

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            council_config = root / "council.json"
            agent_config = root / "agents.json"
            live_agent_config = root / "live-agents.json"
            _write_single_agent_session_configs(council_config, agent_config, live_agent_config)
            start_live_agent_meeting(
                root,
                council_config_path=council_config,
                agent_config_path=agent_config,
                meeting_id="resident-m1",
            )
            heartbeat_live_agent(root, "agent-a", status="online")
            supervisor = RunningSessionSupervisor()
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root, process_supervisor=supervisor))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/live-agent-sessions/resume",
                    data=json.dumps(
                        {
                            "meeting_id": "resident-m1",
                            "group_id": "resident-main",
                            "live_agent_config_path": str(live_agent_config),
                            "connect_timeout_seconds": 0,
                        }
                    ).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urlopen(request, timeout=4) as response:
                    session_payload = json.loads(response.read().decode("utf-8"))
                with urlopen(f"http://127.0.0.1:{server.server_port}/api/live-agent-operations?limit=20", timeout=4) as response:
                    operations = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

            self.assertEqual(session_payload["status"], "ready")
            self.assertEqual(session_payload["meeting_id"], "resident-m1")
            self.assertEqual(session_payload["group_id"], "resident-main")
            self.assertEqual(session_payload["connection"]["connected"], 1)
            self.assertEqual(supervisor.started, [])
            session_operations = [operation for operation in operations["operations"] if operation["operation"] == "session.resume"]
            self.assertEqual(session_operations[-1]["status"], "success")
            self.assertEqual(session_operations[-1]["target_id"], "resident-m1")
            self.assertEqual(session_operations[-1]["details"]["result_status"], "ready")
            operation_blob = json.dumps(session_operations, ensure_ascii=False)
            self.assertNotIn(str(live_agent_config), operation_blob)

    def test_live_agent_session_resume_skips_auto_rounds_until_session_ready(self):
        class StartingSessionSupervisor:
            def list_groups(self):
                return []

            def start_group(self, **kwargs):
                return {
                    "group_id": kwargs.get("group_id") or "resident-main",
                    "status": "running",
                    "agents": [{"agent_id": "agent-a", "provider_kind": "local_cli", "connection_kind": "local_cli"}],
                }

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            council_config = root / "council.json"
            agent_config = root / "agents.json"
            live_agent_config = root / "live-agents.json"
            _write_single_agent_session_configs(council_config, agent_config, live_agent_config)
            start_live_agent_meeting(
                root,
                council_config_path=council_config,
                agent_config_path=agent_config,
                meeting_id="resident-m1",
            )
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root, process_supervisor=StartingSessionSupervisor()))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/live-agent-sessions/resume",
                    data=json.dumps(
                        {
                            "meeting_id": "resident-m1",
                            "group_id": "resident-main",
                            "live_agent_config_path": str(live_agent_config),
                            "connect_timeout_seconds": 0,
                            "run_remaining_rounds": True,
                            "round_timeout_seconds": 0,
                            "round_max_rounds": 1,
                        }
                    ).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urlopen(request, timeout=4) as response:
                    session_payload = json.loads(response.read().decode("utf-8"))
                with urlopen(f"http://127.0.0.1:{server.server_port}/api/live-agent-operations?limit=20", timeout=4) as response:
                    operations = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

            self.assertEqual(session_payload["status"], "starting")
            self.assertEqual(session_payload["auto_rounds"]["status"], "skipped")
            self.assertEqual(session_payload["auto_rounds"]["reason"], "session_not_ready")
            request_events = [
                event
                for event in read_live_events(root / "meetings" / "resident-m1", limit=None)
                if event.get("kind") == "live_agent_turn_request"
            ]
            self.assertEqual(request_events, [])
            session_operations = [operation for operation in operations["operations"] if operation["operation"] == "session.resume"]
            self.assertEqual(session_operations[-1]["status"], "degraded")
            self.assertEqual(session_operations[-1]["details"]["auto_rounds_status"], "skipped")

    def test_live_agent_session_resume_missing_meeting_returns_safe_error(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            live_agent_config = root / "live-agents.json"
            live_agent_config.write_text(
                json.dumps(
                    {
                        "agents": [
                            {
                                "agent_id": "agent-a",
                                "display_name": "Agent A",
                                "provider_kind": "local_cli",
                                "connection_kind": "local_cli",
                                "command": [sys.executable, "-c", "print('ok')"],
                            }
                        ]
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root, process_supervisor=LiveAgentProcessSupervisor(root)))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/live-agent-sessions/resume",
                    data=json.dumps(
                        {
                            "meeting_id": "missing-meeting",
                            "group_id": "resident-main",
                            "live_agent_config_path": str(live_agent_config),
                        }
                    ).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with self.assertRaises(HTTPError) as raised:
                    urlopen(request, timeout=4)
                body = raised.exception.read().decode("utf-8")
                error_payload = json.loads(body)
                raised.exception.close()
            finally:
                server.shutdown()
                server.server_close()

            self.assertIn("Meeting missing-meeting was not found", body)
            self.assertNotIn(str(live_agent_config), body)
            self.assertEqual(error_payload["details"]["requested_meeting_id"], "missing-meeting")

    def test_live_agent_session_resume_redacts_command_names_from_error_response(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            council_config = root / "council.json"
            agent_config = root / "agents.json"
            live_agent_config = root / "live-agents.json"
            _write_single_agent_session_configs(council_config, agent_config, live_agent_config)
            live_agent_config.write_text(
                json.dumps(
                    {
                        "agents": [
                            {
                                "agent_id": "agent-a",
                                "display_name": "Agent A",
                                "provider_kind": "local_cli",
                                "connection_kind": "local_cli",
                                "command": ["secret-local-agent-cli"],
                            }
                        ]
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            start_live_agent_meeting(
                root,
                council_config_path=council_config,
                agent_config_path=agent_config,
                meeting_id="resident-m1",
            )
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root, process_supervisor=LiveAgentProcessSupervisor(root)))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/live-agent-sessions/resume",
                    data=json.dumps(
                        {
                            "meeting_id": "resident-m1",
                            "group_id": "resident-main",
                            "live_agent_config_path": str(live_agent_config),
                        }
                    ).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with self.assertRaises(HTTPError) as raised:
                    urlopen(request, timeout=4)
                body = raised.exception.read().decode("utf-8")
                error_payload = json.loads(body)
                raised.exception.close()
            finally:
                server.shutdown()
                server.server_close()

            self.assertIn("details redacted", body)
            self.assertNotIn("secret-local-agent-cli", body)
            self.assertNotIn(str(live_agent_config), body)
            self.assertEqual(error_payload["details"]["requested_meeting_id"], "resident-m1")

    def test_live_agent_session_stop_marks_agents_offline_and_records_safe_operation(self):
        class StopSessionSupervisor:
            def __init__(self) -> None:
                self.stopped = []

            def stop_group(self, group_id):
                self.stopped.append(group_id)
                return {
                    "group_id": group_id,
                    "status": "stopped",
                    "config_path": "/private/live-agents.json",
                    "log_tail": "secret provider output",
                    "agents": [{"agent_id": "agent-a"}],
                }

            def list_groups(self):
                return [{"group_id": "resident-main", "status": "stopped", "agents": [{"agent_id": "agent-a"}]}]

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            council_config = root / "council.json"
            agent_config = root / "agents.json"
            live_agent_config = root / "live-agents.json"
            _write_single_agent_session_configs(council_config, agent_config, live_agent_config)
            start_live_agent_meeting(
                root,
                council_config_path=council_config,
                agent_config_path=agent_config,
                meeting_id="resident-m1",
            )
            heartbeat_live_agent(root, "agent-a", status="online")
            supervisor = StopSessionSupervisor()
            session_run_controller = LiveAgentSessionRunController(root)
            session_run = session_run_controller.begin_run(
                action="ensure",
                payload={
                    "meeting_id": "resident-m1",
                    "group_id": "resident-main",
                    "live_agent_config_path": str(live_agent_config),
                },
            )
            session_run_controller.finish_run(
                session_run["run_id"],
                session={
                    "status": "ready",
                    "meeting_id": "resident-m1",
                    "group_id": "resident-main",
                    "action": "none",
                },
            )
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root, process_supervisor=supervisor))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/live-agent-sessions/stop",
                    data=json.dumps({"meeting_id": "resident-m1", "group_id": "resident main"}).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urlopen(request, timeout=4) as response:
                    session_payload = json.loads(response.read().decode("utf-8"))
                with urlopen(f"http://127.0.0.1:{server.server_port}/api/live-agent-operations?limit=20", timeout=4) as response:
                    operations = json.loads(response.read().decode("utf-8"))
                with urlopen(f"http://127.0.0.1:{server.server_port}/api/live-agent-session-runs?limit=20", timeout=4) as response:
                    session_runs = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

            self.assertEqual(session_payload["status"], "stopped")
            self.assertEqual(session_payload["group_id"], "resident-main")
            self.assertEqual(session_payload["session_runs"][0]["run_id"], session_run["run_id"])
            self.assertEqual(session_payload["session_runs"][0]["status"], "stopped")
            self.assertFalse(session_runs["runs"][0]["active"])
            self.assertEqual(supervisor.stopped, ["resident-main"])
            agents = {agent["agent_id"]: agent for agent in read_live_agents(root)}
            self.assertEqual(agents["agent-a"]["status"], "offline")
            session_operations = [operation for operation in operations["operations"] if operation["operation"] == "session.stop"]
            self.assertEqual(session_operations[-1]["status"], "success")
            self.assertEqual(session_operations[-1]["target_id"], "resident-m1")
            self.assertEqual(session_operations[-1]["details"]["offline_agent_count"], 1)
            self.assertEqual(session_operations[-1]["details"]["session_run_stopped_count"], 1)
            self.assertEqual(session_operations[-1]["details"]["session_run_ids"], [session_run["run_id"]])
            operation_blob = json.dumps(session_operations, ensure_ascii=False)
            self.assertNotIn("/private/live-agents.json", operation_blob)
            self.assertNotIn("secret provider output", operation_blob)

    def test_live_agent_session_stop_missing_meeting_returns_safe_error(self):
        class StopSessionSupervisor:
            def __init__(self) -> None:
                self.stopped = []

            def stop_group(self, group_id):
                self.stopped.append(group_id)
                return {"group_id": group_id, "status": "stopped", "agents": []}

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            supervisor = StopSessionSupervisor()
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root, process_supervisor=supervisor))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/live-agent-sessions/stop",
                    data=json.dumps({"meeting_id": "missing-meeting", "group_id": "resident-main"}).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with self.assertRaises(HTTPError) as raised:
                    urlopen(request, timeout=4)
                body = raised.exception.read().decode("utf-8")
                error_payload = json.loads(body)
                raised.exception.close()
            finally:
                server.shutdown()
                server.server_close()

            self.assertIn("Meeting missing-meeting was not found", body)
            self.assertEqual(error_payload["details"]["requested_meeting_id"], "missing-meeting")
            self.assertEqual(error_payload["details"]["group_id"], "resident-main")
            self.assertEqual(supervisor.stopped, [])

    def test_live_agent_session_stop_failure_does_not_offline_agents_and_records_safe_operation(self):
        class StopSessionSupervisor:
            def __init__(self) -> None:
                self.stopped = []

            def stop_group(self, group_id):
                self.stopped.append(group_id)
                raise ValueError("provider output: raw model reply should never leak")

            def list_groups(self):
                return [{"group_id": "resident-main", "status": "running", "agents": [{"agent_id": "agent-a"}]}]

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            council_config = root / "council.json"
            agent_config = root / "agents.json"
            live_agent_config = root / "live-agents.json"
            _write_single_agent_session_configs(council_config, agent_config, live_agent_config)
            start_live_agent_meeting(
                root,
                council_config_path=council_config,
                agent_config_path=agent_config,
                meeting_id="resident-m1",
            )
            heartbeat_live_agent(root, "agent-a", status="online")
            supervisor = StopSessionSupervisor()
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root, process_supervisor=supervisor))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/live-agent-sessions/stop",
                    data=json.dumps({"meeting_id": "resident-m1", "group_id": "resident-main"}).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with self.assertRaises(HTTPError) as raised:
                    urlopen(request, timeout=4)
                body = raised.exception.read().decode("utf-8")
                raised.exception.close()
                with urlopen(f"http://127.0.0.1:{server.server_port}/api/live-agent-operations?limit=20", timeout=4) as response:
                    operations = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

            self.assertEqual(supervisor.stopped, ["resident-main"])
            agents = {agent["agent_id"]: agent for agent in read_live_agents(root)}
            self.assertEqual(agents["agent-a"]["status"], "online")
            self.assertIn("details redacted", body)
            self.assertNotIn("raw model reply", body)
            self.assertNotIn("provider output", body)
            session_operations = [operation for operation in operations["operations"] if operation["operation"] == "session.stop"]
            self.assertEqual(session_operations[-1]["status"], "failed")
            self.assertEqual(session_operations[-1]["details"]["meeting_id"], "resident-m1")
            operation_blob = json.dumps(session_operations, ensure_ascii=False)
            self.assertNotIn("raw model reply", operation_blob)
            self.assertNotIn("provider output", operation_blob)

    def test_live_agent_session_check_returns_ready_snapshot_and_records_safe_operation(self):
        class CheckSessionSupervisor:
            def snapshot_groups(self):
                return [
                    {
                        "group_id": "resident-main",
                        "status": "running",
                        "config_path": "/private/live-agents.json",
                        "log_tail": "secret provider output",
                        "agents": [{"agent_id": "agent-a"}],
                    }
                ]

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            council_config = root / "council.json"
            agent_config = root / "agents.json"
            live_agent_config = root / "live-agents.json"
            _write_single_agent_session_configs(council_config, agent_config, live_agent_config)
            start_live_agent_meeting(
                root,
                council_config_path=council_config,
                agent_config_path=agent_config,
                meeting_id="resident-m1",
            )
            heartbeat_live_agent(root, "agent-a", status="online")
            before_roster = (root / "live_agents.json").read_text(encoding="utf-8")
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root, process_supervisor=CheckSessionSupervisor()))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/live-agent-sessions/check",
                    data=json.dumps({"meeting_id": "resident-m1", "group_id": "resident main"}).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urlopen(request, timeout=4) as response:
                    session_payload = json.loads(response.read().decode("utf-8"))
                with urlopen(f"http://127.0.0.1:{server.server_port}/api/live-agent-operations?limit=20", timeout=4) as response:
                    operations = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

            self.assertEqual(session_payload["status"], "ready")
            self.assertEqual(session_payload["group_id"], "resident-main")
            self.assertEqual(session_payload["connection"]["connected"], 1)
            self.assertEqual((root / "live_agents.json").read_text(encoding="utf-8"), before_roster)
            session_operations = [operation for operation in operations["operations"] if operation["operation"] == "session.check"]
            self.assertEqual(session_operations[-1]["status"], "success")
            self.assertEqual(session_operations[-1]["target_id"], "resident-m1")
            self.assertEqual(session_operations[-1]["details"]["connected_agent_count"], 1)
            operation_blob = json.dumps(session_operations, ensure_ascii=False)
            self.assertNotIn("/private/live-agents.json", operation_blob)
            self.assertNotIn("secret provider output", operation_blob)

    def test_live_agent_session_check_missing_meeting_returns_safe_error(self):
        class CheckSessionSupervisor:
            def list_groups(self):
                return [{"group_id": "resident-main", "status": "running", "agents": [{"agent_id": "agent-a"}]}]

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root, process_supervisor=CheckSessionSupervisor()))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/live-agent-sessions/check",
                    data=json.dumps({"meeting_id": "missing-meeting", "group_id": "resident-main"}).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with self.assertRaises(HTTPError) as raised:
                    urlopen(request, timeout=4)
                body = raised.exception.read().decode("utf-8")
                error_payload = json.loads(body)
                raised.exception.close()
                with urlopen(f"http://127.0.0.1:{server.server_port}/api/live-agent-operations?limit=20", timeout=4) as response:
                    operations = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

            self.assertIn("Meeting missing-meeting was not found", body)
            self.assertEqual(error_payload["details"]["requested_meeting_id"], "missing-meeting")
            self.assertEqual(error_payload["details"]["group_id"], "resident-main")
            session_operations = [operation for operation in operations["operations"] if operation["operation"] == "session.check"]
            self.assertEqual(session_operations[-1]["status"], "failed")

    def test_live_agent_session_readiness_endpoint_returns_ready_snapshot_without_operation_record(self):
        class ReadinessSessionSupervisor:
            def snapshot_groups(self):
                return [
                    {
                        "group_id": "resident-main",
                        "status": "running",
                        "config_path": "/private/live-agents.json",
                        "log_tail": "secret provider output",
                        "agents": [{"agent_id": "agent-a"}],
                    }
                ]

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            council_config = root / "council.json"
            agent_config = root / "agents.json"
            live_agent_config = root / "live-agents.json"
            _write_single_agent_session_configs(council_config, agent_config, live_agent_config)
            start_live_agent_meeting(
                root,
                council_config_path=council_config,
                agent_config_path=agent_config,
                meeting_id="resident-m1",
            )
            heartbeat_live_agent(root, "agent-a", status="online")
            before_roster = (root / "live_agents.json").read_text(encoding="utf-8")
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root, process_supervisor=ReadinessSessionSupervisor()))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with urlopen(
                    f"http://127.0.0.1:{server.server_port}/api/live-agent-sessions/readiness?meeting_id=resident-m1&group_id=resident%20main",
                    timeout=4,
                ) as response:
                    session_payload = json.loads(response.read().decode("utf-8"))
                with urlopen(f"http://127.0.0.1:{server.server_port}/api/live-agent-operations?limit=20", timeout=4) as response:
                    operations = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

            self.assertEqual(session_payload["status"], "ready")
            self.assertEqual(session_payload["group_id"], "resident-main")
            self.assertEqual(session_payload["connection"]["connected"], 1)
            self.assertEqual((root / "live_agents.json").read_text(encoding="utf-8"), before_roster)
            self.assertEqual(operations["operations"], [])
            payload_blob = json.dumps(session_payload, ensure_ascii=False)
            self.assertNotIn("/private/live-agents.json", payload_blob)
            self.assertNotIn("secret provider output", payload_blob)

    def test_live_agent_session_readiness_degrades_when_binding_provider_is_missing(self):
        class ReadinessSessionSupervisor:
            def snapshot_groups(self):
                return [
                    {
                        "group_id": "resident-main",
                        "status": "running",
                        "meeting_id": "resident-m1",
                        "agents": [
                            {
                                "agent_id": "agent-a",
                                "provider_kind": "local_cli",
                                "connection_kind": "local_cli",
                            }
                        ],
                    }
                ]

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            meeting_dir = root / "meetings" / "resident-m1"
            meeting_dir.mkdir(parents=True)
            write_live_state(
                meeting_dir,
                {
                    "meeting_id": "resident-m1",
                    "agent_bindings": [
                        {
                            "role_id": "architect",
                            "agent_id": "agent-a",
                            "provider_id": "missing-provider",
                        }
                    ],
                    "provider_configs": {},
                },
            )
            connect_live_agent(
                root,
                {
                    "agent_id": "agent-a",
                    "display_name": "Agent A",
                    "provider_kind": "local_cli",
                    "connection_kind": "local_cli",
                    "meeting_id": "resident-m1",
                },
            )
            server = ThreadingHTTPServer(
                ("127.0.0.1", 0),
                _make_handler(root, process_supervisor=ReadinessSessionSupervisor()),
            )
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with urlopen(
                    f"http://127.0.0.1:{server.server_port}/api/live-agent-sessions/readiness?meeting_id=resident-m1&group_id=resident-main",
                    timeout=4,
                ) as response:
                    session_payload = json.loads(response.read().decode("utf-8"))
                with urlopen(f"http://127.0.0.1:{server.server_port}/api/live-agent-operations?limit=20", timeout=4) as response:
                    operations = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

        self.assertEqual(session_payload["status"], "degraded")
        self.assertEqual(session_payload["connection"]["connected"], 0)
        self.assertEqual(session_payload["connection"]["attention"], ["agent-a:binding_provider_missing"])
        self.assertEqual(operations["operations"], [])
        payload_blob = json.dumps(session_payload, ensure_ascii=False)
        self.assertNotIn("missing-provider", payload_blob)

    def test_live_agent_session_readiness_endpoint_returns_degraded_missing_group_without_operation_record(self):
        class ReadinessSessionSupervisor:
            def snapshot_groups(self):
                return []

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            council_config = root / "council.json"
            agent_config = root / "agents.json"
            live_agent_config = root / "live-agents.json"
            _write_single_agent_session_configs(council_config, agent_config, live_agent_config)
            start_live_agent_meeting(
                root,
                council_config_path=council_config,
                agent_config_path=agent_config,
                meeting_id="resident-m1",
            )
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root, process_supervisor=ReadinessSessionSupervisor()))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with urlopen(
                    f"http://127.0.0.1:{server.server_port}/api/live-agent-sessions/readiness?meeting_id=resident-m1&group_id=resident-main",
                    timeout=4,
                ) as response:
                    session_payload = json.loads(response.read().decode("utf-8"))
                with urlopen(f"http://127.0.0.1:{server.server_port}/api/live-agent-operations?limit=20", timeout=4) as response:
                    operations = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

            self.assertEqual(session_payload["status"], "degraded")
            self.assertEqual(session_payload["group_id"], "resident-main")
            self.assertIn("group:unknown", session_payload["process"]["attention"])
            self.assertIn("agent-a:not_in_group", session_payload["process"]["attention"])
            self.assertEqual(operations["operations"], [])

    def test_live_agent_session_readiness_endpoint_includes_safe_process_reason(self):
        class ReadinessSessionSupervisor:
            def snapshot_groups(self):
                return [
                    {
                        "group_id": "resident-main",
                        "status": "unknown",
                        "meeting_id": "resident-m1",
                        "agents": [{"agent_id": "agent-a"}],
                        "recent_events": [{"event_type": "recovered_unknown", "status": "unknown"}],
                    }
                ]

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            council_config = root / "council.json"
            agent_config = root / "agents.json"
            live_agent_config = root / "live-agents.json"
            _write_single_agent_session_configs(council_config, agent_config, live_agent_config)
            start_live_agent_meeting(
                root,
                council_config_path=council_config,
                agent_config_path=agent_config,
                meeting_id="resident-m1",
            )
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root, process_supervisor=ReadinessSessionSupervisor()))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with urlopen(
                    f"http://127.0.0.1:{server.server_port}/api/live-agent-sessions/readiness?meeting_id=resident-m1&group_id=resident-main",
                    timeout=4,
                ) as response:
                    session_payload = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

        self.assertEqual(session_payload["status"], "degraded")
        self.assertEqual(
            session_payload["process_reason"],
            {
                "event_type": "recovered_unknown",
                "reason": "orphan running record marked unknown",
            },
        )

    def test_live_agent_session_readiness_process_reason_uses_same_process_snapshot(self):
        class ChangingReadinessSessionSupervisor:
            def __init__(self):
                self.calls = 0

            def snapshot_groups(self):
                self.calls += 1
                if self.calls == 1:
                    return [
                        {
                            "group_id": "resident-main",
                            "status": "unknown",
                            "meeting_id": "resident-m1",
                            "agents": [{"agent_id": "agent-a"}],
                            "recent_events": [{"event_type": "recovered_unknown", "status": "unknown"}],
                        }
                    ]
                return [
                    {
                        "group_id": "resident-main",
                        "status": "running",
                        "meeting_id": "resident-m1",
                        "agents": [{"agent_id": "agent-a"}],
                    }
                ]

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            council_config = root / "council.json"
            agent_config = root / "agents.json"
            live_agent_config = root / "live-agents.json"
            _write_single_agent_session_configs(council_config, agent_config, live_agent_config)
            start_live_agent_meeting(
                root,
                council_config_path=council_config,
                agent_config_path=agent_config,
                meeting_id="resident-m1",
            )
            supervisor = ChangingReadinessSessionSupervisor()
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root, process_supervisor=supervisor))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with urlopen(
                    f"http://127.0.0.1:{server.server_port}/api/live-agent-sessions/readiness?meeting_id=resident-m1&group_id=resident-main",
                    timeout=4,
                ) as response:
                    session_payload = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

        self.assertEqual(supervisor.calls, 1)
        self.assertEqual(session_payload["process"]["status"], "unknown")
        self.assertEqual(
            session_payload["process_reason"],
            {
                "event_type": "recovered_unknown",
                "reason": "orphan running record marked unknown",
            },
        )

    def test_live_agent_session_ensure_returns_ready_without_mutating_ready_session(self):
        class EnsureSessionSupervisor:
            def __init__(self) -> None:
                self.started = []

            def snapshot_groups(self):
                return [
                    {
                        "group_id": "resident-main",
                        "status": "running",
                        "meeting_id": "resident-m1",
                        "agents": [{"agent_id": "agent-a"}],
                    }
                ]

            def list_groups(self):
                return self.snapshot_groups()

            def start_group(self, **kwargs):
                self.started.append(kwargs)
                raise AssertionError("ready ensure must not start a group")

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            council_config = root / "council.json"
            agent_config = root / "agents.json"
            live_agent_config = root / "live-agents.json"
            _write_single_agent_session_configs(council_config, agent_config, live_agent_config)
            start_live_agent_meeting(
                root,
                council_config_path=council_config,
                agent_config_path=agent_config,
                meeting_id="resident-m1",
            )
            heartbeat_live_agent(root, "agent-a", status="online")
            supervisor = EnsureSessionSupervisor()
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root, process_supervisor=supervisor))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/live-agent-sessions/ensure",
                    data=json.dumps(
                        {
                            "meeting_id": "resident-m1",
                            "group_id": "resident-main",
                            "live_agent_config_path": str(live_agent_config),
                        }
                    ).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urlopen(request, timeout=4) as response:
                    session_payload = json.loads(response.read().decode("utf-8"))
                with urlopen(f"http://127.0.0.1:{server.server_port}/api/live-agent-operations?limit=20", timeout=4) as response:
                    operations = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

            self.assertEqual(session_payload["status"], "ready")
            self.assertEqual(session_payload["action"], "none")
            self.assertEqual(supervisor.started, [])
            session_operations = [operation for operation in operations["operations"] if operation["operation"].startswith("session.")]
            self.assertEqual([operation["operation"] for operation in session_operations], ["session.ensure"])
            self.assertEqual(session_operations[-1]["status"], "success")
            self.assertEqual(session_operations[-1]["details"]["ensure_action"], "none")

    def test_live_agent_session_ensure_records_durable_session_run_status(self):
        class EnsureSessionSupervisor:
            def snapshot_groups(self):
                return [
                    {
                        "group_id": "resident-main",
                        "status": "running",
                        "meeting_id": "resident-m1",
                        "agents": [{"agent_id": "agent-a"}],
                    }
                ]

            def list_groups(self):
                return self.snapshot_groups()

            def start_group(self, **kwargs):
                raise AssertionError("ready ensure must not start a group")

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            council_config = root / "council.json"
            agent_config = root / "agents.json"
            live_agent_config = root / "live-agents.json"
            _write_single_agent_session_configs(council_config, agent_config, live_agent_config)
            start_live_agent_meeting(
                root,
                council_config_path=council_config,
                agent_config_path=agent_config,
                meeting_id="resident-m1",
            )
            heartbeat_live_agent(root, "agent-a", status="online")
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root, process_supervisor=EnsureSessionSupervisor()))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/live-agent-session-runs/ensure",
                    data=json.dumps(
                        {
                            "meeting_id": "resident-m1",
                            "group_id": "resident-main",
                            "live_agent_config_path": str(live_agent_config),
                            "probe_bound_agents": True,
                        }
                    ).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with patch(
                    "agentsassemble.gui.run_live_agent_probe",
                    return_value={"status": "ok", "agent_id": "agent-a", "source_event_id": "probe-1", "reply_event_id": "reply-1"},
                ):
                    with urlopen(request, timeout=4) as response:
                        session_payload = json.loads(response.read().decode("utf-8"))
                with urlopen(f"http://127.0.0.1:{server.server_port}/api/live-agent-session-runs?limit=20", timeout=4) as response:
                    runs_payload = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

        self.assertEqual(session_payload["status"], "ready")
        self.assertIn("session_run", session_payload)
        self.assertEqual(session_payload["session_run"]["status"], "ready")
        run_id = session_payload["session_run"]["run_id"]
        self.assertEqual(runs_payload["runs"][0]["run_id"], run_id)
        self.assertEqual(runs_payload["runs"][0]["action"], "ensure")
        self.assertEqual(runs_payload["runs"][0]["status"], "ready")
        self.assertTrue(runs_payload["runs"][0]["active"])
        self.assertEqual(runs_payload["runs"][0]["meeting_id"], "resident-m1")
        self.assertEqual(runs_payload["runs"][0]["group_id"], "resident-main")
        self.assertEqual(runs_payload["runs"][0]["result"]["reply_probe"]["status"], "ok")
        self.assertNotIn(str(live_agent_config), str(runs_payload))

    def test_live_agent_session_run_ensure_api_requires_current_approval_for_real_provider_config(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            live_agent_config = root / "live-agents.real.json"
            live_agent_config.write_text(
                json.dumps(
                    {
                        "agents": [
                            {
                                "agent_id": "claude-live",
                                "provider_kind": "claude_code",
                                "connection_kind": "terminal_session",
                                "command": ["claude"],
                            }
                        ]
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
                    f"http://127.0.0.1:{server.server_port}/api/live-agent-session-runs/ensure",
                    data=json.dumps(
                        {
                            "meeting_id": "resident-m1",
                            "group_id": "resident-main",
                            "live_agent_config_path": str(live_agent_config),
                        }
                    ).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with patch("agentsassemble.gui.live_agent_session_ensure_payload") as ensure_payload:
                    ensure_payload.side_effect = AssertionError("approval gate must stop before durable ensure")
                    with self.assertRaises(HTTPError) as raised:
                        urlopen(request, timeout=4)
                body = raised.exception.read().decode("utf-8")
                error_payload = json.loads(body)
                raised.exception.close()
                with urlopen(f"http://127.0.0.1:{server.server_port}/api/live-agent-session-runs?limit=20", timeout=4) as response:
                    runs_payload = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

        self.assertEqual(raised.exception.code, 400)
        self.assertIn("requires current operator approval", error_payload["error"])
        self.assertEqual(runs_payload["runs"][0]["status"], "failed")
        self.assertIn("requires current operator approval", runs_payload["runs"][0]["last_error"])
        self.assertNotIn(str(live_agent_config), body)
        self.assertNotIn(str(live_agent_config), json.dumps(runs_payload, ensure_ascii=False))

    def test_live_agent_session_run_ensure_api_uses_current_real_provider_approval_without_persisting_it(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            live_agent_config = root / "live-agents.real.json"
            live_agent_config.write_text(
                json.dumps(
                    {
                        "agents": [
                            {
                                "agent_id": "claude-live",
                                "provider_kind": "claude_code",
                                "connection_kind": "terminal_session",
                                "command": ["claude"],
                            }
                        ]
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()

            def approved_ensure(output_root, process_supervisor, payload, *, default_server):
                del output_root, process_supervisor, default_server
                self.assertEqual(payload["approve_real_providers"], True)
                return {
                    "status": "ready",
                    "meeting_id": payload["meeting_id"],
                    "group_id": payload["group_id"],
                    "action": "start",
                }

            try:
                request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/live-agent-session-runs/ensure",
                    data=json.dumps(
                        {
                            "meeting_id": "resident-m1",
                            "group_id": "resident-main",
                            "live_agent_config_path": str(live_agent_config),
                            "approve_real_providers": True,
                        }
                    ).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with patch("agentsassemble.gui.live_agent_session_ensure_payload", side_effect=approved_ensure):
                    with urlopen(request, timeout=4) as response:
                        session_payload = json.loads(response.read().decode("utf-8"))
                with urlopen(f"http://127.0.0.1:{server.server_port}/api/live-agent-session-runs?limit=20", timeout=4) as response:
                    runs_payload = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

        self.assertEqual(session_payload["status"], "ready")
        self.assertEqual(runs_payload["runs"][0]["status"], "ready")
        self.assertNotIn("approve_real_providers", json.dumps(runs_payload, ensure_ascii=False))
        self.assertNotIn(str(live_agent_config), json.dumps(runs_payload, ensure_ascii=False))

    def test_live_agent_session_runs_api_filters_meeting_group_before_limit(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            session_run_controller = LiveAgentSessionRunController(root)
            target = session_run_controller.begin_run(
                action="ensure",
                payload={"meeting_id": "resident-m1", "group_id": "resident-main"},
            )
            session_run_controller.finish_run(
                target["run_id"],
                session={
                    "status": "running",
                    "meeting_id": "resident-m1",
                    "group_id": "resident-main",
                    "action": "start",
                },
            )
            unrelated = session_run_controller.begin_run(
                action="ensure",
                payload={"meeting_id": "resident-m2", "group_id": "resident-alt"},
            )
            session_run_controller.finish_run(
                unrelated["run_id"],
                session={
                    "status": "ready",
                    "meeting_id": "resident-m2",
                    "group_id": "resident-alt",
                    "action": "none",
                },
            )
            server = ThreadingHTTPServer(
                ("127.0.0.1", 0),
                _make_handler(root, session_run_controller=session_run_controller),
            )
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with urlopen(
                    f"http://127.0.0.1:{server.server_port}/api/live-agent-session-runs"
                    "?limit=1&meeting_id=resident-m1&group_id=resident-main",
                    timeout=4,
                ) as response:
                    runs_payload = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

        self.assertEqual([run["run_id"] for run in runs_payload["runs"]], [target["run_id"]])
        self.assertEqual(runs_payload["runs"][0]["meeting_id"], "resident-m1")
        self.assertEqual(runs_payload["runs"][0]["group_id"], "resident-main")

    def test_live_agent_session_runs_api_filters_run_id_before_limit_and_overlays_readiness(self):
        class ReadinessSessionSupervisor:
            def snapshot_groups(self):
                return [
                    {
                        "group_id": "resident-main",
                        "status": "stopped",
                        "meeting_id": "resident-m1",
                        "agents": [{"agent_id": "agent-a"}],
                    }
                ]

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            council_config = root / "council.json"
            agent_config = root / "agents.json"
            live_agent_config = root / "live-agents.json"
            _write_single_agent_session_configs(council_config, agent_config, live_agent_config)
            start_live_agent_meeting(
                root,
                council_config_path=council_config,
                agent_config_path=agent_config,
                meeting_id="resident-m1",
            )
            session_run_controller = LiveAgentSessionRunController(root)
            target = session_run_controller.begin_run(
                action="ensure",
                payload={"meeting_id": "resident-m1", "group_id": "resident-main"},
            )
            session_run_controller.finish_run(
                target["run_id"],
                session={
                    "status": "ready",
                    "meeting_id": "resident-m1",
                    "group_id": "resident-main",
                    "action": "none",
                },
            )
            unrelated = session_run_controller.begin_run(
                action="ensure",
                payload={"meeting_id": "resident-m2", "group_id": "resident-alt"},
            )
            session_run_controller.finish_run(
                unrelated["run_id"],
                session={
                    "status": "running",
                    "meeting_id": "resident-m2",
                    "group_id": "resident-alt",
                    "action": "start",
                },
            )
            server = ThreadingHTTPServer(
                ("127.0.0.1", 0),
                _make_handler(
                    root,
                    process_supervisor=ReadinessSessionSupervisor(),
                    session_run_controller=session_run_controller,
                ),
            )
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with urlopen(
                    f"http://127.0.0.1:{server.server_port}/api/live-agent-session-runs"
                    f"?limit=1&run_id={target['run_id']}&include_readiness=1",
                    timeout=4,
                ) as response:
                    runs_payload = json.loads(response.read().decode("utf-8"))
                with urlopen(
                    f"http://127.0.0.1:{server.server_port}/api/live-agent-operations?limit=20",
                    timeout=4,
                ) as response:
                    operations_payload = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

        run = runs_payload["runs"][0]
        self.assertEqual(run["run_id"], target["run_id"])
        self.assertEqual(run["readiness"]["status"], "degraded")
        self.assertEqual(run["readiness"]["process_status"], "stopped")
        self.assertEqual(operations_payload["operations"], [])
        self.assertNotIn(str(live_agent_config), str(runs_payload))

    def test_live_agent_session_run_retry_now_api_clears_backoff_and_records_operation(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            session_run_controller = LiveAgentSessionRunController(root)
            target = session_run_controller.begin_run(
                action="ensure",
                payload={"meeting_id": "resident-m1", "group_id": "resident-main"},
            )
            session_run_controller.finish_run(
                target["run_id"],
                session={
                    "status": "degraded",
                    "meeting_id": "resident-m1",
                    "group_id": "resident-main",
                    "action": "recover",
                },
            )
            server = ThreadingHTTPServer(
                ("127.0.0.1", 0),
                _make_handler(root, session_run_controller=session_run_controller),
            )
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/live-agent-session-runs/{target['run_id']}/retry-now",
                    data=b"{}",
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with patch(
                    "agentsassemble.gui.live_agent_session_ensure_payload",
                    return_value={
                        "status": "ready",
                        "meeting_id": "resident-m1",
                        "group_id": "resident-main",
                        "action": "recover",
                    },
                ) as ensure_payload:
                    with urlopen(request, timeout=4) as response:
                        retry_payload = json.loads(response.read().decode("utf-8"))
                with urlopen(f"http://127.0.0.1:{server.server_port}/api/live-agent-session-runs?limit=20", timeout=4) as response:
                    runs_payload = json.loads(response.read().decode("utf-8"))
                with urlopen(f"http://127.0.0.1:{server.server_port}/api/live-agent-operations?limit=20", timeout=4) as response:
                    operations_payload = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

        self.assertEqual(retry_payload["status"], "reconciled")
        self.assertEqual(retry_payload["session_run"]["run_id"], target["run_id"])
        self.assertEqual(retry_payload["session_run"]["phase"], "recover")
        self.assertEqual(retry_payload["session_run"]["next_reconcile_at"], "")
        self.assertEqual(retry_payload["session_run"]["reconcile_backoff_seconds"], 0)
        self.assertEqual(retry_payload["results"][0]["status"], "ready")
        ensure_payload.assert_called_once()
        self.assertEqual(runs_payload["runs"][0]["phase"], "recover")
        self.assertEqual(runs_payload["runs"][0]["status"], "ready")
        self.assertEqual(operations_payload["operations"][-1]["operation"], "session_run.retry_now")
        self.assertEqual(operations_payload["operations"][-1]["target_id"], target["run_id"])

    def test_live_agent_session_run_retry_now_api_uses_current_real_provider_approval_without_persisting_it(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            live_agent_config = root / "live-agents.real.json"
            live_agent_config.write_text(
                json.dumps(
                    {
                        "agents": [
                            {
                                "agent_id": "claude-live",
                                "display_name": "Claude",
                                "provider_kind": "claude_code",
                                "connection_kind": "terminal_session",
                                "command": ["claude"],
                            }
                        ]
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            session_run_controller = LiveAgentSessionRunController(root)
            target = session_run_controller.begin_run(
                action="ensure",
                payload={
                    "meeting_id": "resident-m1",
                    "group_id": "resident-main",
                    "live_agent_config_path": str(live_agent_config),
                    "server": "http://room.local",
                },
            )
            session_run_controller.finish_run(
                target["run_id"],
                session={
                    "status": "degraded",
                    "meeting_id": "resident-m1",
                    "group_id": "resident-main",
                    "action": "recover",
                },
            )
            server = ThreadingHTTPServer(
                ("127.0.0.1", 0),
                _make_handler(root, session_run_controller=session_run_controller),
            )
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            seen_payloads = []

            def approved_ensure(output_root, process_supervisor, payload, *, default_server):
                del output_root, process_supervisor, default_server
                seen_payloads.append(dict(payload))
                return {
                    "status": "ready",
                    "meeting_id": payload["meeting_id"],
                    "group_id": payload["group_id"],
                    "action": "recover",
                }

            try:
                request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/live-agent-session-runs/{target['run_id']}/retry-now",
                    data=json.dumps({"approve_real_providers": True}).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with patch("agentsassemble.gui.live_agent_session_ensure_payload", side_effect=approved_ensure):
                    with urlopen(request, timeout=4) as response:
                        retry_payload = json.loads(response.read().decode("utf-8"))
                with urlopen(f"http://127.0.0.1:{server.server_port}/api/live-agent-session-runs?limit=20", timeout=4) as response:
                    runs_payload = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

        self.assertEqual(retry_payload["status"], "reconciled")
        self.assertEqual(retry_payload["session_run"]["status"], "ready")
        self.assertEqual(seen_payloads[0]["approve_real_providers"], True)
        self.assertNotIn("approve_real_providers", json.dumps(runs_payload, ensure_ascii=False))
        self.assertNotIn(str(live_agent_config), json.dumps(runs_payload, ensure_ascii=False))

    def test_live_agent_session_run_retry_now_api_rejects_string_false_real_provider_approval(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            live_agent_config = root / "live-agents.real.json"
            live_agent_config.write_text(
                json.dumps(
                    {
                        "agents": [
                            {
                                "agent_id": "claude-live",
                                "provider_kind": "claude_code",
                                "connection_kind": "terminal_session",
                                "command": ["claude"],
                            }
                        ]
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            session_run_controller = LiveAgentSessionRunController(root)
            target = session_run_controller.begin_run(
                action="ensure",
                payload={
                    "meeting_id": "resident-m1",
                    "group_id": "resident-main",
                    "live_agent_config_path": str(live_agent_config),
                    "server": "http://room.local",
                },
            )
            session_run_controller.finish_run(
                target["run_id"],
                session={
                    "status": "degraded",
                    "meeting_id": "resident-m1",
                    "group_id": "resident-main",
                    "action": "recover",
                },
            )
            server = ThreadingHTTPServer(
                ("127.0.0.1", 0),
                _make_handler(root, session_run_controller=session_run_controller),
            )
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/live-agent-session-runs/{target['run_id']}/retry-now",
                    data=json.dumps({"approve_real_providers": "false"}).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with patch("agentsassemble.gui.live_agent_session_ensure_payload") as ensure_payload:
                    ensure_payload.side_effect = AssertionError("string false approval must not relaunch real providers")
                    with urlopen(request, timeout=4) as response:
                        retry_payload = json.loads(response.read().decode("utf-8"))
                with urlopen(f"http://127.0.0.1:{server.server_port}/api/live-agent-session-runs?limit=20", timeout=4) as response:
                    runs_payload = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

        self.assertEqual(retry_payload["status"], "reconciled")
        self.assertEqual(retry_payload["session_run"]["status"], "degraded")
        self.assertIn("requires current operator approval", runs_payload["runs"][0]["last_error"])
        self.assertNotIn(str(live_agent_config), json.dumps(runs_payload, ensure_ascii=False))

    def test_live_agent_session_run_retry_now_api_resolves_latest_matching_meeting_group(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            session_run_controller = LiveAgentSessionRunController(root)
            older_matching = session_run_controller.begin_run(
                action="ensure",
                payload={"meeting_id": "resident-m1", "group_id": "resident-main"},
            )
            session_run_controller.finish_run(
                older_matching["run_id"],
                session={
                    "status": "ready",
                    "meeting_id": "resident-m1",
                    "group_id": "resident-main",
                    "action": "none",
                },
            )
            target = session_run_controller.begin_run(
                action="ensure",
                payload={"meeting_id": "resident-m1", "group_id": "resident-main"},
            )
            session_run_controller.finish_run(
                target["run_id"],
                session={
                    "status": "degraded",
                    "meeting_id": "resident-m1",
                    "group_id": "resident-main",
                    "action": "recover",
                },
            )
            unrelated = session_run_controller.begin_run(
                action="ensure",
                payload={"meeting_id": "resident-m2", "group_id": "resident-alt"},
            )
            session_run_controller.finish_run(
                unrelated["run_id"],
                session={
                    "status": "degraded",
                    "meeting_id": "resident-m2",
                    "group_id": "resident-alt",
                    "action": "recover",
                },
            )
            server = ThreadingHTTPServer(
                ("127.0.0.1", 0),
                _make_handler(root, session_run_controller=session_run_controller),
            )
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/live-agent-session-runs/retry-now",
                    data=json.dumps({"meeting_id": "resident-m1", "group_id": "resident-main"}).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with patch(
                    "agentsassemble.gui.live_agent_session_ensure_payload",
                    return_value={
                        "status": "ready",
                        "meeting_id": "resident-m1",
                        "group_id": "resident-main",
                        "action": "recover",
                    },
                ) as ensure_payload:
                    with urlopen(request, timeout=4) as response:
                        retry_payload = json.loads(response.read().decode("utf-8"))
                with urlopen(f"http://127.0.0.1:{server.server_port}/api/live-agent-operations?limit=20", timeout=4) as response:
                    operations_payload = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

        self.assertEqual(retry_payload["status"], "reconciled")
        self.assertEqual(retry_payload["session_run"]["run_id"], target["run_id"])
        self.assertEqual(retry_payload["session_run"]["status"], "ready")
        ensure_payload.assert_called_once()
        self.assertEqual(operations_payload["operations"][-1]["operation"], "session_run.retry_now")
        self.assertEqual(operations_payload["operations"][-1]["target_id"], target["run_id"])
        self.assertEqual(operations_payload["operations"][-1]["details"]["meeting_id"], "resident-m1")
        self.assertEqual(operations_payload["operations"][-1]["details"]["group_id"], "resident-main")

    def test_live_agent_session_run_retry_now_api_run_id_takes_precedence_over_meeting_group(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            session_run_controller = LiveAgentSessionRunController(root)
            exact = session_run_controller.begin_run(
                action="ensure",
                payload={"meeting_id": "resident-m2", "group_id": "resident-alt"},
            )
            session_run_controller.finish_run(
                exact["run_id"],
                session={
                    "status": "degraded",
                    "meeting_id": "resident-m2",
                    "group_id": "resident-alt",
                    "action": "recover",
                },
            )
            matching = session_run_controller.begin_run(
                action="ensure",
                payload={"meeting_id": "resident-m1", "group_id": "resident-main"},
            )
            session_run_controller.finish_run(
                matching["run_id"],
                session={
                    "status": "degraded",
                    "meeting_id": "resident-m1",
                    "group_id": "resident-main",
                    "action": "recover",
                },
            )
            server = ThreadingHTTPServer(
                ("127.0.0.1", 0),
                _make_handler(root, session_run_controller=session_run_controller),
            )
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/live-agent-session-runs/retry-now",
                    data=json.dumps(
                        {
                            "run_id": exact["run_id"],
                            "meeting_id": "resident-m1",
                            "group_id": "resident-main",
                        }
                    ).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with patch(
                    "agentsassemble.gui.live_agent_session_ensure_payload",
                    return_value={
                        "status": "ready",
                        "meeting_id": "resident-m2",
                        "group_id": "resident-alt",
                        "action": "recover",
                    },
                ):
                    with urlopen(request, timeout=4) as response:
                        retry_payload = json.loads(response.read().decode("utf-8"))
                with urlopen(f"http://127.0.0.1:{server.server_port}/api/live-agent-operations?limit=20", timeout=4) as response:
                    operations_payload = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

        self.assertEqual(retry_payload["status"], "reconciled")
        self.assertEqual(retry_payload["session_run"]["run_id"], exact["run_id"])
        self.assertEqual(retry_payload["session_run"]["meeting_id"], "resident-m2")
        self.assertEqual(retry_payload["session_run"]["group_id"], "resident-alt")
        self.assertEqual(operations_payload["operations"][-1]["target_id"], exact["run_id"])

    def test_live_agent_session_run_retry_now_api_target_skips_current_ready_run(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            session_run_controller = LiveAgentSessionRunController(root)
            target = session_run_controller.begin_run(
                action="ensure",
                payload={"meeting_id": "resident-m1", "group_id": "resident-main"},
            )
            session_run_controller.finish_run(
                target["run_id"],
                session={
                    "status": "ready",
                    "meeting_id": "resident-m1",
                    "group_id": "resident-main",
                    "action": "none",
                },
            )
            server = ThreadingHTTPServer(
                ("127.0.0.1", 0),
                _make_handler(root, session_run_controller=session_run_controller),
            )
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/live-agent-session-runs/retry-now",
                    data=json.dumps({"meeting_id": "resident-m1", "group_id": "resident-main"}).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with patch("agentsassemble.gui.live_agent_session_readiness_payload", return_value={"status": "ready"}):
                    with patch("agentsassemble.gui.live_agent_session_ensure_payload") as ensure_payload:
                        with urlopen(request, timeout=4) as response:
                            retry_payload = json.loads(response.read().decode("utf-8"))
                with urlopen(f"http://127.0.0.1:{server.server_port}/api/live-agent-operations?limit=20", timeout=4) as response:
                    operations_payload = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

        self.assertEqual(retry_payload["status"], "skipped")
        self.assertEqual(retry_payload["session_run"]["run_id"], target["run_id"])
        self.assertEqual(retry_payload["results"], [])
        ensure_payload.assert_not_called()
        self.assertEqual(operations_payload["operations"][-1]["status"], "success")
        self.assertEqual(operations_payload["operations"][-1]["details"]["skipped_reason"], "already_ready")

    def test_live_agent_session_run_retry_now_api_target_refuses_without_matching_run(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            session_run_controller = LiveAgentSessionRunController(root)
            server = ThreadingHTTPServer(
                ("127.0.0.1", 0),
                _make_handler(root, session_run_controller=session_run_controller),
            )
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/live-agent-session-runs/retry-now",
                    data=json.dumps({"meeting_id": "resident-m1", "group_id": "resident-main"}).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with self.assertRaises(HTTPError) as caught:
                    urlopen(request, timeout=4)
                error_payload = json.loads(caught.exception.read().decode("utf-8"))
                caught.exception.close()
                with urlopen(f"http://127.0.0.1:{server.server_port}/api/live-agent-operations?limit=20", timeout=4) as response:
                    operations_payload = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

        self.assertEqual(caught.exception.code, 400)
        self.assertIn("No matching live-agent session run", error_payload["error"])
        self.assertEqual(operations_payload["operations"][-1]["operation"], "session_run.retry_now")
        self.assertEqual(operations_payload["operations"][-1]["status"], "failed")
        self.assertEqual(operations_payload["operations"][-1]["details"]["meeting_id"], "resident-m1")
        self.assertEqual(operations_payload["operations"][-1]["details"]["group_id"], "resident-main")

    def test_live_agent_session_run_retry_now_api_skips_ready_current_ready_without_mutating(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            session_run_controller = LiveAgentSessionRunController(root)
            target = session_run_controller.begin_run(
                action="ensure",
                payload={"meeting_id": "resident-m1", "group_id": "resident-main"},
            )
            session_run_controller.finish_run(
                target["run_id"],
                session={
                    "status": "ready",
                    "meeting_id": "resident-m1",
                    "group_id": "resident-main",
                    "action": "none",
                },
            )
            server = ThreadingHTTPServer(
                ("127.0.0.1", 0),
                _make_handler(root, session_run_controller=session_run_controller),
            )
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/live-agent-session-runs/{target['run_id']}/retry-now",
                    data=b"{}",
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with patch("agentsassemble.gui.live_agent_session_readiness_payload", return_value={"status": "ready"}):
                    with patch("agentsassemble.gui.live_agent_session_ensure_payload") as ensure_payload:
                        with urlopen(request, timeout=4) as response:
                            retry_payload = json.loads(response.read().decode("utf-8"))
                with urlopen(f"http://127.0.0.1:{server.server_port}/api/live-agent-session-runs?limit=20", timeout=4) as response:
                    runs_payload = json.loads(response.read().decode("utf-8"))
                with urlopen(f"http://127.0.0.1:{server.server_port}/api/live-agent-operations?limit=20", timeout=4) as response:
                    operations_payload = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

        self.assertEqual(retry_payload["status"], "skipped")
        self.assertEqual(retry_payload["session_run"]["run_id"], target["run_id"])
        self.assertEqual(retry_payload["session_run"]["phase"], "none")
        self.assertEqual(retry_payload["results"], [])
        ensure_payload.assert_not_called()
        self.assertEqual(runs_payload["runs"][0]["phase"], "none")
        self.assertEqual(runs_payload["runs"][0]["status"], "ready")
        self.assertEqual(operations_payload["operations"][-1]["operation"], "session_run.retry_now")
        self.assertEqual(operations_payload["operations"][-1]["status"], "success")
        self.assertEqual(operations_payload["operations"][-1]["details"]["skipped_reason"], "already_ready")

    def test_live_agent_session_run_pause_resume_api_controls_durable_reconcile(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            session_run_controller = LiveAgentSessionRunController(root)
            target = session_run_controller.begin_run(
                action="ensure",
                payload={"meeting_id": "resident-m1", "group_id": "resident-main"},
            )
            session_run_controller.finish_run(
                target["run_id"],
                session={
                    "status": "degraded",
                    "meeting_id": "resident-m1",
                    "group_id": "resident-main",
                    "action": "recover",
                },
            )
            server = ThreadingHTTPServer(
                ("127.0.0.1", 0),
                _make_handler(root, session_run_controller=session_run_controller),
            )
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                pause_request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/live-agent-session-runs/{target['run_id']}/pause",
                    data=b"{}",
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urlopen(pause_request, timeout=4) as response:
                    pause_payload = json.loads(response.read().decode("utf-8"))
                with patch("agentsassemble.gui.live_agent_session_ensure_payload") as ensure_payload:
                    reconciled_while_paused = session_run_controller.reconcile_active_runs(
                        lambda run: ensure_payload(run) or {"status": "ready"}
                    )
                resume_request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/live-agent-session-runs/{target['run_id']}/resume",
                    data=b"{}",
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urlopen(resume_request, timeout=4) as response:
                    resume_payload = json.loads(response.read().decode("utf-8"))
                with urlopen(f"http://127.0.0.1:{server.server_port}/api/live-agent-session-runs?limit=20", timeout=4) as response:
                    runs_payload = json.loads(response.read().decode("utf-8"))
                with urlopen(f"http://127.0.0.1:{server.server_port}/api/live-agent-operations?limit=20", timeout=4) as response:
                    operations_payload = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

        self.assertEqual(pause_payload["status"], "paused")
        self.assertEqual(pause_payload["session_run"]["status"], "paused")
        self.assertFalse(pause_payload["session_run"]["active"])
        self.assertEqual(pause_payload["session_run"]["paused_status"], "degraded")
        self.assertEqual(reconciled_while_paused, [])
        ensure_payload.assert_not_called()
        self.assertEqual(resume_payload["status"], "resumed")
        self.assertEqual(resume_payload["session_run"]["status"], "degraded")
        self.assertTrue(resume_payload["session_run"]["active"])
        self.assertEqual(resume_payload["session_run"]["next_reconcile_at"], "")
        self.assertEqual(resume_payload["session_run"]["reconcile_backoff_seconds"], 0)
        self.assertEqual(runs_payload["runs"][0]["phase"], "resume_requested")
        self.assertEqual(runs_payload["runs"][0]["next_reconcile_at"], "")
        self.assertEqual(runs_payload["runs"][0]["reconcile_backoff_seconds"], 0)
        self.assertEqual([item["operation"] for item in operations_payload["operations"][-2:]], ["session_run.pause", "session_run.resume"])

    def test_live_agent_session_run_pause_resume_api_resolves_latest_matching_meeting_group(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            session_run_controller = LiveAgentSessionRunController(root)
            older_matching = session_run_controller.begin_run(
                action="ensure",
                payload={"meeting_id": "resident-m1", "group_id": "resident-main"},
            )
            session_run_controller.finish_run(
                older_matching["run_id"],
                session={
                    "status": "degraded",
                    "meeting_id": "resident-m1",
                    "group_id": "resident-main",
                    "action": "recover",
                },
            )
            target = session_run_controller.begin_run(
                action="ensure",
                payload={"meeting_id": "resident-m1", "group_id": "resident-main"},
            )
            session_run_controller.finish_run(
                target["run_id"],
                session={
                    "status": "degraded",
                    "meeting_id": "resident-m1",
                    "group_id": "resident-main",
                    "action": "recover",
                },
            )
            unrelated = session_run_controller.begin_run(
                action="ensure",
                payload={"meeting_id": "resident-m2", "group_id": "resident-alt"},
            )
            session_run_controller.finish_run(
                unrelated["run_id"],
                session={
                    "status": "degraded",
                    "meeting_id": "resident-m2",
                    "group_id": "resident-alt",
                    "action": "recover",
                },
            )
            server = ThreadingHTTPServer(
                ("127.0.0.1", 0),
                _make_handler(root, session_run_controller=session_run_controller),
            )
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                pause_request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/live-agent-session-runs/pause",
                    data=json.dumps({"meeting_id": "resident-m1", "group_id": "resident-main"}).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urlopen(pause_request, timeout=4) as response:
                    pause_payload = json.loads(response.read().decode("utf-8"))
                resume_request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/live-agent-session-runs/resume",
                    data=json.dumps({"meeting_id": "resident-m1", "group_id": "resident-main"}).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urlopen(resume_request, timeout=4) as response:
                    resume_payload = json.loads(response.read().decode("utf-8"))
                with urlopen(f"http://127.0.0.1:{server.server_port}/api/live-agent-operations?limit=20", timeout=4) as response:
                    operations_payload = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

        self.assertEqual(pause_payload["status"], "paused")
        self.assertEqual(pause_payload["session_run"]["run_id"], target["run_id"])
        self.assertEqual(resume_payload["status"], "resumed")
        self.assertEqual(resume_payload["session_run"]["run_id"], target["run_id"])
        self.assertEqual([item["operation"] for item in operations_payload["operations"][-2:]], ["session_run.pause", "session_run.resume"])
        self.assertEqual(operations_payload["operations"][-2]["target_id"], target["run_id"])
        self.assertEqual(operations_payload["operations"][-1]["target_id"], target["run_id"])

    def test_live_agent_session_run_stop_api_resolves_latest_matching_meeting_group(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            session_run_controller = LiveAgentSessionRunController(root)
            older_matching = session_run_controller.begin_run(
                action="ensure",
                payload={"meeting_id": "resident-m1", "group_id": "resident-main"},
            )
            session_run_controller.finish_run(
                older_matching["run_id"],
                session={
                    "status": "degraded",
                    "meeting_id": "resident-m1",
                    "group_id": "resident-main",
                    "action": "recover",
                },
            )
            target = session_run_controller.begin_run(
                action="ensure",
                payload={"meeting_id": "resident-m1", "group_id": "resident-main"},
            )
            session_run_controller.finish_run(
                target["run_id"],
                session={
                    "status": "degraded",
                    "meeting_id": "resident-m1",
                    "group_id": "resident-main",
                    "action": "recover",
                },
            )
            server = ThreadingHTTPServer(
                ("127.0.0.1", 0),
                _make_handler(root, session_run_controller=session_run_controller),
            )
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/live-agent-session-runs/stop",
                    data=json.dumps({"meeting_id": "resident-m1", "group_id": "resident-main"}).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urlopen(request, timeout=4) as response:
                    stop_payload = json.loads(response.read().decode("utf-8"))
                with urlopen(f"http://127.0.0.1:{server.server_port}/api/live-agent-operations?limit=20", timeout=4) as response:
                    operations_payload = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

        self.assertEqual(stop_payload["status"], "stopped")
        self.assertEqual(stop_payload["session_run"]["run_id"], target["run_id"])
        self.assertEqual(stop_payload["session_run"]["status"], "stopped")
        self.assertEqual(session_run_controller.get_run(older_matching["run_id"])["status"], "degraded")
        self.assertEqual(operations_payload["operations"][-1]["operation"], "session_run.stop")
        self.assertEqual(operations_payload["operations"][-1]["target_id"], target["run_id"])

    def test_live_agent_session_run_stop_api_path_stops_exact_run(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            session_run_controller = LiveAgentSessionRunController(root)
            target = session_run_controller.begin_run(
                action="ensure",
                payload={"meeting_id": "resident-m1", "group_id": "resident-main"},
            )
            session_run_controller.finish_run(
                target["run_id"],
                session={
                    "status": "degraded",
                    "meeting_id": "resident-m1",
                    "group_id": "resident-main",
                    "action": "recover",
                },
            )
            server = ThreadingHTTPServer(
                ("127.0.0.1", 0),
                _make_handler(root, session_run_controller=session_run_controller),
            )
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/live-agent-session-runs/{target['run_id']}/stop",
                    data=b"{}",
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urlopen(request, timeout=4) as response:
                    stop_payload = json.loads(response.read().decode("utf-8"))
                with urlopen(f"http://127.0.0.1:{server.server_port}/api/live-agent-operations?limit=20", timeout=4) as response:
                    operations_payload = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

        self.assertEqual(stop_payload["status"], "stopped")
        self.assertEqual(stop_payload["session_run"]["run_id"], target["run_id"])
        self.assertEqual(stop_payload["session_run"]["status"], "stopped")
        self.assertEqual(operations_payload["operations"][-1]["operation"], "session_run.stop")
        self.assertEqual(operations_payload["operations"][-1]["details"]["session_run_id"], target["run_id"])

    def test_live_agent_session_run_pause_api_run_id_takes_precedence_over_meeting_group(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            session_run_controller = LiveAgentSessionRunController(root)
            exact = session_run_controller.begin_run(
                action="ensure",
                payload={"meeting_id": "resident-m2", "group_id": "resident-alt"},
            )
            session_run_controller.finish_run(
                exact["run_id"],
                session={
                    "status": "degraded",
                    "meeting_id": "resident-m2",
                    "group_id": "resident-alt",
                    "action": "recover",
                },
            )
            matching = session_run_controller.begin_run(
                action="ensure",
                payload={"meeting_id": "resident-m1", "group_id": "resident-main"},
            )
            session_run_controller.finish_run(
                matching["run_id"],
                session={
                    "status": "degraded",
                    "meeting_id": "resident-m1",
                    "group_id": "resident-main",
                    "action": "recover",
                },
            )
            server = ThreadingHTTPServer(
                ("127.0.0.1", 0),
                _make_handler(root, session_run_controller=session_run_controller),
            )
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/live-agent-session-runs/pause",
                    data=json.dumps(
                        {
                            "run_id": exact["run_id"],
                            "meeting_id": "resident-m1",
                            "group_id": "resident-main",
                        }
                    ).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urlopen(request, timeout=4) as response:
                    pause_payload = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

        self.assertEqual(pause_payload["status"], "paused")
        self.assertEqual(pause_payload["session_run"]["run_id"], exact["run_id"])
        self.assertEqual(pause_payload["session_run"]["meeting_id"], "resident-m2")
        self.assertEqual(pause_payload["session_run"]["group_id"], "resident-alt")

    def test_live_agent_session_run_resume_api_run_id_takes_precedence_over_meeting_group(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            session_run_controller = LiveAgentSessionRunController(root)
            exact = session_run_controller.begin_run(
                action="ensure",
                payload={"meeting_id": "resident-m2", "group_id": "resident-alt"},
            )
            session_run_controller.finish_run(
                exact["run_id"],
                session={
                    "status": "degraded",
                    "meeting_id": "resident-m2",
                    "group_id": "resident-alt",
                    "action": "recover",
                },
            )
            session_run_controller.pause_run(exact["run_id"])
            matching = session_run_controller.begin_run(
                action="ensure",
                payload={"meeting_id": "resident-m1", "group_id": "resident-main"},
            )
            session_run_controller.finish_run(
                matching["run_id"],
                session={
                    "status": "degraded",
                    "meeting_id": "resident-m1",
                    "group_id": "resident-main",
                    "action": "recover",
                },
            )
            session_run_controller.pause_run(matching["run_id"])
            server = ThreadingHTTPServer(
                ("127.0.0.1", 0),
                _make_handler(root, session_run_controller=session_run_controller),
            )
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/live-agent-session-runs/resume",
                    data=json.dumps(
                        {
                            "run_id": exact["run_id"],
                            "meeting_id": "resident-m1",
                            "group_id": "resident-main",
                        }
                    ).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urlopen(request, timeout=4) as response:
                    resume_payload = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

        self.assertEqual(resume_payload["status"], "resumed")
        self.assertEqual(resume_payload["session_run"]["run_id"], exact["run_id"])
        self.assertEqual(resume_payload["session_run"]["meeting_id"], "resident-m2")
        self.assertEqual(resume_payload["session_run"]["group_id"], "resident-alt")
        self.assertEqual(session_run_controller.get_run(matching["run_id"])["status"], "paused")

    def test_live_agent_session_run_stop_api_run_id_takes_precedence_over_meeting_group(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            session_run_controller = LiveAgentSessionRunController(root)
            exact = session_run_controller.begin_run(
                action="ensure",
                payload={"meeting_id": "resident-m2", "group_id": "resident-alt"},
            )
            session_run_controller.finish_run(
                exact["run_id"],
                session={
                    "status": "degraded",
                    "meeting_id": "resident-m2",
                    "group_id": "resident-alt",
                    "action": "recover",
                },
            )
            matching = session_run_controller.begin_run(
                action="ensure",
                payload={"meeting_id": "resident-m1", "group_id": "resident-main"},
            )
            session_run_controller.finish_run(
                matching["run_id"],
                session={
                    "status": "degraded",
                    "meeting_id": "resident-m1",
                    "group_id": "resident-main",
                    "action": "recover",
                },
            )
            server = ThreadingHTTPServer(
                ("127.0.0.1", 0),
                _make_handler(root, session_run_controller=session_run_controller),
            )
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/live-agent-session-runs/stop",
                    data=json.dumps(
                        {
                            "run_id": exact["run_id"],
                            "meeting_id": "resident-m1",
                            "group_id": "resident-main",
                        }
                    ).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urlopen(request, timeout=4) as response:
                    stop_payload = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

        self.assertEqual(stop_payload["status"], "stopped")
        self.assertEqual(stop_payload["session_run"]["run_id"], exact["run_id"])
        self.assertEqual(stop_payload["session_run"]["meeting_id"], "resident-m2")
        self.assertEqual(stop_payload["session_run"]["group_id"], "resident-alt")
        self.assertEqual(session_run_controller.get_run(matching["run_id"])["status"], "degraded")

    def test_live_agent_session_run_pause_resume_api_uses_latest_before_eligibility_checks(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            session_run_controller = LiveAgentSessionRunController(root)
            older_pause_candidate = session_run_controller.begin_run(
                action="ensure",
                payload={"meeting_id": "resident-m1", "group_id": "resident-main"},
            )
            session_run_controller.finish_run(
                older_pause_candidate["run_id"],
                session={
                    "status": "degraded",
                    "meeting_id": "resident-m1",
                    "group_id": "resident-main",
                    "action": "recover",
                },
            )
            latest_pause_target = session_run_controller.begin_run(
                action="ensure",
                payload={"meeting_id": "resident-m1", "group_id": "resident-main"},
            )
            session_run_controller.fail_run(latest_pause_target["run_id"], "terminal failure")
            older_resume_candidate = session_run_controller.begin_run(
                action="ensure",
                payload={"meeting_id": "resident-m3", "group_id": "resident-review"},
            )
            session_run_controller.finish_run(
                older_resume_candidate["run_id"],
                session={
                    "status": "degraded",
                    "meeting_id": "resident-m3",
                    "group_id": "resident-review",
                    "action": "recover",
                },
            )
            session_run_controller.pause_run(older_resume_candidate["run_id"])
            latest_resume_target = session_run_controller.begin_run(
                action="ensure",
                payload={"meeting_id": "resident-m3", "group_id": "resident-review"},
            )
            session_run_controller.finish_run(
                latest_resume_target["run_id"],
                session={
                    "status": "degraded",
                    "meeting_id": "resident-m3",
                    "group_id": "resident-review",
                    "action": "recover",
                },
            )
            server = ThreadingHTTPServer(
                ("127.0.0.1", 0),
                _make_handler(root, session_run_controller=session_run_controller),
            )
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                pause_request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/live-agent-session-runs/pause",
                    data=json.dumps({"meeting_id": "resident-m1", "group_id": "resident-main"}).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with self.assertRaises(HTTPError) as pause_error:
                    urlopen(pause_request, timeout=4)
                pause_error_payload = json.loads(pause_error.exception.read().decode("utf-8"))
                pause_error.exception.close()
                resume_request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/live-agent-session-runs/resume",
                    data=json.dumps({"meeting_id": "resident-m3", "group_id": "resident-review"}).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with self.assertRaises(HTTPError) as resume_error:
                    urlopen(resume_request, timeout=4)
                resume_error_payload = json.loads(resume_error.exception.read().decode("utf-8"))
                resume_error.exception.close()
                with urlopen(f"http://127.0.0.1:{server.server_port}/api/live-agent-operations?limit=20", timeout=4) as response:
                    operations_payload = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

        self.assertEqual(pause_error.exception.code, 400)
        self.assertIn(latest_pause_target["run_id"], pause_error_payload["error"])
        self.assertEqual(resume_error.exception.code, 400)
        self.assertIn(latest_resume_target["run_id"], resume_error_payload["error"])
        self.assertEqual(session_run_controller.get_run(older_pause_candidate["run_id"])["status"], "degraded")
        self.assertEqual(session_run_controller.get_run(older_resume_candidate["run_id"])["status"], "paused")
        self.assertEqual(operations_payload["operations"][-2]["target_id"], latest_pause_target["run_id"])
        self.assertEqual(operations_payload["operations"][-1]["target_id"], latest_resume_target["run_id"])

    def test_live_agent_session_run_pause_api_target_refuses_without_matching_run(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            session_run_controller = LiveAgentSessionRunController(root)
            server = ThreadingHTTPServer(
                ("127.0.0.1", 0),
                _make_handler(root, session_run_controller=session_run_controller),
            )
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/live-agent-session-runs/pause",
                    data=json.dumps({"meeting_id": "resident-m1", "group_id": "resident-main"}).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with self.assertRaises(HTTPError) as caught:
                    urlopen(request, timeout=4)
                error_payload = json.loads(caught.exception.read().decode("utf-8"))
                caught.exception.close()
                with urlopen(f"http://127.0.0.1:{server.server_port}/api/live-agent-operations?limit=20", timeout=4) as response:
                    operations_payload = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

        self.assertEqual(caught.exception.code, 400)
        self.assertIn("No matching live-agent session run", error_payload["error"])
        self.assertEqual(operations_payload["operations"][-1]["operation"], "session_run.pause")
        self.assertEqual(operations_payload["operations"][-1]["status"], "failed")
        self.assertEqual(operations_payload["operations"][-1]["details"]["meeting_id"], "resident-m1")
        self.assertEqual(operations_payload["operations"][-1]["details"]["group_id"], "resident-main")

    def test_live_agent_session_runs_api_can_overlay_current_readiness_without_operations(self):
        class ReadinessSessionSupervisor:
            def snapshot_groups(self):
                return [
                    {
                        "group_id": "resident-main",
                        "status": "stopped",
                        "meeting_id": "resident-m1",
                        "agents": [{"agent_id": "agent-a"}],
                    }
                ]

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            council_config = root / "council.json"
            agent_config = root / "agents.json"
            live_agent_config = root / "live-agents.json"
            _write_single_agent_session_configs(council_config, agent_config, live_agent_config)
            start_live_agent_meeting(
                root,
                council_config_path=council_config,
                agent_config_path=agent_config,
                meeting_id="resident-m1",
            )
            session_run_controller = LiveAgentSessionRunController(root)
            target = session_run_controller.begin_run(
                action="ensure",
                payload={"meeting_id": "resident-m1", "group_id": "resident-main"},
            )
            session_run_controller.finish_run(
                target["run_id"],
                session={
                    "status": "ready",
                    "meeting_id": "resident-m1",
                    "group_id": "resident-main",
                    "action": "none",
                },
            )
            server = ThreadingHTTPServer(
                ("127.0.0.1", 0),
                _make_handler(
                    root,
                    process_supervisor=ReadinessSessionSupervisor(),
                    session_run_controller=session_run_controller,
                ),
            )
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with urlopen(
                    f"http://127.0.0.1:{server.server_port}/api/live-agent-session-runs"
                    "?limit=1&meeting_id=resident-m1&group_id=resident-main&include_readiness=1",
                    timeout=4,
                ) as response:
                    runs_payload = json.loads(response.read().decode("utf-8"))
                with urlopen(
                    f"http://127.0.0.1:{server.server_port}/api/live-agent-operations?limit=20",
                    timeout=4,
                ) as response:
                    operations = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

        run = runs_payload["runs"][0]
        self.assertEqual(run["run_id"], target["run_id"])
        self.assertEqual(run["status"], "ready")
        self.assertEqual(run["readiness"]["status"], "degraded")
        self.assertEqual(run["readiness"]["process_status"], "stopped")
        self.assertEqual(run["readiness"]["expected"], 1)
        self.assertEqual(run["readiness"]["connected"], 0)
        self.assertEqual(operations["operations"], [])
        self.assertNotIn(str(live_agent_config), str(runs_payload))

    def test_live_agent_session_runs_api_redacts_token_like_readiness_process_reason(self):
        sensitive_agent_token = "ghp_abcdefghijklmnopqrstuvwxyz1234567890"

        class ReadinessSessionSupervisor:
            def snapshot_groups(self):
                return [
                    {
                        "group_id": "resident-main",
                        "status": "error",
                        "meeting_id": "resident-m1",
                        "recent_events": [
                            {
                                "event_type": "stale_watchdog",
                                "reason": f"missing manifest agent {sensitive_agent_token}",
                            }
                        ],
                        "agents": [{"agent_id": "agent-a"}],
                    }
                ]

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _write_health_resident_meeting(root, agent_ids=["agent-a"])
            session_run_controller = LiveAgentSessionRunController(root)
            target = session_run_controller.begin_run(
                action="ensure",
                payload={"meeting_id": "resident-m1", "group_id": "resident-main"},
            )
            session_run_controller.finish_run(
                target["run_id"],
                session={
                    "status": "ready",
                    "meeting_id": "resident-m1",
                    "group_id": "resident-main",
                    "action": "none",
                },
            )
            server = ThreadingHTTPServer(
                ("127.0.0.1", 0),
                _make_handler(
                    root,
                    process_supervisor=ReadinessSessionSupervisor(),
                    session_run_controller=session_run_controller,
                ),
            )
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with urlopen(
                    f"http://127.0.0.1:{server.server_port}/api/live-agent-session-runs"
                    "?limit=1&meeting_id=resident-m1&group_id=resident-main&include_readiness=1",
                    timeout=4,
                ) as response:
                    runs_payload = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

        serialized = json.dumps(runs_payload, ensure_ascii=False)
        self.assertNotIn(sensitive_agent_token, serialized)
        run = runs_payload["runs"][0]
        self.assertEqual(run["run_id"], target["run_id"])
        self.assertEqual(run["readiness"]["status"], "degraded")
        self.assertNotIn("process_reason", run["readiness"])

    def test_live_agent_session_runs_api_redacts_sensitive_legacy_ids_with_readiness(self):
        class FakeSupervisor:
            def snapshot_groups(self):
                return []

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            runs_dir = root / "live-agent-runs"
            runs_dir.mkdir(parents=True)
            (runs_dir / "session-runs.json").write_text(
                json.dumps(
                    {
                        "runs": [
                            {
                                "run_id": "ghp_abcdefghijklmnopqrstuvwxyz1234567890",
                                "action": "ensure",
                                "status": "ready",
                                "active": True,
                                "phase": "/Users/me/private/live-agents.secret.json token sk-test-secret",
                                "meeting_id": "env:SECRET_MEETING",
                                "group_id": "literal:SECRET_GROUP",
                                "request": {
                                    "meeting_id": "env:SECRET_MEETING",
                                    "group_id": "literal:SECRET_GROUP",
                                },
                                "result": {
                                    "status": "ready",
                                    "process": {
                                        "env:SECRET_TOKEN": "ok",
                                        "nested": {"ghp_abcdefghijklmnopqrstuvwxyz1234567890": "ok"},
                                    },
                                    "connection": {"/Users/me/private/config.json": "ok"},
                                },
                            }
                        ]
                    },
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )
            server = ThreadingHTTPServer(
                ("127.0.0.1", 0),
                _make_handler(root, process_supervisor=FakeSupervisor()),
            )
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with urlopen(
                    f"http://127.0.0.1:{server.server_port}/api/live-agent-session-runs?limit=20&include_readiness=1",
                    timeout=4,
                ) as response:
                    runs_payload = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

        serialized = json.dumps(runs_payload, ensure_ascii=False)
        self.assertNotIn("ghp_abcdefghijklmnopqrstuvwxyz1234567890", serialized)
        self.assertNotIn("SECRET_MEETING", serialized)
        self.assertNotIn("SECRET_GROUP", serialized)
        self.assertNotIn("SECRET_TOKEN", serialized)
        self.assertNotIn("Users/me/private", serialized)
        self.assertNotIn("config.json", serialized)
        self.assertNotIn("live-agents.secret.json", serialized)
        self.assertNotIn("sk-test-secret", serialized)
        self.assertEqual(runs_payload["runs"][0]["run_id"], "")
        self.assertEqual(runs_payload["runs"][0]["phase"], "")
        self.assertEqual(runs_payload["runs"][0]["meeting_id"], "")
        self.assertEqual(runs_payload["runs"][0]["group_id"], "")
        self.assertEqual(runs_payload["runs"][0]["readiness"]["attention"], ["session_run:missing_target"])

    def test_live_agent_session_runs_api_redacts_relative_path_like_legacy_values(self):
        class FakeSupervisor:
            def snapshot_groups(self):
                return []

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            runs_dir = root / "live-agent-runs"
            runs_dir.mkdir(parents=True)
            (runs_dir / "session-runs.json").write_text(
                json.dumps(
                    {
                        "runs": [
                            {
                                "run_id": "run-relative-paths",
                                "action": "ensure",
                                "status": "ready",
                                "active": True,
                                "phase": "configs/private.yaml",
                                "meeting_id": "resident-m1",
                                "group_id": "resident-main",
                                "request": {
                                    "meeting_id": "resident-m1",
                                    "group_id": "resident-main",
                                    "live_agent_config_path": "configs/private.yaml",
                                },
                                "result": {
                                    "status": "ready",
                                    "process": {
                                        "relative/private.txt": "ok",
                                        "safe_key": "relative/private.txt",
                                    },
                                },
                            }
                        ]
                    },
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )
            server = ThreadingHTTPServer(
                ("127.0.0.1", 0),
                _make_handler(root, process_supervisor=FakeSupervisor()),
            )
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with urlopen(
                    f"http://127.0.0.1:{server.server_port}/api/live-agent-session-runs?limit=20&include_readiness=1",
                    timeout=4,
                ) as response:
                    runs_payload = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

        serialized = json.dumps(runs_payload, ensure_ascii=False)
        self.assertNotIn("configs/private.yaml", serialized)
        self.assertNotIn("relative/private.txt", serialized)
        run = runs_payload["runs"][0]
        self.assertEqual(run["phase"], "")
        self.assertNotIn("live_agent_config_path", run["request"])
        self.assertEqual(run["result"]["process"], {"safe_key": "[redacted]"})

    def test_live_agent_session_runs_api_redacts_backslash_relative_path_like_legacy_values(self):
        class FakeSupervisor:
            def snapshot_groups(self):
                return []

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            runs_dir = root / "live-agent-runs"
            runs_dir.mkdir(parents=True)
            (runs_dir / "session-runs.json").write_text(
                json.dumps(
                    {
                        "runs": [
                            {
                                "run_id": "run-backslash-paths",
                                "action": "ensure",
                                "status": "ready",
                                "active": True,
                                "phase": "configs\\private.yaml",
                                "meeting_id": "resident-m1",
                                "group_id": "resident-main",
                                "request": {
                                    "meeting_id": "resident-m1",
                                    "group_id": "resident-main",
                                },
                                "result": {
                                    "status": "ready",
                                    "process": {
                                        "relative\\private.txt": "ok",
                                        "safe_key": "..\\private.txt",
                                    },
                                },
                            }
                        ]
                    },
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )
            server = ThreadingHTTPServer(
                ("127.0.0.1", 0),
                _make_handler(root, process_supervisor=FakeSupervisor()),
            )
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with urlopen(
                    f"http://127.0.0.1:{server.server_port}/api/live-agent-session-runs?limit=20&include_readiness=1",
                    timeout=4,
                ) as response:
                    runs_payload = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

        serialized = json.dumps(runs_payload, ensure_ascii=False)
        self.assertNotIn("configs\\\\private.yaml", serialized)
        self.assertNotIn("relative\\\\private.txt", serialized)
        self.assertNotIn("..\\\\private.txt", serialized)
        run = runs_payload["runs"][0]
        self.assertEqual(run["phase"], "")
        self.assertEqual(run["result"]["process"], {"safe_key": "[redacted]"})

    def test_live_agent_session_run_monitor_reconciles_active_runs_on_each_tick(self):
        from agentsassemble.gui import LiveAgentSessionRunMonitor

        class TwoTickStop:
            def __init__(self):
                self.waits = []

            def is_set(self):
                return False

            def wait(self, seconds):
                self.waits.append(seconds)
                return len(self.waits) >= 2

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            live_agent_config = root / "live-agents.json"
            live_agent_config.write_text(
                json.dumps(
                    {
                        "agents": [
                            {
                                "agent_id": "agent-a",
                                "display_name": "Agent A",
                                "provider_kind": "local_cli",
                                "connection_kind": "local_cli",
                                "command": [sys.executable, "-c", "print('ok')"],
                            }
                        ]
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            controller = LiveAgentSessionRunController(root)
            run = controller.begin_run(
                action="ensure",
                payload={
                    "meeting_id": "resident-m1",
                    "group_id": "resident-main",
                    "live_agent_config_path": str(live_agent_config),
                    "server": "http://room.local",
                    "probe_bound_agents": True,
                    "probe_timeout_seconds": 7,
                },
            )
            controller.finish_run(
                run["run_id"],
                session={
                    "status": "ready",
                    "meeting_id": "resident-m1",
                    "group_id": "resident-main",
                    "action": "none",
                },
            )
            observed_requests = []

            def ensure_from_run(output_root, process_supervisor, payload, *, default_server):
                del output_root, process_supervisor, default_server
                observed_requests.append(dict(payload))
                return {
                    "status": "ready",
                    "meeting_id": payload["meeting_id"],
                    "group_id": payload["group_id"],
                    "action": "none",
                }

            monitor = LiveAgentSessionRunMonitor(
                root,
                object(),
                controller,
                default_server="http://room.local",
                interval_seconds=0,
            )
            stop = TwoTickStop()
            with patch("agentsassemble.gui.live_agent_session_ensure_payload", side_effect=ensure_from_run):
                monitor._loop(stop)
            reconciled = controller.list_runs()[0]

        self.assertEqual(len(observed_requests), 2)
        self.assertEqual(observed_requests[0]["live_agent_config_path"], str(live_agent_config))
        self.assertTrue(observed_requests[0]["probe_bound_agents"])
        self.assertEqual(observed_requests[0]["probe_timeout_seconds"], 7)
        self.assertTrue(observed_requests[1]["probe_bound_agents"])
        self.assertEqual(observed_requests[1]["probe_timeout_seconds"], 7)
        self.assertEqual(reconciled["status"], "ready")
        self.assertEqual(reconciled["reconcile_count"], 2)
        self.assertTrue(all(seconds >= 1.0 for seconds in stop.waits))

    def test_live_agent_session_run_monitor_records_safe_failure_and_keeps_loop_alive(self):
        from agentsassemble.gui import LiveAgentSessionRunMonitor, live_agent_health_payload, live_agent_operations_payload

        class RaisingController:
            def __init__(self):
                self.calls = 0

            def reconcile_active_runs(self, callback, **kwargs):
                del callback
                del kwargs
                self.calls += 1
                raise RuntimeError("provider failed in /Users/me/private/live-agents.secret.json")

        class TwoTickStop:
            def __init__(self):
                self.waits = 0

            def is_set(self):
                return False

            def wait(self, seconds):
                del seconds
                self.waits += 1
                return self.waits >= 2

        class FakeSupervisor:
            def snapshot_groups(self):
                return []

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            controller = RaisingController()
            supervisor = FakeSupervisor()
            monitor = LiveAgentSessionRunMonitor(
                root,
                supervisor,
                controller,
                default_server="http://room.local",
                interval_seconds=1,
            )

            monitor._loop(TwoTickStop())
            operations = live_agent_operations_payload(root, limit=10)["operations"]
            health = live_agent_health_payload(root, supervisor, session_run_monitor=monitor)

        self.assertEqual(controller.calls, 2)
        self.assertEqual(len(operations), 2)
        self.assertEqual(operations[-1]["operation"], "session_run.monitor")
        self.assertEqual(operations[-1]["status"], "failed")
        self.assertEqual(operations[-1]["error"], "Live-agent session run monitor failed.")
        self.assertNotIn("/Users/me/private", str(operations))
        self.assertEqual(health["status"], "degraded")
        self.assertEqual(health["session_run_monitor"]["last_status"], "failed")
        self.assertEqual(health["session_run_monitor"]["last_result_count"], 0)
        self.assertEqual(health["session_run_monitor"]["last_error_type"], "RuntimeError")
        self.assertNotIn("/Users/me/private", json.dumps(health, ensure_ascii=False))

    def test_live_agent_session_run_monitor_records_degraded_reconcile_summary(self):
        from agentsassemble.gui import LiveAgentSessionRunMonitor, live_agent_operations_payload

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            live_agent_config = root / "live-agents.json"
            live_agent_config.write_text(
                json.dumps(
                    {
                        "agents": [
                            {
                                "agent_id": "agent-a",
                                "display_name": "Agent A",
                                "provider_kind": "local_cli",
                                "connection_kind": "local_cli",
                                "command": [sys.executable, "-c", "print('ok')"],
                            }
                        ]
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            controller = LiveAgentSessionRunController(root)
            controller.begin_run(
                action="ensure",
                payload={
                    "meeting_id": "resident-m1",
                    "group_id": "resident-main",
                    "live_agent_config_path": str(live_agent_config),
                    "server": "http://room.local",
                },
            )

            def degraded_ensure(output_root, process_supervisor, payload, *, default_server):
                del output_root, process_supervisor, default_server
                return {
                    "status": "degraded",
                    "meeting_id": payload["meeting_id"],
                    "group_id": payload["group_id"],
                    "action": "recover",
                }

            monitor = LiveAgentSessionRunMonitor(
                root,
                object(),
                controller,
                default_server="http://room.local",
            )
            with patch("agentsassemble.gui.live_agent_session_ensure_payload", side_effect=degraded_ensure):
                results = monitor.run_once()
            operations = live_agent_operations_payload(root, limit=10)["operations"]

        self.assertEqual(results[0]["status"], "degraded")
        self.assertEqual(operations[-1]["operation"], "session_run.reconcile")
        self.assertEqual(operations[-1]["status"], "degraded")
        self.assertEqual(operations[-1]["details"]["session_run_count"], 1)
        self.assertEqual(operations[-1]["details"]["session_run_failed_count"], 0)
        self.assertEqual(operations[-1]["details"]["session_run_degraded_count"], 1)

    def test_live_agent_session_run_monitor_requires_current_approval_before_real_provider_reconcile(self):
        from agentsassemble.gui import LiveAgentSessionRunMonitor, live_agent_operations_payload

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            live_agent_config = root / "live-agents.real.json"
            live_agent_config.write_text(
                json.dumps(
                    {
                        "agents": [
                            {
                                "agent_id": "claude-live",
                                "display_name": "Claude",
                                "provider_kind": "claude_code",
                                "connection_kind": "terminal_session",
                                "command": ["/Users/me/private/bin/claude", "--token", "secret-token"],
                            }
                        ]
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            controller = LiveAgentSessionRunController(root)
            controller.begin_run(
                action="ensure",
                payload={
                    "meeting_id": "resident-m1",
                    "group_id": "resident-main",
                    "live_agent_config_path": str(live_agent_config),
                    "server": "http://room.local",
                },
            )
            monitor = LiveAgentSessionRunMonitor(root, object(), controller, default_server="http://room.local")

            with patch("agentsassemble.gui.live_agent_session_ensure_payload") as ensure_payload:
                ensure_payload.side_effect = AssertionError("approval gate must stop before provider ensure")
                results = monitor.run_once()
            operations = live_agent_operations_payload(root, limit=10)["operations"]
            stored_run = controller.list_runs()[0]

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["status"], "degraded")
        self.assertEqual(stored_run["phase"], "reconcile_failed")
        self.assertIn("requires current operator approval", stored_run["last_error"])
        self.assertEqual(stored_run["reconcile_count"], 1)
        self.assertEqual(operations[-1]["operation"], "session_run.reconcile")
        self.assertEqual(operations[-1]["status"], "degraded")
        serialized = json.dumps({"results": results, "operations": operations, "run": stored_run}, ensure_ascii=False)
        self.assertNotIn("/Users/me/private", serialized)
        self.assertNotIn("secret-token", serialized)
        self.assertNotIn(str(live_agent_config), serialized)

    def test_live_agent_session_run_monitor_checks_persisted_process_config_before_recover(self):
        from agentsassemble.gui import LiveAgentSessionRunMonitor, live_agent_operations_payload

        class PersistedRealProviderSupervisor:
            def __init__(self, config_path: Path) -> None:
                self.config_path = config_path

            def snapshot_groups(self):
                return [
                    {
                        "group_id": "resident-main",
                        "status": "stopped",
                        "meeting_id": "resident-m1",
                        "config_path": str(self.config_path),
                        "server": "http://room.local",
                    }
                ]

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            live_agent_config = root / "live-agents.real.json"
            live_agent_config.write_text(
                json.dumps(
                    {
                        "agents": [
                            {
                                "agent_id": "claude-live",
                                "display_name": "Claude",
                                "provider_kind": "claude_code",
                                "connection_kind": "terminal_session",
                                "command": ["claude"],
                            }
                        ]
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            controller = LiveAgentSessionRunController(root)
            controller.begin_run(
                action="ensure",
                payload={
                    "meeting_id": "resident-m1",
                    "group_id": "resident-main",
                    "server": "http://room.local",
                },
            )
            monitor = LiveAgentSessionRunMonitor(
                root,
                PersistedRealProviderSupervisor(live_agent_config),
                controller,
                default_server="http://room.local",
            )

            with patch("agentsassemble.gui.live_agent_session_ensure_payload") as ensure_payload:
                ensure_payload.side_effect = AssertionError("persisted process config gate must stop before recover")
                results = monitor.run_once()
            operations = live_agent_operations_payload(root, limit=10)["operations"]
            stored_run = controller.list_runs()[0]

        self.assertEqual(results[0]["status"], "degraded")
        self.assertIn("requires current operator approval", stored_run["last_error"])
        self.assertEqual(stored_run["reconcile_count"], 1)
        self.assertEqual(operations[-1]["status"], "degraded")
        serialized = json.dumps({"results": results, "operations": operations, "run": stored_run}, ensure_ascii=False)
        self.assertNotIn(str(live_agent_config), serialized)

    def test_live_agent_session_run_monitor_fails_closed_when_process_config_cannot_be_inspected(self):
        from agentsassemble.gui import LiveAgentSessionRunMonitor

        class BrokenSupervisor:
            def snapshot_groups(self):
                raise RuntimeError("snapshot failed near /Users/me/private/live-agents.real.json")

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            controller = LiveAgentSessionRunController(root)
            controller.begin_run(
                action="ensure",
                payload={
                    "meeting_id": "resident-m1",
                    "group_id": "resident-main",
                    "server": "http://room.local",
                },
            )
            monitor = LiveAgentSessionRunMonitor(root, BrokenSupervisor(), controller, default_server="http://room.local")

            with patch("agentsassemble.gui.live_agent_session_ensure_payload") as ensure_payload:
                ensure_payload.side_effect = AssertionError("approval gate must fail closed before reconcile")
                results = monitor.run_once()
            stored_run = controller.list_runs()[0]

        self.assertEqual(results[0]["status"], "degraded")
        self.assertIn("requires current operator approval", stored_run["last_error"])
        self.assertEqual(stored_run["reconcile_count"], 1)
        self.assertNotIn("/Users/me/private", json.dumps({"results": results, "run": stored_run}, ensure_ascii=False))

    def test_live_agent_session_run_monitor_skips_mutating_ensure_for_current_ready_run(self):
        from agentsassemble.gui import LiveAgentSessionRunMonitor, live_agent_operations_payload

        class ReadySessionSupervisor:
            def snapshot_groups(self):
                return [
                    {
                        "group_id": "resident-main",
                        "status": "running",
                        "meeting_id": "resident-m1",
                        "agents": [{"agent_id": "agent-a"}],
                    }
                ]

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            council_config = root / "council.json"
            agent_config = root / "agents.json"
            live_agent_config = root / "live-agents.json"
            _write_single_agent_session_configs(council_config, agent_config, live_agent_config)
            start_live_agent_meeting(
                root,
                council_config_path=council_config,
                agent_config_path=agent_config,
                meeting_id="resident-m1",
            )
            heartbeat_live_agent(root, "agent-a", status="online")
            controller = LiveAgentSessionRunController(root)
            run = controller.begin_run(
                action="ensure",
                payload={
                    "meeting_id": "resident-m1",
                    "group_id": "resident-main",
                    "live_agent_config_path": str(live_agent_config),
                    "server": "http://room.local",
                    "probe_bound_agents": True,
                    "run_remaining_rounds": True,
                    "finalize_after_rounds": True,
                },
            )
            controller.finish_run(
                run["run_id"],
                session={
                    "status": "ready",
                    "meeting_id": "resident-m1",
                    "group_id": "resident-main",
                    "action": "none",
                    "reply_probe": {"status": "ok"},
                    "auto_rounds": {"status": "complete"},
                    "finalization": {"status": "already_finalized"},
                },
            )
            monitor = LiveAgentSessionRunMonitor(
                root,
                ReadySessionSupervisor(),
                controller,
                default_server="http://room.local",
            )

            with patch("agentsassemble.gui.live_agent_session_ensure_payload") as ensure_payload:
                ensure_payload.side_effect = AssertionError("ready monitor tick must stay read-only")
                results = monitor.run_once()
            operations = live_agent_operations_payload(root, limit=10)["operations"]
            stored_run = controller.list_runs()[0]
            snapshot = monitor.snapshot()

        self.assertEqual(results, [])
        self.assertEqual(operations, [])
        self.assertEqual(stored_run["status"], "ready")
        self.assertEqual(stored_run["reconcile_count"], 0)
        self.assertEqual(snapshot["last_status"], "ok")
        self.assertEqual(snapshot["last_result_count"], 0)
        self.assertEqual(snapshot["last_error_type"], "")

    def test_live_agent_session_run_monitor_reconciles_ready_run_with_stale_observation_lag(self):
        from agentsassemble.gui import LiveAgentSessionRunMonitor, live_agent_operations_payload

        class ObservationLagSupervisor:
            def __init__(self, output_root: Path) -> None:
                self.output_root = output_root
                self.stopped = []
                self.restarted = []
                self.group = {
                    "group_id": "resident-main",
                    "status": "running",
                    "meeting_id": "resident-m1",
                    "agents": [{"agent_id": "agent-a"}],
                    "auto_restart": True,
                    "max_restarts": 3,
                    "restart_count": 0,
                    "stale_restart_after_seconds": 1,
                }

            def snapshot_groups(self):
                return [dict(self.group)]

            def stop_group(self, group_id):
                self.stopped.append(group_id)
                self.group["status"] = "stopped"
                return dict(self.group)

            def restart_group(self, group_id, *, restart_count=None):
                self.restarted.append(group_id)
                self.group["status"] = "running"
                if restart_count is not None:
                    self.group["restart_count"] = restart_count
                connect_live_agent(
                    self.output_root,
                    {
                        "agent_id": "agent-a",
                        "display_name": "Agent A",
                        "provider_kind": "local_cli",
                        "connection_kind": "local_cli",
                        "status": "online",
                        "meeting_id": "resident-m1",
                        "last_observed_event_id": "lobby-old",
                    },
                )
                return dict(self.group)

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _write_health_resident_meeting(root, agent_ids=["agent-a"])
            _write_lobby_jsonl_event(root, event_id="lobby-old", actor_id="human", created_at="2000-01-01T00:00:00+00:00")
            connect_live_agent(
                root,
                {
                    "agent_id": "agent-a",
                    "display_name": "Agent A",
                    "provider_kind": "local_cli",
                    "connection_kind": "local_cli",
                    "meeting_id": "resident-m1",
                    "last_observed_event_id": "older-event",
                },
            )
            controller = LiveAgentSessionRunController(root)
            run = controller.begin_run(
                action="ensure",
                payload={
                    "meeting_id": "resident-m1",
                    "group_id": "resident-main",
                    "connect_timeout_seconds": 0,
                },
            )
            controller.finish_run(
                run["run_id"],
                session={"status": "ready", "meeting_id": "resident-m1", "group_id": "resident-main", "action": "none"},
            )
            supervisor = ObservationLagSupervisor(root)
            monitor = LiveAgentSessionRunMonitor(root, supervisor, controller, default_server="http://room.local")

            results = monitor.run_once()
            operations = live_agent_operations_payload(root, limit=10)["operations"]
            stored_run = controller.list_runs()[0]

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["status"], "ready")
        self.assertEqual(results[0]["result"]["ensure_reason"], "stale_lobby_observation")
        self.assertEqual(stored_run["phase"], "restart")
        self.assertEqual(stored_run["result"]["ensure_reason"], "stale_lobby_observation")
        self.assertEqual(stored_run["reconcile_count"], 1)
        self.assertEqual(supervisor.stopped, ["resident-main"])
        self.assertEqual(supervisor.restarted, ["resident-main"])
        self.assertEqual(operations[-1]["operation"], "session_run.reconcile")

    def test_live_agent_session_run_monitor_stops_stale_observation_restart_after_budget(self):
        from agentsassemble.gui import LiveAgentSessionRunMonitor

        class BudgetSupervisor:
            def __init__(self, output_root: Path) -> None:
                self.output_root = output_root
                self.restart_counts = []
                self.group = {
                    "group_id": "resident-main",
                    "status": "running",
                    "meeting_id": "resident-m1",
                    "agents": [{"agent_id": "agent-a"}],
                    "auto_restart": True,
                    "max_restarts": 1,
                    "restart_count": 0,
                    "stale_restart_after_seconds": 1,
                }

            def snapshot_groups(self):
                return [dict(self.group)]

            def stop_group(self, group_id):
                self.group["status"] = "stopped"
                return dict(self.group)

            def restart_group(self, group_id, *, restart_count=None):
                self.restart_counts.append(restart_count)
                self.group["status"] = "running"
                self.group["restart_count"] = restart_count if restart_count is not None else 0
                heartbeat_live_agent(self.output_root, "agent-a", status="online")
                return dict(self.group)

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _write_health_resident_meeting(root, agent_ids=["agent-a"])
            _write_lobby_jsonl_event(root, event_id="lobby-old", actor_id="human", created_at="2000-01-01T00:00:00+00:00")
            connect_live_agent(
                root,
                {
                    "agent_id": "agent-a",
                    "display_name": "Agent A",
                    "provider_kind": "local_cli",
                    "connection_kind": "local_cli",
                    "meeting_id": "resident-m1",
                    "last_observed_event_id": "older-event",
                },
            )
            controller = LiveAgentSessionRunController(root)
            run = controller.begin_run(
                action="ensure",
                payload={
                    "meeting_id": "resident-m1",
                    "group_id": "resident-main",
                    "connect_timeout_seconds": 0,
                },
            )
            controller.finish_run(
                run["run_id"],
                session={"status": "ready", "meeting_id": "resident-m1", "group_id": "resident-main", "action": "none"},
            )
            monitor = LiveAgentSessionRunMonitor(root, BudgetSupervisor(root), controller, default_server="http://room.local")

            first_results = monitor.run_once()
            second_results = monitor.run_once()

        self.assertEqual(len(first_results), 1)
        self.assertEqual(second_results, [])
        self.assertEqual(monitor.process_supervisor.restart_counts, [1])

    def test_live_agent_session_run_monitor_replays_current_run_target_when_original_request_was_blank(self):
        from agentsassemble.gui import LiveAgentSessionRunMonitor

        class ObservationLagSupervisor:
            def snapshot_groups(self):
                return [
                    {
                        "group_id": "resident-main",
                        "status": "running",
                        "meeting_id": "resident-m1",
                        "agents": [{"agent_id": "agent-a"}],
                        "auto_restart": True,
                        "max_restarts": 3,
                        "restart_count": 0,
                        "stale_restart_after_seconds": 1,
                    }
                ]

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _write_health_resident_meeting(root, agent_ids=["agent-a"])
            _write_lobby_jsonl_event(root, event_id="lobby-old", actor_id="human", created_at="2000-01-01T00:00:00+00:00")
            connect_live_agent(
                root,
                {
                    "agent_id": "agent-a",
                    "display_name": "Agent A",
                    "provider_kind": "local_cli",
                    "connection_kind": "local_cli",
                    "meeting_id": "resident-m1",
                    "last_observed_event_id": "older-event",
                },
            )
            controller = LiveAgentSessionRunController(root)
            run = controller.begin_run(action="ensure", payload={})
            controller.finish_run(
                run["run_id"],
                session={"status": "ready", "meeting_id": "resident-m1", "group_id": "resident-main", "action": "none"},
            )
            monitor = LiveAgentSessionRunMonitor(root, ObservationLagSupervisor(), controller, default_server="http://room.local")

            def checked_ensure(output_root, process_supervisor, payload, *, default_server):
                del output_root, process_supervisor, default_server
                self.assertEqual(payload["meeting_id"], "resident-m1")
                self.assertEqual(payload["group_id"], "resident-main")
                return {"status": "ready", "meeting_id": "resident-m1", "group_id": "resident-main", "action": "restart"}

            with patch("agentsassemble.gui.live_agent_session_ensure_payload", side_effect=checked_ensure):
                results = monitor.run_once()

        self.assertEqual(results[0]["status"], "ready")

    def test_live_agent_session_run_monitor_stop_waits_until_in_flight_tick_finishes(self):
        from agentsassemble.gui import LiveAgentSessionRunMonitor

        class BlockingMonitor(LiveAgentSessionRunMonitor):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                self.entered = threading.Event()
                self.release = threading.Event()

            def run_once(self):
                self.entered.set()
                self.release.wait(timeout=2)
                return []

        with tempfile.TemporaryDirectory() as temp_dir:
            monitor = BlockingMonitor(
                Path(temp_dir),
                object(),
                LiveAgentSessionRunController(Path(temp_dir)),
                default_server="http://room.local",
                interval_seconds=1,
            )
            monitor.start()
            self.assertTrue(monitor.entered.wait(timeout=1))
            stopper = threading.Thread(target=lambda: monitor.stop(timeout_seconds=None))
            stopper.start()
            time.sleep(0.05)
            still_waiting = stopper.is_alive()
            monitor.release.set()
            stopper.join(timeout=1)

        self.assertTrue(still_waiting)
        self.assertFalse(stopper.is_alive())

    def test_serve_gui_stops_session_run_monitor_before_process_supervisor(self):
        events = []

        class FakeProcessSupervisor:
            def start_monitor(self):
                events.append("process_start")

            def close(self):
                events.append("process_close")

        class FakeSessionRunMonitor:
            def __init__(self, *args, **kwargs):
                del args, kwargs

            def start(self):
                events.append("session_start")

            def stop(self, *, timeout_seconds=5.0):
                del timeout_seconds
                events.append("session_stop")

        class FakeServer:
            server_address = ("127.0.0.1", 8765)

            def serve_forever(self):
                raise KeyboardInterrupt()

            def server_close(self):
                events.append("server_close")

        with tempfile.TemporaryDirectory() as temp_dir:
            with patch("agentsassemble.gui.LiveAgentProcessSupervisor", return_value=FakeProcessSupervisor()):
                with patch("agentsassemble.gui.LiveAgentSessionRunMonitor", FakeSessionRunMonitor):
                    with patch("agentsassemble.gui.ThreadingHTTPServer", return_value=FakeServer()):
                        serve_gui(output_root=Path(temp_dir))

        self.assertEqual(events[:2], ["process_start", "session_start"])
        self.assertLess(events.index("session_stop"), events.index("process_close"))
        self.assertEqual(events[-1], "server_close")

    def test_live_agent_session_ensure_resolves_blank_meeting_id_from_owned_ready_group(self):
        class EnsureSessionSupervisor:
            def __init__(self) -> None:
                self.started = []

            def snapshot_groups(self):
                return [
                    {
                        "group_id": "resident-main",
                        "status": "running",
                        "meeting_id": "resident-m1",
                        "agents": [{"agent_id": "agent-a"}],
                    }
                ]

            def list_groups(self):
                return self.snapshot_groups()

            def start_group(self, **kwargs):
                self.started.append(kwargs)
                raise AssertionError("blank meeting ensure should adopt the owned meeting before starting")

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            council_config = root / "council.json"
            agent_config = root / "agents.json"
            live_agent_config = root / "live-agents.json"
            _write_single_agent_session_configs(council_config, agent_config, live_agent_config)
            start_live_agent_meeting(
                root,
                council_config_path=council_config,
                agent_config_path=agent_config,
                meeting_id="resident-m1",
            )
            heartbeat_live_agent(root, "agent-a", status="online")
            supervisor = EnsureSessionSupervisor()
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root, process_supervisor=supervisor))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/live-agent-sessions/ensure",
                    data=json.dumps(
                        {
                            "meeting_id": "",
                            "group_id": "resident-main",
                            "live_agent_config_path": str(live_agent_config),
                        }
                    ).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urlopen(request, timeout=4) as response:
                    session_payload = json.loads(response.read().decode("utf-8"))
                with urlopen(f"http://127.0.0.1:{server.server_port}/api/live-agent-operations?limit=20", timeout=4) as response:
                    operations = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

            self.assertEqual(session_payload["status"], "ready")
            self.assertEqual(session_payload["meeting_id"], "resident-m1")
            self.assertEqual(session_payload["action"], "none")
            self.assertEqual(supervisor.started, [])
            session_operations = [operation for operation in operations["operations"] if operation["operation"] == "session.ensure"]
            self.assertEqual(session_operations[-1]["status"], "success")
            self.assertEqual(session_operations[-1]["target_id"], "resident-m1")
            self.assertEqual(session_operations[-1]["details"]["ensure_action"], "none")

    def test_live_agent_session_ensure_blank_meeting_refuses_missing_owned_meeting_without_new_start(self):
        class EnsureSessionSupervisor:
            def __init__(self) -> None:
                self.started = []

            def snapshot_groups(self):
                return [
                    {
                        "group_id": "resident-main",
                        "status": "running",
                        "meeting_id": "missing-meeting",
                        "agents": [{"agent_id": "agent-a"}],
                    }
                ]

            def list_groups(self):
                return self.snapshot_groups()

            def start_group(self, **kwargs):
                self.started.append(kwargs)
                raise AssertionError("missing owned meeting must be refused before a new start")

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            live_agent_config = root / "live-agents.json"
            _write_single_agent_session_configs(root / "council.json", root / "agents.json", live_agent_config)
            supervisor = EnsureSessionSupervisor()
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root, process_supervisor=supervisor))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/live-agent-sessions/ensure",
                    data=json.dumps(
                        {
                            "meeting_id": "",
                            "group_id": "resident-main",
                            "live_agent_config_path": str(live_agent_config),
                        }
                    ).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with self.assertRaises(HTTPError) as raised:
                    urlopen(request, timeout=4)
                body = raised.exception.read().decode("utf-8")
                error_payload = json.loads(body)
                raised.exception.close()
                with urlopen(f"http://127.0.0.1:{server.server_port}/api/live-agent-operations?limit=20", timeout=4) as response:
                    operations = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

            self.assertEqual(supervisor.started, [])
            self.assertIn("Meeting missing-meeting was not found", body)
            self.assertEqual(error_payload["details"]["requested_meeting_id"], "missing-meeting")
            self.assertEqual(error_payload["details"]["group_id"], "resident-main")
            session_operations = [operation for operation in operations["operations"] if operation["operation"] == "session.ensure"]
            self.assertEqual(session_operations[-1]["status"], "failed")

    def test_live_agent_session_ensure_restarts_ready_session_when_resident_session_id_drifted(self):
        class EnsureSessionSupervisor:
            def __init__(self, output_root: Path, live_agent_config: Path) -> None:
                self.output_root = output_root
                self.live_agent_config = live_agent_config
                self.started = []
                self.stopped = []
                self.restarted = []
                self.group = {
                    "group_id": "resident-main",
                    "status": "running",
                    "meeting_id": "resident-m1",
                    "config_path": str(live_agent_config),
                    "server": "http://127.0.0.1:8765",
                    "agents": [{"agent_id": "agent-a"}],
                }

            def snapshot_groups(self):
                return [dict(self.group)]

            def list_groups(self):
                return self.snapshot_groups()

            def stop_group(self, group_id):
                self.stopped.append(group_id)
                self.group["status"] = "stopped"

            def restart_group(self, group_id):
                self.restarted.append(group_id)
                config = json.loads(self.live_agent_config.read_text(encoding="utf-8"))
                agent = config["agents"][0]
                connect_live_agent_payload(
                    self.output_root,
                    {
                        "agent_id": agent["agent_id"],
                        "display_name": "Agent A",
                        "provider_kind": agent.get("provider_kind", "codex_live_session"),
                        "connection_kind": agent.get("connection_kind", "live_session"),
                        "meeting_id": "resident-m1",
                        "session_id": agent.get("session_id", ""),
                    },
                )
                heartbeat_live_agent(self.output_root, agent["agent_id"], status="online", metadata={"session_id": agent.get("session_id", "")})
                self.group["status"] = "running"
                return dict(self.group)

            def start_group(self, **kwargs):
                self.started.append(kwargs)
                raise AssertionError("drifted ready ensure should restart the existing group")

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            meeting_dir = root / "meetings" / "resident-m1"
            meeting_dir.mkdir(parents=True)
            write_live_state(
                meeting_dir,
                {
                    "meeting_id": "resident-m1",
                    "roles": [{"id": "architect", "display_name": "Architect"}],
                    "provider_configs": {"codex-live": {"id": "codex-live", "kind": "codex_live_session"}},
                    "permission_profiles": {"meeting_readonly": {"id": "meeting_readonly", "meeting_read": True}},
                    "agent_bindings": [
                        {
                            "agent_id": "agent-a",
                            "role_id": "architect",
                            "provider_id": "codex-live",
                            "permission_profile_id": "meeting_readonly",
                            "session_id": "new-session",
                        }
                    ],
                    "debate_rounds": [],
                    "live_status": "running",
                },
            )
            live_agent_config = root / "live-agents.json"
            live_agent_config.write_text(
                json.dumps(
                    {
                        "agents": [
                            {
                                "agent_id": "agent-a",
                                "display_name": "Agent A",
                                "provider_kind": "codex_live_session",
                                "connection_kind": "live_session",
                                "meeting_id": "resident-m1",
                                "session_id": "new-session",
                            }
                        ]
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            connect_live_agent_payload(
                root,
                {
                    "agent_id": "agent-a",
                    "display_name": "Agent A",
                    "provider_kind": "codex_live_session",
                    "connection_kind": "live_session",
                    "meeting_id": "resident-m1",
                    "session_id": "old-session",
                },
            )
            heartbeat_live_agent(root, "agent-a", status="online", metadata={"session_id": "old-session"})
            supervisor = EnsureSessionSupervisor(root, live_agent_config)
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root, process_supervisor=supervisor))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/live-agent-sessions/ensure",
                    data=json.dumps(
                        {
                            "meeting_id": "resident-m1",
                            "group_id": "resident-main",
                            "live_agent_config_path": str(live_agent_config),
                            "connect_timeout_seconds": 0,
                        }
                    ).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with patch(
                    "agentsassemble.live_agent_sessions.preflight_live_agent_config",
                    return_value={"status": "ok", "summary": {"agents": 1}},
                ):
                    with urlopen(request, timeout=4) as response:
                        session_payload = json.loads(response.read().decode("utf-8"))
                with urlopen(f"http://127.0.0.1:{server.server_port}/api/live-agent-operations?limit=20", timeout=4) as response:
                    operations = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

            self.assertEqual(session_payload["status"], "ready")
            self.assertEqual(session_payload["action"], "restart")
            self.assertEqual(session_payload["ensure_reason"], "resident_session_id_drift")
            serialized_session = json.dumps(session_payload, ensure_ascii=False)
            self.assertNotIn("old-session", serialized_session)
            self.assertNotIn("new-session", serialized_session)
            self.assertEqual(supervisor.started, [])
            self.assertEqual(supervisor.stopped, ["resident-main"])
            self.assertEqual(supervisor.restarted, ["resident-main"])
            session_operations = [operation for operation in operations["operations"] if operation["operation"] == "session.ensure"]
            self.assertEqual(session_operations[-1]["details"]["ensure_reason"], "resident_session_id_drift")
            agent = next(agent for agent in read_live_agents(root) if agent["agent_id"] == "agent-a")
            self.assertEqual(agent["session_id"], "new-session")

    def test_live_agent_session_ensure_restarts_ready_session_when_stale_lobby_cursor_lags(self):
        class ObservationLagSupervisor:
            def __init__(self, output_root: Path) -> None:
                self.output_root = output_root
                self.started = []
                self.stopped = []
                self.restarted = []
                self.group = {
                    "group_id": "resident-main",
                    "status": "running",
                    "meeting_id": "resident-m1",
                    "agents": [{"agent_id": "agent-a"}],
                    "auto_restart": True,
                    "max_restarts": 3,
                    "restart_count": 0,
                    "stale_restart_after_seconds": 1,
                }

            def snapshot_groups(self):
                return [dict(self.group)]

            def list_groups(self):
                return self.snapshot_groups()

            def stop_group(self, group_id):
                self.stopped.append(group_id)
                self.group["status"] = "stopped"
                return dict(self.group)

            def restart_group(self, group_id, *, restart_count=None):
                self.restarted.append(group_id)
                self.group["status"] = "running"
                if restart_count is not None:
                    self.group["restart_count"] = restart_count
                connect_live_agent(
                    self.output_root,
                    {
                        "agent_id": "agent-a",
                        "display_name": "Agent A",
                        "provider_kind": "local_cli",
                        "connection_kind": "local_cli",
                        "status": "online",
                        "meeting_id": "resident-m1",
                        "last_observed_event_id": "lobby-old",
                    },
                )
                return dict(self.group)

            def start_group(self, **kwargs):
                self.started.append(kwargs)
                raise AssertionError("stale observation ensure should restart the existing group")

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _write_health_resident_meeting(root, agent_ids=["agent-a"])
            _write_lobby_jsonl_event(root, event_id="lobby-old", actor_id="human", created_at="2000-01-01T00:00:00+00:00")
            connect_live_agent(
                root,
                {
                    "agent_id": "agent-a",
                    "display_name": "Agent A",
                    "provider_kind": "local_cli",
                    "connection_kind": "local_cli",
                    "meeting_id": "resident-m1",
                    "last_observed_event_id": "older-event",
                },
            )
            supervisor = ObservationLagSupervisor(root)

            session = live_agent_session_ensure_payload(
                root,
                supervisor,
                {
                    "meeting_id": "resident-m1",
                    "group_id": "resident-main",
                    "connect_timeout_seconds": 0,
                },
                default_server="http://room.local",
            )
            agents = read_live_agents(root)

        self.assertEqual(session["status"], "ready")
        self.assertEqual(session["action"], "restart")
        self.assertEqual(session["ensure_reason"], "stale_lobby_observation")
        self.assertEqual(supervisor.started, [])
        self.assertEqual(supervisor.stopped, ["resident-main"])
        self.assertEqual(supervisor.restarted, ["resident-main"])
        agent = next(agent for agent in agents if agent["agent_id"] == "agent-a")
        self.assertEqual(agent["last_observed_event_id"], "lobby-old")

    def test_live_agent_session_ensure_restarts_ready_session_when_stale_live_cursor_lags(self):
        class ObservationLagSupervisor:
            def __init__(self, output_root: Path) -> None:
                self.output_root = output_root
                self.started = []
                self.stopped = []
                self.restarted = []
                self.group = {
                    "group_id": "resident-main",
                    "status": "running",
                    "meeting_id": "resident-m1",
                    "agents": [{"agent_id": "agent-a"}],
                    "auto_restart": True,
                    "max_restarts": 3,
                    "restart_count": 0,
                    "stale_restart_after_seconds": 1,
                }

            def snapshot_groups(self):
                return [dict(self.group)]

            def list_groups(self):
                return self.snapshot_groups()

            def stop_group(self, group_id):
                self.stopped.append(group_id)
                self.group["status"] = "stopped"
                return dict(self.group)

            def restart_group(self, group_id, *, restart_count=None):
                self.restarted.append(group_id)
                self.group["status"] = "running"
                if restart_count is not None:
                    self.group["restart_count"] = restart_count
                heartbeat_live_agent(self.output_root, "agent-a", status="online", metadata={"last_observed_live_event_id": "live-old"})
                return dict(self.group)

            def start_group(self, **kwargs):
                self.started.append(kwargs)
                raise AssertionError("stale official-turn observation should restart the existing group")

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            meeting_dir = _write_health_resident_meeting(root, agent_ids=["agent-a"])
            request = _write_live_jsonl_event(
                meeting_dir,
                event_id="live-old",
                kind="live_agent_turn_request",
                target_agent_id="agent-a",
                created_at="2000-01-01T00:00:00+00:00",
                content="official request text must stay out",
            )
            connect_live_agent(
                root,
                {
                    "agent_id": "agent-a",
                    "display_name": "Agent A",
                    "provider_kind": "local_cli",
                    "connection_kind": "local_cli",
                    "meeting_id": "resident-m1",
                    "last_observed_live_event_id": "older-live-event",
                },
            )
            supervisor = ObservationLagSupervisor(root)

            session = live_agent_session_ensure_payload(
                root,
                supervisor,
                {
                    "meeting_id": "resident-m1",
                    "group_id": "resident-main",
                    "connect_timeout_seconds": 0,
                },
                default_server="http://room.local",
            )

        self.assertEqual(session["status"], "ready")
        self.assertEqual(session["action"], "restart")
        self.assertEqual(session["ensure_reason"], "stale_live_observation")
        self.assertEqual(supervisor.started, [])
        self.assertEqual(supervisor.stopped, ["resident-main"])
        self.assertEqual(supervisor.restarted, ["resident-main"])
        self.assertNotIn("official request text", json.dumps(session, ensure_ascii=False))
        self.assertEqual(request["id"], "live-old")

    def test_live_agent_session_ensure_does_not_restart_answered_official_turn_lag(self):
        class ObservationLagSupervisor:
            def __init__(self) -> None:
                self.stopped = []
                self.restarted = []
                self.group = {
                    "group_id": "resident-main",
                    "status": "running",
                    "meeting_id": "resident-m1",
                    "agents": [{"agent_id": "agent-a"}],
                    "auto_restart": True,
                    "max_restarts": 3,
                    "restart_count": 0,
                    "stale_restart_after_seconds": 1,
                }

            def snapshot_groups(self):
                return [dict(self.group)]

            def list_groups(self):
                return self.snapshot_groups()

            def stop_group(self, group_id):
                self.stopped.append(group_id)
                raise AssertionError("answered official-turn lag must not stop the group")

            def restart_group(self, group_id):
                self.restarted.append(group_id)
                raise AssertionError("answered official-turn lag must not restart the group")

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            meeting_dir = _write_health_resident_meeting(root, agent_ids=["agent-a"])
            request = _write_live_jsonl_event(
                meeting_dir,
                event_id="live-old",
                kind="live_agent_turn_request",
                target_agent_id="agent-a",
                created_at="2000-01-01T00:00:00+00:00",
                content="official request text must stay out",
            )
            _write_live_jsonl_event(
                meeting_dir,
                event_id="live-reply",
                kind="message",
                actor_id="agent-a",
                source_event_id=request["id"],
                created_at="2000-01-01T00:00:01+00:00",
                content="official reply text must stay out",
            )
            connect_live_agent(
                root,
                {
                    "agent_id": "agent-a",
                    "display_name": "Agent A",
                    "provider_kind": "local_cli",
                    "connection_kind": "local_cli",
                    "meeting_id": "resident-m1",
                    "last_observed_live_event_id": "older-live-event",
                },
            )
            supervisor = ObservationLagSupervisor()

            session = live_agent_session_ensure_payload(
                root,
                supervisor,
                {
                    "meeting_id": "resident-m1",
                    "group_id": "resident-main",
                    "connect_timeout_seconds": 0,
                },
                default_server="http://room.local",
            )

        self.assertEqual(session["status"], "ready")
        self.assertEqual(session["action"], "none")
        self.assertEqual(supervisor.stopped, [])
        self.assertEqual(supervisor.restarted, [])
        self.assertNotIn("official request text", json.dumps(session, ensure_ascii=False))
        self.assertNotIn("official reply text", json.dumps(session, ensure_ascii=False))

    def test_live_agent_session_restart_ignores_external_stale_restart_count_payload(self):
        from agentsassemble.gui import live_agent_session_restart_payload

        class RestartSupervisor:
            def __init__(self, output_root: Path) -> None:
                self.output_root = output_root
                self.restart_counts = []
                self.group = {
                    "group_id": "resident-main",
                    "status": "running",
                    "meeting_id": "resident-m1",
                    "agents": [{"agent_id": "agent-a"}],
                    "auto_restart": True,
                    "max_restarts": 3,
                    "restart_count": 2,
                    "stale_restart_after_seconds": 1,
                }

            def snapshot_groups(self):
                return [dict(self.group)]

            def stop_group(self, group_id):
                self.group["status"] = "stopped"
                return dict(self.group)

            def restart_group(self, group_id, *, restart_count=None):
                self.restart_counts.append(restart_count)
                self.group["status"] = "running"
                self.group["restart_count"] = restart_count if restart_count is not None else 0
                heartbeat_live_agent(self.output_root, "agent-a", status="online")
                return dict(self.group)

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _write_health_resident_meeting(root, agent_ids=["agent-a"])
            connect_live_agent(
                root,
                {
                    "agent_id": "agent-a",
                    "display_name": "Agent A",
                    "provider_kind": "local_cli",
                    "connection_kind": "local_cli",
                    "meeting_id": "resident-m1",
                    "status": "online",
                },
            )
            supervisor = RestartSupervisor(root)

            session = live_agent_session_restart_payload(
                root,
                supervisor,
                {
                    "meeting_id": "resident-m1",
                    "group_id": "resident-main",
                    "connect_timeout_seconds": 0,
                    "_stale_observation_restart_count": 7,
                },
            )

        self.assertEqual(session["status"], "ready")
        self.assertEqual(supervisor.restart_counts, [None])
        self.assertEqual(supervisor.group["restart_count"], 0)

    def test_live_agent_session_ensure_does_not_restart_observation_lag_without_auto_restart(self):
        class ObservationLagSupervisor:
            def __init__(self) -> None:
                self.stopped = []
                self.restarted = []
                self.group = {
                    "group_id": "resident-main",
                    "status": "running",
                    "meeting_id": "resident-m1",
                    "agents": [{"agent_id": "agent-a"}],
                    "auto_restart": False,
                    "max_restarts": 0,
                    "restart_count": 0,
                    "stale_restart_after_seconds": 1,
                }

            def snapshot_groups(self):
                return [dict(self.group)]

            def list_groups(self):
                return self.snapshot_groups()

            def stop_group(self, group_id):
                self.stopped.append(group_id)
                raise AssertionError("disabled auto-restart must not stop the group")

            def restart_group(self, group_id):
                self.restarted.append(group_id)
                raise AssertionError("disabled auto-restart must not restart the group")

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _write_health_resident_meeting(root, agent_ids=["agent-a"])
            _write_lobby_jsonl_event(root, event_id="lobby-old", actor_id="human", created_at="2000-01-01T00:00:00+00:00")
            connect_live_agent(
                root,
                {
                    "agent_id": "agent-a",
                    "display_name": "Agent A",
                    "provider_kind": "local_cli",
                    "connection_kind": "local_cli",
                    "meeting_id": "resident-m1",
                    "last_observed_event_id": "older-event",
                },
            )
            supervisor = ObservationLagSupervisor()

            session = live_agent_session_ensure_payload(
                root,
                supervisor,
                {
                    "meeting_id": "resident-m1",
                    "group_id": "resident-main",
                    "connect_timeout_seconds": 0,
                },
                default_server="http://room.local",
            )

        self.assertEqual(session["status"], "ready")
        self.assertEqual(session["action"], "none")
        self.assertEqual(supervisor.stopped, [])
        self.assertEqual(supervisor.restarted, [])

    def test_live_agent_session_ensure_ready_noop_can_probe_and_run_remaining_rounds(self):
        class EnsureSessionSupervisor:
            def snapshot_groups(self):
                return [
                    {
                        "group_id": "resident-main",
                        "status": "running",
                        "meeting_id": "resident-m1",
                        "agents": [{"agent_id": "agent-a"}],
                    }
                ]

            def list_groups(self):
                return self.snapshot_groups()

            def start_group(self, **kwargs):
                raise AssertionError("ready ensure with post-ready checks must not start a group")

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            council_config = root / "council.json"
            agent_config = root / "agents.json"
            live_agent_config = root / "live-agents.json"
            _write_single_agent_session_configs(council_config, agent_config, live_agent_config)
            start_live_agent_meeting(
                root,
                council_config_path=council_config,
                agent_config_path=agent_config,
                meeting_id="resident-m1",
            )
            heartbeat_live_agent(root, "agent-a", status="online")
            auto_rounds = {
                "status": "answered",
                "meeting_id": "resident-m1",
                "round_count": 1,
                "answered_round_count": 1,
                "completed_round_count": 0,
                "timeout_round_count": 0,
                "skipped_round_count": 0,
                "stopped_round_count": 0,
                "stopped": False,
                "results": [{"round_id": "round_1", "status": "answered"}],
            }
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root, process_supervisor=EnsureSessionSupervisor()))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with (
                    patch(
                        "agentsassemble.gui.run_live_agent_probe",
                        return_value={"status": "ok", "agent_id": "agent-a", "source_event_id": "probe-1", "reply_event_id": "reply-1"},
                    ) as run_probe,
                    patch("agentsassemble.gui.live_agent_turn_rounds_payload", return_value=auto_rounds) as rounds_payload,
                ):
                    request = Request(
                        f"http://127.0.0.1:{server.server_port}/api/live-agent-sessions/ensure",
                        data=json.dumps(
                            {
                                "meeting_id": "resident-m1",
                                "group_id": "resident-main",
                                "live_agent_config_path": str(live_agent_config),
                                "probe_bound_agents": True,
                                "probe_timeout_seconds": 0.5,
                                "run_remaining_rounds": True,
                                "round_timeout_seconds": 2,
                                "round_max_rounds": 1,
                            }
                        ).encode("utf-8"),
                        headers={"Content-Type": "application/json"},
                        method="POST",
                    )
                    with urlopen(request, timeout=4) as response:
                        session_payload = json.loads(response.read().decode("utf-8"))
                with urlopen(f"http://127.0.0.1:{server.server_port}/api/live-agent-operations?limit=20", timeout=4) as response:
                    operations = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

        run_probe.assert_called_once_with(root, "agent-a", timeout_seconds=0.5)
        rounds_payload.assert_called_once()
        self.assertEqual(session_payload["status"], "ready")
        self.assertEqual(session_payload["action"], "none")
        self.assertEqual(session_payload["reply_probe"]["status"], "ok")
        self.assertEqual(session_payload["auto_rounds"]["status"], "answered")
        session_operations = [operation for operation in operations["operations"] if operation["operation"] == "session.ensure"]
        self.assertEqual(session_operations[-1]["status"], "success")
        self.assertEqual(session_operations[-1]["details"]["ensure_action"], "none")
        self.assertEqual(session_operations[-1]["details"]["reply_probe_status"], "ok")
        self.assertEqual(session_operations[-1]["details"]["auto_rounds_status"], "answered")

    def test_live_agent_session_ensure_can_finalize_after_answered_remaining_rounds(self):
        class EnsureSessionSupervisor:
            def snapshot_groups(self):
                return [
                    {
                        "group_id": "resident-main",
                        "status": "running",
                        "meeting_id": "resident-m1",
                        "agents": [{"agent_id": "agent-a"}],
                    }
                ]

            def list_groups(self):
                return self.snapshot_groups()

            def start_group(self, **kwargs):
                raise AssertionError("ready ensure with post-ready checks must not start a group")

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            council_config = root / "council.json"
            agent_config = root / "agents.json"
            live_agent_config = root / "live-agents.json"
            _write_single_agent_session_configs(council_config, agent_config, live_agent_config)
            start_live_agent_meeting(
                root,
                council_config_path=council_config,
                agent_config_path=agent_config,
                meeting_id="resident-m1",
            )
            meeting_dir = root / "meetings" / "resident-m1"
            live_state = json.loads((meeting_dir / "live_state.json").read_text(encoding="utf-8"))
            live_state["debate_rounds"] = [
                {"id": round_item["id"], "status": "answered"}
                for round_item in live_state["meeting_template"]["rounds"]
            ]
            write_live_state(meeting_dir, live_state)
            heartbeat_live_agent(root, "agent-a", status="online")
            auto_rounds = {
                "status": "answered",
                "meeting_id": "resident-m1",
                "round_count": 1,
                "answered_round_count": 1,
                "completed_round_count": 0,
                "timeout_round_count": 0,
                "skipped_round_count": 0,
                "stopped_round_count": 0,
                "stopped": False,
                "results": [{"round_id": "round_1", "status": "answered"}],
            }
            finalized = {
                "status": "finalized",
                "meeting_id": "resident-m1",
                "official_event_count": 1,
                "artifact_event_id": "artifact-1",
                "shared_memory": {
                    "official_event_count": 1,
                    "last_official_event_id": "reply-1",
                    "decision_count": 0,
                    "open_question_count": 0,
                    "action_item_count": 1,
                },
            }
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root, process_supervisor=EnsureSessionSupervisor()))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with (
                    patch("agentsassemble.gui.live_agent_turn_rounds_payload", return_value=auto_rounds) as rounds_payload,
                    patch("agentsassemble.gui.finalize_live_agent_meeting", return_value=finalized) as finalize_meeting,
                ):
                    request = Request(
                        f"http://127.0.0.1:{server.server_port}/api/live-agent-sessions/ensure",
                        data=json.dumps(
                            {
                                "meeting_id": "resident-m1",
                                "group_id": "resident-main",
                                "live_agent_config_path": str(live_agent_config),
                                "run_remaining_rounds": True,
                                "round_timeout_seconds": 2,
                                "round_max_rounds": 1,
                                "finalize_after_rounds": True,
                            }
                        ).encode("utf-8"),
                        headers={"Content-Type": "application/json"},
                        method="POST",
                    )
                    with urlopen(request, timeout=4) as response:
                        session_payload = json.loads(response.read().decode("utf-8"))
                with urlopen(f"http://127.0.0.1:{server.server_port}/api/live-agent-operations?limit=20", timeout=4) as response:
                    operations = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

        rounds_payload.assert_called_once()
        finalize_meeting.assert_called_once_with((root / "meetings" / "resident-m1").resolve())
        self.assertEqual(session_payload["auto_rounds"]["status"], "answered")
        self.assertEqual(session_payload["finalization"]["status"], "finalized")
        self.assertEqual(session_payload["finalization"]["official_event_count"], 1)
        session_operations = [operation for operation in operations["operations"] if operation["operation"] == "session.ensure"]
        self.assertEqual(session_operations[-1]["status"], "success")
        self.assertEqual(session_operations[-1]["details"]["auto_rounds_status"], "answered")
        self.assertEqual(session_operations[-1]["details"]["finalization_status"], "finalized")
        self.assertEqual(session_operations[-1]["details"]["finalization_official_event_count"], 1)
        self.assertEqual(session_operations[-1]["details"]["shared_memory_official_event_count"], 1)
        self.assertEqual(session_operations[-1]["details"]["shared_memory_last_event_id"], "reply-1")
        self.assertEqual(session_operations[-1]["details"]["shared_memory_action_item_count"], 1)
        operations_text = json.dumps(operations["operations"], ensure_ascii=False)
        self.assertNotIn("round_1 instruction", operations_text)

    def test_live_agent_session_finalize_after_rounds_skips_when_rounds_timeout(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            session = {"status": "ready", "meeting_id": "resident-m1"}
            with patch(
                "agentsassemble.gui.live_agent_turn_rounds_payload",
                return_value={
                    "status": "timeout",
                    "meeting_id": "resident-m1",
                    "round_count": 1,
                    "answered_round_count": 0,
                    "timeout_round_count": 1,
                    "results": [{"round_id": "round_1", "status": "timeout"}],
                },
            ):
                result = _attach_session_auto_rounds_if_requested(
                    root,
                    session,
                    {
                        "run_remaining_rounds": True,
                        "finalize_after_rounds": True,
                        "round_timeout_seconds": 1,
                        "round_max_rounds": 1,
                    },
                )

        self.assertEqual(result["auto_rounds"]["status"], "timeout")
        self.assertEqual(result["finalization"]["status"], "skipped")
        self.assertEqual(result["finalization"]["reason"], "rounds_not_ready")

    def test_live_agent_session_ensure_resumes_existing_meeting_when_group_is_missing(self):
        class EnsureSessionSupervisor:
            def __init__(self, output_root: Path) -> None:
                self.output_root = output_root
                self.started = []
                self.groups = []

            def snapshot_groups(self):
                return list(self.groups)

            def list_groups(self):
                return list(self.groups)

            def start_group(self, **kwargs):
                self.started.append(kwargs)
                heartbeat_live_agent(self.output_root, "agent-a", status="online")
                group = {
                    "group_id": kwargs.get("group_id") or "resident-main",
                    "status": "running",
                    "meeting_id": kwargs.get("meeting_id"),
                    "agents": [{"agent_id": "agent-a"}],
                }
                self.groups = [group]
                return group

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            council_config = root / "council.json"
            agent_config = root / "agents.json"
            live_agent_config = root / "live-agents.json"
            _write_single_agent_session_configs(council_config, agent_config, live_agent_config)
            start_live_agent_meeting(
                root,
                council_config_path=council_config,
                agent_config_path=agent_config,
                meeting_id="resident-m1",
            )
            supervisor = EnsureSessionSupervisor(root)
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root, process_supervisor=supervisor))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/live-agent-sessions/ensure",
                    data=json.dumps(
                        {
                            "meeting_id": "resident-m1",
                            "group_id": "resident-main",
                            "live_agent_config_path": str(live_agent_config),
                            "connect_timeout_seconds": 0,
                        }
                    ).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urlopen(request, timeout=4) as response:
                    session_payload = json.loads(response.read().decode("utf-8"))
                with urlopen(f"http://127.0.0.1:{server.server_port}/api/live-agent-operations?limit=20", timeout=4) as response:
                    operations = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

            self.assertEqual(session_payload["status"], "ready")
            self.assertEqual(session_payload["action"], "resume")
            self.assertEqual(supervisor.started[0]["group_id"], "resident-main")
            self.assertEqual(supervisor.started[0]["meeting_id"], "resident-m1")
            session_operations = [operation for operation in operations["operations"] if operation["operation"].startswith("session.")]
            self.assertEqual([operation["operation"] for operation in session_operations], ["session.ensure"])
            self.assertEqual(session_operations[-1]["status"], "success")
            self.assertEqual(session_operations[-1]["details"]["ensure_action"], "resume")

    def test_live_agent_session_ensure_selects_start_restart_and_recover_actions(self):
        class EnsureActionSupervisor:
            def __init__(self, output_root: Path, initial_status: str = "") -> None:
                self.output_root = output_root
                self.groups = []
                self.calls = []
                if initial_status:
                    self.groups = [
                        {
                            "group_id": "resident-main",
                            "status": initial_status,
                            "meeting_id": "resident-m1",
                            "agents": [{"agent_id": "agent-a"}],
                        }
                    ]

            def snapshot_groups(self):
                return list(self.groups)

            def list_groups(self):
                return list(self.groups)

            def start_group(self, **kwargs):
                self.calls.append(("start", kwargs))
                return self._running_group(kwargs.get("meeting_id"), kwargs.get("group_id"))

            def restart_group(self, group_id):
                self.calls.append(("restart", {"group_id": group_id}))
                return self._running_group("resident-m1", group_id)

            def recover_group(self, group_id):
                self.calls.append(("recover", {"group_id": group_id}))
                return self._running_group("resident-m1", group_id)

            def _running_group(self, meeting_id, group_id):
                heartbeat_live_agent(self.output_root, "agent-a", status="online")
                group = {
                    "group_id": group_id or "resident-main",
                    "status": "running",
                    "meeting_id": meeting_id,
                    "agents": [{"agent_id": "agent-a"}],
                }
                self.groups = [group]
                return group

        cases = [
            ("start", "", False, "start"),
            ("resume", "restarting", True, "start"),
            ("restart", "stopped", True, "restart"),
            ("recover", "error", True, "recover"),
            ("recover", "unknown", True, "recover"),
        ]
        for expected_action, initial_status, create_meeting, expected_call in cases:
            with self.subTest(action=expected_action):
                with tempfile.TemporaryDirectory() as temp_dir:
                    root = Path(temp_dir)
                    council_config = root / "council.json"
                    agent_config = root / "agents.json"
                    live_agent_config = root / "live-agents.json"
                    _write_single_agent_session_configs(council_config, agent_config, live_agent_config)
                    if create_meeting:
                        start_live_agent_meeting(
                            root,
                            council_config_path=council_config,
                            agent_config_path=agent_config,
                            meeting_id="resident-m1",
                        )
                    supervisor = EnsureActionSupervisor(root, initial_status)

                    session_payload = live_agent_session_ensure_payload(
                        root,
                        supervisor,
                        {
                            "meeting_id": "resident-m1",
                            "group_id": "resident-main",
                            "council_config_path": str(council_config),
                            "agent_config_path": str(agent_config),
                            "live_agent_config_path": str(live_agent_config),
                            "connect_timeout_seconds": 0,
                        },
                        default_server="http://127.0.0.1:8765",
                    )

                    self.assertEqual(session_payload["status"], "ready")
                    self.assertEqual(session_payload["action"], expected_action)
                    self.assertEqual(supervisor.calls[0][0], expected_call)

    def test_live_agent_session_restart_returns_ready_snapshot_and_records_safe_operation(self):
        class RestartSessionSupervisor:
            def __init__(self, root: Path, config_path: Path) -> None:
                self.root = root
                self.config_path = config_path
                self.stopped = []
                self.restarted = []

            def snapshot_groups(self):
                return [
                    {
                        "group_id": "resident-main",
                        "status": "running",
                        "config_path": str(self.config_path),
                        "server": "http://127.0.0.1:8765",
                        "log_tail": "secret provider output",
                        "agents": [{"agent_id": "agent-a"}],
                    }
                ]

            def stop_group(self, group_id):
                self.stopped.append(group_id)
                return {"group_id": group_id, "status": "stopped", "agents": [{"agent_id": "agent-a"}]}

            def restart_group(self, group_id):
                self.restarted.append(group_id)
                agents = {agent["agent_id"]: agent for agent in read_live_agents(self.root)}
                if agents["agent-a"]["status"] != "offline":
                    raise AssertionError("restart must clear stale presence before starting the group again")
                heartbeat_live_agent(self.root, "agent-a", status="online")
                return {
                    "group_id": group_id,
                    "status": "running",
                    "config_path": str(self.config_path),
                    "server": "http://127.0.0.1:8765",
                    "log_tail": "secret provider output",
                    "agents": [{"agent_id": "agent-a"}],
                }

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            council_config = root / "council.json"
            agent_config = root / "agents.json"
            live_agent_config = root / "live-agents.json"
            _write_single_agent_session_configs(council_config, agent_config, live_agent_config)
            start_live_agent_meeting(
                root,
                council_config_path=council_config,
                agent_config_path=agent_config,
                meeting_id="resident-m1",
            )
            heartbeat_live_agent(root, "agent-a", status="online")
            supervisor = RestartSessionSupervisor(root, live_agent_config)
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root, process_supervisor=supervisor))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/live-agent-sessions/restart",
                    data=json.dumps(
                        {
                            "meeting_id": "resident-m1",
                            "group_id": "resident main",
                            "connect_timeout_seconds": 0,
                        }
                    ).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urlopen(request, timeout=4) as response:
                    session_payload = json.loads(response.read().decode("utf-8"))
                with urlopen(f"http://127.0.0.1:{server.server_port}/api/live-agent-operations?limit=20", timeout=4) as response:
                    operations = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

            self.assertEqual(supervisor.stopped, ["resident-main"])
            self.assertEqual(supervisor.restarted, ["resident-main"])
            self.assertEqual(session_payload["status"], "ready")
            self.assertEqual(session_payload["group_id"], "resident-main")
            self.assertEqual(session_payload["connection"]["connected"], 1)
            session_operations = [operation for operation in operations["operations"] if operation["operation"] == "session.restart"]
            self.assertEqual(session_operations[-1]["status"], "success")
            self.assertEqual(session_operations[-1]["target_id"], "resident-m1")
            self.assertEqual(session_operations[-1]["details"]["connected_agent_count"], 1)
            operation_blob = json.dumps(session_operations, ensure_ascii=False)
            self.assertNotIn(str(live_agent_config), operation_blob)
            self.assertNotIn("/private/live-agents.json", operation_blob)
            self.assertNotIn("secret provider output", operation_blob)

    def test_live_agent_session_restart_auto_runs_remaining_rounds_when_ready(self):
        class RestartSessionSupervisor:
            def __init__(self, root: Path, config_path: Path) -> None:
                self.root = root
                self.config_path = config_path

            def snapshot_groups(self):
                return [
                    {
                        "group_id": "resident-main",
                        "status": "running",
                        "config_path": str(self.config_path),
                        "server": "http://127.0.0.1:8765",
                        "agents": [{"agent_id": "agent-a"}],
                    }
                ]

            def stop_group(self, group_id):
                return {"group_id": group_id, "status": "stopped", "agents": [{"agent_id": "agent-a"}]}

            def restart_group(self, group_id):
                heartbeat_live_agent(self.root, "agent-a", status="online")
                return {"group_id": group_id, "status": "running", "agents": [{"agent_id": "agent-a"}]}

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            council_config = root / "council.json"
            agent_config = root / "agents.json"
            live_agent_config = root / "live-agents.json"
            _write_single_agent_session_configs(council_config, agent_config, live_agent_config)
            start_live_agent_meeting(
                root,
                council_config_path=council_config,
                agent_config_path=agent_config,
                meeting_id="resident-m1",
            )
            heartbeat_live_agent(root, "agent-a", status="online")
            supervisor = RestartSessionSupervisor(root, live_agent_config)
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root, process_supervisor=supervisor))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            auto_rounds = {
                "status": "answered",
                "meeting_id": "resident-m1",
                "round_count": 1,
                "answered_round_count": 1,
                "completed_round_count": 0,
                "timeout_round_count": 0,
                "skipped_round_count": 0,
                "stopped_round_count": 0,
                "stopped": False,
                "timeout_seconds": 8.0,
                "max_rounds": 2,
                "results": [{"round_id": "round_1", "status": "answered", "role_ids": ["architect"]}],
            }
            try:
                with patch("agentsassemble.gui.live_agent_turn_rounds_payload", return_value=auto_rounds) as rounds_payload:
                    request = Request(
                        f"http://127.0.0.1:{server.server_port}/api/live-agent-sessions/restart",
                        data=json.dumps(
                            {
                                "meeting_id": "resident-m1",
                                "group_id": "resident-main",
                                "connect_timeout_seconds": 0,
                                "run_remaining_rounds": True,
                                "round_timeout_seconds": 8,
                                "round_max_rounds": 2,
                                "round_stop_on_timeout": True,
                            }
                        ).encode("utf-8"),
                        headers={"Content-Type": "application/json"},
                        method="POST",
                    )
                    with urlopen(request, timeout=4) as response:
                        session_payload = json.loads(response.read().decode("utf-8"))
                with urlopen(f"http://127.0.0.1:{server.server_port}/api/live-agent-operations?limit=20", timeout=4) as response:
                    operations = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

        rounds_payload.assert_called_once_with(
            root,
            "resident-m1",
            {"timeout_seconds": 8.0, "max_rounds": 2, "stop_on_timeout": True},
        )
        self.assertEqual(session_payload["status"], "ready")
        self.assertEqual(session_payload["auto_rounds"]["status"], "answered")
        session_operations = [operation for operation in operations["operations"] if operation["operation"] == "session.restart"]
        self.assertEqual(session_operations[-1]["status"], "success")
        self.assertEqual(session_operations[-1]["summary"], "restarted resident live-agent session and ran remaining rounds")
        self.assertEqual(session_operations[-1]["details"]["auto_rounds_status"], "answered")
        self.assertEqual(session_operations[-1]["details"]["auto_rounds_round_count"], 1)

    def test_live_agent_session_restart_missing_meeting_returns_safe_error(self):
        class RestartSessionSupervisor:
            def snapshot_groups(self):
                return [{"group_id": "resident-main", "status": "running", "agents": [{"agent_id": "agent-a"}]}]

            def restart_group(self, group_id):
                raise AssertionError("missing meeting must be refused before restart")

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root, process_supervisor=RestartSessionSupervisor()))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/live-agent-sessions/restart",
                    data=json.dumps({"meeting_id": "missing-meeting", "group_id": "resident-main"}).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with self.assertRaises(HTTPError) as raised:
                    urlopen(request, timeout=4)
                body = raised.exception.read().decode("utf-8")
                error_payload = json.loads(body)
                raised.exception.close()
                with urlopen(f"http://127.0.0.1:{server.server_port}/api/live-agent-operations?limit=20", timeout=4) as response:
                    operations = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

            self.assertIn("Meeting missing-meeting was not found", body)
            self.assertEqual(error_payload["details"]["requested_meeting_id"], "missing-meeting")
            self.assertEqual(error_payload["details"]["group_id"], "resident-main")
            session_operations = [operation for operation in operations["operations"] if operation["operation"] == "session.restart"]
            self.assertEqual(session_operations[-1]["status"], "failed")

    def test_live_agent_session_recover_returns_ready_snapshot_and_records_safe_operation(self):
        class RecoverSessionSupervisor:
            def __init__(self, root: Path) -> None:
                self.root = root
                self.recovered = []

            def snapshot_groups(self):
                return [
                    {
                        "group_id": "resident-main",
                        "status": "unknown",
                        "config_path": str(self.root / "live-agents.json"),
                        "server": "http://127.0.0.1:8765",
                        "log_tail": "secret provider output",
                        "agents": [{"agent_id": "agent-a"}],
                    }
                ]

            def recover_group(self, group_id):
                self.recovered.append(group_id)
                agents = {agent["agent_id"]: agent for agent in read_live_agents(self.root)}
                if agents["agent-a"]["status"] != "offline":
                    raise AssertionError("recover must clear stale presence before starting the group again")
                heartbeat_live_agent(self.root, "agent-a", status="online")
                return {
                    "group_id": group_id,
                    "status": "running",
                    "config_path": "/private/live-agents.json",
                    "log_tail": "secret provider output",
                    "agents": [{"agent_id": "agent-a"}],
                    "recovered_from_status": "unknown",
                }

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            council_config = root / "council.json"
            agent_config = root / "agents.json"
            live_agent_config = root / "live-agents.json"
            _write_single_agent_session_configs(council_config, agent_config, live_agent_config)
            start_live_agent_meeting(
                root,
                council_config_path=council_config,
                agent_config_path=agent_config,
                meeting_id="resident-m1",
            )
            heartbeat_live_agent(root, "agent-a", status="online")
            supervisor = RecoverSessionSupervisor(root)
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root, process_supervisor=supervisor))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/live-agent-sessions/recover",
                    data=json.dumps(
                        {
                            "meeting_id": "resident-m1",
                            "group_id": "resident main",
                            "connect_timeout_seconds": 0,
                        }
                    ).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urlopen(request, timeout=4) as response:
                    session_payload = json.loads(response.read().decode("utf-8"))
                with urlopen(f"http://127.0.0.1:{server.server_port}/api/live-agent-operations?limit=20", timeout=4) as response:
                    operations = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

            self.assertEqual(supervisor.recovered, ["resident-main"])
            self.assertEqual(session_payload["status"], "ready")
            self.assertEqual(session_payload["group_id"], "resident-main")
            self.assertEqual(session_payload["connection"]["connected"], 1)
            self.assertEqual(session_payload["offline"]["offline"], 1)
            session_operations = [operation for operation in operations["operations"] if operation["operation"] == "session.recover"]
            self.assertEqual(session_operations[-1]["status"], "success")
            self.assertEqual(session_operations[-1]["target_id"], "resident-m1")
            self.assertEqual(session_operations[-1]["details"]["connected_agent_count"], 1)
            self.assertEqual(session_operations[-1]["details"]["offline_agent_count"], 1)
            operation_blob = json.dumps(session_operations, ensure_ascii=False)
            self.assertNotIn(str(live_agent_config), operation_blob)
            self.assertNotIn("/private/live-agents.json", operation_blob)
            self.assertNotIn("secret provider output", operation_blob)

    def test_live_agent_session_recover_auto_rounds_are_skipped_until_ready(self):
        class SlowRecoverSessionSupervisor:
            def __init__(self, root: Path) -> None:
                self.root = root

            def snapshot_groups(self):
                return [
                    {
                        "group_id": "resident-main",
                        "status": "unknown",
                        "config_path": str(self.root / "live-agents.json"),
                        "server": "http://127.0.0.1:8765",
                        "agents": [{"agent_id": "agent-a"}],
                    }
                ]

            def recover_group(self, group_id):
                return {"group_id": group_id, "status": "running", "agents": [{"agent_id": "agent-a"}]}

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            council_config = root / "council.json"
            agent_config = root / "agents.json"
            live_agent_config = root / "live-agents.json"
            _write_single_agent_session_configs(council_config, agent_config, live_agent_config)
            start_live_agent_meeting(
                root,
                council_config_path=council_config,
                agent_config_path=agent_config,
                meeting_id="resident-m1",
            )
            heartbeat_live_agent(root, "agent-a", status="offline")
            supervisor = SlowRecoverSessionSupervisor(root)
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root, process_supervisor=supervisor))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with patch("agentsassemble.gui.live_agent_turn_rounds_payload") as rounds_payload:
                    request = Request(
                        f"http://127.0.0.1:{server.server_port}/api/live-agent-sessions/recover",
                        data=json.dumps(
                            {
                                "meeting_id": "resident-m1",
                                "group_id": "resident-main",
                                "connect_timeout_seconds": 0,
                                "run_remaining_rounds": True,
                                "round_timeout_seconds": 8,
                                "round_max_rounds": 2,
                                "round_stop_on_timeout": True,
                            }
                        ).encode("utf-8"),
                        headers={"Content-Type": "application/json"},
                        method="POST",
                    )
                    with urlopen(request, timeout=4) as response:
                        session_payload = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

        rounds_payload.assert_not_called()
        self.assertEqual(session_payload["status"], "starting")
        self.assertEqual(session_payload["auto_rounds"]["status"], "skipped")
        self.assertEqual(session_payload["auto_rounds"]["reason"], "session_not_ready")

    def test_live_agent_session_recover_persisted_preflight_failure_records_safe_error_without_roster_reset(self):
        class RecoverSessionSupervisor:
            def __init__(self, config_path: Path) -> None:
                self.config_path = config_path
                self.recovered = []

            def snapshot_groups(self):
                return [
                    {
                        "group_id": "resident-main",
                        "status": "unknown",
                        "config_path": str(self.config_path),
                        "server": "http://127.0.0.1:8765",
                        "log_tail": "secret provider output",
                        "agents": [{"agent_id": "agent-a"}],
                    }
                ]

            def recover_group(self, group_id):
                self.recovered.append(group_id)
                raise AssertionError("recover-session must preflight persisted config before recovery")

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            council_config = root / "council.json"
            agent_config = root / "agents.json"
            live_agent_config = root / "live-agents.json"
            _write_single_agent_session_configs(council_config, agent_config, live_agent_config)
            live_agent_config.write_text(
                json.dumps(
                    {
                        "agents": [
                            {
                                "agent_id": "agent-a",
                                "display_name": "Agent A",
                                "provider_kind": "local_cli",
                                "connection_kind": "local_cli",
                                "command": [sys.executable, "-c", "print('ok')"],
                            },
                            {
                                "agent_id": "agent-a",
                                "display_name": "Agent A Duplicate",
                                "provider_kind": "local_cli",
                                "connection_kind": "local_cli",
                                "command": [sys.executable, "-c", "print('ok')"],
                            },
                        ]
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            start_live_agent_meeting(
                root,
                council_config_path=council_config,
                agent_config_path=agent_config,
                meeting_id="resident-m1",
            )
            heartbeat_live_agent(root, "agent-a", status="online")
            supervisor = RecoverSessionSupervisor(live_agent_config)
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root, process_supervisor=supervisor))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/live-agent-sessions/recover",
                    data=json.dumps({"meeting_id": "resident-m1", "group_id": "resident main"}).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with self.assertRaises(HTTPError) as raised:
                    urlopen(request, timeout=4)
                body = raised.exception.read().decode("utf-8")
                raised.exception.close()
                with urlopen(f"http://127.0.0.1:{server.server_port}/api/live-agent-operations?limit=20", timeout=4) as response:
                    operations = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

            self.assertIn("Duplicate agent ids", body)
            self.assertEqual(supervisor.recovered, [])
            agents = {agent["agent_id"]: agent for agent in read_live_agents(root)}
            self.assertEqual(agents["agent-a"]["status"], "online")
            session_operations = [operation for operation in operations["operations"] if operation["operation"] == "session.recover"]
            self.assertEqual(session_operations[-1]["status"], "failed")
            operation_blob = json.dumps(session_operations, ensure_ascii=False)
            self.assertNotIn(str(live_agent_config), operation_blob)
            self.assertNotIn("secret provider output", operation_blob)

    def test_live_agent_session_recover_missing_meeting_returns_safe_error(self):
        class RecoverSessionSupervisor:
            def snapshot_groups(self):
                return [{"group_id": "resident-main", "status": "unknown", "agents": [{"agent_id": "agent-a"}]}]

            def recover_group(self, group_id):
                raise AssertionError("missing meeting must be refused before recover")

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root, process_supervisor=RecoverSessionSupervisor()))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/live-agent-sessions/recover",
                    data=json.dumps({"meeting_id": "missing-meeting", "group_id": "resident-main"}).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with self.assertRaises(HTTPError) as raised:
                    urlopen(request, timeout=4)
                body = raised.exception.read().decode("utf-8")
                error_payload = json.loads(body)
                raised.exception.close()
                with urlopen(f"http://127.0.0.1:{server.server_port}/api/live-agent-operations?limit=20", timeout=4) as response:
                    operations = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

            self.assertIn("Meeting missing-meeting was not found", body)
            self.assertEqual(error_payload["details"]["requested_meeting_id"], "missing-meeting")
            self.assertEqual(error_payload["details"]["group_id"], "resident-main")
            session_operations = [operation for operation in operations["operations"] if operation["operation"] == "session.recover"]
            self.assertEqual(session_operations[-1]["status"], "failed")

    def test_live_agent_official_turn_round_validates_before_append(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            meeting_dir = root / "meetings" / "m1"
            meeting_dir.mkdir(parents=True)
            write_live_state(
                meeting_dir,
                {
                    "meeting_id": "m1",
                    "roles": [
                        {"id": "architect", "display_name": "Architect"},
                        {"id": "critic", "display_name": "Critic"},
                    ],
                    "meeting_template": {
                        "rounds": [
                            {
                                "id": "round_1",
                                "instruction": "Template instruction",
                                "turn_control": {"selection": "all_roles"},
                            }
                        ]
                    },
                    "agent_bindings": [
                        {"role_id": "architect", "agent_id": "agent-a"},
                        {"role_id": "critic", "agent_id": "agent-b"},
                    ],
                },
            )
            connect_live_agent_payload(root, {"agent_id": "agent-a", "display_name": "Agent A", "meeting_id": "m1"})
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                round_request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/meetings/m1/live-agent-turns/round",
                    data=json.dumps({"round_id": "round_1", "content": "private round instruction"}).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with self.assertRaises(HTTPError) as error:
                    urlopen(round_request, timeout=4)
                error.exception.close()
                with urlopen(f"http://127.0.0.1:{server.server_port}/api/live-agent-operations", timeout=4) as response:
                    operations = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

            self.assertEqual(error.exception.code, 400)
            self.assertEqual(read_live_events(meeting_dir, limit=None), [])
            round_operations = [item for item in operations["operations"] if item["operation"] == "official_turn.round"]
            self.assertEqual(round_operations[0]["status"], "failed")
            self.assertNotIn("private round instruction", json.dumps(operations["operations"], ensure_ascii=False))

    def test_live_agent_review_checkpoint_answers_ready_resident_agents_without_official_record(self):
        class ReadySupervisor:
            def snapshot_groups(self):
                return [
                    {
                        "group_id": "resident-main",
                        "status": "running",
                        "meeting_id": "m1",
                        "agents": [{"agent_id": "agent-a"}, {"agent_id": "agent-b"}],
                    }
                ]

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            meeting_dir = root / "meetings" / "m1"
            meeting_dir.mkdir(parents=True)
            write_live_state(
                meeting_dir,
                {
                    "meeting_id": "m1",
                    "topic": "resident review",
                    "live_status": "running",
                    "roles": [
                        {"id": "architect", "display_name": "Architect"},
                        {"id": "critic", "display_name": "Critic"},
                    ],
                    "agent_bindings": [
                        {"role_id": "architect", "agent_id": "agent-a", "provider_id": "local-cli"},
                        {"role_id": "critic", "agent_id": "agent-b", "provider_id": "local-cli"},
                    ],
                    "provider_configs": {"local-cli": {"kind": "local_cli", "display_name": "Local CLI"}},
                },
            )
            connect_live_agent_payload(
                root,
                {
                    "agent_id": "agent-a",
                    "display_name": "Agent A",
                    "provider_kind": "local_cli",
                    "connection_kind": "local_cli",
                    "meeting_id": "m1",
                },
            )
            connect_live_agent_payload(
                root,
                {
                    "agent_id": "agent-b",
                    "display_name": "Agent B",
                    "provider_kind": "local_cli",
                    "connection_kind": "local_cli",
                    "meeting_id": "m1",
                },
            )

            responder_done = threading.Event()

            def answer_review_requests():
                answered = set()
                deadline = time.time() + 3.0
                while time.time() < deadline and len(answered) < 2:
                    for event in read_live_events(meeting_dir, limit=None):
                        if event.get("kind") != "live_agent_turn_request":
                            continue
                        if event.get("review_checkpoint_id") != "checkpoint-1":
                            continue
                        event_id = str(event.get("id") or "")
                        agent_id = str(event.get("target_agent_id") or "")
                        if not event_id or not agent_id or event_id in answered:
                            continue
                        live_agent_official_turn_payload(
                            root,
                            agent_id,
                            {
                                "meeting_id": "m1",
                                "source_event_id": event_id,
                                "content": f"secret review reply from {agent_id}",
                            },
                        )
                        answered.add(event_id)
                    time.sleep(0.01)
                responder_done.set()

            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root, process_supervisor=ReadySupervisor()))
            server_thread = threading.Thread(target=server.serve_forever, daemon=True)
            response_thread = threading.Thread(target=answer_review_requests, daemon=True)
            server_thread.start()
            response_thread.start()
            try:
                checkpoint_request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/meetings/m1/review-checkpoints",
                    data=json.dumps(
                        {
                            "group_id": "resident main",
                            "checkpoint_id": "checkpoint-1",
                            "content": "secret prompt for reviewers",
                            "timeout_seconds": 2.0,
                        }
                    ).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urlopen(checkpoint_request, timeout=4) as response:
                    checkpoint = json.loads(response.read().decode("utf-8"))
                responder_done.wait(timeout=2)
                with urlopen(f"http://127.0.0.1:{server.server_port}/api/live-agent-operations", timeout=4) as response:
                    operations = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

            self.assertEqual(checkpoint["status"], "answered")
            self.assertEqual(checkpoint["checkpoint_id"], "checkpoint-1")
            self.assertEqual(checkpoint["turn_count"], 2)
            self.assertEqual(checkpoint["answered_count"], 2)
            request_events = [
                event
                for event in read_live_events(meeting_dir, limit=None)
                if event.get("kind") == "live_agent_turn_request" and event.get("review_checkpoint_id") == "checkpoint-1"
            ]
            reply_events = [
                event
                for event in read_live_events(meeting_dir, limit=None)
                if event.get("kind") == "message" and event.get("review_checkpoint_id") == "checkpoint-1"
            ]
            self.assertEqual(len(request_events), 2)
            self.assertEqual(len(reply_events), 2)
            for event in request_events + reply_events:
                self.assertEqual(event["channel"], "review")
                self.assertFalse(event["official_record"])
            transcript = build_meeting_payload(meeting_dir)["artifacts"]["transcript.md"]
            self.assertNotIn("secret prompt for reviewers", transcript)
            self.assertNotIn("secret review reply", transcript)
            checkpoint_markdown = meeting_dir / "review_checkpoints" / "checkpoint-1.md"
            checkpoint_json = meeting_dir / "review_checkpoints" / "checkpoint-1.json"
            self.assertTrue(checkpoint_markdown.exists())
            self.assertTrue(checkpoint_json.exists())
            artifact_text = checkpoint_markdown.read_text(encoding="utf-8")
            artifact_json = json.loads(checkpoint_json.read_text(encoding="utf-8"))
            self.assertIn("secret prompt for reviewers", artifact_text)
            self.assertIn("secret review reply from agent-a", artifact_text)
            self.assertEqual(artifact_json["checkpoint_id"], "checkpoint-1")
            self.assertEqual(artifact_json["status"], "answered")
            self.assertEqual(artifact_json["answered_count"], 2)
            self.assertEqual(artifact_json["results"][0]["request"]["content"], "secret prompt for reviewers")
            self.assertIn("secret review reply", artifact_json["results"][0]["reply"]["content"])
            payload = build_meeting_payload(meeting_dir)
            self.assertIn("checkpoint-1.md", payload["review_checkpoints"])
            self.assertIn("secret review reply from agent-a", payload["review_checkpoints"]["checkpoint-1.md"])
            artifact_events = [
                event
                for event in read_live_events(meeting_dir, limit=None)
                if event.get("artifact_kind") == "review_checkpoint"
            ]
            self.assertEqual(len(artifact_events), 1)
            self.assertEqual(artifact_events[0]["channel"], "review")
            self.assertFalse(artifact_events[0]["official_record"])
            self.assertEqual(artifact_events[0]["artifact_path"], "review_checkpoints/checkpoint-1.md")
            self.assertNotIn("secret prompt for reviewers", json.dumps(artifact_events, ensure_ascii=False))
            self.assertNotIn("secret review reply", json.dumps(artifact_events, ensure_ascii=False))
            checkpoint_operations = [item for item in operations["operations"] if item["operation"] == "review.checkpoint"]
            self.assertEqual(checkpoint_operations[-1]["status"], "success")
            self.assertEqual(checkpoint_operations[-1]["details"]["checkpoint_id"], "checkpoint-1")
            self.assertEqual(checkpoint_operations[-1]["details"]["answered_count"], 2)
            operation_blob = json.dumps(checkpoint_operations, ensure_ascii=False)
            self.assertNotIn("secret prompt for reviewers", operation_blob)
            self.assertNotIn("secret review reply", operation_blob)

    def test_live_agent_review_checkpoint_degrades_without_ready_session_and_omits_prompt_content(self):
        class MissingGroupSupervisor:
            def snapshot_groups(self):
                return []

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            meeting_dir = root / "meetings" / "m1"
            meeting_dir.mkdir(parents=True)
            write_live_state(
                meeting_dir,
                {
                    "meeting_id": "m1",
                    "topic": "resident review",
                    "live_status": "running",
                    "roles": [{"id": "architect", "display_name": "Architect"}],
                    "agent_bindings": [{"role_id": "architect", "agent_id": "agent-a", "provider_id": "local-cli"}],
                    "provider_configs": {"local-cli": {"kind": "local_cli", "display_name": "Local CLI"}},
                },
            )
            connect_live_agent_payload(root, {"agent_id": "agent-a", "display_name": "Agent A", "meeting_id": "m1"})
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root, process_supervisor=MissingGroupSupervisor()))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                checkpoint_request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/meetings/m1/review-checkpoints",
                    data=json.dumps(
                        {
                            "group_id": "resident-main",
                            "checkpoint_id": "checkpoint-1",
                            "content": "secret unavailable review prompt",
                            "timeout_seconds": 0,
                        }
                    ).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urlopen(checkpoint_request, timeout=4) as response:
                    checkpoint = json.loads(response.read().decode("utf-8"))
                with urlopen(f"http://127.0.0.1:{server.server_port}/api/live-agent-operations", timeout=4) as response:
                    operations = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

            self.assertEqual(checkpoint["status"], "degraded")
            self.assertEqual(checkpoint["reason"], "session_not_ready")
            self.assertEqual(read_live_events(meeting_dir, limit=None), [])
            checkpoint_operations = [item for item in operations["operations"] if item["operation"] == "review.checkpoint"]
            self.assertEqual(checkpoint_operations[-1]["status"], "degraded")
            self.assertEqual(checkpoint_operations[-1]["details"]["result_status"], "degraded")
            self.assertEqual(checkpoint_operations[-1]["details"]["reason"], "session_not_ready")
            self.assertNotIn("secret unavailable review prompt", json.dumps(checkpoint_operations, ensure_ascii=False))

    def test_live_agent_review_checkpoint_reply_endpoint_records_review_operation(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            meeting_dir = root / "meetings" / "m1"
            meeting_dir.mkdir(parents=True)
            write_live_state(meeting_dir, {"meeting_id": "m1", "topic": "runtime", "live_status": "running"})
            request_event = append_live_event(
                meeting_dir,
                {
                    "kind": "live_agent_turn_request",
                    "meeting_id": "m1",
                    "target_agent_id": "agent-a",
                    "role_id": "architect",
                    "display_name": "Agent A",
                    "content": "secret review prompt",
                    "channel": "review",
                    "official_record": False,
                    "review_checkpoint_id": "checkpoint-1",
                },
            )
            connect_live_agent_payload(root, {"agent_id": "agent-a", "display_name": "Agent A", "meeting_id": "m1"})
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                reply_request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/live-agents/agent-a/official-turn",
                    data=json.dumps(
                        {
                            "meeting_id": "m1",
                            "source_event_id": request_event["id"],
                            "content": "secret review reply",
                        }
                    ).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urlopen(reply_request, timeout=4) as response:
                    replied = json.loads(response.read().decode("utf-8"))
                with urlopen(f"http://127.0.0.1:{server.server_port}/api/live-agent-operations", timeout=4) as response:
                    operations = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

            self.assertEqual(replied["event"]["channel"], "review")
            self.assertFalse(replied["event"]["official_record"])
            self.assertEqual(replied["event"]["review_checkpoint_id"], "checkpoint-1")
            operation_names = [item["operation"] for item in operations["operations"]]
            self.assertIn("review.reply", operation_names)
            self.assertNotIn("official_turn.reply", operation_names)
            review_operations = [item for item in operations["operations"] if item["operation"] == "review.reply"]
            self.assertEqual(review_operations[-1]["details"]["review_checkpoint_id"], "checkpoint-1")
            operations_text = json.dumps(operations["operations"], ensure_ascii=False)
            self.assertNotIn("secret review prompt", operations_text)
            self.assertNotIn("secret review reply", operations_text)

    def test_live_agent_official_turn_request_rejects_meeting_id_path_traversal(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "room"
            (root / "meetings").mkdir(parents=True)
            outside_meeting = Path(temp_dir) / "escape-target"
            outside_meeting.mkdir(parents=True)
            write_live_state(outside_meeting, {"meeting_id": "../../escape-target", "live_status": "running"})
            connect_live_agent_payload(
                root,
                {
                    "agent_id": "agent-a",
                    "display_name": "Agent A",
                    "meeting_id": "../../escape-target",
                    "engagement_mode": "moderator_called",
                },
            )

            with self.assertRaises(ValueError):
                live_agent_turn_request_payload(root, "../../escape-target", {"agent_id": "agent-a", "content": "escape"})

            self.assertFalse((outside_meeting / "live_events.jsonl").exists())

    def test_live_agent_official_turn_reply_is_idempotent_for_same_request(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            meeting_dir = root / "meetings" / "m1"
            meeting_dir.mkdir(parents=True)
            write_live_state(meeting_dir, {"meeting_id": "m1", "topic": "runtime", "live_status": "running"})
            request_event = append_live_event(
                meeting_dir,
                {
                    "kind": "live_agent_turn_request",
                    "meeting_id": "m1",
                    "target_agent_id": "agent-a",
                    "role_id": "architect",
                    "display_name": "Agent A",
                    "content": "공식 발언 차례",
                },
            )
            connect_live_agent_payload(root, {"agent_id": "agent-a", "display_name": "Agent A", "meeting_id": "m1"})
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                events = []
                for content in ("첫 공식 답변", "중복 공식 답변"):
                    reply_request = Request(
                        f"http://127.0.0.1:{server.server_port}/api/live-agents/agent-a/official-turn",
                        data=json.dumps(
                            {
                                "meeting_id": "m1",
                                "source_event_id": request_event["id"],
                                "content": content,
                            }
                        ).encode("utf-8"),
                        headers={"Content-Type": "application/json"},
                        method="POST",
                    )
                    with urlopen(reply_request, timeout=4) as response:
                        events.append(json.loads(response.read().decode("utf-8"))["event"])
            finally:
                server.shutdown()
                server.server_close()

            self.assertEqual(events[0]["id"], events[1]["id"])
            official_replies = [
                event
                for event in read_live_events(meeting_dir)
                if event.get("kind") == "message" and event.get("source_event_id") == request_event["id"]
            ]
            self.assertEqual(len(official_replies), 1)
            transcript = build_meeting_payload(meeting_dir)["artifacts"]["transcript.md"]
            self.assertEqual(transcript.count("첫 공식 답변"), 1)
            self.assertNotIn("중복 공식 답변", transcript)

    def test_live_agent_finalize_meeting_endpoint_writes_artifacts_and_operation(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            meeting_dir = root / "meetings" / "m1"
            meeting_dir.mkdir(parents=True)
            write_live_state(
                meeting_dir,
                {
                    "meeting_id": "m1",
                    "question": "Finalize resident meeting?",
                    "display_question": "Finalize resident meeting?",
                    "topic": "runtime",
                    "display_topic": "runtime",
                    "meeting_mode": "debate",
                    "moderator": {"enabled": True},
                    "roles": [{"id": "architect", "display_name": "Architect", "lens": "shape"}],
                    "meeting_template": {
                        "display_name": "Resident live",
                        "rounds": [
                            {
                                "id": "round_1",
                                "title": "Round 1",
                                "instruction": "Answer.",
                                "turn_control": {"selection": "all_roles"},
                            }
                        ],
                    },
                    "research_depth": {"name": "resident_live"},
                    "research_steering": {"prompt": None},
                    "memory_context": {"recent_episodes": [], "agent_memories": {}},
                    "memory_input": {"research_summaries": []},
                    "agent_bindings": [{"role_id": "architect", "agent_id": "agent-a", "provider_id": "local-cli"}],
                    "provider_configs": {"local-cli": {"kind": "local_cli", "display_name": "Local CLI"}},
                    "debate_rounds": [],
                    "moderator_synthesis": {},
                    "decision_gate": {},
                    "artifacts": {"agenda": "agenda.md"},
                    "live_status": "running",
                },
            )
            request_event = append_live_event(
                meeting_dir,
                {
                    "kind": "live_agent_turn_request",
                    "meeting_id": "m1",
                    "target_agent_id": "agent-a",
                    "role_id": "architect",
                    "display_name": "Architect",
                    "content": "private prompt",
                    "turn_id": "round_1:0:architect",
                    "turn_index": 0,
                },
            )
            append_live_event(
                meeting_dir,
                {
                    "kind": "message",
                    "meeting_id": "m1",
                    "actor_id": "agent-a",
                    "target_agent_id": "agent-a",
                    "source_event_id": request_event["id"],
                    "role_id": "architect",
                    "display_name": "Architect",
                    "content": "official answer for final artifacts",
                    "turn_id": "round_1:0:architect",
                    "turn_index": 0,
                },
            )
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                finalize_request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/meetings/m1/finalize",
                    data=json.dumps({}).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urlopen(finalize_request, timeout=4) as response:
                    finalized = json.loads(response.read().decode("utf-8"))
                with urlopen(f"http://127.0.0.1:{server.server_port}/api/live-agent-operations", timeout=4) as response:
                    operations = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

            self.assertEqual(finalized["status"], "finalized")
            self.assertEqual(finalized["official_event_count"], 1)
            payload = build_meeting_payload(meeting_dir)
            self.assertIn("official answer for final artifacts", payload["artifacts"]["transcript.md"])
            self.assertNotIn("private prompt", payload["artifacts"]["transcript.md"])
            self.assertEqual(payload["meeting"]["live_status"], "complete")
            self.assertTrue((meeting_dir / "decision.md").exists())
            self.assertEqual(operations["operations"][0]["operation"], "meeting.finalize")
            self.assertEqual(operations["operations"][0]["status"], "success")
            self.assertEqual(operations["operations"][0]["details"]["official_event_count"], 1)
            operations_text = json.dumps(operations["operations"], ensure_ascii=False)
            self.assertNotIn("official answer for final artifacts", operations_text)
            self.assertNotIn("private prompt", operations_text)

    def test_live_agent_finalize_meeting_endpoint_failure_operation_omits_prompt_content(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            meeting_dir = root / "meetings" / "m1"
            meeting_dir.mkdir(parents=True)
            write_live_state(
                meeting_dir,
                {
                    "meeting_id": "m1",
                    "question": "Finalize resident meeting?",
                    "display_question": "Finalize resident meeting?",
                    "topic": "runtime",
                    "display_topic": "runtime",
                    "meeting_mode": "debate",
                    "moderator": {"enabled": True},
                    "roles": [{"id": "architect", "display_name": "Architect", "lens": "shape"}],
                    "meeting_template": {
                        "display_name": "Resident live",
                        "rounds": [
                            {
                                "id": "round_1",
                                "title": "Round 1",
                                "instruction": "Answer.",
                                "turn_control": {"selection": "all_roles"},
                            }
                        ],
                    },
                    "research_depth": {"name": "resident_live"},
                    "research_steering": {"prompt": None},
                    "memory_context": {"recent_episodes": [], "agent_memories": {}},
                    "memory_input": {"research_summaries": []},
                    "agent_bindings": [{"role_id": "architect", "agent_id": "agent-a", "provider_id": "local-cli"}],
                    "provider_configs": {"local-cli": {"kind": "local_cli", "display_name": "Local CLI"}},
                    "debate_rounds": [],
                    "moderator_synthesis": {},
                    "decision_gate": {},
                    "artifacts": {"agenda": "agenda.md"},
                    "live_status": "running",
                },
            )
            request_event = append_live_event(
                meeting_dir,
                {
                    "kind": "live_agent_turn_request",
                    "meeting_id": "m1",
                    "target_agent_id": "agent-a",
                    "role_id": "architect",
                    "display_name": "Architect",
                    "content": "secret pending prompt",
                    "turn_id": "round_1:0:architect",
                    "turn_index": 0,
                },
            )
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                finalize_request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/meetings/m1/finalize",
                    data=json.dumps({}).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with self.assertRaises(HTTPError) as context:
                    urlopen(finalize_request, timeout=4)
                context.exception.read()
                context.exception.close()
                with urlopen(f"http://127.0.0.1:{server.server_port}/api/live-agent-operations", timeout=4) as response:
                    operations = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

            self.assertEqual(operations["operations"][0]["operation"], "meeting.finalize")
            self.assertEqual(operations["operations"][0]["status"], "failed")
            self.assertIn(request_event["id"], operations["operations"][0]["error"])
            operations_text = json.dumps(operations["operations"], ensure_ascii=False)
            self.assertNotIn("secret pending prompt", operations_text)

    def test_live_agent_official_turn_reply_ignores_explicit_nonofficial_same_source_message(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            meeting_dir = root / "meetings" / "m1"
            meeting_dir.mkdir(parents=True)
            write_live_state(meeting_dir, {"meeting_id": "m1", "topic": "runtime", "live_status": "running"})
            request_event = append_live_event(
                meeting_dir,
                {
                    "kind": "live_agent_turn_request",
                    "meeting_id": "m1",
                    "target_agent_id": "agent-a",
                    "content": "공식 발언 차례",
                },
            )
            nonofficial_event = {
                "id": "nonofficial-same-source",
                "kind": "message",
                "meeting_id": "m1",
                "channel": "system",
                "official_record": False,
                "actor_id": "agent-a",
                "source_event_id": request_event["id"],
                "content": "비공식 상태 메모",
            }
            with (meeting_dir / "live_events.jsonl").open("a", encoding="utf-8") as file:
                file.write(json.dumps(nonofficial_event, ensure_ascii=False, sort_keys=True) + "\n")
            connect_live_agent_payload(root, {"agent_id": "agent-a", "display_name": "Agent A", "meeting_id": "m1"})

            result = live_agent_official_turn_payload(
                root,
                "agent-a",
                {
                    "meeting_id": "m1",
                    "source_event_id": request_event["id"],
                    "content": "정식 공식 답변",
                },
            )

            self.assertNotEqual(result["event"]["id"], nonofficial_event["id"])
            self.assertEqual(result["event"]["content"], "정식 공식 답변")
            self.assertEqual(result["event"]["channel"], "official")
            self.assertTrue(result["event"]["official_record"])
            official_replies = [
                event
                for event in read_live_events(meeting_dir, limit=None)
                if event.get("kind") == "message"
                and event.get("channel") == "official"
                and event.get("official_record") is True
                and event.get("source_event_id") == request_event["id"]
            ]
            self.assertEqual(len(official_replies), 1)

    def test_live_agent_official_turn_reply_rejects_meeting_the_agent_is_not_attached_to(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            for meeting_id in ("m1", "m2"):
                meeting_dir = root / "meetings" / meeting_id
                meeting_dir.mkdir(parents=True)
                write_live_state(meeting_dir, {"meeting_id": meeting_id, "topic": "runtime", "live_status": "running"})
            other_request = append_live_event(
                root / "meetings" / "m2",
                {
                    "kind": "live_agent_turn_request",
                    "meeting_id": "m2",
                    "target_agent_id": "agent-a",
                    "content": "다른 회의 요청",
                },
            )
            connect_live_agent_payload(root, {"agent_id": "agent-a", "display_name": "Agent A", "meeting_id": "m1"})
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                reply_request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/live-agents/agent-a/official-turn",
                    data=json.dumps(
                        {
                            "meeting_id": "m2",
                            "source_event_id": other_request["id"],
                            "content": "잘못된 회의 답변",
                        }
                    ).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with self.assertRaises(HTTPError) as context:
                    urlopen(reply_request, timeout=4)
                error = context.exception
                error.read()
                error.close()
            finally:
                server.shutdown()
                server.server_close()

            self.assertEqual(error.code, 400)
            official_replies = [
                event
                for event in read_live_events(root / "meetings" / "m2")
                if event.get("kind") == "message" and event.get("source_event_id") == other_request["id"]
            ]
            self.assertEqual(official_replies, [])

    def test_live_agent_official_turn_reply_finds_request_beyond_latest_live_event_tail(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            meeting_dir = root / "meetings" / "m1"
            meeting_dir.mkdir(parents=True)
            write_live_state(meeting_dir, {"meeting_id": "m1", "topic": "runtime", "live_status": "running"})
            request_event = append_live_event(
                meeting_dir,
                {
                    "kind": "live_agent_turn_request",
                    "meeting_id": "m1",
                    "target_agent_id": "agent-a",
                    "role_id": "architect",
                    "display_name": "Agent A",
                    "content": "오래된 요청",
                },
            )
            for index in range(201):
                append_live_event(meeting_dir, {"kind": "status", "content": f"중간 상태 {index}"})
            connect_live_agent_payload(root, {"agent_id": "agent-a", "display_name": "Agent A", "meeting_id": "m1"})
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                reply_request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/live-agents/agent-a/official-turn",
                    data=json.dumps(
                        {
                            "meeting_id": "m1",
                            "source_event_id": request_event["id"],
                            "content": "늦은 공식 답변",
                        }
                    ).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urlopen(reply_request, timeout=4) as response:
                    replied = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

            self.assertEqual(replied["event"]["source_event_id"], request_event["id"])
            self.assertEqual(replied["event"]["content"], "늦은 공식 답변")

    def test_live_agent_official_turn_reply_finds_existing_reply_beyond_latest_live_event_tail(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            meeting_dir = root / "meetings" / "m1"
            meeting_dir.mkdir(parents=True)
            write_live_state(meeting_dir, {"meeting_id": "m1", "topic": "runtime", "live_status": "running"})
            request_event = append_live_event(
                meeting_dir,
                {
                    "kind": "live_agent_turn_request",
                    "meeting_id": "m1",
                    "target_agent_id": "agent-a",
                    "content": "공식 발언 차례",
                },
            )
            first_reply = append_live_event(
                meeting_dir,
                {
                    "kind": "message",
                    "meeting_id": "m1",
                    "actor_id": "agent-a",
                    "target_agent_id": "agent-a",
                    "source_event_id": request_event["id"],
                    "content": "이미 기록된 공식 답변",
                },
            )
            for index in range(201):
                append_live_event(meeting_dir, {"kind": "status", "content": f"후속 상태 {index}"})
            connect_live_agent_payload(root, {"agent_id": "agent-a", "display_name": "Agent A", "meeting_id": "m1"})
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                reply_request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/live-agents/agent-a/official-turn",
                    data=json.dumps(
                        {
                            "meeting_id": "m1",
                            "source_event_id": request_event["id"],
                            "content": "중복 공식 답변",
                        }
                    ).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urlopen(reply_request, timeout=4) as response:
                    replied = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

            self.assertEqual(replied["event"]["id"], first_reply["id"])
            official_replies = [
                event
                for event in read_live_events(meeting_dir, limit=None)
                if event.get("kind") == "message" and event.get("source_event_id") == request_event["id"]
            ]
            self.assertEqual(len(official_replies), 1)

    def test_live_agent_official_turn_reply_requires_matching_request(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            meeting_dir = root / "meetings" / "m1"
            meeting_dir.mkdir(parents=True)
            write_live_state(meeting_dir, {"meeting_id": "m1", "topic": "runtime", "live_status": "running"})
            other_request = append_live_event(
                meeting_dir,
                {
                    "kind": "live_agent_turn_request",
                    "meeting_id": "m1",
                    "target_agent_id": "agent-b",
                    "content": "다른 에이전트 차례",
                },
            )
            connect_live_agent_payload(root, {"agent_id": "agent-a", "display_name": "Agent A", "meeting_id": "m1"})
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                reply_request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/live-agents/agent-a/official-turn",
                    data=json.dumps(
                        {
                            "meeting_id": "m1",
                            "source_event_id": other_request["id"],
                            "content": "잘못된 답변",
                        }
                    ).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with self.assertRaises(HTTPError) as context:
                    urlopen(reply_request, timeout=4)
                error = context.exception
                error.read()
                error.close()
            finally:
                server.shutdown()
                server.server_close()

            self.assertEqual(error.code, 400)

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

    def test_live_agent_leave_endpoint_marks_offline_and_records_safe_operation(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            connect_live_agent_payload(
                root,
                {
                    "agent_id": "external-reviewer",
                    "display_name": "External Reviewer",
                    "provider_kind": "manual",
                    "connection_kind": "manual",
                    "meeting_id": "resident-m1",
                    "session_id": "secret-session",
                    "last_error": "old error",
                },
            )
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                leave_request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/live-agents/external-reviewer/leave",
                    data=json.dumps(
                        {
                            "last_observed_event_id": "evt1",
                            "last_observed_live_event_id": "live1",
                        }
                    ).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urlopen(leave_request, timeout=4) as response:
                    payload = json.loads(response.read().decode("utf-8"))
                with urlopen(f"http://127.0.0.1:{server.server_port}/api/live-agent-operations", timeout=4) as response:
                    operations = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

        agent = payload["agent"]
        self.assertEqual(agent["status"], "offline")
        self.assertEqual(agent["last_error"], "")
        self.assertEqual(agent["last_observed_event_id"], "evt1")
        self.assertEqual(agent["last_observed_live_event_id"], "live1")
        leave_operations = [item for item in operations["operations"] if item["operation"] == "live_agent.leave"]
        self.assertEqual(len(leave_operations), 1)
        self.assertEqual(leave_operations[0]["status"], "success")
        self.assertEqual(leave_operations[0]["target_id"], "external-reviewer")
        self.assertEqual(leave_operations[0]["details"]["agent_id"], "external-reviewer")
        self.assertEqual(leave_operations[0]["details"]["meeting_id"], "resident-m1")
        self.assertEqual(leave_operations[0]["details"]["previous_status"], "online")
        self.assertFalse((root / "live-agent-runs" / "processes.json").exists())
        serialized = json.dumps(operations, ensure_ascii=False)
        self.assertNotIn("secret-session", serialized)
        self.assertNotIn("old error", serialized)

    def test_live_agent_lobby_message_records_actor_source_and_chain_depth(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            connect_live_agent_payload(
                root,
                {
                    "agent_id": "gemini-cli",
                    "display_name": "Gemini CLI",
                    "last_error": "previous command failed",
                    "last_observed_live_event_id": "live-evt0",
                },
            )
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
            agent = payload["agent"]
            persisted_agent = json.loads((root / "live_agents.json").read_text(encoding="utf-8"))["agents"][0]
        self.assertEqual(event["actor_id"], "gemini-cli")
        self.assertEqual(event["source_event_id"], "evt1")
        self.assertEqual(event["auto_chain_depth"], 1)
        self.assertTrue(event["live_agent_endpoint"])
        self.assertEqual(agent["last_reply_at"], event["created_at"])
        self.assertEqual(agent["last_error"], "")
        self.assertEqual(agent["last_observed_event_id"], "evt1")
        self.assertEqual(agent["last_observed_live_event_id"], "live-evt0")
        self.assertEqual(persisted_agent["last_reply_at"], event["created_at"])
        self.assertEqual(persisted_agent["last_error"], "")
        self.assertEqual(persisted_agent["last_observed_event_id"], "evt1")
        self.assertEqual(persisted_agent["last_observed_live_event_id"], "live-evt0")

    def test_live_agent_lobby_message_is_idempotent_for_same_actor_and_source(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            connect_live_agent_payload(root, {"agent_id": "gemini-cli", "display_name": "Gemini CLI"})
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                request_body = json.dumps(
                    {"message": "자동 반응", "source_event_id": "evt1", "auto_chain_depth": 1}
                ).encode("utf-8")
                first_request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/live-agents/gemini-cli/lobby",
                    data=request_body,
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urlopen(first_request, timeout=4) as response:
                    first = json.loads(response.read().decode("utf-8"))
                second_request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/live-agents/gemini-cli/lobby",
                    data=request_body,
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urlopen(second_request, timeout=4) as response:
                    second = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

            matching_events = [
                event
                for event in read_lobby(root)
                if event.get("actor_id") == "gemini-cli" and event.get("source_event_id") == "evt1"
            ]

        self.assertEqual(first["event"]["id"], second["event"]["id"])
        self.assertEqual(len(matching_events), 1)

    def test_live_agent_lobby_message_idempotency_checks_full_lobby_history(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            connect_live_agent_payload(root, {"agent_id": "gemini-cli", "display_name": "Gemini CLI"})
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                first_request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/live-agents/gemini-cli/lobby",
                    data=json.dumps({"message": "자동 반응", "source_event_id": "evt1"}).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urlopen(first_request, timeout=4) as response:
                    first = json.loads(response.read().decode("utf-8"))
                for index in range(81):
                    append_lobby_event(root, {"name": "human", "message": f"filler {index}"})
                second_request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/live-agents/gemini-cli/lobby",
                    data=json.dumps({"message": "자동 반응", "source_event_id": "evt1"}).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urlopen(second_request, timeout=4) as response:
                    second = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

            matching_events = [
                event
                for event in read_lobby(root, limit=200)
                if event.get("actor_id") == "gemini-cli" and event.get("source_event_id") == "evt1"
            ]

        self.assertEqual(first["event"]["id"], second["event"]["id"])
        self.assertEqual(len(matching_events), 1)

    def test_live_agent_lobby_message_idempotency_is_thread_safe(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            connect_live_agent_payload(root, {"agent_id": "gemini-cli", "display_name": "Gemini CLI"})
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            original_append = append_lobby_event

            def slow_append(*args, **kwargs):
                time.sleep(0.05)
                return original_append(*args, **kwargs)

            responses: list[dict[str, object]] = []
            errors: list[BaseException] = []

            def post_reply() -> None:
                try:
                    request = Request(
                        f"http://127.0.0.1:{server.server_port}/api/live-agents/gemini-cli/lobby",
                        data=json.dumps({"message": "자동 반응", "source_event_id": "evt1"}).encode("utf-8"),
                        headers={"Content-Type": "application/json"},
                        method="POST",
                    )
                    with urlopen(request, timeout=4) as response:
                        responses.append(json.loads(response.read().decode("utf-8")))
                except BaseException as error:
                    errors.append(error)

            try:
                with patch("agentsassemble.gui.append_lobby_event", side_effect=slow_append):
                    threads = [threading.Thread(target=post_reply), threading.Thread(target=post_reply)]
                    for worker in threads:
                        worker.start()
                    for worker in threads:
                        worker.join(timeout=4)
            finally:
                server.shutdown()
                server.server_close()

            matching_events = [
                event
                for event in read_lobby(root)
                if event.get("actor_id") == "gemini-cli" and event.get("source_event_id") == "evt1"
            ]

        self.assertEqual(errors, [])
        self.assertEqual(len(responses), 2)
        self.assertEqual(responses[0]["event"]["id"], responses[1]["event"]["id"])
        self.assertEqual(len(matching_events), 1)

    def test_live_agent_lobby_message_without_source_preserves_existing_cursors(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            connect_live_agent_payload(
                root,
                {
                    "agent_id": "gemini-cli",
                    "display_name": "Gemini CLI",
                    "last_observed_event_id": "evt0",
                    "last_observed_live_event_id": "live-evt0",
                },
            )
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/live-agents/gemini-cli/lobby",
                    data=json.dumps({"message": "수동 메모"}).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urlopen(request, timeout=4) as response:
                    payload = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

            event = payload["event"]
            agent = payload["agent"]
            persisted_agent = json.loads((root / "live_agents.json").read_text(encoding="utf-8"))["agents"][0]

        self.assertEqual(event["source_event_id"], "")
        self.assertEqual(agent["last_reply_at"], event["created_at"])
        self.assertEqual(agent["last_observed_event_id"], "evt0")
        self.assertEqual(agent["last_observed_live_event_id"], "live-evt0")
        self.assertEqual(persisted_agent["last_reply_at"], event["created_at"])
        self.assertEqual(persisted_agent["last_observed_event_id"], "evt0")
        self.assertEqual(persisted_agent["last_observed_live_event_id"], "live-evt0")

    def test_generic_lobby_post_cannot_mark_live_agent_endpoint(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/lobby",
                    data=json.dumps(
                        {
                            "name": "Agent A",
                            "side": "other-agent",
                            "message": "forged",
                            "actor_id": "agent-a",
                            "source_event_id": "probe-source",
                            "live_agent_endpoint": True,
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

            self.assertFalse(payload["event"]["live_agent_endpoint"])

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

    def test_diagnostic_meetings_do_not_become_latest_or_listed(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            normal_dir = root / "meetings" / "normal-live"
            normal_dir.mkdir(parents=True)
            write_live_state(
                normal_dir,
                {
                    "meeting_id": "normal-live",
                    "topic": "Normal live",
                    "question": "Visible?",
                    "roles": [],
                    "debate_rounds": [],
                    "moderator_synthesis": {},
                    "live_status": "running",
                },
            )
            diagnostic_dir = root / "meetings" / "official-round-smoke-diag"
            diagnostic_dir.mkdir(parents=True)
            write_live_state(
                diagnostic_dir,
                {
                    "meeting_id": "official-round-smoke-diag",
                    "topic": "Official round smoke",
                    "question": "Diagnostic?",
                    "roles": [],
                    "debate_rounds": [],
                    "moderator_synthesis": {},
                    "live_status": "running",
                    "diagnostic": True,
                    "diagnostic_kind": "official_round_smoke",
                },
            )
            newer = time.time() + 10
            os.utime(diagnostic_dir / "live_state.json", (newer, newer))
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with urlopen(f"http://127.0.0.1:{server.server_port}/api/meetings", timeout=4) as response:
                    meetings_payload = json.loads(response.read().decode("utf-8"))
                with urlopen(f"http://127.0.0.1:{server.server_port}/api/meetings/latest", timeout=4) as response:
                    latest_payload = json.loads(response.read().decode("utf-8"))
                with urlopen(
                    f"http://127.0.0.1:{server.server_port}/api/meetings/official-round-smoke-diag",
                    timeout=4,
                ) as response:
                    direct_payload = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

            self.assertEqual([meeting["meeting_id"] for meeting in meetings_payload["meetings"]], ["normal-live"])
            self.assertEqual(latest_payload["meeting"]["meeting_id"], "normal-live")
            self.assertEqual(direct_payload["meeting"]["meeting_id"], "official-round-smoke-diag")
            self.assertTrue(direct_payload["meeting"]["diagnostic"])

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
            self.assertEqual(payload["config_path"], "[redacted]")
            self.assertEqual(payload["summary"]["providers"], 1)
            self.assertEqual(payload["providers"][0]["provider_id"], "mock-provider")
            self.assertNotIn(str(config_path), json.dumps(payload, ensure_ascii=False))

    def test_provider_health_endpoint_redacts_sensitive_config_failure_payload(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            missing_config = root / "private" / "agents.secret.json"
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/provider-health",
                    data=json.dumps({"config_path": str(missing_config)}).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urlopen(request, timeout=4) as response:
                    payload = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

            self.assertEqual(payload["status"], "failed")
            self.assertEqual(payload["config_path"], "[redacted]")
            self.assertEqual(payload["checks"][0]["message"], "Config load failed: details redacted.")
            serialized_payload = json.dumps(payload, ensure_ascii=False)
            self.assertNotIn(str(missing_config), serialized_payload)
            self.assertNotIn("agents.secret.json", serialized_payload)

    def test_provider_health_endpoint_redacts_malformed_config_failure_payload(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = root / "agents.json"
            config_path.write_text("{", encoding="utf-8")
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/provider-health",
                    data=json.dumps({"config_path": str(config_path)}).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urlopen(request, timeout=4) as response:
                    payload = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

            self.assertEqual(payload["status"], "failed")
            self.assertEqual(payload["config_path"], "[redacted]")
            self.assertEqual(payload["checks"][0]["message"], "Config load failed: details redacted.")
            serialized_payload = json.dumps(payload, ensure_ascii=False)
            self.assertNotIn(str(config_path), serialized_payload)
            self.assertNotIn("Expecting", serialized_payload)
            self.assertNotIn("line 1", serialized_payload)
            self.assertNotIn("char 0", serialized_payload)

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

    def test_provider_health_endpoint_forwards_api_probe_options(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = root / "agents.json"
            config_path.write_text("{}", encoding="utf-8")
            report = {
                "status": "ok",
                "probe_mode": "api",
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
                            "probe_mode": "api",
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

            self.assertEqual(payload["probe_mode"], "api")
            provider_health.assert_called_once_with(
                config_path,
                probe_mode="api",
                probe_timeout_seconds=0.75,
            )


def _write_health_resident_meeting(root: Path, *, agent_ids: list[str]) -> Path:
    meeting_dir = root / "meetings" / "resident-m1"
    meeting_dir.mkdir(parents=True)
    provider_configs = {
        f"{agent_id}-provider": {"id": f"{agent_id}-provider", "kind": "local_cli"}
        for agent_id in agent_ids
    }
    meeting = {
        "meeting_id": "resident-m1",
        "topic": "resident health",
        "question": "Are residents current?",
        "agent_bindings": [
            {
                "agent_id": agent_id,
                "role_id": agent_id,
                "provider_id": f"{agent_id}-provider",
            }
            for agent_id in agent_ids
        ],
        "provider_configs": provider_configs,
    }
    (meeting_dir / "meeting.json").write_text(json.dumps(meeting, ensure_ascii=False), encoding="utf-8")
    return meeting_dir


def _write_lobby_jsonl_event(root: Path, *, event_id: str, actor_id: str, created_at: str) -> None:
    event = {
        "id": event_id,
        "created_at": created_at,
        "name": "human",
        "side": "other",
        "kind": "message",
        "message": "stale event text must stay out",
        "channel": "lobby",
        "audience": "room",
        "official_record": False,
        "actor_id": actor_id,
        "source_event_id": "",
        "auto_chain_depth": 0,
        "live_agent_endpoint": False,
    }
    with (root / "lobby.jsonl").open("a", encoding="utf-8") as file:
        file.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")


def _write_live_jsonl_event(
    meeting_dir: Path,
    *,
    event_id: str,
    kind: str,
    created_at: str,
    target_agent_id: str = "",
    actor_id: str = "",
    source_event_id: str = "",
    content: str = "",
) -> dict[str, object]:
    event = {
        "id": event_id,
        "created_at": created_at,
        "kind": kind,
        "meeting_id": "resident-m1",
        "channel": "official" if kind == "message" else "live",
        "audience": "room",
        "official_record": True,
        "actor_id": actor_id,
        "target_agent_id": target_agent_id,
        "source_event_id": source_event_id,
        "review_checkpoint_id": "",
        "role_id": "",
        "display_name": "",
        "round": None,
        "turn_id": "",
        "turn_index": None,
        "engagement_mode": "",
        "content": content,
        "position": "",
        "stance_status": None,
        "stance_delta": None,
        "changed_by": [],
        "change_reason": "",
        "remaining_resistance": "",
        "emotion": {},
        "confidence": None,
        "retry_status": None,
        "retry_attempts": None,
    }
    path = meeting_dir / "live_events.jsonl"
    with path.open("a", encoding="utf-8") as file:
        file.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")
    return event


def _write_single_agent_session_configs(council_config: Path, agent_config: Path, live_agent_config: Path) -> None:
    council_config.write_text(
        json.dumps(
            {
                "topic": "resident session",
                "question": "Can a resident session resume?",
                "roles": [
                    {"id": "architect", "display_name": "Architect", "lens": "Architecture", "research_focus": "system"}
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    agent_config.write_text(
        json.dumps(
            {
                "providers": [{"id": "local-cli", "kind": "local_cli", "display_name": "Local CLI"}],
                "permission_profiles": [{"id": "meeting_readonly", "meeting_read": True, "official_turn": True}],
                "agent_bindings": [
                    {
                        "agent_id": "agent-a",
                        "role_id": "architect",
                        "provider_id": "local-cli",
                        "permission_profile_id": "meeting_readonly",
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
                "agents": [
                    {
                        "agent_id": "agent-a",
                        "display_name": "Agent A",
                        "provider_kind": "local_cli",
                        "connection_kind": "local_cli",
                        "command": [sys.executable, "-c", "print('ok')"],
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def _write_three_agent_fake_session_configs(council_config: Path, agent_config: Path, live_agent_config: Path) -> None:
    council_config.write_text(
        json.dumps(
            {
                "topic": "resident fake completion",
                "question": "Can three resident fake agents complete and finalize?",
                "roles": [
                    {"id": "architect", "display_name": "Architect", "lens": "Architecture", "research_focus": "system"},
                    {"id": "critic", "display_name": "Critic", "lens": "Risk", "research_focus": "gaps"},
                    {"id": "operator", "display_name": "Operator", "lens": "Operations", "research_focus": "runbook"},
                ],
                "meeting_template": {
                    "id": "resident_fake_completion",
                    "display_name": "Resident Fake Completion",
                    "rounds": [
                        {
                            "id": "round_1",
                            "title": "Round 1",
                            "instruction": "Reply with one concise fake resident answer.",
                            "turn_control": {"selection": "all_roles"},
                        },
                        {
                            "id": "round_2",
                            "title": "Round 2",
                            "instruction": "Reply again so finalization proves all template rounds are complete.",
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
                "providers": [{"id": "local-cli", "kind": "local_cli", "display_name": "Local CLI"}],
                "permission_profiles": [{"id": "meeting_readonly", "meeting_read": True, "official_turn": True}],
                "agent_bindings": [
                    {
                        "agent_id": "agent-a",
                        "role_id": "architect",
                        "provider_id": "local-cli",
                        "permission_profile_id": "meeting_readonly",
                    },
                    {
                        "agent_id": "agent-b",
                        "role_id": "critic",
                        "provider_id": "local-cli",
                        "permission_profile_id": "meeting_readonly",
                    },
                    {
                        "agent_id": "agent-c",
                        "role_id": "operator",
                        "provider_id": "local-cli",
                        "permission_profile_id": "meeting_readonly",
                    },
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
                        "agent_id": "agent-a",
                        "display_name": "Agent A",
                        "provider_kind": "local_cli",
                        "connection_kind": "local_cli",
                        "engagement_mode": "moderator_called",
                        "command": [sys.executable, "-c", "import sys; sys.stdin.read(); print('agent-a official reply')"],
                        "timeout_seconds": 5,
                    },
                    {
                        "agent_id": "agent-b",
                        "display_name": "Agent B",
                        "provider_kind": "local_cli",
                        "connection_kind": "local_cli",
                        "engagement_mode": "moderator_called",
                        "command": [sys.executable, "-c", "import sys; sys.stdin.read(); print('agent-b official reply')"],
                        "timeout_seconds": 5,
                    },
                    {
                        "agent_id": "agent-c",
                        "display_name": "Agent C",
                        "provider_kind": "local_cli",
                        "connection_kind": "local_cli",
                        "engagement_mode": "moderator_called",
                        "command": [sys.executable, "-c", "import sys; sys.stdin.read(); print('agent-c official reply')"],
                        "timeout_seconds": 5,
                    },
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def _write_fake_codex_executable(path: Path) -> None:
    path.write_text(
        """#!/usr/bin/env python3
import json
import os
import sys
from pathlib import Path


SESSION_IDS = {
    "codex-live-lore": "019e0000-0000-7000-a000-000000000001",
    "codex-live-feats": "019e0000-0000-7000-a000-000000000002",
    "codex-live-skeptic": "019e0000-0000-7000-a000-000000000003",
}


def sandbox_flags(args):
    if "exec" not in args:
        return []
    index = args.index("exec")
    return args[index + 1:index + 4]


def record(payload):
    log_path = os.environ.get("AGENTSASSEMBLE_FAKE_CODEX_LOG")
    if not log_path:
        return
    with open(log_path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, sort_keys=True) + "\\n")


args = sys.argv[1:]
if "--help" in args:
    record({"mode": "help", "sandbox_flags": sandbox_flags(args)})
    print("Usage: codex exec [OPTIONS] resume --help")
    raise SystemExit(0)

try:
    output_path = Path(args[args.index("--output-last-message") + 1])
except (ValueError, IndexError):
    print("missing --output-last-message", file=sys.stderr)
    raise SystemExit(2)

agent_id = output_path.name.removesuffix("-last-message.txt")
mode = "resume" if "resume" in args else "fresh"
if mode == "resume":
    try:
        session_id = args[args.index("--output-last-message") + 2]
    except (ValueError, IndexError):
        session_id = ""
else:
    session_id = SESSION_IDS.get(agent_id, "019e0000-0000-7000-a000-000000000099")

output_path.write_text(f"{agent_id} fake Codex {mode} reply", encoding="utf-8")
record(
    {
        "mode": mode,
        "agent_id": agent_id,
        "session_id": session_id,
        "sandbox_flags": sandbox_flags(args),
    }
)
if mode == "fresh":
    print(json.dumps({"type": "session.started", "session": {"id": session_id}}))
""",
        encoding="utf-8",
    )
    path.chmod(0o755)


if __name__ == "__main__":
    unittest.main()
