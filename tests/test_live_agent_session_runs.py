import json
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

    def test_finish_run_consumes_successful_post_ready_request_fields(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            controller = LiveAgentSessionRunController(root)
            run = controller.begin_run(
                action="ensure",
                payload={
                    "meeting_id": "resident-m1",
                    "group_id": "resident-main",
                    "live_agent_config_path": "configs/live-agents.example.json",
                    "server": "http://room.local",
                    "probe_bound_agents": True,
                    "probe_timeout_seconds": 3,
                    "run_remaining_rounds": True,
                    "round_timeout_seconds": 5,
                    "round_max_rounds": 2,
                    "round_stop_on_timeout": True,
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
                    "reply_probe": {"status": "ok", "ok_count": 1},
                    "auto_rounds": {"status": "answered", "answered_round_count": 1},
                    "finalization": {"status": "finalized", "official_event_count": 1},
                },
            )

            reloaded = LiveAgentSessionRunController(root)
            observed_requests = []
            reloaded.reconcile_active_runs(
                lambda reconcile_run: observed_requests.append(dict(reconcile_run["request"]))
                or {
                    "status": "ready",
                    "meeting_id": "resident-m1",
                    "group_id": "resident-main",
                    "action": "none",
                }
            )
            public_run = reloaded.list_runs()[0]

        self.assertEqual(public_run["status"], "ready")
        for key in (
            "probe_bound_agents",
            "probe_timeout_seconds",
            "run_remaining_rounds",
            "round_timeout_seconds",
            "round_max_rounds",
            "round_stop_on_timeout",
            "finalize_after_rounds",
        ):
            self.assertNotIn(key, public_run["request"])
            self.assertNotIn(key, observed_requests[0])

    def test_legacy_ready_run_consumes_successful_post_ready_request_fields_on_read(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            runs_dir = root / "live-agent-runs"
            runs_dir.mkdir(parents=True)
            (runs_dir / "session-runs.json").write_text(
                json.dumps(
                    {
                        "runs": [
                            {
                                "run_id": "legacy-ready",
                                "action": "ensure",
                                "status": "ready",
                                "active": True,
                                "phase": "none",
                                "meeting_id": "resident-m1",
                                "group_id": "resident-main",
                                "request": {
                                    "meeting_id": "resident-m1",
                                    "group_id": "resident-main",
                                    "live_agent_config_path": "configs/live-agents.example.json",
                                    "server": "http://room.local",
                                    "probe_bound_agents": True,
                                    "probe_timeout_seconds": 3,
                                    "run_remaining_rounds": True,
                                    "round_timeout_seconds": 5,
                                    "round_max_rounds": 2,
                                    "round_stop_on_timeout": True,
                                    "finalize_after_rounds": True,
                                },
                                "result": {
                                    "status": "ready",
                                    "meeting_id": "resident-m1",
                                    "group_id": "resident-main",
                                    "action": "none",
                                    "reply_probe": {"status": "ok"},
                                    "auto_rounds": {"status": "answered"},
                                    "finalization": {"status": "finalized"},
                                },
                                "last_error": "",
                                "created_at": "2026-05-21T10:00:00+00:00",
                                "updated_at": "2026-05-21T10:00:01+00:00",
                                "finished_at": "2026-05-21T10:00:01+00:00",
                                "last_reconciled_at": "",
                                "reconcile_count": 0,
                            }
                        ]
                    },
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )

            controller = LiveAgentSessionRunController(root)
            public_run = controller.list_runs()[0]
            observed_requests = []
            controller.reconcile_active_runs(
                lambda reconcile_run: observed_requests.append(dict(reconcile_run["request"]))
                or {
                    "status": "ready",
                    "meeting_id": "resident-m1",
                    "group_id": "resident-main",
                    "action": "none",
                }
            )

        for key in (
            "probe_bound_agents",
            "probe_timeout_seconds",
            "run_remaining_rounds",
            "round_timeout_seconds",
            "round_max_rounds",
            "round_stop_on_timeout",
            "finalize_after_rounds",
        ):
            self.assertNotIn(key, public_run["request"])
            self.assertNotIn(key, observed_requests[0])

    def test_finish_run_keeps_failed_post_ready_request_fields_for_retry(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            now = {"value": datetime(2026, 5, 21, 10, 0, tzinfo=UTC)}
            controller = LiveAgentSessionRunController(root, now_fn=lambda: now["value"])
            run = controller.begin_run(
                action="ensure",
                payload={
                    "meeting_id": "resident-m1",
                    "group_id": "resident-main",
                    "live_agent_config_path": "configs/live-agents.example.json",
                    "server": "http://room.local",
                    "probe_bound_agents": True,
                    "probe_timeout_seconds": 3,
                    "run_remaining_rounds": True,
                    "round_timeout_seconds": 5,
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
                    "reply_probe": {"status": "timeout", "timeout_count": 1},
                    "auto_rounds": {"status": "skipped", "reason": "probe_not_ready"},
                    "finalization": {"status": "skipped", "reason": "rounds_not_ready"},
                },
            )

            observed_requests = []
            reloaded = LiveAgentSessionRunController(root, now_fn=lambda: now["value"])
            initial_run = reloaded.list_runs()[0]
            now["value"] = datetime(2026, 5, 21, 10, 1, tzinfo=UTC)
            reloaded.reconcile_active_runs(
                lambda reconcile_run: observed_requests.append(dict(reconcile_run["request"]))
                or {
                    "status": "ready",
                    "meeting_id": "resident-m1",
                    "group_id": "resident-main",
                    "action": "none",
                    "reply_probe": {"status": "ok"},
                    "auto_rounds": {"status": "answered"},
                    "finalization": {"status": "finalized"},
                }
            )
            public_run = reloaded.list_runs()[0]

        self.assertEqual(initial_run["status"], "degraded")
        self.assertTrue(initial_run["active"])
        self.assertEqual(initial_run["next_reconcile_at"], "2026-05-21T10:01:00+00:00")
        self.assertEqual(public_run["status"], "ready")
        self.assertTrue(observed_requests[0]["probe_bound_agents"])
        self.assertTrue(observed_requests[0]["run_remaining_rounds"])
        self.assertTrue(observed_requests[0]["finalize_after_rounds"])

    def test_reconcile_active_runs_skips_not_due_degraded_run(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            runs_dir = root / "live-agent-runs"
            runs_dir.mkdir(parents=True)
            (runs_dir / "session-runs.json").write_text(
                json.dumps(
                    {
                        "runs": [
                            {
                                "run_id": "retry-later",
                                "action": "ensure",
                                "status": "degraded",
                                "active": True,
                                "phase": "reconcile_failed",
                                "meeting_id": "resident-m1",
                                "group_id": "resident-main",
                                "request": {
                                    "meeting_id": "resident-m1",
                                    "group_id": "resident-main",
                                    "live_agent_config_path": "configs/live-agents.example.json",
                                },
                                "result": {"status": "degraded"},
                                "last_error": "",
                                "created_at": "2026-05-21T10:00:00+00:00",
                                "updated_at": "2026-05-21T10:00:01+00:00",
                                "finished_at": "2026-05-21T10:00:01+00:00",
                                "last_reconciled_at": "2026-05-21T10:00:01+00:00",
                                "next_reconcile_at": "2026-05-21T10:02:00+00:00",
                                "reconcile_failure_count": 1,
                                "reconcile_backoff_seconds": 60,
                                "reconcile_count": 1,
                            }
                        ]
                    },
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )
            controller = LiveAgentSessionRunController(
                root,
                now_fn=lambda: datetime(2026, 5, 21, 10, 1, tzinfo=UTC),
            )

            calls = []
            results = controller.reconcile_active_runs(lambda run: calls.append(run) or {"status": "ready"})
            stored_run = controller.list_runs()[0]

        self.assertEqual(results, [])
        self.assertEqual(calls, [])
        self.assertEqual(stored_run["status"], "degraded")
        self.assertEqual(stored_run["next_reconcile_at"], "2026-05-21T10:02:00+00:00")
        self.assertEqual(stored_run["reconcile_failure_count"], 1)
        self.assertEqual(stored_run["reconcile_backoff_seconds"], 60)
        self.assertEqual(stored_run["reconcile_count"], 1)

    def test_retry_run_now_makes_backoff_run_due_without_losing_failure_evidence(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            runs_dir = root / "live-agent-runs"
            runs_dir.mkdir(parents=True)
            (runs_dir / "session-runs.json").write_text(
                json.dumps(
                    {
                        "runs": [
                            {
                                "run_id": "retry-later",
                                "action": "ensure",
                                "status": "degraded",
                                "active": True,
                                "phase": "reconcile_failed",
                                "meeting_id": "resident-m1",
                                "group_id": "resident-main",
                                "request": {
                                    "meeting_id": "resident-m1",
                                    "group_id": "resident-main",
                                    "live_agent_config_path": "configs/live-agents.example.json",
                                },
                                "result": {"status": "degraded"},
                                "last_error": "provider temporarily missing",
                                "created_at": "2026-05-21T10:00:00+00:00",
                                "updated_at": "2026-05-21T10:00:01+00:00",
                                "finished_at": "2026-05-21T10:00:01+00:00",
                                "last_reconciled_at": "2026-05-21T10:00:01+00:00",
                                "next_reconcile_at": "2026-05-21T10:30:00+00:00",
                                "reconcile_failure_count": 2,
                                "reconcile_backoff_seconds": 120,
                                "reconcile_count": 3,
                            }
                        ]
                    },
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )
            now = {"value": datetime(2026, 5, 21, 10, 2, tzinfo=UTC)}
            controller = LiveAgentSessionRunController(root, now_fn=lambda: now["value"])

            retried = controller.retry_run_now("retry-later")
            calls = []
            results = controller.reconcile_active_runs(
                lambda run: calls.append(run)
                or {
                    "status": "ready",
                    "meeting_id": "resident-m1",
                    "group_id": "resident-main",
                    "action": "recover",
                }
            )

        self.assertEqual(retried["status"], "degraded")
        self.assertEqual(retried["phase"], "retry_requested")
        self.assertEqual(retried["next_reconcile_at"], "")
        self.assertEqual(retried["reconcile_failure_count"], 2)
        self.assertEqual(retried["reconcile_backoff_seconds"], 0)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["run_id"], "retry-later")
        self.assertEqual(results[0]["status"], "ready")

    def test_retry_run_now_refuses_terminal_run(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            controller = LiveAgentSessionRunController(root)
            run = controller.begin_run(action="ensure", payload={"meeting_id": "resident-m1", "group_id": "resident-main"})
            controller.fail_run(run["run_id"], "operator-visible failure")

            with self.assertRaises(ValueError):
                controller.retry_run_now(run["run_id"])

    def test_retry_run_now_refuses_inactive_nonterminal_run(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            runs_dir = root / "live-agent-runs"
            runs_dir.mkdir(parents=True)
            (runs_dir / "session-runs.json").write_text(
                json.dumps(
                    {
                        "runs": [
                            {
                                "run_id": "inactive-degraded",
                                "action": "ensure",
                                "status": "degraded",
                                "active": False,
                                "phase": "operator_paused",
                                "meeting_id": "resident-m1",
                                "group_id": "resident-main",
                                "request": {"meeting_id": "resident-m1", "group_id": "resident-main"},
                                "created_at": "2026-05-21T10:00:00+00:00",
                                "updated_at": "2026-05-21T10:00:00+00:00",
                            }
                        ]
                    },
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )
            controller = LiveAgentSessionRunController(root)

            with self.assertRaises(ValueError):
                controller.retry_run_now("inactive-degraded")
            stored_run = controller.get_run("inactive-degraded")

        self.assertFalse(stored_run["active"])
        self.assertEqual(stored_run["phase"], "operator_paused")

    def test_pause_run_skips_reconcile_until_resumed(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            now = {"value": datetime(2026, 5, 21, 10, 0, tzinfo=UTC)}
            controller = LiveAgentSessionRunController(root, now_fn=lambda: now["value"])
            run = controller.begin_run(action="ensure", payload={"meeting_id": "resident-m1", "group_id": "resident-main"})
            controller.finish_run(
                run["run_id"],
                session={
                    "status": "degraded",
                    "meeting_id": "resident-m1",
                    "group_id": "resident-main",
                    "action": "recover",
                },
            )
            now["value"] = datetime(2026, 5, 21, 10, 2, tzinfo=UTC)

            paused = controller.pause_run(run["run_id"])
            paused_calls = []
            paused_results = controller.reconcile_active_runs(
                lambda reconcile_run: paused_calls.append(reconcile_run) or {"status": "ready"}
            )
            resumed = controller.resume_run(run["run_id"])
            resumed_calls = []
            resumed_results = controller.reconcile_active_runs(
                lambda reconcile_run: resumed_calls.append(reconcile_run)
                or {
                    "status": "ready",
                    "meeting_id": "resident-m1",
                    "group_id": "resident-main",
                    "action": "recover",
                }
            )

        self.assertEqual(paused["status"], "paused")
        self.assertFalse(paused["active"])
        self.assertEqual(paused["phase"], "paused")
        self.assertEqual(paused["paused_status"], "degraded")
        self.assertEqual(paused_results, [])
        self.assertEqual(paused_calls, [])
        self.assertEqual(resumed["status"], "degraded")
        self.assertTrue(resumed["active"])
        self.assertEqual(resumed["phase"], "resume_requested")
        self.assertEqual(resumed["paused_status"], "")
        self.assertEqual(len(resumed_calls), 1)
        self.assertEqual(resumed_calls[0]["run_id"], run["run_id"])
        self.assertEqual(resumed_results[0]["status"], "ready")

    def test_pause_run_refuses_terminal_run_and_resume_refuses_unpaused_run(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            controller = LiveAgentSessionRunController(root)
            stopped = controller.begin_run(action="ensure", payload={"meeting_id": "resident-m1", "group_id": "resident-main"})
            controller.fail_run(stopped["run_id"], "operator-visible failure")
            active = controller.begin_run(action="ensure", payload={"meeting_id": "resident-m2", "group_id": "resident-main"})

            with self.assertRaises(ValueError):
                controller.pause_run(stopped["run_id"])
            with self.assertRaises(ValueError):
                controller.resume_run(active["run_id"])

    def test_finish_run_does_not_overwrite_paused_in_flight_run(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            controller = LiveAgentSessionRunController(root)
            run = controller.begin_run(action="ensure", payload={"meeting_id": "resident-m1", "group_id": "resident-main"})
            paused = controller.pause_run(run["run_id"])

            finished = controller.finish_run(
                run["run_id"],
                session={
                    "status": "ready",
                    "meeting_id": "resident-m1",
                    "group_id": "resident-main",
                    "action": "recover",
                },
            )
            stored = controller.get_run(run["run_id"])

        self.assertEqual(paused["status"], "paused")
        self.assertEqual(finished["status"], "paused")
        self.assertFalse(finished["active"])
        self.assertEqual(finished["paused_status"], "running")
        self.assertEqual(stored["status"], "paused")
        self.assertFalse(stored["active"])

    def test_fail_run_does_not_overwrite_paused_in_flight_run(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            controller = LiveAgentSessionRunController(root)
            run = controller.begin_run(action="ensure", payload={"meeting_id": "resident-m1", "group_id": "resident-main"})
            paused = controller.pause_run(run["run_id"])

            failed = controller.fail_run(run["run_id"], "provider timed out")
            stored = controller.get_run(run["run_id"])

        self.assertEqual(paused["status"], "paused")
        self.assertEqual(failed["status"], "paused")
        self.assertFalse(failed["active"])
        self.assertEqual(failed["last_error"], "")
        self.assertEqual(stored["status"], "paused")
        self.assertFalse(stored["active"])

    def test_resume_run_with_missing_paused_status_falls_back_to_degraded(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            runs_dir = root / "live-agent-runs"
            runs_dir.mkdir(parents=True)
            (runs_dir / "session-runs.json").write_text(
                json.dumps(
                    {
                        "runs": [
                            {
                                "run_id": "legacy-paused",
                                "action": "ensure",
                                "status": "paused",
                                "active": False,
                                "phase": "paused",
                                "meeting_id": "resident-m1",
                                "group_id": "resident-main",
                                "request": {"meeting_id": "resident-m1", "group_id": "resident-main"},
                                "created_at": "2026-05-21T10:00:00+00:00",
                                "updated_at": "2026-05-21T10:00:00+00:00",
                            }
                        ]
                    },
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )
            controller = LiveAgentSessionRunController(root)

            loaded = controller.get_run("legacy-paused")
            resumed = controller.resume_run("legacy-paused")

        self.assertEqual(loaded["paused_status"], "")
        self.assertEqual(resumed["status"], "degraded")
        self.assertTrue(resumed["active"])

    def test_reconcile_failure_keeps_run_active_degraded_with_retry_backoff(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            now = {"value": datetime(2026, 5, 21, 10, 0, tzinfo=UTC)}
            controller = LiveAgentSessionRunController(root, now_fn=lambda: now["value"])
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
                    "status": "degraded",
                    "meeting_id": "resident-m1",
                    "group_id": "resident-main",
                    "action": "recover",
                },
            )
            now["value"] = datetime(2026, 5, 21, 10, 5, tzinfo=UTC)

            results = controller.reconcile_active_runs(
                lambda _run: (_ for _ in ()).throw(
                    RuntimeError("provider failed with /Users/me/private/live-agents.secret.json")
                )
            )
            stored_run = controller.list_runs()[0]

        self.assertEqual(results[0]["status"], "degraded")
        self.assertTrue(results[0]["active"])
        self.assertEqual(results[0]["phase"], "reconcile_failed")
        self.assertEqual(results[0]["reconcile_failure_count"], 2)
        self.assertEqual(results[0]["reconcile_backoff_seconds"], 120)
        self.assertEqual(results[0]["next_reconcile_at"], "2026-05-21T10:07:00+00:00")
        self.assertEqual(stored_run["status"], "degraded")
        self.assertTrue(stored_run["active"])
        self.assertEqual(stored_run["last_error"], "Live-agent session run error details redacted.")
        self.assertNotIn("/Users/me/private", str(stored_run))

    def test_reconcile_failure_after_ready_survives_reload_as_degraded_retry(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            now = {"value": datetime(2026, 5, 21, 10, 0, tzinfo=UTC)}
            controller = LiveAgentSessionRunController(root, now_fn=lambda: now["value"])
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
            now["value"] = datetime(2026, 5, 21, 10, 1, tzinfo=UTC)

            controller.reconcile_active_runs(lambda _run: (_ for _ in ()).throw(RuntimeError("temporary provider miss")))
            reloaded = LiveAgentSessionRunController(root, now_fn=lambda: now["value"])
            stored_run = reloaded.list_runs()[0]

        self.assertEqual(stored_run["status"], "degraded")
        self.assertTrue(stored_run["active"])
        self.assertEqual(stored_run["phase"], "reconcile_failed")
        self.assertEqual(stored_run["result"]["status"], "degraded")
        self.assertEqual(stored_run["reconcile_failure_count"], 1)
        self.assertEqual(stored_run["reconcile_backoff_seconds"], 60)
        self.assertEqual(stored_run["next_reconcile_at"], "2026-05-21T10:02:00+00:00")

    def test_reload_preserves_explicit_degraded_retry_over_stale_ready_result(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            runs_dir = root / "live-agent-runs"
            runs_dir.mkdir(parents=True)
            (runs_dir / "session-runs.json").write_text(
                json.dumps(
                    {
                        "runs": [
                            {
                                "run_id": "stale-ready-result",
                                "action": "ensure",
                                "status": "degraded",
                                "active": True,
                                "phase": "reconcile_failed",
                                "meeting_id": "resident-m1",
                                "group_id": "resident-main",
                                "request": {
                                    "meeting_id": "resident-m1",
                                    "group_id": "resident-main",
                                    "live_agent_config_path": "configs/live-agents.example.json",
                                },
                                "result": {
                                    "status": "ready",
                                    "meeting_id": "resident-m1",
                                    "group_id": "resident-main",
                                    "action": "none",
                                },
                                "last_error": "temporary provider miss",
                                "created_at": "2026-05-21T10:00:00+00:00",
                                "updated_at": "2026-05-21T10:01:00+00:00",
                                "finished_at": "2026-05-21T10:01:00+00:00",
                                "last_reconciled_at": "2026-05-21T10:01:00+00:00",
                                "next_reconcile_at": "2026-05-21T10:02:00+00:00",
                                "reconcile_failure_count": 1,
                                "reconcile_backoff_seconds": 60,
                                "reconcile_count": 1,
                            }
                        ]
                    },
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )

            stored_run = LiveAgentSessionRunController(root).list_runs()[0]

        self.assertEqual(stored_run["status"], "degraded")
        self.assertTrue(stored_run["active"])
        self.assertEqual(stored_run["phase"], "reconcile_failed")
        self.assertEqual(stored_run["reconcile_failure_count"], 1)
        self.assertEqual(stored_run["next_reconcile_at"], "2026-05-21T10:02:00+00:00")

    def test_reload_preserves_recovering_retry_over_stale_ready_result(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            runs_dir = root / "live-agent-runs"
            runs_dir.mkdir(parents=True)
            (runs_dir / "session-runs.json").write_text(
                json.dumps(
                    {
                        "runs": [
                            {
                                "run_id": "recovering-ready-result",
                                "action": "ensure",
                                "status": "recovering",
                                "active": True,
                                "phase": "reconcile",
                                "meeting_id": "resident-m1",
                                "group_id": "resident-main",
                                "request": {
                                    "meeting_id": "resident-m1",
                                    "group_id": "resident-main",
                                    "live_agent_config_path": "configs/live-agents.example.json",
                                },
                                "result": {
                                    "status": "ready",
                                    "meeting_id": "resident-m1",
                                    "group_id": "resident-main",
                                    "action": "none",
                                },
                                "last_error": "",
                                "created_at": "2026-05-21T10:00:00+00:00",
                                "updated_at": "2026-05-21T10:01:00+00:00",
                                "finished_at": "2026-05-21T10:00:00+00:00",
                                "last_reconciled_at": "2026-05-21T10:01:00+00:00",
                                "next_reconcile_at": "2026-05-21T10:02:00+00:00",
                                "reconcile_failure_count": 1,
                                "reconcile_backoff_seconds": 60,
                                "reconcile_count": 1,
                            }
                        ]
                    },
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )

            stored_run = LiveAgentSessionRunController(root).list_runs()[0]

        self.assertEqual(stored_run["status"], "recovering")
        self.assertTrue(stored_run["active"])
        self.assertEqual(stored_run["phase"], "reconcile")
        self.assertEqual(stored_run["reconcile_failure_count"], 1)
        self.assertEqual(stored_run["reconcile_backoff_seconds"], 60)
        self.assertEqual(stored_run["next_reconcile_at"], "2026-05-21T10:02:00+00:00")

    def test_initial_degraded_finish_schedules_retry_backoff(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            now = {"value": datetime(2026, 5, 21, 10, 0, tzinfo=UTC)}
            controller = LiveAgentSessionRunController(root, now_fn=lambda: now["value"])
            run = controller.begin_run(
                action="ensure",
                payload={
                    "meeting_id": "resident-m1",
                    "group_id": "resident-main",
                    "live_agent_config_path": "configs/live-agents.example.json",
                },
            )

            finished = controller.finish_run(
                run["run_id"],
                session={
                    "status": "degraded",
                    "meeting_id": "resident-m1",
                    "group_id": "resident-main",
                    "action": "recover",
                },
            )
            stored_run = LiveAgentSessionRunController(root, now_fn=lambda: now["value"]).list_runs()[0]

        self.assertEqual(finished["status"], "degraded")
        self.assertEqual(finished["reconcile_failure_count"], 1)
        self.assertEqual(finished["reconcile_backoff_seconds"], 60)
        self.assertEqual(finished["next_reconcile_at"], "2026-05-21T10:01:00+00:00")
        self.assertEqual(stored_run["status"], "degraded")
        self.assertEqual(stored_run["reconcile_failure_count"], 1)
        self.assertEqual(stored_run["reconcile_backoff_seconds"], 60)
        self.assertEqual(stored_run["next_reconcile_at"], "2026-05-21T10:01:00+00:00")

    def test_successful_reconcile_clears_retry_backoff(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            runs_dir = root / "live-agent-runs"
            runs_dir.mkdir(parents=True)
            (runs_dir / "session-runs.json").write_text(
                json.dumps(
                    {
                        "runs": [
                            {
                                "run_id": "retry-now",
                                "action": "ensure",
                                "status": "degraded",
                                "active": True,
                                "phase": "reconcile_failed",
                                "meeting_id": "resident-m1",
                                "group_id": "resident-main",
                                "request": {
                                    "meeting_id": "resident-m1",
                                    "group_id": "resident-main",
                                    "live_agent_config_path": "configs/live-agents.example.json",
                                },
                                "result": {"status": "degraded"},
                                "last_error": "Live-agent session run error details redacted.",
                                "created_at": "2026-05-21T10:00:00+00:00",
                                "updated_at": "2026-05-21T10:00:01+00:00",
                                "finished_at": "2026-05-21T10:00:01+00:00",
                                "last_reconciled_at": "2026-05-21T10:00:01+00:00",
                                "next_reconcile_at": "2026-05-21T10:01:00+00:00",
                                "reconcile_failure_count": 2,
                                "reconcile_backoff_seconds": 120,
                                "reconcile_count": 2,
                            }
                        ]
                    },
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )
            controller = LiveAgentSessionRunController(
                root,
                now_fn=lambda: datetime(2026, 5, 21, 10, 2, tzinfo=UTC),
            )

            results = controller.reconcile_active_runs(
                lambda run: {
                    "status": "ready",
                    "meeting_id": run["meeting_id"],
                    "group_id": run["group_id"],
                    "action": "none",
                }
            )
            stored_run = controller.list_runs()[0]

        self.assertEqual(results[0]["status"], "ready")
        self.assertEqual(stored_run["status"], "ready")
        self.assertEqual(stored_run["reconcile_failure_count"], 0)
        self.assertEqual(stored_run["reconcile_backoff_seconds"], 0)
        self.assertEqual(stored_run["next_reconcile_at"], "")
        self.assertEqual(stored_run["last_error"], "")
        self.assertEqual(stored_run["reconcile_count"], 3)

    def test_terminal_failed_and_stopped_runs_clear_retry_backoff(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            now = {"value": datetime(2026, 5, 21, 10, 0, tzinfo=UTC)}
            controller = LiveAgentSessionRunController(root, now_fn=lambda: now["value"])
            failed_run = controller.begin_run(
                action="ensure",
                payload={
                    "meeting_id": "resident-m1",
                    "group_id": "resident-main",
                    "live_agent_config_path": "configs/live-agents.example.json",
                },
            )
            stopped_run = controller.begin_run(
                action="ensure",
                payload={
                    "meeting_id": "resident-m2",
                    "group_id": "resident-secondary",
                    "live_agent_config_path": "configs/live-agents.example.json",
                },
            )
            controller.finish_run(
                failed_run["run_id"],
                session={
                    "status": "degraded",
                    "meeting_id": "resident-m1",
                    "group_id": "resident-main",
                    "action": "recover",
                },
            )
            controller.finish_run(
                stopped_run["run_id"],
                session={
                    "status": "degraded",
                    "meeting_id": "resident-m2",
                    "group_id": "resident-secondary",
                    "action": "recover",
                },
            )
            now["value"] = datetime(2026, 5, 21, 10, 5, tzinfo=UTC)
            controller.reconcile_active_runs(lambda _run: {"status": "degraded", "action": "recover"})

            failed = controller.fail_run(failed_run["run_id"], "operator refused")
            stopped = controller.mark_matching_stopped(meeting_id="resident-m2", group_id="resident-secondary")[0]
            stored = {item["run_id"]: item for item in controller.list_runs()}

        self.assertEqual(failed["status"], "failed")
        self.assertEqual(failed["next_reconcile_at"], "")
        self.assertEqual(failed["reconcile_failure_count"], 0)
        self.assertEqual(failed["reconcile_backoff_seconds"], 0)
        self.assertEqual(stopped["status"], "stopped")
        self.assertEqual(stopped["next_reconcile_at"], "")
        self.assertEqual(stopped["reconcile_failure_count"], 0)
        self.assertEqual(stopped["reconcile_backoff_seconds"], 0)
        self.assertEqual(stored[failed_run["run_id"]]["next_reconcile_at"], "")
        self.assertEqual(stored[stopped_run["run_id"]]["next_reconcile_at"], "")

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
