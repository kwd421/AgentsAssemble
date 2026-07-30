"""Allowlisted provider CLI login launched from the local operator UI."""

from __future__ import annotations

import json
import platform
import shlex
import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from agentsassemble.providers.launch_specs import native_cli_provider_definition
from agentsassemble.room.text import clean_room_text


ProviderLoginLauncher = Callable[[list[str]], object]
ProviderLoginResolver = Callable[[str], str | None]
ProviderLoginRecorder = Callable[..., object]
ProviderCatalogRefresher = Callable[[], object]


class ProviderLoginProcess(Protocol):
    def wait(self, timeout: float | None = None) -> int: ...


@dataclass(frozen=True)
class ProviderLoginService:
    """Launch only the login command owned by a current provider definition."""

    command_launcher: ProviderLoginLauncher | None = None
    command_resolver: ProviderLoginResolver | None = None
    operation_recorder: ProviderLoginRecorder | None = None
    catalog_refresher: ProviderCatalogRefresher | None = None
    oauth_timeout_seconds: float = 600.0

    def start(self, payload: dict[str, object]) -> dict[str, object]:
        requested_id = clean_room_text(payload.get("provider_id"), limit=64)
        definition = native_cli_provider_definition(requested_id)
        if definition is None:
            self._record(status="failed", target_id=requested_id, error="Unknown agent provider.")
            raise ValueError("Unknown agent provider.")
        if not definition.login_command:
            message = f"{definition.display_name} does not support local login from this UI."
            self._record(status="failed", target_id=definition.provider_id, error=message)
            raise ValueError(message)

        try:
            command = _resolved_login_command(
                definition.login_command,
                command_resolver=self.command_resolver or shutil.which,
            )
            process = (
                self.command_launcher(command)
                if self.command_launcher is not None
                else launch_provider_login(command, login_flow=definition.login_flow)
            )
            authenticated = False
            if definition.login_flow == "browser_oauth" and _is_waitable_process(process):
                try:
                    returncode = process.wait(timeout=self.oauth_timeout_seconds)
                except subprocess.TimeoutExpired as error:
                    _stop_login_process(process)
                    raise OSError(
                        f"{definition.display_name} login timed out."
                    ) from error
                if returncode != 0:
                    raise OSError(
                        f"{definition.display_name} login exited with code {returncode}."
                    )
                if self.catalog_refresher is not None:
                    self.catalog_refresher()
                authenticated = True
        except (OSError, ValueError) as error:
            self._record(
                status="failed",
                target_id=definition.provider_id,
                error=str(error),
            )
            raise

        self._record(
            status="success",
            target_id=definition.provider_id,
            summary=(
                "completed provider login from agent creation"
                if authenticated
                else "started provider login from agent creation"
            ),
            details={"provider_id": definition.provider_id},
        )
        return {
            "status": "authenticated" if authenticated else "started",
            "provider_id": definition.provider_id,
            "label": f"{definition.display_name} 로그인",
            "message": (
                f"{definition.display_name} 로그인이 완료됐습니다."
                if authenticated
                else (
                    f"{definition.display_name} 로그인 창을 열었습니다. "
                    "완료한 뒤 로그인 상태를 다시 확인하세요."
                )
            ),
        }

    def record_invalid_json(self) -> None:
        self._record(status="failed", target_id="", error="Invalid JSON")

    def _record(
        self,
        *,
        status: str,
        target_id: str,
        summary: str = "",
        error: str = "",
        details: dict[str, object] | None = None,
    ) -> None:
        if self.operation_recorder is None:
            return
        self.operation_recorder(
            operation="frontend_agent.login",
            status=status,
            target_id=target_id,
            summary=summary,
            error=error,
            details=details or {},
        )


def _resolved_login_command(
    command: tuple[str, ...],
    *,
    command_resolver: ProviderLoginResolver,
) -> list[str]:
    parts = [str(part).strip() for part in command if str(part).strip()]
    if not parts:
        raise ValueError("Provider login command is not configured.")
    executable = command_resolver(parts[0])
    if not executable:
        raise ValueError(
            f"{parts[0]} command was not found. Install or configure the provider CLI first."
        )
    return [executable, *parts[1:]]


def launch_provider_login(
    command: list[str],
    *,
    login_flow: str,
) -> ProviderLoginProcess:
    """Launch browser OAuth quietly or open a visible interactive terminal."""

    system = platform.system()
    if login_flow == "browser_oauth":
        return subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=system != "Windows",
            creationflags=(
                getattr(subprocess, "CREATE_NO_WINDOW", 0)
                if system == "Windows"
                else 0
            ),
        )
    if login_flow != "interactive_terminal":
        raise ValueError("Provider login flow is not configured.")
    if system == "Darwin" and shutil.which("osascript"):
        shell_command = shlex.join(command)
        script = f'tell application "Terminal" to do script {json.dumps(shell_command)}'
        return subprocess.Popen(
            ["osascript", "-e", script],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    if system == "Windows":
        return subprocess.Popen(
            command,
            creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0),
            close_fds=True,
        )

    for terminal_command in _linux_terminal_commands(command):
        if shutil.which(terminal_command[0]):
            return subprocess.Popen(
                terminal_command,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
    raise OSError("No supported graphical terminal was found for provider login.")


def _linux_terminal_commands(command: list[str]) -> tuple[list[str], ...]:
    return (
        ["x-terminal-emulator", "-e", *command],
        ["gnome-terminal", "--", *command],
        ["konsole", "-e", *command],
        ["xfce4-terminal", "-x", *command],
    )


def _is_waitable_process(value: object) -> bool:
    return callable(getattr(value, "wait", None))


def _stop_login_process(process: object) -> None:
    terminate = getattr(process, "terminate", None)
    if callable(terminate):
        terminate()


__all__ = [
    "ProviderLoginLauncher",
    "ProviderLoginResolver",
    "ProviderLoginService",
    "launch_provider_login",
]
