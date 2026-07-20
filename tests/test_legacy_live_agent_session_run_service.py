import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agentsassemble.legacy.live_agent.session_run_service import (
    LegacyLiveAgentSessionRunMutationService,
    LegacySessionRunActions,
    LegacySessionRunMutationError,
)
from agentsassemble.legacy.live_agent.runtime.session_runs import LiveAgentSessionRunController


class LegacyLiveAgentSessionRunMutationServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.runs = LiveAgentSessionRunController(self.root)
        self.operations: list[dict[str, object]] = []
        self.action_calls: list[str] = []
        self.actions = LegacySessionRunActions(
            should_reconcile=lambda *_args, **_kwargs: self.action_calls.append("should_reconcile") or True,
            reconcile=lambda *_args, **_kwargs: self.action_calls.append("reconcile") or [],
            assert_launch_approved=lambda *_args, **_kwargs: self.action_calls.append("approve"),
            ensure=lambda *_args, **_kwargs: self.action_calls.append("ensure") or {},
        )
        self.service = LegacyLiveAgentSessionRunMutationService(
            self.root,
            session_runs=self.runs,
            actions=self.actions,
            record_operation=lambda _root, **fields: self.operations.append(fields),
        )

    def _run(self, *, meeting_id: str = "room-a", group_id: str = "group-a") -> dict[str, object]:
        return self.runs.begin_run(
            action="ensure",
            payload={"meeting_id": meeting_id, "group_id": group_id},
        )

    def test_path_run_id_takes_precedence_over_body_target(self) -> None:
        selected = self._run()
        other = self._run(meeting_id="room-b", group_id="group-b")

        result = self.service.mutate(
            "pause",
            {"run_id": other["run_id"], "meeting_id": "room-b", "group_id": "group-b"},
            path_run_id=str(selected["run_id"]),
        )

        self.assertEqual(result["session_run"]["run_id"], selected["run_id"])
        self.assertEqual(self.runs.get_run(str(other["run_id"]))["status"], "running")

    def test_meeting_group_fallback_selects_latest_matching_run(self) -> None:
        older = self._run()
        latest = self._run()

        result = self.service.mutate("pause", {"meeting_id": "room-a", "group_id": "group-a"})

        self.assertEqual(result["session_run"]["run_id"], latest["run_id"])
        self.assertEqual(self.runs.get_run(str(older["run_id"]))["status"], "running")

    def test_pause_resume_restores_previous_status_and_records_audit(self) -> None:
        run = self._run()
        self.service.mutate("pause", {"run_id": run["run_id"]})
        resumed = self.service.mutate("resume", {"run_id": run["run_id"]})

        self.assertEqual(resumed["session_run"]["status"], "running")
        self.assertEqual(resumed["session_run"]["phase"], "resume_requested")
        self.assertEqual([item["operation"] for item in self.operations], ["session_run.pause", "session_run.resume"])
        self.assertEqual(self.operations[0]["details"]["paused_status"], "running")

    def test_stop_is_idempotent_and_audited_each_time(self) -> None:
        run = self._run()

        first = self.service.mutate("stop", {"run_id": run["run_id"]})
        second = self.service.mutate("stop", {"run_id": run["run_id"]})

        self.assertEqual(first["session_run"]["status"], "stopped")
        self.assertEqual(second["session_run"], first["session_run"])
        self.assertEqual([item["status"] for item in self.operations], ["success", "success"])

    def test_missing_target_returns_typed_safe_error_and_failed_audit(self) -> None:
        with self.assertRaises(LegacySessionRunMutationError) as raised:
            self.service.mutate("pause", {"meeting_id": "room-a"})

        self.assertEqual(str(raised.exception), "Missing session run id")
        self.assertEqual(raised.exception.details, {"session_run_id": "", "meeting_id": "room-a"})
        self.assertEqual(self.operations[-1]["status"], "failed")

    def test_unsupported_action_does_not_touch_controller_or_audit(self) -> None:
        self._run()

        with self.assertRaisesRegex(ValueError, "Unsupported session-run action"):
            self.service.mutate("retry-now", {})

        self.assertEqual(self.operations, [])
        self.assertEqual(self.action_calls, [])

    def test_retry_now_skips_ready_target_without_mutation(self) -> None:
        run = self._run()
        ready = self.runs.finish_run(
            str(run["run_id"]),
            session={"status": "ready", "meeting_id": "room-a", "group_id": "group-a", "action": "none"},
        )
        self.actions = LegacySessionRunActions(
            **{**self.actions.__dict__, "should_reconcile": lambda *_args, **_kwargs: False}
        )
        self.service.actions = self.actions

        result = self.service.retry_now({"run_id": run["run_id"]})

        self.assertEqual(result, {"status": "skipped", "session_run": ready, "results": []})
        self.assertEqual(self.operations[-1]["details"]["skipped_reason"], "already_ready")
        self.assertEqual(self.runs.get_run(str(run["run_id"]))["phase"], "none")

    def test_retry_now_reconciles_path_target_and_forwards_request_only_approval(self) -> None:
        selected = self._run()
        other = self._run(meeting_id="room-b", group_id="group-b")
        selected = self.runs.finish_run(
            str(selected["run_id"]),
            session={"status": "degraded", "meeting_id": "room-a", "group_id": "group-a", "action": "recover"},
        )
        seen: dict[str, object] = {}
        reconciled = {**selected, "status": "ready", "phase": "recover"}

        def reconcile(**kwargs):
            seen.update(kwargs)
            return [reconciled]

        self.service.actions = LegacySessionRunActions(
            **{**self.actions.__dict__, "reconcile": reconcile}
        )
        result = self.service.retry_now(
            {
                "run_id": other["run_id"],
                "meeting_id": "room-b",
                "group_id": "group-b",
                "approve_real_providers": True,
            },
            path_run_id=str(selected["run_id"]),
            default_server="http://room.local",
        )

        self.assertEqual(result["status"], "reconciled")
        self.assertEqual(result["session_run"], reconciled)
        self.assertEqual(seen["target_run_id"], selected["run_id"])
        self.assertEqual(seen["default_server"], "http://room.local")
        self.assertIs(seen["approve_real_providers"], True)
        self.assertEqual(self.operations[-1]["status"], "success")
        self.assertNotIn("approve_real_providers", self.runs.get_run(str(selected["run_id"]))["request"])

    def test_retry_now_without_reconcile_result_reports_scheduled(self) -> None:
        run = self._run()

        result = self.service.retry_now({"run_id": run["run_id"]})

        self.assertEqual(result["status"], "scheduled")
        self.assertEqual(result["results"], [])
        self.assertEqual(result["session_run"]["phase"], "retry_requested")
        self.assertEqual(self.operations[-1]["details"]["reconciled"], False)

    def test_retry_now_does_not_use_an_unrelated_last_result(self) -> None:
        run = self._run()
        unrelated = {**self._run(meeting_id="room-b", group_id="group-b"), "status": "ready"}
        self.service.actions = LegacySessionRunActions(
            **{**self.actions.__dict__, "reconcile": lambda **_kwargs: [unrelated]}
        )

        result = self.service.retry_now({"run_id": run["run_id"]})

        self.assertEqual(result["status"], "scheduled")
        self.assertEqual(result["session_run"]["run_id"], run["run_id"])
        self.assertEqual(result["session_run"]["phase"], "retry_requested")

    def test_retry_now_rejects_duplicate_target_results(self) -> None:
        run = self._run()
        duplicate = {**run, "status": "ready"}
        self.service.actions = LegacySessionRunActions(
            **{**self.actions.__dict__, "reconcile": lambda **_kwargs: [duplicate, duplicate]}
        )

        with self.assertRaises(LegacySessionRunMutationError):
            self.service.retry_now({"run_id": run["run_id"]})

        self.assertEqual(self.operations[-1]["status"], "failed")

    def test_retry_now_failure_is_typed_and_audited(self) -> None:
        with self.assertRaises(LegacySessionRunMutationError) as raised:
            self.service.retry_now({"meeting_id": "room-a", "group_id": "missing"})

        self.assertIn("No matching", str(raised.exception))
        self.assertEqual(self.operations[-1]["status"], "failed")

    def test_ensure_approves_executes_finishes_and_preserves_reply_probe(self) -> None:
        calls: list[str] = []
        session = {
            "status": "ready",
            "meeting_id": "room-a",
            "group_id": "group-a",
            "action": "start",
            "reply_probe": {"status": "ok", "probe_count": 1, "ok_count": 1},
        }
        self.service.actions = LegacySessionRunActions(
            should_reconcile=self.actions.should_reconcile,
            reconcile=self.actions.reconcile,
            assert_launch_approved=lambda *_args, **_kwargs: calls.append("approve"),
            ensure=lambda *_args, **_kwargs: calls.append("ensure") or dict(session),
        )

        result = self.service.ensure(
            {
                "meeting_id": "room-a",
                "group_id": "group-a",
                "approve_real_providers": True,
                "live_agent_config_path": "/private/live-agents.json",
            },
            default_server="http://room.local",
        )

        self.assertEqual(calls, ["approve", "ensure"])
        self.assertEqual(result["session_run"]["status"], "ready")
        self.assertEqual(result["reply_probe"]["status"], "ok")
        self.assertEqual(self.operations[-1]["details"]["reply_probe_status"], "ok")
        durable = self.runs.get_run(str(result["session_run"]["run_id"]))
        self.assertNotIn("approve_real_providers", durable["request"])
        self.assertNotIn("/private/", str(durable))

    def test_ensure_approval_failure_records_failed_run_without_provider_action(self) -> None:
        calls: list[str] = []

        def reject(*_args, **_kwargs):
            calls.append("approve")
            raise ValueError("approval denied for /private/live-agents.json")

        self.service.actions = LegacySessionRunActions(
            should_reconcile=self.actions.should_reconcile,
            reconcile=self.actions.reconcile,
            assert_launch_approved=reject,
            ensure=lambda *_args, **_kwargs: calls.append("ensure") or {},
        )

        with self.assertRaises(LegacySessionRunMutationError) as raised:
            self.service.ensure(
                {
                    "meeting_id": "room-a",
                    "group_id": "group-a",
                    "live_agent_config_path": "/private/live-agents.json",
                }
            )

        self.assertEqual(calls, ["approve"])
        self.assertNotIn("/private/", str(raised.exception))
        failed = self.runs.list_runs(limit=1)[0]
        self.assertEqual(failed["status"], "failed")
        self.assertEqual(self.operations[-1]["status"], "failed")

    def test_unexpected_ensure_failure_closes_durable_run_and_is_not_hidden_as_bad_request(self) -> None:
        self.service.actions = LegacySessionRunActions(
            should_reconcile=self.actions.should_reconcile,
            reconcile=self.actions.reconcile,
            assert_launch_approved=self.actions.assert_launch_approved,
            ensure=lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("programmer failure")),
        )

        with self.assertRaisesRegex(RuntimeError, "programmer failure"):
            self.service.ensure({"meeting_id": "room-a", "group_id": "group-a"})

        durable = self.runs.list_runs(limit=1)[0]
        self.assertEqual(durable["status"], "failed")
        self.assertEqual(self.operations[-1]["details"]["exception_type"], "RuntimeError")

    def test_ensure_provider_failure_fails_existing_durable_run(self) -> None:
        calls: list[str] = []

        def fail(*_args, **_kwargs):
            calls.append("ensure")
            raise OSError("provider failed")

        self.service.actions = LegacySessionRunActions(
            should_reconcile=self.actions.should_reconcile,
            reconcile=self.actions.reconcile,
            assert_launch_approved=lambda *_args, **_kwargs: calls.append("approve"),
            ensure=fail,
        )

        with self.assertRaises(LegacySessionRunMutationError):
            self.service.ensure({"meeting_id": "room-a", "group_id": "group-a"})

        self.assertEqual(calls, ["approve", "ensure"])
        self.assertEqual(self.runs.list_runs(limit=1)[0]["status"], "failed")

    def test_finish_failure_closes_the_durable_run_and_records_failed_audit(self) -> None:
        with patch.object(self.runs, "finish_run", side_effect=OSError("finish storage failed")):
            with self.assertRaises(LegacySessionRunMutationError):
                self.service.ensure({"meeting_id": "room-a", "group_id": "group-a"})

        durable = self.runs.list_runs(limit=1)[0]
        self.assertEqual(durable["status"], "failed")
        self.assertEqual(self.operations[-1]["status"], "failed")
        self.assertEqual(self.operations[-1]["operation"], "session_run.ensure")


if __name__ == "__main__":
    unittest.main()
