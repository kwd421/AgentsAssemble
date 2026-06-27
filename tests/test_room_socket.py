import tempfile
import time
import unittest
from pathlib import Path

from agentsassemble.live_cli_control import (
    DEFAULT_LIVE_CLI_PROVIDER_SPECS,
    GeneralRoomController,
    LiveCliProviderSpec,
)
from agentsassemble.room_socket import GeneralRoomSocketHub


class FakeRuntime:
    def __init__(self, agent_id: str, *, delay_seconds: float = 0.0) -> None:
        self.agent_id = agent_id
        self.delay_seconds = delay_seconds
        self.last_seen_event_id = ""
        self.started = False
        self.pid = abs(hash(agent_id)) % 100000 + 1000
        self.delivered: list[list[dict[str, object]]] = []
        self.interrupted = False

    def start(self) -> dict[str, object]:
        self.started = True
        return self.health()

    def deliver(self, events: list[dict[str, object]]) -> None:
        self.delivered.append(list(events))
        if events:
            self.last_seen_event_id = str(events[-1].get("event_id") or "")

    def read_output(self, *, timeout_seconds: float, on_delta=None) -> dict[str, object]:
        del timeout_seconds
        if self.delay_seconds:
            time.sleep(self.delay_seconds)
        if on_delta is not None:
            on_delta(f"{self.agent_id} streaming")
        return {
            "actor_id": self.agent_id,
            "actor_type": "agent",
            "kind": "agent_message",
            "content": f"{self.agent_id} reply",
        }

    def interrupt(self) -> None:
        self.interrupted = True

    def stop(self, *, timeout_seconds: float = 2.0) -> None:
        del timeout_seconds
        self.started = False

    def health(self) -> dict[str, object]:
        return {
            "agent_id": self.agent_id,
            "running": self.started,
            "stopped": not self.started,
            "pid": self.pid if self.started else None,
            "last_seen_event_id": self.last_seen_event_id,
        }


def _controller(root: Path, runtimes: dict[str, FakeRuntime]) -> GeneralRoomController:
    specs = [
        LiveCliProviderSpec(agent_id=agent_id, display_name=agent_id.title(), command=[agent_id])
        for agent_id in runtimes
    ]
    return GeneralRoomController(
        root,
        providers=specs,
        runtime_factory=lambda spec: runtimes[spec.agent_id],
        read_timeout_seconds=2,
    )


def _wait_for_message(sent: list[dict[str, object]], message_type: str) -> dict[str, object]:
    deadline = time.time() + 3
    while time.time() < deadline:
        for message in sent:
            if message.get("type") == message_type:
                return message
        time.sleep(0.01)
    raise AssertionError(f"no {message_type} message received: {sent!r}")


class GeneralRoomSocketHubTests(unittest.TestCase):
    def test_default_live_cli_provider_specs_use_real_tui_commands(self):
        by_id = {spec.agent_id: spec for spec in DEFAULT_LIVE_CLI_PROVIDER_SPECS}

        self.assertEqual(by_id["codex"].command[:2], ["codex", "--no-alt-screen"])
        self.assertEqual(by_id["codex"].input_mode, "bracketed_paste")
        self.assertEqual(by_id["antigravity"].command[0], "agy")
        self.assertEqual(by_id["antigravity"].input_mode, "bracketed_paste")
        self.assertEqual(by_id["grok"].command[:2], ["grok", "--no-alt-screen"])

    def test_hello_sends_snapshot_with_backfill_after_cursor(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            controller = _controller(root, {"codex": FakeRuntime("codex")})
            first = controller.room.append_user_message("human", "first")
            controller.room.append_user_message("human", "second")
            sent: list[dict[str, object]] = []
            hub = GeneralRoomSocketHub(controller)

            connection = hub.connect(sent.append)
            hub.handle_message(
                connection,
                {"type": "hello", "client_id": "browser-1", "after_event_id": first["event_id"]},
            )

        self.assertEqual(sent[-1]["type"], "snapshot")
        self.assertEqual([event["content"] for event in sent[-1]["events"]], ["second"])
        self.assertEqual(sent[-1]["agents"][0]["agent_id"], "codex")
        self.assertIn("codex", sent[-1]["latency"])

    def test_user_message_broadcasts_room_events_agent_delta_final_and_latency(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            runtimes = {"codex": FakeRuntime("codex"), "grok": FakeRuntime("grok")}
            controller = _controller(root, runtimes)
            sent: list[dict[str, object]] = []
            hub = GeneralRoomSocketHub(controller)
            connection = hub.connect(sent.append)

            hub.handle_message(connection, {"type": "hello"})
            hub.handle_message(connection, {"type": "user_message", "content": "@codex hello"})
            controller.wait_for_idle(timeout_seconds=3)

        self.assertEqual(len(runtimes["codex"].delivered), 1)
        self.assertEqual(runtimes["grok"].delivered, [])
        self.assertEqual(_wait_for_message(sent, "room_event")["event"]["kind"], "user_message")
        self.assertEqual(_wait_for_message(sent, "agent_delta")["agent_id"], "codex")
        self.assertEqual(_wait_for_message(sent, "agent_message")["event"]["actor_id"], "codex")
        self.assertEqual(_wait_for_message(sent, "latency")["agent_id"], "codex")
        self.assertIn("busy", [message.get("agent", {}).get("status") for message in sent])
        self.assertIn("idle", [message.get("agent", {}).get("status") for message in sent])

    def test_agent_control_start_stop_resume_interrupt_pushes_state(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            runtime = FakeRuntime("codex")
            controller = _controller(root, {"codex": runtime})
            sent: list[dict[str, object]] = []
            hub = GeneralRoomSocketHub(controller)
            connection = hub.connect(sent.append)

            hub.handle_message(connection, {"type": "agent_control", "agent_id": "codex", "action": "start"})
            hub.handle_message(connection, {"type": "agent_control", "agent_id": "codex", "action": "interrupt"})
            hub.handle_message(connection, {"type": "agent_control", "agent_id": "codex", "action": "stop"})
            hub.handle_message(connection, {"type": "agent_control", "agent_id": "codex", "action": "resume"})

        states = [
            message["agent"]
            for message in sent
            if message.get("type") == "agent_state" and message.get("agent", {}).get("agent_id") == "codex"
        ]
        self.assertTrue(runtime.interrupted)
        self.assertEqual(states[0]["status"], "idle")
        self.assertEqual(states[0]["pid"], runtime.pid)
        self.assertEqual(states[2]["status"], "stopped")
        self.assertFalse(states[-1]["resumed_process"])

    def test_bad_client_message_gets_recoverable_error(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            controller = _controller(root, {"codex": FakeRuntime("codex")})
            sent: list[dict[str, object]] = []
            hub = GeneralRoomSocketHub(controller)
            connection = hub.connect(sent.append)

            hub.handle_message(connection, {"type": "agent_control", "agent_id": "missing", "action": "start"})

        self.assertEqual(sent[-1]["type"], "error")
        self.assertTrue(sent[-1]["recoverable"])
