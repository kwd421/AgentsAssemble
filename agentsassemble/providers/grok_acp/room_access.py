"""Room portal permission policy for Grok ACP tool requests."""

from __future__ import annotations

from agentsassemble.providers.room_portal import (
    VIRTUAL_ROOM_OUTBOX_PATH,
    direct_outbox_target,
)
from agentsassemble.providers.terminal_interactions import is_safe_room_roll_command
from agentsassemble.room.text import clean_room_text


_MAX_PERMISSION_COMMAND_CHARS = 600


def permission_is_room_outbox_write(
    params: dict[str, object],
    tool_call: dict[str, object],
    *,
    session_id: str,
    active_room_observation: bool,
    cached: dict[str, object],
) -> bool:
    tool_call_id = permission_tool_call_id(params, tool_call)
    if (
        not tool_call_id
        or not active_room_observation
        or str(params.get("sessionId") or "") != session_id
    ):
        return False
    raw_input = (
        tool_call.get("rawInput")
        if isinstance(tool_call.get("rawInput"), dict)
        else {}
    )
    cached_input = (
        cached.get("rawInput")
        if isinstance(cached.get("rawInput"), dict)
        else {}
    )
    if raw_input.get("command") or cached_input.get("command"):
        return False
    identity = " ".join(
        clean_room_text(value, limit=120)
        for value in (
            tool_call.get("name"),
            tool_call.get("title"),
            cached.get("name"),
            cached.get("title"),
            cached.get("label"),
        )
        if value
    ).casefold()
    if not any(
        word in identity
        for word in ("write", "write_file", "write text", "fs/write_text_file")
    ):
        return False
    targets: list[str] = []
    for values in (tool_call, raw_input, cached, cached_input):
        for key in ("file_path", "path", "target_file"):
            value = values.get(key)
            if isinstance(value, str) and value:
                targets.append(value)
    for values in (tool_call, cached):
        for key in ("location", "locations"):
            locations = values.get(key)
            if isinstance(locations, dict):
                locations = [locations]
            if isinstance(locations, list):
                targets.extend(
                    str(location.get("path") or "")
                    for location in locations
                    if isinstance(location, dict) and location.get("path")
                )
    return bool(targets) and all(
        target == VIRTUAL_ROOM_OUTBOX_PATH or bool(direct_outbox_target(target))
        for target in targets
    )


def permission_is_room_roll(
    params: dict[str, object],
    tool_call: dict[str, object],
    *,
    session_id: str,
    active_room_observation: bool,
    cached: dict[str, object],
) -> bool:
    tool_call_id = permission_tool_call_id(params, tool_call)
    if (
        not tool_call_id
        or not active_room_observation
        or str(params.get("sessionId") or "") != session_id
    ):
        return False
    raw_input = (
        tool_call.get("rawInput")
        if isinstance(tool_call.get("rawInput"), dict)
        else {}
    )
    cached_input = (
        cached.get("rawInput")
        if isinstance(cached.get("rawInput"), dict)
        else {}
    )
    if (
        raw_input.get("_command_overflow")
        or cached_input.get("_command_overflow")
        or cached_input.get("_command_conflict")
        or cached.get("_identity_conflict")
    ):
        return False
    tool_names = [
        clean_room_text(value, limit=120).casefold()
        for value in (
            tool_call.get("name"),
            cached.get("name"),
        )
        if value
    ]
    if not tool_names or any(name != "run_terminal_command" for name in tool_names):
        return False
    commands = [
        command
        for command in (
            raw_input.get("command"),
            cached_input.get("command"),
        )
        if isinstance(command, str) and command
    ]
    return bool(commands) and all(
        is_safe_room_roll_command(command)
        for command in commands
    )


def room_outbox_path(
    tool_call: dict[str, object],
    *,
    cached: dict[str, object],
) -> str:
    raw_input = (
        tool_call.get("rawInput")
        if isinstance(tool_call.get("rawInput"), dict)
        else {}
    )
    cached_input = (
        cached.get("rawInput")
        if isinstance(cached.get("rawInput"), dict)
        else {}
    )
    for values in (tool_call, raw_input, cached, cached_input):
        for key in ("file_path", "path", "target_file"):
            value = str(values.get(key) or "")
            if value == VIRTUAL_ROOM_OUTBOX_PATH or direct_outbox_target(value):
                return value
    for values in (tool_call, cached):
        for key in ("location", "locations"):
            locations = values.get(key)
            if isinstance(locations, dict):
                locations = [locations]
            if not isinstance(locations, list):
                continue
            for location in locations:
                if not isinstance(location, dict):
                    continue
                value = str(location.get("path") or "")
                if value == VIRTUAL_ROOM_OUTBOX_PATH or direct_outbox_target(value):
                    return value
    return ""


