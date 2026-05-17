import tempfile
import unittest
from pathlib import Path

from agentsassemble.live_agent_smoke import (
    LiveAgentSmokeFailed,
    build_live_agent_smoke_config,
    run_live_agent_smoke,
    seed_smoke_agent_cursors,
)


class LiveAgentSmokeTests(unittest.TestCase):
    def test_builds_credential_free_group_config_with_all_resident_connection_kinds(self):
        config = build_live_agent_smoke_config(
            server="http://127.0.0.1:8765",
            agent_ids={
                "local_cli": "smoke-local",
                "live_session": "smoke-session",
                "remote_bridge": "smoke-bridge",
            },
            python_executable="/usr/bin/python3",
            bridge_endpoint="http://127.0.0.1:7777",
            bridge_auth_ref="literal:smoke-token",
        )

        self.assertEqual(config["server"], "http://127.0.0.1:8765")
        agents = {agent["agent_id"]: agent for agent in config["agents"]}
        self.assertEqual(agents["smoke-local"]["connection_kind"], "local_cli")
        self.assertEqual(agents["smoke-session"]["connection_kind"], "live_session")
        self.assertEqual(agents["smoke-bridge"]["connection_kind"], "remote_bridge")
        self.assertEqual(agents["smoke-bridge"]["provider_kind"], "remote_http_bridge")
        self.assertEqual(agents["smoke-bridge"]["endpoint"], "http://127.0.0.1:7777")
        self.assertEqual(agents["smoke-bridge"]["auth_ref"], "literal:smoke-token")
        self.assertNotIn("command", agents["smoke-bridge"])
        self.assertEqual(agents["smoke-local"]["command"][0], "/usr/bin/python3")
        self.assertEqual(agents["smoke-session"]["command"][0], "/usr/bin/python3")
        self.assertIn("smoke local_cli ok", agents["smoke-local"]["command"][-1])
        self.assertIn("smoke live_session ok", agents["smoke-session"]["command"][-1])
        self.assertNotIn("claude", str(config).casefold())
        self.assertNotIn("gemini", str(config).casefold())

    def test_smoke_seeds_agent_cursors_before_probe_event(self):
        calls = []

        def request_json(url, *, method="GET", payload=None):
            calls.append((url, method, payload))
            return {"agent": {"agent_id": (payload or {}).get("agent_id", "agent")}}

        seed_smoke_agent_cursors(
            "http://room.local",
            agent_ids={
                "local_cli": "smoke-local",
                "live_session": "smoke-session",
                "remote_bridge": "smoke-bridge",
            },
            last_observed_event_id="old-event",
            request_json=request_json,
        )

        heartbeat_payloads = [payload for url, method, payload in calls if url.endswith("/heartbeat")]
        self.assertEqual(len(heartbeat_payloads), 3)
        self.assertEqual({payload["last_observed_event_id"] for payload in heartbeat_payloads}, {"old-event"})
        self.assertEqual({payload["status"] for payload in heartbeat_payloads}, {"online"})

    def test_smoke_stops_started_group_when_replies_timeout(self):
        calls = []

        def request_json(url, *, method="GET", payload=None):
            calls.append((url, method, payload))
            if url.endswith("/api/lobby") and method == "GET":
                return {"events": [{"id": "old", "message": "old chatter"}]}
            if url.endswith("/api/lobby") and method == "POST":
                return {"event": {"id": "probe"}}
            if url.endswith("/api/live-agent-processes/start"):
                return {"group": {"group_id": "smoke-timeout", "status": "running"}}
            if url.endswith("/api/live-agent-processes"):
                return {"groups": [{"group_id": "smoke-timeout", "status": "running"}]}
            if url.endswith("/api/live-agent-processes/smoke-timeout/stop"):
                return {"group": {"group_id": "smoke-timeout", "status": "stopped"}}
            return {"agent": {"agent_id": "smoke"}}

        with self.assertRaisesRegex(LiveAgentSmokeFailed, "Timed out"):
            run_live_agent_smoke(
                server="http://room.local",
                group_id="smoke-timeout",
                timeout_seconds=0,
                request_json=request_json,
                sleep_fn=lambda seconds: None,
            )

        self.assertTrue(any(url.endswith("/api/live-agent-processes/smoke-timeout/stop") for url, method, payload in calls))

    def test_smoke_ignores_replies_without_live_agent_endpoint_evidence(self):
        calls = []
        state = {"started": False}

        def request_json(url, *, method="GET", payload=None):
            calls.append((url, method, payload))
            if url.endswith("/api/lobby") and method == "GET":
                if state["started"]:
                    return {
                        "events": [
                            {
                                "id": "reply-local",
                                "actor_id": "smoke-forged-local-cli",
                                "message": "smoke local_cli ok",
                                "source_event_id": "probe",
                            },
                            {
                                "id": "reply-session",
                                "actor_id": "smoke-forged-live-session",
                                "message": "smoke live_session ok",
                                "source_event_id": "probe",
                            },
                            {
                                "id": "reply-bridge",
                                "actor_id": "smoke-forged-remote-bridge",
                                "message": "smoke remote_bridge ok",
                                "source_event_id": "probe",
                            },
                        ]
                    }
                return {"events": []}
            if url.endswith("/api/live-agents") or url.endswith("/heartbeat"):
                return {"agent": {"agent_id": "smoke"}}
            if url.endswith("/api/lobby") and method == "POST":
                return {"event": {"id": "probe"}}
            if url.endswith("/api/live-agent-processes/start"):
                state["started"] = True
                return {"group": {"group_id": "smoke-forged", "status": "running"}}
            if url.endswith("/api/live-agent-processes"):
                return {"groups": [{"group_id": "smoke-forged", "status": "running"}]}
            if url.endswith("/api/live-agent-processes/smoke-forged/stop"):
                return {"group": {"group_id": "smoke-forged", "status": "stopped"}}
            return {}

        with self.assertRaisesRegex(LiveAgentSmokeFailed, "Timed out"):
            run_live_agent_smoke(
                server="http://room.local",
                group_id="smoke-forged",
                timeout_seconds=0.1,
                request_json=request_json,
                sleep_fn=lambda seconds: None,
            )

        self.assertTrue(any(url.endswith("/api/live-agent-processes/smoke-forged/stop") for url, method, payload in calls))

    def test_smoke_writes_absolute_temp_config_path_to_start_payload(self):
        start_payloads = []
        state = {"started": False}

        def request_json(url, *, method="GET", payload=None):
            if url.endswith("/api/lobby") and method == "GET":
                if state["started"]:
                    return {
                        "events": [
                            {
                                "id": "reply-local",
                                "actor_id": "smoke-path-local-cli",
                                "message": "smoke local_cli ok",
                                "source_event_id": "probe",
                                "live_agent_endpoint": True,
                            },
                            {
                                "id": "reply-session",
                                "actor_id": "smoke-path-live-session",
                                "message": "smoke live_session ok",
                                "source_event_id": "probe",
                                "live_agent_endpoint": True,
                            },
                            {
                                "id": "reply-bridge",
                                "actor_id": "smoke-path-remote-bridge",
                                "message": "smoke remote_bridge ok",
                                "source_event_id": "probe",
                                "live_agent_endpoint": True,
                            },
                        ]
                    }
                return {"events": []}
            if url.endswith("/api/live-agents") or url.endswith("/heartbeat"):
                return {"agent": {"agent_id": "smoke"}}
            if url.endswith("/api/lobby") and method == "POST":
                return {"event": {"id": "probe"}}
            if url.endswith("/api/live-agent-processes/start"):
                start_payloads.append(payload)
                state["started"] = True
                return {"group": {"group_id": "smoke-path", "status": "stopped"}}
            if url.endswith("/api/live-agent-processes"):
                return {"groups": [{"group_id": "smoke-path", "status": "stopped"}]}
            return {}

        with tempfile.TemporaryDirectory() as temp_dir:
            result = run_live_agent_smoke(
                server="http://room.local",
                group_id="smoke-path",
                timeout_seconds=0.1,
                request_json=request_json,
                sleep_fn=lambda seconds: None,
                temp_dir_factory=lambda: _FixedTemporaryDirectory(Path(temp_dir)),
            )

        config_path = Path(start_payloads[0]["config_path"])
        self.assertTrue(config_path.is_absolute())
        self.assertEqual(result["status"], "ok")


class _FixedTemporaryDirectory:
    def __init__(self, path: Path):
        self.path = path

    def __enter__(self):
        return str(self.path)

    def __exit__(self, exc_type, exc_value, traceback):
        return False


if __name__ == "__main__":
    unittest.main()
