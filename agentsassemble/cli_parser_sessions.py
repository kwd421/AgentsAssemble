"""Legacy session command parser registrations."""
from __future__ import annotations

import argparse

from agentsassemble.cli_parser_common import _hide_subparser_from_help
from agentsassemble.codex_sessions import (
    DEFAULT_INVITE_CONFIG_PATH,
    DEFAULT_LIVE_AGENT_CONFIG_PATH,
)


def register_sessions_parsers(subparsers: argparse._SubParsersAction) -> None:
    sessions = subparsers.add_parser("sessions", help="Inspect and invite Codex CLI live sessions.")
    _hide_subparser_from_help(subparsers, "sessions")
    sessions.add_argument("--legacy-internal", action="store_true", help=argparse.SUPPRESS)
    session_subparsers = sessions.add_subparsers(dest="sessions_command", required=True)

    session_list = session_subparsers.add_parser("list", help="List recent Codex CLI sessions.")
    session_list.add_argument("--limit", type=int, default=20)
    session_list.add_argument("--json", action="store_true", dest="as_json")

    session_invite = session_subparsers.add_parser("invite", help="Bind a Codex CLI session to a meeting role.")
    session_invite.add_argument("session_id")
    session_invite.add_argument("--role", required=True, dest="role_id")
    session_invite.add_argument("--server", default="", help="Room server URL; when set, record the invite through the GUI control plane.")
    session_invite.add_argument("--meeting-id", default="", help="Meeting id for server-side role validation.")
    session_invite.add_argument("--output", default=str(DEFAULT_INVITE_CONFIG_PATH))
    session_invite.add_argument("--json", action="store_true", dest="as_json")

    session_live_agent_config = session_subparsers.add_parser(
        "live-agent-config",
        help="Build a resident live-agent run-group config from a Codex invite config.",
    )
    session_live_agent_config.add_argument("--input", default=str(DEFAULT_INVITE_CONFIG_PATH), dest="input_path")
    session_live_agent_config.add_argument("--output", default=str(DEFAULT_LIVE_AGENT_CONFIG_PATH))
    session_live_agent_config.add_argument("--server", default="http://127.0.0.1:8765")
    session_live_agent_config.add_argument("--meeting-id", default="")
    session_live_agent_config.add_argument("--engagement-mode", default="moderator_called")
    session_live_agent_config.add_argument("--json", action="store_true", dest="as_json")
