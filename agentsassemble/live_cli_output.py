from __future__ import annotations

import re

from agentsassemble.claude_resident import render_terminal_screen


_ANSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]|\x1b[@-_][0-?]*[ -/]*[@-~]?")
_SPINNER_RE = re.compile(r"[⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏⣻⣽⣾⣷⣯⣟⡿⢿█❙]+")
_BOX_RE = re.compile(r"[─━│╭╮╰╯┌┐└┘├┤┬┴┼]{3,}")
_MODEL_FOOTER_RE = re.compile(r"\b(?:gpt|Gemini|Grok)[A-Za-z0-9 ._-]*(?:\([^)]*\))?")
_TIME_RE = re.compile(r"\d{1,2}:\d{2}(?:AM|PM)")
_TOKEN_RE = re.compile(r"\b(?:rating\s+for\s+)?\d+(?:\.\d+)?s,\s*\d+\s+tokens\b", re.IGNORECASE)
_TURN_DONE_RE = re.compile(r"\bTurn\s*completed\s*in\s*\S+", re.IGNORECASE)
_THOUGHT_RE = re.compile(r"\b(?:Th)?ought\s+for\s*\S+", re.IGNORECASE)
_STATUS_RE = re.compile(
    r"\b(?:Working|Generating|Responding|Thinking)(?:\.\.\.)?(?:\s*[-–—]\s*[^…\n]*?(?:grok|Grok|Re…))*",
    re.IGNORECASE,
)
_GROK_TITLE_RE = re.compile(r"Grok Setup for #general[^…\n]*(?:Re…|grok)?", re.IGNORECASE)
_CODEX_FOOTER_RE = re.compile(r"›\s*Run /review on my current changes.*$", re.IGNORECASE)
_PROJECT_FOOTER_RE = re.compile(r"\bgpt-[^·\n]*·\s*~\/\S+", re.IGNORECASE)
_CHROME_WORDS = (
    "for shortcuts",
    "esc to cancel",
    "esc to interrupt",
    ".shortcuts",
    "shortcuts",
    "Do you trust",
)
_REASONING_FRAGMENTS = (
    "I need to",
    "The topic is",
    "translates to",
)


def strip_terminal_ansi(raw: bytes) -> str:
    text = raw.decode("utf-8", errors="replace").replace("\r\n", "\n").replace("\r", "\n")
    return _ANSI_RE.sub("", text).replace("\x07", "")


def extract_live_cli_terminal_message(raw: bytes) -> str:
    """Extract user-visible assistant text from a redrawing CLI TUI capture.

    The PTY stream is a control surface: spinners, status bars, prompt echoes,
    model footers, and thinking panels are not chat messages. This extractor is
    intentionally conservative. If it cannot find a provider marker, it strips
    known terminal chrome and returns only the remaining natural-language text.
    """

    if not raw:
        return ""
    candidates: list[str] = []
    for text in _candidate_texts(raw):
        marked = _extract_marked_answer(text)
        if marked:
            candidates.append(marked)
            continue
        filtered = filter_live_cli_terminal_text(text)
        if filtered:
            candidates.append(filtered)
    if not candidates:
        return ""
    return max(candidates, key=_message_score)


def filter_live_cli_terminal_text(text: str) -> str:
    lines: list[str] = []
    for raw_line in str(text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = _clean_terminal_line(raw_line)
        if not line or _is_terminal_chrome_line(line):
            continue
        lines.append(line)
    return _clean_message_text("\n".join(lines))


def _candidate_texts(raw: bytes) -> list[str]:
    candidates = [render_terminal_screen(raw), strip_terminal_ansi(raw)]
    unique: list[str] = []
    for candidate in candidates:
        if candidate and candidate not in unique:
            unique.append(candidate)
    return unique


def _extract_marked_answer(text: str) -> str:
    matches = list(re.finditer(r"(?:^|\s)•\s+", text))
    for match in reversed(matches):
        candidate = text[match.end() :]
        candidate = _cut_at_first_chrome(candidate)
        candidate = filter_live_cli_terminal_text(candidate)
        if _message_score(candidate) > 0:
            return candidate
    return ""


def _cut_at_first_chrome(text: str) -> str:
    cut = len(text)
    for pattern in ("›Run /review", "gpt-", "Gemini ", "Grok Setup", "? for shortcuts", ".shortcuts"):
        index = text.find(pattern)
        if index >= 0:
            cut = min(cut, index)
    return text[:cut]


def _clean_terminal_line(line: str) -> str:
    text = _ANSI_RE.sub("", str(line or ""))
    text = _CODEX_FOOTER_RE.sub("", text)
    text = _PROJECT_FOOTER_RE.sub("", text)
    text = _GROK_TITLE_RE.sub("", text)
    text = _MODEL_FOOTER_RE.sub("", text)
    text = _TOKEN_RE.sub("", text)
    text = _TURN_DONE_RE.sub("", text)
    text = _THOUGHT_RE.sub("", text)
    text = _TIME_RE.sub("", text)
    text = _STATUS_RE.sub("", text)
    text = re.sub(r"…\s*\d+(?:\.\d+)?s\d*", "", text)
    text = _BOX_RE.sub(" ", text)
    text = _SPINNER_RE.sub("", text)
    text = text.replace("AgentsAssemble", "")
    text = text.replace("esc to cancel", "").replace("esc to interrupt", "")
    text = text.replace(".shortcuts", "").replace("shortcuts", "")
    if "•" in text:
        text = text.rsplit("•", 1)[-1]
    text = _strip_interleaved_tui_digits(text)
    return _clean_message_text(text)


def _strip_interleaved_tui_digits(text: str) -> str:
    text = re.sub(r"(?<=[가-힣])\d+(?=[가-힣\s\"“”])", "", text)
    text = re.sub(r"(?<=[\"“”])\d+(?=[가-힣])", "", text)
    text = re.sub(r"(?<=\s)\d+(?=[가-힣])", "", text)
    text = re.sub(r"(?<=[가-힣])\d+(?=[.,!?])", "", text)
    return text


def _is_terminal_chrome_line(line: str) -> bool:
    stripped = line.strip()
    if not stripped:
        return True
    if stripped[0] in {"›", ">", "?", "╭", "╰", "│"}:
        return True
    lower = stripped.lower()
    if any(word.lower() in lower for word in _CHROME_WORDS):
        return True
    if any(fragment.lower() in lower for fragment in _REASONING_FRAGMENTS):
        return True
    if stripped in {"-", "—", ">", "█"}:
        return True
    if not re.search(r"[A-Za-z가-힣]", stripped):
        return True
    return False


def _clean_message_text(text: str) -> str:
    text = str(text or "").replace("\uFFFD", "")
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = re.sub(r"\s+\n", "\n", text)
    text = re.sub(r"\n\s+", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip(" \n\t·,-:;")


def _message_score(text: str) -> int:
    if not text:
        return 0
    score = len(text)
    lower = text.lower()
    for marker in ("generating", "responding", "thinking", "for shortcuts", "agentsassemble", "turncompleted"):
        if marker in lower:
            score -= 200
    if re.search(r"[가-힣]", text):
        score += 100
    return max(0, score)
