from __future__ import annotations

from collections.abc import Iterable

from agentsassemble.room_attention_policy import evaluate_attention
from agentsassemble.room_repository import RoomRepository


class RoomAttentionCoordinator:
    """Persists model-free attention decisions without assigning provider turns."""

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
