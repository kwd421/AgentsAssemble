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
            self.assertEqual(
                [
                    event["content"]
                    for event in store.read_events(
                        "room-a",
                        event_types=("message_final",),
                    )
                ],
                ["before join", "after join", "connector reply"],
            )

            left = connector.leave()
            self.assertEqual(left["status"], "left")


if __name__ == "__main__":
    unittest.main()
