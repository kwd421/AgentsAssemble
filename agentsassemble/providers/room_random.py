"""Validated server-side randomness for shared-room game tools."""

from __future__ import annotations

import re
import secrets
from collections.abc import Callable, Sequence


_DICE_NOTATION = re.compile(
    r"^\s*(?P<count>\d{0,3})d(?P<sides>\d{1,4})"
    r"(?P<modifier>[+-]\d{1,5})?\s*$",
    re.IGNORECASE,
)


class RoomRandomError(ValueError):
    """Raised when a room randomizer request is invalid."""


def roll_dice(
    notation: object,
    *,
    randbelow: Callable[[int], int] = secrets.randbelow,
) -> dict[str, object]:
    """Roll standard NdS±M notation with bounded, auditable output."""
    match = _DICE_NOTATION.fullmatch(str(notation or ""))
    if match is None:
        raise RoomRandomError("Dice notation must look like d20, 2d6, or 1d20+3.")
    count = int(match.group("count") or 1)
    sides = int(match.group("sides"))
    modifier = int(match.group("modifier") or 0)
    if not 1 <= count <= 100:
        raise RoomRandomError("A dice roll must use between 1 and 100 dice.")
    if not 2 <= sides <= 1000:
        raise RoomRandomError("Dice must have between 2 and 1000 sides.")
    if not -100_000 <= modifier <= 100_000:
        raise RoomRandomError("The dice modifier is out of range.")
    rolls = [int(randbelow(sides)) + 1 for _ in range(count)]
    normalized = f"{count}d{sides}"
    if modifier:
        normalized += f"{modifier:+d}"
    return {
        "notation": normalized,
        "rolls": rolls,
        "modifier": modifier,
        "total": sum(rolls) + modifier,
    }


def choose_random(
    options: Sequence[object],
    *,
    randbelow: Callable[[int], int] = secrets.randbelow,
) -> dict[str, object]:
    """Choose one non-empty option from a bounded list."""
    if isinstance(options, (str, bytes)):
        raise RoomRandomError("Random choice requires a list of options.")
    values = list(options)
    if len(values) > 50:
        raise RoomRandomError("Random choice accepts at most 50 options.")
    cleaned = [str(option or "").strip()[:200] for option in values]
    if len(cleaned) < 2 or any(not option for option in cleaned):
        raise RoomRandomError("Random choice requires 2 to 50 non-empty options.")
    index = int(randbelow(len(cleaned)))
    return {
        "choice": cleaned[index],
        "index": index,
        "option_count": len(cleaned),
    }


__all__ = [
    "RoomRandomError",
    "choose_random",
    "roll_dice",
]
