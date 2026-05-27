from __future__ import annotations

import json
import re
import subprocess
import tempfile
from pathlib import Path
from subprocess import TimeoutExpired
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from agentsassemble.live_agent_runner import ResidentAgentConfig


_SAFE_SESSION_ID_RE = re.compile(r"[A-Za-z0-9_.:-]{1,160}")

GROK_SUBPROCESS_TIMEOUT = "grok_subprocess_timeout"
GROK_SUBPROCESS_NONZERO = "grok_subprocess_nonzero"
GROK_JSON_PARSE_FAILURE = "grok_json_parse_failure"
GROK_EMPTY_TEXT = "grok_empty_text"
GROK_MISSING_SESSION_ID = "grok_missing_session_id"


class GrokResidentRuntimeError(RuntimeError):
    """Safe categorized failure from the Grok live-session adapter."""

    def __init__(self, message: str, *, category: str) -> None:
        super().__init__(message)
        self.grok_error_category = category


class GrokResidentValueError(ValueError):
    """Safe categorized validation failure from the Grok live-session adapter."""

    def __init__(self, message: str, *, category: str) -> None:
        super().__init__(message)
        self.grok_error_category = category


def grok_error_category(error: Exception) -> str:
    value = getattr(error, "grok_error_category", "")
    return value if isinstance(value, str) else ""


class GrokResidentCommandRunner:
    """Run a resident Grok CLI participant through grok --resume JSON output."""

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
        self.session_id = clean_grok_session_id(config.session_id)
        self._prompt_dir = tempfile.TemporaryDirectory(prefix="agentsassemble-grok-resident-")
        self._turn_index = 0

    def __call__(self, command: list[str], prompt: str, *, timeout_seconds: int) -> str:
        del command
        self._turn_index += 1
        prompt_path = Path(self._prompt_dir.name) / f"{_safe_stem(self.config.agent_id)}-{self._turn_index}.txt"
        prompt_path.write_text(prompt, encoding="utf-8")
        grok_command = self._build_command(prompt_path)
        try:
            completed = self.command_runner(
                grok_command,
                input="",
                text=True,
                capture_output=True,
                timeout=timeout_seconds,
                check=False,
                cwd=str(self.cwd),
            )
        except TimeoutExpired as error:
            raise GrokResidentRuntimeError(
                f"Grok live session command timed out after {timeout_seconds} seconds.",
                category=GROK_SUBPROCESS_TIMEOUT,
            ) from error
        returncode = int(getattr(completed, "returncode", 0) or 0)
        if returncode != 0:
            raise GrokResidentRuntimeError(
                f"Grok live session command failed with return code {returncode}.",
                category=GROK_SUBPROCESS_NONZERO,
            )
        payload = _parse_grok_stdout_json(getattr(completed, "stdout", ""))
        reply = _json_text(payload)
        if not reply:
            raise GrokResidentValueError(
                "Grok live session returned an empty JSON text reply.",
                category=GROK_EMPTY_TEXT,
            )
        session_id = clean_grok_session_id(payload.get("sessionId") or payload.get("session_id"))
        if session_id:
            self.session_id = session_id
        elif not self.session_id:
            raise GrokResidentValueError(
                "Grok live session did not expose a safe session id.",
                category=GROK_MISSING_SESSION_ID,
            )
        return reply

    def close(self) -> None:
        self._prompt_dir.cleanup()

    def _build_command(self, prompt_path: Path) -> list[str]:
        configured_command = list(self.config.command or ["grok"])
        executable = configured_command[0] if configured_command else "grok"
        command = [
            executable,
            "--prompt-file",
            str(prompt_path),
            "--output-format",
            "json",
            "--disable-web-search",
            "--no-subagents",
            "--verbatim",
        ]
        if self.session_id:
            command.extend(["--resume", self.session_id])
        return command


def default_grok_resident_command(provider_kind: str, connection_kind: str, command: list[str]) -> list[str]:
    if provider_kind == "grok_live_session" and connection_kind == "live_session" and not command:
        return ["grok"]
    return command


def grok_provider_connection_check(provider_kind: str, connection_kind: str) -> dict[str, str] | None:
    if provider_kind != "grok_live_session":
        return None
    if connection_kind == "live_session":
        return {
            "id": "provider_connection_kind",
            "status": "ok",
            "message": "grok_live_session uses live_session.",
        }
    return {
        "id": "provider_connection_kind",
        "status": "failed",
        "message": "grok_live_session residents require live_session connection_kind.",
    }


def grok_command_check(command: list[str]) -> dict[str, str]:
    executable = str(command[0] if command else "").strip()
    if len(command) != 1:
        return {
            "id": "grok_command",
            "status": "failed",
            "message": "grok_live_session command must contain only the grok executable.",
        }
    if Path(executable).name in {"grok", "grok.exe"}:
        return {
            "id": "grok_command",
            "status": "ok",
            "message": "grok_live_session command executable is grok.",
        }
    return {
        "id": "grok_command",
        "status": "failed",
        "message": "grok_live_session command executable must be named grok.",
    }


def clean_grok_session_id(value: object) -> str:
    text = _text(value).strip()
    return text if _SAFE_SESSION_ID_RE.fullmatch(text) else ""


def _parse_grok_stdout_json(stdout: object) -> dict[str, object]:
    text = _text(stdout).strip()
    if not text:
        raise GrokResidentValueError(
            "Grok live session returned empty JSON stdout.",
            category=GROK_JSON_PARSE_FAILURE,
        )
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as error:
        raise GrokResidentValueError(
            "Grok live session returned invalid JSON stdout.",
            category=GROK_JSON_PARSE_FAILURE,
        ) from error
    if not isinstance(payload, dict):
        raise GrokResidentValueError(
            "Grok live session JSON stdout must be an object.",
            category=GROK_JSON_PARSE_FAILURE,
        )
    return payload


def _json_text(payload: dict[str, object]) -> str:
    value = payload.get("text")
    return value.strip() if isinstance(value, str) else ""


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def _safe_stem(value: str) -> str:
    stem = re.sub(r"[^A-Za-z0-9_.-]+", "-", value.strip())
    return stem or "grok-live"
