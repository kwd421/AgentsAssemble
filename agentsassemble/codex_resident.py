from __future__ import annotations

import re
import subprocess
import tempfile
from pathlib import Path
from subprocess import TimeoutExpired
from typing import TYPE_CHECKING, Any

from agentsassemble.codex_session_ids import extract_codex_session_id
from agentsassemble.provider_auth import provider_auth_error_message, provider_login_required_message
from agentsassemble.sandbox_launcher import CODEX_EXEC_SAFETY_FLAGS, sandbox_launcher_for

if TYPE_CHECKING:
    from agentsassemble.live_agent_runner import ResidentAgentConfig


CODEX_AUTH_REQUIRED = "codex_auth_required"
CODEX_LOGIN_REQUIRED_MESSAGE = provider_login_required_message("Codex", "codex login")


def codex_exec_prefix(base_command: list[str], *, sandbox: str = "read-only") -> list[str]:
    return sandbox_launcher_for("codex_live_session", "live_session", sandbox=sandbox).command(base_command)


class CodexResidentCommandRunner:
    """Run a resident Codex CLI participant through codex exec/resume."""

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
        self.session_id = str(config.session_id or "").strip()
        self._output_dir = tempfile.TemporaryDirectory(prefix="agentsassemble-codex-resident-")

    def __call__(self, command: list[str], prompt: str, *, timeout_seconds: int) -> str:
        del command
        output_path = Path(self._output_dir.name) / f"{_safe_stem(self.config.agent_id)}-last-message.txt"
        codex_command = self._build_command(output_path)
        try:
            completed = self.command_runner(
                codex_command,
                input=prompt,
                text=True,
                capture_output=True,
                timeout=timeout_seconds,
                check=False,
                cwd=str(self.cwd),
            )
        except TimeoutExpired as error:
            raise RuntimeError(
                f"Codex live session command timed out after {timeout_seconds} seconds."
            ) from error
        returncode = int(getattr(completed, "returncode", 0) or 0)
        if returncode != 0:
            login_message = codex_login_required_message(
                f"{_text(getattr(completed, 'stdout', ''))}\n{_text(getattr(completed, 'stderr', ''))}"
            )
            if login_message:
                raise RuntimeError(login_message)
            raise RuntimeError(f"Codex live session command failed with return code {returncode}.")
        stdout = _text(getattr(completed, "stdout", ""))
        stderr = _text(getattr(completed, "stderr", ""))
        extracted_session_id = extract_codex_session_id(stdout + "\n" + stderr)
        if extracted_session_id:
            self.session_id = extracted_session_id
        message = output_path.read_text(encoding="utf-8") if output_path.exists() else stdout
        clean_message = message.strip()
        if not clean_message:
            raise ValueError("Codex live session returned an empty reply.")
        return clean_message

    def close(self) -> None:
        self._output_dir.cleanup()

    def _build_command(self, output_path: Path) -> list[str]:
        configured_command = list(self.config.command or ["codex"])
        base_command = [configured_command[0]]
        exec_prefix = codex_exec_prefix(base_command, sandbox=str(getattr(self.config, "codex_sandbox", "") or "read-only"))
        tuning_args = _codex_tuning_args(self.config.model_id, self.config.effort)
        if self.session_id:
            return [
                *exec_prefix,
                *tuning_args,
                "resume",
                "--skip-git-repo-check",
                "--output-last-message",
                str(output_path),
                self.session_id,
                "-",
            ]
        return [
            *exec_prefix,
            *tuning_args,
            "--skip-git-repo-check",
            "--cd",
            str(self.cwd),
            "--output-last-message",
            str(output_path),
            "-",
        ]


def default_codex_resident_command(provider_kind: str, connection_kind: str, command: list[str]) -> list[str]:
    if provider_kind == "codex_live_session" and connection_kind == "live_session" and not command:
        return ["codex"]
    return command


def _codex_tuning_args(model_id: str, effort: str) -> list[str]:
    args = _codex_model_args(model_id)
    clean_effort = str(effort or "").strip()
    if clean_effort:
        args.extend(["-c", f'model_reasoning_effort="{clean_effort}"'])
    return args


def _codex_model_args(model_id: str) -> list[str]:
    clean_model_id = str(model_id or "").strip()
    if not clean_model_id:
        return []
    return ["--model", clean_model_id]


def codex_provider_connection_check(provider_kind: str, connection_kind: str) -> dict[str, str] | None:
    if provider_kind != "codex_live_session":
        return None
    if connection_kind == "live_session":
        return {
            "id": "provider_connection_kind",
            "status": "ok",
            "message": "codex_live_session uses live_session.",
        }
    return {
        "id": "provider_connection_kind",
        "status": "failed",
        "message": "codex_live_session residents require live_session connection_kind.",
    }


def codex_auth_check(
    command: list[str],
    *,
    command_runner: Any | None = None,
    timeout_seconds: int = 10,
) -> dict[str, str]:
    if not command:
        return {"id": "codex_auth", "status": "failed", "message": "Codex command is empty."}
    probe_command = [command[0], "login", "status"]
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
            "id": "codex_auth",
            "status": "failed",
            "message": "Codex 로그인 상태를 확인하지 못했습니다. codex login 상태를 확인한 뒤 다시 연결 확인을 누르세요.",
        }
    except OSError as error:
        return {
            "id": "codex_auth",
            "status": "failed",
            "message": f"Codex 로그인 상태를 확인하지 못했습니다. codex 실행 실패: {error.__class__.__name__}.",
        }
    output = f"{_text(getattr(completed, 'stdout', ''))}\n{_text(getattr(completed, 'stderr', ''))}"
    if int(getattr(completed, "returncode", 1) or 0) == 0:
        return {"id": "codex_auth", "status": "ok", "message": "Codex 로그인 상태를 확인했습니다."}
    login_message = codex_login_required_message(output)
    if login_message:
        return {"id": "codex_auth", "status": "failed", "message": login_message}
    return {
        "id": "codex_auth",
        "status": "failed",
        "message": "Codex 로그인 상태를 확인하지 못했습니다. codex login 상태를 확인한 뒤 다시 연결 확인을 누르세요.",
    }


def codex_login_required_message(text: str) -> str:
    return provider_auth_error_message(text, provider_label="Codex", login_command="codex login")


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def _safe_stem(value: str) -> str:
    stem = re.sub(r"[^A-Za-z0-9_.-]+", "-", value.strip())
    return stem or "codex-live"
