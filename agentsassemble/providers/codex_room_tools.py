"""Codex app-server tools for the private autonomous-room portal."""

from __future__ import annotations

from agentsassemble.providers.room_portal import (
    RoomPortal,
    RoomPortalError,
    VIRTUAL_ROOM_OUTBOX_PATH,
    VIRTUAL_ROOM_VIEW_PATH,
)


ROOM_READ_TOOL = "agentsassemble_room_read"
ROOM_SPEAK_TOOL = "agentsassemble_room_speak"


class CodexRoomTools:
    """Expose room observation without granting the model filesystem writes."""

    def __init__(self, portal: RoomPortal) -> None:
        self.portal = portal

    def specs(self) -> list[dict[str, object]]:
        return [
            {
                "type": "function",
                "name": ROOM_READ_TOOL,
                "description": "Read the current shared-room view and available media references.",
                "inputSchema": {
                    "type": "object",
                    "properties": {},
                    "additionalProperties": False,
                },
            },
            {
                "type": "function",
                "name": ROOM_SPEAK_TOOL,
                "description": "Publish one message to the shared room when you choose to speak.",
                "inputSchema": {
                    "type": "object",
                    "required": ["content"],
                    "properties": {
                        "content": {
                            "type": "string",
                            "description": "The exact room-visible message.",
                        }
                    },
                    "additionalProperties": False,
                },
            },
        ]

    def handle(self, tool: str, arguments: object) -> dict[str, object]:
        values = arguments if isinstance(arguments, dict) else {}
        if tool == ROOM_READ_TOOL:
            try:
                room_view = self.portal.acp_read_text(VIRTUAL_ROOM_VIEW_PATH)
            except OSError as error:
                raise RoomPortalError("The shared-room view is unavailable.") from error
            return _text_result(room_view)
        if tool == ROOM_SPEAK_TOOL:
            content = values.get("content")
            if not isinstance(content, str) or not content.strip():
                raise RoomPortalError("A room publication requires content.")
            self.portal.acp_write_text(VIRTUAL_ROOM_OUTBOX_PATH, content)
            return _text_result("Published to the shared room.")
        raise RoomPortalError("Unknown shared-room tool.")


def _text_result(text: str) -> dict[str, object]:
    return {
        "success": True,
        "contentItems": [{"type": "inputText", "text": text}],
    }


__all__ = ["CodexRoomTools", "ROOM_READ_TOOL", "ROOM_SPEAK_TOOL"]
