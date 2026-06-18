from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from agentsassemble.room_speech import (
    ActorIdentity,
    GovernedLobbySayRejected,
    ensure_lobby_say_allowed,
    governed_channel_say,
    governed_lobby_say,
)


class GovernedLobbySayTests(unittest.TestCase):
    def test_stamps_identity_and_preserves_safe_reply_metadata(self):
        captured: dict[str, object] = {}

        def append_lobby_event(
            root: Path,
            payload: dict[str, object],
            *,
            allow_flow_metadata: bool,
        ) -> dict[str, object]:
            captured.update(payload)
            captured["allow_flow_metadata"] = allow_flow_metadata
            return {"id": "evt1", **payload}

        with tempfile.TemporaryDirectory() as temp_dir:
            event = governed_lobby_say(
                Path(temp_dir),
                identity=ActorIdentity(
                    agent_id="guest-1",
                    display_name="Guest One",
                    participant_type="remote",
                    meeting_id="room-1",
                ),
                payload={
                    "message": "reply",
                    "name": "Spoofed",
                    "actor_id": "spoofed",
                    "actor_type": "human",
                    "side": "mine",
                    "kind": "deploy",
                    "source_event_id": "src1",
                    "auto_chain_depth": 2,
                },
                append_lobby_event=append_lobby_event,
                public_lobby_allows_room_scope=lambda payload: payload.get("flow_meeting_id") == "room-1",
                is_muted=lambda root, meeting_id, agent_id: False,
            )

        self.assertEqual(event["name"], "Guest One")
        self.assertEqual(event["actor_id"], "guest-1")
        self.assertEqual(event["actor_type"], "agent")
        self.assertEqual(event["side"], "other")
        self.assertEqual(event["kind"], "message")
        self.assertEqual(event["flow_meeting_id"], "room-1")
        self.assertEqual(event["source_event_id"], "src1")
        self.assertEqual(event["auto_chain_depth"], 2)
        self.assertTrue(captured["allow_flow_metadata"])

    def test_allows_vote_kinds_only(self):
        events: list[dict[str, object]] = []

        def append_lobby_event(
            root: Path,
            payload: dict[str, object],
            *,
            allow_flow_metadata: bool,
        ) -> dict[str, object]:
            events.append(payload)
            return payload

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            identity = ActorIdentity(agent_id="human-1", display_name="Human", participant_type="human")
            governed_lobby_say(
                root,
                identity=identity,
                payload={"message": "poll", "kind": "vote"},
                append_lobby_event=append_lobby_event,
                public_lobby_allows_room_scope=lambda payload: False,
                is_muted=lambda root, meeting_id, agent_id: False,
            )
            governed_lobby_say(
                root,
                identity=identity,
                payload={"message": "bad", "kind": "deploy"},
                append_lobby_event=append_lobby_event,
                public_lobby_allows_room_scope=lambda payload: False,
                is_muted=lambda root, meeting_id, agent_id: False,
            )

        self.assertEqual(events[0]["kind"], "vote")
        self.assertEqual(events[1]["kind"], "message")

    def test_rejects_read_only_and_muted_before_append(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            with self.assertRaises(GovernedLobbySayRejected) as read_only:
                ensure_lobby_say_allowed(
                    root,
                    ActorIdentity(agent_id="guest-1", display_name="Guest", invite_scope="read_only"),
                    is_muted=lambda root, meeting_id, agent_id: False,
                )
            self.assertEqual(read_only.exception.category, "read_only")

            with self.assertRaises(GovernedLobbySayRejected) as muted:
                ensure_lobby_say_allowed(
                    root,
                    ActorIdentity(agent_id="guest-1", display_name="Guest", meeting_id="room-1"),
                    is_muted=lambda root, meeting_id, agent_id: meeting_id == "room-1" and agent_id == "guest-1",
                )
            self.assertEqual(muted.exception.category, "muted")

    def test_ws_style_empty_message_rejection_is_opt_in(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            with self.assertRaises(GovernedLobbySayRejected) as empty:
                governed_lobby_say(
                    root,
                    identity=ActorIdentity(agent_id="guest-1", display_name="Guest"),
                    payload={"message": "   "},
                    append_lobby_event=lambda root, payload, *, allow_flow_metadata: payload,
                    public_lobby_allows_room_scope=lambda payload: False,
                    is_muted=lambda root, meeting_id, agent_id: False,
                    require_nonempty_message=True,
                )
            self.assertEqual(empty.exception.category, "empty")

    def test_channel_say_stamps_identity_and_appends_to_channel_path(self):
        captured: dict[str, object] = {}

        def append_channel_event(
            path: Path,
            payload: dict[str, object],
            *,
            allow_flow_metadata: bool,
        ) -> dict[str, object]:
            captured["path"] = str(path)
            captured.update(payload)
            captured["allow_flow_metadata"] = allow_flow_metadata
            return {"id": "chan1", **payload}

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            event = governed_channel_say(
                root,
                channel_path=root / "channel_c123.jsonl",
                identity=ActorIdentity(
                    agent_id="guest-1",
                    display_name="Guest One",
                    participant_type="remote",
                    meeting_id="room-1",
                ),
                payload={
                    "message": "채널 답변",
                    "name": "Spoofed",
                    "actor_id": "spoofed",
                    "side": "mine",
                    "kind": "deploy",
                },
                append_channel_event=append_channel_event,
                is_muted=lambda root, meeting_id, agent_id: False,
            )

        self.assertEqual(captured["path"], str(Path(temp_dir) / "channel_c123.jsonl"))
        self.assertEqual(event["message"], "채널 답변")
        self.assertEqual(event["name"], "Guest One")
        self.assertEqual(event["actor_id"], "guest-1")
        self.assertEqual(event["actor_type"], "agent")
        self.assertEqual(event["side"], "other")
        self.assertEqual(event["kind"], "message")
        self.assertEqual(event["flow_meeting_id"], "room-1")
        self.assertTrue(captured["allow_flow_metadata"])


if __name__ == "__main__":
    unittest.main()
