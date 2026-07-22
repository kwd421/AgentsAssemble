"""Execution for the retained internal MCP CLI adapter."""
from __future__ import annotations

import argparse
import sys


def run_mcp_command(args: argparse.Namespace) -> int:
    if not bool(getattr(args, "legacy_internal", False)):
        print(
            "MCP is a legacy/internal room adapter; use Agent Session room commands instead.",
            file=sys.stderr,
        )
        return 2
    try:
        if args.mcp_command == "serve":
            from agentsassemble.legacy.live_agent.mcp_server import serve_mcp

            serve_mcp(
                profile=args.profile,
                server=args.server,
                agent_id=args.agent_id,
                meeting_id=args.meeting_id,
                display_name=args.display_name,
                provider_kind=args.provider_kind,
                connection_kind=args.connection_kind,
                engagement_mode=args.engagement_mode,
            )
            return 0
    except (RuntimeError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    return 1
