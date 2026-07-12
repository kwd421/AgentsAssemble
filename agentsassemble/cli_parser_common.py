"""Shared constants, validators, and argument helpers for CLI parsers."""
from __future__ import annotations

import argparse
import math

from agentsassemble.live_agent_runner import SUPPORTED_RESIDENT_CONNECTION_KINDS
from agentsassemble.live_agent_smoke import (
    MAX_SESSION_SMOKE_LOBBY_PROBES,
    MAX_SESSION_SMOKE_SOAK_CYCLES,
    MAX_SESSION_SMOKE_SOAK_INTERVAL_SECONDS,
)


LIVE_AGENT_CONNECTION_KIND_CHOICES = [
    "codex_resume",
    "local_cli",
    "live_session",
    "terminal_session",
    "remote_bridge",
    "self_service",
    "manual",
]
LIVE_AGENT_DELEGATE_CONNECTION_KIND_CHOICES = ["codex_resume", "local_cli", "remote_bridge", "manual"]
LIVE_AGENT_RESIDENT_CONNECTION_KIND_CHOICES = list(SUPPORTED_RESIDENT_CONNECTION_KINDS)
MAX_LIVE_AGENT_ROUND_BATCH = 8


def parse_codex_timeout(value: str) -> int | None:
    if value.casefold() in {"none", "off", "unlimited", "0"}:
        return None
    timeout = int(value)
    if timeout < 0:
        raise argparse.ArgumentTypeError("timeout must be positive, 0, or none")
    return timeout


def parse_nonnegative_int(value: str) -> int:
    parsed = int(value)
    if parsed < 0:
        raise argparse.ArgumentTypeError("value must be non-negative")
    return parsed


def parse_positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("value must be positive")
    return parsed


def parse_session_smoke_lobby_probe_count(value: str) -> int:
    parsed = parse_positive_int(value)
    if parsed > MAX_SESSION_SMOKE_LOBBY_PROBES:
        raise argparse.ArgumentTypeError(f"value must be at most {MAX_SESSION_SMOKE_LOBBY_PROBES}")
    return parsed


def parse_session_smoke_soak_cycle_count(value: str) -> int:
    parsed = parse_nonnegative_int(value)
    if parsed > MAX_SESSION_SMOKE_SOAK_CYCLES:
        raise argparse.ArgumentTypeError(f"value must be at most {MAX_SESSION_SMOKE_SOAK_CYCLES}")
    return parsed


def parse_session_smoke_soak_interval_seconds(value: str) -> float:
    parsed = parse_nonnegative_float(value)
    if parsed > MAX_SESSION_SMOKE_SOAK_INTERVAL_SECONDS:
        raise argparse.ArgumentTypeError(f"value must be at most {MAX_SESSION_SMOKE_SOAK_INTERVAL_SECONDS:g}")
    return parsed


def parse_nonnegative_float(value: str) -> float:
    parsed = float(value)
    if not math.isfinite(parsed) or parsed < 0:
        raise argparse.ArgumentTypeError("value must be a finite non-negative number")
    return parsed


def _add_session_readiness_wait_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--wait-ready",
        action="store_true",
        help="After the session command returns, poll read-only session readiness until the target is ready.",
    )
    parser.add_argument("--wait-timeout", type=parse_nonnegative_float, default=30.0)
    parser.add_argument("--wait-poll-interval", type=parse_nonnegative_float, default=2.0)


def _add_session_auto_restart_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--auto-restart", action="store_true")
    parser.add_argument("--max-restarts", type=parse_nonnegative_int, default=0)
    parser.add_argument("--restart-backoff-seconds", type=parse_nonnegative_float, default=5.0)
    parser.add_argument("--stale-restart-after-seconds", type=parse_nonnegative_float, default=0.0)


def _add_session_finalize_after_rounds_arg(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--finalize-after-rounds",
        action="store_true",
        help="After successful remaining rounds, finalize resident meeting artifacts.",
    )


def _hide_subparser_from_help(subparsers: argparse._SubParsersAction, name: str) -> None:
    subparsers._choices_actions = [
        action for action in subparsers._choices_actions if getattr(action, "dest", "") != name
    ]
