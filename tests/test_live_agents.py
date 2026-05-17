import json
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
            visible = read_live_agents(root, now=now)[0]
            self.assertEqual({key: visible[key] for key in agent}, agent)
            self.assertEqual(visible["heartbeat_age_seconds"], 0)
            self.assertEqual(visible["stale_after_seconds"], 180)

    def test_read_live_agents_marks_quiet_online_agents_stale(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            started = datetime(2026, 5, 17, 12, 0, tzinfo=UTC)
            later = started + timedelta(seconds=181)

            connect_live_agent(root, {"agent_id": "remote-claude", "display_name": "Remote Claude"}, now=started)

            agents = read_live_agents(root, now=later, stale_after_seconds=180)

            self.assertEqual(agents[0]["agent_id"], "remote-claude")
            self.assertEqual(agents[0]["status"], "stale")
            self.assertEqual(agents[0]["heartbeat_age_seconds"], 181)
            self.assertEqual(agents[0]["stale_after_seconds"], 180)

    def test_read_live_agents_reports_boundary_age_that_matches_stale_status(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            started = datetime(2026, 5, 17, 12, 0, tzinfo=UTC)
            later = started + timedelta(seconds=180, microseconds=500000)

            connect_live_agent(root, {"agent_id": "edge-agent", "display_name": "Edge Agent"}, now=started)

            agents = read_live_agents(root, now=later, stale_after_seconds=180)

        self.assertEqual(agents[0]["status"], "stale")
        self.assertEqual(agents[0]["heartbeat_age_seconds"], 181)

    def test_read_live_agents_adds_output_only_freshness_evidence(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            started = datetime(2026, 5, 17, 12, 0, tzinfo=UTC)
            later = started + timedelta(seconds=42)

            connect_live_agent(root, {"agent_id": "fresh-agent", "display_name": "Fresh Agent"}, now=started)
            agents = read_live_agents(root, now=later, stale_after_seconds=180)
            persisted = json.loads((root / "live_agents.json").read_text(encoding="utf-8"))

        self.assertEqual(agents[0]["status"], "online")
        self.assertEqual(agents[0]["heartbeat_age_seconds"], 42)
        self.assertEqual(agents[0]["stale_after_seconds"], 180)
        self.assertNotIn("heartbeat_age_seconds", persisted["agents"][0])
        self.assertNotIn("stale_after_seconds", persisted["agents"][0])

    def test_read_live_agents_ignores_persisted_freshness_evidence_without_valid_heartbeat(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "live_agents.json").write_text(
                json.dumps(
                    {
                        "agents": [
                            {
                                "agent_id": "corrupt-agent",
                                "display_name": "Corrupt Agent",
                                "provider_kind": "manual",
                                "connection_kind": "manual",
                                "status": "online",
                                "last_seen_at": "not-a-timestamp",
                                "heartbeat_age_seconds": 999,
                                "stale_after_seconds": 999,
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )

            agent = read_live_agents(root, stale_after_seconds=180)[0]

        self.assertEqual(agent["agent_id"], "corrupt-agent")
        self.assertEqual(agent["stale_after_seconds"], 180)
        self.assertNotIn("heartbeat_age_seconds", agent)

    def test_heartbeat_removes_persisted_output_only_freshness_evidence(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            started = datetime(2026, 5, 17, 12, 0, tzinfo=UTC)
            (root / "live_agents.json").write_text(
                json.dumps(
                    {
                        "agents": [
                            {
                                "agent_id": "dirty-agent",
                                "display_name": "Dirty Agent",
                                "provider_kind": "manual",
                                "connection_kind": "manual",
                                "status": "online",
                                "engagement_mode": "always",
                                "last_seen_at": "2026-05-17T11:59:00+00:00",
                                "heartbeat_age_seconds": 60,
                                "stale_after_seconds": 180,
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )

            heartbeat_live_agent(root, "dirty-agent", status="online", now=started)
            persisted = json.loads((root / "live_agents.json").read_text(encoding="utf-8"))

        self.assertNotIn("heartbeat_age_seconds", persisted["agents"][0])
        self.assertNotIn("stale_after_seconds", persisted["agents"][0])

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

    def test_connect_live_agent_rejects_unsafe_remote_bridge_endpoint(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            with self.assertRaisesRegex(ValueError, "Remote bridge endpoint"):
                connect_live_agent(
                    root,
                    {
                        "agent_id": "friend-bridge",
                        "connection_kind": "remote_bridge",
                        "endpoint": "http://bridge-token@friend.local:8777?secret=1",
                    },
                )

            self.assertFalse((root / "live_agents.json").exists())

    def test_connect_live_agent_rejects_blank_remote_bridge_endpoint(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            with self.assertRaisesRegex(ValueError, "Remote bridge endpoint"):
                connect_live_agent(
                    root,
                    {
                        "agent_id": "friend-bridge",
                        "connection_kind": "remote_bridge",
                    },
                )

            self.assertFalse((root / "live_agents.json").exists())

    def test_connect_live_agent_rejects_malformed_remote_bridge_endpoint_netloc(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            for endpoint in ("http://:8777", "http://friend.local:bad", "http://friend.local:99999"):
                with self.assertRaisesRegex(ValueError, "valid host and port"):
                    connect_live_agent(
                        root,
                        {
                            "agent_id": f"friend-bridge-{endpoint}",
                            "connection_kind": "remote_bridge",
                            "endpoint": endpoint,
                        },
                    )

            self.assertFalse((root / "live_agents.json").exists())

    def test_connect_live_agent_preserves_safe_remote_bridge_endpoint(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            agent = connect_live_agent(
                Path(temp_dir),
                {
                    "agent_id": "friend-bridge",
                    "connection_kind": "remote_bridge",
                    "endpoint": "http://friend.local:8777",
                },
            )

        self.assertEqual(agent["connection_kind"], "remote_bridge")
        self.assertEqual(agent["endpoint"], "http://friend.local:8777")

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
