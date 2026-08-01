from __future__ import annotations

from typing import Callable, Protocol

from agentsassemble.room.event_broker import RoomEventBroker, RoomSocketChannel
from agentsassemble.room.repository import RoomRepository
from agentsassemble.room.text import clean_room_text


EnsureRoom = Callable[[str], dict[str, object]]
EnsureExternalBridgeSession = Callable[[str, dict[str, object]], None]
SessionCallback = Callable[[str, dict[str, object]], object]


class AttentionReset(Protocol):
    def __call__(
        self,
        room_id: str,
        session: dict[str, object],
        *,
        pending_event_ids: list[str],
    ) -> dict[str, object]: ...


class RoomConnectionService:
    """Own browser and Agent Bridge connection membership transitions."""

    def __init__(
        self,
        *,
        store: RoomRepository,
        broker: RoomEventBroker,
        ensure_room: EnsureRoom,
        ensure_external_bridge_session: EnsureExternalBridgeSession,
        reconcile_session_attention: AttentionReset,
        publish_session_state: SessionCallback,
    ) -> None:
        self.store = store
        self.broker = broker
        self._ensure_room = ensure_room
        self._ensure_external_bridge_session = ensure_external_bridge_session
        self._reconcile_session_attention = reconcile_session_attention
        self._publish_session_state = publish_session_state

    def connect(self, identity: dict[str, object]) -> RoomSocketChannel:
        room_id = clean_room_text(identity.get("meeting_id"), 128)
        self._ensure_room(room_id)
        if identity.get("client_type") == "agent_bridge":
            self._ensure_external_bridge_session(room_id, identity)
        else:
            participant_id = clean_room_text(identity.get("agent_id"), 128)
            if participant_id:
                existing = self.store.participant(room_id, participant_id)
                display_name = (
                    clean_room_text(identity.get("display_name"), 64)
                    or participant_id
                )
                if not existing:
                    self.store.upsert_participant(
                        room_id,
                        {
                            "participant_id": participant_id,
                            "display_name": display_name,
                            "participant_type": "human",
                            "role": "host" if identity.get("operator") else "member",
                            "status": "joined",
                        },
                    )
                elif existing.get("status") not in {"left", "kicked"}:
                    self.store.update_participant_fields(
                        room_id,
                        participant_id,
                        display_name=display_name,
                    )
        return self.broker.connect(identity)

    def disconnect(self, channel: RoomSocketChannel) -> None:
        identity = channel.identity
        was_active = self.broker.disconnect(channel)
        if identity.get("client_type") != "agent_bridge":
            return
        if not was_active:
            return
        room_id = clean_room_text(identity.get("meeting_id"), 128)
        session_id = clean_room_text(
            identity.get("session_id") or identity.get("agent_id"),
            128,
        )
        session = self.store.session(room_id, session_id)
        if (
            not session
            or session.get("runtime_status") in {"stopping", "stopped"}
            or not session.get("enabled")
        ):
            return
        pending = list(
            dict.fromkeys(
                [
                    *list(session.get("inflight_event_ids") or []),
                    *list(session.get("pending_event_ids") or []),
                ]
            )
        )
        attention_reset = self._reconcile_session_attention(
            room_id,
            session,
            pending_event_ids=pending,
        )
        self.store.update_session_fields(
            room_id,
            session_id,
            status="unavailable",
            runtime_status="disconnected",
            pid=None,
            provider_session_active=False,
            active_turn_id="",
            active_source_event_id="",
            active_relay_depth=0,
            turn_phase="",
            input_up_to_event_id="",
            input_up_to_seq=0,
            inflight_event_ids=[],
            **attention_reset,
            last_error="Agent bridge disconnected.",
        )
        participant_id = clean_room_text(session.get("participant_id"), 128)
        if participant_id and self.store.participant(room_id, participant_id):
            self.store.update_participant_fields(
                room_id,
                participant_id,
                status="detached",
            )
        self.store.append_event(
            room_id,
            "session_detached",
            participant_id=participant_id,
            session_id=session_id,
            reason="agent bridge disconnected",
        )
        self._publish_session_state(
            room_id,
            self.store.session(room_id, session_id),
        )


__all__ = [
    "AttentionReset",
    "EnsureExternalBridgeSession",
    "EnsureRoom",
    "RoomConnectionService",
    "SessionCallback",
]
