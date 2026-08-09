"""Ordered-conversation target selection and room-observation queueing."""

from __future__ import annotations

from collections.abc import Callable

from agentsassemble.providers.launch_specs import NativeCliProviderSpec
from agentsassemble.providers.runtime_contracts import ORDERED_FLOOR
from agentsassemble.room.repository import RoomRepository
from agentsassemble.room.text import clean_room_text
from agentsassemble.room.turn_coordinator import RoomTurnCoordinator
from agentsassemble.room_floor_policy import AgentFloorEligibility, ordered_floor_target
from agentsassemble.room_routing import direct_message_targets


class RoomOrderedMessageRouter:
    """Route one public message through the ordered floor policy."""

    def __init__(
        self,
        *,
        store: RoomRepository,
        turn_coordinator: RoomTurnCoordinator,
        floor_eligibility: Callable[[str, str], AgentFloorEligibility],
    ) -> None:
        self._store = store
        self._turn_coordinator = turn_coordinator
        self._floor_eligibility = floor_eligibility

    def route(
        self,
        event: dict[str, object],
        providers: dict[str, NativeCliProviderSpec],
        *,
        exclude_previous_speaker: bool,
    ) -> None:
        room_id = clean_room_text(event.get("room_id"), limit=128)
        actor = event.get("actor") if isinstance(event.get("actor"), dict) else {}
        actor_id = clean_room_text(
            actor.get("participant_id") or event.get("participant_id"),
            limit=128,
        )
        direct_targets = direct_message_targets(event, providers)
        eligible_agent_ids = self._eligible_agents(room_id, providers, actor_id)
        actor_role = clean_room_text(
            self._store.participant(room_id, actor_id).get("role"),
            limit=32,
        )
        if actor_id in providers and actor_role != "director" and not direct_targets:
            eligible_directors = [
                agent_id
                for agent_id in eligible_agent_ids
                if clean_room_text(
                    self._store.participant(room_id, agent_id).get("role"),
                    limit=32,
                )
                == "director"
            ]
            if eligible_directors:
                eligible_agent_ids = eligible_directors
        message_counts, previous_speaker_id = self.recent_speaking_state(
            room_id,
            providers,
        )
        target_ids = ordered_floor_target(
            provider_ids=providers,
            actor_id=actor_id,
            direct_targets=direct_targets,
            eligible_agent_ids=eligible_agent_ids,
            message_counts=message_counts,
            previous_speaker_id=previous_speaker_id,
            exclude_previous_speaker=exclude_previous_speaker,
        )
        for agent_id in target_ids:
            participant = self._store.participant(room_id, agent_id)
            if participant.get("status") == "kicked" or participant.get("muted"):
                continue
            self._turn_coordinator.queue_event(
                room_id,
                agent_id,
                event,
                relay_depth=0,
                input_mode="room_observation",
                observation_kind=ORDERED_FLOOR,
            )

    def recent_speaking_state(
        self,
        room_id: str,
        providers: dict[str, NativeCliProviderSpec],
    ) -> tuple[dict[str, int], str]:
        counts = {agent_id: 0 for agent_id in providers}
        previous_speaker_id = ""
        for message in self._store.read_events(
            room_id,
            event_types=("message_final",),
            limit=100,
            newest=True,
        ):
            actor = message.get("actor") if isinstance(message.get("actor"), dict) else {}
            participant_id = clean_room_text(
                message.get("participant_id") or actor.get("participant_id"),
                limit=128,
            )
            if participant_id in counts:
                counts[participant_id] += 1
                previous_speaker_id = participant_id
        return counts, previous_speaker_id

    def _eligible_agents(
        self,
        room_id: str,
        providers: dict[str, NativeCliProviderSpec],
        actor_id: str,
    ) -> list[str]:
        eligible: list[str] = []
        for agent_id in providers:
            if agent_id == actor_id:
                continue
            eligibility = self._floor_eligibility(room_id, agent_id)
            if eligibility.eligible or eligibility.reason_code == "runtime_busy":
                eligible.append(agent_id)
        return eligible


__all__ = ["RoomOrderedMessageRouter"]
