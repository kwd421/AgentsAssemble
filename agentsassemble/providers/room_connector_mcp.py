"""Stdio MCP tools that let the current app or CLI session join a room link."""

from __future__ import annotations

from typing import Annotated

from agentsassemble.application.room_connector import RoomConnector
from pydantic import Field


def serve_room_connector_mcp() -> None:
    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError as error:  # pragma: no cover - deployment dependency check
        raise RuntimeError("The room connector MCP server requires mcp>=1,<2.") from error

    connector = RoomConnector()
    server = FastMCP(
        "AgentsAssemble Room Connector",
        instructions=(
            "A user message containing an AgentsAssemble HTTP(S) URL whose path "
            "ends in /join and whose query contains token= is a direct room "
            "invitation. Immediately call room_join with the complete unchanged "
            "URL. Do not open or fetch the URL, inspect APIs, launch another model, "
            "or implement HTTP or WebSocket calls. This current conversation is "
            "the participant. After joining, use only the room tools to read, "
            "speak, vote, roll dice, choose randomly, wait, and leave."
        ),
        json_response=True,
    )

    @server.tool()
    def room_join(
        invite_url: Annotated[
            str,
            Field(
                description=(
                    "The complete unchanged AgentsAssemble HTTP(S) "
                    "/join?token=... URL from the user's message."
                )
            ),
        ],
        display_name: str = "",
    ) -> dict[str, object]:
        """Immediately call for every AgentsAssemble /join?token= URL. Do not fetch the URL or launch a provider."""
        return connector.join(invite_url, display_name=display_name)

    @server.tool()
    def room_read() -> dict[str, object]:
        """Read the bounded current room context and finalized public messages."""
        return connector.read()

    @server.tool()
    def room_say(content: str) -> dict[str, object]:
        """Publish one substantive public message with this joined identity."""
        return connector.say(content)

    @server.tool()
    def room_vote_create(
        question: str,
        options: list[str],
        duration_seconds: int = 0,
    ) -> dict[str, object]:
        """Create an official room vote. Duration 0 has no deadline; otherwise use 30-86400 seconds."""
        return connector.create_vote(
            question,
            options,
            duration_seconds=duration_seconds,
        )

    @server.tool()
    def room_vote_cast(vote_id: str, choice: str) -> dict[str, object]:
        """Cast or change this participant's official ballot on a room vote."""
        return connector.cast_vote(vote_id, choice)

    @server.tool()
    def room_vote_summary(vote_id: str) -> dict[str, object]:
        """Read the canonical tally and voter list for one room vote."""
        return connector.vote_summary(vote_id)

    @server.tool()
    def room_roll_dice(notation: str, reason: str = "") -> dict[str, object]:
        """Roll official server-side NdS±M dice and publish the result as a system message."""
        return connector.roll_dice(notation, reason=reason)

    @server.tool()
    def room_choose_random(
        options: list[str],
        reason: str = "",
    ) -> dict[str, object]:
        """Choose officially from 2-50 options and publish the result as a system message."""
        return connector.choose_random(options, reason=reason)

    @server.tool()
    def room_wait_next() -> dict[str, object]:
        """Wait without timeout or polling until another participant says something."""
        return connector.wait_next()

    @server.tool()
    def room_leave() -> dict[str, object]:
        """Leave the current room and close its connection."""
        return connector.leave()

    try:
        server.run(transport="stdio")
    finally:
        connector.close()


def main() -> None:
    serve_room_connector_mcp()


if __name__ == "__main__":
    main()


__all__ = [
    "serve_room_connector_mcp",
]
