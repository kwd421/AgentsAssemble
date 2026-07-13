from __future__ import annotations

from collections.abc import Iterable

from agentsassemble.room_attention_policy import evaluate_ambient_attention, evaluate_attention
from agentsassemble.room_repository import RoomRepository


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

    def evaluate_active(
        self,
        event: dict[str, object],
        *,
        candidate_ids: Iterable[str],
        eligible_ids: Iterable[str],
        last_spoke_sequences: dict[str, int],
        max_agent_relay_depth: int,
        owner_id: str,
        lease_seconds: float,
    ) -> dict[str, object]:
        candidates = tuple(dict.fromkeys(str(value) for value in candidate_ids if str(value)))
        evaluation = evaluate_ambient_attention(
            event,
            candidate_ids=candidates,
            eligible_ids=eligible_ids,
            last_spoke_sequences=last_spoke_sequences,
            max_agent_relay_depth=max_agent_relay_depth,
        )
        persisted_candidates = tuple(
            participant_id
            for participant_id in candidates
            if self.repository.participant(evaluation.room_id, participant_id)
        )
        with self.repository.transaction(evaluation.room_id) as transaction:
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
        return {"job": job, "lease": lease}
