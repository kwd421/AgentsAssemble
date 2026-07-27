from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import cast
from unittest import TestCase

from agentsassemble.room_attention import (
    AttentionEvaluation,
    AttentionEvaluationConflict,
    AttentionLeaseConflict,
)
from agentsassemble.room.command_uow import (
    RoomCommandIdempotencyConflict,
    RoomCommandNotFinalized,
    RoomCommandUnitOfWork,
)
from agentsassemble.room.repository import RoomRepository


def _raise_at(actual: str, expected: str) -> None:
    if actual == expected:
        raise RuntimeError(expected)


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

    def test_room_ensure_creates_once_without_overwriting_existing_room(self) -> None:
        first = self.repository.ensure_room("ensured-room", label="Original")
        self.repository.update_room_settings(
            "ensured-room",
            {
                "label": "Customized",
                "topic": "Preserve this topic",
                "conversation_mode": "ambient",
            },
        )
        self.repository.set_room_status("ensured-room", "archived")
        room_before_second_ensure = self.repository.room("ensured-room")
        settings_before_second_ensure = self.repository.room_settings("ensured-room")
        events_before_second_ensure = self.repository.read_events("ensured-room")
        second = self.repository.ensure_room(
            "ensured-room",
            label="Replacement",
            status="active",
        )
        events_after_second_ensure = self.repository.read_events("ensured-room")

        case = self._test_case()
        case.assertEqual(first["label"], "Original")
        case.assertEqual(second, room_before_second_ensure)
        case.assertEqual(
            self.repository.room("ensured-room"),
            room_before_second_ensure,
        )
        case.assertEqual(
            self.repository.room_settings("ensured-room"),
            settings_before_second_ensure,
        )
        case.assertEqual(
            events_after_second_ensure,
            events_before_second_ensure,
        )

    def test_concurrent_room_ensure_keeps_the_first_committed_room(self) -> None:
        barrier = threading.Barrier(2)

        def ensure(label: str) -> dict[str, object]:
            barrier.wait()
            return self.repository.ensure_room("concurrent-ensure-room", label=label)

        with ThreadPoolExecutor(max_workers=2) as executor:
            first_future = executor.submit(ensure, "First candidate")
            second_future = executor.submit(ensure, "Second candidate")
            rooms = [first_future.result(), second_future.result()]

        case = self._test_case()
        case.assertEqual(len({str(room["label"]) for room in rooms}), 1)
        case.assertIn(
            str(rooms[0]["label"]),
            {"First candidate", "Second candidate"},
        )
        case.assertEqual(
            self.repository.room_settings("concurrent-ensure-room")["label"],
            rooms[0]["label"],
        )
        case.assertEqual(
            [
                event["type"]
                for event in self.repository.read_events("concurrent-ensure-room")
            ],
            ["room_created"],
        )

    def test_room_global_settings_are_backend_neutral_and_strict(self) -> None:
        self.repository.create_room("settings-room", label="Initial")

        defaults = self.repository.room_settings("settings-room")
        updated = self.repository.update_room_settings(
            "settings-room",
            {
                "label": "Renamed",
                "topic": "Repository-owned settings",
                "appearance": {"banner_preset": "midnight"},
                "conversation_mode": "ambient",
                "max_relay_turns": 4,
            },
        )

        case = self._test_case()
        case.assertEqual(defaults["label"], "Initial")
        case.assertEqual(defaults["conversation_mode"], "ordered")
        case.assertEqual(updated["label"], "Renamed")
        case.assertEqual(updated["appearance"]["banner_preset"], "midnight")
        case.assertEqual(updated["appearance"]["invite_scope"], "room")
        case.assertEqual(self.repository.room("settings-room")["label"], "Renamed")
        case.assertEqual(self.repository.room_settings("settings-room"), updated)

        with case.assertRaisesRegex(ValueError, "Unsupported conversation_mode"):
            self.repository.update_room_settings(
                "settings-room",
                {"conversation_mode": "free"},
            )
        with case.assertRaisesRegex(ValueError, "Unsupported room settings fields"):
            self.repository.update_room_settings(
                "settings-room",
                {"notifications": "mute"},
            )
        case.assertEqual(self.repository.room_settings("settings-room"), updated)

    def test_room_global_settings_roll_back_with_the_room_transaction(self) -> None:
        self.repository.create_room("settings-rollback", label="Before")

        with self._test_case().assertRaisesRegex(RuntimeError, "abort settings"):
            with self.repository.transaction("settings-rollback") as transaction:
                transaction.update_room_settings(
                    {
                        "label": "Must Roll Back",
                        "conversation_mode": "continuous",
                    }
                )
                raise RuntimeError("abort settings")

        case = self._test_case()
        case.assertEqual(self.repository.room("settings-rollback")["label"], "Before")
        case.assertEqual(
            self.repository.room_settings("settings-rollback")["conversation_mode"],
            "ordered",
        )

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

    def test_command_crash_windows_roll_back_and_retry_without_duplicates(self) -> None:
        failure_points = (
            "after_domain_mutation",
            "after_event_append",
            "after_session_update",
            "after_ack_construction",
            "after_command_result",
            "before_commit",
        )
        case = self._test_case()

        for index, failure_point in enumerate(failure_points, start=1):
            with case.subTest(failure_point=failure_point):
                room_id = f"command-crash-{index}"
                request_id = f"request-{index}"
                self.repository.create_room(room_id)
                baseline_seq = self.repository.latest_event_sequence(room_id)
                received: list[dict[str, object]] = []
                remove_listener = self.repository.add_event_listener(room_id, received.append)
                try:
                    with case.assertRaisesRegex(RuntimeError, failure_point):
                        self._run_command_transaction(
                            room_id,
                            request_id=request_id,
                            failure_point=failure_point,
                        )

                    case.assertEqual(received, [])
                    case.assertEqual(self.repository.participant(room_id, "agent-a"), {})
                    case.assertEqual(self.repository.session(room_id, "session-a"), {})
                    case.assertEqual(
                        self.repository.command_record(room_id, "host-a", request_id),
                        {},
                    )
                    case.assertEqual(self.repository.latest_event_sequence(room_id), baseline_seq)

                    committed = self._run_command_transaction(room_id, request_id=request_id)
                finally:
                    remove_listener()

                case.assertEqual([event["id"] for event in received], [committed["event"]["id"]])
                case.assertEqual(
                    self.repository.event_count(room_id, event_types=("message_final",)),
                    1,
                )
                case.assertEqual(
                    self.repository.command_record(room_id, "host-a", request_id)["result"],
                    committed["ack"],
                )

    def test_command_unit_of_work_owns_dedupe_ack_and_rollback(self) -> None:
        room_id = "command-uow"
        payload = {"content": "hello"}
        self.repository.create_room(room_id)
        received: list[dict[str, object]] = []
        remove_listener = self.repository.add_event_listener(room_id, received.append)
        try:
            with RoomCommandUnitOfWork(
                self.repository,
                room_id=room_id,
                principal_id="host-a",
                request_id="request-a",
                action="message.send",
                payload=payload,
            ) as unit:
                case = self._test_case()
                case.assertFalse(unit.deduplicated)
                participant, _created = unit.upsert_participant(
                    {
                        "participant_id": "agent-a",
                        "display_name": "Agent A",
                        "participant_type": "agent",
                        "status": "joined",
                    }
                )
                event = unit.append_event(
                    "message_final",
                    participant_id="agent-a",
                    participant_type="agent",
                    content="hello",
                )
                ack = unit.build_ack({"event": event, "participant": participant})
                unit.record_ack()

            with RoomCommandUnitOfWork(
                self.repository,
                room_id=room_id,
                principal_id="host-a",
                request_id="request-a",
                action="message.send",
                payload=payload,
            ) as duplicate:
                case.assertTrue(duplicate.deduplicated)
                duplicate_ack = duplicate.resolved_ack()

            with case.assertRaises(RoomCommandIdempotencyConflict):
                with RoomCommandUnitOfWork(
                    self.repository,
                    room_id=room_id,
                    principal_id="host-a",
                    request_id="request-a",
                    action="message.send",
                    payload={"content": "different"},
                ):
                    pass

            with case.assertRaises(RoomCommandNotFinalized):
                with RoomCommandUnitOfWork(
                    self.repository,
                    room_id=room_id,
                    principal_id="host-a",
                    request_id="request-unfinalized",
                    action="agent.configure",
                    payload={"display_name": "Must Roll Back"},
                ) as unfinished:
                    case.assertEqual(unfinished.participant("agent-a")["display_name"], "Agent A")
                    unfinished.update_participant_fields("agent-a", display_name="Must Roll Back")
        finally:
            remove_listener()

        case.assertEqual(ack["deduplicated"], False)
        case.assertEqual(duplicate_ack, {**ack, "deduplicated": True})
        case.assertEqual(self.repository.event_count(room_id, event_types=("message_final",)), 1)
        case.assertEqual([item["id"] for item in received], [event["id"]])
        case.assertEqual(self.repository.participant(room_id, "agent-a")["display_name"], "Agent A")
        case.assertEqual(
            self.repository.command_record(room_id, "host-a", "request-unfinalized"),
            {},
        )

    def _run_command_transaction(
        self,
        room_id: str,
        *,
        request_id: str,
        failure_point: str = "",
    ) -> dict[str, dict[str, object]]:
        with self.repository.transaction(room_id) as transaction:
            transaction.upsert_participant(
                {
                    "participant_id": "agent-a",
                    "display_name": "Agent A",
                    "participant_type": "agent",
                    "status": "joined",
                }
            )
            _raise_at(failure_point, "after_domain_mutation")
            event = transaction.append_event(
                "message_final",
                participant_id="agent-a",
                participant_type="agent",
                content="atomic reply",
            )
            _raise_at(failure_point, "after_event_append")
            transaction.upsert_session(
                {
                    "session_id": "session-a",
                    "participant_id": "agent-a",
                    "status": "attached",
                    "runtime_status": "idle",
                }
            )
            _raise_at(failure_point, "after_session_update")
            ack = {
                "op": "ack",
                "request_id": request_id,
                "accepted": True,
                "result": {"event": event},
            }
            _raise_at(failure_point, "after_ack_construction")
            transaction.record_command_result(
                request_id,
                ack,
                principal_id="host-a",
                action="message.send",
                payload_hash="payload-hash",
            )
            _raise_at(failure_point, "after_command_result")
            _raise_at(failure_point, "before_commit")
        return {"event": event, "ack": ack}

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

    def test_vote_query_returns_only_the_poll_and_its_visible_later_ballots(self) -> None:
        self.repository.create_room("vote-query")
        with self.repository.transaction("vote-query") as transaction:
            poll = transaction.append_event(
                "message_final",
                participant_id="host-a",
                participant_type="human",
                display_name="Host",
                message_kind="vote",
                vote_question="Choose",
                vote_options=["A", "B"],
            )
            self._test_case().assertEqual(
                transaction.event_by_id(str(poll["id"])),
                poll,
            )
            transaction.append_event(
                "message_final",
                participant_id="host-a",
                participant_type="human",
                content="unrelated",
                message_kind="message",
            )
            first_ballot = transaction.append_event(
                "message_final",
                participant_id="guest-a",
                participant_type="human",
                display_name="Guest A",
                message_kind="vote_cast",
                vote_id=poll["id"],
                vote_choice="A",
            )
            transaction.append_event(
                "message_final",
                participant_id="guest-hidden",
                participant_type="human",
                display_name="Hidden",
                message_kind="vote_cast",
                vote_id=poll["id"],
                vote_choice="B",
                visibility="legacy_hidden",
            )
            transaction.append_event(
                "message_final",
                participant_id="guest-other",
                participant_type="human",
                display_name="Other",
                message_kind="vote_cast",
                vote_id="another-vote",
                vote_choice="A",
            )
            latest_ballot = transaction.append_event(
                "message_final",
                participant_id="guest-a",
                participant_type="human",
                display_name="Guest A",
                message_kind="vote_cast",
                vote_id=poll["id"],
                vote_choice="B",
            )

        vote_events = self.repository.vote_events(
            "vote-query",
            str(poll["id"]),
        )

        self._test_case().assertEqual(
            [event["id"] for event in vote_events],
            [poll["id"], first_ballot["id"], latest_ballot["id"]],
        )
        self._test_case().assertEqual(
            self.repository.vote_events("vote-query", "missing-vote"),
            [],
        )

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
        with case.assertRaisesRegex(ValueError, "cannot be recreated"):
            self.repository.ensure_room("general")

    def test_deleted_room_tombstone_preserves_command_and_cleanup_state(self) -> None:
        self.repository.create_room("general")
        pending_ack = {
            "op": "ack",
            "request_id": "delete-request",
            "accepted": True,
            "action": "room.delete",
            "result": {"room_id": "general", "deleted": True},
            "deduplicated": False,
        }

        self.repository.delete_room(
            "general",
            reason="owner deleted server",
            tombstone={
                "principal_id": "browser:owner",
                "request_id": "delete-request",
                "action": "room.delete",
                "payload_hash": "payload-hash",
                "result": pending_ack,
            },
            cleanup_status="pending",
            room_name="Council",
        )
        pending = self.repository.deleted_room_record("general")

        self.repository.delete_room("general", reason="redundant delete")
        preserved = self.repository.deleted_room_record("general")

        case = self._test_case()
        case.assertEqual(pending["principal_id"], "browser:owner")
        case.assertEqual(pending["request_id"], "delete-request")
        case.assertEqual(pending["payload_hash"], "payload-hash")
        case.assertEqual(pending["cleanup_status"], "pending")
        case.assertEqual(pending["room_name"], "Council")
        case.assertEqual(pending["result"], pending_ack)
        case.assertEqual(preserved, pending)

        completed_ack = {
            **pending_ack,
            "result": {"room_id": "general", "deleted": True, "revoked_sessions": 2},
        }
        completed = self.repository.update_deleted_room_record(
            "general",
            result=completed_ack,
            cleanup_status="complete",
        )

        case.assertEqual(completed["cleanup_status"], "complete")
        case.assertEqual(completed["result"], completed_ack)

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
            state_in_transaction = transaction.attention_state("agent-a")
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
        case.assertEqual(state_in_transaction, state)
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

    def test_observed_checkpoint_is_monotonic_and_preserves_other_cursors(self) -> None:
        self.repository.create_room("general")
        with self.repository.transaction("general") as transaction:
            transaction.upsert_participant(
                {
                    "participant_id": "agent-a",
                    "display_name": "Agent A",
                    "participant_type": "agent",
                }
            )
            transaction.advance_attention_state("agent-a", attention_evaluated_seq=7)
            advanced = transaction.checkpoint_observed_seq("agent-a", 8)
        with self.repository.transaction("general") as transaction:
            equal = transaction.checkpoint_observed_seq("agent-a", 8)
            stale = transaction.checkpoint_observed_seq("agent-a", 3)
            latest = transaction.checkpoint_observed_seq("agent-a", 11)

        case = self._test_case()
        case.assertEqual(advanced.last_observed_seq, 8)
        case.assertEqual(equal.last_observed_seq, 8)
        case.assertEqual(stale.last_observed_seq, 8)
        case.assertEqual(latest.last_observed_seq, 11)
        case.assertEqual(latest.last_attention_evaluated_seq, 7)

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

    def test_selected_attention_job_has_one_durable_lease_lifecycle(self) -> None:
        self.repository.create_room("general")
        with self.repository.transaction("general") as transaction:
            transaction.upsert_participant(
                {
                    "participant_id": "agent-a",
                    "display_name": "Agent A",
                    "participant_type": "agent",
                }
            )
            job = transaction.record_attention_evaluation(
                AttentionEvaluation(
                    room_id="general",
                    source_event_id="event-9",
                    source_seq=9,
                    outcome="selected",
                    selected_participant_id="agent-a",
                    eligible_participant_ids=("agent-a",),
                    reasons=("direct_mention",),
                ),
                mode="active",
                status="pending",
            )
            first = transaction.claim_attention_job(
                job["job_id"],
                participant_id="agent-a",
                owner_id="worker-a",
                lease_seconds=30,
            )
            duplicate = transaction.claim_attention_job(
                job["job_id"],
                participant_id="agent-a",
                owner_id="worker-a",
                lease_seconds=30,
            )
            transaction_job = transaction.attention_job(job["job_id"])
            transaction_lease = transaction.attention_lease(first["lease_id"])

        case = self._test_case()
        case.assertEqual(duplicate["lease_id"], first["lease_id"])
        case.assertEqual(transaction_job["status"], "leased")
        case.assertEqual(transaction_lease["lease_id"], first["lease_id"])
        case.assertEqual(self.repository.attention_job("general", job["job_id"])["status"], "leased")
        case.assertEqual(
            [lease["lease_id"] for lease in self.repository.attention_leases("general", status="active")],
            [first["lease_id"]],
        )
        case.assertEqual(
            self.repository.attention_jobs("general", mode="active")[0]["status"],
            "leased",
        )
        with case.assertRaisesRegex(AttentionLeaseConflict, "already_leased"):
            with self.repository.transaction("general") as transaction:
                transaction.claim_attention_job(
                    job["job_id"],
                    participant_id="agent-a",
                    owner_id="worker-b",
                    lease_seconds=30,
                )

        with self.repository.transaction("general") as transaction:
            released = transaction.resolve_attention_lease(first["lease_id"], status="released")
            duplicate_release = transaction.resolve_attention_lease(
                first["lease_id"],
                status="released",
            )

        case.assertEqual(released["status"], "released")
        case.assertEqual(duplicate_release["status"], "released")
        case.assertEqual(
            self.repository.attention_lease("general", first["lease_id"])["status"],
            "released",
        )
        case.assertEqual(
            self.repository.attention_jobs("general", mode="active")[0]["status"],
            "completed",
        )

        with self.repository.transaction("general") as transaction:
            pending_job = transaction.record_attention_evaluation(
                AttentionEvaluation(
                    room_id="general",
                    source_event_id="event-cancel",
                    source_seq=11,
                    outcome="selected",
                    selected_participant_id="agent-a",
                    eligible_participant_ids=("agent-a",),
                    reasons=("direct_mention",),
                ),
                mode="active",
                status="pending",
            )
            cancelled_job = transaction.cancel_attention_job(pending_job["job_id"])
        case.assertEqual(cancelled_job["status"], "cancelled")
        case.assertEqual(
            self.repository.attention_job("general", pending_job["job_id"])["status"],
            "cancelled",
        )

    def test_expired_active_attention_lease_is_reclaimed_in_the_claim_transaction(self) -> None:
        self.repository.create_room("general")
        with self.repository.transaction("general") as transaction:
            transaction.upsert_participant(
                {
                    "participant_id": "agent-a",
                    "display_name": "Agent A",
                    "participant_type": "agent",
                }
            )
            job = transaction.record_attention_evaluation(
                AttentionEvaluation(
                    room_id="general",
                    source_event_id="event-expiry",
                    source_seq=10,
                    outcome="selected",
                    selected_participant_id="agent-a",
                    eligible_participant_ids=("agent-a",),
                    reasons=("direct_mention",),
                ),
                mode="active",
                status="pending",
            )
            first = transaction.claim_attention_job(
                job["job_id"],
                participant_id="agent-a",
                owner_id="worker-a",
                lease_seconds=1,
            )

        time.sleep(1.05)
        with self._test_case().assertRaisesRegex(RuntimeError, "rollback reclaimed lease"):
            with self.repository.transaction("general") as transaction:
                transaction.claim_attention_job(
                    job["job_id"],
                    participant_id="agent-a",
                    owner_id="worker-b",
                    lease_seconds=30,
                )
                raise RuntimeError("rollback reclaimed lease")

        case = self._test_case()
        case.assertEqual(
            self.repository.attention_lease("general", first["lease_id"])["status"],
            "active",
        )
        case.assertEqual(
            self.repository.attention_jobs("general", mode="active")[0]["status"],
            "leased",
        )

        with self.repository.transaction("general") as transaction:
            reclaimed = transaction.claim_attention_job(
                job["job_id"],
                participant_id="agent-a",
                owner_id="worker-b",
                lease_seconds=30,
            )

        case.assertNotEqual(reclaimed["lease_id"], first["lease_id"])
        case.assertEqual(
            self.repository.attention_lease("general", first["lease_id"])["status"],
            "expired",
        )
        case.assertEqual(reclaimed["status"], "active")
        case.assertEqual(reclaimed["owner_id"], "worker-b")
        case.assertEqual(
            self.repository.attention_jobs("general", mode="active")[0]["status"],
            "leased",
        )

    def test_selected_attention_queue_rolls_back_as_one_repository_unit(self) -> None:
        self.repository.create_room("general")
        source = self.repository.append_event(
            "general",
            "message_final",
            actor_id="human",
            actor_type="human",
            content="continue",
        )
        with self.repository.transaction("general") as transaction:
            transaction.upsert_participant(
                {
                    "participant_id": "agent-a",
                    "display_name": "Agent A",
                    "participant_type": "agent",
                }
            )
            transaction.upsert_session(
                {
                    "session_id": "agent-a",
                    "participant_id": "agent-a",
                    "status": "attached",
                    "runtime_status": "idle",
                    "pending_event_ids": [],
                }
            )
        evaluation = AttentionEvaluation(
            room_id="general",
            source_event_id=str(source["id"]),
            source_seq=int(source["seq"]),
            outcome="selected",
            selected_participant_id="agent-a",
            eligible_participant_ids=("agent-a",),
            reasons=("ambient_human_message",),
        )

        with self._test_case().assertRaisesRegex(RuntimeError, "rollback selected queue"):
            with self.repository.transaction("general") as transaction:
                transaction.advance_attention_state(
                    "agent-a",
                    attention_evaluated_seq=int(source["seq"]),
                )
                job = transaction.record_attention_evaluation(
                    evaluation,
                    mode="active",
                    status="pending",
                )
                lease = transaction.claim_attention_job(
                    job["job_id"],
                    participant_id="agent-a",
                    owner_id="worker-a",
                    lease_seconds=30,
                )
                transaction.update_session_fields(
                    "agent-a",
                    pending_event_ids=[source["id"]],
                    pending_attention_job_id=job["job_id"],
                    pending_attention_lease_id=lease["lease_id"],
                    pending_attention_source_event_id=source["id"],
                )
                raise RuntimeError("rollback selected queue")

        case = self._test_case()
        case.assertEqual(self.repository.attention_jobs("general", mode="active"), [])
        case.assertEqual(
            self.repository.attention_state("general", "agent-a").last_attention_evaluated_seq,
            0,
        )
        case.assertEqual(self.repository.session("general", "agent-a")["pending_event_ids"], [])

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
