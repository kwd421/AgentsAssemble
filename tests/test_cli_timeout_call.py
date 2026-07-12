import unittest
import tempfile
from pathlib import Path
from io import StringIO
from unittest.mock import patch

from agentsassemble.cli import build_parser, main


class CliTimeoutCallTests(unittest.TestCase):

    def test_live_agent_call_parses_operator_options(self):
        args = build_parser().parse_args(
            [
                "live-agent",
                "call",
                "--server",
                "http://room.local",
                "--meeting-id",
                "m1",
                "--agent-id",
                "agent-a",
                "--role-id",
                "architect",
                "--display-name",
                "Agent A",
                "--turn-id",
                "round_1:0:architect",
                "--turn-index",
                "0",
                "--json",
                "공식",
                "발언",
                "요청",
            ]
        )

        self.assertEqual(args.live_agent_command, "call")
        self.assertEqual(args.server, "http://room.local")
        self.assertEqual(args.meeting_id, "m1")
        self.assertEqual(args.agent_id, "agent-a")
        self.assertEqual(args.role_id, "architect")
        self.assertEqual(args.display_name, "Agent A")
        self.assertEqual(args.turn_id, "round_1:0:architect")
        self.assertEqual(args.turn_index, 0)
        self.assertEqual(args.message, ["공식", "발언", "요청"])
        self.assertTrue(args.as_json)
        self.assertFalse(args.wait)
        self.assertEqual(args.timeout, 30.0)

    def test_live_agent_call_parses_wait_options(self):
        args = build_parser().parse_args(
            [
                "live-agent",
                "call",
                "--server",
                "http://room.local",
                "--meeting-id",
                "m1",
                "--agent-id",
                "agent-a",
                "--wait",
                "--timeout",
                "8",
                "공식",
                "발언",
                "요청",
            ]
        )

        self.assertTrue(args.wait)
        self.assertEqual(args.timeout, 8.0)

    def test_live_agent_call_posts_turn_request_and_prints_summary(self):
        response = {"event": {"id": "turn-request-1", "target_agent_id": "agent-a", "meeting_id": "m1"}}
        stdout = StringIO()
        with patch("agentsassemble.cli._request_json", return_value=response) as request_json:
            with patch("sys.stdout", stdout):
                exit_code = main(
                    [
                        "live-agent",
                        "call",
                        "--server",
                        "http://room.local",
                        "--meeting-id",
                        "m1",
                        "--agent-id",
                        "agent-a",
                        "--role-id",
                        "architect",
                        "공식",
                        "발언",
                        "요청",
                    ]
                )

        self.assertEqual(exit_code, 0)
        request_json.assert_called_once_with(
            "http://room.local/api/meetings/m1/live-agent-turns/request",
            method="POST",
            payload={
                "agent_id": "agent-a",
                "role_id": "architect",
                "display_name": "",
                "content": "공식 발언 요청",
                "turn_id": "",
                "turn_index": None,
            },
        )
        self.assertIn("Called agent-a for official turn turn-request-1", stdout.getvalue())

    def test_live_agent_call_waits_for_answered_turn_and_prints_summary(self):
        response = {
            "status": "answered",
            "request_event": {"id": "turn-request-1", "target_agent_id": "agent-a", "meeting_id": "m1"},
            "reply_event": {"id": "reply-1", "actor_id": "agent-a"},
        }
        stdout = StringIO()
        with patch("agentsassemble.cli._request_json", return_value=response) as request_json:
            with patch("sys.stdout", stdout):
                exit_code = main(
                    [
                        "live-agent",
                        "call",
                        "--server",
                        "http://room.local",
                        "--meeting-id",
                        "m1",
                        "--agent-id",
                        "agent-a",
                        "--role-id",
                        "architect",
                        "--wait",
                        "--timeout",
                        "8",
                        "공식",
                        "발언",
                        "요청",
                    ]
                )

        self.assertEqual(exit_code, 0)
        request_json.assert_called_once_with(
            "http://room.local/api/meetings/m1/live-agent-turns/call",
            method="POST",
            payload={
                "agent_id": "agent-a",
                "role_id": "architect",
                "display_name": "",
                "content": "공식 발언 요청",
                "turn_id": "",
                "turn_index": None,
                "timeout_seconds": 8.0,
            },
            timeout_seconds=14.0,
        )
        self.assertIn("Answered agent-a official turn reply-1", stdout.getvalue())

    def test_live_agent_call_wait_returns_one_on_timeout(self):
        response = {
            "status": "timeout",
            "request_event": {"id": "turn-request-1", "target_agent_id": "agent-a"},
            "reply_event": None,
        }
        stdout = StringIO()
        with patch("agentsassemble.cli._request_json", return_value=response):
            with patch("sys.stdout", stdout):
                exit_code = main(
                    [
                        "live-agent",
                        "call",
                        "--server",
                        "http://room.local",
                        "--meeting-id",
                        "m1",
                        "--agent-id",
                        "agent-a",
                        "--wait",
                        "--timeout",
                        "0",
                        "공식",
                        "발언",
                        "요청",
                    ]
                )

        self.assertEqual(exit_code, 1)
        self.assertIn("Timed out waiting for agent-a official turn turn-request-1", stdout.getvalue())

    def test_live_agent_call_sequence_parses_operator_options(self):
        args = build_parser().parse_args(
            [
                "live-agent",
                "call-sequence",
                "--server",
                "http://room.local",
                "--meeting-id",
                "m1",
                "--turns-json",
                '[{"agent_id":"agent-a","content":"A"},{"agent_id":"agent-b","content":"B"}]',
                "--timeout",
                "8",
                "--stop-on-timeout",
                "--json",
            ]
        )

        self.assertEqual(args.live_agent_command, "call-sequence")
        self.assertEqual(args.server, "http://room.local")
        self.assertEqual(args.meeting_id, "m1")
        self.assertEqual(args.timeout, 8.0)
        self.assertTrue(args.stop_on_timeout)
        self.assertTrue(args.as_json)

    def test_live_agent_call_round_parser_accepts_role_filters(self):
        args = build_parser().parse_args(
            [
                "live-agent",
                "call-round",
                "--server",
                "http://room.local",
                "--meeting-id",
                "m1",
                "--round-id",
                "round_1",
                "--role",
                "critic",
                "--role",
                "architect",
                "--timeout",
                "8",
                "--stop-on-timeout",
                "--json",
                "Discuss",
                "this",
                "round",
            ]
        )

        self.assertEqual(args.live_agent_command, "call-round")
        self.assertEqual(args.server, "http://room.local")
        self.assertEqual(args.meeting_id, "m1")
        self.assertEqual(args.round_id, "round_1")
        self.assertEqual(args.role_ids, ["critic", "architect"])
        self.assertEqual(args.timeout, 8.0)
        self.assertTrue(args.stop_on_timeout)
        self.assertTrue(args.as_json)
        self.assertEqual(args.instruction, ["Discuss", "this", "round"])

    def test_live_agent_call_preset_parser_accepts_role_filters(self):
        args = build_parser().parse_args(
            [
                "live-agent",
                "call-preset",
                "--server",
                "http://room.local",
                "--meeting-id",
                "m1",
                "--preset",
                "meme_debate_argument",
                "--role",
                "critic",
                "--timeout",
                "8",
                "--stop-on-timeout",
                "--json",
            ]
        )

        self.assertEqual(args.live_agent_command, "call-preset")
        self.assertEqual(args.server, "http://room.local")
        self.assertEqual(args.meeting_id, "m1")
        self.assertEqual(args.preset_id, "meme_debate_argument")
        self.assertEqual(args.role_ids, ["critic"])
        self.assertEqual(args.timeout, 8.0)
        self.assertTrue(args.stop_on_timeout)
        self.assertTrue(args.as_json)

    def test_live_agent_call_remaining_rounds_parser_accepts_bounds(self):
        args = build_parser().parse_args(
            [
                "live-agent",
                "call-remaining-rounds",
                "--server",
                "http://room.local",
                "--meeting-id",
                "m1",
                "--timeout",
                "8",
                "--max-rounds",
                "2",
                "--stop-on-timeout",
                "--json",
            ]
        )

        self.assertEqual(args.live_agent_command, "call-remaining-rounds")
        self.assertEqual(args.server, "http://room.local")
        self.assertEqual(args.meeting_id, "m1")
        self.assertEqual(args.timeout, 8.0)
        self.assertEqual(args.max_rounds, 2)
        self.assertTrue(args.stop_on_timeout)
        self.assertTrue(args.as_json)

    def test_live_agent_review_checkpoint_parser_accepts_targets(self):
        args = build_parser().parse_args(
            [
                "live-agent",
                "review-checkpoint",
                "--server",
                "http://room.local",
                "--meeting-id",
                "m1",
                "--group-id",
                "resident main",
                "--agent-id",
                "agent-a",
                "--agent-id",
                "agent-b",
                "--timeout",
                "8",
                "--checkpoint-id",
                "checkpoint-1",
                "--json",
                "Review",
                "this",
                "slice",
            ]
        )

        self.assertEqual(args.live_agent_command, "review-checkpoint")
        self.assertEqual(args.server, "http://room.local")
        self.assertEqual(args.meeting_id, "m1")
        self.assertEqual(args.group_id, "resident main")
        self.assertEqual(args.agent_ids, ["agent-a", "agent-b"])
        self.assertEqual(args.timeout, 8.0)
        self.assertEqual(args.checkpoint_id, "checkpoint-1")
        self.assertTrue(args.as_json)
        self.assertEqual(args.message, ["Review", "this", "slice"])

    def test_live_agent_review_checkpoint_posts_request_and_prints_summary(self):
        response = {
            "status": "answered",
            "checkpoint_id": "checkpoint-1",
            "turn_count": 2,
            "answered_count": 2,
            "timeout_count": 0,
            "skipped_count": 0,
            "results": [
                {"agent_id": "agent-a", "status": "answered", "reply_event": {"id": "reply-a"}},
                {"agent_id": "agent-b", "status": "answered", "reply_event": {"id": "reply-b"}},
            ],
        }
        stdout = StringIO()
        with patch("agentsassemble.cli._request_json", return_value=response) as request_json:
            with patch("sys.stdout", stdout):
                exit_code = main(
                    [
                        "live-agent",
                        "review-checkpoint",
                        "--server",
                        "http://room.local",
                        "--meeting-id",
                        "m1",
                        "--group-id",
                        "resident-main",
                        "--agent-id",
                        "agent-a",
                        "--agent-id",
                        "agent-b",
                        "--timeout",
                        "8",
                        "--checkpoint-id",
                        "checkpoint-1",
                        "Review",
                        "this",
                        "slice",
                    ]
                )

        self.assertEqual(exit_code, 0)
        self.assertIn("Review checkpoint checkpoint-1 answered: 2/2 answered, 0 timed out, 0 skipped", stdout.getvalue())
        request_json.assert_called_once()
        url = request_json.call_args.args[0]
        payload = request_json.call_args.kwargs["payload"]
        self.assertEqual(url, "http://room.local/api/meetings/m1/review-checkpoints")
        self.assertEqual(payload["group_id"], "resident-main")
        self.assertEqual(payload["agent_ids"], ["agent-a", "agent-b"])
        self.assertEqual(payload["content"], "Review this slice")
        self.assertEqual(payload["checkpoint_id"], "checkpoint-1")
        self.assertEqual(payload["timeout_seconds"], 8.0)

    def test_live_agent_review_checkpoint_returns_one_when_not_answered(self):
        response = {
            "status": "timeout",
            "checkpoint_id": "checkpoint-1",
            "turn_count": 1,
            "answered_count": 0,
            "timeout_count": 1,
            "skipped_count": 0,
            "results": [{"agent_id": "agent-a", "status": "timeout", "request_event": {"id": "request-a"}}],
        }
        stdout = StringIO()
        with patch("agentsassemble.cli._request_json", return_value=response):
            with patch("sys.stdout", stdout):
                exit_code = main(
                    [
                        "live-agent",
                        "review-checkpoint",
                        "--server",
                        "http://room.local",
                        "--meeting-id",
                        "m1",
                        "--group-id",
                        "resident-main",
                        "--timeout",
                        "0",
                        "Review",
                        "this",
                    ]
                )

        self.assertEqual(exit_code, 1)
        self.assertIn("Review checkpoint checkpoint-1 timeout: 0/1 answered, 1 timed out, 0 skipped", stdout.getvalue())

    def test_live_agent_call_round_posts_request_and_prints_summary(self):
        response = {
            "status": "answered",
            "round_id": "round_1",
            "answered_count": 2,
            "timeout_count": 0,
            "skipped_count": 0,
            "results": [
                {"agent_id": "agent-b", "status": "answered", "reply_event": {"id": "reply-b"}},
                {"agent_id": "agent-a", "status": "answered", "reply_event": {"id": "reply-a"}},
            ],
        }
        stdout = StringIO()
        with patch("agentsassemble.cli._request_json", return_value=response) as request_json:
            with patch("sys.stdout", stdout):
                exit_code = main(
                    [
                        "live-agent",
                        "call-round",
                        "--server",
                        "http://room.local",
                        "--meeting-id",
                        "m1",
                        "--round-id",
                        "round_1",
                        "--role",
                        "critic",
                        "--role",
                        "architect",
                        "--timeout",
                        "8",
                        "--stop-on-timeout",
                        "Discuss",
                        "this",
                        "round",
                    ]
                )

        self.assertEqual(exit_code, 0)
        request_json.assert_called_once_with(
            "http://room.local/api/meetings/m1/live-agent-turns/round",
            method="POST",
            payload={
                "round_id": "round_1",
                "role_ids": ["critic", "architect"],
                "content": "Discuss this round",
                "timeout_seconds": 8.0,
                "stop_on_timeout": True,
            },
            timeout_seconds=22.0,
        )
        self.assertIn("Official round round_1 answered: 2 answered, 0 timed out, 0 skipped", stdout.getvalue())
        self.assertIn("- agent-b: answered reply-b", stdout.getvalue())

    def test_live_agent_call_preset_posts_request_and_prints_summary(self):
        response = {
            "status": "answered",
            "preset_id": "meme_debate_argument",
            "answered_count": 1,
            "timeout_count": 0,
            "skipped_count": 0,
            "results": [{"agent_id": "agent-b", "status": "answered", "reply_event": {"id": "reply-b"}}],
        }
        stdout = StringIO()
        with patch("agentsassemble.cli._request_json", return_value=response) as request_json:
            with patch("sys.stdout", stdout):
                exit_code = main(
                    [
                        "live-agent",
                        "call-preset",
                        "--server",
                        "http://room.local",
                        "--meeting-id",
                        "m1",
                        "--preset",
                        "meme_debate_argument",
                        "--role",
                        "critic",
                        "--timeout",
                        "8",
                        "--stop-on-timeout",
                    ]
                )

        self.assertEqual(exit_code, 0)
        request_json.assert_called_once_with(
            "http://room.local/api/meetings/m1/live-agent-turns/preset",
            method="POST",
            payload={
                "preset_id": "meme_debate_argument",
                "role_ids": ["critic"],
                "timeout_seconds": 8.0,
                "stop_on_timeout": True,
            },
            timeout_seconds=14.0,
        )
        self.assertIn("Play preset meme_debate_argument answered: 1 answered, 0 timed out, 0 skipped", stdout.getvalue())

    def test_live_agent_call_remaining_rounds_posts_request_and_prints_summary(self):
        response = {
            "status": "answered",
            "round_count": 1,
            "answered_round_count": 1,
            "timeout_round_count": 0,
            "skipped_round_count": 0,
            "results": [{"round_id": "round_2", "status": "answered"}],
        }
        stdout = StringIO()
        with patch("agentsassemble.cli._request_json", return_value=response) as request_json:
            with patch("sys.stdout", stdout):
                exit_code = main(
                    [
                        "live-agent",
                        "call-remaining-rounds",
                        "--server",
                        "http://room.local",
                        "--meeting-id",
                        "m1",
                        "--timeout",
                        "8",
                        "--max-rounds",
                        "2",
                        "--stop-on-timeout",
                    ]
                )

        self.assertEqual(exit_code, 0)
        request_json.assert_called_once_with(
            "http://room.local/api/meetings/m1/live-agent-turns/rounds",
            method="POST",
            payload={
                "timeout_seconds": 8.0,
                "stop_on_timeout": True,
                "max_rounds": 2,
            },
            timeout_seconds=198.0,
        )
        self.assertIn("Official remaining rounds answered: 1 rounds, 1 answered, 0 already complete, 0 timed out, 0 skipped", stdout.getvalue())
        self.assertIn("- round_2: answered", stdout.getvalue())

    def test_live_agent_call_remaining_rounds_can_finalize_after_success(self):
        response = {
            "status": "answered",
            "round_count": 1,
            "answered_round_count": 1,
            "timeout_round_count": 0,
            "skipped_round_count": 0,
            "results": [{"round_id": "round_2", "status": "answered"}],
            "finalization": {
                "status": "finalized",
                "meeting_id": "m1",
                "official_event_count": 2,
            },
        }
        stdout = StringIO()
        with patch("agentsassemble.cli._request_json", return_value=response) as request_json:
            with patch("sys.stdout", stdout):
                exit_code = main(
                    [
                        "live-agent",
                        "call-remaining-rounds",
                        "--server",
                        "http://room.local",
                        "--meeting-id",
                        "m1",
                        "--timeout",
                        "8",
                        "--max-rounds",
                        "1",
                        "--finalize-after-rounds",
                    ]
                )

        self.assertEqual(exit_code, 0)
        request_json.assert_called_once_with(
            "http://room.local/api/meetings/m1/live-agent-turns/rounds",
            method="POST",
            payload={
                "timeout_seconds": 8.0,
                "stop_on_timeout": False,
                "max_rounds": 1,
                "finalize_after_rounds": True,
            },
            timeout_seconds=102.0,
        )
        self.assertIn("finalization finalized: 2 official events", stdout.getvalue())

    def test_live_agent_call_remaining_rounds_finalize_failure_exits_nonzero(self):
        response = {
            "status": "answered",
            "round_count": 1,
            "answered_round_count": 1,
            "timeout_round_count": 0,
            "skipped_round_count": 0,
            "results": [{"round_id": "round_2", "status": "answered"}],
            "finalization": {"status": "failed", "reason": "pending_turn_request"},
        }
        stdout = StringIO()
        with patch("agentsassemble.cli._request_json", return_value=response):
            with patch("sys.stdout", stdout):
                exit_code = main(
                    [
                        "live-agent",
                        "call-remaining-rounds",
                        "--server",
                        "http://room.local",
                        "--meeting-id",
                        "m1",
                        "--finalize-after-rounds",
                    ]
                )

        self.assertEqual(exit_code, 1)
        self.assertIn("finalization failed", stdout.getvalue())

    def test_live_agent_call_remaining_rounds_returns_one_when_partial(self):
        response = {
            "status": "stopped",
            "round_count": 2,
            "answered_round_count": 0,
            "timeout_round_count": 1,
            "skipped_round_count": 1,
            "results": [{"round_id": "round_1", "status": "timeout"}, {"round_id": "round_2", "status": "skipped"}],
        }
        with patch("agentsassemble.cli._request_json", return_value=response):
            exit_code = main(
                [
                    "live-agent",
                    "call-remaining-rounds",
                    "--server",
                    "http://room.local",
                    "--meeting-id",
                    "m1",
                    "--timeout",
                    "0",
                ]
            )

        self.assertEqual(exit_code, 1)

    def test_live_agent_call_remaining_rounds_rejects_more_than_batch_limit(self):
        stderr = StringIO()
        with patch("agentsassemble.cli._request_json") as request_json:
            with patch("sys.stderr", stderr):
                exit_code = main(
                    [
                        "live-agent",
                        "call-remaining-rounds",
                        "--server",
                        "http://room.local",
                        "--meeting-id",
                        "m1",
                        "--max-rounds",
                        "9",
                    ]
                )

        self.assertEqual(exit_code, 2)
        request_json.assert_not_called()
        self.assertIn("--max-rounds supports at most 8", stderr.getvalue())

    def test_live_agent_call_sequence_posts_turns_and_prints_summary(self):
        response = {
            "status": "answered",
            "answered_count": 2,
            "timeout_count": 0,
            "skipped_count": 0,
            "results": [
                {"agent_id": "agent-a", "status": "answered", "reply_event": {"id": "reply-a"}},
                {"agent_id": "agent-b", "status": "answered", "reply_event": {"id": "reply-b"}},
            ],
        }
        stdout = StringIO()
        turns_json = '[{"agent_id":"agent-a","content":"A"},{"agent_id":"agent-b","content":"B"}]'
        with patch("agentsassemble.cli._request_json", return_value=response) as request_json:
            with patch("sys.stdout", stdout):
                exit_code = main(
                    [
                        "live-agent",
                        "call-sequence",
                        "--server",
                        "http://room.local",
                        "--meeting-id",
                        "m1",
                        "--turns-json",
                        turns_json,
                        "--timeout",
                        "8",
                    ]
                )

        self.assertEqual(exit_code, 0)
        request_json.assert_called_once_with(
            "http://room.local/api/meetings/m1/live-agent-turns/sequence",
            method="POST",
            payload={
                "turns": [{"agent_id": "agent-a", "content": "A"}, {"agent_id": "agent-b", "content": "B"}],
                "timeout_seconds": 8.0,
                "stop_on_timeout": False,
            },
            timeout_seconds=22.0,
        )
        self.assertIn("Official turn sequence answered: 2 answered, 0 timed out, 0 skipped", stdout.getvalue())
        self.assertIn("- agent-a: answered reply-a", stdout.getvalue())

    def test_live_agent_call_sequence_reads_turns_file(self):
        response = {"status": "answered", "answered_count": 1, "timeout_count": 0, "skipped_count": 0, "results": []}
        with tempfile.TemporaryDirectory() as temp_dir:
            turns_path = Path(temp_dir) / "turns.json"
            turns_path.write_text('[{"agent_id":"agent-a","content":"A"}]\n', encoding="utf-8")
            with patch("agentsassemble.cli._request_json", return_value=response) as request_json:
                exit_code = main(
                    [
                        "live-agent",
                        "call-sequence",
                        "--server",
                        "http://room.local",
                        "--meeting-id",
                        "m1",
                        "--turns-file",
                        str(turns_path),
                        "--timeout",
                        "8",
                    ]
                )

        self.assertEqual(exit_code, 0)
        self.assertEqual(
            request_json.call_args.kwargs["payload"]["turns"],
            [{"agent_id": "agent-a", "content": "A"}],
        )

    def test_live_agent_call_sequence_returns_one_when_partial(self):
        response = {
            "status": "timeout",
            "answered_count": 1,
            "timeout_count": 1,
            "skipped_count": 0,
            "results": [
                {"agent_id": "agent-a", "status": "answered", "reply_event": {"id": "reply-a"}},
                {"agent_id": "agent-b", "status": "timeout", "request_event": {"id": "request-b"}, "reply_event": None},
            ],
        }
        stdout = StringIO()
        turns_json = '[{"agent_id":"agent-a","content":"A"},{"agent_id":"agent-b","content":"B"}]'
        with patch("agentsassemble.cli._request_json", return_value=response):
            with patch("sys.stdout", stdout):
                exit_code = main(
                    [
                        "live-agent",
                        "call-sequence",
                        "--server",
                        "http://room.local",
                        "--meeting-id",
                        "m1",
                        "--turns-json",
                        turns_json,
                        "--timeout",
                        "8",
                    ]
                )

        self.assertEqual(exit_code, 1)
        self.assertIn("Official turn sequence timeout: 1 answered, 1 timed out, 0 skipped", stdout.getvalue())
        self.assertIn("- agent-b: timeout request-b", stdout.getvalue())
