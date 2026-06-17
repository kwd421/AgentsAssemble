"""Stream a Claude Code session's thinking by tailing its transcript JSONL.

Claude Code draws its TUI on screen (which the resident scrapes for the final
answer) but ALSO writes a structured transcript to
``~/.claude/projects/<cwd-encoded>/<session-id>.jsonl`` — one JSON object per
line with assistant text, tool_use, and tool_result entries. That structured
log is a far cleaner stream than re-scraping the redrawing terminal, so for live
"thinking" we tail the transcript instead.

We launch claude with ``--session-id <uuid>`` (a uuid we pick), so the transcript
filename is known exactly — no guessing the newest file even when many claude
sessions run at once.

This module is pure parsing + a file tailer (no subprocess); the runner wires it
to the room via post_room_thought.
"""
from __future__ import annotations

import json
import time
import uuid
from pathlib import Path


def generate_claude_session_id() -> str:
    return str(uuid.uuid4())


def find_claude_transcript(session_id: str, *, home: Path | None = None) -> Path | None:
    """Locate the transcript file for a known session id.

    The file is named ``<session-id>.jsonl`` under some project dir; globbing by
    the (unique) session id avoids needing to reproduce claude's cwd encoding and
    is unambiguous across concurrent sessions.
    """
    clean = str(session_id or "").strip()
    if not clean:
        return None
    base = (home or Path.home()) / ".claude" / "projects"
    if not base.exists():
        return None
    for candidate in base.glob(f"*/{clean}.jsonl"):
        return candidate
    return None


def parse_claude_transcript_line(line: str) -> dict | None:
    """Map one transcript JSONL line to a thought event, or None to ignore.

    Returns {"kind": "message"|"command"|"reasoning", "text": ...}. Only
    assistant-side entries are surfaced; user/tool_result rows are skipped.
    """
    text = (line or "").strip()
    if not text:
        return None
    try:
        entry = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(entry, dict) or str(entry.get("type") or "") != "assistant":
        return None
    message = entry.get("message") if isinstance(entry.get("message"), dict) else {}
    content = message.get("content")
    if isinstance(content, str):
        body = content.strip()
        return {"kind": "message", "text": body} if body else None
    if not isinstance(content, list):
        return None
    parts: list[str] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        block_type = str(block.get("type") or "")
        if block_type == "text":
            piece = str(block.get("text") or "").strip()
            if piece:
                parts.append(piece)
        elif block_type == "thinking":
            piece = str(block.get("thinking") or block.get("text") or "").strip()
            if piece:
                return {"kind": "reasoning", "text": piece}
        elif block_type == "tool_use":
            tool = str(block.get("name") or "tool")
            return {"kind": "command", "text": f"{tool}"}
    body = "\n".join(parts).strip()
    return {"kind": "message", "text": body} if body else None


class ClaudeTranscriptTailer:
    """Tail a transcript file from its current end, yielding new thought events.

    Usage: construct (snapshots the file's current size as the start offset so we
    don't replay history), then call poll() repeatedly until the turn is done.
    Tolerant of the file not existing yet (claude creates it a beat after launch).
    """

    def __init__(self, path_provider) -> None:
        # path_provider: () -> Path|None, resolved lazily (file appears after spawn)
        self._path_provider = path_provider
        self._path: Path | None = None
        self._offset = 0

    def _resolve(self) -> Path | None:
        if self._path is None:
            path = self._path_provider()
            if path is not None and path.exists():
                self._path = path
                # Start at end: only stream events produced during this turn.
                self._offset = path.stat().st_size
        return self._path

    def poll(self) -> list[dict]:
        path = self._resolve()
        if path is None or not path.exists():
            return []
        events: list[dict] = []
        try:
            with path.open("r", encoding="utf-8") as handle:
                handle.seek(self._offset)
                for line in handle:
                    if not line.endswith("\n"):
                        break  # partial write; retry next poll
                    self._offset += len(line.encode("utf-8"))
                    event = parse_claude_transcript_line(line)
                    if event is not None:
                        events.append(event)
        except OSError:
            return events
        return events


def tail_until(tailer: ClaudeTranscriptTailer, stop, on_event, *, interval: float = 0.4) -> None:
    """Poll the tailer until stop() is true, calling on_event(event) for each."""
    while not stop():
        for event in tailer.poll():
            on_event(event)
        time.sleep(interval)
    for event in tailer.poll():  # final drain
        on_event(event)
