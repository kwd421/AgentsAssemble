import unittest

import agentsassemble.room.event_broker as event_broker_module
from agentsassemble.room.event_broker import RoomEventBroker, RoomSocketChannel


def _event_message(event_type: str, sequence: int) -> dict[str, object]:
    return {
        "op": "event",
        "stream": "room_events",
        "events": [{"id": f"evt-{sequence}", "seq": sequence, "type": event_type}],
    }


class RoomSocketChannelBackpressureTests(unittest.TestCase):
    def test_connection_limit_rejects_a_second_live_socket_for_same_session(self):
        broker = RoomEventBroker(max_connections=4, max_connections_per_session=1)
        first = broker.connect(
            {"meeting_id": "general", "session_id": "session-a", "agent_id": "guest-a"}
        )

        with self.assertRaises(event_broker_module.RoomConnectionLimitError):
            broker.connect(
                {"meeting_id": "general", "session_id": "session-a", "agent_id": "guest-a"}
            )

        broker.disconnect(first)
        replacement = broker.connect(
            {"meeting_id": "general", "session_id": "session-a", "agent_id": "guest-a"}
        )
        self.assertFalse(replacement.closed)
        broker.close()

    def test_final_event_evicts_an_older_delta_first(self):
        channel = RoomSocketChannel({"meeting_id": "general"}, max_messages=10)
        for sequence in range(10):
            self.assertTrue(channel.send(_event_message("message_delta", sequence)))

        self.assertTrue(channel.send(_event_message("message_final", 10)))
        drained = channel.drain()

        self.assertEqual(len(drained), 10)
        self.assertTrue(any(message["events"][0]["type"] == "message_final" for message in drained))
        self.assertEqual(channel.diagnostics()["dropped_delta_count"], 1)
        channel.close()

    def test_provider_catalog_control_is_sent_only_to_browser_clients(self):
        broker = RoomEventBroker()
        browser = broker.connect({"meeting_id": "general", "client_type": "browser"})
        bridge = broker.connect(
            {"meeting_id": "general", "client_type": "agent_bridge", "agent_id": "codex"}
        )

        broker.broadcast_control(
            "general",
            {"op": "provider_catalog_updated", "catalog": {"catalog_revision": "cat-test"}},
            client_type="browser",
        )

        self.assertEqual(browser.drain()[0]["op"], "provider_catalog_updated")
        self.assertEqual(bridge.drain(), [])
        broker.close()

    def test_full_essential_queue_emits_resync_marker_before_new_final(self):
        channel = RoomSocketChannel({"meeting_id": "general"}, max_messages=10)
        for sequence in range(10):
            self.assertTrue(channel.send(_event_message("message_final", sequence)))

        self.assertTrue(channel.send(_event_message("message_final", 10)))
        drained = channel.drain()

        self.assertEqual(drained[0]["op"], "resync_required")
        self.assertEqual(drained[1]["events"][0]["seq"], 10)
        self.assertEqual(channel.diagnostics()["resync_count"], 1)
        channel.close()

    def test_incoming_delta_is_dropped_when_only_essential_events_are_queued(self):
        channel = RoomSocketChannel({"meeting_id": "general"}, max_messages=10)
        for sequence in range(10):
            channel.send(_event_message("message_final", sequence))

        self.assertFalse(channel.send(_event_message("message_delta", 10)))
        drained = channel.drain()

        self.assertEqual(len(drained), 10)
        self.assertEqual(channel.diagnostics()["dropped_delta_count"], 1)
        channel.close()


if __name__ == "__main__":
    unittest.main()
