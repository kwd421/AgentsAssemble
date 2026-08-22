"""Server-owned room tool availability independent of conversation routing."""

from __future__ import annotations


CHAT_TOOL_MODE = "chat"
TABLETOP_TOOL_MODE = "tabletop"
ROOM_TOOL_MODES = frozenset({CHAT_TOOL_MODE, TABLETOP_TOOL_MODE})

CORE_ROOM_TOOLS = frozenset(
    {
        "read_discussion",
        "search_messages",
        "read_message_context",
        "list_participants",
        "publish_message",
        "decline_to_speak",
        "create_vote",
        "cast_vote",
        "vote_summary",
    }
)
TABLETOP_ROOM_TOOLS = CORE_ROOM_TOOLS | frozenset(
    {"roll_dice", "choose_random"}
)


def validate_room_tool_mode(value: object) -> str:
    if not isinstance(value, str) or value not in ROOM_TOOL_MODES:
        raise ValueError(f"Unsupported tool_mode: {value!r}.")
    return value


def room_tool_names(mode: object) -> frozenset[str]:
    canonical = validate_room_tool_mode(mode)
    return TABLETOP_ROOM_TOOLS if canonical == TABLETOP_TOOL_MODE else CORE_ROOM_TOOLS


def room_random_tools_enabled(mode: object) -> bool:
    return "roll_dice" in room_tool_names(mode)


__all__ = [
    "CHAT_TOOL_MODE",
    "CORE_ROOM_TOOLS",
    "ROOM_TOOL_MODES",
    "TABLETOP_ROOM_TOOLS",
    "TABLETOP_TOOL_MODE",
    "room_random_tools_enabled",
    "room_tool_names",
    "validate_room_tool_mode",
]
