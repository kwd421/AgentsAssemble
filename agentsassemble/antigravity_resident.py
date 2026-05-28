from __future__ import annotations

import re
import subprocess
import tempfile
from pathlib import Path
from subprocess import TimeoutExpired
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from agentsassemble.live_agent_runner import ResidentAgentConfig


_SAFE_CONVERSATION_ID_RE = re.compile(r"[A-Za-z0-9_.:-]{1,200}")
_CREATED_CONVERSATION_RE = re.compile(r"Created conversation\s+([A-Za-z0-9_.:-]{1,200})")

ANTIGRAVITY_SUBPROCESS_TIMEOUT = "antigravity_subprocess_timeout"
ANTIGRAVITY_SUBPROCESS_NONZERO = "antigravity_subprocess_nonzero"
ANTIGRAVITY_EMPTY_REPLY = "antigravity_empty_reply"
ANTIGRAVITY_MISSING_CONVERSATION_ID = "antigravity_missing_conversation_id"


class AntigravityResidentRuntimeError(RuntimeError):
    """Safe categorized failure from the Antigravity live-session adapter."""

    def __init__(self, message: str, *, category: str) -> None:
        super().__init__(message)
        self.antigravity_error_category = category


class AntigravityResidentValueError(ValueError):
    """Safe categorized validation failure from the Antigravity live-session adapter."""

    def __init__(self, message: str, *, category: str) -> None:
        super().__init__(message)
        self.antigravity_error_category = category


def antigravity_error_category(error: Exception) -> str:
    value = getattr(error, "antigravity_error_category", "")
    return value if isinstance(value, str) else ""


class AntigravityResidentCommandRunner:
    """Run a resident Antigravity CLI participant through agy --conversation."""

    def __init__(
        self,
        config: ResidentAgentConfig,
        *,
        command_runner: Any | None = None,
        cwd: Path | None = None,
    ) -> None:
        self.config = config
        self.command_runner = command_runner or subprocess.run
        self.cwd = Path(cwd or Path.cwd())
        self.session_id = clean_antigravity_conversation_id(config.session_id)
        self._log_dir = tempfile.TemporaryDirectory(prefix="agentsassemble-antigravity-resident-")
        self._turn_index = 0

    def __call__(self, command: list[str], prompt: str, *, timeout_seconds: int) -> str:
        del command
        self._turn_index += 1
        log_path = Path(self._log_dir.name) / f"turn-{self._turn_index}.log"
        agy_command = self._build_command(prompt, log_path=log_path, timeout_seconds=timeout_seconds)
        completed = self._run_antigravity_command(agy_command, timeout_seconds=timeout_seconds)
        if not self.session_id:
            self.session_id = self._conversation_id_from_log(log_path)
            if not self.session_id:
                raise AntigravityResidentValueError(
                    "Antigravity live session did not expose a safe conversation id.",
                    category=ANTIGRAVITY_MISSING_CONVERSATION_ID,
                )
        reply = _text(getattr(completed, "stdout", "")).strip()
        if not reply:
            raise AntigravityResidentValueError(
                "Antigravity live session returned an empty reply.",
                category=ANTIGRAVITY_EMPTY_REPLY,
            )
        return reply

    def close(self) -> None:
        self._log_dir.cleanup()

    def _build_command(self, prompt: str, *, log_path: Path, timeout_seconds: int) -> list[str]:
        executable = _antigravity_executable(self.config.command)
        agy_timeout = f"{max(1, int(timeout_seconds))}s"
        agy_command = [
            executable,
            "--log-file",
            str(log_path),
            "--print-timeout",
            agy_timeout,
        ]
        if self.session_id:
            agy_command.extend(["--conversation", self.session_id])
        agy_command.extend(["--print", prompt])
        return agy_command

    def _run_antigravity_command(self, command: list[str], *, timeout_seconds: int) -> Any:
        try:
            completed = self.command_runner(
                command,
                text=True,
                capture_output=True,
                timeout=timeout_seconds,
                check=False,
                cwd=str(self.cwd),
            )
        except TimeoutExpired as error:
            raise AntigravityResidentRuntimeError(
                f"Antigravity live session command timed out after {timeout_seconds} seconds.",
                category=ANTIGRAVITY_SUBPROCESS_TIMEOUT,
            ) from error
        returncode = int(getattr(completed, "returncode", 0) or 0)
        if returncode != 0:
            raise AntigravityResidentRuntimeError(
                f"Antigravity live session command failed with return code {returncode}.",
                category=ANTIGRAVITY_SUBPROCESS_NONZERO,
            )
        return completed

    def _conversation_id_from_log(self, log_path: Path) -> str:
        try:
            text = log_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ""
        for match in _CREATED_CONVERSATION_RE.finditer(text):
            conversation_id = clean_antigravity_conversation_id(match.group(1))
            if conversation_id:
                return conversation_id
        return ""


def default_antigravity_resident_command(provider_kind: str, connection_kind: str, command: list[str]) -> list[str]:
    if provider_kind == "antigravity_live_session" and connection_kind == "live_session" and not command:
        return ["agy"]
    return command


def antigravity_provider_connection_check(provider_kind: str, connection_kind: str) -> dict[str, str] | None:
    if provider_kind != "antigravity_live_session":
        return None
    if connection_kind == "live_session":
        return {
            "id": "provider_connection_kind",
            "status": "ok",
            "message": "antigravity_live_session uses live_session.",
        }
    return {
        "id": "provider_connection_kind",
        "status": "failed",
        "message": "antigravity_live_session residents require live_session connection_kind.",
    }


def antigravity_command_check(command: list[str]) -> dict[str, str]:
    executable = str(command[0] if command else "").strip()
    if len(command) != 1:
        return {
            "id": "antigravity_command",
            "status": "failed",
            "message": "antigravity_live_session command must contain only the agy or antigravity executable.",
        }
    if Path(executable).name in {"agy", "agy.exe", "antigravity", "antigravity.exe"}:
        return {
            "id": "antigravity_command",
            "status": "ok",
            "message": "antigravity_live_session command executable is agy/antigravity.",
        }
    return {
        "id": "antigravity_command",
        "status": "failed",
        "message": "antigravity_live_session command executable must be named agy or antigravity.",
    }


def clean_antigravity_conversation_id(value: object) -> str:
    text = _text(value).strip()
    if ".." in text:
        return ""
    return text if _SAFE_CONVERSATION_ID_RE.fullmatch(text) else ""


def _antigravity_executable(command: list[str]) -> str:
    configured = list(command or ["agy"])
    return configured[0] if configured else "agy"


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)
