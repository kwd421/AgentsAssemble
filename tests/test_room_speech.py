from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from agentsassemble.room_speech import (
    ActorIdentity,
    GovernedLobbySayRejected,
    SERVER_AUTO_CHAIN_DEPTH_LIMIT,
    SERVER_SPEECH_BURST_LIMIT,
    ensure_lobby_say_allowed,
    governed_official_reply,
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

    def test_rejects_over_depth_auto_reply_before_append(self):
        appended: list[dict[str, object]] = []

        with tempfile.TemporaryDirectory() as temp_dir:
            with self.assertRaises(GovernedLobbySayRejected) as rejected:
                governed_lobby_say(
                    Path(temp_dir),
                    identity=ActorIdentity(agent_id="agent-a", display_name="Agent A"),
                    payload={
                        "message": "too deep",
                        "source_event_id": "src1",
                        "auto_chain_depth": SERVER_AUTO_CHAIN_DEPTH_LIMIT + 1,
                    },
                    append_lobby_event=lambda root, payload, *, allow_flow_metadata: appended.append(payload) or payload,
                    public_lobby_allows_room_scope=lambda payload: False,
                    is_muted=lambda root, meeting_id, agent_id: False,
                )

        self.assertEqual(rejected.exception.category, "chain_depth")
        self.assertEqual(appended, [])

    def test_rejects_actor_flood_before_append(self):
        appended: list[dict[str, object]] = []

        def append_lobby_event(
            root: Path,
            payload: dict[str, object],
            *,
            allow_flow_metadata: bool,
        ) -> dict[str, object]:
            appended.append(payload)
            return payload

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            identity = ActorIdentity(agent_id="agent-flood", display_name="Flood Bot")
            for index in range(SERVER_SPEECH_BURST_LIMIT):
                governed_lobby_say(
                    root,
                    identity=identity,
                    payload={"message": f"burst {index}"},
                    append_lobby_event=append_lobby_event,
                    public_lobby_allows_room_scope=lambda payload: False,
                    is_muted=lambda root, meeting_id, agent_id: False,
                    now_monotonic=lambda: 100.0,
                )
            with self.assertRaises(GovernedLobbySayRejected) as rejected:
                governed_lobby_say(
                    root,
                    identity=identity,
                    payload={"message": "one too many"},
                    append_lobby_event=append_lobby_event,
                    public_lobby_allows_room_scope=lambda payload: False,
                    is_muted=lambda root, meeting_id, agent_id: False,
                    now_monotonic=lambda: 100.0,
                )

        self.assertEqual(rejected.exception.category, "rate_limited")
        self.assertEqual(len(appended), SERVER_SPEECH_BURST_LIMIT)

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

    def test_lobby_say_can_mark_live_agent_endpoint(self):
        captured: dict[str, object] = {}

        def append_lobby_event(
            root: Path,
            payload: dict[str, object],
            *,
            live_agent_endpoint: bool,
            allow_flow_metadata: bool,
        ) -> dict[str, object]:
            captured.update(payload)
            captured["live_agent_endpoint"] = live_agent_endpoint
            captured["allow_flow_metadata"] = allow_flow_metadata
            return {"id": "evt-live", **payload, "live_agent_endpoint": live_agent_endpoint}

        with tempfile.TemporaryDirectory() as temp_dir:
            event = governed_lobby_say(
                Path(temp_dir),
                identity=ActorIdentity(
                    agent_id="agent-a",
                    display_name="Agent A",
                    participant_type="live_session",
                    meeting_id="room-1",
                ),
                payload={
                    "message": "live reply",
                    "kind": "ready",
                    "source_event_id": "src1",
                    "auto_chain_depth": 3,
                    "flow_id": "flow-1",
                    "flow_action": "challenge",
                },
                append_lobby_event=append_lobby_event,
                public_lobby_allows_room_scope=lambda payload: False,
                is_muted=lambda root, meeting_id, agent_id: False,
                side="other-agent",
                live_agent_endpoint=True,
                allow_flow_metadata=True,
                allowed_kinds={"message", "ready"},
            )

        self.assertEqual(event["name"], "Agent A")
        self.assertEqual(event["actor_id"], "agent-a")
        self.assertEqual(event["side"], "other-agent")
        self.assertEqual(event["kind"], "ready")
        self.assertEqual(event["flow_meeting_id"], "room-1")
        self.assertEqual(event["flow_id"], "flow-1")
        self.assertEqual(event["flow_action"], "challenge")
        self.assertTrue(captured["live_agent_endpoint"])
        self.assertTrue(captured["allow_flow_metadata"])

    def test_official_reply_builds_official_event_payload(self):
        captured: dict[str, object] = {}

        def append_live_event(meeting_dir: Path, payload: dict[str, object]) -> dict[str, object]:
            captured["meeting_dir"] = str(meeting_dir)
            captured.update(payload)
            return {"id": "live1", **payload}

        with tempfile.TemporaryDirectory() as temp_dir:
            meeting_dir = Path(temp_dir) / "meetings" / "m1"
            event = governed_official_reply(
                meeting_dir,
                identity=ActorIdentity(agent_id="agent-a", display_name="Agent A", participant_type="live_session"),
                meeting_id="m1",
                source_event_id="turn-request-1",
                role_id="architect",
                display_name="Architect A",
                content="공식 답변",
                turn_id="turn-1",
                turn_index=2,
                append_live_event=append_live_event,
            )

        self.assertEqual(captured["meeting_dir"], str(meeting_dir))
        self.assertEqual(event["kind"], "message")
        self.assertEqual(event["meeting_id"], "m1")
        self.assertEqual(event["actor_id"], "agent-a")
        self.assertEqual(event["target_agent_id"], "agent-a")
        self.assertEqual(event["source_event_id"], "turn-request-1")
        self.assertEqual(event["role_id"], "architect")
        self.assertEqual(event["display_name"], "Architect A")
        self.assertEqual(event["content"], "공식 답변")
        self.assertEqual(event["turn_id"], "turn-1")
        self.assertEqual(event["turn_index"], 2)
        self.assertEqual(event["engagement_mode"], "moderator_called")

    def test_official_reply_marks_review_checkpoint_nonofficial(self):
        captured: dict[str, object] = {}

        def append_live_event(meeting_dir: Path, payload: dict[str, object]) -> dict[str, object]:
            captured.update(payload)
            return {"id": "review1", **payload}

        with tempfile.TemporaryDirectory() as temp_dir:
            event = governed_official_reply(
                Path(temp_dir),
                identity=ActorIdentity(agent_id="agent-a", display_name="Agent A", participant_type="live_session"),
                meeting_id="m1",
                source_event_id="turn-request-1",
                role_id="reviewer",
                display_name="Reviewer A",
                content="리뷰 답변",
                review_checkpoint_id="checkpoint-1",
                append_live_event=append_live_event,
            )

        self.assertEqual(event["review_checkpoint_id"], "checkpoint-1")
        self.assertEqual(event["channel"], "review")
        self.assertFalse(event["official_record"])


if __name__ == "__main__":
    unittest.main()
