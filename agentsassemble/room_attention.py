from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Literal

from agentsassemble.meeting_events import clean_lobby_text


AttentionOutcome = Literal["selected", "eligible", "silent"]
AttentionJobStatus = Literal["pending", "leased", "completed", "cancelled"]
AttentionLeaseStatus = Literal["active", "released", "expired", "cancelled"]
WakeupStatus = Literal["scheduled", "claimed", "completed", "cancelled"]
ObligationStatus = Literal["open", "satisfied", "expired", "cancelled"]

ATTENTION_OUTCOMES = frozenset({"selected", "eligible", "silent"})
ATTENTION_JOB_STATUSES = frozenset({"pending", "leased", "completed", "cancelled"})
ATTENTION_LEASE_STATUSES = frozenset({"active", "released", "expired", "cancelled"})
WAKEUP_STATUSES = frozenset({"scheduled", "claimed", "completed", "cancelled"})
OBLIGATION_STATUSES = frozenset({"open", "satisfied", "expired", "cancelled"})


@dataclass(frozen=True)
class AgentAttentionState:
    """Independent durable cursors for observing, evaluating, syncing, and speaking."""

    room_id: str
    participant_id: str
    last_observed_seq: int = 0
    last_attention_evaluated_seq: int = 0
    last_provider_sync_seq: int = 0
    last_spoke_seq: int = 0

    def advance(
        self,
        *,
        observed_seq: int | None = None,
        attention_evaluated_seq: int | None = None,
        provider_sync_seq: int | None = None,
        spoke_seq: int | None = None,
    ) -> AgentAttentionState:
        updates = {
            "last_observed_seq": _monotonic_cursor(
                "last_observed_seq",
                self.last_observed_seq,
                observed_seq,
            ),
            "last_attention_evaluated_seq": _monotonic_cursor(
                "last_attention_evaluated_seq",
                self.last_attention_evaluated_seq,
                attention_evaluated_seq,
            ),
            "last_provider_sync_seq": _monotonic_cursor(
                "last_provider_sync_seq",
                self.last_provider_sync_seq,
                provider_sync_seq,
            ),
            "last_spoke_seq": _monotonic_cursor(
                "last_spoke_seq",
                self.last_spoke_seq,
                spoke_seq,
            ),
        }
        return replace(self, **updates)


@dataclass(frozen=True)
class AttentionEvaluation:
    """One model-free shadow decision for a committed room event."""

    room_id: str
    source_event_id: str
    source_seq: int
    outcome: AttentionOutcome
    selected_participant_id: str = ""
    eligible_participant_ids: tuple[str, ...] = ()
    reasons: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.outcome not in ATTENTION_OUTCOMES:
            raise ValueError(f"Unsupported attention outcome: {self.outcome}")
        if self.source_seq <= 0:
            raise ValueError("source_seq must be positive.")
        if not clean_lobby_text(self.room_id, limit=128):
            raise ValueError("room_id is required.")
        if not clean_lobby_text(self.source_event_id, limit=128):
            raise ValueError("source_event_id is required.")
        selected = clean_lobby_text(self.selected_participant_id, limit=128)
        eligible = tuple(
            participant_id
            for participant_id in (
                clean_lobby_text(value, limit=128) for value in self.eligible_participant_ids
            )
            if participant_id
        )
        if self.outcome == "selected" and not selected:
            raise ValueError("selected outcome requires selected_participant_id.")
        if self.outcome != "selected" and selected:
            raise ValueError("Only a selected outcome may name selected_participant_id.")
        if self.outcome == "eligible" and not eligible:
            raise ValueError("eligible outcome requires at least one eligible participant.")
        if self.outcome == "silent" and eligible:
            raise ValueError("silent outcome cannot include eligible participants.")


def _monotonic_cursor(name: str, current: int, incoming: int | None) -> int:
    if incoming is None:
        return max(0, int(current))
    value = max(0, int(incoming))
    if value < current:
        raise ValueError(f"{name} cannot move backwards from {current} to {value}.")
    return value
