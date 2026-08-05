from __future__ import annotations

import json
import tempfile
import threading
import time
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.request import Request, urlopen

from agentsassemble.application.room_connector import RoomConnector
from agentsassemble.gui import _make_handler
from agentsassemble.admission.invite import (
    create_room_invite,
    reset_state,
)
from agentsassemble.persistence.local.room.repository import RoomStore
from agentsassemble.web.room_client import connect_room_ws_with_ticket


class RoomConnectorTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_state()
        self.addCleanup(reset_state)
        self._servers: list[ThreadingHTTPServer] = []

    def tearDown(self) -> None:
        for server in self._servers:
            server.shutdown()
            server.server_close()

    def _start_server(self, root: Path) -> tuple[str, RoomStore]:
        store = RoomStore(root)
        store.create_room("room-a", label="Room A")
        server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self._servers.append(server)
        return f"http://127.0.0.1:{server.server_port}", store

    def _host_client(self, base: str):
        request = Request(
            f"{base}/api/ws-ticket",
            data=json.dumps({"meeting_id": "room-a"}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(request, timeout=4) as response:
            ticket = str(json.loads(response.read().decode("utf-8"))["ticket"])
        return connect_room_ws_with_ticket(base, ticket, ["room_events"])

    def _send_host_message(self, client, content: str, request_id: str) -> None:
        client.command(
            "message.send",
            {"content": content},
            request_id=request_id,
        )
        for _ in range(20):
            for message in client.receive():
                if message.get("request_id") == request_id:
                    self.assertEqual(message["op"], "ack")
                    return
        self.fail(f"host command {request_id} was not acknowledged")

    def _wait_for_ack(self, client, request_id: str) -> dict[str, object]:
        for _ in range(20):
            for message in client.receive():
                if message.get("request_id") == request_id:
                    return message
        self.fail(f"command {request_id} was not acknowledged")

    def test_host_role_change_is_durable_idempotent_and_visible_to_another_client(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base, store = self._start_server(Path(temp_dir))
            store.upsert_participant(
                "room-a",
                {
                    "participant_id": "agent-one",
                    "display_name": "Agent One",
                    "participant_type": "agent",
                    "role": "agent",
                    "status": "joined",
                },
            )
            host = self._host_client(base)
            observer = self._host_client(base)
            host.set_receive_timeout(0.25)
            observer.set_receive_timeout(0.25)
            self.addCleanup(host.close)
            self.addCleanup(observer.close)

            host.command(
                "participant.role.update",
                {"participant_id": "agent-one", "role": "reviewer"},
                request_id="role-change-1",
            )
            ack = self._wait_for_ack(host, "role-change-1")

            self.assertEqual(ack["op"], "ack")
            self.assertEqual(ack["result"]["participant"]["role"], "reviewer")
            self.assertEqual(store.participant("room-a", "agent-one")["role"], "reviewer")

            observed: list[dict[str, object]] = []
            for _ in range(20):
                for message in observer.receive():
                    if message.get("op") == "event":
                        observed.extend(message.get("events", []))
                if any(
                    message.get("type") == "participant_updated"
                    and message.get("participant_id") == "agent-one"
                    and message.get("role") == "reviewer"
                    for message in observed
                ):
                    break
            self.assertTrue(
                any(
                    message.get("type") == "participant_updated"
                    and message.get("participant_id") == "agent-one"
                    and message.get("role") == "reviewer"
                    for message in observed
                )
            )

            host.command(
                "participant.role.update",
                {"participant_id": "agent-one", "role": "reviewer"},
                request_id="role-change-1",
            )
            repeated = self._wait_for_ack(host, "role-change-1")
            self.assertTrue(repeated["deduplicated"])
            self.assertEqual(
                len(
                    [
                        event
                        for event in store.read_events(
                            "room-a",
                            event_types=("participant_updated",),
                        )
                        if event.get("participant_id") == "agent-one"
                    ]
                ),
                1,
            )

    def test_link_join_waits_for_new_event_and_publishes_canonically(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base, store = self._start_server(Path(temp_dir))
            host = self._host_client(base)
            self.addCleanup(host.close)
            self._send_host_message(host, "before join", "host-before")

            invite = create_room_invite(
                room_url=base,
                meeting_id="room-a",
                agent_id="external-agent",
                display_name="External Agent",
                participant_type="agent",
                client_type="browser",
                max_uses=1,
            )
            connector = RoomConnector()
            self.addCleanup(connector.close)
            joined = connector.join(
                f"{base}/join?token={invite['invite_token']}",
            )

            self.assertEqual(joined["participant_id"], "external-agent")
            self.assertEqual(joined["room_id"], "room-a")
            self.assertEqual(
                [message["content"] for message in connector.read()["messages"]],
                ["before join"],
            )

            delivered: dict[str, object] = {}

            def wait_for_room() -> None:
                delivered.update(connector.wait_next())

            waiter = threading.Thread(target=wait_for_room, daemon=True)
            waiter.start()
            time.sleep(0.2)
            self.assertTrue(waiter.is_alive(), "pre-join history woke the current session")

            self._send_host_message(host, "after join", "host-after")
            waiter.join(timeout=4)
            self.assertFalse(waiter.is_alive(), "new room activity did not wake the connector")
            self.assertEqual(
                [message["content"] for message in delivered["messages"]],
                ["after join"],
            )

            published = connector.say("connector reply")
            self.assertEqual(published["event"]["content"], "connector reply")
            self.assertEqual(published["event"]["actor_id"], "external-agent")

            poll = connector.create_vote(
                "Which route?",
                ["north", "south"],
                duration_seconds=0,
            )
            vote_id = str(poll["event"]["id"])
            cast = connector.cast_vote(vote_id, "north")
            self.assertEqual(cast["event"]["vote_choice"], "north")
            summary = connector.vote_summary(vote_id)
            self.assertEqual(summary["tallies"], {"north": 1, "south": 0})
            self.assertEqual(summary["voter_ids"]["north"], ["external-agent"])

            store.update_room_settings("room-a", {"tool_mode": "tabletop"})
            rolled = connector.roll_dice("1d6", reason="route check")
            roll_event = rolled["event"]
            self.assertEqual(roll_event["actor_id"], "room-system")
            self.assertEqual(roll_event["message_kind"], "system")
            self.assertEqual(
                roll_event["metadata"]["room_result_kind"],
                "dice_roll",
            )
            self.assertGreaterEqual(
                roll_event["metadata"]["details"]["total"],
                1,
            )
            self.assertLessEqual(
                roll_event["metadata"]["details"]["total"],
                6,
            )
            chosen = connector.choose_random(
                ["north", "south"],
                reason="route check",
            )
            choice_event = chosen["event"]
            self.assertEqual(choice_event["actor_id"], "room-system")
            self.assertEqual(
                choice_event["metadata"]["room_result_kind"],
                "random_choice",
            )
            self.assertIn(
                choice_event["metadata"]["details"]["choice"],
                {"north", "south"},
            )

            self.assertEqual(
                [
                    str(event.get("content") or "")
                    for event in store.read_events(
                        "room-a",
                        event_types=("message_final",),
                    )
                ],
                [
                    "before join",
                    "after join",
                    "connector reply",
                    "",
                    "",
                    roll_event["content"],
                    choice_event["content"],
                ],
            )

            left = connector.leave()
            self.assertEqual(left["status"], "left")


if __name__ == "__main__":
    unittest.main()
