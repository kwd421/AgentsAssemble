from __future__ import annotations

import json
from pathlib import Path

from agentsassemble.providers.transcripts.core import (
    LiveCliMessageSnapshot,
    _JsonlOffsetMessageSource,
    _activity_category,
    _antigravity_selected_model,
    _antigravity_turn_input_matches,
    _antigravity_user_request,
    _clean_provider_message_text,
    _jsonl_objects,
    _normalize_turn_input,
    _recent_paths,
    _structured_tool_detail,
)
from agentsassemble.room.text import clean_room_text


class AntigravityTranscriptMessageSource(_JsonlOffsetMessageSource):
    source_kind = "antigravity_transcript_jsonl"

    def __init__(self, *, home: Path | None = None, cwd: str | Path | None = None) -> None:
        super().__init__(home=home, cwd=cwd)
        self._observed_model_id = ""

    def prepare_start(self) -> None:
        super().prepare_start()
        self._observed_model_id = ""

    def _candidate_paths(self) -> list[Path]:
        root = self.home / ".gemini" / "antigravity-cli" / "brain"
        if not root.exists():
            return []
        preferred = self._preferred_transcript(root)
        paths = _recent_paths(root.glob("*/.system_generated/logs/transcript.jsonl"))
        if preferred is not None and preferred.exists():
            return [preferred] + [path for path in paths if path != preferred]
        return paths

    def _preferred_transcript(self, root: Path) -> Path | None:
        if self.cwd is None:
            return None
        cache = self.home / ".gemini" / "antigravity-cli" / "cache" / "last_conversations.json"
        try:
            payload = json.loads(cache.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, ValueError):
            return None
        if not isinstance(payload, dict):
            return None
        conversation_id = clean_room_text(payload.get(str(self.cwd)), limit=200)
        if not conversation_id:
            return None
        return root / conversation_id / ".system_generated" / "logs" / "transcript.jsonl"

    def _extract_from_text(self, text: str, *, source: str) -> LiveCliMessageSnapshot:
        latest = ""
        observed_model_id = self._observed_model_id
        for entry in _jsonl_objects(text):
            if str(entry.get("source") or "") != "MODEL":
                continue
            entry_type = str(entry.get("type") or "")
            if entry_type != "PLANNER_RESPONSE":
                continue
            tool_calls = entry.get("tool_calls")
            pending_tool_calls = tool_calls if isinstance(tool_calls, list) else []
            for tool_call in pending_tool_calls:
                if not isinstance(tool_call, dict):
                    continue
                name = clean_room_text(tool_call.get("name"), limit=120) or "tool"
                arguments = tool_call.get("args") if isinstance(tool_call.get("args"), dict) else {}
                detail = _structured_tool_detail(
                    name,
                    arguments,
                    preferred_keys=("CommandLine", "query", "toolSummary"),
                )
                self._pending_activities.append(
                    {
                        "category": _activity_category(name),
                        "status": "running",
                        "content": f"{name}: {detail}" if detail else name,
                    }
                )
            if pending_tool_calls:
                continue
            if str(entry.get("status") or "") and str(entry.get("status") or "") != "DONE":
                continue
            content = _clean_provider_message_text(entry.get("content"), limit=12000)
            if content:
                latest = content
                observed_model_id = clean_room_text(
                    entry.get("model") or entry.get("model_id") or entry.get("modelId"),
                    limit=128,
                ) or observed_model_id
        return LiveCliMessageSnapshot(
            content=latest,
            complete=bool(latest),
            source=source if latest else "",
            source_kind=self.source_kind,
            observed_model_id=observed_model_id,
        )

    def _observe_text(self, text: str, *, source: str) -> None:
        del source
        for entry in _jsonl_objects(text):
            candidate = _antigravity_selected_model(entry.get("content"))
            if candidate:
                self._observed_model_id = candidate

    def _turn_input_texts(self, text: str) -> list[str]:
        return [
            _antigravity_user_request(entry.get("content"))
            for entry in _jsonl_objects(text)
            if str(entry.get("source") or "") == "USER_EXPLICIT"
            or str(entry.get("type") or "") == "USER_INPUT"
        ]

    def _contains_turn_input(self, text: str, *, expected_input: str) -> bool:
        inputs = [_normalize_turn_input(value) for value in self._turn_input_texts(text)]
        if not expected_input:
            return any(inputs)
        return any(
            _antigravity_turn_input_matches(expected_input, observed)
            for observed in inputs
        )


__all__ = ["AntigravityTranscriptMessageSource"]
