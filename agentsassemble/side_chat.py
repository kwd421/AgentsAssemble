"""Compatibility exports for the optional side-chat service."""
from agentsassemble.features.side_chat.service import (
    append_side_chat_event,
    read_side_chat,
)

__all__ = ["append_side_chat_event", "read_side_chat"]
