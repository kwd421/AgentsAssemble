"""Room command parser registrations."""
from __future__ import annotations

import argparse

from agentsassemble.cli_parser_common import (
    parse_nonnegative_float,
    parse_nonnegative_int,
    parse_positive_int,
)
from agentsassemble.live_cli_smoke import DEFAULT_LIVE_CLI_SMOKE_CONFIG
from agentsassemble.room_repository_factory import DEFAULT_POSTGRES_DSN_ENV


def register_room_parsers(subparsers: argparse._SubParsersAction) -> None:
    room = subparsers.add_parser("room", help="Work with turn-based Agent Sessions in a room.")
    room_subparsers = room.add_subparsers(dest="room_command", required=True)
    room_server = argparse.ArgumentParser(add_help=False)
    room_server.add_argument("--server", default="http://127.0.0.1:8765")

    room_list = room_subparsers.add_parser("list", parents=[room_server], help="List persisted rooms.")
    room_list.add_argument("--include-archived", action="store_true")
    room_list.add_argument("--json", action="store_true", dest="as_json")

    room_status = room_subparsers.add_parser("status", parents=[room_server], help="Show one room's persisted state.")
    room_status.add_argument("room_id")
    room_status.add_argument("--json", action="store_true", dest="as_json")

    room_migrate_legacy = room_subparsers.add_parser(
        "migrate-legacy-messages",
        help="Import preserved legacy meeting messages into the canonical room event store.",
    )
    room_migrate_legacy.add_argument("--output-root", default=".agentsassemble")
    migrate_legacy_mode = room_migrate_legacy.add_mutually_exclusive_group(required=True)
    migrate_legacy_mode.add_argument("--dry-run", action="store_true")
    migrate_legacy_mode.add_argument("--apply", action="store_true")
    room_migrate_legacy.add_argument("--json", action="store_true", dest="as_json")

    room_migrate_postgres = room_subparsers.add_parser(
        "migrate-postgres",
        help="Inspect or copy canonical SQLite room state into an empty PostgreSQL repository.",
    )
    room_migrate_postgres.add_argument("--output-root", default=".agentsassemble")
    room_migrate_postgres.add_argument(
        "--postgres-dsn-env",
        default=DEFAULT_POSTGRES_DSN_ENV,
        help="Environment variable containing the PostgreSQL DSN; the DSN is never accepted on argv.",
    )
    migrate_postgres_mode = room_migrate_postgres.add_mutually_exclusive_group()
    migrate_postgres_mode.add_argument("--dry-run", action="store_false", dest="apply")
    migrate_postgres_mode.add_argument("--apply", action="store_true")
    room_migrate_postgres.set_defaults(apply=False)
    room_migrate_postgres.add_argument("--json", action="store_true", dest="as_json")

    room_attend = room_subparsers.add_parser(
        "attend",
        help="Join an agent-owned room invite over the canonical WebSocket; reads the invite URL from hidden stdin.",
    )
    room_attend.add_argument("--provider", required=True, help="Native provider id such as codex, antigravity, or opencode.")
    room_attend.add_argument("--display-name", default="")
    room_attend.add_argument("--workspace", default="", help="Optional workspace; the default is an empty temporary directory.")
    room_attend.add_argument("--model", default="")
    room_attend.add_argument("--effort", default="")
    room_attend.add_argument("--service-tier", default="")
    room_attend.add_argument("--variant", default="")
    room_attend.add_argument(
        "--permission-mode",
        choices=["meeting_read_only", "workspace_write"],
        default="meeting_read_only",
    )

    for room_command in ("join", "resume"):
        room_join = room_subparsers.add_parser(
            room_command,
            parents=[room_server],
            help=f"{room_command.title()} an Agent Session using the room backend.",
        )
        room_join.add_argument("room_id")
        room_join.add_argument("--agent", required=True)
        room_join.add_argument("--session", default="")
        room_join.add_argument("--provider-session-id", default="", help="Explicit provider-owned session id for Codex resume; never use --last.")
        room_join.add_argument("--model", default="")
        room_join.add_argument("--effort", default="")
        room_join.add_argument("--sandbox", default="")
        room_join.add_argument("--permissions", default="")
        room_join.add_argument("--provider", default="", help="User-facing provider alias, such as codex.")
        room_join.add_argument("--provider-kind", default="", help="Internal provider kind for legacy scripts.")
        room_join.add_argument("--start", action="store_true", help="Opt in to launching/resuming the provider process.")
        room_join.add_argument("--dry-run", action="store_true", help="Return the launch plan without starting the provider.")
        room_join.add_argument("--json", action="store_true", dest="as_json")

    room_turn = room_subparsers.add_parser("turn", parents=[room_server], help="Run one Agent Session turn from room state.")
    room_turn.add_argument("room_id")
    room_turn.add_argument("--agent", required=True)
    room_turn.add_argument("--session", default="")
    room_turn.add_argument("--dry-run", action="store_true", help="Build the turn packet without running the provider.")
    room_turn.add_argument("--json", action="store_true", dest="as_json")
    room_turn.add_argument("instruction")

    room_smoke = room_subparsers.add_parser("smoke", help="Run opt-in Agent Session or live CLI smoke checks.")
    room_smoke.add_argument("--providers", default="", help="Comma-separated live CLI provider ids to smoke, such as codex,grok.")
    room_smoke.add_argument("--config", default=str(DEFAULT_LIVE_CLI_SMOKE_CONFIG), help="Live CLI provider smoke config JSON.")
    room_smoke.add_argument("--timeout", type=parse_nonnegative_float, default=120.0, help="Per-provider live CLI smoke timeout.")
    room_smoke.add_argument(
        "--latency-samples",
        type=parse_nonnegative_int,
        default=0,
        help="After one warmup, compare same-turn provider-runtime and room-observed TTFO this many times per provider.",
    )
    room_smoke.add_argument(
        "--agent-conversation",
        action="store_true",
        help="Start two or more providers and verify server-assigned turns over one shared public room history.",
    )
    room_smoke.add_argument(
        "--conversation-seconds",
        type=parse_nonnegative_float,
        default=0.0,
        help="Keep a shared-room agent conversation running for at least this many seconds; 0 runs one speaker cycle.",
    )
    room_smoke.add_argument(
        "--conversation-topic",
        default="",
        help="Topic used by the time-boxed real agent conversation.",
    )
    room_smoke.add_argument(
        "--verify-controls",
        action="store_true",
        help="Verify process-preserving pause/resume backlog handling and participant kick cleanup.",
    )
    room_smoke.add_argument(
        "--observe-gui-port",
        type=parse_nonnegative_int,
        default=0,
        help="Serve the canonical React room on this port during the smoke and persist its room state.",
    )
    room_smoke.add_argument("--approve-real-provider", action="store_true", help="Allow real local provider CLI commands to run.")
    room_smoke.add_argument("--json", action="store_true", dest="as_json", help="Print JSON output.")
    room_smoke_subparsers = room_smoke.add_subparsers(dest="room_smoke_command")
    for smoke_command in (
        "fresh-codex",
        "explicit-session-codex",
        "two-agent-codex",
        "context-recovery-codex",
        "codex-app-server-same-profile",
        "codex-app-server-warm",
        "codex-app-server-two-agent",
        "codex-app-server-profile-isolation",
        "codex-app-server-restart-recovery",
        "codex-app-server-stderr-backpressure",
        "codex-exec-jsonl-fallback",
    ):
        smoke = room_smoke_subparsers.add_parser(smoke_command, help=f"Run {smoke_command} smoke check.")
        smoke.add_argument("--approve-real-provider", action="store_true")
        smoke.add_argument("--json", action="store_true", dest="as_json")

    room_benchmark = room_subparsers.add_parser(
        "benchmark",
        help="Measure the canonical SQLite room at long-room cardinality without starting providers.",
    )
    room_benchmark.add_argument("--output-root", default="", help="Parent directory for benchmark artifacts.")
    room_benchmark.add_argument("--events", type=parse_positive_int, default=100_000)
    room_benchmark.add_argument("--agent-count", type=parse_positive_int, default=10)
    room_benchmark.add_argument("--read-window", type=parse_positive_int, default=200)
    room_benchmark.add_argument("--samples", type=parse_positive_int, default=50)
    room_benchmark.add_argument("--keep-output", action="store_true")
    room_benchmark.add_argument("--json", action="store_true", dest="as_json")

    room_leave = room_subparsers.add_parser("leave", parents=[room_server], help="Leave a room as an Agent Session participant.")
    room_leave.add_argument("room_id")
    room_leave.add_argument("--agent", required=True)
    room_leave.add_argument("--json", action="store_true", dest="as_json")
