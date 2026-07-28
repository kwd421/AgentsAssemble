import tempfile
import unittest
from pathlib import Path

from agentsassemble.room.moderation import set_room_member_muted
from agentsassemble.room.members import (
    read_room_members,
    room_members_payload,
    set_canonical_room_member_role,
    upsert_room_member,
)
from agentsassemble.persistence.local.room.repository import RoomStore

ROOM = "room-presence-test"


class RoomMembersPresenceTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.output_root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        self.repository = RoomStore(self.output_root)
        self.addCleanup(self.repository.close)

    def _saved_guest(self, participant_id, display_name, *, updated_at):
        upsert_room_member(
            self.output_root,
            {
                "meeting_id": ROOM,
                "participant_id": participant_id,
                "display_name": display_name,
                "role": "human",
                "participant_type": "human",
                "status": "online",
                "source": "room_invite",
                "created_at": updated_at,
                "updated_at": updated_at,
            },
        )

    def test_canonical_role_change_updates_room_authority_and_emits_an_event(self):
        self.repository.create_room(ROOM)
        self.repository.upsert_participant(
            ROOM,
            {
                "participant_id": "agent-director",
                "display_name": "Director",
                "role": "agent",
                "participant_type": "subscription_ai",
                "status": "joined",
            },
        )

        member = set_canonical_room_member_role(
            self.repository,
            meeting_id=ROOM,
            participant_id="agent-director",
            role="director",
        )
        events = self.repository.read_events(
            ROOM,
            event_types=("participant_updated",),
        )

        self.assertEqual(member["role"], "director")
        self.assertEqual(
            self.repository.participant(ROOM, "agent-director")["role"],
            "director",
        )
        self.assertEqual(events[-1]["role"], "director")
        with self.assertRaisesRegex(ValueError, "Unsupported room member role"):
            set_canonical_room_member_role(
                self.repository,
                meeting_id=ROOM,
                participant_id="agent-director",
                role="unknown-role",
            )

    def test_invite_member_without_live_session_shows_offline(self):
        self._saved_guest("guest-dead01", "유령", updated_at="2026-06-10T00:00:00+00:00")
        payload = room_members_payload(
            self.output_root, [], meeting_id=ROOM, sessions=[], repository=self.repository
        )
        members = payload["members"]
        self.assertEqual(len(members), 1)
        self.assertEqual(members[0]["status"], "offline")

    def test_invite_member_with_live_session_stays_online(self):
        self._saved_guest("guest-live01", "현재인", updated_at="2026-06-10T00:00:00+00:00")
        sessions = [
            {
                "agent_id": "guest-live01",
                "display_name": "현재인",
                "meeting_id": ROOM,
                "participant_type": "human",
                "joined_at": "2026-06-11T00:00:00+00:00",
            }
        ]
        payload = room_members_payload(
            self.output_root, [], meeting_id=ROOM, sessions=sessions, repository=self.repository
        )
        members = payload["members"]
        self.assertEqual(len(members), 1)
        self.assertEqual(members[0]["status"], "online")

    def test_stale_same_name_guests_collapse_to_newest(self):
        # Pre-stable-identity rejoins minted a fresh guest id per join.
        self._saved_guest("guest-aaaa01", "친절한페이블찡", updated_at="2026-06-11T01:00:00+00:00")
        self._saved_guest("guest-aaaa02", "친절한페이블찡", updated_at="2026-06-11T02:00:00+00:00")
        self._saved_guest("guest-aaaa03", "친절한페이블찡", updated_at="2026-06-11T03:00:00+00:00")
        payload = room_members_payload(
            self.output_root, [], meeting_id=ROOM, sessions=[], repository=self.repository
        )
        members = payload["members"]
        self.assertEqual(len(members), 1)
        self.assertEqual(members[0]["participant_id"], "guest-aaaa03")
        self.assertEqual(members[0]["status"], "offline")

    def test_stale_duplicates_vanish_when_same_name_is_live(self):
        self._saved_guest("guest-bbbb01", "친절한페이블찡", updated_at="2026-06-11T01:00:00+00:00")
        self._saved_guest("guest-bbbb02", "친절한페이블찡", updated_at="2026-06-11T02:00:00+00:00")
        sessions = [
            {
                "agent_id": "user-stable-1",
                "display_name": "친절한페이블찡",
                "meeting_id": ROOM,
                "participant_type": "human",
                "joined_at": "2026-06-11T05:00:00+00:00",
            }
        ]
        payload = room_members_payload(
            self.output_root, [], meeting_id=ROOM, sessions=sessions, repository=self.repository
        )
        members = payload["members"]
        self.assertEqual(len(members), 1)
        self.assertEqual(members[0]["participant_id"], "user-stable-1")
        self.assertEqual(members[0]["status"], "online")

    def test_distinct_live_guests_with_same_name_both_remain(self):
        sessions = [
            {
                "agent_id": "guest-cccc01",
                "display_name": "Guest",
                "meeting_id": ROOM,
                "participant_type": "human",
                "joined_at": "2026-06-11T05:00:00+00:00",
            },
            {
                "agent_id": "guest-cccc02",
                "display_name": "Guest",
                "meeting_id": ROOM,
                "participant_type": "human",
                "joined_at": "2026-06-11T05:01:00+00:00",
            },
        ]
        payload = room_members_payload(
            self.output_root, [], meeting_id=ROOM, sessions=sessions, repository=self.repository
        )
        self.assertEqual(len(payload["members"]), 2)
        self.assertTrue(all(member["status"] == "online" for member in payload["members"]))

    def test_live_agent_status_not_overridden_by_session_presence(self):
        agents = [
            {
                "agent_id": "agent-haiku",
                "display_name": "하이쿠",
                "meeting_id": ROOM,
                "provider_kind": "claude",
                "status": "working",
            }
        ]
        payload = room_members_payload(
            self.output_root, agents, meeting_id=ROOM, sessions=[], repository=self.repository
        )
        members = payload["members"]
        self.assertEqual(len(members), 1)
        self.assertEqual(members[0]["status"], "working")

    def test_declared_agent_session_keeps_agent_role_for_grouping(self):
        sessions = [
            {
                "agent_id": "remote-claude-1",
                "display_name": "Claude (Guest)",
                "meeting_id": ROOM,
                "participant_type": "remote",
                "joined_at": "2026-06-11T05:00:00+00:00",
            }
        ]
        payload = room_members_payload(
            self.output_root, [], meeting_id=ROOM, sessions=sessions, repository=self.repository
        )
        members = payload["members"]
        self.assertEqual(len(members), 1)
        self.assertEqual(members[0]["role"], "agent")
        self.assertEqual(members[0]["participant_type"], "remote")

    def test_collapse_does_not_rewrite_saved_store(self):
        self._saved_guest("guest-dddd01", "보관됨", updated_at="2026-06-11T01:00:00+00:00")
        self._saved_guest("guest-dddd02", "보관됨", updated_at="2026-06-11T02:00:00+00:00")
        room_members_payload(
            self.output_root, [], meeting_id=ROOM, sessions=[], repository=self.repository
        )
        saved = read_room_members(self.output_root, meeting_id=ROOM)
        self.assertEqual(len(saved), 2)


