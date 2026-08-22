from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from agentsassemble.features.message_search.service import MessageSearchService
from agentsassemble.persistence.local.room.repository import RoomStore


class MessageSearchServiceTests(unittest.TestCase):
    def test_search_covers_complete_public_history_and_whitespace_insensitive_phrase(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            rooms = RoomStore(root)
            rooms.create_room("search-room")
            target = rooms.append_event(
                "search-room",
                "message_final",
                participant_id="human-a",
                participant_type="human",
                display_name="Release Owner",
                content="오래된 배포오류를 고쳤습니다",
                message_kind="message",
                attachments=[
                    {
                        "id": "attachment-1",
                        "filename": "release-notes.txt",
                        "content_type": "text/plain",
                        "size": 10,
                    }
                ],
            )
            for index in range(240):
                rooms.append_event(
                    "search-room",
                    "message_final",
                    participant_id="human-b",
                    participant_type="human",
                    display_name="Chatter",
                    content=f"later message {index}",
                    message_kind="message",
                )
            rooms.append_event(
                "search-room",
                "activity_delta",
                participant_id="agent-a",
                participant_type="agent",
                display_name="Private Tool",
                content="배포 오류 secret thought",
            )

            search = MessageSearchService(root)
            search.sync_lobby(rooms, "search-room")
            page = search.search(
                "search-room",
                query="배포 오류",
                channel_ids=["lobby"],
            )

            self.assertEqual([item["event_id"] for item in page["results"]], [target["id"]])
            self.assertEqual(page["results"][0]["attachment_filenames"], ["release-notes.txt"])

    def test_search_pages_newest_first_without_a_total_cutoff(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            rooms = RoomStore(root)
            rooms.create_room("search-room")
            for index in range(67):
                rooms.append_event(
                    "search-room",
                    "message_final",
                    participant_id="human-a",
                    participant_type="human",
                    display_name="Human",
                    content=f"needle result {index:02d}",
                    message_kind="message",
                )

            search = MessageSearchService(root)
            search.sync_lobby(rooms, "search-room")
            first = search.search("search-room", query="needle", channel_ids=["lobby"])
            second = search.search(
                "search-room",
                query="needle",
                channel_ids=["lobby"],
                cursor=str(first["next_cursor"]),
            )
            third = search.search(
                "search-room",
                query="needle",
                channel_ids=["lobby"],
                cursor=str(second["next_cursor"]),
            )

            self.assertEqual([len(first["results"]), len(second["results"]), len(third["results"])], [30, 30, 7])
            sequences = [
                int(item["seq"])
                for page in (first, second, third)
                for item in page["results"]
            ]
            self.assertEqual(sequences, sorted(sequences, reverse=True))
            self.assertEqual(third["next_cursor"], "")


if __name__ == "__main__":
    unittest.main()
