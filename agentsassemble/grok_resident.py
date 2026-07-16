from __future__ import annotations

import json
import re
import subprocess
import tempfile
import threading
from pathlib import Path
from subprocess import TimeoutExpired
from typing import Any

from agentsassemble.providers.auth import provider_auth_error_message, provider_login_required_message
from agentsassemble.providers.resident_config import ResidentCommandConfig
from agentsassemble.room_thought import ThoughtChunker, post_room_thought


_SAFE_SESSION_ID_RE = re.compile(r"[A-Za-z0-9_.:-]{1,160}")

GROK_SUBPROCESS_TIMEOUT = "grok_subprocess_timeout"
GROK_SUBPROCESS_NONZERO = "grok_subprocess_nonzero"
GROK_JSON_PARSE_FAILURE = "grok_json_parse_failure"
GROK_EMPTY_TEXT = "grok_empty_text"
GROK_MISSING_SESSION_ID = "grok_missing_session_id"
GROK_AUTH_REQUIRED = "grok_auth_required"
GROK_LOGIN_REQUIRED_MESSAGE = provider_login_required_message("Grok", "grok login")


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
        config: ResidentCommandConfig,
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
        # Live override pushed by the runner each turn (edited from the room).
        self._override_permission_option: str | None = None

    def apply_runtime_overrides(
        self, *, permission_option: str | None = None, fast_mode: bool | None = None
    ) -> None:
        del fast_mode  # grok has no fast toggle
        self._override_permission_option = permission_option

    def __call__(self, command: list[str], prompt: str, *, timeout_seconds: int) -> str:
        del command
        self._turn_index += 1
        prompt_path = Path(self._prompt_dir.name) / f"{_safe_stem(self.config.agent_id)}-{self._turn_index}.txt"
        prompt_path.write_text(prompt, encoding="utf-8")
        if getattr(self.config, "stream_thinking", False):
            return self._streaming_call(prompt_path, timeout_seconds=timeout_seconds)
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
            login_message = grok_login_required_message(
                f"{_text(getattr(completed, 'stdout', ''))}\n{_text(getattr(completed, 'stderr', ''))}"
            )
            if login_message:
                raise GrokResidentRuntimeError(
                    login_message,
                    category=GROK_AUTH_REQUIRED,
                )
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

    def _streaming_call(self, prompt_path: Path, *, timeout_seconds: int) -> str:
        """Run grok with --output-format streaming-json and stream its reasoning
        to the room token-by-token (buffered into sentence chunks); return the
        assembled answer text as the final reply."""
        grok_command = self._build_command(prompt_path, stream=True)
        try:
            process = subprocess.Popen(  # noqa: S603 - command built from the grok executable + fixed flags
                grok_command,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                cwd=str(self.cwd),
            )
        except OSError as error:
            raise GrokResidentRuntimeError(
                f"Grok live session command failed to start: {error}.",
                category=GROK_SUBPROCESS_NONZERO,
            ) from error
        killed = {"value": False}

        def _kill_on_timeout() -> None:
            killed["value"] = True
            try:
                process.kill()
            except Exception:
                pass

        watchdog = threading.Timer(max(1.0, float(timeout_seconds)), _kill_on_timeout)
        watchdog.daemon = True
        watchdog.start()
        chunker = ThoughtChunker()
        answer_parts: list[str] = []
        try:
            for line in process.stdout or ():
                event = parse_grok_stream_line(line)
                if event is None:
                    continue
                kind = event["kind"]
                if kind == "thought":
                    for chunk in chunker.add(event["text"]):
                        post_room_thought(self.config, chunk, kind="reasoning")
                elif kind == "text":
                    answer_parts.append(event["text"])
                elif kind == "end":
                    if event["text"]:
                        self.session_id = clean_grok_session_id(event["text"]) or self.session_id
            leftover = chunker.flush()
            if leftover:
                post_room_thought(self.config, leftover, kind="reasoning")
            stderr = _text(process.stderr.read()) if process.stderr is not None else ""
            process.wait(timeout=5)
        finally:
            watchdog.cancel()
        if killed["value"]:
            raise GrokResidentRuntimeError(
                f"Grok live session command timed out after {timeout_seconds} seconds.",
                category=GROK_SUBPROCESS_TIMEOUT,
            )
        if int(process.returncode or 0) != 0:
            login_message = grok_login_required_message(stderr)
            if login_message:
                raise GrokResidentRuntimeError(login_message, category=GROK_AUTH_REQUIRED)
            raise GrokResidentRuntimeError(
                f"Grok live session command failed with return code {process.returncode}.",
                category=GROK_SUBPROCESS_NONZERO,
            )
        answer = "".join(answer_parts).strip()
        if not answer:
            raise GrokResidentValueError(
                "Grok live session returned an empty streamed reply.",
                category=GROK_EMPTY_TEXT,
            )
        return answer

    def _build_command(self, prompt_path: Path, *, stream: bool = False) -> list[str]:
        configured_command = list(self.config.command or ["grok"])
        executable = configured_command[0] if configured_command else "grok"
        command = [
            executable,
            "--prompt-file",
            str(prompt_path),
            "--output-format",
            "streaming-json" if stream else "json",
            "--disable-web-search",
            "--no-subagents",
            "--verbatim",
        ]
        model_id = str(self.config.model_id or "").strip()
        if model_id:
            command.extend(["--model", model_id])
        effort = str(self.config.effort or "").strip()
        if effort:
            command.extend(["--effort", effort])
        permission = (
            self._override_permission_option
            if self._override_permission_option is not None
            else str(getattr(self.config, "permission_option", "") or "")
        ).strip()
        if permission:
            command.extend(["--permission-mode", permission])
        if self.session_id:
            command.extend(["--resume", self.session_id])
        return command


