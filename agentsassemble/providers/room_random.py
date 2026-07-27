"""Validated server-side randomness for shared-room game tools."""

from __future__ import annotations

import secrets
from collections.abc import Callable, Sequence

from agentsassemble.room.random_contract import (
    RoomRandomContractError,
    normalize_dice_notation,
    normalize_random_options,
)


class RoomRandomError(ValueError):
    """Raised when a room randomizer request is invalid."""


def roll_dice(
    notation: object,
    *,
    randbelow: Callable[[int], int] = secrets.randbelow,
) -> dict[str, object]:
    """Roll standard NdS±M notation with bounded, auditable output."""
    try:
        parsed = normalize_dice_notation(notation)
    except RoomRandomContractError as error:
        raise RoomRandomError(str(error)) from error
    rolls = [
        int(randbelow(parsed.sides)) + 1
        for _ in range(parsed.count)
    ]
    return {
        "notation": parsed.notation,
        "rolls": rolls,
        "modifier": parsed.modifier,
        "total": sum(rolls) + parsed.modifier,
    }


def choose_random(
    options: Sequence[object],
    *,
    randbelow: Callable[[int], int] = secrets.randbelow,
) -> dict[str, object]:
    """Choose one non-empty option from a bounded list."""
    try:
        normalized = normalize_random_options(options)
    except RoomRandomContractError as error:
        raise RoomRandomError(str(error)) from error
    index = int(randbelow(len(normalized)))
    return {
        "choice": normalized[index],
        "index": index,
        "option_count": len(normalized),
        "options": list(normalized),
    }


__all__ = [
    "RoomRandomError",
    "choose_random",
    "roll_dice",
]
