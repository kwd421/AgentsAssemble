from __future__ import annotations

from pathlib import Path
import shutil

from agentsassemble.room.text import clean_room_text
from agentsassemble.providers.transcripts.antigravity import AntigravityTranscriptMessageSource
from agentsassemble.providers.transcripts.claude import ClaudeSessionMessageSource
from agentsassemble.providers.transcripts.codex import CodexSessionMessageSource
from agentsassemble.providers.transcripts.core import LiveCliMessageSource, TerminalCaptureMessageSource
from agentsassemble.providers.transcripts.cursor import CursorSessionMessageSource
from agentsassemble.providers.transcripts.grok import GrokSessionMessageSource


def make_live_cli_message_source(
    agent_id: str,
    command: list[str],
    *,
    cwd: str | Path | None = None,
) -> LiveCliMessageSource:
    provider = _provider_key(agent_id, command)
    if provider == "codex":
        return CodexSessionMessageSource(cwd=cwd)
    if provider == "grok":
        return GrokSessionMessageSource(cwd=cwd)
    if provider == "antigravity":
        return AntigravityTranscriptMessageSource(cwd=cwd)
    if provider == "claude":
        return ClaudeSessionMessageSource(cwd=cwd)
    if provider == "cursor":
        return CursorSessionMessageSource(cwd=cwd)
    return TerminalCaptureMessageSource()


def _provider_key(agent_id: str, command: list[str]) -> str:
    agent = clean_room_text(agent_id, limit=128).casefold()
    executable = Path(str(command[0] if command else "")).name.casefold()
    resolved = Path(shutil.which(str(command[0])) or executable).name.casefold() if command else ""
    names = {agent, executable, resolved}
    if "codex" in names:
        return "codex"
    if "grok" in names:
        return "grok"
    if names & {"agy", "antigravity"}:
        return "antigravity"
    if "claude" in names:
        return "claude"
    if names & {"cursor", "cursor-agent"}:
        return "cursor"
    return ""


__all__ = ["make_live_cli_message_source"]
