from tests.gui_server_test_support import (
    HTTPError,
    LiveAgentProcessSupervisor,
    Path,
    Request,
    ThreadingHTTPServer,
    _make_handler,
    json,
    tempfile,
    threading,
    unittest,
    urlopen,
)


class GuiServerProcessSmokeTests(unittest.TestCase):

    def test_live_agent_process_endpoints_start_list_and_stop_group(self):
        class FakeSupervisor:
            def __init__(self):
                self.groups = []
                self.started = []
                self.stopped = []
                self.restarted = []

            def list_groups(self):
                return list(self.groups)

            def start_group(
                self,
                *,
                config_path,
                server,
                group_id=None,
                auto_restart=False,
                max_restarts=0,
                restart_backoff_seconds=5.0,
                stale_restart_after_seconds=0.0,
            ):
                self.started.append(
                    {
                        "config_path": config_path,
                        "server": server,
                        "group_id": group_id,
                        "auto_restart": auto_restart,
                        "max_restarts": max_restarts,
                        "restart_backoff_seconds": restart_backoff_seconds,
                        "stale_restart_after_seconds": stale_restart_after_seconds,
                    }
                )
                record = {
                    "group_id": group_id or "default",
                    "status": "running",
                    "pid": 4321,
                    "config_path": str(config_path),
                    "server": server,
                    "log_path": "live-agent-runs/default.log",
                    "started_at": "2026-05-17T12:00:00+00:00",
                    "stopped_at": "",
                    "returncode": None,
                    "last_error": "",
                    "log_tail": "resident booted",
                    "auto_restart": auto_restart,
                    "restart_count": 0,
                    "max_restarts": max_restarts,
                    "restart_backoff_seconds": restart_backoff_seconds,
                    "stale_restart_after_seconds": stale_restart_after_seconds,
                    "next_restart_at": "",
                    "agents": [
                        {
                            "agent_id": "local-a",
                            "display_name": "Local A",
                            "provider_kind": "local_cli",
                            "connection_kind": "local_cli",
                        }
                    ],
                    "recent_events": [
                        {
                            "event_type": "started",
                            "timestamp": "2026-05-17T12:00:00+00:00",
                            "group_id": group_id or "default",
                            "status": "running",
                            "pid": 4321,
                        }
                    ],
                }
                self.groups = [record]
                return record

            def stop_group(self, group_id):
                self.stopped.append(group_id)
                record = dict(self.groups[0])
                record["status"] = "stopped"
                record["returncode"] = 0
                record["offline"] = {
                    "expected": 1,
                    "offline": 1,
                    "skipped": 0,
                    "offline_agent_ids": ["local-a"],
                    "attention": [],
                }
                self.groups = [record]
                return record

            def restart_group(self, group_id):
                self.restarted.append(group_id)
                record = dict(self.groups[0])
                record["status"] = "running"
                record["pid"] = 9876
                record["returncode"] = None
                self.groups = [record]
                return record

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = root / "live-agents.json"
            config_path.write_text('{"agents": [{"agent_id": "a", "command": ["fake"]}]}', encoding="utf-8")
            supervisor = FakeSupervisor()
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root, process_supervisor=supervisor))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                start_request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/live-agent-processes/start",
                    data=json.dumps(
                        {
                            "config_path": str(config_path),
                            "group_id": "crew",
                            "auto_restart": True,
                            "max_restarts": 2,
                            "restart_backoff_seconds": 1.5,
                            "stale_restart_after_seconds": 240,
                        }
                    ).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urlopen(start_request, timeout=4) as response:
                    started = json.loads(response.read().decode("utf-8"))
                with urlopen(f"http://127.0.0.1:{server.server_port}/api/live-agent-processes", timeout=4) as response:
                    listed = json.loads(response.read().decode("utf-8"))
                stop_request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/live-agent-processes/crew/stop",
                    data=b"{}",
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urlopen(stop_request, timeout=4) as response:
                    stopped = json.loads(response.read().decode("utf-8"))
                restart_request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/live-agent-processes/crew/restart",
                    data=b"{}",
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urlopen(restart_request, timeout=4) as response:
                    restarted = json.loads(response.read().decode("utf-8"))
                with urlopen(
                    f"http://127.0.0.1:{server.server_port}/api/live-agent-operations?limit=10",
                    timeout=4,
                ) as response:
                    operations = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

            self.assertEqual(started["group"]["status"], "running")
            self.assertEqual(started["group"]["agents"][0]["agent_id"], "local-a")
            self.assertEqual(started["group"]["recent_events"][0]["event_type"], "started")
            self.assertEqual(listed["groups"][0]["group_id"], "crew")
            self.assertEqual(listed["groups"][0]["agents"][0]["connection_kind"], "local_cli")
            self.assertEqual(listed["groups"][0]["recent_events"][0]["status"], "running")
            self.assertEqual(listed["groups"][0]["log_tail"], "resident booted")
            self.assertEqual(stopped["group"]["status"], "stopped")
            self.assertEqual(stopped["group"]["offline"]["offline_agent_ids"], ["local-a"])
            self.assertEqual(restarted["group"]["status"], "running")
            self.assertEqual(restarted["group"]["pid"], 9876)
            self.assertEqual(restarted["group"]["agents"][0]["display_name"], "Local A")
            self.assertEqual(supervisor.started[0]["server"], f"http://127.0.0.1:{server.server_port}")
            self.assertEqual(supervisor.started[0]["auto_restart"], True)
            self.assertEqual(supervisor.started[0]["max_restarts"], 2)
            self.assertEqual(supervisor.started[0]["restart_backoff_seconds"], 1.5)
            self.assertEqual(supervisor.started[0]["stale_restart_after_seconds"], 240.0)
            self.assertEqual(supervisor.stopped, ["crew"])
            self.assertEqual(supervisor.restarted, ["crew"])
            self.assertEqual(
                [(operation["operation"], operation["status"], operation["target_id"]) for operation in operations["operations"]],
                [
                    ("process.start", "success", "crew"),
                    ("process.stop", "success", "crew"),
                    ("process.restart", "success", "crew"),
                ],
            )
            operation_text = json.dumps(operations, ensure_ascii=False)
            self.assertIn('"offline_agent_count": 1', operation_text)
            self.assertIn('"offline_expected_agent_count": 1', operation_text)
            self.assertIn('"offline_agent_ids": ["local-a"]', operation_text)
            self.assertNotIn(str(config_path), operation_text)
            self.assertNotIn(f"http://127.0.0.1:{server.server_port}", operation_text)


    def test_live_agent_process_stop_running_endpoint_records_sanitized_operation(self):
        class FakeSupervisor:
            def __init__(self):
                self.groups = [
                    {"group_id": "crew-a", "status": "running", "pid": 1111, "config_path": "/tmp/secret-a.json"},
                    {"group_id": "crew-b", "status": "restarting", "pid": None, "config_path": "/tmp/secret-b.json"},
                    {"group_id": "old-crew", "status": "unknown", "pid": None, "config_path": "/tmp/secret-c.json"},
                ]
                self.stopped_running = False

            def list_groups(self):
                return list(self.groups)

            def stop_running_groups(self):
                self.stopped_running = True
                self.groups = [
                    {"group_id": "crew-a", "status": "stopped", "pid": None, "config_path": "/tmp/secret-a.json"},
                    {"group_id": "crew-b", "status": "stopped", "pid": None, "config_path": "/tmp/secret-b.json"},
                    {"group_id": "old-crew", "status": "unknown", "pid": None, "config_path": "/tmp/secret-c.json"},
                ]
                return {
                    "stopped_count": 2,
                    "failed_count": 0,
                    "skipped_count": 1,
                    "stopped": [
                        {
                            **self.groups[0],
                            "offline": {
                                "expected": 1,
                                "offline": 1,
                                "skipped": 0,
                                "offline_agent_ids": ["agent-a"],
                                "attention": [],
                            },
                        },
                        {
                            **self.groups[1],
                            "offline": {
                                "expected": 1,
                                "offline": 0,
                                "skipped": 1,
                                "offline_agent_ids": [],
                                "attention": [{"agent_id": "agent-b", "status": "wrong_meeting"}],
                            },
                        },
                    ],
                    "failed": [],
                    "skipped": self.groups[2:],
                }

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            supervisor = FakeSupervisor()
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root, process_supervisor=supervisor))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/live-agent-processes/stop-running",
                    data=b"{}",
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urlopen(request, timeout=4) as response:
                    payload = json.loads(response.read().decode("utf-8"))
                with urlopen(
                    f"http://127.0.0.1:{server.server_port}/api/live-agent-operations?limit=10",
                    timeout=4,
                ) as response:
                    operations = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

            self.assertTrue(supervisor.stopped_running)
            self.assertEqual(payload["result"]["stopped_count"], 2)
            self.assertEqual([group["status"] for group in payload["groups"]], ["stopped", "stopped", "unknown"])
            self.assertEqual(
                [(operation["operation"], operation["status"], operation["target_id"]) for operation in operations["operations"]],
                [("process.stop_running", "success", "running-groups")],
            )
            details = operations["operations"][0]["details"]
            self.assertEqual(details["stopped_count"], 2)
            self.assertEqual(details["failed_count"], 0)
            self.assertEqual(details["skipped_count"], 1)
            self.assertEqual(details["stopped_group_ids"], ["crew-a", "crew-b"])
            self.assertEqual(details["offline_agent_count"], 1)
            self.assertEqual(details["offline_expected_agent_count"], 2)
            self.assertEqual(details["offline_agent_ids"], ["agent-a"])
            self.assertEqual(details["offline_attention"], ["agent-b:wrong_meeting"])
            operation_text = json.dumps(operations, ensure_ascii=False)
            self.assertNotIn("/tmp/secret-a.json", operation_text)
            self.assertNotIn("/tmp/secret-b.json", operation_text)


    def test_live_agent_process_events_endpoint_returns_sanitized_tail_without_operation_record(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            path = root / "live-agent-runs" / "events.jsonl"
            path.parent.mkdir(parents=True)
            path.write_text(
                "\n".join(
                    [
                        json.dumps(
                            {
                                "timestamp": "2026-05-17T12:00:00+00:00",
                                "group_id": "crew",
                                "event_type": "started",
                                "status": "running",
                                "pid": 1234,
                                "server": "http://room.local",
                                "config_path": "/tmp/live-agents.json",
                            }
                        ),
                        json.dumps(
                            {
                                "timestamp": "2026-05-17T12:01:00+00:00",
                                "group_id": "other",
                                "event_type": "started",
                                "status": "running",
                                "pid": 2234,
                            }
                        ),
                        json.dumps(
                            {
                                "timestamp": "2026-05-17T12:02:00+00:00",
                                "group_id": "crew",
                                "event_type": "restart_scheduled",
                                "status": "restarting",
                                "returncode": 2,
                                "offline": {
                                    "expected": 2,
                                    "offline": 1,
                                    "skipped": 1,
                                    "offline_agent_ids": ["agent-a"],
                                    "attention": [{"agent_id": "agent-b", "status": "wrong_meeting"}],
                                },
                                "prompt": "secret prompt",
                                "log_tail": "provider output",
                            }
                        ),
                    ]
                ),
                encoding="utf-8",
            )
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with urlopen(
                    f"http://127.0.0.1:{server.server_port}/api/live-agent-process-events?group_id=crew&limit=2&scan_limit=4",
                    timeout=4,
                ) as response:
                    payload = json.loads(response.read().decode("utf-8"))
                with urlopen(
                    f"http://127.0.0.1:{server.server_port}/api/live-agent-operations?limit=10",
                    timeout=4,
                ) as response:
                    operations = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

        self.assertEqual([event["event_type"] for event in payload["events"]], ["started", "restart_scheduled"])
        self.assertEqual(payload["events"][1]["offline"]["offline_agent_ids"], ["agent-a"])
        self.assertEqual(payload["limit"], 2)
        self.assertEqual(payload["group_id"], "crew")
        self.assertEqual(payload["scan_limit"], 4)
        self.assertEqual(payload["scanned_event_count"], 3)
        self.assertEqual(payload["truncated"], False)
        payload_text = json.dumps(payload, ensure_ascii=False)
        self.assertNotIn("other", json.dumps([event["group_id"] for event in payload["events"]]))
        self.assertNotIn("http://room.local", payload_text)
        self.assertNotIn("config_path", payload_text)
        self.assertNotIn("prompt", payload_text)
        self.assertNotIn("log_tail", payload_text)
        self.assertEqual(operations["operations"], [])


    def test_live_agent_process_start_sanitizes_non_finite_backoff(self):
        class FakeSupervisor:
            def __init__(self):
                self.started = []

            def start_group(
                self,
                *,
                config_path,
                server,
                group_id=None,
                auto_restart=False,
                max_restarts=0,
                restart_backoff_seconds=5.0,
            ):
                self.started.append(restart_backoff_seconds)
                return {
                    "group_id": group_id or "default",
                    "status": "running",
                    "pid": 1234,
                    "config_path": str(config_path),
                    "server": server,
                    "restart_backoff_seconds": restart_backoff_seconds,
                }

            def list_groups(self):
                return []

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = root / "live-agents.json"
            config_path.write_text('{"agents": [{"agent_id": "a", "command": ["fake"]}]}', encoding="utf-8")
            supervisor = FakeSupervisor()
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root, process_supervisor=supervisor))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/live-agent-processes/start",
                    data=json.dumps(
                        {
                            "config_path": str(config_path),
                            "group_id": "crew",
                            "auto_restart": True,
                            "max_restarts": 1,
                            "restart_backoff_seconds": float("inf"),
                        }
                    ).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urlopen(request, timeout=4) as response:
                    payload = json.loads(response.read().decode("utf-8"))
                with urlopen(
                    f"http://127.0.0.1:{server.server_port}/api/live-agent-operations",
                    timeout=4,
                ) as response:
                    operations = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

            self.assertEqual(payload["group"]["restart_backoff_seconds"], 5.0)
            self.assertEqual(supervisor.started, [5.0])


    def test_live_agent_process_start_sanitizes_non_finite_restart_count(self):
        class FakeSupervisor:
            def __init__(self):
                self.started = []

            def start_group(
                self,
                *,
                config_path,
                server,
                group_id=None,
                auto_restart=False,
                max_restarts=0,
                restart_backoff_seconds=5.0,
            ):
                self.started.append(max_restarts)
                return {
                    "group_id": group_id or "default",
                    "status": "running",
                    "pid": 1234,
                    "config_path": str(config_path),
                    "server": server,
                    "max_restarts": max_restarts,
                }

            def list_groups(self):
                return []

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = root / "live-agents.json"
            config_path.write_text('{"agents": [{"agent_id": "a", "command": ["fake"]}]}', encoding="utf-8")
            supervisor = FakeSupervisor()
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root, process_supervisor=supervisor))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/live-agent-processes/start",
                    data=json.dumps(
                        {
                            "config_path": str(config_path),
                            "group_id": "crew",
                            "auto_restart": True,
                            "max_restarts": float("inf"),
                        }
                    ).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urlopen(request, timeout=4) as response:
                    payload = json.loads(response.read().decode("utf-8"))
                operations_url = f"http://127.0.0.1:{server.server_port}/api/live-agent-operations"
                with urlopen(operations_url, timeout=4) as response:
                    operations = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

            self.assertEqual(payload["group"]["max_restarts"], 0)
            self.assertEqual(supervisor.started, [0])


    def test_live_agent_process_start_returns_400_when_preflight_fails_without_launch(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = root / "live-agents.json"
            config_path.write_text(
                '{"agents": [{"agent_id": "bad-agent", "command": ["definitely-missing-agentsassemble-cli"]}]}',
                encoding="utf-8",
            )

            def command_factory(command, **kwargs):
                raise AssertionError("preflight failure must not launch a process")

            supervisor = LiveAgentProcessSupervisor(root, command_factory=command_factory)
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root, process_supervisor=supervisor))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/live-agent-processes/start",
                    data=json.dumps({"config_path": str(config_path), "group_id": "crew"}).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                try:
                    urlopen(request, timeout=4)
                except HTTPError as error:
                    error_code = error.code
                    body = error.read().decode("utf-8")
                    error.close()
                else:
                    self.fail("preflight failure should return HTTP 400")
                with urlopen(
                    f"http://127.0.0.1:{server.server_port}/api/live-agent-operations",
                    timeout=4,
                ) as response:
                    operations = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()
                supervisor.close()

            self.assertEqual(error_code, 400)
            self.assertIn("Live agent preflight failed", body)
            self.assertIn("bad-agent command", body)
            self.assertFalse((root / "live-agent-runs" / "crew.log").exists())
            self.assertFalse((root / "live-agent-runs" / "processes.json").exists())
            self.assertEqual(operations["operations"][0]["operation"], "process.start")
            self.assertEqual(operations["operations"][0]["status"], "failed")
            self.assertEqual(operations["operations"][0]["target_id"], "crew")
            self.assertIn("Live agent preflight failed", operations["operations"][0]["error"])
            self.assertNotIn("bad-agent command", operations["operations"][0]["error"])
            self.assertNotIn("definitely-missing-agentsassemble-cli", json.dumps(operations, ensure_ascii=False))


    def test_live_agent_process_start_redacts_sensitive_error_body(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            missing_config = root / "private" / "live-agents.secret.json"
            supervisor = LiveAgentProcessSupervisor(root)
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root, process_supervisor=supervisor))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/live-agent-processes/start",
                    data=json.dumps({"config_path": str(missing_config), "group_id": "crew"}).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                try:
                    urlopen(request, timeout=4)
                except HTTPError as error:
                    error_code = error.code
                    body = json.loads(error.read().decode("utf-8"))
                    error.close()
                else:
                    self.fail("missing config should return HTTP 400")
                with urlopen(
                    f"http://127.0.0.1:{server.server_port}/api/live-agent-operations",
                    timeout=4,
                ) as response:
                    operations = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()
                supervisor.close()

            response_text = json.dumps(body, ensure_ascii=False)
            operation_text = json.dumps(operations, ensure_ascii=False)
            self.assertEqual(error_code, 400)
            self.assertEqual(body["error"], "Resident process group failed to start: details redacted.")
            self.assertNotIn("live-agents.secret.json", response_text)
            self.assertNotIn(str(missing_config), response_text)
            self.assertNotIn("live-agents.secret.json", operation_text)
            self.assertNotIn(str(missing_config), operation_text)


    def test_live_agent_process_restart_redacts_sensitive_error_body(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            missing_config = root / "private" / "live-agents.secret.json"
            state_dir = root / "live-agent-runs"
            state_dir.mkdir(parents=True)
            (state_dir / "processes.json").write_text(
                json.dumps(
                    {
                        "groups": [
                            {
                                "group_id": "crew",
                                "status": "error",
                                "pid": None,
                                "meeting_id": "",
                                "config_path": str(missing_config),
                                "server": "http://room.local",
                                "log_path": "",
                                "started_at": "",
                                "stopped_at": "",
                                "returncode": 2,
                                "last_error": "",
                                "auto_restart": False,
                                "restart_count": 0,
                                "max_restarts": 0,
                                "restart_backoff_seconds": 5,
                                "stale_restart_after_seconds": 0,
                                "next_restart_at": "",
                                "diagnostic": False,
                                "agents": [],
                                "recovered_from_status": "",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            supervisor = LiveAgentProcessSupervisor(root)
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root, process_supervisor=supervisor))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/live-agent-processes/crew/restart",
                    data=b"{}",
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                try:
                    urlopen(request, timeout=4)
                except HTTPError as error:
                    error_code = error.code
                    body = json.loads(error.read().decode("utf-8"))
                    error.close()
                else:
                    self.fail("missing persisted config should return HTTP 400")
                with urlopen(
                    f"http://127.0.0.1:{server.server_port}/api/live-agent-operations",
                    timeout=4,
                ) as response:
                    operations = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()
                supervisor.close()

            response_text = json.dumps(body, ensure_ascii=False)
            operation_text = json.dumps(operations, ensure_ascii=False)
            self.assertEqual(error_code, 400)
            self.assertEqual(body["error"], "Resident process group failed to restart: details redacted.")
            self.assertEqual(body["group_id"], "crew")
            self.assertNotIn("live-agents.secret.json", response_text)
            self.assertNotIn(str(missing_config), response_text)
            self.assertNotIn("live-agents.secret.json", operation_text)
            self.assertNotIn(str(missing_config), operation_text)


    def test_live_agent_process_recover_redacts_sensitive_error_body(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            missing_config = root / "private" / "live-agents.secret.json"
            state_dir = root / "live-agent-runs"
            state_dir.mkdir(parents=True)
            (state_dir / "processes.json").write_text(
                json.dumps(
                    {
                        "groups": [
                            {
                                "group_id": "crew",
                                "status": "error",
                                "pid": None,
                                "meeting_id": "",
                                "config_path": str(missing_config),
                                "server": "http://room.local",
                                "log_path": "",
                                "started_at": "",
                                "stopped_at": "",
                                "returncode": 2,
                                "last_error": "",
                                "auto_restart": False,
                                "restart_count": 0,
                                "max_restarts": 0,
                                "restart_backoff_seconds": 5,
                                "stale_restart_after_seconds": 0,
                                "next_restart_at": "",
                                "diagnostic": False,
                                "agents": [],
                                "recovered_from_status": "",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            supervisor = LiveAgentProcessSupervisor(root)
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root, process_supervisor=supervisor))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/live-agent-processes/crew/recover",
                    data=b"{}",
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                try:
                    urlopen(request, timeout=4)
                except HTTPError as error:
                    error_code = error.code
                    body = json.loads(error.read().decode("utf-8"))
                    error.close()
                else:
                    self.fail("missing persisted config should return HTTP 400")
            finally:
                server.shutdown()
                server.server_close()
                supervisor.close()

            response_text = json.dumps(body, ensure_ascii=False)
            self.assertEqual(error_code, 400)
            self.assertEqual(body["error"], "Resident process group failed to recover: details redacted.")
            self.assertEqual(body["group_id"], "crew")
            self.assertNotIn("live-agents.secret.json", response_text)
            self.assertNotIn(str(missing_config), response_text)


    def test_live_agent_process_stop_keeps_safe_not_found_error_body(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            supervisor = LiveAgentProcessSupervisor(root)
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root, process_supervisor=supervisor))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/live-agent-processes/missing-group/stop",
                    data=b"{}",
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                try:
                    urlopen(request, timeout=4)
                except HTTPError as error:
                    error_code = error.code
                    body = json.loads(error.read().decode("utf-8"))
                    error.close()
                else:
                    self.fail("missing group should return HTTP 400")
            finally:
                server.shutdown()
                server.server_close()
                supervisor.close()

            self.assertEqual(error_code, 400)
            self.assertEqual(body["error"], "Live agent group missing-group was not found.")
            self.assertEqual(body["group_id"], "missing-group")


    def test_live_agent_process_start_records_invalid_json_operation(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/live-agent-processes/start",
                    data=b"{not json",
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                try:
                    urlopen(request, timeout=4)
                except HTTPError as error:
                    error_code = error.code
                    error.read()
                    error.close()
                else:
                    self.fail("invalid JSON should return HTTP 400")
                with urlopen(
                    f"http://127.0.0.1:{server.server_port}/api/live-agent-operations",
                    timeout=4,
                ) as response:
                    operations = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

            self.assertEqual(error_code, 400)
            self.assertEqual(operations["operations"][0]["operation"], "process.start")
            self.assertEqual(operations["operations"][0]["status"], "failed")
            self.assertEqual(operations["operations"][0]["error"], "Invalid JSON")


    def test_live_agent_process_start_records_invalid_utf8_operation(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/live-agent-processes/start",
                    data=b"\xff",
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                try:
                    urlopen(request, timeout=4)
                except HTTPError as error:
                    error_code = error.code
                    error.read()
                    error.close()
                else:
                    self.fail("invalid UTF-8 should return HTTP 400")
                with urlopen(
                    f"http://127.0.0.1:{server.server_port}/api/live-agent-operations",
                    timeout=4,
                ) as response:
                    operations = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

            self.assertEqual(error_code, 400)
            self.assertEqual(operations["operations"][0]["operation"], "process.start")
            self.assertEqual(operations["operations"][0]["status"], "failed")
            self.assertEqual(operations["operations"][0]["error"], "Invalid JSON")


    def test_live_agent_process_restart_returns_400_when_preflight_fails_without_launch(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            runs_dir = root / "live-agent-runs"
            runs_dir.mkdir()
            config_path = root / "live-agents.json"
            config_path.write_text(
                '{"agents": [{"agent_id": "bad-agent", "command": ["definitely-missing-agentsassemble-cli"]}]}',
                encoding="utf-8",
            )
            (runs_dir / "processes.json").write_text(
                json.dumps(
                    {
                        "groups": [
                            {
                                "group_id": "crew",
                                "status": "stopped",
                                "pid": None,
                                "config_path": str(config_path),
                                "server": "http://room.local",
                                "log_path": str(runs_dir / "crew.log"),
                                "started_at": "2026-05-17T12:00:00+00:00",
                                "stopped_at": "2026-05-17T12:01:00+00:00",
                                "returncode": 0,
                                "last_error": "",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )

            def command_factory(command, **kwargs):
                raise AssertionError("preflight failure must not launch a process")

            supervisor = LiveAgentProcessSupervisor(root, command_factory=command_factory)
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root, process_supervisor=supervisor))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/live-agent-processes/crew/restart",
                    data=b"{}",
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                try:
                    urlopen(request, timeout=4)
                except HTTPError as error:
                    error_code = error.code
                    body = error.read().decode("utf-8")
                    error.close()
                else:
                    self.fail("preflight failure should return HTTP 400")
                with urlopen(
                    f"http://127.0.0.1:{server.server_port}/api/live-agent-operations",
                    timeout=4,
                ) as response:
                    operations = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()
                supervisor.close()

            persisted = json.loads((runs_dir / "processes.json").read_text(encoding="utf-8"))

            self.assertEqual(error_code, 400)
            self.assertIn("Live agent preflight failed", body)
            self.assertIn("bad-agent command", body)
            self.assertFalse((runs_dir / "crew.log").exists())
            self.assertEqual(persisted["groups"][0]["status"], "stopped")
            self.assertEqual(persisted["groups"][0]["pid"], None)
            self.assertEqual(operations["operations"][0]["operation"], "process.restart")
            self.assertEqual(operations["operations"][0]["status"], "failed")
            self.assertEqual(operations["operations"][0]["target_id"], "crew")
            self.assertIn("Live agent preflight failed", operations["operations"][0]["error"])


    def test_live_agent_process_recover_records_safe_operation(self):
        class FakeRecoverySupervisor:
            def __init__(self):
                self.recovered = []
                self.group = {
                    "group_id": "crew",
                    "status": "unknown",
                    "pid": None,
                    "config_path": "/private/live-agents.json",
                    "server": "http://secret-room.local",
                    "auto_restart": True,
                    "restart_count": 1,
                    "max_restarts": 3,
                    "recovered_from_status": "unknown",
                    "agents": [{"agent_id": "local-a", "display_name": "Local A", "connection_kind": "local_cli"}],
                }

            def recover_group(self, group_id):
                self.recovered.append(group_id)
                recovered = dict(self.group)
                recovered["status"] = "running"
                recovered["pid"] = 6789
                recovered["recent_events"] = [
                    {"event_type": "recovered", "status": "running", "previous_status": "unknown"}
                ]
                self.group = recovered
                return recovered

            def list_groups(self):
                return [self.group]

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            supervisor = FakeRecoverySupervisor()
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root, process_supervisor=supervisor))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/live-agent-processes/crew/recover",
                    data=b"{}",
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urlopen(request, timeout=4) as response:
                    recovered = json.loads(response.read().decode("utf-8"))
                with urlopen(
                    f"http://127.0.0.1:{server.server_port}/api/live-agent-operations?limit=10",
                    timeout=4,
                ) as response:
                    operations = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

            self.assertEqual(supervisor.recovered, ["crew"])
            self.assertEqual(recovered["group"]["status"], "running")
            self.assertEqual(recovered["group"]["recovered_from_status"], "unknown")
            self.assertEqual(operations["operations"][0]["operation"], "process.recover")
            self.assertEqual(operations["operations"][0]["status"], "success")
            self.assertEqual(operations["operations"][0]["details"]["previous_status"], "unknown")
            operation_text = json.dumps(operations, ensure_ascii=False)
            self.assertNotIn("/private/live-agents.json", operation_text)
            self.assertNotIn("secret-room.local", operation_text)
