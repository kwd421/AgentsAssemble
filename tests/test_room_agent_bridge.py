import io
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from agentsassemble.providers.codex_app_server_live import CodexAppServerLiveRuntime
from agentsassemble.providers.grok_acp import GrokAcpRuntime
from agentsassemble.providers.bridge_protocol import BridgeReportTimeout
from agentsassemble.providers.runtime_contracts import (
    AdapterContractError,
    ProviderTurnResult,
)
from agentsassemble.providers.runtime_config import (
    BridgeConfigError,
    CanonicalBridgeLaunchConfig,
    ProviderRuntimeConfig,
    ProviderRuntimeProfile,
)
from agentsassemble.providers.runtime_factory import (
    ProviderRuntimeFactoryError,
    runtime_from_config,
)
from agentsassemble.providers.agent_bridge import RoomAgentBridge
from agentsassemble.providers.room_portal import RoomPortal


class FakeClient:
    def __init__(self):
        self.messages = []
        self.commands = []
        self.command_responses = {}
        self.closed = False
        self._lock = threading.Lock()

    def receive(self):
        with self._lock:
            messages = list(self.messages)
            self.messages.clear()
            return messages

    def command(self, action, payload=None, *, request_id=""):
        with self._lock:
            self.commands.append((action, dict(payload or {}), request_id))
            configured = self.command_responses.get(action, ...)
            if configured is None:
                return request_id
            if configured is ...:
                response = {"op": "ack", "request_id": request_id, "accepted": True}
                if action == "room.observed":
                    response["result"] = {
                        "observed_through_seq": int((payload or {}).get("through_seq") or 0)
                    }
            else:
                response = dict(configured)
            response["request_id"] = request_id
            self.messages.append(response)
        return request_id

    def close(self):
        self.closed = True


class FakeRuntime:
    def __init__(self):
        self.start_count = 0
        self.stop_count = 0
        self.sent = []
        self.interrupted = False
        self.running = False

    def start(self):
        self.start_count += 1
        self.running = True
        return self.health()

    def send(self, text):
        self.sent.append(text)

    def read_output(self, *, timeout_seconds, on_delta=None, on_activity=None):
        del timeout_seconds
        if on_activity:
            on_activity(
                {
                    "category": "command",
                    "status": "running",
                    "content": "cat /private/project/.env TOKEN=secret",
                }
            )
        if on_delta:
            on_delta("clean ")
            on_delta("delta")
        return {
            "outcome": "message",
            "content": "clean final",
            "metadata": {
                "message_source": "fake-transcript",
                "observed_model_id": "gpt-test-observed",
            },
        }
    def interrupt(self):
        self.interrupted = True

    def stop(self, *, timeout_seconds=2.0):
        del timeout_seconds
        self.stop_count += 1
        self.running = False

    def health(self):
        return {
            "pid": 4242,
            "running": self.running,
            "pty": True,
            "transport": "pty",
            "provider_session_active": True,
            "is_one_shot": False,
            "resolved_executable": "/fake/codex",
            "started_at": "2026-01-01T00:00:00+00:00",
        }


class DecliningRuntime(FakeRuntime):
    def read_output(self, *, timeout_seconds, on_delta=None, on_activity=None):
        del timeout_seconds, on_delta, on_activity
        return {"outcome": "decline", "reason_code": "nothing_useful_to_add"}


class StopFailingRuntime(FakeRuntime):
    def stop(self, *, timeout_seconds=2.0):
        del timeout_seconds
        self.stop_count += 1
        raise RuntimeError("provider refused to stop")


class InvalidDecliningRuntime(FakeRuntime):
    def read_output(self, *, timeout_seconds, on_delta=None, on_activity=None):
        del timeout_seconds, on_delta, on_activity
        return {"outcome": "decline"}


class InvalidHealthAfterStartRuntime(FakeRuntime):
    def __init__(self):
        super().__init__()
        self.health_count = 0

    def health(self):
        self.health_count += 1
        if self.health_count == 1:
            return super().health()
        return {"running": True, "provider_session_active": True}


class InvalidActivityRuntime(FakeRuntime):
    def read_output(self, *, timeout_seconds, on_delta=None, on_activity=None):
        del timeout_seconds, on_delta
        if on_activity:
            on_activity({"category": "mystery", "status": "waiting"})
        return {
            "outcome": "message",
            "content": "clean final",
            "metadata": {
                "message_source": "fake-transcript",
                "observed_model_id": "gpt-test-observed",
            },
        }


