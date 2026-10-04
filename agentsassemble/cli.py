from __future__ import annotations

import argparse

from agentsassemble.application.cli.api_commands import run_api_call_command
from agentsassemble.application.cli.core_commands import (
    run_frontend_info_command,
    run_gui_command,
    run_release_health_command,
    run_rolling_restart_command,
)
from agentsassemble.application.cli.http import request_json, server_url
from agentsassemble.application.cli.persona_commands import run_persona_command
from agentsassemble.application.cli.room_commands import RoomCliRuntime, run_room_command
from agentsassemble.application.room_native_cli_smoke import run_room_native_cli_smoke
from agentsassemble.diagnostics.live_cli_smoke import DEFAULT_LIVE_CLI_SMOKE_CONFIG
from agentsassemble.gui import serve_gui
from agentsassemble.room.text import clean_room_text


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="assemble")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.metavar = (
        "{gui,frontend-info,release-health,rolling-restart,api-call,persona,room}"
    )

    from agentsassemble.application.cli.core import register_core_parsers
    from agentsassemble.application.cli.persona import register_persona_parsers
    from agentsassemble.application.cli.room import register_room_parsers

    register_core_parsers(subparsers)
    register_persona_parsers(subparsers)
    register_room_parsers(subparsers)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "gui":
        return run_gui_command(args, serve_gui=serve_gui)
    if args.command == "frontend-info":
        return run_frontend_info_command(args)
    if args.command == "release-health":
        return run_release_health_command(args)
    if args.command == "rolling-restart":
        return run_rolling_restart_command(args)
    if args.command == "api-call":
        return run_api_call_command(args)
    if args.command == "persona":
        return run_persona_command(args)
    if args.command == "room":
        return _run_room_command(args)
    return 1


def _run_room_command(args: argparse.Namespace) -> int:
    return run_room_command(
        args,
        runtime=RoomCliRuntime(
            request_json=request_json,
            server_url=server_url,
            clean_text=clean_room_text,
            run_codex_smoke=lambda *_args, **_kwargs: {
                "status": "not_run",
                "smoke": "removed",
            },
            run_native_smoke=run_room_native_cli_smoke,
            codex_smoke_commands=(),
            default_live_cli_smoke_config=DEFAULT_LIVE_CLI_SMOKE_CONFIG,
        ),
    )


if __name__ == "__main__":
    raise SystemExit(main())
