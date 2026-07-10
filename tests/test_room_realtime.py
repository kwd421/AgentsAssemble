import tempfile
import unittest
from pathlib import Path

from agentsassemble.room_realtime import (
    NativeCliProviderSpec,
    RoomCommandRejected,
    RoomEventBroker,
    RoomRealtimeController,
    default_native_cli_provider_specs,
    validate_native_cli_provider_spec,
)
from agentsassemble.room_store import RoomStore


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
        self.stops: list[tuple[str, str]] = []
        self.start_errors = []

    def start(self, room_id, session, spec, *, server_url="", ticket_issuer=None):
        del server_url, ticket_issuer
        if self.start_errors:
            raise self.start_errors.pop(0)
        self.starts.append((room_id, str(session["session_id"])))
        return {"bridge_pid": 701, "resolved_executable": f"/fake/{spec.command[0]}"}

    def stop(self, room_id, session_id, *, timeout_seconds=2.0, provider_pid=None):
        del timeout_seconds, provider_pid
        self.stops.append((room_id, session_id))
        return {"stopped": True, "alive": False}

    def close(self):
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
    def test_default_specs_include_interactive_claude_haiku_without_print_mode(self):
        specs = {spec.agent_id: spec for spec in default_native_cli_provider_specs()}

        self.assertIn("claude", specs)
        self.assertEqual(specs["claude"].model, "haiku")
        self.assertEqual(specs["claude"].provider_kind, "claude_code")
        self.assertIn("--model", specs["claude"].command)
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
        self.controller = RoomRealtimeController(
            self.root,
            providers=[_spec()],
            bridge_manager=self.manager,
            recovery_scheduler=self.recovery_scheduler,
        )

    def tearDown(self):
        self.controller.close()
        self.temp.cleanup()

    def _command(self, request_id, action, payload=None, identity=None):
        return self.controller.handle_command(
            identity or HOST,
            {"op": "command", "request_id": request_id, "action": action, "payload": payload or {}},
        )

    def _connect_bridge(self, agent_id="codex"):
        identity = _bridge_identity(agent_id)
        channel = self.controller.connect(identity)
        channel.subscribe({"room_events"})
        self.ready_count += 1
        self._command(
            f"ready-{agent_id}-{self.ready_count}",
            "bridge.ready",
            {"pid": 808, "pty": True, "transport": "pty", "is_one_shot": False},
            identity,
        )
        return identity, channel

    def test_message_command_uses_server_identity_and_is_deduplicated(self):
        first = self._command("req-message", "message.send", {"content": "@codex hello"})
        duplicate = self._command("req-message", "message.send", {"content": "different"})
        messages = [event for event in RoomStore(self.root).read_events("general") if event["type"] == "message_final"]

        self.assertTrue(first["accepted"])
        self.assertTrue(duplicate["deduplicated"])
        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0]["actor"]["participant_id"], "operator-local")
        self.assertEqual(messages[0]["content"], "@codex hello")

    def test_read_only_browser_cannot_send_or_control_agents(self):
        read_only = {**HOST, "operator": False, "invite_scope": "read_only", "agent_id": "guest"}
        with self.assertRaises(RoomCommandRejected) as send_error:
            self._command("req-ro-send", "message.send", {"content": "no"}, read_only)
        with self.assertRaises(RoomCommandRejected) as control_error:
            self._command("req-ro-start", "agent.start", {"agent_id": "codex"}, read_only)

        self.assertEqual(send_error.exception.code, "permission_denied")
        self.assertEqual(control_error.exception.code, "permission_denied")

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
            ["codex", "antigravity", "grok", "claude"],
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
        self.assertIn(first_assignment["source_event_id"], recovering["pending_event_ids"])
        self.assertIn("fatal provider stderr", recovering["stderr_tail"])

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
        self.assertTrue(session["runtime_profile_key"])
        self.assertNotIn("-p", session["command_configured"])
        self.assertNotIn("--print", session["command_configured"])

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

        self.assertEqual(first_session["participant_id"], second_session["participant_id"])
        self.assertNotEqual(first_session["workspace"], second_session["workspace"])
        self.assertNotEqual(first_session["runtime_profile_key"], second_session["runtime_profile_key"])

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
        self.assertEqual(session["pid"], 808)
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

    def test_bridge_delta_and_final_create_only_canonical_turn_events(self):
        self._command("req-start", "agent.start", {"agent_id": "codex"})
        identity, channel = self._connect_bridge()
        self._command("req-prompt", "message.send", {"content": "@codex hello"})
        assignment = next(message for message in channel.drain() if message.get("op") == "turn.assign")

        self._command(
            "req-delta",
            "message.delta",
            {"turn_id": assignment["turn_id"], "content": "clean delta"},
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
        self.assertIn("message_delta", event_types)
        self.assertIn("message_final", event_types)
        self.assertIn("turn_finished", event_types)
        self.assertFalse((self.root / "rooms" / "general" / "live_cli_events.jsonl").exists())
        self.assertEqual(RoomStore(self.root).session("general", "codex")["runtime_status"], "idle")

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
        self.assertEqual(len(session["pending_event_ids"]), 1)

    def test_muted_participant_cannot_send_through_command_path(self):
        guest = {**HOST, "operator": False, "agent_id": "guest", "display_name": "Guest"}
        channel = self.controller.connect(guest)
        self._command("req-mute-guest", "participant.mute", {"participant_id": "guest", "muted": True})

        with self.assertRaises(RoomCommandRejected) as muted_error:
            self._command("req-muted-message", "message.send", {"content": "blocked"}, guest)

        self.assertEqual(muted_error.exception.code, "muted")
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
        self._command("req-start-before-kick", "agent.start", {"agent_id": "codex"})
        kicked = self._command("req-kick-agent", "participant.kick", {"participant_id": "codex"})

        self.assertEqual(kicked["result"]["participant"]["status"], "kicked")
        self.assertEqual(self.manager.stops, [("general", "codex")])
        with self.assertRaises(RoomCommandRejected) as missing_error:
            self._command("req-start-kicked", "agent.start", {"agent_id": "codex"})
        self.assertEqual(missing_error.exception.code, "not_found")

        self.controller.register_provider("general", _spec())
        restarted = self._command("req-start-readded", "agent.start", {"agent_id": "codex"})

        self.assertTrue(restarted["accepted"])
        self.assertEqual(RoomStore(self.root).participant("general", "codex")["status"], "detached")

    def test_room_host_cannot_be_kicked(self):
        with self.assertRaises(RoomCommandRejected) as error:
            self._command("req-kick-host", "participant.kick", {"participant_id": "operator-local"})

        self.assertEqual(error.exception.code, "permission_denied")


if __name__ == "__main__":
    unittest.main()
