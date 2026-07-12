from tests.gui_server_test_support import (
    HTTPError,
    LiveAgentProcessSupervisor,
    LiveAgentSessionRunController,
    Path,
    Request,
    ThreadingHTTPServer,
    _make_handler,
    _run_session_bound_agent_probe,
    _write_fake_codex_executable,
    _write_single_agent_session_configs,
    _write_three_agent_fake_session_configs,
    build_meeting_payload,
    connect_live_agent,
    connect_live_agent_payload,
    heartbeat_live_agent,
    json,
    os,
    patch,
    read_live_agents,
    read_live_events,
    start_live_agent_meeting,
    sys,
    tempfile,
    threading,
    time,
    unittest,
    urlopen,
    write_live_state,
)


class GuiServerSessionLifecycleTests(unittest.TestCase):

    def test_live_agent_meeting_start_creates_visible_bound_meeting_and_round_control(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            council_config = root / "council.json"
            agent_config = root / "agents.json"
            council_config.write_text(
                json.dumps(
                    {
                        "topic": "resident runtime",
                        "question": "Can resident agents run a real official round?",
                        "roles": [
                            {
                                "id": "architect",
                                "display_name": "Architect",
                                "lens": "Architecture",
                                "research_focus": "system shape",
                            },
                            {
                                "id": "critic",
                                "display_name": "Critic",
                                "lens": "Critique",
                                "research_focus": "risk",
                            },
                        ],
                        "meeting_template": {
                            "id": "resident-template",
                            "display_name": "Resident Template",
                            "rounds": [
                                {
                                    "id": "round_1",
                                    "title": "Resident round",
                                    "instruction": "Template instruction",
                                    "turn_control": {"selection": "all_roles"},
                                }
                            ],
                        },
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            agent_config.write_text(
                json.dumps(
                    {
                        "providers": [
                            {
                                "id": "local-cli",
                                "kind": "local_cli",
                                "display_name": "Local CLI",
                                "endpoint": "https://api.example/run?api_key=SECRET",
                                "command": ["fake-agent"],
                            }
                        ],
                        "permission_profiles": [
                            {
                                "id": "meeting_readonly",
                                "meeting_read": True,
                                "lobby_chat": True,
                                "official_turn": True,
                            }
                        ],
                        "agent_bindings": [
                            {
                                "agent_id": "agent-a",
                                "role_id": "architect",
                                "provider_id": "local-cli",
                                "permission_profile_id": "meeting_readonly",
                                "engagement_mode": "always",
                            },
                            {
                                "agent_id": "agent-b",
                                "role_id": "critic",
                                "provider_id": "local-cli",
                                "permission_profile_id": "meeting_readonly",
                                "engagement_mode": "watch",
                            },
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            meeting_dir = root / "meetings" / "resident-m1"
            errors = []
            answered_request_ids = set()

            def answer_round_requests():
                try:
                    deadline = time.time() + 4
                    while time.time() < deadline and len(answered_request_ids) < 2:
                        if not meeting_dir.exists():
                            time.sleep(0.01)
                            continue
                        for event in read_live_events(meeting_dir, limit=None):
                            if event.get("kind") != "live_agent_turn_request":
                                continue
                            if event["id"] in answered_request_ids:
                                continue
                            agent_id = event["target_agent_id"]
                            reply_request = Request(
                                f"http://127.0.0.1:{server.server_port}/api/live-agents/{agent_id}/official-turn",
                                data=json.dumps(
                                    {
                                        "meeting_id": "resident-m1",
                                        "source_event_id": event["id"],
                                        "content": f"{agent_id} resident reply",
                                    }
                                ).encode("utf-8"),
                                headers={"Content-Type": "application/json"},
                                method="POST",
                            )
                            with urlopen(reply_request, timeout=4) as response:
                                response.read()
                            answered_request_ids.add(event["id"])
                        time.sleep(0.01)
                except Exception as error:  # pragma: no cover - failure is asserted below
                    errors.append(error)

            responder = threading.Thread(target=answer_round_requests, daemon=True)
            responder.start()
            try:
                start_request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/live-agent-meetings/start",
                    data=json.dumps(
                        {
                            "meeting_id": "resident-m1",
                            "council_config_path": str(council_config),
                            "agent_config_path": str(agent_config),
                        }
                    ).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urlopen(start_request, timeout=4) as response:
                    start_payload = json.loads(response.read().decode("utf-8"))
                with urlopen(f"http://127.0.0.1:{server.server_port}/api/meetings", timeout=4) as response:
                    meetings_payload = json.loads(response.read().decode("utf-8"))
                with urlopen(f"http://127.0.0.1:{server.server_port}/api/live-agents", timeout=4) as response:
                    agents_payload = json.loads(response.read().decode("utf-8"))
                round_request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/meetings/resident-m1/live-agent-turns/round",
                    data=json.dumps({"round_id": "round_1", "timeout_seconds": 2}).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urlopen(round_request, timeout=6) as response:
                    round_result = json.loads(response.read().decode("utf-8"))
                responder.join(timeout=2)
                with urlopen(f"http://127.0.0.1:{server.server_port}/api/live-agent-operations?limit=20", timeout=4) as response:
                    operations = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

            if errors:
                raise errors[0]
            self.assertEqual(start_payload["meeting_id"], "resident-m1")
            self.assertEqual(start_payload["meeting"]["live_status"], "running")
            self.assertFalse(start_payload["meeting"].get("diagnostic", False))
            self.assertIn("resident-m1", {meeting["meeting_id"] for meeting in meetings_payload["meetings"]})
            agents = {agent["agent_id"]: agent for agent in agents_payload["agents"]}
            binding_modes = {binding["agent_id"]: binding["engagement_mode"] for binding in start_payload["meeting"]["agent_bindings"]}
            self.assertEqual(binding_modes["agent-a"], "moderator_called")
            self.assertEqual(binding_modes["agent-b"], "moderator_called")
            self.assertEqual(agents["agent-a"]["meeting_id"], "resident-m1")
            self.assertEqual(agents["agent-b"]["meeting_id"], "resident-m1")
            self.assertEqual(agents["agent-a"]["engagement_mode"], "moderator_called")
            self.assertEqual(agents["agent-b"]["engagement_mode"], "moderator_called")
            self.assertEqual(agents["agent-a"]["endpoint"], "")
            self.assertEqual(agents["agent-b"]["endpoint"], "")
            self.assertEqual(round_result["status"], "answered")
            self.assertEqual([result["agent_id"] for result in round_result["results"]], ["agent-a", "agent-b"])
            transcript = build_meeting_payload(meeting_dir)["artifacts"]["transcript.md"]
            self.assertIn("agent-a resident reply", transcript)
            self.assertIn("agent-b resident reply", transcript)
            operation_pairs = [(item["operation"], item["status"], item["target_id"]) for item in operations["operations"]]
            self.assertIn(("meeting.start", "success", "resident-m1"), operation_pairs)
            self.assertIn(("official_turn.round", "success", "resident-m1"), operation_pairs)
            operation_blob = json.dumps(operations["operations"], ensure_ascii=False)
            self.assertNotIn(str(council_config), operation_blob)
            self.assertNotIn(str(agent_config), operation_blob)
            self.assertNotIn("resident reply", operation_blob)


    def test_live_agent_meeting_start_rejects_path_traversal_meeting_id(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/live-agent-meetings/start",
                    data=json.dumps({"meeting_id": "../outside"}).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with self.assertRaises(HTTPError) as raised:
                    urlopen(request, timeout=4)
                self.assertEqual(raised.exception.code, 400)
                raised.exception.close()
            finally:
                server.shutdown()
                server.server_close()

            self.assertFalse((root / "outside").exists())


    def test_live_agent_session_start_creates_meeting_starts_group_and_records_safe_operation(self):
        class FakeSessionSupervisor:
            def __init__(self, output_root: Path) -> None:
                self.output_root = output_root
                self.started = []
                self.group = {}

            def start_group(self, **kwargs):
                self.started.append(kwargs)
                heartbeat_live_agent(self.output_root, "agent-a", status="online")
                heartbeat_live_agent(self.output_root, "agent-b", status="working")
                self.group = {
                    "group_id": kwargs.get("group_id") or "resident-main",
                    "status": "running",
                    "agents": [
                        {"agent_id": "agent-a", "display_name": "Agent A", "provider_kind": "local_cli", "connection_kind": "local_cli"},
                        {"agent_id": "agent-b", "display_name": "Agent B", "provider_kind": "local_cli", "connection_kind": "local_cli"},
                    ],
                }
                return self.group

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            council_config = root / "council.json"
            agent_config = root / "agents.json"
            live_agent_config = root / "live-agents.json"
            council_config.write_text(
                json.dumps(
                    {
                        "topic": "resident session",
                        "question": "Can a resident session start in one operation?",
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
                        "providers": [
                            {
                                "id": "local-cli",
                                "kind": "local_cli",
                                "display_name": "Local CLI",
                                "command": [sys.executable, "-c", "print('ok')"],
                            }
                        ],
                        "permission_profiles": [
                            {"id": "meeting_readonly", "meeting_read": True, "lobby_chat": True, "official_turn": True}
                        ],
                        "agent_bindings": [
                            {
                                "agent_id": "agent-a",
                                "role_id": "architect",
                                "provider_id": "local-cli",
                                "permission_profile_id": "meeting_readonly",
                                "engagement_mode": "always",
                            },
                            {
                                "agent_id": "agent-b",
                                "role_id": "critic",
                                "provider_id": "local-cli",
                                "permission_profile_id": "meeting_readonly",
                                "engagement_mode": "watch",
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
                        "agents": [
                            {
                                "agent_id": "agent-a",
                                "display_name": "Agent A",
                                "provider_kind": "local_cli",
                                "connection_kind": "local_cli",
                                "command": [sys.executable, "-c", "print('ok')"],
                            },
                            {
                                "agent_id": "agent-b",
                                "display_name": "Agent B",
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
            supervisor = FakeSessionSupervisor(root)
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root, process_supervisor=supervisor))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/live-agent-sessions/start",
                    data=json.dumps(
                        {
                            "meeting_id": "resident-m1",
                            "group_id": "resident-main",
                            "council_config_path": str(council_config),
                            "agent_config_path": str(agent_config),
                            "live_agent_config_path": str(live_agent_config),
                            "connect_timeout_seconds": 0,
                            "auto_restart": True,
                            "max_restarts": 2,
                            "restart_backoff_seconds": 1,
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
            self.assertEqual(session_payload["group_id"], "resident-main")
            self.assertEqual(session_payload["meeting"]["role_count"], 2)
            self.assertEqual(session_payload["meeting"]["bound_agent_count"], 2)
            self.assertEqual(session_payload["group"], {"group_id": "resident-main", "status": "running"})
            self.assertEqual(session_payload["connection"]["connected"], 2)
            self.assertEqual(session_payload["connection"]["expected"], 2)
            self.assertEqual(supervisor.started[0]["config_path"], live_agent_config)
            self.assertEqual(supervisor.started[0]["group_id"], "resident-main")
            self.assertTrue(supervisor.started[0]["auto_restart"])
            self.assertEqual(supervisor.started[0]["max_restarts"], 2)
            meeting = json.loads((root / "meetings" / "resident-m1" / "live_state.json").read_text(encoding="utf-8"))
            self.assertEqual({binding["engagement_mode"] for binding in meeting["agent_bindings"]}, {"moderator_called"})
            session_operations = [operation for operation in operations["operations"] if operation["operation"] == "session.start"]
            self.assertEqual(session_operations[-1]["status"], "success")
            self.assertEqual(session_operations[-1]["target_id"], "resident-m1")
            self.assertEqual(session_operations[-1]["details"]["result_status"], "ready")
            operation_blob = json.dumps(session_operations, ensure_ascii=False)
            self.assertNotIn(str(council_config), operation_blob)
            self.assertNotIn(str(agent_config), operation_blob)
            self.assertNotIn(str(live_agent_config), operation_blob)


    def test_live_agent_session_start_auto_runs_remaining_rounds_when_ready(self):
        class FakeSessionSupervisor:
            def __init__(self, output_root: Path) -> None:
                self.output_root = output_root
                self.started = []

            def start_group(self, **kwargs):
                self.started.append(kwargs)
                heartbeat_live_agent(self.output_root, "agent-a", status="online")
                return {
                    "group_id": kwargs.get("group_id") or "resident-main",
                    "status": "running",
                    "agents": [
                        {"agent_id": "agent-a", "display_name": "Agent A", "provider_kind": "local_cli", "connection_kind": "local_cli"},
                    ],
                }

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            council_config = root / "council.json"
            agent_config = root / "agents.json"
            live_agent_config = root / "live-agents.json"
            council_config.write_text(
                json.dumps(
                    {
                        "topic": "resident session",
                        "question": "Can a resident session run itself?",
                        "roles": [
                            {"id": "architect", "display_name": "Architect", "lens": "Architecture", "research_focus": "system"},
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
                            }
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
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
                            }
                        ]
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            supervisor = FakeSessionSupervisor(root)
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root, process_supervisor=supervisor))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            errors = []
            answered_request_ids = set()

            def answer_auto_round_request():
                try:
                    meeting_dir = root / "meetings" / "resident-m1"
                    deadline = time.time() + 4
                    while time.time() < deadline and not answered_request_ids:
                        if not meeting_dir.exists():
                            time.sleep(0.01)
                            continue
                        for event in read_live_events(meeting_dir, limit=None):
                            if event.get("kind") != "live_agent_turn_request":
                                continue
                            reply_request = Request(
                                f"http://127.0.0.1:{server.server_port}/api/live-agents/{event['target_agent_id']}/official-turn",
                                data=json.dumps(
                                    {
                                        "meeting_id": "resident-m1",
                                        "source_event_id": event["id"],
                                        "content": "auto round reply",
                                    }
                                ).encode("utf-8"),
                                headers={"Content-Type": "application/json"},
                                method="POST",
                            )
                            with urlopen(reply_request, timeout=4) as response:
                                response.read()
                            answered_request_ids.add(event["id"])
                            break
                        time.sleep(0.01)
                except Exception as error:  # pragma: no cover - failure is asserted below
                    errors.append(error)

            responder = threading.Thread(target=answer_auto_round_request, daemon=True)
            responder.start()
            try:
                request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/live-agent-sessions/start",
                    data=json.dumps(
                        {
                            "meeting_id": "resident-m1",
                            "group_id": "resident-main",
                            "council_config_path": str(council_config),
                            "agent_config_path": str(agent_config),
                            "live_agent_config_path": str(live_agent_config),
                            "connect_timeout_seconds": 0,
                            "run_remaining_rounds": True,
                            "round_timeout_seconds": 2,
                            "round_max_rounds": 1,
                            "round_stop_on_timeout": True,
                        }
                    ).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urlopen(request, timeout=7) as response:
                    session_payload = json.loads(response.read().decode("utf-8"))
                responder.join(timeout=2)
                with urlopen(f"http://127.0.0.1:{server.server_port}/api/live-agent-operations?limit=20", timeout=4) as response:
                    operations = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

            if errors:
                raise errors[0]
            self.assertEqual(session_payload["status"], "ready")
            self.assertEqual(session_payload["auto_rounds"]["status"], "answered")
            self.assertEqual(session_payload["auto_rounds"]["round_count"], 1)
            self.assertEqual(session_payload["auto_rounds"]["answered_round_count"], 1)
            self.assertEqual(session_payload["auto_rounds"]["results"][0]["round_id"], "round_1")
            meeting_dir = root / "meetings" / "resident-m1"
            live_state = json.loads((meeting_dir / "live_state.json").read_text(encoding="utf-8"))
            self.assertEqual(live_state["debate_rounds"][0]["status"], "answered")
            session_operations = [operation for operation in operations["operations"] if operation["operation"] == "session.start"]
            self.assertEqual(session_operations[-1]["status"], "success")
            self.assertEqual(session_operations[-1]["details"]["auto_rounds_status"], "answered")
            self.assertEqual(session_operations[-1]["details"]["auto_rounds_round_count"], 1)
            operation_blob = json.dumps(session_operations, ensure_ascii=False)
            self.assertNotIn(str(council_config), operation_blob)
            self.assertNotIn(str(agent_config), operation_blob)
            self.assertNotIn(str(live_agent_config), operation_blob)
            self.assertNotIn("auto round reply", operation_blob)


    def test_start_session_fake_three_agents_runs_remaining_rounds_finalizes_and_stop_marks_offline(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "room"
            root.mkdir()
            config_dir = Path(temp_dir) / "configs"
            config_dir.mkdir()
            council_config = config_dir / "council.json"
            agent_config = config_dir / "agents.json"
            live_agent_config = config_dir / "live-agents.json"
            _write_three_agent_fake_session_configs(council_config, agent_config, live_agent_config)
            supervisor = LiveAgentProcessSupervisor(root)
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root, process_supervisor=supervisor))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            meeting_id = "resident-fake-complete"
            group_id = "resident-fake-complete"
            stop_payload = {}
            try:
                start_request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/live-agent-sessions/start",
                    data=json.dumps(
                        {
                            "meeting_id": meeting_id,
                            "group_id": group_id,
                            "council_config_path": str(council_config),
                            "agent_config_path": str(agent_config),
                            "live_agent_config_path": str(live_agent_config),
                            "connect_timeout_seconds": 8,
                            "run_remaining_rounds": True,
                            "round_timeout_seconds": 6,
                            "round_max_rounds": 2,
                            "round_stop_on_timeout": True,
                            "finalize_after_rounds": True,
                        }
                    ).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urlopen(start_request, timeout=60) as response:
                    session_payload = json.loads(response.read().decode("utf-8"))
                stop_request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/live-agent-sessions/stop",
                    data=json.dumps({"meeting_id": meeting_id, "group_id": group_id}).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urlopen(stop_request, timeout=12) as response:
                    stop_payload = json.loads(response.read().decode("utf-8"))
            finally:
                if not stop_payload:
                    try:
                        cleanup_request = Request(
                            f"http://127.0.0.1:{server.server_port}/api/live-agent-sessions/stop",
                            data=json.dumps({"meeting_id": meeting_id, "group_id": group_id}).encode("utf-8"),
                            headers={"Content-Type": "application/json"},
                            method="POST",
                        )
                        with urlopen(cleanup_request, timeout=12) as response:
                            response.read()
                    except Exception:
                        pass
                supervisor.close()
                server.shutdown()
                server.server_close()

            self.assertEqual(session_payload["status"], "ready")
            self.assertEqual(session_payload["connection"]["expected"], 3)
            self.assertEqual(session_payload["connection"]["connected"], 3)
            self.assertEqual(session_payload["auto_rounds"]["status"], "answered")
            self.assertEqual(session_payload["auto_rounds"]["round_count"], 2)
            self.assertEqual(session_payload["auto_rounds"]["answered_round_count"], 2)
            self.assertEqual(session_payload["finalization"]["status"], "finalized")
            self.assertEqual(session_payload["finalization"]["official_event_count"], 6)
            self.assertEqual(session_payload["finalization"]["return_packet_event_count"], 3)

            meeting_dir = root / "meetings" / meeting_id
            for relative_path in ("agenda.md", "transcript.md", "decision.md", "meeting.json"):
                self.assertTrue((meeting_dir / relative_path).exists(), relative_path)
            shared_memory = json.loads((meeting_dir / "shared_memory" / "index.json").read_text(encoding="utf-8"))
            self.assertEqual(shared_memory["official_event_count"], 6)
            for role_id in ("architect", "critic", "operator"):
                self.assertTrue((meeting_dir / "return_packets" / f"{role_id}.md").exists())
                self.assertTrue((meeting_dir / "return_packets" / f"{role_id}.json").exists())
            return_packet_events = [
                event
                for event in read_live_events(meeting_dir, limit=None)
                if event.get("kind") == "artifact" and event.get("artifact_kind") == "return_packet"
            ]
            self.assertEqual({event.get("target_agent_id") for event in return_packet_events}, {"agent-a", "agent-b", "agent-c"})
            self.assertEqual({event.get("official_record") for event in return_packet_events}, {False})

            self.assertEqual(stop_payload["status"], "stopped")
            self.assertEqual(stop_payload["offline"]["expected"], 3)
            self.assertEqual(stop_payload["offline"]["offline"], 3)
            agents = {agent["agent_id"]: agent for agent in read_live_agents(root) if agent.get("meeting_id") == meeting_id}
            self.assertEqual({agents[agent_id]["status"] for agent_id in ("agent-a", "agent-b", "agent-c")}, {"offline"})


    def test_start_session_codex_live_fake_cli_preserves_sessions_through_remaining_rounds(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "room"
            root.mkdir()
            bin_dir = Path(temp_dir) / "bin"
            bin_dir.mkdir()
            fake_codex_log = Path(temp_dir) / "fake-codex.jsonl"
            _write_fake_codex_executable(bin_dir / "codex")
            supervisor = LiveAgentProcessSupervisor(root)
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root, process_supervisor=supervisor))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            meeting_id = "codex-live-fake-complete"
            group_id = "codex-live-fake-complete"
            restart_payload = {}
            stop_payload = {}
            env = {
                "PATH": f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}",
                "AGENTSASSEMBLE_FAKE_CODEX_LOG": str(fake_codex_log),
            }
            try:
                with patch.dict(os.environ, env, clear=False):
                    start_request = Request(
                        f"http://127.0.0.1:{server.server_port}/api/live-agent-sessions/start",
                        data=json.dumps(
                            {
                                "meeting_id": meeting_id,
                                "group_id": group_id,
                                "council_config_path": "configs/demo-council.json",
                                "agent_config_path": "configs/codex-live-session.example.json",
                                "live_agent_config_path": "configs/live-agents.codex-session.example.json",
                                "connect_timeout_seconds": 8,
                                "run_remaining_rounds": True,
                                "round_timeout_seconds": 12,
                                "round_max_rounds": 1,
                                "round_stop_on_timeout": True,
                            }
                        ).encode("utf-8"),
                        headers={"Content-Type": "application/json"},
                        method="POST",
                    )
                    with urlopen(start_request, timeout=80) as response:
                        session_payload = json.loads(response.read().decode("utf-8"))
                    restart_request = Request(
                        f"http://127.0.0.1:{server.server_port}/api/live-agent-sessions/restart",
                        data=json.dumps(
                            {
                                "meeting_id": meeting_id,
                                "group_id": group_id,
                                "connect_timeout_seconds": 8,
                                "run_remaining_rounds": True,
                                "round_timeout_seconds": 12,
                                "round_max_rounds": 2,
                                "round_stop_on_timeout": True,
                                "finalize_after_rounds": True,
                            }
                        ).encode("utf-8"),
                        headers={"Content-Type": "application/json"},
                        method="POST",
                    )
                    with urlopen(restart_request, timeout=80) as response:
                        restart_payload = json.loads(response.read().decode("utf-8"))
                    stop_request = Request(
                        f"http://127.0.0.1:{server.server_port}/api/live-agent-sessions/stop",
                        data=json.dumps({"meeting_id": meeting_id, "group_id": group_id}).encode("utf-8"),
                        headers={"Content-Type": "application/json"},
                        method="POST",
                    )
                    with urlopen(stop_request, timeout=12) as response:
                        stop_payload = json.loads(response.read().decode("utf-8"))
            finally:
                if not stop_payload:
                    try:
                        cleanup_request = Request(
                            f"http://127.0.0.1:{server.server_port}/api/live-agent-sessions/stop",
                            data=json.dumps({"meeting_id": meeting_id, "group_id": group_id}).encode("utf-8"),
                            headers={"Content-Type": "application/json"},
                            method="POST",
                        )
                        with urlopen(cleanup_request, timeout=12) as response:
                            response.read()
                    except Exception:
                        pass
                supervisor.close()
                server.shutdown()
                server.server_close()

            self.assertEqual(session_payload["status"], "ready")
            self.assertEqual(session_payload["connection"]["expected"], 3)
            self.assertEqual(session_payload["connection"]["connected"], 3)
            self.assertEqual(session_payload["auto_rounds"]["status"], "answered")
            self.assertEqual(session_payload["auto_rounds"]["round_count"], 1)
            self.assertEqual(session_payload["auto_rounds"]["answered_round_count"], 1)
            self.assertEqual(restart_payload["status"], "ready")
            self.assertEqual(restart_payload["auto_rounds"]["status"], "answered")
            self.assertEqual(restart_payload["auto_rounds"]["round_count"], 1)
            self.assertEqual(restart_payload["auto_rounds"]["answered_round_count"], 1)
            self.assertEqual(restart_payload["finalization"]["status"], "finalized")
            self.assertEqual(restart_payload["finalization"]["official_event_count"], 6)

            invocations = [
                json.loads(line)
                for line in fake_codex_log.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            run_invocations = [entry for entry in invocations if entry["mode"] in {"fresh", "resume"}]
            self.assertEqual(len(run_invocations), 6)
            self.assertEqual([entry["mode"] for entry in run_invocations].count("fresh"), 3)
            self.assertEqual([entry["mode"] for entry in run_invocations].count("resume"), 3)
            self.assertEqual(
                {entry["session_id"] for entry in run_invocations if entry["mode"] == "resume"},
                {
                    "019e0000-0000-7000-a000-000000000001",
                    "019e0000-0000-7000-a000-000000000002",
                    "019e0000-0000-7000-a000-000000000003",
                },
            )
            self.assertTrue(
                all(entry["sandbox_flags"] == ["--sandbox", "read-only", "--ignore-user-config", "--ignore-rules"] for entry in run_invocations)
            )

            agents = {agent["agent_id"]: agent for agent in read_live_agents(root) if agent.get("meeting_id") == meeting_id}
            self.assertEqual(
                {agents[agent_id]["session_id"] for agent_id in ("codex-live-lore", "codex-live-feats", "codex-live-skeptic")},
                {
                    "019e0000-0000-7000-a000-000000000001",
                    "019e0000-0000-7000-a000-000000000002",
                    "019e0000-0000-7000-a000-000000000003",
                },
            )
            self.assertEqual({agents[agent_id]["status"] for agent_id in agents}, {"offline"})
            self.assertEqual(stop_payload["status"], "stopped")
            self.assertEqual(stop_payload["offline"]["offline"], 3)


    def test_live_agent_session_start_probe_failure_skips_auto_rounds_and_records_safe_operation(self):
        class FakeSessionSupervisor:
            def __init__(self, output_root: Path) -> None:
                self.output_root = output_root

            def start_group(self, **kwargs):
                heartbeat_live_agent(self.output_root, "agent-a", status="online")
                return {
                    "group_id": kwargs.get("group_id") or "resident-main",
                    "status": "running",
                    "agents": [
                        {"agent_id": "agent-a", "display_name": "Agent A", "provider_kind": "local_cli", "connection_kind": "local_cli"},
                    ],
                }

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            council_config = root / "council.json"
            agent_config = root / "agents.json"
            live_agent_config = root / "live-agents.json"
            _write_single_agent_session_configs(council_config, agent_config, live_agent_config)
            supervisor = FakeSessionSupervisor(root)
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root, process_supervisor=supervisor))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                probe_result = {
                    "status": "timeout",
                    "agent_id": "agent-a",
                    "reason": "agent did not reply",
                    "source_event_id": "probe-event-1",
                    "reply": {"message": "probe reply should not be recorded"},
                }
                with (
                    patch("agentsassemble.gui.run_live_agent_probe", return_value=probe_result) as run_probe,
                    patch("agentsassemble.gui.live_agent_turn_rounds_payload") as rounds_payload,
                ):
                    request = Request(
                        f"http://127.0.0.1:{server.server_port}/api/live-agent-sessions/start",
                        data=json.dumps(
                            {
                                "meeting_id": "resident-m1",
                                "group_id": "resident-main",
                                "council_config_path": str(council_config),
                                "agent_config_path": str(agent_config),
                                "live_agent_config_path": str(live_agent_config),
                                "connect_timeout_seconds": 0,
                                "probe_bound_agents": True,
                                "probe_timeout_seconds": 0.5,
                                "run_remaining_rounds": True,
                                "round_timeout_seconds": 2,
                                "round_max_rounds": 1,
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

        run_probe.assert_called_once_with(root, "agent-a", timeout_seconds=0.5)
        rounds_payload.assert_not_called()
        self.assertEqual(session_payload["status"], "ready")
        self.assertEqual(session_payload["reply_probe"]["status"], "failed")
        self.assertEqual(session_payload["reply_probe"]["ok_count"], 0)
        self.assertEqual(session_payload["reply_probe"]["timeout_count"], 1)
        self.assertEqual(session_payload["reply_probe"]["probes"][0]["status"], "timeout")
        self.assertEqual(session_payload["auto_rounds"]["status"], "skipped")
        self.assertEqual(session_payload["auto_rounds"]["reason"], "probe_not_ready")
        session_operations = [operation for operation in operations["operations"] if operation["operation"] == "session.start"]
        self.assertEqual(session_operations[-1]["status"], "degraded")
        self.assertEqual(session_operations[-1]["details"]["reply_probe_status"], "failed")
        self.assertEqual(session_operations[-1]["details"]["reply_probe_statuses"], ["agent-a:timeout"])
        self.assertEqual(session_operations[-1]["details"]["auto_rounds_reason"], "probe_not_ready")
        operation_blob = json.dumps(session_operations, ensure_ascii=False)
        self.assertNotIn(str(council_config), operation_blob)
        self.assertNotIn(str(agent_config), operation_blob)
        self.assertNotIn(str(live_agent_config), operation_blob)
        self.assertNotIn("probe reply should not be recorded", operation_blob)


    def test_session_bound_agent_probe_temporarily_opens_moderator_called_agent_and_restores_mode(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            connect_live_agent_payload(
                root,
                {
                    "agent_id": "agent-a",
                    "display_name": "Agent A",
                    "provider_kind": "local_cli",
                    "connection_kind": "local_cli",
                    "engagement_mode": "moderator_called",
                },
            )
            observed_modes = []

            def fake_probe(output_root: Path, agent_id: str, *, timeout_seconds: float):
                agent = next(item for item in read_live_agents(output_root) if item.get("agent_id") == agent_id)
                observed_modes.append(agent.get("engagement_mode"))
                return {"status": "ok", "agent_id": agent_id, "source_event_id": "probe-event-1", "reply_event_id": "reply-1"}

            with patch("agentsassemble.gui.run_live_agent_probe", side_effect=fake_probe) as run_probe:
                result = _run_session_bound_agent_probe(root, "agent-a", timeout_seconds=0.5)

            agent = next(item for item in read_live_agents(root) if item.get("agent_id") == "agent-a")
            persisted_agent = json.loads((root / "live_agents.json").read_text(encoding="utf-8"))["agents"][0]
            self.assertEqual(result["status"], "ok")
            self.assertEqual(observed_modes, ["human_only"])
            self.assertEqual(agent["engagement_mode"], "moderator_called")
            self.assertNotIn("engagement_mode_updated_at", persisted_agent)
            run_probe.assert_called_once_with(root, "agent-a", timeout_seconds=0.5)


    def test_live_agent_session_start_skips_auto_rounds_until_session_ready(self):
        class SlowSessionSupervisor:
            def start_group(self, **kwargs):
                return {
                    "group_id": kwargs.get("group_id") or "resident-main",
                    "status": "running",
                    "agents": [
                        {"agent_id": "agent-a", "display_name": "Agent A", "provider_kind": "local_cli", "connection_kind": "local_cli"},
                    ],
                }

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            council_config = root / "council.json"
            agent_config = root / "agents.json"
            live_agent_config = root / "live-agents.json"
            council_config.write_text(
                json.dumps(
                    {
                        "topic": "resident session",
                        "question": "Should slow sessions skip auto rounds?",
                        "roles": [
                            {"id": "architect", "display_name": "Architect", "lens": "Architecture", "research_focus": "system"},
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
                            }
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
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
                            }
                        ]
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root, process_supervisor=SlowSessionSupervisor()))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/live-agent-sessions/start",
                    data=json.dumps(
                        {
                            "meeting_id": "resident-m1",
                            "group_id": "resident-main",
                            "council_config_path": str(council_config),
                            "agent_config_path": str(agent_config),
                            "live_agent_config_path": str(live_agent_config),
                            "connect_timeout_seconds": 0,
                            "run_remaining_rounds": True,
                            "round_timeout_seconds": 0,
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

            meeting_dir = root / "meetings" / "resident-m1"
            self.assertEqual(session_payload["status"], "starting")
            self.assertEqual(session_payload["auto_rounds"]["status"], "skipped")
            self.assertEqual(session_payload["auto_rounds"]["reason"], "session_not_ready")
            request_events = [event for event in read_live_events(meeting_dir, limit=None) if event.get("kind") == "live_agent_turn_request"]
            self.assertEqual(request_events, [])
            session_operations = [operation for operation in operations["operations"] if operation["operation"] == "session.start"]
            self.assertEqual(session_operations[-1]["status"], "degraded")
            self.assertEqual(session_operations[-1]["details"]["auto_rounds_status"], "skipped")


    def test_live_agent_session_start_redacts_config_load_paths_from_error_response(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            live_agent_config = root / "live-agents.json"
            private_council_config = root / "private-council.json"
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
                            }
                        ]
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            supervisor = LiveAgentProcessSupervisor(root, command_factory=lambda *args, **kwargs: None)
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root, process_supervisor=supervisor))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/live-agent-sessions/start",
                    data=json.dumps(
                        {
                            "meeting_id": "resident-m1",
                            "council_config_path": str(private_council_config),
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
            finally:
                server.shutdown()
                server.server_close()

            self.assertNotIn(str(private_council_config), body)
            self.assertNotIn("private-council", body)
            self.assertIn("details redacted", body)
            self.assertNotIn("meeting_id", error_payload)
            self.assertEqual(error_payload["details"]["requested_meeting_id"], "resident-m1")


    def test_live_agent_session_start_group_failure_returns_created_meeting_for_recovery(self):
        class FailingSessionSupervisor:
            def start_group(self, **kwargs):
                raise RuntimeError("process launch refused")

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            council_config = root / "council.json"
            agent_config = root / "agents.json"
            live_agent_config = root / "live-agents.json"
            council_config.write_text(
                json.dumps(
                    {
                        "topic": "resident session",
                        "question": "Can a failed process start leave recoverable meeting evidence?",
                        "roles": [
                            {"id": "architect", "display_name": "Architect", "lens": "Architecture", "research_focus": "system"}
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            agent_config.write_text(
                json.dumps(
                    {
                        "providers": [
                            {
                                "id": "local-cli",
                                "kind": "local_cli",
                                "display_name": "Local CLI",
                                "command": [sys.executable, "-c", "print('ok')"],
                            }
                        ],
                        "permission_profiles": [
                            {"id": "meeting_readonly", "meeting_read": True, "lobby_chat": True, "official_turn": True}
                        ],
                        "agent_bindings": [
                            {
                                "agent_id": "agent-a",
                                "role_id": "architect",
                                "provider_id": "local-cli",
                                "permission_profile_id": "meeting_readonly",
                            }
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
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
                            }
                        ]
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root, process_supervisor=FailingSessionSupervisor()))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/live-agent-sessions/start",
                    data=json.dumps(
                        {
                            "group_id": "resident-main",
                            "council_config_path": str(council_config),
                            "agent_config_path": str(agent_config),
                            "live_agent_config_path": str(live_agent_config),
                            "connect_timeout_seconds": 0,
                        }
                    ).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with self.assertRaises(HTTPError) as raised:
                    urlopen(request, timeout=4)
                error_payload = json.loads(raised.exception.read().decode("utf-8"))
                raised.exception.close()
                with urlopen(f"http://127.0.0.1:{server.server_port}/api/live-agent-operations?limit=20", timeout=4) as response:
                    operations = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

            meeting_id = error_payload["meeting_id"]
            self.assertEqual(raised.exception.code, 400)
            self.assertTrue(meeting_id)
            self.assertEqual(error_payload["details"]["meeting_id"], meeting_id)
            self.assertEqual(error_payload["details"]["recoverable_meeting_id"], meeting_id)
            self.assertTrue((root / "meetings" / meeting_id / "live_state.json").exists())
            session_operations = [operation for operation in operations["operations"] if operation["operation"] == "session.start"]
            self.assertEqual(session_operations[-1]["status"], "failed")
            self.assertEqual(session_operations[-1]["target_id"], meeting_id)
            self.assertEqual(session_operations[-1]["details"]["meeting_id"], meeting_id)


    def test_live_agent_session_resume_existing_meeting_records_safe_operation(self):
        class RunningSessionSupervisor:
            def __init__(self) -> None:
                self.started = []

            def list_groups(self):
                return [
                    {
                        "group_id": "resident-main",
                        "status": "running",
                        "agents": [{"agent_id": "agent-a", "provider_kind": "local_cli", "connection_kind": "local_cli"}],
                    }
                ]

            def start_group(self, **kwargs):
                self.started.append(kwargs)
                raise AssertionError("resume should reuse the already running group")

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
            supervisor = RunningSessionSupervisor()
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root, process_supervisor=supervisor))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/live-agent-sessions/resume",
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
            self.assertEqual(session_payload["meeting_id"], "resident-m1")
            self.assertEqual(session_payload["group_id"], "resident-main")
            self.assertEqual(session_payload["connection"]["connected"], 1)
            self.assertEqual(supervisor.started, [])
            session_operations = [operation for operation in operations["operations"] if operation["operation"] == "session.resume"]
            self.assertEqual(session_operations[-1]["status"], "success")
            self.assertEqual(session_operations[-1]["target_id"], "resident-m1")
            self.assertEqual(session_operations[-1]["details"]["result_status"], "ready")
            operation_blob = json.dumps(session_operations, ensure_ascii=False)
            self.assertNotIn(str(live_agent_config), operation_blob)


    def test_live_agent_session_resume_skips_auto_rounds_until_session_ready(self):
        class StartingSessionSupervisor:
            def list_groups(self):
                return []

            def start_group(self, **kwargs):
                return {
                    "group_id": kwargs.get("group_id") or "resident-main",
                    "status": "running",
                    "agents": [{"agent_id": "agent-a", "provider_kind": "local_cli", "connection_kind": "local_cli"}],
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
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root, process_supervisor=StartingSessionSupervisor()))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/live-agent-sessions/resume",
                    data=json.dumps(
                        {
                            "meeting_id": "resident-m1",
                            "group_id": "resident-main",
                            "live_agent_config_path": str(live_agent_config),
                            "connect_timeout_seconds": 0,
                            "run_remaining_rounds": True,
                            "round_timeout_seconds": 0,
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

            self.assertEqual(session_payload["status"], "starting")
            self.assertEqual(session_payload["auto_rounds"]["status"], "skipped")
            self.assertEqual(session_payload["auto_rounds"]["reason"], "session_not_ready")
            request_events = [
                event
                for event in read_live_events(root / "meetings" / "resident-m1", limit=None)
                if event.get("kind") == "live_agent_turn_request"
            ]
            self.assertEqual(request_events, [])
            session_operations = [operation for operation in operations["operations"] if operation["operation"] == "session.resume"]
            self.assertEqual(session_operations[-1]["status"], "degraded")
            self.assertEqual(session_operations[-1]["details"]["auto_rounds_status"], "skipped")


    def test_live_agent_session_resume_missing_meeting_returns_safe_error(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            live_agent_config = root / "live-agents.json"
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
                            }
                        ]
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root, process_supervisor=LiveAgentProcessSupervisor(root)))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/live-agent-sessions/resume",
                    data=json.dumps(
                        {
                            "meeting_id": "missing-meeting",
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
            finally:
                server.shutdown()
                server.server_close()

            self.assertIn("Meeting missing-meeting was not found", body)
            self.assertNotIn(str(live_agent_config), body)
            self.assertEqual(error_payload["details"]["requested_meeting_id"], "missing-meeting")


    def test_live_agent_session_resume_redacts_command_names_from_error_response(self):
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
                                "command": ["secret-local-agent-cli"],
                            }
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
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root, process_supervisor=LiveAgentProcessSupervisor(root)))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/live-agent-sessions/resume",
                    data=json.dumps(
                        {
                            "meeting_id": "resident-m1",
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
            finally:
                server.shutdown()
                server.server_close()

            self.assertIn("details redacted", body)
            self.assertNotIn("secret-local-agent-cli", body)
            self.assertNotIn(str(live_agent_config), body)
            self.assertEqual(error_payload["details"]["requested_meeting_id"], "resident-m1")


    def test_live_agent_session_stop_marks_agents_offline_and_records_safe_operation(self):
        class StopSessionSupervisor:
            def __init__(self) -> None:
                self.stopped = []

            def stop_group(self, group_id):
                self.stopped.append(group_id)
                return {
                    "group_id": group_id,
                    "status": "stopped",
                    "config_path": "/private/live-agents.json",
                    "log_tail": "secret provider output",
                    "agents": [{"agent_id": "agent-a"}],
                }

            def list_groups(self):
                return [{"group_id": "resident-main", "status": "stopped", "agents": [{"agent_id": "agent-a"}]}]

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
            supervisor = StopSessionSupervisor()
            session_run_controller = LiveAgentSessionRunController(root)
            session_run = session_run_controller.begin_run(
                action="ensure",
                payload={
                    "meeting_id": "resident-m1",
                    "group_id": "resident-main",
                    "live_agent_config_path": str(live_agent_config),
                },
            )
            session_run_controller.finish_run(
                session_run["run_id"],
                session={
                    "status": "ready",
                    "meeting_id": "resident-m1",
                    "group_id": "resident-main",
                    "action": "none",
                },
            )
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root, process_supervisor=supervisor))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/live-agent-sessions/stop",
                    data=json.dumps({"meeting_id": "resident-m1", "group_id": "resident main"}).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urlopen(request, timeout=4) as response:
                    session_payload = json.loads(response.read().decode("utf-8"))
                with urlopen(f"http://127.0.0.1:{server.server_port}/api/live-agent-operations?limit=20", timeout=4) as response:
                    operations = json.loads(response.read().decode("utf-8"))
                with urlopen(f"http://127.0.0.1:{server.server_port}/api/live-agent-session-runs?limit=20", timeout=4) as response:
                    session_runs = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

            self.assertEqual(session_payload["status"], "stopped")
            self.assertEqual(session_payload["group_id"], "resident-main")
            self.assertEqual(session_payload["session_runs"][0]["run_id"], session_run["run_id"])
            self.assertEqual(session_payload["session_runs"][0]["status"], "stopped")
            self.assertFalse(session_runs["runs"][0]["active"])
            self.assertEqual(supervisor.stopped, ["resident-main"])
            agents = {agent["agent_id"]: agent for agent in read_live_agents(root)}
            self.assertEqual(agents["agent-a"]["status"], "offline")
            session_operations = [operation for operation in operations["operations"] if operation["operation"] == "session.stop"]
            self.assertEqual(session_operations[-1]["status"], "success")
            self.assertEqual(session_operations[-1]["target_id"], "resident-m1")
            self.assertEqual(session_operations[-1]["details"]["offline_agent_count"], 1)
            self.assertEqual(session_operations[-1]["details"]["session_run_stopped_count"], 1)
            self.assertEqual(session_operations[-1]["details"]["session_run_ids"], [session_run["run_id"]])
            operation_blob = json.dumps(session_operations, ensure_ascii=False)
            self.assertNotIn("/private/live-agents.json", operation_blob)
            self.assertNotIn("secret provider output", operation_blob)


    def test_live_agent_session_stop_missing_meeting_returns_safe_error(self):
        class StopSessionSupervisor:
            def __init__(self) -> None:
                self.stopped = []

            def stop_group(self, group_id):
                self.stopped.append(group_id)
                return {"group_id": group_id, "status": "stopped", "agents": []}

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            supervisor = StopSessionSupervisor()
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root, process_supervisor=supervisor))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/live-agent-sessions/stop",
                    data=json.dumps({"meeting_id": "missing-meeting", "group_id": "resident-main"}).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with self.assertRaises(HTTPError) as raised:
                    urlopen(request, timeout=4)
                body = raised.exception.read().decode("utf-8")
                error_payload = json.loads(body)
                raised.exception.close()
            finally:
                server.shutdown()
                server.server_close()

            self.assertIn("Meeting missing-meeting was not found", body)
            self.assertEqual(error_payload["details"]["requested_meeting_id"], "missing-meeting")
            self.assertEqual(error_payload["details"]["group_id"], "resident-main")
            self.assertEqual(supervisor.stopped, [])


    def test_live_agent_session_stop_failure_does_not_offline_agents_and_records_safe_operation(self):
        class StopSessionSupervisor:
            def __init__(self) -> None:
                self.stopped = []

            def stop_group(self, group_id):
                self.stopped.append(group_id)
                raise ValueError("provider output: raw model reply should never leak")

            def list_groups(self):
                return [{"group_id": "resident-main", "status": "running", "agents": [{"agent_id": "agent-a"}]}]

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
            supervisor = StopSessionSupervisor()
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root, process_supervisor=supervisor))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/live-agent-sessions/stop",
                    data=json.dumps({"meeting_id": "resident-m1", "group_id": "resident-main"}).encode("utf-8"),
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

            self.assertEqual(supervisor.stopped, ["resident-main"])
            agents = {agent["agent_id"]: agent for agent in read_live_agents(root)}
            self.assertEqual(agents["agent-a"]["status"], "online")
            self.assertIn("details redacted", body)
            self.assertNotIn("raw model reply", body)
            self.assertNotIn("provider output", body)
            session_operations = [operation for operation in operations["operations"] if operation["operation"] == "session.stop"]
            self.assertEqual(session_operations[-1]["status"], "failed")
            self.assertEqual(session_operations[-1]["details"]["meeting_id"], "resident-m1")
            operation_blob = json.dumps(session_operations, ensure_ascii=False)
            self.assertNotIn("raw model reply", operation_blob)
            self.assertNotIn("provider output", operation_blob)


    def test_live_agent_session_check_returns_ready_snapshot_and_records_safe_operation(self):
        class CheckSessionSupervisor:
            def snapshot_groups(self):
                return [
                    {
                        "group_id": "resident-main",
                        "status": "running",
                        "config_path": "/private/live-agents.json",
                        "log_tail": "secret provider output",
                        "agents": [{"agent_id": "agent-a"}],
                    }
                ]

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
            before_roster = (root / "live_agents.json").read_text(encoding="utf-8")
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root, process_supervisor=CheckSessionSupervisor()))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/live-agent-sessions/check",
                    data=json.dumps({"meeting_id": "resident-m1", "group_id": "resident main"}).encode("utf-8"),
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
            self.assertEqual(session_payload["group_id"], "resident-main")
            self.assertEqual(session_payload["connection"]["connected"], 1)
            self.assertEqual((root / "live_agents.json").read_text(encoding="utf-8"), before_roster)
            session_operations = [operation for operation in operations["operations"] if operation["operation"] == "session.check"]
            self.assertEqual(session_operations[-1]["status"], "success")
            self.assertEqual(session_operations[-1]["target_id"], "resident-m1")
            self.assertEqual(session_operations[-1]["details"]["connected_agent_count"], 1)
            operation_blob = json.dumps(session_operations, ensure_ascii=False)
            self.assertNotIn("/private/live-agents.json", operation_blob)
            self.assertNotIn("secret provider output", operation_blob)


    def test_live_agent_session_check_missing_meeting_returns_safe_error(self):
        class CheckSessionSupervisor:
            def list_groups(self):
                return [{"group_id": "resident-main", "status": "running", "agents": [{"agent_id": "agent-a"}]}]

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root, process_supervisor=CheckSessionSupervisor()))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/live-agent-sessions/check",
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
            session_operations = [operation for operation in operations["operations"] if operation["operation"] == "session.check"]
            self.assertEqual(session_operations[-1]["status"], "failed")


    def test_live_agent_session_readiness_endpoint_returns_ready_snapshot_without_operation_record(self):
        class ReadinessSessionSupervisor:
            def snapshot_groups(self):
                return [
                    {
                        "group_id": "resident-main",
                        "status": "running",
                        "config_path": "/private/live-agents.json",
                        "log_tail": "secret provider output",
                        "agents": [{"agent_id": "agent-a"}],
                    }
                ]

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
            before_roster = (root / "live_agents.json").read_text(encoding="utf-8")
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root, process_supervisor=ReadinessSessionSupervisor()))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with urlopen(
                    f"http://127.0.0.1:{server.server_port}/api/live-agent-sessions/readiness?meeting_id=resident-m1&group_id=resident%20main",
                    timeout=4,
                ) as response:
                    session_payload = json.loads(response.read().decode("utf-8"))
                with urlopen(f"http://127.0.0.1:{server.server_port}/api/live-agent-operations?limit=20", timeout=4) as response:
                    operations = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

            self.assertEqual(session_payload["status"], "ready")
            self.assertEqual(session_payload["group_id"], "resident-main")
            self.assertEqual(session_payload["connection"]["connected"], 1)
            self.assertEqual((root / "live_agents.json").read_text(encoding="utf-8"), before_roster)
            self.assertEqual(operations["operations"], [])
            payload_blob = json.dumps(session_payload, ensure_ascii=False)
            self.assertNotIn("/private/live-agents.json", payload_blob)
            self.assertNotIn("secret provider output", payload_blob)


    def test_live_agent_session_readiness_degrades_when_binding_provider_is_missing(self):
        class ReadinessSessionSupervisor:
            def snapshot_groups(self):
                return [
                    {
                        "group_id": "resident-main",
                        "status": "running",
                        "meeting_id": "resident-m1",
                        "agents": [
                            {
                                "agent_id": "agent-a",
                                "provider_kind": "local_cli",
                                "connection_kind": "local_cli",
                            }
                        ],
                    }
                ]

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            meeting_dir = root / "meetings" / "resident-m1"
            meeting_dir.mkdir(parents=True)
            write_live_state(
                meeting_dir,
                {
                    "meeting_id": "resident-m1",
                    "agent_bindings": [
                        {
                            "role_id": "architect",
                            "agent_id": "agent-a",
                            "provider_id": "missing-provider",
                        }
                    ],
                    "provider_configs": {},
                },
            )
            connect_live_agent(
                root,
                {
                    "agent_id": "agent-a",
                    "display_name": "Agent A",
                    "provider_kind": "local_cli",
                    "connection_kind": "local_cli",
                    "meeting_id": "resident-m1",
                },
            )
            server = ThreadingHTTPServer(
                ("127.0.0.1", 0),
                _make_handler(root, process_supervisor=ReadinessSessionSupervisor()),
            )
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with urlopen(
                    f"http://127.0.0.1:{server.server_port}/api/live-agent-sessions/readiness?meeting_id=resident-m1&group_id=resident-main",
                    timeout=4,
                ) as response:
                    session_payload = json.loads(response.read().decode("utf-8"))
                with urlopen(f"http://127.0.0.1:{server.server_port}/api/live-agent-operations?limit=20", timeout=4) as response:
                    operations = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

        self.assertEqual(session_payload["status"], "degraded")
        self.assertEqual(session_payload["connection"]["connected"], 0)
        self.assertEqual(session_payload["connection"]["attention"], ["agent-a:binding_provider_missing"])
        self.assertEqual(operations["operations"], [])
        payload_blob = json.dumps(session_payload, ensure_ascii=False)
        self.assertNotIn("missing-provider", payload_blob)


    def test_live_agent_session_readiness_endpoint_returns_degraded_missing_group_without_operation_record(self):
        class ReadinessSessionSupervisor:
            def snapshot_groups(self):
                return []

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
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root, process_supervisor=ReadinessSessionSupervisor()))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with urlopen(
                    f"http://127.0.0.1:{server.server_port}/api/live-agent-sessions/readiness?meeting_id=resident-m1&group_id=resident-main",
                    timeout=4,
                ) as response:
                    session_payload = json.loads(response.read().decode("utf-8"))
                with urlopen(f"http://127.0.0.1:{server.server_port}/api/live-agent-operations?limit=20", timeout=4) as response:
                    operations = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

            self.assertEqual(session_payload["status"], "degraded")
            self.assertEqual(session_payload["group_id"], "resident-main")
            self.assertIn("group:unknown", session_payload["process"]["attention"])
            self.assertIn("agent-a:not_in_group", session_payload["process"]["attention"])
            self.assertEqual(operations["operations"], [])


    def test_live_agent_session_readiness_endpoint_includes_safe_process_reason(self):
        class ReadinessSessionSupervisor:
            def snapshot_groups(self):
                return [
                    {
                        "group_id": "resident-main",
                        "status": "unknown",
                        "meeting_id": "resident-m1",
                        "agents": [{"agent_id": "agent-a"}],
                        "recent_events": [{"event_type": "recovered_unknown", "status": "unknown"}],
                    }
                ]

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
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root, process_supervisor=ReadinessSessionSupervisor()))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with urlopen(
                    f"http://127.0.0.1:{server.server_port}/api/live-agent-sessions/readiness?meeting_id=resident-m1&group_id=resident-main",
                    timeout=4,
                ) as response:
                    session_payload = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

        self.assertEqual(session_payload["status"], "degraded")
        self.assertEqual(
            session_payload["process_reason"],
            {
                "event_type": "recovered_unknown",
                "reason": "orphan running record marked unknown",
            },
        )


    def test_live_agent_session_readiness_process_reason_uses_same_process_snapshot(self):
        class ChangingReadinessSessionSupervisor:
            def __init__(self):
                self.calls = 0

            def snapshot_groups(self):
                self.calls += 1
                if self.calls == 1:
                    return [
                        {
                            "group_id": "resident-main",
                            "status": "unknown",
                            "meeting_id": "resident-m1",
                            "agents": [{"agent_id": "agent-a"}],
                            "recent_events": [{"event_type": "recovered_unknown", "status": "unknown"}],
                        }
                    ]
                return [
                    {
                        "group_id": "resident-main",
                        "status": "running",
                        "meeting_id": "resident-m1",
                        "agents": [{"agent_id": "agent-a"}],
                    }
                ]

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
            supervisor = ChangingReadinessSessionSupervisor()
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root, process_supervisor=supervisor))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with urlopen(
                    f"http://127.0.0.1:{server.server_port}/api/live-agent-sessions/readiness?meeting_id=resident-m1&group_id=resident-main",
                    timeout=4,
                ) as response:
                    session_payload = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

        self.assertEqual(supervisor.calls, 1)
        self.assertEqual(session_payload["process"]["status"], "unknown")
        self.assertEqual(
            session_payload["process_reason"],
            {
                "event_type": "recovered_unknown",
                "reason": "orphan running record marked unknown",
            },
        )
