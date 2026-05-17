from __future__ import annotations

import argparse
from pathlib import Path

from agentsassemble.bridges.claude_code_bridge import serve_bridge
from agentsassemble.gui import serve_gui
from agentsassemble.meeting import run_demo_meeting


def parse_codex_timeout(value: str) -> int | None:
    if value.casefold() in {"none", "off", "unlimited", "0"}:
        return None
    timeout = int(value)
    if timeout < 0:
        raise argparse.ArgumentTypeError("timeout must be positive, 0, or none")
    return timeout


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="assemble")
    subparsers = parser.add_subparsers(dest="command", required=True)

    demo = subparsers.add_parser("demo", help="Run the canned v0 council demo.")
    demo.add_argument("--adapter", choices=["mock", "codex", "codex-live"], default="mock")
    demo.add_argument("--output-root", default=".agentsassemble")
    demo.add_argument(
        "--codex-timeout",
        type=parse_codex_timeout,
        default=None,
        help="Seconds per Codex call. Use 'none' or omit for no forced timeout.",
    )
    demo.add_argument("--no-codex-search", action="store_true")
    demo.add_argument("--research-depth", choices=["smoke", "standard", "deep"], default="smoke")
    demo.add_argument("--council-config", default=None, help="Optional JSON file describing the meeting topic and roles.")
    demo.add_argument("--agent-config", default=None, help="Optional JSON file with host-approved providers, permissions, and agent bindings.")
    demo.add_argument(
        "--meeting-mode",
        choices=["debate", "free-chat"],
        default=None,
        help="Run a moderated debate or a non-official free-chat room.",
    )
    demo.add_argument(
        "--moderator",
        choices=["on", "off"],
        default=None,
        help="Enable or disable moderator synthesis for debate mode.",
    )
    demo.add_argument("--follow-up-of", default=None, help="Optional parent meeting id for a follow-up council.")
    demo.add_argument("--follow-up-from", default=None, help="Optional parent meeting directory to reopen as a follow-up council.")
    demo.add_argument("--follow-up-note", default=None, help="Optional note explaining what the follow-up should reopen or continue.")
    demo.add_argument(
        "--research-steering",
        default=None,
        help="Optional user-preferred angle to investigate in extra detail without forcing the conclusion.",
    )

    gui = subparsers.add_parser("gui", help="Run the local browser GUI.")
    gui.add_argument("--host", default="127.0.0.1")
    gui.add_argument("--port", type=int, default=8765)
    gui.add_argument("--output-root", default=".agentsassemble")

    bridge = subparsers.add_parser("claude-bridge", help="Run a friend-owned Claude Code bridge.")
    bridge.add_argument("--host", default="127.0.0.1")
    bridge.add_argument("--port", type=int, default=8777)
    bridge.add_argument("--token", required=True)
    bridge.add_argument("--command", dest="bridge_command", default="claude")

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.command == "demo":
        run_demo_meeting(
            adapter_name=args.adapter,
            output_root=Path(args.output_root),
            reporter=lambda message: print(message, flush=True),
            codex_timeout_seconds=args.codex_timeout,
            codex_search_enabled=not args.no_codex_search,
            research_depth=args.research_depth,
            research_steering=args.research_steering,
            council_config_path=args.council_config,
            agent_config_path=args.agent_config,
            meeting_mode="free_chat" if args.meeting_mode == "free-chat" else args.meeting_mode,
            moderator_enabled=None if args.moderator is None else args.moderator == "on",
            follow_up_of=args.follow_up_of,
            follow_up_from=args.follow_up_from,
            follow_up_note=args.follow_up_note,
        )
        return 0
    if args.command == "gui":
        serve_gui(host=args.host, port=args.port, output_root=Path(args.output_root))
        return 0
    if args.command == "claude-bridge":
        serve_bridge(host=args.host, port=args.port, token=args.token, command=args.bridge_command)
        return 0

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
