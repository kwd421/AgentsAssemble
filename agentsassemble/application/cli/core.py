"""Parser registrations for current application commands."""
from __future__ import annotations

import argparse

from agentsassemble.application.cli.common import (
    parse_nonnegative_float,
    parse_positive_int,
)
from agentsassemble.application.room_repository_factory import (
    DEFAULT_POSTGRES_DSN_ENV,
    ROOM_REPOSITORY_BACKENDS,
)
from agentsassemble.diagnostics.release_health import (
    DEFAULT_RELEASE_HEALTH_TIMEOUT_SECONDS,
)


def register_core_parsers(subparsers: argparse._SubParsersAction) -> None:
    gui = subparsers.add_parser("gui", help="Run the local room application.")
    gui.add_argument("--host", default="127.0.0.1")
    gui.add_argument(
        "--port",
        type=int,
        default=None,
        help="Port to bind. Omit to use 8765, or use 0 for an OS-assigned port.",
    )
    gui.add_argument(
        "--output-root",
        default="",
        help="Local product data directory; defaults to the platform user-data root.",
    )
    gui.add_argument(
        "--room-repository-backend",
        choices=sorted(ROOM_REPOSITORY_BACKENDS),
        default="sqlite",
        help="Canonical room storage backend.",
    )
    gui.add_argument(
        "--room-postgres-dsn-env",
        default=DEFAULT_POSTGRES_DSN_ENV,
        help="Environment variable containing the PostgreSQL DSN.",
    )
    gui.add_argument(
        "--attention-shadow-mode",
        choices=("off", "sample", "full"),
        default="off",
    )
    gui.add_argument("--public-url", default="")
    gui.add_argument("--host-token", default="")
    gui.add_argument("--unsafe-expose-control-plane", action="store_true")
    gui.add_argument(
        "--start-public-tunnel",
        "--public-tunnel",
        action="store_true",
        dest="start_public_tunnel",
    )

    frontend_info = subparsers.add_parser(
        "frontend-info",
        help="Print launch guidance for the React room application.",
    )
    frontend_info.add_argument("--backend", default="http://127.0.0.1:8765")
    frontend_info.add_argument("--port", type=parse_positive_int, default=5173)
    frontend_info.add_argument("--json", action="store_true", dest="as_json")

    rolling_restart = subparsers.add_parser(
        "rolling-restart",
        help="Hand the local listener to a new build.",
    )
    rolling_restart.add_argument("--server", default="http://127.0.0.1:8765")
    rolling_restart.add_argument("--status", action="store_true")
    rolling_restart.add_argument("--wait", type=parse_nonnegative_float, default=0.0)
    rolling_restart.add_argument(
        "--host-token-env",
        default="AGENTSASSEMBLE_HOST_TOKEN",
    )
    rolling_restart.add_argument(
        "--json", "--as-json", action="store_true", dest="as_json"
    )

    release_health = subparsers.add_parser(
        "release-health",
        help="List or run local release-health checks.",
    )
    release_health.add_argument(
        "--json", "--as-json", action="store_true", dest="as_json"
    )
    health_subparsers = release_health.add_subparsers(dest="release_health_command")
    health_list = health_subparsers.add_parser("list")
    health_list.add_argument(
        "--json",
        "--as-json",
        action="store_true",
        dest="as_json",
        default=argparse.SUPPRESS,
    )
    health_run = health_subparsers.add_parser("run")
    health_run.add_argument("--check", action="append", default=[])
    health_run.add_argument("--skip", action="append", default=[])
    health_run.add_argument(
        "--timeout",
        type=parse_nonnegative_float,
        default=DEFAULT_RELEASE_HEALTH_TIMEOUT_SECONDS,
    )
    health_run.add_argument(
        "--json",
        "--as-json",
        action="store_true",
        dest="as_json",
        default=argparse.SUPPRESS,
    )
    health_run.add_argument("--save-report", action="store_true")
    health_run.add_argument("--output-root", default=".agentsassemble")

    api_call = subparsers.add_parser(
        "api-call",
        help="Run one OpenAI-compatible model call from stdin.",
    )
    api_call.add_argument("--provider", required=True)
    api_call.add_argument("--model", required=True)
    api_call.add_argument("--system", default="")
    api_call.add_argument("--output-root", default=".agentsassemble")
    api_call.add_argument("--meeting-id", default="")
    api_call.add_argument("--participant-id", default="")
    api_call.add_argument("--user-id", default="")
    api_call.add_argument(
        "--key-source",
        default="",
        choices=["", "byok", "free", "subscription", "local"],
    )
    api_call.add_argument("--timeout", type=int, default=60)
    api_call.add_argument("--catalog", action="store_true")
