from tests.gui_server_test_support import (
    LiveAgentSessionRunController,
    Path,
    ThreadingHTTPServer,
    _make_handler,
    _write_health_resident_meeting,
    append_live_event,
    append_lobby_event,
    connect_live_agent,
    json,
    live_agent_health_payload,
    tempfile,
    threading,
    unittest,
    urlopen,
)


class GuiServerHealthTests(unittest.TestCase):

    def test_live_agent_health_endpoint_summarizes_agents_and_processes(self):
        class FakeSupervisor:
            def __init__(self):
                self.list_called = False

            def list_groups(self):
                self.list_called = True
                raise AssertionError("health endpoint must not refresh process groups")

            def snapshot_groups(self):
                return [
                    {"group_id": "running-group", "status": "running", "meeting_id": "resident-m1"},
                    {"group_id": "unsafe-owner", "status": "running", "meeting_id": "../secret"},
                    {
                        "group_id": "restart-group",
                        "status": "restarting",
                        "meeting_id": "resident-m2",
                        "recent_events": [
                            {
                                "event_type": "stale_watchdog",
                                "reason": "missing manifest agent agent-a",
                            },
                            {
                                "event_type": "stale_watchdog",
                                "reason": "missing manifest agent live-agents.json",
                            },
                            {
                                "event_type": "restart_scheduled",
                                "reason": "env:SECRET_TOKEN",
                            },
                        ],
                    },
                    {
                        "group_id": "missing-config-group",
                        "status": "error",
                        "last_error": (
                            "Exited with return code 2; auto restart 1/1. "
                            "Restart failed: Live agent group missing-config-group has no config to restart."
                        ),
                        "recent_events": [{"event_type": "restart_failed"}],
                    },
                    {
                        "group_id": "missing-server-group",
                        "status": "error",
                        "last_error": (
                            "Exited with return code 2; auto restart 1/1. "
                            "Restart failed: Live agent group missing-server-group has no server to restart."
                        ),
                        "recent_events": [{"event_type": "restart_failed"}],
                    },
                    {
                        "group_id": "suspicious-restart-group",
                        "status": "error",
                        "last_error": (
                            "Exited with return code 2; auto restart 1/1. "
                            "Restart failed: Live agent group suspicious-restart-group has no config to restart. "
                            "/private/token/live-agents.json"
                        ),
                        "recent_events": [{"event_type": "restart_failed"}],
                    },
                    {"group_id": "crashed-group", "status": "error"},
                    {
                        "group_id": "orphan-group",
                        "status": "unknown",
                        "recent_events": [{"event_type": "recovered_unknown", "status": "unknown"}],
                    },
                    {"group_id": "stopped-group", "status": "stopped"},
                    {"group_id": "diagnostic-stopped", "status": "stopped", "diagnostic": True},
                    {"group_id": "diagnostic-error", "status": "error", "diagnostic": True},
                    {
                        "group_id": "legacy-smoke",
                        "status": "stopped",
                        "returncode": 0,
                        "config_path": "/dev/null/agentsassemble-missing-smoke/live-agents.json",
                    },
                    {"status": "error"},
                    {"group_id": "odd-group", "status": "mystery"},
                ]

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "live_agents.json").write_text(
                json.dumps(
                    {
                        "agents": [
                            {"agent_id": "online-agent", "status": "online"},
                            {"agent_id": "working-agent", "status": "working"},
                            {"agent_id": "error-agent", "status": "error"},
                            {"agent_id": "offline-agent", "status": "offline"},
                            {"agent_id": "diagnostic-offline", "status": "offline", "diagnostic": True},
                            {"agent_id": "diagnostic-error", "status": "error", "diagnostic": True},
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
                                "status": "error",
                            },
                            {"display_name": "Display Only", "status": "error"},
                            {"agent_id": "odd-agent", "status": "mystery"},
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
            self.assertEqual(payload["agents"]["counts"]["online"], 1)
            self.assertEqual(payload["agents"]["counts"]["working"], 1)
            self.assertEqual(payload["agents"]["counts"]["error"], 2)
            self.assertEqual(payload["agents"]["counts"]["offline"], 2)
            self.assertEqual(payload["agents"]["live"], 2)
            self.assertEqual(payload["agents"]["total"], 6)
            self.assertEqual(
                payload["agents"]["attention"],
                ["error-agent", "offline-agent", "missing-agent-id-5", "odd-agent"],
            )
            self.assertEqual(payload["processes"]["counts"]["running"], 2)
            self.assertEqual(payload["processes"]["counts"]["restarting"], 1)
            self.assertEqual(payload["processes"]["counts"]["error"], 5)
            self.assertEqual(payload["processes"]["counts"]["unknown"], 2)
            self.assertEqual(payload["processes"]["counts"]["stopped"], 1)
            self.assertEqual(payload["processes"]["total"], 11)
            self.assertEqual(
                payload["processes"]["meeting_ids"],
                {"running-group": "resident-m1", "restart-group": "resident-m2"},
            )
            self.assertEqual(
                payload["processes"]["attention"],
                [
                    "restart-group",
                    "missing-config-group",
                    "missing-server-group",
                    "suspicious-restart-group",
                    "crashed-group",
                    "orphan-group",
                    "stopped-group",
                    "missing-process-group-id-10",
                    "odd-group",
                ],
            )
            self.assertEqual(
                payload["processes"]["reasons"],
                {
                    "restart-group": {"event_type": "stale_watchdog", "reason": "missing manifest agent agent-a"},
                    "missing-config-group": {"event_type": "restart_failed", "reason": "missing launch config"},
                    "missing-server-group": {"event_type": "restart_failed", "reason": "missing launch server"},
                    "orphan-group": {
                        "event_type": "recovered_unknown",
                        "reason": "orphan running record marked unknown",
                    },
                },
            )
            self.assertNotIn("SECRET_TOKEN", json.dumps(payload, ensure_ascii=False))
            self.assertNotIn("live-agents.json", json.dumps(payload, ensure_ascii=False))
            self.assertNotIn("private/token", json.dumps(payload, ensure_ascii=False))
            self.assertFalse(supervisor.list_called)


    def test_live_agent_health_redacts_sensitive_agent_attention_ids(self):
        sensitive_token = "ghp_abcdefghijklmnopqrstuvwxyz1234567890"

        class FakeSupervisor:
            def snapshot_groups(self):
                return []

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "live_agents.json").write_text(
                json.dumps(
                    {
                        "agents": [
                            {"agent_id": "env:SECRET_AGENT", "status": "error"},
                            {"agent_id": "literal:SECRET_AGENT", "status": "offline"},
                            {"agent_id": sensitive_token, "status": "stale"},
                            {"agent_id": "/Users/me/private/config.json", "status": "error"},
                            {"agent_id": "safe-agent", "status": "offline"},
                        ]
                    }
                ),
                encoding="utf-8",
            )

            payload = live_agent_health_payload(root, FakeSupervisor())

        serialized = json.dumps(payload, ensure_ascii=False)
        self.assertNotIn("SECRET_AGENT", serialized)
        self.assertNotIn(sensitive_token, serialized)
        self.assertNotIn("Users/me/private", serialized)
        self.assertNotIn("config.json", serialized)
        self.assertEqual(
            payload["agents"]["attention"],
            [
                "missing-agent-id-1",
                "missing-agent-id-2",
                "missing-agent-id-3",
                "missing-agent-id-4",
                "safe-agent",
            ],
        )


    def test_live_agent_health_reports_sandbox_enforcement_levels(self):
        class FakeSupervisor:
            def snapshot_groups(self):
                return []

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            connect_live_agent(
                root,
                {
                    "agent_id": "codex-live",
                    "provider_kind": "codex_live_session",
                    "connection_kind": "live_session",
                },
            )
            connect_live_agent(
                root,
                {
                    "agent_id": "claude-live",
                    "provider_kind": "claude_code",
                    "connection_kind": "terminal_session",
                    "sandbox_enforcement": "os_sandboxed",
                },
            )
            (root / "live_agents.json").write_text(
                json.dumps(
                    {
                        "agents": [
                            *json.loads((root / "live_agents.json").read_text(encoding="utf-8"))["agents"],
                            {
                                "agent_id": "unknown-live",
                                "display_name": "Unknown Live",
                                "provider_kind": "mystery_provider",
                                "connection_kind": "mystery_connection",
                                "status": "online",
                            },
                        ]
                    }
                ),
                encoding="utf-8",
            )

            payload = live_agent_health_payload(root, FakeSupervisor())

        self.assertEqual(payload["status"], "degraded")
        self.assertEqual(
            payload["sandbox_enforcement"],
            {
                "counts": {"advisory": 1, "codex_readonly": 1, "os_sandboxed": 0, "unknown": 1},
                "attention": ["unknown-live"],
            },
        )


    def test_live_agent_health_endpoint_summarizes_durable_session_run_retry(self):
        class FakeSupervisor:
            def list_groups(self):
                raise AssertionError("health endpoint must not refresh process groups")

            def snapshot_groups(self):
                return []

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            controller = LiveAgentSessionRunController(root)
            run = controller.begin_run(
                action="ensure",
                payload={
                    "meeting_id": "resident-m1",
                    "group_id": "resident-main",
                    "live_agent_config_path": "configs/private.json",
                    "server": "https://secret.example",
                },
            )
            controller.finish_run(
                run["run_id"],
                session={
                    "status": "degraded",
                    "meeting_id": "resident-m1",
                    "group_id": "resident-main",
                    "action": "recover",
                },
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
        self.assertEqual(payload["session_runs"]["total"], 1)
        self.assertEqual(payload["session_runs"]["active"], 1)
        self.assertEqual(payload["session_runs"]["ready"], 0)
        self.assertEqual(payload["session_runs"]["retrying"], 1)
        self.assertEqual(
            payload["session_runs"]["attention"],
            [f"resident-m1:resident-main:{run['run_id']}:degraded:retrying"],
        )
        self.assertEqual(payload["session_runs"]["items"][0]["run_id"], run["run_id"])
        self.assertEqual(payload["session_runs"]["items"][0]["reconcile_failure_count"], 1)
        self.assertEqual(payload["session_runs"]["items"][0]["reconcile_backoff_seconds"], 60)
        serialized = json.dumps(payload, ensure_ascii=False)
        self.assertNotIn("configs/private.json", serialized)
        self.assertNotIn("secret.example", serialized)


    def test_live_agent_health_degrades_ready_session_run_without_current_readiness(self):
        class FakeSupervisor:
            def list_groups(self):
                raise AssertionError("health endpoint must not refresh process groups")

            def snapshot_groups(self):
                return []

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _write_health_resident_meeting(root, agent_ids=["agent-a"])
            controller = LiveAgentSessionRunController(root)
            run = controller.begin_run(
                action="ensure",
                payload={
                    "meeting_id": "resident-m1",
                    "group_id": "resident-main",
                    "live_agent_config_path": "configs/private.json",
                    "server": "https://secret.example",
                },
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
            operations_path_exists = (root / "live-agent-runs" / "operations.jsonl").exists()

        self.assertEqual(payload["status"], "degraded")
        self.assertFalse(operations_path_exists)
        self.assertEqual(
            payload["session_runs"]["attention"],
            [f"resident-m1:resident-main:{run['run_id']}:ready:no_current_readiness"],
        )
        self.assertEqual(payload["session_runs"]["items"][0]["status"], "ready")
        self.assertEqual(payload["session_runs"]["items"][0]["readiness"]["status"], "degraded")
        self.assertEqual(
            payload["session_runs"]["items"][0]["readiness"]["attention"],
            ["session_run:no_current_readiness"],
        )
        serialized = json.dumps(payload, ensure_ascii=False)
        self.assertNotIn("configs/private.json", serialized)
        self.assertNotIn("secret.example", serialized)


    def test_live_agent_health_keeps_old_active_ready_session_runs_outside_recent_tail(self):
        class FakeSupervisor:
            def list_groups(self):
                raise AssertionError("health endpoint must not refresh process groups")

            def snapshot_groups(self):
                return []

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _write_health_resident_meeting(root, agent_ids=["agent-a"])
            controller = LiveAgentSessionRunController(root)
            active_ready_run = controller.begin_run(
                action="ensure",
                payload={
                    "meeting_id": "resident-m1",
                    "group_id": "resident-main",
                    "live_agent_config_path": "configs/private.json",
                },
            )
            controller.finish_run(
                active_ready_run["run_id"],
                session={
                    "status": "ready",
                    "meeting_id": "resident-m1",
                    "group_id": "resident-main",
                    "action": "none",
                },
            )
            for index in range(60):
                run = controller.begin_run(
                    action="ensure",
                    payload={"meeting_id": f"resident-done-{index}", "group_id": "resident-main"},
                )
                controller.finish_run(
                    run["run_id"],
                    session={
                        "status": "ready",
                        "meeting_id": f"resident-done-{index}",
                        "group_id": "resident-main",
                        "action": "none",
                    },
                )
                controller.stop_run(run["run_id"])

            payload = live_agent_health_payload(root, FakeSupervisor())

        run_ids = [item["run_id"] for item in payload["session_runs"]["items"]]
        self.assertIn(active_ready_run["run_id"], run_ids)
        self.assertIn(
            f"resident-m1:resident-main:{active_ready_run['run_id']}:ready:no_current_readiness",
            payload["session_runs"]["attention"],
        )


    def test_live_agent_health_accepts_ready_session_run_with_current_ready_overlay(self):
        class FakeSupervisor:
            def list_groups(self):
                raise AssertionError("health endpoint must not refresh process groups")

            def snapshot_groups(self):
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
            _write_health_resident_meeting(root, agent_ids=["agent-a"])
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

        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["session_runs"]["attention"], [])
        self.assertEqual(payload["session_runs"]["items"][0]["readiness"]["status"], "ready")


    def test_live_agent_health_degrades_ready_session_run_with_duplicate_current_owner(self):
        class FakeSupervisor:
            def list_groups(self):
                raise AssertionError("health endpoint must not refresh process groups")

            def snapshot_groups(self):
                return [
                    {
                        "group_id": "resident-main",
                        "status": "running",
                        "meeting_id": "resident-m1",
                        "agents": [{"agent_id": "agent-a"}],
                    },
                    {
                        "group_id": "resident-shadow",
                        "status": "running",
                        "meeting_id": "resident-m1",
                        "agents": [{"agent_id": "agent-a"}],
                    },
                ]

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
                },
            )
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

        self.assertEqual(payload["status"], "degraded")
        self.assertIn(
            f"resident-m1:resident-main:{run['run_id']}:ready:current_readiness_degraded",
            payload["session_runs"]["attention"],
        )
        self.assertIn("meeting:duplicate_active_group", payload["session_runs"]["items"][0]["readiness"]["attention"])


    def test_live_agent_health_endpoint_includes_process_monitor_liveness(self):
        class MonitorSupervisor:
            def list_groups(self):
                raise AssertionError("health endpoint must not refresh process groups")

            def snapshot_groups(self):
                return []

            def monitor_snapshot(self):
                return {
                    "running": True,
                    "interval_seconds": 2.5,
                    "last_tick_at": "2026-05-21T10:09:00+00:00",
                    "last_status": "ok",
                    "last_group_count": 1,
                    "last_error_type": "",
                }

        with tempfile.TemporaryDirectory() as temp_dir:
            payload = live_agent_health_payload(Path(temp_dir), MonitorSupervisor())

        self.assertEqual(payload["status"], "ok")
        self.assertEqual(
            payload["process_monitor"],
            {
                "running": True,
                "interval_seconds": 2.5,
                "last_tick_at": "2026-05-21T10:09:00+00:00",
                "last_status": "ok",
                "last_group_count": 1,
                "last_error_type": "",
                "attention": [],
            },
        )


    def test_live_agent_health_endpoint_degrades_on_safe_process_monitor_failure(self):
        class MonitorSupervisor:
            def list_groups(self):
                raise AssertionError("health endpoint must not refresh process groups")

            def snapshot_groups(self):
                return []

            def monitor_snapshot(self):
                return {
                    "running": True,
                    "interval_seconds": 2,
                    "last_tick_at": "2026-05-21T10:09:00+00:00",
                    "last_status": "failed",
                    "last_group_count": 0,
                    "last_error_type": "RuntimeError /Users/me/private/live-agents.secret.json",
                }

        with tempfile.TemporaryDirectory() as temp_dir:
            payload = live_agent_health_payload(Path(temp_dir), MonitorSupervisor())

        self.assertEqual(payload["status"], "degraded")
        self.assertEqual(payload["process_monitor"]["last_error_type"], "")
        self.assertEqual(payload["process_monitor"]["attention"], ["failed:Exception"])
        self.assertNotIn("/Users/me/private", json.dumps(payload, ensure_ascii=False))


    def test_live_agent_health_endpoint_includes_session_run_monitor_liveness(self):
        from agentsassemble.gui import LiveAgentSessionRunMonitor

        class FakeSupervisor:
            def list_groups(self):
                raise AssertionError("health endpoint must not refresh process groups")

            def snapshot_groups(self):
                return []

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            monitor = LiveAgentSessionRunMonitor(
                root,
                FakeSupervisor(),
                LiveAgentSessionRunController(root),
                default_server="http://room.local",
                interval_seconds=2.5,
            )
            monitor.run_once()
            server = ThreadingHTTPServer(
                ("127.0.0.1", 0),
                _make_handler(root, process_supervisor=FakeSupervisor(), session_run_monitor=monitor),
            )
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with urlopen(f"http://127.0.0.1:{server.server_port}/api/live-agent-health", timeout=4) as response:
                    payload = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

        monitor_payload = payload["session_run_monitor"]
        self.assertEqual(monitor_payload["running"], False)
        self.assertEqual(monitor_payload["interval_seconds"], 2.5)
        self.assertEqual(monitor_payload["last_status"], "ok")
        self.assertEqual(monitor_payload["last_result_count"], 0)
        self.assertRegex(monitor_payload["last_tick_at"], r"^\d{4}-\d{2}-\d{2}T")
        self.assertEqual(monitor_payload["last_error_type"], "")


    def test_live_agent_health_degrades_ready_session_when_lobby_cursor_lags_latest_event(self):
        class FakeSupervisor:
            def list_groups(self):
                raise AssertionError("health endpoint must not refresh process groups")

            def snapshot_groups(self):
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
            _write_health_resident_meeting(root, agent_ids=["agent-a"])
            latest = append_lobby_event(root, {"name": "human", "message": "latest event text must stay out"})
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

            payload = live_agent_health_payload(root, FakeSupervisor())

        self.assertEqual(payload["status"], "degraded")
        self.assertEqual(payload["observations"]["latest_lobby_event_id"], latest["id"])
        self.assertEqual(payload["observations"]["ready_agent_count"], 1)
        self.assertEqual(payload["observations"]["lobby_behind_count"], 1)
        self.assertEqual(
            payload["observations"]["attention"],
            ["resident-m1:resident-main:agent-a:lobby_cursor_behind"],
        )
        serialized = json.dumps(payload, ensure_ascii=False)
        self.assertNotIn("latest event text", serialized)


    def test_live_agent_health_keeps_ready_session_ok_when_lobby_cursor_is_current(self):
        class FakeSupervisor:
            def list_groups(self):
                raise AssertionError("health endpoint must not refresh process groups")

            def snapshot_groups(self):
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
            _write_health_resident_meeting(root, agent_ids=["agent-a"])
            latest = append_lobby_event(root, {"name": "human", "message": "current event text must stay out"})
            connect_live_agent(
                root,
                {
                    "agent_id": "agent-a",
                    "display_name": "Agent A",
                    "provider_kind": "local_cli",
                    "connection_kind": "local_cli",
                    "meeting_id": "resident-m1",
                    "last_observed_event_id": latest["id"],
                    "last_reply_at": "2026-05-21T10:12:00+00:00",
                },
            )

            payload = live_agent_health_payload(root, FakeSupervisor())

        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["observations"]["attention"], [])
        self.assertEqual(payload["observations"]["lobby_behind_count"], 0)
        self.assertEqual(payload["observations"]["items"][0]["last_reply_at"], "2026-05-21T10:12:00+00:00")
        self.assertNotIn("current event text", json.dumps(payload, ensure_ascii=False))


    def test_live_agent_health_does_not_expose_or_degrade_on_preserved_last_error(self):
        class FakeSupervisor:
            def list_groups(self):
                raise AssertionError("health endpoint must not refresh process groups")

            def snapshot_groups(self):
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
            _write_health_resident_meeting(root, agent_ids=["agent-a"])
            latest = append_lobby_event(root, {"name": "human", "message": "current event text must stay out"})
            connect_live_agent(
                root,
                {
                    "agent_id": "agent-a",
                    "display_name": "Agent A",
                    "provider_kind": "local_cli",
                    "connection_kind": "local_cli",
                    "meeting_id": "resident-m1",
                    "last_observed_event_id": latest["id"],
                    "last_error": "provider output raw model reply should never leak",
                },
            )

            payload = live_agent_health_payload(root, FakeSupervisor())

        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["observations"]["error_count"], 0)
        self.assertEqual(payload["observations"]["attention"], [])
        self.assertNotIn("last_error", payload["observations"]["items"][0])
        serialized = json.dumps(payload, ensure_ascii=False)
        self.assertNotIn("provider output", serialized)
        self.assertNotIn("raw model reply", serialized)


    def test_live_agent_health_includes_safe_admission_summary_without_degrading(self):
        class FakeSupervisor:
            def list_groups(self):
                raise AssertionError("health endpoint must not refresh process groups")

            def snapshot_groups(self):
                return []

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _write_health_resident_meeting(root, agent_ids=["agent-a", "agent-b"])
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
            connect_live_agent(
                root,
                {
                    "agent_id": "lobby-agent",
                    "display_name": "Lobby Agent",
                    "provider_kind": "manual",
                    "connection_kind": "manual",
                    "session_id": "private-session-d",
                    "last_error": "provider output raw model reply should stay out",
                },
            )
            connect_live_agent(
                root,
                {
                    "agent_id": "diagnostic-agent",
                    "display_name": "Diagnostic Agent",
                    "provider_kind": "local_cli",
                    "connection_kind": "local_cli",
                    "meeting_id": "resident-m1",
                    "session_id": "private-session-diagnostic",
                    "diagnostic": True,
                },
            )
            state_path = root / "live_agents.json"
            state = json.loads(state_path.read_text(encoding="utf-8"))
            state["agents"][2]["admission_status"] = "bound_to_meeting"
            state["agents"][2]["host_approved_binding"] = True
            state["agents"][2]["binding_role_id"] = "spoofed"
            state_path.write_text(json.dumps(state), encoding="utf-8")

            payload = live_agent_health_payload(root, FakeSupervisor())

        self.assertEqual(payload["status"], "ok")
        admission = payload["admission"]
        self.assertEqual(admission["total"], 4)
        self.assertEqual(admission["host_approved"], 1)
        self.assertEqual(admission["unapproved"], 3)
        self.assertEqual(admission["counts"]["bound_to_meeting"], 1)
        self.assertEqual(admission["counts"]["binding_conflict"], 1)
        self.assertEqual(admission["counts"]["meeting_lobby_only"], 1)
        self.assertEqual(admission["counts"]["lobby_only"], 1)
        self.assertEqual(
            admission["attention"],
            [
                "resident-m1:agent-b:binding_conflict",
                "resident-m1:guest-agent:meeting_lobby_only",
                "lobby:lobby-agent:lobby_only",
            ],
        )
        serialized = json.dumps(payload, ensure_ascii=False)
        self.assertNotIn("private-session", serialized)
        self.assertNotIn("spoofed", serialized)
        self.assertNotIn("provider output", serialized)
        self.assertNotIn("raw model reply", serialized)


    def test_live_agent_health_degrades_ready_session_when_official_turn_cursor_lags_request(self):
        class FakeSupervisor:
            def list_groups(self):
                raise AssertionError("health endpoint must not refresh process groups")

            def snapshot_groups(self):
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
            meeting_dir = _write_health_resident_meeting(root, agent_ids=["agent-a"])
            request = append_live_event(
                meeting_dir,
                {
                    "kind": "live_agent_turn_request",
                    "meeting_id": "resident-m1",
                    "target_agent_id": "agent-a",
                    "content": "official request text must stay out",
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
                    "last_observed_live_event_id": "older-live-event",
                },
            )

            payload = live_agent_health_payload(root, FakeSupervisor())

        self.assertEqual(payload["status"], "degraded")
        self.assertEqual(payload["observations"]["latest_live_request_count"], 1)
        self.assertEqual(payload["observations"]["items"][0]["latest_live_event_id"], request["id"])
        self.assertEqual(
            payload["observations"]["attention"],
            ["resident-m1:resident-main:agent-a:live_cursor_behind"],
        )
        self.assertNotIn("official request text", json.dumps(payload, ensure_ascii=False))


    def test_live_agent_health_treats_official_turn_reply_as_observed(self):
        class FakeSupervisor:
            def list_groups(self):
                raise AssertionError("health endpoint must not refresh process groups")

            def snapshot_groups(self):
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
            meeting_dir = _write_health_resident_meeting(root, agent_ids=["agent-a"])
            request = append_live_event(
                meeting_dir,
                {
                    "kind": "live_agent_turn_request",
                    "meeting_id": "resident-m1",
                    "target_agent_id": "agent-a",
                    "content": "official request text must stay out",
                },
            )
            append_live_event(
                meeting_dir,
                {
                    "kind": "message",
                    "meeting_id": "resident-m1",
                    "official_record": True,
                    "actor_id": "agent-a",
                    "source_event_id": request["id"],
                    "content": "official reply text must stay out",
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
                    "last_observed_live_event_id": "older-live-event",
                },
            )

            payload = live_agent_health_payload(root, FakeSupervisor())

        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["observations"]["live_behind_count"], 0)
        self.assertEqual(payload["observations"]["attention"], [])
        self.assertEqual(payload["observations"]["items"][0]["live_status"], "answered")
        serialized = json.dumps(payload, ensure_ascii=False)
        self.assertNotIn("official request text", serialized)
        self.assertNotIn("official reply text", serialized)


    def test_live_agent_health_includes_shared_memory_summary_without_content(self):
        class FakeSupervisor:
            def list_groups(self):
                raise AssertionError("health endpoint must not refresh process groups")

            def snapshot_groups(self):
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
            meeting_dir = _write_health_resident_meeting(root, agent_ids=["agent-a"])
            append_live_event(
                meeting_dir,
                {
                    "kind": "message",
                    "meeting_id": "resident-m1",
                    "official_record": True,
                    "actor_id": "agent-a",
                    "role_id": "agent-a",
                    "content": "Action: Preserve resident memory health evidence.",
                },
            )
            last_event = append_live_event(
                meeting_dir,
                {
                    "kind": "message",
                    "meeting_id": "resident-m1",
                    "official_record": True,
                    "actor_id": "agent-a",
                    "role_id": "agent-a",
                    "content": "Question: Is shared memory still current?",
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

            payload = live_agent_health_payload(root, FakeSupervisor())

        self.assertEqual(payload["status"], "ok")
        shared_memory = payload["shared_memory"]
        self.assertEqual(shared_memory["ready_sessions"], 1)
        self.assertEqual(shared_memory["with_memory"], 1)
        self.assertEqual(shared_memory["official_event_count"], 2)
        self.assertEqual(shared_memory["open_question_count"], 1)
        self.assertEqual(shared_memory["action_item_count"], 1)
        self.assertEqual(shared_memory["last_official_event_id"], last_event["id"])
        self.assertEqual(shared_memory["items"][0]["meeting_id"], "resident-m1")
        self.assertEqual(shared_memory["items"][0]["group_id"], "resident-main")
        self.assertEqual(shared_memory["items"][0]["official_event_count"], 2)
        serialized = json.dumps(payload, ensure_ascii=False)
        self.assertNotIn("Preserve resident memory health evidence", serialized)
        self.assertNotIn("Is shared memory still current", serialized)


    def test_live_agent_health_shared_memory_uses_full_counts_and_drops_source_text(self):
        class FakeSupervisor:
            def list_groups(self):
                raise AssertionError("health endpoint must not refresh process groups")

            def snapshot_groups(self):
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
            meeting_dir = _write_health_resident_meeting(root, agent_ids=["agent-a"])
            shared_dir = meeting_dir / "shared_memory"
            shared_dir.mkdir()
            (shared_dir / "index.json").write_text(
                json.dumps(
                    {
                        "source": "PROMPT BODY SHOULD NOT LEAK",
                        "official_event_count": 1,
                        "action_items": [{"text": "embedded fallback action should not leak"}],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            last_event = None
            for index in range(55):
                last_event = append_live_event(
                    meeting_dir,
                    {
                        "kind": "message",
                        "meeting_id": "resident-m1",
                        "official_record": True,
                        "actor_id": "agent-a",
                        "role_id": "agent-a",
                        "content": f"Action: Full memory count item {index}.",
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

            payload = live_agent_health_payload(root, FakeSupervisor())

        shared_memory = payload["shared_memory"]
        self.assertEqual(shared_memory["official_event_count"], 55)
        self.assertEqual(shared_memory["action_item_count"], 55)
        self.assertEqual(shared_memory["last_official_event_id"], last_event["id"])
        self.assertNotIn("source", shared_memory["items"][0])
        serialized = json.dumps(payload, ensure_ascii=False)
        self.assertNotIn("PROMPT BODY SHOULD NOT LEAK", serialized)
        self.assertNotIn("embedded fallback action should not leak", serialized)
        self.assertNotIn("Full memory count item", serialized)


    def test_live_agent_health_keeps_old_active_session_runs_outside_recent_tail(self):
        class FakeSupervisor:
            def list_groups(self):
                raise AssertionError("health endpoint must not refresh process groups")

            def snapshot_groups(self):
                return []

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            controller = LiveAgentSessionRunController(root)
            active_run = controller.begin_run(
                action="ensure",
                payload={
                    "meeting_id": "resident-old",
                    "group_id": "resident-main",
                    "live_agent_config_path": "configs/private.json",
                    "server": "https://secret.example",
                },
            )
            controller.finish_run(
                active_run["run_id"],
                session={
                    "status": "degraded",
                    "meeting_id": "resident-old",
                    "group_id": "resident-main",
                    "action": "recover",
                },
            )
            for index in range(60):
                run = controller.begin_run(
                    action="ensure",
                    payload={
                        "meeting_id": f"resident-done-{index}",
                        "group_id": "resident-main",
                        "live_agent_config_path": "configs/private.json",
                    },
                )
                controller.finish_run(
                    run["run_id"],
                    session={
                        "status": "ready",
                        "meeting_id": f"resident-done-{index}",
                        "group_id": "resident-main",
                        "action": "none",
                    },
                )

            payload = live_agent_health_payload(root, FakeSupervisor())

        self.assertEqual(payload["status"], "degraded")
        self.assertIn(
            f"resident-old:resident-main:{active_run['run_id']}:degraded:retrying",
            payload["session_runs"]["attention"],
        )
        self.assertIn(active_run["run_id"], [item["run_id"] for item in payload["session_runs"]["items"]])


    def test_live_agent_health_redacts_sensitive_session_run_ids(self):
        class FakeSupervisor:
            def list_groups(self):
                raise AssertionError("health endpoint must not refresh process groups")

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
                                "status": "degraded",
                                "active": True,
                                "phase": "/Users/me/private/live-agents.secret.json token sk-test-secret",
                                "meeting_id": "env:SECRET_MEETING",
                                "group_id": "literal:SECRET_GROUP",
                                "request": {},
                                "result": {"status": "degraded"},
                                "next_reconcile_at": "2026-05-21T10:07:00+00:00",
                                "reconcile_failure_count": 1,
                                "reconcile_backoff_seconds": 60,
                            }
                        ]
                    },
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )

            payload = live_agent_health_payload(root, FakeSupervisor())

        serialized = json.dumps(payload, ensure_ascii=False)
        self.assertNotIn("SECRET_MEETING", serialized)
        self.assertNotIn("SECRET_GROUP", serialized)
        self.assertNotIn("ghp_abcdefghijklmnopqrstuvwxyz1234567890", serialized)
        self.assertNotIn("Users/me/private", serialized)
        self.assertNotIn("live-agents.secret.json", serialized)
        self.assertNotIn("sk-test-secret", serialized)
        self.assertEqual(payload["session_runs"]["items"][0]["phase"], "")
        self.assertEqual(payload["session_runs"]["attention"], ["-:-:-:degraded:retrying"])


    def test_live_agent_health_ignores_diagnostic_session_runs(self):
        class FakeSupervisor:
            def list_groups(self):
                raise AssertionError("health endpoint must not refresh process groups")

            def snapshot_groups(self):
                return []

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            controller = LiveAgentSessionRunController(root)
            run = controller.begin_run(
                action="ensure",
                payload={
                    "meeting_id": "diagnostic-m1",
                    "group_id": "diagnostic-main",
                    "diagnostic": True,
                    "live_agent_config_path": "configs/private.json",
                },
            )
            controller.finish_run(
                run["run_id"],
                session={
                    "status": "degraded",
                    "meeting_id": "diagnostic-m1",
                    "group_id": "diagnostic-main",
                    "action": "recover",
                },
            )

            payload = live_agent_health_payload(root, FakeSupervisor())

        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["session_runs"]["total"], 0)
        self.assertEqual(payload["session_runs"]["active"], 0)
        self.assertEqual(payload["session_runs"]["ready"], 0)
        self.assertEqual(payload["session_runs"]["retrying"], 0)
        self.assertEqual(payload["session_runs"]["attention"], [])
        self.assertEqual(payload["session_runs"]["items"], [])


    def test_live_agent_health_ignores_legacy_string_diagnostic_session_runs(self):
        class FakeSupervisor:
            def list_groups(self):
                raise AssertionError("health endpoint must not refresh process groups")

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
                                "run_id": "legacy-diagnostic-run",
                                "action": "ensure",
                                "status": "degraded",
                                "active": True,
                                "phase": "reconcile_failed",
                                "meeting_id": "diagnostic-m1",
                                "group_id": "diagnostic-main",
                                "request": {"diagnostic": "true"},
                                "result": {"status": "degraded"},
                                "next_reconcile_at": "2026-05-21T10:07:00+00:00",
                                "reconcile_failure_count": 1,
                                "reconcile_backoff_seconds": 60,
                            }
                        ]
                    },
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )

            payload = live_agent_health_payload(root, FakeSupervisor())

        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["session_runs"]["total"], 0)
        self.assertEqual(payload["session_runs"]["items"], [])


    def test_live_agent_health_endpoint_reports_only_current_recovered_unknown_reason(self):
        class FakeSupervisor:
            def list_groups(self):
                raise AssertionError("health endpoint must not refresh process groups")

            def snapshot_groups(self):
                return [
                    {
                        "group_id": "current-orphan",
                        "status": "unknown",
                        "recent_events": [{"event_type": "recovered_unknown", "status": "unknown"}],
                    },
                    {
                        "group_id": "resolved-orphan",
                        "status": "unknown",
                        "recent_events": [
                            {"event_type": "recovered_unknown", "status": "unknown"},
                            {"event_type": "stopped", "status": "stopped"},
                        ],
                    },
                    {
                        "group_id": "running-after-recovery",
                        "status": "running",
                        "recent_events": [{"event_type": "recovered_unknown", "status": "unknown"}],
                    },
                ]

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root, process_supervisor=FakeSupervisor()))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with urlopen(f"http://127.0.0.1:{server.server_port}/api/live-agent-health", timeout=4) as response:
                    payload = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

        self.assertEqual(
            payload["processes"]["reasons"],
            {
                "current-orphan": {
                    "event_type": "recovered_unknown",
                    "reason": "orphan running record marked unknown",
                },
            },
        )


    def test_live_agent_health_endpoint_reports_only_current_restart_failed_launch_reason(self):
        class FakeSupervisor:
            def list_groups(self):
                raise AssertionError("health endpoint must not refresh process groups")

            def snapshot_groups(self):
                return [
                    {
                        "group_id": "current-failure",
                        "status": "error",
                        "last_error": (
                            "Exited with return code 2; auto restart 1/1. "
                            "Restart failed: Live agent group current-failure has no server to restart."
                        ),
                        "recent_events": [
                            {"event_type": "stale_watchdog", "reason": "missing manifest agent agent-a"},
                            {"event_type": "restart_failed"},
                        ],
                    },
                    {
                        "group_id": "resolved-failure",
                        "status": "error",
                        "last_error": (
                            "Exited with return code 2; auto restart 1/1. "
                            "Restart failed: Live agent group resolved-failure has no config to restart."
                        ),
                        "recent_events": [
                            {"event_type": "restart_failed"},
                            {"event_type": "started"},
                        ],
                    },
                    {
                        "group_id": "wrong-id-failure",
                        "status": "error",
                        "last_error": (
                            "Exited with return code 2; auto restart 1/1. "
                            "Restart failed: Live agent group other-group has no config to restart."
                        ),
                        "recent_events": [{"event_type": "restart_failed"}],
                    },
                    {
                        "group_id": "running-failure",
                        "status": "running",
                        "last_error": (
                            "Exited with return code 2; auto restart 1/1. "
                            "Restart failed: Live agent group running-failure has no config to restart."
                        ),
                        "recent_events": [{"event_type": "restart_failed"}],
                    },
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

            self.assertEqual(
                payload["processes"]["reasons"],
                {"current-failure": {"event_type": "restart_failed", "reason": "missing launch server"}},
            )
