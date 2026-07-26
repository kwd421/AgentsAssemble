#!/usr/bin/env python3
"""Reject provider commands that bypass canonical persistent Agent Sessions."""

from __future__ import annotations

import json
import os
import shlex
import sys
from collections.abc import Sequence


_SHELL_OPERATORS = {"&", "&&", "|", "||", ";", ";;", "\n"}
_COMMAND_WRAPPERS = {"command", "exec", "nohup", "time"}
_SHELL_EXECUTABLES = {"bash", "dash", "ksh", "sh", "zsh"}


def _command_segments(command: str) -> list[list[str]]:
    lexer = shlex.shlex(command, posix=True, punctuation_chars=";&|\n")
    lexer.whitespace_split = True
    lexer.commenters = ""
    segments: list[list[str]] = [[]]
    for token in lexer:
        if token in _SHELL_OPERATORS:
            if segments[-1]:
                segments.append([])
            continue
        segments[-1].append(token)
    return [segment for segment in segments if segment]


def _strip_command_prefix(tokens: Sequence[str]) -> list[str]:
    remaining = list(tokens)
    while remaining:
        token = remaining[0]
        if "=" in token and not token.startswith(("/", "./", "../", "-")):
            name, _separator, _value = token.partition("=")
            if name.replace("_", "a").isalnum() and not name[0].isdigit():
                remaining.pop(0)
                continue
        executable = os.path.basename(token)
        if executable in _COMMAND_WRAPPERS:
            remaining.pop(0)
            continue
        if executable == "env":
            remaining.pop(0)
            while remaining and (
                remaining[0].startswith("-")
                or (
                    "=" in remaining[0]
                    and not remaining[0].startswith(("/", "./", "../"))
                )
            ):
                remaining.pop(0)
            continue
        break
    return remaining


def _provider_violation(tokens: Sequence[str]) -> str:
    command = _strip_command_prefix(tokens)
    if not command:
        return ""
    executable = os.path.basename(command[0]).casefold()
    arguments = [part.casefold() for part in command[1:]]

    if executable == "claude" and any(
        part in {"-p", "--print"} or part.startswith("--print=")
        for part in arguments
    ):
        return "Claude print mode is forbidden; use the canonical interactive PTY Agent Session."
    if executable == "grok" and any(
        part in {"-p", "--prompt"} or part.startswith("--prompt=")
        for part in arguments
    ):
        return "Grok prompt mode is forbidden; use the canonical ACP Agent Session."
    if executable == "agy" and any(
        part == "--print" or part.startswith("--print=")
        for part in arguments
    ):
        return "Antigravity print mode is forbidden; use the canonical interactive PTY Agent Session."
    if executable == "codex" and "exec" in arguments:
        return "Codex exec mode is forbidden; use the canonical app-server Agent Session."

    if executable in _SHELL_EXECUTABLES:
        for index, argument in enumerate(arguments):
            if argument in {"-c", "-lc"} and index + 1 < len(command[1:]):
                nested = command[1:][index + 1]
                return provider_print_violation(nested)
    return ""


def provider_print_violation(command: str) -> str:
    for segment in _command_segments(command):
        violation = _provider_violation(segment)
        if violation:
            return violation
    return ""


def _deny(reason: str) -> dict[str, object]:
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }


def main() -> int:
    try:
        payload = json.load(sys.stdin)
        tool_input = payload.get("tool_input") or {}
        command = str(tool_input.get("command") or tool_input.get("cmd") or "")
        violation = provider_print_violation(command)
    except (AttributeError, json.JSONDecodeError, TypeError, ValueError) as exc:
        print(f"Provider launch policy could not inspect the command: {exc}", file=sys.stderr)
        return 2
    if violation:
        print(json.dumps(_deny(violation), ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
