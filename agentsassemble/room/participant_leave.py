from __future__ import annotations

from collections.abc import Callable, Iterable

from agentsassemble.room.command_uow import RoomCommandUnitOfWork
from agentsassemble.room.errors import RoomCommandRejected
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
            unit.update_participant_fields(agent_id, status="left")
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
                    current = self.store.session(room_id, agent_id)
                    if current:
                        self.store.update_session_fields(
                            room_id,
                            agent_id,
                            enabled=False,
                            last_error=(
                                clean_room_text(error, limit=1000)
                                or "Agent shutdown failed while its owner left the room."
                            ),
                        )
            self._revoke_participant_sessions(room_id, agent_id)
            self._disconnect_participant(room_id, agent_id)
            self._remove_membership(room_id, agent_id)
            self._leave_all_voice(room_id, agent_id)
            self._remove_provider(room_id, agent_id)
            if self.store.participant(room_id, agent_id):
                self.store.update_participant_fields(
                    room_id,
                    agent_id,
                    status="left",
                )

        self._remove_membership(room_id, participant_id)
        self._leave_all_voice(room_id, participant_id)

        def revoke_sessions() -> None:
            self._revoke_participant_sessions(room_id, participant_id)

        self._schedule_cleanup(0.1, revoke_sessions)


__all__ = [
    "AgentStopper",
    "CleanupScheduler",
    "MembershipRemover",
    "ParticipantCleanup",
    "ProviderRemover",
    "RoomParticipantLeaveService",
]
