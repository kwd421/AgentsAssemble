from tests.gui_server_test_support import (
    HTTPError,
    Path,
    Request,
    ThreadingHTTPServer,
    _attach_session_auto_rounds_if_requested,
    _make_handler,
    _write_health_resident_meeting,
    _write_live_jsonl_event,
    _write_lobby_jsonl_event,
    _write_single_agent_session_configs,
    connect_live_agent,
    connect_live_agent_payload,
    heartbeat_live_agent,
    json,
    live_agent_session_ensure_payload,
    patch,
    read_live_agents,
    start_live_agent_meeting,
    sys,
    tempfile,
    threading,
    unittest,
    urlopen,
    write_live_state,
)


class GuiServerSessionLifecycleTests(unittest.TestCase):

    def test_live_agent_session_ensure_resolves_blank_meeting_id_from_owned_ready_group(self):
        class EnsureSessionSupervisor:
            def __init__(self) -> None:
                self.started = []

            def snapshot_groups(self):
                return [
                    {
                        "group_id": "resident-main",
                        "status": "running",
                        "meeting_id": "resident-m1",
                        "agents": [{"agent_id": "agent-a"}],
                    }
                ]

            def list_groups(self):
                return self.snapshot_groups()

            def start_group(self, **kwargs):
                self.started.append(kwargs)
                raise AssertionError("blank meeting ensure should adopt the owned meeting before starting")

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            council_config = root / "council.json"
            agent_config = root / "agents.json"
            live_agent_config = root / "live-agents.json"
            _write_single_agent_session_configs(council_config, agent_config, live_agent_config)
            start_live_agent_meeting(
                root,
                council_config_path=council_config,
                agent_config_path=agent_config,
                meeting_id="resident-m1",
            )
            heartbeat_live_agent(root, "agent-a", status="online")
            supervisor = EnsureSessionSupervisor()
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root, process_supervisor=supervisor))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/live-agent-sessions/ensure",
                    data=json.dumps(
                        {
                            "meeting_id": "",
                            "group_id": "resident-main",
                            "live_agent_config_path": str(live_agent_config),
                        }
                    ).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urlopen(request, timeout=4) as response:
                    session_payload = json.loads(response.read().decode("utf-8"))
                with urlopen(f"http://127.0.0.1:{server.server_port}/api/live-agent-operations?limit=20", timeout=4) as response:
                    operations = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

            self.assertEqual(session_payload["status"], "ready")
            self.assertEqual(session_payload["meeting_id"], "resident-m1")
            self.assertEqual(session_payload["action"], "none")
            self.assertEqual(supervisor.started, [])
            session_operations = [operation for operation in operations["operations"] if operation["operation"] == "session.ensure"]
            self.assertEqual(session_operations[-1]["status"], "success")
            self.assertEqual(session_operations[-1]["target_id"], "resident-m1")
            self.assertEqual(session_operations[-1]["details"]["ensure_action"], "none")


    def test_live_agent_session_ensure_blank_meeting_refuses_missing_owned_meeting_without_new_start(self):
        class EnsureSessionSupervisor:
            def __init__(self) -> None:
                self.started = []

            def snapshot_groups(self):
                return [
                    {
                        "group_id": "resident-main",
                        "status": "running",
                        "meeting_id": "missing-meeting",
                        "agents": [{"agent_id": "agent-a"}],
                    }
                ]

            def list_groups(self):
                return self.snapshot_groups()

            def start_group(self, **kwargs):
                self.started.append(kwargs)
                raise AssertionError("missing owned meeting must be refused before a new start")

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            live_agent_config = root / "live-agents.json"
            _write_single_agent_session_configs(root / "council.json", root / "agents.json", live_agent_config)
            supervisor = EnsureSessionSupervisor()
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root, process_supervisor=supervisor))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/live-agent-sessions/ensure",
                    data=json.dumps(
                        {
                            "meeting_id": "",
                            "group_id": "resident-main",
                            "live_agent_config_path": str(live_agent_config),
                        }
                    ).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with self.assertRaises(HTTPError) as raised:
                    urlopen(request, timeout=4)
                body = raised.exception.read().decode("utf-8")
                error_payload = json.loads(body)
                raised.exception.close()
                with urlopen(f"http://127.0.0.1:{server.server_port}/api/live-agent-operations?limit=20", timeout=4) as response:
                    operations = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

            self.assertEqual(supervisor.started, [])
            self.assertIn("Meeting missing-meeting was not found", body)
            self.assertEqual(error_payload["details"]["requested_meeting_id"], "missing-meeting")
            self.assertEqual(error_payload["details"]["group_id"], "resident-main")
            session_operations = [operation for operation in operations["operations"] if operation["operation"] == "session.ensure"]
            self.assertEqual(session_operations[-1]["status"], "failed")


    def test_live_agent_session_ensure_restarts_ready_session_when_resident_session_id_drifted(self):
        class EnsureSessionSupervisor:
            def __init__(self, output_root: Path, live_agent_config: Path) -> None:
                self.output_root = output_root
                self.live_agent_config = live_agent_config
                self.started = []
                self.stopped = []
                self.restarted = []
                self.group = {
                    "group_id": "resident-main",
                    "status": "running",
                    "meeting_id": "resident-m1",
                    "config_path": str(live_agent_config),
                    "server": "http://127.0.0.1:8765",
                    "agents": [{"agent_id": "agent-a"}],
                }

            def snapshot_groups(self):
                return [dict(self.group)]

            def list_groups(self):
                return self.snapshot_groups()

            def stop_group(self, group_id):
                self.stopped.append(group_id)
                self.group["status"] = "stopped"

            def restart_group(self, group_id):
                self.restarted.append(group_id)
                config = json.loads(self.live_agent_config.read_text(encoding="utf-8"))
                agent = config["agents"][0]
                connect_live_agent_payload(
                    self.output_root,
                    {
                        "agent_id": agent["agent_id"],
                        "display_name": "Agent A",
                        "provider_kind": agent.get("provider_kind", "codex_live_session"),
                        "connection_kind": agent.get("connection_kind", "live_session"),
                        "meeting_id": "resident-m1",
                        "session_id": agent.get("session_id", ""),
                    },
                )
                heartbeat_live_agent(self.output_root, agent["agent_id"], status="online", metadata={"session_id": agent.get("session_id", "")})
                self.group["status"] = "running"
                return dict(self.group)

            def start_group(self, **kwargs):
                self.started.append(kwargs)
                raise AssertionError("drifted ready ensure should restart the existing group")

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            meeting_dir = root / "meetings" / "resident-m1"
            meeting_dir.mkdir(parents=True)
            write_live_state(
                meeting_dir,
                {
                    "meeting_id": "resident-m1",
                    "roles": [{"id": "architect", "display_name": "Architect"}],
                    "provider_configs": {"codex-live": {"id": "codex-live", "kind": "codex_live_session"}},
                    "permission_profiles": {"meeting_readonly": {"id": "meeting_readonly", "meeting_read": True}},
                    "agent_bindings": [
                        {
                            "agent_id": "agent-a",
                            "role_id": "architect",
                            "provider_id": "codex-live",
                            "permission_profile_id": "meeting_readonly",
                            "session_id": "new-session",
                        }
                    ],
                    "debate_rounds": [],
                    "live_status": "running",
                },
            )
            live_agent_config = root / "live-agents.json"
            live_agent_config.write_text(
                json.dumps(
                    {
                        "agents": [
                            {
                                "agent_id": "agent-a",
                                "display_name": "Agent A",
                                "provider_kind": "codex_live_session",
                                "connection_kind": "live_session",
                                "meeting_id": "resident-m1",
                                "session_id": "new-session",
                            }
                        ]
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            connect_live_agent_payload(
                root,
                {
                    "agent_id": "agent-a",
                    "display_name": "Agent A",
                    "provider_kind": "codex_live_session",
                    "connection_kind": "live_session",
                    "meeting_id": "resident-m1",
                    "session_id": "old-session",
                },
            )
            heartbeat_live_agent(root, "agent-a", status="online", metadata={"session_id": "old-session"})
            supervisor = EnsureSessionSupervisor(root, live_agent_config)
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root, process_supervisor=supervisor))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/live-agent-sessions/ensure",
                    data=json.dumps(
                        {
                            "meeting_id": "resident-m1",
                            "group_id": "resident-main",
                            "live_agent_config_path": str(live_agent_config),
                            "connect_timeout_seconds": 0,
                        }
                    ).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with patch(
                    "agentsassemble.legacy.live_agent.runtime.sessions.preflight_live_agent_config",
                    return_value={"status": "ok", "summary": {"agents": 1}},
                ):
                    with urlopen(request, timeout=4) as response:
                        session_payload = json.loads(response.read().decode("utf-8"))
                with urlopen(f"http://127.0.0.1:{server.server_port}/api/live-agent-operations?limit=20", timeout=4) as response:
                    operations = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

            self.assertEqual(session_payload["status"], "ready")
            self.assertEqual(session_payload["action"], "restart")
            self.assertEqual(session_payload["ensure_reason"], "resident_session_id_drift")
            serialized_session = json.dumps(session_payload, ensure_ascii=False)
            self.assertNotIn("old-session", serialized_session)
            self.assertNotIn("new-session", serialized_session)
            self.assertEqual(supervisor.started, [])
            self.assertEqual(supervisor.stopped, ["resident-main"])
            self.assertEqual(supervisor.restarted, ["resident-main"])
            session_operations = [operation for operation in operations["operations"] if operation["operation"] == "session.ensure"]
            self.assertEqual(session_operations[-1]["details"]["ensure_reason"], "resident_session_id_drift")
            agent = next(agent for agent in read_live_agents(root) if agent["agent_id"] == "agent-a")
            self.assertEqual(agent["session_id"], "new-session")


    def test_live_agent_session_ensure_restarts_ready_session_when_stale_lobby_cursor_lags(self):
        class ObservationLagSupervisor:
            def __init__(self, output_root: Path) -> None:
                self.output_root = output_root
                self.started = []
                self.stopped = []
                self.restarted = []
                self.group = {
                    "group_id": "resident-main",
                    "status": "running",
                    "meeting_id": "resident-m1",
                    "agents": [{"agent_id": "agent-a"}],
                    "auto_restart": True,
                    "max_restarts": 3,
                    "restart_count": 0,
                    "stale_restart_after_seconds": 1,
                }

            def snapshot_groups(self):
                return [dict(self.group)]

            def list_groups(self):
                return self.snapshot_groups()

            def stop_group(self, group_id):
                self.stopped.append(group_id)
                self.group["status"] = "stopped"
                return dict(self.group)

            def restart_group(self, group_id, *, restart_count=None):
                self.restarted.append(group_id)
                self.group["status"] = "running"
                if restart_count is not None:
                    self.group["restart_count"] = restart_count
                connect_live_agent(
                    self.output_root,
                    {
                        "agent_id": "agent-a",
                        "display_name": "Agent A",
                        "provider_kind": "local_cli",
                        "connection_kind": "local_cli",
                        "status": "online",
                        "meeting_id": "resident-m1",
                        "last_observed_event_id": "lobby-old",
                    },
                )
                return dict(self.group)

            def start_group(self, **kwargs):
                self.started.append(kwargs)
                raise AssertionError("stale observation ensure should restart the existing group")

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _write_health_resident_meeting(root, agent_ids=["agent-a"])
            _write_lobby_jsonl_event(root, event_id="lobby-old", actor_id="human", created_at="2000-01-01T00:00:00+00:00")
            connect_live_agent(
                root,
                {
                    "agent_id": "agent-a",
                    "display_name": "Agent A",
                    "provider_kind": "local_cli",
                    "connection_kind": "local_cli",
                    "meeting_id": "resident-m1",
                    "last_observed_event_id": "older-event",
                },
            )
            supervisor = ObservationLagSupervisor(root)

            session = live_agent_session_ensure_payload(
                root,
                supervisor,
                {
                    "meeting_id": "resident-m1",
                    "group_id": "resident-main",
                    "connect_timeout_seconds": 0,
                },
                default_server="http://room.local",
            )
            agents = read_live_agents(root)

        self.assertEqual(session["status"], "ready")
        self.assertEqual(session["action"], "restart")
        self.assertEqual(session["ensure_reason"], "stale_lobby_observation")
        self.assertEqual(supervisor.started, [])
        self.assertEqual(supervisor.stopped, ["resident-main"])
        self.assertEqual(supervisor.restarted, ["resident-main"])
        agent = next(agent for agent in agents if agent["agent_id"] == "agent-a")
        self.assertEqual(agent["last_observed_event_id"], "lobby-old")


    def test_live_agent_session_ensure_restarts_ready_session_when_stale_live_cursor_lags(self):
        class ObservationLagSupervisor:
            def __init__(self, output_root: Path) -> None:
                self.output_root = output_root
                self.started = []
                self.stopped = []
                self.restarted = []
                self.group = {
                    "group_id": "resident-main",
                    "status": "running",
                    "meeting_id": "resident-m1",
                    "agents": [{"agent_id": "agent-a"}],
                    "auto_restart": True,
                    "max_restarts": 3,
                    "restart_count": 0,
                    "stale_restart_after_seconds": 1,
                }

            def snapshot_groups(self):
                return [dict(self.group)]

            def list_groups(self):
                return self.snapshot_groups()

            def stop_group(self, group_id):
                self.stopped.append(group_id)
                self.group["status"] = "stopped"
                return dict(self.group)

            def restart_group(self, group_id, *, restart_count=None):
                self.restarted.append(group_id)
                self.group["status"] = "running"
                if restart_count is not None:
                    self.group["restart_count"] = restart_count
                heartbeat_live_agent(self.output_root, "agent-a", status="online", metadata={"last_observed_live_event_id": "live-old"})
                return dict(self.group)

            def start_group(self, **kwargs):
                self.started.append(kwargs)
                raise AssertionError("stale official-turn observation should restart the existing group")

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            meeting_dir = _write_health_resident_meeting(root, agent_ids=["agent-a"])
            request = _write_live_jsonl_event(
                meeting_dir,
                event_id="live-old",
                kind="live_agent_turn_request",
                target_agent_id="agent-a",
                created_at="2000-01-01T00:00:00+00:00",
                content="official request text must stay out",
            )
            connect_live_agent(
                root,
                {
                    "agent_id": "agent-a",
                    "display_name": "Agent A",
                    "provider_kind": "local_cli",
                    "connection_kind": "local_cli",
                    "meeting_id": "resident-m1",
                    "last_observed_live_event_id": "older-live-event",
                },
            )
            supervisor = ObservationLagSupervisor(root)

            session = live_agent_session_ensure_payload(
                root,
                supervisor,
                {
                    "meeting_id": "resident-m1",
                    "group_id": "resident-main",
                    "connect_timeout_seconds": 0,
                },
                default_server="http://room.local",
            )

        self.assertEqual(session["status"], "ready")
        self.assertEqual(session["action"], "restart")
        self.assertEqual(session["ensure_reason"], "stale_live_observation")
        self.assertEqual(supervisor.started, [])
        self.assertEqual(supervisor.stopped, ["resident-main"])
        self.assertEqual(supervisor.restarted, ["resident-main"])
        self.assertNotIn("official request text", json.dumps(session, ensure_ascii=False))
        self.assertEqual(request["id"], "live-old")


    def test_live_agent_session_ensure_does_not_restart_answered_official_turn_lag(self):
        class ObservationLagSupervisor:
            def __init__(self) -> None:
                self.stopped = []
                self.restarted = []
                self.group = {
                    "group_id": "resident-main",
                    "status": "running",
                    "meeting_id": "resident-m1",
                    "agents": [{"agent_id": "agent-a"}],
                    "auto_restart": True,
                    "max_restarts": 3,
                    "restart_count": 0,
                    "stale_restart_after_seconds": 1,
                }

            def snapshot_groups(self):
                return [dict(self.group)]

            def list_groups(self):
                return self.snapshot_groups()

            def stop_group(self, group_id):
                self.stopped.append(group_id)
                raise AssertionError("answered official-turn lag must not stop the group")

            def restart_group(self, group_id):
                self.restarted.append(group_id)
                raise AssertionError("answered official-turn lag must not restart the group")

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            meeting_dir = _write_health_resident_meeting(root, agent_ids=["agent-a"])
            request = _write_live_jsonl_event(
                meeting_dir,
                event_id="live-old",
                kind="live_agent_turn_request",
                target_agent_id="agent-a",
                created_at="2000-01-01T00:00:00+00:00",
                content="official request text must stay out",
            )
            _write_live_jsonl_event(
                meeting_dir,
                event_id="live-reply",
                kind="message",
                actor_id="agent-a",
                source_event_id=request["id"],
                created_at="2000-01-01T00:00:01+00:00",
                content="official reply text must stay out",
            )
            connect_live_agent(
                root,
                {
                    "agent_id": "agent-a",
                    "display_name": "Agent A",
                    "provider_kind": "local_cli",
                    "connection_kind": "local_cli",
                    "meeting_id": "resident-m1",
                    "last_observed_live_event_id": "older-live-event",
                },
            )
            supervisor = ObservationLagSupervisor()

            session = live_agent_session_ensure_payload(
                root,
                supervisor,
                {
                    "meeting_id": "resident-m1",
                    "group_id": "resident-main",
                    "connect_timeout_seconds": 0,
                },
                default_server="http://room.local",
            )

        self.assertEqual(session["status"], "ready")
        self.assertEqual(session["action"], "none")
        self.assertEqual(supervisor.stopped, [])
        self.assertEqual(supervisor.restarted, [])
        self.assertNotIn("official request text", json.dumps(session, ensure_ascii=False))
        self.assertNotIn("official reply text", json.dumps(session, ensure_ascii=False))


    def test_live_agent_session_restart_ignores_external_stale_restart_count_payload(self):
        from agentsassemble.gui import live_agent_session_restart_payload

        class RestartSupervisor:
            def __init__(self, output_root: Path) -> None:
                self.output_root = output_root
                self.restart_counts = []
                self.group = {
                    "group_id": "resident-main",
                    "status": "running",
                    "meeting_id": "resident-m1",
                    "agents": [{"agent_id": "agent-a"}],
                    "auto_restart": True,
                    "max_restarts": 3,
                    "restart_count": 2,
                    "stale_restart_after_seconds": 1,
                }

            def snapshot_groups(self):
                return [dict(self.group)]

            def stop_group(self, group_id):
                self.group["status"] = "stopped"
                return dict(self.group)

            def restart_group(self, group_id, *, restart_count=None):
                self.restart_counts.append(restart_count)
                self.group["status"] = "running"
                self.group["restart_count"] = restart_count if restart_count is not None else 0
                heartbeat_live_agent(self.output_root, "agent-a", status="online")
                return dict(self.group)

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _write_health_resident_meeting(root, agent_ids=["agent-a"])
            connect_live_agent(
                root,
                {
                    "agent_id": "agent-a",
                    "display_name": "Agent A",
                    "provider_kind": "local_cli",
                    "connection_kind": "local_cli",
                    "meeting_id": "resident-m1",
                    "status": "online",
                },
            )
            supervisor = RestartSupervisor(root)

            session = live_agent_session_restart_payload(
                root,
                supervisor,
                {
                    "meeting_id": "resident-m1",
                    "group_id": "resident-main",
                    "connect_timeout_seconds": 0,
                    "_stale_observation_restart_count": 7,
                },
            )

        self.assertEqual(session["status"], "ready")
        self.assertEqual(supervisor.restart_counts, [None])
        self.assertEqual(supervisor.group["restart_count"], 0)


    def test_live_agent_session_ensure_does_not_restart_observation_lag_without_auto_restart(self):
        class ObservationLagSupervisor:
            def __init__(self) -> None:
                self.stopped = []
                self.restarted = []
                self.group = {
                    "group_id": "resident-main",
                    "status": "running",
                    "meeting_id": "resident-m1",
                    "agents": [{"agent_id": "agent-a"}],
                    "auto_restart": False,
                    "max_restarts": 0,
                    "restart_count": 0,
                    "stale_restart_after_seconds": 1,
                }

            def snapshot_groups(self):
                return [dict(self.group)]

            def list_groups(self):
                return self.snapshot_groups()

            def stop_group(self, group_id):
                self.stopped.append(group_id)
                raise AssertionError("disabled auto-restart must not stop the group")

            def restart_group(self, group_id):
                self.restarted.append(group_id)
                raise AssertionError("disabled auto-restart must not restart the group")

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _write_health_resident_meeting(root, agent_ids=["agent-a"])
            _write_lobby_jsonl_event(root, event_id="lobby-old", actor_id="human", created_at="2000-01-01T00:00:00+00:00")
            connect_live_agent(
                root,
                {
                    "agent_id": "agent-a",
                    "display_name": "Agent A",
                    "provider_kind": "local_cli",
                    "connection_kind": "local_cli",
                    "meeting_id": "resident-m1",
                    "last_observed_event_id": "older-event",
                },
            )
            supervisor = ObservationLagSupervisor()

            session = live_agent_session_ensure_payload(
                root,
                supervisor,
                {
                    "meeting_id": "resident-m1",
                    "group_id": "resident-main",
                    "connect_timeout_seconds": 0,
                },
                default_server="http://room.local",
            )

        self.assertEqual(session["status"], "ready")
        self.assertEqual(session["action"], "none")
        self.assertEqual(supervisor.stopped, [])
        self.assertEqual(supervisor.restarted, [])


    def test_live_agent_session_ensure_ready_noop_can_probe_and_run_remaining_rounds(self):
        class EnsureSessionSupervisor:
            def snapshot_groups(self):
                return [
                    {
                        "group_id": "resident-main",
                        "status": "running",
                        "meeting_id": "resident-m1",
                        "agents": [{"agent_id": "agent-a"}],
                    }
                ]

            def list_groups(self):
                return self.snapshot_groups()

            def start_group(self, **kwargs):
                raise AssertionError("ready ensure with post-ready checks must not start a group")

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            council_config = root / "council.json"
            agent_config = root / "agents.json"
            live_agent_config = root / "live-agents.json"
            _write_single_agent_session_configs(council_config, agent_config, live_agent_config)
            start_live_agent_meeting(
                root,
                council_config_path=council_config,
                agent_config_path=agent_config,
                meeting_id="resident-m1",
            )
            heartbeat_live_agent(root, "agent-a", status="online")
            auto_rounds = {
                "status": "answered",
                "meeting_id": "resident-m1",
                "round_count": 1,
                "answered_round_count": 1,
                "completed_round_count": 0,
                "timeout_round_count": 0,
                "skipped_round_count": 0,
                "stopped_round_count": 0,
                "stopped": False,
                "results": [{"round_id": "round_1", "status": "answered"}],
            }
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root, process_supervisor=EnsureSessionSupervisor()))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with (
                    patch(
                        "agentsassemble.gui.run_live_agent_probe",
                        return_value={"status": "ok", "agent_id": "agent-a", "source_event_id": "probe-1", "reply_event_id": "reply-1"},
                    ) as run_probe,
                    patch("agentsassemble.gui.live_agent_turn_rounds_payload", return_value=auto_rounds) as rounds_payload,
                ):
                    request = Request(
                        f"http://127.0.0.1:{server.server_port}/api/live-agent-sessions/ensure",
                        data=json.dumps(
                            {
                                "meeting_id": "resident-m1",
                                "group_id": "resident-main",
                                "live_agent_config_path": str(live_agent_config),
                                "probe_bound_agents": True,
                                "probe_timeout_seconds": 0.5,
                                "run_remaining_rounds": True,
                                "round_timeout_seconds": 2,
                                "round_max_rounds": 1,
                            }
                        ).encode("utf-8"),
                        headers={"Content-Type": "application/json"},
                        method="POST",
                    )
                    with urlopen(request, timeout=4) as response:
                        session_payload = json.loads(response.read().decode("utf-8"))
                with urlopen(f"http://127.0.0.1:{server.server_port}/api/live-agent-operations?limit=20", timeout=4) as response:
                    operations = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

        run_probe.assert_called_once_with(root, "agent-a", timeout_seconds=0.5)
        rounds_payload.assert_called_once()
        self.assertEqual(session_payload["status"], "ready")
        self.assertEqual(session_payload["action"], "none")
        self.assertEqual(session_payload["reply_probe"]["status"], "ok")
        self.assertEqual(session_payload["auto_rounds"]["status"], "answered")
        session_operations = [operation for operation in operations["operations"] if operation["operation"] == "session.ensure"]
        self.assertEqual(session_operations[-1]["status"], "success")
        self.assertEqual(session_operations[-1]["details"]["ensure_action"], "none")
        self.assertEqual(session_operations[-1]["details"]["reply_probe_status"], "ok")
        self.assertEqual(session_operations[-1]["details"]["auto_rounds_status"], "answered")


    def test_live_agent_session_ensure_can_finalize_after_answered_remaining_rounds(self):
        class EnsureSessionSupervisor:
            def snapshot_groups(self):
                return [
                    {
                        "group_id": "resident-main",
                        "status": "running",
                        "meeting_id": "resident-m1",
                        "agents": [{"agent_id": "agent-a"}],
                    }
                ]

            def list_groups(self):
                return self.snapshot_groups()

            def start_group(self, **kwargs):
                raise AssertionError("ready ensure with post-ready checks must not start a group")

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            council_config = root / "council.json"
            agent_config = root / "agents.json"
            live_agent_config = root / "live-agents.json"
            _write_single_agent_session_configs(council_config, agent_config, live_agent_config)
            start_live_agent_meeting(
                root,
                council_config_path=council_config,
                agent_config_path=agent_config,
                meeting_id="resident-m1",
            )
            meeting_dir = root / "meetings" / "resident-m1"
            live_state = json.loads((meeting_dir / "live_state.json").read_text(encoding="utf-8"))
            live_state["debate_rounds"] = [
                {"id": round_item["id"], "status": "answered"}
                for round_item in live_state["meeting_template"]["rounds"]
            ]
            write_live_state(meeting_dir, live_state)
            heartbeat_live_agent(root, "agent-a", status="online")
            auto_rounds = {
                "status": "answered",
                "meeting_id": "resident-m1",
                "round_count": 1,
                "answered_round_count": 1,
                "completed_round_count": 0,
                "timeout_round_count": 0,
                "skipped_round_count": 0,
                "stopped_round_count": 0,
                "stopped": False,
                "results": [{"round_id": "round_1", "status": "answered"}],
            }
            finalized = {
                "status": "finalized",
                "meeting_id": "resident-m1",
                "official_event_count": 1,
                "artifact_event_id": "artifact-1",
                "shared_memory": {
                    "official_event_count": 1,
                    "last_official_event_id": "reply-1",
                    "decision_count": 0,
                    "open_question_count": 0,
                    "action_item_count": 1,
                },
            }
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root, process_supervisor=EnsureSessionSupervisor()))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with (
                    patch("agentsassemble.gui.live_agent_turn_rounds_payload", return_value=auto_rounds) as rounds_payload,
                    patch(
                        "agentsassemble.legacy.meeting.official_rounds.finalize_live_agent_meeting",
                        return_value=finalized,
                    ) as finalize_meeting,
                ):
                    request = Request(
                        f"http://127.0.0.1:{server.server_port}/api/live-agent-sessions/ensure",
                        data=json.dumps(
                            {
                                "meeting_id": "resident-m1",
                                "group_id": "resident-main",
                                "live_agent_config_path": str(live_agent_config),
                                "run_remaining_rounds": True,
                                "round_timeout_seconds": 2,
                                "round_max_rounds": 1,
                                "finalize_after_rounds": True,
                            }
                        ).encode("utf-8"),
                        headers={"Content-Type": "application/json"},
                        method="POST",
                    )
                    with urlopen(request, timeout=4) as response:
                        session_payload = json.loads(response.read().decode("utf-8"))
                with urlopen(f"http://127.0.0.1:{server.server_port}/api/live-agent-operations?limit=20", timeout=4) as response:
                    operations = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

        rounds_payload.assert_called_once()
        finalize_meeting.assert_called_once_with((root / "meetings" / "resident-m1").resolve())
        self.assertEqual(session_payload["auto_rounds"]["status"], "answered")
        self.assertEqual(session_payload["finalization"]["status"], "finalized")
        self.assertEqual(session_payload["finalization"]["official_event_count"], 1)
        session_operations = [operation for operation in operations["operations"] if operation["operation"] == "session.ensure"]
        self.assertEqual(session_operations[-1]["status"], "success")
        self.assertEqual(session_operations[-1]["details"]["auto_rounds_status"], "answered")
        self.assertEqual(session_operations[-1]["details"]["finalization_status"], "finalized")
        self.assertEqual(session_operations[-1]["details"]["finalization_official_event_count"], 1)
        self.assertEqual(session_operations[-1]["details"]["shared_memory_official_event_count"], 1)
        self.assertEqual(session_operations[-1]["details"]["shared_memory_last_event_id"], "reply-1")
        self.assertEqual(session_operations[-1]["details"]["shared_memory_action_item_count"], 1)
        operations_text = json.dumps(operations["operations"], ensure_ascii=False)
        self.assertNotIn("round_1 instruction", operations_text)


    def test_live_agent_session_finalize_after_rounds_skips_when_rounds_timeout(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            session = {"status": "ready", "meeting_id": "resident-m1"}
            with patch(
                "agentsassemble.gui.live_agent_turn_rounds_payload",
                return_value={
                    "status": "timeout",
                    "meeting_id": "resident-m1",
                    "round_count": 1,
                    "answered_round_count": 0,
                    "timeout_round_count": 1,
                    "results": [{"round_id": "round_1", "status": "timeout"}],
                },
            ):
                result = _attach_session_auto_rounds_if_requested(
                    root,
                    session,
                    {
                        "run_remaining_rounds": True,
                        "finalize_after_rounds": True,
                        "round_timeout_seconds": 1,
                        "round_max_rounds": 1,
                    },
                )

        self.assertEqual(result["auto_rounds"]["status"], "timeout")
        self.assertEqual(result["finalization"]["status"], "skipped")
        self.assertEqual(result["finalization"]["reason"], "rounds_not_ready")


    def test_live_agent_session_ensure_resumes_existing_meeting_when_group_is_missing(self):
        class EnsureSessionSupervisor:
            def __init__(self, output_root: Path) -> None:
                self.output_root = output_root
                self.started = []
                self.groups = []

            def snapshot_groups(self):
                return list(self.groups)

            def list_groups(self):
                return list(self.groups)

            def start_group(self, **kwargs):
                self.started.append(kwargs)
                heartbeat_live_agent(self.output_root, "agent-a", status="online")
                group = {
                    "group_id": kwargs.get("group_id") or "resident-main",
                    "status": "running",
                    "meeting_id": kwargs.get("meeting_id"),
                    "agents": [{"agent_id": "agent-a"}],
                }
                self.groups = [group]
                return group

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            council_config = root / "council.json"
            agent_config = root / "agents.json"
            live_agent_config = root / "live-agents.json"
            _write_single_agent_session_configs(council_config, agent_config, live_agent_config)
            start_live_agent_meeting(
                root,
                council_config_path=council_config,
                agent_config_path=agent_config,
                meeting_id="resident-m1",
            )
            supervisor = EnsureSessionSupervisor(root)
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root, process_supervisor=supervisor))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/live-agent-sessions/ensure",
                    data=json.dumps(
                        {
                            "meeting_id": "resident-m1",
                            "group_id": "resident-main",
                            "live_agent_config_path": str(live_agent_config),
                            "connect_timeout_seconds": 0,
                        }
                    ).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urlopen(request, timeout=4) as response:
                    session_payload = json.loads(response.read().decode("utf-8"))
                with urlopen(f"http://127.0.0.1:{server.server_port}/api/live-agent-operations?limit=20", timeout=4) as response:
                    operations = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

            self.assertEqual(session_payload["status"], "ready")
            self.assertEqual(session_payload["action"], "resume")
            self.assertEqual(supervisor.started[0]["group_id"], "resident-main")
            self.assertEqual(supervisor.started[0]["meeting_id"], "resident-m1")
            session_operations = [operation for operation in operations["operations"] if operation["operation"].startswith("session.")]
            self.assertEqual([operation["operation"] for operation in session_operations], ["session.ensure"])
            self.assertEqual(session_operations[-1]["status"], "success")
            self.assertEqual(session_operations[-1]["details"]["ensure_action"], "resume")


    def test_live_agent_session_ensure_selects_start_restart_and_recover_actions(self):
        class EnsureActionSupervisor:
            def __init__(self, output_root: Path, initial_status: str = "") -> None:
                self.output_root = output_root
                self.groups = []
                self.calls = []
                if initial_status:
                    self.groups = [
                        {
                            "group_id": "resident-main",
                            "status": initial_status,
                            "meeting_id": "resident-m1",
                            "agents": [{"agent_id": "agent-a"}],
                        }
                    ]

            def snapshot_groups(self):
                return list(self.groups)

            def list_groups(self):
                return list(self.groups)

            def start_group(self, **kwargs):
                self.calls.append(("start", kwargs))
                return self._running_group(kwargs.get("meeting_id"), kwargs.get("group_id"))

            def restart_group(self, group_id):
                self.calls.append(("restart", {"group_id": group_id}))
                return self._running_group("resident-m1", group_id)

            def recover_group(self, group_id):
                self.calls.append(("recover", {"group_id": group_id}))
                return self._running_group("resident-m1", group_id)

            def _running_group(self, meeting_id, group_id):
                heartbeat_live_agent(self.output_root, "agent-a", status="online")
                group = {
                    "group_id": group_id or "resident-main",
                    "status": "running",
                    "meeting_id": meeting_id,
                    "agents": [{"agent_id": "agent-a"}],
                }
                self.groups = [group]
                return group

        cases = [
            ("start", "", False, "start"),
            ("resume", "restarting", True, "start"),
            ("restart", "stopped", True, "restart"),
            ("recover", "error", True, "recover"),
            ("recover", "unknown", True, "recover"),
        ]
        for expected_action, initial_status, create_meeting, expected_call in cases:
            with self.subTest(action=expected_action):
                with tempfile.TemporaryDirectory() as temp_dir:
                    root = Path(temp_dir)
                    council_config = root / "council.json"
                    agent_config = root / "agents.json"
                    live_agent_config = root / "live-agents.json"
                    _write_single_agent_session_configs(council_config, agent_config, live_agent_config)
                    if create_meeting:
                        start_live_agent_meeting(
                            root,
                            council_config_path=council_config,
                            agent_config_path=agent_config,
                            meeting_id="resident-m1",
                        )
                    supervisor = EnsureActionSupervisor(root, initial_status)

                    session_payload = live_agent_session_ensure_payload(
                        root,
                        supervisor,
                        {
                            "meeting_id": "resident-m1",
                            "group_id": "resident-main",
                            "council_config_path": str(council_config),
                            "agent_config_path": str(agent_config),
                            "live_agent_config_path": str(live_agent_config),
                            "connect_timeout_seconds": 0,
                        },
                        default_server="http://127.0.0.1:8765",
                    )

                    self.assertEqual(session_payload["status"], "ready")
                    self.assertEqual(session_payload["action"], expected_action)
                    self.assertEqual(supervisor.calls[0][0], expected_call)


    def test_live_agent_session_restart_returns_ready_snapshot_and_records_safe_operation(self):
        class RestartSessionSupervisor:
            def __init__(self, root: Path, config_path: Path) -> None:
                self.root = root
                self.config_path = config_path
                self.stopped = []
                self.restarted = []

            def snapshot_groups(self):
                return [
                    {
                        "group_id": "resident-main",
                        "status": "running",
                        "config_path": str(self.config_path),
                        "server": "http://127.0.0.1:8765",
                        "log_tail": "secret provider output",
                        "agents": [{"agent_id": "agent-a"}],
                    }
                ]

            def stop_group(self, group_id):
                self.stopped.append(group_id)
                return {"group_id": group_id, "status": "stopped", "agents": [{"agent_id": "agent-a"}]}

            def restart_group(self, group_id):
                self.restarted.append(group_id)
                agents = {agent["agent_id"]: agent for agent in read_live_agents(self.root)}
                if agents["agent-a"]["status"] != "offline":
                    raise AssertionError("restart must clear stale presence before starting the group again")
                heartbeat_live_agent(self.root, "agent-a", status="online")
                return {
                    "group_id": group_id,
                    "status": "running",
                    "config_path": str(self.config_path),
                    "server": "http://127.0.0.1:8765",
                    "log_tail": "secret provider output",
                    "agents": [{"agent_id": "agent-a"}],
                }

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            council_config = root / "council.json"
            agent_config = root / "agents.json"
            live_agent_config = root / "live-agents.json"
            _write_single_agent_session_configs(council_config, agent_config, live_agent_config)
            start_live_agent_meeting(
                root,
                council_config_path=council_config,
                agent_config_path=agent_config,
                meeting_id="resident-m1",
            )
            heartbeat_live_agent(root, "agent-a", status="online")
            supervisor = RestartSessionSupervisor(root, live_agent_config)
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root, process_supervisor=supervisor))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/live-agent-sessions/restart",
                    data=json.dumps(
                        {
                            "meeting_id": "resident-m1",
                            "group_id": "resident main",
                            "connect_timeout_seconds": 0,
                        }
                    ).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urlopen(request, timeout=4) as response:
                    session_payload = json.loads(response.read().decode("utf-8"))
                with urlopen(f"http://127.0.0.1:{server.server_port}/api/live-agent-operations?limit=20", timeout=4) as response:
                    operations = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

            self.assertEqual(supervisor.stopped, ["resident-main"])
            self.assertEqual(supervisor.restarted, ["resident-main"])
            self.assertEqual(session_payload["status"], "ready")
            self.assertEqual(session_payload["group_id"], "resident-main")
            self.assertEqual(session_payload["connection"]["connected"], 1)
            session_operations = [operation for operation in operations["operations"] if operation["operation"] == "session.restart"]
            self.assertEqual(session_operations[-1]["status"], "success")
            self.assertEqual(session_operations[-1]["target_id"], "resident-m1")
            self.assertEqual(session_operations[-1]["details"]["connected_agent_count"], 1)
            operation_blob = json.dumps(session_operations, ensure_ascii=False)
            self.assertNotIn(str(live_agent_config), operation_blob)
            self.assertNotIn("/private/live-agents.json", operation_blob)
            self.assertNotIn("secret provider output", operation_blob)


    def test_live_agent_session_restart_auto_runs_remaining_rounds_when_ready(self):
        class RestartSessionSupervisor:
            def __init__(self, root: Path, config_path: Path) -> None:
                self.root = root
                self.config_path = config_path

            def snapshot_groups(self):
                return [
                    {
                        "group_id": "resident-main",
                        "status": "running",
                        "config_path": str(self.config_path),
                        "server": "http://127.0.0.1:8765",
                        "agents": [{"agent_id": "agent-a"}],
                    }
                ]

            def stop_group(self, group_id):
                return {"group_id": group_id, "status": "stopped", "agents": [{"agent_id": "agent-a"}]}

            def restart_group(self, group_id):
                heartbeat_live_agent(self.root, "agent-a", status="online")
                return {"group_id": group_id, "status": "running", "agents": [{"agent_id": "agent-a"}]}

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            council_config = root / "council.json"
            agent_config = root / "agents.json"
            live_agent_config = root / "live-agents.json"
            _write_single_agent_session_configs(council_config, agent_config, live_agent_config)
            start_live_agent_meeting(
                root,
                council_config_path=council_config,
                agent_config_path=agent_config,
                meeting_id="resident-m1",
            )
            heartbeat_live_agent(root, "agent-a", status="online")
            supervisor = RestartSessionSupervisor(root, live_agent_config)
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root, process_supervisor=supervisor))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            auto_rounds = {
                "status": "answered",
                "meeting_id": "resident-m1",
                "round_count": 1,
                "answered_round_count": 1,
                "completed_round_count": 0,
                "timeout_round_count": 0,
                "skipped_round_count": 0,
                "stopped_round_count": 0,
                "stopped": False,
                "timeout_seconds": 8.0,
                "max_rounds": 2,
                "results": [{"round_id": "round_1", "status": "answered", "role_ids": ["architect"]}],
            }
            try:
                with patch("agentsassemble.gui.live_agent_turn_rounds_payload", return_value=auto_rounds) as rounds_payload:
                    request = Request(
                        f"http://127.0.0.1:{server.server_port}/api/live-agent-sessions/restart",
                        data=json.dumps(
                            {
                                "meeting_id": "resident-m1",
                                "group_id": "resident-main",
                                "connect_timeout_seconds": 0,
                                "run_remaining_rounds": True,
                                "round_timeout_seconds": 8,
                                "round_max_rounds": 2,
                                "round_stop_on_timeout": True,
                            }
                        ).encode("utf-8"),
                        headers={"Content-Type": "application/json"},
                        method="POST",
                    )
                    with urlopen(request, timeout=4) as response:
                        session_payload = json.loads(response.read().decode("utf-8"))
                with urlopen(f"http://127.0.0.1:{server.server_port}/api/live-agent-operations?limit=20", timeout=4) as response:
                    operations = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

        rounds_payload.assert_called_once_with(
            root,
            "resident-m1",
            {"timeout_seconds": 8.0, "max_rounds": 2, "stop_on_timeout": True},
        )
        self.assertEqual(session_payload["status"], "ready")
        self.assertEqual(session_payload["auto_rounds"]["status"], "answered")
        session_operations = [operation for operation in operations["operations"] if operation["operation"] == "session.restart"]
        self.assertEqual(session_operations[-1]["status"], "success")
        self.assertEqual(session_operations[-1]["summary"], "restarted resident live-agent session and ran remaining rounds")
        self.assertEqual(session_operations[-1]["details"]["auto_rounds_status"], "answered")
        self.assertEqual(session_operations[-1]["details"]["auto_rounds_round_count"], 1)


    def test_live_agent_session_restart_missing_meeting_returns_safe_error(self):
        class RestartSessionSupervisor:
            def snapshot_groups(self):
                return [{"group_id": "resident-main", "status": "running", "agents": [{"agent_id": "agent-a"}]}]

            def restart_group(self, group_id):
                raise AssertionError("missing meeting must be refused before restart")

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root, process_supervisor=RestartSessionSupervisor()))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/live-agent-sessions/restart",
                    data=json.dumps({"meeting_id": "missing-meeting", "group_id": "resident-main"}).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with self.assertRaises(HTTPError) as raised:
                    urlopen(request, timeout=4)
                body = raised.exception.read().decode("utf-8")
                error_payload = json.loads(body)
                raised.exception.close()
                with urlopen(f"http://127.0.0.1:{server.server_port}/api/live-agent-operations?limit=20", timeout=4) as response:
                    operations = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

            self.assertIn("Meeting missing-meeting was not found", body)
            self.assertEqual(error_payload["details"]["requested_meeting_id"], "missing-meeting")
            self.assertEqual(error_payload["details"]["group_id"], "resident-main")
            session_operations = [operation for operation in operations["operations"] if operation["operation"] == "session.restart"]
            self.assertEqual(session_operations[-1]["status"], "failed")


    def test_live_agent_session_recover_returns_ready_snapshot_and_records_safe_operation(self):
        class RecoverSessionSupervisor:
            def __init__(self, root: Path) -> None:
                self.root = root
                self.recovered = []

            def snapshot_groups(self):
                return [
                    {
                        "group_id": "resident-main",
                        "status": "unknown",
                        "config_path": str(self.root / "live-agents.json"),
                        "server": "http://127.0.0.1:8765",
                        "log_tail": "secret provider output",
                        "agents": [{"agent_id": "agent-a"}],
                    }
                ]

            def recover_group(self, group_id):
                self.recovered.append(group_id)
                agents = {agent["agent_id"]: agent for agent in read_live_agents(self.root)}
                if agents["agent-a"]["status"] != "offline":
                    raise AssertionError("recover must clear stale presence before starting the group again")
                heartbeat_live_agent(self.root, "agent-a", status="online")
                return {
                    "group_id": group_id,
                    "status": "running",
                    "config_path": "/private/live-agents.json",
                    "log_tail": "secret provider output",
                    "agents": [{"agent_id": "agent-a"}],
                    "recovered_from_status": "unknown",
                }

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            council_config = root / "council.json"
            agent_config = root / "agents.json"
            live_agent_config = root / "live-agents.json"
            _write_single_agent_session_configs(council_config, agent_config, live_agent_config)
            start_live_agent_meeting(
                root,
                council_config_path=council_config,
                agent_config_path=agent_config,
                meeting_id="resident-m1",
            )
            heartbeat_live_agent(root, "agent-a", status="online")
            supervisor = RecoverSessionSupervisor(root)
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root, process_supervisor=supervisor))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/live-agent-sessions/recover",
                    data=json.dumps(
                        {
                            "meeting_id": "resident-m1",
                            "group_id": "resident main",
                            "connect_timeout_seconds": 0,
                        }
                    ).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urlopen(request, timeout=4) as response:
                    session_payload = json.loads(response.read().decode("utf-8"))
                with urlopen(f"http://127.0.0.1:{server.server_port}/api/live-agent-operations?limit=20", timeout=4) as response:
                    operations = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

            self.assertEqual(supervisor.recovered, ["resident-main"])
            self.assertEqual(session_payload["status"], "ready")
            self.assertEqual(session_payload["group_id"], "resident-main")
            self.assertEqual(session_payload["connection"]["connected"], 1)
            self.assertEqual(session_payload["offline"]["offline"], 1)
            session_operations = [operation for operation in operations["operations"] if operation["operation"] == "session.recover"]
            self.assertEqual(session_operations[-1]["status"], "success")
            self.assertEqual(session_operations[-1]["target_id"], "resident-m1")
            self.assertEqual(session_operations[-1]["details"]["connected_agent_count"], 1)
            self.assertEqual(session_operations[-1]["details"]["offline_agent_count"], 1)
            operation_blob = json.dumps(session_operations, ensure_ascii=False)
            self.assertNotIn(str(live_agent_config), operation_blob)
            self.assertNotIn("/private/live-agents.json", operation_blob)
            self.assertNotIn("secret provider output", operation_blob)


    def test_live_agent_session_recover_auto_rounds_are_skipped_until_ready(self):
        class SlowRecoverSessionSupervisor:
            def __init__(self, root: Path) -> None:
                self.root = root

            def snapshot_groups(self):
                return [
                    {
                        "group_id": "resident-main",
                        "status": "unknown",
                        "config_path": str(self.root / "live-agents.json"),
                        "server": "http://127.0.0.1:8765",
                        "agents": [{"agent_id": "agent-a"}],
                    }
                ]

            def recover_group(self, group_id):
                return {"group_id": group_id, "status": "running", "agents": [{"agent_id": "agent-a"}]}

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            council_config = root / "council.json"
            agent_config = root / "agents.json"
            live_agent_config = root / "live-agents.json"
            _write_single_agent_session_configs(council_config, agent_config, live_agent_config)
            start_live_agent_meeting(
                root,
                council_config_path=council_config,
                agent_config_path=agent_config,
                meeting_id="resident-m1",
            )
            heartbeat_live_agent(root, "agent-a", status="offline")
            supervisor = SlowRecoverSessionSupervisor(root)
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root, process_supervisor=supervisor))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with patch("agentsassemble.gui.live_agent_turn_rounds_payload") as rounds_payload:
                    request = Request(
                        f"http://127.0.0.1:{server.server_port}/api/live-agent-sessions/recover",
                        data=json.dumps(
                            {
                                "meeting_id": "resident-m1",
                                "group_id": "resident-main",
                                "connect_timeout_seconds": 0,
                                "run_remaining_rounds": True,
                                "round_timeout_seconds": 8,
                                "round_max_rounds": 2,
                                "round_stop_on_timeout": True,
                            }
                        ).encode("utf-8"),
                        headers={"Content-Type": "application/json"},
                        method="POST",
                    )
                    with urlopen(request, timeout=4) as response:
                        session_payload = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

        rounds_payload.assert_not_called()
        self.assertEqual(session_payload["status"], "starting")
        self.assertEqual(session_payload["auto_rounds"]["status"], "skipped")
        self.assertEqual(session_payload["auto_rounds"]["reason"], "session_not_ready")


    def test_live_agent_session_recover_persisted_preflight_failure_records_safe_error_without_roster_reset(self):
        class RecoverSessionSupervisor:
            def __init__(self, config_path: Path) -> None:
                self.config_path = config_path
                self.recovered = []

            def snapshot_groups(self):
                return [
                    {
                        "group_id": "resident-main",
                        "status": "unknown",
                        "config_path": str(self.config_path),
                        "server": "http://127.0.0.1:8765",
                        "log_tail": "secret provider output",
                        "agents": [{"agent_id": "agent-a"}],
                    }
                ]

            def recover_group(self, group_id):
                self.recovered.append(group_id)
                raise AssertionError("recover-session must preflight persisted config before recovery")

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            council_config = root / "council.json"
            agent_config = root / "agents.json"
            live_agent_config = root / "live-agents.json"
            _write_single_agent_session_configs(council_config, agent_config, live_agent_config)
            live_agent_config.write_text(
                json.dumps(
                    {
                        "agents": [
                            {
                                "agent_id": "agent-a",
                                "display_name": "Agent A",
                                "provider_kind": "local_cli",
                                "connection_kind": "local_cli",
                                "command": [sys.executable, "-c", "print('ok')"],
                            },
                            {
                                "agent_id": "agent-a",
                                "display_name": "Agent A Duplicate",
                                "provider_kind": "local_cli",
                                "connection_kind": "local_cli",
                                "command": [sys.executable, "-c", "print('ok')"],
                            },
                        ]
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            start_live_agent_meeting(
                root,
                council_config_path=council_config,
                agent_config_path=agent_config,
                meeting_id="resident-m1",
            )
            heartbeat_live_agent(root, "agent-a", status="online")
            supervisor = RecoverSessionSupervisor(live_agent_config)
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root, process_supervisor=supervisor))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/live-agent-sessions/recover",
                    data=json.dumps({"meeting_id": "resident-m1", "group_id": "resident main"}).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with self.assertRaises(HTTPError) as raised:
                    urlopen(request, timeout=4)
                body = raised.exception.read().decode("utf-8")
                raised.exception.close()
                with urlopen(f"http://127.0.0.1:{server.server_port}/api/live-agent-operations?limit=20", timeout=4) as response:
                    operations = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

            self.assertIn("Duplicate agent ids", body)
            self.assertEqual(supervisor.recovered, [])
            agents = {agent["agent_id"]: agent for agent in read_live_agents(root)}
            self.assertEqual(agents["agent-a"]["status"], "online")
            session_operations = [operation for operation in operations["operations"] if operation["operation"] == "session.recover"]
            self.assertEqual(session_operations[-1]["status"], "failed")
            operation_blob = json.dumps(session_operations, ensure_ascii=False)
            self.assertNotIn(str(live_agent_config), operation_blob)
            self.assertNotIn("secret provider output", operation_blob)


    def test_live_agent_session_recover_missing_meeting_returns_safe_error(self):
        class RecoverSessionSupervisor:
            def snapshot_groups(self):
                return [{"group_id": "resident-main", "status": "unknown", "agents": [{"agent_id": "agent-a"}]}]

            def recover_group(self, group_id):
                raise AssertionError("missing meeting must be refused before recover")

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root, process_supervisor=RecoverSessionSupervisor()))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/live-agent-sessions/recover",
                    data=json.dumps({"meeting_id": "missing-meeting", "group_id": "resident-main"}).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with self.assertRaises(HTTPError) as raised:
                    urlopen(request, timeout=4)
                body = raised.exception.read().decode("utf-8")
                error_payload = json.loads(body)
                raised.exception.close()
                with urlopen(f"http://127.0.0.1:{server.server_port}/api/live-agent-operations?limit=20", timeout=4) as response:
                    operations = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

            self.assertIn("Meeting missing-meeting was not found", body)
            self.assertEqual(error_payload["details"]["requested_meeting_id"], "missing-meeting")
            self.assertEqual(error_payload["details"]["group_id"], "resident-main")
            session_operations = [operation for operation in operations["operations"] if operation["operation"] == "session.recover"]
            self.assertEqual(session_operations[-1]["status"], "failed")
