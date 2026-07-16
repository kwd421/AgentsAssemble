"""Compatibility projection contract for canonical room admission."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class LegacyAdmissionParticipant:
    participant_id: str
    display_name: str
    provider_kind: str
    connection_kind: str
    room_id: str
    owner_display_name: str = ""


class LegacyAdmissionProjection(Protocol):
    """Compatibility side effect that must never own canonical admission."""

    def participant_joined(self, participant: LegacyAdmissionParticipant) -> bool: ...

    def participant_left(self, participant_id: str) -> bool: ...

    def diagnostics(self) -> dict[str, object]: ...
