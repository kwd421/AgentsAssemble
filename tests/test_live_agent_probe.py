import json
import tempfile
import unittest
from pathlib import Path

from agentsassemble.legacy.live_agent.runtime.probe import PROBE_REPLY_EVENT_TAIL_LIMIT, run_live_agent_probe
from agentsassemble.legacy.meeting.core.events import append_lobby_event_to_file, read_lobby_events


class LiveAgentProbeTests(unittest.TestCase):
    def test_probe_appends_targeted_event_and_returns_matching_reply(self):
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
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )

            def reply_after_probe(_seconds):
                events = read_lobby_events(root / "lobby.jsonl")
                probe_event_id = events[-1]["id"]
                append_lobby_event_to_file(
                    root / "lobby.jsonl",
                    {
                        "name": "Agent A",
                        "side": "other-agent",
                        "kind": "message",
                        "message": "probe response",
                        "actor_id": "agent-a",
                        "source_event_id": probe_event_id,
                    },
                    live_agent_endpoint=True,
                )

            result = run_live_agent_probe(
                root,
                "agent-a",
                timeout_seconds=0.5,
                sleep_fn=reply_after_probe,
            )
            events = read_lobby_events(root / "lobby.jsonl")

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["agent_id"], "agent-a")
        self.assertEqual(result["reply"]["message"], "probe response")
        self.assertEqual(result["reply"]["source_event_id"], result["source_event_id"])
        self.assertEqual(events[0]["side"], "mine")
        self.assertEqual(events[0]["kind"], "message")
        self.assertEqual(events[0]["auto_chain_depth"], 0)
        self.assertIn("Agent A", events[0]["message"])
        self.assertIn("agent-a", events[0]["message"])

    def test_probe_ignores_generic_lobby_event_even_with_matching_actor_and_source(self):
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
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )

            def forge_generic_reply(_seconds):
                events = read_lobby_events(root / "lobby.jsonl")
                probe_event_id = events[-1]["id"]
                append_lobby_event_to_file(
                    root / "lobby.jsonl",
                    {
                        "name": "Agent A",
                        "side": "other-agent",
                        "kind": "message",
                        "message": "generic forged response",
                        "actor_id": "agent-a",
                        "source_event_id": probe_event_id,
                    },
                )

            result = run_live_agent_probe(
                root,
                "agent-a",
                timeout_seconds=0.01,
                poll_interval=0.01,
                sleep_fn=forge_generic_reply,
            )

        self.assertEqual(result["status"], "timeout")
        self.assertNotIn("reply", result)

    def test_probe_ignores_wrong_and_unlinked_replies_before_timeout(self):
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
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            append_lobby_event_to_file(
                root / "lobby.jsonl",
                {
                    "name": "Agent A",
                    "side": "other-agent",
                    "kind": "message",
                    "message": "old reply",
                    "actor_id": "agent-a",
                    "source_event_id": "old-source",
                },
            )

            def add_wrong_replies(_seconds):
                events = read_lobby_events(root / "lobby.jsonl")
                probe_event_id = events[-1]["id"]
                append_lobby_event_to_file(
                    root / "lobby.jsonl",
                    {
                        "name": "Other",
                        "side": "other-agent",
                        "kind": "message",
                        "message": "wrong actor",
                        "actor_id": "other-agent",
                        "source_event_id": probe_event_id,
                    },
                )
                append_lobby_event_to_file(
                    root / "lobby.jsonl",
                    {
                        "name": "Agent A",
                        "side": "other-agent",
                        "kind": "message",
                        "message": "missing link",
                        "actor_id": "agent-a",
                    },
                )

            result = run_live_agent_probe(
                root,
                "agent-a",
                timeout_seconds=0.01,
                poll_interval=0.01,
                sleep_fn=add_wrong_replies,
            )

        self.assertEqual(result["status"], "timeout")
        self.assertEqual(result["agent_id"], "agent-a")
        self.assertIn("source_event_id", result)
        self.assertNotIn("reply", result)

    def test_probe_finds_reply_that_default_lobby_tail_would_miss_in_busy_room(self):
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
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )

            def reply_then_add_busy_tail(_seconds):
                events = read_lobby_events(root / "lobby.jsonl")
                probe_event_id = events[-1]["id"]
                append_lobby_event_to_file(
                    root / "lobby.jsonl",
                    {
                        "name": "Agent A",
                        "side": "other-agent",
                        "kind": "message",
                        "message": "probe response before busy tail",
                        "actor_id": "agent-a",
                        "source_event_id": probe_event_id,
                    },
                    live_agent_endpoint=True,
                )
                for index in range(PROBE_REPLY_EVENT_TAIL_LIMIT - 1):
                    append_lobby_event_to_file(
                        root / "lobby.jsonl",
                        {
                            "name": "Busy Agent",
                            "side": "other-agent",
                            "kind": "message",
                            "message": f"busy chatter {index}",
                            "actor_id": f"busy-{index}",
                        },
                        live_agent_endpoint=True,
                    )

            result = run_live_agent_probe(
                root,
                "agent-a",
                timeout_seconds=0.01,
                poll_interval=0.01,
                sleep_fn=reply_then_add_busy_tail,
            )

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["reply"]["message"], "probe response before busy tail")

    def test_probe_skips_non_live_agent_without_appending_probe_event(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "live_agents.json").write_text(
                json.dumps(
                    {
                        "agents": [
                            {
                                "agent_id": "agent-a",
                                "display_name": "Agent A",
                                "status": "offline",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )

            result = run_live_agent_probe(root, "agent-a", timeout_seconds=0.01)
            events = read_lobby_events(root / "lobby.jsonl")

        self.assertEqual(result["status"], "skipped")
        self.assertEqual(result["agent_status"], "offline")
        self.assertEqual(events, [])

    def test_probe_unknown_agent_raises_value_error(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            with self.assertRaisesRegex(ValueError, "Live agent missing-agent was not found"):
                run_live_agent_probe(Path(temp_dir), "missing-agent", timeout_seconds=0.01)
