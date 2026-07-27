"""Validate provider-reported room tool results for canonical system messages."""

from __future__ import annotations

import re
from dataclasses import dataclass

from agentsassemble.providers.room_random import RoomRandomError, roll_dice
from agentsassemble.room.text import clean_room_text, has_room_visible_text


_NORMALIZED_DICE = re.compile(
    r"^(?P<count>\d{1,3})d(?P<sides>\d{1,4})(?P<modifier>[+-]\d{1,5})?$"
)
_ROOM_RESULT_ID = re.compile(r"^result-[a-f0-9]{32}$")


class RoomSystemResultError(ValueError):
    """Raised when an untrusted bridge result does not match the tool contract."""


@dataclass(frozen=True)
class PreparedRoomSystemResult:
    display_name: str
    content: str
    metadata: dict[str, object]


def prepare_room_system_result(
    *,
    result_id: object,
    operation: object,
    details: object,
    participant_id: object,
    display_name: object,
    source_turn_id: object,
) -> PreparedRoomSystemResult:
    clean_result_id = clean_room_text(result_id, limit=64)
    if _ROOM_RESULT_ID.fullmatch(clean_result_id) is None:
        raise RoomSystemResultError("Room tool result id is invalid.")
    clean_operation = clean_room_text(operation, limit=32)
    clean_participant_id = clean_room_text(participant_id, limit=128)
    clean_display_name = (
        clean_room_text(display_name, limit=80)
        or clean_participant_id
        or "Agent"
    )
    clean_turn_id = clean_room_text(source_turn_id, limit=128)
    if not isinstance(details, dict):
        raise RoomSystemResultError("Room tool result details must be an object.")
    if clean_operation == "roll_dice":
        result_kind, result_name, content, safe_details = _prepare_dice(
            clean_display_name,
            details,
        )
    elif clean_operation == "choose_random":
        result_kind, result_name, content, safe_details = _prepare_choice(
            clean_display_name,
            details,
        )
    else:
        raise RoomSystemResultError("Unsupported room tool result operation.")
    return PreparedRoomSystemResult(
        display_name=result_name,
        content=content,
        metadata={
            "room_result_id": clean_result_id,
            "room_result_kind": result_kind,
            "operation": clean_operation,
            "source_turn_id": clean_turn_id,
            "source_participant_id": clean_participant_id,
            "details": safe_details,
        },
    )


def _prepare_dice(
    display_name: str,
    details: dict[str, object],
) -> tuple[str, str, str, dict[str, object]]:
    notation = clean_room_text(details.get("notation"), limit=32)
    try:
        expected = roll_dice(notation, randbelow=lambda _sides: 0)
    except RoomRandomError as error:
        raise RoomSystemResultError(str(error)) from error
    normalized = str(expected["notation"])
    match = _NORMALIZED_DICE.fullmatch(normalized)
    if match is None or notation != normalized:
        raise RoomSystemResultError("Dice notation must be normalized.")
    count = int(match.group("count"))
    sides = int(match.group("sides"))
    expected_modifier = int(match.group("modifier") or 0)
    rolls_value = details.get("rolls")
    if not isinstance(rolls_value, list) or len(rolls_value) != count:
        raise RoomSystemResultError("Dice result count does not match its notation.")
    rolls = [_strict_int(value, field="roll") for value in rolls_value]
    if any(value < 1 or value > sides for value in rolls):
        raise RoomSystemResultError("Dice result contains an out-of-range roll.")
    modifier = _strict_int(details.get("modifier"), field="modifier")
    total = _strict_int(details.get("total"), field="total")
    if modifier != expected_modifier or total != sum(rolls) + modifier:
        raise RoomSystemResultError("Dice result arithmetic is inconsistent.")
    safe_details: dict[str, object] = {
        "notation": normalized,
        "rolls": rolls,
        "modifier": modifier,
        "total": total,
    }
    roll_text = ", ".join(str(value) for value in rolls)
    return (
        "dice_roll",
        "주사위 결과",
        f"{display_name} · {normalized} → {total} (굴림: {roll_text})",
        safe_details,
    )


def _prepare_choice(
    display_name: str,
    details: dict[str, object],
) -> tuple[str, str, str, dict[str, object]]:
    choice = clean_room_text(details.get("choice"), limit=200)
    index = _strict_int(details.get("index"), field="index")
    option_count = _strict_int(details.get("option_count"), field="option_count")
    options_value = details.get("options")
    if (
        not isinstance(options_value, list)
        or len(options_value) != option_count
        or any(not isinstance(option, str) for option in options_value)
    ):
        raise RoomSystemResultError(
            "Random choice options do not match their option count."
        )
    options = [clean_room_text(option, limit=200) for option in options_value]
    if not choice or not has_room_visible_text(choice):
        raise RoomSystemResultError("Random choice result is empty.")
    if (
        not 2 <= option_count <= 50
        or not 0 <= index < option_count
        or any(
            not option or not has_room_visible_text(option)
            for option in options
        )
        or choice != options[index]
    ):
        raise RoomSystemResultError("Random choice result is out of range.")
    safe_details: dict[str, object] = {
        "choice": choice,
        "index": index,
        "option_count": option_count,
    }
    return (
        "random_choice",
        "무작위 선택 결과",
        f"{display_name} · 무작위 선택 → 「{choice}」 ({index + 1}/{option_count})",
        safe_details,
    )


def _strict_int(value: object, *, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise RoomSystemResultError(f"Room tool result {field} must be an integer.")
    return value


__all__ = [
    "PreparedRoomSystemResult",
    "RoomSystemResultError",
    "prepare_room_system_result",
]
