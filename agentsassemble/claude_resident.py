"""Compatibility exports for Claude resident TUI handling."""

from agentsassemble.providers.claude_resident import (
    CLAUDE_ANSWER_MARKER,
    CLAUDE_CODE_PRINT_FLAGS,
    CLAUDE_CODE_PRINT_MODE_MESSAGE,
    _strip_envelope_leak,
    _strip_terminal_ansi,
    claude_answer_ready,
    claude_code_print_mode_resident_check,
    claude_code_print_mode_resident_error,
    extract_claude_terminal_message,
    render_terminal_screen,
)


__all__ = [
    "CLAUDE_ANSWER_MARKER",
    "CLAUDE_CODE_PRINT_FLAGS",
    "CLAUDE_CODE_PRINT_MODE_MESSAGE",
    "claude_answer_ready",
    "claude_code_print_mode_resident_check",
    "claude_code_print_mode_resident_error",
    "extract_claude_terminal_message",
    "render_terminal_screen",
]
