from __future__ import annotations


CLAUDE_CODE_PRINT_FLAGS = {"-p", "--print"}
CLAUDE_CODE_PRINT_MODE_MESSAGE = (
    "claude_code resident configs must not use Claude Code print/non-interactive mode; "
    "use terminal_session with command ['claude'] or a verified self_service/tool-loop wrapper."
)


def claude_code_print_mode_resident_check(
    provider_kind: str,
    connection_kind: str,
    command: list[str],
) -> dict[str, str] | None:
    if not claude_code_print_mode_resident_error(provider_kind, connection_kind, command):
        return None
    return {
        "id": "claude_code_resident_command",
        "status": "failed",
        "message": CLAUDE_CODE_PRINT_MODE_MESSAGE,
    }


def claude_code_print_mode_resident_error(
    provider_kind: str,
    connection_kind: str,
    command: list[str],
) -> str:
    if provider_kind != "claude_code":
        return ""
    if connection_kind == "remote_bridge":
        return ""
    for part in command:
        if part in CLAUDE_CODE_PRINT_FLAGS or part.startswith("--print="):
            return CLAUDE_CODE_PRINT_MODE_MESSAGE
    return ""
