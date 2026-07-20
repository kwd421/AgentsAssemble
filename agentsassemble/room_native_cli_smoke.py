"""Compatibility exports for frontend-driven native CLI smoke."""

from agentsassemble.application.room_native_cli_smoke import (
    DEFAULT_CONVERSATION_TOPIC,
    NON_ROOM_REPLY,
    ROOM_TTFO_P50_EXTRA_LIMIT_MS,
    ROOM_TTFO_P50_RATIO_LIMIT,
    ROOM_TTFO_P95_EXTRA_LIMIT_MS,
    STRICT_MESSAGE_SOURCES,
    TUI_NOISE,
    _latency_acceptance,
    run_room_native_cli_smoke,
)

__all__ = [
    "DEFAULT_CONVERSATION_TOPIC",
    "NON_ROOM_REPLY",
    "ROOM_TTFO_P50_EXTRA_LIMIT_MS",
    "ROOM_TTFO_P50_RATIO_LIMIT",
    "ROOM_TTFO_P95_EXTRA_LIMIT_MS",
    "STRICT_MESSAGE_SOURCES",
    "TUI_NOISE",
    "_latency_acceptance",
    "run_room_native_cli_smoke",
]
