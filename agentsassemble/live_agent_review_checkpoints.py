from __future__ import annotations

import json
import re
from pathlib import Path
from threading import Lock

from agentsassemble.meeting_events import clean_lobby_text

_REVIEW_CHECKPOINT_ARTIFACT_LOCK = Lock()
_REVIEW_CHECKPOINT_ARTIFACT_LOCKS: dict[str, Lock] = {}


def write_review_checkpoint_artifacts(meeting_dir: Path, checkpoint: dict[str, object]) -> dict[str, str]:
    checkpoint_id = _review_checkpoint_identity_or_default(checkpoint.get("checkpoint_id"))
    artifact_lock = _review_checkpoint_artifact_lock(meeting_dir)
    with artifact_lock:
        file_stem = _review_checkpoint_file_stem_for_write(meeting_dir, checkpoint_id)
        artifact = review_checkpoint_artifact_payload({**checkpoint, "checkpoint_id": checkpoint_id})
        relative_markdown = f"review_checkpoints/{file_stem}.md"
        relative_json = f"review_checkpoints/{file_stem}.json"
        markdown_path = meeting_dir / relative_markdown
        json_path = meeting_dir / relative_json
        markdown_path.parent.mkdir(parents=True, exist_ok=True)
        markdown_path.write_text(render_review_checkpoint_markdown(artifact), encoding="utf-8")
        json_path.write_text(json.dumps(artifact, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {
        "artifact_path": relative_markdown,
        "artifact_json_path": relative_json,
        "checkpoint_file_id": file_stem,
    }


def review_checkpoint_file_stem(value: object) -> str:
    text = _checkpoint_filename_text(value, limit=128)
    text = re.sub(r"[^A-Za-z0-9_.-]+", "_", text).strip("._-")
    if not text or text in {".", ".."}:
        return "checkpoint"
    return text[:96]


def _review_checkpoint_identity(value: object) -> str:
    return "" if value is None else str(value)


def _review_checkpoint_identity_or_default(value: object) -> str:
    identity = _review_checkpoint_identity(value)
    return identity if identity.strip() else "checkpoint"


def _checkpoint_filename_text(value: object, *, limit: int) -> str:
    return _review_checkpoint_identity(value).replace("\n", " ").replace("\r", " ").strip()[:limit]


def _review_checkpoint_artifact_lock(meeting_dir: Path) -> Lock:
    try:
        key = str(meeting_dir.resolve())
    except OSError:
        key = str(meeting_dir)
    with _REVIEW_CHECKPOINT_ARTIFACT_LOCK:
        lock = _REVIEW_CHECKPOINT_ARTIFACT_LOCKS.get(key)
        if lock is None:
            lock = Lock()
            _REVIEW_CHECKPOINT_ARTIFACT_LOCKS[key] = lock
        return lock


def _review_checkpoint_file_stem_for_write(meeting_dir: Path, checkpoint_id: str) -> str:
    base_stem = review_checkpoint_file_stem(checkpoint_id)
    for suffix in range(0, 1000):
        file_stem = base_stem if suffix == 0 else f"{base_stem}-{suffix + 1}"
        if _review_checkpoint_artifact_slot_available(meeting_dir, file_stem, checkpoint_id):
            return file_stem
    raise ValueError(f"Too many review checkpoint artifacts collide with {base_stem}.")


def _review_checkpoint_artifact_slot_available(meeting_dir: Path, file_stem: str, checkpoint_id: str) -> bool:
    markdown_path = meeting_dir / "review_checkpoints" / f"{file_stem}.md"
    json_path = meeting_dir / "review_checkpoints" / f"{file_stem}.json"
    if not markdown_path.exists() and not json_path.exists():
        return True
    return markdown_path.exists() and json_path.exists() and _review_checkpoint_json_id(json_path) == checkpoint_id


def _review_checkpoint_json_id(path: Path) -> str:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ""
    if not isinstance(payload, dict):
        return ""
    return _review_checkpoint_identity(payload.get("checkpoint_id"))


def review_checkpoint_artifact_payload(checkpoint: dict[str, object]) -> dict[str, object]:
    return {
        "checkpoint_id": _review_checkpoint_identity(checkpoint.get("checkpoint_id")),
        "meeting_id": clean_lobby_text(checkpoint.get("meeting_id"), limit=128),
        "group_id": clean_lobby_text(checkpoint.get("group_id"), limit=128),
        "status": clean_lobby_text(checkpoint.get("status"), limit=64) or "unknown",
        "turn_count": _nonnegative_int(checkpoint.get("turn_count")),
        "answered_count": _nonnegative_int(checkpoint.get("answered_count")),
        "timeout_count": _nonnegative_int(checkpoint.get("timeout_count")),
        "skipped_count": _nonnegative_int(checkpoint.get("skipped_count")),
        "timeout_seconds": _nonnegative_float(checkpoint.get("timeout_seconds")),
        "agent_ids": _strings(checkpoint.get("agent_ids"), limit=64),
        "results": [_artifact_result(item) for item in _dicts(checkpoint.get("results"))],
    }


def render_review_checkpoint_markdown(artifact: dict[str, object]) -> str:
    lines = [
        f"# Review Checkpoint: {artifact.get('checkpoint_id') or 'checkpoint'}",
        "",
        f"Status: {artifact.get('status') or 'unknown'}",
        f"Meeting: {artifact.get('meeting_id') or 'unknown'}",
        f"Group: {artifact.get('group_id') or 'unknown'}",
        f"Answered: {artifact.get('answered_count') or 0}/{artifact.get('turn_count') or 0}",
        f"Timed out: {artifact.get('timeout_count') or 0}",
        f"Skipped: {artifact.get('skipped_count') or 0}",
        "",
        "## Prompt",
        "",
        _first_prompt(artifact) or "No prompt recorded.",
        "",
        "## Replies",
        "",
    ]
    results = _dicts(artifact.get("results"))
    if not results:
        lines.append("No resident replies recorded.")
    for result in results:
        lines.extend(_render_result(result))
    return "\n".join(lines).rstrip() + "\n"


def _artifact_result(value: dict[str, object]) -> dict[str, object]:
    request = value.get("request_event") if isinstance(value.get("request_event"), dict) else {}
    reply = value.get("reply_event") if isinstance(value.get("reply_event"), dict) else {}
    result = {
        "index": _nonnegative_int(value.get("index")),
        "agent_id": clean_lobby_text(value.get("agent_id") or request.get("target_agent_id"), limit=64),
        "role_id": clean_lobby_text(value.get("role_id") or request.get("role_id"), limit=128),
        "status": clean_lobby_text(value.get("status"), limit=64) or "unknown",
        "elapsed_seconds": _nonnegative_float(value.get("elapsed_seconds")),
        "timeout_seconds": _nonnegative_float(value.get("timeout_seconds")),
        "request": _artifact_event(request),
        "reply": _artifact_event(reply) if reply else None,
    }
    return result


def _artifact_event(event: dict[str, object]) -> dict[str, object]:
    return {
        "event_id": clean_lobby_text(event.get("id"), limit=128),
        "created_at": clean_lobby_text(event.get("created_at"), limit=128),
        "actor_id": clean_lobby_text(event.get("actor_id"), limit=64),
        "target_agent_id": clean_lobby_text(event.get("target_agent_id"), limit=64),
        "role_id": clean_lobby_text(event.get("role_id"), limit=128),
        "content": clean_lobby_text(event.get("content"), limit=4000),
    }


def _render_result(result: dict[str, object]) -> list[str]:
    agent_id = result.get("agent_id") or "unknown-agent"
    status = result.get("status") or "unknown"
    reply = result.get("reply") if isinstance(result.get("reply"), dict) else {}
    content = str(reply.get("content") or "").strip() if reply else ""
    if not content:
        content = f"_{status}_"
    return [
        f"### {agent_id}",
        "",
        f"Status: {status}",
        "",
        content,
        "",
    ]


def _first_prompt(artifact: dict[str, object]) -> str:
    for result in _dicts(artifact.get("results")):
        request = result.get("request") if isinstance(result.get("request"), dict) else {}
        content = str(request.get("content") or "").strip()
        if content:
            return content
    return ""


def _dicts(value: object) -> list[dict[str, object]]:
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _strings(value: object, *, limit: int) -> list[str]:
    return [text for item in value if (text := clean_lobby_text(item, limit=limit))] if isinstance(value, list) else []


def _nonnegative_int(value: object) -> int:
    if isinstance(value, bool):
        return 0
    try:
        number = int(value)
    except (TypeError, ValueError):
        return 0
    return max(0, number)


def _nonnegative_float(value: object) -> float:
    if isinstance(value, bool):
        return 0.0
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, number)