def parse_grok_stream_line(line: str) -> dict | None:
    """Map one grok `--output-format streaming-json` line to an event.

    grok emits token deltas: {"type":"thought","data":"..."} (reasoning),
    {"type":"text","data":"..."} (answer), {"type":"end","sessionId":"..."}.
    Returns {"kind": "thought"|"text"|"end", "text": ...} or None.
    """
    text = (line or "").strip()
    if not text:
        return None
    try:
        event = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(event, dict):
        return None
    event_type = str(event.get("type") or "")
    if event_type == "thought":
        return {"kind": "thought", "text": str(event.get("data") or "")}
    if event_type == "text":
        return {"kind": "text", "text": str(event.get("data") or "")}
    if event_type == "end":
        return {"kind": "end", "text": str(event.get("sessionId") or event.get("session_id") or "")}
    return None


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


def grok_auth_check(
    command: list[str],
    *,
    command_runner: Any | None = None,
    timeout_seconds: int = 15,
) -> dict[str, str]:
    if not command:
        return {"id": "grok_auth", "status": "failed", "message": "Grok command is empty."}
    probe_command = [command[0], "models"]
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
            "id": "grok_auth",
            "status": "failed",
            "message": "Grok 로그인 상태를 확인하지 못했습니다. grok login 상태를 확인한 뒤 다시 연결 확인을 누르세요.",
        }
    except OSError as error:
        return {
            "id": "grok_auth",
            "status": "failed",
            "message": f"Grok 로그인 상태를 확인하지 못했습니다. grok 실행 실패: {error.__class__.__name__}.",
        }
    output = f"{_text(getattr(completed, 'stdout', ''))}\n{_text(getattr(completed, 'stderr', ''))}"
    if int(getattr(completed, "returncode", 1) or 0) == 0:
        return {"id": "grok_auth", "status": "ok", "message": "Grok 로그인 상태를 확인했습니다."}
    login_message = grok_login_required_message(output)
    if login_message:
        return {"id": "grok_auth", "status": "failed", "message": login_message}
    return {
        "id": "grok_auth",
        "status": "failed",
        "message": "Grok 로그인 상태를 확인하지 못했습니다. grok login 상태를 확인한 뒤 다시 연결 확인을 누르세요.",
    }


def grok_login_required_message(text: str) -> str:
    return provider_auth_error_message(text, provider_label="Grok", login_command="grok login")


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
