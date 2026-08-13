from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from agentsassemble.persistence.local.room.repository import RoomStore
from agentsassemble.room.snapshots import (
    ROOM_SNAPSHOT_EVENT_LIMIT,
    RoomSnapshotService,
)
from agentsassemble.room.global_settings import public_room_global_settings


class _Catalog:
    def __init__(self) -> None:
        self.current_snapshot_calls = 0

    def current_snapshot(self) -> dict[str, object]:
        self.current_snapshot_calls += 1
        return {
            "status": "ready",
            "catalog_revision": "revision-1",
            "providers": [{"provider_id": "codex"}],
        }


class RoomSnapshotServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.store = RoomStore(self.root)
        self.store.create_room("general", label="General")
        self.catalog = _Catalog()
        self.service = RoomSnapshotService(
            store=self.store,
            provider_catalog=self.catalog,
            ensure_room=lambda room_id: self.store.create_room(room_id),
            capabilities=lambda _identity: {"message.send": True},
        )

    def tearDown(self) -> None:
        self.store.close()
        self.temporary_directory.cleanup()

    def test_browser_snapshot_includes_public_events_catalog_and_capabilities(self) -> None:
        self.store.upsert_participant(
            "general",
            {
                "participant_id": "member",
                "display_name": "Member",
                "participant_type": "human",
            },
        )
        self.store.append_event(
            "general",
            "message_final",
            participant_id="member",
            content="hello",
        )

        snapshot = self.service.snapshot(
            {
                "meeting_id": "general",
                "agent_id": "member",
                "client_type": "browser",
            }
        )

        self.assertEqual(snapshot["snapshot_mode"], "initial")
        self.assertEqual(snapshot["events"][-1]["content"], "hello")
        self.assertEqual(
            snapshot["room_settings"],
            public_room_global_settings(self.store.room_settings("general")),
        )
        self.assertEqual(snapshot["provider_catalog"]["catalog_revision"], "revision-1")
        self.assertEqual(snapshot["capabilities"], {"message.send": True})
        self.assertEqual(self.catalog.current_snapshot_calls, 1)

    def test_browser_snapshot_preserves_sequence_across_owner_activity(self) -> None:
        self.store.append_event(
            "general",
            "message_final",
            participant_id="member",
            content="before",
        )
        hidden = self.store.append_event(
            "general",
            "activity_delta",
            participant_id="provider-agent",
            owner_id="provider-agent",
            visibility="owner",
            activity_id="private-reasoning",
            category="reasoning",
            status="running",
        )
        self.store.append_event(
            "general",
            "message_final",
            participant_id="member",
            content="after",
        )

        identity = {
            "meeting_id": "general",
            "agent_id": "member",
            "client_type": "browser",
        }
        snapshot = self.service.snapshot(identity)
        history = self.service.history_page(
            "general",
            identity=identity,
            before_seq=int(snapshot["last_seq"]) + 1,
        )

        snapshot_sequences = [int(event["seq"]) for event in snapshot["events"]]
        self.assertEqual(
            snapshot_sequences,
            list(range(snapshot_sequences[0], snapshot_sequences[-1] + 1)),
        )
        projected_hidden = next(
            event for event in snapshot["events"] if event["id"] == hidden["id"]
        )
        self.assertEqual(projected_hidden["type"], "event_hidden")
        self.assertNotIn("activity_id", projected_hidden)
        self.assertEqual(
            [int(event["seq"]) for event in history["events"]],
            snapshot_sequences,
        )

    def test_bridge_snapshot_only_contains_its_own_participant_and_session(self) -> None:
        for participant_id in ("bridge", "other"):
            self.store.upsert_participant(
                "general",
                {
                    "participant_id": participant_id,
                    "display_name": participant_id.title(),
                    "participant_type": "agent",
                },
            )
            self.store.upsert_session(
                "general",
                {
                    "session_id": participant_id,
                    "participant_id": participant_id,
                    "display_name": participant_id.title(),
                },
            )

        snapshot = self.service.snapshot(
            {
                "meeting_id": "general",
                "agent_id": "bridge",
                "session_id": "bridge",
                "client_type": "agent_bridge",
            }
        )

        self.assertEqual(snapshot["snapshot_mode"], "bridge")
        self.assertEqual(
            snapshot["room_settings"],
            public_room_global_settings(self.store.room_settings("general")),
        )
        self.assertEqual(snapshot["events"], [])
        self.assertEqual(
            [participant["participant_id"] for participant in snapshot["participants"]],
            ["bridge"],
        )
        self.assertEqual(
            [session["session_id"] for session in snapshot["agent_sessions"]],
            ["bridge"],
        )
        self.assertEqual(snapshot["available_providers"], [])

    def test_large_resume_gap_returns_a_bounded_tail_and_history_cursor(self) -> None:
        for index in range(ROOM_SNAPSHOT_EVENT_LIMIT + 2):
            self.store.append_event(
                "general",
                "message_final",
                participant_id="member",
                content=f"message-{index}",
            )

        snapshot = self.service.snapshot(
            {
                "meeting_id": "general",
                "agent_id": "member",
                "client_type": "browser",
            },
            after_seq=1,
        )
        history = self.service.history_page(
            "general",
            identity={
                "meeting_id": "general",
                "agent_id": "member",
                "client_type": "browser",
            },
            before_seq=int(snapshot["oldest_seq"]),
            limit=25,
        )

        self.assertTrue(snapshot["resume_gap"])
        self.assertEqual(snapshot["snapshot_mode"], "gap")
        self.assertEqual(len(snapshot["events"]), ROOM_SNAPSHOT_EVENT_LIMIT)
        self.assertTrue(snapshot["has_more_before"])
        self.assertTrue(history["events"])


if __name__ == "__main__":
    unittest.main()
