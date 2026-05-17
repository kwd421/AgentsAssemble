import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

from agentsassemble.live_agents import connect_live_agent, heartbeat_live_agent, read_live_agents


class LiveAgentPresenceTests(unittest.TestCase):
    def test_connect_live_agent_upserts_sanitized_presence(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            now = datetime(2026, 5, 17, 12, 0, tzinfo=UTC)

            agent = connect_live_agent(
                root,
                {
                    "agent_id": " claude-live\n",
                    "display_name": "Claude\nCode",
                    "provider_kind": "claude_code",
                    "connection_kind": "local_cli",
                    "session_id": "claude-session-1",
                    "engagement_mode": "mentioned",
                    "meeting_id": "m1",
                    "capabilities": ["room_chat", "mentions"],
                },
                now=now,
            )

            self.assertEqual(agent["agent_id"], "claude-live")
            self.assertEqual(agent["display_name"], "Claude Code")
            self.assertEqual(agent["provider_kind"], "claude_code")
            self.assertEqual(agent["connection_kind"], "local_cli")
            self.assertEqual(agent["status"], "online")
            self.assertEqual(agent["last_seen_at"], "2026-05-17T12:00:00+00:00")
            self.assertEqual(agent["capabilities"], ["room_chat", "mentions"])
            self.assertEqual(read_live_agents(root, now=now), [agent])

    def test_read_live_agents_marks_quiet_online_agents_stale(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            started = datetime(2026, 5, 17, 12, 0, tzinfo=UTC)
            later = started + timedelta(seconds=181)

            connect_live_agent(root, {"agent_id": "remote-claude", "display_name": "Remote Claude"}, now=started)

            agents = read_live_agents(root, now=later, stale_after_seconds=180)

            self.assertEqual(agents[0]["agent_id"], "remote-claude")
            self.assertEqual(agents[0]["status"], "stale")

    def test_heartbeat_updates_status_without_losing_connection_metadata(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            started = datetime(2026, 5, 17, 12, 0, tzinfo=UTC)
            pinged = started + timedelta(seconds=45)

            connect_live_agent(
                root,
                {
                    "agent_id": "gemini-cli",
                    "display_name": "Gemini CLI",
                    "provider_kind": "gemini",
                    "connection_kind": "local_cli",
                    "session_id": "gemini-session",
                },
                now=started,
            )
            agent = heartbeat_live_agent(root, "gemini-cli", status="working", now=pinged)

            self.assertEqual(agent["display_name"], "Gemini CLI")
            self.assertEqual(agent["provider_kind"], "gemini")
            self.assertEqual(agent["connection_kind"], "local_cli")
            self.assertEqual(agent["session_id"], "gemini-session")
            self.assertEqual(agent["status"], "working")
            self.assertEqual(agent["last_seen_at"], "2026-05-17T12:00:45+00:00")

    def test_connect_live_agent_rejects_blank_agent_id(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            with self.assertRaises(ValueError):
                connect_live_agent(Path(temp_dir), {"agent_id": "\n "})

    def test_connect_live_agent_preserves_live_session_connection_kind(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            agent = connect_live_agent(
                Path(temp_dir),
                {
                    "agent_id": "jsonl-session",
                    "display_name": "JSONL Session",
                    "connection_kind": "live_session",
                },
            )

        self.assertEqual(agent["connection_kind"], "live_session")

    def test_connect_live_agent_preserves_diagnostic_flag(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            agent = connect_live_agent(
                Path(temp_dir),
                {
                    "agent_id": "doctor-smoke-local-cli",
                    "display_name": "Smoke Local CLI",
                    "diagnostic": True,
                },
            )

            self.assertTrue(agent["diagnostic"])
            self.assertTrue(read_live_agents(Path(temp_dir))[0]["diagnostic"])
