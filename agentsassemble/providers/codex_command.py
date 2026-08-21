"""Shared Codex command construction for current provider callers."""

from __future__ import annotations

from agentsassemble.providers.sandbox_launcher import sandbox_launcher_for


def codex_exec_prefix(
    base_command: list[str],
    *,
    sandbox: str = "read-only",
) -> list[str]:
    return sandbox_launcher_for(
        "codex_live_session",
        "live_session",
        sandbox=sandbox,
    ).command(base_command)


__all__ = ["codex_exec_prefix"]
