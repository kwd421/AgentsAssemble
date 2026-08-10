from __future__ import annotations

import threading
from typing import Callable

from agentsassemble.providers.launch_specs import (
    NativeCliProviderSpec,
    StoredProviderProfileError,
    native_cli_provider_spec_from_stored_session_strict,
)
from agentsassemble.room.errors import RoomCommandRejected
from agentsassemble.room.event_broker import RoomEventBroker
from agentsassemble.room.projection import public_session
from agentsassemble.room.provider_registry import RoomProviderRegistry
from agentsassemble.room.repository import RoomRepository
from agentsassemble.room.text import clean_room_text


EnsureProviderSession = Callable[[str, NativeCliProviderSpec], None]
SessionCallback = Callable[[str, dict[str, object]], object]
StartAgent = Callable[..., dict[str, object]]


class RoomAgentReactivationService:
    """Re-add one stopped server-owned Agent Session from its durable profile."""

    def __init__(
        self,
        *,
        store: RoomRepository,
        broker: RoomEventBroker,
        lock: threading.RLock,
        provider_registry: RoomProviderRegistry,
        ensure_provider_session: EnsureProviderSession,
        publish_session_state: SessionCallback,
        start_agent: StartAgent,
    ) -> None:
        self.store = store
        self.broker = broker
        self._lock = lock
        self._provider_registry = provider_registry
        self._ensure_provider_session = ensure_provider_session
        self._publish_session_state = publish_session_state
        self._start_agent = start_agent

    def readd(
        self,
        room_id: str,
        agent_id: str,
        payload: dict[str, object],
        *,
        server_url: str,
        ticket_issuer: Callable[[dict[str, object]], object] | None,
        operation_id: str = "",
    ) -> dict[str, object]:
        current = self.store.session(room_id, agent_id)
        if not current:
            raise RoomCommandRejected(
                f"Agent session {agent_id} was not found.",
                code="not_found",
            )
        if current.get("process_ownership") != "server":
            raise RoomCommandRejected(
                "External Agent Sessions must reconnect with their original invite.",
                code="runtime_unavailable",
            )
        participant = self.store.participant(room_id, agent_id)
        clean_operation_id = clean_room_text(operation_id, 128)
        continuing_same_reactivation = bool(
            clean_operation_id
            and current.get("reactivation_operation_id") == clean_operation_id
            and current.get("runtime_status") == "error"
        )
        if (
            current.get("runtime_status") not in {"stopped", "available"}
            and not continuing_same_reactivation
        ):
            raise RoomCommandRejected(
                "Only stopped Agent Sessions can be added back to the room.",
                code="readd_invalid_state",
            )
        if current.get("enabled") and not continuing_same_reactivation:
            raise RoomCommandRejected(
                "The Agent Session is still enabled.",
                code="readd_invalid_state",
            )
        if participant.get("status") not in {"detached", "kicked"}:
            raise RoomCommandRejected(
                "The Agent Session participant is still active.",
                code="readd_invalid_state",
            )
        if (
            current.get("active_turn_id")
            or current.get("bridge_handle_id")
            or self.broker.has_bridge(room_id, agent_id)
        ):
            raise RoomCommandRejected(
                "The Agent Session still owns an active runtime.",
                code="readd_invalid_state",
            )
        try:
            spec = native_cli_provider_spec_from_stored_session_strict(current)
        except StoredProviderProfileError as error:
            raise RoomCommandRejected(str(error), code=error.code) from error
        if (
            current.get("runtime_profile_key") != spec.runtime_profile_key()
            or current.get("transport") != spec.transport
        ):
            self.store.update_session_fields(
                room_id,
                agent_id,
                runtime_profile_key=spec.runtime_profile_key(),
                transport=spec.transport,
                command_configured=list(spec.command),
            )
        with self._lock:
            self._provider_registry.register(room_id, spec)
            self._ensure_provider_session(room_id, spec)
            if not continuing_same_reactivation:
                self.store.update_participant_fields(
                    room_id,
                    agent_id,
                    status="detached",
                )
                self.store.update_session_fields(
                    room_id,
                    agent_id,
                    reactivation_operation_id=clean_operation_id,
                )
            session = self.store.session(room_id, agent_id)
            if not continuing_same_reactivation:
                self.store.append_event(
                    room_id,
                    "agent_session_reactivated",
                    participant_id=agent_id,
                    session_id=agent_id,
                )
                self._publish_session_state(room_id, session)
        result: dict[str, object] = {
            "status": "readded",
            "agent_session": public_session(session),
            "participant": self.store.participant(room_id, agent_id),
        }
        if bool(payload.get("start") or payload.get("start_now")):
            result["start"] = self._start_agent(
                room_id,
                agent_id,
                server_url=server_url,
                ticket_issuer=ticket_issuer,
            )
        return result


__all__ = [
    "EnsureProviderSession",
    "RoomAgentReactivationService",
    "SessionCallback",
    "StartAgent",
]
