from __future__ import annotations

import json
import re
import subprocess
import tempfile
from pathlib import Path
from subprocess import TimeoutExpired
from typing import TYPE_CHECKING, Any

from agentsassemble.provider_auth import provider_auth_error_message, provider_login_required_message

if TYPE_CHECKING:
    from agentsassemble.live_agent_runner import ResidentAgentConfig


_SAFE_CONVERSATION_ID_RE = re.compile(r"[A-Za-z0-9_.:-]{1,200}")
_CREATED_CONVERSATION_RE = re.compile(r"Created conversation\s+([A-Za-z0-9_.:-]{1,200})")
_ACTION_FRAGMENT_RE = re.compile(r'\{\s*"action"\s*:', re.IGNORECASE)

ANTIGRAVITY_SUBPROCESS_TIMEOUT = "antigravity_subprocess_timeout"
ANTIGRAVITY_SUBPROCESS_NONZERO = "antigravity_subprocess_nonzero"
ANTIGRAVITY_EMPTY_REPLY = "antigravity_empty_reply"
ANTIGRAVITY_MISSING_CONVERSATION_ID = "antigravity_missing_conversation_id"
ANTIGRAVITY_BACKEND_ERROR = "antigravity_backend_error"
ANTIGRAVITY_AUTH_REQUIRED = "antigravity_auth_required"
ANTIGRAVITY_LOGIN_REQUIRED_MESSAGE = provider_login_required_message("Antigravity", "agy")


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
        self._raise_backend_error_from_log(log_path)
        reply = _visible_antigravity_reply(getattr(completed, "stdout", ""))
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
            login_message = _antigravity_login_required_message(
                f"{_text(getattr(completed, 'stdout', ''))}\n{_text(getattr(completed, 'stderr', ''))}"
            )
            if login_message:
                raise AntigravityResidentRuntimeError(
                    login_message,
                    category=ANTIGRAVITY_AUTH_REQUIRED,
                )
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

    def _raise_backend_error_from_log(self, log_path: Path) -> None:
        try:
            text = log_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return
        if "RESOURCE_EXHAUSTED" not in text and "agent executor error" not in text:
            return
        raise AntigravityResidentRuntimeError(
            "Antigravity live session backend reported an execution or quota error.",
            category=ANTIGRAVITY_BACKEND_ERROR,
        )


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


def antigravity_auth_check(
    command: list[str],
    *,
    command_runner: Any | None = None,
    timeout_seconds: int = 20,
) -> dict[str, str]:
    executable = _antigravity_executable(command)
    runner = command_runner or subprocess.run
    with tempfile.TemporaryDirectory(prefix="agentsassemble-antigravity-auth-") as temp_dir:
        work_dir = Path(temp_dir)
        log_path = work_dir / "auth-check.log"
        probe_command = [
            executable,
            "--log-file",
            str(log_path),
            "--print-timeout",
            "8s",
            "--print",
            "Reply with READY only.",
        ]
        try:
            completed = runner(
                probe_command,
                text=True,
                capture_output=True,
                timeout=timeout_seconds,
                check=False,
                cwd=str(work_dir),
            )
        except TimeoutExpired:
            return {
                "id": "antigravity_auth",
                "status": "failed",
                "message": (
                    f"Antigravity 로그인 상태를 확인하지 못했습니다. agy가 {timeout_seconds}초 안에 "
                    "응답하지 않았습니다. 터미널에서 agy 로그인 상태를 확인한 뒤 다시 연결 확인을 누르세요."
                ),
            }
        except OSError as error:
            return {
                "id": "antigravity_auth",
                "status": "failed",
                "message": f"Antigravity 로그인 상태를 확인하지 못했습니다. agy 실행 실패: {error.__class__.__name__}.",
            }
        returncode = int(getattr(completed, "returncode", 0) or 0)
        if returncode == 0:
            return {
                "id": "antigravity_auth",
                "status": "ok",
                "message": "Antigravity 로그인 상태를 확인했습니다.",
            }
        combined_output = _combined_antigravity_probe_output(completed, log_path)
        login_message = _antigravity_login_required_message(combined_output)
        if login_message:
            return {"id": "antigravity_auth", "status": "failed", "message": login_message}
        return {
            "id": "antigravity_auth",
            "status": "failed",
            "message": (
                f"Antigravity 로그인 상태를 확인하지 못했습니다. agy가 종료 코드 {returncode}로 실패했습니다. "
                "터미널에서 agy 로그인 상태를 확인한 뒤 다시 연결 확인을 누르세요."
            ),
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


def _visible_antigravity_reply(stdout: object) -> str:
    text = _text(stdout).strip()
    if not text:
        return ""
    json_spans = _json_object_spans_in_text(text)
    if json_spans:
        payload, _start, end = json_spans[-1]
        trailing = text[end:].strip()
        if _looks_like_antigravity_status(trailing) or _ACTION_FRAGMENT_RE.search(trailing):
            return ""
        if trailing:
            return _latest_nonempty_line(trailing)
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    candidate = _latest_nonempty_line(text)
    if _looks_like_antigravity_status(candidate) or _ACTION_FRAGMENT_RE.search(candidate):
        return ""
    return candidate


def _json_object_spans_in_text(text: str) -> list[tuple[dict[str, object], int, int]]:
    decoder = json.JSONDecoder()
    spans: list[tuple[dict[str, object], int, int]] = []
    for index, char in enumerate(text):
        if char != "{":
            continue
        try:
            payload, _end = decoder.raw_decode(text[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            spans.append((payload, index, index + _end))
    return spans


def _looks_like_antigravity_status(text: str) -> bool:
    return "is present and ready for AgentsAssemble" in text or "Connection active at cursor" in text


def _latest_nonempty_line(text: str) -> str:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return lines[-1] if lines else ""


def _combined_antigravity_probe_output(completed: Any, log_path: Path) -> str:
    parts = [_text(getattr(completed, "stdout", "")), _text(getattr(completed, "stderr", ""))]
    try:
        parts.append(log_path.read_text(encoding="utf-8", errors="replace"))
    except OSError:
        pass
    return "\n".join(part for part in parts if part)


def _antigravity_login_required_message(text: str) -> str:
    return provider_auth_error_message(text, provider_label="Antigravity", login_command="agy")
