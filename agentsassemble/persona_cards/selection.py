"""Persona-selection serialization shared by provider launch profiles."""

from __future__ import annotations

from agentsassemble.room.text import clean_room_text


def persona_spec_kwargs(payload: dict[str, object]) -> dict[str, object]:
    return {
        "persona_card_id": clean_room_text(payload.get("persona_card_id"), limit=80),
        "persona_card_summary": (
            dict(payload.get("persona_card"))
            if isinstance(payload.get("persona_card"), dict)
            else {}
        ),
    }


def add_persona_runtime_profile(
    profile: dict[str, object],
    persona_card_id: str,
) -> None:
    if persona_card_id:
        profile["persona_card_id"] = persona_card_id


def validate_persona_spec(persona_card_id: str, summary: dict[str, object]) -> None:
    summary_id = clean_room_text(summary.get("id"), limit=80)
    if persona_card_id and summary_id != persona_card_id:
        raise ValueError("Agent Session persona summary does not match its selected asset.")
    if summary and not persona_card_id:
        raise ValueError("Agent Session persona summary requires a selected asset.")


__all__ = [
    "add_persona_runtime_profile",
    "persona_spec_kwargs",
    "validate_persona_spec",
]
