from __future__ import annotations


def claude_interactive_command(
    *,
    executable: str,
    model: str,
    reasoning_effort: str,
    permission_mode: str,
    workspace_write_mode: str,
) -> list[str]:
    """Build the isolated interactive Claude command used by every room runtime."""

    if not model:
        raise ValueError("Claude model is required.")
    command = [executable, "--model", model]
    if reasoning_effort:
        command.extend(("--effort", reasoning_effort))
    if permission_mode == "workspace_write":
        command.extend(("--permission-mode", workspace_write_mode))
    else:
        command.extend(
            (
                "--permission-mode",
                "dontAsk",
                "--tools",
                "Bash,AskUserQuestion",
                "--allowedTools",
                "Bash(agentsassemble-room *),AskUserQuestion",
            )
        )
    command.extend(("--setting-sources", "", "--disable-slash-commands"))
    return command


__all__ = ["claude_interactive_command"]
