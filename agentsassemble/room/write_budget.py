"""One admission budget for authenticated room writes across every provider."""

from __future__ import annotations

import json
import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Callable

from agentsassemble.room.errors import RoomCommandRejected
from agentsassemble.room.repository import RoomRepository
from agentsassemble.room.text import clean_room_text


@dataclass(frozen=True)
class RoomWriteBudgetPolicy:
    window_seconds: float = 60.0
    max_commands_per_window: int = 3_600
    max_payload_bytes_per_window: int = 8 * 1024 * 1024
    max_turn_stream_commands: int = 8_192
    max_turn_stream_bytes: int = 32 * 1024 * 1024

    def __post_init__(self) -> None:
        if self.window_seconds <= 0:
            raise ValueError("write budget window_seconds must be positive")
        for name in (
            "max_commands_per_window",
            "max_payload_bytes_per_window",
            "max_turn_stream_commands",
            "max_turn_stream_bytes",
        ):
            if int(getattr(self, name)) <= 0:
                raise ValueError(f"write budget {name} must be positive")


# A saturated client must still be able to stop work, leave, close a room, or
# complete an already assigned turn. These actions cannot create an unbounded
# sequence of successful public events without another budgeted action first.
_SAFETY_AND_READ_ACTIONS = frozenset(
    {
        "room.history",
        "room.vote.summary",
        "room.check",
        "room.attachment.read",
        "room.archive",
        "room.close",
        "room.delete",
        "agent.pause",
        "agent.stop",
        "agent.interrupt",
        "participant.kick",
        "participant.export",
        "participant.leave",
        "bridge.stopped",
        "message.final",
        "turn.decline",
        "turn.failed",
        "provider.request.resolve",
        "provider.request.closed",
    }
)
_TURN_STREAM_ACTIONS = frozenset(
    {
        "turn.state",
        "activity.update",
        "message.delta",
        "room.result.publish",
    }
)


class RoomWriteBudget:
    """Bound unique authenticated writes and persist each turn's stream usage."""

    def __init__(
        self,
        repository: RoomRepository,
        *,
        policy: RoomWriteBudgetPolicy | None = None,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._repository = repository
        self.policy = policy or RoomWriteBudgetPolicy()
        self._monotonic = monotonic
        self._recent: dict[tuple[str, str], deque[tuple[float, int]]] = {}
        self._lock = threading.Lock()

    def admit(
        self,
        *,
        room_id: str,
        principal_id: str,
        session_id: str,
        request_id: str,
        action: str,
        payload: dict[str, object],
    ) -> None:
        with self._lock:
            if action in _SAFETY_AND_READ_ACTIONS:
                return
            if self._repository.command_record(room_id, principal_id, request_id):
                return
            payload_bytes = _command_size(request_id, action, payload)
            self._admit_window(room_id, principal_id, payload_bytes)
            if action in _TURN_STREAM_ACTIONS:
                self._admit_turn_stream(
                    room_id=room_id,
                    session_id=session_id,
                    turn_id=clean_room_text(payload.get("turn_id"), limit=128),
                    payload_bytes=payload_bytes,
                )

    def clear(self) -> None:
        with self._lock:
            self._recent.clear()

    def _admit_window(
        self,
        room_id: str,
        principal_id: str,
        payload_bytes: int,
    ) -> None:
        now = self._monotonic()
        cutoff = now - self.policy.window_seconds
        key = (room_id, principal_id)
        recent = self._recent.setdefault(key, deque())
        while recent and recent[0][0] <= cutoff:
            recent.popleft()
        if len(recent) >= self.policy.max_commands_per_window:
            raise RoomCommandRejected(
                "Authenticated room write budget exceeded.",
                code="write_budget_exceeded",
            )
        if sum(size for _timestamp, size in recent) + payload_bytes > self.policy.max_payload_bytes_per_window:
            raise RoomCommandRejected(
                "Authenticated room payload budget exceeded.",
                code="write_budget_exceeded",
            )
        recent.append((now, payload_bytes))

    def _admit_turn_stream(
        self,
        *,
        room_id: str,
        session_id: str,
        turn_id: str,
        payload_bytes: int,
    ) -> None:
        if not session_id or not turn_id:
            return
        session = self._repository.session(room_id, session_id)
        if turn_id != clean_room_text(session.get("active_turn_id"), limit=128):
            return
        same_turn = turn_id == clean_room_text(
            session.get("write_budget_turn_id"),
            limit=128,
        )
        command_count = int(session.get("write_budget_stream_commands") or 0) if same_turn else 0
        byte_count = int(session.get("write_budget_stream_bytes") or 0) if same_turn else 0
        if command_count + 1 > self.policy.max_turn_stream_commands:
            raise RoomCommandRejected(
                "Active turn stream command budget exceeded.",
                code="turn_write_budget_exceeded",
            )
        if byte_count + payload_bytes > self.policy.max_turn_stream_bytes:
            raise RoomCommandRejected(
                "Active turn stream payload budget exceeded.",
                code="turn_write_budget_exceeded",
            )
        self._repository.update_session_fields(
            room_id,
            session_id,
            write_budget_turn_id=turn_id,
            write_budget_stream_commands=command_count + 1,
            write_budget_stream_bytes=byte_count + payload_bytes,
        )


def _command_size(
    request_id: str,
    action: str,
    payload: dict[str, object],
) -> int:
    try:
        return len(
            json.dumps(
                {
                    "request_id": request_id,
                    "action": action,
                    "payload": payload,
                },
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
        )
    except (TypeError, ValueError) as error:
        raise RoomCommandRejected(
            "Room command payload is not JSON serializable.",
            code="bad_request",
        ) from error


__all__ = ["RoomWriteBudget", "RoomWriteBudgetPolicy"]
