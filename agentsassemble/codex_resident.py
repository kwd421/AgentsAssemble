from __future__ import annotations

import re
import subprocess
import tempfile
from pathlib import Path
from subprocess import TimeoutExpired
from typing import TYPE_CHECKING, Any

from agentsassemble.codex_session_ids import extract_codex_session_id

if TYPE_CHECKING:
    from agentsassemble.live_agent_runner import ResidentAgentConfig


CODEX_EXEC_SAFETY_FLAGS = ("--sandbox", "read-only", "--ignore-rules")


def codex_exec_prefix(base_command: list[str]) -> list[str]:
    return [*base_command, "exec", *CODEX_EXEC_SAFETY_FLAGS]


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
        exec_prefix = codex_exec_prefix(base_command)
        if self.session_id:
            return [
                *exec_prefix,
                "resume",
                "--skip-git-repo-check",
                "--output-last-message",
                str(output_path),
                self.session_id,
                "-",
            ]
        return [
            *exec_prefix,
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


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def _safe_stem(value: str) -> str:
    stem = re.sub(r"[^A-Za-z0-9_.-]+", "-", value.strip())
    return stem or "codex-live"
