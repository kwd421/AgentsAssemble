from __future__ import annotations

from pathlib import Path

from agentsassemble.providers.transcripts.core import (
    LiveCliMessageExtractionError,
    LiveCliMessageSnapshot,
    _JsonlOffsetMessageSource,
    _activity_category,
    _claude_message_text,
    _claude_project_directory_name,
    _clean_provider_message_text,
    _jsonl_objects,
    _recent_paths,
    _structured_tool_detail,
)
from agentsassemble.room.text import clean_room_text


class ClaudeSessionMessageSource(_JsonlOffsetMessageSource):
    """Read only assistant text from Claude Code's structured session log."""

    source_kind = "claude_session_jsonl"

    def __init__(self, *, home: Path | None = None, cwd: str | Path | None = None) -> None:
        super().__init__(home=home, cwd=cwd)
        self._pending_messages: list[str] = []
        self._observed_model_id = ""
        self._turn_tool_activities: dict[str, dict[str, str]] = {}
        self._turn_activity_sequence = 0

    def prepare_start(self) -> None:
        super().prepare_start()
        self._pending_messages = []
        self._observed_model_id = ""
        self._turn_tool_activities = {}
        self._turn_activity_sequence = 0

    def begin_turn(self, expected_input: str = "") -> None:
        super().begin_turn(expected_input)
        self._pending_messages = []
        self._observed_model_id = ""
        self._turn_tool_activities = {}
        self._turn_activity_sequence = 0

    def _candidate_paths(self) -> list[Path]:
        root = self.home / ".claude" / "projects"
        if not root.exists():
            return []
        if self.cwd is not None:
            project = root / _claude_project_directory_name(self.cwd)
            if project.exists():
                return _recent_paths(project.glob("*.jsonl"))
            return []
        return _recent_paths(root.glob("*/*.jsonl"))

    def _extract_from_text(self, text: str, *, source: str) -> LiveCliMessageSnapshot:
        complete = False
        for entry in _jsonl_objects(text):
            if str(entry.get("type") or "") == "system":
                complete = complete or str(entry.get("subtype") or "") == "turn_duration"
                continue
            message = entry.get("message") if isinstance(entry.get("message"), dict) else {}
            content = message.get("content")
            if str(entry.get("type") or "") == "user" or str(message.get("role") or "") == "user":
                if isinstance(content, list):
                    for block in content:
                        if not isinstance(block, dict) or str(block.get("type") or "") != "tool_result":
                            continue
                        activity_id = clean_room_text(
                            block.get("tool_use_id") or block.get("toolUseId"),
                            limit=128,
                        )
                        previous = self._turn_tool_activities.get(activity_id)
                        if activity_id and previous:
                            failed = bool(
                                block.get("is_error")
                                or block.get("isError")
                                or block.get("error")
                            )
                            self._pending_activities.append(
                                {
                                    **previous,
                                    "activity_id": activity_id,
                                    "status": "failed" if failed else "completed",
                                }
                            )
                continue
            if str(entry.get("type") or "") != "assistant":
                continue
            if bool(entry.get("isApiErrorMessage")) or entry.get("error") or entry.get("apiErrorStatus"):
                detail = _claude_message_text(message) or clean_room_text(entry.get("error"), limit=500)
                raise LiveCliMessageExtractionError(detail or "Claude Code provider authentication failed.")
            if str(message.get("role") or "assistant") != "assistant":
                continue
            self._observed_model_id = clean_room_text(
                message.get("model") or entry.get("model") or entry.get("model_id"),
                limit=128,
            ) or self._observed_model_id
            if isinstance(content, str):
                if str(message.get("stop_reason") or "") == "tool_use":
                    continue
                piece = _clean_provider_message_text(content, limit=12000)
                if piece:
                    self._pending_messages.append(piece)
                continue
            if not isinstance(content, list):
                continue
            entry_id = clean_room_text(
                entry.get("uuid") or entry.get("id") or message.get("id"),
                limit=128,
            )
            for block in content:
                if not isinstance(block, dict):
                    continue
                block_type = str(block.get("type") or "")
                if block_type == "tool_use":
                    self._turn_activity_sequence += 1
                    name = clean_room_text(block.get("name"), limit=120) or "tool"
                    detail = _structured_tool_detail(name, block.get("input"))
                    activity_id = clean_room_text(
                        block.get("id")
                        or f"{entry_id or 'assistant'}-tool-{self._turn_activity_sequence}",
                        limit=128,
                    )
                    activity = {
                        "category": _activity_category(name),
                        "status": "running",
                        "activity_id": activity_id,
                        "activity_title": name,
                        "activity_detail": detail,
                        "content": f"{name}: {detail}" if detail else name,
                    }
                    self._pending_activities.append(activity)
                    if activity_id:
                        self._turn_tool_activities[activity_id] = {
                            "category": activity["category"],
                            "activity_title": name,
                            "activity_detail": detail,
                            "content": f"{name}: {detail}" if detail else name,
                        }
                elif block_type == "thinking":
                    self._turn_activity_sequence += 1
                    thought = _clean_provider_message_text(
                        block.get("thinking") or block.get("text"),
                        limit=2000,
                    )
                    if thought:
                        self._pending_activities.append(
                            {
                                "category": "reasoning",
                                "status": "running",
                                "activity_id": clean_room_text(
                                    block.get("id")
                                    or f"{entry_id or 'assistant'}-reasoning-{self._turn_activity_sequence}",
                                    limit=128,
                                ),
                                "activity_title": "생각",
                                "activity_detail": thought,
                                "content": thought,
                            }
                        )
                elif block_type == "text" and str(message.get("stop_reason") or "") != "tool_use":
                    piece = _clean_provider_message_text(block.get("text"), limit=12000)
                    if piece:
                        self._pending_messages.append(piece)
        result = "\n".join(self._pending_messages).strip()
        return LiveCliMessageSnapshot(
            content=result,
            complete=complete,
            source=source if result or complete else "",
            source_kind=self.source_kind,
            observed_model_id=self._observed_model_id,
        )

    def _turn_input_texts(self, text: str) -> list[str]:
        inputs: list[str] = []
        for entry in _jsonl_objects(text):
            message = entry.get("message") if isinstance(entry.get("message"), dict) else {}
            if str(entry.get("type") or "") != "user" and str(message.get("role") or "") != "user":
                continue
            content = message.get("content")
            if isinstance(content, str):
                inputs.append(content)
            elif isinstance(content, list):
                inputs.extend(
                    str(block.get("text") or "")
                    for block in content
                    if isinstance(block, dict) and str(block.get("type") or "") in {"text", "input_text"}
                )
        return inputs


__all__ = ["ClaudeSessionMessageSource"]
