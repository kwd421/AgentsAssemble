from __future__ import annotations

from collections.abc import Callable, Iterable

from agentsassemble.room.command_uow import RoomCommandUnitOfWork
from agentsassemble.room.errors import RoomCommandRejected
from agentsassemble.room.provider_cleanup_policy import (
    MAX_PROVIDER_CLEANUP_ATTEMPTS,
    provider_cleanup_delay_seconds,
)
from agentsassemble.room.repository import RoomRepository
from agentsassemble.room.text import clean_room_text


MembershipRemover = Callable[[str, str], object]
ParticipantCleanup = Callable[[str, str], object]
CleanupScheduler = Callable[[float, Callable[[], None]], object]
AgentStopper = Callable[[str, str, str], object]
ProviderRemover = Callable[[str, str], None]


class RoomParticipantLeaveService:
    """Persist a participant leave and revoke its room access after commit."""

    def __init__(
        self,
        *,
        store: RoomRepository,
        remove_membership: MembershipRemover,
        leave_all_voice: ParticipantCleanup,
        revoke_participant_sessions: ParticipantCleanup,
        disconnect_participant: ParticipantCleanup,
        stop_agent: AgentStopper,
        remove_provider: ProviderRemover,
        schedule_cleanup: CleanupScheduler,
    ) -> None:
        self.store = store
        self._remove_membership = remove_membership
        self._leave_all_voice = leave_all_voice
        self._revoke_participant_sessions = revoke_participant_sessions
        self._disconnect_participant = disconnect_participant
        self._stop_agent = stop_agent
        self._remove_provider = remove_provider
        self._schedule_cleanup = schedule_cleanup

    def owned_agent_ids(
        self,
        room_id: str,
        *,
        owner_ids: Iterable[str],
    ) -> list[str]:
        owners = {
            clean_room_text(owner_id, limit=128)
            for owner_id in owner_ids
            if clean_room_text(owner_id, limit=128)
        }
        if not owners:
            return []
        sessions_by_participant = {
            clean_room_text(session.get("participant_id"), limit=128): session
            for session in self.store.sessions(room_id)
        }
        owned: list[str] = []
        for participant in self.store.participants(room_id):
            participant_id = clean_room_text(
                participant.get("participant_id"),
                limit=128,
            )
            if (
                not participant_id
                or participant_id not in sessions_by_participant
                or participant.get("status") in {"left", "kicked"}
            ):
                continue
            session = sessions_by_participant.get(participant_id, {})
            ownership_ids = {
                clean_room_text(value, limit=128)
                for value in (
                    participant.get("owner_id"),
                    participant.get("created_by"),
                    session.get("owner_id"),
                    session.get("created_by"),
                )
                if clean_room_text(value, limit=128)
            }
            if owners.intersection(ownership_ids):
                owned.append(participant_id)
        return owned

    def update_in_unit(
        self,
        participant_id: str,
        *,
        is_owner: bool,
        owned_agent_ids: Iterable[str] = (),
        unit: RoomCommandUnitOfWork,
    ) -> dict[str, object]:
        participant = unit.participant(participant_id)
        if not participant:
            raise RoomCommandRejected(
                "Participant was not found in this room.",
                code="not_found",
            )
        if is_owner:
            raise RoomCommandRejected(
                "The room owner must transfer ownership or delete the server.",
                code="owner_must_transfer_or_delete",
            )
        updated = unit.update_participant_fields(
            participant_id,
            status="left",
            access_cleanup_pending=True,
            access_cleanup_warning="",
            access_cleanup_attempt_count=0,
        )
        event = unit.append_event(
            "participant_left",
            participant_id=participant_id,
        )
        departed_agent_ids: list[str] = []
        for agent_id in dict.fromkeys(
            clean_room_text(value, limit=128)
            for value in owned_agent_ids
        ):
            if not agent_id or agent_id == participant_id:
                continue
            agent = unit.participant(agent_id)
            if (
                not agent
                or not unit.session(agent_id)
                or agent.get("status") in {"left", "kicked"}
            ):
                continue
            unit.update_participant_fields(
                agent_id,
                status="left",
                access_cleanup_pending=True,
                access_cleanup_warning="",
                access_cleanup_attempt_count=0,
            )
            unit.append_event(
                "participant_left",
                participant_id=agent_id,
                reason="owner_left",
                owner_participant_id=participant_id,
            )
            departed_agent_ids.append(agent_id)
        return {
            "participant": updated,
            "event": event,
            "revocation_scheduled": True,
            "owned_agent_ids": departed_agent_ids,
        }

    def apply_after_commit(
        self,
        room_id: str,
        participant_id: str,
        *,
        owned_agent_ids: Iterable[str] = (),
        operation_id: str = "",
    ) -> None:
        for agent_id in dict.fromkeys(
            clean_room_text(value, limit=128)
            for value in owned_agent_ids
        ):
            if not agent_id:
                continue
            session = self.store.session(room_id, agent_id)
            if session and session.get("runtime_status") not in {
                "stopped",
                "available",
            }:
                try:
                    self._stop_agent(
                        room_id,
                        agent_id,
                        f"{operation_id}:owned-agent:{agent_id}",
                    )
                except Exception as error:
                    self._record_provider_cleanup_failure(
                        room_id,
                        agent_id,
                        error=error,
                        attempt=1,
                    )
            self._run_access_cleanup(
                room_id,
                agent_id,
                attempt=1,
                include_membership=True,
            )
            if self.store.participant(room_id, agent_id).get(
                "moderation_cleanup_pending"
            ):
                self._schedule_provider_cleanup(
                    room_id,
                    agent_id,
                    attempt=2,
                )
            else:
                self._remove_provider(room_id, agent_id)
                self._clear_pending_cleanup(room_id, agent_id)
            if self.store.participant(room_id, agent_id):
                self.store.update_participant_fields(
                    room_id,
                    agent_id,
                    status="left",
                )

        self._remove_membership(room_id, participant_id)
        self._leave_all_voice(room_id, participant_id)
        self._schedule_access_cleanup(
            room_id,
            participant_id,
            attempt=1,
            delay=0.1,
            include_membership=False,
        )

    def reconcile_pending(self) -> None:
        """Resume durable owned-agent cleanup after a server restart."""
        for room in self.store.list_rooms(include_archived=False):
            room_id = clean_room_text(room.get("room_id"), limit=128)
            if not room_id:
                continue
            for participant in self.store.participants(room_id):
                participant_id = clean_room_text(
                    participant.get("participant_id"),
                    limit=128,
                )
                if not participant_id or participant.get("status") != "left":
                    continue
                if participant.get("access_cleanup_pending"):
                    access_attempt = max(
                        1,
                        int(participant.get("access_cleanup_attempt_count") or 0)
                        + 1,
                    )
                    if access_attempt <= MAX_PROVIDER_CLEANUP_ATTEMPTS:
                        self._schedule_access_cleanup(
                            room_id,
                            participant_id,
                            attempt=access_attempt,
                            delay=0.1,
                            include_membership=True,
                        )
                if participant.get("moderation_cleanup_pending"):
                    provider_attempt = max(
                        1,
                        int(
                            participant.get("moderation_cleanup_attempt_count")
                            or 0
                        )
                        + 1,
                    )
                    if provider_attempt <= MAX_PROVIDER_CLEANUP_ATTEMPTS:
                        self._schedule_provider_cleanup(
                            room_id,
                            participant_id,
                            attempt=provider_attempt,
                        )

    def _schedule_access_cleanup(
        self,
        room_id: str,
        participant_id: str,
        *,
        attempt: int,
        delay: float,
        include_membership: bool,
    ) -> None:
        self._schedule_cleanup(
            delay,
            lambda: self._run_access_cleanup(
                room_id,
                participant_id,
                attempt=attempt,
                include_membership=include_membership,
            ),
        )

    def _run_access_cleanup(
        self,
        room_id: str,
        participant_id: str,
        *,
        attempt: int,
        include_membership: bool,
    ) -> None:
        participant = self.store.participant(room_id, participant_id)
        if (
            not participant
            or participant.get("status") != "left"
            or not participant.get("access_cleanup_pending")
        ):
            return
        operations: list[tuple[str, ParticipantCleanup]] = [
            ("session revocation", self._revoke_participant_sessions),
            ("socket disconnect", self._disconnect_participant),
        ]
        if include_membership:
            operations.extend(
                [
                    ("membership removal", self._remove_membership),
                    ("voice leave", self._leave_all_voice),
                ]
            )
        failures: list[str] = []
        for label, operation in operations:
            try:
                operation(room_id, participant_id)
            except Exception as error:
                message = clean_room_text(error, limit=500) or error.__class__.__name__
                failures.append(f"{label}: {message}")
        if not failures:
            self.store.update_participant_fields(
                room_id,
                participant_id,
                access_cleanup_pending=False,
                access_cleanup_warning="",
                access_cleanup_attempt_count=0,
            )
            return
        self.store.update_participant_fields(
            room_id,
            participant_id,
            access_cleanup_pending=True,
            access_cleanup_warning="; ".join(failures),
            access_cleanup_attempt_count=attempt,
        )
        if attempt < MAX_PROVIDER_CLEANUP_ATTEMPTS:
            self._schedule_access_cleanup(
                room_id,
                participant_id,
                attempt=attempt + 1,
                delay=provider_cleanup_delay_seconds(attempt + 1),
                include_membership=True,
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
            or participant.get("status") != "left"
            or not participant.get("moderation_cleanup_pending")
        ):
            return
        session = self.store.session(room_id, participant_id)
        if session and session.get("runtime_status") not in {
            "stopped",
            "available",
        }:
            try:
                self._stop_agent(
                    room_id,
                    participant_id,
                    f"leave-cleanup:{participant_id}:{attempt}",
                )
            except Exception as error:
                self._record_provider_cleanup_failure(
                    room_id,
                    participant_id,
                    error=error,
                    attempt=attempt,
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

    def _record_provider_cleanup_failure(
        self,
        room_id: str,
        participant_id: str,
        *,
        error: Exception,
        attempt: int,
    ) -> None:
        error_code = clean_room_text(getattr(error, "code", ""), limit=64)
        message = (
            clean_room_text(error, limit=1000)
            or "Agent shutdown failed while its owner left the room."
        )
        warning = f"{error_code}: {message}" if error_code else message
        if self.store.participant(room_id, participant_id):
            self.store.update_participant_fields(
                room_id,
                participant_id,
                moderation_cleanup_pending=True,
                moderation_cleanup_warning=warning,
                moderation_cleanup_attempt_count=attempt,
            )
        if self.store.session(room_id, participant_id):
            self.store.update_session_fields(
                room_id,
                participant_id,
                enabled=False,
                last_error=warning,
            )

    def _clear_pending_cleanup(
        self,
        room_id: str,
        participant_id: str,
    ) -> None:
        if not self.store.participant(room_id, participant_id):
            return
        self.store.update_participant_fields(
            room_id,
            participant_id,
            moderation_cleanup_pending=False,
            moderation_cleanup_warning="",
            moderation_cleanup_attempt_count=0,
        )


__all__ = [
    "AgentStopper",
    "CleanupScheduler",
    "MembershipRemover",
    "ParticipantCleanup",
    "ProviderRemover",
    "RoomParticipantLeaveService",
]
