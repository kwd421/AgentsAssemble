"""List a provider CLI's locally-stored sessions so the agent-create flow can
offer "resume an existing session" instead of always starting fresh.

Each CLI stores sessions differently, so this is a thin per-provider reader that
returns a uniform shape:

    {"session_id": str, "label": str, "updated_at": iso8601}

Discovery never raises across the HTTP boundary. A provider with no local store
returns a ready empty listing; a store that exists but cannot be read returns an
explicit error listing and is logged for diagnosis. The resident already resumes
via session_id (codex --resume, grok --resume, claude --resume/--session-id,
agy --conversation); this just surfaces the ids.
"""
from __future__ import annotations

import json
import logging
import re
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import unquote

_UUID_RE = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
# Rollout headers carry the folder the session was started in.
_CWD_RE = re.compile(r'"cwd"\s*:\s*"((?:[^"\\]|\\.)*)"')
_DEFAULT_LIMIT = 15
LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class ProviderSessionListing:
    status: str
    sessions: list[dict[str, str]]
    error_code: str = ""
    error: str = ""

    def payload(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "status": self.status,
            "sessions": self.sessions,
        }
        if self.error_code:
            payload["error_code"] = self.error_code
        if self.error:
            payload["error"] = self.error
        return payload


def list_provider_sessions(
    provider_kind: str,
    *,
    workspace: str = "",
    home: Path | None = None,
    limit: int = _DEFAULT_LIMIT,
) -> list[dict[str, str]]:
    return inspect_provider_sessions(
        provider_kind,
        workspace=workspace,
        home=home,
        limit=limit,
    ).sessions


def inspect_provider_sessions(
    provider_kind: str,
    *,
    workspace: str = "",
    home: Path | None = None,
    limit: int = _DEFAULT_LIMIT,
) -> ProviderSessionListing:
    base = home or Path.home()
    kind = str(provider_kind or "")
    try:
        if kind == "codex_live_session":
            sessions = _list_jsonl_sessions(
                base / ".codex" / "sessions",
                limit,
                id_from_name=True,
                workspace=workspace,
            )
        elif kind == "claude_code":
            sessions = _list_claude_sessions(base, workspace, limit)
        elif kind == "antigravity_live_session":
            sessions = _list_antigravity_sessions(base, workspace, limit)
        elif kind == "grok_live_session":
            sessions = _list_grok_sessions(base, workspace, limit)
        elif kind == "cursor_live_session":
            sessions = _list_cursor_sessions(base, workspace, limit)
        elif kind == "opencode_server":
            sessions = _list_opencode_sessions(base, workspace, limit)
        # ollama serves models over an API and keeps no conversation store of
        # its own, so there is nothing local to resume.
        else:
            sessions = []
    except Exception:
        LOGGER.exception("Provider session discovery failed for %s", kind)
        return ProviderSessionListing(
            status="error",
            sessions=[],
            error_code="provider_session_discovery_failed",
            error="Provider session store could not be read.",
        )
    return ProviderSessionListing(status="ready", sessions=sessions)


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


def _first_user_text(path: Path, *, max_lines: int = 200) -> str:
    """The first thing the person actually typed, for use as a label.

    A rollout opens with project instructions and plugin lists carrying the
    user role, so the earliest user-role record is usually harness context.
    Prefer a record that explicitly marks a typed message and fall back to the
    first plausible user text only if none appears.
    """
    fallback = ""
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
                if _is_typed_user_record(entry):
                    typed = _extract_user_text(entry)
                    if typed:
                        return typed[:80]
                if not fallback:
                    fallback = _extract_user_text(entry)
    except OSError:
        return ""
    return fallback[:80]


def _is_typed_user_record(entry: object) -> bool:
    if not isinstance(entry, dict):
        return False
    payload = entry.get("payload") if isinstance(entry.get("payload"), dict) else entry
    return str(payload.get("type") or "") == "user_message"


def _is_injected_context(text: str) -> bool:
    """Harness-injected context arrives with the user role but nobody typed it."""
    return text.lstrip().startswith("<") and ">" in text[:200]


