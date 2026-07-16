"""Resident Kiro CLI command adapter."""

from __future__ import annotations

import re
import subprocess
import threading
from pathlib import Path
from subprocess import TimeoutExpired
from typing import Any

from agentsassemble.providers.resident_config import ResidentCommandConfig


_ANSI_RE = re.compile(r"\x1b(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~]|\][^\x07]*(?:\x07|\x1b\\))")
_SESSION_ID_RE = re.compile(
    r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"
)
_SESSION_LINE_RE = re.compile(
    r"Chat\s+SessionId:\s*("
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
    r")",
    re.IGNORECASE,
)
_FRESH_SESSION_LOCK = threading.Lock()


class KiroResidentCommandRunner:
    """Run a resident Kiro CLI participant through kiro chat --resume-id."""

    def __init__(
        self,
        config: ResidentCommandConfig,
        *,
        command_runner: Any | None = None,
        cwd: Path | None = None,
    ) -> None:
        self.config = config
        self.command_runner = command_runner or subprocess.run
        self.cwd = Path(cwd or Path.cwd())
        self.session_id = str(config.session_id or "").strip()

    def __call__(self, command: list[str], prompt: str, *, timeout_seconds: int) -> str:
        del command
        if not self.session_id:
            with _FRESH_SESSION_LOCK:
                if not self.session_id:
                    before_ids = self._list_session_ids(timeout_seconds=timeout_seconds)
                    return self._call_chat(prompt, timeout_seconds=timeout_seconds, before_ids=before_ids)
        return self._call_chat(prompt, timeout_seconds=timeout_seconds, before_ids=None)

    def _call_chat(self, prompt: str, *, timeout_seconds: int, before_ids: list[str] | None) -> str:
        chat_command = self._build_chat_command(prompt)
        try:
            completed = self.command_runner(
                chat_command,
                input="",
                text=True,
                capture_output=True,
                timeout=timeout_seconds,
                check=False,
                cwd=str(self.cwd),
            )
        except TimeoutExpired as error:
            raise RuntimeError(f"Kiro live session command timed out after {timeout_seconds} seconds.") from error
        returncode = int(getattr(completed, "returncode", 0) or 0)
        if returncode != 0:
            raise RuntimeError(f"Kiro live session command failed with return code {returncode}.")
        stdout = _text(getattr(completed, "stdout", ""))
        stderr = _text(getattr(completed, "stderr", ""))
        if before_ids is not None:
            self.session_id = self._new_session_id(before_ids, stdout + "\n" + stderr, timeout_seconds=timeout_seconds)
        clean_message = clean_kiro_reply(stdout)
        if not clean_message:
            raise ValueError("Kiro live session returned an empty reply.")
        return clean_message

    def close(self) -> None:
        return None

    def _build_chat_command(self, prompt: str) -> list[str]:
        command = _configured_chat_command(self.config.command or ["kiro"])
        command = _without_resume_args(command)
        command = _ensure_chat_defaults(command)
        if self.session_id:
            command.extend(["--resume-id", self.session_id])
        command.append(prompt)
        return command

    def _list_session_ids(self, *, timeout_seconds: int) -> list[str]:
        command = _configured_chat_command(self.config.command or ["kiro"])
        command = _without_resume_args(command)
        if "--list-sessions" not in command:
            command.append("--list-sessions")
        try:
            completed = self.command_runner(
                command,
                input="",
                text=True,
                capture_output=True,
                timeout=timeout_seconds,
                check=False,
                cwd=str(self.cwd),
            )
        except TimeoutExpired as error:
            raise RuntimeError(f"Kiro session list timed out after {timeout_seconds} seconds.") from error
        returncode = int(getattr(completed, "returncode", 0) or 0)
        if returncode != 0:
            raise RuntimeError(f"Kiro session list failed with return code {returncode}.")
        return extract_kiro_session_ids(_text(getattr(completed, "stdout", "")) + "\n" + _text(getattr(completed, "stderr", "")))

    def _new_session_id(self, before_ids: list[str], output: str, *, timeout_seconds: int) -> str:
        del output
        before = set(before_ids)
        after_ids = self._list_session_ids(timeout_seconds=timeout_seconds)
        for session_id in after_ids:
            if session_id not in before:
                return session_id
        raise RuntimeError("Kiro live session did not expose a new session id.")


def extract_kiro_session_ids(text: object) -> list[str]:
    cleaned = _strip_ansi(_text(text))
    seen: set[str] = set()
    ids: list[str] = []
    for match in _SESSION_LINE_RE.finditer(cleaned):
        session_id = match.group(1).lower()
        if session_id in seen:
            continue
        seen.add(session_id)
        ids.append(session_id)
    return ids


def clean_kiro_reply(text: object) -> str:
    cleaned = _strip_ansi(_text(text))
    lines: list[str] = []
    for line in cleaned.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.casefold().startswith("credits:"):
            continue
        if _SESSION_LINE_RE.search(stripped):
            continue
        if stripped.startswith("> "):
            stripped = stripped[2:].strip()
        lines.append(stripped)
    return "\n".join(lines).strip()


def default_kiro_resident_command(provider_kind: str, connection_kind: str, command: list[str]) -> list[str]:
    if provider_kind == "kiro_live_session" and connection_kind == "live_session" and not command:
        return ["kiro"]
    return command


def kiro_provider_connection_check(provider_kind: str, connection_kind: str) -> dict[str, str] | None:
    if provider_kind != "kiro_live_session":
        return None
    if connection_kind == "live_session":
        return {
            "id": "provider_connection_kind",
            "status": "ok",
            "message": "kiro_live_session uses live_session.",
        }
    return {
        "id": "provider_connection_kind",
        "status": "failed",
        "message": "kiro_live_session residents require live_session connection_kind.",
    }


def kiro_command_check(command: list[str]) -> dict[str, str]:
    executable = Path(command[0]).name if command else ""
    if executable in {"kiro", "kiro-cli", "kiro-cli-chat"}:
        return {"id": "kiro_command", "status": "ok", "message": "Kiro live session command is valid."}
    return {
        "id": "kiro_command",
        "status": "failed",
        "message": "kiro_live_session residents must use the kiro CLI executable.",
    }


def _configured_chat_command(command: list[str]) -> list[str]:
    if not command:
        return ["kiro", "chat"]
    if Path(command[0]).name == "kiro-cli-chat":
        return list(command)
    if len(command) >= 2 and command[1] == "chat":
        return list(command)
    return [command[0], "chat", *command[1:]]


def _without_resume_args(command: list[str]) -> list[str]:
    stripped: list[str] = []
    skip_next = False
    for part in command:
        if skip_next:
            skip_next = False
            continue
        if part == "--resume-id":
            skip_next = True
            continue
        if part.startswith("--resume-id=") or part in {"--resume", "-r", "--resume-picker"}:
            continue
        stripped.append(part)
    return stripped


def _ensure_chat_defaults(command: list[str]) -> list[str]:
    updated = list(command)
    if "--no-interactive" not in updated:
        updated.append("--no-interactive")
    if "--wrap" not in updated and not any(part.startswith("--wrap=") for part in updated):
        updated.extend(["--wrap", "never"])
    return updated


def _strip_ansi(text: str) -> str:
    return _ANSI_RE.sub("", text)


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)
