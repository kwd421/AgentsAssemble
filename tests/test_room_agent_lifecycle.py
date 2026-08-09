import tempfile
import threading
import unittest
from pathlib import Path

from agentsassemble.diagnostics.cleanup import CleanupReport
from agentsassemble.providers.launch_specs import NativeCliProviderSpec
from agentsassemble.room.agent_lifecycle import RoomAgentLifecycle
from agentsassemble.room.errors import RoomCommandRejected
from agentsassemble.room.event_broker import RoomEventBroker
from agentsassemble.room_store import RoomStore


class FakeBridgeManager:
    def __init__(self) -> None:
        self.starts: list[tuple[str, str]] = []
        self.start_attempts: list[tuple[str, str]] = []
        self.stops: list[tuple[str, str, str]] = []
        self.running: set[tuple[str, str]] = set()
        self.released_security_values: list[tuple[str, str]] = []

    def start(self, room_id, session, spec, *, server_url="", ticket_issuer=None):
        key = (room_id, str(session["session_id"]))
        self.start_attempts.append(key)
        runtime_reused = key in self.running
        if not runtime_reused:
            self.starts.append(key)
            self.running.add(key)
        return {
            "bridge_pid": 321,
            "bridge_handle_id": f"handle-{session['session_id']}",
            "resolved_executable": f"/bin/{spec.agent_id}",
            "runtime_profile_key": spec.runtime_profile_key(),
            "runtime_reused": runtime_reused,
        }

    def stop(self, room_id, session_id, *, timeout_seconds=2.0, handle_id=""):
        self.stops.append((room_id, session_id, handle_id))
        self.running.discard((room_id, session_id))
        return {"stopped": True, "alive": False}

    def health(self, room_id, session_id):
        return {
            "running": (room_id, session_id) in self.running,
            "room_id": room_id,
            "session_id": session_id,
            "bridge_handle_id": f"handle-{session_id}",
        }

    def release_preserved_security_values(self, room_id, session_id):
        self.released_security_values.append((room_id, session_id))

    def close(self):
        return CleanupReport("fake_bridge_manager")


class ScheduledCallback:
    def __init__(self, callback):
        self.callback = callback
        self.cancelled = False

    def cancel(self):
        self.cancelled = True


class ControlledScheduler:
    def __init__(self) -> None:
        self.pending: list[ScheduledCallback] = []

    def __call__(self, _delay_seconds, callback):
        scheduled = ScheduledCallback(callback)
        self.pending.append(scheduled)
        return scheduled

    def run_next(self):
        scheduled = self.pending.pop(0)
        if not scheduled.cancelled:
            scheduled.callback()


class RoomAgentLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = RoomStore(self.root)
        self.store.create_room("general", label="General")
        self.store.upsert_participant(
            "general",
            {
                "participant_id": "codex",
                "display_name": "Codex",
                "role": "agent",
                "status": "joined",
            },
        )
        self.store.upsert_session(
            "general",
            {
                "session_id": "codex",
                "participant_id": "codex",
                "status": "detached",
                "runtime_status": "stopped",
                "enabled": False,
                "process_ownership": "server",
                "pending_event_ids": [],
                "inflight_event_ids": [],
            },
        )
        self.spec = NativeCliProviderSpec(
            agent_id="codex",
            display_name="Codex",
            command=("codex",),
            cwd=str(self.root),
        )
        self.manager = FakeBridgeManager()
        self.broker = RoomEventBroker()
        self.scheduler = ControlledScheduler()
        self.published: list[dict[str, object]] = []
        self.assigned: list[tuple[str, str]] = []
        self.lifecycle = RoomAgentLifecycle(
            store=self.store,
            broker=self.broker,
            bridge_manager=self.manager,
            lock=threading.RLock(),
            provider_lookup=lambda _room_id, _agent_id: self.spec,
            ensure_provider_session=lambda _room_id, _spec: None,
            revoke_participant_sessions=lambda _room_id, _participant_id: 0,
            publish_session_state=lambda _room_id, session: self.published.append(dict(session)),
            assign_pending=self._assign_pending,
            is_closed=lambda: False,
            recovery_delay_seconds=0.1,
            external_stop_timeout_seconds=0.1,
            recovery_scheduler=self.scheduler,
            prepare_session_reset=lambda _room_id, _session, *, pending_event_ids, retry: {
                "pending_event_ids": list(pending_event_ids),
                "pending_attention_job_id": "" if not retry else _session.get("active_attention_job_id", ""),
                "pending_attention_lease_id": "" if not retry else _session.get("active_attention_lease_id", ""),
                "pending_attention_source_event_id": (
                    "" if not retry else _session.get("active_attention_source_event_id", "")
                ),
                "active_attention_job_id": "",
                "active_attention_lease_id": "",
                "active_attention_source_event_id": "",
            },
        )

    def tearDown(self):
        self.broker.close()
        self.temp.cleanup()

    def _connect_bridge(self):
        identity = {
            "agent_id": "codex",
            "session_id": "codex",
            "client_type": "agent_bridge",
            "meeting_id": "general",
        }
        channel = self.broker.connect(identity)
        self.broker.activate_bridge(channel)
        return channel

    def _assign_pending(self, room_id, agent_id):
        self.assigned.append((room_id, agent_id))
        return True

    def test_start_and_stop_use_the_owned_bridge_handle(self):
        started = self.lifecycle.start("general", "codex", server_url="http://room", ticket_issuer=None)

        self.assertFalse(started["runtime_reused"])
        self.assertEqual(self.manager.starts, [("general", "codex")])
        self.assertEqual(self.store.session("general", "codex")["bridge_handle_id"], "handle-codex")

        stopped = self.lifecycle.stop("general", "codex")

        self.assertEqual(self.manager.stops, [("general", "codex", "handle-codex")])
        self.assertEqual(stopped["process"]["ownership"], "server")
        self.assertTrue(stopped["process"]["confirmed"])
        self.assertEqual(self.store.session("general", "codex")["runtime_status"], "stopped")

    def test_start_retry_reuses_process_after_handle_persistence_failure(self):
        original_update = self.store.update_session_fields
        failed_once = False

        def fail_first_handle_write(room_id, session_id, **fields):
            nonlocal failed_once
            if not failed_once and fields.get("bridge_handle_id"):
                failed_once = True
                raise RuntimeError("injected handle persistence failure")
            return original_update(room_id, session_id, **fields)

        self.store.update_session_fields = fail_first_handle_write  # type: ignore[method-assign]

        with self.assertRaisesRegex(RuntimeError, "injected handle persistence failure"):
            self.lifecycle.start(
                "general",
                "codex",
                server_url="http://room",
                ticket_issuer=None,
                operation_id="start-operation",
            )

        prepared = self.store.session("general", "codex")
        self.assertEqual(prepared["runtime_status"], "starting")
        self.assertEqual(prepared["lifecycle_intent_status"], "prepared")
        self.assertEqual(self.manager.starts, [("general", "codex")])

        recovered = self.lifecycle.start(
            "general",
            "codex",
            server_url="http://room",
            ticket_issuer=None,
            operation_id="start-operation",
        )

        self.assertTrue(recovered["runtime_reused"])
        self.assertEqual(self.manager.start_attempts, [("general", "codex"), ("general", "codex")])
        self.assertEqual(self.manager.starts, [("general", "codex")])
        session = self.store.session("general", "codex")
        self.assertEqual(session["bridge_handle_id"], "handle-codex")
        self.assertEqual(session.get("lifecycle_intent_action"), "")

    def test_stop_retry_finalizes_without_stopping_process_twice(self):
        self.lifecycle.start("general", "codex", server_url="http://room", ticket_issuer=None)
        original_update = self.store.update_session_fields
        failed_once = False

        def fail_first_stopped_write(room_id, session_id, **fields):
            nonlocal failed_once
            if not failed_once and fields.get("runtime_status") == "stopped":
                failed_once = True
                raise RuntimeError("injected stop finalization failure")
            return original_update(room_id, session_id, **fields)

        self.store.update_session_fields = fail_first_stopped_write  # type: ignore[method-assign]

        with self.assertRaisesRegex(RuntimeError, "injected stop finalization failure"):
            self.lifecycle.stop("general", "codex", operation_id="stop-operation")

        prepared = self.store.session("general", "codex")
        self.assertEqual(prepared["runtime_status"], "stopping")
        self.assertEqual(prepared["lifecycle_intent_status"], "effect_applied")
        self.assertEqual(self.manager.stops, [("general", "codex", "handle-codex")])

        recovered = self.lifecycle.stop("general", "codex", operation_id="stop-operation")

        self.assertTrue(recovered["process"]["already_stopped"])
        self.assertEqual(self.manager.stops, [("general", "codex", "handle-codex")])
        self.assertEqual(self.store.session("general", "codex")["runtime_status"], "stopped")
        detached = [event for event in self.store.read_events("general") if event.get("type") == "session_detached"]
        self.assertEqual(len(detached), 1)

    def test_close_does_not_overwrite_an_error_without_an_owned_runtime(self):
        self.store.update_session_fields(
            "general",
            "codex",
            status="error",
            runtime_status="error",
            last_error="profile migration required",
        )

        report = self.lifecycle.close([("general", "codex")])

        self.assertTrue(report.ok)
        self.assertEqual(self.manager.stops, [])
        self.assertEqual(self.store.session("general", "codex")["last_error"], "profile migration required")

    def test_pause_and_resume_preserve_the_connected_process(self):
        self._connect_bridge()
        self.store.update_session_fields(
            "general",
            "codex",
            status="attached",
            runtime_status="idle",
            enabled=True,
        )

        paused = self.lifecycle.pause("general", "codex")
        resumed = self.lifecycle.resume("general", "codex", server_url="", ticket_issuer=None)

        self.assertTrue(paused["process_preserved"])
        self.assertTrue(resumed["process_reused"])
        self.assertEqual(self.manager.starts, [])
        self.assertEqual(self.manager.stops, [])
        self.assertEqual(self.assigned, [("general", "codex")])
        self.assertEqual(self.store.session("general", "codex")["runtime_status"], "idle")

    def test_resume_disconnected_session_without_owned_handle_starts_without_stop(self):
        self.store.update_session_fields(
            "general",
            "codex",
            status="unavailable",
            runtime_status="disconnected",
            enabled=False,
            recovery_required=True,
            last_error="Agent bridge disconnected.",
            last_error_code="bridge_process_exited",
            bridge_handle_id="",
        )

        resumed = self.lifecycle.resume(
            "general",
            "codex",
            server_url="http://room",
            ticket_issuer=None,
        )

        self.assertFalse(resumed["runtime_reused"])
        self.assertEqual(self.manager.stops, [])
        self.assertEqual(self.manager.starts, [("general", "codex")])
        self.assertEqual(self.store.session("general", "codex")["runtime_status"], "starting")

    def test_resume_profile_migration_error_does_not_attempt_shutdown(self):
        self.store.update_session_fields(
            "general",
            "codex",
            status="unavailable",
            runtime_status="disconnected",
            recovery_required=True,
            last_error="Stored Agent Session profile must be migrated before it can be reused.",
            last_error_code="profile_migration_required",
            bridge_handle_id="",
        )

        with self.assertRaises(RoomCommandRejected) as raised:
            self.lifecycle.resume(
                "general",
                "codex",
                server_url="http://room",
                ticket_issuer=None,
            )

        self.assertEqual(raised.exception.code, "profile_migration_required")
        self.assertEqual(self.manager.stops, [])
        self.assertEqual(self.manager.starts, [])

    def test_bridge_exit_requeues_inflight_work_and_schedules_one_restart(self):
        self.lifecycle.start("general", "codex", server_url="http://room", ticket_issuer=None)
        self.manager.running.discard(("general", "codex"))
        self.store.update_session_fields(
            "general",
            "codex",
            status="attached",
            runtime_status="busy",
            enabled=True,
            inflight_event_ids=["evt-1"],
            pending_event_ids=["evt-2"],
        )

        self.lifecycle.bridge_process_exited("general", "codex", 7, "provider crashed")

        recovering = self.store.session("general", "codex")
        self.assertEqual(recovering["runtime_status"], "recovering")
        self.assertEqual(recovering["pending_event_ids"], ["evt-1", "evt-2"])
        self.assertEqual(len(self.scheduler.pending), 1)

        self.scheduler.run_next()

        self.assertEqual(self.manager.starts, [("general", "codex"), ("general", "codex")])
        self.assertEqual(self.store.session("general", "codex")["runtime_status"], "starting")


if __name__ == "__main__":
    unittest.main()
