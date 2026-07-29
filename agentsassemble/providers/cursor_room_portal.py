"""Cursor CLI room MCP lifecycle for one managed Agent Session."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path
from typing import Callable

from agentsassemble.providers.process_environment import sanitized_provider_environment
from agentsassemble.providers.room_portal import RoomPortal
from agentsassemble.providers.room_portal_mcp import room_portal_mcp_settings
from agentsassemble.providers.runtime_config import ProviderRuntimeConfig


_REQUIRED_ROOM_TOOLS = frozenset(
    {"read_discussion", "publish_message", "roll_dice", "choose_random"}
)


class CursorRoomPortalRuntime:
    """Wrap a persistent Cursor PTY while owning its exact room MCP approval."""

    def __init__(
        self,
        config: ProviderRuntimeConfig,
        *,
        room_portal: RoomPortal,
        runtime_factory: Callable[..., object],
        environment: dict[str, str] | None = None,
        command_runner=subprocess.run,
    ) -> None:
        self._config = config
        self._room_portal = room_portal
        self._command_runner = command_runner
        self._workspace = Path(config.runtime_state_dir) / "cursor-room-workspace"
        digest = hashlib.sha256(
            f"{config.participant_id}\0{room_portal.root}".encode("utf-8")
        ).hexdigest()[:16]
        self._server_name = f"agentsassemble_room_{digest}"
        self._mcp_status = ""
        self._workspace.mkdir(parents=True, exist_ok=True)
        self._write_mcp_config()
        command = [
            *config.command,
            "--trust",
            "--workspace",
            str(self._workspace),
            "--add-dir",
            config.cwd,
        ]
        self._runtime = runtime_factory(
            config.participant_id,
            command,
            cwd=self._workspace,
            env=environment,
            idle_quiet_seconds=config.quiet_seconds,
            input_mode=config.input_mode,
            submit_newline=config.submit_newline,
            submit_delay_seconds=config.submit_delay_seconds,
            terminal_rows=config.terminal_rows,
            terminal_columns=config.terminal_columns,
            startup_quiet_seconds=config.startup_quiet_seconds,
            startup_timeout_seconds=config.startup_timeout_seconds,
            startup_accept_contains=config.startup_accept_contains,
            startup_accept_keys=config.startup_accept_keys,
            startup_ready_contains=config.startup_ready_contains,
            startup_input=config.startup_input,
            profile_settings={
                "model": config.model,
                "reasoning_effort": config.reasoning_effort,
                "service_tier": config.service_tier,
                "variant": config.variant,
                "permission_mode": config.permission_mode,
            },
        )

    def start(self) -> dict[str, object]:
        if self._mcp_status != "ready":
            self._enable_room_mcp()
        return self.health_from(self._runtime.start())

    def send(self, text: str) -> None:
        self._runtime.send(text)

    def send_room_observation(
        self,
        text: str,
        *,
        media_blocks: list[dict[str, str]] | None = None,
    ) -> None:
        self._runtime.send_room_observation(text, media_blocks=media_blocks)

    def read_output(self, *, timeout_seconds: float, on_delta=None, on_activity=None):
        return self._runtime.read_output(
            timeout_seconds=timeout_seconds,
            on_delta=on_delta,
            on_activity=on_activity,
        )

    def interrupt(self) -> None:
        self._runtime.interrupt()

    def stop(self, *, timeout_seconds: float = 2.0) -> None:
        try:
            self._runtime.stop(timeout_seconds=timeout_seconds)
        finally:
            self._disable_room_mcp()

    def health(self) -> dict[str, object]:
        return self.health_from(self._runtime.health())

    def health_from(self, value: object) -> dict[str, object]:
        health = dict(value) if isinstance(value, dict) else {}
        health["room_mcp_status"] = self._mcp_status
        return health

    def _write_mcp_config(self) -> None:
        settings = room_portal_mcp_settings(self._room_portal.root)
        target = self._workspace / ".cursor" / "mcp.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "mcpServers": {
                self._server_name: {
                    "command": str(settings["command"]),
                    "args": [str(value) for value in settings.get("args", [])],
                    "cwd": str(settings["cwd"]),
                    "env": {
                        "PYTHONPATH": str(Path(__file__).resolve().parents[2]),
                    },
                }
            }
        }
        temporary = target.with_name(f".{target.name}.{os.getpid()}.tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        temporary.chmod(0o600)
        os.replace(temporary, target)

    def _enable_room_mcp(self) -> None:
        self._run_cursor_mcp(["enable", self._server_name], timeout_seconds=20.0)
        result = self._run_cursor_mcp(
            ["list-tools", self._server_name],
            timeout_seconds=30.0,
        )
        listed = {
            line.lstrip()[2:].split(" ", 1)[0].strip()
            for line in result.stdout.splitlines()
            if line.lstrip().startswith("- ")
        }
        missing = _REQUIRED_ROOM_TOOLS - listed
        if missing:
            raise RuntimeError(
                "Cursor room MCP connected without all required room tools."
            )
        self._mcp_status = "ready"

    def _disable_room_mcp(self) -> None:
        if not self._mcp_status:
            return
        try:
            self._run_cursor_mcp(
                ["disable", self._server_name],
                timeout_seconds=10.0,
            )
        except Exception:
            self._mcp_status = "disable_failed"
            return
        self._mcp_status = "disabled"

    def _run_cursor_mcp(
        self,
        arguments: list[str],
        *,
        timeout_seconds: float,
    ) -> subprocess.CompletedProcess[str]:
        command = [str(self._config.command[0]), "mcp", *arguments]
        result = self._command_runner(
            command,
            text=True,
            capture_output=True,
            timeout=timeout_seconds,
            check=False,
            cwd=str(self._workspace),
            env=sanitized_provider_environment(),
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"Cursor room MCP command failed with return code {result.returncode}."
            )
        return result


__all__ = ["CursorRoomPortalRuntime"]
