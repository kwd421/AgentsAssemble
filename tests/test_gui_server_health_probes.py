from tests.gui_server_test_support import (
    HTTPError,
    LiveAgentProcessSupervisor,
    Path,
    Request,
    ThreadingHTTPServer,
    _make_handler,
    json,
    patch,
    tempfile,
    threading,
    unittest,
    urlopen,
)


class GuiServerHealthTests(unittest.TestCase):

    def test_live_agent_probe_endpoint_records_success_without_message_text(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            result = {
                "status": "ok",
                "agent_id": "agent-a",
                "source_event_id": "probe-source",
                "reply_event_id": "reply-event",
                "reply": {"id": "reply-event", "actor_id": "agent-a", "message": "secret probe reply"},
            }
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root, process_supervisor=LiveAgentProcessSupervisor(root)))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with patch("agentsassemble.gui.run_live_agent_probe", return_value=result) as probe:
                    request = Request(
                        f"http://127.0.0.1:{server.server_port}/api/live-agents/agent-a/probe",
                        data=json.dumps({"timeout_seconds": 3}).encode("utf-8"),
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

        probe.assert_called_once()
        self.assertEqual(probe.call_args.args[:2], (root, "agent-a"))
        self.assertEqual(probe.call_args.kwargs["timeout_seconds"], 3.0)
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["reply_event_id"], "reply-event")
        self.assertEqual(operations["operations"][0]["operation"], "probe.run")
        self.assertEqual(operations["operations"][0]["status"], "success")
        self.assertEqual(operations["operations"][0]["target_id"], "agent-a")
        self.assertEqual(operations["operations"][0]["details"]["result_status"], "ok")
        self.assertEqual(operations["operations"][0]["details"]["timeout_seconds"], 3.0)
        operation_text = json.dumps(operations, ensure_ascii=False)
        self.assertNotIn("secret probe reply", operation_text)


    def test_live_agent_probe_endpoint_records_effective_timeout_cap(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            result = {"status": "timeout", "agent_id": "agent-a", "source_event_id": "probe-source"}
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root, process_supervisor=LiveAgentProcessSupervisor(root)))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with patch("agentsassemble.gui.run_live_agent_probe", return_value=result) as probe:
                    request = Request(
                        f"http://127.0.0.1:{server.server_port}/api/live-agents/agent-a/probe",
                        data=json.dumps({"timeout_seconds": 300}).encode("utf-8"),
                        headers={"Content-Type": "application/json"},
                        method="POST",
                    )
                    with urlopen(request, timeout=4) as response:
                        json.loads(response.read().decode("utf-8"))
                with urlopen(
                    f"http://127.0.0.1:{server.server_port}/api/live-agent-operations",
                    timeout=4,
                ) as response:
                    operations = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

        self.assertEqual(probe.call_args.kwargs["timeout_seconds"], 240.0)
        self.assertEqual(operations["operations"][0]["details"]["timeout_seconds"], 240.0)


    def test_live_agent_probe_endpoint_records_timeout_and_unknown_agent(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root, process_supervisor=LiveAgentProcessSupervisor(root)))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                timeout_result = {"status": "timeout", "agent_id": "agent-a", "source_event_id": "probe-source"}
                with patch("agentsassemble.gui.run_live_agent_probe", return_value=timeout_result):
                    request = Request(
                        f"http://127.0.0.1:{server.server_port}/api/live-agents/agent-a/probe",
                        data=b"{}",
                        headers={"Content-Type": "application/json"},
                        method="POST",
                    )
                    with urlopen(request, timeout=4) as response:
                        payload = json.loads(response.read().decode("utf-8"))
                with patch("agentsassemble.gui.run_live_agent_probe", side_effect=ValueError("Live agent missing was not found.")):
                    missing_request = Request(
                        f"http://127.0.0.1:{server.server_port}/api/live-agents/missing/probe",
                        data=json.dumps({"timeout_seconds": 300}).encode("utf-8"),
                        headers={"Content-Type": "application/json"},
                        method="POST",
                    )
                    with self.assertRaises(HTTPError) as error:
                        urlopen(missing_request, timeout=4)
                    error.exception.read()
                    error.exception.close()
                with urlopen(
                    f"http://127.0.0.1:{server.server_port}/api/live-agent-operations",
                    timeout=4,
                ) as response:
                    operations = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

        self.assertEqual(payload["status"], "timeout")
        self.assertEqual(error.exception.code, 404)
        self.assertEqual(
            [(operation["operation"], operation["status"], operation["target_id"]) for operation in operations["operations"]],
            [("probe.run", "failed", "agent-a"), ("probe.run", "failed", "missing")],
        )
        self.assertEqual(operations["operations"][1]["details"]["timeout_seconds"], 240.0)


    def test_live_agent_health_keeps_reused_legacy_smoke_group_visible(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            active_config = root / "active-live-agents.json"
            active_config.write_text('{"agents": []}', encoding="utf-8")
            (root / "live_agents.json").write_text(
                json.dumps(
                    {
                        "agents": [
                            {
                                "agent_id": "legacy-smoke-local-cli",
                                "display_name": "Smoke Local CLI",
                                "provider_kind": "local_cli",
                                "connection_kind": "local_cli",
                                "status": "offline",
                            },
                            {
                                "agent_id": "legacy-smoke-live-session",
                                "display_name": "Smoke Live Session",
                                "provider_kind": "local_cli",
                                "connection_kind": "live_session",
                                "status": "offline",
                            },
                            {
                                "agent_id": "partial-smoke-local-cli",
                                "display_name": "Smoke Local CLI",
                                "provider_kind": "local_cli",
                                "connection_kind": "local_cli",
                                "status": "offline",
                            },
                        ]
                    }
                ),
                encoding="utf-8",
            )

            class FakeSupervisor:
                def snapshot_groups(self):
                    return [
                        {
                            "group_id": "legacy-smoke",
                            "status": "error",
                            "returncode": 1,
                            "config_path": str(active_config),
                        },
                        {
                            "group_id": "partial-smoke",
                            "status": "stopped",
                            "returncode": 0,
                            "config_path": "/dev/null/partial-smoke/live-agents.json",
                        }
                    ]

            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root, process_supervisor=FakeSupervisor()))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with urlopen(f"http://127.0.0.1:{server.server_port}/api/live-agent-health", timeout=4) as response:
                    payload = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

            self.assertEqual(payload["agents"]["total"], 0)
            self.assertEqual(payload["processes"]["total"], 2)
            self.assertEqual(payload["processes"]["counts"]["error"], 1)
            self.assertEqual(payload["processes"]["counts"]["stopped"], 1)
            self.assertEqual(payload["processes"]["attention"], ["legacy-smoke", "partial-smoke"])
