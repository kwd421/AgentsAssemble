from __future__ import annotations

from pathlib import Path
from urllib.parse import quote

from agentsassemble.providers.transcripts.core import (
    LiveCliMessageSnapshot,
    _JsonlOffsetMessageSource,
    _clean_grok_assistant_content,
    _grok_user_inputs,
    _jsonl_objects,
    _recent_paths,
)
from agentsassemble.room.text import clean_room_text


class GrokSessionMessageSource(_JsonlOffsetMessageSource):
    source_kind = "grok_chat_history"

    def _candidate_paths(self) -> list[Path]:
        root = self.home / ".grok" / "sessions"
        if not root.exists():
            return []
        workspace_dir = self._workspace_session_dir(root)
        if workspace_dir is not None and workspace_dir.exists():
            return _recent_paths(workspace_dir.glob("*/chat_history.jsonl"))
        return _recent_paths(root.glob("*/*/chat_history.jsonl"))

    def _workspace_session_dir(self, root: Path) -> Path | None:
        if self.cwd is None:
            return None
        encoded = quote(str(self.cwd), safe="")
        return root / encoded

    def _extract_from_text(self, text: str, *, source: str) -> LiveCliMessageSnapshot:
        latest = ""
        observed_model_id = ""
        for entry in _jsonl_objects(text):
            if str(entry.get("type") or "") != "assistant":
                continue
            content = entry.get("content")
            if isinstance(content, str):
                latest = _clean_grok_assistant_content(content)
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

    def _turn_input_texts(self, text: str) -> list[str]:
        inputs: list[str] = []
        for entry in _jsonl_objects(text):
            if str(entry.get("type") or "") != "user":
                continue
            inputs.extend(_grok_user_inputs(entry.get("content")))
        return inputs


__all__ = ["GrokSessionMessageSource"]
