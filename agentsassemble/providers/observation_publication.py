"""Bridge-side handoff from a validated RoomPortal outbox to the room server."""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

from agentsassemble.providers.bridge_protocol import BridgeReportRejected
from agentsassemble.room.text import clean_room_text


class RoomPortalPublication(Protocol):
    content: str
    target_agent_id: str
    message_kind: str
    vote_id: str
    vote_question: str
    vote_options: tuple[str, ...]
    vote_duration_seconds: int
    vote_choice: str


def room_portal_publication_payload(
    publication: RoomPortalPublication,
    *,
    turn_id: str,
    observed_through_seq: int,
) -> dict[str, object]:
    return {
        "turn_id": turn_id,
        "content": publication.content,
        "target_agent_id": publication.target_agent_id,
        "kind": publication.message_kind,
        "vote_id": publication.vote_id,
        "vote_question": publication.vote_question,
        "vote_options": list(publication.vote_options),
        "vote_duration_seconds": publication.vote_duration_seconds,
        "vote_choice": publication.vote_choice,
        "observed_through_seq": observed_through_seq,
    }


def stage_room_portal_publication(
    command: Callable[[str, dict[str, object]], dict[str, object]],
    payload: dict[str, object],
) -> str:
    staged = command("room.publication.stage", payload)
    staged_result = (
        staged.get("result")
        if isinstance(staged, dict) and isinstance(staged.get("result"), dict)
        else {}
    )
    proof = clean_room_text(staged_result.get("publication_proof"), limit=128)
    if not proof:
        raise BridgeReportRejected(
            "RoomPortal publication staging returned no proof.",
            code="observation_publication_unstaged",
        )
    return proof


__all__ = ["room_portal_publication_payload", "stage_room_portal_publication"]
