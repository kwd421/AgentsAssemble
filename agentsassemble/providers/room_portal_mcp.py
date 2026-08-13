"""Stdio MCP boundary for one provider session's private room portal."""

from __future__ import annotations

import argparse
from pathlib import Path

from agentsassemble.providers.room_portal import RoomPortal
from agentsassemble.providers.room_tool_names import provider_room_tool_name


def serve_room_portal_mcp(root: str | Path) -> None:
    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError as error:  # pragma: no cover - deployment dependency check
        raise RuntimeError("The room portal MCP server requires mcp>=1,<2.") from error

    portal = RoomPortal(
        Path(root).expanduser().resolve(),
        participant_id="room-portal-mcp",
    )
    server = FastMCP("AgentsAssemble Room Portal", json_response=True)

    @server.tool()
    def read_discussion() -> str:
        """Read the finalized messages currently visible in the shared room."""
        return portal.read_discussion()

    @server.tool()
    def list_participants() -> list[dict[str, str]]:
        """List the people and agents currently visible in the shared room."""
        return portal.list_participants()

    @server.tool()
    def publish_message(content: str, next_agent_id: str = "") -> str:
        """Publish one substantive message, optionally handing the floor to one agent."""
        portal.publish_message(content, next_agent_id=next_agent_id)
        return "Published to the shared room."

    @server.tool()
    def decline_to_speak(reason_code: str) -> dict[str, object]:
        """End this wake without posting when speaking would add no value."""
        return portal.decline_to_speak(reason_code)

    @server.tool()
    def create_vote(
        question: str,
        options: list[str],
        duration_seconds: int = 0,
    ) -> dict[str, object]:
        """Create one structured room vote as this turn's public action."""
        return portal.create_vote(
            question,
            options,
            duration_seconds=duration_seconds,
        )

    @server.tool()
    def cast_vote(vote_id: str, choice: str) -> dict[str, object]:
        """Cast or replace this agent's ballot in an existing vote."""
        return portal.cast_vote(vote_id, choice)

    @server.tool()
    def vote_summary(vote_id: str) -> dict[str, object]:
        """Summarize a vote from this session's bounded current room view."""
        return portal.vote_summary(vote_id)

    @server.tool()
    def roll_dice(notation: str, reason: str = "") -> dict[str, object]:
        """Roll bounded NdS±M dice using server-side randomness."""
        return portal.roll_dice(notation, reason=reason)

    @server.tool()
    def choose_random(options: list[str], reason: str = "") -> dict[str, object]:
        """Choose one item from 2 to 50 options using server-side randomness."""
        return portal.choose_random(options, reason=reason)

    @server.tool(name=provider_room_tool_name("rimworld.observe"))
    def rimworld_observe() -> dict[str, object]:
        """Observe colony-wide state for this session's assigned colonist."""
        return portal.activity_plugin_observe()

    @server.tool(name=provider_room_tool_name("rimworld.inspect"))
    def rimworld_inspect(
        target_type: str,
        target_id: str = "",
        x: int = 0,
        y: int = 0,
    ) -> dict[str, object]:
        """Inspect one colonist, structure collection, or map cell."""
        return portal.activity_plugin_inspect(
            {
                "target_type": target_type,
                "target_id": target_id,
                "x": x,
                "y": y,
            }
        )

    @server.tool(name=provider_room_tool_name("rimworld.act"))
    def rimworld_act(
        action: str,
        action_args: dict[str, object] | None = None,
    ) -> dict[str, object]:
        """Stage one action. build uses kind/x/y; valid kinds: bed, wall, door,
        table, campfire, workbench, storage. set_priorities uses a priorities
        map of known jobs to 1-4; set_job uses job and optional target. eat,
        sleep, recreation, and choose_work use an empty action_args object."""
        return portal.activity_plugin_act(action, action_args or {})

    @server.tool(name=provider_room_tool_name("rimworld.speak"))
    def rimworld_speak(text: str) -> dict[str, object]:
        """Stage one in-character colony side-chat line."""
        return portal.activity_plugin_speak(text)

    server.run(transport="stdio")


def room_portal_mcp_settings(root: str | Path) -> dict[str, object]:
    import sys

    arguments = [
        "--root",
        str(Path(root).expanduser().resolve()),
    ]
    if getattr(sys, "frozen", False):
        command_arguments = ["--internal-room-portal-mcp", *arguments]
    else:
        command_arguments = [
            "-m",
            "agentsassemble.providers.room_portal_mcp",
            *arguments,
        ]
    return {
        "command": sys.executable,
        "args": command_arguments,
        "cwd": str(Path(__file__).resolve().parents[2]),
    }


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True)
    args = parser.parse_args(argv)
    serve_room_portal_mcp(args.root)


if __name__ == "__main__":
    main()
