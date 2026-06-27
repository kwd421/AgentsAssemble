import json
import sys
import tempfile
import time
import unittest
from pathlib import Path

from agentsassemble.live_cli import (
    AgentRuntimeBinding,
    ApiRuntime,
    GeneralRoomEventStore,
    LiveCliRuntime,
    RoomScheduler,
    live_cli_supported,
)


def _fake_cli_script(*, delay_seconds: float = 0.0) -> str:
    return "\n".join(
        [
            "import sys, time",
            "count = 0",
            "memory = []",
            "for line in sys.stdin:",
            "    text = line.strip()",
            "    if not text:",
            "        continue",
            "    count += 1",
            "    memory.append(text)",
            f"    time.sleep({delay_seconds!r})",
            "    print(f'reply {count}: {text} | remembered {len(memory)}', flush=True)",
        ]
    )


class LiveCliRuntimeTests(unittest.TestCase):
    @unittest.skipUnless(live_cli_supported(), "requires POSIX PTY support")
    def test_live_cli_runtime_keeps_one_process_state_across_deliveries(self):
        runtime = LiveCliRuntime("alpha", [sys.executable, "-u", "-c", _fake_cli_script()], idle_quiet_seconds=0.05)
        try:
            runtime.start()
            first_pid = runtime.health()["pid"]
            first_event = {"event_id": "evt-1", "actor_id": "human", "actor_type": "user", "kind": "user_message", "content": "first"}
            runtime.deliver([first_event])
            first = runtime.read_output(timeout_seconds=2)
            second_event = {"event_id": "evt-2", "actor_id": "human", "actor_type": "user", "kind": "user_message", "content": "second"}
            runtime.deliver([second_event])
            second = runtime.read_output(timeout_seconds=2)
            second_pid = runtime.health()["pid"]
        finally:
            runtime.stop()

        self.assertEqual(first_pid, second_pid)
        self.assertIn("reply 1: #general human: first | remembered 1", first["content"])
        self.assertIn("reply 2: #general human: second | remembered 2", second["content"])
        self.assertEqual(runtime.last_seen_event_id, "evt-2")

    @unittest.skipUnless(live_cli_supported(), "requires POSIX PTY support")
    def test_live_cli_runtime_can_send_bracketed_paste_to_tui(self):
        runtime = LiveCliRuntime(
            "alpha",
            [sys.executable, "-u", "-c", _fake_cli_script()],
            idle_quiet_seconds=0.05,
            input_mode="bracketed_paste",
            submit_newline="\r",
            terminal_columns=100,
            terminal_rows=30,
        )
        try:
            runtime.start()
            runtime.deliver(
                [
                    {
                        "event_id": "evt-1",
                        "actor_id": "human",
                        "actor_type": "user",
                        "kind": "user_message",
                        "content": "first",
                    }
                ]
            )
            first = runtime.read_output(timeout_seconds=2)
            health = runtime.health()
        finally:
            runtime.stop()

        self.assertIn("reply 1:", first["content"])
        self.assertIn("#general human: first", first["content"])
        self.assertEqual(health["input_mode"], "bracketed_paste")
        self.assertEqual(health["terminal_columns"], 100)
        self.assertEqual(health["terminal_rows"], 30)

    @unittest.skipUnless(live_cli_supported(), "requires POSIX PTY support")
    def test_live_cli_restart_replaces_process_after_stop(self):
        runtime = LiveCliRuntime("alpha", [sys.executable, "-u", "-c", _fake_cli_script()], idle_quiet_seconds=0.05)
        try:
            runtime.start()
            first_pid = runtime.health()["pid"]
            runtime.restart()
            second_pid = runtime.health()["pid"]
        finally:
            runtime.stop()

        self.assertNotEqual(first_pid, second_pid)
        self.assertTrue(runtime.health()["stopped"])

    @unittest.skipUnless(live_cli_supported(), "requires POSIX PTY support")
    def test_stop_does_not_wait_for_blocked_read_timeout(self):
        import threading

        script = "\n".join(
            [
                "import sys, time",
                "for line in sys.stdin:",
                "    time.sleep(5)",
            ]
        )
        runtime = LiveCliRuntime("alpha", [sys.executable, "-u", "-c", script], idle_quiet_seconds=0.05)
        runtime.start()
        runtime.deliver([{"event_id": "evt-1", "actor_id": "human", "actor_type": "user", "kind": "user_message", "content": "block"}])
        errors = []

        def read_forever():
            try:
                runtime.read_output(timeout_seconds=5)
            except Exception as error:
                errors.append(error)

        reader = threading.Thread(target=read_forever)
        reader.start()
        time.sleep(0.1)

        started = time.monotonic()
        runtime.stop(timeout_seconds=0.1)
        elapsed = time.monotonic() - started
        reader.join(timeout=1)

        self.assertLess(elapsed, 1.0)
        self.assertFalse(reader.is_alive())
        self.assertTrue(errors)


