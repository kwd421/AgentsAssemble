"""Shared identity admission for canonical room commands."""

from __future__ import annotations

from agentsassemble.room.commands import capabilities_for_identity
from agentsassemble.room.errors import RoomCommandRejected


_BRIDGE_ONLY_ACTIONS = frozenset(
    {
        "bridge.ready",
        "bridge.start_failed",
        "bridge.health",
        "bridge.stopped",
        "room.observed",
        "room.check",
        "room.result.publish",
        "room.attachment.read",
        "turn.state",
        "turn.decline",
        "activity.update",
        "message.delta",
        "message.final",
        "turn.failed",
        "provider.request.open",
        "provider.request.closed",
    }
)
_BROWSER_ONLY_ACTIONS = frozenset({"room.history", "room.vote.summary"})
_CAPABILITY_BY_ACTION = {
    "bridge.start_failed": "bridge.publish",
    "room.result.publish": "bridge.publish",
    "turn.state": "bridge.publish",
    "turn.decline": "bridge.publish",
    "turn.failed": "bridge.publish",
    "activity.update": "bridge.publish",
    "message.delta": "bridge.publish",
    "message.final": "bridge.publish",
    "provider.request.open": "bridge.publish",
    "provider.request.closed": "bridge.publish",
    "room.random.roll": "room.random",
    "room.random.choose": "room.random",
    "room.archive": "room.manage",
    "room.close": "room.manage",
    "room.delete": "room.delete",
    "room.settings.update": "room.manage",
    "message.send": "message.send",
    "agent.create": "agent.control",
    "agent.readd": "agent.control",
    "agent.configure": "agent.control",
    "agent.start": "agent.control",
    "agent.pause": "agent.control",
    "agent.stop": "agent.control",
    "agent.resume": "agent.control",
    "agent.interrupt": "agent.control",
    "participant.kick": "participant.kick",
    "participant.export": "participant.kick",
    "participant.mute": "participant.mute",
    "participant.role.update": "room.manage",
    "participant.leave": "participant.leave",
    "provider.request.resolve": "provider.request.resolve",
}


def authorize_room_command(identity: dict[str, object], action: str) -> None:
    """Reject identities that cannot invoke ``action`` before shared budgets."""

    bridge = identity.get("client_type") == "agent_bridge"
    if action in _BRIDGE_ONLY_ACTIONS:
        if not bridge:
            raise RoomCommandRejected(
                "This command is reserved for an Agent Bridge.",
                code="permission_denied",
            )
    if action in _BROWSER_ONLY_ACTIONS and bridge:
        raise RoomCommandRejected(
            "This command is reserved for a room participant.",
            code="permission_denied",
        )
    capability = _CAPABILITY_BY_ACTION.get(action)
    if capability and not capabilities_for_identity(identity).get(capability, False):
        raise RoomCommandRejected(
            f"{capability} permission is required.",
            code="permission_denied",
        )


__all__ = ["authorize_room_command"]
