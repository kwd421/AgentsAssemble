"""Compatibility exports for the optional Mafia game service."""
from agentsassemble.features.mafia.game import (
    MAFIA_CHAT_CHANNELS,
    MAFIA_EVENT_CHANNELS,
    MAFIA_GAME_LOCK,
    MAFIA_NIGHT_ACTIONS,
    MAFIA_PHASES,
    MAFIA_ROLES,
    cast_mafia_vote,
    mafia_game_payload,
    post_mafia_chat,
    resolve_mafia_phase,
    start_mafia_game,
    submit_mafia_action,
)

__all__ = [
    "MAFIA_CHAT_CHANNELS",
    "MAFIA_EVENT_CHANNELS",
    "MAFIA_GAME_LOCK",
    "MAFIA_NIGHT_ACTIONS",
    "MAFIA_PHASES",
    "MAFIA_ROLES",
    "cast_mafia_vote",
    "mafia_game_payload",
    "post_mafia_chat",
    "resolve_mafia_phase",
    "start_mafia_game",
    "submit_mafia_action",
]
