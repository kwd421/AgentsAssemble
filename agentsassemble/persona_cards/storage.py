"""Persistence for normalized persona cards."""

from __future__ import annotations

import json
from pathlib import Path

from agentsassemble.persona_cards.models import PersonaCard


def save_persona_card(path: Path, card: PersonaCard) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(
        json.dumps(card.to_dict(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def load_persona_card(path: Path) -> PersonaCard:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("Persona card must be a JSON object.")
    return PersonaCard.from_dict(data)
