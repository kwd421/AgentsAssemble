from __future__ import annotations

import re
import shlex
from typing import Protocol

from agentsassemble.providers.claude_resident import render_terminal_screen
from agentsassemble.providers.live_cli_output import strip_terminal_ansi
from agentsassemble.providers.runtime_contracts import AdapterContractError


_PERMISSION_BLOCK = re.compile(
    r"Requesting permission for:\s*(?P<command>.+?)\s*Do you want to proceed\?",
    flags=re.IGNORECASE | re.DOTALL,
)
_ATTACHMENT_ID = re.compile(r"^[A-Za-z0-9_-]{8,64}$")
_AGENT_ID = re.compile(r"^[A-Za-z0-9_.-]{1,128}$")
_DICE_NOTATION = re.compile(
    r"^(?P<count>\d{0,3})d(?P<sides>\d{1,4})(?P<modifier>[+-]\d{1,5})?$",
    re.IGNORECASE,
)
_SHELL_CONTROL_TOKENS = frozenset({";", ";;", "&", "&&", "|", "||", "<", ">", "(", ")"})


class TerminalInteractionPolicy(Protocol):
    def begin_turn(self) -> None:
        ...

    def response_for(self, output: bytes) -> bytes:
        ...

    def describe(self) -> dict[str, object]:
        ...


class AntigravityRoomPortalInteraction:
    """Approve only validated room-portal commands during an ambient turn."""

    def __init__(self) -> None:
        self._handled_commands: set[str] = set()
        self._approval_count = 0
        self._rejection_count = 0

    def begin_turn(self) -> None:
        self._handled_commands.clear()

    def response_for(self, output: bytes) -> bytes:
        command = _latest_permission_command(output)
        if not command or command in self._handled_commands:
            return b""
        self._handled_commands.add(command)
        if not _is_safe_room_portal_command(command):
            self._rejection_count += 1
            raise AdapterContractError(
                "Antigravity requested an unapproved terminal command during room observation.",
                code="unexpected_provider_permission_request",
            )
        self._approval_count += 1
        # Conversation-scoped approval lets long room messages bypass the
        # truncated permission card. Antigravity still asks again when shell
        # control tokens extend the approved command prefix.
        return b"\x1b[B\r"

    def describe(self) -> dict[str, object]:
        return {
            "room_portal_permission_approval_count": self._approval_count,
            "room_portal_permission_rejection_count": self._rejection_count,
        }


def _latest_permission_command(output: bytes) -> str:
    candidates = (render_terminal_screen(output), strip_terminal_ansi(output))
    for text in candidates:
        matches = list(_PERMISSION_BLOCK.finditer(text))
        if not matches:
            continue
        return " ".join(matches[-1].group("command").split())
    return ""


def _is_safe_room_portal_command(command: str) -> bool:
    try:
        parts = _room_portal_command_parts(command)
    except ValueError:
        return False
    if len(parts) < 2 or parts[0] != "agentsassemble-room":
        return False
    if any(part in _SHELL_CONTROL_TOKENS for part in parts):
        return False
    action = parts[1]
    arguments = parts[2:]
    if action == "read":
        return not arguments
    if action == "media":
        return len(arguments) == 1 and bool(_ATTACHMENT_ID.fullmatch(arguments[0]))
    if action == "roll":
        return len(arguments) == 1 and _is_bounded_dice_notation(arguments[0])
    if action == "speak-to":
        if len(arguments) < 2 or _AGENT_ID.fullmatch(arguments[0]) is None:
            return False
        arguments = arguments[1:]
    elif action != "speak":
        return False
    if not arguments:
        return False
    if _has_shell_expansion_outside_single_quotes(command):
        return False
    return not any(
        argument.startswith("~")
        for argument in arguments
    )


def is_safe_room_roll_command(command: str) -> bool:
    """Return whether a shell command is exactly one bounded room dice roll."""

    try:
        parts = _room_portal_command_parts(command)
    except ValueError:
        return False
    return bool(
        len(parts) == 3
        and parts[0] == "agentsassemble-room"
        and parts[1] == "roll"
        and _is_bounded_dice_notation(parts[2])
    )


def _is_bounded_dice_notation(value: str) -> bool:
    match = _DICE_NOTATION.fullmatch(value)
    if match is None:
        return False
    count = int(match.group("count") or 1)
    sides = int(match.group("sides"))
    modifier = int(match.group("modifier") or 0)
    return bool(
        1 <= count <= 100
        and 2 <= sides <= 1000
        and -100_000 <= modifier <= 100_000
    )


def _room_portal_command_parts(command: str) -> list[str]:
    parts = _split_shell_command(command)
    if len(parts) == 1 and parts[0].startswith("agentsassemble-room "):
        parts = _split_shell_command(parts[0])
    if any(part in _SHELL_CONTROL_TOKENS for part in parts):
        raise ValueError("room portal commands cannot contain shell control tokens")
    return parts


def _has_shell_expansion_outside_single_quotes(command: str) -> bool:
    in_single_quote = False
    in_double_quote = False
    escaped = False
    for character in command:
        if escaped:
            escaped = False
            continue
        if character == "\\" and not in_single_quote:
            escaped = True
            continue
        if character == "'" and not in_double_quote:
            in_single_quote = not in_single_quote
            continue
        if character == '"' and not in_single_quote:
            in_double_quote = not in_double_quote
            continue
        if character in {"$", "`"} and not in_single_quote:
            return True
    return False


def _split_shell_command(command: str) -> list[str]:
    lexer = shlex.shlex(command, posix=True, punctuation_chars=";&|<>()")
    lexer.whitespace_split = True
    lexer.commenters = ""
    return list(lexer)


__all__ = [
    "AntigravityRoomPortalInteraction",
    "TerminalInteractionPolicy",
    "is_safe_room_roll_command",
]
