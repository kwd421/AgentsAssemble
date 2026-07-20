"""Shared constants, validators, and argument helpers for CLI parsers."""
from __future__ import annotations

import argparse
import math


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


def parse_nonnegative_float(value: str) -> float:
    parsed = float(value)
    if not math.isfinite(parsed) or parsed < 0:
        raise argparse.ArgumentTypeError("value must be a finite non-negative number")
    return parsed


def _hide_subparser_from_help(subparsers: argparse._SubParsersAction, name: str) -> None:
    subparsers._choices_actions = [
        action for action in subparsers._choices_actions if getattr(action, "dest", "") != name
    ]
