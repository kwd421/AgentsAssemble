from __future__ import annotations

import json
import re
import subprocess
import tempfile
import threading
import urllib.request
from pathlib import Path
from subprocess import TimeoutExpired
from typing import TYPE_CHECKING, Any

from agentsassemble.codex_session_ids import extract_codex_session_id
from agentsassemble.codex_stream import parse_codex_stream_line
from agentsassemble.providers.auth import provider_auth_error_message, provider_login_required_message
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
        # Live overrides pushed by the runner each turn (edited from the room).
        self._override_permission_option: str | None = None
        self._override_fast_mode: bool | None = None

    def apply_runtime_overrides(
        self, *, permission_option: str | None = None, fast_mode: bool | None = None
    ) -> None:
        self._override_permission_option = permission_option
        self._override_fast_mode = fast_mode

    def __call__(self, command: list[str], prompt: str, *, timeout_seconds: int) -> str:
        del command
        if getattr(self.config, "stream_thinking", False):
            return self._streaming_call(prompt, timeout_seconds=timeout_seconds)
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

    def _streaming_call(self, prompt: str, *, timeout_seconds: int) -> str:
        """Run codex with --json and stream its reasoning/tool runs to the room as
        it works; return the final assistant message as the canonical reply.

        Intermediate agent_message chunks + command runs + reasoning are posted
        live (operator-only) so the human watches the thinking flow. The last
        agent_message is held back and returned so the normal reply path posts it
        once — no duplicate."""
        output_path = Path(self._output_dir.name) / f"{_safe_stem(self.config.agent_id)}-last-message.txt"
        codex_command = self._build_command(output_path, json_stream=True)
        try:
            process = subprocess.Popen(  # noqa: S603 - command is built from a fixed codex prefix
                codex_command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                cwd=str(self.cwd),
            )
        except OSError as error:
            raise RuntimeError(f"Codex live session command failed to start: {error}.") from error

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
        pending_message: str | None = None
        try:
            try:
                if process.stdin is not None:
                    process.stdin.write(prompt)
                    process.stdin.close()
            except (BrokenPipeError, OSError):
                pass
            for line in process.stdout or ():
                event = parse_codex_stream_line(line)
                if event is None:
                    continue
                kind = event["kind"]
                if kind == "thread":
                    if event["text"]:
                        self.session_id = event["text"]
                elif kind == "message":
                    # Post the previous chunk as a live thought; hold the newest
                    # so the final one becomes the canonical reply (no dup).
                    if pending_message is not None:
                        self._post_thought(pending_message, kind="message")
                    pending_message = event["text"]
                elif kind == "command":
                    self._post_thought(f"🔧 {event['text']}", kind="command")
                elif kind == "reasoning":
                    self._post_thought(event["text"], kind="reasoning")
            stderr = _text(process.stderr.read()) if process.stderr is not None else ""
            process.wait(timeout=5)
        finally:
            watchdog.cancel()
        if killed["value"]:
            raise RuntimeError(f"Codex live session command timed out after {timeout_seconds} seconds.")
        returncode = int(process.returncode or 0)
        if returncode != 0:
            login_message = codex_login_required_message(stderr)
            if login_message:
                raise RuntimeError(login_message)
            raise RuntimeError(f"Codex live session command failed with return code {returncode}.")
        final = (pending_message or "").strip()
        if not final and output_path.exists():
            final = output_path.read_text(encoding="utf-8").strip()
        if not final:
            raise ValueError("Codex live session returned an empty reply.")
        return final

    def _post_thought(self, text: str, *, kind: str) -> None:
        """Best-effort post of one live thought to the room (operator-only).

        Never raises — a streaming/network hiccup must not break the turn."""
        body = (text or "").strip()
        server = str(getattr(self.config, "server", "") or "").rstrip("/")
        meeting_id = str(getattr(self.config, "meeting_id", "") or "")
        if not body or not server or not meeting_id:
            return
        payload = {
            "name": self.config.display_name or self.config.agent_id,
            "message": body,
            "kind": "thinking",
            "channel": "lobby",
            "audience": "operator",
            "actor_type": "agent",
            "actor_id": self.config.agent_id,
            "flow_meeting_id": meeting_id,
            "thinking_kind": kind,
        }
        try:
            request = urllib.request.Request(
                f"{server}/api/lobby",
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            urllib.request.urlopen(request, timeout=5).read()
        except Exception:
            return

    def _build_command(self, output_path: Path, *, json_stream: bool = False) -> list[str]:
        configured_command = list(self.config.command or ["codex"])
        base_command = [configured_command[0]]
        sandbox = (
            (self._override_permission_option if self._override_permission_option is not None else "")
            or str(getattr(self.config, "permission_option", "") or "")
            or str(getattr(self.config, "codex_sandbox", "") or "")
            or "read-only"
        )
        exec_prefix = codex_exec_prefix(base_command, sandbox=sandbox)
        tuning_args = _codex_tuning_args(self.config.model_id, self.config.effort)
        # Per-agent fast toggle: explicitly force codex's fast_mode feature on.
        # Off = leave codex's own default (already fast) — no regression.
        fast_mode = (
            self._override_fast_mode
            if self._override_fast_mode is not None
            else bool(getattr(self.config, "fast_mode", False))
        )
        if fast_mode:
            tuning_args = [*tuning_args, "--enable", "fast_mode"]
        stream_args = ["--json"] if json_stream else []
        if self.session_id:
            return [
                *exec_prefix,
                *tuning_args,
                *stream_args,
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
            *stream_args,
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
