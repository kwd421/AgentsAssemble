import io
import tempfile
import subprocess
import json
import threading
import time
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from agentsassemble.room_attention import AttentionEvaluation
from agentsassemble.room_realtime import (
    NativeCliProviderSpec,
    RoomCommandRejected,
    RoomEventBroker,
    RoomRealtimeController,
    default_native_cli_provider_specs,
    validate_native_cli_provider_spec,
)
from agentsassemble.room_command_uow import RoomCommandUnitOfWork
from agentsassemble.room_members import is_room_member_muted, set_room_member_muted
from agentsassemble.room_store import RoomStore
from agentsassemble.room_settings import update_room_settings as update_legacy_room_settings
from agentsassemble.persistence.local.identity.registry import (
    identity_store_for_output_root,
)
from agentsassemble.provider_capabilities import ProviderCapabilityCatalog
from agentsassemble.providers.launch_specs import native_cli_provider_definition
from tests.room_realtime_test_support import memory_room_access_services


HOST = {
    "agent_id": "operator-local",
    "display_name": "Host",
    "participant_type": "human",
    "client_type": "browser",
    "invite_scope": "read_write",
    "meeting_id": "general",
    "operator": True,
}


class FakeBridgeManager:
    def __init__(self) -> None:
        self.starts: list[tuple[str, str]] = []
        self.specs: list[NativeCliProviderSpec] = []
        self.stops: list[tuple[str, str]] = []
        self.running: set[tuple[str, str]] = set()
        self.start_errors = []
        self.stop_errors = []
        self.close_called = False

    def start(self, room_id, session, spec, *, server_url="", ticket_issuer=None):
        del server_url, ticket_issuer
        if self.start_errors:
            raise self.start_errors.pop(0)
        self.starts.append((room_id, str(session["session_id"])))
        self.running.add((room_id, str(session["session_id"])))
        self.specs.append(spec)
        return {
            "bridge_pid": 701,
            "bridge_handle_id": f"handle-{session['session_id']}",
            "resolved_executable": f"/fake/{spec.command[0]}",
        }

    def stop(self, room_id, session_id, *, timeout_seconds=2.0, handle_id=""):
        del timeout_seconds
        self.stops.append((room_id, session_id))
        if self.stop_errors:
            raise self.stop_errors.pop(0)
        self.running.discard((room_id, session_id))
        return {"stopped": bool(handle_id), "alive": False}

    def health(self, room_id, session_id):
        return {"running": (room_id, session_id) in self.running}

    def close(self):
        self.close_called = True
        return None


class _ScheduledRecovery:
    def __init__(self, callback):
        self.callback = callback
        self.cancelled = False

    def cancel(self):
        self.cancelled = True


class ControlledRecoveryScheduler:
    def __init__(self):
        self.delays = []
        self.pending = []

    def __call__(self, delay_seconds, callback):
        scheduled = _ScheduledRecovery(callback)
        self.delays.append(delay_seconds)
        self.pending.append(scheduled)
        return scheduled

    def run_next(self):
        scheduled = self.pending.pop(0)
        if not scheduled.cancelled:
            scheduled.callback()


def _spec(agent_id="codex", *, default_responder=True):
    return NativeCliProviderSpec(
        agent_id=agent_id,
        display_name=agent_id.title(),
        command=(agent_id,),
        cwd=".",
        default_responder=default_responder,
    )


def _bridge_identity(agent_id="codex"):
    return {
        "agent_id": agent_id,
        "display_name": agent_id.title(),
        "participant_type": "agent",
        "client_type": "agent_bridge",
        "invite_scope": "read_write",
        "meeting_id": "general",
        "session_id": agent_id,
        "provider_kind": f"{agent_id}_live_session",
        "operator": False,
    }


def _external_ready_payload(provider_kind="codex_live_session", **overrides):
    definition = native_cli_provider_definition(provider_kind)
    assert definition is not None
    values = {
        "pid": 808,
        "running": True,
        "transport": definition.reported_transports[0],
        "provider_session_active": True,
        "started_at": None,
        "provider_kind": definition.provider_kind,
        "runtime_kind": definition.runtime_kind,
        "model": definition.default_model,
        "reasoning_effort": definition.default_reasoning_effort,
        "service_tier": definition.default_service_tier,
        "variant": definition.default_variant,
        "permission_mode": definition.default_permission_mode,
    }
    values.update(overrides)
    return values


def _test_provider_catalog() -> ProviderCapabilityCatalog:
    def runner(command, _timeout):
        if command[1:3] == ["debug", "models"]:
            return 0, json.dumps(
                {
                    "models": [
                        {"slug": "gpt-5.6-luna", "supported_reasoning_levels": [{"effort": "low"}]},
                        {"slug": "gpt-5.3-codex-spark", "supported_reasoning_levels": [{"effort": "low"}]},
                        {"slug": "gpt-5.3-codex", "supported_reasoning_levels": [{"effort": "low"}]},
                    ]
                }
            ), ""
        if command[0].endswith("agy"):
            return 0, "Gemini 3.5 Flash (Medium)\n", ""
        if command[0].endswith("grok"):
            return 0, "Default model: grok-4.5\n- grok-4.5\n", ""
        if command[0].endswith("claude"):
            return 0, "Claude help", ""
        if command[1:] == ["models", "--verbose"]:
            return 0, "opencode-go/glm-5.2\n", ""
        return 1, "", "unsupported"

    catalog = ProviderCapabilityCatalog(runner=runner, resolver=lambda executable: f"/bin/{executable}")
    catalog.snapshot(refresh=True)
    return catalog


class RoomEventBrokerTests(unittest.TestCase):
    def test_subscribed_connections_receive_same_canonical_event_without_polling(self):
        broker = RoomEventBroker()
        first = broker.connect({**HOST, "agent_id": "browser-a"})
        second = broker.connect({**HOST, "agent_id": "browser-b"})
        first.subscribe({"room_events"})
        second.subscribe({"room_events"})
        event = {"room_id": "general", "id": "evt-1", "seq": 9, "type": "message_final"}

        broker.broadcast_event(event)

        self.assertEqual(first.drain()[0]["events"], [event])
        self.assertEqual(second.drain()[0]["events"], [event])
        self.assertNotEqual(first.fileno(), second.fileno())
        broker.disconnect(first)
        broker.disconnect(second)


class NativeCliProviderSpecTests(unittest.TestCase):
    def test_default_specs_include_exact_interactive_claude_without_print_mode(self):
        specs = {spec.agent_id: spec for spec in default_native_cli_provider_specs()}

        self.assertIn("claude", specs)
        self.assertEqual(specs["claude"].model, "claude-haiku-4-5")
        self.assertEqual(specs["claude"].provider_kind, "claude_code")
        self.assertIn("--model", specs["claude"].command)
        self.assertEqual(specs["claude"].permission_mode, "meeting_read_only")
        self.assertEqual(specs["claude"].startup_accept_contains, "Quick safety check")
        self.assertEqual(specs["claude"].startup_ready_contains, "")
        self.assertNotIn("plan", specs["claude"].command)
        self.assertNotIn("-p", specs["claude"].command)
        self.assertNotIn("--print", specs["claude"].command)
        for spec in specs.values():
            validate_native_cli_provider_spec(spec)

    def test_claude_print_mode_is_rejected_before_launch(self):
        for command in (("claude", "-p"), ("claude", "--print"), ("claude", "--print=json")):
            with self.subTest(command=command), self.assertRaisesRegex(ValueError, "print mode is forbidden"):
                validate_native_cli_provider_spec(
                    NativeCliProviderSpec(
                        agent_id="claude",
                        display_name="Claude",
                        command=command,
                        provider_kind="claude_code",
                    )
                )

    def test_runtime_profile_key_includes_terminal_submission_settings(self):
        first = NativeCliProviderSpec(
            agent_id="claude",
            display_name="Claude",
            command=("claude", "--model", "haiku"),
            provider_kind="claude_code",
            startup_accept_keys="\r",
        )
        second = NativeCliProviderSpec(
            agent_id="claude",
            display_name="Claude",
            command=("claude", "--model", "haiku"),
            provider_kind="claude_code",
            startup_accept_keys="y\r",
        )

        self.assertNotEqual(first.runtime_profile_key(), second.runtime_profile_key())


class RoomRealtimeControllerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.manager = FakeBridgeManager()
        self.recovery_scheduler = ControlledRecoveryScheduler()
        self.ready_count = 0
        self.provider_catalog = _test_provider_catalog()
        self.catalog_revision = str(self.provider_catalog.snapshot()["catalog_revision"])
        self.room_access = memory_room_access_services()
        self.controller = RoomRealtimeController(
            self.root,
            **self.room_access.controller_kwargs(),
            providers=[_spec()],
            bridge_manager=self.manager,
            recovery_scheduler=self.recovery_scheduler,
            provider_catalog=self.provider_catalog,
            external_stop_timeout_seconds=0.2,
        )

    def tearDown(self):
        self.controller.close()
        self.temp.cleanup()

    def test_controller_accepts_one_repository_instance_as_room_authority(self):
        injected_root = self.root / "injected"
        repository = RoomStore(injected_root)
        with patch(
            "agentsassemble.room_realtime.RoomStore",
            side_effect=AssertionError("unexpected SQLite repository construction"),
        ):
            controller = RoomRealtimeController(
                injected_root,
                **self.room_access.controller_kwargs(),
                repository=repository,
                provider_catalog=self.provider_catalog,
            )
        try:
            self.assertIs(controller.store, repository)
            self.assertEqual(repository.room("general")["room_id"], "general")
        finally:
            controller.close()

    def test_startup_reconciles_orphan_attention_and_exposes_audit_report(self):
        profile_root = self.root / "attention-reconciliation"
        store = RoomStore(profile_root)
        store.create_room("general")
        with store.transaction("general") as transaction:
            transaction.upsert_participant(
                {
                    "participant_id": "orphan-agent",
                    "display_name": "Orphan Agent",
                    "participant_type": "agent",
                }
            )
            job = transaction.record_attention_evaluation(
                AttentionEvaluation(
                    room_id="general",
                    source_event_id="orphan-source",
                    source_seq=7,
                    outcome="selected",
                    selected_participant_id="orphan-agent",
                    eligible_participant_ids=("orphan-agent",),
                    reasons=("ambient_human_message",),
                ),
                mode="active",
                status="pending",
            )

        restarted = RoomRealtimeController(
            profile_root,
            **self.room_access.controller_kwargs(),
            providers=[],
            bridge_manager=FakeBridgeManager(),
            provider_catalog=self.provider_catalog,
        )
        try:
            diagnostics = restarted.attention_active_diagnostics()["startup_reconciliation"]
            self.assertGreaterEqual(diagnostics["repair_count"], 1)
            self.assertFalse(diagnostics["truncated"])
            self.assertEqual(
                restarted.store.attention_job("general", job["job_id"])["status"],
                "cancelled",
            )
        finally:
            restarted.close()

    def test_restart_restores_durable_provider_profile_before_default_seed(self):
        profile_root = self.root / "durable-profile"
        definition = native_cli_provider_definition("claude")
        assert definition is not None
        saved = definition.make_selected_spec(
            agent_id="claude",
            display_name="Claude",
            cwd=profile_root,
            model="claude-haiku-4-5",
            reasoning_effort="low",
            service_tier="default",
            permission_mode="meeting_read_only",
        )
        default = definition.make_selected_spec(
            agent_id="claude",
            display_name="Claude",
            cwd=profile_root,
            model="claude-haiku-4-5",
            reasoning_effort="high",
            service_tier="default",
            permission_mode="meeting_read_only",
        )
        first = RoomRealtimeController(
            profile_root,
            **self.room_access.controller_kwargs(),
            providers=[saved],
            bridge_manager=FakeBridgeManager(),
            provider_catalog=self.provider_catalog,
        )
        first.close()
        manager = FakeBridgeManager()
        restarted = RoomRealtimeController(
            profile_root,
            **self.room_access.controller_kwargs(),
            providers=[default],
            bridge_manager=manager,
            provider_catalog=self.provider_catalog,
        )
        try:
            restarted.handle_command(
                HOST,
                {
                    "op": "command",
                    "request_id": "start-restored-profile",
                    "action": "agent.start",
                    "payload": {"agent_id": "claude"},
                },
            )
            session = restarted.store.session("general", "claude")
            self.assertEqual(session["reasoning_effort"], "low")
            self.assertEqual(manager.specs[-1].reasoning_effort, "low")
            self.assertEqual(manager.specs[-1].command, saved.command)
        finally:
            restarted.close()

    def test_restart_clears_only_a_resolved_profile_migration_error(self):
        profile_root = self.root / "legacy-claude-profile"
        definition = native_cli_provider_definition("claude")
        assert definition is not None
        current = definition.make_selected_spec(
            agent_id="claude",
            display_name="Claude",
            cwd=profile_root,
            model="claude-haiku-4-5",
            reasoning_effort="low",
            service_tier="default",
            permission_mode="meeting_read_only",
        )
        legacy = replace(current, startup_ready_contains="plan mode on")
        first = RoomRealtimeController(
            profile_root,
            **self.room_access.controller_kwargs(),
            providers=[current],
            bridge_manager=FakeBridgeManager(),
            provider_catalog=self.provider_catalog,
        )
        first.close()
        RoomStore(profile_root).update_session_fields(
            "general",
            "claude",
            status="error",
            runtime_status="error",
            runtime_profile_key=legacy.runtime_profile_key(),
            enabled=False,
            recovery_required=True,
            last_error="Stored Agent Session profile must be migrated before it can be reused.",
        )

        restarted = RoomRealtimeController(
            profile_root,
            **self.room_access.controller_kwargs(),
            providers=[current],
            bridge_manager=FakeBridgeManager(),
            provider_catalog=self.provider_catalog,
        )
        try:
            restored = restarted.store.session("general", "claude")
            self.assertEqual(restored["runtime_profile_key"], current.runtime_profile_key())
            self.assertEqual(restored["runtime_status"], "stopped")
            self.assertEqual(restored["status"], "available")
            self.assertFalse(restored["recovery_required"])
            self.assertEqual(restored["last_error"], "")
        finally:
            restarted.close()

    def test_restart_does_not_claim_a_profileless_legacy_agent_session(self):
        profile_root = self.root / "legacy-one-shot-session"
        store = RoomStore(profile_root)
        store.create_room("general", label="General")
        store.upsert_participant(
            "general",
            {
                "participant_id": "legacy-agent",
                "display_name": "Legacy Agent",
                "role": "agent",
                "participant_type": "local",
                "status": "joined",
            },
        )
        store.upsert_session(
            "general",
            {
                "session_id": "legacy-agent",
                "participant_id": "legacy-agent",
                "display_name": "Legacy Agent",
                "provider_kind": "codex_live_session",
                "status": "attached",
            },
        )

        restarted = RoomRealtimeController(
            profile_root,
            **self.room_access.controller_kwargs(),
            providers=[],
            bridge_manager=FakeBridgeManager(),
            provider_catalog=self.provider_catalog,
        )
        try:
            restored = restarted.store.session("general", "legacy-agent")
            self.assertEqual(restored["status"], "attached")
            self.assertNotIn("last_error", restored)
            self.assertNotIn("legacy-agent", restarted._room_providers("general"))
        finally:
            restarted.close()

    def _command(self, request_id, action, payload=None, identity=None):
        command_payload = dict(payload or {})
        if action == "agent.create":
            command_payload.setdefault("catalog_revision", self.catalog_revision)
            provider_id = str(command_payload.get("provider_id") or "")
            provider = next(
                item
                for item in self.provider_catalog.snapshot()["providers"]
                if item["id"] == provider_id
            )
            for control in provider["controls"]:
                command_payload.setdefault(control["key"], control["default_value"])
        return self.controller.handle_command(
            identity or HOST,
            {"op": "command", "request_id": request_id, "action": action, "payload": command_payload},
        )

    def _connect_bridge(self, agent_id="codex"):
        session = self.controller.store.session("general", agent_id)
        if session and not session.get("bridge_handle_id"):
            self.controller.store.update_session_fields(
                "general",
                agent_id,
                bridge_handle_id=f"handle-{agent_id}",
            )
        identity = _bridge_identity(agent_id)
        channel = self.controller.connect(identity)
        channel.subscribe({"room_events"})
        self.ready_count += 1
        self._command(
            f"ready-{agent_id}-{self.ready_count}",
            "bridge.ready",
            {
                "pid": 808,
                "running": True,
                "transport": "pty",
                "provider_session_active": True,
                "started_at": None,
                "is_one_shot": False,
            },
            identity,
        )
        return identity, channel

    def _confirmed_external_stop(self, identity, channel, *, request_id="external-stop"):
        results: list[dict[str, object]] = []
        errors: list[BaseException] = []

        def stop() -> None:
            try:
                results.append(
                    self._command(request_id, "agent.stop", {"agent_id": identity["agent_id"]})
                )
            except BaseException as error:
                errors.append(error)

        thread = threading.Thread(target=stop, daemon=True)
        thread.start()
        control = None
        deadline = time.time() + 2.0
        while time.time() < deadline and control is None:
            control = next(
                (
                    message
                    for message in channel.drain()
                    if message.get("op") == "agent.control" and message.get("action") == "stop"
                ),
                None,
            )
            if control is None:
                time.sleep(0.005)
        self.assertIsNotNone(control)
        self._command(
            f"confirm-{request_id}",
            "bridge.stopped",
            {"control_id": control["control_id"], "stopped": True},
            identity,
        )
        thread.join(timeout=2)
        self.assertFalse(thread.is_alive())
        if errors:
            raise errors[0]
        return results[0]

    def test_message_command_uses_server_identity_and_is_deduplicated(self):
        first = self._command("req-message", "message.send", {"content": "@codex hello"})
        duplicate = self._command("req-message", "message.send", {"content": "@codex hello"})
        with self.assertRaises(RoomCommandRejected) as conflict:
            self._command("req-message", "message.send", {"content": "different"})
        messages = [event for event in RoomStore(self.root).read_events("general") if event["type"] == "message_final"]

        self.assertTrue(first["accepted"])
        self.assertTrue(duplicate["deduplicated"])
        self.assertEqual(conflict.exception.code, "idempotency_conflict")
        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0]["actor"]["participant_id"], "operator-local")
        self.assertEqual(messages[0]["content"], "@codex hello")

    def test_message_command_rolls_back_event_and_routing_when_ack_record_fails(self):
        with patch.object(
            RoomCommandUnitOfWork,
            "record_ack",
            side_effect=RuntimeError("command result unavailable"),
        ):
            with self.assertRaisesRegex(RuntimeError, "command result unavailable"):
                self._command("req-message-rollback", "message.send", {"content": "must roll back"})

        self.assertEqual(
            [
                event
                for event in self.controller.store.read_events("general")
                if event.get("type") == "message_final" and event.get("content") == "must roll back"
            ],
            [],
        )
        self.assertEqual(self.controller.store.session("general", "codex")["pending_event_ids"], [])
        self.assertEqual(
            self.controller.store.command_record("general", "operator-local", "req-message-rollback"),
            {},
        )

        retry = self._command("req-message-rollback", "message.send", {"content": "must roll back"})

        self.assertTrue(retry["accepted"])
        messages = [
            event
            for event in self.controller.store.read_events("general")
            if event.get("type") == "message_final" and event.get("content") == "must roll back"
        ]
        self.assertEqual(len(messages), 1)
        self.assertIn(messages[0]["id"], self.controller.store.session("general", "codex")["pending_event_ids"])

    def test_shadow_attention_records_silence_without_changing_ordered_routing(self):
        self.controller.attention_shadow_mode = "full"
        result = self._command("shadow-ordinary", "message.send", {"content": "그냥 상황을 공유할게."})
        event = result["result"]["event"]

        jobs = self.controller.store.attention_jobs("general", mode="shadow")
        session = self.controller.store.session("general", "codex")

        self.assertEqual(jobs[-1]["source_event_id"], event["id"])
        self.assertEqual(jobs[-1]["outcome"], "silent")
        self.assertIn(event["id"], session["pending_event_ids"])
        self.assertEqual(self.controller.attention_shadow_diagnostics()["error_count"], 0)

    def test_shadow_attention_selects_connected_direct_mention(self):
        self.controller.attention_shadow_mode = "full"
        self._command("shadow-start", "agent.start", {"agent_id": "codex"})
        _identity, channel = self._connect_bridge("codex")
        channel.drain()

        result = self._command("shadow-mention", "message.send", {"content": "@codex 확인해줘"})
        event = result["result"]["event"]
        assignment = next(message for message in channel.drain() if message.get("op") == "turn.assign")
        job = self.controller.store.attention_jobs("general", mode="shadow")[-1]

        self.assertEqual(job["source_event_id"], event["id"])
        self.assertEqual(job["outcome"], "selected")
        self.assertEqual(job["selected_participant_id"], "codex")
        self.assertEqual(assignment["source_event_id"], event["id"])

    def test_bridge_observation_advances_only_to_a_committed_room_sequence(self):
        identity, _channel = self._connect_bridge("codex")
        event = self._command(
            "observed-source",
            "message.send",
            {"content": "provider를 깨우지 않고 보는 메시지"},
        )["result"]["event"]

        result = self._command(
            "observed-ack",
            "room.observed",
            {"through_seq": event["seq"]},
            identity,
        )["result"]
        equal = self._command(
            "observed-equal",
            "room.observed",
            {"through_seq": event["seq"]},
            identity,
        )["result"]
        stale = self._command(
            "observed-stale",
            "room.observed",
            {"through_seq": max(1, int(event["seq"]) - 1)},
            identity,
        )["result"]
        with self.assertRaises(RoomCommandRejected) as rejected:
            self._command(
                "observed-ahead",
                "room.observed",
                {"through_seq": event["seq"] + 100},
                identity,
            )

        self.assertEqual(result["observed_through_seq"], event["seq"])
        self.assertEqual(equal["observed_through_seq"], event["seq"])
        self.assertEqual(stale["observed_through_seq"], event["seq"])
        self.assertEqual(
            self.controller.store.attention_state("general", "codex").last_observed_seq,
            event["seq"],
        )
        self.assertEqual(
            self.controller.store.command_record(
                "general",
                "agent_bridge:codex",
                "observed-ack",
            ),
            {},
        )
        self.assertEqual(rejected.exception.code, "observed_seq_invalid")

    def test_bridge_observation_does_not_wait_for_the_lifecycle_lock(self):
        identity, _channel = self._connect_bridge("codex")
        event = self._command(
            "observed-during-stop-source",
            "message.send",
            {"content": "종료 직전까지 받은 이벤트"},
        )["result"]["event"]
        outcomes: list[object] = []

        def checkpoint() -> None:
            try:
                outcomes.append(
                    self._command(
                        "observed-during-stop",
                        "room.observed",
                        {"through_seq": event["seq"]},
                        identity,
                    )
                )
            except Exception as error:  # pragma: no cover - assertion reports the error
                outcomes.append(error)

        with self.controller._lock:
            worker = threading.Thread(target=checkpoint)
            worker.start()
            worker.join(timeout=0.5)

        worker.join(timeout=1.0)
        self.assertFalse(worker.is_alive())
        self.assertEqual(len(outcomes), 1)
        self.assertIsInstance(outcomes[0], dict)
        self.assertEqual(
            outcomes[0]["result"]["observed_through_seq"],
            event["seq"],
        )

    def test_shadow_attention_failure_is_diagnostic_and_does_not_block_current_routing(self):
        self.controller.attention_shadow_mode = "full"
        with patch.object(
            self.controller._attention_coordinator,
            "evaluate_shadow",
            side_effect=RuntimeError("shadow storage unavailable"),
        ), self.assertLogs("agentsassemble.room_realtime", level="ERROR"):
            result = self._command("shadow-error", "message.send", {"content": "routing must continue"})

        event = result["result"]["event"]
        session = self.controller.store.session("general", "codex")
        diagnostics = self.controller.attention_shadow_diagnostics()

        self.assertIn(event["id"], session["pending_event_ids"])
        self.assertEqual(diagnostics["error_count"], 1)
        self.assertIn("shadow storage unavailable", diagnostics["last_error"])

    def test_shadow_attention_is_off_by_default_without_changing_ordered_routing(self):
        with patch.object(self.controller._attention_coordinator, "evaluate_shadow") as evaluate_shadow:
            result = self._command("shadow-off", "message.send", {"content": "기본 라우팅은 계속해."})

        event = result["result"]["event"]
        session = self.controller.store.session("general", "codex")
        diagnostics = self.controller.attention_shadow_diagnostics()

        evaluate_shadow.assert_not_called()
        self.assertEqual(self.controller.store.attention_jobs("general", mode="shadow"), [])
        self.assertIn(event["id"], session["pending_event_ids"])
        self.assertEqual(diagnostics["mode"], "off")
        self.assertEqual(diagnostics["recorded_count"], 0)
        self.assertEqual(diagnostics["skipped_count"], 1)

    def test_startup_reconciliation_moves_inflight_work_back_to_pending(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            definition = native_cli_provider_definition("codex")
            self.assertIsNotNone(definition)
            spec = definition.make_default_spec(cwd=root)
            seed = RoomRealtimeController(
                root,
                **self.room_access.controller_kwargs(),
                providers=[spec],
                bridge_manager=FakeBridgeManager(),
            )
            seed.close()
            RoomStore(root).update_session_fields(
                "general",
                "codex",
                status="attached",
                runtime_status="busy",
                inflight_event_ids=["evt-inflight"],
                pending_event_ids=["evt-pending"],
                bridge_handle_id="lost-handle",
            )
            controller = RoomRealtimeController(
                root,
                **self.room_access.controller_kwargs(),
                providers=[spec],
                bridge_manager=FakeBridgeManager(),
            )
            recovered = RoomStore(root).session("general", "codex")
            controller.close()
        self.assertEqual(recovered["runtime_status"], "disconnected")
        self.assertEqual(recovered["inflight_event_ids"], [])
        self.assertEqual(recovered["pending_event_ids"], ["evt-inflight", "evt-pending"])
        self.assertTrue(recovered["recovery_required"])
        self.assertEqual(recovered["bridge_handle_id"], "")

    def test_read_only_browser_cannot_send_or_control_agents(self):
        read_only = {**HOST, "operator": False, "invite_scope": "read_only", "agent_id": "guest"}
        with self.assertRaises(RoomCommandRejected) as send_error:
            self._command("req-ro-send", "message.send", {"content": "no"}, read_only)
        with self.assertRaises(RoomCommandRejected) as control_error:
            self._command("req-ro-start", "agent.start", {"agent_id": "codex"}, read_only)

        self.assertEqual(send_error.exception.code, "permission_denied")
        self.assertEqual(control_error.exception.code, "permission_denied")

    def test_close_continues_stopping_other_agents_after_one_failure(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            manager = FakeBridgeManager()
            manager.stop_errors.append(RuntimeError("first stop failed"))
            controller = RoomRealtimeController(
                Path(temp_dir),
                **self.room_access.controller_kwargs(),
                providers=[_spec("codex"), _spec("grok")],
                bridge_manager=manager,
            )
            for agent_id in ("codex", "grok"):
                controller.store.update_session_fields(
                    "general",
                    agent_id,
                    runtime_status="idle",
                    enabled=True,
                    bridge_handle_id=f"handle-{agent_id}",
                )
            stderr = io.StringIO()
            with patch("sys.stderr", stderr):
                report = controller.close()

        self.assertEqual(manager.stops, [("general", "codex"), ("general", "grok")])
        self.assertTrue(manager.close_called)
        self.assertFalse(report.ok)
        self.assertTrue(any(failure.stage == "agent.stop" for failure in report.failures))
        self.assertIn("agent.stop", stderr.getvalue())

    def test_request_id_is_scoped_to_principal_and_payload_changes_conflict(self):
        guest = {**HOST, "agent_id": "guest", "operator": False}
        self.controller.connect(guest)
        host = self._command("same-id", "message.send", {"content": "host"})
        other = self._command("same-id", "message.send", {"content": "guest"}, guest)
        self.assertTrue(host["accepted"])
        self.assertTrue(other["accepted"])
        self.assertFalse(other["deduplicated"])

    def test_external_reported_pid_is_diagnostic_only_and_never_sent_to_manager_stop(self):
        identity = _bridge_identity("external")
        identity["provider_kind"] = "codex"
        channel = self.controller.connect(identity)
        self._command(
            "external-ready",
            "bridge.ready",
            _external_ready_payload(pid=1),
            identity,
        )
        attached = RoomStore(self.root).session("general", "external")
        self.assertEqual(attached["provider_kind"], "codex_live_session")
        self.assertEqual(attached["runtime_kind"], "live_cli")
        self.assertEqual(attached["model"], "gpt-5.6-luna")
        self.assertEqual(attached["reasoning_effort"], "low")
        self.assertEqual(attached["service_tier"], "default")
        self.assertEqual(attached["permission_mode"], "meeting_read_only")
        self.assertTrue(attached["runtime_profile_key"])
        with patch.object(
            self.room_access.sessions,
            "revoke_participant",
            return_value=1,
        ) as revoke_sessions:
            stopped = self._confirmed_external_stop(identity, channel)["result"]
        self.assertEqual(self.manager.stops, [])
        self.assertEqual(stopped["process"]["ownership"], "external")
        self.assertEqual(stopped["revoked_sessions"], 1)
        revoke_sessions.assert_called_once_with("general", "external")
        self.assertEqual(RoomStore(self.root).session("general", "external")["reported_provider_pid"], 1)
        self.controller.disconnect(channel)

    def test_external_stop_timeout_is_not_reported_as_stopped(self):
        identity = _bridge_identity("external-timeout")
        identity["provider_kind"] = "codex"
        channel = self.controller.connect(identity)
        self._command(
            "external-timeout-ready",
            "bridge.ready",
            _external_ready_payload(),
            identity,
        )

        with patch.object(
            self.room_access.sessions,
            "revoke_participant",
            return_value=1,
        ) as revoke_sessions:
            with self.assertRaises(RoomCommandRejected) as timeout:
                self._command(
                    "external-timeout-stop",
                    "agent.stop",
                    {"agent_id": "external-timeout"},
                )

        stopped = RoomStore(self.root).session("general", "external-timeout")
        self.assertEqual(timeout.exception.code, "external_stop_unconfirmed")
        self.assertEqual(stopped["runtime_status"], "disconnected")
        self.assertTrue(stopped["recovery_required"])
        self.assertFalse(stopped["enabled"])
        self.assertTrue(channel.closed)
        revoke_sessions.assert_called_once_with("general", "external-timeout")

    def test_stale_stop_confirmation_does_not_create_a_room(self):
        identity = _bridge_identity("external-stale")
        identity["meeting_id"] = "missing-room"

        with self.assertRaises(RoomCommandRejected) as stale:
            self._command(
                "stale-stop-confirmation",
                "bridge.stopped",
                {"control_id": "stop-not-pending", "stopped": True},
                identity,
            )

        self.assertEqual(stale.exception.code, "stale_stop_confirmation")
        self.assertEqual(RoomStore(self.root).room("missing-room"), {})

    def test_kick_revokes_a_nonresponsive_external_bridge_with_a_cleanup_warning(self):
        identity = _bridge_identity("external-kick-timeout")
        identity["provider_kind"] = "codex"
        channel = self.controller.connect(identity)
        self._command(
            "external-kick-timeout-ready",
            "bridge.ready",
            _external_ready_payload(),
            identity,
        )

        kicked = self._command(
            "external-kick-timeout",
            "participant.kick",
            {"participant_id": "external-kick-timeout"},
        )["result"]

        self.assertEqual(kicked["participant"]["status"], "kicked")
        self.assertIn("external_stop_unconfirmed", kicked["cleanup_warning"])
        self.assertTrue(channel.closed)

    def test_server_shutdown_does_not_revoke_external_bridge_access(self):
        identity = _bridge_identity("external-shutdown")
        identity["provider_kind"] = "codex"
        self.controller.connect(identity)
        self._command(
            "external-shutdown-ready",
            "bridge.ready",
            _external_ready_payload(),
            identity,
        )

        with patch.object(self.room_access.sessions, "revoke_participant") as revoke_sessions:
            self.controller.close()

        revoke_sessions.assert_not_called()

    def test_external_bridge_ready_requires_requested_model_for_known_provider(self):
        identity = _bridge_identity("external-no-model")
        identity["provider_kind"] = "codex"
        self.controller.connect(identity)

        with self.assertRaises(RoomCommandRejected) as rejected:
            payload = _external_ready_payload()
            payload.pop("model")
            self._command(
                "external-ready-no-model",
                "bridge.ready",
                payload,
                identity,
            )

        self.assertEqual(rejected.exception.code, "provider_profile_invalid")
        self.assertFalse(self.controller.broker.has_bridge("general", "external-no-model"))

    def test_external_bridge_ready_rejects_provider_transport_mismatch(self):
        identity = _bridge_identity("external-wrong-transport")
        identity["provider_kind"] = "codex"
        self.controller.connect(identity)

        with self.assertRaises(RoomCommandRejected) as rejected:
            self._command(
                "external-ready-wrong-transport",
                "bridge.ready",
                _external_ready_payload(transport="pty"),
                identity,
            )

        self.assertEqual(rejected.exception.code, "provider_profile_invalid")
        self.assertFalse(self.controller.broker.has_bridge("general", "external-wrong-transport"))

    def test_external_bridge_cannot_kill_an_unrelated_real_process(self):
        unrelated = subprocess.Popen(["sleep", "30"], start_new_session=True)
        try:
            identity = _bridge_identity("malicious-external")
            identity["provider_kind"] = "codex"
            channel = self.controller.connect(identity)
            self._command(
                "malicious-ready",
                "bridge.ready",
                _external_ready_payload(pid=unrelated.pid),
                identity,
            )
            self._confirmed_external_stop(
                identity,
                channel,
                request_id="malicious-stop",
            )
            self.assertIsNone(unrelated.poll())
            self._command("malicious-kick", "participant.kick", {"participant_id": "malicious-external"})
            self.assertIsNone(unrelated.poll())
            self.controller.disconnect(channel)
        finally:
            unrelated.terminate()
            unrelated.wait(timeout=2)

    def test_new_bridge_generation_supersedes_old_and_stale_disconnect_is_ignored(self):
        first_identity, first_channel = self._connect_bridge()
        second_identity = _bridge_identity("codex")
        second_channel = self.controller.connect(second_identity)
        self._command(
            "ready-second",
            "bridge.ready",
            {
                "pid": 909,
                "running": True,
                "transport": "websocket",
                "provider_session_active": True,
                "started_at": None,
            },
            second_identity,
        )
        self.assertTrue(first_channel.closed)
        with self.assertRaises(RoomCommandRejected) as stale:
            self._command("stale-health", "bridge.health", {"running": True}, first_identity)
        self.assertEqual(stale.exception.code, "stale_bridge_generation")
        self.controller.disconnect(first_channel)
        self.assertEqual(RoomStore(self.root).session("general", "codex")["runtime_status"], "idle")
        self.controller.disconnect(second_channel)

    def test_bridge_ready_rejects_incomplete_health_without_joining_participant(self):
        identity = _bridge_identity("invalid-health")
        channel = self.controller.connect(identity)

        with self.assertRaises(RoomCommandRejected) as rejected:
            self._command(
                "invalid-health-ready",
                "bridge.ready",
                {
                    "running": True,
                    "provider_session_active": True,
                    "started_at": None,
                },
                identity,
            )

        self.assertEqual(rejected.exception.code, "adapter_health_invalid")
        self.assertEqual(
            RoomStore(self.root).participant("general", "invalid-health")["status"],
            "detached",
        )
        self.assertFalse(
            any(
                event["type"] == "participant_joined"
                and event.get("participant_id") == "invalid-health"
                for event in RoomStore(self.root).read_events("general")
            )
        )
        self.controller.disconnect(channel)

    def test_unknown_moderation_does_not_create_ghost_participant(self):
        for action in ("participant.kick", "participant.mute"):
            with self.assertRaises(RoomCommandRejected) as rejected:
                self._command(f"unknown-{action}", action, {"participant_id": "ghost"})
            self.assertEqual(rejected.exception.code, "not_found")
        self.assertEqual(RoomStore(self.root).participant("general", "ghost"), {})

    def test_running_agent_profile_update_changes_canonical_identity_and_next_message(self):
        identity, channel = self._connect_bridge()
        updated = self._command(
            "profile-update",
            "agent.configure",
            {
                "agent_id": "codex",
                "display_name": "Luna",
                "avatar_image_url": "/api/room-media/avatar-luna",
            },
        )["result"]
        self.assertEqual(updated["status"], "profile_updated")
        self.assertEqual(RoomStore(self.root).participant("general", "codex")["display_name"], "Luna")
        self._command("profile-message", "message.send", {"content": "@codex introduce yourself"})
        assignment = next(message for message in channel.drain() if message.get("op") == "turn.assign")
        self.assertIn("Your display name in this room is: Luna", assignment["provider_input"])
        final = self._command(
            "profile-final",
            "message.final",
            {"turn_id": assignment["turn_id"], "content": "I am Luna."},
            identity,
        )["result"]["event"]
        self.assertEqual(final["display_name"], "Luna")
        self.assertEqual(final["avatar_image_url"], "/api/room-media/avatar-luna")

    def test_agent_profile_update_broadcast_preserves_explicit_avatar_clear(self):
        self._command(
            "profile-with-avatar",
            "agent.configure",
            {
                "agent_id": "codex",
                "display_name": "Luna",
                "avatar_image_url": "/api/room-media/avatar-luna",
            },
        )
        cleared = self._command(
            "profile-clear-avatar",
            "agent.configure",
            {
                "agent_id": "codex",
                "display_name": "Luna",
                "avatar_image_url": "",
            },
        )["result"]

        self.assertEqual(cleared["participant"]["avatar_image_url"], "")
        updates = [
            event
            for event in RoomStore(self.root).read_events("general")
            if event["type"] == "participant_updated" and event.get("participant_id") == "codex"
        ]
        self.assertIn("avatar_image_url", updates[-1])
        self.assertEqual(updates[-1]["avatar_image_url"], "")

    def test_agent_profile_update_rolls_back_when_ack_recording_fails(self):
        store = RoomStore(self.root)
        before_participant = store.participant("general", "codex")
        before_session = store.session("general", "codex")
        payload = {
            "agent_id": "codex",
            "display_name": "Atomic Luna",
            "avatar_image_url": "/api/room-media/atomic-luna",
        }

        with patch.object(RoomCommandUnitOfWork, "record_ack", side_effect=RuntimeError("injected")):
            with self.assertRaisesRegex(RuntimeError, "injected"):
                self._command("atomic-profile", "agent.configure", payload)

        self.assertEqual(store.participant("general", "codex"), before_participant)
        self.assertEqual(store.session("general", "codex"), before_session)
        self.assertFalse(
            any(
                event.get("type") == "participant_updated"
                and event.get("display_name") == "Atomic Luna"
                for event in store.read_events("general")
            )
        )

        saved = self._command("atomic-profile", "agent.configure", payload)
        duplicate = self._command("atomic-profile", "agent.configure", payload)
        self.assertEqual(saved["result"]["participant"]["display_name"], "Atomic Luna")
        self.assertTrue(duplicate["deduplicated"])
        updates = [
            event
            for event in store.read_events("general")
            if event.get("type") == "participant_updated"
            and event.get("display_name") == "Atomic Luna"
        ]
        self.assertEqual(len(updates), 1)

    def test_continuous_room_mode_relays_one_speaker_at_a_time_and_stops_at_limit(self):
        self.controller.create_provider_session("general", _spec("peer"))
        codex_identity, codex_channel = self._connect_bridge("codex")
        peer_identity, peer_channel = self._connect_bridge("peer")
        self.controller.store.update_room_settings(
            "general",
            {"conversation_mode": "continuous", "max_relay_turns": 2},
        )
        self._command("continuous-topic", "message.send", {"content": "둘이 이어서 이야기해"})
        first = next(message for message in codex_channel.drain() if message.get("op") == "turn.assign")
        self.assertFalse(any(message.get("op") == "turn.assign" for message in peer_channel.drain()))
        self._command(
            "continuous-first",
            "message.final",
            {"turn_id": first["turn_id"], "content": "첫 의견이야."},
            codex_identity,
        )
        second = next(message for message in peer_channel.drain() if message.get("op") == "turn.assign")
        self._command(
            "continuous-second",
            "message.final",
            {"turn_id": second["turn_id"], "content": "두 번째 의견이야."},
            peer_identity,
        )
        self.assertFalse(any(message.get("op") == "turn.assign" for message in codex_channel.drain()))
        finals = [event for event in RoomStore(self.root).read_events("general") if event.get("type") == "message_final"]
        self.assertEqual([event.get("participant_id") for event in finals[-3:]], ["operator-local", "codex", "peer"])

    def test_routing_ignores_conflicting_legacy_room_settings_file(self):
        self.controller.create_provider_session("general", _spec("peer"))
        codex_identity, codex_channel = self._connect_bridge("codex")
        _peer_identity, peer_channel = self._connect_bridge("peer")
        update_legacy_room_settings(
            self.root,
            {
                "room_id": "general",
                "conversation_mode": "continuous",
                "max_relay_turns": 4,
            },
        )

        self._command("repository-mode-topic", "message.send", {"content": "한 명만 답해"})
        first = next(
            message for message in codex_channel.drain() if message.get("op") == "turn.assign"
        )
        peer_channel.drain()
        self._command(
            "repository-mode-final",
            "message.final",
            {"turn_id": first["turn_id"], "content": "DB 설정은 ordered야."},
            codex_identity,
        )

        self.assertEqual(
            self.controller.store.room_settings("general")["conversation_mode"],
            "ordered",
        )
        self.assertFalse(
            any(message.get("op") == "turn.assign" for message in peer_channel.drain())
        )

    def test_ambient_mode_leases_one_fair_speaker_and_releases_each_turn(self):
        self.controller.create_provider_session("general", _spec("peer"))
        codex_identity, codex_channel = self._connect_bridge("codex")
        peer_identity, peer_channel = self._connect_bridge("peer")
        codex_channel.drain()
        peer_channel.drain()
        self.controller.store.update_room_settings(
            "general",
            {"conversation_mode": "ambient", "max_relay_turns": 2},
        )

        source = self._command(
            "ambient-topic",
            "message.send",
            {"content": "백룸에서 살아남는 방법을 같이 이야기해봐"},
        )["result"]["event"]
        first = next(message for message in codex_channel.drain() if message.get("op") == "turn.assign")
        self.assertFalse(any(message.get("op") == "turn.assign" for message in peer_channel.drain()))
        first_job = self.controller.store.attention_jobs("general", mode="active")[-1]
        first_session = self.controller.store.session("general", "codex")
        first_lease_id = str(first_session["active_attention_lease_id"])
        self.assertEqual(first_job["source_event_id"], source["id"])
        self.assertEqual(first_job["status"], "leased")
        self.assertEqual(first_job["selected_participant_id"], "codex")

        codex_final = self._command(
            "ambient-first-final",
            "message.final",
            {"turn_id": first["turn_id"], "content": "먼저 출구 표식을 남겨야 해."},
            codex_identity,
        )["result"]["event"]
        second = next(message for message in peer_channel.drain() if message.get("op") == "turn.assign")
        self.assertEqual(self.controller.store.attention_lease("general", first_lease_id)["status"], "released")
        self.assertEqual(
            self.controller.store.attention_jobs("general", mode="active")[0]["status"],
            "completed",
        )

        self._command(
            "ambient-second-final",
            "message.final",
            {"turn_id": second["turn_id"], "content": "그리고 소음을 최소화해야 해."},
            peer_identity,
        )
        self.assertFalse(any(message.get("op") == "turn.assign" for message in codex_channel.drain()))
        jobs = self.controller.store.attention_jobs("general", mode="active")
        self.assertEqual([job["status"] for job in jobs], ["completed", "completed", "completed"])
        self.assertEqual(jobs[-1]["reasons"], ["agent_chain_budget_exhausted"])
        self.assertEqual(
            self.controller.store.attention_state("general", "codex").last_spoke_seq,
            codex_final["seq"],
        )
        self.assertEqual(self.controller.attention_active_diagnostics()["error_count"], 0)

    def test_provider_final_rolls_back_turn_session_cursors_and_lease_with_ack(self):
        identity, channel = self._connect_bridge("codex")
        channel.drain()
        self.controller.store.update_room_settings(
            "general",
            {"conversation_mode": "ambient", "max_relay_turns": 2},
        )
        source = self._command(
            "atomic-final-source",
            "message.send",
            {"content": "원자적으로 답해줘"},
        )["result"]["event"]
        assignment = next(message for message in channel.drain() if message.get("op") == "turn.assign")
        store = RoomStore(self.root)
        before_session = store.session("general", "codex")
        before_attention = store.attention_state("general", "codex")
        lease_id = str(before_session["active_attention_lease_id"])
        before_latest_seq = store.latest_event_sequence("general")
        payload = {"turn_id": assignment["turn_id"], "content": "원자적 최종 답변"}

        with patch.object(RoomCommandUnitOfWork, "record_ack", side_effect=RuntimeError("injected")):
            with self.assertRaisesRegex(RuntimeError, "injected"):
                self._command("atomic-provider-final", "message.final", payload, identity)

        rolled_back = store.session("general", "codex")
        self.assertEqual(rolled_back["runtime_status"], "busy")
        self.assertEqual(rolled_back["active_turn_id"], assignment["turn_id"])
        self.assertEqual(rolled_back["inflight_event_ids"], before_session["inflight_event_ids"])
        self.assertEqual(rolled_back["last_provider_sync_seq"], before_session["last_provider_sync_seq"])
        self.assertEqual(store.attention_state("general", "codex"), before_attention)
        self.assertEqual(store.attention_lease("general", lease_id)["status"], "active")
        self.assertEqual(store.latest_event_sequence("general"), before_latest_seq)
        self.assertFalse(
            any(
                event.get("type") == "message_final"
                and event.get("content") == "원자적 최종 답변"
                for event in store.read_events("general")
            )
        )

        completed = self._command("atomic-provider-final", "message.final", payload, identity)
        duplicate = self._command("atomic-provider-final", "message.final", payload, identity)
        self.assertFalse(completed["deduplicated"])
        self.assertTrue(duplicate["deduplicated"])
        current = store.session("general", "codex")
        self.assertEqual(current["runtime_status"], "idle")
        self.assertEqual(current["active_turn_id"], "")
        self.assertEqual(current["inflight_event_ids"], [])
        self.assertEqual(current["last_provider_sync_seq"], source["seq"])
        self.assertEqual(
            store.attention_state("general", "codex").last_provider_sync_seq,
            source["seq"],
        )
        self.assertEqual(store.attention_lease("general", lease_id)["status"], "released")
        finals = [
            event
            for event in store.read_events("general")
            if event.get("type") == "message_final"
            and event.get("content") == "원자적 최종 답변"
        ]
        finished = [
            event
            for event in store.read_events("general")
            if event.get("type") == "turn_finished"
            and event.get("turn_id") == assignment["turn_id"]
        ]
        self.assertEqual(len(finals), 1)
        self.assertEqual(len(finished), 1)
        self.assertEqual(store.attention_state("general", "codex").last_spoke_seq, finals[0]["seq"])

    def test_ambient_mode_does_not_replace_unavailable_explicit_target(self):
        self.controller.create_provider_session("general", _spec("peer"))
        _peer_identity, peer_channel = self._connect_bridge("peer")
        peer_channel.drain()
        self.controller.store.update_room_settings(
            "general",
            {"conversation_mode": "ambient", "max_relay_turns": 2},
        )

        self._command("ambient-unavailable", "message.send", {"content": "@codex 답해줘"})

        self.assertFalse(any(message.get("op") == "turn.assign" for message in peer_channel.drain()))
        job = self.controller.store.attention_jobs("general", mode="active")[-1]
        self.assertEqual(job["outcome"], "silent")
        self.assertIn("explicit_target_unavailable", job["reasons"])
        self.assertEqual(self.controller.store.session("general", "peer")["pending_event_ids"], [])

    def test_ambient_mode_records_vote_as_silent_without_waking_provider(self):
        _identity, channel = self._connect_bridge("codex")
        channel.drain()
        self.controller.store.update_room_settings(
            "general",
            {"conversation_mode": "ambient", "max_relay_turns": 2},
        )

        event = self._command(
            "ambient-vote",
            "message.send",
            {
                "kind": "vote",
                "vote_id": "vote-1",
                "vote_question": "어디로 갈까?",
                "vote_options": ["왼쪽", "오른쪽"],
            },
        )["result"]["event"]

        self.assertFalse(any(message.get("op") == "turn.assign" for message in channel.drain()))
        job = self.controller.store.attention_jobs("general", mode="active")[-1]
        self.assertEqual(job["source_event_id"], event["id"])
        self.assertEqual(job["outcome"], "silent")
        self.assertEqual(job["reasons"], ["ambient_vote_event"])

    def test_stopping_ambient_speaker_cancels_lease_and_drops_selected_work(self):
        _identity, channel = self._connect_bridge("codex")
        channel.drain()
        self.controller.store.update_room_settings(
            "general",
            {"conversation_mode": "ambient"},
        )
        source = self._command(
            "ambient-stop-source",
            "message.send",
            {"content": "이 대화는 중단할 거야"},
        )["result"]["event"]
        next(message for message in channel.drain() if message.get("op") == "turn.assign")
        active = self.controller.store.session("general", "codex")
        lease_id = str(active["active_attention_lease_id"])

        self._command("ambient-stop", "agent.stop", {"agent_id": "codex"})

        stopped = self.controller.store.session("general", "codex")
        self.assertEqual(self.controller.store.attention_lease("general", lease_id)["status"], "cancelled")
        self.assertEqual(
            self.controller.store.attention_jobs("general", mode="active")[-1]["status"],
            "cancelled",
        )
        self.assertNotIn(source["id"], stopped["pending_event_ids"])
        self.assertEqual(stopped["active_attention_lease_id"], "")

    def test_ambient_attention_failure_is_visible_and_never_uses_legacy_routing(self):
        self.controller.store.update_room_settings(
            "general",
            {"conversation_mode": "ambient", "max_relay_turns": 2},
        )
        with patch.object(
            self.controller._attention_coordinator,
            "evaluate_and_queue_active",
            side_effect=RuntimeError("attention repository unavailable"),
        ), self.assertLogs("agentsassemble.room_realtime", level="ERROR"):
            self._command("ambient-error", "message.send", {"content": "이 메시지를 처리해"})

        events = self.controller.store.read_events("general")
        diagnostics = self.controller.attention_active_diagnostics()
        self.assertEqual(diagnostics["error_count"], 1)
        self.assertIn("attention repository unavailable", diagnostics["last_error"])
        self.assertEqual(events[-1]["type"], "error")
        self.assertEqual(events[-1]["error_code"], "ambient_attention_failed")
        self.assertEqual(self.controller.store.session("general", "codex")["pending_event_ids"], [])

    def test_continuous_room_mode_skips_removed_and_stopped_speakers(self):
        self.controller.create_provider_session("general", _spec("removed"))
        self.controller.create_provider_session("general", _spec("stopped"))
        self.controller.create_provider_session("general", _spec("disconnected"))
        self.controller.create_provider_session("general", _spec("failed"))
        self.controller.create_provider_session("general", _spec("active"))
        self._command("kick-removed", "participant.kick", {"participant_id": "removed"})
        self._command("start-stopped", "agent.start", {"agent_id": "stopped"})
        self._command("stop-stopped", "agent.stop", {"agent_id": "stopped"})
        self.controller.store.update_session_fields(
            "general",
            "disconnected",
            status="unavailable",
            runtime_status="disconnected",
            enabled=True,
        )
        self.controller.store.update_session_fields(
            "general",
            "failed",
            status="attached",
            runtime_status="error",
            enabled=True,
        )
        _active_identity, active_channel = self._connect_bridge("active")
        self.controller.store.update_room_settings(
            "general",
            {"conversation_mode": "continuous", "max_relay_turns": 2},
        )

        self._command("continuous-active-topic", "message.send", {"content": "활성 참가자만 이어서 말해"})

        assignment = next(message for message in active_channel.drain() if message.get("op") == "turn.assign")
        self.assertTrue(assignment["turn_id"])
        self.assertEqual(RoomStore(self.root).session("general", "removed")["pending_event_ids"], [])
        self.assertEqual(RoomStore(self.root).session("general", "stopped")["pending_event_ids"], [])
        self.assertEqual(RoomStore(self.root).session("general", "disconnected")["pending_event_ids"], [])
        self.assertEqual(RoomStore(self.root).session("general", "failed")["pending_event_ids"], [])

    def test_continuous_floor_eligibility_requires_joined_idle_attached_bridge(self):
        _identity, channel = self._connect_bridge("codex")
        self.assertEqual(
            self.controller.agent_floor_eligibility("general", "codex").reason_code,
            "eligible",
        )

        for fields, expected in (
            ({"participant_status": "detached"}, "participant_not_joined"),
            ({"status": "unavailable"}, "session_not_attached"),
            ({"enabled": False}, "session_disabled"),
            ({"runtime_status": "busy"}, "runtime_busy"),
            ({"runtime_status": "paused"}, "runtime_paused"),
            ({"runtime_status": "recovering"}, "runtime_recovering"),
            ({"runtime_status": "error"}, "runtime_error"),
        ):
            with self.subTest(expected=expected):
                self.controller.store.update_participant_fields("general", "codex", status="joined")
                self.controller.store.update_session_fields(
                    "general",
                    "codex",
                    status="attached",
                    enabled=True,
                    runtime_status="idle",
                )
                if "participant_status" in fields:
                    self.controller.store.update_participant_fields(
                        "general", "codex", status=fields["participant_status"]
                    )
                else:
                    self.controller.store.update_session_fields("general", "codex", **fields)
                self.assertEqual(
                    self.controller.agent_floor_eligibility("general", "codex").reason_code,
                    expected,
                )

        self.controller.store.update_participant_fields("general", "codex", status="joined")
        self.controller.store.update_session_fields(
            "general", "codex", status="attached", enabled=True, runtime_status="idle"
        )
        self.controller.broker.disconnect(channel)
        self.assertEqual(
            self.controller.agent_floor_eligibility("general", "codex").reason_code,
            "bridge_disconnected",
        )

    def test_explicit_decline_finishes_without_message_or_continuous_relay(self):
        self.controller.create_provider_session("general", _spec("peer"))
        codex_identity, codex_channel = self._connect_bridge("codex")
        _peer_identity, peer_channel = self._connect_bridge("peer")
        self.controller.store.update_room_settings(
            "general",
            {"conversation_mode": "continuous", "max_relay_turns": 4},
        )
        self._command("silent-topic", "message.send", {"content": "조용히 있어"})
        assignment = next(message for message in codex_channel.drain() if message.get("op") == "turn.assign")

        result = self._command(
            "decline-turn",
            "turn.decline",
            {"turn_id": assignment["turn_id"], "reason_code": "nothing_useful_to_add"},
            codex_identity,
        )["result"]

        self.assertTrue(result["declined"])
        self.assertFalse(any(message.get("op") == "turn.assign" for message in peer_channel.drain()))
        events = RoomStore(self.root).read_events("general")
        self.assertEqual(
            [event.get("participant_id") for event in events if event.get("type") == "message_final"],
            ["operator-local"],
        )
        self.assertEqual(events[-2]["type"], "turn_finished")
        self.assertEqual(events[-2]["status"], "declined")
        self.assertEqual(RoomStore(self.root).session("general", "codex")["runtime_status"], "idle")

    def test_zero_width_final_is_an_error_not_silence(self):
        identity, channel = self._connect_bridge("codex")
        self._command("empty-final-topic", "message.send", {"content": "@codex answer"})
        assignment = next(message for message in channel.drain() if message.get("op") == "turn.assign")

        with self.assertRaises(RoomCommandRejected) as rejected:
            self._command(
                "empty-final",
                "message.final",
                {"turn_id": assignment["turn_id"], "content": "\u200b"},
                identity,
            )

        self.assertEqual(rejected.exception.code, "empty_provider_final")
        session = RoomStore(self.root).session("general", "codex")
        self.assertEqual(session["runtime_status"], "error")
        errors = [event for event in RoomStore(self.root).read_events("general") if event["type"] == "error"]
        self.assertEqual(errors[-1]["error_code"], "empty_provider_final")

    def test_exact_model_mismatch_fails_before_publishing_message(self):
        RoomStore(self.root).update_session_fields(
            "general",
            "codex",
            model="gpt-exact-requested",
            requested_model_id="gpt-exact-requested",
            observed_model_id="",
            model_selection_kind="exact",
            model_observation_policy="required",
        )
        identity, channel = self._connect_bridge("codex")
        self._command("model-mismatch-topic", "message.send", {"content": "@codex answer"})
        assignment = next(message for message in channel.drain() if message.get("op") == "turn.assign")

        with self.assertRaises(RoomCommandRejected) as rejected:
            self._command(
                "model-mismatch-final",
                "message.final",
                {
                    "turn_id": assignment["turn_id"],
                    "content": "must not be published",
                    "observed_model_id": "gpt-different-observed",
                },
                identity,
            )

        self.assertEqual(rejected.exception.code, "provider_model_mismatch")
        events = RoomStore(self.root).read_events("general")
        self.assertFalse(
            any(event["type"] == "message_final" and event.get("content") == "must not be published" for event in events)
        )
        self.assertEqual(
            next(event for event in reversed(events) if event["type"] == "error")["error_code"],
            "provider_model_mismatch",
        )
        self.assertEqual(RoomStore(self.root).session("general", "codex")["observed_model_id"], "")

    def test_required_model_observation_missing_fails_before_publishing_message(self):
        RoomStore(self.root).update_session_fields(
            "general",
            "codex",
            model="gpt-exact-requested",
            requested_model_id="gpt-exact-requested",
            observed_model_id="",
            model_selection_kind="exact",
            model_observation_policy="required",
        )
        identity, channel = self._connect_bridge("codex")
        self._command("model-unobserved-topic", "message.send", {"content": "@codex answer"})
        assignment = next(message for message in channel.drain() if message.get("op") == "turn.assign")

        with self.assertRaises(RoomCommandRejected) as rejected:
            self._command(
                "model-unobserved-final",
                "message.final",
                {
                    "turn_id": assignment["turn_id"],
                    "content": "must not be published",
                },
                identity,
            )

        self.assertEqual(rejected.exception.code, "provider_model_unobserved")
        events = RoomStore(self.root).read_events("general")
        self.assertFalse(
            any(event["type"] == "message_final" and event.get("content") == "must not be published" for event in events)
        )

    def test_alias_model_records_provider_observed_exact_model(self):
        RoomStore(self.root).update_session_fields(
            "general",
            "codex",
            model="sonnet",
            requested_model_id="sonnet",
            observed_model_id="",
            model_selection_kind="alias",
            model_observation_policy="required",
        )
        identity, channel = self._connect_bridge("codex")
        self._command("alias-model-topic", "message.send", {"content": "@codex answer"})
        assignment = next(message for message in channel.drain() if message.get("op") == "turn.assign")

        result = self._command(
            "alias-model-final",
            "message.final",
            {
                "turn_id": assignment["turn_id"],
                "content": "alias resolved",
                "observed_model_id": "claude-sonnet-4-6",
            },
            identity,
        )["result"]

        self.assertEqual(result["event"]["content"], "alias resolved")
        session = RoomStore(self.root).session("general", "codex")
        self.assertEqual(session["requested_model_id"], "sonnet")
        self.assertEqual(session["observed_model_id"], "claude-sonnet-4-6")
        self.assertEqual(session["model_selection_kind"], "alias")
        self.assertEqual(session["model_verification_status"], "resolved_alias")

    def test_claude_release_accepts_the_provider_reported_snapshot_id(self):
        RoomStore(self.root).update_session_fields(
            "general",
            "codex",
            provider_kind="claude_code",
            model="claude-haiku-4-5",
            requested_model_id="claude-haiku-4-5",
            observed_model_id="",
            model_selection_kind="exact",
            model_observation_policy="required",
        )
        identity, channel = self._connect_bridge("codex")
        self._command("claude-snapshot-topic", "message.send", {"content": "@codex answer"})
        assignment = next(message for message in channel.drain() if message.get("op") == "turn.assign")

        result = self._command(
            "claude-snapshot-final",
            "message.final",
            {
                "turn_id": assignment["turn_id"],
                "content": "verified Haiku reply",
                "observed_model_id": "claude-haiku-4-5-20251001",
            },
            identity,
        )["result"]

        self.assertEqual(result["event"]["content"], "verified Haiku reply")
        session = RoomStore(self.root).session("general", "codex")
        self.assertEqual(session["observed_model_id"], "claude-haiku-4-5-20251001")
        self.assertEqual(session["model_verification_status"], "verified_provider_revision")

    def test_claude_release_rejects_a_different_provider_release(self):
        RoomStore(self.root).update_session_fields(
            "general",
            "codex",
            provider_kind="claude_code",
            model="claude-haiku-4-5",
            requested_model_id="claude-haiku-4-5",
            observed_model_id="",
            model_selection_kind="exact",
            model_observation_policy="required",
        )
        identity, channel = self._connect_bridge("codex")
        self._command("claude-wrong-release-topic", "message.send", {"content": "@codex answer"})
        assignment = next(message for message in channel.drain() if message.get("op") == "turn.assign")

        with self.assertRaises(RoomCommandRejected) as rejected:
            self._command(
                "claude-wrong-release-final",
                "message.final",
                {
                    "turn_id": assignment["turn_id"],
                    "content": "must not be published",
                    "observed_model_id": "claude-sonnet-4-6-20260217",
                },
                identity,
            )

        self.assertEqual(rejected.exception.code, "provider_model_mismatch")

    def test_snapshot_and_visible_events_do_not_expose_process_or_path_fields(self):
        self.controller.store.update_session_fields(
            "general",
            "codex",
            pid=123,
            reported_provider_pid=456,
            bridge_pid=789,
            bridge_handle_id="secret-handle",
            resolved_executable="/private/bin/codex",
            workspace="/private/workspace",
            command_configured=["codex", "--secret"],
            provider_session_id="provider-secret",
        )
        event = self.controller.store.append_event(
            "general",
            "message_final",
            participant_id="operator-local",
            content="safe",
            legacy_source_path="/private/legacy.jsonl",
            media={"id": "media-1", "filename": "image.png", "path": "/private/image.png"},
        )
        snapshot = self.controller.snapshot(HOST)
        public_session = next(item for item in snapshot["agent_sessions"] if item["session_id"] == "codex")
        serialized = str(snapshot)
        for forbidden in (
            "secret-handle",
            "/private/bin/codex",
            "/private/workspace",
            "provider-secret",
            "/private/legacy.jsonl",
            "/private/image.png",
        ):
            self.assertNotIn(forbidden, serialized)
        self.assertNotIn("pid", public_session)
        self.assertNotIn("legacy_source_path", event)
        self.assertNotIn("path", event["media"])

    def test_member_leave_and_owner_confirmed_delete(self):
        identity_store = identity_store_for_output_root(self.root)
        identity_store.upsert_room(room_id="general", owner_id="owner-user", label="Council")
        identity_store.resolve_credential_user(
            "owner-device",
            user_id="owner-user",
            participant_id="owner",
            display_name="Owner",
        )
        owner = {**HOST, "agent_id": "owner"}
        member = {**HOST, "agent_id": "member", "operator": False}
        artifact = self.root / "rooms" / "general" / "media" / "artifact.txt"
        artifact.parent.mkdir(parents=True, exist_ok=True)
        artifact.write_text("delete me", encoding="utf-8")
        for workflow_id, status in (
            ("terminal-admission", "completed"),
            ("retryable-admission", "failed_retryable"),
        ):
            self.room_access.repository.create_admission_workflow(
                workflow_id,
                {
                    "request_id": f"request-{workflow_id}",
                    "token_fingerprint": f"token-{workflow_id}",
                    "payload_hash": f"payload-{workflow_id}",
                    "status": status,
                    "resume_phase": status,
                    "room_id": "general",
                    "created_at": "2026-07-15T00:00:00+00:00",
                    "updated_at": "2026-07-15T00:00:00+00:00",
                },
            )
        self.controller.connect(owner)
        self.controller.connect(member)
        with self.assertRaises(RoomCommandRejected) as owner_leave:
            self._command("owner-leave", "participant.leave", {}, owner)
        self.assertEqual(owner_leave.exception.code, "owner_must_transfer_or_delete")
        left = self._command("member-leave", "participant.leave", {}, member)
        self.assertTrue(left["accepted"])
        self.assertEqual(RoomStore(self.root).participant("general", "member")["status"], "left")
        stale_channel = self.controller.connect(member)
        self.assertEqual(RoomStore(self.root).participant("general", "member")["status"], "left")
        self.controller.disconnect(stale_channel)
        with self.assertRaises(RoomCommandRejected) as mismatch:
            self._command("delete-wrong", "room.delete", {"confirmation_name": "Wrong"}, owner)
        self.assertEqual(mismatch.exception.code, "confirmation_mismatch")
        deleted = self._command("delete-right", "room.delete", {"confirmation_name": "Council"}, owner)
        self.assertTrue(deleted["result"]["deleted"])
        self.assertEqual(deleted["result"]["purged_admission_workflows"], 1)
        self.assertIsNone(
            self.room_access.repository.admission_workflow("terminal-admission")
        )
        self.assertIsNotNone(
            self.room_access.repository.admission_workflow("retryable-admission")
        )
        self.assertTrue(RoomStore(self.root).room_is_deleted("general"))
        self.assertIsNone(identity_store.get_room("general"))
        self.assertFalse(artifact.exists())
        with self.assertRaises(ValueError):
            self.controller.ensure_room("general")

    def test_member_leave_rolls_back_when_ack_recording_fails(self):
        member = {**HOST, "agent_id": "atomic-member", "operator": False}
        channel = self.controller.connect(member)
        store = RoomStore(self.root)

        with patch.object(RoomCommandUnitOfWork, "record_ack", side_effect=RuntimeError("injected")):
            with self.assertRaisesRegex(RuntimeError, "injected"):
                self._command("atomic-leave", "participant.leave", {}, member)

        self.assertEqual(store.participant("general", "atomic-member")["status"], "joined")
        self.assertFalse(
            any(
                event.get("type") == "participant_left"
                and event.get("participant_id") == "atomic-member"
                for event in store.read_events("general")
            )
        )

        left = self._command("atomic-leave", "participant.leave", {}, member)
        duplicate = self._command("atomic-leave", "participant.leave", {}, member)
        self.assertEqual(left["result"]["participant"]["status"], "left")
        self.assertTrue(duplicate["deduplicated"])
        left_events = [
            event
            for event in store.read_events("general")
            if event.get("type") == "participant_left"
            and event.get("participant_id") == "atomic-member"
        ]
        self.assertEqual(len(left_events), 1)
        self.controller.disconnect(channel)

    def test_room_delete_keeps_data_when_agent_cleanup_fails(self):
        identity_store = identity_store_for_output_root(self.root)
        identity_store.upsert_room(room_id="general", owner_id="owner-user", label="Council")
        identity_store.resolve_credential_user(
            "owner-device-cleanup",
            user_id="owner-user",
            participant_id="owner",
            display_name="Owner",
        )
        owner = {**HOST, "agent_id": "owner"}
        self.controller.connect(owner)
        self._command("cleanup-start", "agent.start", {"agent_id": "codex"})
        self.manager.stop_errors.append(RuntimeError("provider cleanup failed"))

        with self.assertRaises(RoomCommandRejected) as blocked:
            self._command(
                "cleanup-delete",
                "room.delete",
                {"confirmation_name": "Council"},
                owner,
            )

        self.assertEqual(blocked.exception.code, "room_cleanup_failed")
        self.assertFalse(RoomStore(self.root).room_is_deleted("general"))
        self.assertIsNotNone(identity_store.get_room("general"))
        session = RoomStore(self.root).session("general", "codex")
        self.assertEqual(session["runtime_status"], "disconnected")
        self.assertTrue(session["recovery_required"])

    def test_room_delete_revokes_disconnected_external_session_without_blocking(self):
        identity_store = identity_store_for_output_root(self.root)
        identity_store.upsert_room(room_id="general", owner_id="owner-user", label="Council")
        identity_store.resolve_credential_user(
            "owner-device-external-cleanup",
            user_id="owner-user",
            participant_id="owner",
            display_name="Owner",
        )
        owner = {**HOST, "agent_id": "owner"}
        self.controller.connect(owner)
        self.controller.create_provider_session("general", _spec("external-disconnected"))
        self.controller.store.update_session_fields(
            "general",
            "external-disconnected",
            process_ownership="external",
            external_owned=True,
            runtime_status="disconnected",
            recovery_required=True,
        )

        with patch.object(
            self.room_access.sessions,
            "revoke_participant",
            return_value=1,
        ) as revoke_sessions:
            deleted = self._command(
                "delete-disconnected-external",
                "room.delete",
                {"confirmation_name": "Council"},
                owner,
            )["result"]

        self.assertTrue(deleted["deleted"])
        self.assertIn("without claiming provider shutdown", deleted["cleanup_warnings"][0])
        revoke_sessions.assert_called_once_with("general", "external-disconnected")
        self.assertTrue(RoomStore(self.root).room_is_deleted("general"))

    def test_room_delete_retry_resumes_tombstone_cleanup_without_stopping_twice(self):
        identity_store = identity_store_for_output_root(self.root)
        identity_store.upsert_room(room_id="general", owner_id="owner-user", label="Council")
        identity_store.resolve_credential_user(
            "owner-device-delete-retry",
            user_id="owner-user",
            participant_id="owner",
            display_name="Owner",
        )
        owner = {**HOST, "agent_id": "owner"}
        self.controller.connect(owner)
        self._command("delete-retry-start", "agent.start", {"agent_id": "codex"})

        with patch.object(
            self.controller.store,
            "update_deleted_room_record",
            side_effect=RuntimeError("injected tombstone cleanup failure"),
        ):
            with self.assertRaisesRegex(RuntimeError, "injected tombstone cleanup failure"):
                self._command(
                    "delete-retry-request",
                    "room.delete",
                    {"confirmation_name": "Council"},
                    owner,
                )

        pending = self.controller.store.deleted_room_record("general")
        self.assertEqual(pending["cleanup_status"], "pending")
        self.assertEqual(pending["request_id"], "delete-retry-request")
        self.assertEqual(self.manager.stops, [("general", "codex")])
        self.assertIsNone(identity_store.get_room("general"))

        recovered = self._command(
            "delete-retry-request",
            "room.delete",
            {"confirmation_name": "Council"},
            owner,
        )

        self.assertTrue(recovered["deduplicated"])
        self.assertTrue(recovered["result"]["deleted"])
        self.assertEqual(self.manager.stops, [("general", "codex")])
        self.assertEqual(
            self.controller.store.deleted_room_record("general")["cleanup_status"],
            "complete",
        )
        with self.assertRaises(RoomCommandRejected) as conflict:
            self._command(
                "delete-retry-request",
                "room.delete",
                {"confirmation_name": "Different"},
                owner,
            )
        self.assertEqual(conflict.exception.code, "idempotency_conflict")
        with self.assertRaises(RoomCommandRejected) as deleted:
            self._command(
                "different-delete-request",
                "room.delete",
                {"confirmation_name": "Council"},
                owner,
            )
        self.assertEqual(deleted.exception.code, "room_deleted")

    def test_snapshot_capabilities_are_server_authoritative(self):
        operator_snapshot = self.controller.snapshot(HOST)
        operator = operator_snapshot["capabilities"]
        guest = self.controller.snapshot(
            {**HOST, "operator": False, "invite_scope": "read_write", "agent_id": "guest"}
        )["capabilities"]

        self.assertTrue(operator["participant.kick"])
        self.assertTrue(operator["participant.mute"])
        self.assertTrue(operator["agent.control"])
        self.assertFalse(guest["participant.kick"])
        self.assertFalse(guest["participant.mute"])
        self.assertFalse(guest["agent.control"])
        self.assertEqual(
            [provider["id"] for provider in operator_snapshot["available_providers"]],
            ["codex", "antigravity", "grok", "claude", "opencode", "deepseek"],
        )

    def test_snapshot_is_bounded_and_history_pages_are_read_only(self):
        for index in range(250):
            self.controller.store.append_event("general", "message_final", content=f"message-{index}")

        snapshot = self.controller.snapshot(HOST)
        page_ack = self._command(
            "req-history",
            "room.history",
            {"before_seq": snapshot["oldest_seq"], "limit": 200},
        )
        page = page_ack["result"]

        self.assertEqual(snapshot["snapshot_mode"], "initial")
        self.assertEqual(len(snapshot["events"]), 200)
        self.assertEqual(snapshot["events"][0]["content"], "message-50")
        self.assertEqual(snapshot["events"][-1]["content"], "message-249")
        self.assertTrue(snapshot["has_more_before"])
        self.assertFalse(snapshot["resume_gap"])
        self.assertEqual(len(page["events"]), 51)
        self.assertFalse(page["has_more_before"])
        self.assertEqual(page["events"][-1]["content"], "message-49")
        self.assertEqual(self.controller.store.command_result("general", "req-history"), {})

    def test_resume_snapshot_replays_small_gap_and_reports_large_gap(self):
        for index in range(230):
            self.controller.store.append_event("general", "message_final", content=f"message-{index}")
        latest = self.controller.store.latest_event_sequence("general")

        exact = self.controller.snapshot(HOST, after_seq=latest - 2)
        gap = self.controller.snapshot(HOST, after_seq=1)

        self.assertEqual(exact["snapshot_mode"], "resume")
        self.assertEqual(len(exact["events"]), 2)
        self.assertFalse(exact["resume_gap"])
        self.assertEqual(gap["snapshot_mode"], "gap")
        self.assertEqual(len(gap["events"]), 200)
        self.assertTrue(gap["resume_gap"])

    def test_agent_bridge_snapshot_does_not_replay_room_history(self):
        self.controller.store.append_event("general", "message_final", content="private room history")

        snapshot = self.controller.snapshot(_bridge_identity())

        self.assertEqual(snapshot["snapshot_mode"], "bridge")
        self.assertEqual(snapshot["events"], [])
        self.assertFalse(snapshot["has_more_before"])

    def test_bridge_crash_restarts_once_with_room_memory_and_pending_diff(self):
        self._command("req-start-recovery", "agent.start", {"agent_id": "codex"})
        self.controller.store.update_session_fields(
            "general",
            "codex",
            room_memory={"summary": "Compact recovery memory.", "decisions": [], "open_questions": []},
        )
        _identity, first_channel = self._connect_bridge()
        self._command("req-crash-source", "message.send", {"content": "@codex recover this turn"})
        first_assignment = next(
            message for message in first_channel.drain() if message.get("op") == "turn.assign"
        )
        self.controller.broker.disconnect(first_channel)

        self.controller.bridge_process_exited("general", "codex", 17, "fatal provider stderr")
        recovering = self.controller.store.session("general", "codex")

        self.assertEqual(self.recovery_scheduler.delays, [1.0])
        self.assertEqual(recovering["runtime_status"], "recovering")
        self.assertEqual(recovering["recovery_attempt_count"], 1)
        self.assertTrue(recovering["recovery_required"])
        self.assertFalse(recovering["provider_session_active"])
        self.assertIn(first_assignment["source_event_id"], recovering["pending_event_ids"])
        self.assertIn("fatal provider stderr", recovering["stderr_tail"])
        crash_error = next(
            event
            for event in reversed(self.controller.store.read_events("general"))
            if event.get("type") == "error" and event.get("error_code") == "bridge_process_exited"
        )
        self.assertNotIn("stderr_tail", crash_error)
        self.assertTrue(crash_error["stderr_tail_present"])

        self.recovery_scheduler.run_next()
        self.assertEqual(self.manager.starts, [("general", "codex"), ("general", "codex")])
        _identity, second_channel = self._connect_bridge()
        recovered_assignment = next(
            message for message in second_channel.drain() if message.get("op") == "turn.assign"
        )

        self.assertIn("[Agent Session recovery]", recovered_assignment["provider_input"])
        self.assertIn("Compact recovery memory.", recovered_assignment["provider_input"])
        self.assertIn("recover this turn", recovered_assignment["provider_input"])

        self.controller.broker.disconnect(second_channel)
        self.controller.bridge_process_exited("general", "codex", 18, "failed again")
        failed = self.controller.store.session("general", "codex")

        self.assertEqual(self.recovery_scheduler.pending, [])
        self.assertEqual(failed["runtime_status"], "error")
        self.assertTrue(failed["recovery_required"])

    def test_provider_process_exit_retries_turn_once_but_auth_failure_does_not(self):
        self._command("req-start-provider-retry", "agent.start", {"agent_id": "codex"})
        identity, channel = self._connect_bridge()
        self._command("req-provider-retry-source", "message.send", {"content": "@codex retry provider"})
        first_assignment = next(message for message in channel.drain() if message.get("op") == "turn.assign")
        self.controller.store.update_session_fields(
            "general",
            "codex",
            active_relay_depth=2,
        )

        self._command(
            "req-provider-exit",
            "turn.failed",
            {
                "turn_id": first_assignment["turn_id"],
                "message": "Live CLI runtime exited with return code 9.",
                "diagnostics": {"running": False, "returncode": 9},
            },
            identity,
        )
        self.assertEqual(len(self.recovery_scheduler.pending), 1)

        self.recovery_scheduler.run_next()
        retry_assignment = next(message for message in channel.drain() if message.get("op") == "turn.assign")
        self.assertIn("[Agent Session recovery]", retry_assignment["provider_input"])
        self.assertIn("retry provider", retry_assignment["provider_input"])
        self.assertEqual(self.controller.store.session("general", "codex")["active_relay_depth"], 2)
        self.controller.store.update_session_fields("general", "codex", recovery_attempt_count=0)

        self._command(
            "req-provider-auth-failure",
            "turn.failed",
            {
                "turn_id": retry_assignment["turn_id"],
                "message": "401 Unauthorized: authentication required",
                "diagnostics": {"running": False, "returncode": 1},
            },
            identity,
        )
        failed = self.controller.store.session("general", "codex")

        self.assertEqual(self.recovery_scheduler.pending, [])
        self.assertEqual(failed["runtime_status"], "error")
        self.assertTrue(failed["recovery_required"])

    def test_operator_stop_cancels_scheduled_bridge_recovery(self):
        self._command("req-start-before-cancel", "agent.start", {"agent_id": "codex"})
        _identity, channel = self._connect_bridge()
        self.controller.broker.disconnect(channel)
        self.controller.bridge_process_exited("general", "codex", 11, "crashed")

        self._command("req-stop-recovery", "agent.stop", {"agent_id": "codex"})
        self.recovery_scheduler.run_next()
        stopped = self.controller.store.session("general", "codex")

        self.assertEqual(self.manager.starts, [("general", "codex")])
        self.assertEqual(stopped["runtime_status"], "stopped")
        self.assertFalse(stopped["enabled"])

    def test_failed_automatic_bridge_start_requires_manual_recovery(self):
        self._command("req-start-before-failed-recovery", "agent.start", {"agent_id": "codex"})
        _identity, channel = self._connect_bridge()
        self.controller.broker.disconnect(channel)
        self.controller.bridge_process_exited("general", "codex", 12, "crashed")
        self.manager.start_errors.append(RuntimeError("replacement bridge failed"))

        self.recovery_scheduler.run_next()
        failed = self.controller.store.session("general", "codex")

        self.assertEqual(failed["runtime_status"], "error")
        self.assertTrue(failed["recovery_required"])
        self.assertIn("replacement bridge failed", failed["last_error"])

    def test_agent_create_registers_and_starts_native_cli_on_same_command_path(self):
        created = self._command(
            "req-create-claude",
            "agent.create",
            {
                "provider_id": "claude",
                "display_name": "Claude Review",
                "workspace": str(self.root),
                "model": "haiku",
                "start": True,
            },
        )
        session = RoomStore(self.root).session("general", "claude-claude-review")

        self.assertTrue(created["accepted"])
        self.assertEqual(self.manager.starts[-1], ("general", "claude-claude-review"))
        self.assertEqual(session["provider_kind"], "claude_code")
        self.assertEqual(session["runtime_kind"], "live_cli")
        self.assertEqual(session["connection_kind"], "native_cli_bridge")
        self.assertEqual(session["model"], "haiku")
        self.assertEqual(session["requested_model_id"], "haiku")
        self.assertEqual(session["model_selection_kind"], "alias")
        self.assertEqual(session["model_observation_policy"], "required")
        self.assertEqual(session["model_verification_status"], "pending")
        self.assertEqual(session["catalog_revision"], self.catalog_revision)
        self.assertTrue(session["runtime_profile_key"])
        self.assertNotIn("-p", session["command_configured"])
        self.assertNotIn("--print", session["command_configured"])

    def test_agent_create_rejects_stale_catalog_and_unknown_model(self):
        with self.assertRaises(RoomCommandRejected) as stale:
            self._command(
                "req-create-stale-catalog",
                "agent.create",
                {
                    "provider_id": "codex",
                    "catalog_revision": "cat-stale",
                    "display_name": "Codex Stale",
                    "workspace": str(self.root),
                    "model": "gpt-5.6-luna",
                },
            )
        self.assertEqual(stale.exception.code, "catalog_changed")

        with self.assertRaises(RoomCommandRejected) as unknown:
            self._command(
                "req-create-unknown-model",
                "agent.create",
                {
                    "provider_id": "codex",
                    "display_name": "Codex Unknown",
                    "workspace": str(self.root),
                    "model": "not-a-real-model",
                },
            )
        self.assertEqual(unknown.exception.code, "unsupported_model")
        self.assertFalse(RoomStore(self.root).session("general", "codex-codex-unknown"))

    def test_agent_create_never_upserts_an_existing_stopped_session(self):
        self.controller.create_provider_session(
            "general",
            _spec("existing"),
        )
        store = RoomStore(self.root)
        before = store.session("general", "existing")

        with self.assertRaises(RoomCommandRejected) as raised:
            self._command(
                "req-create-existing",
                "agent.create",
                {
                    "provider_id": "codex",
                    "agent_id": "existing",
                    "display_name": "Existing",
                    "workspace": str(self.root / "changed"),
                    "model": "gpt-5.3-codex",
                },
            )

        self.assertEqual(raised.exception.code, "session_exists")
        self.assertEqual(store.session("general", "existing"), before)

    def test_restart_restores_dynamic_server_owned_provider_for_ui_resume(self):
        created = self._command(
            "req-create-opencode",
            "agent.create",
            {
                "provider_id": "opencode",
                "display_name": "OpenCode",
                "workspace": str(self.root),
                "model": "opencode-go/glm-5.2",
            },
        )
        agent_id = created["result"]["agent_session"]["session_id"]
        self.controller.close()
        restarted_manager = FakeBridgeManager()
        self.controller = RoomRealtimeController(
            self.root,
            **self.room_access.controller_kwargs(),
            providers=[_spec()],
            bridge_manager=restarted_manager,
            recovery_scheduler=self.recovery_scheduler,
        )

        resumed = self._command("req-resume-opencode", "agent.resume", {"agent_id": agent_id})
        bridge_identity = {
            **_bridge_identity(agent_id),
            "provider_kind": "opencode_server",
        }
        restarted_controller_spec = self.controller._provider("general", agent_id)
        channel = self.controller.connect(bridge_identity)

        self.assertTrue(resumed["accepted"])
        self.assertEqual(restarted_manager.starts, [("general", agent_id)])
        self.assertEqual(restarted_controller_spec.command, ("opencode",))
        self.assertEqual(self.controller._provider("general", agent_id).command, ("opencode",))
        session = RoomStore(self.root).session("general", agent_id)
        self.assertEqual(session["provider_kind"], "opencode_server")
        self.assertEqual(session["model"], "opencode-go/glm-5.2")
        self.controller.disconnect(channel)

    def test_same_agent_name_in_different_rooms_keeps_separate_runtime_profiles(self):
        first = self._command(
            "req-create-first",
            "agent.create",
            {
                "provider_id": "codex",
                "display_name": "Analyst",
                "workspace": str(self.root / "first"),
                "model": "gpt-5.3-codex-spark",
            },
        )
        second_identity = {**HOST, "meeting_id": "other-room"}
        second = self._command(
            "req-create-second",
            "agent.create",
            {
                "provider_id": "codex",
                "display_name": "Analyst",
                "workspace": str(self.root / "second"),
                "model": "gpt-5.3-codex",
            },
            second_identity,
        )
        first_session = first["result"]["agent_session"]
        second_session = second["result"]["agent_session"]
        first_internal = RoomStore(self.root).session("general", first_session["session_id"])
        second_internal = RoomStore(self.root).session("other-room", second_session["session_id"])

        self.assertEqual(first_session["participant_id"], second_session["participant_id"])
        self.assertNotIn("workspace", first_session)
        self.assertNotEqual(first_internal["workspace"], second_internal["workspace"])
        self.assertNotEqual(first_session["runtime_profile_key"], second_session["runtime_profile_key"])

    def test_running_agent_rejects_profile_change_without_mutating_its_session(self):
        self._command("req-start-profile", "agent.start", {"agent_id": "codex"})
        self._connect_bridge()
        before = RoomStore(self.root).session("general", "codex")

        with self.assertRaisesRegex(RoomCommandRejected, "already exists") as raised:
            self._command(
                "req-change-profile",
                "agent.create",
                {
                    "provider_id": "codex",
                    "agent_id": "codex",
                    "display_name": "Codex",
                    "workspace": str(self.root / "different"),
                    "model": "gpt-5.3-codex",
                    "start": True,
                },
            )
        after = RoomStore(self.root).session("general", "codex")

        self.assertEqual(raised.exception.code, "session_exists")
        self.assertEqual(after["runtime_profile_key"], before["runtime_profile_key"])
        self.assertEqual(after["command_configured"], before["command_configured"])
        self.assertEqual(after["runtime_status"], "idle")

    def test_stopped_agent_can_explicitly_clear_optional_variant(self):
        created = self._command(
            "req-create-opencode-high",
            "agent.create",
            {
                "provider_id": "opencode",
                "agent_id": "opencode-high",
                "display_name": "OpenCode High",
                "workspace": str(self.root),
                "variant": "high",
            },
        )["result"]["agent_session"]
        self.assertEqual(created["variant"], "high")
        self.assertEqual(RoomStore(self.root).participant("general", "opencode-high")["status"], "detached")
        RoomStore(self.root).update_session_fields(
            "general",
            "opencode-high",
            turn_count=4,
            last_seen_event_id="evt-existing-cursor",
            last_seen_seq=17,
        )
        before_seq = max(event["seq"] for event in RoomStore(self.root).read_events("general"))

        configured = self._command(
            "req-configure-opencode-default",
            "agent.configure",
            {
                "agent_id": "opencode-high",
                "catalog_revision": self.catalog_revision,
                "variant": "",
            },
        )["result"]["agent_session"]

        self.assertEqual(configured["variant"], "")
        self.assertEqual(configured["turn_count"], 4)
        self.assertEqual(configured["last_seen_event_id"], "evt-existing-cursor")
        self.assertEqual(configured["last_seen_seq"], 17)
        new_event_types = {
            event["type"]
            for event in RoomStore(self.root).read_events("general", after_seq=before_seq)
        }
        self.assertIn("agent_session_profile_updated", new_event_types)
        self.assertNotIn("agent_session_created", new_event_types)
        self.assertNotIn("participant_joined", new_event_types)

    def test_stopped_agent_collects_backlog_but_never_auto_starts(self):
        self._command("req-message", "message.send", {"content": "@codex remember this"})
        session = RoomStore(self.root).session("general", "codex")

        self.assertEqual(self.manager.starts, [])
        self.assertFalse(session["enabled"])
        self.assertEqual(len(session["pending_event_ids"]), 1)
        self.assertEqual(session["runtime_status"], "stopped")

    def test_explicit_start_and_bridge_ready_assign_backlog_on_same_socket_path(self):
        message = self._command("req-message", "message.send", {"content": "@codex answer this"})
        started = self._command("req-start", "agent.start", {"agent_id": "codex"})
        identity, channel = self._connect_bridge()
        pushed = channel.drain()
        assignment = next(message for message in pushed if message.get("op") == "turn.assign")
        session = RoomStore(self.root).session("general", "codex")

        self.assertTrue(started["accepted"])
        self.assertEqual(self.manager.starts, [("general", "codex")])
        self.assertEqual(assignment["turn_id"], session["active_turn_id"])
        self.assertIn("@codex answer this", assignment["provider_input"])
        self.assertEqual(assignment["input_up_to_seq"], message["result"]["event_seq"])
        self.assertEqual(session["runtime_status"], "busy")
        self.assertEqual(session["reported_provider_pid"], 808)
        self.assertEqual(identity["client_type"], "agent_bridge")

    def test_busy_agent_automatically_receives_next_pending_turn_after_final(self):
        self._command("req-start", "agent.start", {"agent_id": "codex"})
        identity, channel = self._connect_bridge()
        first_message = self._command("req-first", "message.send", {"content": "@codex first"})
        first_assignment = next(message for message in channel.drain() if message.get("op") == "turn.assign")
        second_message = self._command("req-second", "message.send", {"content": "@codex second"})

        self._command(
            "req-final-one",
            "message.final",
            {"turn_id": first_assignment["turn_id"], "content": "first reply"},
            identity,
        )
        second_assignment = next(message for message in channel.drain() if message.get("op") == "turn.assign")

        self.assertNotEqual(second_assignment["turn_id"], first_assignment["turn_id"])
        self.assertIn("@codex second", second_assignment["provider_input"])
        self.assertNotIn("@codex first", second_assignment["provider_input"])
        self.assertEqual(first_assignment["input_up_to_seq"], first_message["result"]["event_seq"])
        self.assertEqual(second_assignment["input_up_to_seq"], second_message["result"]["event_seq"])
        self.assertEqual(RoomStore(self.root).session("general", "codex")["runtime_status"], "busy")

    def test_server_assigned_group_turn_sees_all_public_messages_since_agent_last_turn(self):
        self.controller.create_provider_session("general", _spec("antigravity", default_responder=False))
        self.controller.create_provider_session("general", _spec("claude", default_responder=False))
        bridges = {}
        identities = {}
        for agent_id in ("codex", "antigravity", "claude"):
            self._command(f"start-{agent_id}", "agent.start", {"agent_id": agent_id})
            identities[agent_id], bridges[agent_id] = self._connect_bridge(agent_id)
            bridges[agent_id].drain()

        topic = self._command(
            "group-topic",
            "message.send",
            {
                "content": "세 사람이 공개 방에서 함께 검토할 주제야.",
                "target_agent_id": "codex",
            },
        )["result"]["event"]
        codex_assignment = next(
            message for message in bridges["codex"].drain() if message.get("op") == "turn.assign"
        )
        self.assertNotIn("@", topic["content"])
        self.assertIn(topic["id"], codex_assignment["provider_context_event_ids"])
        codex_final = self._command(
            "group-codex-final",
            "message.final",
            {"turn_id": codex_assignment["turn_id"], "content": "Codex의 공개 의견"},
            identities["codex"],
        )["result"]["event"]

        antigravity_request = self.controller.request_agent_turn(
            "general",
            "antigravity",
            source_event_id=codex_final["id"],
        )
        antigravity_assignment = next(
            message for message in bridges["antigravity"].drain() if message.get("op") == "turn.assign"
        )
        self.assertTrue(antigravity_request["assigned"])
        self.assertIn("세 사람이 공개 방", antigravity_assignment["provider_input"])
        self.assertIn("Codex의 공개 의견", antigravity_assignment["provider_input"])
        antigravity_final = self._command(
            "group-antigravity-final",
            "message.final",
            {"turn_id": antigravity_assignment["turn_id"], "content": "Antigravity의 공개 의견"},
            identities["antigravity"],
        )["result"]["event"]

        self.controller.request_agent_turn("general", "claude", source_event_id=antigravity_final["id"])
        claude_assignment = next(
            message for message in bridges["claude"].drain() if message.get("op") == "turn.assign"
        )
        self.assertIn("세 사람이 공개 방", claude_assignment["provider_input"])
        self.assertIn("Codex의 공개 의견", claude_assignment["provider_input"])
        self.assertIn("Antigravity의 공개 의견", claude_assignment["provider_input"])
        claude_final = self._command(
            "group-claude-final",
            "message.final",
            {"turn_id": claude_assignment["turn_id"], "content": "Claude의 공개 의견"},
            identities["claude"],
        )["result"]["event"]

        self.controller.request_agent_turn("general", "codex", source_event_id=claude_final["id"])
        codex_second = next(
            message for message in bridges["codex"].drain() if message.get("op") == "turn.assign"
        )
        self.assertNotIn("세 사람이 공개 방", codex_second["provider_input"])
        self.assertNotIn("Codex의 공개 의견", codex_second["provider_input"])
        self.assertIn("Antigravity의 공개 의견", codex_second["provider_input"])
        self.assertIn("Claude의 공개 의견", codex_second["provider_input"])
        self.assertEqual(
            codex_second["provider_context_actor_ids"],
            ["antigravity", "claude"],
        )

    def test_new_agent_can_take_first_turn_from_recent_public_room_history(self):
        topic = self._command(
            "new-agent-topic",
            "message.send",
            {"content": "새 참가자도 봐야 하는 최근 공개 대화", "target_agent_id": "codex"},
        )["result"]["event"]
        self.controller.create_provider_session("general", _spec("late-agent", default_responder=False))
        self._command("start-late-agent", "agent.start", {"agent_id": "late-agent"})
        _identity, channel = self._connect_bridge("late-agent")
        channel.drain()

        requested = self.controller.request_agent_turn(
            "general",
            "late-agent",
            source_event_id=topic["id"],
        )
        assignment = next(message for message in channel.drain() if message.get("op") == "turn.assign")

        self.assertTrue(requested["assigned"])
        self.assertIn("새 참가자도 봐야 하는 최근 공개 대화", assignment["provider_input"])
        self.assertIn(topic["id"], assignment["provider_context_event_ids"])

    def test_prompt_budget_defers_unseen_pending_event_to_immediate_next_turn(self):
        self._command("start-deferred", "agent.start", {"agent_id": "codex"})
        identity, channel = self._connect_bridge()
        channel.drain()
        store = RoomStore(self.root)
        store.update_session_fields(
            "general",
            "codex",
            runtime_status="busy",
            bootstrap_done=True,
        )
        first = self._command(
            "pending-first",
            "message.send",
            {"content": "FIRST-PENDING " + ("a" * 4000), "target_agent_id": "codex"},
        )["result"]["event"]
        second = self._command(
            "pending-second",
            "message.send",
            {"content": "SECOND-PENDING " + ("b" * 4000), "target_agent_id": "codex"},
        )["result"]["event"]
        store.update_session_fields("general", "codex", runtime_status="idle")

        bounded_packets = [
            {
                "provider_input": "FIRST-PENDING",
                "events": [first],
                "last_provider_sync_event_id_after": first["id"],
                "last_provider_sync_seq_after": first["seq"],
                "provider_visible_chars": len("FIRST-PENDING"),
                "provider_visible_event_count": 1,
                "input_mode": "delta",
            },
            {
                "provider_input": "SECOND-PENDING",
                "events": [second],
                "last_provider_sync_event_id_after": second["id"],
                "last_provider_sync_seq_after": second["seq"],
                "provider_visible_chars": len("SECOND-PENDING"),
                "provider_visible_event_count": 1,
                "input_mode": "delta",
            },
        ]

        with patch("agentsassemble.room_realtime.build_room_turn_packet", side_effect=bounded_packets):
            self.assertTrue(self.controller._turn_coordinator.assign_pending("general", "codex"))
            first_assignment = next(message for message in channel.drain() if message.get("op") == "turn.assign")
            during_first = store.session("general", "codex")

            self.assertEqual(first_assignment["source_event_id"], first["id"])
            self.assertEqual(during_first["inflight_event_ids"], [first["id"]])
            self.assertEqual(during_first["pending_event_ids"], [second["id"]])

            self._command(
                "finish-first-deferred",
                "message.final",
                {"turn_id": first_assignment["turn_id"], "content": "first reply"},
                identity,
            )
            second_assignment = next(message for message in channel.drain() if message.get("op") == "turn.assign")

        during_second = store.session("general", "codex")
        self.assertEqual(second_assignment["source_event_id"], second["id"])
        self.assertEqual(during_second["inflight_event_ids"], [second["id"]])
        self.assertEqual(during_second["pending_event_ids"], [])
        self.assertEqual(during_second["last_provider_sync_event_id"], first["id"])

    def test_failed_bounded_turn_restores_inflight_and_deferred_pending_events(self):
        self._command("start-failed-deferred", "agent.start", {"agent_id": "codex"})
        identity, channel = self._connect_bridge()
        channel.drain()
        store = RoomStore(self.root)
        store.update_session_fields("general", "codex", runtime_status="busy", bootstrap_done=True)
        first = self._command(
            "failed-pending-first",
            "message.send",
            {"content": "FIRST-FAIL " + ("a" * 4000), "target_agent_id": "codex"},
        )["result"]["event"]
        second = self._command(
            "failed-pending-second",
            "message.send",
            {"content": "SECOND-FAIL " + ("b" * 4000), "target_agent_id": "codex"},
        )["result"]["event"]
        store.update_session_fields("general", "codex", runtime_status="idle")

        bounded_packet = {
            "provider_input": "FIRST-FAIL",
            "events": [first],
            "last_provider_sync_event_id_after": first["id"],
            "last_provider_sync_seq_after": first["seq"],
            "provider_visible_chars": len("FIRST-FAIL"),
            "provider_visible_event_count": 1,
            "input_mode": "delta",
        }

        with patch("agentsassemble.room_realtime.build_room_turn_packet", return_value=bounded_packet):
            self.assertTrue(self.controller._turn_coordinator.assign_pending("general", "codex"))
            assignment = next(message for message in channel.drain() if message.get("op") == "turn.assign")

        self._command(
            "fail-bounded-turn",
            "turn.failed",
            {"turn_id": assignment["turn_id"], "message": "ordinary provider failure"},
            identity,
        )
        failed = store.session("general", "codex")

        self.assertEqual(failed["inflight_event_ids"], [])
        self.assertEqual(failed["pending_event_ids"], [first["id"], second["id"]])

    def test_pending_partition_discards_missing_and_already_synced_events(self):
        self._command("start-stale-pending", "agent.start", {"agent_id": "codex"})
        _identity, channel = self._connect_bridge()
        channel.drain()
        store = RoomStore(self.root)
        store.update_session_fields("general", "codex", runtime_status="busy", bootstrap_done=True)
        synced = self._command(
            "synced-pending",
            "message.send",
            {"content": "already delivered", "target_agent_id": "codex"},
        )["result"]["event"]
        with store.transaction("general") as transaction:
            transaction.advance_attention_state(
                "codex",
                provider_sync_seq=int(synced["seq"]),
            )
            transaction.update_session_fields(
                "codex",
                runtime_status="idle",
                last_provider_sync_event_id=synced["id"],
                last_provider_sync_seq=synced["seq"],
                pending_event_ids=["missing-event", synced["id"]],
            )

        with patch(
            "agentsassemble.room_realtime.build_room_turn_packet",
            return_value={
                "provider_input": "",
                "events": [],
                "last_provider_sync_event_id_after": synced["id"],
                "last_provider_sync_seq_after": synced["seq"],
                "provider_visible_chars": 0,
                "provider_visible_event_count": 0,
                "input_mode": "delta",
            },
        ):
            self.assertFalse(self.controller._turn_coordinator.assign_pending("general", "codex"))

        cleaned = store.session("general", "codex")
        self.assertEqual(cleaned["pending_event_ids"], [])
        self.assertEqual(cleaned["pending_relay_depth"], 0)
        self.assertFalse(any(message.get("op") == "turn.assign" for message in channel.drain()))

    def test_pending_partition_keeps_only_valid_deferred_and_inflight_events(self):
        self._command("start-mixed-pending", "agent.start", {"agent_id": "codex"})
        _identity, channel = self._connect_bridge()
        channel.drain()
        store = RoomStore(self.root)
        store.update_session_fields("general", "codex", runtime_status="busy", bootstrap_done=True)
        synced = self._command(
            "mixed-synced",
            "message.send",
            {"content": "old", "target_agent_id": "codex"},
        )["result"]["event"]
        included = self._command(
            "mixed-included",
            "message.send",
            {"content": "included", "target_agent_id": "codex"},
        )["result"]["event"]
        deferred = self._command(
            "mixed-deferred",
            "message.send",
            {"content": "deferred", "target_agent_id": "codex"},
        )["result"]["event"]
        with store.transaction("general") as transaction:
            transaction.advance_attention_state(
                "codex",
                provider_sync_seq=int(synced["seq"]),
            )
            transaction.update_session_fields(
                "codex",
                runtime_status="idle",
                last_provider_sync_event_id=synced["id"],
                last_provider_sync_seq=synced["seq"],
                pending_event_ids=["missing-event", synced["id"], included["id"], deferred["id"]],
            )

        with patch(
            "agentsassemble.room_realtime.build_room_turn_packet",
            return_value={
                "provider_input": "included",
                "events": [included],
                "last_provider_sync_event_id_after": included["id"],
                "last_provider_sync_seq_after": included["seq"],
                "provider_visible_chars": len("included"),
                "provider_visible_event_count": 1,
                "input_mode": "delta",
            },
        ):
            self.assertTrue(self.controller._turn_coordinator.assign_pending("general", "codex"))

        assignment = next(message for message in channel.drain() if message.get("op") == "turn.assign")
        during_turn = store.session("general", "codex")
        self.assertEqual(assignment["source_event_id"], included["id"])
        self.assertEqual(during_turn["inflight_event_ids"], [included["id"]])
        self.assertEqual(during_turn["pending_event_ids"], [deferred["id"]])

    def test_bridge_delta_and_final_create_only_canonical_turn_events(self):
        self._command("req-start", "agent.start", {"agent_id": "codex"})
        identity, channel = self._connect_bridge()
        self._command("req-prompt", "message.send", {"content": "@codex hello"})
        assignment = next(message for message in channel.drain() if message.get("op") == "turn.assign")

        activity = self._command(
            "req-activity",
            "activity.update",
            {
                "turn_id": assignment["turn_id"],
                "category": "command",
                "status": "running",
                "content": "cat /private/project/.env TOKEN=secret",
            },
            identity,
        )["result"]["event"]

        self._command(
            "req-delta-one",
            "message.delta",
            {"turn_id": assignment["turn_id"], "content": "clean"},
            identity,
        )
        self._command(
            "req-delta-two",
            "message.delta",
            {"turn_id": assignment["turn_id"], "content": " delta"},
            identity,
        )
        self._command(
            "req-final",
            "message.final",
            {"turn_id": assignment["turn_id"], "content": "clean final"},
            identity,
        )
        events = RoomStore(self.root).read_events("general")
        event_types = [event["type"] for event in events]

        self.assertIn("turn_state", event_types)
        self.assertIn("activity_delta", event_types)
        self.assertIn("message_delta", event_types)
        self.assertIn("message_final", event_types)
        self.assertIn("turn_finished", event_types)
        self.assertEqual(
            [event["content"] for event in events if event["type"] == "message_delta"],
            ["clean", " delta"],
        )
        self.assertEqual(activity["content"], "명령 실행 중")
        self.assertNotIn("/private/project", str(activity))
        self.assertNotIn("TOKEN", str(activity))
        self.assertFalse((self.root / "rooms" / "general" / "live_cli_events.jsonl").exists())
        self.assertEqual(RoomStore(self.root).session("general", "codex")["runtime_status"], "idle")

    def test_bridge_turn_phase_rejects_unknown_values_and_regression(self):
        self._command("phase-start", "agent.start", {"agent_id": "codex"})
        identity, channel = self._connect_bridge()
        self._command("phase-prompt", "message.send", {"content": "@codex hello"})
        assignment = next(message for message in channel.drain() if message.get("op") == "turn.assign")
        store = RoomStore(self.root)

        with self.assertRaises(RoomCommandRejected) as unknown:
            self._command(
                "phase-unknown",
                "turn.state",
                {"turn_id": assignment["turn_id"], "phase": "waiting"},
                identity,
            )
        self.assertEqual(unknown.exception.code, "turn_phase_invalid")
        self.assertEqual(store.session("general", "codex")["turn_phase"], "thinking")

        self._command(
            "phase-delta",
            "message.delta",
            {"turn_id": assignment["turn_id"], "content": "hello"},
            identity,
        )
        with self.assertRaises(RoomCommandRejected) as regressed:
            self._command(
                "phase-regression",
                "turn.state",
                {"turn_id": assignment["turn_id"], "phase": "thinking"},
                identity,
            )
        self.assertEqual(regressed.exception.code, "turn_phase_invalid")
        self.assertEqual(store.session("general", "codex")["turn_phase"], "streaming")

    def test_bridge_terminal_report_rejects_a_corrupt_active_phase(self):
        self._command("corrupt-phase-start", "agent.start", {"agent_id": "codex"})
        identity, channel = self._connect_bridge()
        self._command("corrupt-phase-prompt", "message.send", {"content": "@codex hello"})
        assignment = next(message for message in channel.drain() if message.get("op") == "turn.assign")
        store = RoomStore(self.root)
        store.update_session_fields("general", "codex", turn_phase="")
        before = len(store.read_events("general"))

        with self.assertRaises(RoomCommandRejected) as rejected:
            self._command(
                "corrupt-phase-final",
                "message.final",
                {"turn_id": assignment["turn_id"], "content": "must not publish"},
                identity,
            )

        self.assertEqual(rejected.exception.code, "turn_phase_invalid")
        events = store.read_events("general")
        self.assertEqual(len(events), before)
        self.assertFalse(any(event.get("content") == "must not publish" for event in events))

    def test_invalid_bridge_activity_is_rejected_without_an_event(self):
        self._command("req-start-invalid-activity", "agent.start", {"agent_id": "codex"})
        identity, channel = self._connect_bridge()
        self._command("req-prompt-invalid-activity", "message.send", {"content": "@codex hello"})
        assignment = next(message for message in channel.drain() if message.get("op") == "turn.assign")
        before = len(RoomStore(self.root).read_events("general"))

        for request_id, category, status in (
            ("req-invalid-category", "mystery", "running"),
            ("req-invalid-status", "command", "waiting"),
        ):
            with self.subTest(category=category, status=status), self.assertRaises(RoomCommandRejected) as error:
                self._command(
                    request_id,
                    "activity.update",
                    {
                        "turn_id": assignment["turn_id"],
                        "category": category,
                        "status": status,
                    },
                    identity,
                )
            self.assertEqual(error.exception.code, "adapter_activity_invalid")

        events = RoomStore(self.root).read_events("general")
        self.assertEqual(len(events), before)
        self.assertFalse(any(event["type"] == "activity_delta" for event in events))

    def test_canonical_room_messages_preserve_markdown_newlines(self):
        markdown = "| 이름 | 상태 |\n| --- | --- |\n| Codex | 대기 |"

        event = self._command(
            "req-markdown-message",
            "message.send",
            {"content": markdown, "target_agent_id": "codex"},
        )["result"]["event"]

        self.assertEqual(event["content"], markdown)

    def test_runtime_diagnostics_survive_failure_without_exposing_raw_provider_output(self):
        self._command("req-start-diagnostics", "agent.start", {"agent_id": "codex"})
        identity, channel = self._connect_bridge()
        self._command("req-prompt-diagnostics", "message.send", {"content": "@codex fail safely"})
        assignment = next(message for message in channel.drain() if message.get("op") == "turn.assign")

        result = self._command(
            "req-failed-diagnostics",
            "turn.failed",
            {
                "turn_id": assignment["turn_id"],
                "message": "provider turn failed",
                "diagnostics": {
                    "transport": "acp_stdio",
                    "stderr_drained": True,
                    "stderr_byte_count": 70001,
                    "stderr_warning_count": 44,
                    "stderr_tail": "private provider warning",
                    "terminal_tail": "private terminal screen",
                    "provider_session_active": True,
                    "provider_session_reused": True,
                    "message_source": "grok_acp",
                    "message_source_strict": True,
                    "adapter_activity_invalid_count": 2,
                },
            },
            identity,
        )
        stored = RoomStore(self.root).session("general", "codex")
        error_event = result["result"]["event"]
        public_session = result["result"]["agent_session"]

        self.assertEqual(stored["stderr_tail"], "private provider warning")
        self.assertEqual(stored["terminal_tail"], "private terminal screen")
        self.assertEqual(stored["stderr_byte_count"], 70001)
        self.assertTrue(stored["provider_session_reused"])
        self.assertEqual(stored["message_source"], "grok_acp")
        self.assertEqual(stored["adapter_activity_invalid_count"], 2)
        self.assertEqual(public_session["adapter_activity_invalid_count"], 2)
        self.assertNotIn("stderr_tail", error_event["diagnostics"])
        self.assertNotIn("terminal_tail", error_event["diagnostics"])
        self.assertNotIn("stderr_tail", public_session)
        self.assertNotIn("terminal_tail", public_session)

    def test_stop_disables_session_and_later_messages_do_not_restart_it(self):
        self._command("req-start", "agent.start", {"agent_id": "codex"})
        self._connect_bridge()
        self._command("req-stop", "agent.stop", {"agent_id": "codex"})
        self._command("req-after-stop", "message.send", {"content": "@codex stay stopped"})
        session = RoomStore(self.root).session("general", "codex")

        self.assertEqual(self.manager.starts, [("general", "codex")])
        self.assertEqual(self.manager.stops, [("general", "codex")])
        self.assertFalse(session["enabled"])
        self.assertEqual(session["runtime_status"], "stopped")
        self.assertFalse(session["provider_session_active"])
        self.assertEqual(len(session["pending_event_ids"]), 1)

    def test_pause_preserves_process_and_resume_assigns_backlog_to_same_bridge(self):
        self._command("req-start-pause", "agent.start", {"agent_id": "codex"})
        identity, channel = self._connect_bridge()
        channel.drain()

        paused = self._command("req-pause", "agent.pause", {"agent_id": "codex"})["result"]
        paused_session = RoomStore(self.root).session("general", "codex")
        self.assertTrue(paused["process_preserved"])
        self.assertEqual(paused_session["runtime_status"], "paused")
        self.assertFalse(paused_session["enabled"])
        self.assertEqual(paused_session["reported_provider_pid"], 808)
        self.assertEqual(self.manager.starts, [("general", "codex")])
        self.assertEqual(self.manager.stops, [])

        self._command("req-paused-message", "message.send", {"content": "@codex answer after resume"})
        waiting = RoomStore(self.root).session("general", "codex")
        self.assertEqual(waiting["runtime_status"], "paused")
        self.assertEqual(len(waiting["pending_event_ids"]), 1)
        self.assertFalse(any(message.get("op") == "turn.assign" for message in channel.drain()))

        resumed = self._command("req-resume-paused", "agent.resume", {"agent_id": "codex"})["result"]
        assignment = next(message for message in channel.drain() if message.get("op") == "turn.assign")
        resumed_session = RoomStore(self.root).session("general", "codex")
        self.assertTrue(resumed["runtime_reused"])
        self.assertTrue(resumed["process_reused"])
        self.assertEqual(resumed_session["runtime_status"], "busy")
        self.assertEqual(resumed_session["reported_provider_pid"], 808)
        self.assertEqual(assignment["source_event_id"], waiting["pending_event_ids"][0])
        self.assertEqual(self.manager.starts, [("general", "codex")])
        self.assertEqual(self.manager.stops, [])

        self._command(
            "req-resumed-final",
            "message.final",
            {"turn_id": assignment["turn_id"], "content": "resumed"},
            identity,
        )
        self.assertEqual(RoomStore(self.root).session("general", "codex")["runtime_status"], "idle")

    def test_pause_rejects_busy_session_without_interrupting_it(self):
        self._command("req-start-busy-pause", "agent.start", {"agent_id": "codex"})
        _identity, _channel = self._connect_bridge()
        self._command("req-busy-pause-message", "message.send", {"content": "@codex still working"})

        with self.assertRaises(RoomCommandRejected) as error:
            self._command("req-pause-busy", "agent.pause", {"agent_id": "codex"})

        self.assertEqual(error.exception.code, "invalid_state")
        self.assertEqual(RoomStore(self.root).session("general", "codex")["runtime_status"], "busy")

    def test_muted_participant_cannot_send_through_command_path(self):
        guest = {**HOST, "operator": False, "agent_id": "guest", "display_name": "Guest"}
        channel = self.controller.connect(guest)
        self._command("req-mute-guest", "participant.mute", {"participant_id": "guest", "muted": True})

        with self.assertRaises(RoomCommandRejected) as muted_error:
            self._command("req-muted-message", "message.send", {"content": "blocked"}, guest)

        self.assertEqual(muted_error.exception.code, "muted")
        self.controller.disconnect(channel)

    def test_participant_mute_rolls_back_when_ack_recording_fails(self):
        store = RoomStore(self.root)
        before = store.participant("general", "codex")

        with patch.object(RoomCommandUnitOfWork, "record_ack", side_effect=RuntimeError("injected")):
            with self.assertRaisesRegex(RuntimeError, "injected"):
                self._command(
                    "atomic-mute",
                    "participant.mute",
                    {"participant_id": "codex", "muted": True},
                )

        self.assertEqual(store.participant("general", "codex"), before)
        self.assertFalse(is_room_member_muted(self.root, "general", "codex"))
        self.assertFalse(
            any(
                event.get("type") == "participant_muted"
                and event.get("participant_id") == "codex"
                for event in store.read_events("general")
            )
        )

        muted = self._command(
            "atomic-mute",
            "participant.mute",
            {"participant_id": "codex", "muted": True},
        )
        duplicate = self._command(
            "atomic-mute",
            "participant.mute",
            {"participant_id": "codex", "muted": True},
        )
        self.assertTrue(muted["result"]["participant"]["muted"])
        self.assertTrue(duplicate["deduplicated"])
        mute_events = [
            event
            for event in store.read_events("general")
            if event.get("type") == "participant_muted"
            and event.get("participant_id") == "codex"
        ]
        self.assertEqual(len(mute_events), 1)

    def test_participant_mute_retry_repairs_failed_compatibility_sync(self):
        payload = {"participant_id": "codex", "muted": True}
        with patch(
            "agentsassemble.room_realtime.set_room_member_muted",
            side_effect=RuntimeError("identity store unavailable"),
        ):
            with self.assertRaises(RoomCommandRejected) as failed:
                self._command("mute-sync-retry", "participant.mute", payload)

        self.assertEqual(failed.exception.code, "compatibility_sync_failed")
        self.assertTrue(RoomStore(self.root).participant("general", "codex")["muted"])
        self.assertFalse(is_room_member_muted(self.root, "general", "codex"))

        retried = self._command("mute-sync-retry", "participant.mute", payload)
        self.assertTrue(retried["deduplicated"])
        self.assertTrue(is_room_member_muted(self.root, "general", "codex"))
        mute_events = [
            event
            for event in RoomStore(self.root).read_events("general")
            if event.get("type") == "participant_muted"
            and event.get("participant_id") == "codex"
        ]
        self.assertEqual(len(mute_events), 1)

    def test_canonical_unmute_overrides_stale_compatibility_mute(self):
        guest = {**HOST, "operator": False, "agent_id": "stale-mute-guest"}
        channel = self.controller.connect(guest)
        self._command(
            "canonical-unmute",
            "participant.mute",
            {"participant_id": "stale-mute-guest", "muted": False},
        )
        set_room_member_muted(
            self.root,
            meeting_id="general",
            participant_id="stale-mute-guest",
            muted=True,
        )

        sent = self._command(
            "canonical-unmute-message",
            "message.send",
            {"content": "canonical state wins"},
            guest,
        )

        self.assertEqual(sent["result"]["event"]["content"], "canonical state wins")
        self.controller.disconnect(channel)

    def test_muted_agent_does_not_receive_new_turns_until_unmuted(self):
        self._command("req-mute-agent", "participant.mute", {"participant_id": "codex", "muted": True})
        self._command("req-muted-agent-message", "message.send", {"content": "@codex wait"})
        muted_session = RoomStore(self.root).session("general", "codex")

        self.assertEqual(muted_session["pending_event_ids"], [])

        self._command("req-unmute-agent", "participant.mute", {"participant_id": "codex", "muted": False})
        self._command("req-unmuted-agent-message", "message.send", {"content": "@codex answer"})
        unmuted_session = RoomStore(self.root).session("general", "codex")

        self.assertEqual(len(unmuted_session["pending_event_ids"]), 1)

    def test_kicking_agent_stops_it_and_requires_explicit_re_add(self):
        definition = native_cli_provider_definition("codex")
        self.assertIsNotNone(definition)
        self.controller.configure_stopped_provider_profile(
            "general",
            definition.make_selected_spec(
                agent_id="codex",
                display_name="Codex",
                cwd=self.root,
                model="gpt-5.6-luna",
                reasoning_effort="low",
                service_tier="default",
                permission_mode="meeting_read_only",
            ),
        )
        self._command("req-start-before-kick", "agent.start", {"agent_id": "codex"})
        kicked = self._command("req-kick-agent", "participant.kick", {"participant_id": "codex"})

        self.assertEqual(kicked["result"]["participant"]["status"], "kicked")
        self.assertEqual(self.manager.stops, [("general", "codex")])
        with self.assertRaises(RoomCommandRejected) as missing_error:
            self._command("req-start-kicked", "agent.start", {"agent_id": "codex"})
        self.assertEqual(missing_error.exception.code, "not_found")

        readded = self._command("req-readd-kicked", "agent.readd", {"agent_id": "codex"})
        restarted = self._command("req-start-readded", "agent.start", {"agent_id": "codex"})

        self.assertEqual(readded["result"]["status"], "readded")
        self.assertTrue(restarted["accepted"])
        self.assertEqual(RoomStore(self.root).participant("general", "codex")["status"], "detached")

    def test_kick_retry_does_not_stop_agent_twice_after_ack_failure(self):
        self._command("kick-retry-start", "agent.start", {"agent_id": "codex"})

        with patch.object(
            RoomCommandUnitOfWork,
            "record_ack",
            side_effect=RuntimeError("injected kick ack failure"),
        ):
            with self.assertRaisesRegex(RuntimeError, "injected kick ack failure"):
                self._command(
                    "kick-retry-request",
                    "participant.kick",
                    {"participant_id": "codex"},
                )

        prepared = self.controller.store.participant("general", "codex")
        self.assertEqual(prepared["status"], "detached")
        self.assertEqual(prepared["moderation_intent_status"], "effect_applied")
        self.assertEqual(self.manager.stops, [("general", "codex")])
        self.assertEqual(
            [
                event
                for event in self.controller.store.read_events("general")
                if event.get("type") == "participant_kicked"
            ],
            [],
        )
        public = next(
            participant
            for participant in self.controller.snapshot(HOST)["participants"]
            if participant.get("participant_id") == "codex"
        )
        self.assertFalse(any(key.startswith("moderation_intent_") for key in public))

        recovered = self._command(
            "kick-retry-request",
            "participant.kick",
            {"participant_id": "codex"},
        )
        duplicate = self._command(
            "kick-retry-request",
            "participant.kick",
            {"participant_id": "codex"},
        )

        self.assertTrue(recovered["accepted"])
        self.assertTrue(duplicate["deduplicated"])
        self.assertEqual(self.manager.stops, [("general", "codex")])
        self.assertEqual(self.controller.store.participant("general", "codex")["status"], "kicked")
        self.assertEqual(
            len(
                [
                    event
                    for event in self.controller.store.read_events("general")
                    if event.get("type") == "participant_kicked"
                ]
            ),
            1,
        )

    def test_readd_reuses_stored_server_owned_session_profile(self):
        store = RoomStore(self.root)
        definition = native_cli_provider_definition("codex")
        self.assertIsNotNone(definition)
        spec = definition.make_selected_spec(
            agent_id="codex",
            display_name="Codex",
            cwd=self.root,
            model="gpt-5.6-luna",
            reasoning_effort="low",
            service_tier="default",
            permission_mode="meeting_read_only",
        )
        store.update_session_fields(
            "general",
            "codex",
            command_configured=list(spec.command),
            workspace=spec.cwd,
            model=spec.model,
            reasoning_effort=spec.reasoning_effort,
            service_tier=spec.service_tier,
            variant=spec.variant,
            permission_mode=spec.permission_mode,
            runtime_kind=spec.runtime_kind,
            transport=spec.transport,
            runtime_profile_key=spec.runtime_profile_key(),
            provider_session_id="provider-session-1",
            turn_count=7,
        )
        self._command("req-kick-before-readd", "participant.kick", {"participant_id": "codex"})

        result = self._command(
            "req-readd-existing",
            "agent.readd",
            {"agent_id": "codex", "start": True},
        )["result"]
        restored = store.session("general", "codex")

        self.assertEqual(result["status"], "readded")
        self.assertEqual(result["agent_session"]["participant_id"], "codex")
        for private_key in (
            "workspace",
            "command_configured",
            "provider_session_id",
            "bridge_handle_id",
            "resolved_executable",
            "stdout_path",
            "stderr_path",
        ):
            self.assertNotIn(private_key, result["agent_session"])
        self.assertEqual(store.participant("general", "codex")["status"], "detached")
        self.assertEqual(restored["provider_session_id"], "provider-session-1")
        self.assertEqual(restored["turn_count"], 7)
        self.assertEqual(restored["runtime_status"], "starting")
        event_types = [event["type"] for event in store.read_events("general")]
        self.assertEqual(event_types.count("agent_session_reactivated"), 1)
        self.assertEqual(event_types.count("agent_session_created"), 0)

    def test_readd_migrates_the_known_grok_acp_transport_profile_key(self):
        store = RoomStore(self.root)
        definition = native_cli_provider_definition("grok")
        self.assertIsNotNone(definition)
        spec = definition.make_selected_spec(
            agent_id="grok-low",
            display_name="Grok Low",
            cwd=self.root,
            model="grok-4.5",
            reasoning_effort="low",
            permission_mode="meeting_read_only",
        )
        self.controller.create_provider_session("general", spec)
        store.update_session_fields(
            "general",
            spec.agent_id,
            command_configured=list(spec.command),
            workspace=spec.cwd,
            model=spec.model,
            reasoning_effort=spec.reasoning_effort,
            service_tier=spec.service_tier,
            variant=spec.variant,
            permission_mode=spec.permission_mode,
            runtime_kind=spec.runtime_kind,
            transport="pty",
            runtime_profile_key=replace(spec, transport="pty").runtime_profile_key(),
        )
        self._command("req-kick-grok-before-readd", "participant.kick", {"participant_id": spec.agent_id})

        self._command("req-readd-grok", "agent.readd", {"agent_id": spec.agent_id})
        restored = store.session("general", spec.agent_id)

        self.assertEqual(restored["transport"], "acp_stdio")
        self.assertEqual(restored["runtime_profile_key"], spec.runtime_profile_key())

        self.controller.close()
        self.controller = RoomRealtimeController(
            self.root,
            **self.room_access.controller_kwargs(),
            providers=[_spec()],
            bridge_manager=self.manager,
            recovery_scheduler=self.recovery_scheduler,
        )
        restored_again = store.session("general", spec.agent_id)
        self.assertEqual(restored_again["transport"], "acp_stdio")
        self.assertEqual(restored_again["runtime_profile_key"], spec.runtime_profile_key())

    def test_readd_rejects_recovery_states_and_incomplete_profiles(self):
        store = RoomStore(self.root)
        store.update_session_fields(
            "general",
            "codex",
            status="error",
            runtime_status="error",
            enabled=False,
        )
        store.update_participant_fields("general", "codex", status="detached")

        with self.assertRaises(RoomCommandRejected) as recovery:
            self._command("req-readd-error", "agent.readd", {"agent_id": "codex"})
        self.assertEqual(recovery.exception.code, "readd_invalid_state")

        store.update_session_fields(
            "general",
            "codex",
            status="detached",
            runtime_status="stopped",
            enabled=False,
            model="",
        )
        with self.assertRaises(RoomCommandRejected) as incomplete:
            self._command("req-readd-incomplete", "agent.readd", {"agent_id": "codex"})
        self.assertEqual(incomplete.exception.code, "profile_incomplete")

    def test_room_host_cannot_be_kicked(self):
        with self.assertRaises(RoomCommandRejected) as error:
            self._command("req-kick-host", "participant.kick", {"participant_id": "operator-local"})

        self.assertEqual(error.exception.code, "permission_denied")


if __name__ == "__main__":
    unittest.main()
