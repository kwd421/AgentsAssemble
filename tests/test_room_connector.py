from __future__ import annotations

import json
import tempfile
import threading
import time
import unittest
from contextlib import asynccontextmanager
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.request import Request, urlopen

import anyio
import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from agentsassemble.application.room_connector import RoomConnector
from agentsassemble.gui import _make_handler
from agentsassemble.persistence.local.room.repository import RoomStore
from agentsassemble.providers.room_connector_mcp import build_remote_room_connector_mcp
from agentsassemble.web.room_client import connect_room_ws_with_ticket


class RoomConnectorTests(unittest.TestCase):
    def setUp(self) -> None:
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

            request = Request(
                f"{base}/api/room-invite/create",
                data=json.dumps(
                    {
                        "meeting_id": "room-a",
                        "agent_id": "external-agent",
                        "display_name": "External Agent",
                        "participant_type": "agent",
                        "client_type": "browser",
                        "max_uses": 1,
                        "local_dev_preview": True,
                    }
                ).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urlopen(request, timeout=4) as response:
                invite = json.loads(response.read().decode("utf-8"))
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
            search = connector.search_messages("before join", channel_id="all")
            self.assertEqual(len(search["results"]), 1)
            self.assertEqual(search["results"][0]["content"], "before join")
            context = connector.read_message_context(
                search["results"][0]["channel_id"],
                search["results"][0]["event_id"],
            )
            self.assertEqual(
                [event["content"] for event in context["events"]],
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
                published["event"]["participant_type"],
                store.participant("room-a", "external-agent")["participant_type"],
            )
            self.assertNotEqual(published["event"]["participant_type"], "human")
            self.assertEqual(published["event"]["actor_type"], "agent")

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
            self.assertEqual(summary["own_choice"], "north")
            self.assertNotIn("voters", summary)
            self.assertNotIn("voter_ids", summary)
            withdrawn = connector.withdraw_vote(vote_id)
            self.assertEqual(withdrawn["event"]["message_kind"], "vote_withdraw")
            summary = connector.vote_summary(vote_id)
            self.assertEqual(summary["tallies"], {"north": 0, "south": 0})
            self.assertEqual(summary["own_choice"], "")
            self.assertEqual(summary["total_votes"], 0)
            closed = connector.close_vote(vote_id)
            self.assertEqual(closed["event"]["message_kind"], "vote_close")
            summary = connector.vote_summary(vote_id)
            self.assertTrue(summary["closed"])
            self.assertEqual(summary["close_reason"], "manual")

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
                    "",
                    "",
                    roll_event["content"],
                    choice_event["content"],
                ],
            )

            left = connector.leave()
            self.assertEqual(left["status"], "left")

    def test_remote_mcp_keeps_conversation_sessions_isolated(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base, store = self._start_server(Path(temp_dir))
            store.create_room("room-b", label="Room B")
            invite_a = self._create_connector_invite(
                base,
                room_id="room-a",
                agent_id="web-session-a",
            )
            invite_b = self._create_connector_invite(
                base,
                room_id="room-b",
                agent_id="web-session-b",
            )
            server = build_remote_room_connector_mcp(
                allowed_room_origins=[base],
            )
            self.addCleanup(server.close)

            result = anyio.run(
                self._exercise_isolated_remote_sessions,
                server.streamable_http_app(),
                invite_a,
                invite_b,
            )

            self.assertEqual(result["room_a"], "room-a")
            self.assertEqual(result["room_b"], "room-b")
            self.assertTrue(result["connections_are_distinct"])
            self.assertTrue(result["disallowed_join_rejected"])
            self.assertTrue(result["missing_connection_rejected"])
            self.assertEqual(result["messages_a"], ["message for A"])
            self.assertEqual(result["messages_b"], ["message for B"])
            self.assertEqual(
                [
                    str(event.get("content") or "")
                    for event in store.read_events(
                        "room-a",
                        event_types=("message_final",),
                    )
                ],
                ["message for A"],
            )
            self.assertEqual(
                [
                    str(event.get("content") or "")
                    for event in store.read_events(
                        "room-b",
                        event_types=("message_final",),
                    )
                ],
                ["message for B"],
            )

    def _create_connector_invite(
        self,
        base: str,
        *,
        room_id: str,
        agent_id: str,
    ) -> str:
        request = Request(
            f"{base}/api/room-invite/create",
            data=json.dumps(
                {
                    "meeting_id": room_id,
                    "agent_id": agent_id,
                    "display_name": agent_id,
                    "participant_type": "agent",
                    "client_type": "browser",
                    "max_uses": 1,
                    "local_dev_preview": True,
                }
            ).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(request, timeout=4) as response:
            invite = json.loads(response.read().decode("utf-8"))
        return f"{base}/join?token={invite['invite_token']}"

    async def _exercise_isolated_remote_sessions(
        self,
        app,
        invite_a: str,
        invite_b: str,
    ) -> dict[str, object]:
        async with app.router.lifespan_context(app):
            async with self._remote_mcp_session(app) as session_a:
                disallowed = await session_a.call_tool(
                    "room_join",
                    {"invite_url": "http://127.0.0.1:9/join?token=not-used"},
                )
                join_a = await session_a.call_tool(
                    "room_join",
                    {"invite_url": invite_a},
                )
            async with self._remote_mcp_session(app) as session_b:
                join_b = await session_b.call_tool(
                    "room_join",
                    {"invite_url": invite_b},
                )

            payload_a = dict(join_a.structuredContent or {})
            payload_b = dict(join_b.structuredContent or {})
            connection_a = str(payload_a["connection_id"])
            connection_b = str(payload_b["connection_id"])
            missing_connection = await self._call_remote_once(app, "room_read", {})
            await self._call_remote_once(
                app,
                "room_say",
                {"connection_id": connection_a, "content": "message for A"},
            )
            await self._call_remote_once(
                app,
                "room_say",
                {"connection_id": connection_b, "content": "message for B"},
            )
            read_a = await self._call_remote_once(
                app,
                "room_read",
                {"connection_id": connection_a},
            )
            read_b = await self._call_remote_once(
                app,
                "room_read",
                {"connection_id": connection_b},
            )
            await self._call_remote_once(
                app,
                "room_leave",
                {"connection_id": connection_a},
            )
            await self._call_remote_once(
                app,
                "room_leave",
                {"connection_id": connection_b},
            )

            state_a = dict(read_a.structuredContent or {})
            state_b = dict(read_b.structuredContent or {})
            return {
                "room_a": payload_a.get("room_id"),
                "room_b": payload_b.get("room_id"),
                "connections_are_distinct": connection_a != connection_b,
                "disallowed_join_rejected": disallowed.isError,
                "missing_connection_rejected": missing_connection.isError,
                "messages_a": [item["content"] for item in state_a["messages"]],
                "messages_b": [item["content"] for item in state_b["messages"]],
            }

    async def _call_remote_once(self, app, tool: str, arguments: dict[str, object]):
        async with self._remote_mcp_session(app) as session:
            return await session.call_tool(tool, arguments)

    @asynccontextmanager
    async def _remote_mcp_session(self, app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://127.0.0.1:8000",
        ) as http_client:
            async with streamable_http_client(
                "http://127.0.0.1:8000/mcp",
                http_client=http_client,
            ) as (read_stream, write_stream, _):
                async with ClientSession(read_stream, write_stream) as session:
                    await session.initialize()
                    yield session


if __name__ == "__main__":
    unittest.main()
