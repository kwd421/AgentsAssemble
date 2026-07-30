"""Room portal permission policy for Grok ACP tool requests."""

from __future__ import annotations

from agentsassemble.room.text import clean_room_text


_ROOM_MCP_SERVER_NAME = "agentsassemble_room"
_ROOM_MCP_TOOL_NAMES = frozenset(
    {"read_discussion", "publish_message", "roll_dice", "choose_random"}
)
# Clients qualify an MCP tool with its server name, and they do not agree on the
# separator: one underscore (agentsassemble_room_read_discussion, what the room
# MCP server is registered as and what opencode's permission glob matches) or
# two. Accepting only the double form denied every room tool call, so both
# spellings are allowed here and the bare tool name stays accepted for clients
# that do not qualify at all.
_ROOM_MCP_PERMISSION_NAMES = _ROOM_MCP_TOOL_NAMES | frozenset(
    f"{_ROOM_MCP_SERVER_NAME}{separator}{name}"
    for name in _ROOM_MCP_TOOL_NAMES
    for separator in ("_", "__")
)


def permission_is_room_mcp_tool(
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
        or cached.get("_identity_conflict")
    ):
        return False
    identities = [
        _normalized_tool_name(value)
        for value in (
            tool_call.get("name"),
            cached.get("name"),
        )
        if value
    ]
    return any(identity in _ROOM_MCP_PERMISSION_NAMES for identity in identities)


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
    incoming = {
        "name": clean_room_text(
            tool_metadata.get("name") or update.get("name"),
            limit=120,
        ),
    }
    return (update_session_id, tool_call_id), incoming


def merge_permission_context(
    previous: dict[str, object],
    incoming: dict[str, object],
) -> dict[str, object]:
    merged = dict(previous)
    incoming_name = incoming.get("name")
    if incoming_name:
        if merged.get("name") and merged["name"] != incoming_name:
            previous_normalized = _normalized_tool_name(merged["name"])
            incoming_normalized = _normalized_tool_name(incoming_name)
            if incoming_normalized in {"use_tool", "mcp_tool"}:
                return merged
            if previous_normalized not in {"use_tool", "mcp_tool"}:
                merged["_identity_conflict"] = True
        merged["name"] = incoming_name
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


def _normalized_tool_name(value: object) -> str:
    return "_".join(clean_room_text(value, limit=300).casefold().replace("-", "_").split())


__all__ = [
    "merge_permission_context",
    "permission_context_update",
    "permission_is_room_mcp_tool",
    "permission_tool_call_id",
]
