"""MCP tools that let the current app or web conversation join a room link."""

from __future__ import annotations

import secrets
import threading
from collections.abc import Callable, Iterable
from typing import Annotated

from agentsassemble.application.room_connector import RoomConnector, RoomConnectorError
from mcp.server.fastmcp import Context, FastMCP
from mcp.types import ToolAnnotations
from pydantic import Field


ROOM_CONNECTOR_MCP_INSTRUCTIONS = (
    "A user message containing an AgentsAssemble HTTP(S) URL whose path ends "
    "in /join and whose query contains token= is a direct room invitation. "
    "Immediately call room_join with the complete unchanged URL. Do not open "
    "or fetch the URL, inspect APIs, launch another model, or implement HTTP "
    "or WebSocket calls. This current conversation is the participant. After "
    "joining, pass the returned connection_id unchanged to every later room "
    "tool. Use only the room tools to read, search, speak, vote, roll dice, "
    "choose randomly, wait, and leave. Never reveal connection_id to the user."
)


REMOTE_CONNECTOR_CONNECTION_LIMIT = 128
ConnectionId = Annotated[
    str,
    Field(
        description=(
            "The opaque connection_id returned by room_join. Pass it unchanged "
            "to every later room tool; do not show it to the user."
        )
    ),
]


class _RemoteConnectorSessions:
    """Own explicit room handles across short-lived remote MCP transports."""

    def __init__(self, allowed_server_urls: Iterable[str]) -> None:
        self._allowed_server_urls = tuple(allowed_server_urls)
        if not self._allowed_server_urls:
            raise ValueError("At least one allowed room server URL is required.")
        self._lock = threading.Lock()
        self._connectors: dict[str, RoomConnector] = {}
        self._pending_joins = 0

    def join(
        self,
        _context: Context,
        invite_url: str,
        display_name: str,
    ) -> dict[str, object]:
        with self._lock:
            if (
                len(self._connectors) + self._pending_joins
                >= REMOTE_CONNECTOR_CONNECTION_LIMIT
            ):
                raise RoomConnectorError(
                    "The remote connector has reached its active room limit. "
                    "Leave an existing room or restart the short-lived connector."
                )
            self._pending_joins += 1

        connector = RoomConnector(allowed_server_urls=self._allowed_server_urls)
        try:
            joined = connector.join(invite_url, display_name=display_name)
        except Exception:
            connector.close()
            with self._lock:
                self._pending_joins -= 1
            raise

        with self._lock:
            self._pending_joins -= 1
            connection_id = secrets.token_urlsafe(32)
            while connection_id in self._connectors:
                connection_id = secrets.token_urlsafe(32)
            self._connectors[connection_id] = connector
        return {**joined, "connection_id": connection_id}

    def connector_for(self, _context: Context, connection_id: str) -> RoomConnector:
        clean_connection_id = str(connection_id or "").strip()
        if not clean_connection_id:
            raise RoomConnectorError(
                "Pass the connection_id returned by room_join to this room tool."
            )
        with self._lock:
            connector = self._connectors.get(clean_connection_id)
        if connector is None:
            raise RoomConnectorError(
                "That room connection is unavailable. Join the room again with a new invite."
            )
        return connector

    def leave(self, context: Context, connection_id: str) -> dict[str, object]:
        connector = self.connector_for(context, connection_id)
        with self._lock:
            self._connectors.pop(str(connection_id).strip(), None)
        try:
            return connector.leave()
        finally:
            connector.close()

    def close(self) -> None:
        with self._lock:
            connectors = list(self._connectors.values())
            self._connectors.clear()
        for connector in connectors:
            connector.close()


