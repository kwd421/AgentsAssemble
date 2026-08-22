import json
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path

from agentsassemble.providers.agent_bridge import RoomAgentBridge
from agentsassemble.providers.room_portal import RoomPortal
from tests.test_room_agent_bridge import (
    FakeClient,
    FakeRuntime,
    RoomPortalRuntime,
    _wait_for,
)


class RoomPortalCollaborationTests(unittest.TestCase):
    def test_vote_creator_agent_can_stage_an_early_close(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            portal = RoomPortal(Path(temp_dir) / "portal", participant_id="codex")
            portal.prepare()
            portal.ingest_frame(
                {
                    "room_settings": {"tool_mode": "chat"},
                    "stream": "room_events",
                    "events": [
                        {
                            "id": "vote-1",
                            "seq": 1,
                            "type": "message_final",
                            "participant_id": "codex",
                            "message_kind": "vote",
                            "vote_question": "Continue?",
                            "vote_options": ["Yes", "No"],
                        }
                    ],
                }
            )
            portal.begin_observation("close-vote", input_up_to_seq=1)

            receipt = portal.close_vote("vote-1")
            publication = portal.consume_publication_result("close-vote")

        self.assertEqual(receipt, {"queued": True, "vote_id": "vote-1"})
        self.assertEqual(publication.message_kind, "vote_close")
        self.assertEqual(publication.vote_id, "vote-1")

    def test_room_portal_stages_an_explicit_vote_withdrawal(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            portal = RoomPortal(Path(temp_dir) / "portal", participant_id="codex")
            portal.prepare()
            portal.ingest_frame(
                {
                    "room_settings": {"tool_mode": "chat"},
                    "stream": "room_events",
                    "events": [
                        {
                            "id": "vote-1",
                            "seq": 1,
                            "type": "message_final",
                            "participant_id": "host-id",
                            "message_kind": "vote",
                            "vote_question": "Continue?",
                            "vote_options": ["Yes", "No"],
                        }
                    ],
                }
            )
            portal.begin_observation("withdraw-vote", input_up_to_seq=1)

            receipt = portal.withdraw_vote("vote-1")
            publication = portal.consume_publication_result("withdraw-vote")

        self.assertEqual(receipt, {"queued": True, "vote_id": "vote-1"})
        self.assertEqual(publication.message_kind, "vote_withdraw")
        self.assertEqual(publication.vote_id, "vote-1")
        self.assertEqual(publication.vote_choice, "")

    def test_terminal_helper_discovers_people_and_casts_a_structured_ballot(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            portal = RoomPortal(Path(temp_dir) / "portal", participant_id="gemini")
            portal.prepare()
            portal.ingest_frame(
                {
                    "room_settings": {"tool_mode": "chat"},
                    "participants": [
                        {
                            "participant_id": "host-id",
                            "participant_type": "human",
                            "display_name": "Host",
                            "role": "host",
                        },
                        {
                            "participant_id": "gemini",
                            "participant_type": "agent",
                            "display_name": "Gemini",
                            "role": "agent",
                        },
                    ],
                    "stream": "room_events",
                    "events": [
                        {
                            "id": "vote-1",
                            "seq": 1,
                            "type": "message_final",
                            "participant_id": "host-id",
                            "participant_type": "human",
                            "display_name": "Host",
                            "message_kind": "vote",
                            "vote_question": "Continue?",
                            "vote_options": ["Yes", "No"],
                        },
                        {
                            "id": "ballot-1",
                            "seq": 2,
                            "type": "message_final",
                            "participant_id": "host-id",
                            "participant_type": "human",
                            "display_name": "Host",
                            "message_kind": "vote_cast",
                            "vote_id": "vote-1",
                            "vote_choice": "Yes",
                        },
                        {
                            "id": "ballot-2",
                            "seq": 3,
                            "type": "message_final",
                            "participant_id": "gemini",
                            "participant_type": "agent",
                            "display_name": "Gemini",
                            "message_kind": "vote_cast",
                            "vote_id": "vote-1",
                            "vote_choice": "No",
                        },
                    ],
                }
            )
            portal.begin_observation("terminal-vote", input_up_to_seq=3)

            participants = subprocess.run(
                [str(portal.helper_path), "participants"],
                check=True,
                capture_output=True,
                text=True,
            )
            ballot = subprocess.run(
                [str(portal.helper_path), "vote-cast", "vote-1", "2"],
                check=True,
                capture_output=True,
                text=True,
            )
            summary = subprocess.run(
                [str(portal.helper_path), "vote-summary", "vote-1"],
                check=True,
                capture_output=True,
                text=True,
            )
            publication = portal.consume_publication_result("terminal-vote")

        listed = json.loads(participants.stdout)
        self.assertEqual(
            [(item["participant_id"], item["display_name"]) for item in listed],
            [("gemini", "Gemini"), ("host-id", "Host")],
        )
        self.assertEqual(json.loads(ballot.stdout)["choice"], "No")
        terminal_summary = json.loads(summary.stdout)
        self.assertEqual(terminal_summary["tallies"], {"Yes": 1, "No": 1})
        self.assertEqual(terminal_summary["own_choice"], "No")
        self.assertNotIn("voters", terminal_summary)
        self.assertEqual(
            (publication.message_kind, publication.vote_id, publication.vote_choice),
            ("vote_cast", "vote-1", "No"),
        )

    def test_bridge_reports_structured_vote_and_explicit_decline(self):
        class CollaborationRuntime(RoomPortalRuntime):
            created_vote: dict[str, object] | None = None

            def send(self, text):
                FakeRuntime.send(self, text)
                self.observed_views.append(self.portal.read_discussion())
                if len(self.observed_views) == 1:
                    self.created_vote = self.portal.create_vote(
                        "Deploy?",
                        ["Yes", "No"],
                        duration_seconds=300,
                    )
                else:
                    self.portal.decline_to_speak("duplicate")

        with tempfile.TemporaryDirectory() as temp_dir:
            portal = RoomPortal(Path(temp_dir) / "portal", participant_id="codex")
            portal.prepare()
            client = FakeClient()
            runtime = CollaborationRuntime(portal, [])
            bridge = RoomAgentBridge(
                client,
                runtime,
                room_id="general",
                participant_id="codex",
                session_id="codex",
                receive_sleep_seconds=0.005,
                room_portal=portal,
            )
            thread = threading.Thread(target=bridge.run, daemon=True)
            thread.start()
            _wait_for(lambda: any(action == "bridge.ready" for action, _, _ in client.commands))

            for turn_id, seq in (("vote-wake", 1), ("decline-wake", 2)):
                with client._lock:
                    client.messages.extend(
                        [
                            {
                                "op": "event",
                                "stream": "room_events",
                                "events": [
                                    {
                                        "id": f"event-{seq}",
                                        "seq": seq,
                                        "type": "message_final",
                                        "participant_id": "host",
                                        "participant_type": "human",
                                        "display_name": "Host",
                                        "content": "Review this",
                                    }
                                ],
                            },
                            {
                                "op": "room.wake",
                                "room_id": "general",
                                "participant_id": "codex",
                                "session_id": "codex",
                                "turn_id": turn_id,
                                "source_event_id": f"event-{seq}",
                                "input_up_to_seq": seq,
                                "attachment_ids": [],
                                "observation_kind": "ordered_floor",
                                "publication_mode": "explicit_room_portal",
                                "timeout_seconds": 2,
                            },
                        ]
                    )
                expected = "message.final" if seq == 1 else "turn.decline"
                _wait_for(
                    lambda: any(
                        action == expected and payload.get("turn_id") == turn_id
                        for action, payload, _ in client.commands
                    )
                )

            with client._lock:
                client.messages.append({"op": "agent.control", "action": "stop"})
            thread.join(timeout=2)

        vote = next(
            payload
            for action, payload, _ in client.commands
            if action == "message.final" and payload.get("turn_id") == "vote-wake"
        )
        decline = next(
            payload
            for action, payload, _ in client.commands
            if action == "turn.decline" and payload.get("turn_id") == "decline-wake"
        )
        self.assertNotIn("kind", vote)
        self.assertNotIn("vote_options", vote)
        self.assertEqual(
            (
                runtime.created_vote["question"],
                runtime.created_vote["options"],
                runtime.created_vote["duration_seconds"],
            ),
            ("Deploy?", ["Yes", "No"], 300),
        )
        self.assertEqual(decline["reason_code"], "duplicate")


class CanonicalAgentVoteTests(unittest.TestCase):
    def setUp(self):
        from tests.test_room_realtime import RoomRealtimeControllerTests

        self.room = RoomRealtimeControllerTests(
            methodName="test_controller_accepts_one_repository_instance_as_room_authority"
        )
        self.room.setUp()

    def tearDown(self):
        self.room.tearDown()

    def test_agent_vote_ballot_and_close_reach_the_canonical_tally(self):
        self.room._command("agent-start", "agent.start", {"agent_id": "codex"})
        identity, channel = self.room._connect_bridge("codex")
        channel.drain()
        self.room.controller.store.update_room_settings(
            "general",
            {"conversation_mode": "ordered"},
        )
        self.room._command(
            "agent-vote-request",
            "message.send",
            {"content": "Create a two-option poll"},
        )
        vote_wake = next(
            message for message in channel.drain() if message.get("op") == "room.wake"
        )
        poll = self.room._publish_observation_final(
            "agent-vote-final",
            {
                "turn_id": vote_wake["turn_id"],
                "kind": "vote",
                "vote_question": "Patch size?",
                "vote_options": ["Small", "Large"],
                "vote_duration_seconds": 300,
                "observed_through_seq": vote_wake["input_up_to_seq"],
            },
            identity,
        )["result"]["event"]
        self.room._command(
            "agent-ballot-request",
            "message.send",
            {"content": "Cast option 2 in the latest poll"},
        )
        ballot_wake = next(
            message for message in channel.drain() if message.get("op") == "room.wake"
        )
        ballot = self.room._publish_observation_final(
            "agent-ballot-final",
            {
                "turn_id": ballot_wake["turn_id"],
                "kind": "vote_cast",
                "vote_id": poll["id"],
                "vote_choice": "2",
                "observed_through_seq": ballot_wake["input_up_to_seq"],
            },
            identity,
        )["result"]["event"]
        summary = self.room._command(
            "agent-vote-summary",
            "room.vote.summary",
            {"vote_id": poll["id"]},
        )["result"]
        self.room._command(
            "agent-close-request",
            "message.send",
            {"content": "Close the latest poll"},
        )
        close_wake = next(
            message for message in channel.drain() if message.get("op") == "room.wake"
        )
        closed = self.room._publish_observation_final(
            "agent-close-final",
            {
                "turn_id": close_wake["turn_id"],
                "kind": "vote_close",
                "vote_id": poll["id"],
                "observed_through_seq": close_wake["input_up_to_seq"],
            },
            identity,
        )["result"]["event"]
        closed_summary = self.room._command(
            "agent-closed-summary",
            "room.vote.summary",
            {"vote_id": poll["id"]},
        )["result"]

        self.assertEqual(
            (poll["participant_type"], poll["message_kind"], poll["vote_options"]),
            ("agent", "vote", ["Small", "Large"]),
        )
        self.assertEqual(
            (ballot["message_kind"], ballot["vote_choice"], summary["tallies"]),
            ("vote_cast", "Large", {"Small": 0, "Large": 1}),
        )
        self.assertEqual(closed["message_kind"], "vote_close")
        self.assertTrue(closed_summary["closed"])
