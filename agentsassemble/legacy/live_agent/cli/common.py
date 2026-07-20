"""Resident-only CLI constants, validators, and argument helpers."""
from __future__ import annotations

import argparse

from agentsassemble.application.cli.common import (
    parse_nonnegative_float,
    parse_nonnegative_int,
    parse_positive_int,
)
from agentsassemble.live_agent_runner import SUPPORTED_RESIDENT_CONNECTION_KINDS
from agentsassemble.live_agent_smoke import (
    MAX_SESSION_SMOKE_LOBBY_PROBES,
    MAX_SESSION_SMOKE_SOAK_CYCLES,
    MAX_SESSION_SMOKE_SOAK_INTERVAL_SECONDS,
)


LIVE_AGENT_RESIDENT_CONNECTION_KIND_CHOICES = list(SUPPORTED_RESIDENT_CONNECTION_KINDS)


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


def add_session_readiness_wait_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--wait-ready",
        action="store_true",
        help="After the session command returns, poll read-only session readiness until the target is ready.",
    )
    parser.add_argument("--wait-timeout", type=parse_nonnegative_float, default=30.0)
    parser.add_argument("--wait-poll-interval", type=parse_nonnegative_float, default=2.0)


def add_session_auto_restart_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--auto-restart", action="store_true")
    parser.add_argument("--max-restarts", type=parse_nonnegative_int, default=0)
    parser.add_argument("--restart-backoff-seconds", type=parse_nonnegative_float, default=5.0)
    parser.add_argument("--stale-restart-after-seconds", type=parse_nonnegative_float, default=0.0)


def add_session_finalize_after_rounds_arg(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--finalize-after-rounds",
        action="store_true",
        help="After successful remaining rounds, finalize resident meeting artifacts.",
    )


# Historical public names retained by agentsassemble.cli and its root shim.
_add_session_readiness_wait_args = add_session_readiness_wait_args
_add_session_auto_restart_args = add_session_auto_restart_args
_add_session_finalize_after_rounds_arg = add_session_finalize_after_rounds_arg