class RoomPortalRuntime(FakeRuntime):
    def __init__(self, portal, publications):
        super().__init__()
        self.portal = portal
        self.publications = list(publications)
        self.observed_views = []

    def send(self, text):
        super().send(text)
        self.observed_views.append(
            self.portal.acp_read_text("/agentsassemble-room/current.md")
        )
        publication = self.publications.pop(0)
        if publication:
            self.portal.acp_write_text(
                "/agentsassemble-room/outbox.txt",
                publication,
            )

    def read_output(self, *, timeout_seconds, on_delta=None, on_activity=None):
        del timeout_seconds, on_activity
        if on_delta:
            on_delta("private provider output")
        return {
            "outcome": "message",
            "content": "private provider final",
            "metadata": {"observed_model_id": "gpt-test-observed"},
        }


def _launch_config(**overrides):
    values = {
        "room_id": "general",
        "participant_id": "codex",
        "session_id": "codex",
        "provider_kind": "codex_live_session",
        "runtime_kind": "live_cli",
        "command": ["codex"],
        "cwd": ".",
        "model": "gpt-5.6-luna",
        "reasoning_effort": "low",
        "service_tier": "default",
        "variant": "",
        "permission_mode": "meeting_read_only",
        "transport": "pty",
        "quiet_seconds": 4.0,
        "input_mode": "line",
        "submit_newline": "\r",
        "submit_delay_seconds": 0.1,
        "terminal_rows": 40,
        "terminal_columns": 120,
        "startup_quiet_seconds": 1.0,
        "startup_timeout_seconds": 20.0,
        "startup_accept_contains": "",
        "startup_accept_keys": "\r",
        "startup_ready_contains": "",
        "startup_input": "",
        "turn_timeout_seconds": 180.0,
        "runtime_profile_key": "test-profile",
        "runtime_state_dir": ".agentsassemble/test-provider-state",
        "credential_stdin": False,
        "provider_endpoint": "",
        "provider_server_pid": None,
    }
    values.update(overrides)
    return values


def _runtime_config(**overrides):
    return ProviderRuntimeConfig.parse_strict(_launch_config(**overrides))


def _turn_assignment(turn_id: str, provider_input: str, **overrides):
    values = {
        "op": "turn.assign",
        "room_id": "general",
        "participant_id": "codex",
        "session_id": "codex",
        "turn_id": turn_id,
        "provider_input": provider_input,
        "timeout_seconds": 2,
    }
    values.update(overrides)
    return values


