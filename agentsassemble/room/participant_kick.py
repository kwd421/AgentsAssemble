from __future__ import annotations

from typing import Callable

from agentsassemble.room.command_uow import RoomCommandUnitOfWork
from agentsassemble.room.errors import RoomCommandRejected
from agentsassemble.room.provider_cleanup_policy import (
    MAX_PROVIDER_CLEANUP_ATTEMPTS,
    provider_cleanup_delay_seconds,
)
from agentsassemble.room.projection import public_participant
from agentsassemble.room.repository import RoomRepository
from agentsassemble.room.text import clean_room_text


AgentStopper = Callable[[str, str, str], object]
ParticipantCleanup = Callable[[str, str], object]
ProviderRemover = Callable[[str, str], None]
CleanupScheduler = Callable[[float, Callable[[], None]], object]


class RoomParticipantKickService:
    """Own the retryable participant-kick external-effect saga."""

    def __init__(
        self,
        *,
        store: RoomRepository,
        stop_agent: AgentStopper,
        revoke_participant_sessions: ParticipantCleanup,
        disconnect_participant: ParticipantCleanup,
        remove_membership: ParticipantCleanup,
        leave_all_voice: ParticipantCleanup,
        remove_provider: ProviderRemover,
        schedule_cleanup: CleanupScheduler,
    ) -> None:
        self.store = store
        self._stop_agent = stop_agent
        self._revoke_participant_sessions = revoke_participant_sessions
        self._disconnect_participant = disconnect_participant
        self._remove_membership = remove_membership
        self._leave_all_voice = leave_all_voice
        self._remove_provider = remove_provider
        self._schedule_cleanup = schedule_cleanup

    def prepare_intent(
        self,
        room_id: str,
        participant_id: str,
        *,
        operation_id: str,
    ) -> dict[str, object]:
        if participant_id == "operator-local":
            raise RoomCommandRejected(
                "The room host cannot be removed.",
                code="permission_denied",
            )
        participant = self.store.participant(room_id, participant_id)
        if not participant:
            raise RoomCommandRejected(
                f"Participant {participant_id} was not found.",
                code="not_found",
            )
        if participant.get("status") == "kicked":
            raise RoomCommandRejected(
                "This participant was already removed.",
                code="already_kicked",
            )
        intent_action = clean_room_text(
            participant.get("moderation_intent_action"),
            32,
        )
        intent_id = clean_room_text(
            participant.get("moderation_intent_id"),
            128,
        )
        if intent_action:
            if intent_action != "kick" or intent_id != operation_id:
                raise RoomCommandRejected(
                    "Another moderation operation is already in progress for "
                    "this participant.",
                    code="operation_in_progress",
                )
            return participant
        return self.store.update_participant_fields(
            room_id,
            participant_id,
            moderation_intent_action="kick",
            moderation_intent_id=operation_id,
            moderation_intent_status="prepared",
            moderation_intent_cleanup_warning="",
            moderation_intent_removed_member=False,
            moderation_intent_revoked_sessions=0,
        )

    def apply_effects(
        self,
        room_id: str,
        participant: dict[str, object],
        *,
        operation_id: str,
    ) -> dict[str, object]:
        participant_id = clean_room_text(
            participant.get("participant_id"),
            128,
        )
        if (
            clean_room_text(
                participant.get("moderation_intent_status"),
                32,
            )
            == "effect_applied"
        ):
            return _cleanup_from_participant(participant)
        stop_warning = ""
        session = self._provider_session(room_id, participant_id)
        if session and session.get("runtime_status") not in {
            "stopped",
            "available",
        }:
            try:
                self._stop_agent(
                    room_id,
                    participant_id,
                    f"{operation_id}:stop",
                )
            except RoomCommandRejected as error:
                # Room access must still be revoked when provider shutdown
                # cannot be confirmed.
                stop_warning = f"{error.code}: {error}"
        revoked_sessions = self._revoke_participant_sessions(
            room_id,
            participant_id,
        )
        self._disconnect_participant(room_id, participant_id)
        removed_member = self._remove_membership(room_id, participant_id)
        self._leave_all_voice(room_id, participant_id)
        updated = self.store.update_participant_fields(
            room_id,
            participant_id,
            moderation_intent_status="effect_applied",
            moderation_intent_cleanup_warning=stop_warning,
            moderation_intent_removed_member=bool(removed_member),
            moderation_intent_revoked_sessions=int(revoked_sessions),
        )
        return _cleanup_from_participant(updated)

    def finalize_in_unit(
        self,
        participant_id: str,
        *,
        operation_id: str,
        cleanup: dict[str, object],
        unit: RoomCommandUnitOfWork,
    ) -> dict[str, object]:
        participant = unit.participant(participant_id)
        if not participant:
            raise RoomCommandRejected(
                f"Participant {participant_id} was not found.",
                code="not_found",
            )
        if (
            clean_room_text(
                participant.get("moderation_intent_action"),
                32,
            )
            != "kick"
            or clean_room_text(
                participant.get("moderation_intent_id"),
                128,
            )
            != operation_id
            or clean_room_text(
                participant.get("moderation_intent_status"),
                32,
            )
            != "effect_applied"
        ):
            raise RoomCommandRejected(
                "The participant kick cleanup has not completed.",
                code="moderation_cleanup_incomplete",
            )
        updated = unit.update_participant_fields(
            participant_id,
            status="kicked",
            moderation_intent_action="",
            moderation_intent_id="",
            moderation_intent_status="",
            moderation_intent_cleanup_warning="",
            moderation_intent_removed_member=False,
            moderation_intent_revoked_sessions=0,
            moderation_cleanup_pending=bool(cleanup.get("cleanup_warning")),
            moderation_cleanup_warning=clean_room_text(
                cleanup.get("cleanup_warning"),
                1200,
            ),
            moderation_cleanup_attempt_count=0,
        )
        unit.append_event(
            "participant_kicked",
            participant_id=participant_id,
        )
        return {
            "participant": public_participant(updated),
            **cleanup,
        }

    def apply_after_commit(
        self,
        room_id: str,
        participant: dict[str, object],
    ) -> None:
        participant_id = clean_room_text(
            participant.get("participant_id"),
            128,
        )
        stored = self.store.participant(room_id, participant_id)
        if not self._provider_session(room_id, participant_id):
            self._clear_pending_cleanup(room_id, participant_id)
            return
        if stored.get("moderation_cleanup_pending"):
            self._schedule_provider_cleanup(room_id, participant_id, attempt=1)
            return
        self._remove_provider(room_id, participant_id)

    def reconcile_pending(self) -> None:
        """Resume durable provider cleanup after a server restart."""
        for room in self.store.list_rooms(include_archived=False):
            room_id = clean_room_text(room.get("room_id"), 128)
            if not room_id:
                continue
            for participant in self.store.participants(room_id):
                participant_id = clean_room_text(
                    participant.get("participant_id"),
                    128,
                )
                if (
                    participant_id
                    and participant.get("status") == "kicked"
                    and participant.get("moderation_cleanup_pending")
                ):
                    attempt = max(
                        1,
                        int(
                            participant.get("moderation_cleanup_attempt_count")
                            or 0
                        )
                        + 1,
                    )
                    if attempt <= MAX_PROVIDER_CLEANUP_ATTEMPTS:
                        self._schedule_provider_cleanup(
                            room_id,
                            participant_id,
                            attempt=attempt,
                        )

    def _schedule_provider_cleanup(
        self,
        room_id: str,
        participant_id: str,
        *,
        attempt: int,
    ) -> None:
        self._schedule_cleanup(
            provider_cleanup_delay_seconds(attempt),
            lambda: self._retry_provider_cleanup(
                room_id,
                participant_id,
                attempt=attempt,
            ),
        )

    def _retry_provider_cleanup(
        self,
        room_id: str,
        participant_id: str,
        *,
        attempt: int,
    ) -> None:
        participant = self.store.participant(room_id, participant_id)
        if (
            not participant
            or participant.get("status") != "kicked"
            or not participant.get("moderation_cleanup_pending")
        ):
            return
        session = self._provider_session(room_id, participant_id)
        if session and session.get("runtime_status") not in {"stopped", "available"}:
            try:
                self._stop_agent(
                    room_id,
                    participant_id,
                    f"kick-cleanup:{participant_id}:{attempt}",
                )
            except RoomCommandRejected as error:
                warning = f"{error.code}: {error}"
                self.store.update_participant_fields(
                    room_id,
                    participant_id,
                    moderation_cleanup_pending=True,
                    moderation_cleanup_warning=warning,
                    moderation_cleanup_attempt_count=attempt,
                )
                if session:
                    self.store.update_session_fields(
                        room_id,
                        participant_id,
                        enabled=False,
                        last_error=warning,
                    )
                if attempt < MAX_PROVIDER_CLEANUP_ATTEMPTS:
                    self._schedule_provider_cleanup(
                        room_id,
                        participant_id,
                        attempt=attempt + 1,
                    )
                return
        self._remove_provider(room_id, participant_id)
        self._clear_pending_cleanup(room_id, participant_id)

    def _clear_pending_cleanup(self, room_id: str, participant_id: str) -> None:
        if not self.store.participant(room_id, participant_id):
            return
        self.store.update_participant_fields(
            room_id,
            participant_id,
            moderation_cleanup_pending=False,
            moderation_cleanup_warning="",
            moderation_cleanup_attempt_count=0,
        )

    def _provider_session(
        self,
        room_id: str,
        participant_id: str,
    ) -> dict[str, object]:
        session = self.store.session(room_id, participant_id)
        if (
            clean_room_text(session.get("participant_id"), 128)
            != participant_id
        ):
            return {}
        return session


def _cleanup_from_participant(
    participant: dict[str, object],
) -> dict[str, object]:
    return {
        "revoked_sessions": int(
            participant.get("moderation_intent_revoked_sessions") or 0
        ),
        "removed_member": bool(
            participant.get("moderation_intent_removed_member")
        ),
        "cleanup_warning": clean_room_text(
            participant.get("moderation_intent_cleanup_warning"),
            1200,
        ),
    }


__all__ = [
    "AgentStopper",
    "CleanupScheduler",
    "ParticipantCleanup",
    "ProviderRemover",
    "RoomParticipantKickService",
]
