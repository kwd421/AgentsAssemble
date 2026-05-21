import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

from agentsassemble.live_agent_session_runs import LiveAgentSessionRunController


class LiveAgentSessionRunControllerTests(unittest.TestCase):
    def test_finish_run_persists_active_session_intent_for_recovery(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            now = {"value": datetime(2026, 5, 21, 9, 0, tzinfo=UTC)}
            controller = LiveAgentSessionRunController(root, now_fn=lambda: now["value"])

            run = controller.begin_run(
                action="ensure",
                payload={
                    "meeting_id": "resident-m1",
                    "group_id": "resident-main",
                    "live_agent_config_path": "configs/live-agents.example.json",
                    "server": "http://room.local",
                    "probe_bound_agents": True,
                    "run_remaining_rounds": True,
                    "finalize_after_rounds": True,
                },
            )
            now["value"] += timedelta(seconds=2)
            controller.finish_run(
                run["run_id"],
                session={
                    "status": "ready",
                    "meeting_id": "resident-m1",
                    "group_id": "resident-main",
                    "action": "none",
                    "process": {"status": "running"},
                    "connection": {"expected": 1, "connected": 1},
                    "auto_rounds": {"status": "answered", "round_count": 1},
                    "finalization": {"status": "finalized", "official_event_count": 1},
                },
            )

            reloaded = LiveAgentSessionRunController(root)
            runs = reloaded.list_runs()

        self.assertEqual(len(runs), 1)
        self.assertEqual(runs[0]["run_id"], run["run_id"])
        self.assertEqual(runs[0]["action"], "ensure")
        self.assertEqual(runs[0]["status"], "ready")
        self.assertTrue(runs[0]["active"])
        self.assertEqual(runs[0]["phase"], "none")
        self.assertEqual(runs[0]["meeting_id"], "resident-m1")
        self.assertEqual(runs[0]["group_id"], "resident-main")
        self.assertNotIn("live_agent_config_path", runs[0]["request"])
        self.assertNotIn("http://room.local", str(runs[0]))
        self.assertNotIn("configs/live-agents.example.json", str(runs[0]))
        self.assertEqual(runs[0]["result"]["connection"]["connected"], 1)
        self.assertEqual(runs[0]["result"]["auto_rounds"]["status"], "answered")

    def test_list_runs_filters_by_meeting_and_group_before_limit(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            controller = LiveAgentSessionRunController(root)
            target = controller.begin_run(
                action="ensure",
                payload={"meeting_id": "resident-m1", "group_id": "resident-main"},
            )
            controller.finish_run(
                target["run_id"],
                session={
                    "status": "running",
                    "meeting_id": "resident-m1",
                    "group_id": "resident-main",
                    "action": "start",
                },
            )
            unrelated = controller.begin_run(
                action="ensure",
                payload={"meeting_id": "resident-m2", "group_id": "resident-alt"},
            )
            controller.finish_run(
                unrelated["run_id"],
                session={
                    "status": "ready",
                    "meeting_id": "resident-m2",
                    "group_id": "resident-alt",
                    "action": "none",
                },
            )

            runs = controller.list_runs(limit=1, meeting_id="resident-m1", group_id="resident-main")

        self.assertEqual([run["run_id"] for run in runs], [target["run_id"]])
        self.assertEqual(runs[0]["meeting_id"], "resident-m1")
        self.assertEqual(runs[0]["group_id"], "resident-main")

    def test_list_runs_invalid_nonempty_filter_does_not_broaden_listing(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
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

            runs = controller.list_runs(limit=1, meeting_id="../resident-m1", group_id="resident-main")

        self.assertEqual(runs, [])

    def test_reconcile_active_runs_uses_persisted_request_and_skips_stopped_runs(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            current = {"value": datetime(2026, 5, 21, 10, 0, tzinfo=UTC)}
            controller = LiveAgentSessionRunController(root, now_fn=lambda: current["value"])
            active = controller.begin_run(
                action="ensure",
                payload={
                    "meeting_id": "resident-m1",
                    "group_id": "resident-main",
                    "live_agent_config_path": "configs/live-agents.example.json",
                    "server": "http://room.local",
                    "connect_timeout_seconds": 0.1,
                },
            )
            controller.finish_run(
                active["run_id"],
                session={
                    "status": "ready",
                    "meeting_id": "resident-m1",
                    "group_id": "resident-main",
                    "action": "none",
                },
            )
            stopped = controller.begin_run(
                action="ensure",
                payload={
                    "meeting_id": "resident-m2",
                    "group_id": "resident-old",
                    "live_agent_config_path": "configs/old.json",
                },
            )
            controller.mark_matching_stopped(meeting_id="resident-m2", group_id="resident-old", reason="operator_stop")

            observed_requests = []
            current["value"] += timedelta(minutes=5)
            reloaded = LiveAgentSessionRunController(root, now_fn=lambda: current["value"])
            results = reloaded.reconcile_active_runs(
                lambda run: observed_requests.append(dict(run["request"]))
                or {
                    "status": "ready",
                    "meeting_id": run["meeting_id"],
                    "group_id": run["group_id"],
                    "action": "none",
                }
            )
            runs = {item["run_id"]: item for item in reloaded.list_runs()}

        self.assertEqual(len(results), 1)
        self.assertEqual(observed_requests[0]["live_agent_config_path"], "configs/live-agents.example.json")
        self.assertEqual(observed_requests[0]["server"], "http://room.local")
        self.assertEqual(runs[active["run_id"]]["status"], "ready")
        self.assertEqual(runs[active["run_id"]]["reconcile_count"], 1)
        self.assertEqual(runs[active["run_id"]]["last_reconciled_at"], current["value"].isoformat())
        self.assertEqual(runs[stopped["run_id"]]["status"], "stopped")
        self.assertFalse(runs[stopped["run_id"]]["active"])

    def test_reconcile_active_runs_does_not_resurrect_run_stopped_during_callback(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            controller = LiveAgentSessionRunController(root)
            run = controller.begin_run(
                action="ensure",
                payload={
                    "meeting_id": "resident-m1",
                    "group_id": "resident-main",
                    "live_agent_config_path": "configs/live-agents.example.json",
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

            def stop_then_return_ready(reconcile_run):
                controller.mark_matching_stopped(
                    meeting_id=str(reconcile_run.get("meeting_id") or ""),
                    group_id=str(reconcile_run.get("group_id") or ""),
                    reason="session.stop",
                )
                return {
                    "status": "ready",
                    "meeting_id": "resident-m1",
                    "group_id": "resident-main",
                    "action": "none",
                }

            results = controller.reconcile_active_runs(stop_then_return_ready)
            stored_run = controller.list_runs()[0]

        self.assertEqual(results[0]["run_id"], run["run_id"])
        self.assertEqual(results[0]["status"], "stopped")
        self.assertFalse(results[0]["active"])
        self.assertEqual(stored_run["status"], "stopped")
        self.assertFalse(stored_run["active"])

    def test_reconciled_finish_preserves_terminal_record_at_write_boundary(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            controller = LiveAgentSessionRunController(root)
            run = controller.begin_run(
                action="ensure",
                payload={
                    "meeting_id": "resident-m1",
                    "group_id": "resident-main",
                    "live_agent_config_path": "configs/live-agents.example.json",
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
            controller.mark_matching_stopped(
                meeting_id="resident-m1",
                group_id="resident-main",
                reason="session.stop",
            )

            result = controller._finish_reconciled_run_if_active(
                run["run_id"],
                session={
                    "status": "ready",
                    "meeting_id": "resident-m1",
                    "group_id": "resident-main",
                    "action": "none",
                },
            )
            stored_run = controller.list_runs()[0]

        self.assertEqual(result["status"], "stopped")
        self.assertFalse(result["active"])
        self.assertEqual(stored_run["status"], "stopped")
        self.assertFalse(stored_run["active"])

    def test_fail_run_records_sanitized_error_without_leaking_request_details(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            controller = LiveAgentSessionRunController(root)
            run = controller.begin_run(
                action="ensure",
                payload={
                    "meeting_id": "resident-m1",
                    "group_id": "resident-main",
                    "live_agent_config_path": "/Users/me/private/live-agents.local.json",
                    "server": "https://private.example",
                },
            )

            controller.fail_run(
                run["run_id"],
                "provider failed with token sk-test-secret and config /Users/me/private/live-agents.local.json",
            )
            failed = controller.list_runs()[0]

        self.assertEqual(failed["status"], "failed")
        self.assertFalse(failed["active"])
        self.assertEqual(failed["last_error"], "Live-agent session run error details redacted.")
        self.assertNotIn("sk-test-secret", str(failed))
        self.assertNotIn("/Users/me/private", str(failed))
        self.assertNotIn("https://private.example", str(failed))


if __name__ == "__main__":
    unittest.main()
