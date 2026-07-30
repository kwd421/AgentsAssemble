from __future__ import annotations

import json
import os
import re
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
from urllib.parse import quote

from agentsassemble.providers.live_cli_output import extract_live_cli_terminal_message
from agentsassemble.room.text import clean_room_text


class LiveCliMessageExtractionError(RuntimeError):
    """A provider turn finished without a clean assistant message."""


@dataclass(frozen=True)
class LiveCliMessageSnapshot:
    content: str = ""
    complete: bool = False
    source: str = ""
    source_kind: str = ""
    observed_model_id: str = ""
    error: str = ""


class LiveCliMessageSource(Protocol):
    strict: bool

    def prepare_start(self) -> None:
        ...

    def begin_turn(self, expected_input: str = "") -> None:
        ...

    def poll(self, terminal_output: bytes, *, quiet: bool = False) -> LiveCliMessageSnapshot:
        ...

    def drain_activities(self) -> list[dict[str, object]]:
        ...

    def describe(self) -> dict[str, object]:
        ...


class TerminalCaptureMessageSource:
    """Use PTY output as the canonical source for non-provider test CLIs."""

    strict = False

    def prepare_start(self) -> None:
        return

    def begin_turn(self, expected_input: str = "") -> None:
        del expected_input
        return

    def poll(self, terminal_output: bytes, *, quiet: bool = False) -> LiveCliMessageSnapshot:
        content = extract_live_cli_terminal_message(terminal_output)
        return LiveCliMessageSnapshot(
            content=content,
            complete=bool(content and quiet),
            source="pty",
            source_kind="terminal_capture",
        )

    def drain_activities(self) -> list[dict[str, object]]:
        return []

    def describe(self) -> dict[str, object]:
        return {
            "message_source": "terminal_capture",
            "message_source_strict": False,
        }


class _JsonlOffsetMessageSource:
    strict = True
    source_kind = "jsonl"

    def __init__(self, *, home: Path | None = None, cwd: str | Path | None = None) -> None:
        self.home = Path(home or Path.home())
        self.cwd = Path(cwd).expanduser() if cwd else None
        self._offsets: dict[str, int] = {}
        self._ignored_existing_paths: set[str] = set()
        self._active_paths: set[str] = set()
        self._turn_input_seen_paths: set[str] = set()
        self._bound_path = ""
        self._expected_turn_input = ""
        self._pending_activities: list[dict[str, object]] = []
        self._candidate_scan_interval_seconds = 0.5
        self._last_candidate_scan_at = 0.0
        self._cached_visible_candidate_paths: list[Path] = []

    def prepare_start(self) -> None:
        self._offsets = {}
        self._active_paths = set()
        self._turn_input_seen_paths = set()
        self._bound_path = ""
        self._expected_turn_input = ""
        self._pending_activities = []
        self._ignored_existing_paths = {str(path) for path in self._candidate_paths()}
        self._last_candidate_scan_at = 0.0
        self._cached_visible_candidate_paths = []

    def begin_turn(self, expected_input: str = "") -> None:
        self._offsets = {}
        self._turn_input_seen_paths = set()
        self._expected_turn_input = _normalize_turn_input(expected_input)
        self._pending_activities = []
        for path in self._visible_candidate_paths(force=True):
            self._offsets[str(path)] = _safe_size(path)

    def drain_activities(self) -> list[dict[str, object]]:
        activities = list(self._pending_activities)
        self._pending_activities = []
        return activities

    def poll(self, terminal_output: bytes, *, quiet: bool = False) -> LiveCliMessageSnapshot:
        del terminal_output, quiet
        latest = LiveCliMessageSnapshot(source_kind=self.source_kind)
        for path in self._visible_candidate_paths():
            path_key = str(path)
            start = self._offsets.get(path_key, 0)
            text, next_offset = _read_from_offset(path, start)
            self._offsets[path_key] = next_offset
            if not text:
                continue
            self._observe_text(text, source=path_key)
            scoped_text = text
            if path_key not in self._turn_input_seen_paths:
                turn_input_offset = self._turn_input_offset(text, path_key=path_key)
                if turn_input_offset is None:
                    continue
                self._turn_input_seen_paths.add(path_key)
                if not self._bound_path:
                    self._bound_path = path_key
                    self._active_paths.add(path_key)
                scoped_text = text[turn_input_offset:]
            if path_key not in self._turn_input_seen_paths:
                continue
            if self._bound_path and path_key != self._bound_path:
                continue
            snapshot = self._extract_from_text(scoped_text, source=str(path))
            if snapshot.content or snapshot.error or snapshot.complete:
                self._active_paths.add(path_key)
                latest = snapshot
        return latest

    def _turn_input_offset(self, text: str, *, path_key: str) -> int | None:
        """Locate this turn's user row so earlier activity in the same read is ignored."""
        offset = 0
        for line in text.splitlines(keepends=True):
            exact_input = self._contains_turn_input(
                line,
                expected_input=self._expected_turn_input,
            )
            bound_session_input = bool(
                self._bound_path == path_key and self._contains_any_turn_input(line)
            )
            if exact_input or bound_session_input:
                return offset
            offset += len(line)
        return None

    def describe(self) -> dict[str, object]:
        return {
            "message_source": self.source_kind,
            "message_source_strict": True,
            "message_source_bound": bool(self._bound_path),
        }

    def _candidate_paths(self) -> list[Path]:
        raise NotImplementedError

    def _visible_candidate_paths(self, *, force: bool = False) -> list[Path]:
        if self._bound_path:
            bound = Path(self._bound_path)
            return [bound] if bound.is_file() else []
        now = time.monotonic()
        if (
            not force
            and self._last_candidate_scan_at
            and now - self._last_candidate_scan_at
            < self._candidate_scan_interval_seconds
        ):
            return list(self._cached_visible_candidate_paths)
        paths: list[Path] = []
        for path in self._candidate_paths():
            key = str(path)
            if key in self._ignored_existing_paths and key not in self._active_paths:
                continue
            paths.append(path)
        self._last_candidate_scan_at = 0.0 if force and not paths else now
        self._cached_visible_candidate_paths = list(paths)
        return paths

    def _extract_from_text(self, text: str, *, source: str) -> LiveCliMessageSnapshot:
        raise NotImplementedError

    def _observe_text(self, text: str, *, source: str) -> None:
        del text, source

    def _turn_input_texts(self, text: str) -> list[str]:
        raise NotImplementedError

    def _contains_turn_input(self, text: str, *, expected_input: str) -> bool:
        inputs = [_normalize_turn_input(value) for value in self._turn_input_texts(text)]
        if expected_input:
            return expected_input in inputs
        return any(inputs)

    def _contains_any_turn_input(self, text: str) -> bool:
        return any(_normalize_turn_input(value) for value in self._turn_input_texts(text))


