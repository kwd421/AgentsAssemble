from tests.gui_server_test_support import (
    HTTPError,
    Path,
    Request,
    ThreadingHTTPServer,
    _make_handler,
    connect_live_agent,
    heartbeat_live_agent,
    json,
    tempfile,
    threading,
    unittest,
    urlopen,
)


class GuiServerRosterTests(unittest.TestCase):

    def test_live_agent_register_operation_records_bound_meeting_admission(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            meeting_dir = root / "meetings" / "resident-m1"
            meeting_dir.mkdir(parents=True)
            (meeting_dir / "meeting.json").write_text(
                json.dumps(
                    {
                        "meeting_id": "resident-m1",
                        "agent_bindings": [
                            {
                                "agent_id": "agent-a",
                                "role_id": "architect",
                                "provider_id": "local-cli",
                                "permission_profile_id": "meeting_readonly",
                                "join_mode": "fresh",
                            }
                        ],
                        "provider_configs": {
                            "local-cli": {
                                "id": "local-cli",
                                "kind": "local_cli",
                                "display_name": "Local CLI",
                            }
                        },
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
                    f"http://127.0.0.1:{server.server_port}/api/live-agents",
                    data=json.dumps(
                        {
                            "agent_id": "agent-a",
                            "display_name": "Agent A",
                            "provider_kind": "local_cli",
                            "connection_kind": "local_cli",
                            "meeting_id": "resident-m1",
                            "session_id": "private-session-id",
                        }
                    ).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urlopen(request, timeout=4) as response:
                    response.read()
                with urlopen(f"http://127.0.0.1:{server.server_port}/api/live-agent-operations", timeout=4) as response:
                    operations = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

            register_operation = next(item for item in operations["operations"] if item["operation"] == "live_agent.register")
            details = register_operation["details"]
            self.assertEqual(details["admission_status"], "bound_to_meeting")
            self.assertTrue(details["host_approved_binding"])
            self.assertEqual(details["binding_role_id"], "architect")
            self.assertEqual(details["binding_provider_id"], "local-cli")
            self.assertEqual(details["binding_provider_kind"], "local_cli")
            self.assertEqual(details["binding_permission_profile_id"], "meeting_readonly")
            self.assertEqual(details["binding_join_mode"], "fresh")
            self.assertNotIn("session_id", details)
            self.assertNotIn("private-session-id", json.dumps(operations, ensure_ascii=False))


    def test_live_agent_register_operation_records_unbound_and_conflicting_meeting_admission(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            meeting_dir = root / "meetings" / "resident-m1"
            meeting_dir.mkdir(parents=True)
            (meeting_dir / "meeting.json").write_text(
                json.dumps(
                    {
                        "meeting_id": "resident-m1",
                        "agent_bindings": [
                            {
                                "agent_id": "agent-a",
                                "role_id": "architect",
                                "provider_id": "local-cli",
                                "permission_profile_id": "meeting_readonly",
                            }
                        ],
                        "provider_configs": {"local-cli": {"id": "local-cli", "kind": "local_cli"}},
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                for payload in (
                    {
                        "agent_id": "guest-agent",
                        "display_name": "Guest Agent",
                        "provider_kind": "manual",
                        "connection_kind": "manual",
                        "meeting_id": "resident-m1",
                    },
                    {
                        "agent_id": "agent-a",
                        "display_name": "Agent A",
                        "provider_kind": "manual",
                        "connection_kind": "manual",
                        "meeting_id": "resident-m1",
                    },
                ):
                    request = Request(
                        f"http://127.0.0.1:{server.server_port}/api/live-agents",
                        data=json.dumps(payload).encode("utf-8"),
                        headers={"Content-Type": "application/json"},
                        method="POST",
                    )
                    with urlopen(request, timeout=4) as response:
                        response.read()
                with urlopen(f"http://127.0.0.1:{server.server_port}/api/live-agent-operations", timeout=4) as response:
                    operations = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

            by_target = {
                item["target_id"]: item["details"]
                for item in operations["operations"]
                if item["operation"] == "live_agent.register"
            }
            self.assertEqual(by_target["guest-agent"]["admission_status"], "meeting_lobby_only")
            self.assertFalse(by_target["guest-agent"]["host_approved_binding"])
            self.assertEqual(by_target["agent-a"]["admission_status"], "binding_conflict")
            self.assertFalse(by_target["agent-a"]["host_approved_binding"])
            self.assertEqual(by_target["agent-a"]["binding_role_id"], "architect")
            self.assertEqual(by_target["agent-a"]["binding_conflicts"], ["provider_kind_mismatch"])


    def test_live_agent_register_operation_records_missing_meeting_and_missing_provider_admission(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            meeting_dir = root / "meetings" / "resident-m1"
            meeting_dir.mkdir(parents=True)
            (meeting_dir / "meeting.json").write_text(
                json.dumps(
                    {
                        "meeting_id": "resident-m1",
                        "agent_bindings": [
                            {
                                "agent_id": "agent-b",
                                "role_id": "critic",
                                "provider_id": "missing-provider",
                                "permission_profile_id": "meeting_readonly",
                            }
                        ],
                        "provider_configs": {},
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                for payload in (
                    {
                        "agent_id": "lost-agent",
                        "display_name": "Lost Agent",
                        "provider_kind": "manual",
                        "connection_kind": "manual",
                        "meeting_id": "missing-meeting",
                        "session_id": "lost-secret-session",
                        "endpoint": "https://secret.example/leak",
                        "auth_ref": "literal:top-secret",
                        "config_path": "/Users/me/private-config.json",
                        "prompt": "hidden prompt phrase",
                        "provider_output": "raw-provider-output",
                    },
                    {
                        "agent_id": "agent-b",
                        "display_name": "Agent B",
                        "provider_kind": "local_cli",
                        "connection_kind": "local_cli",
                        "meeting_id": "resident-m1",
                    },
                ):
                    request = Request(
                        f"http://127.0.0.1:{server.server_port}/api/live-agents",
                        data=json.dumps(payload).encode("utf-8"),
                        headers={"Content-Type": "application/json"},
                        method="POST",
                    )
                    with urlopen(request, timeout=4) as response:
                        response.read()
                with urlopen(f"http://127.0.0.1:{server.server_port}/api/live-agent-operations", timeout=4) as response:
                    operations = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

            by_target = {
                item["target_id"]: item["details"]
                for item in operations["operations"]
                if item["operation"] == "live_agent.register"
            }
            self.assertEqual(by_target["lost-agent"]["admission_status"], "meeting_missing")
            self.assertFalse(by_target["lost-agent"]["host_approved_binding"])
            self.assertEqual(by_target["agent-b"]["admission_status"], "binding_conflict")
            self.assertFalse(by_target["agent-b"]["host_approved_binding"])
            self.assertEqual(by_target["agent-b"]["binding_provider_id"], "missing-provider")
            self.assertEqual(by_target["agent-b"]["binding_conflicts"], ["binding_provider_missing"])
            persisted = json.dumps(operations, ensure_ascii=False)
            self.assertNotIn("lost-secret-session", persisted)
            self.assertNotIn("https://secret.example/leak", persisted)
            self.assertNotIn("literal:top-secret", persisted)
            self.assertNotIn("/Users/me/private-config.json", persisted)
            self.assertNotIn("hidden prompt phrase", persisted)
            self.assertNotIn("raw-provider-output", persisted)


    def test_live_agent_http_endpoint_records_invalid_registration_json_operation(self):
        invalid_payloads = [
            ("malformed", b"{not json"),
            ("non_object", json.dumps(["not", "an", "object"]).encode("utf-8")),
            ("invalid_utf8", b"\xff"),
        ]
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                for _, body in invalid_payloads:
                    request = Request(
                        f"http://127.0.0.1:{server.server_port}/api/live-agents",
                        data=body,
                        headers={"Content-Type": "application/json"},
                        method="POST",
                    )
                    try:
                        urlopen(request, timeout=4)
                    except HTTPError as error:
                        self.assertEqual(error.code, 400)
                        error.read()
                        error.close()
                    else:
                        self.fail("invalid registration JSON should return HTTP 400")
                with urlopen(f"http://127.0.0.1:{server.server_port}/api/live-agent-operations", timeout=4) as response:
                    operations = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

            register_operations = [
                item for item in operations["operations"] if item["operation"] == "live_agent.register"
            ]
            self.assertEqual(len(register_operations), len(invalid_payloads))
            for operation in register_operations:
                self.assertEqual(operation["status"], "failed")
                self.assertEqual(operation["error"], "Invalid JSON")
                self.assertEqual(operation["target_id"], "")
                self.assertNotIn("session_id", operation.get("details", {}))
            operation_text = json.dumps(operations, ensure_ascii=False)
            self.assertNotIn("not json", operation_text)
            self.assertNotIn("not\", \"an\", \"object", operation_text)


    def test_live_agent_join_brief_http_endpoint_returns_safe_packet_without_registering(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                server_url = f"http://127.0.0.1:{server.server_port}"
                before_paths = sorted(path.relative_to(root).as_posix() for path in root.rglob("*"))
                request = Request(
                    f"{server_url}/api/live-agent-join-brief",
                    data=json.dumps(
                        {
                            "agent_id": "external-reviewer",
                            "display_name": "External Reviewer",
                            "provider_kind": "manual",
                            "connection_kind": "manual",
                            "meeting_id": "resident-m1",
                            "engagement_mode": "watch",
                            "timeout": 9,
                            "poll_interval": 0.5,
                            "max_chain_depth": 2,
                        }
                    ).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urlopen(request, timeout=4) as response:
                    payload = json.loads(response.read().decode("utf-8"))
                with urlopen(f"{server_url}/api/live-agents", timeout=4) as response:
                    roster = json.loads(response.read().decode("utf-8"))
                after_paths = sorted(path.relative_to(root).as_posix() for path in root.rglob("*"))
            finally:
                server.shutdown()
                server.server_close()

            self.assertEqual(payload["status"], "generated")
            self.assertEqual(payload["agent"]["agent_id"], "external-reviewer")
            self.assertEqual(payload["agent"]["meeting_id"], "resident-m1")
            self.assertIn("admission_contract", payload)
            self.assertEqual(payload["admission_contract"]["host_admission"], "required_before_room_access")
            self.assertEqual(payload["admission_contract"]["identity_proof"], "not_included_in_join_brief")
            self.assertEqual(payload["admission_contract"]["registration_effect"], "not_registered_until_commands_register_runs")
            self.assertEqual(payload["commands"]["register"][5:7], ["--server", server_url])
            self.assertIn("wait-next", payload["commands"]["wait_next"])
            self.assertIn("read-since", payload["commands"]["read_since"])
            self.assertIn("--max-chain-depth", payload["commands"]["wait_next"])
            self.assertIn("2", payload["commands"]["wait_next"])
            self.assertEqual(payload["commands"]["leave"][5:7], ["--server", server_url])
            self.assertIn("leave", payload["commands"]["leave"])
            self.assertIn("For observe_lobby actions, run the returned ack_command and do not post a reply.", payload["instructions"])
            self.assertIn("Run commands.leave before intentionally exiting the room.", payload["instructions"])
            self.assertEqual(payload["templates"]["say"][-2:], ["--", "{message}"])
            self.assertEqual(payload["safety"]["room_contacted"], False)
            self.assertEqual(payload["safety"]["provider_executed"], False)
            self.assertEqual(roster["agents"], [])
            self.assertEqual(after_paths, before_paths)
            self.assertFalse((root / "live-agent-runs" / "operations.jsonl").exists())
            serialized = json.dumps(payload)
            self.assertNotIn("endpoint", serialized)
            self.assertNotIn("auth", serialized)
            self.assertNotIn("session_id", serialized)
            self.assertNotIn("config_path", serialized)
            self.assertNotIn("log_path", serialized)


    def test_live_agent_join_brief_http_endpoint_rejects_nested_values_before_echo(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/live-agent-join-brief",
                    data=json.dumps(
                        {
                            "agent_id": "external-reviewer",
                            "display_name": {"session_id": "secret-session", "prompt": "private prompt"},
                            "provider_kind": ["auth_ref=TOKEN", "provider output"],
                            "server": {"endpoint": "https://example.invalid/private", "config_path": "/tmp/private.json"},
                            "meeting_id": {"reply": "private reply"},
                        }
                    ).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with self.assertRaises(HTTPError) as raised:
                    urlopen(request, timeout=4)
                with urlopen(f"http://127.0.0.1:{server.server_port}/api/live-agents", timeout=4) as response:
                    roster = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

            self.assertEqual(raised.exception.code, 400)
            body = raised.exception.read().decode("utf-8")
            raised.exception.close()
            self.assertNotIn("secret-session", body)
            self.assertNotIn("TOKEN", body)
            self.assertNotIn("private prompt", body)
            self.assertNotIn("private reply", body)
            self.assertEqual(roster["agents"], [])
            self.assertFalse((root / "live-agent-runs" / "operations.jsonl").exists())


    def test_live_agent_http_endpoint_filters_roster_by_meeting_agent_and_status(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
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
            connect_live_agent(
                root,
                {
                    "agent_id": "agent-b",
                    "display_name": "Agent B",
                    "provider_kind": "local_cli",
                    "connection_kind": "local_cli",
                    "meeting_id": "resident-m1",
                },
            )
            connect_live_agent(
                root,
                {
                    "agent_id": "agent-c",
                    "display_name": "Agent C",
                    "provider_kind": "local_cli",
                    "connection_kind": "local_cli",
                    "meeting_id": "resident-m2",
                },
            )
            heartbeat_live_agent(root, "agent-a", status="working")
            heartbeat_live_agent(root, "agent-c", status="working")
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with urlopen(
                    (
                        f"http://127.0.0.1:{server.server_port}/api/live-agents"
                        "?meeting_id=resident-m1&agent_id=agent-a&status=working"
                    ),
                    timeout=4,
                ) as response:
                    listed = json.loads(response.read().decode("utf-8"))
                with urlopen(
                    f"http://127.0.0.1:{server.server_port}/api/live-agents?meeting_id=resident-m1&status=working",
                    timeout=4,
                ) as response:
                    meeting_working = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

            self.assertEqual([agent["agent_id"] for agent in listed["agents"]], ["agent-a"])
            self.assertEqual([agent["agent_id"] for agent in meeting_working["agents"]], ["agent-a"])


    def test_live_agent_http_endpoint_safe_projection_redacts_roster_fields(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            connect_live_agent(
                root,
                {
                    "agent_id": "agent-a",
                    "display_name": "Agent A",
                    "provider_kind": "remote_bridge",
                    "connection_kind": "remote_bridge",
                    "meeting_id": "resident-m1",
                    "session_id": "private-session",
                    "endpoint": "https://secret.local:8777/bridge",
                    "last_error": "token=secret-token config /Users/seinel/private/live-agents.json",
                },
            )
            state_path = root / "live_agents.json"
            state = json.loads(state_path.read_text(encoding="utf-8"))
            state["agents"][0]["auth_ref"] = "env:SECRET_TOKEN"
            state["agents"][0]["config_path"] = "/Users/seinel/private/live-agents.json"
            state["agents"][0]["join_semantics"] = "codex_exec_resume"
            state["agents"][0]["context_durability"] = "provider_managed_resume"
            state_path.write_text(json.dumps(state), encoding="utf-8")
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with urlopen(f"http://127.0.0.1:{server.server_port}/api/live-agents?safe=1", timeout=4) as response:
                    listed = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

            agent = listed["agents"][0]
            self.assertEqual(agent["agent_id"], "agent-a")
            self.assertEqual(agent["meeting_id"], "resident-m1")
            self.assertEqual(agent["join_semantics"], "remote_bridge_room_loop")
            self.assertEqual(agent["context_durability"], "remote_owner_managed")
            self.assertEqual(agent["last_error"], "Live-agent presence error details redacted.")
            self.assertNotIn("session_id", agent)
            self.assertNotIn("endpoint", agent)
            self.assertNotIn("auth_ref", agent)
            self.assertNotIn("config_path", agent)
            encoded = json.dumps(listed)
            self.assertNotIn("secret.local", encoded)
            self.assertNotIn("secret-token", encoded)
            self.assertNotIn("private-session", encoded)
            self.assertNotIn("live-agents.json", encoded)


    def test_live_agent_http_endpoint_safe_projection_derives_admission_evidence(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            meeting_dir = root / "meetings" / "resident-m1"
            meeting_dir.mkdir(parents=True)
            (meeting_dir / "meeting.json").write_text(
                json.dumps(
                    {
                        "meeting_id": "resident-m1",
                        "agent_bindings": [
                            {
                                "agent_id": "agent-a",
                                "role_id": "architect",
                                "provider_id": "local-cli",
                                "permission_profile_id": "meeting_readonly",
                                "join_mode": "resident",
                            },
                            {
                                "agent_id": "agent-b",
                                "role_id": "critic",
                                "provider_id": "local-cli",
                                "permission_profile_id": "meeting_readonly",
                            },
                        ],
                        "provider_configs": {
                            "local-cli": {
                                "id": "local-cli",
                                "kind": "local_cli",
                                "display_name": "Local CLI",
                            }
                        },
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            connect_live_agent(
                root,
                {
                    "agent_id": "agent-a",
                    "display_name": "Agent A",
                    "provider_kind": "local_cli",
                    "connection_kind": "local_cli",
                    "meeting_id": "resident-m1",
                    "session_id": "private-session-a",
                },
            )
            connect_live_agent(
                root,
                {
                    "agent_id": "agent-b",
                    "display_name": "Agent B",
                    "provider_kind": "manual",
                    "connection_kind": "manual",
                    "meeting_id": "resident-m1",
                    "session_id": "private-session-b",
                },
            )
            connect_live_agent(
                root,
                {
                    "agent_id": "guest-agent",
                    "display_name": "Guest Agent",
                    "provider_kind": "manual",
                    "connection_kind": "manual",
                    "meeting_id": "resident-m1",
                    "session_id": "private-session-c",
                },
            )
            state_path = root / "live_agents.json"
            state = json.loads(state_path.read_text(encoding="utf-8"))
            state["agents"][2]["admission_status"] = "bound_to_meeting"
            state["agents"][2]["host_approved_binding"] = True
            state["agents"][2]["admission_evidence_source"] = "meeting_record"
            state["agents"][2]["binding_role_id"] = "spoofed"
            state["agents"][0]["binding_conflicts"] = ["provider_kind_mismatch"]
            state_path.write_text(json.dumps(state), encoding="utf-8")
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with urlopen(
                    f"http://127.0.0.1:{server.server_port}/api/live-agents?safe=1&meeting_id=resident-m1",
                    timeout=4,
                ) as response:
                    listed = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

            by_agent = {agent["agent_id"]: agent for agent in listed["agents"]}
            self.assertEqual(by_agent["agent-a"]["admission_status"], "bound_to_meeting")
            self.assertEqual(by_agent["agent-a"]["admission_evidence_source"], "meeting_record")
            self.assertTrue(by_agent["agent-a"]["host_approved_binding"])
            self.assertEqual(by_agent["agent-a"]["binding_role_id"], "architect")
            self.assertEqual(by_agent["agent-a"]["binding_provider_id"], "local-cli")
            self.assertEqual(by_agent["agent-a"]["binding_provider_kind"], "local_cli")
            self.assertEqual(by_agent["agent-a"]["binding_permission_profile_id"], "meeting_readonly")
            self.assertEqual(by_agent["agent-a"]["binding_join_mode"], "resident")
            self.assertNotIn("binding_conflicts", by_agent["agent-a"])
            self.assertEqual(by_agent["agent-b"]["admission_status"], "binding_conflict")
            self.assertFalse(by_agent["agent-b"]["host_approved_binding"])
            self.assertEqual(by_agent["agent-b"]["binding_conflicts"], ["provider_kind_mismatch"])
            self.assertEqual(by_agent["guest-agent"]["admission_status"], "meeting_lobby_only")
            self.assertEqual(by_agent["guest-agent"]["admission_evidence_source"], "meeting_record")
            self.assertFalse(by_agent["guest-agent"]["host_approved_binding"])
            self.assertNotIn("binding_role_id", by_agent["guest-agent"])
            encoded = json.dumps(listed, ensure_ascii=False)
            self.assertNotIn("private-session", encoded)
            self.assertNotIn("spoofed", encoded)


    def test_live_agent_http_endpoint_safe_projection_refuses_live_state_only_admission(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            meeting_dir = root / "meetings" / "resident-m1"
            meeting_dir.mkdir(parents=True)
            (meeting_dir / "live_state.json").write_text(
                json.dumps(
                    {
                        "meeting_id": "resident-m1",
                        "agent_bindings": [
                            {
                                "agent_id": "agent-a",
                                "role_id": "architect",
                                "provider_id": "local-cli",
                                "permission_profile_id": "meeting_readonly",
                            }
                        ],
                        "provider_configs": {
                            "local-cli": {
                                "id": "local-cli",
                                "kind": "local_cli",
                            }
                        },
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            connect_live_agent(
                root,
                {
                    "agent_id": "agent-a",
                    "display_name": "Agent A",
                    "provider_kind": "local_cli",
                    "connection_kind": "local_cli",
                    "meeting_id": "resident-m1",
                    "session_id": "private-session-a",
                },
            )
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with urlopen(f"http://127.0.0.1:{server.server_port}/api/live-agents?safe=1", timeout=4) as response:
                    listed = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

            agent = listed["agents"][0]
            self.assertEqual(agent["admission_status"], "meeting_missing")
            self.assertEqual(agent["admission_evidence_source"], "meeting_record")
            self.assertFalse(agent["host_approved_binding"])
            self.assertNotIn("binding_role_id", agent)
            self.assertNotIn("binding_provider_id", agent)
            self.assertNotIn("private-session", json.dumps(listed, ensure_ascii=False))


    def test_live_agent_http_endpoint_safe_projection_combines_with_filters(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            connect_live_agent(
                root,
                {
                    "agent_id": "agent-a",
                    "display_name": "Agent A",
                    "provider_kind": "local_cli",
                    "connection_kind": "local_cli",
                    "meeting_id": "resident-m1",
                    "session_id": "private-a",
                },
            )
            connect_live_agent(
                root,
                {
                    "agent_id": "agent-b",
                    "display_name": "Agent B",
                    "provider_kind": "local_cli",
                    "connection_kind": "local_cli",
                    "meeting_id": "resident-m2",
                    "session_id": "private-b",
                },
            )
            connect_live_agent(
                root,
                {
                    "agent_id": "agent-c",
                    "display_name": "Agent C",
                    "provider_kind": "local_cli",
                    "connection_kind": "local_cli",
                    "meeting_id": "resident-m1",
                    "session_id": "private-c",
                },
            )
            heartbeat_live_agent(root, "agent-a", status="working")
            heartbeat_live_agent(root, "agent-b", status="working")
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with urlopen(
                    (
                        f"http://127.0.0.1:{server.server_port}/api/live-agents"
                        "?safe=1&meeting_id=resident-m1"
                        "&agent_id=agent-a&agent_id=agent-c"
                        "&status=online&status=working"
                    ),
                    timeout=4,
                ) as response:
                    listed = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

            self.assertEqual([agent["agent_id"] for agent in listed["agents"]], ["agent-a", "agent-c"])
            self.assertEqual([agent["status"] for agent in listed["agents"]], ["working", "online"])
            self.assertNotIn("session_id", listed["agents"][0])
            self.assertNotIn("session_id", listed["agents"][1])


    def test_live_agent_http_endpoint_filters_inferred_stale_status(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            connect_live_agent(
                root,
                {
                    "agent_id": "stale-agent",
                    "display_name": "Stale Agent",
                    "provider_kind": "local_cli",
                    "connection_kind": "local_cli",
                    "meeting_id": "resident-m1",
                },
            )
            state_path = root / "live_agents.json"
            state = json.loads(state_path.read_text(encoding="utf-8"))
            state["agents"][0]["last_seen_at"] = "2000-01-01T00:00:00+00:00"
            state_path.write_text(json.dumps(state), encoding="utf-8")
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with urlopen(
                    f"http://127.0.0.1:{server.server_port}/api/live-agents?status=stale",
                    timeout=4,
                ) as response:
                    listed = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

            self.assertEqual([agent["agent_id"] for agent in listed["agents"]], ["stale-agent"])
            self.assertEqual(listed["agents"][0]["status"], "stale")
