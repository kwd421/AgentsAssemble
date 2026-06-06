from __future__ import annotations

import re
import json
import subprocess
import tempfile
from pathlib import Path
from subprocess import TimeoutExpired
from typing import TYPE_CHECKING, Any

from agentsassemble.provider_auth import provider_auth_error_message, provider_login_required_message

if TYPE_CHECKING:
    from agentsassemble.live_agent_runner import ResidentAgentConfig


_SAFE_CHAT_ID_RE = re.compile(r"[A-Za-z0-9_.:-]{1,200}")

CURSOR_SUBPROCESS_TIMEOUT = "cursor_subprocess_timeout"
CURSOR_SUBPROCESS_NONZERO = "cursor_subprocess_nonzero"
CURSOR_EMPTY_TEXT = "cursor_empty_text"
CURSOR_INVALID_CHAT_ID = "cursor_invalid_chat_id"
CURSOR_AUTH_REQUIRED = "cursor_auth_required"
CURSOR_LOGIN_REQUIRED_MESSAGE = provider_login_required_message("Cursor", "cursor-agent login")
CURSOR_TERMINAL_SESSION_SUPERSEDED_MESSAGE = (
    "cursor-agent terminal_session residents are superseded by cursor-agent-live-session; "
    "use provider_kind cursor_live_session with live_session connection_kind."
)
CURSOR_GENERIC_RESIDENT_UNSUPPORTED_MESSAGE = (
    "provider_kind cursor is a planned generic provider and is not a runnable resident for "
    "terminal_session or live_session; use cursor-agent-live-session with provider_kind "
    "cursor_live_session and live_session connection_kind."
)


class CursorResidentRuntimeError(RuntimeError):
    """Safe categorized failure from the Cursor live-session adapter."""

    def __init__(self, message: str, *, category: str) -> None:
        super().__init__(message)
        self.cursor_error_category = category


class CursorResidentValueError(ValueError):
    """Safe categorized validation failure from the Cursor live-session adapter."""

    def __init__(self, message: str, *, category: str) -> None:
        super().__init__(message)
        self.cursor_error_category = category


def cursor_error_category(error: Exception) -> str:
    value = getattr(error, "cursor_error_category", "")
    return value if isinstance(value, str) else ""


class CursorResidentCommandRunner:
    """Run a resident Cursor Agent participant through cursor-agent --resume."""

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
        self.session_id = clean_cursor_chat_id(config.session_id)
        self._workspace_dir: tempfile.TemporaryDirectory[str] | None = None
        self._workspace_path: Path | None = None
        configured_workspace = str(getattr(config, "workspace_path", "") or "").strip()
        if configured_workspace:
            self._workspace_path = Path(configured_workspace).expanduser()
        else:
            self._workspace_dir = tempfile.TemporaryDirectory(prefix="agentsassemble-cursor-resident-workspace-")

    @property
    def workspace_dir(self) -> Path:
        if self._workspace_path is not None:
            return self._workspace_path
        if self._workspace_dir is None:
            raise RuntimeError("Cursor live session workspace is not available.")
        return Path(self._workspace_dir.name)

    def __call__(self, command: list[str], prompt: str, *, timeout_seconds: int) -> str:
        del command
        if not self.session_id:
            self.session_id = self._create_chat(timeout_seconds=timeout_seconds)
        cursor_command = self._build_resume_command()
        completed = self._run_cursor_command(cursor_command, prompt, timeout_seconds=timeout_seconds)
        reply = _text(getattr(completed, "stdout", "")).strip()
        if not reply:
            raise CursorResidentValueError(
                "Cursor live session returned an empty reply.",
                category=CURSOR_EMPTY_TEXT,
            )
        return reply

    def close(self) -> None:
        if self._workspace_dir is not None:
            self._workspace_dir.cleanup()

    def _create_chat(self, *, timeout_seconds: int) -> str:
        create_command = [self._cursor_executable(), "create-chat"]
        completed = self._run_cursor_command(create_command, "", timeout_seconds=timeout_seconds)
        chat_id = clean_cursor_chat_id(_first_nonempty_line(getattr(completed, "stdout", "")))
        if not chat_id:
            raise CursorResidentValueError(
                "Cursor live session did not expose a safe Cursor chat id.",
                category=CURSOR_INVALID_CHAT_ID,
            )
        return chat_id

    def _build_resume_command(self) -> list[str]:
        command = [
            self._cursor_executable(),
            "--resume",
            self.session_id,
            "--print",
            "--mode",
            "ask",
            "--sandbox",
            "enabled",
            "--trust",
            "--workspace",
            str(self.workspace_dir),
        ]
        model_id = str(self.config.model_id or "").strip()
        if model_id:
            command[3:3] = ["--model", model_id]
        return command

    def _cursor_executable(self) -> str:
        configured_command = list(self.config.command or ["cursor-agent"])
        return configured_command[0] if configured_command else "cursor-agent"

    def _run_cursor_command(self, command: list[str], prompt: str, *, timeout_seconds: int) -> Any:
        try:
            completed = self.command_runner(
                command,
                input=prompt,
                text=True,
                capture_output=True,
                timeout=timeout_seconds,
                check=False,
                cwd=str(self.cwd),
            )
        except TimeoutExpired as error:
            raise CursorResidentRuntimeError(
                f"Cursor live session command timed out after {timeout_seconds} seconds.",
                category=CURSOR_SUBPROCESS_TIMEOUT,
            ) from error
        returncode = int(getattr(completed, "returncode", 0) or 0)
        if returncode != 0:
            login_message = cursor_login_required_message(
                f"{_text(getattr(completed, 'stdout', ''))}\n{_text(getattr(completed, 'stderr', ''))}"
            )
            if login_message:
                raise CursorResidentRuntimeError(
                    login_message,
                    category=CURSOR_AUTH_REQUIRED,
                )
            raise CursorResidentRuntimeError(
                f"Cursor live session command failed with return code {returncode}.",
                category=CURSOR_SUBPROCESS_NONZERO,
            )
        return completed