def room_outbox_content(
    tool_call: dict[str, object],
    *,
    cached: dict[str, object],
) -> str:
    raw_input = (
        tool_call.get("rawInput")
        if isinstance(tool_call.get("rawInput"), dict)
        else {}
    )
    cached_input = (
        cached.get("rawInput")
        if isinstance(cached.get("rawInput"), dict)
        else {}
    )
    content = raw_input.get("content") or cached_input.get("content")
    return content if isinstance(content, str) and content.strip() else ""


def permission_context_update(
    message: dict[str, object],
    *,
    active_session_id: str,
    active_room_observation: bool,
) -> tuple[tuple[str, str], dict[str, object]] | None:
    params = message.get("params") if isinstance(message.get("params"), dict) else {}
    update = params.get("update") if isinstance(params.get("update"), dict) else {}
    if str(update.get("sessionUpdate") or "") not in {"tool_call", "tool_call_update"}:
        return None
    tool_call_id = _bounded_tool_call_id(
        update.get("toolCallId") or update.get("tool_call_id"),
    )
    update_session_id = clean_room_text(params.get("sessionId"), limit=128)
    if (
        not tool_call_id
        or not active_room_observation
        or not update_session_id
        or update_session_id != active_session_id
    ):
        return None
    metadata = update.get("_meta") if isinstance(update.get("_meta"), dict) else {}
    tool_metadata = (
        metadata.get("x.ai/tool")
        if isinstance(metadata.get("x.ai/tool"), dict)
        else {}
    )
    raw_input = update.get("rawInput") if isinstance(update.get("rawInput"), dict) else {}
    permission_input: dict[str, object] = {}
    for key in ("command", "content", "file_path", "path", "target_file"):
        value = raw_input.get(key)
        if not isinstance(value, str) or not value:
            continue
        if key == "content":
            permission_input[key] = value[:12000]
        elif key == "command":
            if len(value) > _MAX_PERMISSION_COMMAND_CHARS:
                permission_input["_command_overflow"] = True
            else:
                permission_input[key] = value
        else:
            permission_input[key] = clean_room_text(value, limit=600)
    locations: list[dict[str, str]] = []
    for key in ("location", "locations"):
        values = update.get(key)
        if isinstance(values, dict):
            values = [values]
        if isinstance(values, list):
            locations.extend(
                {"path": str(value.get("path"))}
                for value in values
                if isinstance(value, dict) and value.get("path")
            )
    incoming = {
        "name": clean_room_text(
            tool_metadata.get("name") or update.get("name"),
            limit=120,
        ),
        "label": clean_room_text(tool_metadata.get("label"), limit=120),
        "title": clean_room_text(update.get("title"), limit=300),
        "rawInput": permission_input,
        "locations": locations,
        **{
            key: clean_room_text(update.get(key), limit=600)
            for key in ("file_path", "path", "target_file")
            if isinstance(update.get(key), str) and update.get(key)
        },
    }
    return (update_session_id, tool_call_id), incoming


def merge_permission_context(
    previous: dict[str, object],
    incoming: dict[str, object],
) -> dict[str, object]:
    merged = dict(previous)
    merged_input = (
        dict(merged.get("rawInput"))
        if isinstance(merged.get("rawInput"), dict)
        else {}
    )
    if isinstance(incoming.get("rawInput"), dict):
        previous_command = merged_input.get("command")
        incoming_command = incoming["rawInput"].get("command")
        if (
            isinstance(previous_command, str)
            and isinstance(incoming_command, str)
            and previous_command != incoming_command
        ):
            merged_input["_command_conflict"] = True
        merged_input.update(incoming["rawInput"])
    for key in ("name", "label", "title", "file_path", "path", "target_file"):
        if incoming.get(key):
            if key == "name" and merged.get(key) and merged[key] != incoming[key]:
                merged["_identity_conflict"] = True
            merged[key] = incoming[key]
    if incoming.get("locations"):
        merged["locations"] = incoming["locations"]
    merged["rawInput"] = merged_input
    return merged


def permission_tool_call_id(
    params: dict[str, object],
    tool_call: dict[str, object],
) -> str:
    return _bounded_tool_call_id(
        tool_call.get("toolCallId") or params.get("toolCallId"),
    )


def _bounded_tool_call_id(value: object) -> str:
    if not isinstance(value, str):
        return ""
    return value if value == clean_room_text(value, limit=128) else ""


__all__ = [
    "merge_permission_context",
    "permission_context_update",
    "permission_is_room_outbox_write",
    "permission_is_room_roll",
    "permission_tool_call_id",
    "room_outbox_content",
    "room_outbox_path",
]
