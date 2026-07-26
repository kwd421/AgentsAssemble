import json
import tempfile
import unittest
from pathlib import Path

from agentsassemble.diagnostics.room_call_metrics import collect_room_call_metrics


class RoomCallMetricsTests(unittest.TestCase):
    def test_combines_exact_token_rows_with_unknown_provider_call_lower_bounds(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            codex_root = root / "codex"
            codex_root.mkdir()
            (codex_root / "rollout-test.jsonl").write_text(
                "\n".join(
                    [
                        json.dumps(
                            {
                                "type": "session_meta",
                                "payload": {"originator": "AgentsAssemble"},
                            }
                        ),
                        json.dumps(
                            {
                                "timestamp": "2026-07-26T00:00:03Z",
                                "type": "event_msg",
                                "payload": {
                                    "type": "token_count",
                                    "info": {
                                        "last_token_usage": {
                                            "input_tokens": 100,
                                            "output_tokens": 7,
                                        }
                                    },
                                },
                            }
                        ),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            grok_log = (
                root
                / "runtime"
                / "rooms"
                / "room-a"
                / "bridges"
                / "grok-grok"
                / "profile"
                / "provider-state"
                / "logs"
                / "unified.jsonl"
            )
            grok_log.parent.mkdir(parents=True)
            grok_log.write_text(
                json.dumps(
                    {
                        "ts": "2026-07-26T00:00:04Z",
                        "msg": "shell.turn.inference_done",
                        "ctx": {"prompt_tokens": 80, "completion_tokens": 5},
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            room_state = {
                "room": {"room_id": "room-a"},
                "sessions": [
                    {
                        "participant_id": "claude-a",
                        "provider_kind": "claude_code",
                    },
                    {
                        "participant_id": "antigravity-a",
                        "provider_kind": "antigravity_live_session",
                    },
                ],
                "events": [
                    {
                        "type": "message_final",
                        "created_at": "2026-07-26T00:00:01Z",
                    },
                    {
                        "type": "turn_started",
                        "participant_id": "claude-a",
                        "created_at": "2026-07-26T00:00:02Z",
                    },
                    {
                        "type": "message_final",
                        "created_at": "2026-07-26T00:00:03.500Z",
                    },
                    {
                        "type": "turn_started",
                        "participant_id": "antigravity-a",
                        "created_at": "2026-07-26T00:00:05Z",
                    },
                ],
            }

            rows = collect_room_call_metrics(
                room_state,
                since="2026-07-26T00:00:00Z",
                until="2026-07-26T00:00:06Z",
                codex_session_root=codex_root,
                runtime_root=root / "runtime",
            )

        self.assertEqual(
            [
                (
                    row["provider"],
                    row["input_tokens"],
                    row["output_tokens"],
                    row["cumulative_room_message_count"],
                )
                for row in rows
            ],
            [
                ("claude", None, None, 1),
                ("codex", 100, 7, 1),
                ("grok", 80, 5, 2),
                ("antigravity", None, None, 2),
            ],
        )

    def test_ignores_non_agentsassemble_codex_sessions_and_out_of_window_calls(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            for name, originator, timestamp in (
                ("other", "Codex Desktop", "2026-07-26T00:00:02Z"),
                ("late", "AgentsAssemble", "2026-07-26T00:01:00Z"),
            ):
                (root / f"{name}.jsonl").write_text(
                    "\n".join(
                        [
                            json.dumps(
                                {
                                    "type": "session_meta",
                                    "payload": {"originator": originator},
                                }
                            ),
                            json.dumps(
                                {
                                    "timestamp": timestamp,
                                    "type": "event_msg",
                                    "payload": {
                                        "type": "token_count",
                                        "info": {
                                            "last_token_usage": {
                                                "input_tokens": 10,
                                                "output_tokens": 1,
                                            }
                                        },
                                    },
                                }
                            ),
                        ]
                    )
                    + "\n",
                    encoding="utf-8",
                )

            rows = collect_room_call_metrics(
                {"room": {"room_id": "room-a"}, "sessions": [], "events": []},
                since="2026-07-26T00:00:00Z",
                until="2026-07-26T00:00:10Z",
                codex_session_root=root,
                runtime_root=root,
            )

        self.assertEqual(rows, [])
