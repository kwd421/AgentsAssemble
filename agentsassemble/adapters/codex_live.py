from __future__ import annotations

from pathlib import Path
from subprocess import TimeoutExpired
from typing import Any

from agentsassemble.adapters.codex import CodexAdapter
from agentsassemble.codex_resident import codex_exec_prefix
from agentsassemble.models import Role


class CodexLiveSessionAdapter(CodexAdapter):
    name = "codex_live_session"

    def start_session(self, role: Role, meeting_context: dict[str, Any]) -> dict[str, Any]:
        session_ids = meeting_context.get("session_ids") if isinstance(meeting_context.get("session_ids"), dict) else {}
        return {
            "adapter": self.name,
            "role_id": role.id,
            "session_id": session_ids.get(role.id),
            "status": "ready",
            "context_dir": f"roles/{role.id}",
            "meeting_dir": meeting_context.get("meeting_dir"),
            "meeting_id": meeting_context.get("meeting_id"),
        }

    def _invoke_codex(self, session: dict[str, Any], step: str, prompt: str, use_search: bool) -> dict[str, Any]:
        meeting_dir = session.get("meeting_dir")
        if not meeting_dir:
            raise ValueError("CodexLiveSessionAdapter requires meeting_dir in session metadata.")

        meeting_path = Path(meeting_dir)
        output_path = meeting_path / "roles" / session["role_id"] / f"codex-live-{step}-last-message.txt"
        output_path.parent.mkdir(parents=True, exist_ok=True)
        command = ["codex"]
        if self.search_enabled and use_search:
            command.append("--search")
        exec_prefix = codex_exec_prefix(command)
        session_id = session.get("session_id")
        session_mode = "resumed" if session_id else "started"
        if session_id:
            command = [
                *exec_prefix,
                "resume",
                "--skip-git-repo-check",
                "--output-last-message",
                str(output_path),
                str(session_id),
                "-",
            ]
        else:
            command = [
                *exec_prefix,
                "--skip-git-repo-check",
                "--cd",
                str(meeting_path),
                "--output-last-message",
                str(output_path),
                "-",
            ]
        try:
            completed = self.command_runner(
                command,
                input=prompt,
                text=True,
                capture_output=True,
                timeout=self.timeout_seconds,
                check=False,
                cwd=str(meeting_path),
            )
        except TimeoutExpired as error:
            return {
                "text": f"Codex live session call timed out after {self.timeout_seconds} seconds during {step}.",
                "metadata": {
                    "command": command,
                    "returncode": 124,
                    "stdout": self._text(error.stdout),
                    "stderr": self._text(error.stderr),
                    "session_id": session_id,
                    "session_mode": session_mode,
                    "output_last_message": str(output_path),
                    "timeout_seconds": self.timeout_seconds,
                    "timed_out": True,
                },
            }
        text = output_path.read_text(encoding="utf-8") if output_path.exists() else completed.stdout
        extracted_session_id = self._extract_session_id(completed.stdout + "\n" + completed.stderr)
        effective_session_id = extracted_session_id or session_id
        if effective_session_id:
            session["session_id"] = effective_session_id
        return {
            "text": text,
            "metadata": {
                "command": command,
                "returncode": completed.returncode,
                "stdout": self._text(completed.stdout),
                "stderr": self._text(completed.stderr),
                "session_id": effective_session_id,
                "session_mode": session_mode,
                "output_last_message": str(output_path),
                "timed_out": False,
            },
        }
