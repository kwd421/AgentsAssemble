from __future__ import annotations

from dataclasses import dataclass

from agentsassemble.meeting_events import clean_lobby_text

ROOM_COMMAND_ACTIONS = frozenset(
    {
        "room.history",
        "room.delete",
        "message.send",
        "agent.create",
        "agent.readd",
        "agent.configure",
        "agent.start",
        "agent.pause",
        "agent.stop",
        "agent.resume",
        "agent.interrupt",
        "participant.kick",
        "participant.mute",
        "participant.leave",
        "bridge.ready",
        "bridge.health",
        "bridge.stopped",
        "room.observed",
        "turn.state",
        "turn.decline",
        "activity.update",
        "message.delta",
        "message.final",
        "turn.failed",
    }
)


@dataclass(frozen=True)
class ParsedRoomCommand:
    request_id: str
    action: str
    payload: dict[str, object]


class RoomCommandValidationError(ValueError):
    def __init__(self, message: str, *, code: str) -> None:
        super().__init__(message)
        self.code = code


def parse_room_command(message: dict[str, object]) -> ParsedRoomCommand:
    request_id = clean_lobby_text(message.get("request_id"), limit=128)
    action = clean_lobby_text(message.get("action"), limit=64)
    payload = dict(message.get("payload")) if isinstance(message.get("payload"), dict) else {}
    if not request_id:
        raise RoomCommandValidationError("request_id is required.", code="bad_request")
    if action not in ROOM_COMMAND_ACTIONS:
        raise RoomCommandValidationError(f"Unsupported room command: {action}", code="unknown_action")
    return ParsedRoomCommand(request_id=request_id, action=action, payload=payload)


def capabilities_for_identity(identity: dict[str, object]) -> dict[str, bool]:
    operator = bool(identity.get("operator"))
    bridge = identity.get("client_type") == "agent_bridge"
    read_write = str(identity.get("invite_scope") or "read_write") != "read_only"
    return {
        "room.history": not bridge,
        "message.send": read_write and not bridge,
        "room.manage": operator,
        "room.delete": operator,
        "participant.leave": not bridge,
        "participant.kick": operator,
        "participant.mute": operator,
        "agent.control": operator,
        "bridge.report": bridge,
    }
