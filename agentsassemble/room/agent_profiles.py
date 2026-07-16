from __future__ import annotations

from typing import Callable

from agentsassemble.room.command_uow import RoomCommandUnitOfWork
from agentsassemble.room.errors import RoomCommandRejected
from agentsassemble.room.projection import public_session
from agentsassemble.room.provider_registry import RoomProviderRegistry
from agentsassemble.room.repository import RoomRepository
from agentsassemble.room.text import clean_room_text


SessionCallback = Callable[[str, dict[str, object]], object]


class RoomAgentProfileService:
    """Keep an agent's canonical identity and configured provider name aligned."""

    def __init__(
        self,
        *,
        store: RoomRepository,
        provider_registry: RoomProviderRegistry,
        publish_session_state: SessionCallback,
    ) -> None:
        self.store = store
        self._provider_registry = provider_registry
        self._publish_session_state = publish_session_state

    def update_in_unit(
        self,
        agent_id: str,
        payload: dict[str, object],
        *,
        unit: RoomCommandUnitOfWork,
    ) -> dict[str, object]:
        current = unit.session(agent_id)
        if not current:
            raise RoomCommandRejected(
                f"Agent session {agent_id} was not found.",
                code="not_found",
            )
        participant = unit.participant(agent_id)
        if not participant:
            raise RoomCommandRejected(
                f"Participant {agent_id} was not found.",
                code="not_found",
            )
        display_name = clean_room_text(
            payload.get("display_name") or current.get("display_name") or agent_id,
            80,
        )
        avatar_image_url = clean_room_text(payload.get("avatar_image_url"), 4096)
        updated_participant = unit.update_participant_fields(
            agent_id,
            display_name=display_name,
            avatar_image_url=avatar_image_url,
        )
        updated_session = unit.update_session_fields(
            agent_id,
            display_name=display_name,
            avatar_image_url=avatar_image_url,
        )
        unit.append_event(
            "participant_updated",
            participant_id=agent_id,
            display_name=display_name,
            avatar_image_url=avatar_image_url,
        )
        return {
            "status": "profile_updated",
            "agent_session": public_session(updated_session),
            "participant": updated_participant,
        }

    def apply_after_commit(
        self,
        room_id: str,
        ack: dict[str, object],
    ) -> None:
        if ack.get("deduplicated"):
            return
        result = ack.get("result") if isinstance(ack.get("result"), dict) else {}
        session = (
            result.get("agent_session")
            if isinstance(result.get("agent_session"), dict)
            else {}
        )
        agent_id = clean_room_text(
            session.get("session_id") or session.get("participant_id"),
            128,
        )
        display_name = clean_room_text(session.get("display_name"), 80)
        if not agent_id:
            return
        self._provider_registry.update_display_name(
            room_id,
            agent_id,
            display_name,
        )
        self._publish_session_state(
            room_id,
            self.store.session(room_id, agent_id),
        )


__all__ = [
    "RoomAgentProfileService",
    "SessionCallback",
]
