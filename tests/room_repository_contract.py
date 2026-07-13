from __future__ import annotations

from typing import cast
from unittest import TestCase

from agentsassemble.room_repository import RoomRepository


class RoomRepositoryContractMixin:
    """Backend-neutral behavior required from every room repository."""

    repository: RoomRepository

    def _test_case(self) -> TestCase:
        return cast(TestCase, self)

    def test_room_creation_is_idempotent_and_emits_one_creation_event(self) -> None:
        first = self.repository.create_room("general", label="General")
        second = self.repository.create_room("general", label="General renamed")
        events = self.repository.read_events("general")

        case = self._test_case()
        case.assertEqual(first["room_id"], "general")
        case.assertEqual(second["room_id"], "general")
        case.assertEqual(second["label"], "General renamed")
        case.assertEqual([event["type"] for event in events], ["room_created"])

    def test_transaction_commits_state_before_publishing_events(self) -> None:
        self.repository.create_room("general")
        received: list[dict[str, object]] = []
        remove_listener = self.repository.add_event_listener("general", received.append)
        try:
            with self.repository.transaction("general") as transaction:
                participant, participant_created = transaction.upsert_participant(
                    {
                        "participant_id": "agent-a",
                        "display_name": "Agent A",
                        "participant_type": "agent",
                        "status": "joined",
                    }
                )
                session, session_created = transaction.upsert_session(
                    {
                        "session_id": "session-a",
                        "participant_id": "agent-a",
                        "status": "attached",
                        "runtime_status": "idle",
                    }
                )
                event = transaction.append_event(
                    "message_final",
                    participant_id="agent-a",
                    participant_type="agent",
                    content="hello",
                )
                command = transaction.record_command_result(
                    "request-a",
                    {"accepted": True},
                    principal_id="host-a",
                    action="message.send",
                    payload_hash="hash-a",
                )
                self._test_case().assertEqual(received, [])
        finally:
            remove_listener()

        case = self._test_case()
        case.assertTrue(participant_created)
        case.assertTrue(session_created)
        case.assertEqual(participant["display_name"], "Agent A")
        case.assertEqual(session["runtime_status"], "idle")
        case.assertTrue(command["accepted"])
        case.assertEqual([item["id"] for item in received], [event["id"]])
        case.assertEqual(self.repository.participant("general", "agent-a")["display_name"], "Agent A")
        case.assertEqual(self.repository.session("general", "session-a")["runtime_status"], "idle")
        case.assertTrue(
            self.repository.command_record("general", "host-a", "request-a")["result"]["accepted"]
        )

    def test_transaction_rollback_leaves_no_state_event_or_sequence_gap(self) -> None:
        self.repository.create_room("general")
        baseline_sequence = self.repository.latest_event_sequence("general")
        received: list[dict[str, object]] = []
        remove_listener = self.repository.add_event_listener("general", received.append)
        try:
            with self._test_case().assertRaisesRegex(RuntimeError, "abort transaction"):
                with self.repository.transaction("general") as transaction:
                    transaction.upsert_participant(
                        {
                            "participant_id": "rolled-back-agent",
                            "display_name": "Rolled Back",
                            "participant_type": "agent",
                        }
                    )
                    transaction.upsert_session(
                        {
                            "session_id": "rolled-back-session",
                            "participant_id": "rolled-back-agent",
                            "status": "attached",
                        }
                    )
                    transaction.append_event("message_final", content="must disappear")
                    transaction.record_command_result(
                        "rolled-back-request",
                        {"accepted": True},
                        principal_id="host-a",
                    )
                    raise RuntimeError("abort transaction")
        finally:
            remove_listener()

        existing_room = self.repository.create_room("general")
        with self.repository.transaction("general") as transaction:
            event = transaction.append_event("system", content="after rollback")

        case = self._test_case()
        case.assertEqual(existing_room["room_id"], "general")
        case.assertEqual(self.repository.participant("general", "rolled-back-agent"), {})
        case.assertEqual(self.repository.session("general", "rolled-back-session"), {})
        case.assertEqual(self.repository.command_record("general", "host-a", "rolled-back-request"), {})
        case.assertEqual(received, [])
        case.assertEqual(event["seq"], baseline_sequence + 1)

    def test_identity_updates_do_not_rewrite_past_event_actor(self) -> None:
        self.repository.create_room("general")
        with self.repository.transaction("general") as transaction:
            transaction.upsert_participant(
                {
                    "participant_id": "agent-a",
                    "display_name": "Old Name",
                    "participant_type": "agent",
                }
            )
            event = transaction.append_event(
                "message_final",
                participant_id="agent-a",
                participant_type="agent",
                content="hello",
            )
        with self.repository.transaction("general") as transaction:
            transaction.update_participant_fields("agent-a", display_name="Current Name")

        stored_event = self.repository.read_events("general", after_seq=int(event["seq"]) - 1)[0]
        case = self._test_case()
        case.assertEqual(self.repository.participant("general", "agent-a")["display_name"], "Current Name")
        case.assertEqual(stored_event["actor"]["participant_id"], "agent-a")
        case.assertNotIn("display_name", stored_event["actor"])

    def test_session_pending_and_inflight_state_survives_reopen_contract(self) -> None:
        self.repository.create_room("general")
        with self.repository.transaction("general") as transaction:
            transaction.upsert_session(
                {
                    "session_id": "session-a",
                    "participant_id": "agent-a",
                    "status": "attached",
                    "runtime_status": "busy",
                    "pending_event_ids": ["event-2"],
                    "inflight_event_ids": ["event-1"],
                    "last_seen_seq": 7,
                }
            )
        session = self.repository.session("general", "session-a")

        case = self._test_case()
        case.assertEqual(session["pending_event_ids"], ["event-2"])
        case.assertEqual(session["inflight_event_ids"], ["event-1"])
        case.assertEqual(session["last_seen_seq"], 7)

    def test_command_dedupe_is_scoped_by_principal(self) -> None:
        self.repository.create_room("general")
        with self.repository.transaction("general") as transaction:
            first = transaction.record_command_result(
                "same-request",
                {"value": "first"},
                principal_id="principal-a",
            )
            duplicate = transaction.record_command_result(
                "same-request",
                {"value": "ignored"},
                principal_id="principal-a",
            )
            other_principal = transaction.record_command_result(
                "same-request",
                {"value": "second"},
                principal_id="principal-b",
            )

        case = self._test_case()
        case.assertEqual(first, duplicate)
        case.assertEqual(other_principal["value"], "second")
        case.assertEqual(
            self.repository.command_record("general", "principal-a", "same-request")["result"]["value"],
            "first",
        )
        case.assertEqual(
            self.repository.command_record("general", "principal-b", "same-request")["result"]["value"],
            "second",
        )

    def test_cursor_replay_and_delete_tombstone_are_durable_contracts(self) -> None:
        self.repository.create_room("general")
        with self.repository.transaction("general") as transaction:
            first = transaction.append_event("message_final", content="first")
            second = transaction.append_event("message_final", content="second")
            third = transaction.append_event("message_final", content="third")

        replay = self.repository.read_events("general", after_seq=int(first["seq"]))
        deleted = self.repository.delete_room("general", reason="contract test")

        case = self._test_case()
        case.assertEqual([event["id"] for event in replay], [second["id"], third["id"]])
        case.assertTrue(deleted)
        case.assertEqual(self.repository.room("general"), {})
        case.assertTrue(self.repository.room_is_deleted("general"))
        with case.assertRaisesRegex(ValueError, "cannot be recreated"):
            self.repository.create_room("general")
