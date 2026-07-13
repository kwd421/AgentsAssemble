from __future__ import annotations

import json
import os
import socket
import tempfile
import threading
import time
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.request import Request, urlopen

from agentsassemble.gui import _make_handler
from agentsassemble.room_bridge_process import NativeCliBridgeProcessManager
from agentsassemble.room_native_cli_smoke import NON_ROOM_REPLY, _latency_acceptance, run_room_native_cli_smoke
from agentsassemble.room_realtime import NativeCliProviderSpec, RoomRealtimeController
from agentsassemble.ws_room_client import connect_room_ws_with_ticket


FIXTURE = Path(__file__).parent / "fixtures" / "fake_interactive_cli.py"
GROUP_FIXTURE = Path(__file__).parent / "fixtures" / "fake_group_cli.py"


class NativeCliRoomEndToEndTests(unittest.TestCase):
    def test_plan_mode_refusal_and_tool_markup_are_not_room_replies(self):
        refusal = (
            "Plan mode is currently active. <tool_call><tool_name>AskUserQuestion</tool_name></tool_call>"
        )

        self.assertIsNotNone(NON_ROOM_REPLY.search(refusal))
        self.assertIsNone(NON_ROOM_REPLY.search("앞선 두 의견을 읽었고, 시간축부터 검증하자."))

    def test_two_persistent_clis_take_server_assigned_turns_without_visible_mentions(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config = root / "group-providers.json"
            config.write_text(
                json.dumps(
                    {
                        "room_id": "general",
                        "providers": [
                            {
                                "id": agent_id,
                                "display_name": agent_id,
                                "provider_kind": "local_cli",
                                "runtime_kind": "live_cli",
                                "transport": "pty",
                                "model": "fixture-group-model",
                                "permission_mode": "meeting_read_only",
                                "command": [os.sys.executable, "-u", str(GROUP_FIXTURE), agent_id],
                                "cwd": str(root),
                                "input_mode": "bracketed_paste",
                                "quiet_seconds": 0.05,
                                "startup_quiet_seconds": 0.05,
                                "startup_timeout_seconds": 1.0,
                                "default_responder": False,
                            }
                            for agent_id in ("group-a", "group-b")
                        ],
                    }
                ),
                encoding="utf-8",
            )

            result = run_room_native_cli_smoke(
                config_path=config,
                output_root=root,
                providers=["group-a", "group-b"],
                approve_real_provider=True,
                timeout_seconds=5.0,
                agent_conversation=True,
                conversation_seconds=1.0,
                conversation_topic="A tiny haunted station test",
                verify_controls=True,
                observe_gui_port=self._unused_loopback_port(),
            )

        conversation = result["conversation"]
        cycle_count = conversation["speaker_cycles_completed"]
        self.assertEqual(result["status"], "ok", json.dumps(result, ensure_ascii=False, indent=2))
        self.assertIs(result["provider_workspace_isolated"], True)
        self.assertRegex(result["observer_url"], r"^http://127\.0\.0\.1:\d+/$")
        self.assertEqual(conversation["topology"], "server_assigned_shared_room")
        self.assertGreaterEqual(cycle_count, 2)
        self.assertEqual(len(conversation["turns"]), cycle_count * 2)
        self.assertEqual(
            conversation["actual_turn_counts"],
            {"group-a": cycle_count + 1, "group-b": cycle_count + 1},
        )
        self.assertFalse(conversation["unexpected_extra_turns"])
        self.assertEqual(conversation["metrics"]["turn_count"], cycle_count * 2)
        self.assertTrue(conversation["timebox_met"])
        self.assertEqual(conversation["visible_at_mention_count"], 0)
        self.assertTrue(conversation["all_agents_saw_full_peer_context_after_warmup"])
        self.assertEqual(len(conversation["control_checks"]), 2)
        self.assertTrue(all(all(item["checks"].values()) for item in conversation["control_checks"]))
        self.assertIsNotNone(result["metrics"]["p50_time_to_first_agent_delta_ms"])
        for turn in conversation["turns"]:
            self.assertTrue(all(turn["checks"].values()))
            self.assertNotIn("@", turn["output"])
        for provider in conversation["providers"]:
            self.assertTrue(provider["same_pid_over_turns"])
            self.assertTrue(provider["pause_resume_verified"])
            self.assertTrue(provider["kick_verified"])
            self.assertFalse(provider["alive_after_stop"])

    @staticmethod
    def _unused_loopback_port() -> int:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.bind(("127.0.0.1", 0))
            return int(probe.getsockname()[1])

    def test_unified_smoke_harness_records_real_process_provenance(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config = root / "providers.json"
            config.write_text(
                json.dumps(
                    {
                        "room_id": "general",
                        "providers": [
                            {
                                "id": "fake",
                                "display_name": "Fake Interactive CLI",
                                "provider_kind": "local_cli",
                                "runtime_kind": "live_cli",
                                "transport": "pty",
                                "model": "fixture-interactive-model",
                                "permission_mode": "meeting_read_only",
                                "command": [os.sys.executable, "-u", str(FIXTURE)],
                                "cwd": str(root),
                                "input_mode": "bracketed_paste",
                                "quiet_seconds": 0.05,
                                "startup_quiet_seconds": 0.05,
                                "startup_timeout_seconds": 1.0,
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            result = run_room_native_cli_smoke(
                config_path=config,
                output_root=root,
                providers=["fake"],
                approve_real_provider=True,
                timeout_seconds=5.0,
                latency_samples=2,
            )
            provider = result["providers"][0]

        self.assertEqual(result["status"], "ok")
        self.assertIs(result["provider_workspace_isolated"], True)
        self.assertEqual(provider["status"], "ok")
        self.assertEqual(provider["transport"], "pty+websocket")
        self.assertTrue(provider["same_pid_over_turns"])
        self.assertTrue(provider["memory_marker_recalled"])
        self.assertFalse(provider["alive_after_stop"])
        self.assertEqual(provider["message_sources"], ["terminal_capture", "terminal_capture"])
        self.assertEqual(len(provider["provider_direct_ttfo_ms"]), 2)
        self.assertEqual(len(provider["room_observed_ttfo_ms"]), 2)
        self.assertFalse(provider["latency_acceptance"]["enforced"])

    def test_latency_acceptance_enforces_same_turn_room_overhead_limits(self):
        passing = _latency_acceptance(
            [1000.0] * 10,
            [1100.0] * 10,
        )
        excessive_ratio = _latency_acceptance(
            [1000.0] * 10,
            [1200.0] * 10,
        )
        excessive_tail = _latency_acceptance(
            [1000.0] * 9 + [100.0],
            [1100.0] * 9 + [2000.0],
        )

        self.assertTrue(passing["passed"])
        self.assertFalse(excessive_ratio["passed"])
        self.assertFalse(excessive_ratio["checks"]["room_p50_within_115_percent"])
        self.assertFalse(excessive_tail["passed"])
        self.assertFalse(excessive_tail["checks"]["p95_extra_within_750_ms"])

    def test_browser_and_persistent_cli_bridge_share_one_canonical_websocket(self):
        self._inbox: list[dict[str, object]] = []
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            workspace = root / "workspace"
            workspace.mkdir()
            spec = NativeCliProviderSpec(
                agent_id="fake",
                display_name="Fake Interactive CLI",
                command=(os.sys.executable, "-u", str(FIXTURE)),
                cwd=str(workspace),
                provider_kind="local_cli",
                model="fixture-interactive-model",
                default_responder=False,
                quiet_seconds=0.05,
                input_mode="bracketed_paste",
                startup_quiet_seconds=0.05,
                startup_timeout_seconds=1.0,
                turn_timeout_seconds=5.0,
            )
            manager = NativeCliBridgeProcessManager(root)
            controller = RoomRealtimeController(root, providers=[spec], bridge_manager=manager)
            manager.set_exit_listener(controller.bridge_process_exited)
            server = ThreadingHTTPServer(
                ("127.0.0.1", 0),
                _make_handler(root, room_realtime_controller_override=controller),
            )
            server_thread = threading.Thread(target=server.serve_forever, daemon=True)
            server_thread.start()
            host, port = server.server_address
            base = f"http://{host}:{port}"
            client = None
            provider_pid = None
            bridge_pid = None
            try:
                ticket = self._host_ticket(base)
                client = connect_room_ws_with_ticket(base, ticket, ["room_events"], timeout=3.0)
                client.sock.settimeout(0.1)
                received = self._receive_until(client, lambda message: message.get("op") == "snapshot")
                self.assertEqual(received["room"]["room_id"], "general")

                start_request = client.command("agent.start", {"agent_id": "fake"}, request_id="start-fake")
                start_ack = self._receive_until(
                    client,
                    lambda message: message.get("op") == "ack" and message.get("request_id") == start_request,
                )
                self.assertNotIn("bridge_pid", start_ack["result"]["launch"])
                bridge_pid = manager.health("general", "fake")["bridge_pid"]
                self._wait_for(lambda: controller.store.session("general", "fake").get("runtime_status") == "idle")

                first_request = client.command(
                    "message.send",
                    {"content": "@fake AGENTSASSEMBLE_SESSION_MARKER=room-e2e-001 기억해."},
                    request_id="message-one",
                )
                self._receive_until(
                    client,
                    lambda message: message.get("op") == "ack" and message.get("request_id") == first_request,
                )
                try:
                    first_message = self._receive_room_event(
                        client,
                        lambda event: event.get("type") == "message_final"
                        and (event.get("actor") or {}).get("participant_id") == "fake",
                    )
                except AssertionError as error:
                    session = controller.store.session("general", "fake")
                    events = controller.store.read_events("general")[-12:]
                    stderr_tail = manager.health("general", "fake").get("stderr_tail")
                    raise AssertionError(
                        f"{error}\nsession={session!r}\nevents={events!r}\nstderr={stderr_tail!r}"
                    ) from error
                first_session = controller.store.session("general", "fake")
                provider_pid = first_session["reported_provider_pid"]

                second_request = client.command(
                    "message.send",
                    {"content": "@fake 아까 marker 값만 다시 말해."},
                    request_id="message-two",
                )
                self._receive_until(
                    client,
                    lambda message: message.get("op") == "ack" and message.get("request_id") == second_request,
                )
                second_message = self._receive_room_event(
                    client,
                    lambda event: event.get("type") == "message_final"
                    and (event.get("actor") or {}).get("participant_id") == "fake"
                    and event.get("id") != first_message.get("id"),
                )
                self._wait_for(lambda: controller.store.session("general", "fake").get("turn_count") == 2)
                second_session = controller.store.session("general", "fake")

                self.assertIn("room-e2e-001", first_message["content"])
                self.assertIn("room-e2e-001", second_message["content"])
                self.assertEqual(first_session["reported_provider_pid"], second_session["reported_provider_pid"])
                self.assertEqual(second_session["turn_count"], 2)
                self.assertTrue((root / "rooms" / "rooms.sqlite3").is_file())
                self.assertFalse((root / "rooms" / "general" / "live_cli_events.jsonl").exists())

                stop_request = client.command("agent.stop", {"agent_id": "fake"}, request_id="stop-fake")
                stop_ack = self._receive_until(
                    client,
                    lambda message: message.get("op") == "ack" and message.get("request_id") == stop_request,
                )
                self.assertFalse(stop_ack["result"]["process"]["alive"])
                self._wait_for(lambda: not self._pid_alive(int(provider_pid)))
                self._wait_for(lambda: not self._pid_alive(int(bridge_pid)))
            finally:
                if client is not None:
                    client.close()
                controller.close()
                server.shutdown()
                server.server_close()
                server_thread.join(timeout=2.0)

    @staticmethod
    def _host_ticket(base: str) -> str:
        request = Request(
            f"{base}/api/ws-ticket",
            data=json.dumps({"meeting_id": "general"}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(request, timeout=3.0) as response:
            return str(json.loads(response.read().decode("utf-8"))["ticket"])

    def _receive_room_event(self, client, predicate, *, timeout_seconds: float = 8.0):
        message = self._receive_until(
            client,
            lambda item: item.get("op") == "event"
            and any(predicate(event) for event in item.get("events", []) if isinstance(event, dict)),
            timeout_seconds=timeout_seconds,
        )
        return next(event for event in message["events"] if predicate(event))

    def _receive_until(self, client, predicate, *, timeout_seconds: float = 8.0):
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            for index, message in enumerate(self._inbox):
                if predicate(message):
                    return self._inbox.pop(index)
            received = client.receive()
            for index, message in enumerate(received):
                if predicate(message):
                    self._inbox.extend(received[index + 1 :])
                    return message
                self._inbox.append(message)
        raise AssertionError("Timed out waiting for matching WebSocket message.")

    @staticmethod
    def _wait_for(predicate, *, timeout_seconds: float = 8.0) -> None:
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            if predicate():
                return
            time.sleep(0.02)
        raise AssertionError("Timed out waiting for process or session state.")

    @staticmethod
    def _pid_alive(pid: int) -> bool:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        return True


if __name__ == "__main__":
    unittest.main()