def default_cursor_resident_command(provider_kind: str, connection_kind: str, command: list[str]) -> list[str]:
    if provider_kind == "cursor_live_session" and connection_kind == "live_session" and not command:
        return ["cursor-agent"]
    return command


def cursor_provider_connection_check(provider_kind: str, connection_kind: str) -> dict[str, str] | None:
    generic_guard = cursor_generic_resident_guard_check(provider_kind, connection_kind)
    if generic_guard is not None:
        return generic_guard
    if provider_kind != "cursor_live_session":
        return None
    if connection_kind == "live_session":
        return {
            "id": "provider_connection_kind",
            "status": "ok",
            "message": "cursor_live_session uses live_session.",
        }
    return {
        "id": "provider_connection_kind",
        "status": "failed",
        "message": "cursor_live_session residents require live_session connection_kind.",
    }


def cursor_generic_resident_guard_check(provider_kind: str, connection_kind: str) -> dict[str, str] | None:
    if provider_kind == "cursor" and connection_kind in {"terminal_session", "live_session"}:
        return {
            "id": "provider_connection_kind",
            "status": "failed",
            "message": CURSOR_GENERIC_RESIDENT_UNSUPPORTED_MESSAGE,
        }
    return None


def cursor_generic_resident_guard_error(provider_kind: str, connection_kind: str) -> str:
    check = cursor_generic_resident_guard_check(provider_kind, connection_kind)
    if check is None:
        return ""
    return check["message"]


def cursor_command_check(command: list[str]) -> dict[str, str]:
    executable = str(command[0] if command else "").strip()
    if len(command) != 1:
        return {
            "id": "cursor_command",
            "status": "failed",
            "message": "cursor_live_session command must contain only the cursor-agent executable.",
        }
    if _is_cursor_agent_executable(executable):
        return {
            "id": "cursor_command",
            "status": "ok",
            "message": "cursor_live_session command executable is cursor-agent.",
        }
    return {
        "id": "cursor_command",
        "status": "failed",
        "message": "cursor_live_session command executable must be named cursor-agent.",
    }


def cursor_auth_check(
    command: list[str],
    *,
    command_runner: Any | None = None,
    timeout_seconds: int = 10,
) -> dict[str, str]:
    if not command:
        return {"id": "cursor_auth", "status": "failed", "message": "Cursor command is empty."}
    probe_command = [command[0], "status", "--format", "json"]
    runner = command_runner or subprocess.run
    try:
        completed = runner(
            probe_command,
            text=True,
            capture_output=True,
            timeout=timeout_seconds,
            check=False,
        )
    except TimeoutExpired:
        return {
            "id": "cursor_auth",
            "status": "failed",
            "message": "Cursor 로그인 상태를 확인하지 못했습니다. cursor-agent login 상태를 확인한 뒤 다시 연결 확인을 누르세요.",
        }
    except OSError as error:
        return {
            "id": "cursor_auth",
            "status": "failed",
            "message": f"Cursor 로그인 상태를 확인하지 못했습니다. cursor-agent 실행 실패: {error.__class__.__name__}.",
        }
    output = f"{_text(getattr(completed, 'stdout', ''))}\n{_text(getattr(completed, 'stderr', ''))}"
    if int(getattr(completed, "returncode", 1) or 0) == 0 and _cursor_status_is_authenticated(output):
        return {"id": "cursor_auth", "status": "ok", "message": "Cursor 로그인 상태를 확인했습니다."}
    login_message = cursor_login_required_message(output)
    if login_message:
        return {"id": "cursor_auth", "status": "failed", "message": login_message}
    return {
        "id": "cursor_auth",
        "status": "failed",
        "message": "Cursor 로그인이 필요합니다. 터미널에서 cursor-agent login을 실행해 로그인한 뒤 다시 연결 확인을 누르세요.",
    }


def cursor_login_required_message(text: str) -> str:
    return provider_auth_error_message(text, provider_label="Cursor", login_command="cursor-agent login")


def cursor_terminal_session_superseded_check(
    provider_kind: str,
    connection_kind: str,
    command: list[str],
) -> dict[str, str] | None:
    if (
        provider_kind == "cursor"
        and connection_kind == "terminal_session"
        and command
        and _is_cursor_agent_executable(str(command[0]))
    ):
        return {
            "id": "cursor_terminal_session",
            "status": "failed",
            "message": CURSOR_TERMINAL_SESSION_SUPERSEDED_MESSAGE,
        }
    return None


def cursor_terminal_session_superseded_error(
    provider_kind: str,
    connection_kind: str,
    command: list[str],
) -> str:
    check = cursor_terminal_session_superseded_check(provider_kind, connection_kind, command)
    if check is None:
        return ""
    return check["message"]


def _is_cursor_agent_executable(executable: str) -> bool:
    return Path(executable).name in {"cursor-agent", "cursor-agent.exe"}


def clean_cursor_chat_id(value: object) -> str:
    text = _text(value).strip()
    if ".." in text:
        return ""
    return text if _SAFE_CHAT_ID_RE.fullmatch(text) else ""


def _cursor_status_is_authenticated(text: str) -> bool:
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return "authenticated" in text.casefold()
    if not isinstance(payload, dict):
        return False
    return bool(payload.get("isAuthenticated")) or str(payload.get("status") or "").casefold() == "authenticated"


def _first_nonempty_line(value: object) -> str:
    for line in _text(value).splitlines():
        stripped = line.strip()
        if stripped:
            return stripped
    return ""


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)
