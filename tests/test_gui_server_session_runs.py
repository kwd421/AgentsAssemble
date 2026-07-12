from tests.gui_server_test_support import (
    HTTPError,
    LiveAgentSessionRunController,
    Path,
    Request,
    ThreadingHTTPServer,
    _make_handler,
    _write_health_resident_meeting,
    _write_single_agent_session_configs,
    heartbeat_live_agent,
    json,
    patch,
    start_live_agent_meeting,
    tempfile,
    threading,
    unittest,
    urlopen,
)


class GuiServerSessionLifecycleTests(unittest.TestCase):

    def test_live_agent_session_ensure_returns_ready_without_mutating_ready_session(self):
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
                raise AssertionError("ready ensure must not start a group")

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
                            "meeting_id": "resident-m1",
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
            self.assertEqual(session_payload["action"], "none")
            self.assertEqual(supervisor.started, [])
            session_operations = [operation for operation in operations["operations"] if operation["operation"].startswith("session.")]
            self.assertEqual([operation["operation"] for operation in session_operations], ["session.ensure"])
            self.assertEqual(session_operations[-1]["status"], "success")
            self.assertEqual(session_operations[-1]["details"]["ensure_action"], "none")


    def test_live_agent_session_ensure_records_durable_session_run_status(self):
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
                raise AssertionError("ready ensure must not start a group")

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
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root, process_supervisor=EnsureSessionSupervisor()))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/live-agent-session-runs/ensure",
                    data=json.dumps(
                        {
                            "meeting_id": "resident-m1",
                            "group_id": "resident-main",
                            "live_agent_config_path": str(live_agent_config),
                            "probe_bound_agents": True,
                        }
                    ).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with patch(
                    "agentsassemble.gui.run_live_agent_probe",
                    return_value={"status": "ok", "agent_id": "agent-a", "source_event_id": "probe-1", "reply_event_id": "reply-1"},
                ):
                    with urlopen(request, timeout=4) as response:
                        session_payload = json.loads(response.read().decode("utf-8"))
                with urlopen(f"http://127.0.0.1:{server.server_port}/api/live-agent-session-runs?limit=20", timeout=4) as response:
                    runs_payload = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

        self.assertEqual(session_payload["status"], "ready")
        self.assertIn("session_run", session_payload)
        self.assertEqual(session_payload["session_run"]["status"], "ready")
        run_id = session_payload["session_run"]["run_id"]
        self.assertEqual(runs_payload["runs"][0]["run_id"], run_id)
        self.assertEqual(runs_payload["runs"][0]["action"], "ensure")
        self.assertEqual(runs_payload["runs"][0]["status"], "ready")
        self.assertTrue(runs_payload["runs"][0]["active"])
        self.assertEqual(runs_payload["runs"][0]["meeting_id"], "resident-m1")
        self.assertEqual(runs_payload["runs"][0]["group_id"], "resident-main")
        self.assertEqual(runs_payload["runs"][0]["result"]["reply_probe"]["status"], "ok")
        self.assertNotIn(str(live_agent_config), str(runs_payload))


    def test_live_agent_session_run_ensure_api_requires_current_approval_for_real_provider_config(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            live_agent_config = root / "live-agents.real.json"
            live_agent_config.write_text(
                json.dumps(
                    {
                        "agents": [
                            {
                                "agent_id": "claude-live",
                                "provider_kind": "claude_code",
                                "connection_kind": "terminal_session",
                                "command": ["claude"],
                            }
                        ]
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/live-agent-session-runs/ensure",
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
                with patch("agentsassemble.gui.live_agent_session_ensure_payload") as ensure_payload:
                    ensure_payload.side_effect = AssertionError("approval gate must stop before durable ensure")
                    with self.assertRaises(HTTPError) as raised:
                        urlopen(request, timeout=4)
                body = raised.exception.read().decode("utf-8")
                error_payload = json.loads(body)
                raised.exception.close()
                with urlopen(f"http://127.0.0.1:{server.server_port}/api/live-agent-session-runs?limit=20", timeout=4) as response:
                    runs_payload = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

        self.assertEqual(raised.exception.code, 400)
        self.assertIn("requires current operator approval", error_payload["error"])
        self.assertEqual(runs_payload["runs"][0]["status"], "failed")
        self.assertIn("requires current operator approval", runs_payload["runs"][0]["last_error"])
        self.assertNotIn(str(live_agent_config), body)
        self.assertNotIn(str(live_agent_config), json.dumps(runs_payload, ensure_ascii=False))


    def test_live_agent_session_run_ensure_api_uses_current_real_provider_approval_without_persisting_it(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            live_agent_config = root / "live-agents.real.json"
            live_agent_config.write_text(
                json.dumps(
                    {
                        "agents": [
                            {
                                "agent_id": "claude-live",
                                "provider_kind": "claude_code",
                                "connection_kind": "terminal_session",
                                "command": ["claude"],
                            }
                        ]
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()

            def approved_ensure(output_root, process_supervisor, payload, *, default_server):
                del output_root, process_supervisor, default_server
                self.assertEqual(payload["approve_real_providers"], True)
                return {
                    "status": "ready",
                    "meeting_id": payload["meeting_id"],
                    "group_id": payload["group_id"],
                    "action": "start",
                }

            try:
                request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/live-agent-session-runs/ensure",
                    data=json.dumps(
                        {
                            "meeting_id": "resident-m1",
                            "group_id": "resident-main",
                            "live_agent_config_path": str(live_agent_config),
                            "approve_real_providers": True,
                        }
                    ).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with patch("agentsassemble.gui.live_agent_session_ensure_payload", side_effect=approved_ensure):
                    with urlopen(request, timeout=4) as response:
                        session_payload = json.loads(response.read().decode("utf-8"))
                with urlopen(f"http://127.0.0.1:{server.server_port}/api/live-agent-session-runs?limit=20", timeout=4) as response:
                    runs_payload = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

        self.assertEqual(session_payload["status"], "ready")
        self.assertEqual(runs_payload["runs"][0]["status"], "ready")
        self.assertNotIn("approve_real_providers", json.dumps(runs_payload, ensure_ascii=False))
        self.assertNotIn(str(live_agent_config), json.dumps(runs_payload, ensure_ascii=False))


    def test_live_agent_session_runs_api_filters_meeting_group_before_limit(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            session_run_controller = LiveAgentSessionRunController(root)
            target = session_run_controller.begin_run(
                action="ensure",
                payload={"meeting_id": "resident-m1", "group_id": "resident-main"},
            )
            session_run_controller.finish_run(
                target["run_id"],
                session={
                    "status": "running",
                    "meeting_id": "resident-m1",
                    "group_id": "resident-main",
                    "action": "start",
                },
            )
            unrelated = session_run_controller.begin_run(
                action="ensure",
                payload={"meeting_id": "resident-m2", "group_id": "resident-alt"},
            )
            session_run_controller.finish_run(
                unrelated["run_id"],
                session={
                    "status": "ready",
                    "meeting_id": "resident-m2",
                    "group_id": "resident-alt",
                    "action": "none",
                },
            )
            server = ThreadingHTTPServer(
                ("127.0.0.1", 0),
                _make_handler(root, session_run_controller=session_run_controller),
            )
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with urlopen(
                    f"http://127.0.0.1:{server.server_port}/api/live-agent-session-runs"
                    "?limit=1&meeting_id=resident-m1&group_id=resident-main",
                    timeout=4,
                ) as response:
                    runs_payload = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

        self.assertEqual([run["run_id"] for run in runs_payload["runs"]], [target["run_id"]])
        self.assertEqual(runs_payload["runs"][0]["meeting_id"], "resident-m1")
        self.assertEqual(runs_payload["runs"][0]["group_id"], "resident-main")


    def test_live_agent_session_runs_api_filters_run_id_before_limit_and_overlays_readiness(self):
        class ReadinessSessionSupervisor:
            def snapshot_groups(self):
                return [
                    {
                        "group_id": "resident-main",
                        "status": "stopped",
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
            session_run_controller = LiveAgentSessionRunController(root)
            target = session_run_controller.begin_run(
                action="ensure",
                payload={"meeting_id": "resident-m1", "group_id": "resident-main"},
            )
            session_run_controller.finish_run(
                target["run_id"],
                session={
                    "status": "ready",
                    "meeting_id": "resident-m1",
                    "group_id": "resident-main",
                    "action": "none",
                },
            )
            unrelated = session_run_controller.begin_run(
                action="ensure",
                payload={"meeting_id": "resident-m2", "group_id": "resident-alt"},
            )
            session_run_controller.finish_run(
                unrelated["run_id"],
                session={
                    "status": "running",
                    "meeting_id": "resident-m2",
                    "group_id": "resident-alt",
                    "action": "start",
                },
            )
            server = ThreadingHTTPServer(
                ("127.0.0.1", 0),
                _make_handler(
                    root,
                    process_supervisor=ReadinessSessionSupervisor(),
                    session_run_controller=session_run_controller,
                ),
            )
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with urlopen(
                    f"http://127.0.0.1:{server.server_port}/api/live-agent-session-runs"
                    f"?limit=1&run_id={target['run_id']}&include_readiness=1",
                    timeout=4,
                ) as response:
                    runs_payload = json.loads(response.read().decode("utf-8"))
                with urlopen(
                    f"http://127.0.0.1:{server.server_port}/api/live-agent-operations?limit=20",
                    timeout=4,
                ) as response:
                    operations_payload = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

        run = runs_payload["runs"][0]
        self.assertEqual(run["run_id"], target["run_id"])
        self.assertEqual(run["readiness"]["status"], "degraded")
        self.assertEqual(run["readiness"]["process_status"], "stopped")
        self.assertEqual(operations_payload["operations"], [])
        self.assertNotIn(str(live_agent_config), str(runs_payload))


    def test_live_agent_session_run_retry_now_api_clears_backoff_and_records_operation(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            session_run_controller = LiveAgentSessionRunController(root)
            target = session_run_controller.begin_run(
                action="ensure",
                payload={"meeting_id": "resident-m1", "group_id": "resident-main"},
            )
            session_run_controller.finish_run(
                target["run_id"],
                session={
                    "status": "degraded",
                    "meeting_id": "resident-m1",
                    "group_id": "resident-main",
                    "action": "recover",
                },
            )
            server = ThreadingHTTPServer(
                ("127.0.0.1", 0),
                _make_handler(root, session_run_controller=session_run_controller),
            )
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/live-agent-session-runs/{target['run_id']}/retry-now",
                    data=b"{}",
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with patch(
                    "agentsassemble.gui.live_agent_session_ensure_payload",
                    return_value={
                        "status": "ready",
                        "meeting_id": "resident-m1",
                        "group_id": "resident-main",
                        "action": "recover",
                    },
                ) as ensure_payload:
                    with urlopen(request, timeout=4) as response:
                        retry_payload = json.loads(response.read().decode("utf-8"))
                with urlopen(f"http://127.0.0.1:{server.server_port}/api/live-agent-session-runs?limit=20", timeout=4) as response:
                    runs_payload = json.loads(response.read().decode("utf-8"))
                with urlopen(f"http://127.0.0.1:{server.server_port}/api/live-agent-operations?limit=20", timeout=4) as response:
                    operations_payload = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

        self.assertEqual(retry_payload["status"], "reconciled")
        self.assertEqual(retry_payload["session_run"]["run_id"], target["run_id"])
        self.assertEqual(retry_payload["session_run"]["phase"], "recover")
        self.assertEqual(retry_payload["session_run"]["next_reconcile_at"], "")
        self.assertEqual(retry_payload["session_run"]["reconcile_backoff_seconds"], 0)
        self.assertEqual(retry_payload["results"][0]["status"], "ready")
        ensure_payload.assert_called_once()
        self.assertEqual(runs_payload["runs"][0]["phase"], "recover")
        self.assertEqual(runs_payload["runs"][0]["status"], "ready")
        self.assertEqual(operations_payload["operations"][-1]["operation"], "session_run.retry_now")
        self.assertEqual(operations_payload["operations"][-1]["target_id"], target["run_id"])


    def test_live_agent_session_run_retry_now_api_uses_current_real_provider_approval_without_persisting_it(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            live_agent_config = root / "live-agents.real.json"
            live_agent_config.write_text(
                json.dumps(
                    {
                        "agents": [
                            {
                                "agent_id": "claude-live",
                                "display_name": "Claude",
                                "provider_kind": "claude_code",
                                "connection_kind": "terminal_session",
                                "command": ["claude"],
                            }
                        ]
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            session_run_controller = LiveAgentSessionRunController(root)
            target = session_run_controller.begin_run(
                action="ensure",
                payload={
                    "meeting_id": "resident-m1",
                    "group_id": "resident-main",
                    "live_agent_config_path": str(live_agent_config),
                    "server": "http://room.local",
                },
            )
            session_run_controller.finish_run(
                target["run_id"],
                session={
                    "status": "degraded",
                    "meeting_id": "resident-m1",
                    "group_id": "resident-main",
                    "action": "recover",
                },
            )
            server = ThreadingHTTPServer(
                ("127.0.0.1", 0),
                _make_handler(root, session_run_controller=session_run_controller),
            )
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            seen_payloads = []

            def approved_ensure(output_root, process_supervisor, payload, *, default_server):
                del output_root, process_supervisor, default_server
                seen_payloads.append(dict(payload))
                return {
                    "status": "ready",
                    "meeting_id": payload["meeting_id"],
                    "group_id": payload["group_id"],
                    "action": "recover",
                }

            try:
                request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/live-agent-session-runs/{target['run_id']}/retry-now",
                    data=json.dumps({"approve_real_providers": True}).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with patch("agentsassemble.gui.live_agent_session_ensure_payload", side_effect=approved_ensure):
                    with urlopen(request, timeout=4) as response:
                        retry_payload = json.loads(response.read().decode("utf-8"))
                with urlopen(f"http://127.0.0.1:{server.server_port}/api/live-agent-session-runs?limit=20", timeout=4) as response:
                    runs_payload = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

        self.assertEqual(retry_payload["status"], "reconciled")
        self.assertEqual(retry_payload["session_run"]["status"], "ready")
        self.assertEqual(seen_payloads[0]["approve_real_providers"], True)
        self.assertNotIn("approve_real_providers", json.dumps(runs_payload, ensure_ascii=False))
        self.assertNotIn(str(live_agent_config), json.dumps(runs_payload, ensure_ascii=False))


    def test_live_agent_session_run_retry_now_api_rejects_string_false_real_provider_approval(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            live_agent_config = root / "live-agents.real.json"
            live_agent_config.write_text(
                json.dumps(
                    {
                        "agents": [
                            {
                                "agent_id": "claude-live",
                                "provider_kind": "claude_code",
                                "connection_kind": "terminal_session",
                                "command": ["claude"],
                            }
                        ]
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            session_run_controller = LiveAgentSessionRunController(root)
            target = session_run_controller.begin_run(
                action="ensure",
                payload={
                    "meeting_id": "resident-m1",
                    "group_id": "resident-main",
                    "live_agent_config_path": str(live_agent_config),
                    "server": "http://room.local",
                },
            )
            session_run_controller.finish_run(
                target["run_id"],
                session={
                    "status": "degraded",
                    "meeting_id": "resident-m1",
                    "group_id": "resident-main",
                    "action": "recover",
                },
            )
            server = ThreadingHTTPServer(
                ("127.0.0.1", 0),
                _make_handler(root, session_run_controller=session_run_controller),
            )
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/live-agent-session-runs/{target['run_id']}/retry-now",
                    data=json.dumps({"approve_real_providers": "false"}).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with patch("agentsassemble.gui.live_agent_session_ensure_payload") as ensure_payload:
                    ensure_payload.side_effect = AssertionError("string false approval must not relaunch real providers")
                    with urlopen(request, timeout=4) as response:
                        retry_payload = json.loads(response.read().decode("utf-8"))
                with urlopen(f"http://127.0.0.1:{server.server_port}/api/live-agent-session-runs?limit=20", timeout=4) as response:
                    runs_payload = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

        self.assertEqual(retry_payload["status"], "reconciled")
        self.assertEqual(retry_payload["session_run"]["status"], "degraded")
        self.assertIn("requires current operator approval", runs_payload["runs"][0]["last_error"])
        self.assertNotIn(str(live_agent_config), json.dumps(runs_payload, ensure_ascii=False))


    def test_live_agent_session_run_retry_now_api_resolves_latest_matching_meeting_group(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            session_run_controller = LiveAgentSessionRunController(root)
            older_matching = session_run_controller.begin_run(
                action="ensure",
                payload={"meeting_id": "resident-m1", "group_id": "resident-main"},
            )
            session_run_controller.finish_run(
                older_matching["run_id"],
                session={
                    "status": "ready",
                    "meeting_id": "resident-m1",
                    "group_id": "resident-main",
                    "action": "none",
                },
            )
            target = session_run_controller.begin_run(
                action="ensure",
                payload={"meeting_id": "resident-m1", "group_id": "resident-main"},
            )
            session_run_controller.finish_run(
                target["run_id"],
                session={
                    "status": "degraded",
                    "meeting_id": "resident-m1",
                    "group_id": "resident-main",
                    "action": "recover",
                },
            )
            unrelated = session_run_controller.begin_run(
                action="ensure",
                payload={"meeting_id": "resident-m2", "group_id": "resident-alt"},
            )
            session_run_controller.finish_run(
                unrelated["run_id"],
                session={
                    "status": "degraded",
                    "meeting_id": "resident-m2",
                    "group_id": "resident-alt",
                    "action": "recover",
                },
            )
            server = ThreadingHTTPServer(
                ("127.0.0.1", 0),
                _make_handler(root, session_run_controller=session_run_controller),
            )
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/live-agent-session-runs/retry-now",
                    data=json.dumps({"meeting_id": "resident-m1", "group_id": "resident-main"}).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with patch(
                    "agentsassemble.gui.live_agent_session_ensure_payload",
                    return_value={
                        "status": "ready",
                        "meeting_id": "resident-m1",
                        "group_id": "resident-main",
                        "action": "recover",
                    },
                ) as ensure_payload:
                    with urlopen(request, timeout=4) as response:
                        retry_payload = json.loads(response.read().decode("utf-8"))
                with urlopen(f"http://127.0.0.1:{server.server_port}/api/live-agent-operations?limit=20", timeout=4) as response:
                    operations_payload = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

        self.assertEqual(retry_payload["status"], "reconciled")
        self.assertEqual(retry_payload["session_run"]["run_id"], target["run_id"])
        self.assertEqual(retry_payload["session_run"]["status"], "ready")
        ensure_payload.assert_called_once()
        self.assertEqual(operations_payload["operations"][-1]["operation"], "session_run.retry_now")
        self.assertEqual(operations_payload["operations"][-1]["target_id"], target["run_id"])
        self.assertEqual(operations_payload["operations"][-1]["details"]["meeting_id"], "resident-m1")
        self.assertEqual(operations_payload["operations"][-1]["details"]["group_id"], "resident-main")


    def test_live_agent_session_run_retry_now_api_run_id_takes_precedence_over_meeting_group(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            session_run_controller = LiveAgentSessionRunController(root)
            exact = session_run_controller.begin_run(
                action="ensure",
                payload={"meeting_id": "resident-m2", "group_id": "resident-alt"},
            )
            session_run_controller.finish_run(
                exact["run_id"],
                session={
                    "status": "degraded",
                    "meeting_id": "resident-m2",
                    "group_id": "resident-alt",
                    "action": "recover",
                },
            )
            matching = session_run_controller.begin_run(
                action="ensure",
                payload={"meeting_id": "resident-m1", "group_id": "resident-main"},
            )
            session_run_controller.finish_run(
                matching["run_id"],
                session={
                    "status": "degraded",
                    "meeting_id": "resident-m1",
                    "group_id": "resident-main",
                    "action": "recover",
                },
            )
            server = ThreadingHTTPServer(
                ("127.0.0.1", 0),
                _make_handler(root, session_run_controller=session_run_controller),
            )
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/live-agent-session-runs/retry-now",
                    data=json.dumps(
                        {
                            "run_id": exact["run_id"],
                            "meeting_id": "resident-m1",
                            "group_id": "resident-main",
                        }
                    ).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with patch(
                    "agentsassemble.gui.live_agent_session_ensure_payload",
                    return_value={
                        "status": "ready",
                        "meeting_id": "resident-m2",
                        "group_id": "resident-alt",
                        "action": "recover",
                    },
                ):
                    with urlopen(request, timeout=4) as response:
                        retry_payload = json.loads(response.read().decode("utf-8"))
                with urlopen(f"http://127.0.0.1:{server.server_port}/api/live-agent-operations?limit=20", timeout=4) as response:
                    operations_payload = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

        self.assertEqual(retry_payload["status"], "reconciled")
        self.assertEqual(retry_payload["session_run"]["run_id"], exact["run_id"])
        self.assertEqual(retry_payload["session_run"]["meeting_id"], "resident-m2")
        self.assertEqual(retry_payload["session_run"]["group_id"], "resident-alt")
        self.assertEqual(operations_payload["operations"][-1]["target_id"], exact["run_id"])


    def test_live_agent_session_run_retry_now_api_target_skips_current_ready_run(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            session_run_controller = LiveAgentSessionRunController(root)
            target = session_run_controller.begin_run(
                action="ensure",
                payload={"meeting_id": "resident-m1", "group_id": "resident-main"},
            )
            session_run_controller.finish_run(
                target["run_id"],
                session={
                    "status": "ready",
                    "meeting_id": "resident-m1",
                    "group_id": "resident-main",
                    "action": "none",
                },
            )
            server = ThreadingHTTPServer(
                ("127.0.0.1", 0),
                _make_handler(root, session_run_controller=session_run_controller),
            )
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/live-agent-session-runs/retry-now",
                    data=json.dumps({"meeting_id": "resident-m1", "group_id": "resident-main"}).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with patch("agentsassemble.gui.live_agent_session_readiness_payload", return_value={"status": "ready"}):
                    with patch("agentsassemble.gui.live_agent_session_ensure_payload") as ensure_payload:
                        with urlopen(request, timeout=4) as response:
                            retry_payload = json.loads(response.read().decode("utf-8"))
                with urlopen(f"http://127.0.0.1:{server.server_port}/api/live-agent-operations?limit=20", timeout=4) as response:
                    operations_payload = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

        self.assertEqual(retry_payload["status"], "skipped")
        self.assertEqual(retry_payload["session_run"]["run_id"], target["run_id"])
        self.assertEqual(retry_payload["results"], [])
        ensure_payload.assert_not_called()
        self.assertEqual(operations_payload["operations"][-1]["status"], "success")
        self.assertEqual(operations_payload["operations"][-1]["details"]["skipped_reason"], "already_ready")


    def test_live_agent_session_run_retry_now_api_target_refuses_without_matching_run(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            session_run_controller = LiveAgentSessionRunController(root)
            server = ThreadingHTTPServer(
                ("127.0.0.1", 0),
                _make_handler(root, session_run_controller=session_run_controller),
            )
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/live-agent-session-runs/retry-now",
                    data=json.dumps({"meeting_id": "resident-m1", "group_id": "resident-main"}).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with self.assertRaises(HTTPError) as caught:
                    urlopen(request, timeout=4)
                error_payload = json.loads(caught.exception.read().decode("utf-8"))
                caught.exception.close()
                with urlopen(f"http://127.0.0.1:{server.server_port}/api/live-agent-operations?limit=20", timeout=4) as response:
                    operations_payload = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

        self.assertEqual(caught.exception.code, 400)
        self.assertIn("No matching live-agent session run", error_payload["error"])
        self.assertEqual(operations_payload["operations"][-1]["operation"], "session_run.retry_now")
        self.assertEqual(operations_payload["operations"][-1]["status"], "failed")
        self.assertEqual(operations_payload["operations"][-1]["details"]["meeting_id"], "resident-m1")
        self.assertEqual(operations_payload["operations"][-1]["details"]["group_id"], "resident-main")


    def test_live_agent_session_run_retry_now_api_skips_ready_current_ready_without_mutating(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            session_run_controller = LiveAgentSessionRunController(root)
            target = session_run_controller.begin_run(
                action="ensure",
                payload={"meeting_id": "resident-m1", "group_id": "resident-main"},
            )
            session_run_controller.finish_run(
                target["run_id"],
                session={
                    "status": "ready",
                    "meeting_id": "resident-m1",
                    "group_id": "resident-main",
                    "action": "none",
                },
            )
            server = ThreadingHTTPServer(
                ("127.0.0.1", 0),
                _make_handler(root, session_run_controller=session_run_controller),
            )
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/live-agent-session-runs/{target['run_id']}/retry-now",
                    data=b"{}",
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with patch("agentsassemble.gui.live_agent_session_readiness_payload", return_value={"status": "ready"}):
                    with patch("agentsassemble.gui.live_agent_session_ensure_payload") as ensure_payload:
                        with urlopen(request, timeout=4) as response:
                            retry_payload = json.loads(response.read().decode("utf-8"))
                with urlopen(f"http://127.0.0.1:{server.server_port}/api/live-agent-session-runs?limit=20", timeout=4) as response:
                    runs_payload = json.loads(response.read().decode("utf-8"))
                with urlopen(f"http://127.0.0.1:{server.server_port}/api/live-agent-operations?limit=20", timeout=4) as response:
                    operations_payload = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

        self.assertEqual(retry_payload["status"], "skipped")
        self.assertEqual(retry_payload["session_run"]["run_id"], target["run_id"])
        self.assertEqual(retry_payload["session_run"]["phase"], "none")
        self.assertEqual(retry_payload["results"], [])
        ensure_payload.assert_not_called()
        self.assertEqual(runs_payload["runs"][0]["phase"], "none")
        self.assertEqual(runs_payload["runs"][0]["status"], "ready")
        self.assertEqual(operations_payload["operations"][-1]["operation"], "session_run.retry_now")
        self.assertEqual(operations_payload["operations"][-1]["status"], "success")
        self.assertEqual(operations_payload["operations"][-1]["details"]["skipped_reason"], "already_ready")


    def test_live_agent_session_run_pause_resume_api_controls_durable_reconcile(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            session_run_controller = LiveAgentSessionRunController(root)
            target = session_run_controller.begin_run(
                action="ensure",
                payload={"meeting_id": "resident-m1", "group_id": "resident-main"},
            )
            session_run_controller.finish_run(
                target["run_id"],
                session={
                    "status": "degraded",
                    "meeting_id": "resident-m1",
                    "group_id": "resident-main",
                    "action": "recover",
                },
            )
            server = ThreadingHTTPServer(
                ("127.0.0.1", 0),
                _make_handler(root, session_run_controller=session_run_controller),
            )
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                pause_request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/live-agent-session-runs/{target['run_id']}/pause",
                    data=b"{}",
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urlopen(pause_request, timeout=4) as response:
                    pause_payload = json.loads(response.read().decode("utf-8"))
                with patch("agentsassemble.gui.live_agent_session_ensure_payload") as ensure_payload:
                    reconciled_while_paused = session_run_controller.reconcile_active_runs(
                        lambda run: ensure_payload(run) or {"status": "ready"}
                    )
                resume_request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/live-agent-session-runs/{target['run_id']}/resume",
                    data=b"{}",
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urlopen(resume_request, timeout=4) as response:
                    resume_payload = json.loads(response.read().decode("utf-8"))
                with urlopen(f"http://127.0.0.1:{server.server_port}/api/live-agent-session-runs?limit=20", timeout=4) as response:
                    runs_payload = json.loads(response.read().decode("utf-8"))
                with urlopen(f"http://127.0.0.1:{server.server_port}/api/live-agent-operations?limit=20", timeout=4) as response:
                    operations_payload = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

        self.assertEqual(pause_payload["status"], "paused")
        self.assertEqual(pause_payload["session_run"]["status"], "paused")
        self.assertFalse(pause_payload["session_run"]["active"])
        self.assertEqual(pause_payload["session_run"]["paused_status"], "degraded")
        self.assertEqual(reconciled_while_paused, [])
        ensure_payload.assert_not_called()
        self.assertEqual(resume_payload["status"], "resumed")
        self.assertEqual(resume_payload["session_run"]["status"], "degraded")
        self.assertTrue(resume_payload["session_run"]["active"])
        self.assertEqual(resume_payload["session_run"]["next_reconcile_at"], "")
        self.assertEqual(resume_payload["session_run"]["reconcile_backoff_seconds"], 0)
        self.assertEqual(runs_payload["runs"][0]["phase"], "resume_requested")
        self.assertEqual(runs_payload["runs"][0]["next_reconcile_at"], "")
        self.assertEqual(runs_payload["runs"][0]["reconcile_backoff_seconds"], 0)
        self.assertEqual([item["operation"] for item in operations_payload["operations"][-2:]], ["session_run.pause", "session_run.resume"])


    def test_live_agent_session_run_pause_resume_api_resolves_latest_matching_meeting_group(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            session_run_controller = LiveAgentSessionRunController(root)
            older_matching = session_run_controller.begin_run(
                action="ensure",
                payload={"meeting_id": "resident-m1", "group_id": "resident-main"},
            )
            session_run_controller.finish_run(
                older_matching["run_id"],
                session={
                    "status": "degraded",
                    "meeting_id": "resident-m1",
                    "group_id": "resident-main",
                    "action": "recover",
                },
            )
            target = session_run_controller.begin_run(
                action="ensure",
                payload={"meeting_id": "resident-m1", "group_id": "resident-main"},
            )
            session_run_controller.finish_run(
                target["run_id"],
                session={
                    "status": "degraded",
                    "meeting_id": "resident-m1",
                    "group_id": "resident-main",
                    "action": "recover",
                },
            )
            unrelated = session_run_controller.begin_run(
                action="ensure",
                payload={"meeting_id": "resident-m2", "group_id": "resident-alt"},
            )
            session_run_controller.finish_run(
                unrelated["run_id"],
                session={
                    "status": "degraded",
                    "meeting_id": "resident-m2",
                    "group_id": "resident-alt",
                    "action": "recover",
                },
            )
            server = ThreadingHTTPServer(
                ("127.0.0.1", 0),
                _make_handler(root, session_run_controller=session_run_controller),
            )
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                pause_request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/live-agent-session-runs/pause",
                    data=json.dumps({"meeting_id": "resident-m1", "group_id": "resident-main"}).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urlopen(pause_request, timeout=4) as response:
                    pause_payload = json.loads(response.read().decode("utf-8"))
                resume_request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/live-agent-session-runs/resume",
                    data=json.dumps({"meeting_id": "resident-m1", "group_id": "resident-main"}).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urlopen(resume_request, timeout=4) as response:
                    resume_payload = json.loads(response.read().decode("utf-8"))
                with urlopen(f"http://127.0.0.1:{server.server_port}/api/live-agent-operations?limit=20", timeout=4) as response:
                    operations_payload = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

        self.assertEqual(pause_payload["status"], "paused")
        self.assertEqual(pause_payload["session_run"]["run_id"], target["run_id"])
        self.assertEqual(resume_payload["status"], "resumed")
        self.assertEqual(resume_payload["session_run"]["run_id"], target["run_id"])
        self.assertEqual([item["operation"] for item in operations_payload["operations"][-2:]], ["session_run.pause", "session_run.resume"])
        self.assertEqual(operations_payload["operations"][-2]["target_id"], target["run_id"])
        self.assertEqual(operations_payload["operations"][-1]["target_id"], target["run_id"])


    def test_live_agent_session_run_stop_api_resolves_latest_matching_meeting_group(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            session_run_controller = LiveAgentSessionRunController(root)
            older_matching = session_run_controller.begin_run(
                action="ensure",
                payload={"meeting_id": "resident-m1", "group_id": "resident-main"},
            )
            session_run_controller.finish_run(
                older_matching["run_id"],
                session={
                    "status": "degraded",
                    "meeting_id": "resident-m1",
                    "group_id": "resident-main",
                    "action": "recover",
                },
            )
            target = session_run_controller.begin_run(
                action="ensure",
                payload={"meeting_id": "resident-m1", "group_id": "resident-main"},
            )
            session_run_controller.finish_run(
                target["run_id"],
                session={
                    "status": "degraded",
                    "meeting_id": "resident-m1",
                    "group_id": "resident-main",
                    "action": "recover",
                },
            )
            server = ThreadingHTTPServer(
                ("127.0.0.1", 0),
                _make_handler(root, session_run_controller=session_run_controller),
            )
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/live-agent-session-runs/stop",
                    data=json.dumps({"meeting_id": "resident-m1", "group_id": "resident-main"}).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urlopen(request, timeout=4) as response:
                    stop_payload = json.loads(response.read().decode("utf-8"))
                with urlopen(f"http://127.0.0.1:{server.server_port}/api/live-agent-operations?limit=20", timeout=4) as response:
                    operations_payload = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

        self.assertEqual(stop_payload["status"], "stopped")
        self.assertEqual(stop_payload["session_run"]["run_id"], target["run_id"])
        self.assertEqual(stop_payload["session_run"]["status"], "stopped")
        self.assertEqual(session_run_controller.get_run(older_matching["run_id"])["status"], "degraded")
        self.assertEqual(operations_payload["operations"][-1]["operation"], "session_run.stop")
        self.assertEqual(operations_payload["operations"][-1]["target_id"], target["run_id"])


    def test_live_agent_session_run_stop_api_path_stops_exact_run(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            session_run_controller = LiveAgentSessionRunController(root)
            target = session_run_controller.begin_run(
                action="ensure",
                payload={"meeting_id": "resident-m1", "group_id": "resident-main"},
            )
            session_run_controller.finish_run(
                target["run_id"],
                session={
                    "status": "degraded",
                    "meeting_id": "resident-m1",
                    "group_id": "resident-main",
                    "action": "recover",
                },
            )
            server = ThreadingHTTPServer(
                ("127.0.0.1", 0),
                _make_handler(root, session_run_controller=session_run_controller),
            )
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/live-agent-session-runs/{target['run_id']}/stop",
                    data=b"{}",
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urlopen(request, timeout=4) as response:
                    stop_payload = json.loads(response.read().decode("utf-8"))
                with urlopen(f"http://127.0.0.1:{server.server_port}/api/live-agent-operations?limit=20", timeout=4) as response:
                    operations_payload = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

        self.assertEqual(stop_payload["status"], "stopped")
        self.assertEqual(stop_payload["session_run"]["run_id"], target["run_id"])
        self.assertEqual(stop_payload["session_run"]["status"], "stopped")
        self.assertEqual(operations_payload["operations"][-1]["operation"], "session_run.stop")
        self.assertEqual(operations_payload["operations"][-1]["details"]["session_run_id"], target["run_id"])


    def test_live_agent_session_run_pause_api_run_id_takes_precedence_over_meeting_group(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            session_run_controller = LiveAgentSessionRunController(root)
            exact = session_run_controller.begin_run(
                action="ensure",
                payload={"meeting_id": "resident-m2", "group_id": "resident-alt"},
            )
            session_run_controller.finish_run(
                exact["run_id"],
                session={
                    "status": "degraded",
                    "meeting_id": "resident-m2",
                    "group_id": "resident-alt",
                    "action": "recover",
                },
            )
            matching = session_run_controller.begin_run(
                action="ensure",
                payload={"meeting_id": "resident-m1", "group_id": "resident-main"},
            )
            session_run_controller.finish_run(
                matching["run_id"],
                session={
                    "status": "degraded",
                    "meeting_id": "resident-m1",
                    "group_id": "resident-main",
                    "action": "recover",
                },
            )
            server = ThreadingHTTPServer(
                ("127.0.0.1", 0),
                _make_handler(root, session_run_controller=session_run_controller),
            )
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/live-agent-session-runs/pause",
                    data=json.dumps(
                        {
                            "run_id": exact["run_id"],
                            "meeting_id": "resident-m1",
                            "group_id": "resident-main",
                        }
                    ).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urlopen(request, timeout=4) as response:
                    pause_payload = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

        self.assertEqual(pause_payload["status"], "paused")
        self.assertEqual(pause_payload["session_run"]["run_id"], exact["run_id"])
        self.assertEqual(pause_payload["session_run"]["meeting_id"], "resident-m2")
        self.assertEqual(pause_payload["session_run"]["group_id"], "resident-alt")


    def test_live_agent_session_run_resume_api_run_id_takes_precedence_over_meeting_group(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            session_run_controller = LiveAgentSessionRunController(root)
            exact = session_run_controller.begin_run(
                action="ensure",
                payload={"meeting_id": "resident-m2", "group_id": "resident-alt"},
            )
            session_run_controller.finish_run(
                exact["run_id"],
                session={
                    "status": "degraded",
                    "meeting_id": "resident-m2",
                    "group_id": "resident-alt",
                    "action": "recover",
                },
            )
            session_run_controller.pause_run(exact["run_id"])
            matching = session_run_controller.begin_run(
                action="ensure",
                payload={"meeting_id": "resident-m1", "group_id": "resident-main"},
            )
            session_run_controller.finish_run(
                matching["run_id"],
                session={
                    "status": "degraded",
                    "meeting_id": "resident-m1",
                    "group_id": "resident-main",
                    "action": "recover",
                },
            )
            session_run_controller.pause_run(matching["run_id"])
            server = ThreadingHTTPServer(
                ("127.0.0.1", 0),
                _make_handler(root, session_run_controller=session_run_controller),
            )
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/live-agent-session-runs/resume",
                    data=json.dumps(
                        {
                            "run_id": exact["run_id"],
                            "meeting_id": "resident-m1",
                            "group_id": "resident-main",
                        }
                    ).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urlopen(request, timeout=4) as response:
                    resume_payload = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

        self.assertEqual(resume_payload["status"], "resumed")
        self.assertEqual(resume_payload["session_run"]["run_id"], exact["run_id"])
        self.assertEqual(resume_payload["session_run"]["meeting_id"], "resident-m2")
        self.assertEqual(resume_payload["session_run"]["group_id"], "resident-alt")
        self.assertEqual(session_run_controller.get_run(matching["run_id"])["status"], "paused")


    def test_live_agent_session_run_stop_api_run_id_takes_precedence_over_meeting_group(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            session_run_controller = LiveAgentSessionRunController(root)
            exact = session_run_controller.begin_run(
                action="ensure",
                payload={"meeting_id": "resident-m2", "group_id": "resident-alt"},
            )
            session_run_controller.finish_run(
                exact["run_id"],
                session={
                    "status": "degraded",
                    "meeting_id": "resident-m2",
                    "group_id": "resident-alt",
                    "action": "recover",
                },
            )
            matching = session_run_controller.begin_run(
                action="ensure",
                payload={"meeting_id": "resident-m1", "group_id": "resident-main"},
            )
            session_run_controller.finish_run(
                matching["run_id"],
                session={
                    "status": "degraded",
                    "meeting_id": "resident-m1",
                    "group_id": "resident-main",
                    "action": "recover",
                },
            )
            server = ThreadingHTTPServer(
                ("127.0.0.1", 0),
                _make_handler(root, session_run_controller=session_run_controller),
            )
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/live-agent-session-runs/stop",
                    data=json.dumps(
                        {
                            "run_id": exact["run_id"],
                            "meeting_id": "resident-m1",
                            "group_id": "resident-main",
                        }
                    ).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urlopen(request, timeout=4) as response:
                    stop_payload = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

        self.assertEqual(stop_payload["status"], "stopped")
        self.assertEqual(stop_payload["session_run"]["run_id"], exact["run_id"])
        self.assertEqual(stop_payload["session_run"]["meeting_id"], "resident-m2")
        self.assertEqual(stop_payload["session_run"]["group_id"], "resident-alt")
        self.assertEqual(session_run_controller.get_run(matching["run_id"])["status"], "degraded")


    def test_live_agent_session_run_pause_resume_api_uses_latest_before_eligibility_checks(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            session_run_controller = LiveAgentSessionRunController(root)
            older_pause_candidate = session_run_controller.begin_run(
                action="ensure",
                payload={"meeting_id": "resident-m1", "group_id": "resident-main"},
            )
            session_run_controller.finish_run(
                older_pause_candidate["run_id"],
                session={
                    "status": "degraded",
                    "meeting_id": "resident-m1",
                    "group_id": "resident-main",
                    "action": "recover",
                },
            )
            latest_pause_target = session_run_controller.begin_run(
                action="ensure",
                payload={"meeting_id": "resident-m1", "group_id": "resident-main"},
            )
            session_run_controller.fail_run(latest_pause_target["run_id"], "terminal failure")
            older_resume_candidate = session_run_controller.begin_run(
                action="ensure",
                payload={"meeting_id": "resident-m3", "group_id": "resident-review"},
            )
            session_run_controller.finish_run(
                older_resume_candidate["run_id"],
                session={
                    "status": "degraded",
                    "meeting_id": "resident-m3",
                    "group_id": "resident-review",
                    "action": "recover",
                },
            )
            session_run_controller.pause_run(older_resume_candidate["run_id"])
            latest_resume_target = session_run_controller.begin_run(
                action="ensure",
                payload={"meeting_id": "resident-m3", "group_id": "resident-review"},
            )
            session_run_controller.finish_run(
                latest_resume_target["run_id"],
                session={
                    "status": "degraded",
                    "meeting_id": "resident-m3",
                    "group_id": "resident-review",
                    "action": "recover",
                },
            )
            server = ThreadingHTTPServer(
                ("127.0.0.1", 0),
                _make_handler(root, session_run_controller=session_run_controller),
            )
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                pause_request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/live-agent-session-runs/pause",
                    data=json.dumps({"meeting_id": "resident-m1", "group_id": "resident-main"}).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with self.assertRaises(HTTPError) as pause_error:
                    urlopen(pause_request, timeout=4)
                pause_error_payload = json.loads(pause_error.exception.read().decode("utf-8"))
                pause_error.exception.close()
                resume_request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/live-agent-session-runs/resume",
                    data=json.dumps({"meeting_id": "resident-m3", "group_id": "resident-review"}).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with self.assertRaises(HTTPError) as resume_error:
                    urlopen(resume_request, timeout=4)
                resume_error_payload = json.loads(resume_error.exception.read().decode("utf-8"))
                resume_error.exception.close()
                with urlopen(f"http://127.0.0.1:{server.server_port}/api/live-agent-operations?limit=20", timeout=4) as response:
                    operations_payload = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

        self.assertEqual(pause_error.exception.code, 400)
        self.assertIn(latest_pause_target["run_id"], pause_error_payload["error"])
        self.assertEqual(resume_error.exception.code, 400)
        self.assertIn(latest_resume_target["run_id"], resume_error_payload["error"])
        self.assertEqual(session_run_controller.get_run(older_pause_candidate["run_id"])["status"], "degraded")
        self.assertEqual(session_run_controller.get_run(older_resume_candidate["run_id"])["status"], "paused")
        self.assertEqual(operations_payload["operations"][-2]["target_id"], latest_pause_target["run_id"])
        self.assertEqual(operations_payload["operations"][-1]["target_id"], latest_resume_target["run_id"])


    def test_live_agent_session_run_pause_api_target_refuses_without_matching_run(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            session_run_controller = LiveAgentSessionRunController(root)
            server = ThreadingHTTPServer(
                ("127.0.0.1", 0),
                _make_handler(root, session_run_controller=session_run_controller),
            )
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/live-agent-session-runs/pause",
                    data=json.dumps({"meeting_id": "resident-m1", "group_id": "resident-main"}).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with self.assertRaises(HTTPError) as caught:
                    urlopen(request, timeout=4)
                error_payload = json.loads(caught.exception.read().decode("utf-8"))
                caught.exception.close()
                with urlopen(f"http://127.0.0.1:{server.server_port}/api/live-agent-operations?limit=20", timeout=4) as response:
                    operations_payload = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

        self.assertEqual(caught.exception.code, 400)
        self.assertIn("No matching live-agent session run", error_payload["error"])
        self.assertEqual(operations_payload["operations"][-1]["operation"], "session_run.pause")
        self.assertEqual(operations_payload["operations"][-1]["status"], "failed")
        self.assertEqual(operations_payload["operations"][-1]["details"]["meeting_id"], "resident-m1")
        self.assertEqual(operations_payload["operations"][-1]["details"]["group_id"], "resident-main")


    def test_live_agent_session_runs_api_can_overlay_current_readiness_without_operations(self):
        class ReadinessSessionSupervisor:
            def snapshot_groups(self):
                return [
                    {
                        "group_id": "resident-main",
                        "status": "stopped",
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
            session_run_controller = LiveAgentSessionRunController(root)
            target = session_run_controller.begin_run(
                action="ensure",
                payload={"meeting_id": "resident-m1", "group_id": "resident-main"},
            )
            session_run_controller.finish_run(
                target["run_id"],
                session={
                    "status": "ready",
                    "meeting_id": "resident-m1",
                    "group_id": "resident-main",
                    "action": "none",
                },
            )
            server = ThreadingHTTPServer(
                ("127.0.0.1", 0),
                _make_handler(
                    root,
                    process_supervisor=ReadinessSessionSupervisor(),
                    session_run_controller=session_run_controller,
                ),
            )
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with urlopen(
                    f"http://127.0.0.1:{server.server_port}/api/live-agent-session-runs"
                    "?limit=1&meeting_id=resident-m1&group_id=resident-main&include_readiness=1",
                    timeout=4,
                ) as response:
                    runs_payload = json.loads(response.read().decode("utf-8"))
                with urlopen(
                    f"http://127.0.0.1:{server.server_port}/api/live-agent-operations?limit=20",
                    timeout=4,
                ) as response:
                    operations = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

        run = runs_payload["runs"][0]
        self.assertEqual(run["run_id"], target["run_id"])
        self.assertEqual(run["status"], "ready")
        self.assertEqual(run["readiness"]["status"], "degraded")
        self.assertEqual(run["readiness"]["process_status"], "stopped")
        self.assertEqual(run["readiness"]["expected"], 1)
        self.assertEqual(run["readiness"]["connected"], 0)
        self.assertEqual(operations["operations"], [])
        self.assertNotIn(str(live_agent_config), str(runs_payload))


    def test_live_agent_session_runs_api_redacts_token_like_readiness_process_reason(self):
        sensitive_agent_token = "ghp_abcdefghijklmnopqrstuvwxyz1234567890"

        class ReadinessSessionSupervisor:
            def snapshot_groups(self):
                return [
                    {
                        "group_id": "resident-main",
                        "status": "error",
                        "meeting_id": "resident-m1",
                        "recent_events": [
                            {
                                "event_type": "stale_watchdog",
                                "reason": f"missing manifest agent {sensitive_agent_token}",
                            }
                        ],
                        "agents": [{"agent_id": "agent-a"}],
                    }
                ]

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _write_health_resident_meeting(root, agent_ids=["agent-a"])
            session_run_controller = LiveAgentSessionRunController(root)
            target = session_run_controller.begin_run(
                action="ensure",
                payload={"meeting_id": "resident-m1", "group_id": "resident-main"},
            )
            session_run_controller.finish_run(
                target["run_id"],
                session={
                    "status": "ready",
                    "meeting_id": "resident-m1",
                    "group_id": "resident-main",
                    "action": "none",
                },
            )
            server = ThreadingHTTPServer(
                ("127.0.0.1", 0),
                _make_handler(
                    root,
                    process_supervisor=ReadinessSessionSupervisor(),
                    session_run_controller=session_run_controller,
                ),
            )
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with urlopen(
                    f"http://127.0.0.1:{server.server_port}/api/live-agent-session-runs"
                    "?limit=1&meeting_id=resident-m1&group_id=resident-main&include_readiness=1",
                    timeout=4,
                ) as response:
                    runs_payload = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

        serialized = json.dumps(runs_payload, ensure_ascii=False)
        self.assertNotIn(sensitive_agent_token, serialized)
        run = runs_payload["runs"][0]
        self.assertEqual(run["run_id"], target["run_id"])
        self.assertEqual(run["readiness"]["status"], "degraded")
        self.assertNotIn("process_reason", run["readiness"])


    def test_live_agent_session_runs_api_redacts_sensitive_legacy_ids_with_readiness(self):
        class FakeSupervisor:
            def snapshot_groups(self):
                return []

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            runs_dir = root / "live-agent-runs"
            runs_dir.mkdir(parents=True)
            (runs_dir / "session-runs.json").write_text(
                json.dumps(
                    {
                        "runs": [
                            {
                                "run_id": "ghp_abcdefghijklmnopqrstuvwxyz1234567890",
                                "action": "ensure",
                                "status": "ready",
                                "active": True,
                                "phase": "/Users/me/private/live-agents.secret.json token sk-test-secret",
                                "meeting_id": "env:SECRET_MEETING",
                                "group_id": "literal:SECRET_GROUP",
                                "request": {
                                    "meeting_id": "env:SECRET_MEETING",
                                    "group_id": "literal:SECRET_GROUP",
                                },
                                "result": {
                                    "status": "ready",
                                    "process": {
                                        "env:SECRET_TOKEN": "ok",
                                        "nested": {"ghp_abcdefghijklmnopqrstuvwxyz1234567890": "ok"},
                                    },
                                    "connection": {"/Users/me/private/config.json": "ok"},
                                },
                            }
                        ]
                    },
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )
            server = ThreadingHTTPServer(
                ("127.0.0.1", 0),
                _make_handler(root, process_supervisor=FakeSupervisor()),
            )
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with urlopen(
                    f"http://127.0.0.1:{server.server_port}/api/live-agent-session-runs?limit=20&include_readiness=1",
                    timeout=4,
                ) as response:
                    runs_payload = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

        serialized = json.dumps(runs_payload, ensure_ascii=False)
        self.assertNotIn("ghp_abcdefghijklmnopqrstuvwxyz1234567890", serialized)
        self.assertNotIn("SECRET_MEETING", serialized)
        self.assertNotIn("SECRET_GROUP", serialized)
        self.assertNotIn("SECRET_TOKEN", serialized)
        self.assertNotIn("Users/me/private", serialized)
        self.assertNotIn("config.json", serialized)
        self.assertNotIn("live-agents.secret.json", serialized)
        self.assertNotIn("sk-test-secret", serialized)
        self.assertEqual(runs_payload["runs"][0]["run_id"], "")
        self.assertEqual(runs_payload["runs"][0]["phase"], "")
        self.assertEqual(runs_payload["runs"][0]["meeting_id"], "")
        self.assertEqual(runs_payload["runs"][0]["group_id"], "")
        self.assertEqual(runs_payload["runs"][0]["readiness"]["attention"], ["session_run:missing_target"])


    def test_live_agent_session_runs_api_redacts_relative_path_like_legacy_values(self):
        class FakeSupervisor:
            def snapshot_groups(self):
                return []

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            runs_dir = root / "live-agent-runs"
            runs_dir.mkdir(parents=True)
            (runs_dir / "session-runs.json").write_text(
                json.dumps(
                    {
                        "runs": [
                            {
                                "run_id": "run-relative-paths",
                                "action": "ensure",
                                "status": "ready",
                                "active": True,
                                "phase": "configs/private.yaml",
                                "meeting_id": "resident-m1",
                                "group_id": "resident-main",
                                "request": {
                                    "meeting_id": "resident-m1",
                                    "group_id": "resident-main",
                                    "live_agent_config_path": "configs/private.yaml",
                                },
                                "result": {
                                    "status": "ready",
                                    "process": {
                                        "relative/private.txt": "ok",
                                        "safe_key": "relative/private.txt",
                                    },
                                },
                            }
                        ]
                    },
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )
            server = ThreadingHTTPServer(
                ("127.0.0.1", 0),
                _make_handler(root, process_supervisor=FakeSupervisor()),
            )
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with urlopen(
                    f"http://127.0.0.1:{server.server_port}/api/live-agent-session-runs?limit=20&include_readiness=1",
                    timeout=4,
                ) as response:
                    runs_payload = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

        serialized = json.dumps(runs_payload, ensure_ascii=False)
        self.assertNotIn("configs/private.yaml", serialized)
        self.assertNotIn("relative/private.txt", serialized)
        run = runs_payload["runs"][0]
        self.assertEqual(run["phase"], "")
        self.assertNotIn("live_agent_config_path", run["request"])
        self.assertEqual(run["result"]["process"], {"safe_key": "[redacted]"})


    def test_live_agent_session_runs_api_redacts_backslash_relative_path_like_legacy_values(self):
        class FakeSupervisor:
            def snapshot_groups(self):
                return []

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            runs_dir = root / "live-agent-runs"
            runs_dir.mkdir(parents=True)
            (runs_dir / "session-runs.json").write_text(
                json.dumps(
                    {
                        "runs": [
                            {
                                "run_id": "run-backslash-paths",
                                "action": "ensure",
                                "status": "ready",
                                "active": True,
                                "phase": "configs\\private.yaml",
                                "meeting_id": "resident-m1",
                                "group_id": "resident-main",
                                "request": {
                                    "meeting_id": "resident-m1",
                                    "group_id": "resident-main",
                                },
                                "result": {
                                    "status": "ready",
                                    "process": {
                                        "relative\\private.txt": "ok",
                                        "safe_key": "..\\private.txt",
                                    },
                                },
                            }
                        ]
                    },
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )
            server = ThreadingHTTPServer(
                ("127.0.0.1", 0),
                _make_handler(root, process_supervisor=FakeSupervisor()),
            )
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with urlopen(
                    f"http://127.0.0.1:{server.server_port}/api/live-agent-session-runs?limit=20&include_readiness=1",
                    timeout=4,
                ) as response:
                    runs_payload = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

        serialized = json.dumps(runs_payload, ensure_ascii=False)
        self.assertNotIn("configs\\\\private.yaml", serialized)
        self.assertNotIn("relative\\\\private.txt", serialized)
        self.assertNotIn("..\\\\private.txt", serialized)
        run = runs_payload["runs"][0]
        self.assertEqual(run["phase"], "")
        self.assertEqual(run["result"]["process"], {"safe_key": "[redacted]"})
