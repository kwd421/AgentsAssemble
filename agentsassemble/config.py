from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from agentsassemble.models import CouncilConfig, Role


DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent.parent / "configs" / "demo-council.json"


def load_council_config(path: Path | str | None = None) -> CouncilConfig:
    config_path = Path(path) if path is not None else DEFAULT_CONFIG_PATH
    data = json.loads(config_path.read_text(encoding="utf-8"))
    roles = [_role_from_dict(role_data) for role_data in data["roles"]]
    return CouncilConfig(
        topic=data["topic"],
        display_topic=data.get("display_topic", data["topic"]),
        question=data["question"],
        display_question=data.get("display_question", data["question"]),
        roles=roles,
    )


def _role_from_dict(data: dict[str, Any]) -> Role:
    return Role(
        id=data["id"],
        display_name=data["display_name"],
        lens=data["lens"],
        research_focus=data["research_focus"],
        personality=data.get("personality"),
        source_preferences=data.get("source_preferences"),
    )
