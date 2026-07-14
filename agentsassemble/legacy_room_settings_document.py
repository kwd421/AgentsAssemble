"""Read the legacy room_settings.json document without repairing it."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


class LegacyRoomSettingsSourceError(ValueError):
    """The legacy settings source cannot produce a safe migration plan."""


@dataclass(frozen=True)
class LegacyRoomSettingsDocument:
    path: Path
    raw_bytes: bytes
    payload: dict[str, object]
    rooms: dict[str, object]


def read_legacy_room_settings_document(path: Path) -> LegacyRoomSettingsDocument:
    try:
        raw_bytes = path.read_bytes()
        payload = json.loads(raw_bytes.decode("utf-8"))
    except UnicodeDecodeError as error:
        raise LegacyRoomSettingsSourceError(
            "Legacy room_settings.json is not valid UTF-8."
        ) from error
    except json.JSONDecodeError as error:
        raise LegacyRoomSettingsSourceError(
            f"Legacy room_settings.json is malformed at line {error.lineno}, "
            f"column {error.colno}."
        ) from error
    except OSError as error:
        raise LegacyRoomSettingsSourceError(
            f"Legacy room_settings.json could not be read: {type(error).__name__}."
        ) from error
    if not isinstance(payload, dict):
        raise LegacyRoomSettingsSourceError(
            "Legacy room_settings.json must contain an object."
        )
    rooms = payload.get("rooms")
    if not isinstance(rooms, dict):
        raise LegacyRoomSettingsSourceError(
            "Legacy room_settings.json must contain a rooms object."
        )
    return LegacyRoomSettingsDocument(
        path=path,
        raw_bytes=raw_bytes,
        payload=payload,
        rooms=rooms,
    )