class RosterStreamSnapshotTests(unittest.TestCase):
    """The R6 roster SSE stream emits a frame only when the roster changes."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.output_root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        self.repository = RoomStore(self.output_root)
        self.addCleanup(self.repository.close)

    def test_roster_snapshot_signature_changes_only_with_roster(self):
        from agentsassemble.gui import _stream_snapshot_payload

        upsert_room_member(
            self.output_root,
            {"meeting_id": ROOM, "participant_id": "guest-1", "display_name": "사람"},
        )
        first = _stream_snapshot_payload(
            self.output_root, "roster", meeting_id=ROOM, repository=self.repository
        )
        self.assertEqual(first["stream"], "roster")
        self.assertEqual(len(first["members"]), 1)
        again = _stream_snapshot_payload(
            self.output_root, "roster", meeting_id=ROOM, repository=self.repository
        )
        self.assertEqual(first["payload_signature"], again["payload_signature"])

        set_room_member_muted(
            self.output_root, meeting_id=ROOM, participant_id="guest-1", muted=True
        )
        after_mute = _stream_snapshot_payload(
            self.output_root, "roster", meeting_id=ROOM, repository=self.repository
        )
        self.assertNotEqual(first["payload_signature"], after_mute["payload_signature"])
        self.assertTrue(after_mute["members"][0]["muted"])


if __name__ == "__main__":
    unittest.main()
