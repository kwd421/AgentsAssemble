from tests.gui_server_test_support import (
    Path,
    Request,
    StringIO,
    ThreadingHTTPServer,
    _make_handler,
    connect_live_agent_payload,
    json,
    live_agents_payload,
    patch,
    serve_gui,
    tempfile,
    threading,
    unittest,
    urlopen,
)


class GuiServerLifecycleTests(unittest.TestCase):

    def test_serve_gui_rejects_non_loopback_bind_without_explicit_unsafe_opt_in(self):
        with self.assertRaisesRegex(ValueError, "Direct non-loopback GUI bind is disabled"):
            serve_gui(host="0.0.0.0", port=0)

    def test_serve_gui_closes_live_agent_process_supervisor(self):
        class FakeServer:
            def __init__(self, address, handler):
                self.address = address
                self.handler = handler
                self.server_address = (address[0], 43210 if address[1] == 0 else address[1])
                self.closed = False

            def serve_forever(self):
                raise KeyboardInterrupt

            def server_close(self):
                self.closed = True

        class FakeSupervisor:
            instances = []

            def __init__(self, output_root):
                self.output_root = output_root
                self.closed = False
                self.monitor_started = False
                self.instances.append(self)

            def start_monitor(self):
                self.monitor_started = True

            def close(self):
                self.closed = True

        with tempfile.TemporaryDirectory() as temp_dir:
            servers = []

            def server_factory(address, handler):
                server = FakeServer(address, handler)
                servers.append(server)
                return server

            with patch("agentsassemble.gui.LiveAgentProcessSupervisor", FakeSupervisor):
                with patch("agentsassemble.gui.ThreadingHTTPServer", server_factory):
                    with patch("sys.stdout", StringIO()):
                        serve_gui(host="127.0.0.1", port=0, output_root=Path(temp_dir))

        self.assertTrue(servers[0].closed)
        self.assertTrue(FakeSupervisor.instances[0].monitor_started)
        self.assertTrue(FakeSupervisor.instances[0].closed)


    def test_serve_gui_does_not_autostart_without_explicit_config(self):
        class FakeServer:
            def __init__(self, address, handler):
                self.server_address = (address[0], 43210 if address[1] == 0 else address[1])
                self.closed = False

            def serve_forever(self):
                raise KeyboardInterrupt

            def server_close(self):
                self.closed = True

        class FakeSupervisor:
            instances = []

            def __init__(self, output_root):
                self.started = []
                self.closed = False
                self.instances.append(self)

            def start_monitor(self):
                return None

            def start_group(self, **kwargs):
                self.started.append(kwargs)
                return {"group_id": kwargs.get("group_id") or "group", "status": "running"}

            def close(self):
                self.closed = True

        with tempfile.TemporaryDirectory() as temp_dir:
            servers = []

            def server_factory(address, handler):
                server = FakeServer(address, handler)
                servers.append(server)
                return server

            with patch("agentsassemble.gui.LiveAgentProcessSupervisor", FakeSupervisor):
                with patch("agentsassemble.gui.ThreadingHTTPServer", server_factory):
                    with patch("sys.stdout", StringIO()):
                        serve_gui(host="127.0.0.1", port=0, output_root=Path(temp_dir))

        self.assertEqual(FakeSupervisor.instances[0].started, [])
        self.assertTrue(servers[0].closed)
        self.assertTrue(FakeSupervisor.instances[0].closed)


    def test_serve_gui_autostarts_explicit_live_agent_config_after_server_bind(self):
        class FakeServer:
            def __init__(self, address, handler):
                self.server_address = (address[0], 45678 if address[1] == 0 else address[1])
                self.closed = False

            def serve_forever(self):
                raise KeyboardInterrupt

            def server_close(self):
                self.closed = True

        class FakeSupervisor:
            instances = []

            def __init__(self, output_root):
                self.output_root = output_root
                self.started = []
                self.closed = False
                self.instances.append(self)

            def start_monitor(self):
                return None

            def start_group(self, **kwargs):
                self.started.append(kwargs)
                return {"group_id": kwargs.get("group_id") or "group", "status": "running"}

            def close(self):
                self.closed = True

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = root / "live-agents.json"
            config_path.write_text('{"agents": []}', encoding="utf-8")
            servers = []

            def server_factory(address, handler):
                server = FakeServer(address, handler)
                servers.append(server)
                return server

            with patch("agentsassemble.gui.LiveAgentProcessSupervisor", FakeSupervisor):
                with patch("agentsassemble.gui.ThreadingHTTPServer", server_factory):
                    with patch("sys.stdout", StringIO()):
                        serve_gui(
                            host="127.0.0.1",
                            port=0,
                            output_root=root,
                            live_agent_config=config_path,
                            live_agent_group_id="boot",
                            live_agent_auto_restart=True,
                            live_agent_max_restarts=3,
                            live_agent_restart_backoff_seconds=1.5,
                            live_agent_stale_restart_after_seconds=120,
                        )

            operations = json.loads((root / "live-agent-runs" / "operations.jsonl").read_text(encoding="utf-8").splitlines()[-1])

        self.assertEqual(len(FakeSupervisor.instances[0].started), 1)
        started = FakeSupervisor.instances[0].started[0]
        self.assertEqual(started["config_path"], config_path)
        self.assertEqual(started["server"], "http://127.0.0.1:45678")
        self.assertEqual(started["group_id"], "boot")
        self.assertTrue(started["auto_restart"])
        self.assertEqual(started["max_restarts"], 3)
        self.assertEqual(started["restart_backoff_seconds"], 1.5)
        self.assertEqual(started["stale_restart_after_seconds"], 120)
        self.assertEqual(operations["operation"], "process.autostart")
        self.assertEqual(operations["status"], "success")
        self.assertEqual(operations["target_id"], "boot")
        self.assertEqual(operations["details"]["stale_restart_after_seconds"], 120)
        self.assertTrue(servers[0].closed)
        self.assertTrue(FakeSupervisor.instances[0].closed)


    def test_serve_gui_records_failed_autostart_and_still_serves(self):
        class FakeServer:
            def __init__(self, address, handler):
                self.server_address = (address[0], 45679 if address[1] == 0 else address[1])
                self.served = False
                self.closed = False

            def serve_forever(self):
                self.served = True
                raise KeyboardInterrupt

            def server_close(self):
                self.closed = True

        class FakeSupervisor:
            instances = []

            def __init__(self, output_root):
                self.closed = False
                self.instances.append(self)

            def start_monitor(self):
                return None

            def start_group(self, **kwargs):
                raise ValueError("Live agent config /Users/me/private/live-agents.json was not found.")

            def close(self):
                self.closed = True

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = root / "missing-live-agents.json"
            servers = []

            def server_factory(address, handler):
                server = FakeServer(address, handler)
                servers.append(server)
                return server

            with patch("agentsassemble.gui.LiveAgentProcessSupervisor", FakeSupervisor):
                with patch("agentsassemble.gui.ThreadingHTTPServer", server_factory):
                    with patch("sys.stdout", StringIO()):
                        serve_gui(
                            host="127.0.0.1",
                            port=0,
                            output_root=root,
                            live_agent_config=config_path,
                            live_agent_group_id="boot",
                        )

            operations = json.loads((root / "live-agent-runs" / "operations.jsonl").read_text(encoding="utf-8").splitlines()[-1])
            persisted = json.dumps(operations, ensure_ascii=False)

        self.assertTrue(servers[0].served)
        self.assertTrue(servers[0].closed)
        self.assertTrue(FakeSupervisor.instances[0].closed)
        self.assertEqual(operations["operation"], "process.autostart")
        self.assertEqual(operations["status"], "failed")
        self.assertEqual(operations["target_id"], "boot")
        self.assertIn("Live agent config", operations["error"])
        self.assertNotIn("/Users/me/private", persisted)


    def test_serve_gui_cleans_up_when_monitor_start_fails(self):
        class FakeServer:
            def __init__(self, address, handler):
                self.server_address = (address[0], 43210 if address[1] == 0 else address[1])
                self.closed = False

            def serve_forever(self):
                raise AssertionError("serve_forever should not run after monitor startup failure")

            def server_close(self):
                self.closed = True

        class FakeSupervisor:
            instances = []

            def __init__(self, output_root):
                self.closed = False
                self.instances.append(self)

            def start_monitor(self):
                raise RuntimeError("monitor failed")

            def close(self):
                self.closed = True

        with tempfile.TemporaryDirectory() as temp_dir:
            servers = []

            def server_factory(address, handler):
                server = FakeServer(address, handler)
                servers.append(server)
                return server

            with patch("agentsassemble.gui.LiveAgentProcessSupervisor", FakeSupervisor):
                with patch("agentsassemble.gui.ThreadingHTTPServer", server_factory):
                    with patch("sys.stdout", StringIO()):
                        with self.assertRaises(RuntimeError):
                            serve_gui(host="127.0.0.1", port=0, output_root=Path(temp_dir))

        self.assertTrue(servers[0].closed)
        self.assertTrue(FakeSupervisor.instances[0].closed)


    def test_live_agent_payload_registers_non_codex_presence(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)

            payload = connect_live_agent_payload(
                root,
                {
                    "agent_id": "claude-code-live",
                    "display_name": "Claude Code Live",
                    "provider_kind": "claude_code",
                    "connection_kind": "local_cli",
                    "engagement_mode": "mentioned",
                    "meeting_id": "m1",
                },
            )

            self.assertEqual(payload["agent"]["agent_id"], "claude-code-live")
            self.assertEqual(payload["agent"]["provider_kind"], "claude_code")
            self.assertEqual(payload["agent"]["connection_kind"], "local_cli")
            self.assertEqual(payload["agent"]["join_semantics"], "stateless_prompt_call")
            self.assertEqual(payload["agent"]["context_durability"], "stateless_prompt")
            self.assertEqual(payload["agent"]["sandbox_enforcement"], "advisory")
            self.assertEqual(live_agents_payload(root)["agents"][0]["display_name"], "Claude Code Live")


    def test_live_agent_http_endpoint_registers_and_lists_presence(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/live-agents",
                    data=json.dumps(
                        {
                            "agent_id": "gemini-cli",
                            "display_name": "Gemini CLI",
                            "provider_kind": "gemini",
                            "connection_kind": "local_cli",
                            "session_id": "gemini-session",
                            "join_semantics": "env:SECRET_TOKEN",
                            "context_durability": "/private/provider-context",
                            "sandbox_enforcement": "os_sandboxed",
                            "quota_5h": "private-5h",
                            "quota_windows": [{"label": "5-hour", "percent": 50}],
                        }
                    ).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urlopen(request, timeout=4) as response:
                    payload = json.loads(response.read().decode("utf-8"))
                with urlopen(f"http://127.0.0.1:{server.server_port}/api/live-agents", timeout=4) as response:
                    listed = json.loads(response.read().decode("utf-8"))
                with urlopen(f"http://127.0.0.1:{server.server_port}/api/live-agents?safe=0", timeout=4) as response:
                    explicit_raw = json.loads(response.read().decode("utf-8"))
                with urlopen(f"http://127.0.0.1:{server.server_port}/api/live-agent-operations", timeout=4) as response:
                    operations = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

            self.assertEqual(payload["agent"]["agent_id"], "gemini-cli")
            self.assertEqual(listed["agents"][0]["session_id"], "gemini-session")
            self.assertEqual(explicit_raw["agents"][0]["session_id"], "gemini-session")
            self.assertNotIn("quota_5h", listed["agents"][0])
            self.assertNotIn("quota_windows", listed["agents"][0])
            self.assertNotIn("quota_5h", explicit_raw["agents"][0])
            self.assertNotIn("quota_windows", explicit_raw["agents"][0])
            self.assertIsInstance(listed["agents"][0]["heartbeat_age_seconds"], int)
            self.assertGreaterEqual(listed["agents"][0]["heartbeat_age_seconds"], 0)
            self.assertEqual(listed["agents"][0]["stale_after_seconds"], 180)
            register_operations = [item for item in operations["operations"] if item["operation"] == "live_agent.register"]
            self.assertEqual(len(register_operations), 1)
            self.assertEqual(register_operations[0]["status"], "success")
            self.assertEqual(register_operations[0]["target_id"], "gemini-cli")
            self.assertEqual(register_operations[0]["details"]["agent_id"], "gemini-cli")
            self.assertEqual(register_operations[0]["details"]["provider_kind"], "gemini")
            self.assertEqual(register_operations[0]["details"]["connection_kind"], "local_cli")
            self.assertEqual(register_operations[0]["details"]["join_semantics"], "stateless_prompt_call")
            self.assertEqual(register_operations[0]["details"]["context_durability"], "stateless_prompt")
            self.assertEqual(register_operations[0]["details"]["sandbox_enforcement"], "advisory")
            self.assertEqual(register_operations[0]["details"]["registered_status"], "online")
            self.assertEqual(register_operations[0]["details"]["admission_status"], "lobby_only")
            self.assertFalse(register_operations[0]["details"]["host_approved_binding"])
            self.assertNotIn("session_id", register_operations[0]["details"])
            self.assertNotIn("gemini-session", json.dumps(operations, ensure_ascii=False))
            self.assertNotIn("SECRET_TOKEN", json.dumps(operations, ensure_ascii=False))
            self.assertNotIn("/private", json.dumps(operations, ensure_ascii=False))
