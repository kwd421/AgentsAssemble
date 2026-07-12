from tests.gui_server_test_support import (
    LiveAgentProcessSupervisor,
    LiveAgentSessionRunController,
    Path,
    ThreadingHTTPServer,
    _make_handler,
    _write_health_resident_meeting,
    connect_live_agent,
    json,
    live_agent_health_payload,
    tempfile,
    threading,
    unittest,
    urlopen,
    write_live_state,
)


class GuiServerHealthTests(unittest.TestCase):

    def test_live_agent_processes_payload_includes_output_only_agent_connection_evidence(self):
        class FakeSupervisor:
            def __init__(self):
                self.groups = [
                    {
                        "group_id": "crew",
                        "status": "running",
                        "agents": [
                            {"agent_id": "agent-a", "display_name": "Agent A"},
                            {"agent_id": "agent-b", "display_name": "Agent B"},
                        ],
                    }
                ]

            def list_groups(self):
                return self.groups

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "live_agents.json").write_text(
                json.dumps({"agents": [{"agent_id": "agent-a", "display_name": "Agent A", "status": "online"}]}),
                encoding="utf-8",
            )
            supervisor = FakeSupervisor()
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root, process_supervisor=supervisor))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with urlopen(f"http://127.0.0.1:{server.server_port}/api/live-agent-processes", timeout=4) as response:
                    payload = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

        connection = payload["groups"][0]["agent_connection"]
        self.assertEqual(connection["expected"], 2)
        self.assertEqual(connection["connected"], 1)
        self.assertEqual(connection["attention"], [{"agent_id": "agent-b", "status": "missing"}])
        self.assertNotIn("agent_connection", supervisor.groups[0])


    def test_live_agent_process_connection_evidence_reports_wrong_meeting(self):
        class FakeSupervisor:
            def list_groups(self):
                return [
                    {
                        "group_id": "crew",
                        "status": "running",
                        "meeting_id": "resident-m1",
                        "agents": [{"agent_id": "agent-a", "display_name": "Agent A"}],
                    }
                ]

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "live_agents.json").write_text(
                json.dumps(
                    {
                        "agents": [
                            {
                                "agent_id": "agent-a",
                                "display_name": "Agent A",
                                "status": "online",
                                "meeting_id": "resident-m2",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root, process_supervisor=FakeSupervisor()))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with urlopen(f"http://127.0.0.1:{server.server_port}/api/live-agent-processes", timeout=4) as response:
                    payload = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

        connection = payload["groups"][0]["agent_connection"]
        self.assertEqual(connection["expected"], 1)
        self.assertEqual(connection["connected"], 0)
        self.assertEqual(connection["attention"], [{"agent_id": "agent-a", "status": "wrong_meeting"}])


    def test_live_agent_process_connection_evidence_reports_provider_kind_mismatch(self):
        class FakeSupervisor:
            def list_groups(self):
                return [
                    {
                        "group_id": "crew",
                        "status": "running",
                        "meeting_id": "resident-m1",
                        "agents": [
                            {
                                "agent_id": "agent-a",
                                "display_name": "Agent A",
                                "provider_kind": "local_cli",
                                "connection_kind": "local_cli",
                            }
                        ],
                    }
                ]

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "live_agents.json").write_text(
                json.dumps(
                    {
                        "agents": [
                            {
                                "agent_id": "agent-a",
                                "display_name": "Manual Agent A",
                                "status": "online",
                                "meeting_id": "resident-m1",
                                "provider_kind": "manual",
                                "connection_kind": "manual",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root, process_supervisor=FakeSupervisor()))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with urlopen(f"http://127.0.0.1:{server.server_port}/api/live-agent-processes", timeout=4) as response:
                    payload = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

        connection = payload["groups"][0]["agent_connection"]
        self.assertEqual(connection["expected"], 1)
        self.assertEqual(connection["connected"], 0)
        self.assertEqual(connection["attention"], [{"agent_id": "agent-a", "status": "provider_kind_mismatch"}])


    def test_live_agent_process_connection_evidence_requires_presence_after_group_start(self):
        class FakeSupervisor:
            def list_groups(self):
                return [
                    {
                        "group_id": "crew",
                        "status": "running",
                        "started_at": "2999-01-01T00:01:00+00:00",
                        "agents": [{"agent_id": "agent-a", "display_name": "Agent A"}],
                    }
                ]

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "live_agents.json").write_text(
                json.dumps(
                    {
                        "agents": [
                            {
                                "agent_id": "agent-a",
                                "display_name": "Agent A",
                                "status": "online",
                                "last_seen_at": "2999-01-01T00:00:00+00:00",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root, process_supervisor=FakeSupervisor()))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with urlopen(f"http://127.0.0.1:{server.server_port}/api/live-agent-processes", timeout=4) as response:
                    payload = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

        connection = payload["groups"][0]["agent_connection"]
        self.assertEqual(connection["expected"], 1)
        self.assertEqual(connection["connected"], 0)
        self.assertEqual(connection["attention"], [{"agent_id": "agent-a", "status": "not_reconnected"}])


    def test_live_agent_process_connection_evidence_is_not_persisted_by_real_supervisor(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            process_path = root / "live-agent-runs" / "processes.json"
            process_path.parent.mkdir(parents=True)
            process_path.write_text(
                json.dumps(
                    {
                        "groups": [
                            {
                                "group_id": "crew",
                                "status": "stopped",
                                "agents": [
                                    {"agent_id": "agent-a", "display_name": "Agent A"},
                                    {"agent_id": "agent-b", "display_name": "Agent B"},
                                ],
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            (root / "live_agents.json").write_text(
                json.dumps({"agents": [{"agent_id": "agent-a", "display_name": "Agent A", "status": "online"}]}),
                encoding="utf-8",
            )
            supervisor = LiveAgentProcessSupervisor(root)
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root, process_supervisor=supervisor))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with urlopen(f"http://127.0.0.1:{server.server_port}/api/live-agent-processes", timeout=4) as response:
                    payload = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()
                supervisor.close()
            persisted = json.loads(process_path.read_text(encoding="utf-8"))

        self.assertEqual(payload["groups"][0]["agent_connection"]["connected"], 1)
        self.assertNotIn("agent_connection", persisted["groups"][0])


    def test_live_agent_health_degrades_when_running_manifest_agent_has_connection_attention(self):
        class FakeSupervisor:
            def __init__(self):
                self.list_called = False

            def list_groups(self):
                self.list_called = True
                raise AssertionError("health endpoint must not refresh process groups")

            def snapshot_groups(self):
                return [
                    {
                        "group_id": "crew",
                        "status": "running",
                        "agents": [
                            {"agent_id": "agent-a", "display_name": "Agent A"},
                            {"agent_id": "agent-b", "display_name": "Agent B"},
                            {"agent_id": "agent-c", "display_name": "Agent C"},
                            {"agent_id": "agent-d", "display_name": "Agent D"},
                            {"agent_id": "agent-e", "display_name": "Agent E"},
                        ],
                    }
                ]

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "live_agents.json").write_text(
                json.dumps(
                    {
                        "agents": [
                            {"agent_id": "agent-a", "display_name": "Agent A", "status": "working"},
                            {
                                "agent_id": "agent-c",
                                "display_name": "Agent C",
                                "status": "online",
                                "last_seen_at": "2020-01-01T00:00:00+00:00",
                            },
                            {"agent_id": "agent-d", "display_name": "Agent D", "status": "offline"},
                            {"agent_id": "agent-e", "display_name": "Agent E", "status": "error"},
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
                with urlopen(f"http://127.0.0.1:{server.server_port}/api/live-agent-health", timeout=4) as response:
                    payload = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

        self.assertEqual(payload["status"], "degraded")
        self.assertEqual(payload["connections"]["expected"], 5)
        self.assertEqual(payload["connections"]["connected"], 1)
        self.assertEqual(
            payload["connections"]["attention"],
            [
                "crew:agent-b:missing",
                "crew:agent-c:stale",
                "crew:agent-d:offline",
                "crew:agent-e:error",
            ],
        )
        self.assertFalse(supervisor.list_called)


    def test_live_agent_health_degrades_when_manifest_agent_is_attached_to_wrong_meeting(self):
        class FakeSupervisor:
            def snapshot_groups(self):
                return [
                    {
                        "group_id": "crew",
                        "status": "running",
                        "meeting_id": "resident-m1",
                        "agents": [{"agent_id": "agent-a", "display_name": "Agent A"}],
                    }
                ]

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "live_agents.json").write_text(
                json.dumps(
                    {
                        "agents": [
                            {
                                "agent_id": "agent-a",
                                "display_name": "Agent A",
                                "status": "working",
                                "meeting_id": "resident-m2",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root, process_supervisor=FakeSupervisor()))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with urlopen(f"http://127.0.0.1:{server.server_port}/api/live-agent-health", timeout=4) as response:
                    payload = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

        self.assertEqual(payload["status"], "degraded")
        self.assertEqual(payload["connections"]["expected"], 1)
        self.assertEqual(payload["connections"]["connected"], 0)
        self.assertEqual(payload["connections"]["attention"], ["crew:agent-a:wrong_meeting"])


    def test_live_agent_health_degrades_when_manifest_agent_provider_mismatches_presence(self):
        class FakeSupervisor:
            def snapshot_groups(self):
                return [
                    {
                        "group_id": "crew",
                        "status": "running",
                        "meeting_id": "resident-m1",
                        "agents": [
                            {
                                "agent_id": "agent-a",
                                "display_name": "Agent A",
                                "provider_kind": "local_cli",
                                "connection_kind": "local_cli",
                            }
                        ],
                    }
                ]

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "live_agents.json").write_text(
                json.dumps(
                    {
                        "agents": [
                            {
                                "agent_id": "agent-a",
                                "display_name": "Manual Agent A",
                                "status": "online",
                                "meeting_id": "resident-m1",
                                "provider_kind": "manual",
                                "connection_kind": "manual",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root, process_supervisor=FakeSupervisor()))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with urlopen(f"http://127.0.0.1:{server.server_port}/api/live-agent-health", timeout=4) as response:
                    payload = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

        self.assertEqual(payload["status"], "degraded")
        self.assertEqual(payload["connections"]["expected"], 1)
        self.assertEqual(payload["connections"]["connected"], 0)
        self.assertEqual(payload["connections"]["attention"], ["crew:agent-a:provider_kind_mismatch"])


    def test_live_agent_health_sanitizes_connection_attention_id_labels(self):
        class FakeSupervisor:
            def snapshot_groups(self):
                return [
                    {
                        "group_id": "/tmp/secret-group.json",
                        "status": "running",
                        "agents": [{"agent_id": "/tmp/secret-agent.json", "display_name": "Agent A"}],
                    }
                ]

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "live_agents.json").write_text(json.dumps({"agents": []}), encoding="utf-8")
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root, process_supervisor=FakeSupervisor()))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with urlopen(f"http://127.0.0.1:{server.server_port}/api/live-agent-health", timeout=4) as response:
                    payload = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

        self.assertEqual(payload["connections"]["attention"], ["unknown:unknown:missing"])


    def test_live_agent_health_redacts_sensitive_process_and_session_owner_ids(self):
        sensitive_group_token = "ghp_abcdefghijklmnopqrstuvwxyz1234567890"

        class FakeSupervisor:
            def snapshot_groups(self):
                return [
                    {
                        "group_id": "literal:SECRET_GROUP",
                        "status": "running",
                        "meeting_id": "env:SECRET_TOKEN",
                        "agents": [{"agent_id": "agent-a", "display_name": "Agent A"}],
                    },
                    {
                        "group_id": sensitive_group_token,
                        "status": "error",
                        "meeting_id": "env:SECRET_TOKEN",
                        "recent_events": [
                            {
                                "event_type": "stale_watchdog",
                                "reason": "missing manifest agent agent-b",
                            }
                        ],
                        "agents": [{"agent_id": "agent-b", "display_name": "Agent B"}],
                    },
                ]

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "live_agents.json").write_text(json.dumps({"agents": []}), encoding="utf-8")

            payload = live_agent_health_payload(root, FakeSupervisor())

        serialized = json.dumps(payload, ensure_ascii=False)
        self.assertNotIn("SECRET_TOKEN", serialized)
        self.assertNotIn("SECRET_GROUP", serialized)
        self.assertNotIn("literal:", serialized)
        self.assertNotIn("env:", serialized)
        self.assertNotIn(sensitive_group_token, serialized)
        self.assertEqual(payload["processes"]["meeting_ids"], {})
        self.assertEqual(payload["processes"]["attention"], ["missing-process-group-id-2"])
        self.assertEqual(
            payload["processes"]["reasons"],
            {
                "missing-process-group-id-2": {
                    "event_type": "stale_watchdog",
                    "reason": "missing manifest agent agent-b",
                }
            },
        )
        self.assertEqual(payload["connections"]["attention"], ["unknown:agent-a:missing"])
        self.assertEqual(payload["sessions"]["items"], [])


    def test_live_agent_health_redacts_token_like_process_reasons(self):
        sensitive_agent_token = "ghp_abcdefghijklmnopqrstuvwxyz1234567890"

        class FakeSupervisor:
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
                        "agents": [{"agent_id": "agent-a", "display_name": "Agent A"}],
                    }
                ]

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _write_health_resident_meeting(root, agent_ids=["agent-a"])
            controller = LiveAgentSessionRunController(root)
            run = controller.begin_run(
                action="ensure",
                payload={"meeting_id": "resident-m1", "group_id": "resident-main"},
            )
            controller.finish_run(
                run["run_id"],
                session={
                    "status": "ready",
                    "meeting_id": "resident-m1",
                    "group_id": "resident-main",
                    "action": "none",
                },
            )

            payload = live_agent_health_payload(root, FakeSupervisor())

        serialized = json.dumps(payload, ensure_ascii=False)
        self.assertNotIn(sensitive_agent_token, serialized)
        self.assertEqual(payload["processes"]["reasons"], {})
        self.assertNotIn("process_reason", payload["sessions"]["items"][0])
        self.assertNotIn("process_reason", payload["session_runs"]["items"][0]["readiness"])


    def test_live_agent_health_degrades_when_manifest_agent_has_not_reconnected_after_group_start(self):
        class FakeSupervisor:
            def snapshot_groups(self):
                return [
                    {
                        "group_id": "crew",
                        "status": "running",
                        "started_at": "2999-01-01T00:01:00+00:00",
                        "agents": [{"agent_id": "agent-a", "display_name": "Agent A"}],
                    }
                ]

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "live_agents.json").write_text(
                json.dumps(
                    {
                        "agents": [
                            {
                                "agent_id": "agent-a",
                                "display_name": "Agent A",
                                "status": "working",
                                "last_seen_at": "2999-01-01T00:00:00+00:00",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root, process_supervisor=FakeSupervisor()))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with urlopen(f"http://127.0.0.1:{server.server_port}/api/live-agent-health", timeout=4) as response:
                    payload = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

        self.assertEqual(payload["status"], "degraded")
        self.assertEqual(payload["connections"]["expected"], 1)
        self.assertEqual(payload["connections"]["connected"], 0)
        self.assertEqual(payload["connections"]["attention"], ["crew:agent-a:not_reconnected"])


    def test_live_agent_health_ignores_diagnostic_connection_gaps(self):
        class FakeSupervisor:
            def snapshot_groups(self):
                return [
                    {
                        "group_id": "doctor-smoke",
                        "status": "running",
                        "diagnostic": True,
                        "agents": [{"agent_id": "diagnostic-missing", "display_name": "Diagnostic Missing"}],
                    }
                ]

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "live_agents.json").write_text(json.dumps({"agents": []}), encoding="utf-8")
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root, process_supervisor=FakeSupervisor()))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with urlopen(f"http://127.0.0.1:{server.server_port}/api/live-agent-health", timeout=4) as response:
                    payload = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["connections"], {"expected": 0, "connected": 0, "attention": []})


    def test_live_agent_health_reports_meeting_owned_session_readiness(self):
        class FakeSupervisor:
            def snapshot_groups(self):
                return [
                    {
                        "group_id": "resident-ready",
                        "status": "running",
                        "meeting_id": "meeting-ready",
                        "started_at": "2999-01-01T00:00:00+00:00",
                        "agents": [{"agent_id": "agent-a", "display_name": "Agent A"}],
                    },
                    {
                        "group_id": "resident-missing",
                        "status": "running",
                        "meeting_id": "meeting-missing",
                        "agents": [{"agent_id": "agent-b", "display_name": "Agent B"}],
                    },
                    {
                        "group_id": "resident-error",
                        "status": "error",
                        "meeting_id": "meeting-error",
                        "agents": [{"agent_id": "agent-c", "display_name": "Agent C"}],
                        "recent_events": [
                            {
                                "event_type": "stale_watchdog",
                                "reason": "missing manifest agent agent-c",
                            }
                        ],
                    },
                    {
                        "group_id": "resident-diagnostic",
                        "status": "error",
                        "meeting_id": "meeting-diagnostic",
                        "diagnostic": True,
                        "agents": [{"agent_id": "agent-d", "display_name": "Agent D"}],
                    },
                    {
                        "group_id": "manual-no-meeting",
                        "status": "running",
                        "agents": [{"agent_id": "agent-e", "display_name": "Agent E"}],
                    },
                ]

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            for meeting_id, agent_id in (
                ("meeting-ready", "agent-a"),
                ("meeting-missing", "agent-b"),
                ("meeting-error", "agent-c"),
            ):
                meeting_dir = root / "meetings" / meeting_id
                meeting_dir.mkdir(parents=True)
                write_live_state(
                    meeting_dir,
                    {
                        "meeting_id": meeting_id,
                        "agent_bindings": [
                            {"role_id": "resident", "agent_id": agent_id, "provider_id": "local-provider"}
                        ],
                        "provider_configs": {"local-provider": {"kind": "local_cli"}},
                    },
                )
            (root / "live_agents.json").write_text(
                json.dumps(
                    {
                        "agents": [
                            {
                                "agent_id": "agent-a",
                                "display_name": "Agent A",
                                "status": "online",
                                "meeting_id": "meeting-ready",
                                "provider_kind": "local_cli",
                                "connection_kind": "local_cli",
                                "last_seen_at": "2999-01-01T00:00:01+00:00",
                            },
                            {
                                "agent_id": "agent-c",
                                "display_name": "Agent C",
                                "status": "online",
                                "meeting_id": "meeting-error",
                                "provider_kind": "local_cli",
                                "connection_kind": "local_cli",
                            },
                        ]
                    }
                ),
                encoding="utf-8",
            )
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root, process_supervisor=FakeSupervisor()))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with urlopen(f"http://127.0.0.1:{server.server_port}/api/live-agent-health", timeout=4) as response:
                    payload = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

        self.assertEqual(payload["status"], "degraded")
        self.assertEqual(payload["sessions"]["total"], 3)
        self.assertEqual(payload["sessions"]["ready"], 1)
        self.assertEqual(payload["sessions"]["degraded"], 2)
        self.assertEqual(
            payload["sessions"]["attention"],
            [
                "meeting-missing:resident-missing:agent-b:missing",
                "meeting-error:resident-error:group:error",
            ],
        )
        self.assertEqual(
            payload["sessions"]["items"],
            [
                {
                    "meeting_id": "meeting-ready",
                    "group_id": "resident-ready",
                    "status": "ready",
                    "process_status": "running",
                    "expected": 1,
                    "connected": 1,
                    "ownership_attention": [],
                    "process_attention": [],
                    "connection_attention": [],
                    "attention": [],
                },
                {
                    "meeting_id": "meeting-missing",
                    "group_id": "resident-missing",
                    "status": "degraded",
                    "process_status": "running",
                    "expected": 1,
                    "connected": 0,
                    "ownership_attention": [],
                    "process_attention": [],
                    "connection_attention": ["agent-b:missing"],
                    "attention": ["agent-b:missing"],
                },
                {
                    "meeting_id": "meeting-error",
                    "group_id": "resident-error",
                    "status": "degraded",
                    "process_status": "error",
                    "expected": 1,
                    "connected": 1,
                    "ownership_attention": [],
                    "process_attention": ["group:error"],
                    "connection_attention": [],
                    "attention": ["group:error"],
                    "process_reason": {
                        "event_type": "stale_watchdog",
                        "reason": "missing manifest agent agent-c",
                    },
                },
            ],
        )
        session_blob = json.dumps(payload["sessions"], ensure_ascii=False)
        self.assertNotIn("meeting-diagnostic", session_blob)
        self.assertNotIn("manual-no-meeting", session_blob)


    def test_live_agent_health_session_readiness_degrades_missing_binding_provider_config(self):
        class FakeSupervisor:
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
                    "status": "online",
                },
            )

            payload = live_agent_health_payload(root, FakeSupervisor())

        self.assertEqual(payload["status"], "degraded")
        self.assertEqual(payload["sessions"]["ready"], 0)
        self.assertEqual(payload["sessions"]["degraded"], 1)
        self.assertEqual(payload["sessions"]["attention"], ["resident-m1:resident-main:agent-a:binding_provider_missing"])
        self.assertEqual(payload["sessions"]["items"][0]["connected"], 0)
        self.assertEqual(payload["sessions"]["items"][0]["connection_attention"], ["agent-a:binding_provider_missing"])
        self.assertEqual(payload["connections"]["connected"], 1)
        self.assertEqual(payload["connections"]["attention"], [])
        self.assertEqual(payload["admission"]["attention"], ["resident-m1:agent-a:meeting_missing"])
        payload_blob = json.dumps(payload, ensure_ascii=False)
        self.assertNotIn("missing-provider", payload_blob)


    def test_live_agent_health_marks_owned_group_with_missing_meeting_degraded(self):
        class FakeSupervisor:
            def __init__(self):
                self.list_called = False

            def list_groups(self):
                self.list_called = True
                raise AssertionError("health endpoint must not refresh process groups")

            def snapshot_groups(self):
                return [
                    {
                        "group_id": "resident-missing-meeting",
                        "status": "running",
                        "meeting_id": "missing-meeting",
                        "config_path": "/tmp/secret-live-agents.json",
                        "log_tail": "secret provider output",
                        "agents": [{"agent_id": "agent-a", "display_name": "Agent A"}],
                    }
                ]

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "live_agents.json").write_text(json.dumps({"agents": []}), encoding="utf-8")
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root, process_supervisor=FakeSupervisor()))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with urlopen(f"http://127.0.0.1:{server.server_port}/api/live-agent-health", timeout=4) as response:
                    payload = json.loads(response.read().decode("utf-8"))
                with urlopen(f"http://127.0.0.1:{server.server_port}/api/live-agent-operations", timeout=4) as response:
                    operations = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

        self.assertEqual(payload["status"], "degraded")
        self.assertEqual(payload["sessions"]["total"], 1)
        self.assertEqual(payload["sessions"]["ready"], 0)
        self.assertEqual(payload["sessions"]["degraded"], 1)
        self.assertEqual(payload["sessions"]["attention"], ["missing-meeting:resident-missing-meeting:meeting:missing"])
        self.assertEqual(
            payload["sessions"]["items"][0],
            {
                "meeting_id": "missing-meeting",
                "group_id": "resident-missing-meeting",
                "status": "degraded",
                "process_status": "running",
                "expected": 1,
                "connected": 0,
                "ownership_attention": [],
                "process_attention": [],
                "connection_attention": [],
                "attention": ["meeting:missing"],
            },
        )
        session_blob = json.dumps(payload["sessions"], ensure_ascii=False)
        self.assertNotIn("/tmp/secret-live-agents.json", session_blob)
        self.assertNotIn("secret provider output", session_blob)
        self.assertEqual(operations["operations"], [])


    def test_live_agent_health_sanitizes_session_status_attention(self):
        class FakeSupervisor:
            def snapshot_groups(self):
                return [
                    {
                        "group_id": "resident-main",
                        "status": "error live-agents.json /tmp/secret",
                        "meeting_id": "resident-m1",
                        "agents": [{"agent_id": "agent-a", "display_name": "Agent A"}],
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
                    "agent_bindings": [{"role_id": "resident", "agent_id": "agent-a", "provider_id": "local-provider"}],
                    "provider_configs": {"local-provider": {"kind": "local_cli"}},
                },
            )
            (root / "live_agents.json").write_text(
                json.dumps(
                    {
                        "agents": [
                            {
                                "agent_id": "agent-a",
                                "display_name": "Agent A",
                                "status": "offline /tmp/secret log_tail",
                                "meeting_id": "resident-m1",
                                "provider_kind": "local_cli",
                                "connection_kind": "local_cli",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root, process_supervisor=FakeSupervisor()))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with urlopen(f"http://127.0.0.1:{server.server_port}/api/live-agent-health", timeout=4) as response:
                    payload = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

        self.assertEqual(payload["sessions"]["items"][0]["process_status"], "unknown")
        self.assertEqual(payload["sessions"]["items"][0]["process_attention"], ["group:unknown"])
        self.assertEqual(payload["sessions"]["items"][0]["connection_attention"], ["agent-a:offline"])
        session_blob = json.dumps(payload["sessions"], ensure_ascii=False)
        self.assertNotIn("/tmp/secret", session_blob)
        self.assertNotIn("live-agents.json", session_blob)
        self.assertNotIn("log_tail", session_blob)


    def test_live_agent_health_degrades_duplicate_active_meeting_session_groups(self):
        class FakeSupervisor:
            def snapshot_groups(self):
                return [
                    {
                        "group_id": "resident-main",
                        "status": "running",
                        "meeting_id": "resident-m1",
                        "agents": [{"agent_id": "agent-a", "display_name": "Agent A"}],
                    },
                    {
                        "group_id": "resident-shadow",
                        "status": "running",
                        "meeting_id": "resident-m1",
                        "agents": [{"agent_id": "agent-a", "display_name": "Agent A"}],
                    },
                    {
                        "group_id": "resident-stopped",
                        "status": "stopped",
                        "meeting_id": "resident-m1",
                        "agents": [{"agent_id": "agent-a", "display_name": "Agent A"}],
                    },
                    {
                        "group_id": "resident-diagnostic",
                        "status": "running",
                        "meeting_id": "resident-m1",
                        "diagnostic": True,
                        "agents": [{"agent_id": "agent-a", "display_name": "Agent A"}],
                    },
                ]

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            meeting_dir = root / "meetings" / "resident-m1"
            meeting_dir.mkdir(parents=True)
            write_live_state(
                meeting_dir,
                {
                    "meeting_id": "resident-m1",
                    "agent_bindings": [{"role_id": "resident", "agent_id": "agent-a", "provider_id": "local-provider"}],
                    "provider_configs": {"local-provider": {"kind": "local_cli"}},
                },
            )
            (root / "live_agents.json").write_text(
                json.dumps(
                    {
                        "agents": [
                            {
                                "agent_id": "agent-a",
                                "display_name": "Agent A",
                                "status": "online",
                                "meeting_id": "resident-m1",
                                "provider_kind": "local_cli",
                                "connection_kind": "local_cli",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root, process_supervisor=FakeSupervisor()))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with urlopen(f"http://127.0.0.1:{server.server_port}/api/live-agent-health", timeout=4) as response:
                    payload = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

        self.assertEqual(payload["status"], "degraded")
        self.assertEqual(payload["sessions"]["total"], 3)
        self.assertEqual(payload["sessions"]["ready"], 0)
        self.assertEqual(payload["sessions"]["degraded"], 3)
        self.assertEqual(
            payload["sessions"]["attention"],
            [
                "resident-m1:resident-main:meeting:duplicate_active_group",
                "resident-m1:resident-shadow:meeting:duplicate_active_group",
                "resident-m1:resident-stopped:group:stopped",
            ],
        )
        items_by_group = {item["group_id"]: item for item in payload["sessions"]["items"]}
        self.assertEqual(items_by_group["resident-main"]["ownership_attention"], ["meeting:duplicate_active_group"])
        self.assertEqual(items_by_group["resident-shadow"]["ownership_attention"], ["meeting:duplicate_active_group"])
        self.assertEqual(items_by_group["resident-stopped"]["ownership_attention"], [])
        self.assertNotIn("resident-diagnostic", json.dumps(payload["sessions"], ensure_ascii=False))
