"""Pure bounds and normalization for official room randomness."""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass

from agentsassemble.room.text import clean_room_text, has_room_visible_text


_DICE_NOTATION = re.compile(
    r"^\s*(?P<count>\d{0,3})d(?P<sides>\d{1,4})"
    r"(?P<modifier>[+-]\d{1,5})?\s*$",
    re.IGNORECASE,
)


class RoomRandomContractError(ValueError):
    """Raised when a room randomness request is outside the shared contract."""


@dataclass(frozen=True)
class RoomDiceNotation:
    notation: str
    count: int
    sides: int
    modifier: int


def normalize_dice_notation(notation: object) -> RoomDiceNotation:
    match = _DICE_NOTATION.fullmatch(str(notation or ""))
    if match is None:
        raise RoomRandomContractError(
            "Dice notation must look like d20, 2d6, or 1d20+3."
        )
    count = int(match.group("count") or 1)
    sides = int(match.group("sides"))
    modifier = int(match.group("modifier") or 0)
    if not 1 <= count <= 100:
        raise RoomRandomContractError(
            "A dice roll must use between 1 and 100 dice."
        )
    if not 2 <= sides <= 1000:
        raise RoomRandomContractError(
            "Dice must have between 2 and 1000 sides."
        )
    if not -100_000 <= modifier <= 100_000:
        raise RoomRandomContractError("The dice modifier is out of range.")
    normalized = f"{count}d{sides}"
    if modifier:
        normalized += f"{modifier:+d}"
    return RoomDiceNotation(
        notation=normalized,
        count=count,
        sides=sides,
        modifier=modifier,
    )


def normalize_random_options(options: object) -> tuple[str, ...]:
    if isinstance(options, (str, bytes)) or not isinstance(options, Iterable):
        raise RoomRandomContractError(
            "Random choice requires a list of options."
        )
    values = list(options)
    if len(values) > 50:
        raise RoomRandomContractError(
            "Random choice accepts at most 50 options."
        )
    cleaned = tuple(clean_room_text(option, limit=200) for option in values)
    if (
        len(cleaned) < 2
        or any(
            not option or not has_room_visible_text(option)
            for option in cleaned
        )
    ):
        raise RoomRandomContractError(
            "Random choice requires 2 to 50 non-empty options."
        )
    return cleaned


__all__ = [
    "RoomDiceNotation",
    "RoomRandomContractError",
    "normalize_dice_notation",
    "normalize_random_options",
]
