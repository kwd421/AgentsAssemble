from __future__ import annotations

import tempfile
import threading
import time
import unittest
from pathlib import Path

from agentsassemble.providers.agent_bridge import RoomAgentBridge
from agentsassemble.providers.launch_specs import NativeCliProviderSpec
from agentsassemble.providers.provider_requests import BridgeProviderRequestRouter
from agentsassemble.room.errors import RoomCommandRejected
from agentsassemble.room.event_broker import RoomEventBroker
from agentsassemble.room.realtime import RoomRealtimeController
from agentsassemble.room.snapshots import ROOM_SNAPSHOT_EVENT_LIMIT
from tests.test_room_agent_bridge import FakeRuntime, _wait_for
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


class RecordingBroker(RoomEventBroker):
    def __init__(self) -> None:
        super().__init__()
        self.bridge_messages: list[dict[str, object]] = []

    def has_bridge(self, room_id: str, participant_id: str) -> bool:
        return room_id == "general" and participant_id == "grok"

    def direct_to_bridge(
        self,
        room_id: str,
        participant_id: str,
        message: dict[str, object],
    ) -> bool:
        if not self.has_bridge(room_id, participant_id):
            return False
        self.bridge_messages.append(dict(message))
        super().direct_to_bridge(room_id, participant_id, message)
        return True


class ControllerBridgeClient:
    def __init__(
        self,
        controller: RoomRealtimeController,
        identity: dict[str, object],
    ) -> None:
        self.controller = controller
        self.identity = dict(identity)
        self.channel = controller.connect(self.identity)
        self._responses: list[dict[str, object]] = []
        self.closed = False
        self._lock = threading.Lock()

    def receive(self) -> list[dict[str, object]]:
        with self._lock:
            responses = list(self._responses)
            self._responses.clear()
        return [*responses, *self.channel.drain()]

    def command(
        self,
        action: str,
        payload: dict[str, object] | None = None,
        *,
        request_id: str = "",
    ) -> str:
        try:
            response = self.controller.handle_command(
                self.identity,
                {
                    "op": "command",
                    "request_id": request_id,
                    "action": action,
                    "payload": dict(payload or {}),
                },
            )
        except RoomCommandRejected as error:
            response = {
                "op": "error",
                "request_id": request_id,
                "code": error.code,
                "message": str(error),
            }
        with self._lock:
            self._responses.append(response)
        return request_id

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        self.controller.disconnect(self.channel)


class RoomProviderRequestTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        access = memory_room_access_services()
        self.broker = RecordingBroker()
        self.controller = RoomRealtimeController(
            self.root,
            **access.controller_kwargs(),
            broker=self.broker,
            reconcile_startup_sessions=False,
        )
        self.host_channel = self.controller.connect(HOST)
        self.controller.create_provider_session(
            "general",
            NativeCliProviderSpec(
                agent_id="grok",
                display_name="Grok",
                command=("grok", "agent", "stdio"),
                cwd=str(self.root),
                provider_kind="grok_live_session",
                runtime_kind="live_cli",
                transport="acp_stdio",
            ),
        )
        self.bridge = {
            "agent_id": "grok",
            "session_id": "grok",
            "participant_type": "agent",
            "client_type": "agent_bridge",
            "invite_scope": "read_write",
            "meeting_id": "general",
            "operator": False,
        }

    def tearDown(self) -> None:
        self.controller.close()
        self.temp.cleanup()

    def command(
        self,
        identity: dict[str, object],
        request_id: str,
        action: str,
        payload: dict[str, object],
    ) -> dict[str, object]:
        return self.controller.handle_command(
            identity,
            {
                "op": "command",
                "request_id": request_id,
                "action": action,
                "payload": payload,
            },
        )

    def test_owner_resolves_exact_native_option_once_and_bridge_confirms_completion(self) -> None:
        opened = self.command(
            self.bridge,
            "open-1",
            "provider.request.open",
            {
                "provider_request_id": "provider-request-1",
                "request_kind": "permission",
                "response_kind": "option",
                "title": "터미널 명령 실행",
                "description": "프로젝트 테스트를 실행하려고 합니다.",
                "options": [
                    {"id": "allow-once", "label": "이번만 허용", "kind": "allow_once"},
                    {"id": "reject-once", "label": "거절", "kind": "reject_once"},
                ],
            },
        )
        self.assertEqual(opened["result"]["status"], "open")

        owner_events = self.controller.snapshot(HOST)["events"]
        request_event = next(event for event in owner_events if event["type"] == "provider_request_opened")
        self.assertEqual(request_event["provider_request"]["options"][0]["id"], "allow-once")

        guest_events = self.controller.snapshot(
            {**HOST, "agent_id": "guest", "operator": False}
        )["events"]
        self.assertIn(
            "event_hidden",
            [event["type"] for event in guest_events if event["seq"] == request_event["seq"]],
        )

        resolving = self.command(
            HOST,
            "resolve-1",
            "provider.request.resolve",
            {
                "provider_request_id": "provider-request-1",
                "option_id": "allow-once",
            },
        )
        self.assertEqual(resolving["result"]["status"], "resolving")
        self.assertEqual(
            self.broker.bridge_messages[-1],
            {
                "op": "provider.request.resolve",
                "provider_request_id": "provider-request-1",
                "option_id": "allow-once",
            },
        )

        closed = self.command(
            self.bridge,
            "close-1",
            "provider.request.closed",
            {
                "provider_request_id": "provider-request-1",
                "status": "resolved",
            },
        )
        self.assertEqual(closed["result"]["status"], "resolved")
        self.assertNotIn(
            "pending_provider_request",
            self.controller.snapshot(HOST)["agent_sessions"][0],
        )

        with self.assertRaises(RoomCommandRejected) as duplicate:
            self.command(
                HOST,
                "resolve-2",
                "provider.request.resolve",
                {
                    "provider_request_id": "provider-request-1",
                    "option_id": "allow-once",
                },
            )
        self.assertEqual(duplicate.exception.code, "provider_request_not_pending")

    def test_pending_request_survives_snapshot_history_window(self) -> None:
        self.command(
            self.bridge,
            "open-old-request",
            "provider.request.open",
            {
                "provider_request_id": "provider-request-old",
                "request_kind": "permission",
                "response_kind": "option",
                "title": "파일 변경",
                "options": [
                    {"id": "accept", "label": "이번만 허용", "kind": "allow_once"},
                    {"id": "decline", "label": "거절", "kind": "decline"},
                ],
            },
        )
        for index in range(ROOM_SNAPSHOT_EVENT_LIMIT):
            self.command(
                HOST,
                f"message-{index}",
                "message.send",
                {"message": f"later message {index}"},
            )

        snapshot = self.controller.snapshot(HOST)

        self.assertNotIn(
            "provider_request_opened",
            [event["type"] for event in snapshot["events"]],
        )
        self.assertEqual(
            snapshot["provider_requests"][0]["provider_request_id"],
            "provider-request-old",
        )

    def test_room_operator_cannot_see_or_resolve_another_users_private_request(self) -> None:
        self.controller.store.update_participant_fields(
            "general",
            "grok",
            owner_id="guest-owner",
            created_by="guest-owner",
        )
        self.command(
            self.bridge,
            "open-private-request",
            "provider.request.open",
            {
                "provider_request_id": "private-request",
                "request_kind": "permission",
                "response_kind": "option",
                "title": "비공개 파일 변경 승인",
                "options": [
                    {"id": "accept", "label": "이번만 허용", "kind": "allow_once"},
                    {"id": "decline", "label": "거절", "kind": "decline"},
                ],
            },
        )

        self.assertEqual(self.controller.snapshot(HOST)["provider_requests"], [])
        with self.assertRaises(RoomCommandRejected) as denied:
            self.command(
                HOST,
                "resolve-private-request-as-host",
                "provider.request.resolve",
                {
                    "provider_request_id": "private-request",
                    "option_id": "accept",
                },
            )
        self.assertEqual(denied.exception.code, "permission_denied")

        owner = {
            **HOST,
            "agent_id": "guest-owner",
            "user_id": "guest-owner",
            "operator": False,
        }
        self.assertEqual(
            self.controller.snapshot(owner)["provider_requests"][0][
                "provider_request_id"
            ],
            "private-request",
        )

    def test_blocking_provider_request_round_trips_through_durable_room_state(self) -> None:
        report_count = 0

        def report(action: str, payload: dict[str, object]) -> dict[str, object]:
            nonlocal report_count
            report_count += 1
            return self.command(
                self.bridge,
                f"provider-report-{report_count}",
                action,
                payload,
            )

        router = BridgeProviderRequestRouter(
            report=report,
            stopping=threading.Event(),
        )
        provider_resolution: list[dict[str, object]] = []
        worker = threading.Thread(
            target=router.handle,
            args=(
                {
                    "request_kind": "user_input",
                    "response_kind": "answers",
                    "title": "검증 선택",
                    "questions": [
                        {
                            "id": "checks",
                            "header": "검증",
                            "question": "어떤 검증을 실행할까요?",
                            "options": [
                                {
                                    "id": "build",
                                    "label": "빌드",
                                    "kind": "answer",
                                }
                            ],
                            "multiple": False,
                            "is_other": False,
                        }
                    ],
                    "timeout_seconds": 15,
                },
                provider_resolution.append,
            ),
            daemon=True,
        )
        worker.start()
        deadline = time.monotonic() + 2
        pending: list[dict[str, object]] = []
        while time.monotonic() < deadline:
            pending = self.controller.snapshot(HOST)["provider_requests"]
            if pending:
                break
            time.sleep(0.01)
        self.assertEqual(len(pending), 1)
        provider_request_id = str(pending[0]["provider_request_id"])

        self.command(
            HOST,
            "resolve-blocking-provider-request",
            "provider.request.resolve",
            {
                "provider_request_id": provider_request_id,
                "answers": {"checks": ["빌드"]},
            },
        )
        self.assertTrue(router.resolve(self.broker.bridge_messages[-1]))
        worker.join(timeout=2)

        self.assertFalse(worker.is_alive())
        self.assertEqual(provider_resolution, [{"answers": {"checks": ["빌드"]}}])
        self.assertEqual(self.controller.snapshot(HOST)["provider_requests"], [])
        resolved = [
            event
            for event in self.controller.snapshot(HOST)["events"]
            if event["type"] == "provider_request_resolved"
            and event.get("provider_request", {}).get("provider_request_id")
            == provider_request_id
        ]
        self.assertEqual(resolved[-1]["provider_request"]["status"], "resolved")

    def test_resolution_delivered_to_replacement_bridge_fails_durable_request(self) -> None:
        old_channel = self.controller.connect(dict(self.bridge))
        self.controller.handle_command(
            old_channel.identity,
            {
                "op": "command",
                "request_id": "old-bridge-ready",
                "action": "bridge.ready",
                "payload": FakeRuntime().health() | {"running": True},
            },
        )
        self.command(
            old_channel.identity,
            "open-before-lease-change",
            "provider.request.open",
            {
                "provider_request_id": "request-from-old-bridge-lease",
                "request_kind": "permission",
                "response_kind": "option",
                "title": "파일 변경",
                "options": [
                    {"id": "allow-once", "label": "이번만 허용", "kind": "allow_once"},
                    {"id": "reject-once", "label": "거절", "kind": "reject_once"},
                ],
            },
        )

        replacement_client = ControllerBridgeClient(self.controller, self.bridge)
        replacement_bridge = RoomAgentBridge(
            replacement_client,
            FakeRuntime(),
            room_id="general",
            participant_id="grok",
            session_id="grok",
            receive_sleep_seconds=0.005,
        )
        bridge_thread = threading.Thread(target=replacement_bridge.run, daemon=True)
        bridge_thread.start()
        _wait_for(lambda: old_channel.closed)

        self.command(
            HOST,
            "resolve-after-lease-change",
            "provider.request.resolve",
            {
                "provider_request_id": "request-from-old-bridge-lease",
                "option_id": "allow-once",
            },
        )

        _wait_for(lambda: not self.controller.snapshot(HOST)["provider_requests"])
        resolved = [
            event
            for event in self.controller.snapshot(HOST)["events"]
            if event["type"] == "provider_request_resolved"
            and event.get("provider_request", {}).get("provider_request_id")
            == "request-from-old-bridge-lease"
        ]
        self.assertEqual(resolved[-1]["provider_request"]["status"], "failed")
        self.assertTrue(bridge_thread.is_alive())

        replacement_client.channel.send({"op": "agent.control", "action": "stop"})
        bridge_thread.join(timeout=2)
        self.assertFalse(bridge_thread.is_alive())


if __name__ == "__main__":
    unittest.main()
