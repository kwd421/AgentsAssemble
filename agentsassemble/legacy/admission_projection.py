"""Best-effort projection from canonical admission into the retained roster."""
from __future__ import annotations

import threading
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from agentsassemble.live_agents import connect_live_agent


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


class LiveAgentLegacyAdmissionProjection:
    """Mirror canonical participants into the retained live-agent roster.

    Projection failures are observable but do not roll back canonical room
    admission. Only bounded, redacted failure metadata is retained.
    """

    def __init__(
        self,
        output_root: Path,
        *,
        connect: Callable[[Path, dict[str, object]], object] = connect_live_agent,
        failure_tail_size: int = 20,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if failure_tail_size < 1:
            raise ValueError("Legacy admission projection tail size must be positive.")
        self._output_root = output_root
        self._connect = connect
        self._clock = clock or (lambda: datetime.now(UTC))
        self._lock = threading.Lock()
        self._failure_count = 0
        self._recent_failures: deque[dict[str, str]] = deque(maxlen=failure_tail_size)

    def participant_joined(self, participant: LegacyAdmissionParticipant) -> bool:
        return self._project(
            operation="participant_joined",
            participant_id=participant.participant_id,
            payload={
                "agent_id": participant.participant_id,
                "display_name": participant.display_name,
                "provider_kind": participant.provider_kind,
                "connection_kind": participant.connection_kind,
                "meeting_id": participant.room_id,
                "status": "online",
                "owner_display_name": participant.owner_display_name,
            },
        )

    def participant_left(self, participant_id: str) -> bool:
        return self._project(
            operation="participant_left",
            participant_id=participant_id,
            payload={"agent_id": participant_id, "status": "offline"},
        )

    def diagnostics(self) -> dict[str, object]:
        with self._lock:
            failures = [dict(item) for item in self._recent_failures]
            return {
                "healthy": self._failure_count == 0,
                "failure_count": self._failure_count,
                "recent_failures": failures,
                "tail_truncated": self._failure_count > len(failures),
            }

    def _project(
        self,
        *,
        operation: str,
        participant_id: str,
        payload: dict[str, object],
    ) -> bool:
        try:
            self._connect(self._output_root, payload)
        except Exception as error:
            failure = {
                "operation": operation,
                "participant_id": str(participant_id or "")[:128],
                "error_type": type(error).__name__,
                "recorded_at": self._clock().astimezone(UTC).isoformat(),
            }
            with self._lock:
                self._failure_count += 1
                self._recent_failures.append(failure)
            return False
        return True
