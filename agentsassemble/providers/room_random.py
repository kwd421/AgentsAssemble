"""Provider compatibility imports for canonical room randomness."""

from __future__ import annotations

from agentsassemble.room.random import (
    RoomRandomError,
    choose_random,
    roll_dice,
)


__all__ = [
    "RoomRandomError",
    "choose_random",
    "roll_dice",
]