class ApiRuntimeStubTests(unittest.TestCase):
    def test_api_runtime_stub_uses_agent_runtime_shape_without_complete_prompt_method(self):
        runtime = ApiRuntime("api-later")

        self.assertFalse(hasattr(runtime, "complete"))
        self.assertEqual(runtime.health()["status"], "unsupported")
        with self.assertRaisesRegex(RuntimeError, "later AgentRuntime implementation"):
            runtime.read_output(timeout_seconds=1)


class GeneralRoomSchedulerTests(unittest.TestCase):
    @unittest.skipUnless(live_cli_supported(), "requires POSIX PTY support")
    def test_scheduler_routes_mentions_without_replaying_full_transcript(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            room = GeneralRoomEventStore(Path(temp_dir))
            alpha = LiveCliRuntime("alpha", [sys.executable, "-u", "-c", _fake_cli_script()], idle_quiet_seconds=0.05)
            beta = LiveCliRuntime("beta", [sys.executable, "-u", "-c", _fake_cli_script()], idle_quiet_seconds=0.05)
            scheduler = RoomScheduler(
                room,
                [
                    AgentRuntimeBinding("alpha", alpha),
                    AgentRuntimeBinding("beta", beta),
                ],
                read_timeout_seconds=2,
            )
            try:
                room.append_user_message("human", "setup line with no mention")
                scheduler.dispatch_new_events()
                scheduler.wait_for_idle(timeout_seconds=3)

                room.append_user_message("human", "@alpha only answer this")
                scheduler.dispatch_new_events()
                scheduler.wait_for_idle(timeout_seconds=3)
            finally:
                scheduler.stop_all()

            events = room.read_events()
            alpha_inputs = [
                event["content"]
                for event in events
                if event["kind"] == "agent_input" and event.get("actor_id") == "alpha"
            ]
            beta_inputs = [
                event["content"]
                for event in events
                if event["kind"] == "agent_input" and event.get("actor_id") == "beta"
            ]

        self.assertEqual(len(alpha_inputs), 2)
        self.assertEqual(len(beta_inputs), 1)
        self.assertIn("@alpha only answer this", alpha_inputs[-1])
        self.assertNotIn("setup line with no mention", alpha_inputs[-1])

    @unittest.skipUnless(live_cli_supported(), "requires POSIX PTY support")
    def test_slow_cli_does_not_block_other_cli_session(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            room = GeneralRoomEventStore(Path(temp_dir))
            slow = LiveCliRuntime(
                "slow",
                [sys.executable, "-u", "-c", _fake_cli_script(delay_seconds=0.45)],
                idle_quiet_seconds=0.05,
            )
            fast = LiveCliRuntime("fast", [sys.executable, "-u", "-c", _fake_cli_script()], idle_quiet_seconds=0.05)
            scheduler = RoomScheduler(
                room,
                [
                    AgentRuntimeBinding("slow", slow),
                    AgentRuntimeBinding("fast", fast),
                ],
                read_timeout_seconds=2,
            )
            try:
                room.append_user_message("human", "@all report")
                scheduler.dispatch_new_events()
                deadline = time.time() + 1.0
                while time.time() < deadline:
                    messages = [
                        event
                        for event in room.read_events()
                        if event["kind"] == "agent_message" and event.get("actor_id") == "fast"
                    ]
                    if messages:
                        break
                    time.sleep(0.01)
                early_events = room.read_events()
            finally:
                scheduler.stop_all()

        self.assertTrue([event for event in early_events if event["kind"] == "agent_message" and event.get("actor_id") == "fast"])
        self.assertTrue([event for event in early_events if event["kind"] == "agent_delta" and event.get("actor_id") == "fast"])

    @unittest.skipUnless(live_cli_supported(), "requires POSIX PTY support")
    def test_room_events_are_jsonl_under_general_room(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            room = GeneralRoomEventStore(Path(temp_dir))
            event = room.append_user_message("human", "hello")
            path = Path(temp_dir) / "rooms" / "general" / "events.jsonl"

            lines = path.read_text(encoding="utf-8").splitlines()
            loaded = json.loads(lines[-1])

        self.assertEqual(event["event_id"], loaded["event_id"])
        self.assertEqual(loaded["kind"], "user_message")
        self.assertEqual(loaded["room_id"], "general")
        self.assertEqual(loaded["actor_id"], "human")
        self.assertEqual(loaded["actor_type"], "user")
