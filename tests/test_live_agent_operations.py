import json
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

import agentsassemble.live_agent_operations as live_agent_operations
from agentsassemble.live_agent_operations import append_live_agent_operation, read_live_agent_operations


class LiveAgentOperationTests(unittest.TestCase):
    def test_append_and_read_operations_are_bounded_and_safe(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            operation = append_live_agent_operation(
                root,
                operation="process.start",
                status="success",
                target_id="crew",
                summary=(
                    "started with literal:secret and sk-test-secret from configs/live-agents.local.json "
                    "auth: Bearer abc123XYZ"
                ),
                details={
                    "group_id": "crew",
                    "group_alias": "https://friend.example/run?token=secret",
                    "workspace": "/Users/me/private/live-agents.json",
                    "windows_config": "C:\\Users\\me\\private\\live-agents.json",
                    "relative_config": "configs/live-agents.local.json",
                    "provider_key": "sk-test-secret",
                    "literal_ref": "literal:secret",
                    "auth_header": "auth: Bearer abc123XYZ",
                    "header_value": "auth: Bearer abc123XYZ",
                    "auto_restart": True,
                    "max_restarts": 2,
                    "probe_agent_ids": ["agent-a", "agent-b"],
                    "probe_statuses": ["agent-a:ok", "agent-b:timeout"],
                    "server": "http://127.0.0.1:8765",
                    "config_path": "/Users/me/secret/live-agents.json",
                    "auth_ref": "env:SECRET_TOKEN",
                    "command": ["claude", "--danger"],
                    "prompt": "private room prompt",
                    "log_tail": "provider stderr with token",
                },
                error=(
                    "Live agent preflight failed: bad-agent command: "
                    "Command not found: definitely-missing-agentsassemble-cli; "
                    "auth: Bearer abc123XYZ"
                ),
                now=datetime(2026, 5, 18, 1, 2, 3, tzinfo=UTC),
            )

            operations = read_live_agent_operations(root, limit=5)
            persisted = (root / "live-agent-runs" / "operations.jsonl").read_text(encoding="utf-8")

        self.assertEqual(operation["operation"], "process.start")
        self.assertEqual(operation["status"], "success")
        self.assertEqual(operation["target_id"], "crew")
        self.assertEqual(operation["timestamp"], "2026-05-18T01:02:03+00:00")
        self.assertEqual(operations, [operation])
        self.assertEqual(operations[0]["details"]["group_id"], "crew")
        self.assertNotIn("literal:secret", operations[0]["summary"])
        self.assertNotIn("sk-test-secret", operations[0]["summary"])
        self.assertNotIn("configs/live-agents.local.json", operations[0]["summary"])
        self.assertNotIn("abc123XYZ", operations[0]["summary"])
        self.assertNotIn("https://friend.example", operations[0]["details"]["group_alias"])
        self.assertNotIn("/Users/me/private", operations[0]["details"]["workspace"])
        self.assertNotIn("C:\\Users\\me\\private", str(operations[0]["details"].get("windows_config", "")))
        self.assertNotIn("configs/live-agents.local.json", str(operations[0]["details"].get("relative_config", "")))
        self.assertNotIn("sk-test-secret", str(operations[0]["details"].get("provider_key", "")))
        self.assertNotIn("literal:secret", str(operations[0]["details"].get("literal_ref", "")))
        self.assertNotIn("Bearer abc123XYZ", str(operations[0]["details"].get("auth_header", "")))
        self.assertNotIn("abc123XYZ", str(operations[0]["details"].get("auth_header", "")))
        self.assertNotIn("abc123XYZ", str(operations[0]["details"].get("header_value", "")))
        self.assertTrue(operations[0]["details"]["auto_restart"])
        self.assertEqual(operations[0]["details"]["max_restarts"], 2)
        self.assertEqual(operations[0]["details"]["probe_agent_ids"], ["agent-a", "agent-b"])
        self.assertEqual(operations[0]["details"]["probe_statuses"], ["agent-a:ok", "agent-b:timeout"])
        self.assertNotIn("server", operations[0]["details"])
        self.assertNotIn("config_path", operations[0]["details"])
        self.assertNotIn("auth_ref", operations[0]["details"])
        self.assertNotIn("command", operations[0]["details"])
        self.assertNotIn("prompt", operations[0]["details"])
        self.assertNotIn("log_tail", operations[0]["details"])
        self.assertIn("Live agent preflight failed", operations[0]["error"])
        self.assertNotIn("bad-agent command", operations[0]["error"])
        self.assertNotIn("definitely-missing-agentsassemble-cli", operations[0]["error"])
        self.assertNotIn("SECRET_TOKEN", persisted)
        self.assertNotIn("claude", persisted)
        self.assertNotIn("private room prompt", persisted)
        self.assertNotIn("https://friend.example", persisted)
        self.assertNotIn("/Users/me/private", persisted)
        self.assertNotIn("C:\\Users\\me\\private", persisted)
        self.assertNotIn("configs/live-agents.local.json", persisted)
        self.assertNotIn("sk-test-secret", persisted)
        self.assertNotIn("literal:secret", persisted)
        self.assertNotIn("Bearer abc123XYZ", persisted)
        self.assertNotIn("abc123XYZ", persisted)
        self.assertNotIn("definitely-missing-agentsassemble-cli", persisted)

    def test_read_operations_ignores_corrupt_lines_and_returns_recent_limit(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            path = root / "live-agent-runs" / "operations.jsonl"
            path.parent.mkdir(parents=True)
            path.write_text("{not json}\n", encoding="utf-8")
            for index in range(3):
                append_live_agent_operation(
                    root,
                    operation="engagement.update",
                    status="success",
                    target_id=f"agent-{index}",
                    details={"engagement_mode": "watch", "index": index},
                    now=datetime(2026, 5, 18, 1, 2, index, tzinfo=UTC),
                )

            operations = read_live_agent_operations(root, limit=2)

        self.assertEqual([operation["target_id"] for operation in operations], ["agent-1", "agent-2"])
        self.assertEqual([operation["details"]["index"] for operation in operations], [1, 2])

    def test_readiness_health_details_preserve_safe_operational_words(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            operation = append_live_agent_operation(
                root,
                operation="readiness.check",
                status="degraded",
                target_id="doctor-smoke",
                details={
                    "health_process_attention": [
                        "missing-config-group",
                        "/private/live-agents.json",
                        "env:SECRET_TOKEN",
                    ],
                    "health_process_reasons": [
                        "missing-config-group restart_failed missing launch config",
                        "/private/live-agents.json restart_failed env:SECRET_TOKEN",
                        "auth-group restart_failed Authorization Bearer abc123",
                        "bearer-group restart_failed Bearer abc123",
                        "password-group restart_failed password hunter2",
                        "api-group restart_failed --api-key abc123",
                    ],
                },
                now=datetime(2026, 5, 18, 1, 2, 3, tzinfo=UTC),
            )

            operations = read_live_agent_operations(root)
            persisted = (root / "live-agent-runs" / "operations.jsonl").read_text(encoding="utf-8")

        self.assertEqual(operations, [operation])
        self.assertEqual(
            operations[0]["details"]["health_process_attention"][0],
            "missing-config-group",
        )
        self.assertEqual(
            operations[0]["details"]["health_process_reasons"][0],
            "missing-config-group restart_failed missing launch config",
        )
        operation_blob = json.dumps(operations[0], ensure_ascii=False)
        self.assertNotIn("/private/live-agents.json", operation_blob)
        self.assertNotIn("SECRET_TOKEN", operation_blob)
        self.assertNotIn("Bearer abc123", operation_blob)
        self.assertNotIn("password hunter2", operation_blob)
        self.assertNotIn("--api-key abc123", operation_blob)
        self.assertNotIn("/private/live-agents.json", persisted)
        self.assertNotIn("SECRET_TOKEN", persisted)
        self.assertNotIn("Bearer abc123", persisted)
        self.assertNotIn("password hunter2", persisted)
        self.assertNotIn("--api-key abc123", persisted)

    def test_read_operations_does_not_load_whole_history_file_at_once(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            for index in range(5):
                append_live_agent_operation(
                    root,
                    operation="engagement.update",
                    status="success",
                    target_id=f"agent-{index}",
                    details={"index": index},
                    now=datetime(2026, 5, 18, 1, 2, index, tzinfo=UTC),
                )

            original_open = Path.open

            class BoundedReadFile:
                def __init__(self, wrapped):
                    self.wrapped = wrapped

                def __enter__(self):
                    self.wrapped.__enter__()
                    return self

                def __exit__(self, exc_type, exc, traceback):
                    return self.wrapped.__exit__(exc_type, exc, traceback)

                def __getattr__(self, name):
                    return getattr(self.wrapped, name)

                def read(self, size=-1):
                    if size is None or size < 0:
                        raise AssertionError("unbounded operation read")
                    return self.wrapped.read(size)

            def open_guard(path, *args, **kwargs):
                mode = str(args[0] if args else kwargs.get("mode", "r"))
                opened = original_open(path, *args, **kwargs)
                if path.name == "operations.jsonl" and "r" in mode:
                    return BoundedReadFile(opened)
                return opened

            with patch.object(Path, "open", open_guard):
                operations = read_live_agent_operations(root, limit=2)

        self.assertEqual([operation["target_id"] for operation in operations], ["agent-3", "agent-4"])

    def test_read_operations_stops_after_recent_tail_records(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            path = root / "live-agent-runs" / "operations.jsonl"
            path.parent.mkdir(parents=True)
            path.write_text(
                json.dumps(
                    {
                        "id": "old-sentinel",
                        "timestamp": "2026-05-18T00:00:00+00:00",
                        "operation": "explode-old",
                        "status": "success",
                        "target_id": "old",
                        "summary": "",
                        "details": {},
                    },
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )
            for index in range(4):
                append_live_agent_operation(
                    root,
                    operation="engagement.update",
                    status="success",
                    target_id=f"agent-{index}",
                    details={"index": index},
                    now=datetime(2026, 5, 18, 1, 2, index, tzinfo=UTC),
                )

            original_loads = live_agent_operations.json.loads

            def guarded_loads(line):
                if "explode-old" in line:
                    raise AssertionError("old operation was parsed")
                return original_loads(line)

            with patch.object(live_agent_operations.json, "loads", side_effect=guarded_loads):
                operations = read_live_agent_operations(root, limit=2)

        self.assertEqual([operation["target_id"] for operation in operations], ["agent-2", "agent-3"])

    def test_read_operations_ignores_invalid_utf8_file(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            path = root / "live-agent-runs" / "operations.jsonl"
            path.parent.mkdir(parents=True)
            path.write_bytes(b"\xff")

            operations = read_live_agent_operations(root)

        self.assertEqual(operations, [])

    def test_read_operations_sanitizes_legacy_sensitive_details(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            path = root / "live-agent-runs" / "operations.jsonl"
            path.parent.mkdir(parents=True)
            path.write_text(
                json.dumps(
                    {
                        "id": "legacy",
                        "timestamp": "2026-05-18T01:02:03+00:00",
                        "operation": "process.start",
                        "status": "success",
                        "target_id": "https://friend.example/run?token=secret",
                        "summary": "legacy from env:SECRET_TOKEN",
                        "error": "No such file or directory: '/Users/me/private/live-agents.json'",
                        "details": {
                            "group_id": "crew",
                            "group_alias": "https://friend.example/run?token=secret",
                            "endpoint_url": "https://friend.example/run?token=secret",
                            "auth_token": "secret",
                        },
                    },
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )

            operations = read_live_agent_operations(root)

        self.assertEqual(set(operations[0]["details"]), {"group_id", "group_alias"})
        self.assertNotIn("https://friend.example", operations[0]["target_id"])
        self.assertNotIn("SECRET_TOKEN", operations[0]["summary"])
        self.assertNotIn("https://friend.example", operations[0]["details"]["group_alias"])
        self.assertNotIn("/Users/me/private", operations[0]["error"])
        self.assertNotIn("SECRET_TOKEN", operations[0]["error"])