def _claude_project_directory_name(cwd: Path) -> str:
    return re.sub(r"[^A-Za-z0-9-]", "-", str(cwd))


def _cursor_project_directory_name(cwd: Path) -> str:
    encoded = re.sub(r"[^A-Za-z0-9-]", "-", str(cwd)).strip("-")
    return re.sub(r"-+", "-", encoded)


def _claude_message_text(message: dict[str, object]) -> str:
    content = message.get("content")
    if isinstance(content, str):
        return _clean_provider_message_text(content, limit=1000)
    if not isinstance(content, list):
        return ""
    return "\n".join(
        piece
        for block in content
        if isinstance(block, dict) and str(block.get("type") or "") == "text"
        if (piece := _clean_provider_message_text(block.get("text"), limit=1000))
    ).strip()


def _structured_tool_detail(
    tool_name: str,
    tool_input: object,
    *,
    preferred_keys: tuple[str, ...] = (
        "command",
        "file_path",
        "path",
        "pattern",
        "query",
        "url",
        "description",
        "prompt",
    ),
) -> str:
    del tool_name
    if not isinstance(tool_input, dict):
        return ""
    for key in preferred_keys:
        value = tool_input.get(key)
        if isinstance(value, str) and value.strip():
            return clean_room_text(value, limit=600)
    return ""


def _activity_category(tool_name: str) -> str:
    value = clean_room_text(tool_name, limit=120).casefold()
    if any(word in value for word in ("read", "file", "open")):
        return "file_read"
    if any(word in value for word in ("grep", "search", "find")):
        return "search"
    if any(word in value for word in ("browser", "fetch", "http", "web")):
        return "web"
    if any(word in value for word in ("bash", "command", "exec", "run", "shell", "terminal")):
        return "command"
    return "tool"


def _codex_response_item_text(payload: dict[str, object]) -> str:
    if str(payload.get("type") or "") != "message" or str(payload.get("role") or "") != "assistant":
        return ""
    parts: list[str] = []
    content = payload.get("content")
    if not isinstance(content, list):
        return ""
    for item in content:
        if isinstance(item, dict) and str(item.get("type") or "") == "output_text":
            piece = _clean_provider_message_text(item.get("text"), limit=12000)
            if piece:
                parts.append(piece)
    return "\n".join(parts).strip()


def _jsonl_objects(text: str) -> list[dict[str, object]]:
    objects: list[dict[str, object]] = []
    for line in str(text or "").splitlines():
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(entry, dict):
            objects.append(entry)
    return objects


