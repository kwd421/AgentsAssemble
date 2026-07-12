from tests.gui_server_test_support import (
    LiveAgentSmokeFailed,
    Path,
    Request,
    ThreadingHTTPServer,
    _make_handler,
    json,
    patch,
    sys,
    tempfile,
    threading,
    unittest,
    urlopen,
)


class GuiServerProcessSmokeTests(unittest.TestCase):

    def test_live_agent_readiness_endpoint_rejects_negative_session_smoke_soak_payload(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "room"
            root.mkdir()
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with (
                    patch("agentsassemble.gui.run_live_agent_smoke", return_value={"status": "ok", "group_id": "doctor-smoke"}),
                    patch("agentsassemble.gui.run_live_agent_session_smoke") as session_smoke,
                ):
                    request = Request(
                        f"http://127.0.0.1:{server.server_port}/api/live-agent-readiness",
                        data=json.dumps(
                            {
                                "group_id": "doctor-smoke",
                                "session_smoke": True,
                                "session_smoke_soak_cycle_count": -1,
                                "session_smoke_soak_interval_seconds": -0.5,
                            }
                        ).encode("utf-8"),
                        headers={"Content-Type": "application/json"},
                        method="POST",
                    )
                    with urlopen(request, timeout=12) as response:
                        payload = json.loads(response.read().decode("utf-8"))
                with urlopen(
                    f"http://127.0.0.1:{server.server_port}/api/live-agent-operations?limit=20",
                    timeout=4,
                ) as response:
                    operations = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

        session_smoke.assert_not_called()
        self.assertEqual(payload["status"], "failed")
        self.assertEqual(payload["session_smoke"]["status"], "failed")
        self.assertEqual(payload["session_smoke"]["error"], "session smoke could not be run")
        readiness_operations = [operation for operation in operations["operations"] if operation["operation"] == "readiness.check"]
        self.assertEqual(readiness_operations[-1]["status"], "failed")


    def test_live_agent_readiness_endpoint_skips_session_smoke_when_base_smoke_fails(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "room"
            root.mkdir()
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with (
                    patch("agentsassemble.gui.run_live_agent_smoke", side_effect=LiveAgentSmokeFailed("Timed out")),
                    patch("agentsassemble.gui.run_live_agent_session_smoke") as session_smoke,
                ):
                    request = Request(
                        f"http://127.0.0.1:{server.server_port}/api/live-agent-readiness",
                        data=json.dumps({"group_id": "doctor-smoke", "timeout": 8, "session_smoke": True}).encode("utf-8"),
                        headers={"Content-Type": "application/json", "Host": "127.0.0.1:1"},
                        method="POST",
                    )
                    with urlopen(request, timeout=12) as response:
                        payload = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

        self.assertEqual(payload["status"], "failed")
        self.assertEqual(payload["session_smoke"]["status"], "skipped")
        self.assertEqual(payload["session_smoke"]["reason"], "smoke did not pass")
        session_smoke.assert_not_called()


    def test_live_agent_readiness_endpoint_sanitizes_official_round_smoke_failure(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "room"
            root.mkdir()
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with (
                    patch("agentsassemble.gui.run_live_agent_smoke", return_value={"status": "ok", "group_id": "doctor-smoke", "replies": []}),
                    patch(
                        "agentsassemble.gui.run_live_agent_official_round_smoke",
                        side_effect=ValueError("config_path=/Users/me/private-live-agents.json token=SECRET"),
                    ),
                ):
                    request = Request(
                        f"http://127.0.0.1:{server.server_port}/api/live-agent-readiness",
                        data=json.dumps(
                            {
                                "group_id": "doctor-smoke",
                                "timeout": 8,
                                "official_round_smoke": True,
                            }
                        ).encode("utf-8"),
                        headers={"Content-Type": "application/json", "Host": "127.0.0.1:1"},
                        method="POST",
                    )
                    with urlopen(request, timeout=12) as response:
                        payload = json.loads(response.read().decode("utf-8"))
                with urlopen(
                    f"http://127.0.0.1:{server.server_port}/api/live-agent-operations?limit=20",
                    timeout=4,
                ) as response:
                    operations = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

        self.assertEqual(payload["status"], "failed")
        self.assertEqual(payload["official_round_smoke"]["status"], "failed")
        self.assertEqual(payload["official_round_smoke"]["error"], "official round smoke could not be run")
        serialized_payload = json.dumps(payload, ensure_ascii=False)
        serialized_operations = json.dumps(operations, ensure_ascii=False)
        for secret in ("SECRET", "private-live-agents", "/Users/me", "config_path", "token="):
            self.assertNotIn(secret, serialized_payload)
            self.assertNotIn(secret, serialized_operations)


    def test_live_agent_readiness_endpoint_sanitizes_official_round_smoke_error_result(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "room"
            root.mkdir()
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with (
                    patch("agentsassemble.gui.run_live_agent_smoke", return_value={"status": "ok", "group_id": "doctor-smoke", "replies": []}),
                    patch(
                        "agentsassemble.gui.run_live_agent_official_round_smoke",
                        return_value={
                            "status": "failed",
                            "group_id": "doctor-smoke",
                            "error": "config_path=/Users/me/private-live-agents.json token=SECRET",
                        },
                    ),
                ):
                    request = Request(
                        f"http://127.0.0.1:{server.server_port}/api/live-agent-readiness",
                        data=json.dumps(
                            {
                                "group_id": "doctor-smoke",
                                "timeout": 8,
                                "official_round_smoke": True,
                            }
                        ).encode("utf-8"),
                        headers={"Content-Type": "application/json", "Host": "127.0.0.1:1"},
                        method="POST",
                    )
                    with urlopen(request, timeout=12) as response:
                        payload = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

        self.assertEqual(payload["status"], "failed")
        self.assertEqual(payload["official_round_smoke"]["error"], "official round smoke could not be run")
        serialized_payload = json.dumps(payload, ensure_ascii=False)
        for secret in ("SECRET", "private-live-agents", "/Users/me", "config_path", "token="):
            self.assertNotIn(secret, serialized_payload)


    def test_live_agent_readiness_endpoint_skips_official_round_smoke_when_base_smoke_fails(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "room"
            root.mkdir()
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with (
                    patch("agentsassemble.gui.run_live_agent_smoke", side_effect=LiveAgentSmokeFailed("Timed out")),
                    patch("agentsassemble.gui.run_live_agent_official_round_smoke") as official_smoke,
                ):
                    request = Request(
                        f"http://127.0.0.1:{server.server_port}/api/live-agent-readiness",
                        data=json.dumps(
                            {
                                "group_id": "doctor-smoke",
                                "timeout": 8,
                                "official_round_smoke": True,
                            }
                        ).encode("utf-8"),
                        headers={"Content-Type": "application/json", "Host": "127.0.0.1:1"},
                        method="POST",
                    )
                    with urlopen(request, timeout=12) as response:
                        payload = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

        self.assertEqual(payload["status"], "failed")
        self.assertEqual(payload["official_round_smoke"]["status"], "skipped")
        self.assertEqual(payload["official_round_smoke"]["reason"], "smoke did not pass")
        official_smoke.assert_not_called()


    def test_live_agent_readiness_endpoint_refuses_too_many_targeted_probes(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "room"
            root.mkdir()
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            agent_ids = [f"agent-{index}" for index in range(11)]
            try:
                with (
                    patch("agentsassemble.gui.run_live_agent_smoke", return_value={"status": "ok", "group_id": "doctor-smoke", "replies": []}),
                    patch("agentsassemble.gui.run_live_agent_probe") as probe,
                ):
                    request = Request(
                        f"http://127.0.0.1:{server.server_port}/api/live-agent-readiness",
                        data=json.dumps(
                            {
                                "group_id": "doctor-smoke",
                                "timeout": 8,
                                "probe_agent_ids": agent_ids,
                            }
                        ).encode("utf-8"),
                        headers={"Content-Type": "application/json", "Host": "127.0.0.1:1"},
                        method="POST",
                    )
                    with urlopen(request, timeout=12) as response:
                        payload = json.loads(response.read().decode("utf-8"))
                with urlopen(
                    f"http://127.0.0.1:{server.server_port}/api/live-agent-operations?limit=20",
                    timeout=4,
                ) as response:
                    operations = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

        self.assertEqual(payload["status"], "failed")
        self.assertEqual(payload["probe_error"], "Too many probe agents requested; maximum is 10.")
        self.assertEqual(
            {check["id"]: check["status"] for check in payload["checks"]},
            {"health": "ok", "smoke": "ok", "probe_request_limit": "failed"},
        )
        probe.assert_not_called()
        readiness_operations = [
            operation for operation in operations["operations"] if operation["operation"] == "readiness.check"
        ]
        self.assertEqual(readiness_operations[-1]["status"], "failed")
        self.assertEqual(readiness_operations[-1]["details"]["probe_agent_ids"], agent_ids)
        self.assertEqual(readiness_operations[-1]["details"]["probe_error"], "Too many probe agents requested; maximum is 10.")


    def test_live_agent_readiness_endpoint_sanitizes_smoke_payload(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "room"
            root.mkdir()
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            smoke_result = {
                "status": "ok",
                "group_id": "doctor-smoke",
                "agent_ids": ["smoke-local"],
                "source_event_id": "smoke-source",
                "started_group": {
                    "config_path": "/Users/me/private-live-agents.json",
                    "server": "http://127.0.0.1:8765",
                    "log_path": "/Users/me/.agentsassemble/live-agent-runs/doctor-smoke.log",
                    "log_tail": "secret log tail",
                },
                "stopped_group": {
                    "config_path": "/Users/me/private-live-agents.json",
                    "server": "http://127.0.0.1:8765",
                    "log_tail": "secret stopped log tail",
                },
                "replies": [{"actor_id": "smoke-local", "message": "secret smoke reply"}],
            }
            try:
                with patch("agentsassemble.gui.run_live_agent_smoke", return_value=smoke_result):
                    request = Request(
                        f"http://127.0.0.1:{server.server_port}/api/live-agent-readiness",
                        data=json.dumps({"group_id": "doctor-smoke", "timeout": 8}).encode("utf-8"),
                        headers={"Content-Type": "application/json", "Host": "127.0.0.1:1"},
                        method="POST",
                    )
                    with urlopen(request, timeout=12) as response:
                        payload = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

        self.assertEqual(
            payload["smoke"],
            {
                "status": "ok",
                "group_id": "doctor-smoke",
                "agent_ids": ["smoke-local"],
                "reply_count": 1,
            },
        )
        serialized_payload = json.dumps(payload, ensure_ascii=False)
        self.assertNotIn("smoke-source", serialized_payload)
        self.assertNotIn("private-live-agents", serialized_payload)
        self.assertNotIn("127.0.0.1:8765", serialized_payload)
        self.assertNotIn("log_tail", serialized_payload)
        self.assertNotIn("secret smoke reply", serialized_payload)


    def test_live_agent_readiness_endpoint_expands_probe_group_manifest(self):
        class FakeSupervisor:
            def snapshot_groups(self):
                return [
                    {
                        "group_id": "resident-main",
                        "status": "running",
                        "agents": [
                            {"agent_id": "agent-a", "display_name": "Agent A"},
                            {"agent_id": "agent-b", "display_name": "Agent B"},
                        ],
                    }
                ]

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "room"
            root.mkdir()
            (root / "live_agents.json").write_text(
                json.dumps(
                    {
                        "agents": [
                            {"agent_id": "agent-a", "display_name": "Agent A", "status": "online"},
                            {"agent_id": "agent-b", "display_name": "Agent B", "status": "online"},
                        ]
                    }
                ),
                encoding="utf-8",
            )
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root, process_supervisor=FakeSupervisor()))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            probe_results = [
                {"status": "ok", "agent_id": "agent-a", "source_event_id": "probe-source-a", "reply_event_id": "reply-a"},
                {"status": "ok", "agent_id": "agent-b", "source_event_id": "probe-source-b", "reply_event_id": "reply-b"},
            ]
            try:
                with (
                    patch("agentsassemble.gui.run_live_agent_smoke", return_value={"status": "ok", "group_id": "doctor-smoke", "replies": []}),
                    patch("agentsassemble.gui.run_live_agent_probe", side_effect=probe_results) as probe,
                ):
                    request = Request(
                        f"http://127.0.0.1:{server.server_port}/api/live-agent-readiness",
                        data=json.dumps(
                            {
                                "group_id": "doctor-smoke",
                                "timeout": 8,
                                "probe_agent_ids": ["agent-a"],
                                "probe_group_ids": ["resident-main"],
                            }
                        ).encode("utf-8"),
                        headers={"Content-Type": "application/json", "Host": "127.0.0.1:1"},
                        method="POST",
                    )
                    with urlopen(request, timeout=12) as response:
                        payload = json.loads(response.read().decode("utf-8"))
                with urlopen(
                    f"http://127.0.0.1:{server.server_port}/api/live-agent-operations?limit=20",
                    timeout=4,
                ) as response:
                    operations = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

        self.assertEqual(payload["status"], "ready")
        self.assertEqual(
            {check["id"]: check["status"] for check in payload["checks"]},
            {
                "health": "ok",
                "smoke": "ok",
                "probe_group:resident-main": "ok",
                "probe:agent-a": "ok",
                "probe:agent-b": "ok",
            },
        )
        self.assertEqual(
            payload["probe_groups"],
            [{"status": "ok", "group_id": "resident-main", "agent_ids": ["agent-a", "agent-b"]}],
        )
        self.assertEqual([call.args[:2] for call in probe.call_args_list], [(root, "agent-a"), (root, "agent-b")])
        readiness_operations = [
            operation for operation in operations["operations"] if operation["operation"] == "readiness.check"
        ]
        self.assertEqual(readiness_operations[-1]["details"]["probe_group_ids"], ["resident-main"])
        self.assertEqual(readiness_operations[-1]["details"]["effective_probe_agent_ids"], ["agent-a", "agent-b"])


    def test_live_agent_readiness_endpoint_refuses_invalid_probe_groups_without_probe_side_effects(self):
        class FakeSupervisor:
            def snapshot_groups(self):
                return [
                    {"group_id": "stopped-group", "status": "stopped", "agents": [{"agent_id": "agent-a"}]},
                    {"group_id": "empty-group", "status": "running", "agents": []},
                ]

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "room"
            root.mkdir()
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root, process_supervisor=FakeSupervisor()))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with (
                    patch("agentsassemble.gui.run_live_agent_smoke", return_value={"status": "ok", "group_id": "doctor-smoke", "replies": []}),
                    patch("agentsassemble.gui.run_live_agent_probe") as probe,
                ):
                    request = Request(
                        f"http://127.0.0.1:{server.server_port}/api/live-agent-readiness",
                        data=json.dumps(
                            {
                                "group_id": "doctor-smoke",
                                "timeout": 8,
                                "probe_group_ids": ["stopped-group", "missing-group", "empty-group"],
                            }
                        ).encode("utf-8"),
                        headers={"Content-Type": "application/json", "Host": "127.0.0.1:1"},
                        method="POST",
                    )
                    with urlopen(request, timeout=12) as response:
                        payload = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

        self.assertEqual(payload["status"], "failed")
        self.assertEqual(
            payload["probe_groups"],
            [
                {"status": "failed", "group_id": "stopped-group", "reason": "group is not running"},
                {"status": "failed", "group_id": "missing-group", "reason": "group was not found"},
                {"status": "failed", "group_id": "empty-group", "reason": "group has no manifest agents"},
            ],
        )
        self.assertEqual(
            {check["id"]: check["status"] for check in payload["checks"]},
            {
                "health": "degraded",
                "smoke": "ok",
                "probe_group:stopped-group": "failed",
                "probe_group:missing-group": "failed",
                "probe_group:empty-group": "failed",
            },
        )
        probe.assert_not_called()


    def test_live_agent_readiness_endpoint_refuses_probe_groups_over_agent_cap_without_probe_side_effects(self):
        class FakeSupervisor:
            def snapshot_groups(self):
                return [
                    {
                        "group_id": "large-group",
                        "status": "running",
                        "agents": [{"agent_id": f"agent-{index}"} for index in range(11)],
                    }
                ]

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "room"
            root.mkdir()
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root, process_supervisor=FakeSupervisor()))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with (
                    patch("agentsassemble.gui.run_live_agent_smoke", return_value={"status": "ok", "group_id": "doctor-smoke", "replies": []}),
                    patch("agentsassemble.gui.run_live_agent_probe") as probe,
                ):
                    request = Request(
                        f"http://127.0.0.1:{server.server_port}/api/live-agent-readiness",
                        data=json.dumps(
                            {
                                "group_id": "doctor-smoke",
                                "timeout": 8,
                                "probe_group_ids": ["large-group"],
                            }
                        ).encode("utf-8"),
                        headers={"Content-Type": "application/json", "Host": "127.0.0.1:1"},
                        method="POST",
                    )
                    with urlopen(request, timeout=12) as response:
                        payload = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

        self.assertEqual(payload["status"], "failed")
        self.assertEqual(payload["probe_error"], "Too many probe agents requested; maximum is 10.")
        self.assertEqual(payload["probe_groups"][0]["status"], "ok")
        self.assertEqual(payload["probe_groups"][0]["agent_count"], 11)
        self.assertNotIn("agent_ids", payload["probe_groups"][0])
        self.assertNotIn("effective_probe_agent_ids", payload)
        probe.assert_not_called()


    def test_live_agent_readiness_endpoint_refuses_malformed_probe_ids_without_echoing_payload(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "room"
            root.mkdir()
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with (
                    patch("agentsassemble.gui.run_live_agent_smoke", return_value={"status": "ok", "group_id": "doctor-smoke", "replies": []}),
                    patch("agentsassemble.gui.run_live_agent_probe") as probe,
                ):
                    request = Request(
                        f"http://127.0.0.1:{server.server_port}/api/live-agent-readiness",
                        data=json.dumps(
                            {
                                "group_id": "doctor-smoke",
                                "timeout": 8,
                                "probe_agent_ids": [{"config_path": "/Users/me/private.json"}],
                                "probe_group_ids": [{"endpoint": "http://secret.local", "auth_ref": "env:SECRET_TOKEN"}],
                            }
                        ).encode("utf-8"),
                        headers={"Content-Type": "application/json", "Host": "127.0.0.1:1"},
                        method="POST",
                    )
                    with urlopen(request, timeout=12) as response:
                        payload = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

        self.assertEqual(payload["status"], "failed")
        self.assertEqual(payload["probe_error"], "Invalid probe id payload; expected a list of strings.")
        self.assertEqual(
            {check["id"]: check["status"] for check in payload["checks"]},
            {"health": "ok", "smoke": "ok", "probe_request_payload": "failed"},
        )
        probe.assert_not_called()
        serialized_payload = json.dumps(payload, ensure_ascii=False)
        self.assertNotIn("private.json", serialized_payload)
        self.assertNotIn("secret.local", serialized_payload)
        self.assertNotIn("SECRET_TOKEN", serialized_payload)


    def test_live_agent_preflight_endpoint_checks_config_without_starting_processes(self):
        class FakeSupervisor:
            def __init__(self):
                self.started = False

            def start_group(self, **kwargs):
                self.started = True
                raise AssertionError("preflight must not start process groups")

            def snapshot_groups(self):
                return []

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "room"
            root.mkdir()
            config_path = root / "live-agents.json"
            config_path.write_text(
                json.dumps(
                    {
                        "agents": [
                            {
                                "agent_id": "preflight-agent",
                                "provider_kind": "local_cli",
                                "connection_kind": "local_cli",
                                "command": [sys.executable, "-c", "print('not executed')"],
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            supervisor = FakeSupervisor()
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root, process_supervisor=supervisor))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/live-agent-preflight",
                    data=json.dumps({"config_path": str(config_path)}).encode("utf-8"),
                    headers={"Content-Type": "application/json", "Host": "127.0.0.1:1"},
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

            self.assertFalse(supervisor.started)
            self.assertEqual(payload["status"], "ok")
            self.assertEqual(payload["server"], "http://127.0.0.1:1")
            self.assertEqual(payload["summary"], {"agents": 1, "failed_agents": 0, "checks_failed": 0})
            self.assertEqual(payload["agents"][0]["agent_id"], "preflight-agent")
            self.assertEqual(operations["operations"][0]["operation"], "preflight.check")
            self.assertEqual(operations["operations"][0]["status"], "success")
            self.assertEqual(operations["operations"][0]["details"]["result_status"], "ok")


    def test_live_agent_preflight_endpoint_redacts_sensitive_config_failure_payload(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "room"
            root.mkdir()
            missing_config = root / "private" / "live-agents.secret.json"
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/live-agent-preflight",
                    data=json.dumps({"config_path": str(missing_config)}).encode("utf-8"),
                    headers={"Content-Type": "application/json", "Host": "127.0.0.1:1"},
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

            self.assertEqual(payload["status"], "failed")
            self.assertEqual(payload["config_path"], "[redacted]")
            self.assertEqual(payload["checks"][0]["message"], "Config load failed: details redacted.")
            serialized_payload = json.dumps(payload, ensure_ascii=False)
            serialized_operations = json.dumps(operations, ensure_ascii=False)
            self.assertNotIn(str(missing_config), serialized_payload)
            self.assertNotIn("live-agents.secret.json", serialized_payload)
            self.assertNotIn(str(missing_config), serialized_operations)
            self.assertNotIn("live-agents.secret.json", serialized_operations)


    def test_live_agent_preflight_endpoint_redacts_malformed_config_failure_payload(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "room"
            root.mkdir()
            config_path = root / "live-agents.json"
            config_path.write_text("{", encoding="utf-8")
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/live-agent-preflight",
                    data=json.dumps({"config_path": str(config_path)}).encode("utf-8"),
                    headers={"Content-Type": "application/json", "Host": "127.0.0.1:1"},
                    method="POST",
                )
                with urlopen(request, timeout=4) as response:
                    payload = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

            self.assertEqual(payload["status"], "failed")
            self.assertEqual(payload["config_path"], "[redacted]")
            self.assertEqual(payload["checks"][0]["message"], "Config load failed: details redacted.")
            serialized_payload = json.dumps(payload, ensure_ascii=False)
            self.assertNotIn(str(config_path), serialized_payload)
            self.assertNotIn("Expecting", serialized_payload)
            self.assertNotIn("line 1", serialized_payload)
            self.assertNotIn("char 0", serialized_payload)
