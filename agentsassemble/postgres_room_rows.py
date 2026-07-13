from __future__ import annotations

import json


def payload_from_row(
    row: dict[str, object] | None,
    *,
    column: str = "data_json",
) -> dict[str, object]:
    if row is None:
        return {}
    value = row.get(column)
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (json.JSONDecodeError, TypeError, ValueError):
            return {}
        return dict(parsed) if isinstance(parsed, dict) else {}
    return {}
