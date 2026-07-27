"""Validate provider-reported room tool results for canonical system messages."""

from __future__ import annotations

import re
from dataclasses import dataclass

from agentsassemble.room.random_contract import (
    RoomRandomContractError,
    normalize_dice_notation,
    normalize_random_options,
)
from agentsassemble.room.text import clean_room_text, has_room_visible_text


_ROOM_RESULT_ID = re.compile(r"^result-[a-f0-9]{32}$")


class RoomSystemResultError(ValueError):
    """Raised when an untrusted bridge result does not match the tool contract."""


@dataclass(frozen=True)
class PreparedRoomSystemResult:
    display_name: str
    content: str
    metadata: dict[str, object]


@dataclass(frozen=True)
class ValidatedRoomSystemResult:
    result_id: str
    operation: str
    details: dict[str, object]


def validate_room_system_result(
    *,
    result_id: object,
    operation: object,
    details: object,
) -> ValidatedRoomSystemResult:
    """Validate and bound one untrusted room-tool result."""
    clean_result_id = clean_room_text(result_id, limit=64)
    if _ROOM_RESULT_ID.fullmatch(clean_result_id) is None:
        raise RoomSystemResultError("Room tool result id is invalid.")
    clean_operation = clean_room_text(operation, limit=32)
    if not isinstance(details, dict):
        raise RoomSystemResultError("Room tool result details must be an object.")
    if clean_operation == "roll_dice":
        safe_details = _validate_dice(details)
    elif clean_operation == "choose_random":
        safe_details = _validate_choice(details)
    else:
        raise RoomSystemResultError("Unsupported room tool result operation.")
    return ValidatedRoomSystemResult(
        result_id=clean_result_id,
        operation=clean_operation,
        details=safe_details,
    )


def prepare_room_system_result(
    *,
    result_id: object,
    operation: object,
    details: object,
    participant_id: object,
    display_name: object,
    source_turn_id: object,
) -> PreparedRoomSystemResult:
    validated = validate_room_system_result(
        result_id=result_id,
        operation=operation,
        details=details,
    )
    clean_participant_id = clean_room_text(participant_id, limit=128)
    clean_display_name = (
        clean_room_text(display_name, limit=80)
        or clean_participant_id
        or "Agent"
    )
    clean_turn_id = clean_room_text(source_turn_id, limit=128)
    if validated.operation == "roll_dice":
        result_kind, result_name, content, safe_details = _prepare_dice(
            clean_display_name,
            validated.details,
        )
    else:
        result_kind, result_name, content, safe_details = _prepare_choice(
            clean_display_name,
            validated.details,
        )
    return PreparedRoomSystemResult(
        display_name=result_name,
        content=content,
        metadata={
            "room_result_id": validated.result_id,
            "room_result_kind": result_kind,
            "operation": validated.operation,
            "source_turn_id": clean_turn_id,
            "source_participant_id": clean_participant_id,
            "details": safe_details,
        },
    )


def _validate_dice(
    details: dict[str, object],
) -> dict[str, object]:
    notation = clean_room_text(details.get("notation"), limit=32)
    try:
        parsed = normalize_dice_notation(notation)
    except RoomRandomContractError as error:
        raise RoomSystemResultError(str(error)) from error
    if notation != parsed.notation:
        raise RoomSystemResultError("Dice notation must be normalized.")
    rolls_value = details.get("rolls")
    if not isinstance(rolls_value, list) or len(rolls_value) != parsed.count:
        raise RoomSystemResultError("Dice result count does not match its notation.")
    rolls = [_strict_int(value, field="roll") for value in rolls_value]
    if any(value < 1 or value > parsed.sides for value in rolls):
        raise RoomSystemResultError("Dice result contains an out-of-range roll.")
    modifier = _strict_int(details.get("modifier"), field="modifier")
    total = _strict_int(details.get("total"), field="total")
    if modifier != parsed.modifier or total != sum(rolls) + modifier:
        raise RoomSystemResultError("Dice result arithmetic is inconsistent.")
    safe: dict[str, object] = {
        "notation": parsed.notation,
        "rolls": rolls,
        "modifier": modifier,
        "total": total,
    }
    reason = _bounded_result_reason(details.get("reason"))
    if reason:
        safe["reason"] = reason
    return safe


def _validate_choice(
    details: dict[str, object],
) -> dict[str, object]:
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
    try:
        options = normalize_random_options(options_value)
    except RoomRandomContractError as error:
        raise RoomSystemResultError(str(error)) from error
    if not choice or not has_room_visible_text(choice):
        raise RoomSystemResultError("Random choice result is empty.")
    if (
        not 2 <= option_count <= 50
        or not 0 <= index < option_count
        or choice != options[index]
    ):
        raise RoomSystemResultError("Random choice result is out of range.")
    safe: dict[str, object] = {
        "choice": choice,
        "index": index,
        "option_count": option_count,
        "options": list(options),
    }
    reason = _bounded_result_reason(details.get("reason"))
    if reason:
        safe["reason"] = reason
    return safe


def _prepare_dice(
    display_name: str,
    details: dict[str, object],
) -> tuple[str, str, str, dict[str, object]]:
    normalized = str(details["notation"])
    rolls = list(details["rolls"])
    modifier = int(details["modifier"])
    total = int(details["total"])
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
    choice = str(details["choice"])
    index = int(details["index"])
    option_count = int(details["option_count"])
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


def _bounded_result_reason(value: object) -> str:
    return clean_room_text(value, limit=200) if isinstance(value, str) else ""


__all__ = [
    "PreparedRoomSystemResult",
    "RoomSystemResultError",
    "ValidatedRoomSystemResult",
    "prepare_room_system_result",
    "validate_room_system_result",
]