def _extract_user_text(entry: object) -> str:
    if not isinstance(entry, dict):
        return ""
    # claude nests under "message", codex under "payload", others are flat.
    for key in ("message", "payload"):
        nested = entry.get(key)
        if isinstance(nested, dict):
            entry = nested
            break
    # codex records what the person actually typed as its own event.
    if str(entry.get("type") or "") == "user_message":
        typed = str(entry.get("message") or "").strip()
        return "" if _is_injected_context(typed) else typed
    role = str(entry.get("role") or "")
    if role != "user":
        # A flat record with no role at all is still worth reading; anything
        # that names a non-user role is not.
        if role:
            return ""
    content = entry.get("content")
    if isinstance(content, str):
        stripped = content.strip()
        return "" if _is_injected_context(stripped) else stripped
    if isinstance(content, list):
        for block in content:
            # codex writes input_text, claude writes text.
            if isinstance(block, dict) and block.get("type") in {"text", "input_text"}:
                piece = str(block.get("text") or "").strip()
                if piece and not _is_injected_context(piece):
                    return piece
    return ""


def _recorded_cwd(path: Path, *, max_lines: int = 8) -> str:
    """Working directory a rollout was started in, from its own header."""
    try:
        with path.open("r", encoding="utf-8") as handle:
            for index, line in enumerate(handle):
                if index >= max_lines:
                    break
                match = _CWD_RE.search(line)
                if match:
                    return match.group(1)
    except OSError:
        return ""
    return ""


def _list_jsonl_sessions(
    directory: Path,
    limit: int,
    *,
    id_from_name: bool,
    workspace: str = "",
) -> list[dict[str, str]]:
    if not directory.exists():
        return []
    wanted = _normalized_workspace(workspace)
    items: list[dict[str, str]] = []
    for path in directory.rglob("*.jsonl"):
        try:
            mtime = path.stat().st_mtime
        except OSError:
            continue
        # Resuming a rollout recorded in another folder would hand the agent
        # history about an unrelated project.
        if wanted and _normalized_workspace(_recorded_cwd(path)) != wanted:
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
    # A named folder with no project directory has no sessions. Falling back to
    # the whole store would answer a question about one folder with every
    # folder's history.
    return candidate if candidate.exists() else None


def _list_claude_sessions(home: Path, workspace: str, limit: int) -> list[dict[str, str]]:
    directory = _claude_project_dir(home, workspace)
    if directory is None:
        return []
    return _list_jsonl_sessions(directory, limit, id_from_name=False)


