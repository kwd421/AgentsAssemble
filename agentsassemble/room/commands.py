from __future__ import annotations

from dataclasses import dataclass

from agentsassemble.room.identity import room_identity_is_operator
from agentsassemble.room.text import clean_room_text as clean_lobby_text

ROOM_COMMAND_ACTIONS = frozenset(
    {
        "room.history",
        "room.vote.summary",
        "room.random.roll",
        "room.random.choose",
        "room.archive",
        "room.close",
        "room.delete",
        "room.settings.update",
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
        "participant.export",
        "participant.mute",
        "participant.role.update",
        "participant.leave",
        "bridge.ready",
        "bridge.start_failed",
        "bridge.health",
        "bridge.stopped",
        "room.observed",
        "room.check",
        "room.result.publish",
        "room.publication.stage",
        "room.attachment.read",
        "turn.state",
        "turn.decline",
        "activity.update",
        "message.delta",
        "message.final",
        "turn.failed",
        "provider.request.open",
        "provider.request.resolve",
        "provider.request.closed",
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
    operator = room_identity_is_operator(identity)
    bridge = identity.get("client_type") == "agent_bridge"
    read_write = str(identity.get("invite_scope") or "read_write") != "read_only"
    return {
        "room.history": not bridge,
        "room.vote.summary": not bridge,
        "room.random": read_write and not bridge,
        "message.send": read_write and not bridge,
        "room.manage": operator,
        "room.delete": operator,
        "participant.leave": not bridge,
        "participant.kick": operator,
        "participant.mute": operator,
        "agent.control": operator,
        "provider.request.resolve": read_write and not bridge,
        "bridge.report": bridge,
    }


__all__ = [
    "ROOM_COMMAND_ACTIONS",
    "ParsedRoomCommand",
    "RoomCommandValidationError",
    "capabilities_for_identity",
    "parse_room_command",
]