def _wait_for(predicate, timeout=2.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("condition was not met")


class RoomAgentBridgeTests(unittest.TestCase):
    def test_idle_room_check_is_event_driven_and_not_a_fast_poll_loop(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            portal = RoomPortal(Path(temp_dir) / "portal", participant_id="codex")
            portal.prepare()
            client = FakeClient()
            runtime = FakeRuntime()
            bridge = RoomAgentBridge(
                client,
                runtime,
                room_id="general",
                participant_id="codex",
                session_id="codex",
                receive_sleep_seconds=0.005,
                room_portal=portal,
            )
            bridge._next_idle_room_check_at = 0
            thread = threading.Thread(target=bridge.run, daemon=True)
            thread.start()

            _wait_for(
                lambda: any(
                    action == "room.check"
                    for action, _, _ in client.commands
                )
            )
            time.sleep(0.1)
            with client._lock:
                room_checks = [
                    item for item in client.commands if item[0] == "room.check"
                ]
                client.messages.append({"op": "agent.control", "action": "stop"})
            thread.join(timeout=2)

        self.assertEqual(len(room_checks), 1)

    def test_autonomous_room_wake_publishes_only_outbox_and_can_stay_silent(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            portal = RoomPortal(Path(temp_dir) / "portal", participant_id="codex")
            portal.prepare()
            client = FakeClient()
            runtime = RoomPortalRuntime(portal, ["public room reply", ""])
            bridge = RoomAgentBridge(
                client,
                runtime,
                room_id="general",
                participant_id="codex",
                session_id="codex",
                receive_sleep_seconds=0.005,
                initial_orientation="private invite orientation",
                room_portal=portal,
                runtime_profile=ProviderRuntimeProfile(
                    provider_kind="codex_live_session",
                    runtime_kind="live_cli",
                    model="gpt-test",
                    reasoning_effort="low",
                    service_tier="default",
                    variant="",
                    permission_mode="meeting_read_only",
                    transport="pty",
                ),
            )
            thread = threading.Thread(target=bridge.run, daemon=True)
            thread.start()
            _wait_for(lambda: any(action == "bridge.ready" for action, _, _ in client.commands))

            for turn_id, event_id, seq, content in (
                ("wake-1", "event-1", 1, "first room message"),
                ("wake-2", "event-2", 2, "second room message"),
            ):
                with client._lock:
                    client.messages.extend(
                        [
                            {
                                "op": "event",
                                "stream": "room_events",
                                "events": [
                                    {
                                        "id": event_id,
                                        "seq": seq,
                                        "type": "message_final",
                                        "participant_id": "host",
                                        "display_name": "Host",
                                        "content": content,
                                    }
                                ],
                            },
                            {
                                "op": "room.wake",
                                "room_id": "general",
                                "participant_id": "codex",
                                "session_id": "codex",
                                "turn_id": turn_id,
                                "source_event_id": event_id,
                                "input_up_to_seq": seq,
                                "attachment_ids": [],
                                "timeout_seconds": 2,
                            },
                        ]
                    )
                expected_action = "message.final" if turn_id == "wake-1" else "turn.decline"
                _wait_for(
                    lambda: len(
                        [
                            item
                            for item in client.commands
                            if item[0] == expected_action
                        ]
                    )
                    >= 1
                )

            with client._lock:
                client.messages.append({"op": "agent.control", "action": "stop"})
            thread.join(timeout=2)

        final = next(payload for action, payload, _ in client.commands if action == "message.final")
        self.assertEqual(final["content"], "public room reply")
        self.assertFalse(any(action == "message.delta" for action, _, _ in client.commands))
        self.assertEqual(
            len([item for item in client.commands if item[0] == "turn.decline"]),
            1,
        )
        self.assertIn("first room message", runtime.observed_views[0])
        self.assertIn("second room message", runtime.observed_views[1])
        self.assertNotIn("first room message", "\n".join(runtime.sent))
        self.assertNotIn("second room message", "\n".join(runtime.sent))
        self.assertIn("private invite orientation", runtime.sent[0])
        self.assertNotIn("private invite orientation", runtime.sent[1])
        self.assertTrue(runtime.sent[0].endswith("room.wake wake-1"))
        self.assertEqual(runtime.sent[1], "room.wake wake-2")

    def test_room_wake_hides_events_beyond_its_assigned_sequence(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            portal = RoomPortal(Path(temp_dir) / "portal", participant_id="codex")
            portal.prepare()
            client = FakeClient()
            runtime = RoomPortalRuntime(portal, ["", ""])
            bridge = RoomAgentBridge(
                client,
                runtime,
                room_id="general",
                participant_id="codex",
                session_id="codex",
                receive_sleep_seconds=0.005,
                room_portal=portal,
            )
            portal.ingest_frame(
                {
                    "stream": "room_events",
                    "events": [
                        {
                            "id": "event-1",
                            "seq": 1,
                            "type": "message_final",
                            "participant_id": "host",
                            "display_name": "Host",
                            "content": "first assigned message",
                        },
                        {
                            "id": "event-2",
                            "seq": 2,
                            "type": "message_final",
                            "participant_id": "peer",
                            "display_name": "Peer",
                            "content": "later queued message",
                        }
                    ]
                }
            )
            thread = threading.Thread(target=bridge.run, daemon=True)
            thread.start()
            _wait_for(lambda: any(action == "bridge.ready" for action, _, _ in client.commands))

            for turn_id, input_up_to_seq in (("wake-1", 1), ("wake-2", 2)):
                with client._lock:
                    client.messages.append(
                        {
                            "op": "room.wake",
                            "room_id": "general",
                            "participant_id": "codex",
                            "session_id": "codex",
                            "turn_id": turn_id,
                            "input_up_to_seq": input_up_to_seq,
                            "attachment_ids": [],
                            "timeout_seconds": 2,
                        }
                    )
                _wait_for(
                    lambda: len(
                        [
                            item
                            for item in client.commands
                            if item[0] == "turn.decline"
                        ]
                    )
                    >= input_up_to_seq
                )

            with client._lock:
                client.messages.append({"op": "agent.control", "action": "stop"})
            thread.join(timeout=2)

        self.assertIn("first assigned message", runtime.observed_views[0])
        self.assertNotIn("later queued message", runtime.observed_views[0])
        self.assertIn("later queued message", runtime.observed_views[1])

    def test_grok_acp_config_selects_structured_runtime(self):
        runtime = runtime_from_config(
            _runtime_config(
                participant_id="grok",
                session_id="grok",
                provider_kind="grok_live_session",
                command=["grok", "agent", "stdio"],
                model="grok-4.5",
                transport="acp_stdio",
                runtime_state_dir=".agentsassemble/test-grok-acp",
            )
        )

        self.assertIsInstance(runtime, GrokAcpRuntime)

    def test_codex_config_uses_app_server_room_tools_without_workspace_write(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            portal = RoomPortal(Path(temp_dir) / "portal", participant_id="codex")
            portal.prepare()
            runtime = runtime_from_config(
                _runtime_config(
                    participant_id="codex",
                    session_id="codex",
                    provider_kind="codex_live_session",
                    command=["codex", "--no-alt-screen"],
                    model="gpt-5.6-luna",
                    service_tier="priority",
                    permission_mode="meeting_read_only",
                ),
                room_portal=portal,
            )

        self.assertIsInstance(runtime, CodexAppServerLiveRuntime)
        self.assertEqual(runtime.profile["sandbox"], "read-only")
        self.assertEqual(runtime.profile["service_tier"], "priority")
        self.assertIn('service_tier="priority"', runtime.runtime.command)
        self.assertIsNotNone(runtime.room_tools)

    def test_pty_runtime_preserves_an_intentional_empty_cli_argument(self):
        runtime = runtime_from_config(
            _runtime_config(
                participant_id="claude",
                session_id="claude",
                provider_kind="claude_code",
                command=["claude", "--tools", "", "--safe-mode"],
                model="claude-sonnet-4-6",
            )
        )

        self.assertEqual(runtime.command, ["claude", "--tools", "", "--safe-mode"])

    def test_real_grok_command_does_not_fall_back_to_pty(self):
        with self.assertRaisesRegex(ValueError, "exact grok agent stdio"):
            runtime_from_config(
                _runtime_config(
                    participant_id="grok",
                    session_id="grok",
                    provider_kind="grok_live_session",
                    command=["grok", "--no-alt-screen"],
                    model="grok-4.5",
                    transport="acp_stdio",
                )
            )

    def test_runtime_factory_rejects_unknown_provider_transport_pair(self):
        with self.assertRaises(ProviderRuntimeFactoryError) as rejected:
            runtime_from_config(
                _runtime_config(
                    provider_kind="mystery_provider",
                    model="mystery-model",
                )
            )

        self.assertEqual(rejected.exception.code, "unsupported_provider_transport")

    def test_runtime_factory_rejects_runtime_kind_mismatch(self):
        with self.assertRaises(ProviderRuntimeFactoryError) as rejected:
            runtime_from_config(_runtime_config(runtime_kind="api"))

        self.assertEqual(rejected.exception.code, "provider_runtime_kind_mismatch")

    def test_canonical_bridge_config_rejects_missing_profile_and_transport(self):
        for missing in (
            "model",
            "runtime_kind",
            "transport",
            "cwd",
            "startup_ready_contains",
            "turn_timeout_seconds",
        ):
            with self.subTest(missing=missing):
                values = _launch_config()
                values.pop(missing)
                with self.assertRaises(BridgeConfigError) as raised:
                    CanonicalBridgeLaunchConfig.parse_strict(values)
                self.assertEqual(raised.exception.code, "bridge_config_invalid")

    def test_canonical_bridge_config_preserves_empty_cli_arguments_and_profile_values(self):
        parsed = CanonicalBridgeLaunchConfig.parse_strict(
            _launch_config(
                participant_id="claude",
                session_id="claude",
                provider_kind="claude_code",
                command=["claude", "--tools", "", "--safe-mode"],
                model="claude-sonnet-4-6",
                reasoning_effort="",
                service_tier="",
            )
        )

        self.assertEqual(parsed.runtime.command[2], "")
        self.assertEqual(parsed.runtime.reasoning_effort, "")
        self.assertEqual(parsed.runtime.service_tier, "")

    def test_pty_runtime_preserves_startup_readiness_marker(self):
        runtime = runtime_from_config(
            _runtime_config(
                participant_id="claude",
                session_id="claude",
                provider_kind="claude_code",
                command=["claude", "--safe-mode"],
                model="claude-sonnet-4-6",
                startup_ready_contains="plan mode on",
            )
        )

        self.assertEqual(runtime.startup_ready_contains, "plan mode on")

    def test_persistent_runtime_handles_multiple_turns_without_restart(self):
        client = FakeClient()
        runtime = FakeRuntime()
        bridge = RoomAgentBridge(
            client,
            runtime,
            room_id="general",
            participant_id="codex",
            session_id="codex",
            receive_sleep_seconds=0.005,
        )
        thread = threading.Thread(target=bridge.run, daemon=True)
        thread.start()
        _wait_for(lambda: any(action == "bridge.ready" for action, _, _ in client.commands))

        client.messages.append(_turn_assignment("turn-1", "first prompt"))
        _wait_for(lambda: len([item for item in client.commands if item[0] == "message.final"]) == 1)
        client.messages.append(_turn_assignment("turn-2", "second prompt"))
        _wait_for(lambda: len([item for item in client.commands if item[0] == "message.final"]) == 2)
        client.messages.append({"op": "agent.control", "action": "stop"})
        thread.join(timeout=2)

        self.assertFalse(thread.is_alive())
        self.assertTrue(bridge.remote_stop_requested)
        self.assertEqual(runtime.start_count, 1)
        self.assertEqual(runtime.sent, ["first prompt", "second prompt"])
        self.assertEqual(runtime.stop_count, 1)
        deltas = [payload["content"] for action, payload, _ in client.commands if action == "message.delta"]
        self.assertEqual(deltas, ["clean ", "delta", "clean ", "delta"])
        finals = [payload for action, payload, _ in client.commands if action == "message.final"]
        self.assertEqual([payload["content"] for payload in finals], ["clean final", "clean final"])
        self.assertTrue(all(payload["message_source"] == "fake-transcript" for payload in finals))
        self.assertTrue(all(payload["observed_model_id"] == "gpt-test-observed" for payload in finals))
        activities = [payload for action, payload, _ in client.commands if action == "activity.update"]
        self.assertEqual(
            activities,
            [
                {
                    "turn_id": "turn-1",
                    "activity_kind": "tool",
                    "category": "command",
                    "status": "running",
                    "content": "cat [local path] [redacted]",
                },
                {
                    "turn_id": "turn-2",
                    "activity_kind": "tool",
                    "category": "command",
                    "status": "running",
                    "content": "cat [local path] [redacted]",
                },
            ],
        )
        self.assertNotIn("/private/project", str(activities))
        self.assertNotIn("TOKEN", str(activities))

    def test_room_event_batches_advance_observation_without_invoking_provider(self):
        client = FakeClient()
        runtime = FakeRuntime()
        bridge = RoomAgentBridge(
            client,
            runtime,
            room_id="general",
            participant_id="codex",
            session_id="codex",
            receive_sleep_seconds=0.005,
        )
        thread = threading.Thread(target=bridge.run, daemon=True)
        thread.start()
        _wait_for(lambda: any(action == "bridge.ready" for action, _, _ in client.commands))

        with client._lock:
            client.messages.extend(
                [
                    {
                        "op": "event",
                        "stream": "room_events",
                        "events": [{"id": "event-4", "seq": 4, "type": "message_final"}],
                    },
                    {
                        "op": "event",
                        "stream": "room_events",
                        "events": [{"id": "event-7", "seq": 7, "type": "agent_session_state"}],
                    },
                ]
            )
        _wait_for(lambda: any(action == "room.observed" for action, _, _ in client.commands))
        client.messages.append({"op": "agent.control", "action": "stop"})
        thread.join(timeout=2)

        observations = [payload for action, payload, _ in client.commands if action == "room.observed"]
        self.assertEqual(observations, [{"through_seq": 7}])
        self.assertEqual(runtime.sent, [])

    def test_observed_cursor_advances_only_after_correlated_ack(self):
        client = FakeClient()
        client.command_responses["room.observed"] = None
        bridge = RoomAgentBridge(
            client,
            FakeRuntime(),
            room_id="general",
            participant_id="codex",
            session_id="codex",
            receive_sleep_seconds=0.005,
            observed_checkpoint_interval_seconds=0.05,
        )
        thread = threading.Thread(target=bridge.run, daemon=True)
        thread.start()
        _wait_for(lambda: any(action == "bridge.ready" for action, _, _ in client.commands))
        with client._lock:
            client.messages.append(
                {
                    "op": "event",
                    "stream": "room_events",
                    "events": [{"id": "event-4", "seq": 4, "type": "message_final"}],
                }
            )
        _wait_for(lambda: any(action == "room.observed" for action, _, _ in client.commands))
        observation = next(
            command for command in client.commands if command[0] == "room.observed"
        )
        self.assertEqual(bridge._last_observed_seq_reported, 0)

        with client._lock:
            client.messages.append(
                {
                    "op": "ack",
                    "request_id": observation[2],
                    "accepted": True,
                    "result": {"observed_through_seq": 4},
                }
            )
        _wait_for(lambda: bridge._last_observed_seq_reported == 4)
        with client._lock:
            client.messages.append({"op": "agent.control", "action": "stop"})
        thread.join(timeout=2)

        self.assertFalse(thread.is_alive())
        self.assertTrue(bridge.last_cleanup_report.ok)

    def test_observation_flushes_at_event_bound_without_waiting_for_timer(self):
        client = FakeClient()
        bridge = RoomAgentBridge(
            client,
            FakeRuntime(),
            room_id="general",
            participant_id="codex",
            session_id="codex",
            receive_sleep_seconds=0.005,
            observed_checkpoint_max_events=2,
            observed_checkpoint_interval_seconds=10.0,
        )
        thread = threading.Thread(target=bridge.run, daemon=True)
        thread.start()
        _wait_for(lambda: any(action == "bridge.ready" for action, _, _ in client.commands))
        with client._lock:
            client.messages.extend(
                [
                    {
                        "op": "event",
                        "stream": "room_events",
                        "events": [{"id": "event-1", "seq": 1, "type": "message_final"}],
                    },
                    {
                        "op": "event",
                        "stream": "room_events",
                        "events": [{"id": "event-2", "seq": 2, "type": "message_final"}],
                    },
                ]
            )
        _wait_for(lambda: any(action == "room.observed" for action, _, _ in client.commands))
        with client._lock:
            client.messages.append({"op": "agent.control", "action": "stop"})
        thread.join(timeout=2)

        observations = [payload for action, payload, _ in client.commands if action == "room.observed"]
        self.assertEqual(observations, [{"through_seq": 2}])

    def test_graceful_stop_flushes_pending_observation(self):
        client = FakeClient()
        bridge = RoomAgentBridge(
            client,
            FakeRuntime(),
            room_id="general",
            participant_id="codex",
            session_id="codex",
            receive_sleep_seconds=0.005,
            observed_checkpoint_max_events=20,
            observed_checkpoint_interval_seconds=10.0,
        )
        thread = threading.Thread(target=bridge.run, daemon=True)
        thread.start()
        _wait_for(lambda: any(action == "bridge.ready" for action, _, _ in client.commands))
        with client._lock:
            client.messages.extend(
                [
                    {
                        "op": "event",
                        "stream": "room_events",
                        "events": [{"id": "event-3", "seq": 3, "type": "message_final"}],
                    },
                    {"op": "agent.control", "action": "stop"},
                ]
            )
        thread.join(timeout=2)

        observations = [payload for action, payload, _ in client.commands if action == "room.observed"]
        self.assertFalse(thread.is_alive())
        self.assertEqual(observations, [{"through_seq": 3}])
        self.assertEqual(bridge._last_observed_seq_reported, 3)

    def test_bridge_ready_reports_the_explicit_launch_profile(self):
        client = FakeClient()
        bridge = RoomAgentBridge(
            client,
            FakeRuntime(),
            room_id="general",
            participant_id="codex",
            session_id="codex",
            receive_sleep_seconds=0.005,
            runtime_profile=_runtime_config().profile,
        )
        thread = threading.Thread(target=bridge.run, daemon=True)
        thread.start()
        _wait_for(lambda: any(action == "bridge.ready" for action, _, _ in client.commands))
        client.messages.append({"op": "agent.control", "action": "stop"})
        thread.join(timeout=2)

        ready = next(payload for action, payload, _ in client.commands if action == "bridge.ready")
        self.assertEqual(ready["provider_kind"], "codex_live_session")
        self.assertEqual(ready["runtime_kind"], "live_cli")
        self.assertEqual(ready["model"], "gpt-5.6-luna")
        self.assertEqual(ready["reasoning_effort"], "low")
        self.assertEqual(ready["service_tier"], "default")
        self.assertEqual(ready["permission_mode"], "meeting_read_only")

    def test_confirmed_remote_stop_stops_external_runtime_before_reporting(self):
        client = FakeClient()
        runtime = FakeRuntime()
        bridge = RoomAgentBridge(
            client,
            runtime,
            room_id="general",
            participant_id="codex",
            session_id="codex",
            receive_sleep_seconds=0.005,
            stop_runtime_on_exit=False,
        )
        thread = threading.Thread(target=bridge.run, daemon=True)
        thread.start()
        _wait_for(lambda: any(action == "bridge.ready" for action, _, _ in client.commands))

        client.messages.append(
            {
                "op": "agent.control",
                "action": "stop",
                "control_id": "stop-control-1",
                "require_confirmation": True,
            }
        )
        _wait_for(lambda: any(action == "bridge.stopped" for action, _, _ in client.commands))
        thread.join(timeout=2)

        confirmation = next(
            payload for action, payload, _ in client.commands if action == "bridge.stopped"
        )
        self.assertFalse(thread.is_alive())
        self.assertEqual(runtime.stop_count, 1)
        self.assertEqual(confirmation["control_id"], "stop-control-1")
        self.assertTrue(confirmation["stopped"])

    def test_runtime_stop_failure_returns_nonzero_cleanup_report(self):
        client = FakeClient()
        runtime = StopFailingRuntime()
        bridge = RoomAgentBridge(
            client,
            runtime,
            room_id="general",
            participant_id="codex",
            session_id="codex",
            receive_sleep_seconds=0.005,
        )
        exits: list[int] = []
        stderr = io.StringIO()
        with patch("sys.stderr", stderr):
            thread = threading.Thread(target=lambda: exits.append(bridge.run()), daemon=True)
            thread.start()
            _wait_for(lambda: any(action == "bridge.ready" for action, _, _ in client.commands))
            client.messages.append(
                {
                    "op": "agent.control",
                    "action": "stop",
                    "control_id": "failed-stop-control",
                    "require_confirmation": True,
                }
            )
            thread.join(timeout=2)

        self.assertFalse(thread.is_alive())
        self.assertEqual(exits, [1])
        self.assertFalse(bridge.last_cleanup_report.ok)
        self.assertEqual(bridge.last_cleanup_report.failures[0].stage, "runtime.stop")
        self.assertEqual(bridge.last_cleanup_report.orphaned_handle_ids, ["codex"])
        self.assertIn("runtime.stop", stderr.getvalue())
        confirmation = next(
            payload for action, payload, _ in client.commands if action == "bridge.stopped"
        )
        self.assertEqual(confirmation["control_id"], "failed-stop-control")
        self.assertFalse(confirmation["stopped"])
        self.assertEqual(confirmation["error_code"], "runtime_stop_failed")

    def test_invalid_adapter_activity_is_dropped_and_counted(self):
        client = FakeClient()
        bridge = RoomAgentBridge(
            client,
            InvalidActivityRuntime(),
            room_id="general",
            participant_id="codex",
            session_id="codex",
            receive_sleep_seconds=0.005,
        )
        thread = threading.Thread(target=bridge.run, daemon=True)
        thread.start()
        _wait_for(lambda: any(action == "bridge.ready" for action, _, _ in client.commands))
        client.messages.append(_turn_assignment("turn-invalid-activity", "hello"))
        _wait_for(lambda: any(action == "message.final" for action, _, _ in client.commands))
        client.messages.append({"op": "agent.control", "action": "stop"})
        thread.join(timeout=2)

        self.assertFalse(thread.is_alive())
        self.assertFalse(any(action == "activity.update" for action, _, _ in client.commands))
        final = next(payload for action, payload, _ in client.commands if action == "message.final")
        self.assertEqual(final["diagnostics"]["adapter_activity_invalid_count"], 1)

    def test_interrupt_is_forwarded_without_stopping_runtime(self):
        client = FakeClient()
        runtime = FakeRuntime()
        bridge = RoomAgentBridge(client, runtime, room_id="general", participant_id="codex", session_id="codex")
        thread = threading.Thread(target=bridge.run, daemon=True)
        thread.start()
        _wait_for(lambda: runtime.start_count == 1)
        client.messages.append({"op": "agent.control", "action": "interrupt"})
        _wait_for(lambda: runtime.interrupted)
        client.messages.append({"op": "agent.control", "action": "stop"})
        thread.join(timeout=2)

        self.assertTrue(runtime.interrupted)
        self.assertEqual(runtime.start_count, 1)

    def test_structured_decline_does_not_emit_blank_final(self):
        client = FakeClient()
        runtime = DecliningRuntime()
        bridge = RoomAgentBridge(
            client,
            runtime,
            room_id="general",
            participant_id="codex",
            session_id="codex",
            receive_sleep_seconds=0.005,
        )
        thread = threading.Thread(target=bridge.run, daemon=True)
        thread.start()
        _wait_for(lambda: runtime.start_count == 1)
        client.messages.append(_turn_assignment("turn-decline", "observe"))
        _wait_for(lambda: any(action == "turn.decline" for action, _, _ in client.commands))
        client.messages.append({"op": "agent.control", "action": "stop"})
        thread.join(timeout=2)

        declines = [payload for action, payload, _ in client.commands if action == "turn.decline"]
        self.assertEqual(declines[0]["reason_code"], "nothing_useful_to_add")
        self.assertFalse(any(action == "message.final" for action, _, _ in client.commands))

    def test_decline_without_reason_is_an_adapter_contract_error(self):
        client = FakeClient()
        runtime = InvalidDecliningRuntime()
        bridge = RoomAgentBridge(
            client,
            runtime,
            room_id="general",
            participant_id="codex",
            session_id="codex",
            receive_sleep_seconds=0.005,
        )
        thread = threading.Thread(target=bridge.run, daemon=True)
        thread.start()
        _wait_for(lambda: runtime.start_count == 1)
        client.messages.append(_turn_assignment("turn-invalid-decline", "observe"))
        _wait_for(lambda: any(action == "turn.failed" for action, _, _ in client.commands))
        client.messages.append({"op": "agent.control", "action": "stop"})
        thread.join(timeout=2)

        failure = next(payload for action, payload, _ in client.commands if action == "turn.failed")
        self.assertEqual(failure["error_code"], "adapter_contract_error")
        self.assertIn("reason_code", failure["message"])
        self.assertFalse(any(action == "turn.decline" for action, _, _ in client.commands))

    def test_result_outcome_is_required_at_the_top_level(self):
        with self.assertRaises(AdapterContractError) as raised:
            ProviderTurnResult.parse(
                {
                    "content": "looks valid",
                    "metadata": {"outcome": "message"},
                }
            )

        self.assertEqual(raised.exception.code, "adapter_contract_error")

    def test_invalid_health_does_not_fabricate_final_diagnostics(self):
        client = FakeClient()
        runtime = InvalidHealthAfterStartRuntime()
        bridge = RoomAgentBridge(
            client,
            runtime,
            room_id="general",
            participant_id="codex",
            session_id="codex",
            receive_sleep_seconds=0.005,
        )
        thread = threading.Thread(target=bridge.run, daemon=True)
        thread.start()
        _wait_for(lambda: any(action == "bridge.ready" for action, _, _ in client.commands))
        client.messages.append(_turn_assignment("turn-invalid-health", "respond"))
        _wait_for(lambda: any(action == "turn.failed" for action, _, _ in client.commands))
        client.messages.append({"op": "agent.control", "action": "stop"})
        thread.join(timeout=2)

        failure = next(payload for action, payload, _ in client.commands if action == "turn.failed")
        self.assertEqual(failure["error_code"], "adapter_contract_error")
        self.assertTrue(failure["diagnostics"]["adapter_health_invalid"])
        self.assertNotIn("started_at", failure["diagnostics"])
        self.assertFalse(any(action == "message.final" for action, _, _ in client.commands))

    def test_assignment_without_turn_id_closes_the_bridge_as_a_protocol_error(self):
        client = FakeClient()
        runtime = FakeRuntime()
        bridge = RoomAgentBridge(
            client,
            runtime,
            room_id="general",
            participant_id="codex",
            session_id="codex",
            receive_sleep_seconds=0.005,
        )
        thread = threading.Thread(target=bridge.run, daemon=True)
        thread.start()
        _wait_for(lambda: runtime.start_count == 1)
        invalid = _turn_assignment("unused", "hello")
        invalid.pop("turn_id")
        client.messages.append(invalid)
        _wait_for(lambda: client.closed)
        thread.join(timeout=2)

        self.assertFalse(thread.is_alive())
        self.assertEqual(runtime.sent, [])
        self.assertFalse(any(action == "turn.failed" for action, _, _ in client.commands))

    def test_assignment_without_provider_input_reports_failure_instead_of_disappearing(self):
        client = FakeClient()
        runtime = FakeRuntime()
        bridge = RoomAgentBridge(
            client,
            runtime,
            room_id="general",
            participant_id="codex",
            session_id="codex",
            receive_sleep_seconds=0.005,
        )
        thread = threading.Thread(target=bridge.run, daemon=True)
        thread.start()
        _wait_for(lambda: runtime.start_count == 1)
        client.messages.append(_turn_assignment("turn-invalid", ""))
        _wait_for(lambda: any(action == "turn.failed" for action, _, _ in client.commands))
        client.messages.append({"op": "agent.control", "action": "stop"})
        thread.join(timeout=2)

        failure = next(payload for action, payload, _ in client.commands if action == "turn.failed")
        self.assertEqual(failure["error_code"], "assignment_invalid")
        self.assertEqual(runtime.sent, [])

    def test_terminal_report_nack_stops_without_a_duplicate_failure_report(self):
        client = FakeClient()
        client.command_responses["message.final"] = {
            "op": "nack",
            "accepted": False,
            "error": {"code": "provider_model_mismatch", "message": "wrong model"},
        }
        runtime = FakeRuntime()
        bridge = RoomAgentBridge(
            client,
            runtime,
            room_id="general",
            participant_id="codex",
            session_id="codex",
            receive_sleep_seconds=0.005,
        )
        thread = threading.Thread(target=bridge.run, daemon=True)
        thread.start()
        _wait_for(lambda: runtime.start_count == 1)
        client.messages.append(_turn_assignment("turn-nack", "respond"))
        _wait_for(lambda: bridge._stop.is_set())
        thread.join(timeout=2)

        self.assertFalse(thread.is_alive())
        self.assertEqual(
            [action for action, _, _ in client.commands if action == "message.final"],
            ["message.final"],
        )
        self.assertFalse(any(action == "turn.failed" for action, _, _ in client.commands))

    def test_bridge_ready_requires_a_correlated_ack(self):
        client = FakeClient()
        client.command_responses["bridge.ready"] = None
        runtime = FakeRuntime()
        bridge = RoomAgentBridge(
            client,
            runtime,
            room_id="general",
            participant_id="codex",
            session_id="codex",
            receive_sleep_seconds=0.005,
            report_timeout_seconds=0.02,
        )

        with self.assertRaises(BridgeReportTimeout) as raised:
            bridge.run()

        self.assertEqual(raised.exception.code, "bridge_report_timeout")
        self.assertEqual(runtime.stop_count, 1)


if __name__ == "__main__":
    unittest.main()