def _read_from_offset(path: Path, offset: int) -> tuple[str, int]:
    start = max(0, int(offset))
    try:
        with path.open("rb") as handle:
            handle.seek(start)
            data = handle.read()
    except OSError:
        return "", start
    if not data:
        return "", start
    if data.endswith((b"\n", b"\r")):
        complete = data
    else:
        last_newline = max(data.rfind(b"\n"), data.rfind(b"\r"))
        complete = data[: last_newline + 1] if last_newline >= 0 else b""
        trailing = data[last_newline + 1 :]
        try:
            parsed = json.loads(trailing.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
            parsed = None
        if isinstance(parsed, dict):
            complete = data
    return complete.decode("utf-8", errors="replace"), start + len(complete)


def _normalize_turn_input(value: object) -> str:
    return str(value or "").replace("\r\n", "\n").replace("\r", "\n").strip()


def _clean_provider_message_text(value: object, *, limit: int) -> str:
    """Bound provider-visible prose without destroying its Markdown structure."""
    normalized = (
        str(value or "")
        .replace("\x00", "")
        .replace("\r\n", "\n")
        .replace("\r", "\n")
        .strip()
    )
    return normalized[: max(0, int(limit))].strip()


def _antigravity_user_request(value: object) -> str:
    request = _tagged_body(str(value or ""), "USER_REQUEST")
    request = re.sub(r"^\s*/plan\s+", "", request, count=1, flags=re.IGNORECASE)
    return re.sub(r"\s*/plan\s*$", "", request, count=1, flags=re.IGNORECASE).strip()


def _antigravity_turn_input_matches(expected: str, observed: str) -> bool:
    if observed == expected:
        return True
    marker = re.search(r"\n?<truncated \d+ bytes>\n?", observed)
    if marker is None:
        return False
    prefix = observed[: marker.start()].rstrip("\n")
    suffix = observed[marker.end() :].lstrip("\n")
    suffix_anchor = suffix[-512:]
    return (
        len(prefix) >= 128
        and len(suffix_anchor) >= 128
        and expected.startswith(prefix)
        and expected.endswith(suffix_anchor)
    )


def _antigravity_selected_model(value: object) -> str:
    settings = _tagged_body(str(value or ""), "USER_SETTINGS_CHANGE")
    match = re.search(
        r"changed setting `Model Selection` from .*? to (.+?)\.\s+No need to comment",
        settings,
        flags=re.DOTALL,
    )
    return clean_room_text(match.group(1), limit=128) if match else ""


def _grok_user_inputs(value: object) -> list[str]:
    if isinstance(value, str):
        candidates = [value]
    elif isinstance(value, list):
        candidates = [
            str(block.get("text") or "")
            for block in value
            if isinstance(block, dict) and str(block.get("type") or "") == "text"
        ]
    else:
        return []
    return [_tagged_body(candidate, "user_query") for candidate in candidates]


def _clean_grok_assistant_content(value: str) -> str:
    content = re.sub(r"(?:<\|eos\|>)+\s*$", "", str(value or ""), flags=re.IGNORECASE)
    return _clean_provider_message_text(content, limit=12000)


def _tagged_body(content: str, tag: str) -> str:
    opening = f"<{tag}>"
    closing = f"</{tag}>"
    start = content.find(opening)
    if start < 0:
        return content
    end = content.find(closing, start + len(opening))
    if end < 0:
        return content
    return content[start + len(opening) : end]


def _safe_size(path: Path) -> int:
    try:
        return int(path.stat().st_size)
    except OSError:
        return 0


def _recent_paths(paths: object) -> list[Path]:
    found: list[Path] = []
    for path in paths:
        if isinstance(path, Path):
            found.append(path)
    found.sort(key=lambda item: (_safe_mtime(item), str(item)), reverse=True)
    return found[:20]


def _safe_mtime(path: Path) -> float:
    try:
        return os.path.getmtime(path)
    except OSError:
        return 0.0


__all__ = [
    "LiveCliMessageExtractionError",
    "LiveCliMessageSnapshot",
    "LiveCliMessageSource",
    "TerminalCaptureMessageSource",
    "_JsonlOffsetMessageSource",
    "_activity_category",
    "_antigravity_selected_model",
    "_antigravity_turn_input_matches",
    "_antigravity_user_request",
    "_claude_message_text",
    "_claude_project_directory_name",
    "_clean_grok_assistant_content",
    "_clean_provider_message_text",
    "_codex_response_item_text",
    "_cursor_project_directory_name",
    "_grok_user_inputs",
    "_jsonl_objects",
    "_normalize_turn_input",
    "_read_from_offset",
    "_recent_paths",
    "_safe_mtime",
    "_safe_size",
    "_structured_tool_detail",
    "_tagged_body",
]
