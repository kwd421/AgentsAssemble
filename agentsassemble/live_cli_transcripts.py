from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
from urllib.parse import quote

from agentsassemble.live_cli_output import extract_live_cli_terminal_message
from agentsassemble.meeting_events import clean_lobby_text


class LiveCliMessageExtractionError(RuntimeError):
    """A provider turn finished without a clean assistant message."""


@dataclass(frozen=True)
class LiveCliMessageSnapshot:
    content: str = ""
    complete: bool = False
    source: str = ""
    source_kind: str = ""


class LiveCliMessageSource(Protocol):
    strict: bool

    def prepare_start(self) -> None:
        ...

    def begin_turn(self) -> None:
        ...

    def poll(self, terminal_output: bytes, *, quiet: bool = False) -> LiveCliMessageSnapshot:
        ...

    def describe(self) -> dict[str, object]:
        ...


class TerminalCaptureMessageSource:
    """Use PTY output as the canonical source for non-provider test CLIs."""

    strict = False

    def prepare_start(self) -> None:
        return

    def begin_turn(self) -> None:
        return

    def poll(self, terminal_output: bytes, *, quiet: bool = False) -> LiveCliMessageSnapshot:
        content = extract_live_cli_terminal_message(terminal_output)
        return LiveCliMessageSnapshot(
            content=content,
            complete=bool(content and quiet),
            source="pty",
            source_kind="terminal_capture",
        )

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

    def prepare_start(self) -> None:
        self._offsets = {}

    def begin_turn(self) -> None:
        self._offsets = {}
        for path in self._candidate_paths():
            self._offsets[str(path)] = _safe_size(path)

    def poll(self, terminal_output: bytes, *, quiet: bool = False) -> LiveCliMessageSnapshot:
        del terminal_output, quiet
        latest = LiveCliMessageSnapshot(source_kind=self.source_kind)
        for path in self._candidate_paths():
            path_key = str(path)
            start = self._offsets.get(path_key, 0)
            text, next_offset = _read_from_offset(path, start)
            self._offsets[path_key] = next_offset
            if not text:
                continue
            snapshot = self._extract_from_text(text, source=str(path))
            if snapshot.content:
                latest = snapshot
        return latest

    def describe(self) -> dict[str, object]:
        return {
            "message_source": self.source_kind,
            "message_source_strict": True,
        }

    def _candidate_paths(self) -> list[Path]:
        raise NotImplementedError

    def _extract_from_text(self, text: str, *, source: str) -> LiveCliMessageSnapshot:
        raise NotImplementedError


class CodexSessionMessageSource(_JsonlOffsetMessageSource):
    source_kind = "codex_session_jsonl"

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
        for entry in _jsonl_objects(text):
            payload = entry.get("payload") if isinstance(entry.get("payload"), dict) else {}
            entry_type = str(entry.get("type") or "")
            payload_type = str(payload.get("type") or "")
            if entry_type == "event_msg" and payload_type == "agent_message":
                latest = clean_lobby_text(payload.get("message"), limit=12000)
            elif entry_type == "event_msg" and payload_type == "task_complete":
                latest = latest or clean_lobby_text(payload.get("last_agent_message"), limit=12000)
            elif entry_type == "response_item":
                latest = latest or _codex_response_item_text(payload)
        return LiveCliMessageSnapshot(
            content=latest,
            complete=bool(latest),
            source=source if latest else "",
            source_kind=self.source_kind,
        )


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
        for entry in _jsonl_objects(text):
            if str(entry.get("type") or "") != "assistant":
                continue
            content = entry.get("content")
            if isinstance(content, str):
                latest = clean_lobby_text(content, limit=12000)
        return LiveCliMessageSnapshot(
            content=latest,
            complete=bool(latest),
            source=source if latest else "",
            source_kind=self.source_kind,
        )


class AntigravityTranscriptMessageSource(_JsonlOffsetMessageSource):
    source_kind = "antigravity_transcript_jsonl"

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
        conversation_id = clean_lobby_text(payload.get(str(self.cwd)), limit=200)
        if not conversation_id:
            return None
        return root / conversation_id / ".system_generated" / "logs" / "transcript.jsonl"

    def _extract_from_text(self, text: str, *, source: str) -> LiveCliMessageSnapshot:
        latest = ""
        for entry in _jsonl_objects(text):
            if str(entry.get("source") or "") != "MODEL":
                continue
            if str(entry.get("type") or "") != "PLANNER_RESPONSE":
                continue
            if str(entry.get("status") or "") and str(entry.get("status") or "") != "DONE":
                continue
            content = clean_lobby_text(entry.get("content"), limit=12000)
            if content:
                latest = content
        return LiveCliMessageSnapshot(
            content=latest,
            complete=bool(latest),
            source=source if latest else "",
            source_kind=self.source_kind,
        )


def make_live_cli_message_source(
    agent_id: str,
    command: list[str],
    *,
    cwd: str | Path | None = None,
) -> LiveCliMessageSource:
    provider = _provider_key(agent_id, command)
    if provider == "codex":
        return CodexSessionMessageSource(cwd=cwd)
    if provider == "grok":
        return GrokSessionMessageSource(cwd=cwd)
    if provider == "antigravity":
        return AntigravityTranscriptMessageSource(cwd=cwd)
    return TerminalCaptureMessageSource()


def _provider_key(agent_id: str, command: list[str]) -> str:
    agent = clean_lobby_text(agent_id, limit=128).casefold()
    executable = Path(str(command[0] if command else "")).name.casefold()
    resolved = Path(shutil.which(str(command[0])) or executable).name.casefold() if command else ""
    names = {agent, executable, resolved}
    if "codex" in names:
        return "codex"
    if "grok" in names:
        return "grok"
    if names & {"agy", "antigravity"}:
        return "antigravity"
    return ""


def _codex_response_item_text(payload: dict[str, object]) -> str:
    if str(payload.get("type") or "") != "message" or str(payload.get("role") or "") != "assistant":
        return ""
    parts: list[str] = []
    content = payload.get("content")
    if not isinstance(content, list):
        return ""
    for item in content:
        if isinstance(item, dict) and str(item.get("type") or "") == "output_text":
            piece = clean_lobby_text(item.get("text"), limit=12000)
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
    try:
        with path.open("rb") as handle:
            handle.seek(max(0, int(offset)))
            data = handle.read()
            next_offset = handle.tell()
    except OSError:
        return "", max(0, int(offset))
    return data.decode("utf-8", errors="replace"), next_offset


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
