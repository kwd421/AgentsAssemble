from __future__ import annotations

import json
import os
import re
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
    error: str = ""


class LiveCliMessageSource(Protocol):
    strict: bool

    def prepare_start(self) -> None:
        ...

    def begin_turn(self, expected_input: str = "") -> None:
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

    def prepare_start(self) -> None:
        self._offsets = {}
        self._active_paths = set()
        self._turn_input_seen_paths = set()
        self._bound_path = ""
        self._expected_turn_input = ""
        self._ignored_existing_paths = {str(path) for path in self._candidate_paths()}

    def begin_turn(self, expected_input: str = "") -> None:
        self._offsets = {}
        self._turn_input_seen_paths = set()
        self._expected_turn_input = _normalize_turn_input(expected_input)
        for path in self._visible_candidate_paths():
            self._offsets[str(path)] = _safe_size(path)

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
            exact_input = self._contains_turn_input(text, expected_input=self._expected_turn_input)
            bound_session_input = bool(
                self._bound_path == path_key and self._contains_any_turn_input(text)
            )
            if exact_input or bound_session_input:
                self._turn_input_seen_paths.add(path_key)
                if not self._bound_path:
                    self._bound_path = path_key
                    self._active_paths.add(path_key)
            if path_key not in self._turn_input_seen_paths:
                continue
            if self._bound_path and path_key != self._bound_path:
                continue
            snapshot = self._extract_from_text(text, source=str(path))
            if snapshot.content or snapshot.error:
                self._active_paths.add(path_key)
                latest = snapshot
        return latest

    def describe(self) -> dict[str, object]:
        return {
            "message_source": self.source_kind,
            "message_source_strict": True,
            "message_source_bound": bool(self._bound_path),
        }

    def _candidate_paths(self) -> list[Path]:
        raise NotImplementedError

    def _visible_candidate_paths(self) -> list[Path]:
        if self._bound_path:
            bound = Path(self._bound_path)
            return [bound] if bound.is_file() else []
        paths: list[Path] = []
        for path in self._candidate_paths():
            key = str(path)
            if key in self._ignored_existing_paths and key not in self._active_paths:
                continue
            paths.append(path)
        return paths

    def _extract_from_text(self, text: str, *, source: str) -> LiveCliMessageSnapshot:
        raise NotImplementedError

    def _turn_input_texts(self, text: str) -> list[str]:
        raise NotImplementedError

    def _contains_turn_input(self, text: str, *, expected_input: str) -> bool:
        inputs = [_normalize_turn_input(value) for value in self._turn_input_texts(text)]
        if expected_input:
            return expected_input in inputs
        return any(inputs)

    def _contains_any_turn_input(self, text: str) -> bool:
        return any(_normalize_turn_input(value) for value in self._turn_input_texts(text))


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
        turn_completed_without_message = False
        for entry in _jsonl_objects(text):
            payload = entry.get("payload") if isinstance(entry.get("payload"), dict) else {}
            entry_type = str(entry.get("type") or "")
            payload_type = str(payload.get("type") or "")
            if entry_type == "event_msg" and payload_type == "agent_message":
                latest = clean_lobby_text(payload.get("message"), limit=12000)
            elif entry_type == "event_msg" and payload_type == "task_complete":
                latest = clean_lobby_text(payload.get("last_agent_message"), limit=12000) or latest
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
            error=error,
        )

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
                latest = _clean_grok_assistant_content(content)
        return LiveCliMessageSnapshot(
            content=latest,
            complete=bool(latest),
            source=source if latest else "",
            source_kind=self.source_kind,
        )

    def _turn_input_texts(self, text: str) -> list[str]:
        inputs: list[str] = []
        for entry in _jsonl_objects(text):
            if str(entry.get("type") or "") != "user":
                continue
            inputs.extend(_grok_user_inputs(entry.get("content")))
        return inputs


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

    def _turn_input_texts(self, text: str) -> list[str]:
        return [
            _antigravity_user_request(entry.get("content"))
            for entry in _jsonl_objects(text)
            if str(entry.get("source") or "") == "USER_EXPLICIT"
            or str(entry.get("type") or "") == "USER_INPUT"
        ]


class ClaudeSessionMessageSource(_JsonlOffsetMessageSource):
    """Read only assistant text from Claude Code's structured session log."""

    source_kind = "claude_session_jsonl"

    def _candidate_paths(self) -> list[Path]:
        root = self.home / ".claude" / "projects"
        if not root.exists():
            return []
        if self.cwd is not None:
            project = root / re.sub(r"[/.]", "-", str(self.cwd))
            if project.exists():
                return _recent_paths(project.glob("*.jsonl"))
            return []
        return _recent_paths(root.glob("*/*.jsonl"))

    def _extract_from_text(self, text: str, *, source: str) -> LiveCliMessageSnapshot:
        messages: list[str] = []
        for entry in _jsonl_objects(text):
            if str(entry.get("type") or "") != "assistant":
                continue
            message = entry.get("message") if isinstance(entry.get("message"), dict) else {}
            if bool(entry.get("isApiErrorMessage")) or entry.get("error") or entry.get("apiErrorStatus"):
                detail = _claude_message_text(message) or clean_lobby_text(entry.get("error"), limit=500)
                raise LiveCliMessageExtractionError(detail or "Claude Code provider authentication failed.")
            if str(message.get("role") or "assistant") != "assistant":
                continue
            content = message.get("content")
            if isinstance(content, str):
                piece = clean_lobby_text(content, limit=12000)
                if piece:
                    messages.append(piece)
                continue
            if not isinstance(content, list):
                continue
            for block in content:
                if not isinstance(block, dict) or str(block.get("type") or "") != "text":
                    continue
                piece = clean_lobby_text(block.get("text"), limit=12000)
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


def _claude_message_text(message: dict[str, object]) -> str:
    content = message.get("content")
    if isinstance(content, str):
        return clean_lobby_text(content, limit=1000)
    if not isinstance(content, list):
        return ""
    return "\n".join(
        piece
        for block in content
        if isinstance(block, dict) and str(block.get("type") or "") == "text"
        if (piece := clean_lobby_text(block.get("text"), limit=1000))
    ).strip()


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
    if provider == "claude":
        return ClaudeSessionMessageSource(cwd=cwd)
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
    if "claude" in names:
        return "claude"
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


def _antigravity_user_request(value: object) -> str:
    request = _tagged_body(str(value or ""), "USER_REQUEST")
    request = re.sub(r"^\s*/plan\s+", "", request, count=1, flags=re.IGNORECASE)
    return re.sub(r"\s*/plan\s*$", "", request, count=1, flags=re.IGNORECASE).strip()


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
    return clean_lobby_text(content, limit=12000)


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
