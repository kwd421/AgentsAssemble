from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, Sequence

from agentsassemble.legacy.meeting.core.events import clean_lobby_text


SANDBOX_ENFORCEMENT_LEVELS = {
    "advisory",
    "codex_readonly",
    "codex_workspace_write",
    "os_sandboxed",
    "unknown",
}
CODEX_EXEC_SAFETY_FLAGS = ("--sandbox", "read-only", "--ignore-user-config", "--ignore-rules")
# Released sandboxes drop --ignore-user-config so the user's MCP servers / tools
# are available (the room doesn't strip the agent's native capability).
CODEX_EXEC_WORKSPACE_WRITE_FLAGS = ("--sandbox", "workspace-write", "--ignore-rules")
# Full access: no sandbox. EXTREMELY powerful — only via explicit "전체 해제".
CODEX_EXEC_FULL_ACCESS_FLAGS = ("--sandbox", "danger-full-access", "--ignore-rules")


class SandboxLauncher(Protocol):
    @property
    def enforcement(self) -> str:
        ...

    def command(self, command: Sequence[str]) -> list[str]:
        ...


@dataclass(frozen=True)
class NoSandboxLauncher:
    enforcement: str = "advisory"

    def command(self, command: Sequence[str]) -> list[str]:
        return list(command)


@dataclass(frozen=True)
class CodexReadonlyLauncher:
    enforcement: str = "codex_readonly"

    def command(self, command: Sequence[str]) -> list[str]:
        return [*command, "exec", *CODEX_EXEC_SAFETY_FLAGS]


@dataclass(frozen=True)
class CodexWorkspaceWriteLauncher:
    enforcement: str = "codex_workspace_write"

    def command(self, command: Sequence[str]) -> list[str]:
        return [*command, "exec", *CODEX_EXEC_WORKSPACE_WRITE_FLAGS]


@dataclass(frozen=True)
class CodexFullAccessLauncher:
    enforcement: str = "codex_full_access"

    def command(self, command: Sequence[str]) -> list[str]:
        return [*command, "exec", *CODEX_EXEC_FULL_ACCESS_FLAGS]


def sandbox_launcher_for(
    provider_kind: object,
    connection_kind: object,
    *,
    sandbox: str = "read-only",
) -> SandboxLauncher:
    provider = clean_lobby_text(provider_kind, limit=64)
    connection = clean_lobby_text(connection_kind, limit=64)
    if (
        provider == "codex_live_session"
        and connection in {"codex_resume", "live_session"}
    ) or (
        provider == "codex"
        and connection == "codex_resume"
    ):
        # Surface codex's own sandbox modes; default stays read-only.
        mode = clean_lobby_text(sandbox, limit=32)
        if mode == "workspace-write":
            return CodexWorkspaceWriteLauncher()
        if mode == "danger-full-access":
            return CodexFullAccessLauncher()
        return CodexReadonlyLauncher()
    return NoSandboxLauncher()


def safe_sandbox_enforcement(value: object) -> str:
    text = clean_lobby_text(value, limit=64)
    return text if text in SANDBOX_ENFORCEMENT_LEVELS else ""
