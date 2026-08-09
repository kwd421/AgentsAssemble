"""Resolve observation publications from the server-owned RoomPortal boundary."""

from __future__ import annotations

from collections.abc import Callable, Mapping

from agentsassemble.room.errors import RoomCommandRejected
from agentsassemble.room.text import clean_room_text


PortalPublicationReader = Callable[..., dict[str, object] | None]
_BRIDGE_PUBLICATION_FIELDS = frozenset(
    {
        "content",
        "target_agent_id",
        "kind",
        "message_kind",
        "vote_id",
        "vote_question",
        "vote_options",
        "vote_duration_seconds",
        "vote_choice",
        "publication_proof",
    }
)


class RoomObservationPublication:
    """Canonicalize an observation final from the active local portal only."""

    def __init__(self, *, read_portal_publication: PortalPublicationReader | None) -> None:
        self._read_portal_publication = read_portal_publication

    def canonical_payload(
        self,
        payload: dict[str, object],
        *,
        room_id: str,
        session: dict[str, object],
    ) -> dict[str, object]:
        if clean_room_text(session.get("provider_input_mode"), limit=32) != "room_observation":
            return dict(payload)
        if any(field in payload for field in _BRIDGE_PUBLICATION_FIELDS):
            raise RoomCommandRejected(
                "A room observation completion cannot carry publication content.",
                code="observation_completion_content_forbidden",
            )
        if clean_room_text(session.get("process_ownership"), limit=32) != "server":
            raise RoomCommandRejected(
                "This Agent Bridge has no server-verifiable RoomPortal publication path.",
                code="room_portal_provenance_unavailable",
            )
        handle_id = clean_room_text(session.get("bridge_handle_id"), limit=128)
        turn_id = clean_room_text(session.get("active_turn_id"), limit=128)
        session_id = clean_room_text(session.get("session_id"), limit=128)
        if not handle_id or not turn_id or self._read_portal_publication is None:
            raise RoomCommandRejected(
                "The server-owned RoomPortal publication path is unavailable.",
                code="room_portal_provenance_unavailable",
            )
        try:
            publication = self._read_portal_publication(
                room_id,
                session_id,
                turn_id,
                handle_id=handle_id,
            )
        except Exception as error:
            raise RoomCommandRejected(
                "The server could not read the active RoomPortal publication.",
                code="room_portal_publication_read_failed",
            ) from error
        if not isinstance(publication, Mapping):
            raise RoomCommandRejected(
                "The active RoomPortal contains no publication for this turn.",
                code="room_portal_publication_missing",
            )
        return {
            **payload,
            "content": publication.get("content"),
            "target_agent_id": publication.get("target_agent_id"),
            "kind": publication.get("kind"),
            "vote_id": publication.get("vote_id"),
            "vote_question": publication.get("vote_question"),
            "vote_options": publication.get("vote_options"),
            "vote_duration_seconds": publication.get("vote_duration_seconds"),
            "vote_choice": publication.get("vote_choice"),
        }


__all__ = ["PortalPublicationReader", "RoomObservationPublication"]
