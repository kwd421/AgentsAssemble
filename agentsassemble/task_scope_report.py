from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


VERSION = "task_scope_report_v0"
MAX_CANDIDATES_PER_ROLE = 32
MAX_OVERLAPS = 64
_FILE_RE = re.compile(r"^([A-Za-z0-9._-]+/)+[A-Za-z0-9._-]+\.[A-Za-z0-9]{1,8}$")
_DIR_RE = re.compile(r"^([A-Za-z0-9._-]+/)+$")
_ABSOLUTE_OR_REMOTE_RE = re.compile(r"^(?:/|~[/\\]|[A-Za-z]:[\\/]|https?://)", re.IGNORECASE)
_HOST_SEGMENT_RE = re.compile(r"^[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+$")


def build_task_scope_report(
    meeting: dict[str, object],
    *,
    now_iso: str | None = None,
) -> dict[str, object]:
    roles = _role_rows(meeting)
    tasks = _task_map(meeting)
    role_reports = []
    token_roles: dict[tuple[str, str], list[dict[str, str]]] = {}
    roles_without_task_text = 0
    roles_without_candidates = 0
    candidate_total = 0
    for role in roles:
        role_id = role["role_id"]
        task_text = str(tasks.get(role_id) or "").strip()
        task_scope = _structured_scope_for_role(meeting, role_id)
        source = "none"
        if task_scope:
            source = "task_scope_field"
            raw_candidates = _candidate_references_from_scope(task_scope)
        elif task_text:
            source = "task_text"
            raw_candidates = _candidate_references_from_text(task_text)
        else:
            raw_candidates = []
            roles_without_task_text += 1
        candidates = _dedupe_candidates(raw_candidates)
        truncated = len(candidates) > MAX_CANDIDATES_PER_ROLE
        candidates = candidates[:MAX_CANDIDATES_PER_ROLE]
        if not candidates:
            roles_without_candidates += 1
        candidate_total += len(candidates)
        role_report = {
            "role_id": role_id,
            "display_name": role["display_name"],
            "task_scope_source": source,
            "task_text_present": bool(task_text),
            "candidate_references": candidates,
            "candidate_count": len(candidates),
            "truncated": truncated,
        }
        role_reports.append(role_report)
        for candidate in candidates:
            key = (str(candidate["kind"]), str(candidate["token"]))
            token_roles.setdefault(key, []).append({"role_id": role_id, "display_name": role["display_name"]})
    overlaps = _overlap_rows(token_roles)
    overlaps_truncated = len(overlaps) > MAX_OVERLAPS
    overlaps = overlaps[:MAX_OVERLAPS]
    return {
        "version": VERSION,
        "meeting_id": _safe_token(meeting.get("meeting_id"), limit=128),
        "generated_at": now_iso or datetime.now(UTC).isoformat(),
        "summary": "scope_overlap_evidence" if overlaps else "no_obvious_overlaps",
        "overlap_count": len(overlaps),
        "candidate_count_total": candidate_total,
        "roles": role_reports,
        "overlaps": overlaps,
        "overlaps_truncated": overlaps_truncated,
        "compatibility": {
            "roles_without_task_text": roles_without_task_text,
            "roles_without_candidates": roles_without_candidates,
        },
        "limits": {
            "max_candidates_per_role": MAX_CANDIDATES_PER_ROLE,
            "max_overlaps": MAX_OVERLAPS,
            "snippet_max_chars": 0,
        },
        "advisory": {
            "implementation_approval": False,
            "filesystem_write": False,
            "note": "Advisory scope-overlap evidence; review tasks/<role>.md and decision.md before editing files.",
        },
    }


