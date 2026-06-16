from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, Sequence

from agentsassemble.meeting_events import clean_lobby_text


SANDBOX_ENFORCEMENT_LEVELS = {
    "advisory",
    "codex_readonly",
    "codex_workspace_write",
    "os_sandboxed",
    "unknown",
}
CODEX_EXEC_SAFETY_FLAGS = ("--sandbox", "read-only", "--ignore-user-config", "--ignore-rules")
# Opt-in "worker" sandbox: codex may write within its working dir (the repo) but
# not the wider filesystem (and no network). Only used when a resident is
# explicitly launched with --codex-sandbox workspace-write.
CODEX_EXEC_WORKSPACE_WRITE_FLAGS = ("--sandbox", "workspace-write", "--ignore-user-config", "--ignore-rules")


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
        # Default is read-only; workspace-write is an explicit opt-in for worker
        # residents that are meant to edit the repo they run in.
        if clean_lobby_text(sandbox, limit=32) == "workspace-write":
            return CodexWorkspaceWriteLauncher()
        return CodexReadonlyLauncher()
    return NoSandboxLauncher()


def safe_sandbox_enforcement(value: object) -> str:
    text = clean_lobby_text(value, limit=64)
    return text if text in SANDBOX_ENFORCEMENT_LEVELS else ""
