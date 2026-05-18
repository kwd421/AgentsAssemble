import json
import tempfile
import unittest
from pathlib import Path

from agentsassemble.live_agent_smoke import (
    LiveAgentSmokeFailed,
    build_live_agent_official_round_smoke_config,
    build_live_agent_smoke_config,
    run_live_agent_official_round_smoke,
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

    def test_builds_official_round_smoke_group_config_in_moderator_called_mode(self):
        config = build_live_agent_official_round_smoke_config(
            server="http://127.0.0.1:8765",
            meeting_id="official-round-smoke",
            agent_ids={
                "local_cli": "round-local",
                "live_session": "round-session",
                "remote_bridge": "round-bridge",
            },
            python_executable="/usr/bin/python3",
            bridge_endpoint="http://127.0.0.1:7777",
            bridge_auth_ref="literal:smoke-token",
        )

        self.assertEqual(config["server"], "http://127.0.0.1:8765")
        self.assertGreaterEqual(config["max_ticks"], 20)
        agents = {agent["agent_id"]: agent for agent in config["agents"]}
        self.assertEqual(agents["round-local"]["connection_kind"], "local_cli")
        self.assertEqual(agents["round-session"]["connection_kind"], "live_session")
        self.assertEqual(agents["round-bridge"]["connection_kind"], "remote_bridge")
        self.assertEqual({agent["engagement_mode"] for agent in agents.values()}, {"moderator_called"})
        self.assertEqual({agent["meeting_id"] for agent in agents.values()}, {"official-round-smoke"})
        self.assertNotIn("claude", str(config).casefold())
        self.assertNotIn("gemini", str(config).casefold())

    def test_official_round_smoke_budgets_round_timeout_and_runner_lifetime(self):
        calls = []

        def request_json(url, *, method="GET", payload=None, timeout_seconds=None):
            calls.append((url, method, payload, timeout_seconds))
            if url.endswith("/api/live-agent-processes/start"):
                config = Path(payload["config_path"]).read_text(encoding="utf-8")
                self.assertIn('"engagement_mode": "moderator_called"', config)
                self.assertIn('"max_ticks": 780', config)
                return {"group": {"group_id": "round-budget", "status": "running"}}
            if url.endswith("/live-agent-turns/round"):
                return _answered_round_result()
            if url.endswith("/api/live-agent-processes"):
                return {"groups": [{"group_id": "round-budget", "status": "running"}]}
            if url.endswith("/api/live-agent-processes/round-budget/stop"):
                return {"group": {"group_id": "round-budget", "status": "stopped"}}
            return {"agent": {"agent_id": "smoke"}}

        with tempfile.TemporaryDirectory() as temp_dir:
            result = run_live_agent_official_round_smoke(
                output_root=Path(temp_dir) / "room",
                server="http://room.local",
                group_id="round-budget",
                timeout_seconds=8,
                request_json=request_json,
                sleep_fn=lambda seconds: None,
                temp_dir_factory=lambda: _FixedTemporaryDirectory(Path(temp_dir) / "config"),
            )

        round_calls = [call for call in calls if call[0].endswith("/live-agent-turns/round")]
        self.assertEqual(round_calls[0][3], 38.0)
        self.assertEqual(result["status"], "ok")

    def test_official_round_smoke_reports_failed_when_cleanup_does_not_stop_group(self):
        def request_json(url, *, method="GET", payload=None, timeout_seconds=None):
            if url.endswith("/api/live-agent-processes/start"):
                return {"group": {"group_id": "round-cleanup", "status": "running"}}
            if url.endswith("/live-agent-turns/round"):
                return _answered_round_result()
            if url.endswith("/api/live-agent-processes"):
                return {"groups": [{"group_id": "round-cleanup", "status": "running"}]}
            if url.endswith("/api/live-agent-processes/round-cleanup/stop"):
                return {"group": {"group_id": "round-cleanup", "status": "error"}}
            return {"agent": {"agent_id": "smoke"}}

        with tempfile.TemporaryDirectory() as temp_dir:
            result = run_live_agent_official_round_smoke(
                output_root=Path(temp_dir) / "room",
                server="http://room.local",
                group_id="round-cleanup",
                timeout_seconds=8,
                request_json=request_json,
                sleep_fn=lambda seconds: None,
                temp_dir_factory=lambda: _FixedTemporaryDirectory(Path(temp_dir) / "config"),
            )

        self.assertEqual(result["status"], "failed")
        self.assertFalse(result["stopped"])

    def test_official_round_smoke_meeting_is_marked_diagnostic(self):
        def request_json(url, *, method="GET", payload=None, timeout_seconds=None):
            if url.endswith("/api/live-agent-processes/start"):
                return {"group": {"group_id": "round-diagnostic", "status": "running"}}
            if url.endswith("/live-agent-turns/round"):
                return _answered_round_result()
            if url.endswith("/api/live-agent-processes"):
                return {"groups": [{"group_id": "round-diagnostic", "status": "running"}]}
            if url.endswith("/api/live-agent-processes/round-diagnostic/stop"):
                return {"group": {"group_id": "round-diagnostic", "status": "stopped"}}
            return {"agent": {"agent_id": "smoke"}}

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "room"
            run_live_agent_official_round_smoke(
                output_root=root,
                server="http://room.local",
                group_id="round-diagnostic",
                timeout_seconds=8,
                request_json=request_json,
                sleep_fn=lambda seconds: None,
                temp_dir_factory=lambda: _FixedTemporaryDirectory(Path(temp_dir) / "config"),
            )

            live_state = (root / "meetings" / "official-round-smoke-round-diagnostic" / "live_state.json").read_text(
                encoding="utf-8"
            )

        meeting = json.loads(live_state)
        self.assertTrue(meeting["diagnostic"])
        self.assertEqual(meeting["diagnostic_kind"], "official_round_smoke")

    def test_official_round_smoke_stops_group_when_round_request_fails(self):
        calls = []

        def request_json(url, *, method="GET", payload=None, timeout_seconds=None):
            calls.append((url, method, payload))
            if url.endswith("/api/live-agent-processes/start"):
                return {"group": {"group_id": "round-raise", "status": "running"}}
            if url.endswith("/live-agent-turns/round"):
                raise RuntimeError("round request failed")
            if url.endswith("/api/live-agent-processes"):
                return {"groups": [{"group_id": "round-raise", "status": "running"}]}
            if url.endswith("/api/live-agent-processes/round-raise/stop"):
                return {"group": {"group_id": "round-raise", "status": "stopped"}}
            return {"agent": {"agent_id": "smoke"}}

        with tempfile.TemporaryDirectory() as temp_dir:
            with self.assertRaisesRegex(RuntimeError, "round request failed"):
                run_live_agent_official_round_smoke(
                    output_root=Path(temp_dir) / "room",
                    server="http://room.local",
                    group_id="round-raise",
                    timeout_seconds=8,
                    request_json=request_json,
                    sleep_fn=lambda seconds: None,
                    temp_dir_factory=lambda: _FixedTemporaryDirectory(Path(temp_dir) / "config"),
                )

        self.assertTrue(any(url.endswith("/api/live-agent-processes/round-raise/stop") for url, method, payload in calls))

    def test_official_round_smoke_bounds_caller_controlled_group_id(self):
        long_group_id = "round-" + ("x" * 200)
        bounded_group_id = "round-" + ("x" * 42)

        def request_json(url, *, method="GET", payload=None, timeout_seconds=None):
            group_id = payload.get("group_id") if isinstance(payload, dict) else ""
            if url.endswith("/api/live-agent-processes/start"):
                return {"group": {"group_id": group_id, "status": "running"}}
            if url.endswith("/live-agent-turns/round"):
                return _answered_round_result()
            if url.endswith("/api/live-agent-processes"):
                return {"groups": [{"group_id": bounded_group_id, "status": "running"}]}
            if url.endswith("/stop"):
                return {"group": {"group_id": bounded_group_id, "status": "stopped"}}
            return {"agent": {"agent_id": "smoke"}}

        with tempfile.TemporaryDirectory() as temp_dir:
            result = run_live_agent_official_round_smoke(
                output_root=Path(temp_dir) / "room",
                server="http://room.local",
                group_id=long_group_id,
                timeout_seconds=8,
                request_json=request_json,
                sleep_fn=lambda seconds: None,
                temp_dir_factory=lambda: _FixedTemporaryDirectory(Path(temp_dir) / "config"),
            )

        self.assertLessEqual(len(result["group_id"]), 48)
        self.assertLessEqual(max(len(agent_id) for agent_id in result["agent_ids"]), 64)

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
        self.path.mkdir(parents=True, exist_ok=True)
        return str(self.path)

    def __exit__(self, exc_type, exc_value, traceback):
        return False


def _answered_round_result():
    return {
        "status": "answered",
        "round_id": "official_round_smoke",
        "role_ids": ["smoke_local_cli", "smoke_live_session", "smoke_remote_bridge"],
        "turn_count": 3,
        "answered_count": 3,
        "timeout_count": 0,
        "skipped_count": 0,
        "results": [
            {
                "status": "answered",
                "agent_id": "round-local-cli",
                "request_event": {"id": "request-local"},
                "reply_event": {"id": "reply-local"},
            },
            {
                "status": "answered",
                "agent_id": "round-live-session",
                "request_event": {"id": "request-session"},
                "reply_event": {"id": "reply-session"},
            },
            {
                "status": "answered",
                "agent_id": "round-remote-bridge",
                "request_event": {"id": "request-bridge"},
                "reply_event": {"id": "reply-bridge"},
            },
        ],
    }


if __name__ == "__main__":
    unittest.main()
