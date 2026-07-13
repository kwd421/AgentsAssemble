from __future__ import annotations

from typing import cast
from unittest import TestCase

from agentsassemble.room_attention import AttentionEvaluation, AttentionEvaluationConflict
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

    def test_attention_state_and_shadow_job_are_durable_and_idempotent(self) -> None:
        self.repository.create_room("general")
        with self.repository.transaction("general") as transaction:
            transaction.upsert_participant(
                {
                    "participant_id": "agent-a",
                    "display_name": "Agent A",
                    "participant_type": "agent",
                }
            )
            state = transaction.advance_attention_state(
                "agent-a",
                observed_seq=8,
                attention_evaluated_seq=7,
            )
            evaluation = AttentionEvaluation(
                room_id="general",
                source_event_id="event-7",
                source_seq=7,
                outcome="selected",
                selected_participant_id="agent-a",
                eligible_participant_ids=("agent-a",),
                reasons=("direct_mention",),
            )
            first_job = transaction.record_attention_evaluation(
                evaluation,
                mode="shadow",
                status="completed",
            )
        with self.repository.transaction("general") as transaction:
            duplicate_job = transaction.record_attention_evaluation(
                evaluation,
                mode="shadow",
                status="completed",
            )

        jobs = self.repository.attention_jobs("general", mode="shadow")
        case = self._test_case()
        case.assertEqual(state.last_observed_seq, 8)
        case.assertEqual(self.repository.attention_state("general", "agent-a"), state)
        case.assertEqual(duplicate_job["job_id"], first_job["job_id"])
        case.assertEqual([job["job_id"] for job in jobs], [first_job["job_id"]])

        conflicting = AttentionEvaluation(
            room_id="general",
            source_event_id="event-7",
            source_seq=7,
            outcome="silent",
            reasons=("cooldown",),
        )
        with case.assertRaisesRegex(AttentionEvaluationConflict, "attention_evaluation_conflict"):
            with self.repository.transaction("general") as transaction:
                transaction.record_attention_evaluation(
                    conflicting,
                    mode="shadow",
                    status="completed",
                )

    def test_attention_writes_roll_back_and_room_delete_cascades(self) -> None:
        self.repository.create_room("general")
        with self.repository.transaction("general") as transaction:
            transaction.upsert_participant(
                {
                    "participant_id": "agent-a",
                    "display_name": "Agent A",
                    "participant_type": "agent",
                }
            )
        evaluation = AttentionEvaluation(
            room_id="general",
            source_event_id="event-4",
            source_seq=4,
            outcome="silent",
            reasons=("no_attention_signal",),
        )
        with self._test_case().assertRaisesRegex(RuntimeError, "rollback attention"):
            with self.repository.transaction("general") as transaction:
                transaction.advance_attention_state(
                    "agent-a",
                    observed_seq=4,
                    attention_evaluated_seq=4,
                )
                transaction.record_attention_evaluation(
                    evaluation,
                    mode="shadow",
                    status="completed",
                )
                raise RuntimeError("rollback attention")

        case = self._test_case()
        case.assertEqual(self.repository.attention_state("general", "agent-a").last_observed_seq, 0)
        case.assertEqual(self.repository.attention_jobs("general"), [])

        with self.repository.transaction("general") as transaction:
            transaction.advance_attention_state(
                "agent-a",
                observed_seq=4,
                attention_evaluated_seq=4,
            )
            transaction.record_attention_evaluation(
                evaluation,
                mode="shadow",
                status="completed",
            )
        self.repository.delete_room("general", reason="attention cascade")

        case.assertEqual(self.repository.attention_jobs("general"), [])

    def test_participant_terminal_status_detaches_sessions_and_records_event(self) -> None:
        self.repository.create_room("general")
        self.repository.upsert_participant(
            "general",
            {
                "participant_id": "agent-a",
                "display_name": "Agent A",
                "participant_type": "agent",
            },
        )
        self.repository.upsert_session(
            "general",
            {
                "session_id": "session-a",
                "participant_id": "agent-a",
                "status": "attached",
                "runtime_status": "idle",
            },
        )

        participant = self.repository.set_participant_status(
            "general",
            "agent-a",
            "kicked",
            reason="contract test",
        )

        case = self._test_case()
        case.assertEqual(participant["status"], "kicked")
        case.assertEqual(self.repository.session("general", "session-a")["status"], "detached")
        case.assertEqual(self.repository.read_events("general")[-1]["type"], "participant_kicked")

    def test_archived_room_is_hidden_from_default_directory(self) -> None:
        self.repository.create_room("general", label="General")

        room = self.repository.set_room_status("general", "archived")

        case = self._test_case()
        case.assertEqual(room["status"], "archived")
        case.assertNotIn("general", [item["room_id"] for item in self.repository.list_rooms()])
        case.assertIn(
            "general",
            [item["room_id"] for item in self.repository.list_rooms(include_archived=True)],
        )
        case.assertEqual(self.repository.read_events("general")[-1]["type"], "room_archived")

    def test_event_queries_apply_visibility_type_actor_and_cursor_filters(self) -> None:
        self.repository.create_room("general")
        first = self.repository.append_event(
            "general",
            "message_final",
            participant_id="agent-a",
            participant_type="agent",
            content="first",
        )
        hidden = self.repository.append_event(
            "general",
            "message_final",
            participant_id="agent-a",
            participant_type="agent",
            content="hidden",
            visibility="legacy_hidden",
        )
        last = self.repository.append_event(
            "general",
            "system",
            participant_id="host-a",
            participant_type="human",
            content="last",
        )

        case = self._test_case()
        case.assertEqual(self.repository.event_by_id("general", hidden["id"]), {})
        case.assertEqual(
            self.repository.event_by_id("general", hidden["id"], include_hidden=True)["id"],
            hidden["id"],
        )
        case.assertEqual(
            [event["id"] for event in self.repository.read_events("general", after=first["id"])],
            [last["id"]],
        )
        case.assertEqual(
            [
                event["id"]
                for event in self.repository.read_events(
                    "general",
                    include_hidden=True,
                    event_types=("message_final",),
                    exclude_actor_id="host-a",
                )
            ],
            [first["id"], hidden["id"]],
        )
        case.assertEqual(self.repository.event_count("general"), 3)
        case.assertEqual(self.repository.event_count("general", include_hidden=True), 4)
        case.assertEqual(self.repository.event_sequence("general", last["id"]), last["seq"])
        case.assertEqual(self.repository.oldest_event_sequence("general"), 1)

    def test_media_event_keeps_internal_path_out_of_canonical_payload(self) -> None:
        self.repository.create_room("general")

        media = self.repository.attach_media(
            "general",
            filename="diagram.png",
            content_type="image/png",
            data=b"image-bytes",
            supported=True,
        )
        event = self.repository.read_events("general")[-1]

        case = self._test_case()
        case.assertEqual(media["size"], len(b"image-bytes"))
        case.assertTrue(str(media.get("path") or ""))
        case.assertEqual(event["type"], "media_attached")
        case.assertEqual(event["media"]["id"], media["id"])
        case.assertNotIn("path", event["media"])
