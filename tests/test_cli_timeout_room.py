import unittest
import json
import tempfile
import threading
from pathlib import Path
from http.server import ThreadingHTTPServer
from io import StringIO
from unittest.mock import patch

from agentsassemble import cli as cli_module
from agentsassemble.cli import build_parser, main
from agentsassemble.gui import _make_handler


class CliTimeoutRoomTests(unittest.TestCase):

    def test_live_agent_wait_room_event_parses_cursor_and_wait_options(self):
        args = build_parser().parse_args(
            [
                "live-agent",
                "wait-room-event",
                "--server",
                "http://room.local",
                "--agent-id",
                "claude-terminal",
                "--after-event-id",
                "evt-old",
                "--max-chain-depth",
                "2",
                "--timeout",
                "3",
                "--poll-interval",
                "0.5",
                "--json",
            ]
        )

        self.assertEqual(args.live_agent_command, "wait-room-event")
        self.assertEqual(args.agent_id, "claude-terminal")
        self.assertEqual(args.after_event_id, "evt-old")
        self.assertEqual(args.max_chain_depth, 2)
        self.assertEqual(args.timeout, 3.0)
        self.assertEqual(args.poll_interval, 0.5)
        self.assertTrue(args.as_json)

    def test_live_agent_wait_room_event_returns_next_non_self_lobby_event(self):
        stdout = StringIO()
        room_payload = {
            "agent": {
                "agent_id": "claude-terminal",
                "display_name": "Claude Terminal",
                "last_observed_event_id": "evt-old",
            },
            "lobby_events": [
                {"id": "evt-old", "name": "나", "message": "old"},
                {"id": "evt-self", "actor_id": "claude-terminal", "name": "Claude Terminal", "message": "self"},
                {"id": "evt-next", "name": "나", "message": "새 이벤트", "auto_chain_depth": 1},
            ],
        }

        with patch("agentsassemble.cli._request_json", return_value=room_payload) as request_json:
            with patch("sys.stdout", stdout):
                exit_code = main(
                    [
                        "live-agent",
                        "wait-room-event",
                        "--server",
                        "http://room.local",
                        "--agent-id",
                        "claude-terminal",
                        "--timeout",
                        "0",
                        "--json",
                    ]
                )

        self.assertEqual(exit_code, 0)
        request_json.assert_called_once_with("http://room.local/api/live-agents/claude-terminal/room")
        payload = json.loads(stdout.getvalue())
        self.assertEqual(payload["status"], "event")
        self.assertEqual(payload["event"]["id"], "evt-next")
        self.assertEqual(payload["source_event_id"], "evt-next")
        self.assertEqual(payload["reply_command"][0:7], ["python3", "-m", "agentsassemble.cli", "live-agent", "say", "--server", "http://room.local"])
        self.assertEqual(payload["reply_command"][-2:], ["--", "<reply>"])

    def test_live_agent_wait_room_event_includes_official_only_shared_memory(self):
        stdout = StringIO()
        room_payload = {
            "agent": {"agent_id": "claude-terminal", "last_observed_event_id": "evt-old"},
            "shared_memory": {
                "official_event_count": 1,
                "last_official_event_id": "reply-1",
                "rolling_summary": [
                    {"event_id": "reply-1", "speaker": "Architect", "summary": "Official context only."}
                ],
                "action_items": [
                    {"event_id": "reply-1", "speaker": "Architect", "text": "Keep terminal agents in sync."}
                ],
                "private_prompt": "malicious injected prompt",
                "raw_nested": {"token": "secret-token"},
            },
            "lobby_events": [
                {"id": "evt-old", "name": "나", "message": "old"},
                {"id": "evt-next", "name": "나", "message": "lobby question"},
            ],
            "live_events": [
                {
                    "id": "secret-request",
                    "kind": "live_agent_turn_request",
                    "target_agent_id": "other-agent",
                    "content": "private prompt must not leak",
                }
            ],
        }

        with patch("agentsassemble.cli._request_json", return_value=room_payload):
            with patch("sys.stdout", stdout):
                exit_code = main(
                    [
                        "live-agent",
                        "wait-room-event",
                        "--server",
                        "http://room.local",
                        "--agent-id",
                        "claude-terminal",
                        "--timeout",
                        "0",
                        "--json",
                    ]
                )

        self.assertEqual(exit_code, 0)
        payload = json.loads(stdout.getvalue())
        self.assertEqual(payload["room"]["shared_memory"]["official_event_count"], 1)
        self.assertEqual(payload["room"]["shared_memory"]["action_items"][0]["text"], "Keep terminal agents in sync.")
        payload_text = json.dumps(payload["room"]["shared_memory"], ensure_ascii=False)
        self.assertIn("Official context only.", payload_text)
        self.assertNotIn("private prompt must not leak", payload_text)
        self.assertNotIn("malicious injected prompt", payload_text)
        self.assertNotIn("secret-token", payload_text)

    def test_live_agent_wait_room_event_treats_actor_id_as_authoritative_for_self_check(self):
        stdout = StringIO()
        room_payload = {
            "agent": {
                "agent_id": "claude-terminal",
                "display_name": "Shared Name",
                "last_observed_event_id": "evt-old",
            },
            "lobby_events": [
                {"id": "evt-old", "name": "나", "message": "old"},
                {"id": "evt-other", "actor_id": "other-agent", "name": "Shared Name", "message": "not self"},
            ],
        }

        with patch("agentsassemble.cli._request_json", return_value=room_payload):
            with patch("sys.stdout", stdout):
                exit_code = main(
                    [
                        "live-agent",
                        "wait-room-event",
                        "--server",
                        "http://room.local",
                        "--agent-id",
                        "claude-terminal",
                        "--timeout",
                        "0",
                        "--json",
                    ]
                )

        self.assertEqual(exit_code, 0)
        payload = json.loads(stdout.getvalue())
        self.assertEqual(payload["event"]["id"], "evt-other")

    def test_live_agent_wait_room_event_polls_until_candidate_arrives(self):
        stdout = StringIO()
        first_room = {
            "agent": {"agent_id": "claude-terminal", "last_observed_event_id": "evt-old"},
            "lobby_events": [{"id": "evt-old", "name": "나", "message": "old"}],
        }
        second_room = {
            "agent": {"agent_id": "claude-terminal", "last_observed_event_id": "evt-old"},
            "lobby_events": [
                {"id": "evt-old", "name": "나", "message": "old"},
                {"id": "evt-next", "name": "나", "message": "new"},
            ],
        }

        with patch("agentsassemble.cli._request_json", side_effect=[first_room, second_room]) as request_json:
            with patch("agentsassemble.cli.time.sleep") as sleep:
                with patch("sys.stdout", stdout):
                    exit_code = main(
                        [
                            "live-agent",
                            "wait-room-event",
                            "--server",
                            "http://room.local",
                            "--agent-id",
                            "claude-terminal",
                            "--timeout",
                            "1",
                            "--poll-interval",
                            "0",
                            "--json",
                        ]
                    )

        self.assertEqual(exit_code, 0)
        self.assertEqual(request_json.call_count, 2)
        sleep.assert_called_once()
        payload = json.loads(stdout.getvalue())
        self.assertEqual(payload["event"]["id"], "evt-next")

    def test_live_agent_wait_room_event_times_out_without_new_event(self):
        stdout = StringIO()
        room_payload = {
            "agent": {"agent_id": "claude-terminal", "last_observed_event_id": "evt-old"},
            "lobby_events": [{"id": "evt-old", "name": "나", "message": "old"}],
        }

        with patch("agentsassemble.cli._request_json", return_value=room_payload):
            with patch("sys.stdout", stdout):
                exit_code = main(
                    [
                        "live-agent",
                        "wait-room-event",
                        "--server",
                        "http://room.local",
                        "--agent-id",
                        "claude-terminal",
                        "--timeout",
                        "0",
                        "--json",
                    ]
                )

        self.assertEqual(exit_code, 1)
        payload = json.loads(stdout.getvalue())
        self.assertEqual(payload["status"], "timeout")
        self.assertEqual(payload["last_observed_event_id"], "evt-old")

    def test_live_agent_wait_room_event_skips_over_chain_limit(self):
        stdout = StringIO()
        room_payload = {
            "agent": {"agent_id": "claude-terminal", "last_observed_event_id": "evt-old"},
            "lobby_events": [
                {"id": "evt-old", "name": "나", "message": "old"},
                {"id": "evt-chain", "name": "Gemini", "message": "chain", "auto_chain_depth": 2},
            ],
        }

        with patch("agentsassemble.cli._request_json", return_value=room_payload):
            with patch("sys.stdout", stdout):
                exit_code = main(
                    [
                        "live-agent",
                        "wait-room-event",
                        "--server",
                        "http://room.local",
                        "--agent-id",
                        "claude-terminal",
                        "--max-chain-depth",
                        "1",
                        "--timeout",
                        "0",
                        "--json",
                    ]
                )

        self.assertEqual(exit_code, 1)
        payload = json.loads(stdout.getvalue())
        self.assertEqual(payload["status"], "timeout")
        self.assertEqual(payload["last_observed_event_id"], "evt-chain")

    def test_live_agent_wait_official_turn_parses_cursor_and_wait_options(self):
        args = build_parser().parse_args(
            [
                "live-agent",
                "wait-official-turn",
                "--server",
                "http://room.local",
                "--agent-id",
                "claude-terminal",
                "--after-event-id",
                "live-old",
                "--timeout",
                "3",
                "--poll-interval",
                "0.5",
                "--json",
            ]
        )

        self.assertEqual(args.live_agent_command, "wait-official-turn")
        self.assertEqual(args.agent_id, "claude-terminal")
        self.assertEqual(args.after_event_id, "live-old")
        self.assertEqual(args.timeout, 3.0)
        self.assertEqual(args.poll_interval, 0.5)
        self.assertTrue(args.as_json)

    def test_live_agent_wait_turn_request_alias_parses_same_options(self):
        args = build_parser().parse_args(
            [
                "live-agent",
                "wait-turn-request",
                "--server",
                "http://room.local",
                "--agent-id",
                "claude-terminal",
                "--after-event-id",
                "live-old",
                "--timeout",
                "3",
                "--poll-interval",
                "0.5",
                "--json",
            ]
        )

        self.assertEqual(args.live_agent_command, "wait-turn-request")
        self.assertEqual(args.agent_id, "claude-terminal")
        self.assertEqual(args.after_event_id, "live-old")
        self.assertEqual(args.timeout, 3.0)
        self.assertEqual(args.poll_interval, 0.5)
        self.assertTrue(args.as_json)

    def test_live_agent_wait_official_turn_returns_targeted_unanswered_request(self):
        stdout = StringIO()
        room_payload = {
            "agent": {
                "agent_id": "claude-terminal",
                "meeting_id": "meeting-1",
                "last_observed_live_event_id": "live-old",
            },
            "live_events": [
                {"id": "live-old", "kind": "message", "channel": "official", "actor_id": "other-agent", "content": "old"},
                {
                    "id": "live-other",
                    "kind": "live_agent_turn_request",
                    "target_agent_id": "other-agent",
                    "content": "not yours",
                },
                {
                    "id": "live-answered",
                    "kind": "live_agent_turn_request",
                    "target_agent_id": "claude-terminal",
                    "content": "already answered",
                },
                {
                    "id": "reply-answered",
                    "kind": "message",
                    "channel": "official",
                    "official_record": True,
                    "actor_id": "claude-terminal",
                    "source_event_id": "live-answered",
                    "content": "done",
                },
                {
                    "id": "live-next",
                    "kind": "live_agent_turn_request",
                    "meeting_id": "meeting-1",
                    "target_agent_id": "claude-terminal",
                    "role_id": "architect",
                    "display_name": "Claude Terminal",
                    "content": "Give the official answer.",
                },
            ],
        }

        with patch("agentsassemble.cli._request_json", return_value=room_payload) as request_json:
            with patch("sys.stdout", stdout):
                exit_code = main(
                    [
                        "live-agent",
                        "wait-official-turn",
                        "--server",
                        "http://room.local",
                        "--agent-id",
                        "claude-terminal",
                        "--timeout",
                        "0",
                        "--json",
                    ]
                )

        self.assertEqual(exit_code, 0)
        request_json.assert_called_once_with("http://room.local/api/live-agents/claude-terminal/room")
        payload = json.loads(stdout.getvalue())
        self.assertEqual(payload["status"], "event")
        self.assertEqual(payload["event"]["id"], "live-next")
        self.assertEqual(payload["meeting_id"], "meeting-1")
        self.assertEqual(payload["source_event_id"], "live-next")
        self.assertEqual(payload["reply_command"][0:7], ["python3", "-m", "agentsassemble.cli", "live-agent", "official-reply", "--server", "http://room.local"])
        self.assertEqual(payload["reply_command"][-2:], ["--", "<reply>"])

    def test_live_agent_wait_official_turn_includes_official_only_shared_memory(self):
        stdout = StringIO()
        room_payload = {
            "agent": {
                "agent_id": "claude-terminal",
                "meeting_id": "meeting-1",
                "last_observed_live_event_id": "live-old",
            },
            "meeting_id": "meeting-1",
            "shared_memory": {
                "official_event_count": 2,
                "last_official_event_id": "reply-2",
                "decisions": [
                    {"event_id": "reply-1", "speaker": "Architect", "text": "Keep self-service informed."}
                ],
                "provider_output": "raw model output must not leak",
            },
            "live_events": [
                {"id": "live-old", "kind": "message", "content": "old"},
                {
                    "id": "live-next",
                    "kind": "live_agent_turn_request",
                    "meeting_id": "meeting-1",
                    "target_agent_id": "claude-terminal",
                    "content": "official question",
                },
                {
                    "id": "secret-request",
                    "kind": "live_agent_turn_request",
                    "meeting_id": "meeting-1",
                    "target_agent_id": "other-agent",
                    "content": "other private turn",
                },
            ],
        }

        with patch("agentsassemble.cli._request_json", return_value=room_payload):
            with patch("sys.stdout", stdout):
                exit_code = main(
                    [
                        "live-agent",
                        "wait-official-turn",
                        "--server",
                        "http://room.local",
                        "--agent-id",
                        "claude-terminal",
                        "--timeout",
                        "0",
                        "--json",
                    ]
                )

        self.assertEqual(exit_code, 0)
        payload = json.loads(stdout.getvalue())
        self.assertEqual(payload["room"]["shared_memory"]["official_event_count"], 2)
        payload_text = json.dumps(payload["room"]["shared_memory"], ensure_ascii=False)
        self.assertIn("Keep self-service informed.", payload_text)
        self.assertNotIn("other private turn", payload_text)
        self.assertNotIn("raw model output must not leak", payload_text)

    def test_live_agent_wait_official_turn_uses_visible_tail_when_cursor_is_missing(self):
        stdout = StringIO()
        room_payload = {
            "agent": {
                "agent_id": "claude-terminal",
                "meeting_id": "meeting-1",
                "last_observed_live_event_id": "evicted-live-cursor",
            },
            "live_events": [
                {
                    "id": "live-visible",
                    "kind": "live_agent_turn_request",
                    "meeting_id": "meeting-1",
                    "target_agent_id": "claude-terminal",
                    "content": "visible tail request",
                },
            ],
        }

        with patch("agentsassemble.cli._request_json", return_value=room_payload):
            with patch("sys.stdout", stdout):
                exit_code = main(
                    [
                        "live-agent",
                        "wait-official-turn",
                        "--server",
                        "http://room.local",
                        "--agent-id",
                        "claude-terminal",
                        "--timeout",
                        "0",
                        "--json",
                    ]
                )

        self.assertEqual(exit_code, 0)
        payload = json.loads(stdout.getvalue())
        self.assertEqual(payload["event"]["id"], "live-visible")

    def test_live_agent_wait_official_turn_times_out_without_targeted_request(self):
        stdout = StringIO()
        room_payload = {
            "agent": {"agent_id": "claude-terminal", "last_observed_live_event_id": "live-old"},
            "live_events": [
                {"id": "live-old", "kind": "message", "content": "old"},
                {"id": "live-info", "kind": "message", "content": "visible non-turn update"},
            ],
        }

        with patch("agentsassemble.cli._request_json", return_value=room_payload):
            with patch("sys.stdout", stdout):
                exit_code = main(
                    [
                        "live-agent",
                        "wait-official-turn",
                        "--server",
                        "http://room.local",
                        "--agent-id",
                        "claude-terminal",
                        "--timeout",
                        "0",
                        "--json",
                    ]
                )

        self.assertEqual(exit_code, 1)
        payload = json.loads(stdout.getvalue())
        self.assertEqual(payload["status"], "timeout")
        self.assertEqual(payload["last_observed_live_event_id"], "live-info")

    def test_live_agent_official_reply_posts_official_reply(self):
        stdout = StringIO()
        response = {
            "agent": {"agent_id": "claude-terminal", "last_observed_live_event_id": "live-next"},
            "event": {"id": "reply-next"},
        }

        with patch("agentsassemble.cli._request_json", return_value=response) as request_json:
            with patch("sys.stdout", stdout):
                exit_code = main(
                    [
                        "live-agent",
                        "official-reply",
                        "--server",
                        "http://room.local",
                        "--agent-id",
                        "claude-terminal",
                        "--meeting-id",
                        "meeting-1",
                        "--source-event-id",
                        "live-next",
                        "--json",
                        "Official answer.",
                    ]
                )

        self.assertEqual(exit_code, 0)
        request_json.assert_called_once_with(
            "http://room.local/api/live-agents/claude-terminal/official-turn",
            method="POST",
            payload={
                "meeting_id": "meeting-1",
                "source_event_id": "live-next",
                "content": "Official answer.",
            },
        )
        payload = json.loads(stdout.getvalue())
        self.assertEqual(payload["event"]["id"], "reply-next")

    def test_live_agent_answer_turn_alias_posts_official_reply(self):
        stdout = StringIO()
        response = {
            "agent": {"agent_id": "claude-terminal", "last_observed_live_event_id": "live-next"},
            "event": {"id": "reply-next"},
        }

        with patch("agentsassemble.cli._request_json", return_value=response) as request_json:
            with patch("sys.stdout", stdout):
                exit_code = main(
                    [
                        "live-agent",
                        "answer-turn",
                        "--server",
                        "http://room.local",
                        "--agent-id",
                        "claude-terminal",
                        "--meeting-id",
                        "meeting-1",
                        "--source-event-id",
                        "live-next",
                        "--json",
                        "Official answer.",
                    ]
                )

        self.assertEqual(exit_code, 0)
        request_json.assert_called_once_with(
            "http://room.local/api/live-agents/claude-terminal/official-turn",
            method="POST",
            payload={
                "meeting_id": "meeting-1",
                "source_event_id": "live-next",
                "content": "Official answer.",
            },
        )
        payload = json.loads(stdout.getvalue())
        self.assertEqual(payload["event"]["id"], "reply-next")

    def test_live_agent_wait_next_parses_lobby_and_official_cursor_options(self):
        args = build_parser().parse_args(
            [
                "live-agent",
                "wait-next",
                "--server",
                "http://room.local",
                "--agent-id",
                "claude-terminal",
                "--after-event-id",
                "evt-old",
                "--after-live-event-id",
                "live-old",
                "--max-chain-depth",
                "2",
                "--timeout",
                "3",
                "--poll-interval",
                "0.5",
                "--json",
            ]
        )

        self.assertEqual(args.live_agent_command, "wait-next")
        self.assertEqual(args.agent_id, "claude-terminal")
        self.assertEqual(args.after_event_id, "evt-old")
        self.assertEqual(args.after_live_event_id, "live-old")
        self.assertEqual(args.max_chain_depth, 2)
        self.assertEqual(args.timeout, 3.0)
        self.assertEqual(args.poll_interval, 0.5)
        self.assertTrue(args.as_json)

    def test_live_agent_read_since_parses_lobby_and_official_cursor_options(self):
        args = build_parser().parse_args(
            [
                "live-agent",
                "read-since",
                "--server",
                "http://room.local",
                "--agent-id",
                "claude-terminal",
                "--after-event-id",
                "evt-old",
                "--after-live-event-id",
                "live-old",
                "--json",
            ]
        )

        self.assertEqual(args.live_agent_command, "read-since")
        self.assertEqual(args.agent_id, "claude-terminal")
        self.assertEqual(args.after_event_id, "evt-old")
        self.assertEqual(args.after_live_event_id, "live-old")
        self.assertTrue(args.as_json)

    def test_live_agent_read_since_returns_room_diff_without_posting(self):
        stdout = StringIO()
        room_payload = {
            "agent": {
                "agent_id": "claude-terminal",
                "meeting_id": "meeting-1",
                "last_observed_event_id": "evt-old",
                "last_observed_live_event_id": "live-old",
            },
            "shared_memory": {
                "official_event_count": 1,
                "rolling_summary": [
                    {"event_id": "reply-1", "speaker": "Architect", "summary": "Shared room context."}
                ],
            },
            "lobby_events": [
                {"id": "evt-old", "name": "나", "message": "old lobby"},
                {"id": "evt-next", "name": "나", "message": "new lobby"},
            ],
            "live_events": [
                {"id": "live-old", "kind": "message", "content": "old official"},
                {"id": "live-next", "kind": "message", "content": "new official"},
            ],
        }

        with patch("agentsassemble.cli._request_json", return_value=room_payload) as request_json:
            with patch("sys.stdout", stdout):
                exit_code = main(
                    [
                        "live-agent",
                        "read-since",
                        "--server",
                        "http://room.local",
                        "--agent-id",
                        "claude-terminal",
                        "--json",
                    ]
                )

        self.assertEqual(exit_code, 0)
        request_json.assert_called_once_with("http://room.local/api/live-agents/claude-terminal/room")
        payload = json.loads(stdout.getvalue())
        self.assertEqual([event["id"] for event in payload["lobby_events"]], ["evt-next"])
        self.assertEqual([event["id"] for event in payload["live_events"]], ["live-next"])
        self.assertEqual(payload["last_observed_event_id"], "evt-old")
        self.assertEqual(payload["last_observed_live_event_id"], "live-old")
        self.assertEqual(payload["next_last_observed_event_id"], "evt-next")
        self.assertEqual(payload["next_last_observed_live_event_id"], "live-next")
        self.assertEqual(payload["room"]["shared_memory"]["rolling_summary"][0]["summary"], "Shared room context.")
        self.assertIn("--last-observed-event-id=evt-next", payload["ack_command"])
        self.assertIn("--last-observed-live-event-id=live-next", payload["ack_command"])

    def test_live_agent_wait_next_prefers_official_turn_over_lobby_event(self):
        stdout = StringIO()
        room_payload = {
            "agent": {
                "agent_id": "claude-terminal",
                "meeting_id": "meeting-1",
                "last_observed_event_id": "evt-old",
                "last_observed_live_event_id": "live-old",
            },
            "lobby_events": [
                {"id": "evt-old", "name": "나", "message": "old"},
                {"id": "evt-next", "name": "나", "message": "lobby question"},
            ],
            "live_events": [
                {"id": "live-old", "kind": "message", "content": "old"},
                {
                    "id": "live-next",
                    "kind": "live_agent_turn_request",
                    "meeting_id": "meeting-1",
                    "target_agent_id": "claude-terminal",
                    "content": "official question",
                },
            ],
        }

        with patch("agentsassemble.cli._request_json", return_value=room_payload):
            with patch("sys.stdout", stdout):
                exit_code = main(
                    [
                        "live-agent",
                        "wait-next",
                        "--server",
                        "http://room.local",
                        "--agent-id",
                        "claude-terminal",
                        "--timeout",
                        "0",
                        "--json",
                    ]
                )

        self.assertEqual(exit_code, 0)
        payload = json.loads(stdout.getvalue())
        self.assertEqual(payload["action"], "official_turn")
        self.assertEqual(payload["event"]["id"], "live-next")
        self.assertEqual(payload["reply_command"][4], "official-reply")

    def test_live_agent_wait_next_prefers_dm_over_official_turn_and_lobby_event(self):
        stdout = StringIO()
        room_payload = {
            "agent": {
                "agent_id": "claude-terminal",
                "meeting_id": "meeting-1",
                "last_observed_event_id": "evt-old",
                "last_observed_live_event_id": "live-old",
                "last_observed_dm_event_id": "dm-old",
            },
            "dm_events": [
                {
                    "id": "dm-old",
                    "friend_id": "friend:claude-terminal",
                    "side": "mine",
                    "target_agent_id": "claude-terminal",
                    "message": "old",
                },
                {
                    "id": "dm-next",
                    "friend_id": "friend:claude-terminal",
                    "side": "mine",
                    "target_agent_id": "claude-terminal",
                    "message": "private question",
                },
            ],
            "lobby_events": [
                {"id": "evt-next", "name": "나", "message": "lobby question"},
            ],
            "live_events": [
                {
                    "id": "live-next",
                    "kind": "live_agent_turn_request",
                    "meeting_id": "meeting-1",
                    "target_agent_id": "claude-terminal",
                    "content": "official question",
                },
            ],
        }

        with patch("agentsassemble.cli._request_json", return_value=room_payload):
            with patch("sys.stdout", stdout):
                exit_code = main(
                    [
                        "live-agent",
                        "wait-next",
                        "--server",
                        "http://room.local",
                        "--agent-id",
                        "claude-terminal",
                        "--timeout",
                        "0",
                        "--json",
                    ]
                )

        self.assertEqual(exit_code, 0)
        payload = json.loads(stdout.getvalue())
        self.assertEqual(payload["action"], "dm")
        self.assertEqual(payload["event"]["id"], "dm-next")
        self.assertEqual(payload["source_event_id"], "dm-next")
        self.assertEqual(payload["reply_command"][4], "dm-reply")
        self.assertIn("--source-event-id", payload["reply_command"])

    def test_live_agent_dm_reply_posts_direct_message(self):
        stdout = StringIO()
        calls = []

        def request_json(url, *, method="GET", payload=None, **kwargs):
            del kwargs
            calls.append({"url": url, "method": method, "payload": payload})
            return {"event": {"id": "reply-1", "message": payload["message"]}}

        with patch("agentsassemble.cli._request_json", side_effect=request_json):
            with patch("sys.stdout", stdout):
                exit_code = main(
                    [
                        "live-agent",
                        "dm-reply",
                        "--server",
                        "http://room.local",
                        "--agent-id",
                        "claude-terminal",
                        "--source-event-id",
                        "dm-next",
                        "--json",
                        "--",
                        "direct answer",
                    ]
                )

        self.assertEqual(exit_code, 0)
        self.assertEqual(calls[0]["url"], "http://room.local/api/live-agents/claude-terminal/dm-reply")
        self.assertEqual(calls[0]["method"], "POST")
        self.assertEqual(calls[0]["payload"], {"source_event_id": "dm-next", "message": "direct answer"})
        self.assertEqual(json.loads(stdout.getvalue())["event"]["id"], "reply-1")

    def test_live_agent_wait_next_reports_persona_blocked_official_turn_for_active_persona_agent(self):
        stdout = StringIO()
        room_payload = {
            "agent": {
                "agent_id": "claude-terminal",
                "meeting_id": "meeting-1",
                "last_observed_live_event_id": "live-old",
                "persona_card_id": "yanagi",
                "character_mode": "on",
                "connection_kind": "self_service",
            },
            "lobby_events": [],
            "live_events": [
                {"id": "live-old", "kind": "message", "content": "old"},
                {
                    "id": "live-next",
                    "kind": "live_agent_turn_request",
                    "meeting_id": "meeting-1",
                    "target_agent_id": "claude-terminal",
                    "content": "official question",
                },
            ],
        }

        with patch("agentsassemble.cli._request_json", return_value=room_payload):
            with patch("sys.stdout", stdout):
                exit_code = main(
                    [
                        "live-agent",
                        "wait-next",
                        "--server",
                        "http://room.local",
                        "--agent-id",
                        "claude-terminal",
                        "--timeout",
                        "0",
                        "--json",
                    ]
                )

        self.assertEqual(exit_code, 0)
        payload = json.loads(stdout.getvalue())
        self.assertEqual(payload["status"], "event")
        self.assertEqual(payload["action"], "persona_blocks_official_turn")
        self.assertEqual(payload["source_event_id"], "live-next")
        self.assertEqual(payload["event"]["id"], "live-next")
        self.assertEqual(payload["reason"], "persona_context_blocked_official_turn")
        self.assertEqual(payload["attention"], ["persona_context_blocked_official_turn"])
        self.assertNotIn("reply_command", payload)
        self.assertIn("--last-observed-live-event-id=live-next", payload["ack_command"])
        self.assertIn("--last-attention=persona_context_blocked_official_turn", payload["ack_command"])

    def test_live_agent_wait_official_turn_reports_persona_blocked_official_turn_for_active_persona_agent(self):
        stdout = StringIO()
        room_payload = {
            "agent": {
                "agent_id": "claude-terminal",
                "meeting_id": "meeting-1",
                "last_observed_live_event_id": "live-old",
                "persona_card_id": "yanagi",
                "character_mode": "on",
                "connection_kind": "self_service",
            },
            "live_events": [
                {"id": "live-old", "kind": "message", "content": "old"},
                {
                    "id": "live-next",
                    "kind": "live_agent_turn_request",
                    "meeting_id": "meeting-1",
                    "target_agent_id": "claude-terminal",
                    "content": "official question",
                },
            ],
        }

        with patch("agentsassemble.cli._request_json", return_value=room_payload):
            with patch("sys.stdout", stdout):
                exit_code = main(
                    [
                        "live-agent",
                        "wait-official-turn",
                        "--server",
                        "http://room.local",
                        "--agent-id",
                        "claude-terminal",
                        "--timeout",
                        "0",
                        "--json",
                    ]
                )

        self.assertEqual(exit_code, 0)
        payload = json.loads(stdout.getvalue())
        self.assertEqual(payload["status"], "event")
        self.assertEqual(payload["action"], "persona_blocks_official_turn")
        self.assertEqual(payload["source_event_id"], "live-next")
        self.assertEqual(payload["event"]["id"], "live-next")
        self.assertEqual(payload["reason"], "persona_context_blocked_official_turn")
        self.assertEqual(payload["attention"], ["persona_context_blocked_official_turn"])
        self.assertNotIn("reply_command", payload)
        self.assertIn("--last-observed-live-event-id=live-next", payload["ack_command"])
        self.assertIn("--last-attention=persona_context_blocked_official_turn", payload["ack_command"])

    def test_live_agent_wait_next_returns_lobby_event_when_no_official_turn_is_pending(self):
        stdout = StringIO()
        room_payload = {
            "agent": {
                "agent_id": "claude-terminal",
                "display_name": "Claude Terminal",
                "last_observed_event_id": "evt-old",
            },
            "shared_memory": {
                "official_event_count": 1,
                "rolling_summary": [
                    {"event_id": "reply-1", "speaker": "Architect", "summary": "Shared room context."}
                ],
            },
            "lobby_events": [
                {"id": "evt-old", "name": "나", "message": "old"},
                {"id": "evt-next", "name": "나", "message": "lobby question"},
            ],
            "live_events": [],
        }

        with patch("agentsassemble.cli._request_json", return_value=room_payload):
            with patch("sys.stdout", stdout):
                exit_code = main(
                    [
                        "live-agent",
                        "wait-next",
                        "--server",
                        "http://room.local",
                        "--agent-id",
                        "claude-terminal",
                        "--timeout",
                        "0",
                        "--json",
                    ]
                )

        self.assertEqual(exit_code, 0)
        payload = json.loads(stdout.getvalue())
        self.assertEqual(payload["action"], "lobby")
        self.assertEqual(payload["event"]["id"], "evt-next")
        self.assertEqual(payload["reply_command"][4], "say")
        self.assertEqual(payload["room"]["shared_memory"]["rolling_summary"][0]["summary"], "Shared room context.")

    def test_live_agent_wait_next_observes_unmentioned_lobby_event_without_reply_command(self):
        stdout = StringIO()
        room_payload = {
            "agent": {
                "agent_id": "claude-terminal",
                "display_name": "Claude Terminal",
                "engagement_mode": "mentioned",
                "last_observed_event_id": "evt-old",
            },
            "lobby_events": [
                {"id": "evt-old", "name": "나", "message": "old"},
                {"id": "evt-observe", "name": "나", "message": "general room note"},
            ],
            "live_events": [],
        }

        with patch("agentsassemble.cli._request_json", return_value=room_payload):
            with patch("sys.stdout", stdout):
                exit_code = main(
                    [
                        "live-agent",
                        "wait-next",
                        "--server",
                        "http://room.local",
                        "--agent-id",
                        "claude-terminal",
                        "--timeout",
                        "0",
                        "--json",
                    ]
                )

        self.assertEqual(exit_code, 0)
        payload = json.loads(stdout.getvalue())
        self.assertEqual(payload["action"], "observe_lobby")
        self.assertEqual(payload["source_event_id"], "evt-observe")
        self.assertEqual(payload["engagement_mode"], "mentioned")
        self.assertNotIn("reply_command", payload)
        self.assertIn("--last-observed-event-id=evt-observe", payload["ack_command"])
        self.assertIn("--last-error=", payload["ack_command"])

    def test_live_agent_wait_next_observes_manual_and_watch_lobby_events_without_replying(self):
        for engagement_mode in ("manual", "watch"):
            with self.subTest(engagement_mode=engagement_mode):
                stdout = StringIO()
                room_payload = {
                    "agent": {
                        "agent_id": "claude-terminal",
                        "display_name": "Claude Terminal",
                        "engagement_mode": engagement_mode,
                        "last_observed_event_id": "evt-old",
                    },
                    "lobby_events": [
                        {"id": "evt-old", "name": "나", "message": "old"},
                        {"id": "evt-observe", "name": "나", "message": "general room note"},
                    ],
                    "live_events": [],
                }

                with patch("agentsassemble.cli._request_json", return_value=room_payload):
                    with patch("sys.stdout", stdout):
                        exit_code = main(
                            [
                                "live-agent",
                                "wait-next",
                                "--server",
                                "http://room.local",
                                "--agent-id",
                                "claude-terminal",
                                "--timeout",
                                "0",
                                "--json",
                            ]
                        )

                self.assertEqual(exit_code, 0)
                payload = json.loads(stdout.getvalue())
                self.assertEqual(payload["action"], "observe_lobby")
                self.assertEqual(payload["source_event_id"], "evt-observe")
                self.assertEqual(payload["engagement_mode"], engagement_mode)
                self.assertNotIn("reply_command", payload)
                self.assertIn("--last-observed-event-id=evt-observe", payload["ack_command"])

    def test_live_agent_wait_next_observes_over_depth_lobby_event_without_replying(self):
        stdout = StringIO()
        room_payload = {
            "agent": {
                "agent_id": "claude-terminal",
                "display_name": "Claude Terminal",
                "engagement_mode": "always",
                "last_observed_event_id": "evt-old",
            },
            "lobby_events": [
                {"id": "evt-old", "name": "나", "message": "old"},
                {
                    "id": "evt-chain",
                    "name": "Gemini",
                    "message": "chain reply",
                    "auto_chain_depth": 2,
                },
            ],
            "live_events": [],
        }

        with patch("agentsassemble.cli._request_json", return_value=room_payload):
            with patch("sys.stdout", stdout):
                exit_code = main(
                    [
                        "live-agent",
                        "wait-next",
                        "--server",
                        "http://room.local",
                        "--agent-id",
                        "claude-terminal",
                        "--max-chain-depth",
                        "1",
                        "--timeout",
                        "0",
                        "--json",
                    ]
                )

        self.assertEqual(exit_code, 0)
        payload = json.loads(stdout.getvalue())
        self.assertEqual(payload["action"], "observe_lobby")
        self.assertEqual(payload["source_event_id"], "evt-chain")
        self.assertEqual(payload["engagement_mode"], "always")
        self.assertNotIn("reply_command", payload)
        self.assertIn("--last-observed-event-id=evt-chain", payload["ack_command"])

    def test_live_agent_wait_next_continues_past_observable_lobby_events_to_replyable_event(self):
        stdout = StringIO()
        room_payload = {
            "agent": {
                "agent_id": "claude-terminal",
                "display_name": "Claude Terminal",
                "engagement_mode": "mentioned",
                "last_observed_event_id": "evt-old",
            },
            "lobby_events": [
                {"id": "evt-old", "name": "나", "message": "old"},
                {"id": "evt-observe", "name": "나", "message": "general room note"},
                {"id": "evt-reply", "name": "나", "message": "Claude Terminal 확인해줘"},
            ],
            "live_events": [],
        }

        with patch("agentsassemble.cli._request_json", return_value=room_payload):
            with patch("sys.stdout", stdout):
                exit_code = main(
                    [
                        "live-agent",
                        "wait-next",
                        "--server",
                        "http://room.local",
                        "--agent-id",
                        "claude-terminal",
                        "--timeout",
                        "0",
                        "--json",
                    ]
                )

        self.assertEqual(exit_code, 0)
        payload = json.loads(stdout.getvalue())
        self.assertEqual(payload["action"], "lobby")
        self.assertEqual(payload["source_event_id"], "evt-reply")
        self.assertIn("reply_command", payload)

    def test_live_agent_wait_next_reply_command_preserves_flow_metadata_for_cli_tool_loop(self):
        stdout = StringIO()
        room_payload = {
            "agent": {
                "agent_id": "claude-terminal",
                "display_name": "Claude Terminal",
                "engagement_mode": "always",
                "last_observed_event_id": "",
            },
            "meeting_id": "m1",
            "lobby_events": [
                {
                    "id": "flow-start",
                    "name": "Play Mode",
                    "message": "flow started",
                    "flow_id": "flow-1",
                    "flow_meeting_id": "m1",
                    "flow_event_type": "started",
                }
            ],
            "live_events": [],
        }

        with patch("agentsassemble.cli._request_json", return_value=room_payload):
            with patch("sys.stdout", stdout):
                exit_code = main(
                    [
                        "live-agent",
                        "wait-next",
                        "--server",
                        "http://room.local",
                        "--agent-id",
                        "claude-terminal",
                        "--timeout",
                        "0",
                        "--json",
                    ]
                )

        self.assertEqual(exit_code, 0)
        payload = json.loads(stdout.getvalue())
        self.assertEqual(payload["action"], "lobby")
        self.assertIn("--flow-id", payload["reply_command"])
        self.assertIn("flow-1", payload["reply_command"])
        self.assertIn("--flow-meeting-id", payload["reply_command"])
        self.assertIn("m1", payload["reply_command"])

    def test_live_agent_wait_next_returns_targeted_return_packet_before_lobby(self):
        stdout = StringIO()
        room_payload = {
            "agent": {
                "agent_id": "claude-terminal",
                "meeting_id": "meeting-1",
                "last_observed_event_id": "evt-old",
                "last_observed_live_event_id": "live-old",
            },
            "lobby_events": [
                {"id": "evt-old", "name": "나", "message": "old"},
                {"id": "evt-next", "name": "나", "message": "lobby question"},
            ],
            "live_events": [
                {"id": "live-old", "kind": "message", "content": "old"},
                {
                    "id": "packet-other",
                    "kind": "artifact",
                    "artifact_kind": "return_packet",
                    "target_agent_id": "other-agent",
                    "artifact_path": "return_packets/other.md",
                    "content": "Other packet ready.",
                },
                {
                    "id": "packet-broadcast",
                    "kind": "artifact",
                    "artifact_kind": "return_packet",
                    "audience": "room",
                    "artifact_path": "return_packets/broadcast.md",
                    "content": "Broadcast packet should not be actionable.",
                },
                {
                    "id": "packet-next",
                    "kind": "artifact",
                    "artifact_kind": "return_packet",
                    "meeting_id": "meeting-1",
                    "target_agent_id": "claude-terminal",
                    "role_id": "architect",
                    "artifact_path": "return_packets/architect.md",
                    "artifact_json_path": "return_packets/architect.json",
                    "content": "Return packet ready.",
                },
            ],
        }

        with patch("agentsassemble.cli._request_json", return_value=room_payload):
            with patch("sys.stdout", stdout):
                exit_code = main(
                    [
                        "live-agent",
                        "wait-next",
                        "--server",
                        "http://room.local",
                        "--agent-id",
                        "claude-terminal",
                        "--timeout",
                        "0",
                        "--json",
                    ]
                )

        self.assertEqual(exit_code, 0)
        payload = json.loads(stdout.getvalue())
        self.assertEqual(payload["action"], "return_packet")
        self.assertEqual(payload["event"]["id"], "packet-next")
        self.assertEqual(payload["artifact_path"], "return_packets/architect.md")
        self.assertEqual(payload["artifact_json_path"], "return_packets/architect.json")
        self.assertIn("return-packet", payload["read_command"])
        self.assertIn("--meeting-id", payload["read_command"])
        self.assertIn("meeting-1", payload["read_command"])
        self.assertIn("--source-event-id", payload["read_command"])
        self.assertIn("packet-next", payload["read_command"])
        self.assertIn("--status", payload["ack_command"])
        self.assertIn("online", payload["ack_command"])
        self.assertIn("--last-error=", payload["ack_command"])
        self.assertIn("--last-observed-live-event-id=packet-next", payload["ack_command"])
        self.assertNotIn("other.md", json.dumps(payload, ensure_ascii=False))
        self.assertNotIn("broadcast.md", json.dumps(payload, ensure_ascii=False))

    def test_live_agent_return_packet_cli_reads_targeted_packet(self):
        stdout = StringIO()
        response = {
            "status": "ok",
            "agent_id": "claude-terminal",
            "meeting_id": "meeting-1",
            "source_event_id": "packet-next",
            "artifact_path": "return_packets/architect.md",
            "artifact_json_path": "return_packets/architect.json",
            "markdown": "Architect private return packet.",
            "json": {"role_id": "architect"},
        }

        with patch("agentsassemble.cli._request_json", return_value=response) as request_json:
            with patch("sys.stdout", stdout):
                exit_code = main(
                    [
                        "live-agent",
                        "return-packet",
                        "--server",
                        "http://room.local",
                        "--agent-id",
                        "claude-terminal",
                        "--meeting-id",
                        "meeting-1",
                        "--source-event-id",
                        "packet-next",
                        "--json",
                    ]
                )

        self.assertEqual(exit_code, 0)
        request_json.assert_called_once()
        self.assertEqual(
            request_json.call_args.args[0],
            "http://room.local/api/live-agents/claude-terminal/return-packet?meeting_id=meeting-1&source_event_id=packet-next",
        )
        payload = json.loads(stdout.getvalue())
        self.assertEqual(payload["markdown"], "Architect private return packet.")
        self.assertEqual(payload["json"]["role_id"], "architect")

    def test_live_agent_wait_next_does_not_use_lobby_cursor_for_official_turns(self):
        stdout = StringIO()
        room_payload = {
            "agent": {
                "agent_id": "claude-terminal",
                "meeting_id": "meeting-1",
                "last_observed_event_id": "evt-lobby",
                "last_observed_live_event_id": "live-newer",
            },
            "lobby_events": [{"id": "evt-lobby", "name": "나", "message": "old lobby"}],
            "live_events": [
                {
                    "id": "live-old-request",
                    "kind": "live_agent_turn_request",
                    "meeting_id": "meeting-1",
                    "target_agent_id": "claude-terminal",
                    "content": "old official request",
                },
                {"id": "live-newer", "kind": "message", "channel": "official", "content": "newer marker"},
            ],
        }

        with patch("agentsassemble.cli._request_json", return_value=room_payload):
            with patch("sys.stdout", stdout):
                exit_code = main(
                    [
                        "live-agent",
                        "wait-next",
                        "--server",
                        "http://room.local",
                        "--agent-id",
                        "claude-terminal",
                        "--after-event-id",
                        "evt-lobby",
                        "--timeout",
                        "0",
                        "--json",
                    ]
                )

        self.assertEqual(exit_code, 1)
        payload = json.loads(stdout.getvalue())
        self.assertEqual(payload["status"], "timeout")
        self.assertEqual(payload["last_observed_event_id"], "evt-lobby")
        self.assertEqual(payload["last_observed_live_event_id"], "live-newer")

    def test_live_agent_wait_next_times_out_with_both_cursors(self):
        stdout = StringIO()
        room_payload = {
            "agent": {
                "agent_id": "claude-terminal",
                "last_observed_event_id": "evt-old",
                "last_observed_live_event_id": "live-old",
            },
            "lobby_events": [{"id": "evt-old", "name": "나", "message": "old"}],
            "live_events": [{"id": "live-old", "kind": "message", "content": "old"}],
        }

        with patch("agentsassemble.cli._request_json", return_value=room_payload):
            with patch("sys.stdout", stdout):
                exit_code = main(
                    [
                        "live-agent",
                        "wait-next",
                        "--server",
                        "http://room.local",
                        "--agent-id",
                        "claude-terminal",
                        "--timeout",
                        "0",
                        "--json",
                    ]
                )

        self.assertEqual(exit_code, 1)
        payload = json.loads(stdout.getvalue())
        self.assertEqual(payload["status"], "timeout")
        self.assertEqual(payload["last_observed_event_id"], "evt-old")
        self.assertEqual(payload["last_observed_live_event_id"], "live-old")

    def test_live_agent_wait_next_timeout_reports_latest_observed_cursors_for_skipped_self_events(self):
        stdout = StringIO()
        room_payload = {
            "agent": {
                "agent_id": "claude-terminal",
                "last_observed_event_id": "evt-old",
                "last_observed_live_event_id": "live-old",
            },
            "lobby_events": [
                {"id": "evt-old", "name": "나", "message": "old"},
                {"id": "evt-self", "actor_id": "claude-terminal", "name": "Claude Terminal", "message": "self"},
            ],
            "live_events": [
                {"id": "live-old", "kind": "message", "content": "old"},
                {"id": "live-info", "kind": "message", "content": "visible non-turn update"},
            ],
        }

        with patch("agentsassemble.cli._request_json", return_value=room_payload):
            with patch("sys.stdout", stdout):
                exit_code = main(
                    [
                        "live-agent",
                        "wait-next",
                        "--server",
                        "http://room.local",
                        "--agent-id",
                        "claude-terminal",
                        "--max-chain-depth",
                        "1",
                        "--timeout",
                        "0",
                        "--json",
                    ]
                )

        self.assertEqual(exit_code, 1)
        payload = json.loads(stdout.getvalue())
        self.assertEqual(payload["status"], "timeout")
        self.assertEqual(payload["last_observed_event_id"], "evt-self")
        self.assertEqual(payload["last_observed_live_event_id"], "live-info")

    def test_live_agent_official_self_service_round_trip_against_gui_server(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "room"
            (root / "meetings" / "m1").mkdir(parents=True)
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                server_url = f"http://127.0.0.1:{server.server_port}"
                with patch("sys.stdout", StringIO()):
                    register_exit = main(
                        [
                            "live-agent",
                            "register",
                            "--server",
                            server_url,
                            "--agent-id",
                            "claude-terminal",
                            "--display-name",
                            "Claude Terminal",
                            "--provider-kind",
                            "claude_code",
                            "--connection-kind",
                            "manual",
                            "--meeting-id",
                            "m1",
                            "--engagement-mode",
                            "moderator_called",
                        ]
                    )
                call_stdout = StringIO()
                with patch("sys.stdout", call_stdout):
                    call_exit = main(
                        [
                            "live-agent",
                            "call",
                            "--server",
                            server_url,
                            "--meeting-id",
                            "m1",
                            "--agent-id",
                            "claude-terminal",
                            "--role-id",
                            "architect",
                            "--json",
                            "Give the official answer.",
                        ]
                    )
                wait_stdout = StringIO()
                with patch("sys.stdout", wait_stdout):
                    wait_exit = main(
                        [
                            "live-agent",
                            "wait-official-turn",
                            "--server",
                            server_url,
                            "--agent-id",
                            "claude-terminal",
                            "--timeout",
                            "0",
                            "--json",
                        ]
                    )
                wait_payload = json.loads(wait_stdout.getvalue())
                reply_stdout = StringIO()
                with patch("sys.stdout", reply_stdout):
                    reply_exit = main(
                        [
                            "live-agent",
                            "official-reply",
                            "--server",
                            server_url,
                            "--agent-id",
                            "claude-terminal",
                            "--meeting-id",
                            "m1",
                            "--source-event-id",
                            wait_payload["source_event_id"],
                            "--json",
                            "Official self-service reply.",
                        ]
                    )
                operations = cli_module._request_json(f"{server_url}/api/live-agent-operations")
                persisted_agent = json.loads((root / "live_agents.json").read_text(encoding="utf-8"))["agents"][0]
                shared_memory_written = (root / "meetings" / "m1" / "shared_memory" / "rolling-summary.md").exists()
            finally:
                server.shutdown()
                server.server_close()

        self.assertEqual((register_exit, call_exit, wait_exit, reply_exit), (0, 0, 0, 0))
        call_payload = json.loads(call_stdout.getvalue())
        reply_payload = json.loads(reply_stdout.getvalue())
        self.assertEqual(wait_payload["source_event_id"], call_payload["event"]["id"])
        self.assertEqual(wait_payload["meeting_id"], "m1")
        self.assertEqual(reply_payload["event"]["source_event_id"], wait_payload["source_event_id"])
        self.assertEqual(reply_payload["event"]["content"], "Official self-service reply.")
        self.assertEqual(reply_payload["shared_memory"]["shared_memory_official_event_count"], 1)
        self.assertTrue(shared_memory_written)
        self.assertEqual(persisted_agent["last_observed_live_event_id"], wait_payload["source_event_id"])
        self.assertIn("official_turn.reply", [item["operation"] for item in operations["operations"]])
