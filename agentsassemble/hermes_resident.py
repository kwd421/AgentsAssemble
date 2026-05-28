from __future__ import annotations

import re
import subprocess
from pathlib import Path
from subprocess import TimeoutExpired
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from agentsassemble.live_agent_runner import ResidentAgentConfig


_SAFE_SESSION_ID_RE = re.compile(r"[A-Za-z0-9_.:-]{1,200}")
_SESSION_ID_RE = re.compile(r"session_id:\s*([A-Za-z0-9_.:-]{1,200})")

HERMES_SUBPROCESS_TIMEOUT = "hermes_subprocess_timeout"
HERMES_SUBPROCESS_NONZERO = "hermes_subprocess_nonzero"
HERMES_EMPTY_REPLY = "hermes_empty_reply"
HERMES_MISSING_SESSION_ID = "hermes_missing_session_id"


class HermesResidentRuntimeError(RuntimeError):
    """Safe categorized failure from the Hermes live-session adapter."""

    def __init__(self, message: str, *, category: str) -> None:
        super().__init__(message)
        self.hermes_error_category = category


class HermesResidentValueError(ValueError):
    """Safe categorized validation failure from the Hermes live-session adapter."""

    def __init__(self, message: str, *, category: str) -> None:
        super().__init__(message)
        self.hermes_error_category = category


def hermes_error_category(error: Exception) -> str:
    value = getattr(error, "hermes_error_category", "")
    return value if isinstance(value, str) else ""


class HermesResidentCommandRunner:
    """Run a resident Hermes CLI participant through hermes chat --resume."""

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
        self.session_id = clean_hermes_session_id(config.session_id)
        self.source = _safe_source(config.meeting_id, config.agent_id)

    def __call__(self, command: list[str], prompt: str, *, timeout_seconds: int) -> str:
        del command
        hermes_command = self._build_command(prompt)
        completed = self._run_hermes_command(hermes_command, timeout_seconds=timeout_seconds)
        session_id = _session_id_from_stderr(getattr(completed, "stderr", ""))
        if session_id:
            self.session_id = session_id
        elif not self.session_id:
            raise HermesResidentValueError(
                "Hermes live session did not expose a safe session id.",
                category=HERMES_MISSING_SESSION_ID,
            )
        reply = _visible_hermes_reply(getattr(completed, "stdout", ""))
        if not reply:
            raise HermesResidentValueError(
                "Hermes live session returned an empty reply.",
                category=HERMES_EMPTY_REPLY,
            )
        return reply

    def _build_command(self, prompt: str) -> list[str]:
        command = [
            _hermes_executable(self.config.command),
            "chat",
            "--query",
            prompt,
            "--quiet",
            "--ignore-user-config",
            "--ignore-rules",
            "--source",
            self.source,
            "--max-turns",
            "1",
            "--pass-session-id",
        ]
        if self.session_id:
            command.extend(["--resume", self.session_id])
        return command

    def _run_hermes_command(self, command: list[str], *, timeout_seconds: int) -> Any:
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
            raise HermesResidentRuntimeError(
                f"Hermes live session command timed out after {timeout_seconds} seconds.",
                category=HERMES_SUBPROCESS_TIMEOUT,
            ) from error
        returncode = int(getattr(completed, "returncode", 0) or 0)
        if returncode != 0:
            raise HermesResidentRuntimeError(
                f"Hermes live session command failed with return code {returncode}.",
                category=HERMES_SUBPROCESS_NONZERO,
            )
        return completed


def default_hermes_resident_command(provider_kind: str, connection_kind: str, command: list[str]) -> list[str]:
    if provider_kind == "hermes_live_session" and connection_kind == "live_session" and not command:
        return ["hermes"]
    return command


def hermes_provider_connection_check(provider_kind: str, connection_kind: str) -> dict[str, str] | None:
    if provider_kind != "hermes_live_session":
        return None
    if connection_kind == "live_session":
        return {
            "id": "provider_connection_kind",
            "status": "ok",
            "message": "hermes_live_session uses live_session.",
        }
    return {
        "id": "provider_connection_kind",
        "status": "failed",
        "message": "hermes_live_session residents require live_session connection_kind.",
    }


def hermes_command_check(command: list[str]) -> dict[str, str]:
    executable = str(command[0] if command else "").strip()
    if len(command) != 1:
        return {
            "id": "hermes_command",
            "status": "failed",
            "message": "hermes_live_session command must contain only the hermes executable.",
        }
    if Path(executable).name in {"hermes", "hermes.exe"}:
        return {
            "id": "hermes_command",
            "status": "ok",
            "message": "hermes_live_session command executable is hermes.",
        }
    return {
        "id": "hermes_command",
        "status": "failed",
        "message": "hermes_live_session command executable must be named hermes.",
    }


def clean_hermes_session_id(value: object) -> str:
    text = _text(value).strip()
    if ".." in text:
        return ""
    return text if _SAFE_SESSION_ID_RE.fullmatch(text) else ""


def _session_id_from_stderr(stderr: object) -> str:
    for match in _SESSION_ID_RE.finditer(_text(stderr)):
        session_id = clean_hermes_session_id(match.group(1))
        if session_id:
            return session_id
    return ""


def _hermes_executable(command: list[str]) -> str:
    configured = list(command or ["hermes"])
    return configured[0] if configured else "hermes"


def _safe_source(meeting_id: str, agent_id: str) -> str:
    raw = f"agentsassemble-{meeting_id or 'room'}-{agent_id or 'hermes'}"
    source = re.sub(r"[^A-Za-z0-9_.:-]+", "-", raw.strip())
    return source[:100] or "agentsassemble-hermes"


def _visible_hermes_reply(stdout: object) -> str:
    text = _text(stdout).strip()
    if not text:
        return ""
    summary_marker = "Requesting summary..."
    marker_index = text.find(summary_marker)
    if marker_index >= 0 and marker_index < 500:
        prefix = text[:marker_index]
        if "Reached maximum iterations" in prefix or "Resumed session" in prefix:
            text = text[marker_index + len(summary_marker) :].strip()
    text = re.sub(r"^\s*\S?\s*Resumed session [^\n]*?\)\s*", "", text).strip()
    text = re.sub(r"^\([^)]* timed out after [^)]*\)\s*", "", text).strip()
    text = _strip_dsml_tool_calls(text).strip()
    return text


def _strip_dsml_tool_calls(text: str) -> str:
    start_marker = "<｜｜DSML｜｜tool_calls>"
    end_marker = "</｜｜DSML｜｜tool_calls>"
    cleaned = text
    while start_marker in cleaned:
        start = cleaned.find(start_marker)
        end = cleaned.find(end_marker, start)
        if end >= 0:
            cleaned = cleaned[:start] + cleaned[end + len(end_marker) :]
            continue
        line_end = cleaned.find("\n", start)
        if line_end >= 0:
            cleaned = cleaned[:start] + cleaned[line_end + 1 :]
            continue
        cleaned = cleaned[:start]
    return cleaned


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)
