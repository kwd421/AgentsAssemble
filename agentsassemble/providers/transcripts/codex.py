from __future__ import annotations

import json
from pathlib import Path

from agentsassemble.providers.transcripts.core import (
    LiveCliMessageSnapshot,
    _JsonlOffsetMessageSource,
    _clean_provider_message_text,
    _codex_response_item_text,
    _jsonl_objects,
    _recent_paths,
)
from agentsassemble.room.text import clean_room_text


class CodexSessionMessageSource(_JsonlOffsetMessageSource):
    source_kind = "codex_session_jsonl"

    def __init__(self, *, home: Path | None = None, cwd: str | Path | None = None) -> None:
        super().__init__(home=home, cwd=cwd)
        self._observed_models: dict[str, str] = {}

    def prepare_start(self) -> None:
        super().prepare_start()
        self._observed_models = {}

    def begin_turn(self, expected_input: str = "") -> None:
        super().begin_turn(expected_input)
        self._observed_models = {}

    def _candidate_paths(self) -> list[Path]:
        root = self.home / ".codex" / "sessions"
        if not root.exists():
            return []
        return [path for path in _recent_paths(root.rglob("*.jsonl")) if self._matches_workspace(path)]

    def _matches_workspace(self, path: Path) -> bool:
        if self.cwd is None:
            return True
        try:
            with path.open("r", encoding="utf-8", errors="replace") as handle:
                for _index, line in zip(range(20), handle):
                    try:
                        entry = json.loads(line)
                    except (json.JSONDecodeError, ValueError):
                        continue
                    if str(entry.get("type") or "") != "session_meta":
                        continue
                    payload = entry.get("payload") if isinstance(entry.get("payload"), dict) else {}
                    return str(payload.get("cwd") or "") == str(self.cwd)
        except OSError:
            return False
        return False

    def _extract_from_text(self, text: str, *, source: str) -> LiveCliMessageSnapshot:
        latest = ""
        observed_model_id = self._observed_models.get(source, "")
        turn_completed_without_message = False
        for entry in _jsonl_objects(text):
            payload = entry.get("payload") if isinstance(entry.get("payload"), dict) else {}
            entry_type = str(entry.get("type") or "")
            payload_type = str(payload.get("type") or "")
            if entry_type == "event_msg" and payload_type == "agent_message":
                latest = _clean_provider_message_text(payload.get("message"), limit=12000)
            elif entry_type == "event_msg" and payload_type == "task_complete":
                latest = _clean_provider_message_text(
                    payload.get("last_agent_message"),
                    limit=12000,
                ) or latest
                turn_completed_without_message = not bool(latest)
            elif entry_type == "response_item":
                latest = _codex_response_item_text(payload) or latest
        error = ""
        if turn_completed_without_message and not latest:
            error = (
                "Codex completed the turn without an assistant message; "
                "provider quota or availability may be exhausted."
            )
        return LiveCliMessageSnapshot(
            content=latest,
            complete=bool(latest),
            source=source if latest or error else "",
            source_kind=self.source_kind,
            observed_model_id=observed_model_id,
            error=error,
        )

    def _observe_text(self, text: str, *, source: str) -> None:
        observed_model_id = self._observed_models.get(source, "")
        for entry in _jsonl_objects(text):
            entry_type = str(entry.get("type") or "")
            payload = entry.get("payload") if isinstance(entry.get("payload"), dict) else {}
            candidate = ""
            if entry_type == "turn_context":
                candidate = clean_room_text(payload.get("model"), limit=128)
            elif entry_type == "event_msg" and str(payload.get("type") or "") == "thread_settings_applied":
                settings = (
                    payload.get("thread_settings")
                    if isinstance(payload.get("thread_settings"), dict)
                    else {}
                )
                candidate = clean_room_text(settings.get("model"), limit=128)
            if candidate:
                observed_model_id = candidate
        if observed_model_id:
            self._observed_models[source] = observed_model_id

    def _turn_input_texts(self, text: str) -> list[str]:
        inputs: list[str] = []
        for entry in _jsonl_objects(text):
            payload = entry.get("payload") if isinstance(entry.get("payload"), dict) else {}
            if str(entry.get("type") or "") == "event_msg" and str(payload.get("type") or "") == "user_message":
                inputs.append(str(payload.get("message") or ""))
            if (
                str(entry.get("type") or "") == "response_item"
                and str(payload.get("type") or "") == "message"
                and str(payload.get("role") or "") == "user"
            ):
                content = payload.get("content")
                if isinstance(content, list):
                    inputs.extend(
                        str(item.get("text") or "")
                        for item in content
                        if isinstance(item, dict) and str(item.get("type") or "") == "input_text"
                    )
        return inputs


__all__ = ["CodexSessionMessageSource"]
