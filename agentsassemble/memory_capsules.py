from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


CAPSULE_SCHEMA = "agentsassemble.memory_capsule.v0"
REDACTED_CAPSULE_PATH = "[redacted]"
MAX_CAPSULE_FILE_BYTES = 512 * 1024
REQUIRED_CAPSULE_FILES = (
    "persona.md",
    "memory_summary.md",
    "decision_history.md",
    "lessons_learned.md",
    "evidence_index.json",
    "handoff.md",
    "permissions.json",
    "provenance.json",
)
RECOMMENDED_CAPSULE_FILES = ("risk_review.md",)
JSON_CAPSULE_FILES = ("evidence_index.json", "permissions.json", "provenance.json")
ALLOWED_PERMISSION_TRUE_KEYS = {"meeting_read", "lobby_chat", "official_turn"}
DENIED_PERMISSION_TRUE_KEYS = {
    "credential_access",
    "deploy",
    "filesystem_read",
    "filesystem_write",
    "git_write",
    "implementation",
    "network",
    "push",
    "release",
    "secrets",
    "shell",
    "tool_use",
}
RAW_SESSION_DUMP_NAMES = {
    ".env",
    "conversation.json",
    "conversation.jsonl",
    "cookies.sqlite",
    "messages.json",
    "messages.jsonl",
    "session.json",
    "session.jsonl",
    "state.json",
    "transcript.jsonl",
}
SECRET_ASSIGNMENT_PATTERN = re.compile(
    r"(?i)(authorization\s*:\s*bearer|[\"']?api[_-]?key[\"']?\s*[:=]|[\"']?password[\"']?\s*[:=]|"
    r"[\"']?secret[\"']?\s*[:=]|[\"']?token[\"']?\s*[:=])"
)


def memory_capsule_gate_report(capsule_dir: Path) -> dict[str, object]:
    root = Path(capsule_dir)
    required_files = _required_file_reports(root)
    recommended_files = _recommended_file_reports(root)
    checks: list[dict[str, object]] = [
        _directory_check(root),
        _required_files_check(required_files),
        _recommended_files_check(recommended_files),
    ]
    parsed_json = _parsed_json_documents(root, required_files)
    checks.extend(_json_checks(parsed_json))
    checks.append(_permissions_policy_check(parsed_json.get("permissions.json")))
    checks.append(_raw_session_dump_check(root))
    checks.append(_secret_marker_check(root, [*required_files, *recommended_files]))
    evidence_summary = _evidence_summary(parsed_json.get("evidence_index.json"))
    summary = _summary(required_files, recommended_files, checks)
    status = "failed" if summary["failed_checks"] else "degraded" if summary["warnings"] else "ok"
    return {
        "schema": CAPSULE_SCHEMA,
        "status": status,
        "capsule_path": REDACTED_CAPSULE_PATH,
        "execution_allowed": False,
        "meeting_influence_allowed": status == "ok",
        "summary": summary,
        "required_files": required_files,
        "recommended_files": recommended_files,
        "permissions": _permissions_summary(parsed_json.get("permissions.json")),
        "evidence_index": evidence_summary,
        "checks": checks,
    }


def _directory_check(root: Path) -> dict[str, str]:
    if root.is_dir():
        return {"id": "capsule_directory", "status": "ok", "message": "Memory capsule directory is readable."}
    return {"id": "capsule_directory", "status": "failed", "message": "Memory capsule path is not a directory."}


def _required_file_reports(root: Path) -> list[dict[str, object]]:
    return [_file_report(root, name, required=True) for name in REQUIRED_CAPSULE_FILES]


def _recommended_file_reports(root: Path) -> list[dict[str, object]]:
    return [_file_report(root, name, required=False) for name in RECOMMENDED_CAPSULE_FILES]


def _file_report(root: Path, name: str, *, required: bool) -> dict[str, object]:
    path = root / name
    if not path.is_file():
        return {"name": name, "status": "missing", "required": required, "bytes": 0}
    try:
        size = path.stat().st_size
    except OSError:
        return {"name": name, "status": "unreadable", "required": required, "bytes": 0}
    status = "too_large" if size > MAX_CAPSULE_FILE_BYTES else "ok"
    return {"name": name, "status": status, "required": required, "bytes": size}


def _required_files_check(files: list[dict[str, object]]) -> dict[str, str]:
    missing = [str(item["name"]) for item in files if item.get("status") == "missing"]
    unreadable = [str(item["name"]) for item in files if item.get("status") == "unreadable"]
    too_large = [str(item["name"]) for item in files if item.get("status") == "too_large"]
    problems = missing + unreadable + too_large
    if problems:
        return {
            "id": "required_files",
            "status": "failed",
            "message": "Required capsule files need attention: " + ", ".join(problems),
        }
    return {"id": "required_files", "status": "ok", "message": "Required capsule files are present."}


def _recommended_files_check(files: list[dict[str, object]]) -> dict[str, str]:
    missing = [str(item["name"]) for item in files if item.get("status") == "missing"]
    if missing:
        return {
            "id": "recommended_files",
            "status": "ok",
            "message": "Recommended capsule files are missing: " + ", ".join(missing),
        }
    return {"id": "recommended_files", "status": "ok", "message": "Recommended capsule files are present."}