def render_task_scope_report_markdown(report: dict[str, object]) -> str:
    lines = [
        "# Task Scope Report",
        "",
        "Advisory only: this report does not approve implementation or filesystem writes.",
        "",
        f"- Meeting: {report.get('meeting_id') or 'unknown'}",
        f"- Summary: {report.get('summary') or 'unknown'}",
        f"- Candidate references: {report.get('candidate_count_total') or 0}",
        f"- Overlaps: {report.get('overlap_count') or 0}",
        "",
        "## Overlaps",
        "",
    ]
    overlaps = report.get("overlaps") if isinstance(report.get("overlaps"), list) else []
    if overlaps:
        for overlap in overlaps:
            if not isinstance(overlap, dict):
                continue
            roles = ", ".join(str(role_id) for role_id in overlap.get("role_ids", []) if str(role_id).strip())
            lines.append(f"- `{overlap.get('token')}` ({overlap.get('kind')}): {roles}")
    else:
        lines.append("- No overlap evidence detected from task text.")
    lines.extend(["", "## Role Candidates", ""])
    for role in report.get("roles") if isinstance(report.get("roles"), list) else []:
        if not isinstance(role, dict):
            continue
        lines.extend(
            [
                f"### {role.get('display_name') or role.get('role_id')}",
                "",
                f"- Role: `{role.get('role_id')}`",
                f"- Source: {role.get('task_scope_source') or 'none'}",
                f"- Candidate count: {role.get('candidate_count') or 0}",
            ]
        )
        if role.get("truncated"):
            lines.append("- Candidate list was truncated.")
        candidates = role.get("candidate_references") if isinstance(role.get("candidate_references"), list) else []
        if candidates:
            lines.append("- Candidates:")
            for candidate in candidates:
                if isinstance(candidate, dict):
                    lines.append(f"  - `{candidate.get('token')}` ({candidate.get('kind')})")
        else:
            lines.append("- Candidates: none")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def write_task_scope_report(
    meeting_dir: Path,
    meeting: dict[str, object],
    *,
    now_iso: str | None = None,
) -> dict[str, object]:
    report = build_task_scope_report(meeting, now_iso=now_iso)
    (meeting_dir / "task_scope_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (meeting_dir / "task_scope_report.md").write_text(render_task_scope_report_markdown(report), encoding="utf-8")
    artifacts = meeting.get("artifacts")
    if not isinstance(artifacts, dict):
        artifacts = {}
        meeting["artifacts"] = artifacts
    artifacts["task_scope_report"] = "task_scope_report.md"
    artifacts["task_scope_report_json"] = "task_scope_report.json"
    meeting["task_scope_report"] = {
        "version": report["version"],
        "summary": report["summary"],
        "overlap_count": report["overlap_count"],
        "candidate_count_total": report["candidate_count_total"],
    }
    return report


def _role_rows(meeting: dict[str, object]) -> list[dict[str, str]]:
    roles = []
    seen = set()
    for role in meeting.get("roles") if isinstance(meeting.get("roles"), list) else []:
        if not isinstance(role, dict):
            continue
        role_id = _safe_token(role.get("id") or role.get("role_id"), limit=128)
        if not role_id or role_id in seen:
            continue
        seen.add(role_id)
        roles.append(
            {
                "role_id": role_id,
                "display_name": _safe_display_name(role.get("display_name") or role.get("name") or role_id),
            }
        )
    for role_id in sorted(_task_map(meeting)):
        clean_role_id = _safe_token(role_id, limit=128)
        if clean_role_id and clean_role_id not in seen:
            seen.add(clean_role_id)
            roles.append({"role_id": clean_role_id, "display_name": clean_role_id})
    return roles


def _task_map(meeting: dict[str, object]) -> dict[str, str]:
    synthesis = meeting.get("moderator_synthesis") if isinstance(meeting.get("moderator_synthesis"), dict) else {}
    tasks = synthesis.get("tasks") if isinstance(synthesis.get("tasks"), dict) else {}
    return {
        _safe_token(role_id, limit=128): str(task)
        for role_id, task in tasks.items()
        if _safe_token(role_id, limit=128)
    }


def _structured_scope_for_role(meeting: dict[str, object], role_id: str) -> object:
    synthesis = meeting.get("moderator_synthesis") if isinstance(meeting.get("moderator_synthesis"), dict) else {}
    task_scope = synthesis.get("task_scope") if isinstance(synthesis.get("task_scope"), dict) else {}
    return task_scope.get(role_id)


def _candidate_references_from_scope(scope: object) -> list[dict[str, str]]:
    values: list[str] = []
    if isinstance(scope, str):
        values = scope.split()
    elif isinstance(scope, list):
        values = [str(item) for item in scope]
    elif isinstance(scope, dict):
        for key in ("files", "dirs", "paths", "references"):
            raw = scope.get(key)
            if isinstance(raw, list):
                values.extend(str(item) for item in raw)
            elif isinstance(raw, str):
                values.extend(raw.split())
    return _candidate_references(values)


def _candidate_references_from_text(text: str) -> list[dict[str, str]]:
    return _candidate_references(str(text or "").split())


def _candidate_references(values: list[str]) -> list[dict[str, str]]:
    candidates = []
    for value in values:
        candidate = _candidate_reference(value)
        if candidate:
            candidates.append(candidate)
    return candidates


def _candidate_reference(token: object) -> dict[str, str]:
    clean = _clean_candidate_token(token)
    if not clean or _ABSOLUTE_OR_REMOTE_RE.match(clean) or _has_dot_segment(clean) or _has_host_segment(clean):
        return {}
    if _DIR_RE.match(clean):
        return {"token": clean, "kind": "dir"}
    if _FILE_RE.match(clean):
        return {"token": clean, "kind": "file"}
    return {}


def _has_dot_segment(token: str) -> bool:
    return any(segment in {".", ".."} for segment in token.split("/"))


def _has_host_segment(token: str) -> bool:
    first_segment = token.split("/", 1)[0].rstrip(".")
    return bool(_HOST_SEGMENT_RE.match(first_segment))


def _clean_candidate_token(token: object) -> str:
    text = str(token or "").strip()
    text = text.strip("`'\"[](){}<>")
    text = text.rstrip(".,;:")
    return text


def _dedupe_candidates(candidates: list[dict[str, str]]) -> list[dict[str, str]]:
    result = []
    seen = set()
    for candidate in candidates:
        key = (candidate.get("kind"), candidate.get("token"))
        if not key[0] or not key[1] or key in seen:
            continue
        seen.add(key)
        result.append({"token": str(key[1]), "kind": str(key[0])})
    return result


def _overlap_rows(token_roles: dict[tuple[str, str], list[dict[str, str]]]) -> list[dict[str, object]]:
    rows = []
    for (kind, token), roles in sorted(token_roles.items()):
        if len(roles) < 2:
            continue
        rows.append(
            {
                "kind": kind,
                "token": token,
                "role_ids": [role["role_id"] for role in roles],
                "display_names": [role["display_name"] for role in roles],
            }
        )
    return rows


def _safe_token(value: object, *, limit: int) -> str:
    return re.sub(r"[^A-Za-z0-9_.:-]+", "-", str(value or "").strip()).strip(".-")[:limit]


def _safe_display_name(value: object) -> str:
    text = re.sub(r"[\r\n\t]+", " ", str(value or "").strip())
    text = re.sub(r"[\\/]+", "-", text)
    return text[:120]
