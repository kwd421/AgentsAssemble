"""Claude Code interactive TUI parsing and resident command validation."""

from __future__ import annotations

import re
import unicodedata


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


_CSI_RE = re.compile(r"\x1b\[([0-9;?]*)([@-~])")
_SCREEN_MAX_ROWS = 400
_SCREEN_MAX_COLS = 400


def render_terminal_screen(raw: bytes) -> str:
    """Render a cursor-positioned TUI byte stream into plain text by emulating a
    virtual screen grid. Claude Code draws each glyph at an absolute (row, col)
    and uses cursor moves where spaces would be, so a linear ANSI-strip mashes
    words ("현 시점"→"현시점"). Replaying the moves onto a space-filled grid puts
    the gaps back. Handles the CSI subset Claude actually emits (CUP/CUU/CUD/CUF/
    CUB/CHA, EL, ED) + CR/LF/BS/TAB; SGR and the rest are ignored."""
    text = raw.decode("utf-8", errors="replace")
    grid: list[list[str]] = []
    row = col = 0

    def cell_row(r: int) -> list[str]:
        while len(grid) <= r:
            grid.append([])
        return grid[r]

    def put(ch: str) -> None:
        nonlocal col
        if row >= _SCREEN_MAX_ROWS or col >= _SCREEN_MAX_COLS:
            return
        # CJK/fullwidth glyphs occupy two terminal columns; advancing by one would
        # leave a spurious gap per character ("현재" → "현 재"). Mark the trailing
        # column with a sentinel that the render step drops.
        width = 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
        line = cell_row(row)
        while len(line) <= col + width - 1:
            line.append(" ")
        line[col] = ch
        if width == 2:
            line[col + 1] = "\x00"
        col += width

    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        if ch == "\x1b":
            match = _CSI_RE.match(text, i)
            if not match:
                i += 2  # non-CSI escape (e.g. ESC ] OSC start) — skip the pair
                continue
            params, final = match.group(1), match.group(2)
            nums = [int(p) for p in params.split(";") if p.isdigit()]
            first = nums[0] if nums else 0
            if final in "Hf":
                row = max(0, (nums[0] - 1) if len(nums) >= 1 and nums[0] else 0)
                col = max(0, (nums[1] - 1) if len(nums) >= 2 and nums[1] else 0)
            elif final == "A":
                row = max(0, row - max(1, first))
            elif final == "B":
                row = row + max(1, first)
            elif final == "C":
                col = col + max(1, first)
            elif final == "D":
                col = max(0, col - max(1, first))
            elif final == "G":
                col = max(0, first - 1 if first else 0)
            elif final == "K":  # erase line
                line = cell_row(row)
                if first == 0:
                    del line[col:]
                elif first == 1:
                    for c in range(min(col + 1, len(line))):
                        line[c] = " "
                else:
                    line.clear()
            elif final == "J":  # erase display
                if first == 2:
                    grid.clear()
                    row = col = 0
                elif first == 0:
                    del cell_row(row)[col:]
                    del grid[row + 1:]
            i = match.end()
            continue
        if ch == "\n":
            row += 1
            i += 1
        elif ch == "\r":
            col = 0
            i += 1
        elif ch == "\x08":
            col = max(0, col - 1)
            i += 1
        elif ch == "\t":
            col = (col // 8 + 1) * 8
            i += 1
        elif ch == "\x07":
            i += 1
        else:
            put(ch)
            i += 1

    lines = ["".join(c for c in line if c != "\x00").rstrip() for line in grid]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


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


# Distinctive fragments of the "Room delivery envelope" prompt boilerplate
# (live_agent_runner) — these must NEVER appear in a reply. The TUI scrape can
# tack the echoed envelope onto the answer, and the lost spaces defeat the normal
# control-meta filter, so we strip it here space-insensitively.
_ENVELOPE_LEAK_SIGNATURES = (
    "transporthasroomtools",
    "roomdeliveryenvelope",
    "agentsassembleownsroom",
    "inspectread-since",
    "speakeridentity",
    "sourceeventid:",
    "officialcursor:",
    "lobbycursor:",
)


def _strip_envelope_leak(message: str) -> str:
    """Cut the message at the earliest echoed-envelope signature, matched with
    spaces removed (so "transport has room tools" and "transporthasroomtools"
    both trigger). Maps the compact hit back to the original index."""
    compact_chars: list[str] = []
    original_index: list[int] = []
    for index, char in enumerate(message):
        if not char.isspace():
            compact_chars.append(char.lower())
            original_index.append(index)
    compact = "".join(compact_chars)
    cut: int | None = None
    for signature in _ENVELOPE_LEAK_SIGNATURES:
        pos = compact.find(signature)
        if pos != -1:
            cut = pos if cut is None else min(cut, pos)
    if cut is None:
        return message
    return message[: original_index[cut]].rstrip(" \n\t·,-")


def extract_claude_terminal_message(raw: bytes) -> str:
    """Pull Claude's reply out of the scraped TUI: the text after the LAST ⏺
    marker, joined until the next chrome line. Returns '' if no answer rendered."""
    lines = render_terminal_screen(raw).split("\n")
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
    return _strip_envelope_leak("\n".join(part for part in collected if part).strip())


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