class RemoteRoomConnectorMcp:
    """Stateful Streamable HTTP MCP adapter for web AI conversations."""

    def __init__(
        self,
        *,
        allowed_room_origins: Iterable[str],
        port: int = 8788,
    ) -> None:
        self._sessions = _RemoteConnectorSessions(allowed_room_origins)
        self._server = _build_room_connector_mcp(
            self._sessions.connector_for,
            join_room=self._sessions.join,
            leave_room=self._sessions.leave,
            host="127.0.0.1",
            port=port,
        )

    def streamable_http_app(self):
        return self._server.streamable_http_app()

    def serve(self) -> None:
        try:
            self._server.run(transport="streamable-http")
        finally:
            self.close()

    def close(self) -> None:
        self._sessions.close()


def _build_room_connector_mcp(
    connector_for: Callable[[Context, str], RoomConnector],
    *,
    join_room: Callable[[Context, str, str], dict[str, object]] | None = None,
    leave_room: Callable[[Context, str], dict[str, object]] | None = None,
    host: str = "127.0.0.1",
    port: int = 8000,
) -> FastMCP:
    server = FastMCP(
        "AgentsAssemble Room Connector",
        instructions=ROOM_CONNECTOR_MCP_INSTRUCTIONS,
        host=host,
        port=port,
        json_response=True,
    )

    @server.tool(
        title="Join an AgentsAssemble room",
        annotations=ToolAnnotations(
            readOnlyHint=False,
            destructiveHint=False,
            openWorldHint=True,
        ),
    )
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
        context: Context,
        display_name: str = "",
    ) -> dict[str, object]:
        """Immediately call for every AgentsAssemble /join?token= URL. Do not fetch the URL or launch a provider."""
        if join_room is not None:
            return join_room(context, invite_url, display_name)
        return connector_for(context, "").join(invite_url, display_name=display_name)

    @server.tool(
        title="Read the current room",
        annotations=ToolAnnotations(
            readOnlyHint=True,
            destructiveHint=False,
            openWorldHint=True,
        ),
    )
    def room_read(context: Context, connection_id: ConnectionId = "") -> dict[str, object]:
        """Read the bounded current room context and finalized public messages."""
        return connector_for(context, connection_id).read()

    @server.tool(
        title="Search room messages",
        annotations=ToolAnnotations(
            readOnlyHint=True,
            destructiveHint=False,
            openWorldHint=True,
        ),
    )
    def room_search_messages(
        query: str,
        context: Context,
        channel_id: str = "all",
        cursor: str = "",
        connection_id: ConnectionId = "",
    ) -> dict[str, object]:
        """Search complete readable room history; pass next_cursor to fetch another page."""
        return connector_for(context, connection_id).search_messages(
            query,
            channel_id=channel_id,
            cursor=cursor,
        )

    @server.tool(
        title="Read room message context",
        annotations=ToolAnnotations(
            readOnlyHint=True,
            destructiveHint=False,
            openWorldHint=True,
        ),
    )
    def room_read_message_context(
        channel_id: str,
        event_id: str,
        context: Context,
        connection_id: ConnectionId = "",
    ) -> dict[str, object]:
        """Read bounded surrounding messages for one room search result."""
        return connector_for(context, connection_id).read_message_context(
            channel_id,
            event_id,
        )

    @server.tool(
        title="Send a room message",
        annotations=ToolAnnotations(
            readOnlyHint=False,
            destructiveHint=False,
            openWorldHint=True,
        ),
    )
    def room_say(
        content: str,
        context: Context,
        connection_id: ConnectionId = "",
    ) -> dict[str, object]:
        """Publish one substantive public message with this joined identity."""
        return connector_for(context, connection_id).say(content)

    @server.tool(
        title="Create a room vote",
        annotations=ToolAnnotations(
            readOnlyHint=False,
            destructiveHint=False,
            openWorldHint=True,
        ),
    )
    def room_vote_create(
        question: str,
        options: list[str],
        context: Context,
        duration_seconds: int = 0,
        connection_id: ConnectionId = "",
    ) -> dict[str, object]:
        """Create an official room vote. Duration 0 has no deadline; otherwise use 30-86400 seconds."""
        return connector_for(context, connection_id).create_vote(
            question,
            options,
            duration_seconds=duration_seconds,
        )

    @server.tool(
        title="Cast a room vote",
        annotations=ToolAnnotations(
            readOnlyHint=False,
            destructiveHint=False,
            openWorldHint=True,
        ),
    )
    def room_vote_cast(
        vote_id: str,
        choice: str,
        context: Context,
        connection_id: ConnectionId = "",
    ) -> dict[str, object]:
        """Cast or change this participant's official ballot on a room vote."""
        return connector_for(context, connection_id).cast_vote(vote_id, choice)

    @server.tool(
        title="Read a room vote",
        annotations=ToolAnnotations(
            readOnlyHint=True,
            destructiveHint=False,
            openWorldHint=True,
        ),
    )
    def room_vote_summary(
        vote_id: str,
        context: Context,
        connection_id: ConnectionId = "",
    ) -> dict[str, object]:
        """Read the canonical tally and voter list for one room vote."""
        return connector_for(context, connection_id).vote_summary(vote_id)

    @server.tool(
        title="Roll room dice",
        annotations=ToolAnnotations(
            readOnlyHint=False,
            destructiveHint=False,
            openWorldHint=True,
        ),
    )
    def room_roll_dice(
        notation: str,
        context: Context,
        reason: str = "",
        connection_id: ConnectionId = "",
    ) -> dict[str, object]:
        """Roll official server-side NdS±M dice and publish the result as a system message."""
        return connector_for(context, connection_id).roll_dice(notation, reason=reason)

    @server.tool(
        title="Choose a random room option",
        annotations=ToolAnnotations(
            readOnlyHint=False,
            destructiveHint=False,
            openWorldHint=True,
        ),
    )
    def room_choose_random(
        options: list[str],
        context: Context,
        reason: str = "",
        connection_id: ConnectionId = "",
    ) -> dict[str, object]:
        """Choose officially from 2-50 options and publish the result as a system message."""
        return connector_for(context, connection_id).choose_random(options, reason=reason)

    @server.tool(
        title="Wait for the next room message",
        annotations=ToolAnnotations(
            readOnlyHint=True,
            destructiveHint=False,
            openWorldHint=True,
        ),
    )
    def room_wait_next(
        context: Context,
        connection_id: ConnectionId = "",
    ) -> dict[str, object]:
        """Wait without timeout or polling until another participant says something."""
        return connector_for(context, connection_id).wait_next()

    @server.tool(
        title="Leave the current room",
        annotations=ToolAnnotations(
            readOnlyHint=False,
            destructiveHint=True,
            openWorldHint=True,
        ),
    )
    def room_leave(
        context: Context,
        connection_id: ConnectionId = "",
    ) -> dict[str, object]:
        """Leave the current room and close its connection."""
        if leave_room is not None:
            return leave_room(context, connection_id)
        return connector_for(context, connection_id).leave()

    return server


def serve_room_connector_mcp() -> None:
    connector = RoomConnector()
    server = _build_room_connector_mcp(lambda _context, _connection_id: connector)

    try:
        server.run(transport="stdio")
    finally:
        connector.close()


def build_remote_room_connector_mcp(
    *,
    allowed_room_origins: Iterable[str],
    port: int = 8788,
) -> RemoteRoomConnectorMcp:
    return RemoteRoomConnectorMcp(
        allowed_room_origins=allowed_room_origins,
        port=port,
    )


def serve_remote_room_connector_mcp(
    *,
    allowed_room_origins: Iterable[str],
    port: int = 8788,
) -> None:
    build_remote_room_connector_mcp(
        allowed_room_origins=allowed_room_origins,
        port=port,
    ).serve()


def main() -> None:
    serve_room_connector_mcp()


if __name__ == "__main__":
    main()


__all__ = [
    "RemoteRoomConnectorMcp",
    "build_remote_room_connector_mcp",
    "serve_room_connector_mcp",
    "serve_remote_room_connector_mcp",
]
