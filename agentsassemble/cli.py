from __future__ import annotations

import argparse
from pathlib import Path

from agentsassemble.gui import serve_gui
from agentsassemble.meeting import run_demo_meeting


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="assemble")
    subparsers = parser.add_subparsers(dest="command", required=True)

    demo = subparsers.add_parser("demo", help="Run the canned v0 council demo.")
    demo.add_argument("--adapter", choices=["mock", "codex"], default="mock")
    demo.add_argument("--output-root", default=".agentsassemble")
    demo.add_argument("--codex-timeout", type=int, default=240)
    demo.add_argument("--no-codex-search", action="store_true")

    gui = subparsers.add_parser("gui", help="Run the local browser GUI.")
    gui.add_argument("--host", default="127.0.0.1")
    gui.add_argument("--port", type=int, default=8765)
    gui.add_argument("--output-root", default=".agentsassemble")

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.command == "demo":
        result = run_demo_meeting(
            adapter_name=args.adapter,
            output_root=Path(args.output_root),
            reporter=lambda message: print(message, flush=True),
            codex_timeout_seconds=args.codex_timeout,
            codex_search_enabled=not args.no_codex_search,
        )
        return 0
    if args.command == "gui":
        serve_gui(host=args.host, port=args.port, output_root=Path(args.output_root))
        return 0

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
