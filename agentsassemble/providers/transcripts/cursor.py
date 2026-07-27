from __future__ import annotations

from pathlib import Path

from agentsassemble.providers.transcripts.core import (
    LiveCliMessageSnapshot,
    _JsonlOffsetMessageSource,
    _clean_provider_message_text,
    _cursor_project_directory_name,
    _jsonl_objects,
    _normalize_turn_input,
    _recent_paths,
    _tagged_body,
)


class CursorSessionMessageSource(_JsonlOffsetMessageSource):
    """Read assistant prose from Cursor Agent's workspace transcript."""

    source_kind = "cursor_agent_transcript_jsonl"

    def begin_turn(self, expected_input: str = "") -> None:
        super().begin_turn(expected_input)
        if self._bound_path:
            self._offsets[self._bound_path] = 0

    def _candidate_paths(self) -> list[Path]:
        root = self.home / ".cursor" / "projects"
        if not root.exists():
            return []
        if self.cwd is not None:
            project = root / _cursor_project_directory_name(self.cwd)
            if not project.exists():
                return []
            return _recent_paths(project.glob("agent-transcripts/*/*.jsonl"))
        return _recent_paths(root.glob("*/agent-transcripts/*/*.jsonl"))

    def _extract_from_text(self, text: str, *, source: str) -> LiveCliMessageSnapshot:
        messages: list[str] = []
        matched_input = (
            source in self._turn_input_seen_paths
            or not bool(self._expected_turn_input)
        )
        for entry in _jsonl_objects(text):
            role = str(entry.get("role") or "")
            message = entry.get("message") if isinstance(entry.get("message"), dict) else {}
            content = message.get("content")
            if role == "user":
                inputs = [
                    _normalize_turn_input(_tagged_body(str(block.get("text") or ""), "user_query"))
                    for block in (content if isinstance(content, list) else [])
                    if isinstance(block, dict) and str(block.get("type") or "") == "text"
                ]
                if self._expected_turn_input and self._expected_turn_input in inputs:
                    matched_input = True
                    messages = []
                elif matched_input and self._expected_turn_input:
                    matched_input = False
                continue
            if role != "assistant" or not matched_input:
                continue
            if not isinstance(content, list):
                continue
            for block in content:
                if not isinstance(block, dict) or str(block.get("type") or "") != "text":
                    continue
                piece = _clean_provider_message_text(block.get("text"), limit=12000)
                if piece:
                    messages.append(piece)
        result = "\n".join(messages).strip()
        return LiveCliMessageSnapshot(
            content=result,
            complete=bool(result),
            source=source if result else "",
            source_kind=self.source_kind,
        )

    def _turn_input_texts(self, text: str) -> list[str]:
        inputs: list[str] = []
        for entry in _jsonl_objects(text):
            if str(entry.get("role") or "") != "user":
                continue
            message = entry.get("message") if isinstance(entry.get("message"), dict) else {}
            content = message.get("content")
            if not isinstance(content, list):
                continue
            inputs.extend(
                _tagged_body(str(block.get("text") or ""), "user_query")
                for block in content
                if isinstance(block, dict) and str(block.get("type") or "") == "text"
            )
        return inputs


__all__ = ["CursorSessionMessageSource"]
