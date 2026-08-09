"""Provider streaming ingress and exact-sensitive-value buffering."""

from __future__ import annotations

from agentsassemble.room import bridge_diagnostics
from agentsassemble.room.errors import RoomCommandRejected
from agentsassemble.room.projection import (
    PUBLIC_ACTIVITY_LABELS,
    PUBLIC_ACTIVITY_STATUSES,
    public_activity,
    safe_activity_detail,
    safe_activity_display_detail,
    safe_activity_id,
)
from agentsassemble.room.repository import RoomRepository
from agentsassemble.room.text import clean_room_text, has_room_visible_text


class RoomProviderStreamIngress:
    """Own streaming delta validation, redaction, publication, and cleanup."""

    def __init__(
        self,
        store: RoomRepository,
        *,
        redact_diagnostic: bridge_diagnostics.DiagnosticRedactor | None = None,
        redact_delta: bridge_diagnostics.StreamDeltaRedactor | None = None,
        discard_delta: bridge_diagnostics.StreamDeltaDiscarder | None = None,
        redact_activity: bridge_diagnostics.ActivityPayloadRedactor | None = None,
        discard_activity: bridge_diagnostics.ActivityPayloadDiscarder | None = None,
    ) -> None:
        self._store = store
        self._redact_diagnostic = (
            redact_diagnostic or bridge_diagnostics.default_diagnostic_redactor
        )
        self._redact_delta = redact_delta or bridge_diagnostics.default_stream_delta_redactor
        self._discard_delta = discard_delta or bridge_diagnostics.default_stream_delta_discarder
        self._redact_activity = (
            redact_activity or bridge_diagnostics.default_activity_payload_redactor
        )
        self._discard_activity = (
            discard_activity or bridge_diagnostics.default_activity_payload_discarder
        )

    def publish_delta(
        self,
        room_id: str,
        payload: dict[str, object],
        *,
        agent_id: str,
        session: dict[str, object],
        current_phase: str,
    ) -> dict[str, object]:
        if clean_room_text(session.get("provider_input_mode"), limit=32) == "room_observation":
            raise RoomCommandRejected(
                "Room observations publish only through the RoomPortal boundary.",
                code="observation_publication_required",
            )
        content = _message_delta_text(payload.get("content"), limit=12000)
        if not has_room_visible_text(content):
            raise RoomCommandRejected("Delta content is required.", code="empty")
        content = self._redact_delta(
            room_id,
            str(session["session_id"]),
            str(session["active_turn_id"]),
            content,
        )
        if not content:
            return {"buffered": True, "event_seq": self._store.latest_event_sequence(room_id)}
        if current_phase != "streaming":
            self._store.update_session_fields(
                room_id,
                str(session["session_id"]),
                turn_phase="streaming",
            )
            self._store.append_event(
                room_id,
                "turn_state",
                participant_id=agent_id,
                session_id=session["session_id"],
                turn_id=session["active_turn_id"],
                phase="streaming",
            )
        event = self._store.append_event(
            room_id,
            "message_delta",
            participant_id=agent_id,
            participant_type="agent",
            session_id=session["session_id"],
            turn_id=session["active_turn_id"],
            content=content,
        )
        return {"event": event, "event_seq": event["seq"]}

    def publish_activity(
        self,
        room_id: str,
        payload: dict[str, object],
        *,
        agent_id: str,
        session: dict[str, object],
    ) -> dict[str, object]:
        category = clean_room_text(payload.get("category"), limit=32)
        status = clean_room_text(payload.get("status"), limit=32)
        if category not in PUBLIC_ACTIVITY_LABELS or status not in PUBLIC_ACTIVITY_STATUSES:
            raise RoomCommandRejected(
                "Agent activity category or status is invalid.",
                code="adapter_activity_invalid",
            )
        raw_detail, raw_title = bridge_diagnostics.redacted_activity_text(
            self._redact_diagnostic,
            room_id,
            str(session["session_id"]),
            payload,
        )
        normalized_payload = dict(payload)
        normalized_payload.pop("content", None)
        normalized_payload["activity_title"] = raw_title
        normalized_payload["activity_detail"] = raw_detail
        safe_payloads = self._redact_activity(
            room_id,
            str(session["session_id"]),
            str(session["active_turn_id"]),
            normalized_payload,
        )
        if not safe_payloads:
            return {
                "buffered": True,
                "event_seq": self._store.latest_event_sequence(room_id),
            }
        events = [
            self._publish_activity_event(
                room_id,
                safe_payload,
                agent_id=agent_id,
                session=session,
            )
            for safe_payload in safe_payloads
        ]
        return {"event": events[-1], "event_seq": events[-1]["seq"]}

    def _publish_activity_event(
        self,
        room_id: str,
        payload: dict[str, object],
        *,
        agent_id: str,
        session: dict[str, object],
    ) -> dict[str, object]:
        category = clean_room_text(payload.get("category"), limit=32)
        status = clean_room_text(payload.get("status"), limit=32)
        raw_title = str(payload.get("activity_title") or "")
        raw_detail = str(payload.get("activity_detail") or "")
        content, activity_kind = public_activity(category, status, detail=raw_detail)
        activity_fields: dict[str, object] = {}
        activity_id = safe_activity_id(payload.get("activity_id"))
        activity_title = safe_activity_detail(raw_title, limit=160)
        activity_detail = safe_activity_display_detail(
            raw_detail,
            limit=2000 if category == "reasoning" else 600,
        )
        if activity_id:
            activity_fields["activity_id"] = activity_id
        if activity_title:
            activity_fields["activity_title"] = activity_title
        if activity_detail:
            activity_fields["activity_detail"] = activity_detail
        return self._store.append_event(
            room_id,
            "activity_delta",
            participant_id=agent_id,
            participant_type="agent",
            actor_id=agent_id,
            actor_type="agent",
            display_name=session.get("display_name") or agent_id,
            session_id=session["session_id"],
            turn_id=session["active_turn_id"],
            owner_id=session.get("owner_id") or session.get("created_by") or "",
            visibility="public" if session.get("share_activity") else "owner",
            activity_kind=activity_kind,
            category=category,
            status=status,
            content=content,
            **activity_fields,
        )

    def discard(self, room_id: str, session: dict[str, object]) -> None:
        self._discard_activity(
            room_id,
            str(session["session_id"]),
            str(session["active_turn_id"]),
        )
        self._discard_delta(
            room_id,
            str(session["session_id"]),
            str(session["active_turn_id"]),
        )


def _message_delta_text(value: object, *, limit: int) -> str:
    return str(value or "").replace("\x00", "").replace("\r\n", "\n").replace("\r", "\n")[:limit]


__all__ = ["RoomProviderStreamIngress"]
