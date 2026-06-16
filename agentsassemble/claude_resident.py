from __future__ import annotations

import re


CLAUDE_CODE_PRINT_FLAGS = {"-p", "--print"}

# Claude Code's interactive TUI is the only non-print (allowed) local path, so a
# resident drives it over a PTY and scrapes the screen. The CLI prefixes the
# assistant's reply line with ⏺ (U+23FA); the surrounding chrome — the echoed
# prompt (❯), the gerund "thinking" spinner (✳✶✷✸✢⠂…), the conversation title,
# the "(1s · ↓N tokens)" footer, box-drawing, and the usage banner — is noise.
# We extract the text after the LAST ⏺ up to the next chrome line. (A full VT
# screen emulator would be heavier; the marker makes it unnecessary for chat.)
CLAUDE_ANSWER_MARKER = "⏺"
_CLAUDE_CHROME_LEAD = "❯✳✶✷✸✢✱✻✽✿●─╭╮╰╯│•⠀⠁⠂⠄⠈⠐⠠⡀⢀⠿"
_CLAUDE_TOKEN_FOOTER_RE = re.compile(r"^\(\d+\s*[smh]?\b")


def _strip_terminal_ansi(raw: bytes) -> str:
    text = raw.decode("utf-8", errors="replace").replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", text)
    text = re.sub(r"\x1b[@-_][0-?]*[ -/]*[@-~]?", "", text)
    return text.replace("\x07", "")


def _is_claude_chrome_line(line: str) -> bool:
    stripped = line.strip()
    if not stripped:
        return True
    if stripped[0] in _CLAUDE_CHROME_LEAD:
        return True
    if _CLAUDE_TOKEN_FOOTER_RE.match(stripped):
        return True
    if "tokens)" in stripped or "for shortcuts" in stripped or "esc to interrupt" in stripped:
        return True
    if "% of your" in stripped and "limit" in stripped:
        return True
    return False


def claude_answer_ready(raw: bytes) -> bool:
    """True once the assistant's answer marker has rendered — the signal that
    Claude has produced (not just is thinking about) a reply. Used as the read
    loop's completion gate so an idle pause *before* the answer never returns early."""
    return CLAUDE_ANSWER_MARKER in _strip_terminal_ansi(raw)


def extract_claude_terminal_message(raw: bytes) -> str:
    """Pull Claude's reply out of the scraped TUI: the text after the LAST ⏺
    marker, joined until the next chrome line. Returns '' if no answer rendered."""
    lines = _strip_terminal_ansi(raw).split("\n")
    marker_index: int | None = None
    for index, line in enumerate(lines):
        if CLAUDE_ANSWER_MARKER in line:
            marker_index = index
    if marker_index is None:
        return ""
    first = lines[marker_index].split(CLAUDE_ANSWER_MARKER, 1)[1].strip()
    collected = [first] if first else []
    for line in lines[marker_index + 1:]:
        if _is_claude_chrome_line(line):
            break
        collected.append(line.strip())
    return "\n".join(part for part in collected if part).strip()
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