def _parsed_json_documents(root: Path, required_files: list[dict[str, object]]) -> dict[str, dict[str, object] | None]:
    file_status = {str(item["name"]): item.get("status") for item in required_files}
    parsed: dict[str, dict[str, object] | None] = {}
    for name in JSON_CAPSULE_FILES:
        if file_status.get(name) != "ok":
            parsed[name] = None
            continue
        try:
            loaded = json.loads((root / name).read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            parsed[name] = None
            continue
        parsed[name] = loaded if isinstance(loaded, dict) else None
    return parsed


def _json_checks(parsed_json: dict[str, dict[str, object] | None]) -> list[dict[str, str]]:
    checks: list[dict[str, str]] = []
    for name in JSON_CAPSULE_FILES:
        if parsed_json.get(name) is None:
            checks.append({"id": f"json:{name}", "status": "failed", "message": f"{name} must be a JSON object."})
        else:
            checks.append({"id": f"json:{name}", "status": "ok", "message": f"{name} is a JSON object."})
    return checks


def _permissions_policy_check(permissions: dict[str, object] | None) -> dict[str, str]:
    if permissions is None:
        return {"id": "permissions_policy", "status": "failed", "message": "permissions.json could not be evaluated."}
    denied_true = sorted(
        key for key in DENIED_PERMISSION_TRUE_KEYS if permissions.get(key) is True
    )
    if denied_true:
        return {
            "id": "permissions_policy",
            "status": "failed",
            "message": "Memory capsule requests denied permissions: " + ", ".join(denied_true),
        }
    unknown_true = sorted(
        key
        for key, value in permissions.items()
        if value is True and key not in ALLOWED_PERMISSION_TRUE_KEYS and key not in DENIED_PERMISSION_TRUE_KEYS
    )
    if unknown_true:
        return {
            "id": "permissions_policy",
            "status": "warning",
            "message": f"Memory capsule has {len(unknown_true)} unrecognized true permission(s).",
        }
    return {"id": "permissions_policy", "status": "ok", "message": "Memory capsule permissions are meeting-safe."}


def _raw_session_dump_check(root: Path) -> dict[str, str]:
    try:
        children = list(root.rglob("*")) if root.is_dir() else []
    except OSError:
        children = []
    risky = sorted(path.name for path in children if path.name.casefold() in RAW_SESSION_DUMP_NAMES)
    if risky:
        return {
            "id": "raw_session_dump",
            "status": "failed",
            "message": "Raw or hidden session dump files are not importable memory capsules: " + ", ".join(risky),
        }
    return {"id": "raw_session_dump", "status": "ok", "message": "No raw session dump files were found."}


def _secret_marker_check(root: Path, files: list[dict[str, object]]) -> dict[str, str]:
    risky: list[str] = []
    for item in files:
        if item.get("status") != "ok":
            continue
        name = str(item.get("name") or "")
        try:
            text = (root / name).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if SECRET_ASSIGNMENT_PATTERN.search(text):
            risky.append(name)
    if risky:
        return {
            "id": "secret_markers",
            "status": "failed",
            "message": "Capsule files contain credential-like assignments: " + ", ".join(sorted(risky)),
        }
    return {"id": "secret_markers", "status": "ok", "message": "No credential-like assignments were found in capsule files."}


def _permissions_summary(permissions: dict[str, object] | None) -> dict[str, object]:
    if permissions is None:
        return {"available": False, "allowed_true": [], "denied_true": [], "unknown_true_count": 0}
    allowed_true = sorted(key for key in ALLOWED_PERMISSION_TRUE_KEYS if permissions.get(key) is True)
    denied_true = sorted(key for key in DENIED_PERMISSION_TRUE_KEYS if permissions.get(key) is True)
    unknown_true_count = sum(
        1
        for key, value in permissions.items()
        if value is True and key not in ALLOWED_PERMISSION_TRUE_KEYS and key not in DENIED_PERMISSION_TRUE_KEYS
    )
    return {
        "available": True,
        "allowed_true": allowed_true,
        "denied_true": denied_true,
        "unknown_true_count": unknown_true_count,
    }


def _evidence_summary(evidence_index: dict[str, object] | None) -> dict[str, object]:
    if evidence_index is None:
        return {"available": False, "source_count": 0, "entry_count": 0}
    sources = evidence_index.get("sources")
    entries = evidence_index.get("entries")
    evidence = evidence_index.get("evidence")
    entry_count = _list_count(entries)
    if entry_count == 0:
        entry_count = _list_count(evidence)
    return {
        "available": True,
        "source_count": _list_count(sources),
        "entry_count": entry_count,
    }


def _list_count(value: Any) -> int:
    return len(value) if isinstance(value, list) else 0


def _summary(
    required_files: list[dict[str, object]],
    recommended_files: list[dict[str, object]],
    checks: list[dict[str, object]],
) -> dict[str, int]:
    failed_checks = sum(1 for check in checks if check.get("status") == "failed")
    warnings = sum(1 for check in checks if check.get("status") == "warning")
    return {
        "required_files": len(required_files),
        "required_files_ok": sum(1 for item in required_files if item.get("status") == "ok"),
        "recommended_files": len(recommended_files),
        "recommended_files_ok": sum(1 for item in recommended_files if item.get("status") == "ok"),
        "failed_checks": failed_checks,
        "warnings": warnings,
    }