def _list_antigravity_sessions(home: Path, workspace: str, limit: int) -> list[dict[str, str]]:
    conversations = home / ".gemini" / "antigravity-cli" / "conversations"
    if not conversations.exists():
        return []
    # cwd -> conversation_id. Doubles as the workspace filter and the label:
    # resuming a conversation started in another folder would drop the agent
    # into unrelated history.
    labels: dict[str, str] = {}
    owned_here: set[str] = set()
    wanted = _normalized_workspace(workspace)
    cache = home / ".gemini" / "antigravity-cli" / "cache" / "last_conversations.json"
    try:
        data = json.loads(cache.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            for cwd, conv_id in data.items():
                labels[str(conv_id)] = Path(str(cwd)).name or str(cwd)
                if wanted and _normalized_workspace(str(cwd)) == wanted:
                    owned_here.add(str(conv_id))
    except (OSError, json.JSONDecodeError, ValueError):
        pass
    items: list[dict[str, str]] = []
    for path in conversations.glob("*.db"):
        try:
            mtime = path.stat().st_mtime
        except OSError:
            continue
        conv_id = path.stem
        if wanted and conv_id not in owned_here:
            continue
        items.append(
            {
                "session_id": conv_id,
                "label": labels.get(conv_id, conv_id[:8]),
                "updated_at": _iso(mtime),
            }
        )
    return _sorted_limited(items, limit)


def _list_grok_sessions(home: Path, workspace: str, limit: int) -> list[dict[str, str]]:
    """Grok keys its store by workspace already: sessions/<url-encoded cwd>/<id>/."""
    root = home / ".grok" / "sessions"
    if not root.exists():
        return []
    wanted = _normalized_workspace(workspace)
    items: list[dict[str, str]] = []
    for folder in root.iterdir():
        if not folder.is_dir():
            continue
        if wanted and _normalized_workspace(unquote(folder.name)) != wanted:
            continue
        prompts = _grok_first_prompts(folder / "prompt_history.jsonl")
        for session_dir in folder.iterdir():
            if not session_dir.is_dir():
                continue
            summary = _read_json(session_dir / "summary.json")
            try:
                mtime = session_dir.stat().st_mtime
            except OSError:
                continue
            info = summary.get("info") if isinstance(summary.get("info"), dict) else {}
            session_id = str(info.get("id") or session_dir.name)
            label = (
                str(summary.get("session_summary") or "").strip()
                or prompts.get(session_id, "")
                or session_dir.name
            )
            items.append(
                {
                    "session_id": session_id,
                    "label": " ".join(label.split())[:80],
                    "updated_at": _iso(mtime),
                }
            )
    return _sorted_limited(items, limit)


def _grok_first_prompts(path: Path, *, max_lines: int = 4000) -> dict[str, str]:
    """session_id -> its opening prompt, from the folder's shared history."""
    first: dict[str, str] = {}
    for entry in _read_jsonl(path, max_lines=max_lines):
        if entry.get("is_bash"):
            continue
        session_id = str(entry.get("session_id") or "")
        prompt = str(entry.get("prompt") or "").strip()
        if _is_injected_context(prompt):
            continue
        if session_id and prompt and session_id not in first:
            first[session_id] = prompt
    return first


def _list_cursor_sessions(home: Path, workspace: str, limit: int) -> list[dict[str, str]]:
    """Cursor records cwd, title and updated time in each chat's meta.json."""
    root = home / ".cursor" / "chats"
    if not root.exists():
        return []
    wanted = _normalized_workspace(workspace)
    items: list[dict[str, str]] = []
    for bucket in root.iterdir():
        if not bucket.is_dir():
            continue
        for session_dir in bucket.iterdir():
            meta = _read_json(session_dir / "meta.json")
            if not meta:
                continue
            if wanted and _normalized_workspace(str(meta.get("cwd") or "")) != wanted:
                continue
            updated_ms = meta.get("updatedAtMs") or meta.get("createdAtMs") or 0
            try:
                updated = _iso(float(updated_ms) / 1000.0)
            except (TypeError, ValueError):
                continue
            items.append(
                {
                    "session_id": session_dir.name,
                    "label": str(meta.get("title") or session_dir.name)[:80],
                    "updated_at": updated,
                }
            )
    return _sorted_limited(items, limit)


def _list_opencode_sessions(home: Path, workspace: str, limit: int) -> list[dict[str, str]]:
    """OpenCode keeps sessions in one sqlite store with the directory on each row."""
    database = home / ".local" / "share" / "opencode" / "opencode.db"
    if not database.exists():
        return []
    wanted = _normalized_workspace(workspace)
    items: list[dict[str, str]] = []
    connection = None
    try:
        # Read-only so a running opencode is never disturbed.
        connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True, timeout=2.0)
        rows = connection.execute(
            "SELECT id, directory, title, time_updated FROM session "
            "WHERE time_archived IS NULL ORDER BY time_updated DESC LIMIT 500"
        ).fetchall()
    except sqlite3.Error as error:
        raise RuntimeError("OpenCode session store could not be read.") from error
    finally:
        if connection is not None:
            connection.close()
    for session_id, directory, title, updated_ms in rows:
        if wanted and _normalized_workspace(str(directory or "")) != wanted:
            continue
        try:
            updated = _iso(float(updated_ms or 0) / 1000.0)
        except (TypeError, ValueError):
            continue
        items.append(
            {
                "session_id": str(session_id),
                "label": " ".join(str(title or session_id).split())[:80],
                "updated_at": updated,
            }
        )
    return _sorted_limited(items, limit)


def _read_json(path: Path) -> dict:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _read_jsonl(path: Path, *, max_lines: int) -> list[dict]:
    entries: list[dict] = []
    try:
        with path.open("r", encoding="utf-8") as handle:
            for index, line in enumerate(handle):
                if index >= max_lines:
                    break
                try:
                    entry = json.loads(line)
                except (json.JSONDecodeError, ValueError):
                    continue
                if isinstance(entry, dict):
                    entries.append(entry)
    except OSError:
        return []
    return entries


def _normalized_workspace(value: str) -> str:
    clean = str(value or "").strip()
    if not clean:
        return ""
    try:
        return str(Path(clean).expanduser().resolve())
    except (OSError, RuntimeError, ValueError):
        return clean.rstrip("/")
