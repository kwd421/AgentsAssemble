from tests.gui_server_test_support import (
    LiveAgentSessionRunController,
    Path,
    _write_health_resident_meeting,
    _write_lobby_jsonl_event,
    _write_single_agent_session_configs,
    connect_live_agent,
    heartbeat_live_agent,
    json,
    live_agent_health_payload,
    patch,
    serve_gui,
    start_live_agent_meeting,
    sys,
    tempfile,
    threading,
    time,
    unittest,
)


class GuiServerSessionLifecycleTests(unittest.TestCase):

    def test_live_agent_session_run_monitor_reconciles_active_runs_on_each_tick(self):
        from agentsassemble.gui import LiveAgentSessionRunMonitor

        class TwoTickStop:
            def __init__(self):
                self.waits = []

            def is_set(self):
                return False

            def wait(self, seconds):
                self.waits.append(seconds)
                return len(self.waits) >= 2

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
            controller = LiveAgentSessionRunController(root)
            run = controller.begin_run(
                action="ensure",
                payload={
                    "meeting_id": "resident-m1",
                    "group_id": "resident-main",
                    "live_agent_config_path": str(live_agent_config),
                    "server": "http://room.local",
                    "probe_bound_agents": True,
                    "probe_timeout_seconds": 7,
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
            observed_requests = []

            def ensure_from_run(output_root, process_supervisor, payload, *, default_server):
                del output_root, process_supervisor, default_server
                observed_requests.append(dict(payload))
                return {
                    "status": "ready",
                    "meeting_id": payload["meeting_id"],
                    "group_id": payload["group_id"],
                    "action": "none",
                }

            monitor = LiveAgentSessionRunMonitor(
                root,
                object(),
                controller,
                default_server="http://room.local",
                interval_seconds=0,
            )
            stop = TwoTickStop()
            with patch("agentsassemble.gui.live_agent_session_ensure_payload", side_effect=ensure_from_run):
                monitor._loop(stop)
            reconciled = controller.list_runs()[0]

        self.assertEqual(len(observed_requests), 2)
        self.assertEqual(observed_requests[0]["live_agent_config_path"], str(live_agent_config))
        self.assertTrue(observed_requests[0]["probe_bound_agents"])
        self.assertEqual(observed_requests[0]["probe_timeout_seconds"], 7)
        self.assertTrue(observed_requests[1]["probe_bound_agents"])
        self.assertEqual(observed_requests[1]["probe_timeout_seconds"], 7)
        self.assertEqual(reconciled["status"], "ready")
        self.assertEqual(reconciled["reconcile_count"], 2)
        self.assertTrue(all(seconds >= 1.0 for seconds in stop.waits))


    def test_live_agent_session_run_monitor_records_safe_failure_and_keeps_loop_alive(self):
        from agentsassemble.gui import LiveAgentSessionRunMonitor, live_agent_health_payload, live_agent_operations_payload

        class RaisingController:
            def __init__(self):
                self.calls = 0

            def reconcile_active_runs(self, callback, **kwargs):
                del callback
                del kwargs
                self.calls += 1
                raise RuntimeError("provider failed in /Users/me/private/live-agents.secret.json")

        class TwoTickStop:
            def __init__(self):
                self.waits = 0

            def is_set(self):
                return False

            def wait(self, seconds):
                del seconds
                self.waits += 1
                return self.waits >= 2

        class FakeSupervisor:
            def snapshot_groups(self):
                return []

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            controller = RaisingController()
            supervisor = FakeSupervisor()
            monitor = LiveAgentSessionRunMonitor(
                root,
                supervisor,
                controller,
                default_server="http://room.local",
                interval_seconds=1,
            )

            monitor._loop(TwoTickStop())
            operations = live_agent_operations_payload(root, limit=10)["operations"]
            health = live_agent_health_payload(root, supervisor, session_run_monitor=monitor)

        self.assertEqual(controller.calls, 2)
        self.assertEqual(len(operations), 2)
        self.assertEqual(operations[-1]["operation"], "session_run.monitor")
        self.assertEqual(operations[-1]["status"], "failed")
        self.assertEqual(operations[-1]["error"], "Live-agent session run monitor failed.")
        self.assertNotIn("/Users/me/private", str(operations))
        self.assertEqual(health["status"], "degraded")
        self.assertEqual(health["session_run_monitor"]["last_status"], "failed")
        self.assertEqual(health["session_run_monitor"]["last_result_count"], 0)
        self.assertEqual(health["session_run_monitor"]["last_error_type"], "RuntimeError")
        self.assertNotIn("/Users/me/private", json.dumps(health, ensure_ascii=False))


    def test_live_agent_session_run_monitor_records_degraded_reconcile_summary(self):
        from agentsassemble.gui import LiveAgentSessionRunMonitor, live_agent_operations_payload

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
            controller = LiveAgentSessionRunController(root)
            controller.begin_run(
                action="ensure",
                payload={
                    "meeting_id": "resident-m1",
                    "group_id": "resident-main",
                    "live_agent_config_path": str(live_agent_config),
                    "server": "http://room.local",
                },
            )

            def degraded_ensure(output_root, process_supervisor, payload, *, default_server):
                del output_root, process_supervisor, default_server
                return {
                    "status": "degraded",
                    "meeting_id": payload["meeting_id"],
                    "group_id": payload["group_id"],
                    "action": "recover",
                }

            monitor = LiveAgentSessionRunMonitor(
                root,
                object(),
                controller,
                default_server="http://room.local",
            )
            with patch("agentsassemble.gui.live_agent_session_ensure_payload", side_effect=degraded_ensure):
                results = monitor.run_once()
            operations = live_agent_operations_payload(root, limit=10)["operations"]

        self.assertEqual(results[0]["status"], "degraded")
        self.assertEqual(operations[-1]["operation"], "session_run.reconcile")
        self.assertEqual(operations[-1]["status"], "degraded")
        self.assertEqual(operations[-1]["details"]["session_run_count"], 1)
        self.assertEqual(operations[-1]["details"]["session_run_failed_count"], 0)
        self.assertEqual(operations[-1]["details"]["session_run_degraded_count"], 1)


    def test_live_agent_session_run_monitor_requires_current_approval_before_real_provider_reconcile(self):
        from agentsassemble.gui import LiveAgentSessionRunMonitor, live_agent_operations_payload

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
                                "command": ["/Users/me/private/bin/claude", "--token", "secret-token"],
                            }
                        ]
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            controller = LiveAgentSessionRunController(root)
            controller.begin_run(
                action="ensure",
                payload={
                    "meeting_id": "resident-m1",
                    "group_id": "resident-main",
                    "live_agent_config_path": str(live_agent_config),
                    "server": "http://room.local",
                },
            )
            monitor = LiveAgentSessionRunMonitor(root, object(), controller, default_server="http://room.local")

            with patch("agentsassemble.gui.live_agent_session_ensure_payload") as ensure_payload:
                ensure_payload.side_effect = AssertionError("approval gate must stop before provider ensure")
                results = monitor.run_once()
            operations = live_agent_operations_payload(root, limit=10)["operations"]
            stored_run = controller.list_runs()[0]

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["status"], "degraded")
        self.assertEqual(stored_run["phase"], "reconcile_failed")
        self.assertIn("requires current operator approval", stored_run["last_error"])
        self.assertEqual(stored_run["reconcile_count"], 1)
        self.assertEqual(operations[-1]["operation"], "session_run.reconcile")
        self.assertEqual(operations[-1]["status"], "degraded")
        serialized = json.dumps({"results": results, "operations": operations, "run": stored_run}, ensure_ascii=False)
        self.assertNotIn("/Users/me/private", serialized)
        self.assertNotIn("secret-token", serialized)
        self.assertNotIn(str(live_agent_config), serialized)


    def test_live_agent_session_run_monitor_checks_persisted_process_config_before_recover(self):
        from agentsassemble.gui import LiveAgentSessionRunMonitor, live_agent_operations_payload

        class PersistedRealProviderSupervisor:
            def __init__(self, config_path: Path) -> None:
                self.config_path = config_path

            def snapshot_groups(self):
                return [
                    {
                        "group_id": "resident-main",
                        "status": "stopped",
                        "meeting_id": "resident-m1",
                        "config_path": str(self.config_path),
                        "server": "http://room.local",
                    }
                ]

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
            controller = LiveAgentSessionRunController(root)
            controller.begin_run(
                action="ensure",
                payload={
                    "meeting_id": "resident-m1",
                    "group_id": "resident-main",
                    "server": "http://room.local",
                },
            )
            monitor = LiveAgentSessionRunMonitor(
                root,
                PersistedRealProviderSupervisor(live_agent_config),
                controller,
                default_server="http://room.local",
            )

            with patch("agentsassemble.gui.live_agent_session_ensure_payload") as ensure_payload:
                ensure_payload.side_effect = AssertionError("persisted process config gate must stop before recover")
                results = monitor.run_once()
            operations = live_agent_operations_payload(root, limit=10)["operations"]
            stored_run = controller.list_runs()[0]

        self.assertEqual(results[0]["status"], "degraded")
        self.assertIn("requires current operator approval", stored_run["last_error"])
        self.assertEqual(stored_run["reconcile_count"], 1)
        self.assertEqual(operations[-1]["status"], "degraded")
        serialized = json.dumps({"results": results, "operations": operations, "run": stored_run}, ensure_ascii=False)
        self.assertNotIn(str(live_agent_config), serialized)


    def test_live_agent_session_run_monitor_fails_closed_when_process_config_cannot_be_inspected(self):
        from agentsassemble.gui import LiveAgentSessionRunMonitor

        class BrokenSupervisor:
            def snapshot_groups(self):
                raise RuntimeError("snapshot failed near /Users/me/private/live-agents.real.json")

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            controller = LiveAgentSessionRunController(root)
            controller.begin_run(
                action="ensure",
                payload={
                    "meeting_id": "resident-m1",
                    "group_id": "resident-main",
                    "server": "http://room.local",
                },
            )
            monitor = LiveAgentSessionRunMonitor(root, BrokenSupervisor(), controller, default_server="http://room.local")

            with patch("agentsassemble.gui.live_agent_session_ensure_payload") as ensure_payload:
                ensure_payload.side_effect = AssertionError("approval gate must fail closed before reconcile")
                results = monitor.run_once()
            stored_run = controller.list_runs()[0]

        self.assertEqual(results[0]["status"], "degraded")
        self.assertIn("requires current operator approval", stored_run["last_error"])
        self.assertEqual(stored_run["reconcile_count"], 1)
        self.assertNotIn("/Users/me/private", json.dumps({"results": results, "run": stored_run}, ensure_ascii=False))


    def test_live_agent_session_run_monitor_skips_mutating_ensure_for_current_ready_run(self):
        from agentsassemble.gui import LiveAgentSessionRunMonitor, live_agent_operations_payload

        class ReadySessionSupervisor:
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
            controller = LiveAgentSessionRunController(root)
            run = controller.begin_run(
                action="ensure",
                payload={
                    "meeting_id": "resident-m1",
                    "group_id": "resident-main",
                    "live_agent_config_path": str(live_agent_config),
                    "server": "http://room.local",
                    "probe_bound_agents": True,
                    "run_remaining_rounds": True,
                    "finalize_after_rounds": True,
                },
            )
            controller.finish_run(
                run["run_id"],
                session={
                    "status": "ready",
                    "meeting_id": "resident-m1",
                    "group_id": "resident-main",
                    "action": "none",
                    "reply_probe": {"status": "ok"},
                    "auto_rounds": {"status": "complete"},
                    "finalization": {"status": "already_finalized"},
                },
            )
            monitor = LiveAgentSessionRunMonitor(
                root,
                ReadySessionSupervisor(),
                controller,
                default_server="http://room.local",
            )

            with patch("agentsassemble.gui.live_agent_session_ensure_payload") as ensure_payload:
                ensure_payload.side_effect = AssertionError("ready monitor tick must stay read-only")
                results = monitor.run_once()
            operations = live_agent_operations_payload(root, limit=10)["operations"]
            stored_run = controller.list_runs()[0]
            snapshot = monitor.snapshot()

        self.assertEqual(results, [])
        self.assertEqual(operations, [])
        self.assertEqual(stored_run["status"], "ready")
        self.assertEqual(stored_run["reconcile_count"], 0)
        self.assertEqual(snapshot["last_status"], "ok")
        self.assertEqual(snapshot["last_result_count"], 0)
        self.assertEqual(snapshot["last_error_type"], "")


    def test_live_agent_session_run_monitor_reconciles_ready_run_with_stale_observation_lag(self):
        from agentsassemble.gui import LiveAgentSessionRunMonitor, live_agent_operations_payload

        class ObservationLagSupervisor:
            def __init__(self, output_root: Path) -> None:
                self.output_root = output_root
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
            controller = LiveAgentSessionRunController(root)
            run = controller.begin_run(
                action="ensure",
                payload={
                    "meeting_id": "resident-m1",
                    "group_id": "resident-main",
                    "connect_timeout_seconds": 0,
                },
            )
            controller.finish_run(
                run["run_id"],
                session={"status": "ready", "meeting_id": "resident-m1", "group_id": "resident-main", "action": "none"},
            )
            supervisor = ObservationLagSupervisor(root)
            monitor = LiveAgentSessionRunMonitor(root, supervisor, controller, default_server="http://room.local")

            results = monitor.run_once()
            operations = live_agent_operations_payload(root, limit=10)["operations"]
            stored_run = controller.list_runs()[0]

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["status"], "ready")
        self.assertEqual(results[0]["result"]["ensure_reason"], "stale_lobby_observation")
        self.assertEqual(stored_run["phase"], "restart")
        self.assertEqual(stored_run["result"]["ensure_reason"], "stale_lobby_observation")
        self.assertEqual(stored_run["reconcile_count"], 1)
        self.assertEqual(supervisor.stopped, ["resident-main"])
        self.assertEqual(supervisor.restarted, ["resident-main"])
        self.assertEqual(operations[-1]["operation"], "session_run.reconcile")


    def test_live_agent_session_run_monitor_stops_stale_observation_restart_after_budget(self):
        from agentsassemble.gui import LiveAgentSessionRunMonitor

        class BudgetSupervisor:
            def __init__(self, output_root: Path) -> None:
                self.output_root = output_root
                self.restart_counts = []
                self.group = {
                    "group_id": "resident-main",
                    "status": "running",
                    "meeting_id": "resident-m1",
                    "agents": [{"agent_id": "agent-a"}],
                    "auto_restart": True,
                    "max_restarts": 1,
                    "restart_count": 0,
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
            controller = LiveAgentSessionRunController(root)
            run = controller.begin_run(
                action="ensure",
                payload={
                    "meeting_id": "resident-m1",
                    "group_id": "resident-main",
                    "connect_timeout_seconds": 0,
                },
            )
            controller.finish_run(
                run["run_id"],
                session={"status": "ready", "meeting_id": "resident-m1", "group_id": "resident-main", "action": "none"},
            )
            monitor = LiveAgentSessionRunMonitor(root, BudgetSupervisor(root), controller, default_server="http://room.local")

            first_results = monitor.run_once()
            second_results = monitor.run_once()

        self.assertEqual(len(first_results), 1)
        self.assertEqual(second_results, [])
        self.assertEqual(monitor.process_supervisor.restart_counts, [1])


    def test_live_agent_session_run_monitor_replays_current_run_target_when_original_request_was_blank(self):
        from agentsassemble.gui import LiveAgentSessionRunMonitor

        class ObservationLagSupervisor:
            def snapshot_groups(self):
                return [
                    {
                        "group_id": "resident-main",
                        "status": "running",
                        "meeting_id": "resident-m1",
                        "agents": [{"agent_id": "agent-a"}],
                        "auto_restart": True,
                        "max_restarts": 3,
                        "restart_count": 0,
                        "stale_restart_after_seconds": 1,
                    }
                ]

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
            controller = LiveAgentSessionRunController(root)
            run = controller.begin_run(action="ensure", payload={})
            controller.finish_run(
                run["run_id"],
                session={"status": "ready", "meeting_id": "resident-m1", "group_id": "resident-main", "action": "none"},
            )
            monitor = LiveAgentSessionRunMonitor(root, ObservationLagSupervisor(), controller, default_server="http://room.local")

            def checked_ensure(output_root, process_supervisor, payload, *, default_server):
                del output_root, process_supervisor, default_server
                self.assertEqual(payload["meeting_id"], "resident-m1")
                self.assertEqual(payload["group_id"], "resident-main")
                return {"status": "ready", "meeting_id": "resident-m1", "group_id": "resident-main", "action": "restart"}

            with patch("agentsassemble.gui.live_agent_session_ensure_payload", side_effect=checked_ensure):
                results = monitor.run_once()

        self.assertEqual(results[0]["status"], "ready")


    def test_live_agent_session_run_monitor_stop_waits_until_in_flight_tick_finishes(self):
        from agentsassemble.gui import LiveAgentSessionRunMonitor

        class BlockingMonitor(LiveAgentSessionRunMonitor):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                self.entered = threading.Event()
                self.release = threading.Event()

            def run_once(self):
                self.entered.set()
                self.release.wait(timeout=2)
                return []

        with tempfile.TemporaryDirectory() as temp_dir:
            monitor = BlockingMonitor(
                Path(temp_dir),
                object(),
                LiveAgentSessionRunController(Path(temp_dir)),
                default_server="http://room.local",
                interval_seconds=1,
            )
            monitor.start()
            self.assertTrue(monitor.entered.wait(timeout=1))
            stopper = threading.Thread(target=lambda: monitor.stop(timeout_seconds=None))
            stopper.start()
            time.sleep(0.05)
            still_waiting = stopper.is_alive()
            monitor.release.set()
            stopper.join(timeout=1)

        self.assertTrue(still_waiting)
        self.assertFalse(stopper.is_alive())


    def test_serve_gui_stops_session_run_monitor_before_process_supervisor(self):
        events = []

        class FakeProcessSupervisor:
            def start_monitor(self):
                events.append("process_start")

            def close(self):
                events.append("process_close")

        class FakeSessionRunMonitor:
            def __init__(self, *args, **kwargs):
                del args, kwargs

            def start(self):
                events.append("session_start")

            def stop(self, *, timeout_seconds=5.0):
                del timeout_seconds
                events.append("session_stop")

        class FakeServer:
            server_address = ("127.0.0.1", 8765)

            def serve_forever(self):
                raise KeyboardInterrupt()

            def server_close(self):
                events.append("server_close")

        with tempfile.TemporaryDirectory() as temp_dir:
            with patch("agentsassemble.gui.LiveAgentProcessSupervisor", return_value=FakeProcessSupervisor()):
                with patch("agentsassemble.gui.LiveAgentSessionRunMonitor", FakeSessionRunMonitor):
                    with patch("agentsassemble.gui.ThreadingHTTPServer", return_value=FakeServer()):
                        serve_gui(output_root=Path(temp_dir))

        self.assertEqual(events[:2], ["process_start", "session_start"])
        self.assertLess(events.index("session_stop"), events.index("process_close"))
        self.assertEqual(events[-1], "server_close")
