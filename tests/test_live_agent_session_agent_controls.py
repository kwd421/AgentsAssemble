import json
import sys
import tempfile
import unittest
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from agentsassemble.gui import _make_handler
from agentsassemble.legacy.live_agent.runtime.meetings import start_live_agent_meeting
from agentsassemble.legacy.live_agent.runtime.sessions import (
    resume_live_agent_session_agent,
    stop_live_agent_session_agent,
)
from agentsassemble.live_agents import connect_live_agent, heartbeat_live_agent, read_live_agents


def _write_two_agent_session_configs(root: Path) -> tuple[Path, Path, Path]:
    council_config = root / "council.json"
    agent_config = root / "agents.json"
    live_agent_config = root / "live-agents.json"
    council_config.write_text(
        json.dumps(
            {
                "topic": "resident controls",
                "question": "Can agent controls stay per-agent?",
                "roles": [
                    {"id": "architect", "display_name": "Architect", "lens": "Architecture", "research_focus": "system"},
                    {"id": "critic", "display_name": "Critic", "lens": "Critique", "research_focus": "risk"},
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    agent_config.write_text(
        json.dumps(
            {
                "providers": [{"id": "local-cli", "kind": "local_cli", "display_name": "Local CLI"}],
                "permission_profiles": [{"id": "meeting_readonly", "meeting_read": True, "official_turn": True}],
                "agent_bindings": [
                    {
                        "agent_id": "agent-a",
                        "role_id": "architect",
                        "provider_id": "local-cli",
                        "permission_profile_id": "meeting_readonly",
                    },
                    {
                        "agent_id": "agent-b",
                        "role_id": "critic",
                        "provider_id": "local-cli",
                        "permission_profile_id": "meeting_readonly",
                    },
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    live_agent_config.write_text(
        json.dumps(
            {
                "server": "http://room.local",
                "poll_interval": 2,
                "agents": [
                    {
                        "agent_id": "agent-a",
                        "display_name": "Agent A",
                        "provider_kind": "local_cli",
                        "connection_kind": "local_cli",
                        "command": [sys.executable, "-c", "print('a')"],
                    },
                    {
                        "agent_id": "agent-b",
                        "display_name": "Agent B",
                        "provider_kind": "local_cli",
                        "connection_kind": "local_cli",
                        "command": [sys.executable, "-c", "print('b')"],
                    },
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return council_config, agent_config, live_agent_config


class AgentControlSupervisor:
    def __init__(self, output_root: Path, groups: list[dict[str, object]] | None = None) -> None:
        self.output_root = output_root
        self.groups = list(groups or [])
        self.started: list[dict[str, object]] = []
        self.stopped: list[str] = []

    def list_groups(self):
        return list(self.groups)

    def start_group(self, **kwargs):
        self.started.append(kwargs)
        config = json.loads(Path(kwargs["config_path"]).read_text(encoding="utf-8"))
        agents = config["agents"]
        group = {
            "group_id": kwargs["group_id"],
            "status": "running",
            "meeting_id": kwargs["meeting_id"],
            "config_path": str(kwargs["config_path"]),
            "agents": [
                {
                    "agent_id": agent["agent_id"],
                    "display_name": agent.get("display_name", agent["agent_id"]),
                    "provider_kind": agent.get("provider_kind", "local_cli"),
                    "connection_kind": agent.get("connection_kind", "local_cli"),
                }
                for agent in agents
            ],
        }
        self.groups.append(group)
        for agent in agents:
            heartbeat_live_agent(self.output_root, agent["agent_id"], status="online")
        return group

    def stop_group(self, group_id: str):
        self.stopped.append(group_id)
        for index, group in enumerate(self.groups):
            if group.get("group_id") != group_id:
                continue
            stopped = {**group, "status": "stopped"}
            self.groups[index] = stopped
            return stopped
        return {"group_id": group_id, "status": "stopped", "agents": []}

    def stop_group_if_owned(self, group_id: str, *, meeting_id: str, agent_ids: list[str]):
        for group in self.groups:
            if group.get("group_id") != group_id:
                continue
            if group.get("meeting_id") != meeting_id:
                raise ValueError("wrong meeting")
            manifest_agent_ids = [
                str(agent.get("agent_id") or "")
                for agent in group.get("agents", [])
                if isinstance(agent, dict) and str(agent.get("agent_id") or "")
            ]
            if manifest_agent_ids != agent_ids:
                raise ValueError("not an agent-owned process")
            return self.stop_group(group_id)
        raise ValueError("group not found")


class LiveAgentSessionAgentControlTests(unittest.TestCase):
    def test_agent_timing_endpoint_updates_poll_interval_and_cooldown(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _, _, live_agent_config = _write_two_agent_session_configs(root)
            connect_live_agent(
                root,
                {
                    "agent_id": "agent-a",
                    "display_name": "Agent A",
                    "meeting_id": "resident-m1",
                    "provider_kind": "local_cli",
                    "connection_kind": "local_cli",
                },
            )
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/live-agent-sessions/agent-timing",
                    data=json.dumps(
                        {
                            "meeting_id": "resident-m1",
                            "group_id": "resident-main",
                            "agent_id": "agent-a",
                            "live_agent_config_path": str(live_agent_config),
                            "poll_interval": 0.25,
                            "cooldown": 0.5,
                        }
                    ).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urlopen(request, timeout=4) as response:
                    payload = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

            self.assertEqual(payload["poll_interval"], 0.25)
            self.assertEqual(payload["cooldown"], 0.5)
            agent = read_live_agents(root)[0]
            self.assertEqual(agent["poll_interval"], 0.25)
            self.assertEqual(agent["cooldown"], 0.5)
            config_payload = json.loads(live_agent_config.read_text(encoding="utf-8"))
            config_agent = next(agent for agent in config_payload["agents"] if agent["agent_id"] == "agent-a")
            self.assertEqual(config_agent["poll_interval"], 0.25)
            self.assertEqual(config_agent["cooldown"], 0.5)

    def test_agent_options_endpoint_updates_permission_and_fast_in_record_and_config(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _, _, live_agent_config = _write_two_agent_session_configs(root)
            connect_live_agent(
                root,
                {
                    "agent_id": "agent-a",
                    "display_name": "Agent A",
                    "meeting_id": "resident-m1",
                    "provider_kind": "codex_live_session",
                    "connection_kind": "live_session",
                    "permission_option": "read-only",
                },
            )
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/live-agent-sessions/agent-options",
                    data=json.dumps(
                        {
                            "agent_id": "agent-a",
                            "live_agent_config_path": str(live_agent_config),
                            "permission_option": "danger-full-access",
                            "fast_mode": True,
                        }
                    ).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urlopen(request, timeout=4) as response:
                    payload = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

            self.assertEqual(payload["permission_option"], "danger-full-access")
            self.assertIs(payload["fast_mode"], True)
            self.assertEqual(payload["applies_on"], "next_start")
            agent = read_live_agents(root)[0]
            self.assertEqual(agent["permission_option"], "danger-full-access")
            self.assertIs(agent["fast_mode"], True)
            config_payload = json.loads(live_agent_config.read_text(encoding="utf-8"))
            config_agent = next(a for a in config_payload["agents"] if a["agent_id"] == "agent-a")
            self.assertEqual(config_agent["permission_option"], "danger-full-access")
            self.assertIs(config_agent["fast_mode"], True)

    def test_resume_agent_from_stopped_bundle_starts_agent_owned_process(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            council_config, agent_config, live_agent_config = _write_two_agent_session_configs(root)
            start_live_agent_meeting(
                root,
                council_config_path=council_config,
                agent_config_path=agent_config,
                meeting_id="resident-m1",
            )
            supervisor = AgentControlSupervisor(
                root,
                [
                    {
                        "group_id": "resident-main",
                        "status": "stopped",
                        "meeting_id": "resident-m1",
                        "config_path": str(live_agent_config),
                        "agents": [{"agent_id": "agent-a"}, {"agent_id": "agent-b"}],
                    }
                ],
            )

            session = resume_live_agent_session_agent(
                root,
                supervisor,
                server="http://room.local",
                live_agent_config_path=None,
                meeting_id="resident-m1",
                group_id="resident-main",
                agent_id="agent-a",
                connect_timeout_seconds=0,
            )

            self.assertEqual(session["status"], "ready")
            self.assertEqual(session["agent_id"], "agent-a")
            self.assertEqual(session["group_id"], "resident-main--agent-a")
            self.assertEqual(supervisor.started[0]["group_id"], "resident-main--agent-a")
            generated_config = json.loads(Path(supervisor.started[0]["config_path"]).read_text(encoding="utf-8"))
            self.assertEqual([agent["agent_id"] for agent in generated_config["agents"]], ["agent-a"])

    def test_stop_agent_refuses_running_bundle_without_stopping_group(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            council_config, agent_config, live_agent_config = _write_two_agent_session_configs(root)
            start_live_agent_meeting(
                root,
                council_config_path=council_config,
                agent_config_path=agent_config,
                meeting_id="resident-m1",
            )
            supervisor = AgentControlSupervisor(
                root,
                [
                    {
                        "group_id": "resident-main",
                        "status": "running",
                        "meeting_id": "resident-m1",
                        "config_path": str(live_agent_config),
                        "agents": [{"agent_id": "agent-a"}, {"agent_id": "agent-b"}],
                    }
                ],
            )

            with self.assertRaisesRegex(ValueError, "individual STOP\\(KILL\\) requires an agent-owned process"):
                stop_live_agent_session_agent(
                    root,
                    supervisor,
                    meeting_id="resident-m1",
                    group_id="resident-main",
                    agent_id="agent-a",
                )

            self.assertEqual(supervisor.stopped, [])

    def test_resume_agent_refuses_duplicate_when_agent_runs_inside_bundle(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            council_config, agent_config, live_agent_config = _write_two_agent_session_configs(root)
            start_live_agent_meeting(
                root,
                council_config_path=council_config,
                agent_config_path=agent_config,
                meeting_id="resident-m1",
            )
            supervisor = AgentControlSupervisor(
                root,
                [
                    {
                        "group_id": "resident-main",
                        "status": "running",
                        "meeting_id": "resident-m1",
                        "config_path": str(live_agent_config),
                        "agents": [{"agent_id": "agent-a"}, {"agent_id": "agent-b"}],
                    },
                    {
                        "group_id": "resident-main--agent-a",
                        "status": "stopped",
                        "meeting_id": "resident-m1",
                        "config_path": str(live_agent_config),
                        "agents": [{"agent_id": "agent-a"}],
                    },
                ],
            )

            with self.assertRaisesRegex(ValueError, "running inside multi-agent group resident-main"):
                resume_live_agent_session_agent(
                    root,
                    supervisor,
                    server="http://room.local",
                    live_agent_config_path=None,
                    meeting_id="resident-m1",
                    group_id="resident-main--agent-a",
                    agent_id="agent-a",
                    connect_timeout_seconds=0,
                )

            self.assertEqual(supervisor.started, [])

    def test_stop_agent_owned_process_marks_only_that_agent_offline(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            council_config, agent_config, live_agent_config = _write_two_agent_session_configs(root)
            start_live_agent_meeting(
                root,
                council_config_path=council_config,
                agent_config_path=agent_config,
                meeting_id="resident-m1",
            )
            for agent_id in ("agent-a", "agent-b"):
                connect_live_agent(
                    root,
                    {
                        "agent_id": agent_id,
                        "display_name": agent_id,
                        "provider_kind": "local_cli",
                        "connection_kind": "local_cli",
                        "meeting_id": "resident-m1",
                        "status": "online",
                    },
                )
            supervisor = AgentControlSupervisor(
                root,
                [
                    {
                        "group_id": "resident-main--agent-a",
                        "status": "running",
                        "meeting_id": "resident-m1",
                        "config_path": str(live_agent_config),
                        "agents": [{"agent_id": "agent-a"}],
                    }
                ],
            )

            session = stop_live_agent_session_agent(
                root,
                supervisor,
                meeting_id="resident-m1",
                group_id="resident-main--agent-a",
                agent_id="agent-a",
            )

            self.assertEqual(session["status"], "stopped")
            self.assertEqual(supervisor.stopped, ["resident-main--agent-a"])
            agents = {agent["agent_id"]: agent for agent in read_live_agents(root)}
            self.assertEqual(agents["agent-a"]["status"], "offline")
            self.assertEqual(agents["agent-b"]["status"], "online")

    def test_stop_agent_prefers_owned_process_when_requested_group_is_bundle(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            council_config, agent_config, live_agent_config = _write_two_agent_session_configs(root)
            start_live_agent_meeting(
                root,
                council_config_path=council_config,
                agent_config_path=agent_config,
                meeting_id="resident-m1",
            )
            supervisor = AgentControlSupervisor(
                root,
                [
                    {
                        "group_id": "resident-main",
                        "status": "running",
                        "meeting_id": "resident-m1",
                        "config_path": str(live_agent_config),
                        "agents": [{"agent_id": "agent-a"}, {"agent_id": "agent-b"}],
                    },
                    {
                        "group_id": "resident-main--agent-a",
                        "status": "running",
                        "meeting_id": "resident-m1",
                        "config_path": str(live_agent_config),
                        "agents": [{"agent_id": "agent-a"}],
                    },
                ],
            )

            session = stop_live_agent_session_agent(
                root,
                supervisor,
                meeting_id="resident-m1",
                group_id="resident-main",
                agent_id="agent-a",
            )

            self.assertEqual(session["group_id"], "resident-main--agent-a")
            self.assertEqual(supervisor.stopped, ["resident-main--agent-a"])

    def test_resume_agent_rejects_duplicate_agent_entries_in_source_config(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            council_config, agent_config, live_agent_config = _write_two_agent_session_configs(root)
            duplicate_config = json.loads(live_agent_config.read_text(encoding="utf-8"))
            duplicate_config["agents"] = [
                duplicate_config["agents"][0],
                {**duplicate_config["agents"][0], "display_name": "Agent A Duplicate"},
                duplicate_config["agents"][1],
            ]
            live_agent_config.write_text(json.dumps(duplicate_config, ensure_ascii=False), encoding="utf-8")
            start_live_agent_meeting(
                root,
                council_config_path=council_config,
                agent_config_path=agent_config,
                meeting_id="resident-m1",
            )
            supervisor = AgentControlSupervisor(
                root,
                [
                    {
                        "group_id": "resident-main",
                        "status": "stopped",
                        "meeting_id": "resident-m1",
                        "config_path": str(live_agent_config),
                        "agents": [{"agent_id": "agent-a"}, {"agent_id": "agent-b"}],
                    }
                ],
            )

            with self.assertRaisesRegex(ValueError, "duplicate entries for agent-a"):
                resume_live_agent_session_agent(
                    root,
                    supervisor,
                    server="http://room.local",
                    live_agent_config_path=None,
                    meeting_id="resident-m1",
                    group_id="resident-main",
                    agent_id="agent-a",
                    connect_timeout_seconds=0,
                )

            self.assertEqual(supervisor.started, [])

    def test_http_agent_control_routes_use_agent_id_not_group_wide_stop(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            council_config, agent_config, live_agent_config = _write_two_agent_session_configs(root)
            start_live_agent_meeting(
                root,
                council_config_path=council_config,
                agent_config_path=agent_config,
                meeting_id="resident-m1",
            )
            supervisor = AgentControlSupervisor(
                root,
                [
                    {
                        "group_id": "resident-main",
                        "status": "stopped",
                        "meeting_id": "resident-m1",
                        "config_path": str(live_agent_config),
                        "agents": [{"agent_id": "agent-a"}, {"agent_id": "agent-b"}],
                    }
                ],
            )
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root, process_supervisor=supervisor))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                resume_request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/live-agent-sessions/resume-agent",
                    data=json.dumps(
                        {
                            "meeting_id": "resident-m1",
                            "group_id": "resident-main",
                            "agent_id": "agent-a",
                            "connect_timeout_seconds": 0,
                        }
                    ).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urlopen(resume_request, timeout=4) as response:
                    resume_payload = json.loads(response.read().decode("utf-8"))

                stop_request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/live-agent-sessions/stop-agent",
                    data=json.dumps(
                        {
                            "meeting_id": "resident-m1",
                            "group_id": "resident-main",
                            "agent_id": "agent-b",
                        }
                    ).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with self.assertRaises(HTTPError) as raised:
                    urlopen(stop_request, timeout=4)
                error_body = raised.exception.read().decode("utf-8")
                raised.exception.close()
            finally:
                server.shutdown()
                server.server_close()

            self.assertEqual(resume_payload["agent_id"], "agent-a")
            self.assertEqual(resume_payload["group_id"], "resident-main--agent-a")
            self.assertEqual(supervisor.stopped, [])
            self.assertIn("individual STOP(KILL) requires an agent-owned process", error_body)


if __name__ == "__main__":
    unittest.main()
