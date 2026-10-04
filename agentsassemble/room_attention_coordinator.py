from __future__ import annotations

from collections.abc import Iterable

from agentsassemble.room.text import clean_room_text as clean_lobby_text
from agentsassemble.room_attention_policy import evaluate_ambient_attention, evaluate_attention
from agentsassemble.room.repository import RoomRepository, RoomTransaction


class RoomAttentionCoordinator:
    """Persists model-free attention decisions and exclusive active leases."""

    def __init__(self, repository: RoomRepository) -> None:
        self.repository = repository

    def evaluate_shadow(
        self,
        event: dict[str, object],
        *,
        candidate_ids: Iterable[str],
        eligible_ids: Iterable[str],
    ) -> dict[str, object]:
        candidates = tuple(dict.fromkeys(str(value) for value in candidate_ids if str(value)))
        evaluation = evaluate_attention(
            event,
            candidate_ids=candidates,
            eligible_ids=eligible_ids,
        )
        persisted_candidates = tuple(
            participant_id
            for participant_id in candidates
            if self.repository.participant(evaluation.room_id, participant_id)
        )
        with self.repository.transaction(evaluation.room_id) as transaction:
            job = transaction.record_attention_evaluation(
                evaluation,
                mode="shadow",
                status="completed",
            )
            for participant_id in persisted_candidates:
                transaction.advance_attention_state(
                    participant_id,
                    attention_evaluated_seq=evaluation.source_seq,
                )
        return job

    def evaluate_and_queue_active(
        self,
        event: dict[str, object],
        *,
        candidate_ids: Iterable[str],
        eligible_ids: Iterable[str],
        last_spoke_sequences: dict[str, int],
        owner_id: str,
        lease_seconds: float,
        relay_depth: int,
    ) -> dict[str, object]:
        candidates = tuple(dict.fromkeys(str(value) for value in candidate_ids if str(value)))
        evaluation = evaluate_ambient_attention(
            event,
            candidate_ids=candidates,
            eligible_ids=eligible_ids,
            last_spoke_sequences=last_spoke_sequences,
        )
        with self.repository.transaction(evaluation.room_id) as transaction:
            persisted_candidates = tuple(
                participant_id
                for participant_id in candidates
                if transaction.participant(participant_id)
            )
            job = transaction.record_attention_evaluation(
                evaluation,
                mode="active",
                status="pending" if evaluation.outcome == "selected" else "completed",
            )
            for participant_id in persisted_candidates:
                transaction.advance_attention_state(
                    participant_id,
                    attention_evaluated_seq=evaluation.source_seq,
                )
            lease = (
                transaction.claim_attention_job(
                    job["job_id"],
                    participant_id=evaluation.selected_participant_id,
                    owner_id=owner_id,
                    lease_seconds=lease_seconds,
                )
                if evaluation.outcome == "selected"
                else {}
            )
            queued_session = (
                self._queue_selected_session(
                    transaction,
                    evaluation.selected_participant_id,
                    source_event_id=evaluation.source_event_id,
                    job_id=clean_lobby_text(job.get("job_id"), limit=128),
                    lease_id=clean_lobby_text(lease.get("lease_id"), limit=128),
                    relay_depth=relay_depth,
                )
                if evaluation.outcome == "selected"
                else {}
            )
        return {"job": job, "lease": lease, "session": queued_session}

    @staticmethod
    def _queue_selected_session(
        transaction: RoomTransaction,
        participant_id: str,
        *,
        source_event_id: str,
        job_id: str,
        lease_id: str,
        relay_depth: int,
    ) -> dict[str, object]:
        session = transaction.session(participant_id)
        if not session:
            raise ValueError("Selected attention participant has no Agent Session.")
        clean_event_id = clean_lobby_text(source_event_id, limit=128)
        if not clean_event_id or not job_id or not lease_id:
            raise ValueError("Selected attention work requires source, job, and lease identifiers.")
        pending = list(dict.fromkeys(
            event_id
            for event_id in (
                *list(session.get("pending_event_ids") or []),
                clean_event_id,
            )
            if event_id
        ))
        return transaction.update_session_fields(
            str(session["session_id"]),
            pending_event_ids=pending,
            pending_relay_depth=max(
                int(session.get("pending_relay_depth") or 0),
                max(0, int(relay_depth)),
            ),
            pending_attention_job_id=job_id,
            pending_attention_lease_id=lease_id,
            pending_attention_source_event_id=clean_event_id,
        )
