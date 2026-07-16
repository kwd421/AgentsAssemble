"""Extract provider session identifiers from Codex CLI output."""

from __future__ import annotations

import json
import re
from typing import Any, Iterable


CODEX_SESSION_ID_PATTERN = r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
CODEX_SESSION_ID_RE = re.compile(rf"\b({CODEX_SESSION_ID_PATTERN})\b")
CODEX_SESSION_LABEL_RE = re.compile(
    rf"\b(?:codex\s+)?session(?:[-_\s]?id)?\s*[:=]\s*[\"']?({CODEX_SESSION_ID_PATTERN})\b",
    re.IGNORECASE,
)


def extract_codex_session_id(output: str) -> str:
    """Extract a Codex session id from text or Codex JSONL event output."""

    for value in _json_session_id_candidates(output):
        if _is_codex_session_id(value):
            return value.strip()

    match = CODEX_SESSION_LABEL_RE.search(output)
    if match:
        return match.group(1)
    return ""


def _json_session_id_candidates(output: str) -> Iterable[str]:
    for line in output.splitlines():
        stripped = line.strip()
        if not stripped or not stripped.startswith("{"):
            continue
        try:
            payload = json.loads(stripped)
        except json.JSONDecodeError:
            continue
        yield from _session_id_values(payload)


def _session_id_values(value: Any) -> Iterable[str]:
    if isinstance(value, dict):
        for key in ("session_id", "sessionId", "sessionID"):
            raw = value.get(key)
            if isinstance(raw, str):
                yield raw

        session = value.get("session")
        if isinstance(session, str):
            yield session
        elif isinstance(session, dict):
            for key in ("id", "session_id", "sessionId", "sessionID"):
                raw = session.get(key)
                if isinstance(raw, str):
                    yield raw

        event_type = value.get("type")
        if isinstance(event_type, str) and "session" in event_type.lower():
            raw_id = value.get("id")
            if isinstance(raw_id, str):
                yield raw_id

        for nested in value.values():
            yield from _session_id_values(nested)
    elif isinstance(value, list):
        for item in value:
            yield from _session_id_values(item)


def _is_codex_session_id(value: str) -> bool:
    return bool(CODEX_SESSION_ID_RE.fullmatch(value.strip()))
