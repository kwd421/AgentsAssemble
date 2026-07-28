import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from agentsassemble.providers.grok_acp import GrokAcpRuntime
from agentsassemble.providers.room_portal import (
    RoomPortal,
    VIRTUAL_ROOM_DIRECT_OUTBOX_PREFIX,
    VIRTUAL_ROOM_OUTBOX_PATH,
)


ROOT = Path(__file__).resolve().parents[1]
FAKE_ACP = ROOT / "tests" / "fixtures" / "fake_grok_acp.py"


class GrokAcpRuntimeTests(unittest.TestCase):
    def make_runtime(self, root: Path) -> GrokAcpRuntime:
        return GrokAcpRuntime(
            "grok",
            [sys.executable, "-u", str(FAKE_ACP)],
            cwd=ROOT,
            state_dir=root / "grok-home",
            auth_path=root / "auth.json",
            startup_timeout_seconds=5,
        )

    def test_persistent_structured_session_streams_only_agent_message_chunks(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            runtime = self.make_runtime(Path(temp_dir))
            try:
                first_health = runtime.start()
                first_pid = first_health["pid"]
                first_deltas: list[str] = []
                runtime.send("first turn")
                first = runtime.read_output(timeout_seconds=5, on_delta=first_deltas.append)
                runtime.send("second turn")
                second = runtime.read_output(timeout_seconds=5)
                second_health = runtime.health()
            finally:
                runtime.stop()

        self.assertEqual(first["content"], "remembered first turn")
        self.assertEqual("".join(first_deltas), first["content"])
        self.assertEqual(second["content"], "remembered second turn")
        self.assertEqual(first_pid, second_health["pid"])
        self.assertFalse(first_health["pty"])
        self.assertEqual(first_health["transport"], "acp_stdio")
        self.assertEqual(first["metadata"]["message_source"], "grok_acp")
        self.assertGreaterEqual(second_health["stderr_line_count"], 3000)
        self.assertGreater(second_health["stderr_byte_count"], 0)
        self.assertTrue(second_health["stderr_tail_truncated"])
        self.assertFalse(runtime.health()["running"])
        with self.assertRaises(OSError):
            os.kill(int(first_pid), 0)

    def test_permission_request_is_denied_without_hanging_turn(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            runtime = self.make_runtime(Path(temp_dir))
            try:
                runtime.send("permission")
                output = runtime.read_output(timeout_seconds=5)
                health = runtime.health()
            finally:
                runtime.stop()

        self.assertEqual(output["content"], "permission denied safely")
        self.assertEqual(health["permission_request_count"], 1)
        self.assertEqual(health["permission_denied_count"], 1)

    def test_permission_allows_only_cached_room_outbox_write_during_observation(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            runtime = self.make_runtime(Path(temp_dir))
            runtime.room_portal = Mock()
            runtime._session_id = "session-1"
            runtime._active_room_observation = True
            runtime._remember_tool_permission_context(
                {
                    "method": "session/update",
                    "params": {
                        "sessionId": "session-1",
                        "update": {
                            "sessionUpdate": "tool_call",
                            "toolCallId": "tool-write",
                            "title": "write",
                            "_meta": {
                                "x.ai/tool": {
                                    "name": "write",
                                    "label": "Write",
                                }
                            },
                            "rawInput": {
                                "file_path": VIRTUAL_ROOM_OUTBOX_PATH,
                                "content": "room reply",
                            },
                        },
                    },
                }
            )
            runtime._remember_tool_permission_context(
                {
                    "method": "session/update",
                    "params": {
                        "sessionId": "session-1",
                        "update": {
                            "sessionUpdate": "tool_call_update",
                            "toolCallId": "tool-write",
                            "status": "in_progress",
                            "title": "Write room outbox",
                        },
                    },
                }
            )
            request = {
                "jsonrpc": "2.0",
                "id": "permission-1",
                "method": "session/request_permission",
                "params": {
                    "sessionId": "session-1",
                    "toolCall": {"toolCallId": "tool-write", "title": "write"},
                    "options": [
                        {"optionId": "allow-once", "kind": "allow_once"},
                        {"optionId": "reject-once", "kind": "reject_once"},
                    ],
                },
            }

            with patch.object(runtime, "_send_json") as send_json:
                runtime._respond_to_permission_request(request)

        self.assertEqual(
            send_json.call_args.args[0]["result"],
            {"outcome": {"outcome": "selected", "optionId": "allow-once"}},
        )
        self.assertEqual(runtime._permission_request_count, 1)
        self.assertEqual(runtime._permission_denied_count, 0)
        runtime.room_portal.acp_write_text.assert_called_once_with(
            VIRTUAL_ROOM_OUTBOX_PATH,
            "room reply",
        )

    def test_permission_stages_targeted_room_outbox_write_atomically(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            portal = RoomPortal(root / "portal", participant_id="grok")
            portal.prepare()
            portal.begin_observation("turn-targeted")
            runtime = self.make_runtime(root)
            runtime.room_portal = portal
            runtime._session_id = "session-1"
            runtime._active_room_observation = True
            target_path = f"{VIRTUAL_ROOM_DIRECT_OUTBOX_PREFIX}sonnet.txt"
            request = {
                "jsonrpc": "2.0",
                "id": "permission-targeted",
                "method": "session/request_permission",
                "params": {
                    "sessionId": "session-1",
                    "toolCall": {
                        "toolCallId": "tool-targeted",
                        "title": "write",
                        "rawInput": {
                            "file_path": target_path,
                            "content": "Sonnet, please make the next judgment.",
                        },
                    },
                    "options": [
                        {"optionId": "allow-once", "kind": "allow_once"},
                        {"optionId": "reject-once", "kind": "reject_once"},
                    ],
                },
            }

            with patch.object(runtime, "_send_json"):
                runtime._respond_to_permission_request(request)

            publication = portal.consume_publication_result("turn-targeted")

        self.assertEqual(publication.target_agent_id, "sonnet")
        self.assertEqual(publication.content, "Sonnet, please make the next judgment.")

    def test_outbox_is_not_staged_when_request_has_no_allow_once_option(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            portal = RoomPortal(root / "portal", participant_id="grok")
            portal.prepare()
            portal.begin_observation("turn-1")
            runtime = self.make_runtime(root)
            runtime.room_portal = portal
            runtime._session_id = "session-1"
            runtime._active_room_observation = True
            runtime._respond_to_permission_request(
                {
                    "jsonrpc": "2.0",
                    "id": "permission-without-allow",
                    "method": "session/request_permission",
                    "params": {
                        "sessionId": "session-1",
                        "toolCall": {
                            "toolCallId": "tool-write",
                            "title": "write",
                            "rawInput": {
                                "file_path": VIRTUAL_ROOM_OUTBOX_PATH,
                                "content": "must not be published",
                            },
                        },
                        "options": [
                            {"optionId": "reject-once", "kind": "reject_once"},
                        ],
                    },
                }
            )

            publication = portal.consume_publication_result("turn-1")

        self.assertEqual(publication.content, "")
        self.assertEqual(runtime._permission_denied_count, 1)

    def test_permission_allows_only_bounded_room_roll_during_observation(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            runtime = self.make_runtime(Path(temp_dir))
            runtime.room_portal = object()
            runtime._session_id = "session-1"
            runtime._active_room_observation = True
            requests = []
            for tool_call_id, tool_name, command in (
                ("tool-roll", "run_terminal_command", "agentsassemble-room roll '1d20+4'"),
                ("tool-write", "write", "agentsassemble-room roll d20"),
                ("tool-unbounded", "run_terminal_command", "agentsassemble-room roll 999d9999"),
                ("tool-chained", "run_terminal_command", "agentsassemble-room roll d20 && whoami"),
                (
                    "tool-python",
                    "run_terminal_command",
                    'python3 -c "import random; print(random.randint(1,20))"',
                ),
                (
                    "tool-long-suffix",
                    "run_terminal_command",
                    "agentsassemble-room roll d20" + (" " * 700) + "&& whoami",
                ),
            ):
                runtime._remember_tool_permission_context(
                    {
                        "method": "session/update",
                        "params": {
                            "sessionId": "session-1",
                            "update": {
                                "sessionUpdate": "tool_call",
                                "toolCallId": tool_call_id,
                                "_meta": {
                                    "x.ai/tool": {
                                        "name": tool_name,
                                        "label": "Run Command",
                                    }
                                },
                                "rawInput": {"command": command},
                            },
                        },
                    }
                )
                requests.append(
                    {
                        "jsonrpc": "2.0",
                        "id": f"permission-{tool_call_id}",
                        "method": "session/request_permission",
                        "params": {
                            "sessionId": "session-1",
                            "toolCall": {"toolCallId": tool_call_id},
                            "options": [
                                {"optionId": "allow-once", "kind": "allow_once"},
                                {"optionId": "reject-once", "kind": "reject_once"},
                            ],
                        },
                    }
                )

            with patch.object(runtime, "_send_json") as send_json:
                for request in requests:
                    runtime._respond_to_permission_request(request)

        self.assertEqual(
            [call.args[0]["result"] for call in send_json.call_args_list],
            [
                {"outcome": {"outcome": "selected", "optionId": "allow-once"}},
                {"outcome": {"outcome": "selected", "optionId": "reject-once"}},
                {"outcome": {"outcome": "selected", "optionId": "reject-once"}},
                {"outcome": {"outcome": "selected", "optionId": "reject-once"}},
                {"outcome": {"outcome": "selected", "optionId": "reject-once"}},
                {"outcome": {"outcome": "selected", "optionId": "reject-once"}},
            ],
        )
        self.assertEqual(runtime._permission_request_count, 6)
        self.assertEqual(runtime._permission_denied_count, 5)

    def test_permission_rejects_conflicting_command_updates_for_one_tool(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            runtime = self.make_runtime(Path(temp_dir))
            runtime.room_portal = object()
            runtime._session_id = "session-1"
            runtime._active_room_observation = True
            for command in (
                "agentsassemble-room roll d20 && whoami",
                "agentsassemble-room roll d20",
            ):
                runtime._remember_tool_permission_context(
                    {
                        "method": "session/update",
                        "params": {
                            "sessionId": "session-1",
                            "update": {
                                "sessionUpdate": "tool_call_update",
                                "toolCallId": "tool-conflict",
                                "_meta": {
                                    "x.ai/tool": {
                                        "name": "run_terminal_command",
                                        "label": "Run Command",
                                    }
                                },
                                "rawInput": {"command": command},
                            },
                        },
                    }
                )
            request = {
                "jsonrpc": "2.0",
                "id": "permission-conflict",
                "method": "session/request_permission",
                "params": {
                    "sessionId": "session-1",
                    "toolCall": {"toolCallId": "tool-conflict"},
                    "options": [
                        {"optionId": "allow-once", "kind": "allow_once"},
                        {"optionId": "reject-once", "kind": "reject_once"},
                    ],
                },
            }

            with patch.object(runtime, "_send_json") as send_json:
                runtime._respond_to_permission_request(request)

        self.assertEqual(
            send_json.call_args.args[0]["result"],
            {"outcome": {"outcome": "selected", "optionId": "reject-once"}},
        )
        self.assertEqual(runtime._permission_request_count, 1)
        self.assertEqual(runtime._permission_denied_count, 1)

    def test_room_roll_permission_is_correlated_to_exact_tool_session_and_turn(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            runtime = self.make_runtime(Path(temp_dir))
            runtime.room_portal = object()
            runtime._session_id = "session-1"
            runtime._active_room_observation = True
            exact_tool_id = "r" * 128
            runtime._remember_tool_permission_context(
                {
                    "method": "session/update",
                    "params": {
                        "sessionId": "session-1",
                        "update": {
                            "sessionUpdate": "tool_call",
                            "toolCallId": exact_tool_id,
                            "_meta": {
                                "x.ai/tool": {
                                    "name": "run_terminal_command",
                                    "label": "Run Command",
                                }
                            },
                            "rawInput": {"command": "agentsassemble-room roll d20"},
                        },
                    },
                }
            )

            self.assertTrue(
                runtime._permission_is_room_roll(
                    {
                        "sessionId": "session-1",
                        "toolCallId": exact_tool_id,
                    },
                    {},
                )
            )
            self.assertFalse(
                runtime._permission_is_room_roll(
                    {
                        "sessionId": "session-2",
                        "toolCallId": exact_tool_id,
                    },
                    {},
                )
            )
            self.assertFalse(
                runtime._permission_is_room_roll(
                    {
                        "sessionId": "session-1",
                        "toolCallId": exact_tool_id + "x",
                    },
                    {},
                )
            )
            runtime._active_room_observation = False
            self.assertFalse(
                runtime._permission_is_room_roll(
                    {
                        "sessionId": "session-1",
                        "toolCallId": exact_tool_id,
                    },
                    {},
                )
            )

    def test_permission_ignores_tool_context_from_another_session(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            runtime = self.make_runtime(Path(temp_dir))
            runtime.room_portal = object()
            runtime._session_id = "session-1"
            runtime._active_room_observation = True
            runtime._remember_tool_permission_context(
                {
                    "method": "session/update",
                    "params": {
                        "sessionId": "session-2",
                        "update": {
                            "sessionUpdate": "tool_call",
                            "toolCallId": "shared-tool-id",
                            "_meta": {"x.ai/tool": {"name": "write"}},
                            "rawInput": {
                                "file_path": VIRTUAL_ROOM_OUTBOX_PATH,
                            },
                        },
                    },
                }
            )
            request = {
                "jsonrpc": "2.0",
                "id": "permission-foreign-context",
                "method": "session/request_permission",
                "params": {
                    "sessionId": "session-1",
                    "toolCall": {
                        "toolCallId": "shared-tool-id",
                        "title": "write",
                    },
                    "options": [
                        {"optionId": "allow-once", "kind": "allow_once"},
                        {"optionId": "reject-once", "kind": "reject_once"},
                    ],
                },
            }

            with patch.object(runtime, "_send_json") as send_json:
                runtime._respond_to_permission_request(request)

        self.assertEqual(
            send_json.call_args.args[0]["result"],
            {"outcome": {"outcome": "selected", "optionId": "reject-once"}},
        )
        self.assertEqual(runtime._permission_denied_count, 1)

    def test_permission_rejects_terminal_write_and_non_outbox_target(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            runtime = self.make_runtime(Path(temp_dir))
            runtime.room_portal = object()
            runtime._session_id = "session-1"
            runtime._active_room_observation = True
            requests = []
            for tool_call_id, name, raw_input in (
                (
                    "tool-terminal",
                    "run_terminal_command",
                    {
                        "command": f"printf reply > {VIRTUAL_ROOM_OUTBOX_PATH}",
                        "path": VIRTUAL_ROOM_OUTBOX_PATH,
                    },
                ),
                (
                    "tool-other",
                    "write",
                    {"file_path": "/tmp/not-the-room-outbox.txt"},
                ),
            ):
                runtime._remember_tool_permission_context(
                    {
                        "method": "session/update",
                        "params": {
                            "sessionId": "session-1",
                            "update": {
                                "sessionUpdate": "tool_call",
                                "toolCallId": tool_call_id,
                                "_meta": {"x.ai/tool": {"name": name, "label": name}},
                                "rawInput": raw_input,
                            },
                        },
                    }
                )
                requests.append(
                    {
                        "jsonrpc": "2.0",
                        "id": f"permission-{tool_call_id}",
                        "method": "session/request_permission",
                        "params": {
                            "sessionId": "session-1",
                            "toolCall": {"toolCallId": tool_call_id},
                            "options": [
                                {"optionId": "allow-once", "kind": "allow_once"},
                                {"optionId": "reject-once", "kind": "reject_once"},
                            ],
                        },
                    }
                )

            with patch.object(runtime, "_send_json") as send_json:
                for request in requests:
                    runtime._respond_to_permission_request(request)

        self.assertEqual(
            [call.args[0]["result"] for call in send_json.call_args_list],
            [
                {"outcome": {"outcome": "selected", "optionId": "reject-once"}},
                {"outcome": {"outcome": "selected", "optionId": "reject-once"}},
            ],
        )
        self.assertEqual(runtime._permission_denied_count, 2)

    def test_structured_notifications_emit_thought_and_tool_detail_without_output(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            runtime = self.make_runtime(Path(temp_dir))
            for update in (
                {
                    "sessionUpdate": "agent_thought_chunk",
                    "content": {"type": "text", "text": "Checking "},
                },
                {
                    "sessionUpdate": "agent_thought_chunk",
                    "content": {"type": "text", "text": "the room "},
                },
                {
                    "sessionUpdate": "agent_thought_chunk",
                    "content": {"type": "text", "text": "context."},
                },
                {
                    "sessionUpdate": "tool_call_update",
                    "toolCallId": "tool-command",
                    "status": "in_progress",
                    "title": "Execute command",
                    "_meta": {
                        "x.ai/tool": {
                            "name": "run_terminal_command",
                            "label": "Run Command",
                        }
                    },
                    "rawInput": {"command": "pwd"},
                    "rawOutput": {"content": "must stay private"},
                },
                {
                    "sessionUpdate": "tool_call_update",
                    "toolCallId": "tool-command",
                    "status": "in_progress",
                    "title": "Execute command",
                    "_meta": {
                        "x.ai/tool": {
                            "name": "run_terminal_command",
                            "label": "Run Command",
                        }
                    },
                    "rawInput": {"command": "pwd"},
                    "rawOutput": {"content": "must stay private"},
                },
            ):
                runtime._notifications.put(
                    {
                        "method": "session/update",
                        "params": {
                            "sessionId": "session-1",
                            "update": update,
                        },
                    }
                )
            activities = []

            runtime._consume_notifications(
                "session-1",
                [],
                on_delta=None,
                on_activity=activities.append,
            )

        self.assertEqual(
            activities,
            [
                {
                    "category": "reasoning",
                    "status": "running",
                    "content": "Checking",
                },
                {
                    "category": "reasoning",
                    "status": "running",
                    "content": "Checking the room context.",
                },
                {
                    "category": "command",
                    "status": "running",
                    "content": "Run Command: pwd",
                },
            ],
        )
        self.assertNotIn("must stay private", str(activities))

    def test_always_approve_session_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            runtime = self.make_runtime(Path(temp_dir))
            try:
                runtime.send("unsafe-yolo")
                with self.assertRaisesRegex(RuntimeError, "always-approve mode is active"):
                    runtime.read_output(timeout_seconds=5)
            finally:
                runtime.stop()

    def test_always_approve_notification_queued_at_start_is_not_discarded(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            runtime = GrokAcpRuntime(
                "grok",
                [sys.executable, "-u", str(FAKE_ACP)],
                cwd=ROOT,
                state_dir=root / "grok-home",
                auth_path=root / "auth.json",
                env={"FAKE_GROK_ACP_YOLO_ON_START": "1"},
                startup_timeout_seconds=5,
            )
            try:
                runtime.start()
                with self.assertRaisesRegex(RuntimeError, "always-approve mode is active"):
                    runtime.send("must not be delivered")
            finally:
                runtime.stop()

    def test_empty_completed_turn_fails_without_a_second_provider_prompt(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            runtime = self.make_runtime(Path(temp_dir))
            try:
                with patch.object(runtime, "_begin_request", wraps=runtime._begin_request) as begin_request:
                    runtime.send("empty-turn")
                    with self.assertRaisesRegex(RuntimeError, "without a room-visible assistant message") as raised:
                        runtime.read_output(timeout_seconds=5)
            finally:
                runtime.stop()

        prompt_calls = [call for call in begin_request.call_args_list if call.args[0] == "session/prompt"]
        self.assertEqual(len(prompt_calls), 1)
        self.assertEqual(getattr(raised.exception, "code", ""), "empty_provider_final")

    def test_empty_room_observation_is_a_decline_not_a_session_error(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            runtime = self.make_runtime(Path(temp_dir))
            try:
                runtime.send_room_observation("empty-turn")
                output = runtime.read_output(timeout_seconds=5)
                health = runtime.health()
            finally:
                runtime.stop()

        self.assertEqual(output["outcome"], "decline")
        self.assertEqual(output["reason_code"], "nothing_useful_to_add")
        self.assertTrue(health["running"])

    def test_provider_quota_error_is_classified_from_drained_stderr(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            runtime = self.make_runtime(Path(temp_dir))
            try:
                runtime.send("quota")
                with self.assertRaisesRegex(RuntimeError, "usage balance is exhausted"):
                    runtime.read_output(timeout_seconds=5)
                health = runtime.health()
            finally:
                runtime.stop()

        self.assertIn("usage balance is exhausted", health["last_error"])
        self.assertGreater(health["stderr_byte_count"], 64_000)

    def test_stderr_tail_storage_is_bounded_by_line_and_character_limits(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            runtime = self.make_runtime(Path(temp_dir))
            for index in range(150):
                line = f"WARN-{index}-" + ("x" * 1000)
                runtime._record_stderr_line(line, byte_count=len(line.encode()))
            health = runtime.health()

        self.assertEqual(health["stderr_line_count"], 150)
        self.assertEqual(health["stderr_warning_count"], 150)
        self.assertTrue(health["stderr_tail_truncated"])
        self.assertLessEqual(len(health["stderr_tail"]), 16_000)
        self.assertIn("WARN-149", health["stderr_tail"])

    def test_structured_output_queue_loss_fails_instead_of_returning_truncated_text(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            runtime = GrokAcpRuntime(
                "grok",
                [sys.executable, "-u", str(FAKE_ACP)],
                cwd=ROOT,
                state_dir=root / "grok-home",
                auth_path=root / "auth.json",
                notification_queue_size=2,
                startup_timeout_seconds=5,
            )
            try:
                runtime.send("overflow")
                time.sleep(0.05)
                with self.assertRaisesRegex(RuntimeError, "output backpressure dropped"):
                    runtime.read_output(timeout_seconds=5)
                health = runtime.health()
            finally:
                runtime.stop()

        self.assertGreater(health["notification_drop_count"], 0)

    def test_same_runtime_restarts_after_process_exit_without_stale_eof(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            runtime = self.make_runtime(Path(temp_dir))
            try:
                first_pid = runtime.start()["pid"]
                runtime.send("exit-mid-turn")
                with self.assertRaisesRegex(RuntimeError, "exited before turn completion"):
                    runtime.read_output(timeout_seconds=5)

                runtime.send("after-restart")
                restarted = runtime.read_output(timeout_seconds=5)
                second_health = runtime.health()
            finally:
                runtime.stop()

        self.assertNotEqual(first_pid, second_health["pid"])
        self.assertTrue(second_health["provider_session_reused"])
        self.assertEqual(restarted["content"], "remembered after-restart")

    def test_restart_loads_the_same_provider_session_and_preserves_memory(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            first_runtime = self.make_runtime(root)
            try:
                first_health = first_runtime.start()
                first_runtime.send("restart-marker-413")
                first_runtime.read_output(timeout_seconds=5)
            finally:
                first_runtime.stop()

            second_runtime = self.make_runtime(root)
            try:
                second_health = second_runtime.start()
                second_runtime.send("recall-after-restart")
                recalled = second_runtime.read_output(timeout_seconds=5)
                state_mode = (root / "grok-home" / "agentsassemble-session.json").stat().st_mode & 0o777
            finally:
                second_runtime.stop()

        self.assertFalse(first_health["provider_session_reused"])
        self.assertTrue(second_health["provider_session_load_supported"])
        self.assertTrue(second_health["provider_session_reused"])
        self.assertFalse(second_health["provider_session_resume_failed"])
        self.assertEqual(recalled["content"], "recalled restart-marker-413")
        self.assertEqual(state_mode, 0o600)

    def test_failed_session_load_starts_fresh_and_reports_recovery_loss(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            first_runtime = self.make_runtime(root)
            try:
                first_runtime.start()
            finally:
                first_runtime.stop()

            second_runtime = GrokAcpRuntime(
                "grok",
                [sys.executable, "-u", str(FAKE_ACP)],
                cwd=ROOT,
                state_dir=root / "grok-home",
                auth_path=root / "auth.json",
                env={"FAKE_GROK_ACP_LOAD_FAIL": "1"},
                startup_timeout_seconds=5,
            )
            try:
                health = second_runtime.start()
            finally:
                second_runtime.stop()

        self.assertFalse(health["provider_session_reused"])
        self.assertTrue(health["provider_session_resume_failed"])
        self.assertIn("stored session is unavailable", health["provider_session_resume_error"])
        self.assertEqual(health["last_error"], "")


if __name__ == "__main__":
    unittest.main()
