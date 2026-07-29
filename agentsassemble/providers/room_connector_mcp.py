"""Stdio MCP tools that let the current app or CLI session join a room link."""

from __future__ import annotations

from agentsassemble.application.room_connector import RoomConnector


def serve_room_connector_mcp() -> None:
    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError as error:  # pragma: no cover - deployment dependency check
        raise RuntimeError("The room connector MCP server requires mcp>=1,<2.") from error

    connector = RoomConnector()
    server = FastMCP(
        "AgentsAssemble Room Connector",
        instructions=(
            "When the user provides an AgentsAssemble /join link, call room_join "
            "with that exact URL. Do not launch another model or implement HTTP or "
            "WebSocket calls yourself. Read, speak, wait, and leave only through "
            "these room tools."
        ),
        json_response=True,
    )

    @server.tool()
    def room_join(invite_url: str, display_name: str = "") -> dict[str, object]:
        """Join the supplied AgentsAssemble link as this current app or CLI session."""
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
