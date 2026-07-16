"""List a provider CLI's locally-stored sessions so the agent-create flow can
offer "resume an existing session" instead of always starting fresh.

Each CLI stores sessions differently, so this is a thin per-provider reader that
returns a uniform shape:

    {"session_id": str, "label": str, "updated_at": iso8601}

Best-effort and never raises — a provider with no readable store returns [].
The resident already resumes via session_id (codex --resume, grok --resume,
claude --resume/--session-id, agy --conversation); this just surfaces the ids.
"""
from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path

_UUID_RE = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
_DEFAULT_LIMIT = 15


def list_provider_sessions(
    provider_kind: str,
    *,
    workspace: str = "",
    home: Path | None = None,
    limit: int = _DEFAULT_LIMIT,
) -> list[dict[str, str]]:
    base = home or Path.home()
    kind = str(provider_kind or "")
    try:
        if kind == "codex_live_session":
            return _list_jsonl_sessions(base / ".codex" / "sessions", limit, id_from_name=True)
        if kind == "claude_code":
            return _list_claude_sessions(base, workspace, limit)
        if kind == "antigravity_live_session":
            return _list_antigravity_sessions(base, limit)
        # grok stores no clean per-session list we can surface (active_sessions
        # is empty; the sessions dir holds internal indexes, not conversations).
        if kind == "grok_live_session":
            return []
    except Exception:
        return []
    return []


def _iso(mtime: float) -> str:
    return datetime.fromtimestamp(mtime, UTC).isoformat()


def _sorted_limited(items: list[dict[str, str]], limit: int) -> list[dict[str, str]]:
    items.sort(key=lambda item: item.get("updated_at", ""), reverse=True)
    return items[: max(1, int(limit))]


def _session_id_from(path: Path, *, id_from_name: bool) -> str:
    if id_from_name:
        match = _UUID_RE.search(path.name)
        if match:
            return match.group(0)
    return path.stem


def _first_user_text(path: Path, *, max_lines: int = 60) -> str:
    try:
        with path.open("r", encoding="utf-8") as handle:
            for index, line in enumerate(handle):
                if index > max_lines:
                    break
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except (json.JSONDecodeError, ValueError):
                    continue
                text = _extract_user_text(entry)
                if text:
                    return text[:80]
    except OSError:
        return ""
    return ""


def _extract_user_text(entry: object) -> str:
    if not isinstance(entry, dict):
        return ""
    message = entry.get("message") if isinstance(entry.get("message"), dict) else entry
    role = str(message.get("role") or entry.get("role") or "")
    if role and role != "user":
        return ""
    content = message.get("content")
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                piece = str(block.get("text") or "").strip()
                if piece:
                    return piece
    return ""


def _list_jsonl_sessions(directory: Path, limit: int, *, id_from_name: bool) -> list[dict[str, str]]:
    if not directory.exists():
        return []
    items: list[dict[str, str]] = []
    for path in directory.rglob("*.jsonl"):
        try:
            mtime = path.stat().st_mtime
        except OSError:
            continue
        items.append(
            {
                "session_id": _session_id_from(path, id_from_name=id_from_name),
                "label": _first_user_text(path) or path.stem[:80],
                "updated_at": _iso(mtime),
            }
        )
    return _sorted_limited(items, limit)


def _claude_project_dir(home: Path, workspace: str) -> Path | None:
    base = home / ".claude" / "projects"
    if not base.exists():
        return None
    clean = str(workspace or "").strip()
    if not clean:
        return base  # scan all projects
    encoded = re.sub(r"[/.]", "-", clean)
    candidate = base / encoded
    return candidate if candidate.exists() else base


def _list_claude_sessions(home: Path, workspace: str, limit: int) -> list[dict[str, str]]:
    directory = _claude_project_dir(home, workspace)
    if directory is None:
        return []
    return _list_jsonl_sessions(directory, limit, id_from_name=False)


def _list_antigravity_sessions(home: Path, limit: int) -> list[dict[str, str]]:
    conversations = home / ".gemini" / "antigravity-cli" / "conversations"
    if not conversations.exists():
        return []
    # cwd -> conversation_id map gives human labels (the working dir).
    labels: dict[str, str] = {}
    cache = home / ".gemini" / "antigravity-cli" / "cache" / "last_conversations.json"
    try:
        data = json.loads(cache.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            for cwd, conv_id in data.items():
                labels[str(conv_id)] = Path(str(cwd)).name or str(cwd)
    except (OSError, json.JSONDecodeError, ValueError):
        pass
    items: list[dict[str, str]] = []
    for path in conversations.glob("*.db"):
        try:
            mtime = path.stat().st_mtime
        except OSError:
            continue
        conv_id = path.stem
        items.append(
            {
                "session_id": conv_id,
                "label": labels.get(conv_id, conv_id[:8]),
                "updated_at": _iso(mtime),
            }
        )
    return _sorted_limited(items, limit)
