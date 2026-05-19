import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agentsassemble.live_agent_smoke import (
    LiveAgentSmokeFailed,
    _make_session_smoke_group_recoverable,
    build_live_agent_official_round_smoke_config,
    build_live_agent_smoke_config,
    run_live_agent_session_smoke,
    run_live_agent_official_round_smoke,
    run_live_agent_smoke,
    seed_smoke_agent_cursors,
)


class LiveAgentSmokeTests(unittest.TestCase):
    def test_session_smoke_runs_start_reply_check_resume_restart_and_stop_sequence(self):
        calls = []
        state = {"probe_ids": [], "started": False}

        def request_json(url, *, method="GET", payload=None, timeout_seconds=None):
            calls.append((url, method, payload, timeout_seconds))
            if url.endswith("/api/live-agent-sessions/start"):
                self.assertIs(payload["diagnostic"], True)
                for key in ("council_config_path", "agent_config_path", "live_agent_config_path"):
                    config_path = Path(payload[key])
                    self.assertTrue(config_path.is_absolute())
                    self.assertTrue(config_path.exists())
                live_config = json.loads(Path(payload["live_agent_config_path"]).read_text(encoding="utf-8"))
                self.assertEqual(
                    [agent["agent_id"] for agent in live_config["agents"]],
                    ["session-smoke-local-cli", "session-smoke-live-session", "session-smoke-remote-bridge"],
                )
                self.assertEqual(
                    [agent["connection_kind"] for agent in live_config["agents"]],
                    ["local_cli", "live_session", "remote_bridge"],
                )
                self.assertEqual(live_config["max_chain_depth"], 0)
                agent_config = json.loads(Path(payload["agent_config_path"]).read_text(encoding="utf-8"))
                self.assertEqual(
                    [provider["kind"] for provider in agent_config["providers"]],
                    ["local_cli", "remote_http_bridge"],
                )
                self.assertEqual(len(agent_config["agent_bindings"]), 3)
                meeting_dir = state["root"] / "meetings" / payload["meeting_id"]
                meeting_dir.mkdir(parents=True, exist_ok=True)
                (meeting_dir / "live_state.json").write_text(
                    json.dumps({"meeting_id": payload["meeting_id"], "live_status": "running"}),
                    encoding="utf-8",
                )
                state["started"] = True
                return {
                    "status": "ready",
                    "meeting_id": payload["meeting_id"],
                    "group_id": payload["group_id"],
                    "connection": {"expected": 3, "connected": 3, "attention": []},
                }
            if url.endswith("/live-agent-turns/rounds"):
                self.assertEqual(payload["max_rounds"], 1)
                self.assertFalse(payload["stop_on_timeout"])
                return {
                    "status": "answered",
                    "meeting_id": "session-smoke-meeting",
                    "round_count": 1,
                    "answered_round_count": 1,
                    "completed_round_count": 0,
                    "timeout_round_count": 0,
                    "skipped_round_count": 0,
                    "stopped_round_count": 0,
                    "results": [{"round_id": "session_smoke_round", "status": "answered"}],
                }
            if url.endswith("/engagement"):
                self.assertEqual(payload, {"engagement_mode": "always"})
                return {"agent": {"agent_id": url.rsplit("/", 2)[-2], "engagement_mode": "always"}}
            if url.endswith("/api/lobby") and method == "POST":
                probe_id = f"session-probe-{len(state['probe_ids']) + 1}"
                state["probe_ids"].append(probe_id)
                return {"event": {"id": probe_id}}
            if url.endswith("/api/lobby") and method == "GET":
                if not state["probe_ids"]:
                    return {"events": [{"id": "old", "message": "old chatter"}]}
                events = []
                for probe_id in state["probe_ids"]:
                    events.extend(
                        [
                            {
                                "id": f"reply-local-{probe_id}",
                                "actor_id": "session-smoke-local-cli",
                                "message": "session smoke local_cli ok",
                                "source_event_id": probe_id,
                                "live_agent_endpoint": True,
                            },
                            {
                                "id": f"reply-session-{probe_id}",
                                "actor_id": "session-smoke-live-session",
                                "message": "session smoke live_session ok",
                                "source_event_id": probe_id,
                                "live_agent_endpoint": True,
                            },
                            {
                                "id": f"reply-bridge-{probe_id}",
                                "actor_id": "session-smoke-remote-bridge",
                                "message": "session smoke remote_bridge ok",
                                "source_event_id": probe_id,
                                "live_agent_endpoint": True,
                            },
                        ]
                    )
                return {"events": events}
            if url.endswith("/api/live-agent-sessions/check"):
                return {
                    "status": "ready",
                    "meeting_id": payload["meeting_id"],
                    "group_id": payload["group_id"],
                    "connection": {"expected": 3, "connected": 3, "attention": []},
                }
            if url.endswith("/api/live-agent-sessions/resume"):
                return {
                    "status": "ready",
                    "meeting_id": payload["meeting_id"],
                    "group_id": payload["group_id"],
                    "connection": {"expected": 3, "connected": 3, "attention": []},
                }
            if url.endswith("/api/live-agent-sessions/restart"):
                return {
                    "status": "ready",
                    "meeting_id": payload["meeting_id"],
                    "group_id": payload["group_id"],
                    "connection": {"expected": 3, "connected": 3, "attention": []},
                }
            if url.endswith("/api/live-agent-processes"):
                return {
                    "groups": [
                        {
                            "group_id": "session-smoke",
                            "status": "error",
                            "meeting_id": "session-smoke-meeting",
                            "diagnostic": True,
                            "agents": [
                                {"agent_id": "session-smoke-local-cli"},
                                {"agent_id": "session-smoke-live-session"},
                                {"agent_id": "session-smoke-remote-bridge"},
                            ],
                        }
                    ]
                }
            if url.endswith("/api/live-agent-sessions/recover"):
                self.assertEqual(
                    payload,
                    {"meeting_id": "session-smoke-meeting", "group_id": "session-smoke", "connect_timeout_seconds": 8.0},
                )
                return {
                    "status": "ready",
                    "meeting_id": payload["meeting_id"],
                    "group_id": payload["group_id"],
                    "connection": {"expected": 3, "connected": 3, "attention": []},
                }
            if url.endswith("/api/live-agent-sessions/stop"):
                return {
                    "status": "stopped",
                    "meeting_id": payload["meeting_id"],
                    "group_id": payload["group_id"],
                    "offline": {"expected": 3, "offline": 3, "attention": []},
                }
            return {}

        with tempfile.TemporaryDirectory() as temp_dir:
            state["root"] = Path(temp_dir) / "room"
            result = run_live_agent_session_smoke(
                server="http://room.local",
                group_id="session-smoke",
                meeting_id="session-smoke-meeting",
                timeout_seconds=8,
                request_json=request_json,
                output_root=state["root"],
                sleep_fn=lambda seconds: None,
                temp_dir_factory=lambda: _FixedTemporaryDirectory(Path(temp_dir) / "config"),
            )

            live_state = json.loads(
                (state["root"] / "meetings" / "session-smoke-meeting" / "live_state.json").read_text(encoding="utf-8")
            )

        self.assertTrue(live_state["diagnostic"])
        self.assertEqual(live_state["diagnostic_kind"], "session_smoke")
        urls = [url for url, method, payload, timeout_seconds in calls]
        self.assertIn("http://room.local/api/live-agent-sessions/start", urls)
        self.assertIn("http://room.local/api/live-agent-sessions/check", urls)
        self.assertIn("http://room.local/api/live-agent-sessions/resume", urls)
        self.assertIn("http://room.local/api/live-agent-sessions/restart", urls)
        self.assertIn("http://room.local/api/live-agent-sessions/recover", urls)
        self.assertIn("http://room.local/api/live-agent-sessions/stop", urls)
        lobby_post_indexes = [
            index
            for index, (url, method, payload, timeout_seconds) in enumerate(calls)
            if url == "http://room.local/api/lobby" and method == "POST"
        ]
        self.assertEqual(len(lobby_post_indexes), 3)
        self.assertLess(
            urls.index("http://room.local/api/live-agent-sessions/check"),
            urls.index("http://room.local/api/live-agent-sessions/resume"),
        )
        self.assertLess(
            urls.index("http://room.local/api/live-agent-sessions/resume"),
            urls.index("http://room.local/api/live-agent-sessions/restart"),
        )
        self.assertLess(
            urls.index("http://room.local/api/live-agent-sessions/restart"),
            lobby_post_indexes[1],
        )
        self.assertLess(
            lobby_post_indexes[1],
            urls.index("http://room.local/api/live-agent-sessions/recover"),
        )
        self.assertLess(
            urls.index("http://room.local/api/live-agent-sessions/recover"),
            lobby_post_indexes[2],
        )
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["meeting_id"], "session-smoke-meeting")
        self.assertEqual(result["group_id"], "session-smoke")
        self.assertEqual(result["rounds_status"], "answered")
        self.assertEqual(result["round_count"], 1)
        self.assertEqual(result["answered_round_count"], 1)
        self.assertEqual(result["expected_reply_count"], 3)
        self.assertEqual(result["reply_count"], 3)
        self.assertEqual(result["post_restart_reply_count"], 3)
        self.assertEqual(result["post_restart_source_event_id"], "session-probe-2")
        self.assertEqual(result["post_recover_reply_count"], 3)
        self.assertEqual(result["post_recover_source_event_id"], "session-probe-3")
        self.assertEqual(result["start_status"], "ready")
        self.assertEqual(result["check_status"], "ready")
        self.assertEqual(result["resume_status"], "ready")
        self.assertEqual(result["restart_status"], "ready")
        self.assertEqual(result["recover_status"], "ready")
        self.assertEqual(result["stop_status"], "stopped")
        self.assertEqual(
            {reply["actor_id"] for reply in result["replies"]},
            {"session-smoke-local-cli", "session-smoke-live-session", "session-smoke-remote-bridge"},
        )
        self.assertEqual({reply["source_event_id"] for reply in result["post_restart_replies"]}, {"session-probe-2"})
        self.assertEqual({reply["source_event_id"] for reply in result["post_recover_replies"]}, {"session-probe-3"})
        serialized = json.dumps(result, ensure_ascii=False)
        self.assertNotIn(str(Path(temp_dir)), serialized)
        self.assertNotIn("session smoke local_cli ok", serialized)
        self.assertNotIn("session smoke live_session ok", serialized)
        self.assertNotIn("session smoke remote_bridge ok", serialized)
        self.assertNotIn("agentsassemble-smoke-token", serialized)
        self.assertNotIn("command", serialized)

    def test_session_smoke_can_repeat_lobby_probes_before_and_after_restart(self):
        calls = []
        state = {"probe_ids": []}

        def request_json(url, *, method="GET", payload=None, timeout_seconds=None):
            calls.append((url, method, payload, timeout_seconds))
            if url.endswith("/api/live-agent-sessions/start"):
                meeting_dir = state["root"] / "meetings" / payload["meeting_id"]
                meeting_dir.mkdir(parents=True, exist_ok=True)
                (meeting_dir / "live_state.json").write_text(
                    json.dumps({"meeting_id": payload["meeting_id"], "live_status": "running"}),
                    encoding="utf-8",
                )
                return {
                    "status": "ready",
                    "meeting_id": payload["meeting_id"],
                    "group_id": payload["group_id"],
                    "connection": {"expected": 3, "connected": 3, "attention": []},
                }
            if url.endswith("/live-agent-turns/rounds"):
                return {
                    "status": "answered",
                    "meeting_id": "session-smoke-meeting",
                    "round_count": 1,
                    "answered_round_count": 1,
                    "completed_round_count": 0,
                    "timeout_round_count": 0,
                    "skipped_round_count": 0,
                    "stopped_round_count": 0,
                }
            if url.endswith("/engagement"):
                return {"agent": {"agent_id": url.rsplit("/", 2)[-2], "engagement_mode": "always"}}
            if url.endswith("/api/lobby") and method == "POST":
                probe_id = f"probe-{len(state['probe_ids']) + 1}"
                state["probe_ids"].append(probe_id)
                return {"event": {"id": probe_id}}
            if url.endswith("/api/lobby") and method == "GET":
                events = []
                for probe_id in state["probe_ids"]:
                    events.extend(
                        [
                            {
                                "id": f"reply-local-{probe_id}",
                                "actor_id": "session-smoke-local-cli",
                                "message": "session smoke local_cli ok",
                                "source_event_id": probe_id,
                                "live_agent_endpoint": True,
                            },
                            {
                                "id": f"reply-session-{probe_id}",
                                "actor_id": "session-smoke-live-session",
                                "message": "session smoke live_session ok",
                                "source_event_id": probe_id,
                                "live_agent_endpoint": True,
                            },
                            {
                                "id": f"reply-bridge-{probe_id}",
                                "actor_id": "session-smoke-remote-bridge",
                                "message": "session smoke remote_bridge ok",
                                "source_event_id": probe_id,
                                "live_agent_endpoint": True,
                            },
                        ]
                    )
                return {"events": events}
            if (
                url.endswith("/api/live-agent-sessions/check")
                or url.endswith("/api/live-agent-sessions/resume")
                or url.endswith("/api/live-agent-sessions/restart")
                or url.endswith("/api/live-agent-sessions/recover")
            ):
                return {
                    "status": "ready",
                    "meeting_id": payload["meeting_id"],
                    "group_id": payload["group_id"],
                    "connection": {"expected": 3, "connected": 3, "attention": []},
                }
            if url.endswith("/api/live-agent-processes"):
                return {
                    "groups": [
                        {
                            "group_id": "session-smoke",
                            "status": "error",
                            "meeting_id": "session-smoke-meeting",
                            "diagnostic": True,
                            "agents": [
                                {"agent_id": "session-smoke-local-cli"},
                                {"agent_id": "session-smoke-live-session"},
                                {"agent_id": "session-smoke-remote-bridge"},
                            ],
                        }
                    ]
                }
            if url.endswith("/api/live-agent-sessions/stop"):
                return {
                    "status": "stopped",
                    "meeting_id": payload["meeting_id"],
                    "group_id": payload["group_id"],
                    "offline": {"expected": 3, "offline": 3, "attention": []},
                }
            return {}

        with tempfile.TemporaryDirectory() as temp_dir:
            state["root"] = Path(temp_dir) / "room"
            result = run_live_agent_session_smoke(
                server="http://room.local",
                group_id="session-smoke",
                meeting_id="session-smoke-meeting",
                timeout_seconds=8,
                lobby_probe_count=2,
                request_json=request_json,
                output_root=state["root"],
                sleep_fn=lambda seconds: None,
                temp_dir_factory=lambda: _FixedTemporaryDirectory(Path(temp_dir) / "config"),
            )

        urls = [url for url, method, payload, timeout_seconds in calls]
        lobby_post_indexes = [
            index
            for index, (url, method, payload, timeout_seconds) in enumerate(calls)
            if url == "http://room.local/api/lobby" and method == "POST"
        ]
        self.assertEqual(len(lobby_post_indexes), 6)
        self.assertLess(lobby_post_indexes[1], urls.index("http://room.local/api/live-agent-sessions/check"))
        self.assertLess(urls.index("http://room.local/api/live-agent-sessions/restart"), lobby_post_indexes[2])
        self.assertLess(lobby_post_indexes[3], urls.index("http://room.local/api/live-agent-sessions/recover"))
        self.assertLess(urls.index("http://room.local/api/live-agent-sessions/recover"), lobby_post_indexes[4])
        self.assertEqual(result["lobby_probe_count"], 2)
        self.assertEqual(result["source_event_ids"], ["probe-1", "probe-2"])
        self.assertEqual(result["post_restart_source_event_ids"], ["probe-3", "probe-4"])
        self.assertEqual(result["post_recover_source_event_ids"], ["probe-5", "probe-6"])
        self.assertEqual(result["reply_count"], 6)
        self.assertEqual(result["post_restart_reply_count"], 6)
        self.assertEqual(result["post_recover_reply_count"], 6)
        self.assertEqual({reply["source_event_id"] for reply in result["replies"]}, {"probe-1", "probe-2"})
        self.assertEqual({reply["source_event_id"] for reply in result["post_restart_replies"]}, {"probe-3", "probe-4"})
        self.assertEqual({reply["source_event_id"] for reply in result["post_recover_replies"]}, {"probe-5", "probe-6"})

    def test_session_smoke_can_run_same_session_soak_cycles(self):
        calls = []
        state = {"probe_ids": [], "check_count": 0, "sleeps": []}

        def request_json(url, *, method="GET", payload=None, timeout_seconds=None):
            calls.append((url, method, payload, timeout_seconds))
            if url.endswith("/api/live-agent-sessions/start"):
                meeting_dir = state["root"] / "meetings" / payload["meeting_id"]
                meeting_dir.mkdir(parents=True, exist_ok=True)
                (meeting_dir / "live_state.json").write_text(
                    json.dumps({"meeting_id": payload["meeting_id"], "live_status": "running"}),
                    encoding="utf-8",
                )
                return {
                    "status": "ready",
                    "meeting_id": payload["meeting_id"],
                    "group_id": payload["group_id"],
                    "connection": {"expected": 3, "connected": 3, "attention": []},
                }
            if url.endswith("/live-agent-turns/rounds"):
                return {
                    "status": "answered",
                    "meeting_id": "session-smoke-meeting",
                    "round_count": 1,
                    "answered_round_count": 1,
                    "completed_round_count": 0,
                    "timeout_round_count": 0,
                    "skipped_round_count": 0,
                    "stopped_round_count": 0,
                }
            if url.endswith("/engagement"):
                return {"agent": {"agent_id": url.rsplit("/", 2)[-2], "engagement_mode": "always"}}
            if url.endswith("/api/lobby") and method == "POST":
                probe_id = f"probe-{len(state['probe_ids']) + 1}"
                state["probe_ids"].append(probe_id)
                return {"event": {"id": probe_id}}
            if url.endswith("/api/lobby") and method == "GET":
                events = []
                for probe_id in state["probe_ids"]:
                    events.extend(
                        [
                            {
                                "id": f"reply-local-{probe_id}",
                                "actor_id": "session-smoke-local-cli",
                                "message": "session smoke local_cli ok",
                                "source_event_id": probe_id,
                                "live_agent_endpoint": True,
                            },
                            {
                                "id": f"reply-session-{probe_id}",
                                "actor_id": "session-smoke-live-session",
                                "message": "session smoke live_session ok",
                                "source_event_id": probe_id,
                                "live_agent_endpoint": True,
                            },
                            {
                                "id": f"reply-bridge-{probe_id}",
                                "actor_id": "session-smoke-remote-bridge",
                                "message": "session smoke remote_bridge ok",
                                "source_event_id": probe_id,
                                "live_agent_endpoint": True,
                            },
                        ]
                    )
                return {"events": events}
            if url.endswith("/api/live-agent-sessions/check"):
                state["check_count"] += 1
                return {
                    "status": "ready",
                    "meeting_id": payload["meeting_id"],
                    "group_id": payload["group_id"],
                    "connection": {"expected": 3, "connected": 3, "attention": []},
                }
            if (
                url.endswith("/api/live-agent-sessions/resume")
                or url.endswith("/api/live-agent-sessions/restart")
                or url.endswith("/api/live-agent-sessions/recover")
            ):
                return {
                    "status": "ready",
                    "meeting_id": payload["meeting_id"],
                    "group_id": payload["group_id"],
                    "connection": {"expected": 3, "connected": 3, "attention": []},
                }
            if url.endswith("/api/live-agent-processes"):
                return {
                    "groups": [
                        {
                            "group_id": "session-smoke",
                            "status": "error",
                            "meeting_id": "session-smoke-meeting",
                            "diagnostic": True,
                            "agents": [
                                {"agent_id": "session-smoke-local-cli"},
                                {"agent_id": "session-smoke-live-session"},
                                {"agent_id": "session-smoke-remote-bridge"},
                            ],
                        }
                    ]
                }
            if url.endswith("/api/live-agent-sessions/stop"):
                return {
                    "status": "stopped",
                    "meeting_id": payload["meeting_id"],
                    "group_id": payload["group_id"],
                    "offline": {"expected": 3, "offline": 3, "attention": []},
                }
            return {}

        with tempfile.TemporaryDirectory() as temp_dir:
            state["root"] = Path(temp_dir) / "room"
            result = run_live_agent_session_smoke(
                server="http://room.local",
                group_id="session-smoke",
                meeting_id="session-smoke-meeting",
                timeout_seconds=8,
                soak_cycle_count=2,
                soak_interval_seconds=0.5,
                request_json=request_json,
                output_root=state["root"],
                sleep_fn=lambda seconds: state["sleeps"].append(seconds),
                temp_dir_factory=lambda: _FixedTemporaryDirectory(Path(temp_dir) / "config"),
            )

        urls = [url for url, method, payload, timeout_seconds in calls]
        lobby_post_indexes = [
            index
            for index, (url, method, payload, timeout_seconds) in enumerate(calls)
            if url == "http://room.local/api/lobby" and method == "POST"
        ]
        stop_index = urls.index("http://room.local/api/live-agent-sessions/stop")
        self.assertEqual(len(lobby_post_indexes), 5)
        self.assertLess(urls.index("http://room.local/api/live-agent-sessions/recover"), lobby_post_indexes[2])
        self.assertLess(lobby_post_indexes[2], lobby_post_indexes[3])
        self.assertLess(lobby_post_indexes[3], lobby_post_indexes[4])
        self.assertLess(lobby_post_indexes[4], stop_index)
        self.assertEqual(state["check_count"], 3)
        self.assertEqual(state["sleeps"], [0.5, 0.5])
        self.assertEqual(result["soak_cycle_count"], 2)
        self.assertEqual(result["soak_interval_seconds"], 0.5)
        self.assertEqual(result["soak_check_statuses"], ["ready", "ready"])
        self.assertEqual(result["soak_source_event_ids"], ["probe-4", "probe-5"])
        self.assertEqual(result["soak_reply_count"], 6)
        self.assertEqual({reply["source_event_id"] for reply in result["soak_replies"]}, {"probe-4", "probe-5"})

    def test_session_smoke_bounds_lobby_probe_count_before_side_effects(self):
        calls = []

        def request_json(url, *, method="GET", payload=None, timeout_seconds=None):
            calls.append((url, method, payload, timeout_seconds))
            return {}

        with self.assertRaisesRegex(ValueError, "between 1 and 5"):
            run_live_agent_session_smoke(
                server="http://room.local",
                timeout_seconds=8,
                lobby_probe_count=6,
                request_json=request_json,
                sleep_fn=lambda seconds: None,
            )

        self.assertEqual(calls, [])

    def test_session_smoke_bounds_soak_options_before_side_effects(self):
        calls = []

        def request_json(url, *, method="GET", payload=None, timeout_seconds=None):
            calls.append((url, method, payload, timeout_seconds))
            return {}

        with self.assertRaisesRegex(ValueError, "soak_cycle_count"):
            run_live_agent_session_smoke(
                server="http://room.local",
                timeout_seconds=8,
                soak_cycle_count=6,
                request_json=request_json,
                sleep_fn=lambda seconds: None,
            )
        with self.assertRaisesRegex(ValueError, "soak_interval_seconds"):
            run_live_agent_session_smoke(
                server="http://room.local",
                timeout_seconds=8,
                soak_interval_seconds=61,
                request_json=request_json,
                sleep_fn=lambda seconds: None,
            )

        self.assertEqual(calls, [])

    def test_session_smoke_stops_session_when_soak_check_fails(self):
        calls = []
        state = {"probe_ids": [], "check_count": 0}

        def request_json(url, *, method="GET", payload=None, timeout_seconds=None):
            calls.append((url, method, payload, timeout_seconds))
            if url.endswith("/api/live-agent-sessions/start"):
                meeting_dir = state["root"] / "meetings" / payload["meeting_id"]
                meeting_dir.mkdir(parents=True, exist_ok=True)
                (meeting_dir / "live_state.json").write_text(
                    json.dumps({"meeting_id": payload["meeting_id"], "live_status": "running"}),
                    encoding="utf-8",
                )
                return {"status": "ready", "meeting_id": payload["meeting_id"], "group_id": payload["group_id"]}
            if url.endswith("/live-agent-turns/rounds"):
                return {"status": "answered", "answered_round_count": 1}
            if url.endswith("/engagement"):
                return {"agent": {"agent_id": url.rsplit("/", 2)[-2], "engagement_mode": "always"}}
            if url.endswith("/api/lobby") and method == "POST":
                probe_id = f"probe-{len(state['probe_ids']) + 1}"
                state["probe_ids"].append(probe_id)
                return {"event": {"id": probe_id}}
            if url.endswith("/api/lobby") and method == "GET":
                events = []
                for probe_id in state["probe_ids"]:
                    for agent_id, message in [
                        ("session-smoke-local-cli", "session smoke local_cli ok"),
                        ("session-smoke-live-session", "session smoke live_session ok"),
                        ("session-smoke-remote-bridge", "session smoke remote_bridge ok"),
                    ]:
                        events.append(
                            {
                                "id": f"reply-{agent_id}-{probe_id}",
                                "actor_id": agent_id,
                                "message": message,
                                "source_event_id": probe_id,
                                "live_agent_endpoint": True,
                            }
                        )
                return {"events": events}
            if url.endswith("/api/live-agent-sessions/check"):
                state["check_count"] += 1
                status = "ready" if state["check_count"] == 1 else "degraded"
                return {"status": status, "meeting_id": payload["meeting_id"], "group_id": payload["group_id"]}
            if url.endswith("/api/live-agent-sessions/resume") or url.endswith("/api/live-agent-sessions/restart"):
                return {"status": "ready", "meeting_id": payload["meeting_id"], "group_id": payload["group_id"]}
            if url.endswith("/api/live-agent-sessions/recover"):
                return {"status": "ready", "meeting_id": payload["meeting_id"], "group_id": payload["group_id"]}
            if url.endswith("/api/live-agent-processes"):
                return {
                    "groups": [
                        {
                            "group_id": "session-smoke",
                            "status": "error",
                            "meeting_id": "session-smoke-meeting",
                            "diagnostic": True,
                            "agents": [
                                {"agent_id": "session-smoke-local-cli"},
                                {"agent_id": "session-smoke-live-session"},
                                {"agent_id": "session-smoke-remote-bridge"},
                            ],
                        }
                    ]
                }
            if url.endswith("/api/live-agent-sessions/stop"):
                return {"status": "stopped", "meeting_id": payload["meeting_id"], "group_id": payload["group_id"]}
            return {}

        with tempfile.TemporaryDirectory() as temp_dir:
            state["root"] = Path(temp_dir) / "room"
            with self.assertRaisesRegex(LiveAgentSmokeFailed, "soak check"):
                run_live_agent_session_smoke(
                    server="http://room.local",
                    group_id="session-smoke",
                    meeting_id="session-smoke-meeting",
                    timeout_seconds=8,
                    soak_cycle_count=1,
                    request_json=request_json,
                    output_root=state["root"],
                    sleep_fn=lambda seconds: None,
                    temp_dir_factory=lambda: _FixedTemporaryDirectory(Path(temp_dir) / "config"),
                )

        self.assertIn(
            ("http://room.local/api/live-agent-sessions/stop", "POST"),
            [(url, method) for url, method, payload, timeout_seconds in calls],
        )

    def test_session_smoke_default_ids_are_rerunnable(self):
        start_group_ids = []

        def run_once(root: Path) -> dict[str, object]:
            state = {"probe_ids": [], "group_id": "", "meeting_id": ""}

            def request_json(url, *, method="GET", payload=None, timeout_seconds=None):
                if url.endswith("/api/live-agent-sessions/start"):
                    state["group_id"] = payload["group_id"]
                    state["meeting_id"] = payload["meeting_id"]
                    start_group_ids.append(payload["group_id"])
                    meeting_dir = root / "meetings" / payload["meeting_id"]
                    meeting_dir.mkdir(parents=True, exist_ok=True)
                    (meeting_dir / "live_state.json").write_text(
                        json.dumps({"meeting_id": payload["meeting_id"], "live_status": "running"}),
                        encoding="utf-8",
                    )
                    return {
                        "status": "ready",
                        "meeting_id": payload["meeting_id"],
                        "group_id": payload["group_id"],
                        "connection": {"expected": 3, "connected": 3, "attention": []},
                    }
                if url.endswith("/live-agent-turns/rounds"):
                    return {
                        "status": "answered",
                        "meeting_id": state["meeting_id"],
                        "round_count": 1,
                        "answered_round_count": 1,
                        "completed_round_count": 0,
                        "timeout_round_count": 0,
                        "skipped_round_count": 0,
                        "stopped_round_count": 0,
                        "results": [{"round_id": "session_smoke_round", "status": "answered"}],
                    }
                if url.endswith("/engagement"):
                    return {"agent": {"agent_id": url.rsplit("/", 2)[-2], "engagement_mode": "always"}}
                if url.endswith("/api/lobby") and method == "POST":
                    probe_id = f"probe-{state['group_id']}-{len(state['probe_ids']) + 1}"
                    state["probe_ids"].append(probe_id)
                    return {"event": {"id": probe_id}}
                if url.endswith("/api/lobby") and method == "GET":
                    if not state["probe_ids"]:
                        return {"events": []}
                    group_id = state["group_id"]
                    events = []
                    for probe_id in state["probe_ids"]:
                        events.extend(
                            [
                                {
                                    "id": f"reply-{group_id}-local-{probe_id}",
                                    "actor_id": f"{group_id}-local-cli",
                                    "message": "session smoke local_cli ok",
                                    "source_event_id": probe_id,
                                    "live_agent_endpoint": True,
                                },
                                {
                                    "id": f"reply-{group_id}-session-{probe_id}",
                                    "actor_id": f"{group_id}-live-session",
                                    "message": "session smoke live_session ok",
                                    "source_event_id": probe_id,
                                    "live_agent_endpoint": True,
                                },
                                {
                                    "id": f"reply-{group_id}-bridge-{probe_id}",
                                    "actor_id": f"{group_id}-remote-bridge",
                                    "message": "session smoke remote_bridge ok",
                                    "source_event_id": probe_id,
                                    "live_agent_endpoint": True,
                                },
                            ]
                        )
                    return {"events": events}
                if (
                    url.endswith("/api/live-agent-sessions/check")
                    or url.endswith("/api/live-agent-sessions/resume")
                    or url.endswith("/api/live-agent-sessions/restart")
                    or url.endswith("/api/live-agent-sessions/recover")
                ):
                    return {
                        "status": "ready",
                        "meeting_id": payload["meeting_id"],
                        "group_id": payload["group_id"],
                        "connection": {"expected": 3, "connected": 3, "attention": []},
                    }
                if url.endswith("/api/live-agent-processes"):
                    return {
                        "groups": [
                            {
                                "group_id": state["group_id"],
                                "status": "error",
                                "meeting_id": state["meeting_id"],
                                "diagnostic": True,
                                "agents": [
                                    {"agent_id": f"{state['group_id']}-local-cli"},
                                    {"agent_id": f"{state['group_id']}-live-session"},
                                    {"agent_id": f"{state['group_id']}-remote-bridge"},
                                ],
                            }
                        ]
                    }
                if url.endswith("/api/live-agent-sessions/stop"):
                    return {
                        "status": "stopped",
                        "meeting_id": payload["meeting_id"],
                        "group_id": payload["group_id"],
                        "offline": {"expected": 3, "offline": 3, "attention": []},
                    }
                return {}

            return run_live_agent_session_smoke(
                server="http://room.local",
                timeout_seconds=8,
                request_json=request_json,
                output_root=root,
                sleep_fn=lambda seconds: None,
            )

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "room"
            with patch(
                "agentsassemble.live_agent_smoke.time.time",
                side_effect=[1000.001, 1000.002, 1000.003, 1000.004, 1001.001, 1001.002, 1001.003, 1001.004],
            ):
                first = run_once(root)
                second = run_once(root)

        self.assertEqual(first["status"], "ok")
        self.assertEqual(second["status"], "ok")
        self.assertNotEqual(first["group_id"], second["group_id"])
        self.assertNotEqual(first["meeting_id"], second["meeting_id"])
        self.assertEqual(start_group_ids, [first["group_id"], second["group_id"]])

    def test_session_smoke_recoverable_guard_kills_only_valid_diagnostic_group(self):
        calls = []
        killed = []

        def request_json(url, *, method="GET", payload=None, timeout_seconds=None):
            calls.append(url)
            status = "error" if killed else "running"
            return {
                "groups": [
                    {
                        "group_id": "session-smoke",
                        "status": status,
                        "pid": 1234,
                        "meeting_id": "session-smoke-meeting",
                        "diagnostic": True,
                        "agents": [
                            {"agent_id": "session-smoke-local-cli"},
                            {"agent_id": "session-smoke-live-session"},
                            {"agent_id": "session-smoke-remote-bridge"},
                        ],
                    }
                ]
            }

        group = _make_session_smoke_group_recoverable(
            "http://room.local",
            "session-smoke",
            meeting_id="session-smoke-meeting",
            expected_agent_ids=[
                "session-smoke-local-cli",
                "session-smoke-live-session",
                "session-smoke-remote-bridge",
            ],
            request_json=request_json,
            sleep_fn=lambda seconds: None,
            timeout_seconds=8,
            process_killer=lambda pid: killed.append(pid),
        )

        self.assertEqual(killed, [1234])
        self.assertEqual(group["status"], "error")
        self.assertEqual(calls, ["http://room.local/api/live-agent-processes", "http://room.local/api/live-agent-processes"])

    def test_session_smoke_recoverable_guard_refuses_unsafe_group_before_kill(self):
        expected_agent_ids = [
            "session-smoke-local-cli",
            "session-smoke-live-session",
            "session-smoke-remote-bridge",
        ]

        unsafe_groups = [
            (
                "not diagnostic",
                {
                    "group_id": "session-smoke",
                    "status": "running",
                    "pid": 1234,
                    "meeting_id": "session-smoke-meeting",
                    "diagnostic": False,
                    "agents": [{"agent_id": agent_id} for agent_id in expected_agent_ids],
                },
                "not diagnostic",
            ),
            (
                "wrong meeting",
                {
                    "group_id": "session-smoke",
                    "status": "running",
                    "pid": 1234,
                    "meeting_id": "real-meeting",
                    "diagnostic": True,
                    "agents": [{"agent_id": agent_id} for agent_id in expected_agent_ids],
                },
                "different meeting",
            ),
            (
                "wrong manifest",
                {
                    "group_id": "session-smoke",
                    "status": "running",
                    "pid": 1234,
                    "meeting_id": "session-smoke-meeting",
                    "diagnostic": True,
                    "agents": [{"agent_id": "real-agent"}],
                },
                "does not match",
            ),
        ]

        for label, group, pattern in unsafe_groups:
            killed = []

            def request_json(url, *, method="GET", payload=None, timeout_seconds=None):
                return {"groups": [group]}

            with self.subTest(label=label):
                with self.assertRaisesRegex(LiveAgentSmokeFailed, pattern):
                    _make_session_smoke_group_recoverable(
                        "http://room.local",
                        "session-smoke",
                        meeting_id="session-smoke-meeting",
                        expected_agent_ids=expected_agent_ids,
                        request_json=request_json,
                        sleep_fn=lambda seconds: None,
                        timeout_seconds=8,
                        process_killer=lambda pid: killed.append(pid),
                    )
                self.assertEqual(killed, [])

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
