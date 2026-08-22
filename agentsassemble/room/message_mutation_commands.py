"""Durable edit/delete command routing and post-commit cleanup."""

from __future__ import annotations

from collections.abc import Callable
from typing import ContextManager

from agentsassemble.room.command_uow import RoomCommandUnitOfWork
from agentsassemble.room.messages import RoomMessageService
from agentsassemble.room.text import clean_room_text

MESSAGE_MUTATION_ACTIONS = frozenset({"message.edit", "message.delete"})

DurableCommandExecutor = Callable[
    [
        dict[str, object],
        str,
        str,
        str,
        dict[str, object],
        Callable[[RoomCommandUnitOfWork], dict[str, object]],
    ],
    dict[str, object],
]


def execute_message_mutation(
    action: str,
    identity: dict[str, object],
    room_id: str,
    request_id: str,
    payload: dict[str, object],
    messages: RoomMessageService,
    lock: ContextManager[object],
    require_capability: Callable[[dict[str, object], str], None],
    execute_durable_command: DurableCommandExecutor,
    is_room_owner: Callable[[dict[str, object], str], bool],
    unpin_message: Callable[[str, str, str], bool],
) -> dict[str, object]:
    if action not in MESSAGE_MUTATION_ACTIONS:
        raise ValueError(f"Unsupported message mutation action: {action}")
    require_capability(identity, "message.modify")
    with lock:
        if action == "message.edit":
            return execute_durable_command(
                identity,
                room_id,
                request_id,
                action,
                payload,
                lambda unit: messages.edit_in_unit(identity, payload, unit=unit),
            )
        ack = execute_durable_command(
            identity,
            room_id,
            request_id,
            action,
            payload,
            lambda unit: messages.delete_in_unit(
                identity,
                payload,
                unit=unit,
                can_moderate=is_room_owner(identity, room_id),
            ),
        )
        result = dict(ack.get("result") or {})
        target_event_id = clean_room_text(result.get("target_event_id"), limit=128)
        if target_event_id:
            unpin_message(room_id, "lobby", target_event_id)
        messages.cleanup_deleted_attachments(result.get("attachment_ids"))
        return ack


__all__ = ["MESSAGE_MUTATION_ACTIONS", "execute_message_mutation"]
