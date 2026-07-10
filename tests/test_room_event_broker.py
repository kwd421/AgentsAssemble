import unittest

from agentsassemble.room_event_broker import RoomSocketChannel


def _event_message(event_type: str, sequence: int) -> dict[str, object]:
    return {
        "op": "event",
        "stream": "room_events",
        "events": [{"id": f"evt-{sequence}", "seq": sequence, "type": event_type}],
    }


class RoomSocketChannelBackpressureTests(unittest.TestCase):
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
